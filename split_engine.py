"""
split_engine.py

Split PDF processing: page-range parsing, split-mode group computation,
and the actual split engine that writes multiple output PDFs from one
source. Nothing in this file imports tkinter.

Reuses pdf_engine's existing validation and atomic-save machinery
rather than creating a second output-saving system: this module is
intentionally thin. It computes WHICH pages go in WHICH output file;
pdf_engine.validate_pdf() and pdf_engine._atomic_save_pdf() still do the
actual PDF-safety and crash-safe-write work, exactly as they already do
for merge_pdfs() and compress_pdf().
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

import pymupdf

import file_manager
import pdf_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class PageRangeError(PDFEngineError):
    """Raised for invalid/malformed page-range or split-setting input.

    A subclass of PDFEngineError so the same "human-readable message,
    never a raw traceback" handling already used everywhere else in the
    app (see ui.py's worker error handling) applies here automatically,
    with no special-casing needed.
    """


# ---------------------------------------------------------------------------
# Page-range parsing (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def parse_page_ranges(text: str, page_count: int) -> List[List[int]]:
    """Parse a comma-separated page-range string into a list of page-
    index GROUPS -- one group per comma-separated token, each group
    being the 0-based page indices for exactly ONE output file.

    Accepts tokens like "3" (a single page) or "1-5" (an inclusive
    range), separated by commas: "1-3,5,7-9". Whitespace around tokens
    and around the '-' in a range is ignored.

    Example:
        parse_page_ranges("1-3,5,7-9", 10)
        -> [[0, 1, 2], [4], [6, 7, 8]]

    This directly matches the "custom ranges" split mode's requirement
    of one output file per range: "1-3" / "5" / "7-9" typed by the user
    become three separate output files, each named and containing
    exactly what that one token specifies.

    Order: the returned groups are in the exact order the user typed
    the tokens, not renumbered or sorted -- "5,1-3" returns
    [[4], [0, 1, 2]], so the resulting output files are created in that
    same order (part 1 = page 5, part 2 = pages 1-3).

    Duplicates/overlaps: EXPLICITLY ALLOWED, not deduplicated. Each
    token is parsed and validated entirely independently of every other
    token, so "1-3,2-4" is valid and produces two separate output files
    -- one with pages 1-3, another with pages 2-4. This is a deliberate
    design decision: a user may legitimately want two overlapping
    excerpts as two separate files. It is never ambiguous, because each
    token's meaning is fully determined on its own -- there is no
    "merging" or "deduplicating" step that could produce a different
    result depending on token order or interpretation.

    Pages are 1-based as typed by the user; the returned indices are
    0-based (subtract 1), matching PyMuPDF's page-indexing convention.

    Raises PageRangeError, with a specific and human-readable message,
    for:
      - empty/whitespace-only input, or an empty token (e.g. "1,,3")
      - non-numeric values (e.g. "abc", "1.5")
      - page 0 or a negative page number
      - malformed range syntax (e.g. "1-2-3", "-5", "5-")
      - a range whose start is after its end (e.g. "5-2")
      - any page number greater than page_count
      - page_count itself being 0 or negative (nothing to select from)

    Never returns a partial/best-effort result for invalid text -- a
    single invalid token invalidates the whole input, since a partially
    applied split would be a confusing, silent surprise for the user.
    """
    if page_count <= 0:
        raise PageRangeError("The document has no pages to select from.")

    text = text.strip()
    if not text:
        raise PageRangeError("Enter at least one page or page range.")

    groups: List[List[int]] = []

    for raw_token in text.split(","):
        token = raw_token.strip()
        if not token:
            raise PageRangeError(
                "Found an empty entry -- check for extra commas."
            )

        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise PageRangeError(f"'{token}' is not a valid page range.")

            start = _parse_single_page_number(parts[0].strip(), token)
            end = _parse_single_page_number(parts[1].strip(), token)

            if start > end:
                raise PageRangeError(
                    f"'{token}' is invalid: the start page ({start}) is "
                    f"after the end page ({end})."
                )
            _check_within_document(start, page_count, token)
            _check_within_document(end, page_count, token)

            groups.append(list(range(start - 1, end)))  # inclusive of end
        else:
            page = _parse_single_page_number(token, token)
            _check_within_document(page, page_count, token)
            groups.append([page - 1])

    return groups


def _parse_single_page_number(value: str, original_token: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise PageRangeError(
            f"'{original_token}' contains a non-numeric value."
        ) from None
    if number <= 0:
        raise PageRangeError(
            f"'{original_token}' contains page {number}, but pages start "
            f"at 1."
        )
    return number


def _check_within_document(page: int, page_count: int, original_token: str) -> None:
    if page > page_count:
        raise PageRangeError(
            f"'{original_token}' refers to page {page}, but the document "
            f"only has {page_count} page{'s' if page_count != 1 else ''}."
        )


# ---------------------------------------------------------------------------
# Split-mode group computation (individual pages / every N pages)
# ---------------------------------------------------------------------------

def compute_individual_page_groups(page_count: int) -> List[List[int]]:
    """One group per page: [[0], [1], [2], ...]."""
    if page_count <= 0:
        raise PageRangeError("The document has no pages to split.")
    return [[i] for i in range(page_count)]


def compute_every_n_page_groups(page_count: int, n: int) -> List[List[int]]:
    """Groups of N consecutive pages each; the last group holds whatever
    remains if page_count isn't evenly divisible by N (e.g. 10 pages,
    N=3 -> groups of 3, 3, 3, 1).
    """
    if page_count <= 0:
        raise PageRangeError("The document has no pages to split.")
    if n < 1:
        raise PageRangeError("Enter a number of pages of at least 1.")
    return [
        list(range(start, min(start + n, page_count)))
        for start in range(0, page_count, n)
    ]


# ---------------------------------------------------------------------------
# Output naming
# ---------------------------------------------------------------------------

def _format_sequence(index: int, total: int) -> str:
    width = max(3, len(str(total)))
    return str(index).zfill(width)


def _range_label(group: List[int]) -> str:
    """0-based page indices -> a human 1-based label, e.g.
    [0, 1, 2] -> "1-3", [4] -> "5". Assumes a contiguous ascending
    group, which is always true for the groups this module itself
    produces (each token/mode always yields a contiguous run).
    """
    first_page = group[0] + 1
    last_page = group[-1] + 1
    return f"{first_page}-{last_page}" if first_page != last_page else str(first_page)


def _build_suffix(index: int, total: int, group: List[int], include_range_label: bool) -> str:
    seq = _format_sequence(index, total)
    if include_range_label:
        return f"_{seq}_pages_{_range_label(group)}"
    return f"_{seq}"


# ---------------------------------------------------------------------------
# Split engine
# ---------------------------------------------------------------------------

def split_pdf(
    source_path: Path,
    groups: List[List[int]],
    output_dir: Path,
    include_range_label: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """Split `source_path` into one output PDF per entry in `groups`
    (each entry is a list of 0-based page indices for that output file),
    written into `output_dir`.

    - Re-validates the source immediately before processing (missing/
      corrupted/encrypted/directory), exactly like merge_pdfs() and
      compress_pdf() already do for their own inputs.
    - Never modifies the source file: each output is built by opening a
      fresh read-only copy of the source and selecting pages on that
      in-memory copy; the file on disk is never written to.
    - Writes each output atomically via pdf_engine's existing
      _atomic_save_pdf(), and names it via a collision-safe generated
      path (file_manager.generate_split_output_path()) -- never
      silently overwrites an existing file.
    - progress_callback, if given, is called once per output file with
      a real, meaningful message ("Creating part 2 of 4...") -- never a
      fabricated percentage.

    ATOMICITY ACROSS THE WHOLE OPERATION: if any output fails partway
    through the overall split, every output file THIS call has already
    created is deleted before the error propagates, so a failure never
    leaves the user with a confusing, incomplete set of split files.
    Only paths this call itself created are ever removed -- a
    pre-existing file with a colliding name is never touched (it simply
    isn't a candidate, since generate_split_output_path() never returns
    an already-existing path), and the source PDF is never touched.

    Returns {"output_paths": [Path, ...], "total_parts": int} on
    success. Raises PDFEngineError (PageRangeError included, since it
    is a subclass) on any failure.
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    if not groups:
        raise PageRangeError(
            "Nothing to split -- no output files would be produced."
        )

    pdf_engine.validate_pdf(source_path)

    total = len(groups)
    created_paths: List[Path] = []

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PDFEngineError(
            f"Could not create the output folder '{output_dir}': "
            f"{exc.strerror or exc}."
        ) from exc

    try:
        for index, group in enumerate(groups, start=1):
            if progress_callback:
                progress_callback(f"Creating part {index} of {total}...")

            suffix = _build_suffix(index, total, group, include_range_label)
            output_path = file_manager.generate_split_output_path(
                source_path, output_dir, suffix
            )

            try:
                with pymupdf.open(source_path) as doc:
                    doc.select(group)
                    pdf_engine._atomic_save_pdf(doc, output_path)
            except PDFEngineError:
                raise
            except Exception as exc:
                raise PDFEngineError(
                    f"Failed to create part {index} of {total}: {exc}"
                ) from exc

            created_paths.append(output_path)

        if progress_callback:
            progress_callback("Split completed successfully.")

        return {"output_paths": list(created_paths), "total_parts": total}

    except Exception:
        for path in created_paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.warning(
                    "Could not remove partial split output: %s", path
                )
        raise
