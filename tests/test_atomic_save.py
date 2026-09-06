"""
test_atomic_save.py

Phase 7 tests for the atomic, crash-safe output writing in pdf_engine.py
(_atomic_save_pdf(), and its use inside merge_pdfs() and compress_pdf()).

Covers, per the Phase 7 requirements:
  - A successful save produces correct, complete output.
  - A simulated failure partway through saving leaves NO file at all at
    the destination (not a partial/corrupt one) when nothing existed
    there before.
  - A simulated failure when a file already existed at the destination
    leaves that original file completely untouched -- the operation
    never partially overwrites it.
  - No leftover temp files survive a failed save.
  - Filesystem errors (e.g. an unwritable destination folder) surface as
    a human-readable PDFEngineError, not a raw OSError/traceback.
  - Source files are never modified by any of this.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine


def _make_pdf(path, pages=1, text=None):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((50, 50), text)
    doc.save(path)
    doc.close()


# ---------------------------------------------------------------------------
# merge_pdfs: success path (unchanged external behavior)
# ---------------------------------------------------------------------------

def test_merge_pdfs_still_produces_correct_output(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)

    output = tmp_path / "merged.pdf"
    pdf_engine.merge_pdfs([a, b], output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5


def test_merge_pdfs_leaves_no_temp_files_behind(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, pages=1)
    output = tmp_path / "merged.pdf"

    pdf_engine.merge_pdfs([a], output)

    leftover_temp_files = list(tmp_path.glob(".tmp_*"))
    assert leftover_temp_files == []


# ---------------------------------------------------------------------------
# Atomic behavior: simulated save failure
# ---------------------------------------------------------------------------

def test_failed_merge_leaves_no_file_when_destination_did_not_exist(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, pages=1)
    output = tmp_path / "merged.pdf"
    assert not output.exists()

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.merge_pdfs([a], output)

    assert not output.exists(), "a failed save must not leave any file at the destination"
    assert list(tmp_path.glob(".tmp_*")) == [], "temp file must be cleaned up after failure"


def test_failed_merge_does_not_touch_existing_destination_file(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, pages=1)
    output = tmp_path / "merged.pdf"
    original_content = b"this is the pre-existing merged.pdf content"
    output.write_bytes(original_content)

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.merge_pdfs([a], output)

    assert output.read_bytes() == original_content, (
        "a failed save must leave a pre-existing destination file "
        "completely untouched, not partially overwritten"
    )
    assert list(tmp_path.glob(".tmp_*")) == []


def test_failed_compress_leaves_no_file_when_destination_did_not_exist(tmp_path):
    src = tmp_path / "source.pdf"
    _make_pdf(src, pages=1)
    output = tmp_path / "compressed.pdf"

    with patch.object(pymupdf.Document, "save", side_effect=OSError("permission denied")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.compress_pdf(src, output, level="recommended")

    assert not output.exists()
    assert list(tmp_path.glob(".tmp_*")) == []


def test_failed_compress_does_not_touch_existing_destination_file(tmp_path):
    src = tmp_path / "source.pdf"
    _make_pdf(src, pages=1)
    output = tmp_path / "compressed.pdf"
    original_content = b"pre-existing compressed.pdf content"
    output.write_bytes(original_content)

    with patch.object(pymupdf.Document, "save", side_effect=OSError("permission denied")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.compress_pdf(src, output, level="recommended")

    assert output.read_bytes() == original_content


def test_filesystem_error_message_is_human_readable_not_a_raw_traceback(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, pages=1)
    output = tmp_path / "merged.pdf"

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with pytest.raises(pdf_engine.PDFEngineError) as exc_info:
            pdf_engine.merge_pdfs([a], output)

    message = str(exc_info.value)
    assert "Traceback" not in message
    assert "merged.pdf" in message  # names the file the user cares about


def test_unwritable_destination_folder_raises_friendly_error(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, pages=1)
    # A destination whose parent can never be created (a file, not a
    # folder, sitting where a folder is expected).
    blocking_file = tmp_path / "not_a_folder"
    blocking_file.write_text("I am a file, not a directory")
    output = blocking_file / "merged.pdf"

    with pytest.raises(pdf_engine.PDFEngineError):
        pdf_engine.merge_pdfs([a], output)


# ---------------------------------------------------------------------------
# Source files are never modified
# ---------------------------------------------------------------------------

def test_source_files_untouched_after_successful_merge(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)
    original_a = a.read_bytes()
    original_b = b.read_bytes()

    pdf_engine.merge_pdfs([a, b], tmp_path / "merged.pdf")

    assert a.read_bytes() == original_a
    assert b.read_bytes() == original_b


def test_source_file_untouched_after_failed_merge(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, pages=1)
    original_a = a.read_bytes()

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.merge_pdfs([a], tmp_path / "merged.pdf")

    assert a.read_bytes() == original_a


def test_source_file_untouched_after_successful_compress(tmp_path):
    src = tmp_path / "source.pdf"
    _make_pdf(src, pages=3)
    original = src.read_bytes()

    pdf_engine.compress_pdf(src, tmp_path / "out.pdf", level="recommended")

    assert src.read_bytes() == original
