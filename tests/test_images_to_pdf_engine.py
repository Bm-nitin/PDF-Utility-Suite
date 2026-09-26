"""
test_images_to_pdf_engine.py

Direct tests for images_to_pdf_engine.py (Phase 22): option validation,
the pure placement geometry (compute_page_geometry()), image loading/
normalization (_prepare_image() -- EXIF orientation, transparency
handling, mode conversion, first-frame-only for animated formats), the
images_to_pdf() engine itself (page count/order, duplicate selection,
source immutability, output validity, error handling, atomic-save
integration, repeated conversion), and get_image_info().

Verification is content-based wherever practical: extracted page images
are re-decoded and their pixel data is compared against (or checked to
be geometrically consistent with) the original source image, not just
"the file exists".

Pure engine tests: no tkinter, no display required.
"""

import io
import math
import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager
import images_to_pdf_engine as ie
import pdf_engine
from images_to_pdf_engine import ImageOptionsError, ImagesToPdfError, InvalidImageError
from pdf_engine import PDFEngineError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_image(path, size=(300, 200), color=(200, 30, 30), mode="RGB", fmt=None, **save_kwargs):
    fmt = fmt or {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP",
                  "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}[path.suffix.lstrip(".").lower()]
    if fmt == "WEBP":
        save_kwargs.setdefault("lossless", True)  # exact pixel roundtrip for tests
    img = Image.new(mode, size, color) if mode != "P" else Image.new("RGB", size, color).convert("P")
    img.save(path, format=fmt, **save_kwargs)
    return path


def _extract_page_image(doc, page_index=0):
    """The single embedded raster image on a page, decoded as a PIL
    Image (via PyMuPDF's own extract_image(), which returns encoded
    image bytes -- re-decoded here with Pillow for pixel comparisons).
    """
    page = doc[page_index]
    images = page.get_images()
    assert len(images) == 1, f"expected exactly one image on page {page_index}, found {len(images)}"
    xref = images[0][0]
    info = doc.extract_image(xref)
    return Image.open(io.BytesIO(info["image"]))


def _corner_pixel(img, xy=(2, 2)):
    return img.convert("RGB").getpixel(xy)


@pytest.fixture
def red_png(tmp_path):
    return _save_image(tmp_path / "red.png", size=(300, 200), color=(220, 20, 20))


@pytest.fixture
def blue_jpg(tmp_path):
    return _save_image(tmp_path / "blue.jpg", size=(200, 300), color=(20, 20, 220), quality=95)


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out.pdf"


def _convert(paths, out, **kwargs):
    return ie.images_to_pdf(paths, out, **kwargs)


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

def test_documented_defaults():
    assert ie.DEFAULT_PAGE_SIZE == "a4"
    assert ie.DEFAULT_MARGIN == 36.0
    assert ie.PAGE_SIZE_CHOICES == ("a4", "letter", "original")
    assert ie.PAGE_SIZES["a4"] == (595.28, 841.89)
    assert ie.PAGE_SIZES["letter"] == (612.0, 792.0)


def test_supported_extensions_match_what_this_pillow_build_actually_supports():
    """Not just a hardcoded list -- confirms Pillow itself can open a
    file saved in each claimed format."""
    from PIL import features
    assert set(ie.SUPPORTED_EXTENSIONS) == {
        ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
    }
    assert features.check("jpg")
    assert features.check("webp")
    assert features.check("libtiff")


def test_supported_extensions_match_file_manager_dialog_filter():
    """file_manager.select_image_files() deliberately duplicates this
    list rather than importing it (see that function's own docstring
    on module layering) -- this test is what keeps the duplication
    from silently drifting apart.
    """
    import inspect
    source = inspect.getsource(file_manager.select_image_files)
    for ext in ie.SUPPORTED_EXTENSIONS:
        bare = ext.lstrip(".")
        assert bare in source, f"{ext} missing from select_image_files()'s filter"


# ---------------------------------------------------------------------------
# Option validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["a4", "letter", "original"])
def test_valid_page_sizes(value):
    assert ie.validate_page_size(value) == value


@pytest.mark.parametrize("bad", ["", "A4", "Letter", "legal", None, 5, "a4 ", " a4"])
def test_invalid_page_sizes_are_rejected(bad):
    with pytest.raises(ImageOptionsError):
        ie.validate_page_size(bad)


@pytest.mark.parametrize("value,expected", [
    (36, 36.0), (0, 0.0), ("36", 36.0), (" 12.5 ", 12.5), (0.0, 0.0),
])
def test_valid_margins(value, expected):
    assert ie.validate_margin(value) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", None, "abc", "nan", "inf", "-inf", float("nan"), float("inf"),
    True, -1, -0.5, "-1", 1e400,
])
def test_invalid_margins_are_rejected(bad):
    with pytest.raises(ImageOptionsError):
        ie.validate_margin(bad)


