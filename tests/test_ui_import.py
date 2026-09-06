"""
test_ui_import.py

Phase 4 tests for the pure import-classification logic in ui.py
(`prepare_import`). This function deliberately has no tkinter
dependency, so these tests run without a display/Xvfb -- unlike full
widget interaction, which is tested manually per the Phase 4 checklist.

Covers, per the Phase 4 requirements:
  - Valid PDFs are added with correct name/size/page count, in order.
  - Duplicate detection is by exact resolved path, not filename (two
    different folders' "report.pdf" are NOT duplicates of each other).
  - A duplicate within a single batch is caught.
  - A duplicate against already-imported files (the "Add More" case) is
    caught.
  - Corrupted, encrypted, and missing files are classified as errors
    with a human-readable reason, without raising and without aborting
    the rest of the batch.
  - Original files are never modified by the import/validation process.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf
import pytest

from ui import prepare_import


@pytest.fixture
def pdf_dir(tmp_path):
    """Builds a small set of fixture PDFs covering the Phase 4 edge cases."""
    (tmp_path / "dirA").mkdir()

    # Valid: 3-page PDF
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    doc.save(tmp_path / "report.pdf")
    doc.close()

    # Valid: 5-page PDF
    doc = pymupdf.open()
    for _ in range(5):
        doc.new_page(width=792, height=612)  # landscape
    doc.save(tmp_path / "notes.pdf")
    doc.close()

    # Same filename as report.pdf, but a different folder -- NOT a duplicate.
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "dirA" / "report.pdf")
    doc.close()

    # Corrupt: garbage bytes with a .pdf extension.
    (tmp_path / "corrupt.pdf").write_bytes(b"NOT A REAL PDF FILE" * 20)

    # Encrypted / password-protected.
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        tmp_path / "protected.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret123",
        owner_pw="secret123",
    )
    doc.close()

    return tmp_path


def test_valid_pdfs_added_in_order_with_correct_metadata(pdf_dir):
    paths = [pdf_dir / "report.pdf", pdf_dir / "notes.pdf"]
    result = prepare_import(paths, existing_paths=set())

    assert [f.name for f in result["added"]] == ["report.pdf", "notes.pdf"]
    assert result["added"][0].page_count == 3
    assert result["added"][1].page_count == 5
    assert result["added"][0].size > 0
    assert not result["skipped_duplicates"]
    assert not result["errors"]


def test_same_filename_different_folder_is_not_a_duplicate(pdf_dir):
    paths = [pdf_dir / "report.pdf", pdf_dir / "dirA" / "report.pdf"]
    result = prepare_import(paths, existing_paths=set())

    assert len(result["added"]) == 2
    assert not result["skipped_duplicates"]


def test_exact_duplicate_path_within_same_batch_is_skipped(pdf_dir):
    paths = [pdf_dir / "report.pdf", pdf_dir / "report.pdf"]
    result = prepare_import(paths, existing_paths=set())

    assert len(result["added"]) == 1
    assert len(result["skipped_duplicates"]) == 1


def test_duplicate_against_already_imported_files_is_skipped(pdf_dir):
    existing = {(pdf_dir / "report.pdf").resolve()}
    result = prepare_import([pdf_dir / "report.pdf", pdf_dir / "notes.pdf"], existing)

    assert [f.name for f in result["added"]] == ["notes.pdf"]
    assert len(result["skipped_duplicates"]) == 1


def test_corrupt_pdf_is_reported_as_error_not_raised(pdf_dir):
    result = prepare_import([pdf_dir / "corrupt.pdf"], existing_paths=set())

    assert not result["added"]
    assert len(result["errors"]) == 1
    path, message = result["errors"][0]
    assert path.name == "corrupt.pdf"
    assert isinstance(message, str) and message  # human-readable, non-empty


def test_encrypted_pdf_is_reported_as_error(pdf_dir):
    result = prepare_import([pdf_dir / "protected.pdf"], existing_paths=set())

    assert not result["added"]
    assert len(result["errors"]) == 1
    path, message = result["errors"][0]
    assert path.name == "protected.pdf"
    assert "password" in message.lower()


def test_missing_file_is_reported_as_error(pdf_dir):
    result = prepare_import([pdf_dir / "does_not_exist.pdf"], existing_paths=set())

    assert not result["added"]
    assert len(result["errors"]) == 1


def test_one_bad_file_does_not_abort_the_rest_of_the_batch(pdf_dir):
    paths = [pdf_dir / "corrupt.pdf", pdf_dir / "report.pdf", pdf_dir / "protected.pdf"]
    result = prepare_import(paths, existing_paths=set())

    assert [f.name for f in result["added"]] == ["report.pdf"]
    assert len(result["errors"]) == 2


def test_import_never_modifies_source_files(pdf_dir):
    target = pdf_dir / "report.pdf"
    original_bytes = target.read_bytes()

    prepare_import([target], existing_paths=set())

    assert target.read_bytes() == original_bytes
