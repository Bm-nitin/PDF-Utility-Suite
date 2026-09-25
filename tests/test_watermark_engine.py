"""
test_watermark_engine.py

Direct tests for watermark_engine.py (Phase 21): option validation,
page-selection resolution, rotation-aware placement geometry, the
add_watermark() engine itself (content, overlay behavior, page
selection, rotation handling for 0/90/180/270 degree pages, source
immutability, metadata preservation, error handling, atomic-save
integration, encrypted-PDF behavior), and the file_manager output-path
helper.

Verification is content-based, not just "the file exists":

- the watermark's presence/absence on a page is checked by extracting
  text from the OUTPUT and searching for it;
- its position and direction are checked geometrically, from the
  extracted text line's bounding box and direction vector, mapped from
  PyMuPDF's unrotated content space into DISPLAYED space with
  page.rotation_matrix (see watermark_engine's module docstring);
- a smaller set of tests renders pages to pixmaps and measures where
  the ink actually landed (its centroid and principal axis) -- coarse,
  tolerance-based measurements, never pixel-perfect screenshots, and in
  pure Python so no imaging/numpy dependency is needed.

Pure engine tests: no tkinter, no display required.
"""

import math
import os
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager
import pdf_engine
import split_engine
import watermark_engine
from pdf_engine import EncryptedPDFError, InvalidPDFError, PDFEngineError
from watermark_engine import WatermarkOptionsError

WM = "WMARKTEST"  # a distinctive watermark string never present in source text

LETTER = (612, 792)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf(path, pages=3, rotations=None, body_text=True, size=LETTER,
              metadata=None):
    """A multi-page PDF; each page carries a unique 'BODY<n>' text (unless
    body_text=False, giving blank pages) and an optional /Rotate.
    """
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        if body_text:
            page.insert_text((72, 100), f"BODY{i + 1}", fontsize=14)
        if rotations and rotations[i]:
            page.set_rotation(rotations[i])
    if metadata:
        doc.set_metadata(metadata)
    doc.save(path)
    doc.close()
    return Path(path)


def _wm_lines(page, text=WM):
    """All extracted text lines on `page` whose full text equals `text`."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if "".join(span["text"] for span in line["spans"]) == text:
                lines.append(line)
    return lines


def _display_bbox(page, line):
    """The line's bounding box in DISPLAYED coordinates."""
    rect = pymupdf.Rect(line["bbox"]) * page.rotation_matrix
    rect.normalize()
    return rect


def _display_angle(page, line):
    """The line's text direction as seen on the displayed page, in
    degrees counter-clockwise from 'left-to-right', in [0, 360)."""
    dx, dy = line["dir"]
    m = page.rotation_matrix
    ddx = dx * m.a + dy * m.c
    ddy = dx * m.b + dy * m.d
    return math.degrees(math.atan2(-ddy, ddx)) % 360


def _angle_close(actual, expected, tol=0.5):
    diff = abs(actual - expected) % 360
    return min(diff, 360 - diff) <= tol


def _has_text(page, text=WM):
    return text in page.get_text()


def _render_stats(page, threshold=200):
    """(centroid_x, centroid_y, axis_angle_ccw_mod_180, ink_pixel_count)
    of the dark pixels of `page` rendered at 72 dpi, in pure Python.
    """
    pix = page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, alpha=False)
    width, height = pix.width, pix.height
    samples = pix.samples
    count = sx = sy = sxx = syy = sxy = 0
    for y in range(height):
        row = y * width
        for x in range(width):
            if samples[row + x] < threshold:
                count += 1
                sx += x
                sy += y
                sxx += x * x
                syy += y * y
                sxy += x * y
    assert count > 0, "no ink found on rendered page"
    cx, cy = sx / count, sy / count
    vxx = sxx / count - cx * cx
    vyy = syy / count - cy * cy
    vxy = sxy / count - cx * cy
    axis_img = 0.5 * math.atan2(2 * vxy, vxx - vyy)  # radians, y-down image space
    axis_ccw = (-math.degrees(axis_img)) % 180
    return cx, cy, axis_ccw, count


def _axis_close(actual, expected, tol=3.0):
    diff = abs(actual - expected) % 180
    return min(diff, 180 - diff) <= tol


def _min_gray(page):
    pix = page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, alpha=False)
    return min(pix.samples)


@pytest.fixture
def src(tmp_path):
    """A 5-page, text-bearing Letter PDF."""
    return _make_pdf(tmp_path / "document.pdf", pages=5)


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out.pdf"


def _watermark(src, out, **kwargs):
    kwargs.setdefault("text", WM)
    return watermark_engine.add_watermark(src, out, **kwargs)


# ---------------------------------------------------------------------------
# 1. Defaults
# ---------------------------------------------------------------------------

def test_documented_defaults():
    assert watermark_engine.DEFAULT_TEXT == "CONFIDENTIAL"
    assert watermark_engine.DEFAULT_POSITION == "center"
    assert watermark_engine.DEFAULT_FONT_SIZE == 36
    assert watermark_engine.DEFAULT_OPACITY == 30
    assert watermark_engine.DEFAULT_ROTATION == 45
    assert watermark_engine.DEFAULT_COLOR_NAME == "gray"
    assert watermark_engine.DEFAULT_COLOR == watermark_engine.COLOR_PRESETS["gray"]
    assert watermark_engine.DEFAULT_COLOR == (0.5, 0.5, 0.5)


def test_defaults_are_all_valid_options():
    assert watermark_engine.validate_text(watermark_engine.DEFAULT_TEXT)
    assert watermark_engine.validate_position(watermark_engine.DEFAULT_POSITION)
    assert watermark_engine.validate_font_size(watermark_engine.DEFAULT_FONT_SIZE)
    assert watermark_engine.validate_opacity(watermark_engine.DEFAULT_OPACITY) == 30
    assert watermark_engine.validate_rotation(watermark_engine.DEFAULT_ROTATION) == 45
    assert watermark_engine.validate_color(watermark_engine.DEFAULT_COLOR)


def test_default_configuration_end_to_end(src, out):
    """add_watermark() with no option arguments uses every default:
    CONFIDENTIAL, centered, 45 degrees, on every page."""
    watermark_engine.add_watermark(src, out)

    with pymupdf.open(out) as doc:
        for page in doc:
            lines = _wm_lines(page, "CONFIDENTIAL")
            assert len(lines) == 1
            bbox = _display_bbox(page, lines[0])
            assert abs((bbox.x0 + bbox.x1) / 2 - page.rect.width / 2) < 0.5
            assert abs((bbox.y0 + bbox.y1) / 2 - page.rect.height / 2) < 0.5
            assert _angle_close(_display_angle(page, lines[0]), 45)