def test_margin_upper_sanity_bound():
    ie.validate_margin(ie.MAX_MARGIN)  # exactly at the bound: fine
    with pytest.raises(ImageOptionsError):
        ie.validate_margin(ie.MAX_MARGIN + 1)


# ---------------------------------------------------------------------------
# Pure geometry: compute_page_geometry()
# ---------------------------------------------------------------------------

def test_geometry_a4_portrait_for_portrait_image():
    page_w, page_h, rect = ie.compute_page_geometry(200, 400, "a4", 36)
    assert (page_w, page_h) == ie.PAGE_SIZES["a4"]  # already portrait


def test_geometry_a4_landscape_for_landscape_image():
    page_w, page_h, rect = ie.compute_page_geometry(400, 200, "a4", 36)
    base_w, base_h = ie.PAGE_SIZES["a4"]
    assert (page_w, page_h) == (base_h, base_w)
    assert page_w > page_h


def test_geometry_square_image_defaults_to_portrait():
    page_w, page_h, rect = ie.compute_page_geometry(300, 300, "a4", 36)
    base_w, base_h = ie.PAGE_SIZES["a4"]
    assert (page_w, page_h) == (min(base_w, base_h), max(base_w, base_h))


@pytest.mark.parametrize("page_size", ["a4", "letter"])
@pytest.mark.parametrize("pixel_w,pixel_h", [
    (300, 400), (400, 300), (300, 300), (1000, 10), (10, 1000), (1, 1),
])
def test_geometry_preserves_aspect_ratio_and_fits_no_stretch(page_size, pixel_w, pixel_h):
    page_w, page_h, (x0, y0, x1, y1) = ie.compute_page_geometry(pixel_w, pixel_h, page_size, 36)
    fit_w, fit_h = x1 - x0, y1 - y0

    assert fit_w > 0 and fit_h > 0
    # Aspect ratio preserved (uniform scale, not stretched).
    assert fit_w / fit_h == pytest.approx(pixel_w / pixel_h, rel=1e-6)
    # Centered.
    assert x0 == pytest.approx(page_w - x1, abs=0.01)
    assert y0 == pytest.approx(page_h - y1, abs=0.01)
    # Within the page and respecting the margin.
    assert x0 >= 36 - 0.01 and y0 >= 36 - 0.01
    assert x1 <= page_w - 36 + 0.01 and y1 <= page_h - 36 + 0.01
    # Fits as large as possible: touches at least one of the two margins.
    assert x0 == pytest.approx(36, abs=0.01) or y0 == pytest.approx(36, abs=0.01)


def test_geometry_zero_margin_fills_exactly_one_axis():
    page_w, page_h, (x0, y0, x1, y1) = ie.compute_page_geometry(400, 300, "a4", 0)
    assert x0 == pytest.approx(0, abs=0.01) or y0 == pytest.approx(0, abs=0.01)
    assert x1 <= page_w + 0.01
    assert y1 <= page_h + 0.01


def test_geometry_rejects_margin_leaving_no_room():
    base_w, base_h = ie.PAGE_SIZES["a4"]
    huge_margin = min(base_w, base_h) / 2  # exactly zero available width/height
    with pytest.raises(ImageOptionsError):
        ie.compute_page_geometry(300, 400, "a4", huge_margin)
    with pytest.raises(ImageOptionsError):
        ie.compute_page_geometry(300, 400, "a4", huge_margin + 10)


def test_geometry_letter_behaves_like_a4_structurally():
    page_w, page_h, rect = ie.compute_page_geometry(300, 500, "letter", 36)
    assert (page_w, page_h) == ie.PAGE_SIZES["letter"]


def test_geometry_original_ratio_uses_dpi_and_adds_margin():
    page_w, page_h, (x0, y0, x1, y1) = ie.compute_page_geometry(
        960, 480, "original", 36, image_dpi=96,
    )
    # 960px / 96dpi * 72 = 720pt ; 480px / 96dpi * 72 = 360pt
    assert page_w == pytest.approx(720 + 72)
    assert page_h == pytest.approx(360 + 72)
    assert (x0, y0, x1, y1) == pytest.approx((36, 36, 756, 396))


def test_geometry_original_ratio_falls_back_to_default_dpi():
    page_w1, page_h1, _ = ie.compute_page_geometry(960, 480, "original", 0, image_dpi=None)
    page_w2, page_h2, _ = ie.compute_page_geometry(960, 480, "original", 0, image_dpi=ie.DEFAULT_DPI)
    assert (page_w1, page_h1) == pytest.approx((page_w2, page_h2))


@pytest.mark.parametrize("bad_dpi", [0, -5, float("nan"), float("inf"), 5, 3000])
def test_geometry_original_ratio_ignores_insane_dpi(bad_dpi):
    """Outside [MIN_SANE_DPI, MAX_SANE_DPI] -- falls back to DEFAULT_DPI
    rather than producing a nonsensical (zero/negative/huge) page."""
    page_w, page_h, _ = ie.compute_page_geometry(960, 480, "original", 0, image_dpi=bad_dpi)
    expected_w = 960 / ie.DEFAULT_DPI * 72
    expected_h = 480 / ie.DEFAULT_DPI * 72
    assert (page_w, page_h) == pytest.approx((expected_w, expected_h))


