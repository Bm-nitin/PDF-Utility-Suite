"""
test_ui_compress_and_merge_compress.py

Phase 8 tests for the two remaining actions: COMPRESS ONLY (single-file
Save As path and multi-file batch/folder path) and MERGE + COMPRESS.

Covers, per the Phase 8 requirements:
  - Compress Only calls pdf_engine.compress_pdf() directly, never
    merge_pdfs() or merge_and_compress() -- files are never merged.
  - Single file -> Save As dialog with a sensible default filename.
  - Multiple files -> one folder picker, then N separate output files,
    each independently compressed, never combined into one.
  - Collision-safe automatic naming for two different source files that
    share a filename.
  - One bad file in a batch does not abort the rest.
  - Merge + Compress calls pdf_engine.merge_and_compress() directly,
    producing exactly one output file that is both merged and
    compressed.
  - Merge Only's behavior is completely unaffected by any of this.
  - Source files are never modified by any of the three operations.
  - Cancelling any dialog is a safe no-op.

These tests drive real tkinter widgets, so they need a display (Xvfb on
Linux, a real display on Windows), following the same module-scoped
single-root fixture pattern as test_ui_file_list_mutations.py and
test_ui_merge_only.py.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk

import pdf_engine
from ui import MainWindow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pdf_files(tmp_path):
    (tmp_path / "dirA").mkdir()

    def make(path, pages, text=None):
        doc = pymupdf.open()
        for _ in range(pages):
            page = doc.new_page()
            if text:
                page.insert_text((50, 50), text)
        doc.save(path)
        doc.close()

    make(tmp_path / "a.pdf", 3, text="PAGE_A")
    make(tmp_path / "b.pdf", 2, text="PAGE_B")
    make(tmp_path / "report.pdf", 4, text="ROOT_REPORT")
    make(tmp_path / "dirA" / "report.pdf", 5, text="DIRA_REPORT")

    return {
        "a": tmp_path / "a.pdf",
        "b": tmp_path / "b.pdf",
        "report_root": tmp_path / "report.pdf",
        "report_dirA": tmp_path / "dirA" / "report.pdf",
    }


def _import(win, paths):
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _compress_only(win, dialog_return, is_folder):
    """Drives Compress Only, mocking whichever native dialog applies."""
    patch_target = (
        "file_manager.select_output_folder" if is_folder else "file_manager.save_pdf_file"
    )
    with patch(patch_target, return_value=dialog_return):
        win._on_compress_only_clicked()
        deadline = time.time() + 15
        while win._compress_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _merge_compress(win, output_path):
    with patch("file_manager.save_pdf_file", return_value=output_path):
        win._on_merge_compress_clicked()
        deadline = time.time() + 15
        while win._mergecompress_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


# ---------------------------------------------------------------------------
# Compress Only: single file
# ---------------------------------------------------------------------------

def test_compress_only_single_file_uses_save_as(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"]])
    output = tmp_path / "out.pdf"

    with patch("file_manager.save_pdf_file", return_value=output) as mock_save:
        window._on_compress_only_clicked()
        deadline = time.time() + 15
        while window._compress_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    mock_save.assert_called_once()
    _, kwargs = mock_save.call_args
    assert kwargs["default_name"] == "a_compressed.pdf"
    assert output.exists()


def test_compress_only_single_file_does_not_merge(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"]])  # 3 pages
    output = tmp_path / "out.pdf"

    _compress_only(window, output, is_folder=False)

    with pymupdf.open(output) as doc:
        assert doc.page_count == 3  # unchanged page count -- no merging


def test_compress_only_calls_compress_pdf_not_other_functions(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "compress_pdf", wraps=pdf_engine.compress_pdf) as spy_compress, \
         patch.object(pdf_engine, "merge_pdfs") as spy_merge, \
         patch.object(pdf_engine, "merge_and_compress") as spy_mac:
        _compress_only(window, output, is_folder=False)

    spy_compress.assert_called_once()
    spy_merge.assert_not_called()
    spy_mac.assert_not_called()


def test_compress_only_single_cancel_is_safe_noop(window, pdf_files):
    _import(window, [pdf_files["a"]])

    _compress_only(window, None, is_folder=False)

    assert window.status_var.get() == "Status: Ready"
    assert window.state.total_files == 1
    assert str(window.compress_only_btn["state"]) == "normal"


def test_compress_only_single_does_not_modify_source(window, pdf_files, tmp_path):
    original = pdf_files["a"].read_bytes()
    _import(window, [pdf_files["a"]])

    _compress_only(window, tmp_path / "out.pdf", is_folder=False)

    assert pdf_files["a"].read_bytes() == original


# ---------------------------------------------------------------------------
# Compress Only: multiple files (batch / folder)
# ---------------------------------------------------------------------------

def test_compress_only_batch_uses_folder_picker(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("file_manager.select_output_folder", return_value=output_dir) as mock_folder:
        window._on_compress_only_clicked()
        deadline = time.time() + 15
        while window._compress_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    mock_folder.assert_called_once()


def test_compress_only_batch_produces_n_separate_files_never_merged(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])  # 3 pages, 2 pages
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    _compress_only(window, output_dir, is_folder=True)

    outputs = sorted(output_dir.glob("*.pdf"))
    assert len(outputs) == 2, "must produce exactly 2 separate files, not 1 merged file"

    page_counts = sorted(pymupdf.open(p).page_count for p in outputs)
    assert page_counts == [2, 3], "each output must retain its own original page count"


def test_compress_only_batch_never_calls_merge_functions(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch.object(pdf_engine, "merge_pdfs") as spy_merge, \
         patch.object(pdf_engine, "merge_and_compress") as spy_mac:
        _compress_only(window, output_dir, is_folder=True)

    spy_merge.assert_not_called()
    spy_mac.assert_not_called()


def test_compress_only_batch_collision_safe_naming_for_same_filename(window, pdf_files, tmp_path):
    """The exact scenario the Phase 7 architecture was built for: two
    different source files sharing a filename must both succeed with
    distinct, correctly-attributed output files.
    """
    _import(window, [pdf_files["report_root"], pdf_files["report_dirA"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    _compress_only(window, output_dir, is_folder=True)

    outputs = sorted(output_dir.glob("*.pdf"))
    names = {p.name for p in outputs}
    assert names == {"report_compressed.pdf", "report_compressed (1).pdf"}

    texts = {p.name: pymupdf.open(p)[0].get_text() for p in outputs}
    assert "ROOT_REPORT" in texts["report_compressed.pdf"]
    assert "DIRA_REPORT" in texts["report_compressed (1).pdf"]


def test_compress_only_batch_one_bad_file_does_not_abort_the_rest(window, pdf_files, tmp_path):
    doomed = pdf_files["a"]
    _import(window, [doomed, pdf_files["b"]])
    doomed.unlink()  # simulate the file vanishing after import

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("tkinter.messagebox.showwarning") as mock_warn:
        _compress_only(window, output_dir, is_folder=True)

    assert mock_warn.called
    outputs = list(output_dir.glob("*.pdf"))
    assert len(outputs) == 1
    assert outputs[0].name == "b_compressed.pdf"
    assert "Compressed 1 file" in window.status_var.get()
    assert "could not be compressed" in window.status_var.get()


def test_compress_only_batch_shows_progress_messages(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    seen_messages = []
    original_set = window.status_var.set

    def tracking_set(value):
        seen_messages.append(value)
        original_set(value)

    window.status_var.set = tracking_set
    try:
        _compress_only(window, output_dir, is_folder=True)
    finally:
        window.status_var.set = original_set

    assert any("file 1 of 2" in m for m in seen_messages)
    assert any("file 2 of 2" in m for m in seen_messages)


def test_compress_only_batch_cancel_is_safe_noop(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"]])

    _compress_only(window, None, is_folder=True)

    assert window.status_var.get() == "Status: Ready"
    assert window.state.total_files == 2


def test_compress_only_batch_does_not_modify_sources(window, pdf_files, tmp_path):
    originals = {
        name: path.read_bytes()
        for name, path in [("a", pdf_files["a"]), ("b", pdf_files["b"])]
    }
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    _compress_only(window, output_dir, is_folder=True)

    assert pdf_files["a"].read_bytes() == originals["a"]
    assert pdf_files["b"].read_bytes() == originals["b"]


# ---------------------------------------------------------------------------
# Button-state guard: Compress Only routes correctly based on file count
# ---------------------------------------------------------------------------

def test_one_file_routes_to_save_as_not_folder_picker(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"]])

    with patch("file_manager.save_pdf_file", return_value=tmp_path / "out.pdf") as mock_save, \
         patch("file_manager.select_output_folder") as mock_folder:
        window._on_compress_only_clicked()
        deadline = time.time() + 15
        while window._compress_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    mock_save.assert_called_once()
    mock_folder.assert_not_called()


def test_two_files_routes_to_folder_picker_not_save_as(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("file_manager.select_output_folder", return_value=output_dir) as mock_folder, \
         patch("file_manager.save_pdf_file") as mock_save:
        window._on_compress_only_clicked()
        deadline = time.time() + 15
        while window._compress_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    mock_folder.assert_called_once()
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Merge + Compress
# ---------------------------------------------------------------------------

def test_merge_compress_produces_one_merged_and_compressed_file(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])  # 3 + 2 pages
    output = tmp_path / "out.pdf"

    _merge_compress(window, output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5


def test_merge_compress_calls_merge_and_compress_directly(window, pdf_files, tmp_path):
    """Confirms the UI layer calls pdf_engine.merge_and_compress() to
    perform Merge + Compress.

    Note: merge_and_compress() legitimately calls merge_pdfs() and
    compress_pdf() internally as part of its own real implementation
    (merge to a temp file, then compress that temp file) -- that is
    correct and expected, not a violation of the Phase 6 requirement.
    That requirement is about ui.py never manually chaining merge_pdfs()
    + compress_pdf() itself as a substitute for calling
    merge_and_compress() -- which is what spy_mac.assert_called_once()
    below actually proves. wraps= is used for all three functions (not
    bare Mocks) so the real merge+compress logic still runs and produces
    correct output; replacing merge_pdfs/compress_pdf with inert no-ops
    would break merge_and_compress's actual implementation and cause a
    real failure dialog to appear -- which is exactly what happened
    during development of this test before this fix.
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_and_compress", wraps=pdf_engine.merge_and_compress) as spy_mac, \
         patch.object(pdf_engine, "merge_pdfs", wraps=pdf_engine.merge_pdfs) as spy_merge, \
         patch.object(pdf_engine, "compress_pdf", wraps=pdf_engine.compress_pdf) as spy_compress:
        _merge_compress(window, output)

    spy_mac.assert_called_once()
    # merge_pdfs/compress_pdf ARE called -- by merge_and_compress's own
    # internal implementation, which is correct. What matters is that
    # merge_and_compress is the single thing the UI calls.
    assert spy_merge.called
    assert spy_compress.called
    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5


