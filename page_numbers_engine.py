"""
page_numbers_engine.py

Phase 20: Add Page Numbers processing -- inserting a simple, sequential
page number onto a chosen set of pages, in one of six corner/edge
positions, correctly oriented for the page's existing rotation, leaving
the source completely untouched. Nothing in this file imports tkinter.

Reuses split_engine.parse_page_ranges() for page-selection text
parsing, exactly like remove_pages_engine.py, extract_engine.py, and
rotate_engine.py do. Follows rotate_engine's/remove_pages_engine's SET
semantics (sorted, de-duplicated) rather than extract_engine's order-
preserving semantics: a page named twice in a range expression (e.g.
"1-3,2-4") must still only receive one page number, not two stamped on
top of each other.

Like every other engine in this project, reuses pdf_engine's existing
validation and atomic-save machinery rather than creating a second
output-saving system.

PYMUPDF API -- INSPECTED, NOT ASSUMED
=======================================
Before writing anything, the installed library (PyMuPDF 1.28.2) was
inspected directly and its rotation-handling behavior confirmed
experimentally (see tests/test_page_numbers_engine.py for the tests
that encode these findings):

- Page.insert_text()'s `point` argument is in the page's UNROTATED
  content-stream coordinate space, NOT the visually displayed space --
  confirmed experimentally: inserting text at a point computed from
  Page.rect (which DOES already reflect the page's current rotation,
  e.g. a 90-degree-rotated 200x300 page reports rect width/height as
  300x200) lands the text in the wrong on-screen corner once rendered,
  unless that point is first converted through Page.derotation_matrix.
- Page.rotation_matrix / Page.derotation_matrix are real, PyMuPDF-
  native properties (not invented here) that convert between the
  visually displayed coordinate space and the unrotated content-stream
  space. This module always computes a desired position in DISPLAY
  space (using Page.rect, so "top left" always means the visual top
  left regardless of rotation) and converts it to content-stream space
  via `display_point * page.derotation_matrix` before calling
  insert_text() -- confirmed experimentally (rendering each rotation to
  a pixmap and checking the number's visual position) to land the
  number in the intended on-screen corner for 0, 90, 180, and 270
  degrees.
- insert_text() additionally takes a `rotate` argument that rotates the
  glyphs themselves (not just their position); passing
  `rotate=page.rotation` keeps the number upright as displayed,
  matching the page's own rotation.
- insert_text()'s default `overlay=True` draws into a new, appended
  content stream rather than rewriting existing page content -- this is
  what lets a page number be added without rasterizing the page,
  rebuilding it through images, or otherwise disturbing existing text,
  images, or annotations already on the page (per the Phase 20 spec's
  explicit "prefer overlay, don't rasterize, don't flatten"
  requirement).
- pymupdf.get_text_length() (a module-level function, not a Page
  method) measures a string's rendered width for a given font/size --
  used here to right-align or center a page number without needing to
  guess or hard-code character widths.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, List, Optional

import pymupdf

import pdf_engine
import split_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class PageNumberOptionsError(PDFEngineError):
    """Raised for an invalid start number, position, font size, margin,
    or page selection. A PDFEngineError subclass, exactly like every
    other engine's own domain-specific error classes.
    """


#: The six supported positions -- a stable, internal string convention
#: (not UI display text) that every function in this module and its
#: caller in ui.py agree on. Follows the exact naming the Phase 20 spec
#: itself recommends.
POSITIONS = (
    "top_left", "top_center", "top_right",
    "bottom_left", "bottom_center", "bottom_right",
)

DEFAULT_START_NUMBER = 1
DEFAULT_POSITION = "bottom_center"
DEFAULT_FONT_SIZE = 11.0
DEFAULT_MARGIN = 36.0

_FONT_NAME = "helv"  # a standard PDF14 font -- always available, no font file needed

_INTEGER_RE = re.compile(r"-?\d+")


# ---------------------------------------------------------------------------
# Page-selection resolution (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def resolve_pages_to_number(text: str, page_count: int) -> List[int]:
    """Parse a comma-separated, 1-based page/range selection string
    (e.g. "1,3,5-7") into a sorted, de-duplicated list of 0-based page
    indices to number, reusing split_engine.parse_page_ranges() for the
    actual syntax -- see this module's docstring for why.

    Example:
        resolve_pages_to_number("3-5", 10) -> [2, 3, 4]
    """
    groups = split_engine.parse_page_ranges(text, page_count)
    return sorted({index for group in groups for index in group})


# ---------------------------------------------------------------------------
# Option validation (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def validate_start_number(text) -> int:
    """Parses a start-number field into a non-negative int.

    Rejects empty input, non-numeric input, decimal values (a literal
    "." anywhere is rejected, even for something like "1.0" -- the
    Phase 20 spec asks for whole-number-only input, not "numerically
    equal to a whole number"), and negative values. Accepts 0 and any
    positive integer, with no arbitrary upper bound (the spec explicitly
    says not to impose one unless the library requires it, and it
    doesn't).
    """
    stripped = str(text).strip() if text is not None else ""
    if not stripped:
        raise PageNumberOptionsError("Enter a start number.")
    if not _INTEGER_RE.fullmatch(stripped):
        raise PageNumberOptionsError(
            "Start number must be a whole number (0 or a positive integer)."
        )
    value = int(stripped)
    if value < 0:
        raise PageNumberOptionsError("Start number cannot be negative.")
    return value


def validate_font_size(text) -> float:
    """Parses a font-size field into a positive float. Rejects empty,
    non-numeric, zero, negative, and non-finite (inf/nan) values.
    """
    stripped = str(text).strip() if text is not None else ""
    if not stripped:
        raise PageNumberOptionsError("Enter a font size.")
    try:
        value = float(stripped)
    except ValueError:
        raise PageNumberOptionsError("Font size must be a number.")
    if not _is_finite(value):
        raise PageNumberOptionsError("Font size must be a number.")
    if value <= 0:
        raise PageNumberOptionsError("Font size must be greater than zero.")
    return value


def validate_margin(text) -> float:
    """Parses a margin field into a non-negative float. Rejects empty,
    non-numeric, negative, and non-finite (inf/nan) values. Accepts
    zero.
    """
    stripped = str(text).strip() if text is not None else ""
    if not stripped:
        raise PageNumberOptionsError("Enter a margin.")
    try:
        value = float(stripped)
    except ValueError:
        raise PageNumberOptionsError("Margin must be a number.")
    if not _is_finite(value):
        raise PageNumberOptionsError("Margin must be a number.")
    if value < 0:
        raise PageNumberOptionsError("Margin cannot be negative.")
    return value


def validate_position(position: str) -> str:
    """Raises PageNumberOptionsError unless `position` is one of the
    six values in POSITIONS.
    """
    if position not in POSITIONS:
        raise PageNumberOptionsError(f"'{position}' is not a valid position.")
    return position


def _is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


# ---------------------------------------------------------------------------
# Rotation-aware placement (see this module's docstring's "PYMUPDF API"
# section for the experimentally-confirmed reasoning behind this)
# ---------------------------------------------------------------------------

def _compute_display_point(
    page: "pymupdf.Page", text: str, font_size: float, margin: float,
    position: str,
) -> "pymupdf.Point":
    """Computes the desired baseline point for `text`, in the page's
    DISPLAYED (rotation-aware) coordinate space -- i.e. Page.rect,
    which already reports rotated width/height. Converting this to the
    content-stream space insert_text() actually needs is the caller's
    job (see _insert_page_number() below); keeping that conversion
    separate makes this function's own geometry easy to reason about
    and to test independent of rotation handling.
    """
    display_rect = page.rect
    text_width = pymupdf.get_text_length(
        text, fontname=_FONT_NAME, fontsize=font_size,
    )

    if position.endswith("_left"):
        x = margin
    elif position.endswith("_center"):
        x = (display_rect.width - text_width) / 2
    else:  # _right
        x = display_rect.width - margin - text_width

    if position.startswith("top_"):
        # insert_text()'s point is a text BASELINE, not a top-left
        # corner, so a small additional offset (font_size itself is a
        # reasonable, simple approximation of typical font ascent) is
        # added below the margin line to keep the glyph's top from
        # being clipped above the page edge.
        y = margin + font_size
    else:  # bottom_
        y = display_rect.height - margin

    return pymupdf.Point(x, y)


def _insert_page_number(
    page: "pymupdf.Page", number: int, font_size: float, margin: float,
    position: str,
) -> None:
    """Inserts `number` onto `page` at `position`, correctly oriented
    for the page's current rotation -- see this module's docstring for
    the experimentally-confirmed reasoning behind the derotation_matrix
    conversion and the `rotate=` argument below.
    """
    text = str(number)
    display_point = _compute_display_point(page, text, font_size, margin, position)
    content_point = display_point * page.derotation_matrix

    page.insert_text(
        content_point,
        text,
        fontsize=font_size,
        fontname=_FONT_NAME,
        rotate=page.rotation,
        overlay=True,
    )


# ---------------------------------------------------------------------------
# Add Page Numbers engine
# ---------------------------------------------------------------------------

def add_page_numbers(
    source_path: Path,
    output_path: Path,
    page_indices: List[int],
    start_number: int = DEFAULT_START_NUMBER,
    position: str = DEFAULT_POSITION,
    font_size: float = DEFAULT_FONT_SIZE,
    margin: float = DEFAULT_MARGIN,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` containing every page of
    `source_path`, unchanged, except that each 0-based index in
    `page_indices` receives a sequential page number stamped at
    `position`, starting from `start_number` for the first (lowest-
    numbered) selected page and incrementing by one for each
    subsequent selected page in ascending order -- unselected pages are
    left completely untouched, receiving no number at all.

    Example: a 10-page document, page_indices=[2, 3, 4] (pages 3-5),
    start_number=1 -> page 3 gets "1", page 4 gets "2", page 5 gets "3".

    - Re-validates the source immediately via pdf_engine.validate_pdf()
      -- missing/corrupted/encrypted/directory sources are rejected
      exactly like every other engine's own inputs (an encrypted source
      is rejected here, not silently bypassed: this tool has no
      password-handling of its own, matching the Phase 20 spec's
      explicit instruction not to add any unless genuinely required).
    - Re-validates every index in `page_indices` against the document's
      actual, just-opened page count, in case the source changed
      between selection and this call.
    - Never modifies the source file: it is opened fresh, and only that
      in-memory copy receives the inserted text; the result is written
      to `output_path` -- a different file.
    - Places each number using rotation-aware coordinates (see this
      module's docstring) so it appears in the intended visual corner
      regardless of the page's existing rotation, and inserts it as an
      overlay (see _insert_page_number()) rather than rasterizing,
      rebuilding through images, or flattening the page.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Adding page numbers...", "Saving...") -- never a
      fabricated percentage.

    Raises PageNumberOptionsError (a PDFEngineError subclass) for an
    empty `page_indices`, an invalid `position`, a negative
    `start_number`, a non-positive `font_size`, a negative `margin`, or
    a page index that no longer fits the document. Raises
    PDFEngineError for any other PDF/filesystem failure.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not page_indices:
        raise PageNumberOptionsError("No pages selected for numbering.")

    validate_position(position)
    if start_number < 0:
        raise PageNumberOptionsError("Start number cannot be negative.")
    if font_size <= 0:
        raise PageNumberOptionsError("Font size must be greater than zero.")
    if margin < 0:
        raise PageNumberOptionsError("Margin cannot be negative.")

    pdf_engine.validate_pdf(source_path)

    if progress_callback:
        progress_callback("Adding page numbers...")

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            sorted_indices = sorted(set(page_indices))
            out_of_range = [
                index for index in sorted_indices
                if index < 0 or index >= page_count
            ]
            if out_of_range:
                raise PageNumberOptionsError(
                    "The selected pages no longer match this document "
                    "-- it may have changed since it was selected."
                )

            for sequence, index in enumerate(sorted_indices):
                number = start_number + sequence
                _insert_page_number(doc[index], number, font_size, margin, position)

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(doc, output_path)
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to add page numbers to '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
