"""
images_to_pdf_engine.py

Phase 22: Images -> PDF -- convert one or more image files, in a given
order, into a single new PDF (one page per image). Nothing in this file
imports tkinter.

Reuses pdf_engine's existing error hierarchy (PDFEngineError) and its
atomic-save machinery (pdf_engine._atomic_save_pdf()), exactly like every
other engine in this project, rather than inventing a second output
system. Every image is inserted as a real image XObject via PyMuPDF's
native Page.insert_image() -- nothing is rasterized from a PDF page (
there is no PDF page to rasterize: the source is already a raster image),
and no existing PDF content is ever touched.

DEPENDENCY / FORMAT INSPECTION -- NOT ASSUMED FROM MEMORY
=============================================================
The installed stack was inspected directly rather than assumed:

- Pillow 12.1.1 is already an installed dependency of this environment
  (it was not previously listed in requirements.txt; this phase adds
  it, since a reliable, well-tested image decoder -- correct EXIF
  handling, correct P/CMYK/RGBA conversion, deterministic first-frame
  selection for animated formats -- is exactly what Pillow is for, and
  re-implementing that on top of PyMuPDF's own, much thinner image
  loader would be reinventing a wheel this project already has
  available).
- `PIL.features.pilinfo()` confirms this Pillow build has working PNG,
  JPEG (libjpeg-turbo), BMP, TIFF (libtiff) and WEBP (libwebp 1.6.0)
  codecs -- i.e. every format the Phase 22 spec asks for "at minimum"
  is genuinely, reliably supported, not just nominally registered.
  SUPPORTED_EXTENSIONS below is exactly that list; nothing broader is
  claimed available.
- PyMuPDF's Page.insert_image() was inspected (not guessed) to accept
  a `pixmap=` argument. Feeding it a `pymupdf.Pixmap(pymupdf.csRGB,
  width, height, raw_rgb_bytes, False)` built directly from a Pillow
  image's own `.tobytes()` was verified, by rendering the result back
  and sampling pixels, to (a) preserve row order -- a Pillow image's
  row 0 (its top row) lands at the top of the placed rectangle on the
  displayed PDF page, with no unexpected vertical flip -- and (b)
  requires the Pixmap's pixel data to be exactly 3 bytes/pixel, i.e.
  the source image must already be true RGB by the time the Pixmap is
  built (see _prepare_image() below for how every input mode is
  normalized to RGB first).
- Pillow's `ImageOps.exif_transpose()` was verified, for all 8 EXIF
  Orientation values (1-8), to both physically re-orient the pixel
  data to match the image's intended visual orientation AND clear the
  Orientation tag (so nothing downstream re-applies it) -- this is
  what makes the resulting PDF page show the photo the way a camera
  or phone intended it to be viewed, without permanently modifying the
  source file (a new, transposed in-memory image is returned; the
  source JPEG on disk is never opened for writing).
- `Image.open()` on a multi-frame GIF or animated WEBP was verified to
  open positioned at frame 0 (`.tell()` is 0 immediately after open)
  without calling `.seek()` -- so simply never calling `.seek()` is
  the deterministic "first frame only" behavior Phase 22 asks for;
  `n_frames`/`.is_animated` are never consulted and no other frame is
  ever read.
- Pillow's per-image `dpi` metadata (`Image.info.get("dpi")`) was
  verified present for images saved with explicit DPI (JPEG, PNG, BMP
  -- BMP's is a unit-converted approximation, e.g. 96.01 rather than
  exactly 96) and absent for some formats even when doing so would be
  reasonable (plain PNG/WEBP saved without a dpi argument). This is
  why DPI is only ever used as an informational hint for "Original
  Image Ratio" pages (see PAGE_SIZES below) and is never treated as
  authoritative or required.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError

import pdf_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class ImagesToPdfError(PDFEngineError):
    """Base class for every Images -> PDF specific error."""


class InvalidImageError(ImagesToPdfError):
    """Raised for a missing, unreadable, corrupt, or unsupported image
    file -- one bad image is always reported by name, never as a raw
    traceback.
    """


class ImageOptionsError(ImagesToPdfError):
    """Raised for an invalid page-size choice or margin."""


# ---------------------------------------------------------------------------
# Supported formats (see this module's docstring for how this list was
# derived from the actually-installed Pillow build, not assumed)
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")

_SUPPORTED_EXTENSIONS_DISPLAY = "PNG, JPEG, BMP, TIFF, WEBP"


# ---------------------------------------------------------------------------
# Page size policy
# ---------------------------------------------------------------------------

#: Portrait dimensions in points (1 pt = 1/72 inch). A4 uses the standard
#: ISO 216 rounding (595.28 x 841.89 pt); Letter is the exact US Letter
#: size (612 x 792 pt = 8.5in x 11in).
PAGE_SIZES = {
    "a4": (595.28, 841.89),
    "letter": (612.0, 792.0),
}

PAGE_SIZE_CHOICES = ("a4", "letter", "original")
DEFAULT_PAGE_SIZE = "a4"

#: Matches watermark_engine.DEFAULT_MARGIN (0.5in) -- the same "reasonable
#: small margin" default, kept consistent across the app's tools.
DEFAULT_MARGIN = 36.0

#: DPI policy for "Original Image Ratio" pages (see compute_page_geometry()
#: below): an image's own embedded DPI is used when present and sane;
#: otherwise this documented, deterministic default (a common screen
#: density) is used instead. This is an assumed physical size for display
#: purposes only -- it is never presented to the user as the image's
#: "true" print resolution, since most images carry no reliable DPI at
#: all (see this module's docstring).
DEFAULT_DPI = 96.0
MIN_SANE_DPI = 10.0
MAX_SANE_DPI = 2400.0

MAX_MARGIN = 5000.0  # generous upper bound; anything past this is almost certainly a mistake


# ---------------------------------------------------------------------------
# Option validation (pure: no Pillow, no PyMuPDF, no filesystem I/O)
# ---------------------------------------------------------------------------

def validate_page_size(page_size) -> str:
    if not isinstance(page_size, str) or page_size not in PAGE_SIZE_CHOICES:
        raise ImageOptionsError(f"'{page_size}' is not a valid page size.")
    return page_size


def validate_margin(value) -> float:
    """Parses a margin in points. Rejects empty/non-numeric/NaN/infinite
    and negative values. `MAX_MARGIN` is a generous sanity ceiling, not
    the page-fit check -- a margin that is merely too large for the
    chosen page size is instead caught by compute_page_geometry(), since
    whether a given margin fits depends on the page size and (for A4/
    Letter) the auto-selected orientation.
    """
    if value is None or isinstance(value, bool):
        raise ImageOptionsError("Margin must be a number.")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ImageOptionsError("Enter a margin.")
        try:
            number = float(stripped)
        except ValueError:
            raise ImageOptionsError("Margin must be a number.")
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ImageOptionsError("Margin must be a number.")
    if not math.isfinite(number):
        raise ImageOptionsError("Margin must be a number.")
    if number < 0:
        raise ImageOptionsError("Margin cannot be negative.")
    if number > MAX_MARGIN:
        raise ImageOptionsError(f"Margin cannot be larger than {MAX_MARGIN:g} points.")
    return number


# ---------------------------------------------------------------------------
# Pure placement geometry (independent of any image/PDF I/O -- see
# test_images_to_pdf_engine.py's dedicated geometry tests)
# ---------------------------------------------------------------------------

def compute_page_geometry(
    pixel_width: int, pixel_height: int, page_size: str, margin: float,
    image_dpi: Optional[float] = None,
) -> Tuple[float, float, Tuple[float, float, float, float]]:
    """Returns (page_width_pt, page_height_pt, (x0, y0, x1, y1)):

    - page_width_pt / page_height_pt: the PDF page's size, in points;
    - (x0, y0, x1, y1): where the image is placed on that page, in points,
      in PDF page space (origin top-left, matching PyMuPDF's own Rect
      convention).

    "original" page size: the page is exactly the image's own size
    (mapped from pixels to points via `image_dpi`, or DEFAULT_DPI if
    none was given/it was outside the sane range) plus `margin` on
    every side -- the image is never scaled.

    "a4"/"letter": the page uses PAGE_SIZES' fixed physical page size,
    in whichever of portrait/landscape better matches the image's own
    aspect ratio (landscape only when the image is strictly wider than
    it is tall; ties -- including perfectly square images -- default to
    portrait, a deterministic, arbitrary-but-documented choice). The
    image is then scaled UNIFORMLY (both axes by the same factor, so it
    is never stretched) to fit as large as possible inside the page
    minus `margin` on every side, and centered there.

    Raises ImageOptionsError if `margin` leaves no positive area to
    place the image in (a "the margin is too large for this page size"
    configuration) -- this can only happen for "a4"/"letter", since
    "original" pages are sized to always leave exactly `margin` of
    room by construction.
    """
    if pixel_width <= 0 or pixel_height <= 0:
        raise InvalidImageError("Image has no usable pixel data.")

    if page_size == "original":
        dpi = image_dpi
        if dpi is None or not math.isfinite(dpi) or not (MIN_SANE_DPI <= dpi <= MAX_SANE_DPI):
            dpi = DEFAULT_DPI
        image_width_pt = pixel_width / dpi * 72.0
        image_height_pt = pixel_height / dpi * 72.0
        page_width = image_width_pt + 2 * margin
        page_height = image_height_pt + 2 * margin
        rect = (margin, margin, margin + image_width_pt, margin + image_height_pt)
        return page_width, page_height, rect

    base_width, base_height = PAGE_SIZES[page_size]
    if pixel_width > pixel_height:
        page_width, page_height = max(base_width, base_height), min(base_width, base_height)
    else:
        page_width, page_height = min(base_width, base_height), max(base_width, base_height)

    available_width = page_width - 2 * margin
    available_height = page_height - 2 * margin
    if available_width <= 0 or available_height <= 0:
        raise ImageOptionsError(
            "The margin is too large for the selected page size."
        )

    scale = min(available_width / pixel_width, available_height / pixel_height)
    fit_width = pixel_width * scale
    fit_height = pixel_height * scale
    x0 = (page_width - fit_width) / 2.0
    y0 = (page_height - fit_height) / 2.0
    rect = (x0, y0, x0 + fit_width, y0 + fit_height)
    return page_width, page_height, rect


# ---------------------------------------------------------------------------
# Image validation / loading (impure: touches the filesystem via Pillow)
# ---------------------------------------------------------------------------

def validate_image_file(path: Path) -> None:
    """Cheap, extension/existence-level checks -- always run before any
    attempt to actually decode the file. Does NOT by itself prove the
    file is a valid, undamaged image; see _open_and_verify() for that.
    """
    path = Path(path)
    if not path.exists():
        raise InvalidImageError(f"'{path.name}' does not exist.")
    if path.is_dir():
        raise InvalidImageError(f"'{path.name}' is a folder, not an image file.")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidImageError(
            f"'{path.name}' is not a supported image type. Supported "
            f"formats: {_SUPPORTED_EXTENSIONS_DISPLAY}."
        )


def _open_and_verify(path: Path) -> None:
    """Actually attempts to open and verify the image -- per the Phase 22
    requirement not to trust the extension alone. Image.verify() checks
    the file is well-formed without fully decoding pixel data; the
    handle it was called on is unusable afterward (a Pillow constraint),
    so this is a throwaway probe -- callers reopen a fresh handle for
    real use.
    """
    try:
        with Image.open(path) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            f"'{path.name}' is not a valid or readable image file."
        ) from exc


def get_image_info(path: Path) -> dict:
    """Validates `path` as a usable image and returns its display info
    -- the Images -> PDF analogue of pdf_engine.get_pdf_info(), used to
    build a models.ImageFile entry at import time. Cheap relative to a
    full conversion: decodes the image once to confirm it is genuinely
    readable and to read its pixel dimensions, but does not apply EXIF
    transposition, mode normalization, or any other conversion-time
    work (that happens again, from scratch, in _prepare_image() at
    actual conversion time -- see images_to_pdf()'s docstring for why
    re-validating then, not just trusting this earlier check, matters).
    """
    path = Path(path)
    validate_image_file(path)
    _open_and_verify(path)

    try:
        with Image.open(path) as img:
            width, height = img.size
            img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            f"'{path.name}' is not a valid or readable image file."
        ) from exc

    size = path.stat().st_size
    return {"path": path, "name": path.name, "size": size, "width": width, "height": height}


def _extract_dpi(img: "Image.Image") -> Optional[float]:
    """The image's own embedded DPI (x-axis), or None if absent/unusable.
    See this module's docstring for how inconsistently this metadata is
    actually populated across formats/encoders -- never treated as
    required.
    """
    dpi_info = img.info.get("dpi")
    if not dpi_info:
        return None
    try:
        dpi = float(dpi_info[0])
    except (TypeError, ValueError, IndexError):
        return None
    if not math.isfinite(dpi) or dpi <= 0:
        return None
    return dpi


def _prepare_image(path: Path) -> Tuple["Image.Image", Optional[float]]:
    """Opens `path` fresh, decodes it, and returns (image, dpi) ready for
    Pixmap construction and compute_page_geometry():

    - `image` is always in RGB mode (3 bytes/pixel), matching the
      constraint Pixmap construction needs (see this module's
      docstring) -- an image with transparency (RGBA/LA, or a
      palette image with a transparency entry) is composited onto an
      OPAQUE WHITE background rather than silently dropping the alpha
      channel (which would otherwise leave whatever undefined garbage
      the codec put in the "color" channels of fully transparent
      pixels) or producing a black background (dropping alpha in a
      naive .convert("RGB") without compositing first would do
      exactly that for many codecs). This is a deliberate, documented,
      and tested choice, not an incidental side effect of format
      conversion.
    - Any other non-RGB mode (L, P without transparency, CMYK, etc.)
      is converted to RGB with Pillow's own .convert("RGB") --
      correct for grayscale (fans out one channel to three equal
      ones) and for a palette image (resolves indices through the
      palette). CMYK's conversion is Pillow's uncalibrated formula
      (no embedded/absolute ICC transform is applied) -- adequate for
      this tool's purpose, and documented as a known limitation in
      the Phase 22 completion report rather than left unstated.
    - EXIF orientation (JPEG/TIFF's Orientation tag) is applied via
      Pillow's own exif_transpose() BEFORE the mode conversion above,
      so the final pixel data is both correctly oriented and correctly
      colored.
    - Multi-frame images (animated GIF/WEBP) are read at whatever frame
      Image.open() itself opens them at -- frame 0 -- since .seek() is
      never called; see this module's docstring for the experimental
      verification of that default.
    - The source file itself is only ever opened for reading here; it
      is never written to, so it is never modified by this function.

    Raises InvalidImageError if the file can no longer be decoded (it
    may have changed or been deleted since an earlier, successful
    get_image_info() call).
    """
    path = Path(path)
    try:
        opened = Image.open(path)
        opened.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            f"'{path.name}' is not a valid or readable image file."
        ) from exc

    # exif_transpose() always returns an independent, fully in-memory
    # image (a new one when a rotation/flip was applied, or the very
    # same object when orientation 1/absent needs no change) -- either
    # way, .load() above has already materialized every pixel this
    # function will ever need, so the underlying file handle is closed
    # right after, rather than left for the garbage collector to close
    # later. This matters most for GIF/WEBP: Pillow's lazy, multi-frame
    # decoders keep their file handle open past .load() more often than
    # single-frame codecs do, which otherwise surfaces as a
    # ResourceWarning under this project's warnings-as-errors test run.
    img = ImageOps.exif_transpose(opened) or opened
    opened.close()

    dpi = _extract_dpi(img)

    has_transparency = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if has_transparency:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    return img, dpi


# ---------------------------------------------------------------------------
# Images -> PDF engine
# ---------------------------------------------------------------------------

def images_to_pdf(
    image_paths: List[Path],
    output_path: Path,
    page_size: str = DEFAULT_PAGE_SIZE,
    margin: float = DEFAULT_MARGIN,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` with exactly one page per entry
    in `image_paths`, in that exact order -- the displayed list order
    is authoritative; nothing is sorted or deduplicated here (a path
    listed twice produces two pages, per the Phase 22 "do not silently
    deduplicate" requirement -- this function never even looks at
    identity/equality between entries, so that behavior falls out
    naturally rather than needing special-case handling).

    - Every image is re-opened, re-verified, and re-decoded here from
      scratch (via _prepare_image()) rather than trusting whatever an
      earlier get_image_info() call found -- an image selected earlier
      could have been deleted, replaced, or corrupted since. This is
      the same "re-validate at the point of use" convention every
      other engine in this project follows for its own sources.
    - Never modifies any source image file: every one is opened
      read-only; only the new output PDF is ever written to.
    - `page_size` (one of PAGE_SIZE_CHOICES) and `margin` are validated
      once, up front, before any image is touched or any output file
      is created -- an invalid option must never leave a partial
      output behind.
    - Placement uses compute_page_geometry() (pure, independently
      tested) for every page, so a bad margin/page-size combination
      for one particular image's aspect ratio is caught and reported
      by name, and nothing is written.
    - Sets PDF metadata with a sensible generated title ("Images to
      PDF") -- this is a new document, not a preserved one, so no
      attempt is made to carry over per-image EXIF/metadata beyond
      that; see this module's docstring for that scope decision.
    - Writes atomically via pdf_engine._atomic_save_pdf(): a failure
      partway through (a bad image discovered on page 3 of 5, a full
      disk on save, ...) never leaves a partially-written output file
      at `output_path` -- the in-memory pymupdf.Document being built is
      simply discarded, and _atomic_save_pdf() itself only ever
      replaces the destination once a complete temp file has been
      written successfully.
    - progress_callback, if given, receives one real, per-image message
      ("Converting image 2 of 5: photo.jpg") immediately before that
      image is opened -- never a fabricated percentage.

    Raises ImageOptionsError for an invalid page size/margin,
    InvalidImageError (naming the offending file) for any image that
    cannot be used, and PDFEngineError for any other failure (e.g. the
    final save). Raises ImagesToPdfError("No images selected.") for an
    empty `image_paths` -- the Create PDF action must never be started
    with zero images in the first place, but this is re-checked here
    too as defense in depth.
    """
    image_paths = [Path(p) for p in image_paths]
    output_path = Path(output_path)

    if not image_paths:
        raise ImagesToPdfError("No images selected.")

    page_size = validate_page_size(page_size)
    margin = validate_margin(margin)

    total = len(image_paths)
    doc = pymupdf.open()
    try:
        for index, path in enumerate(image_paths, start=1):
            if progress_callback:
                progress_callback(f"Converting image {index} of {total}: {path.name}")

            validate_image_file(path)
            img, dpi = _prepare_image(path)

            page_width, page_height, rect = compute_page_geometry(
                img.width, img.height, page_size, margin, dpi,
            )

            page = doc.new_page(width=page_width, height=page_height)
            pixmap = pymupdf.Pixmap(
                pymupdf.csRGB, img.width, img.height, img.tobytes(), False,
            )
            page.insert_image(pymupdf.Rect(*rect), pixmap=pixmap, keep_proportion=False)

        doc.set_metadata({"title": "Images to PDF"})

        if progress_callback:
            progress_callback("Saving...")

        pdf_engine._atomic_save_pdf(doc, output_path)
    except ImagesToPdfError:
        raise
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to create the PDF: {exc}"
        ) from exc
    finally:
        doc.close()

    if progress_callback:
        progress_callback("Done.")

    return output_path