def test_supported_constants():
    assert watermark_engine.POSITIONS == (
        "top_left", "top_center", "top_right",
        "center_left", "center", "center_right",
        "bottom_left", "bottom_center", "bottom_right",
    )
    assert watermark_engine.ROTATIONS == (0, 45, 90, 135, 180, 225, 270, 315)
    assert set(watermark_engine.COLOR_PRESETS) == {
        "black", "gray", "red", "blue", "green",
    }
    for rgb in watermark_engine.COLOR_PRESETS.values():
        assert len(rgb) == 3 and all(0.0 <= c <= 1.0 for c in rgb)


# ---------------------------------------------------------------------------
# 2-3. Basic + custom text
# ---------------------------------------------------------------------------

def test_basic_watermark_adds_text_and_keeps_body(src, out):
    result = _watermark(src, out)

    assert result == out
    assert out.exists()
    with pymupdf.open(out) as doc:
        for i, page in enumerate(doc):
            assert _has_text(page)
            assert f"BODY{i + 1}" in page.get_text()


@pytest.mark.parametrize("text", [
    "DRAFT",
    "Caf\u00e9 \u00d1andú \u00a9 2026",
    "Internal use only -- do not copy!",
    "A",
    "  padded  ",
])
def test_custom_text_is_rendered(src, out, text):
    _watermark(src, out, text=text, font_size=20, rotation=0)

    with pymupdf.open(out) as doc:
        assert text.strip() in doc[0].get_text()


def test_interior_spacing_is_preserved(src, out):
    _watermark(src, out, text="A  B", font_size=20, rotation=0)
    with pymupdf.open(out) as doc:
        assert "A  B" in doc[0].get_text().replace("\n", "")


def test_no_arbitrary_length_limit(src, out):
    long_text = "LONGWATERMARK " * 8  # 112 chars
    _watermark(src, out, text=long_text, font_size=4, rotation=0)
    with pymupdf.open(out) as doc:
        assert "LONGWATERMARK" in doc[0].get_text()


def test_validate_text_strips_only_surrounding_whitespace():
    assert watermark_engine.validate_text("  a  b  ") == "a  b"


# ---------------------------------------------------------------------------
# 4-7. Page selection
# ---------------------------------------------------------------------------

def test_all_pages_is_the_default_and_none_means_all(src, out):
    _watermark(src, out, page_indices=None)
    with pymupdf.open(out) as doc:
        assert all(_has_text(p) for p in doc)


def test_explicit_full_index_list_watermarks_every_page(src, out):
    _watermark(src, out, page_indices=list(range(5)))
    with pymupdf.open(out) as doc:
        assert all(_has_text(p) for p in doc)


def test_selected_pages_only(src, out):
    _watermark(src, out, page_indices=[1, 3])
    with pymupdf.open(out) as doc:
        assert [_has_text(p) for p in doc] == [False, True, False, True, False]


def test_page_indices_are_deduplicated_and_order_independent(src, out):
    _watermark(src, out, page_indices=[3, 1, 3, 1])
    with pymupdf.open(out) as doc:
        assert [_has_text(p) for p in doc] == [False, True, False, True, False]
        # Each selected page carries exactly ONE watermark, not two.
        assert len(_wm_lines(doc[1])) == 1
        assert len(_wm_lines(doc[3])) == 1


def test_multiple_page_ranges(tmp_path, out):
    src = _make_pdf(tmp_path / "ten.pdf", pages=10)
    indices = watermark_engine.resolve_pages_to_watermark("1-3,5,7-9", 10)
    assert indices == [0, 1, 2, 4, 6, 7, 8]

    _watermark(src, out, page_indices=indices)
    with pymupdf.open(out) as doc:
        assert [_has_text(p) for p in doc] == [
            True, True, True, False, True, False, True, True, True, False,
        ]


def test_overlapping_ranges_are_a_set(tmp_path):
    assert watermark_engine.resolve_pages_to_watermark("1-3,2-4", 6) == [0, 1, 2, 3]


@pytest.mark.parametrize("text", [
    " 1 - 3 , 5 ", "1-3,5", "  1-3,   5  ", "1 -3, 5", "1- 3,5 ",
])
def test_whitespace_in_page_ranges(text):
    assert watermark_engine.resolve_pages_to_watermark(text, 6) == [0, 1, 2, 4]


def test_page_range_parsing_reuses_split_engine():
    with patch.object(
        split_engine, "parse_page_ranges", wraps=split_engine.parse_page_ranges,
    ) as spy:
        watermark_engine.resolve_pages_to_watermark("2-3", 5)
    spy.assert_called_once_with("2-3", 5)


@pytest.mark.parametrize("text", ["", "   ", "abc", "0", "-1", "3-1", "1,,2", "1-2-3", "1.5"])
def test_invalid_page_range_is_rejected(text):
    with pytest.raises(split_engine.PageRangeError):
        watermark_engine.resolve_pages_to_watermark(text, 5)


@pytest.mark.parametrize("text", ["6", "1-6", "99", "5,6"])
def test_out_of_range_page_is_rejected(text):
    with pytest.raises(split_engine.PageRangeError):
        watermark_engine.resolve_pages_to_watermark(text, 5)


def test_page_range_errors_are_pdf_engine_errors():
    with pytest.raises(PDFEngineError):
        watermark_engine.resolve_pages_to_watermark("99", 5)


def test_out_of_range_index_in_engine_is_rejected_and_writes_nothing(src, out):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, page_indices=[0, 5])
    assert not out.exists()

    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, page_indices=[-1])
    assert not out.exists()


def test_empty_index_list_is_rejected(src, out):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, page_indices=[])
    assert not out.exists()


# ---------------------------------------------------------------------------
# 8-9. Watermark text validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", " ", "   ", "\t", "\n", " \t \n "])
def test_empty_or_whitespace_only_text_is_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_text(bad)


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_text_is_rejected_by_engine_and_writes_nothing(src, out, bad):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, text=bad)
    assert not out.exists()


@pytest.mark.parametrize("bad", [None, 5, b"abc", ["a"]])
def test_non_string_text_is_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_text(bad)


@pytest.mark.parametrize("bad", ["line1\nline2", "tab\there", "nul\x00", "bell\x07"])
def test_control_characters_are_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_text(bad)


