"""
test_pdf_to_images_engine.py

Direct tests for pdf_to_images_engine.py (Phase 23): option validation,
page-selection resolution (and its deliberate order/duplicate semantics
vs. watermark_engine's own use of the same parser), the pure DPI-to-
matrix conversion, the render_pdf_to_images() engine itself (page
count/order/filenames, source immutability, output validity, rotation
handling, error handling, whole-batch rollback on partial failure,
collision handling, repeated conversion), and get_pdf_info() reuse via
pdf_engine.

Verification is content-based wherever practical: output images are
decoded and their dimensions/format/representative pixels are checked
against what the source page should produce, not just "the file
exists". Anti-aliased edges are avoided by testing solid-fill regions
and using generous, non-pixel-perfect tolerances.

Pure engine tests: no tkinter, no display required.
"""

import io
import math
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager
import pdf_engine
import pdf_to_images_engine as pe
import split_engine
from pdf_engine import EncryptedPDFError, InvalidPDFError, PDFEngineError
from pdf_to_images_engine import ImageOptionsError, PdfToImagesError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf(path, pages=3, rotations=None, size=(200, 300), colors=None):
    """A multi-page PDF; page i has a solid-fill rectangle covering most
    of the page in `colors[i]` (default: a distinct color per page) plus
    a text label "PAGE<n>", so pages are distinguishable both visually
    and via extracted text.
    """
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        color = colors[i] if colors else _distinct_color(i)
        page.draw_rect(
            pymupdf.Rect(10, 10, size[0] - 10, size[1] - 10),
            color=color, fill=color,
        )
        page.insert_text((15, size[1] - 5), f"PAGE{i + 1}", fontsize=8, color=(0, 0, 0))
        if rotations and rotations[i]:
            page.set_rotation(rotations[i])
    doc.save(path)
    doc.close()
    return Path(path)


def _distinct_color(i):
    palette = [
        (0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8),
        (0.8, 0.8, 0.1), (0.1, 0.8, 0.8), (0.8, 0.1, 0.8),
    ]
    return palette[i % len(palette)]


def _center_pixel(path):
    img = _open_image(path)
    return img.getpixel((img.width // 2, img.height // 2))


def _rgb_close(actual, expected, tol=20):
    return all(abs(a - e) <= tol for a, e in zip(actual[:3], expected[:3]))


def _open_image(path):
    """Open, fully decode, and return an independent in-memory copy of
    the image at `path` -- releasing the underlying file handle before
    returning (Pillow's file-based Image objects otherwise keep it open
    until garbage collection, which shows up as a ResourceWarning under
    this project's warnings-as-errors regression run)."""
    with Image.open(path) as img:
        img.load()
        image_format = img.format
        copy = img.copy()
    copy.format = image_format
    return copy


@pytest.fixture
def src(tmp_path):
    """A 5-page, distinctly-colored test PDF."""
    return _make_pdf(tmp_path / "document.pdf", pages=5)


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "output"


def _convert(src, out_dir, **kwargs):
    return pe.render_pdf_to_images(src, out_dir, **kwargs)


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

def test_documented_defaults():
    assert pe.DEFAULT_FORMAT == "png"
    assert pe.DEFAULT_DPI == 150.0
    assert pe.DEFAULT_JPEG_QUALITY == 90
    assert pe.FORMAT_CHOICES == ("png", "jpeg")
    assert pe.DPI_CHOICES == (72, 96, 150, 200, 300)
    assert pe.JPEG_QUALITY_CHOICES == (50, 60, 70, 80, 90, 95, 100)
    assert pe.MAX_DPI == 600.0


def test_defaults_are_all_valid():
    assert pe.validate_format(pe.DEFAULT_FORMAT)
    assert pe.validate_dpi(pe.DEFAULT_DPI) == pe.DEFAULT_DPI
    assert pe.validate_jpeg_quality(pe.DEFAULT_JPEG_QUALITY) == pe.DEFAULT_JPEG_QUALITY


# ---------------------------------------------------------------------------
# Option validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["png", "jpeg"])
def test_valid_formats(value):
    assert pe.validate_format(value) == value


@pytest.mark.parametrize("bad", ["", "PNG", "JPEG", "jpg", None, 5, "png "])
def test_invalid_formats_are_rejected(bad):
    with pytest.raises(ImageOptionsError):
        pe.validate_format(bad)


@pytest.mark.parametrize("value,expected", [
    (150, 150.0), ("150", 150.0), (" 96 ", 96.0), (72, 72.0), (600, 600.0),
    (0.5, 0.5), (1, 1.0),
])
def test_valid_dpi(value, expected):
    assert pe.validate_dpi(value) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", None, "abc", "nan", "inf", "-inf", float("nan"), float("inf"),
    True, 0, 0.0, "-1", -1, -50, 601, 1000, "1e400",
])
def test_invalid_dpi_is_rejected(bad):
    with pytest.raises(ImageOptionsError):
        pe.validate_dpi(bad)


def test_dpi_upper_bound_is_exactly_max_dpi():
    pe.validate_dpi(pe.MAX_DPI)  # exactly at the bound: fine
    with pytest.raises(ImageOptionsError):
        pe.validate_dpi(pe.MAX_DPI + 0.01)


