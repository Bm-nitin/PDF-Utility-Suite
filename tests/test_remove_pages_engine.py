"""
test_remove_pages_engine.py

Comprehensive tests for remove_pages_engine.py (Phase 14): page-selection
resolution (resolve_pages_to_remove -- built on top of
split_engine.parse_page_ranges, not a duplicate parser) and the actual
remove_pages_from_pdf() engine (valid input, invalid input, source
safety, output safety).

No tkinter dependency -- remove_pages_engine.py is pure logic + file
I/O, so these run without a display.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine
import remove_pages_engine
from split_engine import PageRangeError


def _make_pdf(path, pages=1, text_prefix="PAGE "):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


def _page_texts(path):
    doc = pymupdf.open(path)
    try:
        return [doc[i].get_text().strip() for i in range(doc.page_count)]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# resolve_pages_to_remove() -- VALID INPUT
# ---------------------------------------------------------------------------

def test_remove_one_page():
    assert remove_pages_engine.resolve_pages_to_remove("3", 10) == [2]


def test_remove_first_page():
    assert remove_pages_engine.resolve_pages_to_remove("1", 10) == [0]


def test_remove_last_page():
    assert remove_pages_engine.resolve_pages_to_remove("10", 10) == [9]


def test_remove_middle_page():
    assert remove_pages_engine.resolve_pages_to_remove("5", 10) == [4]


def test_remove_multiple_pages():
    assert remove_pages_engine.resolve_pages_to_remove("1,3,5", 10) == [0, 2, 4]


def test_remove_contiguous_range():
    assert remove_pages_engine.resolve_pages_to_remove("5-7", 10) == [4, 5, 6]


def test_remove_multiple_ranges():
    assert remove_pages_engine.resolve_pages_to_remove("1-3,7,10-12", 15) == [
        0, 1, 2, 6, 9, 10, 11,
    ]


def test_mixed_single_pages_and_ranges():
    assert remove_pages_engine.resolve_pages_to_remove("1,3,5-7", 10) == [
        0, 2, 4, 5, 6,
    ]


def test_whitespace_around_input_is_tolerated():
    assert remove_pages_engine.resolve_pages_to_remove(" 1, 3, 5-7 ", 10) == [
        0, 2, 4, 5, 6,
    ]


def test_result_is_sorted_regardless_of_input_order():
    assert remove_pages_engine.resolve_pages_to_remove("7,1,4", 10) == [0, 3, 6]


def test_overlapping_tokens_collapse_without_duplicates():
    # Unlike Split PDF (where each token is a separate output and
    # duplicates are meaningful), Remove Pages only cares about the
    # final set -- "1-3,2-4" must not double-count page 2 or 3.
    assert remove_pages_engine.resolve_pages_to_remove("1-3,2-4", 10) == [
        0, 1, 2, 3,
    ]


def test_correct_count_for_a_typical_selection():
    indices = remove_pages_engine.resolve_pages_to_remove("2,4,6-8", 10)
    assert len(indices) == 5


# ---------------------------------------------------------------------------
# resolve_pages_to_remove() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_selection_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("", 10)


def test_whitespace_only_selection_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("   ", 10)


def test_page_zero_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("0", 10)


def test_negative_page_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("-2", 10)


def test_non_numeric_token_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("abc", 10)


def test_malformed_range_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("1-3-5", 10)


def test_reversed_range_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("7-3", 10)


def test_out_of_range_page_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("15", 10)


def test_invalid_separator_rejected():
    with pytest.raises(PageRangeError):
        remove_pages_engine.resolve_pages_to_remove("1;3;5", 10)


def test_selection_removing_every_page_rejected():
    with pytest.raises(PageRangeError, match="every page"):
        remove_pages_engine.resolve_pages_to_remove("1-10", 10)


def test_selection_covering_every_page_via_overlap_rejected():
    # "1-5,1-10" resolves to all 10 pages once flattened, even though
    # neither token alone does -- must still be rejected.
    with pytest.raises(PageRangeError, match="every page"):
        remove_pages_engine.resolve_pages_to_remove("1-5,1-10", 10)


def test_single_page_document_cannot_remove_its_only_page():
    with pytest.raises(PageRangeError, match="every page"):
        remove_pages_engine.resolve_pages_to_remove("1", 1)


# ---------------------------------------------------------------------------
# remove_pages_from_pdf() -- VALID INPUT / content correctness
# ---------------------------------------------------------------------------

def test_remove_one_page_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [2])

    assert _page_texts(output_path) == ["PAGE 1", "PAGE 2", "PAGE 4", "PAGE 5"]


def test_remove_first_page_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=4)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    assert _page_texts(output_path) == ["PAGE 2", "PAGE 3", "PAGE 4"]


def test_remove_last_page_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=4)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [3])

    assert _page_texts(output_path) == ["PAGE 1", "PAGE 2", "PAGE 3"]


def test_remove_multiple_pages_preserves_order(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    # Remove 1-based pages 1, 3, 5, 6, 7 -> 0-based 0, 2, 4, 5, 6
    remove_pages_engine.remove_pages_from_pdf(src, output_path, [0, 2, 4, 5, 6])

    assert _page_texts(output_path) == [
        "PAGE 2", "PAGE 4", "PAGE 8", "PAGE 9", "PAGE 10",
    ]


def test_remove_pages_from_pdf_accepts_unsorted_input(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [6, 0, 4, 5, 2])

    assert _page_texts(output_path) == [
        "PAGE 2", "PAGE 4", "PAGE 8", "PAGE 9", "PAGE 10",
    ]


def test_remove_pages_from_pdf_accepts_duplicate_indices(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [1, 1, 1])

    assert _page_texts(output_path) == ["PAGE 1", "PAGE 3", "PAGE 4", "PAGE 5"]


def test_correct_remaining_page_count(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [0, 2, 4, 5, 6])

    doc = pymupdf.open(output_path)
    try:
        assert doc.page_count == 5
    finally:
        doc.close()


def test_full_text_to_engine_pipeline(tmp_path):
    """End-to-end: parse a text selection with resolve_pages_to_remove(),
    then feed straight into remove_pages_from_pdf() -- the exact path
    the UI takes.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    indices = remove_pages_engine.resolve_pages_to_remove("1,3,5-7", 10)
    remove_pages_engine.remove_pages_from_pdf(src, output_path, indices)

    assert _page_texts(output_path) == [
        "PAGE 2", "PAGE 4", "PAGE 8", "PAGE 9", "PAGE 10",
    ]


