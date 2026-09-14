"""
test_organize_engine.py

Comprehensive tests for organize_engine.py (Phase 16): page-order
parsing (parse_page_order -- a dedicated, ranges-free, complete-
permutation parser, NOT built on split_engine's range parser) and the
actual organize_pages_from_pdf() engine (valid permutations, invalid
input, source safety, output safety).

No tkinter dependency -- organize_engine.py is pure logic + file I/O, so
these run without a display.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import organize_engine
import pdf_engine
from organize_engine import OrganizeOrderError


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
# parse_page_order() -- VALID INPUT
# ---------------------------------------------------------------------------

def test_identity_order():
    assert organize_engine.parse_page_order("1,2,3,4,5", 5) == [0, 1, 2, 3, 4]


def test_simple_swap():
    assert organize_engine.parse_page_order("2,1,3,4,5", 5) == [1, 0, 2, 3, 4]


def test_arbitrary_permutation():
    assert organize_engine.parse_page_order("3,1,5,2,4", 5) == [2, 0, 4, 1, 3]


def test_reverse_order():
    assert organize_engine.parse_page_order("5,4,3,2,1", 5) == [4, 3, 2, 1, 0]


def test_multiple_page_movement():
    assert organize_engine.parse_page_order("1,4,5,2,3", 5) == [0, 3, 4, 1, 2]


def test_single_page_document_identity():
    assert organize_engine.parse_page_order("1", 1) == [0]


def test_whitespace_around_tokens_is_tolerated():
    assert organize_engine.parse_page_order(" 5, 3, 1, 2, 4 ", 5) == [4, 2, 0, 1, 3]


def test_identity_order_helper_matches_parser():
    text = organize_engine.identity_order(6)
    assert text == "1,2,3,4,5,6"
    assert organize_engine.parse_page_order(text, 6) == [0, 1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# parse_page_order() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_string_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("", 5)


def test_whitespace_only_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("   ", 5)


def test_none_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order(None, 5)


def test_page_zero_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("0,1,2,3,4", 5)


def test_negative_page_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("-1,2,3,4,5", 5)


def test_out_of_range_page_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,2,3,4,6", 5)


def test_non_numeric_token_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,2,abc,4,5", 5)


def test_malformed_decimal_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,2,3.5,4,5", 5)


def test_empty_token_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,,3,4,5", 5)


def test_trailing_comma_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,2,3,4,5,", 5)


def test_duplicate_page_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,1,3,4,5", 5)


def test_duplicate_page_rejected_even_if_another_is_missing():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("3,1,5,2,2", 5)


def test_incomplete_order_missing_one_page_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("3,1,5,2", 5)


def test_incomplete_order_only_one_page_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1", 5)


def test_too_many_pages_listed_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1,2,3,4,5,1", 5)


def test_range_syntax_rejected_not_silently_expanded():
    """The core semantic decision documented in organize_engine.py's
    module docstring: ranges are deliberately NOT supported for
    reordering, to avoid ambiguity. "1-3,4,5" must be rejected outright,
    never silently expanded to [1,2,3,4,5].
    """
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1-3,4,5", 5)


def test_page_count_zero_rejected():
    with pytest.raises(OrganizeOrderError):
        organize_engine.parse_page_order("1", 0)


def test_missing_pages_named_in_error_message():
    with pytest.raises(OrganizeOrderError) as excinfo:
        organize_engine.parse_page_order("3,1", 5)
    message = str(excinfo.value)
    assert "2" in message
    assert "4" in message
    assert "5" in message


# ---------------------------------------------------------------------------
# organize_pages_from_pdf() -- VALID INPUT / content correctness
# ---------------------------------------------------------------------------

def test_organize_identity_order_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(src, output_path, [0, 1, 2, 3, 4])

    assert _page_texts(output_path) == [
        "PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5",
    ]


def test_organize_arbitrary_permutation_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    # 1-based "3,1,5,2,4" -> 0-based [2, 0, 4, 1, 3]
    organize_engine.organize_pages_from_pdf(src, output_path, [2, 0, 4, 1, 3])

    assert _page_texts(output_path) == [
        "PAGE 3", "PAGE 1", "PAGE 5", "PAGE 2", "PAGE 4",
    ]


def test_organize_reverse_order_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(src, output_path, [4, 3, 2, 1, 0])

    assert _page_texts(output_path) == [
        "PAGE 5", "PAGE 4", "PAGE 3", "PAGE 2", "PAGE 1",
    ]


def test_organize_simple_swap_content(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(src, output_path, [1, 0, 2])

    assert _page_texts(output_path) == ["PAGE 2", "PAGE 1", "PAGE 3"]


def test_organize_via_parse_then_engine_end_to_end(tmp_path):
    """The exact path the UI takes: parse_page_order() then feed
    straight into organize_pages_from_pdf().
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    order = organize_engine.parse_page_order("3,1,5,2,4", 5)
    organize_engine.organize_pages_from_pdf(src, output_path, order)

    assert _page_texts(output_path) == [
        "PAGE 3", "PAGE 1", "PAGE 5", "PAGE 2", "PAGE 4",
    ]


def test_output_page_count_matches_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=8)
    output_path = tmp_path / "output.pdf"

    order = list(reversed(range(8)))
    organize_engine.organize_pages_from_pdf(src, output_path, order)

    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 8


