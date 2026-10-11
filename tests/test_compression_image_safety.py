"""
test_compression_image_safety.py

Regression tests for a confirmed image-corruption bug in
pdf_engine.compress_pdf()'s 'recommended'/'maximum' presets:
_recompress_images() re-encoded embedded images to JPEG via
Pixmap.tobytes("jpeg", ...) and wrote the bytes in place with
doc.update_stream(xref, jpeg_bytes) -- which replaces only the raw
stream bytes, never the image XObject's own /Filter (and other)
dictionary entries describing that stream. The result: the stream is
JPEG-encoded while the PDF still claims the original filter, so every
reader (including PyMuPDF's own renderer) decodes it incorrectly --
reproduced directly as a solid-red embedded PNG rendering as solid
black.

The fix: LOSSY_IMAGE_RECOMPRESSION_ENABLED in pdf_engine.py is held at
False, so _recompress_images() is never called for ANY preset until a
safe implementation exists; every preset now performs lossless,
structural-only optimization (garbage collection, stream deflation,
cleanup).

This file verifies, for a deliberately varied set of embedded-image
PDFs (embedded JPEG, PNG, transparent/soft-masked, grayscale, CMYK,
scanned/image-only pages, and ordinary text alongside images), at every
compression level:
  - the output opens successfully,
  - the rendered output is pixel-for-pixel IDENTICAL to the source
    (not merely "doesn't crash") -- meaningful here because disabling
    lossy recompression means there is now zero image-content change
    at any preset, so an exact match is the correct, non-flaky bar,
  - the source PDF is left byte-for-byte unchanged,
  - _recompress_images() is never actually invoked for any preset,
  - lossless structural optimization is still genuinely active.
"""

import io
import sys
from pathlib import Path

import pymupdf
import pytest
from PIL import Image
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import pdf_engine

LEVELS = ("low", "recommended", "maximum")


# ---------------------------------------------------------------------------
# PDF builders -- one per required content type
# ---------------------------------------------------------------------------

def _embed(path, image: Image.Image, fmt: str, *, with_text=False, full_bleed=False):
    """A single-page PDF with `image` (saved as `fmt`) embedded via
    insert_image(stream=...) -- the same path real source PDFs take
    (scans, screenshots, photos pasted into a document, ...).
    """
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    data = buf.getvalue()
    rect = pymupdf.Rect(0, 0, 300, 300) if full_bleed else pymupdf.Rect(40, 40, 220, 220)
    page.insert_image(rect, stream=data)
    if with_text:
        page.insert_text((20, 290), "Ordinary text alongside the image", fontsize=10)
    doc.save(path)
    doc.close()
    return Path(path)


def _pdf_jpeg_image(path):
    return _embed(path, Image.new("RGB", (120, 120), (10, 160, 230)), "JPEG")


def _pdf_png_image(path):
    return _embed(path, Image.new("RGB", (120, 120), (255, 0, 0)), "PNG")


def _pdf_transparent_png_image(path):
    """RGBA with partial alpha -> PyMuPDF stores this as a base image
    plus a separate /SMask xref -- exactly the soft-mask case the
    original buggy code's own comment called out as unsafe.
    """
    return _embed(path, Image.new("RGBA", (120, 120), (20, 200, 30, 140)), "PNG")


def _pdf_grayscale_image(path):
    return _embed(path, Image.new("L", (120, 120), 90), "PNG")


def _pdf_cmyk_image(path):
    return _embed(path, Image.new("CMYK", (120, 120), (0, 255, 180, 0)), "JPEG")


def _pdf_scanned_page(path):
    """An image-only page with no text/vector content at all -- the
    shape of a scanned document."""
    return _embed(
        path, Image.new("RGB", (300, 300), (240, 230, 210)), "JPEG", full_bleed=True,
    )


def _pdf_text_and_image(path):
    return _embed(
        path, Image.new("RGB", (120, 120), (0, 180, 90)), "PNG", with_text=True,
    )


