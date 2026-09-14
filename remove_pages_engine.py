"""
remove_pages_engine.py

Phase 14: Remove Pages processing -- resolving a user's page/range
selection into the concrete pages to delete, and the actual engine that
writes a new PDF with those pages removed. Nothing in this file imports
tkinter.

Reuses split_engine.parse_page_ranges() for the actual text-parsing
syntax (see resolve_pages_to_remove() below) rather than duplicating a
second, potentially-inconsistent parser: Remove Pages and Split PDF's
"custom ranges" mode always agree on what "1,3,5-7" means, on what
counts as malformed input, and on the exact wording of every parsing
error. This module only adds the one piece of validation logic that is
genuinely specific to Remove Pages -- flattening per-token groups into a
single de-duplicated set of pages to delete, and rejecting a selection
that would remove every page in the document, since a PDF must contain
at least one page afterwards.

Like split_engine.py, this module reuses pdf_engine's existing
validation and atomic-save machinery rather than creating a second
output-saving system: pdf_engine.validate_pdf() and
pdf_engine._atomic_save_pdf() still do the actual PDF-safety and
crash-safe-write work, exactly as they already do for merge_pdfs(),
compress_pdf(), and split_engine.split_pdf().
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

def resolve_pages_to_remove(text: str, page_count: int) -> List[int]:
    """Parse a comma-separated, 1-based page/range selection string
    (e.g. "1,3,5-7") into a sorted, de-duplicated list of 0-based page
    indices to remove.

    Token syntax, whitespace handling, and every rejected-input case
    (empty input, an empty token, non-numeric values, page 0 or a
    negative page, malformed range syntax, a reversed range, and any
    page number beyond page_count) are all exactly as
    split_engine.parse_page_ranges() already defines and tests them --
    reused here directly, not reimplemented, so the two tools can never
    silently drift into inconsistent range semantics.

    Unlike Split PDF's custom-ranges mode (where each comma-separated
    token becomes a SEPARATE output file, so "1-3,2-4" is two
    deliberately overlapping outputs), Remove Pages only cares about the
    final SET of pages to delete -- so overlapping/duplicate tokens
    (e.g. "1-3,2-4" or "5,5") are perfectly valid here and simply
    collapse to the same set of pages once flattened; they are not an
    error, and this is not "the project's convention" requiring
    rejection (split_engine's own parser explicitly allows duplicates
    across tokens for the same reason -- see its docstring).

    Additionally raises PageRangeError if the resulting selection would
    remove every page in the document: a PDF must contain at least one
    page after removal.
    """
    groups = split_engine.parse_page_ranges(text, page_count)
    indices = sorted({index for group in groups for index in group})

    if len(indices) >= page_count:
        raise PageRangeError(
            "Cannot remove every page -- the file must contain at "
            "least one page after removal."
        )

    return indices


# ---------------------------------------------------------------------------
# Remove Pages engine
# ---------------------------------------------------------------------------

def remove_pages_from_pdf(
    source_path: Path,
    output_path: Path,
    pages_to_remove: List[int],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` containing every page of
    `source_path` EXCEPT the 0-based page indices listed in
    `pages_to_remove`, preserving the original order of every remaining
    page.

    - Re-validates the source immediately before processing (missing/
      corrupted/encrypted/directory), exactly like merge_pdfs(),
      compress_pdf(), and split_engine.split_pdf() already do for their
      own inputs.
    - Never modifies the source file: it is opened fresh, pages are
      deleted from that in-memory copy, and the result is written to
      `output_path` -- a different file. The file on disk at
      `source_path` is never written to.
    - Re-validates `pages_to_remove` against the document's actual,
      just-opened page count (not merely trusting whatever was computed
      earlier from possibly-stale metadata) and re-checks the "can't
      remove every page" rule at this point too, in case the source
      changed between selection and this call.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Removing selected pages...", "Saving...") -- never a
      fabricated percentage.

    Raises PageRangeError (a PDFEngineError subclass) for an empty
    `pages_to_remove`, for a selection that no longer fits the document,
    or for a selection that would remove every page. Raises
    PDFEngineError for any other PDF/filesystem failure.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not pages_to_remove:
        raise PageRangeError("No pages selected to remove.")

    pdf_engine.validate_pdf(source_path)

    unique_pages = sorted(set(pages_to_remove))

    if progress_callback:
        progress_callback("Removing selected pages...")

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            out_of_range = [p for p in unique_pages if p < 0 or p >= page_count]
            if out_of_range:
                raise PageRangeError(
                    "The selected pages no longer match this document "
                    "-- it may have changed since it was selected."
                )

            if len(unique_pages) >= page_count:
                raise PageRangeError(
                    "Cannot remove every page -- the file must contain "
                    "at least one page after removal."
                )

            doc.delete_pages(unique_pages)

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(doc, output_path)
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to remove pages from '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
