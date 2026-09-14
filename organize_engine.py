"""
organize_engine.py

Phase 16: Organize / Reorder Pages processing -- parsing a user's
complete page-order input into a validated, 0-based page-index list, and
the actual engine that writes a new PDF with the source's pages
rearranged into that order, leaving the source completely untouched.
Nothing in this file imports tkinter.

Deliberately NOT built on split_engine.parse_page_ranges() (unlike
extract_engine.py, which reuses it directly). That parser's whole
purpose is expressing a SUBSET of pages, via ranges ("1-3") and
independent, possibly-overlapping, possibly-incomplete tokens -- exactly
the opposite of what a reorder needs. A reorder is a *permutation*: it
must name every source page exactly once, and a "1-3" token would be
ambiguous here in a way it never is for Split/Extract -- does it mean
"insert pages 1, 2, 3 here in that order" (fine) or is it merely
shorthand that a careless user might expect to somehow also enforce
"...and every OTHER page keeps its relative order" (not fine, and not
how Split/Extract's parser behaves)? Rather than accept that ambiguity,
this module's own parse_page_order() below supports only a flat,
explicit, comma-separated list of individual 1-based page numbers -- no
ranges. Every page must be named individually. This keeps the semantics
of "the final order" fully unambiguous and matches this phase's own
requirement ("the final page order must be explicit and deterministic").

Like split_engine.py, remove_pages_engine.py, and extract_engine.py,
this module reuses pdf_engine's existing validation and atomic-save
machinery rather than creating a second output-saving system.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

import pymupdf

import pdf_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class OrganizeOrderError(PDFEngineError):
    """Raised for invalid/malformed/incomplete page-order input.

    A subclass of PDFEngineError so the same "human-readable message,
    never a raw traceback" handling already used everywhere else in the
    app (see ui.py's worker error handling) applies here automatically,
    with no special-casing needed.
    """


# ---------------------------------------------------------------------------
# Page-order parsing (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def parse_page_order(text: str, page_count: int) -> List[int]:
    """Parse a comma-separated, 1-based, COMPLETE page-order string
    (e.g. "3,1,5,2,4") into the ORDERED list of 0-based page indices to
    write, in the exact order given.

    Intentionally simple and strict, by design (see module docstring):
    no ranges, no shorthand. Every one of the document's pages must be
    named exactly once. This is what makes a *reorder* well-defined --
    unlike Extract Pages, a reorder is not a selection, and unlike
    Remove Pages, it does not describe what to delete; it must always
    describe a complete rearrangement of every page.

    Example:
        parse_page_order("3,1,5,2,4", 5) -> [2, 0, 4, 1, 3]
        parse_page_order("1,2,3,4,5", 5) -> [0, 1, 2, 3, 4]  # identity

    Raises OrganizeOrderError, with a specific and human-readable
    message, for:
      - page_count itself being 0 or negative (nothing to reorder)
      - empty/whitespace-only input, or an empty token (e.g. "1,,3")
      - non-numeric values (e.g. "abc", "1.5")
      - a "-" anywhere in a token (ranges are not supported -- see
        module docstring for why)
      - page 0 or a negative page number
      - any page number greater than page_count
      - a page number repeated more than once (a reorder must be a
        permutation, not a selection with repeats)
      - a page missing from the order (the order must name every page
        from 1 to page_count exactly once -- not a subset)

    Never returns a partial/best-effort result for invalid text -- a
    single invalid or missing page invalidates the whole input, since a
    partially applied reorder would silently drop or duplicate content.
    """
    if page_count <= 0:
        raise OrganizeOrderError("The document has no pages to reorder.")

    if text is None:
        text = ""
    text = text.strip()
    if not text:
        raise OrganizeOrderError(
            "Enter a complete page order, e.g. "
            + ",".join(str(n) for n in range(1, page_count + 1))
        )

    order: List[int] = []
    seen: set = set()

    for raw_token in text.split(","):
        token = raw_token.strip()
        if not token:
            raise OrganizeOrderError(
                "Found an empty entry -- check for extra commas."
            )

        if "-" in token:
            raise OrganizeOrderError(
                f"'{token}' is not valid here -- list every page "
                f"individually (ranges like '1-3' are not supported for "
                f"reordering)."
            )

        try:
            page = int(token)
        except ValueError:
            raise OrganizeOrderError(
                f"'{token}' is not a valid page number."
            ) from None

        if page <= 0:
            raise OrganizeOrderError(
                f"'{token}' is invalid -- pages start at 1."
            )
        if page > page_count:
            raise OrganizeOrderError(
                f"'{token}' refers to page {page}, but the document only "
                f"has {page_count} page{'s' if page_count != 1 else ''}."
            )
        if page in seen:
            raise OrganizeOrderError(
                f"Page {page} appears more than once -- each page must "
                f"appear exactly once in the new order."
            )

        seen.add(page)
        order.append(page - 1)

    if len(order) != page_count:
        missing = sorted(set(range(1, page_count + 1)) - seen)
        missing_display = ", ".join(str(p) for p in missing)
        raise OrganizeOrderError(
            f"The order is incomplete -- missing page"
            f"{'s' if len(missing) != 1 else ''} {missing_display}. "
            f"Every page from 1 to {page_count} must appear exactly once."
        )

    return order


def identity_order(page_count: int) -> str:
    """The default, "no change" order string for a freshly-imported
    source with the given page count -- "1,2,3,...,N". Used by the UI
    to initialize the order text the moment a source is selected (see
    Phase 16 requirement 5).
    """
    return ",".join(str(n) for n in range(1, page_count + 1))


# ---------------------------------------------------------------------------
# Organize / Reorder Pages engine
# ---------------------------------------------------------------------------

def organize_pages_from_pdf(
    source_path: Path,
    output_path: Path,
    page_order: List[int],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` containing every page of
    `source_path`, rearranged into the exact 0-based order given by
    `page_order` (a complete permutation -- see parse_page_order()
    above for how that's validated).

    - Re-validates the source immediately before processing (missing/
      corrupted/encrypted/directory), exactly like split_pdf(),
      remove_pages_from_pdf(), and extract_pages_from_pdf() already do
      for their own inputs.
    - Never modifies the source file: it is opened fresh and pages are
      only ever SELECTED (never deleted) on that in-memory copy; the
      result is written to `output_path` -- a different file. The file
      on disk at `source_path` is never written to.
    - Re-validates `page_order` against the document's actual, just-
      opened page count (not merely trusting whatever was computed
      earlier from possibly-stale metadata) -- both that it is a
      complete permutation and that every index is in range, in case
      the source changed between selection and this call.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Reordering pages...", "Saving...") -- never a
      fabricated percentage.

    Raises OrganizeOrderError (a PDFEngineError subclass) for an empty
    `page_order`, or one that no longer forms a complete, valid
    permutation of the document's current pages. Raises PDFEngineError
    for any other PDF/filesystem failure.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not page_order:
        raise OrganizeOrderError("No page order was provided.")

    pdf_engine.validate_pdf(source_path)

    if progress_callback:
        progress_callback("Reordering pages...")

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            out_of_range = [p for p in page_order if p < 0 or p >= page_count]
            if out_of_range:
                raise OrganizeOrderError(
                    "The requested page order no longer matches this "
                    "document -- it may have changed since it was selected."
                )
            if len(page_order) != page_count or set(page_order) != set(range(page_count)):
                raise OrganizeOrderError(
                    "The requested page order no longer matches this "
                    "document -- it may have changed since it was selected."
                )

            # doc.select() rebuilds the document to contain exactly the
            # given page numbers, in the given order -- since page_order
            # is guaranteed (by the checks above) to be a complete
            # permutation with no duplicates, this is a pure reorder:
            # every page survives, none are dropped or repeated.
            doc.select(list(page_order))

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(doc, output_path)
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to reorder pages in '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
