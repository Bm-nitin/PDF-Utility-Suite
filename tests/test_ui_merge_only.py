"""
test_ui_merge_only.py

Phase 6 tests for the MERGE ONLY action: button enable/disable rules
across the 0/1/2+ file-count states, real merge processing wired through
pdf_engine.merge_pdfs() (never merge_and_compress()), displayed-order
preservation, Save As cancel handling, and error handling.

These tests drive real tkinter widgets, so they need a display (Xvfb on
Linux, a real display on Windows). They share the module-scoped single-
root fixture pattern established in test_ui_file_list_mutations.py --
see that file's fixture docstring for why a fresh Tk() root is NOT
created per test.

The native file dialogs (file_manager.select_pdf_files and
file_manager.save_pdf_file) are mocked so these tests don't depend on
interactive OS UI. Everything downstream of that -- validation, merging,
button states, error handling -- is real production code.
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
    """A handful of small, distinguishable PDFs."""

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
    make(tmp_path / "c.pdf", 5, text="PAGE_C")

    return {
        "a": tmp_path / "a.pdf",
        "b": tmp_path / "b.pdf",
        "c": tmp_path / "c.pdf",
    }


def _import(win, paths):
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _merge(win, output_path):
    with patch("file_manager.save_pdf_file", return_value=output_path):
        win._on_merge_only_clicked()
        deadline = time.time() + 10
        while win._merge_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


# ---------------------------------------------------------------------------
# Button enable/disable rules (0 / 1 / 2+ files)
# ---------------------------------------------------------------------------

def test_zero_files_all_three_buttons_disabled(window):
    assert window.state.total_files == 0
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "disabled"
    assert str(window.merge_compress_btn["state"]) == "disabled"


def test_one_file_only_compress_only_enabled(window, pdf_files):
    _import(window, [pdf_files["a"]])
    assert window.state.total_files == 1

    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "disabled"


def test_two_or_more_files_all_three_enabled(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    assert window.state.total_files == 2

    assert str(window.merge_only_btn["state"]) == "normal"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "normal"


def test_button_states_transition_correctly_as_files_change(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"], pdf_files["c"]])
    assert str(window.merge_only_btn["state"]) == "normal"

    window._on_remove_file(window.state.files[0])
    window.root.update()
    assert window.state.total_files == 2
    assert str(window.merge_only_btn["state"]) == "normal"

    window._on_remove_file(window.state.files[0])
    window.root.update()
    assert window.state.total_files == 1
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "normal"

    window._on_remove_file(window.state.files[0])
    window.root.update()
    assert window.state.total_files == 0
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "disabled"


# ---------------------------------------------------------------------------
# Merge Only: real processing
# ---------------------------------------------------------------------------

def test_merge_only_produces_correct_page_count(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"], pdf_files["c"]])
    output = tmp_path / "out.pdf"

    _merge(window, output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 10  # 3 + 2 + 5


def test_merge_only_calls_merge_pdfs_not_merge_and_compress(window, pdf_files, tmp_path):
    """Direct regression test for the Phase 6 correction: Merge Only must
    call pdf_engine.merge_pdfs(), never pdf_engine.merge_and_compress().
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_pdfs", wraps=pdf_engine.merge_pdfs) as spy_merge, \
         patch.object(pdf_engine, "merge_and_compress") as spy_merge_and_compress:
        _merge(window, output)

    spy_merge.assert_called_once()
    spy_merge_and_compress.assert_not_called()


def test_merge_only_output_is_not_compressed(window, pdf_files, tmp_path):
    """Proves no compression occurred anywhere in the UI's Merge Only
    path. A byte-for-byte comparison against a direct merge_pdfs() call
    would be too strict here -- PyMuPDF embeds a document /ID that can
    legitimately differ between two separate save() calls even with
    identical page content, which is metadata non-determinism, not
    compression. The precise, deterministic way to prove "no compression
    happened" is to prove pdf_engine.compress_pdf() was never invoked.
    """
    inputs = [pdf_files["a"], pdf_files["b"], pdf_files["c"]]
    _import(window, inputs)

    with patch.object(pdf_engine, "compress_pdf") as spy_compress:
        _merge(window, tmp_path / "ui_output.pdf")

    spy_compress.assert_not_called()

    # Cross-check with a direct call for a same-size sanity bound: PyMuPDF's
    # structural output for identical input content should be within a few
    # bytes of a fresh merge_pdfs() call (differing only by incidental
    # metadata like the /ID), not meaningfully smaller as compression
    # would produce.
    ground_truth = tmp_path / "ground_truth.pdf"
    pdf_engine.merge_pdfs(inputs, ground_truth)
    ui_size = (tmp_path / "ui_output.pdf").stat().st_size
    gt_size = ground_truth.stat().st_size
    assert abs(ui_size - gt_size) < 50, (
        f"UI output size ({ui_size}) and direct merge_pdfs() size "
        f"({gt_size}) differ by more than incidental metadata would "
        f"account for -- possible compression leaked in."
    )