@pytest.mark.parametrize("bad", [
    "\u673a\u5bc6", "\u041a\u041e\u041d\u0424", "\u201cquoted\u201d", "5\u20ac",
    "lock \U0001f512",
])
def test_unrepresentable_characters_are_rejected_not_silently_corrupted(bad):
    """The built-in Helvetica can't render these; writing them would
    silently produce placeholder dots (verified experimentally), so the
    engine refuses with a clear message instead."""
    with pytest.raises(WatermarkOptionsError) as exc_info:
        watermark_engine.validate_text(bad)
    assert "can't be rendered" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 12-14. Font size
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (36, 36.0), (36.5, 36.5), ("36", 36.0), (" 12.5 ", 12.5), ("0.5", 0.5),
    (1000, 1000.0),
])
def test_valid_font_sizes(value, expected):
    assert watermark_engine.validate_font_size(value) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", None, "abc", "12pt", "1,5", "nan", "NaN", "inf", "-inf",
    "Infinity", float("nan"), float("inf"), float("-inf"), True,
    "0", 0, 0.0, "-1", -1, -0.5, "-0", 1000.5, 1e9, "1e400",
])
def test_invalid_font_sizes_are_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_font_size(bad)


@pytest.mark.parametrize("bad", ["abc", 0, -5, float("nan"), float("inf")])
def test_invalid_font_size_in_engine_writes_nothing(src, out, bad):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, font_size=bad)
    assert not out.exists()


def test_font_size_is_applied(src, out):
    _watermark(src, out, font_size=20, rotation=0)
    _watermark(src, out.with_name("big.pdf"), font_size=60, rotation=0)

    with pymupdf.open(out) as small, pymupdf.open(out.with_name("big.pdf")) as big:
        small_w = _display_bbox(small[0], _wm_lines(small[0])[0]).width
        big_w = _display_bbox(big[0], _wm_lines(big[0])[0]).width
    assert big_w == pytest.approx(small_w * 3, rel=0.01)


# ---------------------------------------------------------------------------
# 15-19. Opacity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, 0.0), (100, 100.0), (30, 30.0), (0.5, 0.5), (99.9, 99.9),
    ("30", 30.0), ("30%", 30.0), (" 30 % ", 30.0), ("0", 0.0), ("100", 100.0),
])
def test_valid_opacities(value, expected):
    assert watermark_engine.validate_opacity(value) == expected


@pytest.mark.parametrize("bad", [
    "", "  ", None, "abc", "nan", "inf", "-inf", float("nan"), float("inf"),
    True, 100.01, 101, "101", 1000, -0.01, -1, "-1", -100, "1e400", "%",
])
def test_invalid_opacities_are_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_opacity(bad)


def test_opacity_converts_to_alpha():
    assert watermark_engine.opacity_to_alpha(0) == 0.0
    assert watermark_engine.opacity_to_alpha(100) == 1.0
    assert watermark_engine.opacity_to_alpha(30) == pytest.approx(0.3)
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.opacity_to_alpha(101)


@pytest.mark.parametrize("bad", [101, -1, float("nan"), "abc"])
def test_invalid_opacity_in_engine_writes_nothing(src, out, bad):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, opacity=bad)
    assert not out.exists()


@pytest.mark.parametrize("opacity,expected_gray", [
    (100, 0), (50, 128), (30, 179), (10, 230), (0, 255),
])
def test_opacity_is_reflected_in_the_rendering(tmp_path, out, opacity, expected_gray):
    """Black text on a blank page: the darkest rendered pixel reveals
    the constant alpha actually applied (0 = invisible, 100 = solid)."""
    blank = _make_pdf(tmp_path / "blank.pdf", pages=1, body_text=False, size=(300, 200))
    _watermark(blank, out, text="WWWW", font_size=60, rotation=0,
               opacity=opacity, color=(0, 0, 0))

    with pymupdf.open(out) as doc:
        assert abs(_min_gray(doc[0]) - expected_gray) <= 3


def test_opacity_zero_still_inserts_invisible_text(src, out):
    _watermark(src, out, opacity=0)
    with pymupdf.open(out) as doc:
        assert _has_text(doc[0])


# ---------------------------------------------------------------------------
# 20. Positions
# ---------------------------------------------------------------------------

def _expected_anchor(position, width, height, margin):
    """(x_kind, y_kind): which page edge/center each bbox edge should
    touch for a rotation-0 watermark."""
    col = position.split("_")[-1] if position != "center" else "center"
    row = position.split("_")[0] if position != "center" else "center"
    return col, row


@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("position", watermark_engine.POSITIONS)
def test_every_position_lands_in_the_right_place_on_the_displayed_page(
    tmp_path, position, page_rotation,
):
    src = _make_pdf(
        tmp_path / "p.pdf", pages=1, rotations=[page_rotation], body_text=False,
    )
    out = tmp_path / "o.pdf"
    _watermark(src, out, position=position, rotation=0, font_size=24)

    margin = watermark_engine.DEFAULT_MARGIN
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rotation == page_rotation
        bbox = _display_bbox(page, _wm_lines(page)[0])
        width, height = page.rect.width, page.rect.height

    col = "center" if position == "center" else position.split("_")[1]
    row = "center" if position == "center" else position.split("_")[0]
    if position in ("center_left", "center_right"):
        row = "center"
    if position in ("top_center", "bottom_center"):
        col = "center"

    if col == "left":
        assert bbox.x0 == pytest.approx(margin, abs=0.5)
    elif col == "right":
        assert bbox.x1 == pytest.approx(width - margin, abs=0.5)
    else:
        assert (bbox.x0 + bbox.x1) / 2 == pytest.approx(width / 2, abs=0.5)

    if row == "top":
        assert bbox.y0 == pytest.approx(margin, abs=0.5)
    elif row == "bottom":
        assert bbox.y1 == pytest.approx(height - margin, abs=0.5)
    else:
        assert (bbox.y0 + bbox.y1) / 2 == pytest.approx(height / 2, abs=0.5)

    # Never outside the page.
    assert bbox.x0 >= 0 and bbox.y0 >= 0
    assert bbox.x1 <= width and bbox.y1 <= height