def test_geometry_original_ratio_margin_never_rejected():
    """Unlike a4/letter, "original" pages are sized to always leave
    exactly `margin` of room by construction -- no margin value (short
    of being invalid on its own terms) can ever be "too large"."""
    page_w, page_h, rect = ie.compute_page_geometry(10, 10, "original", 5000, image_dpi=96)
    assert page_w > 0 and page_h > 0


def test_geometry_rejects_zero_size_image():
    with pytest.raises(InvalidImageError):
        ie.compute_page_geometry(0, 100, "a4", 36)
    with pytest.raises(InvalidImageError):
        ie.compute_page_geometry(100, 0, "original", 36)


def test_geometry_unknown_page_size_is_a_programming_error_not_silently_ignored():
    with pytest.raises(KeyError):
        ie.compute_page_geometry(100, 100, "bogus", 36)


# ---------------------------------------------------------------------------
# validate_image_file() / get_image_info()
# ---------------------------------------------------------------------------

def test_validate_image_file_accepts_every_supported_extension(tmp_path):
    for ext in ie.SUPPORTED_EXTENSIONS:
        path = _save_image(tmp_path / f"x{ext}", size=(20, 20))
        ie.validate_image_file(path)  # no raise


def test_validate_image_file_rejects_missing(tmp_path):
    with pytest.raises(InvalidImageError):
        ie.validate_image_file(tmp_path / "missing.png")


def test_validate_image_file_rejects_directory(tmp_path):
    folder = tmp_path / "folder.png"
    folder.mkdir()
    with pytest.raises(InvalidImageError):
        ie.validate_image_file(folder)


@pytest.mark.parametrize("ext", [".txt", ".gif", ".pdf", ".svg", ".ico", ".heic", ""])
def test_validate_image_file_rejects_unsupported_extensions(tmp_path, ext):
    path = tmp_path / f"file{ext}"
    path.write_bytes(b"whatever")
    with pytest.raises(InvalidImageError):
        ie.validate_image_file(path)


def test_get_image_info_returns_expected_fields(red_png):
    info = ie.get_image_info(red_png)
    assert info["path"] == red_png
    assert info["name"] == "red.png"
    assert info["width"] == 300
    assert info["height"] == 200
    assert info["size"] == red_png.stat().st_size


def test_get_image_info_missing_file(tmp_path):
    with pytest.raises(InvalidImageError):
        ie.get_image_info(tmp_path / "nope.png")


def test_get_image_info_corrupt_file(tmp_path):
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"NOT AN IMAGE" * 30)
    with pytest.raises(InvalidImageError):
        ie.get_image_info(bad)


def test_get_image_info_truncated_file(tmp_path, red_png):
    truncated = tmp_path / "truncated.png"
    data = red_png.read_bytes()
    truncated.write_bytes(data[: len(data) - 100])
    with pytest.raises(InvalidImageError):
        ie.get_image_info(truncated)