# ---------------------------------------------------------------------------
# remove_pages_from_pdf() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_pages_to_remove_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageRangeError):
        remove_pages_engine.remove_pages_from_pdf(src, output_path, [])

    assert not output_path.exists()


def test_removing_every_page_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageRangeError, match="every page"):
        remove_pages_engine.remove_pages_from_pdf(src, output_path, [0, 1, 2])

    assert not output_path.exists()


def test_out_of_range_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageRangeError):
        remove_pages_engine.remove_pages_from_pdf(src, output_path, [10])

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        remove_pages_engine.remove_pages_from_pdf(missing, output_path, [0])

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        remove_pages_engine.remove_pages_from_pdf(corrupt, output_path, [0])


def test_encrypted_source_raises_clear_error(tmp_path):
    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret",
    )
    doc.close()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.EncryptedPDFError):
        remove_pages_engine.remove_pages_from_pdf(protected, output_path, [0])


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        remove_pages_engine.remove_pages_from_pdf(a_directory, output_path, [0])


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# SOURCE SAFETY
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged_after_successful_removal(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [0, 2, 4])

    assert src.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_failed_removal(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    assert src.read_bytes() == original_bytes


def test_source_remains_readable_after_removal(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    # The source must still open cleanly and still have all 6 pages --
    # remove_pages_from_pdf() must not have mutated it in place.
    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_failed_operation_does_not_corrupt_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_output_is_a_different_file_from_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    assert output_path != src
    assert output_path.exists()
    assert src.exists()
    assert output_path.read_bytes() != src.read_bytes()


# ---------------------------------------------------------------------------
# OUTPUT SAFETY
# ---------------------------------------------------------------------------

def test_output_gets_pdf_extension_via_existing_file_manager_helpers():
    import file_manager

    name = file_manager.ensure_pdf_extension(
        file_manager.sanitize_windows_filename("document_without_pages")
    )
    assert name.endswith(".pdf")


def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    """If the underlying save fails, no partial/temp file should be left
    behind at output_path -- proving _atomic_save_pdf (not a second,
    home-grown save mechanism) is what remove_pages_from_pdf() uses.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            remove_pages_engine.remove_pages_from_pdf(src, output_path, [0])

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_progress_callback_reports_removal_and_saving(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    messages = []
    remove_pages_engine.remove_pages_from_pdf(
        src, output_path, [0], progress_callback=messages.append,
    )

    assert any("removing" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_error_messages_contain_no_traceback(tmp_path):
    output_path = tmp_path / "output.pdf"
    try:
        remove_pages_engine.remove_pages_from_pdf(
            tmp_path / "missing.pdf", output_path, [0],
        )
    except pdf_engine.PDFEngineError as exc:
        assert "Traceback" not in str(exc)


# ---------------------------------------------------------------------------
# REGRESSION: reusing split_engine's parser must not change Split itself
# ---------------------------------------------------------------------------

def test_split_engine_parse_page_ranges_still_importable_and_unchanged():
    import split_engine

    # remove_pages_engine imports and calls this directly -- confirm it
    # is still the same public function with the same behavior Split's
    # own test suite already covers in depth (see test_split_engine.py).
    assert split_engine.parse_page_ranges("1,3,5-7", 10) == [[0], [2], [4, 5, 6]]