@pytest.mark.parametrize("value,expected", [
    (90, 90), ("90", 90), (1, 1), (100, 100), ("100", 100), (50.0, 50),
])
def test_valid_jpeg_quality(value, expected):
    assert pe.validate_jpeg_quality(value) == expected


@pytest.mark.parametrize("bad", [
    "", "  ", None, "abc", "nan", "inf", float("nan"), float("inf"), True,
    0, -1, 101, 1000, "101", "0", 50.5, "1e400",
])
def test_invalid_jpeg_quality_is_rejected(bad):
    with pytest.raises(ImageOptionsError):
        pe.validate_jpeg_quality(bad)


@pytest.mark.parametrize("quality", [1, 100])
def test_jpeg_quality_boundaries_accepted(quality):
    assert pe.validate_jpeg_quality(quality) == quality


# ---------------------------------------------------------------------------
# Page-selection resolution
# ---------------------------------------------------------------------------

def test_all_pages_natural_order():
    assert pe.resolve_pages_to_render(None, 5) == [0, 1, 2, 3, 4]


def test_selected_single_page():
    assert pe.resolve_pages_to_render("3", 10) == [2]


def test_selected_page_range():
    assert pe.resolve_pages_to_render("3-5", 10) == [2, 3, 4]


def test_multiple_ranges_preserve_typed_order():
    assert pe.resolve_pages_to_render("3-5,8", 10) == [2, 3, 4, 7]


def test_reversed_token_order_is_preserved_not_sorted():
    """Deliberately follows parse_page_ranges()'s own documented
    semantics (typed order, not sorted) rather than inventing sorted
    output for this tool."""
    assert pe.resolve_pages_to_render("8,3-5", 10) == [7, 2, 3, 4]


def test_overlapping_ranges_produce_duplicate_indices_not_deduplicated():
    """The key documented divergence from
    watermark_engine.resolve_pages_to_watermark() (which sorts AND
    deduplicates the very same parser's output): here, a page mentioned
    twice must render twice, as two separate image files."""
    assert pe.resolve_pages_to_render("1-3,2-4", 10) == [0, 1, 2, 1, 2, 3]


def test_same_single_page_twice():
    assert pe.resolve_pages_to_render("2,2", 5) == [1, 1]


@pytest.mark.parametrize("text", [
    " 1 - 3 , 5 ", "1-3,5", "  1-3,   5  ", "1 -3, 5", "1- 3,5 ",
])
def test_whitespace_in_ranges(text):
    assert pe.resolve_pages_to_render(text, 6) == [0, 1, 2, 4]


@pytest.mark.parametrize("text", ["", "   ", "abc", "3-1", "1,,2", "1-2-3", "1.5"])
def test_invalid_range_is_rejected(text):
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render(text, 5)


def test_zero_page_is_rejected():
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render("0", 5)


def test_negative_page_is_rejected():
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render("-1", 5)


@pytest.mark.parametrize("text", ["6", "1-6", "99", "5,6"])
def test_out_of_range_page_is_rejected(text):
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render(text, 5)


def test_page_range_reuses_split_engine():
    with patch.object(
        split_engine, "parse_page_ranges", wraps=split_engine.parse_page_ranges,
    ) as spy:
        pe.resolve_pages_to_render("2-3", 5)
    spy.assert_called_once_with("2-3", 5)


def test_all_pages_on_zero_page_document_is_rejected():
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render(None, 0)


def test_page_range_errors_are_pdf_engine_errors():
    with pytest.raises(PDFEngineError):
        pe.resolve_pages_to_render("99", 5)


# ---------------------------------------------------------------------------
# DPI -> matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dpi,expected_scale", [
    (72, 1.0), (144, 2.0), (36, 0.5), (150, 150 / 72), (300, 300 / 72),
])
def test_dpi_to_matrix(dpi, expected_scale):
    matrix = pe.dpi_to_matrix(dpi)
    assert matrix.a == pytest.approx(expected_scale)
    assert matrix.d == pytest.approx(expected_scale)
    assert matrix.b == 0 and matrix.c == 0  # no shear/rotation


