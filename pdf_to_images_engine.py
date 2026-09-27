"""
pdf_to_images_engine.py

Phase 23: PDF -> Images -- render selected pages of a PDF to individual
PNG or JPEG image files, one file per rendered page. The reverse
conversion of Phase 22's Images -> PDF. Nothing in this file imports
tkinter.

Reuses pdf_engine.validate_pdf() (source validation, including the
existing encrypted-PDF rejection) and split_engine.parse_page_ranges()
(page-selection syntax) exactly like every other engine in this project,
rather than inventing new versions of either. Every page is rendered
directly by PyMuPDF's own Pixmap encoder (no Pillow involved anywhere in
this module -- see below).

PYMUPDF API -- INSPECTED AND VERIFIED EXPERIMENTALLY (PyMuPDF 1.28.2)
=======================================================================
Nothing below is assumed from memory; each point was confirmed against
the installed library by rendering a page and inspecting the actual
pixel data or encoder behavior.

- Page.get_pixmap() -- UNLIKE Page.insert_text() (see watermark_engine.py
  and page_numbers_engine.py's own docstrings for that tool's very
  different, non-obvious coordinate behavior) -- already accounts for
  the page's own /Rotate internally: rendering a 200x300 page rotated
  90 degrees produces a 300x200 pixmap with content in the correct,
  normally-displayed orientation. Verified for 0/90/180/270 degree
  pages by drawing an asymmetric marker on the page and confirming it
  renders in the correct DISPLAYED corner every time. No manual
  rotation/derotation matrix handling is needed here at all -- the one
  thing this module's own rendering matrix controls is DPI scale, never
  rotation.
- get_pixmap() accepts a `dpi=` shortcut, but this module instead always
  builds an explicit `matrix=pymupdf.Matrix(scale, scale)` from
  `dpi / 72.0` (PDF's native 72-points-per-inch coordinate space,
  confirmed by rendering at dpi=72 and getting back a pixmap whose
  pixel dimensions exactly equal the page's point dimensions) via
  dpi_to_matrix() below -- an explicit, independently testable pure
  function, rather than leaving the DPI-to-scale conversion implicit
  inside a library shortcut.
- get_pixmap(alpha=False) (the default) composites the page onto an
  OPAQUE WHITE background; get_pixmap(alpha=True) instead leaves any
  area the page didn't actually paint on as fully transparent
  (RGBA (0, 0, 0, 0)), verified by rendering a page with nothing drawn
  on it. This module always renders with alpha=False, for BOTH PNG and
  JPEG output: a blank/mostly-empty PDF page is the overwhelmingly
  common case for the kind of documents this app's other tools work
  with (typed pages, scans, forms), and a page that renders as
  partially transparent would look broken to someone expecting a normal
  white page image, not like a deliberately designed transparent asset.
  JPEG has no alpha channel at all -- attempting to encode a 4-channel
  pixmap as JPEG was confirmed to fail outright ("'{output}' cannot
  have alpha") -- so alpha=False is required there regardless; using it
  for PNG too keeps both formats' visual behavior consistent and
  predictable.
- Pixmap.tobytes("png") and Pixmap.tobytes("jpg", jpg_quality=N) both
  encode directly, entirely inside PyMuPDF, with no Pillow involvement
  and no intermediate file -- confirmed by decoding the returned bytes
  back with Pillow (already a project dependency, used only by this
  module's own tests, never at runtime) and checking format/size/mode.
  This is why Pillow is not imported anywhere in this module: PyMuPDF's
  own encoder is reliable and sufficient, per the Phase 23 requirement
  not to add or lean on an unnecessary dependency for this direction of
  conversion.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

import pymupdf

import file_manager
import pdf_engine
import split_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class PdfToImagesError(PDFEngineError):
    """Base class for every PDF -> Images specific error."""


class ImageOptionsError(PdfToImagesError):
    """Raised for an invalid image format, DPI, or JPEG quality."""


# ---------------------------------------------------------------------------
# Constants (the engine API contract -- NOT UI display labels)
# ---------------------------------------------------------------------------

#: Stable internal format identifiers. UI display labels ("PNG", "JPEG")
#: are owned entirely by ui.py; these are never shown to the user.
FORMAT_CHOICES = ("png", "jpeg")
DEFAULT_FORMAT = "png"

#: The file extension actually written for each format -- "jpeg" the
#: identifier, but ".jpg" the extension, matching the far more common
#: convention for JPEG files on Windows (and file_manager.
#: select_image_files()'s own filter, which lists "*.jpg" first).
_EXTENSIONS = {"png": ".png", "jpeg": ".jpg"}

#: Recommended DPI presets for the UI's combobox (Phase 23 spec); the
#: validator below accepts any finite value in (0, MAX_DPI], not just
#: these -- they are presets, not an exhaustive enum (unlike, say,
#: watermark_engine.ROTATIONS, which really is a closed set).
DPI_CHOICES = (72, 96, 150, 200, 300)
DEFAULT_DPI = 150.0
#: 600 DPI on a normal Letter/A4 page is already an 8.5k x 11k-ish pixel
#: image (tens of megabytes uncompressed per page) -- a documented,
#: deliberate ceiling against uncontrolled memory use from a mistyped
#: value, not a hardware limit of PyMuPDF itself.
MAX_DPI = 600.0

#: Recommended JPEG-quality presets for the UI's combobox; same
#: "presets, not an enum" relationship to the validator as DPI_CHOICES.
JPEG_QUALITY_CHOICES = (50, 60, 70, 80, 90, 95, 100)
DEFAULT_JPEG_QUALITY = 90


# ---------------------------------------------------------------------------
# Option validation (pure: no PyMuPDF rendering, no filesystem I/O)
# ---------------------------------------------------------------------------

def validate_format(image_format) -> str:
    if not isinstance(image_format, str) or image_format not in FORMAT_CHOICES:
        raise ImageOptionsError(f"'{image_format}' is not a supported image format.")
    return image_format


def _to_float(value, label: str) -> float:
    if value is None or isinstance(value, bool):
        raise ImageOptionsError(f"{label} must be a number.")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ImageOptionsError(f"Enter {label.lower()}.")
        try:
            number = float(stripped)
        except ValueError:
            raise ImageOptionsError(f"{label} must be a number.")
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ImageOptionsError(f"{label} must be a number.")
    if not math.isfinite(number):
        raise ImageOptionsError(f"{label} must be a number.")
    return number


def validate_dpi(value) -> float:
    """Parses a rendering resolution in DPI. Rejects empty, non-numeric,
    NaN/infinite, zero, negative, and values above MAX_DPI.
    """
    dpi = _to_float(value, "DPI")
    if dpi <= 0:
        raise ImageOptionsError("DPI must be greater than zero.")
    if dpi > MAX_DPI:
        raise ImageOptionsError(f"DPI cannot be greater than {MAX_DPI:g}.")
    return dpi


def validate_jpeg_quality(value) -> int:
    """Parses a JPEG quality setting. Only meaningful (and only ever
    called by render_pdf_to_images() below) when the target format is
    "jpeg" -- PNG is lossless and this setting has no effect on it (see
    render_pdf_to_images()'s own docstring). Rejects empty, non-numeric,
    NaN/infinite, and anything outside the inclusive 1-100 range.
    """
    quality = _to_float(value, "JPEG quality")
    if not float(quality).is_integer():
        raise ImageOptionsError("JPEG quality must be a whole number.")
    quality = int(quality)
    if quality < 1 or quality > 100:
        raise ImageOptionsError("JPEG quality must be between 1 and 100.")
    return quality


# ---------------------------------------------------------------------------
# Page-selection resolution (pure: no PyMuPDF rendering, no filesystem I/O)
# ---------------------------------------------------------------------------

def resolve_pages_to_render(text: Optional[str], page_count: int) -> List[int]:
    """Parse a page-selection string into a flat list of 0-based page
    indices to render, reusing split_engine.parse_page_ranges() for the
    actual range syntax.

    `text=None` means "All Pages": every page, in natural PDF order,
    `[0, 1, ..., page_count - 1]`.

    For an explicit selection, this DELIBERATELY follows
    parse_page_ranges()'s own documented semantics exactly, rather than
    inventing different behavior for this tool (per the Phase 23 spec's
    explicit instruction to determine this rather than silently assume
    it): the parser returns one GROUP of page indices per comma-
    separated token, in the exact order the user typed them, with
    overlapping/duplicate tokens EXPLICITLY ALLOWED and never
    deduplicated (see that function's own docstring). This function
    simply flattens those groups, in order, into one list -- so
    "3-5,8" -> [2, 3, 4, 7] (pages 3, 4, 5, 8), and a selection that
    mentions the same page twice (e.g. "1-3,2-4") legitimately produces
    TWO rendered images for that page, exactly mirroring how Split PDF
    (Phase 13) already treats "1-3,2-4" as two separate output files
    rather than silently merging or deduplicating the overlap. This is
    a different choice than watermark_engine.resolve_pages_to_watermark()
    makes with the very same parser (which sorts and deduplicates) --
    appropriately so there, since watermarking a page twice has no
    separate-output-file concept to preserve; here, each render IS a
    separate output file, so the duplicate is meaningful and is kept.

    Example:
        resolve_pages_to_render("3-5,8", 10) -> [2, 3, 4, 7]
        resolve_pages_to_render("1-3,2-4", 10) -> [0, 1, 2, 1, 2, 3]
        resolve_pages_to_render(None, 5) -> [0, 1, 2, 3, 4]
    """
    if text is None:
        if page_count <= 0:
            raise split_engine.PageRangeError(
                "The document has no pages to render."
            )
        return list(range(page_count))

    groups = split_engine.parse_page_ranges(text, page_count)
    return [index for group in groups for index in group]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def dpi_to_matrix(dpi: float) -> "pymupdf.Matrix":
    """Converts a DPI value into the PyMuPDF rendering matrix that
    produces it: PDF space is natively 72 points per inch, so a page
    rendered with scale factor `dpi / 72` on both axes produces a pixmap
    at exactly that DPI (verified experimentally -- see this module's
    docstring). A pure function, independent of any actual page or
    document, so it is directly unit-testable.
    """
    scale = dpi / 72.0
    return pymupdf.Matrix(scale, scale)


def _format_page_suffix(page_number: int, page_count: int) -> str:
    """"_page_003" for page 3 of a document with e.g. 42 pages (3-digit
    minimum, widening only if the document has 1000+ pages) -- the same
    "at least 3 digits, more if the total needs it" convention
    split_engine._format_sequence() already establishes for Split PDF's
    own output naming, applied here to the ACTUAL PDF page number
    (1-based) rather than a renumbered sequence index, per the Phase 23
    spec's explicit "prefer document_page_008.png over an ambiguous
    renumbering" requirement.
    """
    width = max(3, len(str(page_count)))
    return f"_page_{str(page_number).zfill(width)}"


def _render_page_bytes(
    page: "pymupdf.Page", matrix: "pymupdf.Matrix", image_format: str, jpeg_quality: int,
) -> bytes:
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    if image_format == "png":
        return pixmap.tobytes("png")
    return pixmap.tobytes("jpg", jpg_quality=jpeg_quality)


def _atomic_write_bytes(data: bytes, output_path: Path) -> None:
    """Writes `data` to `output_path` atomically -- the Phase 23,
    image-file analogue of pdf_engine._atomic_save_pdf(): a temp file is
    written in the SAME directory as `output_path` first, and only
    replaces the real destination via os.replace() (atomic on both
    Windows and POSIX when source and destination share a filesystem,
    guaranteed here by using the same directory) once that write has
    fully succeeded. _atomic_save_pdf() itself isn't reused directly --
    it is built specifically around a pymupdf.Document's own .save(),
    not arbitrary bytes -- but this follows its exact temp-file
    strategy and error-translation behavior, so a permission error or a
    full disk here is reported exactly the same way every other engine
    in this project already reports one.
    """
    output_path = Path(output_path)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PDFEngineError(
            f"Could not create the destination folder "
            f"'{output_path.parent}': {exc.strerror or exc}."
        ) from exc

    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            suffix=output_path.suffix, prefix=".tmp_", dir=str(output_path.parent),
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        tmp_path = Path(tmp_name)
        os.replace(tmp_path, output_path)  # atomic: same directory/filesystem
        tmp_path = None  # successfully moved -- nothing left to clean up
    except OSError as exc:
        raise PDFEngineError(
            f"Could not save '{output_path.name}': {exc.strerror or exc}. "
            f"Choose a different location and try again."
        ) from exc
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("Could not remove temp file: %s", tmp_path)


def render_pdf_to_images(
    source_path: Path,
    output_dir: Path,
    page_indices: Optional[List[int]] = None,
    image_format: str = DEFAULT_FORMAT,
    dpi: float = DEFAULT_DPI,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """Render pages of `source_path` to individual image files in
    `output_dir`, one file per entry in `page_indices` (None = every
    page, in natural order) -- the direct reverse of Phase 22's
    images_to_pdf_engine.images_to_pdf().

    - Re-validates the source via pdf_engine.validate_pdf(): missing,
      corrupt, non-PDF, directory, and password-protected sources are
      all rejected before anything is rendered -- an encrypted source is
      never opened or bypassed here (this tool has no password handling
      of its own, matching every tool except Unlock PDF).
    - Never modifies the source: it is opened read-only and only ever
      read from; nothing is ever written back to it.
    - `page_indices` is re-validated against the just-opened document's
      actual page count (it may have changed since an earlier
      selection); an empty list, or any out-of-range index, is rejected
      before any file is written.
    - `jpeg_quality` is validated only when `image_format == "jpeg"` --
      for "png" it is accepted but entirely ignored, since PNG is
      lossless and has no meaningful "quality" setting (per the Phase 23
      spec's own "do not expose a misleading PNG quality control"
      guidance, extended here to the engine: a leftover/default quality
      value must never block a PNG render).
    - Filenames follow "<source stem>_page_<N>.<ext>", where <N> is the
      ACTUAL 1-based PDF page number (not a renumbered sequence index)
      zero-padded per _format_page_suffix()'s own convention, and <ext>
      is ".png" or ".jpg". Never overwrites an existing file: every
      output path is chosen via file_manager.generate_image_output_path(),
      which also guards against two renders in the SAME batch (a page
      selected more than once) claiming the same name before either
      exists on disk yet.
    - Writes each image atomically via _atomic_write_bytes() above.
    - ATOMICITY ACROSS THE WHOLE BATCH: if any page fails to render or
      write partway through, every output file THIS call has already
      created is deleted before the error propagates -- exactly
      mirroring split_engine.split_pdf()'s own whole-batch rollback --
      so a failure never leaves the destination folder holding a
      confusing, incomplete subset of the requested images. A
      pre-existing file with a colliding name is never touched (it was
      never a candidate in the first place), and the source PDF is
      never touched.
    - progress_callback, if given, receives one real, per-page message
      before that page is rendered -- "Rendering page 3 of 12" when
      rendering every page in natural order, or "Rendering page 8 (3 of
      5)" for a non-contiguous selection, so the person can always see
      which PDF page corresponds to which step -- never a fabricated
      percentage.

    Returns {"output_paths": [Path, ...], "total_images": int} on
    success. Raises ImageOptionsError for an invalid format/DPI/quality,
    PdfToImagesError/split_engine.PageRangeError for an invalid or empty
    page selection, and PDFEngineError for any other failure (a bad
    source, an unwritable output folder, a mid-batch render failure).
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    image_format = validate_format(image_format)
    dpi = validate_dpi(dpi)
    if image_format == "jpeg":
        jpeg_quality = validate_jpeg_quality(jpeg_quality)

    if page_indices is not None and len(page_indices) == 0:
        raise PdfToImagesError("No pages selected to render.")

    pdf_engine.validate_pdf(source_path)

    matrix = dpi_to_matrix(dpi)
    extension = _EXTENSIONS[image_format]

    created_paths: List[Path] = []
    reserved_names: set = set()

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PDFEngineError(
            f"Could not create the output folder '{output_dir}': "
            f"{exc.strerror or exc}."
        ) from exc

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            if page_indices is None:
                target_indices = list(range(page_count))
            else:
                target_indices = list(page_indices)
                out_of_range = [
                    index for index in target_indices
                    if not isinstance(index, int) or index < 0 or index >= page_count
                ]
                if out_of_range:
                    raise PdfToImagesError(
                        "The selected pages no longer match this document "
                        "-- it may have changed since it was selected."
                    )

            total = len(target_indices)
            rendering_every_page = page_indices is None

            for step, page_index in enumerate(target_indices, start=1):
                page_number = page_index + 1
                if progress_callback:
                    if rendering_every_page:
                        progress_callback(f"Rendering page {page_number} of {total}")
                    else:
                        progress_callback(
                            f"Rendering page {page_number} ({step} of {total})"
                        )

                try:
                    data = _render_page_bytes(
                        doc[page_index], matrix, image_format, jpeg_quality,
                    )
                except PDFEngineError:
                    raise
                except Exception as exc:
                    raise PDFEngineError(
                        f"Failed to render page {page_number}: {exc}"
                    ) from exc

                suffix = _format_page_suffix(page_number, page_count)
                output_path = file_manager.generate_image_output_path(
                    source_path, output_dir, suffix, extension,
                    exclude=reserved_names,
                )
                reserved_names.add(output_path)

                _atomic_write_bytes(data, output_path)
                created_paths.append(output_path)

        if progress_callback:
            progress_callback("Conversion completed successfully.")

        return {"output_paths": list(created_paths), "total_images": total}

    except Exception:
        for path in created_paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.warning(
                    "Could not remove partial PDF -> Images output: %s", path
                )
        raise
