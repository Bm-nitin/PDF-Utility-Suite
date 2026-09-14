"""
test_extract_engine.py

Comprehensive tests for extract_engine.py (Phase 15): page-selection
resolution (resolve_pages_to_extract -- built on top of
split_engine.parse_page_ranges, not a duplicate parser) and the actual
extract_pages_from_pdf() engine (valid input, invalid input, order/
duplicate preservation, source safety, output safety).

No tkinter dependency -- extract_engine.py is pure logic + file I/O, so
these run without a display.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract_engine
import pdf_engine
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
# resolve_pages_to_extract() -- VALID INPUT
# ---------------------------------------------------------------------------

def test_extract_one_page():
    assert extract_engine.resolve_pages_to_extract("3", 10) == [2]


def test_extract_first_page():
    assert extract_engine.resolve_pages_to_extract("1", 10) == [0]


def test_extract_last_page():
    assert extract_engine.resolve_pages_to_extract("10", 10) == [9]


def test_extract_multiple_pages():
    assert extract_engine.resolve_pages_to_extract("1,3,5", 10) == [0, 2, 4]


def test_extract_contiguous_range():
    assert extract_engine.resolve_pages_to_extract("5-7", 10) == [4, 5, 6]


def test_extract_multiple_ranges():
    assert extract_engine.resolve_pages_to_extract("1-3,7,10-12", 15) == [
        0, 1, 2, 6, 9, 10, 11,
    ]


def test_mixed_single_pages_and_ranges():
    assert extract_engine.resolve_pages_to_extract("1,3,5-7", 10) == [
        0, 2, 4, 5, 6,
    ]


def test_whitespace_around_input_is_tolerated():
    assert extract_engine.resolve_pages_to_extract(" 1, 3, 5-7 ", 10) == [
        0, 2, 4, 5, 6,
    ]


def test_correct_count_for_a_typical_selection():
    indices = extract_engine.resolve_pages_to_extract("2,4,6-8", 10)
    assert len(indices) == 5


# ---------------------------------------------------------------------------
# resolve_pages_to_extract() -- ORDER AND DUPLICATE PRESERVATION
# ---------------------------------------------------------------------------
# The core semantic distinction from Remove Pages (which sorts and
# de-duplicates): Extract Pages preserves exactly what the user typed,
# in the order they typed it, duplicates included.

def test_result_preserves_requested_order_not_sorted():
    assert extract_engine.resolve_pages_to_extract("7,1,4", 10) == [6, 0, 3]


def test_reversed_token_order_is_preserved():
    assert extract_engine.resolve_pages_to_extract("4,2", 5) == [3, 1]


def test_duplicate_single_page_token_is_preserved():
    assert extract_engine.resolve_pages_to_extract("2,2", 5) == [1, 1]


def test_duplicate_page_across_separate_tokens_is_preserved():
    assert extract_engine.resolve_pages_to_extract("1,3,1", 5) == [0, 2, 0]


def test_overlapping_ranges_are_not_collapsed():
    # Unlike Remove Pages (where "1-3,2-4" collapses to the set {1,2,3,4}),
    # Extract Pages keeps every requested page, including the overlap.
    assert extract_engine.resolve_pages_to_extract("1-3,2-4", 5) == [
        0, 1, 2, 1, 2, 3,
    ]


def test_example_from_spec_extract_is_not_remove():
    # Source pages 1..5, input "2,4" -> extract output is pages 2,4 (in
    # that order); this test only checks the resolved indices -- the
    # matching engine-level content test below confirms the source
    # itself is untouched and the output actually contains 2 then 4.
    assert extract_engine.resolve_pages_to_extract("2,4", 5) == [1, 3]


# ---------------------------------------------------------------------------
# resolve_pages_to_extract() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_selection_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("", 10)


def test_whitespace_only_selection_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("   ", 10)


def test_page_zero_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("0", 10)


def test_negative_page_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("-2", 10)


def test_non_numeric_token_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("abc", 10)


def test_malformed_range_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("1-3-5", 10)


def test_reversed_range_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("7-3", 10)


def test_out_of_range_page_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("15", 10)


def test_invalid_separator_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("1;3;5", 10)


def test_empty_token_rejected():
    with pytest.raises(PageRangeError):
        extract_engine.resolve_pages_to_extract("1,,3", 10)


# ---------------------------------------------------------------------------
# extract_pages_from_pdf() -- VALID INPUT / content correctness
# ---------------------------------------------------------------------------

def test_extract_one_page_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [1])

    assert _page_texts(output_path) == ["PAGE 2"]


def test_extract_multiple_pages_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    # 1-based "2,4" -> 0-based [1, 3]
    extract_engine.extract_pages_from_pdf(src, output_path, [1, 3])

    assert _page_texts(output_path) == ["PAGE 2", "PAGE 4"]


def test_extract_preserves_requested_order_not_source_order(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    # 1-based "4,2" -> 0-based [3, 1]: output must be page 4 THEN page 2.
    extract_engine.extract_pages_from_pdf(src, output_path, [3, 1])

    assert _page_texts(output_path) == ["PAGE 4", "PAGE 2"]


def test_extract_preserves_duplicates(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [1, 1])

    assert _page_texts(output_path) == ["PAGE 2", "PAGE 2"]


def test_extract_contiguous_range_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [4, 5, 6])

    assert _page_texts(output_path) == ["PAGE 5", "PAGE 6", "PAGE 7"]


def test_extract_via_resolve_then_engine_end_to_end(tmp_path):
    """The exact path the UI takes: resolve_pages_to_extract() then
    feed straight into extract_pages_from_pdf().
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    indices = extract_engine.resolve_pages_to_extract("2,4", 5)
    extract_engine.extract_pages_from_pdf(src, output_path, indices)

    assert _page_texts(output_path) == ["PAGE 2", "PAGE 4"]