@pytest.mark.parametrize("rotation", watermark_engine.ROTATIONS)
@pytest.mark.parametrize("position", watermark_engine.POSITIONS)
def test_rotated_watermark_stays_inside_the_page_at_every_position(
    tmp_path, position, rotation,
):
    src = _make_pdf(tmp_path / "p.pdf", pages=1, body_text=False)
    out = tmp_path / "o.pdf"
    _watermark(src, out, position=position, rotation=rotation)  # default 36pt

    with pymupdf.open(out) as doc:
        page = doc[0]
        bbox = _display_bbox(page, _wm_lines(page)[0])
        assert bbox.x0 >= watermark_engine.DEFAULT_MARGIN - 0.5
        assert bbox.y0 >= watermark_engine.DEFAULT_MARGIN - 0.5
        assert bbox.x1 <= page.rect.width - watermark_engine.DEFAULT_MARGIN + 0.5
        assert bbox.y1 <= page.rect.height - watermark_engine.DEFAULT_MARGIN + 0.5


def test_invalid_positions_are_rejected(src, out):
    for bad in ["", "middle", "Top Left", "TOP_LEFT", "top-left", None, 5, "left"]:
        with pytest.raises(WatermarkOptionsError):
            watermark_engine.validate_position(bad)
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, position="nowhere")
    assert not out.exists()


def test_positions_use_stable_identifiers_not_display_labels():
    for position in watermark_engine.POSITIONS:
        assert position == position.lower()
        assert " " not in position


# --- pure geometry ---------------------------------------------------------

def test_geometry_center_position_is_page_center():
    (sx, sy), (cx, cy) = watermark_engine.compute_watermark_geometry(
        600, 800, 200, 36, "center", 0,
    )
    assert (cx, cy) == (300, 400)
    assert sx == pytest.approx(300 - 100)


def test_geometry_left_and_right_use_margin_and_text_width():
    _, (left_cx, _) = watermark_engine.compute_watermark_geometry(
        600, 800, 200, 36, "center_left", 0, margin=30,
    )
    _, (right_cx, _) = watermark_engine.compute_watermark_geometry(
        600, 800, 200, 36, "center_right", 0, margin=30,
    )
    assert left_cx - 100 == pytest.approx(30)
    assert right_cx + 100 == pytest.approx(600 - 30)


def test_geometry_oversized_text_is_centered_rather_than_pushed_off_page():
    _, (cx, _) = watermark_engine.compute_watermark_geometry(
        100, 800, 400, 36, "center_right", 0,
    )
    assert cx == 50


@pytest.mark.parametrize("size", [(200, 300), (100, 100), (612, 792), (595, 842), (1000, 400)])
@pytest.mark.parametrize("position", watermark_engine.POSITIONS)
@pytest.mark.parametrize("rotation", watermark_engine.ROTATIONS)
def test_geometry_center_is_never_outside_the_page(size, position, rotation):
    width, height = size
    text_width = pymupdf.get_text_length("CONFIDENTIAL", "helv", 36)
    _, (cx, cy) = watermark_engine.compute_watermark_geometry(
        width, height, text_width, 36, position, rotation,
    )
    assert 0 <= cx <= width
    assert 0 <= cy <= height


# ---------------------------------------------------------------------------
# 21. Colors
# ---------------------------------------------------------------------------

def _dominant_ink_rgb(page):
    pix = page.get_pixmap(dpi=72, alpha=False)
    counts = Counter()
    samples = pix.samples
    for i in range(0, len(samples), 3):
        rgb = (samples[i], samples[i + 1], samples[i + 2])
        if rgb != (255, 255, 255):
            counts[rgb] += 1
    return counts.most_common(1)[0][0]


@pytest.mark.parametrize("name", sorted(watermark_engine.COLOR_PRESETS))
def test_every_preset_color_is_rendered_in_that_color(tmp_path, name):
    blank = _make_pdf(tmp_path / "blank.pdf", pages=1, body_text=False, size=(300, 200))
    out = tmp_path / "o.pdf"
    rgb = watermark_engine.COLOR_PRESETS[name]
    _watermark(blank, out, text="WWWW", font_size=70, rotation=0,
               opacity=100, color=rgb)

    with pymupdf.open(out) as doc:
        ink = _dominant_ink_rgb(doc[0])
    for channel, expected in zip(ink, rgb):
        assert abs(channel - round(expected * 255)) <= 3


def test_custom_color_is_rendered(tmp_path):
    blank = _make_pdf(tmp_path / "blank.pdf", pages=1, body_text=False, size=(300, 200))
    out = tmp_path / "o.pdf"
    _watermark(blank, out, text="WWWW", font_size=70, rotation=0,
               opacity=100, color=(1.0, 0.0, 1.0))
    with pymupdf.open(out) as doc:
        ink = _dominant_ink_rgb(doc[0])
    assert ink[0] > 250 and ink[1] < 5 and ink[2] > 250


def test_color_is_written_as_normalized_rgb_in_the_content_stream(src, out):
    _watermark(src, out, color=(0.25, 0.5, 0.75))
    with pymupdf.open(out) as doc:
        content = doc[0].read_contents().decode("latin-1")
    # PyMuPDF writes numbers without a leading zero (".25 .5 .75").
    assert ".25 .5 .75 rg" in content


@pytest.mark.parametrize("value", [(0, 0, 0), (1, 1, 1), (0.1, 0.2, 0.3), [0.5, 0.5, 0.5]])
def test_valid_colors(value):
    result = watermark_engine.validate_color(value)
    assert isinstance(result, tuple) and len(result) == 3


@pytest.mark.parametrize("bad", [
    None, "red", "#ff0000", (1, 0), (1, 0, 0, 0), (1.1, 0, 0), (-0.1, 0, 0),
    (float("nan"), 0, 0), (float("inf"), 0, 0), ("1", 0, 0), (True, 0, 0), 5, (),
])
def test_invalid_colors_are_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_color(bad)


def test_invalid_color_in_engine_writes_nothing(src, out):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, color=(2, 0, 0))
    assert not out.exists()


def test_color_from_rgb255():
    assert watermark_engine.color_from_rgb255(255, 0, 51) == (1.0, 0.0, 0.2)
    for bad in [(256, 0, 0), (-1, 0, 0), (1.5, 0, 0), (True, 0, 0)]:
        with pytest.raises(WatermarkOptionsError):
            watermark_engine.color_from_rgb255(*bad)


# ---------------------------------------------------------------------------
# 22. Rotation values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rotation", watermark_engine.ROTATIONS)
def test_every_supported_rotation_is_accepted_and_applied(src, out, rotation):
    assert watermark_engine.validate_rotation(rotation) == rotation
    _watermark(src, out, rotation=rotation, page_indices=[0])

    with pymupdf.open(out) as doc:
        line = _wm_lines(doc[0])[0]
        assert _angle_close(_display_angle(doc[0], line), rotation)