def test_dpi_to_matrix_produces_correct_pixel_dimensions(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    for dpi in (72, 96, 150):
        pix = page.get_pixmap(matrix=pe.dpi_to_matrix(dpi))
        assert pix.width == pytest.approx(200 * dpi / 72, abs=1)
        assert pix.height == pytest.approx(300 * dpi / 72, abs=1)
    doc.close()


# ---------------------------------------------------------------------------
# 1-4. Single/multi page, PNG/JPEG
# ---------------------------------------------------------------------------

def test_single_page_pdf_to_png(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "one.pdf", pages=1)
    result = _convert(source, out_dir, image_format="png")
    assert result["total_images"] == 1
    assert result["output_paths"][0].suffix == ".png"
    assert result["output_paths"][0].exists()


def test_multi_page_pdf_to_png(src, out_dir):
    result = _convert(src, out_dir, image_format="png")
    assert result["total_images"] == 5
    assert all(p.suffix == ".png" for p in result["output_paths"])


def test_single_page_pdf_to_jpeg(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "one.pdf", pages=1)
    result = _convert(source, out_dir, image_format="jpeg")
    assert result["total_images"] == 1
    assert result["output_paths"][0].suffix == ".jpg"


def test_multi_page_pdf_to_jpeg(src, out_dir):
    result = _convert(src, out_dir, image_format="jpeg", jpeg_quality=80)
    assert result["total_images"] == 5
    assert all(p.suffix == ".jpg" for p in result["output_paths"])


def test_mixed_pdf_content(tmp_path, out_dir):
    doc = pymupdf.open()
    blank = doc.new_page(width=200, height=200)
    text_page = doc.new_page(width=200, height=200)
    text_page.insert_text((20, 100), "Hello, world!", fontsize=16)
    img_page = doc.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.set_rect(pix.irect, (10, 200, 30))
    img_page.insert_image(pymupdf.Rect(50, 50, 150, 150), pixmap=pix)
    source = tmp_path / "mixed.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir)
    assert result["total_images"] == 3
    for path in result["output_paths"]:
        img = _open_image(path)
        assert img.size[0] > 0 and img.size[1] > 0


# ---------------------------------------------------------------------------
# 6-9. Page selection variants end-to-end
# ---------------------------------------------------------------------------

def test_all_pages_renders_every_page(src, out_dir):
    result = _convert(src, out_dir, page_indices=None)
    assert result["total_images"] == 5


def test_selected_single_page_end_to_end(src, out_dir):
    result = _convert(src, out_dir, page_indices=[2])
    assert result["total_images"] == 1
    assert "page_003" in result["output_paths"][0].name


def test_selected_page_ranges_end_to_end(src, out_dir):
    indices = pe.resolve_pages_to_render("2-4", 5)
    result = _convert(src, out_dir, page_indices=indices)
    assert result["total_images"] == 3
    names = sorted(p.name for p in result["output_paths"])
    assert names == [
        "document_page_002.png", "document_page_003.png", "document_page_004.png",
    ]


def test_multiple_ranges_end_to_end(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "ten.pdf", pages=10)
    indices = pe.resolve_pages_to_render("1-3,5,7-9", 10)
    result = _convert(source, out_dir, page_indices=indices)
    assert result["total_images"] == 7
    names = sorted(p.name for p in result["output_paths"])
    assert names == [
        "ten_page_001.png", "ten_page_002.png", "ten_page_003.png",
        "ten_page_005.png", "ten_page_007.png", "ten_page_008.png",
        "ten_page_009.png",
    ]


def test_whitespace_in_ranges_end_to_end(src, out_dir):
    indices = pe.resolve_pages_to_render(" 1 , 3 ", 5)
    result = _convert(src, out_dir, page_indices=indices)
    assert result["total_images"] == 2


# ---------------------------------------------------------------------------
# 11-14. Invalid / out-of-range page selection
# ---------------------------------------------------------------------------

def test_invalid_range_rejected_before_conversion():
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render("abc", 5)


def test_out_of_range_index_in_engine_is_rejected_and_writes_nothing(src, out_dir):
    with pytest.raises(PdfToImagesError):
        _convert(src, out_dir, page_indices=[0, 99])
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_negative_index_in_engine_is_rejected(src, out_dir):
    with pytest.raises(PdfToImagesError):
        _convert(src, out_dir, page_indices=[-1])


def test_empty_index_list_is_rejected(src, out_dir):
    with pytest.raises(PdfToImagesError):
        _convert(src, out_dir, page_indices=[])
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 15-17. Output page count / filenames / ordering
# ---------------------------------------------------------------------------

def test_output_page_count_matches_selection(src, out_dir):
    result = _convert(src, out_dir, page_indices=[0, 2, 4])
    assert result["total_images"] == 3
    assert len(result["output_paths"]) == 3


def test_output_filenames_use_actual_page_number(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "report.pdf", pages=12)
    indices = pe.resolve_pages_to_render("3-5,8", 12)
    result = _convert(source, out_dir, page_indices=indices)
    names = [p.name for p in result["output_paths"]]
    assert names == [
        "report_page_003.png", "report_page_004.png",
        "report_page_005.png", "report_page_008.png",
    ]


def test_output_ordering_matches_selection_order(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "d.pdf", pages=10)
    indices = pe.resolve_pages_to_render("8,3-5", 10)
    result = _convert(source, out_dir, page_indices=indices)
    names = [p.name for p in result["output_paths"]]
    assert names == [
        "d_page_008.png", "d_page_003.png", "d_page_004.png", "d_page_005.png",
    ]


def test_filename_padding_widens_for_large_page_counts(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "big.pdf", pages=3)
    # Manually simulate a document reporting 1234 total pages worth of
    # width by checking the private suffix helper directly (rendering
    # 1234 real pages would be slow and isn't necessary to prove the
    # padding rule).
    assert pe._format_page_suffix(7, 1234) == "_page_0007"
    assert pe._format_page_suffix(7, 42) == "_page_007"
    assert pe._format_page_suffix(7, 5) == "_page_007"


# ---------------------------------------------------------------------------
# 18-19. PNG / JPEG validity
# ---------------------------------------------------------------------------

def test_png_output_is_valid_png(src, out_dir):
    result = _convert(src, out_dir, image_format="png")
    for path in result["output_paths"]:
        img = _open_image(path)
        assert img.format == "PNG"


def test_jpeg_output_is_valid_jpeg(src, out_dir):
    result = _convert(src, out_dir, image_format="jpeg")
    for path in result["output_paths"]:
        img = _open_image(path)
        assert img.format == "JPEG"


def test_jpeg_output_has_no_alpha_channel(src, out_dir):
    result = _convert(src, out_dir, image_format="jpeg")
    img = _open_image(result["output_paths"][0])
    assert img.mode in ("RGB", "L", "CMYK")
    assert "A" not in img.mode


def test_png_output_files_are_non_zero_size(src, out_dir):
    result = _convert(src, out_dir, image_format="png")
    for path in result["output_paths"]:
        assert path.stat().st_size > 0


# ---------------------------------------------------------------------------
# 20-23. DPI
# ---------------------------------------------------------------------------

def test_default_dpi_used_when_not_specified(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "d.pdf", pages=1, size=(200, 300))
    result = _convert(source, out_dir)
    img = _open_image(result["output_paths"][0])
    expected_w = 200 * pe.DEFAULT_DPI / 72
    expected_h = 300 * pe.DEFAULT_DPI / 72
    assert img.size == (round(expected_w), round(expected_h))


@pytest.mark.parametrize("dpi", [72, 96, 200, 300])
def test_custom_dpi_changes_output_size(tmp_path, out_dir, dpi):
    source = _make_pdf(tmp_path / "d.pdf", pages=1, size=(200, 300))
    result = _convert(source, out_dir, dpi=dpi)
    img = _open_image(result["output_paths"][0])
    expected_w = 200 * dpi / 72
    expected_h = 300 * dpi / 72
    assert img.width == pytest.approx(expected_w, abs=1)
    assert img.height == pytest.approx(expected_h, abs=1)


@pytest.mark.parametrize("bad_dpi", [0, -10, "abc", float("nan"), float("inf")])
def test_invalid_dpi_rejected_and_writes_nothing(src, out_dir, bad_dpi):
    with pytest.raises(ImageOptionsError):
        _convert(src, out_dir, dpi=bad_dpi)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_excessively_high_dpi_rejected(src, out_dir):
    with pytest.raises(ImageOptionsError):
        _convert(src, out_dir, dpi=601)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_max_dpi_is_accepted(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "tiny.pdf", pages=1, size=(20, 20))
    result = _convert(source, out_dir, dpi=pe.MAX_DPI)
    assert result["total_images"] == 1


# ---------------------------------------------------------------------------
# 24-28. JPEG quality
# ---------------------------------------------------------------------------

def test_default_jpeg_quality_used(src, out_dir):
    """Mocking pymupdf.Pixmap.tobytes (a C-extension method) doesn't
    reliably preserve `self` binding through unittest.mock, so this is
    verified behaviorally instead: omitting jpeg_quality must produce
    byte-for-byte the same output as passing DEFAULT_JPEG_QUALITY
    explicitly.
    """
    implicit = _convert(src, out_dir / "implicit", page_indices=[0], image_format="jpeg")
    explicit = _convert(
        src, out_dir / "explicit", page_indices=[0], image_format="jpeg",
        jpeg_quality=pe.DEFAULT_JPEG_QUALITY,
    )
    assert (
        implicit["output_paths"][0].read_bytes()
        == explicit["output_paths"][0].read_bytes()
    )


def test_custom_jpeg_quality_is_applied(src, out_dir):
    low = _convert(src, out_dir / "low", page_indices=[0], image_format="jpeg", jpeg_quality=10)
    high = _convert(src, out_dir / "high", page_indices=[0], image_format="jpeg", jpeg_quality=100)
    low_size = low["output_paths"][0].stat().st_size
    high_size = high["output_paths"][0].stat().st_size
    assert high_size > low_size  # higher quality -> larger file, same content


@pytest.mark.parametrize("bad_quality", [0, 101, -1, "abc", float("nan")])
def test_invalid_jpeg_quality_rejected_and_writes_nothing(src, out_dir, bad_quality):
    with pytest.raises(ImageOptionsError):
        _convert(src, out_dir, image_format="jpeg", jpeg_quality=bad_quality)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


@pytest.mark.parametrize("quality", [1, 100])
def test_jpeg_quality_boundaries_end_to_end(src, out_dir, quality):
    result = _convert(src, out_dir, page_indices=[0], image_format="jpeg", jpeg_quality=quality)
    assert result["total_images"] == 1


def test_png_ignores_invalid_jpeg_quality(src, out_dir):
    """PNG is lossless -- an invalid jpeg_quality must never block a PNG
    render, since it's simply irrelevant to PNG output."""
    result = _convert(src, out_dir, image_format="png", jpeg_quality=999)
    assert result["total_images"] == 5


def test_png_ignores_jpeg_quality_value_entirely(src, out_dir):
    low = _convert(src, out_dir / "a", page_indices=[0], image_format="png", jpeg_quality=1)
    high = _convert(src, out_dir / "b", page_indices=[0], image_format="png", jpeg_quality=100)
    # PNG is lossless: identical content regardless of the (irrelevant)
    # jpeg_quality value.
    assert low["output_paths"][0].read_bytes() == high["output_paths"][0].read_bytes()


# ---------------------------------------------------------------------------
# 29-32. Page rotation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rotation,expected_corner", [
    (0, "top_left"), (90, "top_right"), (180, "bottom_right"), (270, "bottom_left"),
])
def test_page_rotation_rendered_correctly(tmp_path, out_dir, rotation, expected_corner):
    """A page with an asymmetric marker painted at its RAW top-left
    (content-space) corner must, once /Rotate is applied for normal
    display, show that marker at the correspondingly rotated corner of
    the rendered image -- get_pixmap() handles this internally (see
    this module's docstring); this end-to-end test guards the actual
    observable behavior against the exact rotation mapping verified
    experimentally: 0->top-left, 90->top-right, 180->bottom-right,
    270->bottom-left (clockwise, matching PDF's own /Rotate direction)."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.draw_rect(pymupdf.Rect(0, 0, 200, 300), color=(1, 1, 1), fill=(1, 1, 1))
    page.draw_rect(pymupdf.Rect(0, 0, 40, 40), color=(0, 0.6, 0), fill=(0, 0.6, 0))
    page.set_rotation(rotation)
    source = tmp_path / f"rot{rotation}.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0]).convert("RGB")
    w, h = img.size

    corners = {
        "top_left": (5, 5), "top_right": (w - 5, 5),
        "bottom_left": (5, h - 5), "bottom_right": (w - 5, h - 5),
    }
    marker_corner = corners.pop(expected_corner)
    assert _rgb_close(img.getpixel(marker_corner), (0, 153, 0), tol=40)
    for other in corners.values():
        assert _rgb_close(img.getpixel(other), (255, 255, 255), tol=10)


def test_rotated_page_pixmap_dimensions_swap(tmp_path, out_dir):
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.set_rotation(90)
    source = tmp_path / "r.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    assert img.size == (300, 200)


# ---------------------------------------------------------------------------
# 33-36. Cropbox / dimensions / orientation
# ---------------------------------------------------------------------------

def test_normal_page_dimensions(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "d.pdf", pages=1, size=(400, 600))
    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    assert img.size == (400, 600)


def test_portrait_page(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "p.pdf", pages=1, size=(200, 400))
    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    assert img.height > img.width


def test_landscape_page(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "l.pdf", pages=1, size=(400, 200))
    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    assert img.width > img.height


def test_cropbox_restricts_rendered_area(tmp_path, out_dir):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    page.draw_rect(pymupdf.Rect(0, 0, 400, 400), color=(1, 0, 0), fill=(1, 0, 0))
    page.set_cropbox(pymupdf.Rect(100, 100, 300, 300))
    source = tmp_path / "crop.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    assert img.size == (200, 200)  # only the cropbox area, not the full mediabox


# ---------------------------------------------------------------------------
# 37-39. Blank / text / image page content
# ---------------------------------------------------------------------------

def test_blank_page(tmp_path, out_dir):
    doc = pymupdf.open()
    doc.new_page(width=200, height=200)
    source = tmp_path / "blank.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    assert img.convert("RGB").getpixel((100, 100)) == (255, 255, 255)


def test_text_page_produces_non_blank_image(tmp_path, out_dir):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=100)
    page.insert_text((20, 50), "Some readable text here", fontsize=20)
    source = tmp_path / "text.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir, dpi=150)
    img = _open_image(result["output_paths"][0])
    pixels = img.convert("L").tobytes()
    assert min(pixels) < 200  # some dark (text) pixels exist
    assert max(pixels) > 200  # and some light (background) pixels too


def test_image_page_renders_the_embedded_image(tmp_path, out_dir):
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.set_rect(pix.irect, (20, 120, 220))
    page.insert_image(pymupdf.Rect(50, 50, 150, 150), pixmap=pix)
    source = tmp_path / "img.pdf"
    doc.save(source)
    doc.close()

    result = _convert(source, out_dir, dpi=72)
    img = _open_image(result["output_paths"][0])
    center = img.convert("RGB").getpixel((100, 100))
    assert _rgb_close(center, (20, 120, 220), tol=15)


# ---------------------------------------------------------------------------
# 40-41. Source immutability
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged(src, out_dir):
    before = src.read_bytes()
    _convert(src, out_dir)
    assert src.read_bytes() == before


def test_source_page_count_unchanged(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "d.pdf", pages=7)
    _convert(source, out_dir, page_indices=[0, 2])
    with pymupdf.open(source) as doc:
        assert doc.page_count == 7


def test_source_page_rotations_unchanged(tmp_path, out_dir):
    rotations = [0, 90, 180, 270]
    source = _make_pdf(tmp_path / "d.pdf", pages=4, rotations=rotations)
    _convert(source, out_dir)
    with pymupdf.open(source) as doc:
        assert [p.rotation for p in doc] == rotations


def test_source_mtime_unchanged(src, out_dir):
    before_mtime = src.stat().st_mtime_ns
    _convert(src, out_dir)
    assert src.stat().st_mtime_ns == before_mtime


def test_source_metadata_unchanged(tmp_path, out_dir):
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Original Title", "author": "Someone"})
    source = tmp_path / "meta.pdf"
    doc.save(source)
    doc.close()

    _convert(source, out_dir)
    with pymupdf.open(source) as doc2:
        assert doc2.metadata["title"] == "Original Title"
        assert doc2.metadata["author"] == "Someone"


# ---------------------------------------------------------------------------
# 42-43. Output can be reopened / correct dimensions
# ---------------------------------------------------------------------------

def test_output_can_be_reopened_and_decoded(src, out_dir):
    result = _convert(src, out_dir)
    for path in result["output_paths"]:
        img = _open_image(path)
        assert img.size[0] > 0 and img.size[1] > 0


def test_output_dimensions_are_sensible(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "d.pdf", pages=1, size=(612, 792))  # Letter
    result = _convert(source, out_dir, dpi=150)
    img = _open_image(result["output_paths"][0])
    assert img.width == pytest.approx(612 * 150 / 72, abs=1)
    assert img.height == pytest.approx(792 * 150 / 72, abs=1)


# ---------------------------------------------------------------------------
# 44-46. Missing / corrupt / encrypted source
# ---------------------------------------------------------------------------

def test_missing_source(tmp_path, out_dir):
    with pytest.raises(InvalidPDFError):
        _convert(tmp_path / "missing.pdf", out_dir)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_corrupt_pdf(tmp_path, out_dir):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)
    with pytest.raises(InvalidPDFError):
        _convert(corrupt, out_dir)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_directory_as_source(tmp_path, out_dir):
    folder = tmp_path / "folder.pdf"
    folder.mkdir()
    with pytest.raises(InvalidPDFError):
        _convert(folder, out_dir)


def _make_encrypted(path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "SECRET", fontsize=14)
    doc.save(
        path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="userpass", owner_pw="ownerpass",
    )
    doc.close()
    return Path(path)


def test_encrypted_pdf_is_rejected_clearly(tmp_path, out_dir):
    encrypted = _make_encrypted(tmp_path / "locked.pdf")
    before = encrypted.read_bytes()

    with pytest.raises(EncryptedPDFError) as exc_info:
        _convert(encrypted, out_dir)

    assert "password" in str(exc_info.value).lower()
    assert "locked.pdf" in str(exc_info.value)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
    assert encrypted.read_bytes() == before


def test_encrypted_pdf_never_gets_a_password_attempt(tmp_path, out_dir):
    encrypted = _make_encrypted(tmp_path / "locked.pdf")
    with patch("pymupdf.Document.authenticate") as auth:
        with pytest.raises(EncryptedPDFError):
            _convert(encrypted, out_dir)
    auth.assert_not_called()


# ---------------------------------------------------------------------------
# 47-49. Output folder / collisions / no overwrite
# ---------------------------------------------------------------------------

def test_output_folder_is_created_if_missing(src, out_dir):
    assert not out_dir.exists()
    _convert(src, out_dir)
    assert out_dir.exists()


def test_output_folder_reused_if_it_already_exists_with_unrelated_files(src, out_dir):
    out_dir.mkdir()
    (out_dir / "unrelated.txt").write_text("keep me")
    result = _convert(src, out_dir)
    assert result["total_images"] == 5
    assert (out_dir / "unrelated.txt").read_text() == "keep me"


def test_output_folder_cannot_be_created(src, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a folder")
    bad_output_dir = blocker / "sub"

    with pytest.raises(PDFEngineError) as exc_info:
        _convert(src, bad_output_dir)
    assert "Traceback" not in str(exc_info.value)
    assert blocker.read_text() == "i am a file, not a folder"


def test_output_collision_gets_a_counter_suffix(src, out_dir):
    out_dir.mkdir()
    (out_dir / "document_page_001.png").write_bytes(b"pre-existing content")

    result = _convert(src, out_dir, page_indices=[0])
    assert result["output_paths"][0].name == "document_page_001 (1).png"


def test_existing_file_is_never_overwritten(src, out_dir):
    out_dir.mkdir()
    sentinel = out_dir / "document_page_001.png"
    sentinel.write_bytes(b"do not touch me")

    _convert(src, out_dir, page_indices=[0])

    assert sentinel.read_bytes() == b"do not touch me"


def test_duplicate_page_selection_within_one_batch_gets_distinct_filenames(src, out_dir):
    indices = pe.resolve_pages_to_render("1-2,1", 5)  # renders page 1 twice
    result = _convert(src, out_dir, page_indices=indices)
    names = [p.name for p in result["output_paths"]]
    assert names == [
        "document_page_001.png", "document_page_002.png",
        "document_page_001 (1).png",
    ]
    assert len(set(names)) == 3  # genuinely three distinct files on disk


# ---------------------------------------------------------------------------
# 50. Partial failure cleanup
# ---------------------------------------------------------------------------

def test_partial_failure_removes_every_output_this_call_created(src, out_dir):
    real_write = pe._atomic_write_bytes
    call_count = {"n": 0}

    def flaky_write(data, output_path):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise OSError(28, "No space left on device")
        return real_write(data, output_path)

    with patch.object(pe, "_atomic_write_bytes", side_effect=flaky_write):
        with pytest.raises(Exception):
            _convert(src, out_dir, page_indices=[0, 1, 2, 3, 4])

    # Pages 1 and 2 succeeded before the simulated failure on page 3 --
    # both must have been rolled back, and page 3 itself never left a
    # partial file behind either.
    remaining = list(out_dir.iterdir()) if out_dir.exists() else []
    assert remaining == []


def test_partial_failure_does_not_touch_pre_existing_unrelated_files(src, out_dir):
    out_dir.mkdir()
    (out_dir / "keep_this.png").write_bytes(b"unrelated pre-existing file")

    with patch("pymupdf.Pixmap.tobytes", side_effect=RuntimeError("boom")):
        with pytest.raises(Exception):
            _convert(src, out_dir, page_indices=[0, 1, 2])

    assert (out_dir / "keep_this.png").read_bytes() == b"unrelated pre-existing file"
    assert len(list(out_dir.iterdir())) == 1  # only the unrelated file remains


def test_render_failure_on_first_page_leaves_nothing(src, out_dir):
    with patch("pymupdf.Pixmap.tobytes", side_effect=RuntimeError("boom")):
        with pytest.raises(PDFEngineError):
            _convert(src, out_dir, page_indices=[0, 1, 2])
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_disk_full_partway_through_cleans_up(src, out_dir):
    call_count = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src_path, dst_path):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError(28, "No space left on device")
        return real_replace(src_path, dst_path)

    with patch("os.replace", side_effect=flaky_replace):
        with pytest.raises(PDFEngineError) as exc_info:
            _convert(src, out_dir, page_indices=[0, 1, 2])
    assert "No space left" in str(exc_info.value)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_no_temp_files_left_after_success(src, out_dir):
    _convert(src, out_dir)
    leftovers = [p.name for p in out_dir.iterdir() if p.name.startswith(".tmp_")]
    assert leftovers == []


def test_no_temp_files_left_after_failure(src, out_dir):
    with patch("pymupdf.Pixmap.tobytes", side_effect=RuntimeError("boom")):
        with pytest.raises(PDFEngineError):
            _convert(src, out_dir, page_indices=[0])
    if out_dir.exists():
        leftovers = [p.name for p in out_dir.iterdir() if p.name.startswith(".tmp_")]
        assert leftovers == []


# ---------------------------------------------------------------------------
# 51. Repeated conversion
# ---------------------------------------------------------------------------

def test_repeated_conversion_same_source_different_folders(src, tmp_path):
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    result1 = _convert(src, out1)
    result2 = _convert(src, out2)
    assert result1["total_images"] == result2["total_images"] == 5


def test_repeated_conversion_same_folder_gets_collision_suffixes(src, out_dir):
    result1 = _convert(src, out_dir, page_indices=[0])
    result2 = _convert(src, out_dir, page_indices=[0])
    assert result1["output_paths"][0].name == "document_page_001.png"
    assert result2["output_paths"][0].name == "document_page_001 (1).png"
    assert result1["output_paths"][0].exists()
    assert result2["output_paths"][0].exists()


def test_repeated_conversion_does_not_mutate_shared_state(tmp_path):
    a = _make_pdf(tmp_path / "a.pdf", pages=1, colors=[(1, 0, 0)])
    b = _make_pdf(tmp_path / "b.pdf", pages=1, colors=[(0, 0, 1)])
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    r1 = _convert(a, out1)
    r2 = _convert(b, out2)

    c1 = _center_pixel(r1["output_paths"][0])
    c2 = _center_pixel(r2["output_paths"][0])
    assert _rgb_close(c1, (255, 0, 0), tol=30)
    assert _rgb_close(c2, (0, 0, 255), tol=30)


# ---------------------------------------------------------------------------
# 52-53. Output-folder handling / deterministic filenames
# ---------------------------------------------------------------------------

def test_output_folder_can_be_nested_and_is_created(src, tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    result = _convert(src, nested)
    assert nested.exists()
    assert result["total_images"] == 5


def test_filenames_are_fully_deterministic_given_a_fresh_folder(tmp_path):
    source = _make_pdf(tmp_path / "d.pdf", pages=3)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    r1 = _convert(source, out1)
    r2 = _convert(source, out2)
    assert [p.name for p in r1["output_paths"]] == [p.name for p in r2["output_paths"]]


# ---------------------------------------------------------------------------
# 54-55. Unicode / sanitized filenames
# ---------------------------------------------------------------------------

def test_unicode_source_filename(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "R\u00e9sum\u00e9 \u2014 caf\u00e9.pdf", pages=2)
    result = _convert(source, out_dir)
    assert result["total_images"] == 2
    assert all(p.exists() for p in result["output_paths"])
    assert "sum" in result["output_paths"][0].name  # sanity: stem carried through


def test_filenames_sanitized_for_windows_invalid_characters(tmp_path, out_dir):
    with patch.object(
        file_manager, "sanitize_windows_filename",
        wraps=file_manager.sanitize_windows_filename,
    ) as spy:
        source = _make_pdf(tmp_path / "weird.pdf", pages=1)
        _convert(source, out_dir)
    spy.assert_called()


def test_windows_reserved_name_stem_is_sanitized(tmp_path, out_dir):
    source = _make_pdf(tmp_path / "CON.pdf", pages=1)
    result = _convert(source, out_dir)
    assert result["output_paths"][0].exists()
    assert not result["output_paths"][0].name.lower().startswith("con_page")


# ---------------------------------------------------------------------------
# 56. Zero-image / zero-page invalid input
# ---------------------------------------------------------------------------

def test_zero_page_document_all_pages_rejected(tmp_path, out_dir):
    doc = pymupdf.open()
    source = tmp_path / "empty.pdf"
    # PyMuPDF refuses to save a document with zero pages, so simulate
    # the "no pages to render" condition at the resolve_pages_to_render
    # layer directly, which is exactly what render_pdf_to_images() would
    # hit for such a document.
    with pytest.raises(split_engine.PageRangeError):
        pe.resolve_pages_to_render(None, 0)


def test_empty_page_indices_list_never_starts_rendering(src, out_dir):
    with patch("pymupdf.Page.get_pixmap") as spy:
        with pytest.raises(PdfToImagesError):
            _convert(src, out_dir, page_indices=[])
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

def test_progress_callback_all_pages_reports_of_n(src, out_dir):
    messages = []
    _convert(src, out_dir, progress_callback=messages.append)
    per_page = [m for m in messages if m.startswith("Rendering page")]
    assert per_page == [f"Rendering page {i} of 5" for i in range(1, 6)]
    assert messages[-1] == "Conversion completed successfully."


def test_progress_callback_non_contiguous_selection_reports_step_of_total(src, out_dir):
    messages = []
    indices = pe.resolve_pages_to_render("1,3,5", 5)
    _convert(src, out_dir, page_indices=indices, progress_callback=messages.append)
    per_page = [m for m in messages if m.startswith("Rendering page")]
    assert per_page == [
        "Rendering page 1 (1 of 3)", "Rendering page 3 (2 of 3)",
        "Rendering page 5 (3 of 3)",
    ]


def test_no_progress_callback_is_fine(src, out_dir):
    result = _convert(src, out_dir, progress_callback=None)
    assert result["total_images"] == 5


# ---------------------------------------------------------------------------
# Error handling: permission / disk / unexpected
# ---------------------------------------------------------------------------

def test_permission_error_becomes_a_clean_engine_error(src, out_dir):
    with patch("os.replace", side_effect=PermissionError(13, "Permission denied")):
        with pytest.raises(PDFEngineError) as exc_info:
            _convert(src, out_dir, page_indices=[0])
    assert "Permission denied" in str(exc_info.value)


def test_unexpected_render_failure_is_wrapped_without_traceback(src, out_dir):
    with patch("pymupdf.Page.get_pixmap", side_effect=RuntimeError("engine exploded")):
        with pytest.raises(PDFEngineError) as exc_info:
            _convert(src, out_dir, page_indices=[0])
    assert "Traceback" not in str(exc_info.value)
    assert "page 1" in str(exc_info.value).lower()


def test_document_is_always_closed(src, out_dir):
    with patch.object(
        pymupdf.Document, "close", autospec=True, wraps=pymupdf.Document.close,
    ) as spy:
        _convert(src, out_dir)
    assert spy.called  # the important property: whatever was opened, got closed


# ---------------------------------------------------------------------------
# Error hierarchy / hygiene
# ---------------------------------------------------------------------------

def test_error_hierarchy():
    assert issubclass(PdfToImagesError, PDFEngineError)
    assert issubclass(ImageOptionsError, PdfToImagesError)


def test_engine_does_not_import_tkinter():
    source = (Path(__file__).resolve().parent.parent / "pdf_to_images_engine.py").read_text(
        encoding="utf-8"
    )
    assert "import tkinter" not in source
    assert "from tkinter" not in source


def test_engine_does_not_import_pillow():
    """Per the Phase 23 spec: PyMuPDF's own Pixmap encoder is used
    directly; Pillow is not needed for this direction of conversion."""
    source = (Path(__file__).resolve().parent.parent / "pdf_to_images_engine.py").read_text(
        encoding="utf-8"
    )
    assert "PIL" not in source
    assert "Pillow" not in source or "pillow" not in source.lower().replace("pil", "")


def test_engine_reuses_existing_infrastructure():
    source = (Path(__file__).resolve().parent.parent / "pdf_to_images_engine.py").read_text(
        encoding="utf-8"
    )
    assert "split_engine.parse_page_ranges" in source
    assert "pdf_engine.validate_pdf" in source
    assert "file_manager.generate_image_output_path" in source