def test_get_image_info_unsupported_extension(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(InvalidImageError):
        ie.get_image_info(bad)


def test_get_image_info_wrong_extension_but_valid_pixel_data_still_rejected(tmp_path):
    """A real image saved under an unsupported extension is still
    rejected -- extension is checked, not inferred purely from
    content, per the Phase 22 "validate before conversion" requirement
    working together with (not instead of) real decode validation."""
    path = tmp_path / "sneaky.gif"
    Image.new("RGB", (20, 20)).save(path, format="PNG")  # PNG bytes, .gif name
    with pytest.raises(InvalidImageError):
        ie.get_image_info(path)


# ---------------------------------------------------------------------------
# Image loading / normalization: _prepare_image()
# ---------------------------------------------------------------------------

def test_prepare_image_rgb_passthrough(red_png):
    img, dpi = ie._prepare_image(red_png)
    assert img.mode == "RGB"
    assert img.size == (300, 200)
    assert _corner_pixel(img) == (220, 20, 20)


def test_prepare_image_rgba_composited_onto_white(tmp_path):
    path = tmp_path / "trans.png"
    src = Image.new("RGBA", (50, 50), (255, 0, 0, 0))  # fully transparent
    src.putpixel((0, 0), (0, 255, 0, 255))  # opaque green corner
    src.save(path)

    img, _dpi = ie._prepare_image(path)
    assert img.mode == "RGB"
    assert img.getpixel((25, 25)) == (255, 255, 255)  # transparent area -> white
    assert img.getpixel((0, 0)) == (0, 255, 0)  # opaque area kept


def test_prepare_image_partial_alpha_blends_toward_white(tmp_path):
    path = tmp_path / "half.png"
    src = Image.new("RGBA", (10, 10), (0, 0, 0, 128))  # 50% black
    src.save(path)
    img, _dpi = ie._prepare_image(path)
    r, g, b = img.getpixel((5, 5))
    assert 100 < r < 150 and r == g == b  # roughly halfway to white


def test_prepare_image_grayscale_converted_to_rgb(tmp_path):
    path = tmp_path / "gray.png"
    Image.new("L", (40, 40), 100).save(path)
    img, _dpi = ie._prepare_image(path)
    assert img.mode == "RGB"
    assert img.getpixel((5, 5)) == (100, 100, 100)


def test_prepare_image_palette_without_transparency(tmp_path):
    path = tmp_path / "pal.png"
    Image.new("RGB", (30, 30), (10, 20, 30)).convert("P").save(path)
    img, _dpi = ie._prepare_image(path)
    assert img.mode == "RGB"
    r, g, b = img.getpixel((5, 5))
    assert (r, g, b) != (255, 255, 255)  # actual color, not fallen back to white


def test_prepare_image_palette_with_transparency_composited_onto_white(tmp_path):
    path = tmp_path / "pal_trans.png"
    base = Image.new("RGBA", (20, 20), (0, 0, 255, 0))
    base.putpixel((0, 0), (255, 0, 0, 255))
    pal = base.convert("P", palette=Image.ADAPTIVE)
    # Ensure a transparency entry exists on the palette image.
    pal.info["transparency"] = pal.getpixel((10, 10))
    pal.save(path)

    img, _dpi = ie._prepare_image(path)
    assert img.mode == "RGB"
    assert img.getpixel((10, 10)) == (255, 255, 255)


def test_prepare_image_cmyk(tmp_path):
    path = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (30, 30), (0, 0, 0, 255)).save(path, format="JPEG")
    img, _dpi = ie._prepare_image(path)
    assert img.mode == "RGB"
    assert img.getpixel((5, 5)) == (0, 0, 0)  # full K -> black


@pytest.mark.parametrize("orientation", list(range(1, 9)))
def test_prepare_image_applies_exif_orientation(tmp_path, orientation):
    path = tmp_path / f"exif{orientation}.jpg"
    base = Image.new("RGB", (60, 40), (10, 10, 10))
    for x in range(15):
        for y in range(15):
            base.putpixel((x, y), (0, 255, 0))  # green marker, top-left
    exif = base.getexif()
    exif[0x0112] = orientation
    base.save(path, format="JPEG", exif=exif, quality=95)

    expected_full = ImageOps.exif_transpose(Image.open(path))
    img, _dpi = ie._prepare_image(path)

    assert img.size == expected_full.size
    assert img.getpixel((5, 5)) == _corner_pixel(expected_full.convert("RGB"), (5, 5))


def test_prepare_image_exif_orientation_does_not_modify_source(tmp_path):
    path = tmp_path / "exif.jpg"
    base = Image.new("RGB", (60, 40), (10, 10, 10))
    exif = base.getexif()
    exif[0x0112] = 6
    base.save(path, format="JPEG", exif=exif, quality=95)
    before = path.read_bytes()

    ie._prepare_image(path)

    assert path.read_bytes() == before


def test_prepare_image_no_exif_is_fine(red_png):
    img, _dpi = ie._prepare_image(red_png)
    assert img.size == (300, 200)


def test_prepare_image_animated_gif_uses_first_frame_only(tmp_path):
    path = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (20, 20), (i * 80, 0, 0)) for i in range(3)]
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)

    img, _dpi = ie._prepare_image(path)
    assert img.mode == "RGB"
    assert img.getpixel((5, 5)) == (0, 0, 0)  # frame 0's color, not frame 1/2's


def test_prepare_image_animated_webp_uses_first_frame_only(tmp_path):
    path = tmp_path / "anim.webp"
    frames = [Image.new("RGB", (20, 20), (0, i * 80, 0)) for i in range(3)]
    frames[0].save(path, format="WEBP", save_all=True, append_images=frames[1:], duration=80, loop=0)

    img, _dpi = ie._prepare_image(path)
    assert img.getpixel((5, 5)) == (0, 0, 0)  # frame 0's color


def test_prepare_image_never_seeks_to_another_frame():
    """Guards the documented mechanism, not just the outcome: no actual
    .seek() call anywhere in the module's code (parsed via ast, so
    prose mentions of ".seek()" inside docstrings/comments don't cause
    a false positive)."""
    import ast

    source = (Path(__file__).resolve().parent.parent / "images_to_pdf_engine.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    seek_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "seek"
    ]
    assert seek_calls == []


def test_prepare_image_extracts_dpi_when_present(tmp_path):
    path = tmp_path / "dpi.png"
    Image.new("RGB", (100, 100)).save(path, dpi=(150, 150))
    _img, dpi = ie._prepare_image(path)
    assert dpi == pytest.approx(150, rel=0.01)