def test_extracting_every_page_is_allowed(tmp_path):
    """Unlike Remove Pages, extracting every page is perfectly valid --
    there is no "must have at least one page left" constraint on the
    SOURCE, since the source is never modified.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [0, 1, 2])

    assert _page_texts(output_path) == ["PAGE 1", "PAGE 2", "PAGE 3"]


def test_output_page_count_matches_selection_including_duplicates(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [0, 0, 5, 9])

    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 4


# ---------------------------------------------------------------------------
# extract_pages_from_pdf() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_page_indices_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageRangeError):
        extract_engine.extract_pages_from_pdf(src, output_path, [])

    assert not output_path.exists()


def test_out_of_range_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageRangeError):
        extract_engine.extract_pages_from_pdf(src, output_path, [10])

    assert not output_path.exists()


def test_negative_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageRangeError):
        extract_engine.extract_pages_from_pdf(src, output_path, [-1])

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        extract_engine.extract_pages_from_pdf(missing, output_path, [0])

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        extract_engine.extract_pages_from_pdf(corrupt, output_path, [0])


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
        extract_engine.extract_pages_from_pdf(protected, output_path, [0])


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        extract_engine.extract_pages_from_pdf(a_directory, output_path, [0])


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            extract_engine.extract_pages_from_pdf(src, output_path, [0])

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# SOURCE SAFETY -- Extract must NEVER modify the source (the key
# distinction from Remove Pages, which produces a modified copy)
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged_after_successful_extraction(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [1, 3])

    assert src.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_failed_extraction(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            extract_engine.extract_pages_from_pdf(src, output_path, [0])

    assert src.read_bytes() == original_bytes


def test_source_page_count_unchanged_after_extraction(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [0, 1])

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_example_from_spec_source_remains_1_through_5(tmp_path):
    """Source pages 1 2 3 4 5; extracting "2,4" must leave the source
    as 1 2 3 4 5 -- this is the exact example from the Phase 15 spec.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    indices = extract_engine.resolve_pages_to_extract("2,4", 5)
    extract_engine.extract_pages_from_pdf(src, output_path, indices)

    assert _page_texts(src) == ["PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5"]
    assert _page_texts(output_path) == ["PAGE 2", "PAGE 4"]


def test_failed_operation_does_not_corrupt_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            extract_engine.extract_pages_from_pdf(src, output_path, [0])

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_output_is_a_different_file_from_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    extract_engine.extract_pages_from_pdf(src, output_path, [0])

    assert output_path != src
    assert output_path.exists()
    assert src.exists()
    assert output_path.read_bytes() != src.read_bytes()


def test_no_partial_output_remains_after_failure(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            extract_engine.extract_pages_from_pdf(src, output_path, [0])

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# OUTPUT SAFETY
# ---------------------------------------------------------------------------

def test_output_gets_pdf_extension_via_existing_file_manager_helpers():
    import file_manager

    name = file_manager.ensure_pdf_extension(
        file_manager.sanitize_windows_filename("document_extracted")
    )
    assert name.endswith(".pdf")


def test_default_output_naming_convention():
    import file_manager

    stem = file_manager.sanitize_windows_filename(Path("report.pdf").stem)
    default_name = file_manager.ensure_pdf_extension(f"{stem}_extracted")
    assert default_name == "report_extracted.pdf"


def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    """If the underlying save fails, no partial/temp file should be left
    behind at output_path -- proving _atomic_save_pdf (not a second,
    home-grown save mechanism) is what extract_pages_from_pdf() uses.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            extract_engine.extract_pages_from_pdf(src, output_path, [0])

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_progress_callback_reports_extracting_and_saving(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    messages = []
    extract_engine.extract_pages_from_pdf(
        src, output_path, [0], progress_callback=messages.append,
    )

    assert any("extracting" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_error_messages_contain_no_traceback(tmp_path):
    output_path = tmp_path / "output.pdf"
    try:
        extract_engine.extract_pages_from_pdf(
            tmp_path / "missing.pdf", output_path, [0],
        )
    except pdf_engine.PDFEngineError as exc:
        assert "Traceback" not in str(exc)


# ---------------------------------------------------------------------------
# CONCURRENT / WORKER SAFETY
# ---------------------------------------------------------------------------

def test_engine_is_safe_to_call_from_a_background_thread(tmp_path):
    """extract_pages_from_pdf() has no tkinter dependency and no shared
    mutable module state, so it is safe to call from a worker thread --
    exactly how ui.py's _extract_worker() uses it. This test doesn't
    prove thread-safety under true concurrency (the app itself only
    ever runs one background PDF operation at a time -- see
    ui.py._any_operation_in_progress()); it proves the call completes
    correctly when actually made from a non-main thread.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    error = []

    def worker():
        try:
            extract_engine.extract_pages_from_pdf(src, output_path, [1, 3])
        except Exception as exc:  # pragma: no cover - failure path only
            error.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)

    assert not error
    assert _page_texts(output_path) == ["PAGE 2", "PAGE 4"]


# ---------------------------------------------------------------------------
# REGRESSION: reusing split_engine's parser must not change Split or
# Remove Pages
# ---------------------------------------------------------------------------

def test_split_engine_parse_page_ranges_still_importable_and_unchanged():
    import split_engine

    assert split_engine.parse_page_ranges("1,3,5-7", 10) == [[0], [2], [4, 5, 6]]


def test_remove_pages_engine_semantics_unaffected_by_extract_engine():
    import remove_pages_engine

    # Remove Pages must still sort and de-duplicate -- Extract Pages
    # living alongside it must not have altered that in any way.
    assert remove_pages_engine.resolve_pages_to_remove("7,1,4", 10) == [0, 3, 6]