@pytest.mark.parametrize("value,expected", [
    ("45", 45), (" 90 ", 90), ("45\u00b0", 45), (45.0, 45), ("0", 0), ("315", 315),
])
def test_rotation_accepts_numeric_strings(value, expected):
    assert watermark_engine.validate_rotation(value) == expected


@pytest.mark.parametrize("bad", [
    "", None, "abc", "nan", "inf", float("nan"), float("inf"), True, 50, 360, -45,
    720, "44", 45.5, "1e400", 30, 46,
])
def test_unsupported_rotations_are_rejected(bad):
    with pytest.raises(WatermarkOptionsError):
        watermark_engine.validate_rotation(bad)


def test_invalid_rotation_in_engine_writes_nothing(src, out):
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, rotation=50)
    assert not out.exists()


# ---------------------------------------------------------------------------
# 23-28. Watermark rotation vs the page's own rotation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("rotation", watermark_engine.ROTATIONS)
def test_watermark_rotation_is_relative_to_the_displayed_page(
    tmp_path, page_rotation, rotation,
):
    """The heart of the spec's "two separate concepts" requirement: a
    page rotated 90 degrees with a 45 degree watermark must show a 45
    degree watermark ON THE DISPLAYED PAGE (not 135, not 45 in raw
    content space), centered on the displayed page."""
    src = _make_pdf(tmp_path / "p.pdf", pages=1, rotations=[page_rotation],
                    body_text=False, size=(400, 600))
    out = tmp_path / "o.pdf"
    _watermark(src, out, rotation=rotation)

    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rotation == page_rotation  # page's own rotation untouched
        line = _wm_lines(page)[0]
        assert _angle_close(_display_angle(page, line), rotation)

        bbox = _display_bbox(page, line)
        assert (bbox.x0 + bbox.x1) / 2 == pytest.approx(page.rect.width / 2, abs=0.5)
        assert (bbox.y0 + bbox.y1) / 2 == pytest.approx(page.rect.height / 2, abs=0.5)


def test_45_degree_watermark_on_90_degree_page_is_45_when_displayed(tmp_path):
    src = _make_pdf(tmp_path / "p.pdf", pages=1, rotations=[90], body_text=False)
    out = tmp_path / "o.pdf"
    _watermark(src, out, rotation=45)

    with pymupdf.open(out) as doc:
        page = doc[0]
        line = _wm_lines(page)[0]
        assert _angle_close(_display_angle(page, line), 45)
        # ...while in raw (unrotated) content space it is 45 + 90 = 135:
        dx, dy = line["dir"]
        raw = math.degrees(math.atan2(-dy, dx)) % 360
        assert _angle_close(raw, 135)


@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("rotation", [0, 45, 90, 135])
def test_rendered_ink_matches_intended_position_and_orientation(
    tmp_path, page_rotation, rotation,
):
    """Independent of text extraction: render the page and measure where
    the ink actually landed (coarse tolerances, pure Python)."""
    src = _make_pdf(tmp_path / "p.pdf", pages=1, rotations=[page_rotation],
                    body_text=False, size=(300, 400))
    out = tmp_path / "o.pdf"
    _watermark(src, out, text="WATERMARKS", font_size=40, rotation=rotation,
               opacity=100, color=(0, 0, 0))

    with pymupdf.open(out) as doc:
        page = doc[0]
        cx, cy, axis, count = _render_stats(page)
        width, height = page.rect.width, page.rect.height

    assert count > 200
    assert abs(cx - width / 2) < 8
    assert abs(cy - height / 2) < 8
    assert _axis_close(axis, rotation % 180)


@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("position", ["top_left", "bottom_right", "center_left", "top_center"])
def test_rendered_position_is_in_the_intended_displayed_region(
    tmp_path, page_rotation, position,
):
    """The Phase 20 coordinate mistake, guarded: for every page rotation
    the watermark must be in the intended visual region."""
    src = _make_pdf(tmp_path / "p.pdf", pages=1, rotations=[page_rotation],
                    body_text=False, size=(300, 400))
    out = tmp_path / "o.pdf"
    _watermark(src, out, text="WATERMARKS", font_size=14, rotation=0,
               position=position, opacity=100, color=(0, 0, 0))

    with pymupdf.open(out) as doc:
        page = doc[0]
        cx, cy, _axis, _count = _render_stats(page)
        width, height = page.rect.width, page.rect.height

    if position.endswith("_left"):
        assert cx < width / 3
    elif position.endswith("_right"):
        assert cx > width * 2 / 3
    else:
        assert width / 3 < cx < width * 2 / 3
    if position.startswith("top_"):
        assert cy < height / 3
    elif position.startswith("bottom_"):
        assert cy > height * 2 / 3
    else:
        assert height / 3 < cy < height * 2 / 3


@pytest.mark.parametrize("box", ["cropbox", "mediabox_offset", "both"])
@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
def test_pages_with_offset_crop_or_media_boxes(tmp_path, page_rotation, box):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    if box in ("mediabox_offset", "both"):
        page.set_mediabox(pymupdf.Rect(-100, -50, 300, 550))
    if box in ("cropbox", "both"):
        crop = (50, 100, 350, 400) if box == "cropbox" else (60, 70, 300, 350)
        page.set_cropbox(pymupdf.Rect(*crop))
    page.set_rotation(page_rotation)
    src = tmp_path / "p.pdf"
    doc.save(src)
    doc.close()
    out = tmp_path / "o.pdf"

    _watermark(src, out, text="WATERMARKS", font_size=30, rotation=45,
               opacity=100, color=(0, 0, 0))
    with pymupdf.open(out) as result:
        page = result[0]
        cx, cy, axis, _count = _render_stats(page)
        assert abs(cx - page.rect.width / 2) < 8
        assert abs(cy - page.rect.height / 2) < 8
        assert _axis_close(axis, 45)


# ---------------------------------------------------------------------------
# 29-33. Selected / unselected pages; count, order, rotations preserved
# ---------------------------------------------------------------------------

def test_selected_pages_are_watermarked_and_unselected_are_byte_for_byte_unchanged(
    src, out,
):
    _watermark(src, out, page_indices=[0, 2])

    with pymupdf.open(src) as original, pymupdf.open(out) as result:
        for index in range(5):
            if index in (0, 2):
                assert _has_text(result[index])
                assert len(_wm_lines(result[index])) == 1
            else:
                assert not _has_text(result[index])
                # Not just "no watermark text": the page's content is
                # exactly what it was.
                assert result[index].read_contents() == original[index].read_contents()


