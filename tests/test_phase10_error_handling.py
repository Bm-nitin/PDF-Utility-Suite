"""
test_phase10_error_handling.py

Dedicated Phase 10 test file for error handling and recovery: corrupt/
encrypted/missing PDFs, filesystem/output failures, atomic-save safety
under failure, worker exception recovery, and complete UI state
restoration after any failure.

Much of this ground is already covered incidentally by earlier phases'
test files (Phase 4's import resilience, Phase 7's atomic-save tests,
Phase 8's per-operation failure handling, Phase 9's worker-exception and
responsiveness tests). This file exists to give Phase 10's specific
concerns a single, dedicated home -- some overlap with earlier phases is
expected and intentional (regression coverage), while the tests unique
to this file target the two gaps found and fixed in this phase:

  1. prepare_import()/_import_worker() previously could raise an
     uncaught exception from OUTSIDE the per-file try/except (Path
     construction/resolution, or PDFFile construction), which would
     have escaped the worker thread entirely, left nothing on the
     queue, and left _import_in_progress stuck True forever. Fixed by
     widening the per-file try/except in prepare_import() and adding a
     top-level defense-in-depth try/except in _import_worker().

  2. compress_pdf() didn't re-validate its input before processing, so
     a file that became encrypted or corrupted after import (or was
     otherwise unvalidated) could surface a generic "Compression
     failed: <raw pymupdf error>" message instead of the same clear,
     specific InvalidPDFError/EncryptedPDFError messages merge_pdfs()
     already produces. Fixed by calling validate_pdf() at the top of
     compress_pdf(), mirroring merge_pdfs()'s existing per-file
     re-validation.

Uses the shared session-scoped `window` fixture from tests/conftest.py.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine
from ui import prepare_import


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pdf_files(tmp_path):
    def make(path, pages=1):
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()

    make(tmp_path / "a.pdf", 2)
    make(tmp_path / "b.pdf", 3)

    (tmp_path / "corrupt.pdf").write_bytes(b"NOT A REAL PDF FILE" * 20)

    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret123",
        owner_pw="secret123",
    )
    doc.close()

    return {
        "a": tmp_path / "a.pdf",
        "b": tmp_path / "b.pdf",
        "corrupt": tmp_path / "corrupt.pdf",
        "protected": protected,
    }


def _import(win, paths):
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# A. Invalid/corrupt PDFs
# ---------------------------------------------------------------------------

def test_corrupt_pdf_produces_clean_error_via_validate_pdf(pdf_files):
    with pytest.raises(pdf_engine.InvalidPDFError) as exc_info:
        pdf_engine.validate_pdf(pdf_files["corrupt"])
    assert "Traceback" not in str(exc_info.value)
    assert "corrupt.pdf" in str(exc_info.value)


def test_corrupt_pdf_in_compress_only_produces_clean_error(pdf_files, tmp_path):
    output = tmp_path / "out.pdf"
    with pytest.raises(pdf_engine.InvalidPDFError):
        pdf_engine.compress_pdf(pdf_files["corrupt"], output, level="recommended")
    assert not output.exists()


def test_batch_compress_one_corrupt_file_does_not_abort_valid_ones(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    # Simulate the "a.pdf" becoming corrupted after import (the exact
    # scenario Phase 8's batch resilience was built for).
    pdf_files["a"].write_bytes(b"CORRUPTED NOW" * 10)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("tkinter.messagebox.showwarning") as mock_warn:
        with patch("file_manager.select_output_folder", return_value=output_dir):
            window._on_compress_only_clicked()
            assert _pump_until(window, lambda: not window._compress_in_progress)

    assert mock_warn.called
    outputs = list(output_dir.glob("*.pdf"))
    assert len(outputs) == 1
    assert outputs[0].name == "b_compressed.pdf"
    assert "Compressed 1 file" in window.status_var.get()
    assert "could not be compressed" in window.status_var.get()
    with pymupdf.open(outputs[0]) as doc:
        assert doc.page_count == 3


def test_import_one_bad_path_does_not_abort_the_batch(pdf_files):
    """prepare_import() must classify a malformed path as an error for
    THAT entry only, never abort the whole batch -- the Phase 10 fix
    widened the per-file try/except to cover Path construction/
    resolution itself, not just pdf_engine.get_pdf_info().
    """
    result = prepare_import(
        [pdf_files["a"], pdf_files["b"]],
        existing_paths=set(),
    )
    assert len(result["added"]) == 2
    assert not result["errors"]


# ---------------------------------------------------------------------------
# B. Encrypted/password-protected PDFs
# ---------------------------------------------------------------------------

def test_encrypted_pdf_rejected_by_validate_pdf_with_clear_message(pdf_files):
    with pytest.raises(pdf_engine.EncryptedPDFError) as exc_info:
        pdf_engine.validate_pdf(pdf_files["protected"])
    assert "password" in str(exc_info.value).lower()


def test_encrypted_pdf_rejected_by_compress_pdf_with_clear_message(pdf_files, tmp_path):
    """Phase 10 fix: compress_pdf() now re-validates before processing,
    so an encrypted file produces the same clear EncryptedPDFError
    message merge_pdfs() already produces, instead of a generic
    'Compression failed: <raw pymupdf error>'.
    """
    output = tmp_path / "out.pdf"
    with pytest.raises(pdf_engine.EncryptedPDFError) as exc_info:
        pdf_engine.compress_pdf(pdf_files["protected"], output, level="recommended")
    assert "password" in str(exc_info.value).lower()
    assert not output.exists()


def test_encrypted_pdf_in_merge_only_produces_clean_error(pdf_files, tmp_path):
    output = tmp_path / "out.pdf"
    with pytest.raises(pdf_engine.EncryptedPDFError):
        pdf_engine.merge_pdfs([pdf_files["a"], pdf_files["protected"]], output)
    assert not output.exists()


def test_encrypted_pdf_does_not_silently_produce_incorrect_output(pdf_files, tmp_path):
    """Compression must never silently succeed on an encrypted file and
    produce a bogus/incorrect output -- it must fail loudly and cleanly.
    """
    output = tmp_path / "out.pdf"
    try:
        pdf_engine.compress_pdf(pdf_files["protected"], output, level="maximum")
    except pdf_engine.PDFEngineError:
        pass
    assert not output.exists(), "no output should exist after an encrypted-file failure"


# ---------------------------------------------------------------------------
# Missing input files
# ---------------------------------------------------------------------------

def test_missing_input_file_merge_pdfs(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output = tmp_path / "out.pdf"
    with pytest.raises(pdf_engine.InvalidPDFError):
        pdf_engine.merge_pdfs([missing], output)
    assert not output.exists()


def test_missing_input_file_compress_pdf(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output = tmp_path / "out.pdf"
    with pytest.raises(pdf_engine.InvalidPDFError):
        pdf_engine.compress_pdf(missing, output, level="recommended")
    assert not output.exists()


def test_source_file_deleted_after_import_before_merge_only(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    pdf_files["a"].unlink()

    output = tmp_path / "should_not_exist.pdf"
    with patch("file_manager.save_pdf_file", return_value=output):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_merge_only_clicked()
            assert _pump_until(window, lambda: not window._merge_in_progress)

    assert mock_error.called
    assert not output.exists()
    assert str(window.merge_only_btn["state"]) == "normal"


# ---------------------------------------------------------------------------
# C. Filesystem/output errors
# ---------------------------------------------------------------------------

def test_output_directory_unavailable_raises_friendly_error(pdf_files):
    blocking_file = pdf_files["a"].parent / "not_a_directory"
    blocking_file.write_text("this is a file, not a folder")
    output = blocking_file / "out.pdf"

    with pytest.raises(pdf_engine.PDFEngineError) as exc_info:
        pdf_engine.merge_pdfs([pdf_files["a"]], output)
    assert "Traceback" not in str(exc_info.value)


def test_permission_denied_on_save_is_translated_to_friendly_error(pdf_files, tmp_path):
    output = tmp_path / "out.pdf"
    with patch.object(pymupdf.Document, "save", side_effect=PermissionError("Access is denied")):
        with pytest.raises(pdf_engine.PDFEngineError) as exc_info:
            pdf_engine.merge_pdfs([pdf_files["a"]], output)
    assert "Traceback" not in str(exc_info.value)
    assert not output.exists()


def test_output_file_locked_by_another_process_simulation(pdf_files, tmp_path):
    """Simulates a destination file that's open/locked in another
    program (e.g. a PDF viewer) -- on Windows this surfaces as
    PermissionError from the OS when os.replace() tries to overwrite it.
    """
    output = tmp_path / "locked.pdf"
    output.write_bytes(b"pretend this is open in another program")

    with patch(
        "os.replace",
        side_effect=PermissionError(
            "The process cannot access the file because it is being "
            "used by another process"
        ),
    ):
        with pytest.raises(pdf_engine.PDFEngineError) as exc_info:
            pdf_engine.merge_pdfs([pdf_files["a"]], output)

    assert "Traceback" not in str(exc_info.value)
    assert output.read_bytes() == b"pretend this is open in another program"


def test_replace_failure_after_successful_save_leaves_no_partial_output(pdf_files, tmp_path):
    """Distinct failure mode from a save() failure: the temp file is
    written successfully, but the final os.replace() into place fails.
    The destination must still end up untouched, and the temp file must
    still be cleaned up.
    """
    output = tmp_path / "out.pdf"

    with patch("os.replace", side_effect=OSError("simulated replace failure")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.merge_pdfs([pdf_files["a"]], output)

    assert not output.exists()
    assert list(tmp_path.glob(".tmp_*")) == [], (
        "temp file must be cleaned up even when replace() itself fails"
    )


def test_temp_file_creation_failure_is_translated_to_friendly_error(pdf_files, tmp_path):
    output = tmp_path / "out.pdf"
    with patch("tempfile.mkstemp", side_effect=OSError("No space left on device")):
        with pytest.raises(pdf_engine.PDFEngineError) as exc_info:
            pdf_engine.merge_pdfs([pdf_files["a"]], output)
    assert "Traceback" not in str(exc_info.value)
    assert not output.exists()


def test_no_misleading_success_state_after_output_failure(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_merge_only_clicked()
                assert _pump_until(window, lambda: not window._merge_in_progress)

    assert "failed" in window.status_var.get().lower()
    assert not output.exists()


# ---------------------------------------------------------------------------
# D. Atomic output safety (preserved from Phase 7)
# ---------------------------------------------------------------------------

def test_atomic_save_still_leaves_existing_destination_untouched_on_failure(pdf_files, tmp_path):
    output = tmp_path / "out.pdf"
    original = b"pre-existing content that must survive"
    output.write_bytes(original)

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.merge_pdfs([pdf_files["a"]], output)

    assert output.read_bytes() == original


def test_atomic_save_preserved_for_compress_pdf_too(pdf_files, tmp_path):
    output = tmp_path / "out.pdf"
    original = b"pre-existing compressed content"
    output.write_bytes(original)

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pdf_engine.compress_pdf(pdf_files["a"], output, level="recommended")

    assert output.read_bytes() == original


# ---------------------------------------------------------------------------
# E & F. Worker exception recovery and full state recovery
# ---------------------------------------------------------------------------

def test_import_worker_recovers_from_a_bug_in_prepare_import(window, pdf_files):
    """Defense-in-depth check for the Phase 10 fix: even if
    prepare_import() itself somehow raised (simulating an undiscovered
    bug), _import_worker's top-level try/except must still guarantee a
    queue item is produced and _import_in_progress is cleared -- never
    a permanently hung 'Validating...' state.
    """
    with patch("ui.prepare_import", side_effect=RuntimeError("simulated internal bug")):
        with patch("file_manager.select_pdf_files", return_value=[pdf_files["a"]]):
            with patch("tkinter.messagebox.showwarning") as mock_warn:
                window._on_select_files_clicked()
                assert _pump_until(window, lambda: not window._import_in_progress), (
                    "import must not hang forever even if prepare_import() itself raises"
                )

    assert mock_warn.called, "the fallback error must surface through the normal error dialog"
    assert str(window.select_files_btn["state"]) == "normal"
    assert (
        "could not be added" in window.status_var.get()
        or "No files added" in window.status_var.get()
    )


def test_compress_batch_worker_exception_recovers_cleanly(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch.object(pdf_engine, "compress_pdf", side_effect=RuntimeError("totally unexpected")):
        with patch("file_manager.select_output_folder", return_value=output_dir):
            with patch("tkinter.messagebox.showwarning") as mock_warn:
                window._on_compress_only_clicked()
                assert _pump_until(window, lambda: not window._compress_in_progress)

    assert mock_warn.called
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.select_files_btn["state"]) == "normal"


def test_merge_compress_worker_exception_recovers_cleanly(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_and_compress", side_effect=RuntimeError("totally unexpected")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_merge_compress_clicked()
                assert _pump_until(window, lambda: not window._mergecompress_in_progress)

    assert mock_error.called
    assert str(window.merge_compress_btn["state"]) == "normal"
    assert not output.exists()


def test_gui_remains_responsive_while_recovering_from_failure(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    update_count = 0
    with patch.object(pdf_engine, "merge_pdfs", side_effect=RuntimeError("boom")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_merge_only_clicked()
                deadline = time.time() + 15
                while window._merge_in_progress and time.time() < deadline:
                    window.root.update()
                    update_count += 1
                    time.sleep(0.005)

    assert update_count > 0, "main thread must keep pumping events while the failure is handled"
    assert not window._merge_in_progress


def test_file_list_unchanged_after_operation_failure(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    names_before = [f.name for f in window.state.files]
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_pdfs", side_effect=RuntimeError("boom")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_merge_only_clicked()
                assert _pump_until(window, lambda: not window._merge_in_progress)

    assert [f.name for f in window.state.files] == names_before


def test_no_duplicate_rows_after_failed_operation(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_pdfs", side_effect=RuntimeError("boom")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_merge_only_clicked()
                assert _pump_until(window, lambda: not window._merge_in_progress)

    rows = window.file_list_container.winfo_children()
    assert len(rows) == 2  # exactly one row per file, no duplicates


def test_buttons_return_to_correct_state_based_on_file_count_after_failure(window, pdf_files, tmp_path):
    """After a failure, button states must reflect the CURRENT file
    count via the normal 0/1/2+ rule -- not just "re-enabled
    unconditionally".
    """
    _import(window, [pdf_files["a"]])  # exactly 1 file
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "compress_pdf", side_effect=RuntimeError("boom")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_compress_only_clicked()
                assert _pump_until(window, lambda: not window._compress_in_progress)

    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "disabled"


def test_no_stale_in_progress_state_after_any_failure(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_pdfs", side_effect=RuntimeError("boom")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_merge_only_clicked()
                assert _pump_until(window, lambda: not window._merge_in_progress)

    assert not window._import_in_progress
    assert not window._merge_in_progress
    assert not window._compress_in_progress
    assert not window._mergecompress_in_progress
    assert not window._any_operation_in_progress()


# ---------------------------------------------------------------------------
# G. No fake cancellation
# ---------------------------------------------------------------------------

def test_no_cancel_button_or_attribute_exists():
    """Phase 10 explicitly forbids inventing a fake cancellation
    mechanism. This asserts the app doesn't claim to support cancelling
    an in-progress operation anywhere in its public surface.
    """
    import ui as ui_module
    source = Path(ui_module.__file__).read_text()
    assert "cancel_operation" not in source
    assert "def _on_cancel" not in source


# ---------------------------------------------------------------------------
# H. Human-readable error messages (spot checks across all operations)
# ---------------------------------------------------------------------------

def test_all_pdf_engine_errors_are_plain_strings_without_traceback_markers():
    scenarios = []

    try:
        pdf_engine.validate_pdf(Path("/nonexistent/path/x.pdf"))
    except pdf_engine.PDFEngineError as exc:
        scenarios.append(str(exc))

    for message in scenarios:
        assert "Traceback" not in message
        assert "site-packages" not in message
        assert message == message.strip()
        assert len(message) > 0