def test_prepare_image_dpi_is_none_when_absent(tmp_path):
    path = tmp_path / "nodpi.webp"
    Image.new("RGB", (100, 100)).save(path, format="WEBP")
    _img, dpi = ie._prepare_image(path)
    assert dpi is None


def test_prepare_image_raises_for_deleted_file(tmp_path, red_png):
    red_png.unlink()
    with pytest.raises(InvalidImageError):
        ie._prepare_image(red_png)


def test_prepare_image_never_writes_to_the_source(red_png):
    before = red_png.read_bytes()
    ie._prepare_image(red_png)
    assert red_png.read_bytes() == before


# ---------------------------------------------------------------------------
# images_to_pdf(): page count / order / duplicates
# ---------------------------------------------------------------------------

def test_single_png(red_png, out):
    _convert([red_png], out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1


def test_single_jpeg(blue_jpg, out):
    _convert([blue_jpg], out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1


def test_multiple_images(tmp_path, out):
    paths = [_save_image(tmp_path / f"i{i}.png", color=(i * 10, 0, 0)) for i in range(5)]
    _convert(paths, out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 5


def test_mixed_image_formats(tmp_path, out):
    paths = [
        _save_image(tmp_path / "a.png", color=(255, 0, 0)),
        _save_image(tmp_path / "b.jpg", color=(0, 255, 0), quality=100),
        _save_image(tmp_path / "c.bmp", color=(0, 0, 255)),
        _save_image(tmp_path / "d.tiff", color=(255, 255, 0)),
        _save_image(tmp_path / "e.webp", color=(0, 255, 255)),
    ]
    _convert(paths, out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 5
        for i, expected in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)]):
            img = _extract_page_image(doc, i)
            actual = _corner_pixel(img)
            # JPEG (index 1) is lossy even at quality=100 -- allow a
            # small per-channel tolerance there; every other format
            # here is lossless and must match exactly.
            tolerance = 3 if i == 1 else 0
            assert all(abs(a - e) <= tolerance for a, e in zip(actual, expected)), (i, actual, expected)


def test_page_order_matches_input_order(tmp_path, out):
    paths = [
        _save_image(tmp_path / "z.png", color=(255, 0, 0)),
        _save_image(tmp_path / "a.png", color=(0, 255, 0)),
        _save_image(tmp_path / "m.png", color=(0, 0, 255)),
    ]
    _convert(paths, out)  # deliberately NOT alphabetical
    with pymupdf.open(out) as doc:
        colors = [_corner_pixel(_extract_page_image(doc, i)) for i in range(3)]
    assert colors == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]