def test_page_count_and_order_preserved(tmp_path, out):
    src = _make_pdf(tmp_path / "s.pdf", pages=7)
    _watermark(src, out, page_indices=[2, 4])

    with pymupdf.open(out) as doc:
        assert doc.page_count == 7
        for i, page in enumerate(doc):
            assert page.get_text().split()[0] == f"BODY{i + 1}"


def test_page_rotations_preserved(tmp_path, out):
    rotations = [0, 90, 180, 270, 90]
    src = _make_pdf(tmp_path / "s.pdf", pages=5, rotations=rotations)
    _watermark(src, out, page_indices=[0, 1, 2])

    with pymupdf.open(out) as doc:
        assert [p.rotation for p in doc] == rotations


def test_page_sizes_preserved(tmp_path, out):
    doc = pymupdf.open()
    doc.new_page(width=200, height=300)
    doc.new_page(width=595, height=842)
    doc.new_page(width=1000, height=400)
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out, font_size=14)
    with pymupdf.open(src) as before, pymupdf.open(out) as after:
        assert [tuple(p.mediabox) for p in after] == [tuple(p.mediabox) for p in before]


# ---------------------------------------------------------------------------
# 34. Source immutability
# ---------------------------------------------------------------------------

def test_source_is_untouched_on_success(tmp_path, out):
    rotations = [0, 90, 180]
    src = _make_pdf(tmp_path / "s.pdf", pages=3, rotations=rotations)
    before_bytes = src.read_bytes()
    before_mtime = src.stat().st_mtime_ns

    _watermark(src, out)

    assert src.exists()
    assert src.read_bytes() == before_bytes
    assert src.stat().st_mtime_ns == before_mtime
    with pymupdf.open(src) as doc:
        assert doc.page_count == 3
        assert [p.rotation for p in doc] == rotations
        assert [p.get_text().split()[0] for p in doc] == ["BODY1", "BODY2", "BODY3"]
        assert not any(_has_text(p) for p in doc)


def test_source_is_untouched_on_failure(src, out):
    before = src.read_bytes()
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, out, opacity=500)
    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=PDFEngineError("boom")):
        with pytest.raises(PDFEngineError):
            _watermark(src, out)
    assert src.read_bytes() == before


def test_output_may_not_be_the_source_file(src):
    before = src.read_bytes()
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, src)
    assert src.read_bytes() == before


def test_output_may_not_be_the_source_via_a_different_spelling(src):
    before = src.read_bytes()
    sneaky = src.parent / "." / src.name
    with pytest.raises(WatermarkOptionsError):
        _watermark(src, sneaky)
    assert src.read_bytes() == before


# ---------------------------------------------------------------------------
# 35-37. Content preservation: blank, text, image, vector pages
# ---------------------------------------------------------------------------

def test_blank_pages(tmp_path, out):
    src = _make_pdf(tmp_path / "blank.pdf", pages=3, body_text=False)
    _watermark(src, out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3
        assert all(_wm_lines(p) for p in doc)


def test_text_pages_keep_their_text_exactly(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "The quick brown fox", fontsize=12)
    page.insert_text((72, 130), "jumps over the lazy dog", fontsize=12)
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out)
    with pymupdf.open(src) as before, pymupdf.open(out) as after:
        original_text = before[0].get_text()
        result_text = after[0].get_text()
    assert original_text.strip() in result_text
    assert "CONFIDENTIAL" not in original_text


def _solid_png_pixmap(width, height, rgb):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, rgb)
    return pix


def test_image_pages_keep_their_image_and_are_not_rasterized(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    pix = _solid_png_pixmap(100, 100, (10, 200, 30))
    page.insert_image(pymupdf.Rect(250, 250, 350, 350), pixmap=pix)
    page.insert_text((50, 60), "Caption", fontsize=12)
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out, position="top_left", opacity=100, font_size=20, rotation=0)

    with pymupdf.open(src) as before, pymupdf.open(out) as after:
        assert len(after[0].get_images()) == len(before[0].get_images()) == 1
        # The original image is byte-identical (not re-encoded)...
        before_img = before.extract_image(before[0].get_images()[0][0])
        after_img = after.extract_image(after[0].get_images()[0][0])
        assert before_img["image"] == after_img["image"]
        # ...the page text is still real text, and the image region
        # renders exactly as before (the watermark is elsewhere).
        assert "Caption" in after[0].get_text()
        assert _has_text(after[0])
        clip = pymupdf.Rect(260, 260, 340, 340)
        assert (
            before[0].get_pixmap(dpi=72, clip=clip).samples
            == after[0].get_pixmap(dpi=72, clip=clip).samples
        )
    # A rasterized rebuild would balloon the file; an overlay adds ~1KB.
    assert out.stat().st_size < src.stat().st_size + 20_000


def test_vector_content_is_preserved(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 300, 300, 400), color=(1, 0, 0), fill=(0.9, 0.9, 0))
    page.draw_line((50, 500), (500, 520), color=(0, 0, 1), width=2)
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out)
    with pymupdf.open(src) as before, pymupdf.open(out) as after:
        assert len(after[0].get_drawings()) == len(before[0].get_drawings()) == 2


def test_original_content_stream_is_kept_and_watermark_is_appended(src, out):
    """Overlay, not replacement: the original content stream object is
    untouched and a NEW stream is added after it."""
    with pymupdf.open(src) as before:
        before_contents = before[0].get_contents()
        before_bytes = before[0].read_contents()

    _watermark(src, out, page_indices=[0])

    with pymupdf.open(out) as after:
        after_contents = after[0].get_contents()
        after_bytes = after[0].read_contents()
    assert len(after_contents) == len(before_contents) + 1
    assert after_bytes.startswith(before_bytes.rstrip()[:200])
    assert len(after_bytes) > len(before_bytes)


def test_watermark_is_drawn_above_existing_content(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(pymupdf.Rect(0, 0, 300, 200), color=(1, 1, 1), fill=(1, 1, 1))
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out, text="WWWW", font_size=70, rotation=0,
               opacity=100, color=(0, 0, 0))
    with pymupdf.open(out) as result:
        assert _min_gray(result[0]) < 10  # visible over the opaque white rect


# ---------------------------------------------------------------------------
# 38. Output validity
# ---------------------------------------------------------------------------

def test_output_is_a_valid_reopenable_pdf(src, out):
    _watermark(src, out)
    pdf_engine.validate_pdf(out)
    with pymupdf.open(out) as doc:
        assert doc.is_pdf
        assert doc.page_count == 5
        assert not doc.needs_pass
        for page in doc:
            page.get_pixmap(dpi=36)  # renders without error