def test_example_from_spec(tmp_path):
    """Source pages 1 2 3 4 5, requested order 3 1 5 2 4 -> output
    3 1 5 2 4 -- the exact example from the Phase 16 spec.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    order = organize_engine.parse_page_order("3,1,5,2,4", 5)
    organize_engine.organize_pages_from_pdf(src, output_path, order)

    assert _page_texts(output_path) == [
        "PAGE 3", "PAGE 1", "PAGE 5", "PAGE 2", "PAGE 4",
    ]
    assert _page_texts(src) == [
        "PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5",
    ]


# ---------------------------------------------------------------------------
# organize_pages_from_pdf() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_page_order_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(OrganizeOrderError):
        organize_engine.organize_pages_from_pdf(src, output_path, [])

    assert not output_path.exists()


def test_incomplete_order_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(OrganizeOrderError):
        organize_engine.organize_pages_from_pdf(src, output_path, [0, 1, 2])

    assert not output_path.exists()


def test_duplicate_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(OrganizeOrderError):
        organize_engine.organize_pages_from_pdf(
            src, output_path, [0, 0, 2, 3, 4]
        )

    assert not output_path.exists()


def test_out_of_range_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(OrganizeOrderError):
        organize_engine.organize_pages_from_pdf(
            src, output_path, [0, 1, 2, 3, 10]
        )

    assert not output_path.exists()


def test_negative_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(OrganizeOrderError):
        organize_engine.organize_pages_from_pdf(
            src, output_path, [-1, 1, 2, 3, 4]
        )

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        organize_engine.organize_pages_from_pdf(missing, output_path, [0])

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        organize_engine.organize_pages_from_pdf(corrupt, output_path, [0])


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
        organize_engine.organize_pages_from_pdf(protected, output_path, [0, 1])


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        organize_engine.organize_pages_from_pdf(a_directory, output_path, [0])


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            organize_engine.organize_pages_from_pdf(
                src, output_path, [0, 1, 2, 3, 4]
            )

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# SOURCE SAFETY
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged_after_successful_organize(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(
        src, output_path, [5, 4, 3, 2, 1, 0]
    )

    assert src.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_failed_organize(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            organize_engine.organize_pages_from_pdf(
                src, output_path, [0, 1, 2, 3, 4]
            )

    assert src.read_bytes() == original_bytes


def test_source_page_count_unchanged_after_organize(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(
        src, output_path, [5, 4, 3, 2, 1, 0]
    )

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_source_content_order_unchanged_after_organize(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(
        src, output_path, [4, 3, 2, 1, 0]
    )

    assert _page_texts(src) == [
        "PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5",
    ]


def test_failed_operation_does_not_corrupt_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            organize_engine.organize_pages_from_pdf(
                src, output_path, [0, 1, 2, 3, 4, 5]
            )

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_output_is_a_different_file_from_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    organize_engine.organize_pages_from_pdf(
        src, output_path, [4, 3, 2, 1, 0]
    )

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
            organize_engine.organize_pages_from_pdf(
                src, output_path, [0, 1, 2, 3, 4]
            )

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# OUTPUT SAFETY
# ---------------------------------------------------------------------------

def test_default_output_naming_convention():
    import file_manager

    stem = file_manager.sanitize_windows_filename(Path("report.pdf").stem)
    default_name = file_manager.ensure_pdf_extension(f"{stem}_organized")
    assert default_name == "report_organized.pdf"


def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    """If the underlying save fails, no partial/temp file should be left
    behind at output_path -- proving _atomic_save_pdf (not a second,
    home-grown save mechanism) is what organize_pages_from_pdf() uses.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            organize_engine.organize_pages_from_pdf(
                src, output_path, [0, 1, 2, 3, 4]
            )

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_progress_callback_reports_reordering_and_saving(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    messages = []
    organize_engine.organize_pages_from_pdf(
        src, output_path, [4, 3, 2, 1, 0], progress_callback=messages.append,
    )

    assert any("reorder" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_error_messages_contain_no_traceback(tmp_path):
    output_path = tmp_path / "output.pdf"
    try:
        organize_engine.organize_pages_from_pdf(
            tmp_path / "missing.pdf", output_path, [0],
        )
    except pdf_engine.PDFEngineError as exc:
        assert "Traceback" not in str(exc)


# ---------------------------------------------------------------------------
# CONCURRENT / WORKER SAFETY
# ---------------------------------------------------------------------------

def test_engine_is_safe_to_call_from_a_background_thread(tmp_path):
    """organize_pages_from_pdf() has no tkinter dependency and no shared
    mutable module state, so it is safe to call from a worker thread --
    exactly how ui.py's _organize_worker() uses it. This test doesn't
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
            organize_engine.organize_pages_from_pdf(
                src, output_path, [2, 0, 4, 1, 3]
            )
        except Exception as exc:  # pragma: no cover - failure path only
            error.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)

    assert not error
    assert _page_texts(output_path) == [
        "PAGE 3", "PAGE 1", "PAGE 5", "PAGE 2", "PAGE 4",
    ]


# ---------------------------------------------------------------------------
# REGRESSION: this module must not have altered any other engine
# ---------------------------------------------------------------------------

def test_split_engine_parse_page_ranges_still_supports_ranges():
    import split_engine

    assert split_engine.parse_page_ranges("1,3,5-7", 10) == [[0], [2], [4, 5, 6]]


def test_extract_engine_semantics_unaffected_by_organize_engine():
    import extract_engine

    assert extract_engine.resolve_pages_to_extract("4,2", 5) == [3, 1]


def test_remove_pages_engine_semantics_unaffected_by_organize_engine():
    import remove_pages_engine

    assert remove_pages_engine.resolve_pages_to_remove("7,1,4", 10) == [0, 3, 6]