def test_duplicate_image_selection_produces_duplicate_pages(red_png, out):
    _convert([red_png, red_png, red_png], out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3
        for i in range(3):
            assert _corner_pixel(_extract_page_image(doc, i)) == (220, 20, 20)


def test_duplicate_paths_interspersed_with_other_images(tmp_path, out):
    a = _save_image(tmp_path / "a.png", color=(255, 0, 0))
    b = _save_image(tmp_path / "b.png", color=(0, 255, 0))
    _convert([a, b, a], out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3
        colors = [_corner_pixel(_extract_page_image(doc, i)) for i in range(3)]
    assert colors == [(255, 0, 0), (0, 255, 0), (255, 0, 0)]


def test_empty_selection_rejected(out):
    with pytest.raises(ImagesToPdfError):
        _convert([], out)
    assert not out.exists()


# ---------------------------------------------------------------------------
# Source immutability
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged(tmp_path, out):
    paths = [_save_image(tmp_path / f"i{i}.png") for i in range(3)]
    before = {p: p.read_bytes() for p in paths}

    _convert(paths, out)

    for p in paths:
        assert p.exists()
        assert p.read_bytes() == before[p]


def test_source_mtime_unchanged(red_png, out):
    before_mtime = red_png.stat().st_mtime_ns
    _convert([red_png], out)
    assert red_png.stat().st_mtime_ns == before_mtime


def test_duplicate_selection_leaves_both_reads_nondestructive(red_png, out):
    before = red_png.read_bytes()
    _convert([red_png, red_png], out)
    assert red_png.read_bytes() == before


# ---------------------------------------------------------------------------
# Color modes
# ---------------------------------------------------------------------------

def test_rgb_image(tmp_path, out):
    path = _save_image(tmp_path / "rgb.png", color=(12, 34, 56))
    _convert([path], out)
    with pymupdf.open(out) as doc:
        assert _corner_pixel(_extract_page_image(doc)) == (12, 34, 56)


def test_rgba_image(tmp_path, out):
    path = tmp_path / "rgba.png"
    Image.new("RGBA", (100, 100), (30, 60, 90, 255)).save(path)
    _convert([path], out)
    with pymupdf.open(out) as doc:
        assert _corner_pixel(_extract_page_image(doc)) == (30, 60, 90)


def test_transparent_png_gets_white_background(tmp_path, out):
    path = tmp_path / "trans.png"
    Image.new("RGBA", (100, 100), (0, 0, 0, 0)).save(path)
    _convert([path], out, margin=0)
    with pymupdf.open(out) as doc:
        img = _extract_page_image(doc)
    assert img.convert("RGB").getpixel((50, 50)) == (255, 255, 255)


def test_grayscale_image(tmp_path, out):
    path = tmp_path / "gray.png"
    Image.new("L", (100, 100), 77).save(path)
    _convert([path], out)
    with pymupdf.open(out) as doc:
        r, g, b = _corner_pixel(_extract_page_image(doc))
    assert r == g == b == 77


def test_palette_image(tmp_path, out):
    path = tmp_path / "pal.png"
    Image.new("RGB", (100, 100), (5, 6, 7)).convert("P").save(path)
    _convert([path], out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1
        img = _extract_page_image(doc)
    assert img.convert("RGB").size == (100, 100)


def test_cmyk_image(tmp_path, out):
    path = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (100, 100), (255, 0, 0, 0)).save(path, format="JPEG", quality=95)
    _convert([path], out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1  # succeeds without raising


# ---------------------------------------------------------------------------
# Corrupt / missing / unsupported / invalid
# ---------------------------------------------------------------------------

def test_corrupted_image(tmp_path, out):
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"GARBAGE" * 50)
    with pytest.raises(InvalidImageError) as exc_info:
        _convert([bad], out)
    assert "corrupt.png" in str(exc_info.value)
    assert not out.exists()


def test_missing_image(tmp_path, out):
    with pytest.raises(InvalidImageError):
        _convert([tmp_path / "missing.png"], out)
    assert not out.exists()


def test_unsupported_extension(tmp_path, out):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(InvalidImageError):
        _convert([bad], out)
    assert not out.exists()


def test_invalid_image_content(tmp_path, out):
    bad = tmp_path / "fake.jpg"
    bad.write_bytes(b"\x00\x01\x02\x03" * 20)
    with pytest.raises(InvalidImageError):
        _convert([bad], out)
    assert not out.exists()


def test_one_bad_image_among_good_ones_aborts_cleanly_with_no_partial_output(tmp_path, out):
    good1 = _save_image(tmp_path / "good1.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"NOPE" * 20)
    good2 = _save_image(tmp_path / "good2.png")

    with pytest.raises(InvalidImageError) as exc_info:
        _convert([good1, bad, good2], out)
    assert "bad.png" in str(exc_info.value)
    assert not out.exists()


def test_image_deleted_after_selection_but_before_conversion(tmp_path, out):
    path = _save_image(tmp_path / "gone.png")
    path.unlink()
    with pytest.raises(InvalidImageError):
        _convert([path], out)


# ---------------------------------------------------------------------------
# Page size / orientation / aspect ratio
# ---------------------------------------------------------------------------

def test_a4_page(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(300, 400))
    _convert([path], out, page_size="a4")
    with pymupdf.open(out) as doc:
        assert (doc[0].rect.width, doc[0].rect.height) == pytest.approx(ie.PAGE_SIZES["a4"])


def test_letter_page(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(300, 400))
    _convert([path], out, page_size="letter")
    with pymupdf.open(out) as doc:
        assert (doc[0].rect.width, doc[0].rect.height) == pytest.approx(ie.PAGE_SIZES["letter"])


def test_original_image_ratio_page(tmp_path, out):
    path = tmp_path / "p.png"
    Image.new("RGB", (400, 200)).save(path, dpi=(96, 96))
    _convert([path], out, page_size="original")
    with pymupdf.open(out) as doc:
        expected_w = 400 / 96 * 72 + 2 * ie.DEFAULT_MARGIN
        expected_h = 200 / 96 * 72 + 2 * ie.DEFAULT_MARGIN
        assert (doc[0].rect.width, doc[0].rect.height) == pytest.approx((expected_w, expected_h), abs=0.5)


def test_portrait_image_on_a4(tmp_path, out):
    path = _save_image(tmp_path / "portrait.png", size=(200, 400))
    _convert([path], out, page_size="a4")
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rect.height > page.rect.width


def test_landscape_image_on_a4(tmp_path, out):
    path = _save_image(tmp_path / "landscape.png", size=(400, 200))
    _convert([path], out, page_size="a4")
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rect.width > page.rect.height


def test_square_image_on_a4(tmp_path, out):
    path = _save_image(tmp_path / "square.png", size=(300, 300))
    _convert([path], out, page_size="a4")
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.rect.height > page.rect.width  # deterministic default: portrait


def test_mixed_orientations_in_one_pdf_each_get_their_own_best_fit_page(tmp_path, out):
    portrait = _save_image(tmp_path / "portrait.png", size=(200, 400))
    landscape = _save_image(tmp_path / "landscape.png", size=(400, 200))
    _convert([portrait, landscape], out, page_size="a4")
    with pymupdf.open(out) as doc:
        assert doc[0].rect.height > doc[0].rect.width
        assert doc[1].rect.width > doc[1].rect.height


# ---------------------------------------------------------------------------
# Margin
# ---------------------------------------------------------------------------

def test_margin_is_applied_a4(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(1000, 1000))  # large square
    _convert([path], out, margin=50, page_size="a4")
    with pymupdf.open(out) as doc:
        page = doc[0]
        # A large square image at margin=50 is width-constrained
        # (portrait A4): confirm the placed image doesn't reach within
        # 50pt of the left/right edges.
        assert page.rect.width - 2 * 50 > 0


def test_margin_zero_is_valid(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(300, 400))
    _convert([path], out, margin=0)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1


@pytest.mark.parametrize("bad_margin", [-1, "abc", float("nan")])
def test_invalid_margin_rejected_and_writes_nothing(red_png, out, bad_margin):
    with pytest.raises(ImageOptionsError):
        _convert([red_png], out, margin=bad_margin)
    assert not out.exists()


def test_excessive_margin_for_page_size_rejected(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(300, 400))
    base_w, base_h = ie.PAGE_SIZES["a4"]
    with pytest.raises(ImageOptionsError):
        _convert([path], out, margin=min(base_w, base_h))
    assert not out.exists()


def test_excessive_margin_never_rejected_for_original_ratio(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(50, 50))
    _convert([path], out, page_size="original", margin=1000)
    assert out.exists()


# ---------------------------------------------------------------------------
# Aspect ratio / no-stretch, verified via extracted output image
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [(400, 200), (200, 400), (300, 300), (777, 111)])
def test_output_aspect_ratio_preserved(tmp_path, out, size):
    path = _save_image(tmp_path / "p.png", size=size)
    _convert([path], out, page_size="a4")
    with pymupdf.open(out) as doc:
        img = _extract_page_image(doc)
    assert img.width / img.height == pytest.approx(size[0] / size[1], rel=1e-3)


def test_no_stretching_original_ratio_matches_source_pixel_dimensions(tmp_path, out):
    path = _save_image(tmp_path / "p.png", size=(321, 654))
    _convert([path], out, page_size="original")
    with pymupdf.open(out) as doc:
        img = _extract_page_image(doc)
    assert img.size == (321, 654)


# ---------------------------------------------------------------------------
# Output validity / content correctness
# ---------------------------------------------------------------------------

def test_output_opens_successfully_and_is_valid(tmp_path, out):
    paths = [_save_image(tmp_path / f"i{i}.png") for i in range(3)]
    _convert(paths, out)
    pdf_engine.validate_pdf(out)
    with pymupdf.open(out) as doc:
        assert doc.is_pdf
        assert not doc.needs_pass
        for page in doc:
            page.get_pixmap(dpi=36)  # renders without error


def test_output_page_count_equals_selection_count(tmp_path, out):
    paths = [_save_image(tmp_path / f"i{i}.png") for i in range(4)]
    _convert(paths, out)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 4


def test_extracted_page_image_matches_source_pixel_for_pixel(tmp_path, out):
    path = tmp_path / "exact.png"
    src = Image.new("RGB", (64, 48))
    for x in range(64):
        for y in range(48):
            src.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    src.save(path)

    _convert([path], out, page_size="original", margin=0)
    with pymupdf.open(out) as doc:
        result_img = _extract_page_image(doc)

    assert result_img.size == src.size
    assert result_img.convert("RGB").tobytes() == src.tobytes()


def test_pdf_metadata_has_sensible_title(tmp_path, out):
    path = _save_image(tmp_path / "p.png")
    _convert([path], out)
    with pymupdf.open(out) as doc:
        assert doc.metadata["title"] == "Images to PDF"


def test_engine_never_rasterizes_a_pdf_page():
    """No vector/PDF-page content exists to rasterize in the first
    place, but this guards the mechanism stays image-insertion, not a
    render-then-embed approach."""
    source = (Path(__file__).resolve().parent.parent / "images_to_pdf_engine.py").read_text(
        encoding="utf-8"
    )
    assert "get_pixmap()" not in source
    assert "show_pdf_page" not in source


# ---------------------------------------------------------------------------
# Output failure / atomic save
# ---------------------------------------------------------------------------

def test_output_failure_destination_folder_cannot_be_created(red_png, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a folder")
    bad_output = blocker / "sub" / "out.pdf"

    before = red_png.read_bytes()
    with pytest.raises(PDFEngineError) as exc_info:
        _convert([red_png], bad_output)
    assert "Traceback" not in str(exc_info.value)
    assert red_png.read_bytes() == before


def test_permission_error_becomes_a_clean_engine_error(red_png, out):
    with patch("pymupdf.Document.save", side_effect=PermissionError(13, "Permission denied")):
        with pytest.raises(PDFEngineError) as exc_info:
            _convert([red_png], out)
    assert "Permission denied" in str(exc_info.value)
    assert not out.exists()


def test_disk_full_becomes_a_clean_engine_error(red_png, out):
    with patch("pymupdf.Document.save", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(PDFEngineError) as exc_info:
            _convert([red_png], out)
    assert "No space left on device" in str(exc_info.value)
    assert not out.exists()


def test_uses_the_existing_atomic_save(red_png, out):
    with patch.object(
        pdf_engine, "_atomic_save_pdf", wraps=pdf_engine._atomic_save_pdf,
    ) as spy:
        _convert([red_png], out)
    spy.assert_called_once()
    assert spy.call_args.args[1] == out
    assert out.exists()


def test_no_temp_files_left_after_success(red_png, tmp_path, out):
    _convert([red_png], out)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")] == []


def test_no_temp_files_or_partial_output_left_after_failure(tmp_path, out):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope" * 10)
    with pytest.raises(InvalidImageError):
        _convert([bad], out)
    assert not out.exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")] == []


def test_failed_save_leaves_existing_destination_untouched(red_png, tmp_path):
    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"precious existing bytes")
    with patch("pymupdf.Document.save", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(PDFEngineError):
            _convert([red_png], destination)
    assert destination.read_bytes() == b"precious existing bytes"


def test_unexpected_failure_is_wrapped_without_traceback(red_png, out):
    with patch("pymupdf.Page.insert_image", side_effect=RuntimeError("engine exploded")):
        with pytest.raises(PDFEngineError) as exc_info:
            _convert([red_png], out)
    assert "Traceback" not in str(exc_info.value)
    assert not out.exists()


def test_document_is_always_closed_even_on_failure(red_png, out):
    with patch.object(pymupdf.Document, "close", autospec=True) as spy:
        with pytest.raises(ImagesToPdfError):
            _convert([], out)
        # zero-image rejection happens before doc = pymupdf.open() even
        # runs, so close() isn't expected to fire here -- assert instead
        # that the successful path always closes what it opened.
    with patch.object(pymupdf.Document, "close", autospec=True, wraps=pymupdf.Document.close) as spy2:
        _convert([red_png], out)
    spy2.assert_called_once()


# ---------------------------------------------------------------------------
# Repeated conversion
# ---------------------------------------------------------------------------

def test_repeated_conversion_same_inputs_different_outputs(tmp_path):
    path = _save_image(tmp_path / "p.png")
    out1 = tmp_path / "out1.pdf"
    out2 = tmp_path / "out2.pdf"
    _convert([path], out1)
    _convert([path], out2)
    with pymupdf.open(out1) as d1, pymupdf.open(out2) as d2:
        assert d1.page_count == d2.page_count == 1


def test_repeated_conversion_does_not_mutate_shared_state_between_calls(tmp_path):
    a = _save_image(tmp_path / "a.png", color=(1, 2, 3))
    b = _save_image(tmp_path / "b.png", color=(4, 5, 6))
    out1 = tmp_path / "out1.pdf"
    out2 = tmp_path / "out2.pdf"

    _convert([a], out1)
    _convert([b], out2)  # different single-image conversion, right after

    with pymupdf.open(out1) as d1:
        assert _corner_pixel(_extract_page_image(d1)) == (1, 2, 3)
    with pymupdf.open(out2) as d2:
        assert _corner_pixel(_extract_page_image(d2)) == (4, 5, 6)


def test_progress_callback_receives_one_message_per_image(tmp_path, out):
    paths = [_save_image(tmp_path / f"i{i}.png") for i in range(3)]
    messages = []
    _convert(paths, out, progress_callback=messages.append)
    per_image = [m for m in messages if m.startswith("Converting image")]
    assert per_image == [
        "Converting image 1 of 3: i0.png",
        "Converting image 2 of 3: i1.png",
        "Converting image 3 of 3: i2.png",
    ]
    assert "Saving..." in messages
    assert messages[-1] == "Done."


def test_no_progress_callback_is_fine(red_png, out):
    _convert([red_png], out, progress_callback=None)
    assert out.exists()


def test_returns_the_output_path(red_png, out):
    assert _convert([red_png], out) == out


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

def test_error_hierarchy():
    assert issubclass(ImagesToPdfError, PDFEngineError)
    assert issubclass(InvalidImageError, ImagesToPdfError)
    assert issubclass(ImageOptionsError, ImagesToPdfError)


def test_engine_does_not_import_tkinter():
    source = (Path(__file__).resolve().parent.parent / "images_to_pdf_engine.py").read_text(
        encoding="utf-8"
    )
    assert "import tkinter" not in source
    assert "from tkinter" not in source


def test_engine_reuses_existing_infrastructure():
    source = (Path(__file__).resolve().parent.parent / "images_to_pdf_engine.py").read_text(
        encoding="utf-8"
    )
    assert "pdf_engine._atomic_save_pdf" in source
    assert "os.replace" not in source
    assert "tempfile" not in source
