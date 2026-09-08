"""
test_phase11_hardening.py

Phase 11 comprehensive testing and hardening. This file covers edge
cases identified by systematically reviewing every production module
against what existing tests (Phases 2-10) actually exercise, rather than
re-testing what's already well covered.

Gaps found and closed here:
  - merge_pdfs() with an empty input list (currently raises
    PDFEngineError -- was never directly asserted).
  - merge_pdfs() called directly with a single file (the UI always
    requires 2+, but the function itself has no such restriction and
    should behave sensibly as a no-op-ish copy).
  - Long filenames (150-200+ characters) through the real pipeline.
  - A *successful* overwrite of an existing output file (Phases 7-10
    only tested the FAILURE case leaving an existing file untouched;
    nobody had proven the SUCCESS case correctly replaces it).
  - Merging/compressing the same source file twice in one operation
    (a legitimate thing a user can do -- add the same PDF, remove and
    re-add it, etc. -- must not corrupt or double up incorrectly).
  - Read-only source files (never require write access to a source).
  - A directory passed where a PDF path was expected.
  - Compression preset ordering: Maximum should never produce a LARGER
    result than Low for genuinely compressible content.
  - is_encrypted() and confirm_overwrite_if_needed() -- both defined,
    exported, and part of the public API, but never directly exercised
    by any existing test (confirm_overwrite_if_needed isn't currently
    wired into any button flow, which is fine -- Merge/Compress Only's
    Save As already relies on the native dialog's own overwrite prompt,
    per the Phase 7 design decision -- but the utility function itself
    should still be correct and tested on its own terms).
  - Unicode/spaces end-to-end through real pdf_engine calls (not just
    the file_manager path-string utilities already tested in Phase 7).

No production code needed to change for any of these -- every one of
them was already handled correctly; this file exists purely to prove
it, per Phase 11's "if you find only a missing test, add the test
without changing production behavior" instruction.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager
import pdf_engine


def _make_pdf(path, pages=1, text=None, image_heavy=False):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((50, 50), text)
        if image_heavy:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 800, 800))
            pix.set_rect(pix.irect, (120, 60, 200))
            page.insert_image(pymupdf.Rect(0, 0, 800, 800), pixmap=pix)
    doc.save(path)
    doc.close()


# ---------------------------------------------------------------------------
# A. PDF ENGINE -- edge cases
# ---------------------------------------------------------------------------

def test_merge_pdfs_empty_list_raises_clear_error(tmp_path):
    with pytest.raises(pdf_engine.PDFEngineError) as exc_info:
        pdf_engine.merge_pdfs([], tmp_path / "out.pdf")
    assert "Traceback" not in str(exc_info.value)
    assert not (tmp_path / "out.pdf").exists()


def test_merge_pdfs_single_file_direct_call(tmp_path):
    """The UI restricts Merge Only to 2+ files, but merge_pdfs() itself
    has no such restriction -- calling it directly with one file should
    just produce a correct one-file "merge" (effectively a copy through
    the merge machinery), not an error.
    """
    src = tmp_path / "a.pdf"
    _make_pdf(src, pages=4)
    output = tmp_path / "out.pdf"

    pdf_engine.merge_pdfs([src], output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 4


def test_long_filename_end_to_end(tmp_path):
    long_stem = "a" * 180  # well under Linux's 255-byte limit, but long
    src = tmp_path / f"{long_stem}.pdf"
    _make_pdf(src, pages=2)
    output = tmp_path / f"{long_stem}_merged.pdf"

    pdf_engine.merge_pdfs([src], output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 2


def test_successful_merge_overwrites_existing_output_file(tmp_path):
    """Distinct from the Phase 7/10 failure-path tests, which only prove
    an existing file is left UNTOUCHED when the save fails. This proves
    the mirror-image success case: when the save succeeds, the existing
    file's content is genuinely replaced with the new merged content,
    not left stale or corrupted.
    """
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)

    output = tmp_path / "out.pdf"
    output.write_bytes(b"this is stale content that must be replaced")

    pdf_engine.merge_pdfs([a, b], output)

    assert output.read_bytes() != b"this is stale content that must be replaced"
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5


def test_successful_compress_overwrites_existing_output_file(tmp_path):
    src = tmp_path / "a.pdf"
    _make_pdf(src, pages=3)

    output = tmp_path / "out.pdf"
    output.write_bytes(b"stale compressed content")

    pdf_engine.compress_pdf(src, output, level="recommended")

    assert output.read_bytes() != b"stale compressed content"
    with pymupdf.open(output) as doc:
        assert doc.page_count == 3


def test_merging_the_same_source_file_twice(tmp_path):
    """A user can legitimately end up merging the same file twice (e.g.
    intentionally duplicating a cover page). merge_pdfs() itself doesn't
    do duplicate detection -- that's the import layer's job (Phase 4) --
    so calling it directly with the same path twice must just merge it
    twice, correctly, not error or silently skip the second occurrence.
    """
    src = tmp_path / "a.pdf"
    _make_pdf(src, pages=2, text="REPEATED")
    output = tmp_path / "out.pdf"

    pdf_engine.merge_pdfs([src, src], output)

    with pymupdf.open(output) as doc:
        assert doc.page_count == 4
        assert "REPEATED" in doc[0].get_text()
        assert "REPEATED" in doc[2].get_text()


def test_read_only_source_file_can_still_be_merged(tmp_path):
    """Merging/compressing only ever needs READ access to source files.
    A read-only source file must work exactly like a normal one.
    """
    src = tmp_path / "a.pdf"
    _make_pdf(src, pages=2)
    src.chmod(0o444)  # read-only

    try:
        output = tmp_path / "out.pdf"
        pdf_engine.merge_pdfs([src], output)
        assert output.exists()
        with pymupdf.open(output) as doc:
            assert doc.page_count == 2
    finally:
        src.chmod(0o644)  # restore so tmp_path cleanup can delete it


def test_read_only_source_file_can_still_be_compressed(tmp_path):
    src = tmp_path / "a.pdf"
    _make_pdf(src, pages=2)
    src.chmod(0o444)

    try:
        output = tmp_path / "out.pdf"
        pdf_engine.compress_pdf(src, output, level="recommended")
        assert output.exists()
    finally:
        src.chmod(0o644)


def test_directory_passed_as_input_is_rejected_cleanly(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()

    with pytest.raises(pdf_engine.InvalidPDFError) as exc_info:
        pdf_engine.validate_pdf(a_directory)
    assert "folder" in str(exc_info.value).lower()


def test_directory_passed_to_merge_pdfs_is_rejected_cleanly(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output = tmp_path / "out.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        pdf_engine.merge_pdfs([a_directory], output)
    assert not output.exists()


def test_compression_preset_ordering_for_image_heavy_content(tmp_path):
    """Maximum compression must never produce a result larger than Low
    for genuinely compressible (image-heavy) content. This is a
    real-content spot check, not a guarantee for every possible PDF
    (the docstrings are explicit that compression always depends on
    source content) -- but for this deliberately compressible fixture,
    the ordering should hold.
    """
    src = tmp_path / "image_heavy.pdf"
    _make_pdf(src, pages=1, image_heavy=True)

    low_out = tmp_path / "low.pdf"
    max_out = tmp_path / "max.pdf"
    pdf_engine.compress_pdf(src, low_out, level="low")
    pdf_engine.compress_pdf(src, max_out, level="maximum")

    assert max_out.stat().st_size <= low_out.stat().st_size


def test_all_three_presets_produce_valid_readable_output(tmp_path):
    src = tmp_path / "src.pdf"
    _make_pdf(src, pages=3, image_heavy=True)

    for level in ("low", "recommended", "maximum"):
        output = tmp_path / f"out_{level}.pdf"
        pdf_engine.compress_pdf(src, output, level=level)
        with pymupdf.open(output) as doc:
            assert doc.page_count == 3


def test_is_encrypted_returns_true_for_encrypted_pdf(tmp_path):
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

    assert pdf_engine.is_encrypted(protected) is True


def test_is_encrypted_returns_false_for_normal_pdf(tmp_path):
    normal = tmp_path / "normal.pdf"
    _make_pdf(normal, pages=1)
    assert pdf_engine.is_encrypted(normal) is False


def test_is_encrypted_returns_false_for_nonexistent_file(tmp_path):
    """is_encrypted() is documented to fail safe (return False) rather
    than raise, for files it can't even open -- callers that need a
    hard error should use validate_pdf() instead.
    """
    assert pdf_engine.is_encrypted(tmp_path / "does_not_exist.pdf") is False


def test_unicode_filenames_end_to_end_through_merge(tmp_path):
    a = tmp_path / "résumé final 報告書.pdf"
    _make_pdf(a, pages=2)
    output = tmp_path / "输出 файл.pdf"

    pdf_engine.merge_pdfs([a], output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 2


def test_unicode_filenames_end_to_end_through_compress(tmp_path):
    src = tmp_path / "café ünïcödé.pdf"
    _make_pdf(src, pages=1)
    output = tmp_path / "compressed café.pdf"

    pdf_engine.compress_pdf(src, output, level="recommended")

    assert output.exists()


def test_merge_and_compress_actually_performs_both_operations(tmp_path):
    """A single, explicit, end-to-end proof that merge_and_compress()
    genuinely does both things: output page count equals the sum of
    inputs (proving the merge happened) AND compress_pdf's structural
    optimization ran (proving the compression stage happened, checked
    via a spy since size reduction itself isn't guaranteed for a
    synthetic 2-page text-only fixture).
    """
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2, image_heavy=True)
    _make_pdf(b, pages=3, image_heavy=True)
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "compress_pdf", wraps=pdf_engine.compress_pdf) as spy_compress:
        result = pdf_engine.merge_and_compress([a, b], output, level="maximum")

    spy_compress.assert_called_once()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5
    assert result["output_path"] == output


# ---------------------------------------------------------------------------
# B. FILE MANAGER -- previously-untested utility
# ---------------------------------------------------------------------------

def test_confirm_overwrite_returns_true_when_path_does_not_exist(tmp_path):
    with patch("tkinter.messagebox.askyesno") as mock_ask:
        result = file_manager.confirm_overwrite_if_needed(tmp_path / "new.pdf")
    assert result is True
    mock_ask.assert_not_called()  # no need to even ask if nothing's there


def test_confirm_overwrite_asks_and_returns_true_on_confirmation(tmp_path):
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"x")

    with patch("tkinter.messagebox.askyesno", return_value=True) as mock_ask:
        result = file_manager.confirm_overwrite_if_needed(existing)

    assert result is True
    mock_ask.assert_called_once()


def test_confirm_overwrite_asks_and_returns_false_on_decline(tmp_path):
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"x")

    with patch("tkinter.messagebox.askyesno", return_value=False):
        result = file_manager.confirm_overwrite_if_needed(existing)

    assert result is False


def test_confirm_overwrite_message_names_the_file(tmp_path):
    existing = tmp_path / "my_report.pdf"
    existing.write_bytes(b"x")

    with patch("tkinter.messagebox.askyesno", return_value=True) as mock_ask:
        file_manager.confirm_overwrite_if_needed(existing)

    _, kwargs = mock_ask.call_args
    assert "my_report.pdf" in kwargs["message"]


# ---------------------------------------------------------------------------
# F. Real-world Windows-relevant edge cases not already covered elsewhere
# ---------------------------------------------------------------------------

def test_output_path_with_spaces_and_parens(tmp_path):
    src = tmp_path / "a.pdf"
    _make_pdf(src, pages=1)
    output = tmp_path / "My Merged File (Final Version).pdf"

    pdf_engine.merge_pdfs([src], output)

    assert output.exists()


def test_generate_compressed_output_path_with_long_source_name(tmp_path):
    long_stem = "b" * 150
    result = file_manager.generate_compressed_output_path(
        Path(f"{long_stem}.pdf"), tmp_path
    )
    assert result.name.startswith(long_stem)
    assert result.name.endswith("_compressed.pdf")