def test_merge_compress_default_filename(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])

    with patch("file_manager.save_pdf_file", return_value=tmp_path / "out.pdf") as mock_save:
        window._on_merge_compress_clicked()
        deadline = time.time() + 15
        while window._mergecompress_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    _, kwargs = mock_save.call_args
    assert kwargs["default_name"] == "merged_compressed.pdf"


def test_merge_compress_preserves_displayed_order(window, tmp_path):
    x_path = tmp_path / "x.pdf"
    y_path = tmp_path / "y.pdf"
    for path, text in [(x_path, "PAGE_X"), (y_path, "PAGE_Y")]:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), text)
        doc.save(path)
        doc.close()

    _import(window, [x_path, y_path])
    window._on_move_file_down(window.state.files[0])  # now y, x
    window.root.update()

    output = tmp_path / "order_check.pdf"
    _merge_compress(window, output)

    with pymupdf.open(output) as doc:
        assert "PAGE_Y" in doc[0].get_text()
        assert "PAGE_X" in doc[1].get_text()


def test_merge_compress_guarded_against_single_file(window, pdf_files):
    _import(window, [pdf_files["a"]])

    with patch("file_manager.save_pdf_file") as mock_save:
        window._on_merge_compress_clicked()
        window.root.update()

    mock_save.assert_not_called()


