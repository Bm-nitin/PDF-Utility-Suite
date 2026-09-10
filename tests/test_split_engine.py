"""
test_split_engine.py

Comprehensive tests for split_engine.py: page-range parsing, split-mode
group computation, and the split engine itself (all three modes, output
naming, error handling, atomicity/cleanup on failure).

No tkinter dependency -- split_engine.py is pure logic + file I/O, so
these run without a display.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine
import split_engine
from split_engine import PageRangeError


def _make_pdf(path, pages=1, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


# ---------------------------------------------------------------------------
# PAGE RANGE PARSER
# ---------------------------------------------------------------------------

def test_single_page():
    assert split_engine.parse_page_ranges("3", 10) == [[2]]


def test_normal_range():
    assert split_engine.parse_page_ranges("1-5", 10) == [[0, 1, 2, 3, 4]]


def test_multiple_values():
    assert split_engine.parse_page_ranges("1,3,5", 10) == [[0], [2], [4]]


def test_mixed_single_pages_and_ranges():
    assert split_engine.parse_page_ranges("1-3,5,7-9", 10) == [
        [0, 1, 2], [4], [6, 7, 8]
    ]


def test_whitespace_is_ignored():
    assert split_engine.parse_page_ranges(" 1 - 3 , 5 , 7 - 9 ", 10) == [
        [0, 1, 2], [4], [6, 7, 8]
    ]


def test_page_zero_rejected():
    with pytest.raises(PageRangeError, match="pages start at 1"):
        split_engine.parse_page_ranges("0", 10)


def test_negative_page_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("-5", 10)


def test_negative_page_in_range_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("-3-5", 10)


def test_malformed_range_too_many_dashes():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("1-2-3", 10)


def test_malformed_range_trailing_dash():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("5-", 10)


def test_malformed_range_leading_dash_only():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("-", 10)


def test_non_numeric_value_rejected():
    with pytest.raises(PageRangeError, match="non-numeric"):
        split_engine.parse_page_ranges("abc", 10)


def test_non_numeric_in_range_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("1-abc", 10)


def test_decimal_value_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("1.5", 10)


def test_start_greater_than_end_rejected():
    with pytest.raises(PageRangeError, match="after the end page"):
        split_engine.parse_page_ranges("5-2", 10)


def test_page_beyond_document_count_rejected():
    with pytest.raises(PageRangeError, match="only has 10 pages"):
        split_engine.parse_page_ranges("15", 10)


def test_range_end_beyond_document_count_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("8-15", 10)


def test_empty_input_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("", 10)


def test_whitespace_only_input_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("   ", 10)


def test_empty_token_between_commas_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("1,,3", 10)


def test_trailing_comma_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("1,3,", 10)


def test_zero_page_count_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("1", 0)


def test_duplicate_pages_are_allowed_and_preserved():
    """Explicit design decision (documented in split_engine.py):
    duplicates are not deduplicated -- each token is independent.
    """
    result = split_engine.parse_page_ranges("3,3", 10)
    assert result == [[2], [2]]


def test_overlapping_ranges_are_allowed_and_preserved():
    result = split_engine.parse_page_ranges("1-3,2-4", 10)
    assert result == [[0, 1, 2], [1, 2, 3]]


def test_multiple_ranges_preserve_input_order():
    """Groups come back in the exact order the user typed them, not
    sorted/renumbered.
    """
    result = split_engine.parse_page_ranges("5,1-3", 10)
    assert result == [[4], [0, 1, 2]]


def test_single_page_document_valid_range():
    assert split_engine.parse_page_ranges("1", 1) == [[0]]


def test_single_page_document_page_2_rejected():
    with pytest.raises(PageRangeError):
        split_engine.parse_page_ranges("2", 1)


# ---------------------------------------------------------------------------
# Split-mode group computation
# ---------------------------------------------------------------------------

def test_compute_individual_page_groups():
    assert split_engine.compute_individual_page_groups(4) == [[0], [1], [2], [3]]


def test_compute_individual_page_groups_single_page():
    assert split_engine.compute_individual_page_groups(1) == [[0]]


def test_compute_individual_page_groups_zero_pages_rejected():
    with pytest.raises(PageRangeError):
        split_engine.compute_individual_page_groups(0)


def test_compute_every_n_page_groups_even_division():
    assert split_engine.compute_every_n_page_groups(9, 3) == [
        [0, 1, 2], [3, 4, 5], [6, 7, 8]
    ]


def test_compute_every_n_page_groups_remainder():
    assert split_engine.compute_every_n_page_groups(10, 3) == [
        [0, 1, 2], [3, 4, 5], [6, 7, 8], [9]
    ]


def test_compute_every_n_page_groups_n_equals_1():
    assert split_engine.compute_every_n_page_groups(3, 1) == [[0], [1], [2]]


def test_compute_every_n_page_groups_n_greater_than_page_count():
    assert split_engine.compute_every_n_page_groups(5, 100) == [[0, 1, 2, 3, 4]]


def test_compute_every_n_page_groups_n_zero_rejected():
    with pytest.raises(PageRangeError):
        split_engine.compute_every_n_page_groups(10, 0)


def test_compute_every_n_page_groups_n_negative_rejected():
    with pytest.raises(PageRangeError):
        split_engine.compute_every_n_page_groups(10, -1)


# ---------------------------------------------------------------------------
# SPLIT ENGINE: individual pages
# ---------------------------------------------------------------------------

def test_individual_page_split_produces_correct_file_count(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_dir = tmp_path / "out"

    groups = split_engine.compute_individual_page_groups(10)
    result = split_engine.split_pdf(src, groups, output_dir)

    assert len(result["output_paths"]) == 10
    for p in result["output_paths"]:
        with pymupdf.open(p) as doc:
            assert doc.page_count == 1


def test_individual_page_split_naming_convention(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_dir = tmp_path / "out"

    groups = split_engine.compute_individual_page_groups(10)
    result = split_engine.split_pdf(src, groups, output_dir)

    names = [p.name for p in result["output_paths"]]
    assert names[0] == "document_001.pdf"
    assert names[9] == "document_010.pdf"


def test_individual_page_split_content_and_order(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5, text_prefix="PAGE_")
    output_dir = tmp_path / "out"

    groups = split_engine.compute_individual_page_groups(5)
    result = split_engine.split_pdf(src, groups, output_dir)

    for i, p in enumerate(result["output_paths"], start=1):
        with pymupdf.open(p) as doc:
            assert f"PAGE_{i}" in doc[0].get_text()


def test_single_page_pdf_individual_split(tmp_path):
    src = tmp_path / "single.pdf"
    _make_pdf(src, pages=1)
    output_dir = tmp_path / "out"

    groups = split_engine.compute_individual_page_groups(1)
    result = split_engine.split_pdf(src, groups, output_dir)

    assert len(result["output_paths"]) == 1
    with pymupdf.open(result["output_paths"][0]) as doc:
        assert doc.page_count == 1


# ---------------------------------------------------------------------------
# SPLIT ENGINE: every N pages
# ---------------------------------------------------------------------------

def test_every_n_pages_split_correct_page_counts(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_dir = tmp_path / "out"

    groups = split_engine.compute_every_n_page_groups(10, 3)
    result = split_engine.split_pdf(src, groups, output_dir)

    counts = []
    for p in result["output_paths"]:
        with pymupdf.open(p) as doc:
            counts.append(doc.page_count)
    assert counts == [3, 3, 3, 1]


def test_every_n_greater_than_page_count_produces_one_file(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_dir = tmp_path / "out"

    groups = split_engine.compute_every_n_page_groups(5, 100)
    result = split_engine.split_pdf(src, groups, output_dir)

    assert len(result["output_paths"]) == 1
    with pymupdf.open(result["output_paths"][0]) as doc:
        assert doc.page_count == 5


def test_every_n_pages_n_equals_1_is_same_as_individual(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=4)
    output_dir = tmp_path / "out"

    groups = split_engine.compute_every_n_page_groups(4, 1)
    result = split_engine.split_pdf(src, groups, output_dir)

    assert len(result["output_paths"]) == 4
    for p in result["output_paths"]:
        with pymupdf.open(p) as doc:
            assert doc.page_count == 1


# ---------------------------------------------------------------------------
# SPLIT ENGINE: custom ranges
# ---------------------------------------------------------------------------

def test_custom_range_split_correct_files_and_naming(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_dir = tmp_path / "out"

    groups = split_engine.parse_page_ranges("1-3,5,7-9", 10)
    result = split_engine.split_pdf(src, groups, output_dir, include_range_label=True)

    names = [p.name for p in result["output_paths"]]
    assert names == [
        "document_001_pages_1-3.pdf",
        "document_002_pages_5.pdf",
        "document_003_pages_7-9.pdf",
    ]


def test_custom_range_split_correct_page_counts(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_dir = tmp_path / "out"

    groups = split_engine.parse_page_ranges("1-3,5,7-9", 10)
    result = split_engine.split_pdf(src, groups, output_dir, include_range_label=True)

    counts = []
    for p in result["output_paths"]:
        with pymupdf.open(p) as doc:
            counts.append(doc.page_count)
    assert counts == [3, 1, 3]


def test_custom_range_split_content_correctness(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10, text_prefix="PAGE_")
    output_dir = tmp_path / "out"

    groups = split_engine.parse_page_ranges("1-3,5,7-9", 10)
    result = split_engine.split_pdf(src, groups, output_dir, include_range_label=True)

    with pymupdf.open(result["output_paths"][0]) as doc:
        assert "PAGE_1" in doc[0].get_text()
        assert "PAGE_3" in doc[2].get_text()
    with pymupdf.open(result["output_paths"][1]) as doc:
        assert "PAGE_5" in doc[0].get_text()


# ---------------------------------------------------------------------------
# Unicode / spaces / parentheses / long filenames
# ---------------------------------------------------------------------------

def test_unicode_source_filename(tmp_path):
    src = tmp_path / "résumé 報告書.pdf"
    _make_pdf(src, pages=3)
    output_dir = tmp_path / "out"

    result = split_engine.split_pdf(
        src, split_engine.compute_individual_page_groups(3), output_dir
    )
    assert len(result["output_paths"]) == 3
    assert result["output_paths"][0].exists()


def test_spaces_and_parentheses_in_filename(tmp_path):
    src = tmp_path / "My Document (Final Version).pdf"
    _make_pdf(src, pages=2)
    output_dir = tmp_path / "out"

    result = split_engine.split_pdf(
        src, split_engine.compute_individual_page_groups(2), output_dir
    )
    assert all(p.exists() for p in result["output_paths"])
    assert "My Document (Final Version)" in result["output_paths"][0].name


def test_long_filename(tmp_path):
    long_stem = "a" * 150
    src = tmp_path / f"{long_stem}.pdf"
    _make_pdf(src, pages=2)
    output_dir = tmp_path / "out"

    result = split_engine.split_pdf(
        src, split_engine.compute_individual_page_groups(2), output_dir
    )
    assert result["output_paths"][0].name.startswith(long_stem)


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------

def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_dir = tmp_path / "out"
    with pytest.raises(pdf_engine.InvalidPDFError):
        split_engine.split_pdf(missing, [[0]], output_dir)
    assert not output_dir.exists() or not list(output_dir.glob("*.pdf"))


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_dir = tmp_path / "out"
    with pytest.raises(pdf_engine.InvalidPDFError):
        split_engine.split_pdf(corrupt, [[0]], output_dir)


def test_encrypted_source_raises_clear_error(tmp_path):
    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret",
    )
    doc.close()
    output_dir = tmp_path / "out"
    with pytest.raises(pdf_engine.EncryptedPDFError):
        split_engine.split_pdf(protected, [[0]], output_dir)


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_dir = tmp_path / "out"
    with pytest.raises(pdf_engine.InvalidPDFError):
        split_engine.split_pdf(a_directory, [[0]], output_dir)


def test_empty_groups_raises_clear_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_dir = tmp_path / "out"
    with pytest.raises(PageRangeError, match="Nothing to split"):
        split_engine.split_pdf(src, [], output_dir)


# ---------------------------------------------------------------------------
# Output collisions
# ---------------------------------------------------------------------------

def test_existing_output_collision_is_avoided(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=2)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "document_001.pdf").write_bytes(b"pre-existing content")

    result = split_engine.split_pdf(
        src, split_engine.compute_individual_page_groups(2), output_dir
    )

    names = [p.name for p in result["output_paths"]]
    assert "document_001 (1).pdf" in names
    assert (output_dir / "document_001.pdf").read_bytes() == b"pre-existing content"


# ---------------------------------------------------------------------------
# Source integrity and atomicity/failure cleanup
# ---------------------------------------------------------------------------

def test_source_remains_byte_unchanged_after_split(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    original_bytes = src.read_bytes()
    output_dir = tmp_path / "out"

    split_engine.split_pdf(
        src, split_engine.compute_individual_page_groups(6), output_dir
    )

    assert src.read_bytes() == original_bytes


def test_failure_partway_through_removes_only_this_operations_outputs(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_dir = tmp_path / "out"

    call_count = {"n": 0}
    original = pdf_engine._atomic_save_pdf

    def flaky(doc_obj, output_path, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise OSError("simulated failure on part 3")
        return original(doc_obj, output_path, **kwargs)

    groups = split_engine.compute_individual_page_groups(5)

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=flaky):
        with pytest.raises(pdf_engine.PDFEngineError):
            split_engine.split_pdf(src, groups, output_dir)

    remaining = list(output_dir.glob("*.pdf")) if output_dir.exists() else []
    assert remaining == [], f"expected no leftover partial outputs, found {remaining}"


def test_failure_does_not_remove_preexisting_unrelated_files(tmp_path):
    """The cleanup-on-failure logic must only ever delete paths THIS
    call created -- never a pre-existing file that happens to share the
    output directory.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    unrelated = output_dir / "unrelated_preexisting_file.pdf"
    unrelated.write_bytes(b"do not touch me")

    call_count = {"n": 0}
    original = pdf_engine._atomic_save_pdf

    def flaky(doc_obj, output_path, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated failure")
        return original(doc_obj, output_path, **kwargs)

    groups = split_engine.compute_individual_page_groups(5)

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=flaky):
        with pytest.raises(pdf_engine.PDFEngineError):
            split_engine.split_pdf(src, groups, output_dir)

    assert unrelated.exists()
    assert unrelated.read_bytes() == b"do not touch me"


def test_failure_does_not_remove_source_pdf(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_dir = tmp_path / "out"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            split_engine.split_pdf(
                src, split_engine.compute_individual_page_groups(5), output_dir
            )

    assert src.exists()


def test_progress_callback_reports_each_part(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=4)
    output_dir = tmp_path / "out"

    messages = []
    split_engine.split_pdf(
        src,
        split_engine.compute_individual_page_groups(4),
        output_dir,
        progress_callback=messages.append,
    )

    assert any("part 1 of 4" in m for m in messages)
    assert any("part 4 of 4" in m for m in messages)
    assert any("completed successfully" in m.lower() for m in messages)


def test_error_messages_contain_no_traceback(tmp_path):
    output_dir = tmp_path / "out"
    try:
        split_engine.split_pdf(tmp_path / "missing.pdf", [[0]], output_dir)
    except pdf_engine.PDFEngineError as exc:
        assert "Traceback" not in str(exc)