def test_progress_callback_receives_real_messages(src, out):
    messages = []
    _watermark(src, out, progress_callback=messages.append)
    assert messages == ["Adding watermark...", "Saving...", "Done."]


def test_no_progress_callback_is_fine(src, out):
    _watermark(src, out, progress_callback=None)
    assert out.exists()


def test_returns_the_output_path(src, out):
    assert _watermark(src, out) == out
    assert isinstance(_watermark(src, str(out) + "2.pdf"), Path)


# ---------------------------------------------------------------------------
# 39-41. Output naming + collisions (file_manager)
# ---------------------------------------------------------------------------

def test_output_naming_default(tmp_path):
    result = file_manager.generate_watermarked_output_path(
        tmp_path / "document.pdf", tmp_path,
    )
    assert result == tmp_path / "document_watermarked.pdf"


def test_output_naming_preserves_spaces_and_unicode(tmp_path):
    assert file_manager.generate_watermarked_output_path(
        tmp_path / "my report \u00e9.pdf", tmp_path,
    ).name == "my report \u00e9_watermarked.pdf"


def test_output_naming_sanitizes_like_every_other_helper(tmp_path):
    result = file_manager.generate_watermarked_output_path(Path("CON.pdf"), tmp_path)
    assert result.name == "_CON_watermarked.pdf" or result.name.endswith("_watermarked.pdf")
    assert result.parent == tmp_path


def test_output_naming_uppercase_extension_source(tmp_path):
    assert file_manager.generate_watermarked_output_path(
        tmp_path / "Doc.PDF", tmp_path,
    ).name == "Doc_watermarked.pdf"


def test_collision_handling_increments(tmp_path):
    (tmp_path / "document_watermarked.pdf").write_bytes(b"one")
    assert file_manager.generate_watermarked_output_path(
        tmp_path / "document.pdf", tmp_path,
    ).name == "document_watermarked (1).pdf"

    (tmp_path / "document_watermarked (1).pdf").write_bytes(b"two")
    assert file_manager.generate_watermarked_output_path(
        tmp_path / "document.pdf", tmp_path,
    ).name == "document_watermarked (2).pdf"

    (tmp_path / "document_watermarked (2).pdf").write_bytes(b"three")
    assert file_manager.generate_watermarked_output_path(
        tmp_path / "document.pdf", tmp_path,
    ).name == "document_watermarked (3).pdf"


def test_collision_handling_fills_the_first_free_slot(tmp_path):
    (tmp_path / "document_watermarked.pdf").write_bytes(b"one")
    (tmp_path / "document_watermarked (2).pdf").write_bytes(b"three")
    assert file_manager.generate_watermarked_output_path(
        tmp_path / "document.pdf", tmp_path,
    ).name == "document_watermarked (1).pdf"


def test_generated_path_never_exists(tmp_path):
    for i in range(4):
        path = file_manager.generate_watermarked_output_path(
            tmp_path / "document.pdf", tmp_path,
        )
        assert not path.exists()
        path.write_bytes(b"x")


def test_existing_outputs_are_never_overwritten(tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=2)
    sentinel = {}
    for i in range(3):
        target = file_manager.generate_watermarked_output_path(src, tmp_path)
        watermark_engine.add_watermark(src, target, text=WM)
        sentinel[target.name] = target.read_bytes()

    assert sorted(sentinel) == [
        "document_watermarked (1).pdf",
        "document_watermarked (2).pdf",
        "document_watermarked.pdf",
    ]
    for name, data in sentinel.items():
        assert (tmp_path / name).read_bytes() == data


def test_collision_logic_is_shared_not_duplicated():
    with patch.object(
        file_manager, "_find_collision_free_path", wraps=file_manager._find_collision_free_path,
    ) as spy:
        file_manager.generate_watermarked_output_path(Path("x.pdf"), Path("."))
    spy.assert_called_once_with(Path("."), "x_watermarked")


# ---------------------------------------------------------------------------
# 42. Metadata (and other document-level data) preservation
# ---------------------------------------------------------------------------

def test_metadata_is_preserved(tmp_path, out):
    meta = {
        "title": "Quarterly Report", "author": "A. Person",
        "subject": "Finance", "keywords": "q3, finance",
    }
    src = _make_pdf(tmp_path / "s.pdf", pages=2, metadata=meta)
    _watermark(src, out)

    with pymupdf.open(out) as doc:
        for key, value in meta.items():
            assert doc.metadata[key] == value


def test_outline_is_preserved(tmp_path, out):
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.set_toc([[1, "Intro", 1], [1, "Middle", 2], [2, "Sub", 3]])
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out)
    with pymupdf.open(out) as result:
        assert result.get_toc() == [[1, "Intro", 1], [1, "Middle", 2], [2, "Sub", 3]]


def test_links_and_annotations_are_preserved(tmp_path, out):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "click", fontsize=12)
    page.insert_link({
        "kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 110, 105),
        "uri": "https://example.com/",
    })
    page.add_text_annot((300, 300), "a note")
    src = tmp_path / "s.pdf"
    doc.save(src)
    doc.close()

    _watermark(src, out)
    with pymupdf.open(out) as result:
        assert [l["uri"] for l in result[0].get_links()] == ["https://example.com/"]
        assert len(list(result[0].annots())) == 1


# ---------------------------------------------------------------------------
# 43-45. Error handling
# ---------------------------------------------------------------------------

def test_corrupt_pdf(tmp_path, out):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)
    with pytest.raises(InvalidPDFError):
        _watermark(corrupt, out)
    assert not out.exists()


def test_truncated_pdf(tmp_path, out, src):
    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(src.read_bytes()[:40])
    with pytest.raises(PDFEngineError):
        _watermark(truncated, out)
    assert not out.exists()


def test_missing_pdf(tmp_path, out):
    with pytest.raises(InvalidPDFError):
        _watermark(tmp_path / "missing.pdf", out)
    assert not out.exists()


def test_non_pdf_extension(tmp_path, out):
    other = tmp_path / "notes.txt"
    other.write_text("hello")
    with pytest.raises(InvalidPDFError):
        _watermark(other, out)


def test_directory_as_source(tmp_path, out):
    folder = tmp_path / "folder.pdf"
    folder.mkdir()
    with pytest.raises(InvalidPDFError):
        _watermark(folder, out)