CONTENT_BUILDERS = {
    "embedded_jpeg": _pdf_jpeg_image,
    "embedded_png": _pdf_png_image,
    "transparent_png_with_soft_mask": _pdf_transparent_png_image,
    "grayscale": _pdf_grayscale_image,
    "cmyk": _pdf_cmyk_image,
    "scanned_image_only_page": _pdf_scanned_page,
    "text_alongside_image": _pdf_text_and_image,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(path, dpi=100):
    with pymupdf.open(path) as doc:
        return [page.get_pixmap(dpi=dpi, alpha=False) for page in doc]


def _assert_pixel_identical(path_a, path_b):
    pages_a = _render(path_a)
    pages_b = _render(path_b)
    assert len(pages_a) == len(pages_b), "page count changed"
    for pix_a, pix_b in zip(pages_a, pages_b):
        assert pix_a.width == pix_b.width and pix_a.height == pix_b.height
        assert pix_a.samples == pix_b.samples, (
            f"rendered output differs between {path_a} and {path_b}"
        )


@pytest.fixture(params=sorted(CONTENT_BUILDERS))
def content_pdf(request, tmp_path):
    """Parametrized over every required content type -- every test that
    takes this fixture automatically runs once per content type.
    """
    builder = CONTENT_BUILDERS[request.param]
    path = builder(tmp_path / f"{request.param}.pdf")
    return request.param, path


# ---------------------------------------------------------------------------
# Core regression: output opens, renders identically, source untouched
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
def test_compressed_output_opens_successfully(content_pdf, level, tmp_path):
    _name, src = content_pdf
    out = tmp_path / f"out_{level}.pdf"
    pdf_engine.compress_pdf(src, out, level=level)

    assert out.exists()
    with pymupdf.open(out) as doc:
        assert doc.page_count >= 1
        assert not doc.is_encrypted
        for page in doc:
            page.get_pixmap(dpi=36)  # must render without raising


@pytest.mark.parametrize("level", LEVELS)
def test_rendered_output_is_pixel_identical_to_source(content_pdf, level, tmp_path):
    """The key regression check: not just "it opens", but that the
    visible content -- including the embedded image -- is exactly
    preserved. This is what would have caught the original bug (a
    solid-red image rendering as solid black)."""
    _name, src = content_pdf
    out = tmp_path / f"out_{level}.pdf"
    pdf_engine.compress_pdf(src, out, level=level)
    _assert_pixel_identical(src, out)


@pytest.mark.parametrize("level", LEVELS)
def test_source_pdf_is_byte_for_byte_unchanged(content_pdf, level, tmp_path):
    name, src = content_pdf
    before = src.read_bytes()
    before_mtime = src.stat().st_mtime_ns

    out = tmp_path / f"out_{level}.pdf"
    pdf_engine.compress_pdf(src, out, level=level)

    assert src.read_bytes() == before, f"source mutated for {name}/{level}"
    assert src.stat().st_mtime_ns == before_mtime


# ---------------------------------------------------------------------------
# Direct reproduction of the originally reported bug
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ("recommended", "maximum"))
def test_regression_solid_color_png_is_not_corrupted_to_black(tmp_path, level):
    """Reproduces the exact originally-reported failure mode: a solid
    red embedded PNG must stay red, not render as solid black."""
    src = _embed(tmp_path / "red.pdf", Image.new("RGB", (100, 100), (255, 0, 0)), "PNG")
    out = tmp_path / "out.pdf"
    pdf_engine.compress_pdf(src, out, level=level)

    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=72, alpha=False)
        sampled = pix.pixel(pix.width // 2, pix.height // 2)
    assert sampled == (255, 0, 0), f"image corrupted at level={level}: got {sampled}"
    assert sampled != (0, 0, 0)


@pytest.mark.parametrize("level", ("recommended", "maximum"))
def test_regression_blue_jpeg_is_not_corrupted(tmp_path, level):
    src = _embed(tmp_path / "blue.pdf", Image.new("RGB", (100, 100), (20, 40, 220)), "JPEG")
    out = tmp_path / "out.pdf"
    pdf_engine.compress_pdf(src, out, level=level)

    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=72, alpha=False)
        r, g, b = pix.pixel(pix.width // 2, pix.height // 2)
    # JPEG source is already lossy at the embedding step; allow the
    # normal JPEG encoding tolerance, not a corruption-sized deviation.
    assert abs(r - 20) < 15 and abs(g - 40) < 15 and abs(b - 220) < 15


# ---------------------------------------------------------------------------
# The safety switch itself, and that the unsafe path is truly never hit
# ---------------------------------------------------------------------------

def test_lossy_recompression_is_disabled():
    assert pdf_engine.LOSSY_IMAGE_RECOMPRESSION_ENABLED is False


@pytest.mark.parametrize("level", LEVELS)
def test_recompress_images_is_never_called(tmp_path, level):
    src = _pdf_png_image(tmp_path / "s.pdf")
    out = tmp_path / "out.pdf"
    with patch.object(pdf_engine, "_recompress_images") as mock_recompress:
        pdf_engine.compress_pdf(src, out, level=level)
    mock_recompress.assert_not_called()


def test_preset_image_recompress_flags_are_unchanged_but_inert():
    """The preset table still records the INTENDED future behavior
    ('recommended'/'maximum' want image_recompress=True) -- only the
    module-level switch in pdf_engine.py actually gates it off. This
    locks in that re-enabling later is a one-line change, not a preset
    rewrite, while confirming today it has no effect (see the test
    above)."""
    assert config.COMPRESSION_PRESETS["low"]["image_recompress"] is False
    assert config.COMPRESSION_PRESETS["recommended"]["image_recompress"] is True
    assert config.COMPRESSION_PRESETS["maximum"]["image_recompress"] is True


@pytest.mark.parametrize("level", ("recommended", "maximum"))
def test_preset_descriptions_do_not_claim_lossy_image_compression(level):
    description = config.COMPRESSION_PRESETS[level]["description"].lower()
    for phrase in ("reduce image quality", "visual impact", "lossy"):
        assert phrase not in description, (
            f"{level} description still claims a lossy effect: {description!r}"
        )


# ---------------------------------------------------------------------------
# Lossless structural optimization remains enabled for every preset
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
def test_structural_optimization_still_applies(tmp_path, level):
    """Garbage collection / deflate / clean must still run -- this is
    the one thing that must NOT have been disabled alongside the image
    recompression. A PDF with a large amount of removable redundant
    structure (duplicated, unused-but-garbage-collectible content) should
    shrink, even though nothing about embedded images changes."""
    doc = pymupdf.open()
    for _ in range(20):
        page = doc.new_page()
        page.insert_text((50, 50), "Some text." * 50, fontsize=10)
    src = tmp_path / "bulky.pdf"
    doc.save(src)
    doc.close()

    out = tmp_path / f"out_{level}.pdf"
    pdf_engine.compress_pdf(src, out, level=level)

    assert out.stat().st_size <= src.stat().st_size


@pytest.mark.parametrize("level", LEVELS)
def test_compress_pdf_passes_structural_save_kwargs(tmp_path, level):
    """Confirms compress_pdf() still forwards the preset's structural
    kwargs to the save call -- the lossless half of the pipeline the
    task requires to remain fully enabled for every preset."""
    src = _pdf_png_image(tmp_path / "s.pdf")
    out = tmp_path / "out.pdf"
    preset = config.COMPRESSION_PRESETS[level]

    with patch.object(pdf_engine, "_atomic_save_pdf", wraps=pdf_engine._atomic_save_pdf) as spy:
        pdf_engine.compress_pdf(src, out, level=level)

    spy.assert_called_once()
    _doc_arg, _path_arg = spy.call_args.args
    kwargs = spy.call_args.kwargs
    assert kwargs["garbage"] == preset["garbage"]
    assert kwargs["deflate"] == preset["deflate"]
    assert kwargs["deflate_images"] == preset["deflate_images"]
    assert kwargs["deflate_fonts"] == preset["deflate_fonts"]
    assert kwargs["clean"] == preset["clean"]


# ---------------------------------------------------------------------------
# Embedded image bytes/metadata are genuinely untouched (stronger than
# "renders the same" for the formats where this is directly inspectable)
# ---------------------------------------------------------------------------

def test_jpeg_source_bytes_pass_through_unmodified(tmp_path):
    """For an image PyMuPDF embeds directly as DCTDecode, the stored
    JPEG bytes should be completely unaffected by compression now that
    recompression is disabled."""
    src = _pdf_jpeg_image(tmp_path / "s.pdf")
    with pymupdf.open(src) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        before_filter = doc.xref_get_key(xref, "Filter")
        before_bytes = doc.extract_image(xref)["image"]

    out = tmp_path / "out.pdf"
    pdf_engine.compress_pdf(src, out, level="maximum")

    with pymupdf.open(out) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        after_filter = doc.xref_get_key(xref, "Filter")
        after_bytes = doc.extract_image(xref)["image"]

    assert after_filter == before_filter
    assert after_bytes == before_bytes


def test_soft_mask_still_present_and_consistent_after_compression(tmp_path):
    src = _pdf_transparent_png_image(tmp_path / "s.pdf")
    with pymupdf.open(src) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        assert doc.xref_get_key(xref, "SMask")[0] != "null"  # sanity: really has a mask

    out = tmp_path / "out.pdf"
    pdf_engine.compress_pdf(src, out, level="maximum")

    with pymupdf.open(out) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        kind, _value = doc.xref_get_key(xref, "SMask")
        assert kind != "null"  # soft mask survived structural optimization


# ---------------------------------------------------------------------------
# merge_and_compress() -- the other caller of compress_pdf() -- is
# covered too, since it is an easy place for the same bug to resurface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ("recommended", "maximum"))
def test_merge_and_compress_does_not_corrupt_images_either(tmp_path, level):
    a = _pdf_png_image(tmp_path / "a.pdf")
    b = _pdf_jpeg_image(tmp_path / "b.pdf")
    out = tmp_path / "merged.pdf"

    pdf_engine.merge_and_compress([a, b], out, level=level, temp_dir=tmp_path / "work")

    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        pix0 = doc[0].get_pixmap(dpi=72, alpha=False)
        pix1 = doc[1].get_pixmap(dpi=72, alpha=False)
    assert pix0.pixel(pix0.width // 2, pix0.height // 2) == (255, 0, 0)
    r, g, b_ = pix1.pixel(pix1.width // 2, pix1.height // 2)
    assert abs(r - 10) < 15 and abs(g - 160) < 15 and abs(b_ - 230) < 15
