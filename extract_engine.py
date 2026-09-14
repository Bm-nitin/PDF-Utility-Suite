"""
extract_engine.py

Phase 15: Extract Pages processing -- resolving a user's page/range
selection into the concrete, ORDERED list of pages to extract, and the
actual engine that writes a new PDF containing exactly those pages (in
the order requested), leaving the source completely untouched. Nothing
in this file imports tkinter.

Reuses split_engine.parse_page_ranges() for the actual text-parsing
syntax (see resolve_pages_to_extract() below), exactly like
remove_pages_engine.py already does, rather than duplicating a second,
potentially-inconsistent parser: Extract Pages, Remove Pages, and Split
PDF's "custom ranges" mode always agree on what "1,3,5-7" means, on what
counts as malformed input, and on the exact wording of every parsing
error. This module only adds the one piece of logic that is genuinely
specific to Extract Pages -- flattening the parser's per-token groups
into a single ORDERED list of pages to extract, in the exact order the
user typed them.

Extract Pages is deliberately NOT Remove Pages with the selection
inverted: Remove Pages only cares about the final, order-independent SET
of pages to delete (so it sorts and de-duplicates -- see
remove_pages_engine.resolve_pages_to_remove()). Extract Pages is the
opposite: the whole point is to save specific pages as a new file, in
the order the user actually asked for them, so "4,2" must extract page 4
then page 2 (in that order), not pages 2 then 4. split_engine's parser
already treats every comma-separated token as an independent group, in
typed order, with duplicates/overlaps explicitly allowed (see its own
docstring) -- so simply flattening the groups in the order they were
parsed gives Extract Pages exactly this order-preserving,
duplicate-preserving behavior, with no extra logic needed and no risk of
silently drifting from Split's own "custom ranges" semantics.

Like split_engine.py and remove_pages_engine.py, this module reuses
pdf_engine's existing validation and atomic-save machinery rather than
creating a second output-saving system: pdf_engine.validate_pdf() and
pdf_engine._atomic_save_pdf() still do the actual PDF-safety and
crash-safe-write work, exactly as they already do for merge_pdfs(),
compress_pdf(), split_engine.split_pdf(), and
remove_pages_engine.remove_pages_from_pdf().
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

import pymupdf

import pdf_engine
import split_engine
from pdf_engine import PDFEngineError
from split_engine import PageRangeError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Page-selection resolution (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def resolve_pages_to_extract(text: str, page_count: int) -> List[int]:
    """Parse a comma-separated, 1-based page/range selection string
    (e.g. "2,4" or "1-3,5,7-9") into an ORDERED list of 0-based page
    indices to extract -- in the exact order the user typed the
    tokens/pages, NOT sorted, and NOT de-duplicated.

    Token syntax, whitespace handling, and every rejected-input case
    (empty input, an empty token, non-numeric values, page 0 or a
    negative page, malformed range syntax, a reversed range, and any
    page number beyond page_count) are all exactly as
    split_engine.parse_page_ranges() already defines and tests them --
    reused here directly, not reimplemented, so all three tools can
    never silently drift into inconsistent range semantics.

    Examples:
        resolve_pages_to_extract("2,4", 5)   -> [1, 3]
        resolve_pages_to_extract("4,2", 5)   -> [3, 1]   (order preserved)
        resolve_pages_to_extract("2,2", 5)   -> [1, 1]   (duplicate preserved)
        resolve_pages_to_extract("1-3", 5)   -> [0, 1, 2]
        resolve_pages_to_extract("1-3,2-4", 5) -> [0, 1, 2, 1, 2, 3]

    Does not itself re-check the result against page_count beyond what
    parse_page_ranges() already validated -- extract_pages_from_pdf()
    below re-validates against the document's actual, just-opened page
    count before writing anything, exactly like
    remove_pages_engine.remove_pages_from_pdf() does for its own
    selection.
    """
    groups = split_engine.parse_page_ranges(text, page_count)
    return [index for group in groups for index in group]


# ---------------------------------------------------------------------------
# Extract Pages engine
# ---------------------------------------------------------------------------

def extract_pages_from_pdf(
    source_path: Path,
    output_path: Path,
    page_indices: List[int],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` containing exactly the 0-based
    page indices listed in `page_indices`, copied from `source_path`, in
    the EXACT order given (duplicates included -- never sorted, never
    de-duplicated; see resolve_pages_to_extract() above for why).

    - Re-validates the source immediately before processing (missing/
      corrupted/encrypted/directory), exactly like split_pdf() and
      remove_pages_from_pdf() already do for their own inputs.
    - Never modifies the source file: it is opened fresh and pages are
      only ever SELECTED (never deleted) on that in-memory copy; the
      result is written to `output_path` -- a different file. The file
      on disk at `source_path` is never written to.
    - Re-validates `page_indices` against the document's actual,
      just-opened page count (not merely trusting whatever was computed
      earlier from possibly-stale metadata), in case the source changed
      between selection and this call.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Extracting selected pages...", "Saving...") -- never a
      fabricated percentage.

    Raises PageRangeError (a PDFEngineError subclass) for an empty
    `page_indices`, or a selection that no longer fits the document.
    Raises PDFEngineError for any other PDF/filesystem failure.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not page_indices:
        raise PageRangeError("No pages selected to extract.")

    pdf_engine.validate_pdf(source_path)

    if progress_callback:
        progress_callback("Extracting selected pages...")

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            out_of_range = [p for p in page_indices if p < 0 or p >= page_count]
            if out_of_range:
                raise PageRangeError(
                    "The selected pages no longer match this document "
                    "-- it may have changed since it was selected."
                )

            # doc.select() rebuilds the document to contain exactly the
            # given page numbers, in the given order, with duplicates
            # allowed -- exactly the semantic Extract Pages needs (see
            # module docstring). Passing a plain list (not the caller's
            # original object) defensively guards against PyMuPDF
            # mutating/consuming it.
            doc.select(list(page_indices))

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(doc, output_path)
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to extract pages from '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
