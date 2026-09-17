"""
rotate_engine.py

Phase 17: Rotate Pages processing -- resolving a user's page/range
selection into the concrete, de-duplicated set of pages to rotate,
converting a (direction, angle) choice into a single clockwise degree
delta, and the actual engine that writes a new PDF with the selected
pages rotated -- ADDED to each page's existing rotation, then
normalized to 0/90/180/270 -- leaving every non-selected page, and the
source file itself, completely untouched. Nothing in this file imports
tkinter.

Reuses split_engine.parse_page_ranges() for text parsing, exactly like
remove_pages_engine.py and extract_engine.py do. Follows
remove_pages_engine's SET semantics (sorted, de-duplicated) rather than
extract_engine's order-preserving semantics: rotation is an action
applied once per page, and applying it twice to the same page (because
it appeared twice in a typed range expression, e.g. "1-3,2-4") would
silently double the rotation -- exactly what the Phase 17 spec warns
against -- so resolve_pages_to_rotate() below de-duplicates for the same
reason remove_pages_engine.resolve_pages_to_remove() does.

Rotation representation: a plain dict of {0-based page index: clockwise
degrees to ADD to that page's current rotation}, with every value in
{90, 180, 270} -- the representation the Phase 17 spec itself
recommends. There is no "0 degrees" entry: a page that isn't in the
dict is simply left alone, which is a clearer and less error-prone way
to express "no change" than a 0-valued key that every consumer would
otherwise have to remember to skip.

Like split_engine.py, remove_pages_engine.py, extract_engine.py, and
organize_engine.py, this module reuses pdf_engine's existing validation
and atomic-save machinery rather than creating a second output-saving
system: pdf_engine.validate_pdf() and pdf_engine._atomic_save_pdf()
still do the actual PDF-safety and crash-safe-write work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pymupdf

import pdf_engine
import split_engine
from pdf_engine import PDFEngineError
from split_engine import PageRangeError

logger = logging.getLogger(__name__)


class RotationError(PDFEngineError):
    """Raised for an invalid rotation angle or direction, or an empty
    rotation mapping. A PDFEngineError subclass, exactly like
    split_engine.PageRangeError and organize_engine.OrganizeOrderError,
    so the same "human-readable message, never a raw traceback"
    handling already used everywhere else in the app applies here
    automatically, with no special-casing needed.
    """


CLOCKWISE = "clockwise"
COUNTERCLOCKWISE = "counterclockwise"

#: The only rotation amounts PDF page rotation actually supports --
#: any other value (0, negative, or a non-multiple of 90 such as 45) is
#: rejected outright rather than silently rounded or ignored.
VALID_ANGLES = (90, 180, 270)


# ---------------------------------------------------------------------------
# Page-selection resolution (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def resolve_pages_to_rotate(text: str, page_count: int) -> List[int]:
    """Parse a comma-separated, 1-based page/range selection string
    (e.g. "1,3,5-7") into a sorted, de-duplicated list of 0-based page
    indices to rotate.

    Token syntax, whitespace handling, and every rejected-input case
    (empty input, an empty token, non-numeric values, page 0 or a
    negative page, malformed range syntax, a reversed range, and any
    page number beyond page_count) are all exactly as
    split_engine.parse_page_ranges() already defines and tests them --
    reused here directly, not reimplemented, so Rotate can never
    silently drift into inconsistent range semantics from Split/Remove/
    Extract.

    Sorted and de-duplicated (like remove_pages_engine.
    resolve_pages_to_remove(), unlike extract_engine.
    resolve_pages_to_extract()) because rotation is applied once per
    page, and a page named twice (e.g. via "1-3,2-4") must still only be
    rotated once -- see the module docstring.

    Example:
        resolve_pages_to_rotate("1,3,5-7", 10) -> [0, 2, 4, 5, 6]
        resolve_pages_to_rotate("1-3,2-4", 10) -> [0, 1, 2, 3]
    """
    groups = split_engine.parse_page_ranges(text, page_count)
    return sorted({index for group in groups for index in group})


# ---------------------------------------------------------------------------
# Direction/angle -> clockwise-degrees resolution
# ---------------------------------------------------------------------------

def resolve_clockwise_degrees(direction: str, angle: int) -> int:
    """Converts a (direction, angle) UI choice into a single clockwise
    degree delta to ADD to a page's current rotation, per the Phase 17
    spec's explicit semantics table:

        clockwise 90          -> +90
        clockwise 180         -> +180
        clockwise 270         -> +270
        counterclockwise 90   -> +270  (equivalent)
        counterclockwise 180  -> +180  (equivalent)
        counterclockwise 270  -> +90   (equivalent)

    `direction` must be CLOCKWISE or COUNTERCLOCKWISE (the two module-
    level constants above); `angle` must be one of VALID_ANGLES (90,
    180, or 270). Raises RotationError for anything else, including 0,
    a negative value, or a non-multiple-of-90 value such as 45.
    """
    if angle not in VALID_ANGLES:
        raise RotationError(
            f"{angle}\u00b0 is not a supported rotation -- choose 90, "
            f"180, or 270."
        )
    if direction == CLOCKWISE:
        return angle
    if direction == COUNTERCLOCKWISE:
        return (360 - angle) % 360
    raise RotationError(
        f"'{direction}' is not a valid rotation direction -- choose "
        f"clockwise or counterclockwise."
    )


def build_rotation_map(
    page_indices: List[int], clockwise_degrees: int,
) -> Dict[int, int]:
    """Builds the {page index: clockwise degrees to add} mapping
    rotate_pages_in_pdf() expects, applying the same single rotation
    amount to every page in `page_indices`. A thin convenience for the
    common UI case of "rotate this set of pages by this one amount";
    nothing stops a caller from building a mixed-rotation dict by hand
    instead and passing it straight to rotate_pages_in_pdf().
    """
    return {index: clockwise_degrees for index in page_indices}


# ---------------------------------------------------------------------------
# Rotate Pages engine
# ---------------------------------------------------------------------------

def rotate_pages_in_pdf(
    source_path: Path,
    output_path: Path,
    rotations: Dict[int, int],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` containing every page of
    `source_path`, unchanged, except that each page index present in
    `rotations` has the corresponding clockwise degree value ADDED to
    that page's EXISTING rotation (not replacing it), normalized to
    0/90/180/270. Pages not present in `rotations` are left completely
    unchanged, including their existing rotation.

    `rotations` is a dict of {0-based page index: clockwise degrees to
    add}; every value must be in {90, 180, 270} -- see
    resolve_clockwise_degrees() / build_rotation_map() above for how the
    UI builds one from a page selection plus a direction/angle choice.

    - Re-validates the source immediately before processing (missing/
      corrupted/encrypted/directory), exactly like split_pdf(),
      remove_pages_from_pdf(), extract_pages_from_pdf(), and
      organize_pages_from_pdf() already do for their own inputs.
    - Never modifies the source file: it is opened fresh and only that
      in-memory copy's page rotations are changed; the result is
      written to `output_path` -- a different file. The file on disk at
      `source_path` is never written to, and neither is its page count,
      page order, or page content.
    - Re-validates every page index in `rotations` against the
      document's actual, just-opened page count (not merely trusting
      whatever was computed earlier from possibly-stale metadata), and
      every rotation value against VALID_ANGLES, in case the source or
      the mapping changed between selection and this call.
    - Cumulative, not a blind replace: each page's rotation becomes
      (current_rotation + requested_degrees) % 360, so a page already
      rotated 90 degrees that receives another +90 correctly ends up at
      180, not 90 -- and rotating a page is idempotent-safe against
      running this function twice with the SAME rotations dict only in
      the sense that each call still adds its degrees again (this is
      "rotate by N more degrees", not "set absolute rotation to N");
      callers that want to represent an absolute target rotation should
      compute the delta themselves before calling.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Rotating selected pages...", "Saving...") -- never a
      fabricated percentage.

    Raises RotationError (a PDFEngineError subclass) for an empty
    `rotations`, an invalid rotation value, or a page index that no
    longer fits the document. Raises PDFEngineError for any other PDF/
    filesystem failure.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not rotations:
        raise RotationError("No pages selected to rotate.")

    invalid_values = {
        index: degrees for index, degrees in rotations.items()
        if degrees not in VALID_ANGLES
    }
    if invalid_values:
        bad_degrees = sorted(set(invalid_values.values()))
        raise RotationError(
            f"{', '.join(str(d) + chr(176) for d in bad_degrees)} is not "
            f"a supported rotation -- choose 90, 180, or 270."
        )

    pdf_engine.validate_pdf(source_path)

    if progress_callback:
        progress_callback("Rotating selected pages...")

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            out_of_range = [
                index for index in rotations if index < 0 or index >= page_count
            ]
            if out_of_range:
                raise RotationError(
                    "The selected pages no longer match this document "
                    "-- it may have changed since it was selected."
                )

            for index, degrees in rotations.items():
                page = doc[index]
                page.set_rotation((page.rotation + degrees) % 360)

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(doc, output_path)
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to rotate pages in '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