def test_merge_compress_cancel_is_safe_noop(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"]])

    _merge_compress(window, None)

    assert window.status_var.get() == "Status: Ready"
    assert window.state.total_files == 2


def test_merge_compress_does_not_modify_sources(window, pdf_files, tmp_path):
    original_a = pdf_files["a"].read_bytes()
    original_b = pdf_files["b"].read_bytes()
    _import(window, [pdf_files["a"], pdf_files["b"]])

    _merge_compress(window, tmp_path / "out.pdf")

    assert pdf_files["a"].read_bytes() == original_a
    assert pdf_files["b"].read_bytes() == original_b


def test_merge_compress_failure_shows_error_and_recovers(window, pdf_files, tmp_path):
    doomed = pdf_files["a"]
    _import(window, [doomed, pdf_files["b"]])
    doomed.unlink()

    with patch("file_manager.save_pdf_file", return_value=tmp_path / "should_not_exist.pdf"):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_merge_compress_clicked()
            deadline = time.time() + 15
            while window._mergecompress_in_progress and time.time() < deadline:
                window.root.update()
                time.sleep(0.02)
            window.root.update()

    assert mock_error.called
    assert "failed" in window.status_var.get().lower()
    assert not (tmp_path / "should_not_exist.pdf").exists()
    assert str(window.merge_compress_btn["state"]) == "normal"


# ---------------------------------------------------------------------------
# Cross-cutting: Merge Only must be completely unaffected by Phase 8
# ---------------------------------------------------------------------------

def test_merge_only_still_never_compresses(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "compress_pdf") as spy_compress, \
         patch.object(pdf_engine, "merge_and_compress") as spy_mac, \
         patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        deadline = time.time() + 15
        while window._merge_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    spy_compress.assert_not_called()
    spy_mac.assert_not_called()


def test_all_three_buttons_disabled_during_any_single_operation(window, pdf_files, tmp_path):
    """While Merge Only is running, Compress Only and Merge+Compress must
    also be disabled -- operations are mutually exclusive.
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        # Immediately after starting, before the background thread
        # finishes, all three action buttons should be disabled.
        assert str(window.merge_only_btn["state"]) == "disabled"
        assert str(window.compress_only_btn["state"]) == "disabled"
        assert str(window.merge_compress_btn["state"]) == "disabled"

        deadline = time.time() + 15
        while window._merge_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    # After completion, states should be correctly restored.
    assert str(window.merge_only_btn["state"]) == "normal"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "normal"