def test_output_failure_destination_folder_cannot_be_created(src, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a folder")
    bad_output = blocker / "sub" / "out.pdf"

    before = src.read_bytes()
    with pytest.raises(PDFEngineError) as exc_info:
        _watermark(src, bad_output)
    assert "Traceback" not in str(exc_info.value)
    assert src.read_bytes() == before
    assert blocker.read_text() == "i am a file, not a folder"


def test_permission_error_becomes_a_clean_engine_error(src, out):
    with patch("pymupdf.Document.save", side_effect=PermissionError(13, "Permission denied")):
        with pytest.raises(PDFEngineError) as exc_info:
            _watermark(src, out)
    assert "Permission denied" in str(exc_info.value)
    assert not out.exists()


def test_disk_full_becomes_a_clean_engine_error(src, out):
    with patch("pymupdf.Document.save", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(PDFEngineError) as exc_info:
            _watermark(src, out)
    assert "No space left on device" in str(exc_info.value)
    assert not out.exists()


def test_unexpected_pymupdf_failure_is_wrapped_without_traceback(src, out):
    with patch("pymupdf.Page.insert_text", side_effect=RuntimeError("engine exploded")):
        with pytest.raises(PDFEngineError) as exc_info:
            _watermark(src, out)
    assert "document.pdf" in str(exc_info.value)
    assert "Traceback" not in str(exc_info.value)
    assert not out.exists()


def test_all_engine_errors_are_pdf_engine_errors():
    assert issubclass(WatermarkOptionsError, PDFEngineError)
    assert issubclass(EncryptedPDFError, PDFEngineError)


# ---------------------------------------------------------------------------
# 46. Atomic save
# ---------------------------------------------------------------------------

def test_uses_the_existing_atomic_save(src, out):
    with patch.object(
        pdf_engine, "_atomic_save_pdf", wraps=pdf_engine._atomic_save_pdf,
    ) as spy:
        _watermark(src, out)
    spy.assert_called_once()
    assert spy.call_args.args[1] == out
    assert out.exists()


def test_failed_save_leaves_existing_destination_untouched_and_no_temp_files(src, tmp_path):
    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"precious existing bytes")

    with patch("pymupdf.Document.save", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(PDFEngineError):
            _watermark(src, destination)

    assert destination.read_bytes() == b"precious existing bytes"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")]
    assert leftovers == []


def test_no_temp_files_left_after_success(src, tmp_path, out):
    _watermark(src, out)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")] == []


def test_validation_failures_never_reach_the_save_step(src, out):
    with patch.object(pdf_engine, "_atomic_save_pdf") as spy:
        for kwargs in (
            {"text": ""}, {"font_size": 0}, {"opacity": 101}, {"rotation": 50},
            {"position": "x"}, {"color": (9, 9, 9)}, {"page_indices": []},
            {"page_indices": [99]},
        ):
            with pytest.raises(WatermarkOptionsError):
                _watermark(src, out, **kwargs)
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# 47. Repeated operations
# ---------------------------------------------------------------------------

def test_repeated_watermark_operations_accumulate(tmp_path):
    src = _make_pdf(tmp_path / "s.pdf", pages=2)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"

    _watermark(src, first, text="FIRSTMARK", rotation=45)
    _watermark(first, second, text="SECONDMARK", rotation=0, position="top_left")

    with pymupdf.open(second) as doc:
        assert doc.page_count == 2
        for page in doc:
            text = page.get_text()
            assert "FIRSTMARK" in text and "SECONDMARK" in text
            assert "BODY" in text
    with pymupdf.open(first) as doc:
        assert not any("SECONDMARK" in p.get_text() for p in doc)


def test_same_watermark_applied_twice_appears_twice(tmp_path):
    src = _make_pdf(tmp_path / "s.pdf", pages=1)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _watermark(src, first)
    _watermark(first, second)
    with pymupdf.open(second) as doc:
        assert len(_wm_lines(doc[0])) == 2


def test_repeated_calls_do_not_interfere(src, tmp_path):
    for i in range(3):
        target = tmp_path / f"out{i}.pdf"
        _watermark(src, target, text=f"RUN{i}MARK")
        with pymupdf.open(target) as doc:
            assert "RUN%dMARK" % i in doc[0].get_text()


# ---------------------------------------------------------------------------
# 48. Encrypted PDFs
# ---------------------------------------------------------------------------

def _make_encrypted(path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "SECRET", fontsize=14)
    doc.save(
        path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="userpass", owner_pw="ownerpass",
    )
    doc.close()
    return Path(path)


def test_encrypted_pdf_is_rejected_clearly(tmp_path, out):
    encrypted = _make_encrypted(tmp_path / "locked.pdf")
    before = encrypted.read_bytes()

    with pytest.raises(EncryptedPDFError) as exc_info:
        _watermark(encrypted, out)

    assert "password" in str(exc_info.value).lower()
    assert "locked.pdf" in str(exc_info.value)
    assert not out.exists()
    assert encrypted.read_bytes() == before  # never touched, never bypassed


def test_encrypted_pdf_never_gets_a_password_attempt(tmp_path, out):
    encrypted = _make_encrypted(tmp_path / "locked.pdf")
    with patch("pymupdf.Document.authenticate") as auth:
        with pytest.raises(EncryptedPDFError):
            _watermark(encrypted, out)
    auth.assert_not_called()


def test_encrypted_source_is_rejected_by_validate_before_any_option_work(tmp_path, out):
    encrypted = _make_encrypted(tmp_path / "locked.pdf")
    with patch.object(pdf_engine, "_atomic_save_pdf") as spy:
        with pytest.raises(EncryptedPDFError):
            _watermark(encrypted, out)
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# Misc: module hygiene
# ---------------------------------------------------------------------------

def test_engine_does_not_import_tkinter():
    source = (Path(__file__).resolve().parent.parent / "watermark_engine.py").read_text(
        encoding="utf-8"
    )
    assert "import tkinter" not in source
    assert "from tkinter" not in source


def test_engine_reuses_existing_infrastructure():
    source = (Path(__file__).resolve().parent.parent / "watermark_engine.py").read_text(
        encoding="utf-8"
    )
    assert "split_engine.parse_page_ranges" in source
    assert "pdf_engine._atomic_save_pdf" in source
    assert "pdf_engine.validate_pdf" in source
    # ...and does not re-implement any of it.
    assert "os.replace" not in source
    assert "tempfile" not in source


def test_engine_never_rasterizes():
    source = (Path(__file__).resolve().parent.parent / "watermark_engine.py").read_text(
        encoding="utf-8"
    )
    assert "get_pixmap" not in source
    assert "insert_image" not in source
    assert "show_pdf_page" not in source