def test_merge_only_preserves_displayed_order_after_reorder(window, tmp_path):
    """Merges in the exact displayed (possibly reordered) order, not the
    original import order. Uses dedicated single-page fixtures (rather
    than the module's multi-page pdf_files) so checking a specific output
    page index unambiguously identifies which source file it came from.
    """
    x_path = tmp_path / "x.pdf"
    y_path = tmp_path / "y.pdf"
    for path, text in [(x_path, "PAGE_X"), (y_path, "PAGE_Y")]:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), text)
        doc.save(path)
        doc.close()

    _import(window, [x_path, y_path])  # imported as x, y
    x_file = window.state.files[0]
    window._on_move_file_down(x_file)  # now displayed as y, x
    window.root.update()
    assert [f.name for f in window.state.files] == ["y.pdf", "x.pdf"]

    output = tmp_path / "order_check.pdf"
    _merge(window, output)

    with pymupdf.open(output) as doc:
        assert doc.page_count == 2
        page0_text = doc[0].get_text()
        page1_text = doc[1].get_text()

    assert "PAGE_Y" in page0_text
    assert "PAGE_X" in page1_text


def test_merge_only_does_not_modify_source_files(window, pdf_files, tmp_path):
    inputs = [pdf_files["a"], pdf_files["b"], pdf_files["c"]]
    original_bytes = {name: path.read_bytes() for name, path in pdf_files.items()}

    _import(window, inputs)
    _merge(window, tmp_path / "out.pdf")

    for name, path in pdf_files.items():
        assert path.read_bytes() == original_bytes[name]


def test_merge_only_reenables_buttons_after_success(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    _merge(window, tmp_path / "out.pdf")

    assert str(window.merge_only_btn["state"]) == "normal"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "normal"
    assert str(window.select_files_btn["state"]) == "normal"
    assert str(window.add_more_btn["state"]) == "normal"
    assert str(window.clear_all_btn["state"]) == "normal"


def test_merge_only_status_message_on_success(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "final.pdf"

    _merge(window, output)

    assert "Merged" in window.status_var.get()
    assert "final.pdf" in window.status_var.get()


# ---------------------------------------------------------------------------
# Cancel / error handling
# ---------------------------------------------------------------------------

def test_cancelling_save_as_is_a_safe_noop(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"]])

    with patch("file_manager.save_pdf_file", return_value=None):
        window._on_merge_only_clicked()
        window.root.update()

    assert window.status_var.get() == "Status: Ready"
    assert window.state.total_files == 2  # list unchanged
    assert str(window.merge_only_btn["state"]) == "normal"  # not left disabled


def test_merge_failure_shows_error_and_recovers(window, pdf_files, tmp_path):
    """Simulates a source file vanishing after import but before merge --
    pdf_engine.merge_pdfs() re-validates each file and will raise.
    """
    doomed = pdf_files["a"]
    _import(window, [doomed, pdf_files["b"]])
    doomed.unlink()  # remove the file out from under the app

    output = tmp_path / "should_not_exist.pdf"
    with patch("file_manager.save_pdf_file", return_value=output):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_merge_only_clicked()
            deadline = time.time() + 10
            while window._merge_in_progress and time.time() < deadline:
                window.root.update()
                time.sleep(0.02)
            window.root.update()

    assert "failed" in window.status_var.get().lower()
    assert mock_error.called
    assert not output.exists()
    # Controls must recover, not remain stuck disabled after a failure.
    assert str(window.merge_only_btn["state"]) == "normal"
    assert str(window.select_files_btn["state"]) == "normal"


def test_merge_only_guarded_against_single_file(window, pdf_files):
    """Defensive guard: even if called directly (bypassing the disabled
    button), Merge Only must refuse to run on fewer than 2 files.
    """
    _import(window, [pdf_files["a"]])
    assert window.state.total_files == 1

    with patch("file_manager.save_pdf_file") as mock_save:
        window._on_merge_only_clicked()
        window.root.update()

    mock_save.assert_not_called()  # should never even reach the dialog
