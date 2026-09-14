"""
test_ui_remove_pages.py

UI-level tests for the Remove Pages workspace (Phase 14): navigation,
single-file import, live page-selection feedback/validation, the
REMOVE PAGES action end-to-end through the real UI, background-thread/
responsiveness/concurrency behavior, source safety, and the specific
regression risk of switching away from Remove Pages and back to
Merge/Compress or Split PDF (and vice versa).

Uses deterministic threading.Event-based gating (the _GatedCall pattern
established in test_phase9_responsive_processing.py and reused in
test_ui_split.py) for any test that needs to catch an operation
genuinely mid-flight -- never sleep()-based timing assumptions.

Uses the shared session-scoped `window` fixture from tests/conftest.py.
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine
import remove_pages_engine


def _make_pdf(path, pages=1, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


@pytest.fixture
def remove_pages_source_pdf(tmp_path):
    path = tmp_path / "document.pdf"
    _make_pdf(path, pages=10, text_prefix="PAGE_")
    return path


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_remove_pages_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_remove_pages_select_file_clicked()
        assert _pump_until(win, lambda: not win._remove_pages_import_in_progress)


def _reset_remove_pages_state(win):
    win.remove_pages_source = None
    win._update_remove_pages_source_label()
    win.remove_pages_selection_var.set("")
    win.remove_pages_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_remove_pages_controls_state()
    win.root.update()


class _GatedCall:
    """Deterministic gate for pausing a background worker mid-call --
    see test_phase9_responsive_processing.py for the original
    introduction of this pattern. Duplicated here (not imported across
    test modules) to keep each test file self-contained, matching this
    project's existing convention.
    """

    def __init__(self, real_func):
        self._real_func = real_func
        self._gate = threading.Event()
        self.entered = threading.Event()

    def release(self):
        self._gate.set()

    def __call__(self, *args, **kwargs):
        self.entered.set()
        self._gate.wait(timeout=10)
        return self._real_func(*args, **kwargs)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_remove_pages_appears_in_navigation(window):
    assert "remove_pages" in window.tool_nav_buttons
    assert window.tool_nav_buttons["remove_pages"].winfo_exists()

    window._select_tool("remove_pages")
    window.root.update()

    assert window.current_tool_id == "remove_pages"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_remove_pages_opens_correct_workspace(window):
    window._select_tool("remove_pages")
    window.root.update()

    assert window.remove_pages_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.split_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


def test_remove_pages_button_disabled_with_no_source(window):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)

    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source file import
# ---------------------------------------------------------------------------

def test_single_pdf_can_be_imported(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)

    _select_remove_pages_file(window, remove_pages_source_pdf)

    assert window.remove_pages_source is not None
    assert window.remove_pages_source.name == "document.pdf"
    assert window.remove_pages_source.page_count == 10
    assert "document.pdf" in window.remove_pages_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


def test_multiple_pdf_selection_is_handled_via_single_file_picker(window):
    """Remove Pages only ever offers a SINGLE-file native picker
    (file_manager.select_single_pdf_file, the same dedicated
    single-select dialog Split PDF uses) -- there is no dialog state in
    which more than one file could be returned, so "multiple selection"
    is handled cleanly by construction, not by post-hoc filtering.
    """
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)

    # select_pdf_files (the multi-select dialog) must never be used by
    # Remove Pages -- patch both in the same block so the real (blocking,
    # GUI-only) dialog is never reached regardless of which one the code
    # actually calls.
    with patch("file_manager.select_single_pdf_file", return_value=None) as mock_picker, \
         patch("file_manager.select_pdf_files") as mock_multi:
        window._on_remove_pages_select_file_clicked()
        window.root.update()
        mock_picker.assert_called_once()
        mock_multi.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_file_picker_does_not_start_a_worker(window):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None):
        window._on_remove_pages_select_file_clicked()
        window.root.update()

    assert not window._remove_pages_import_in_progress
    assert window.remove_pages_status_var.get() == "Status: Ready"
    assert window.remove_pages_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_corrupt_file_selection_shows_error(window, tmp_path):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_remove_pages_select_file_clicked()
            assert _pump_until(window, lambda: not window._remove_pages_import_in_progress)

    assert mock_error.called
    assert window.remove_pages_source is None
    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_page_count_appears_after_import(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)

    _select_remove_pages_file(window, remove_pages_source_pdf)

    assert "10" in window.remove_pages_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Page-selection feedback / validation (no engine run required)
# ---------------------------------------------------------------------------

def test_valid_page_selection_is_accepted(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("1,3,5-7")
    window.root.update()

    assert window.remove_pages_error_var.get() == ""
    feedback = window.remove_pages_feedback_var.get()
    assert "1,3,5-7" in feedback
    assert "5" in feedback  # pages selected
    assert str(window.remove_pages_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_invalid_selection_shows_validation_error(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("0")
    window.root.update()

    assert window.remove_pages_error_var.get() != ""
    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_bad_input_does_not_crash_the_ui(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    for bad_text in ["abc", "1-3-5", "7-3", "1;3;5", "-1", "", "   ", "999"]:
        window.remove_pages_selection_var.set(bad_text)
        window.root.update()  # must not raise
        assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_remaining_page_count_is_calculated_correctly(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("1,3,5-7")
    window.root.update()

    assert "Pages remaining: 5" in window.remove_pages_feedback_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_remove_button_state_tracks_selection_validity(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    assert str(window.remove_pages_button["state"]) == "disabled"  # empty selection

    window.remove_pages_selection_var.set("2")
    window.root.update()
    assert str(window.remove_pages_button["state"]) == "normal"

    window.remove_pages_selection_var.set("2,")  # becomes malformed
    window.root.update()
    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_remove_all_pages_selection_is_rejected(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("1-10")
    window.root.update()

    assert "every page" in window.remove_pages_error_var.get().lower()
    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_clear_selection_resets_text_but_keeps_source(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("1,2,3")
    window.root.update()
    assert window.remove_pages_selection_var.get() == "1,2,3"

    window._on_remove_pages_clear_selection_clicked()
    window.root.update()

    assert window.remove_pages_selection_var.get() == ""
    assert window.remove_pages_source is not None  # source is untouched
    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# The REMOVE PAGES operation, end-to-end through the real UI
# ---------------------------------------------------------------------------

def test_remove_pages_end_to_end(window, remove_pages_source_pdf, tmp_path):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("1,3,5-7")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_remove_pages_execute_clicked()
        assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 5
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE_2", "PAGE_4", "PAGE_8", "PAGE_9", "PAGE_10"]

    assert "successfully" in window.remove_pages_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_save_dialog_does_not_start_worker(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window.remove_pages_selection_var.set("1,2")
    window.root.update()

    with patch("file_manager.save_pdf_file", return_value=None):
        window._on_remove_pages_execute_clicked()
        window.root.update()

    assert not window.remove_pages_in_progress
    assert window.remove_pages_status_var.get() == "Status: Ready"

    window._select_tool("merge_compress")
    window.root.update()


def test_operation_starts_in_background(window, remove_pages_source_pdf, tmp_path):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("1")
    window.root.update()

    seen_on_main = []
    original = remove_pages_engine.remove_pages_from_pdf

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    output_path = tmp_path / "output.pdf"
    with patch.object(remove_pages_engine, "remove_pages_from_pdf", side_effect=wrapper):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_remove_pages_execute_clicked()
            assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert seen_on_main == [False]

    window._select_tool("merge_compress")
    window.root.update()


def test_controls_disabled_during_operation_and_restored_after(
    window, remove_pages_source_pdf, tmp_path
):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("1")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(remove_pages_engine.remove_pages_from_pdf)
    with patch.object(remove_pages_engine, "remove_pages_from_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_remove_pages_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert str(window.remove_pages_select_btn["state"]) == "disabled"
            assert str(window.remove_pages_clear_btn["state"]) == "disabled"
            assert str(window.remove_pages_selection_entry["state"]) == "disabled"
            assert str(window.remove_pages_button["state"]) == "disabled"
            assert str(window.merge_only_btn["state"]) == "disabled"

            gate.release()
            assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert str(window.remove_pages_select_btn["state"]) == "normal"
    # A completed removal clears its source, so REMOVE PAGES correctly
    # goes back to "disabled" rather than staying "normal" with a stale
    # source (mirrors Split PDF's equivalent behavior).
    assert window.remove_pages_source is None
    assert str(window.remove_pages_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_successful_operation_updates_status(window, remove_pages_source_pdf, tmp_path):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("2")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_remove_pages_execute_clicked()
        assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert "successfully" in window.remove_pages_status_var.get().lower()
    assert output_path.name in window.remove_pages_status_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_failed_operation_restores_ui_state(window, remove_pages_source_pdf, tmp_path):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("2")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch.object(
        remove_pages_engine, "remove_pages_from_pdf",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_remove_pages_execute_clicked()
                assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert mock_error.called
    assert "failed" in window.remove_pages_status_var.get().lower()
    # The source is NOT cleared on failure -- unlike a success, the user
    # should be able to just fix the selection and try again with the
    # same file rather than re-importing it.
    assert window.remove_pages_source is not None
    assert str(window.remove_pages_select_btn["state"]) == "normal"
    assert not window.remove_pages_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_unexpected_worker_exception_recovers_cleanly(
    window, remove_pages_source_pdf, tmp_path
):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("2")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch.object(
        remove_pages_engine, "remove_pages_from_pdf",
        side_effect=RuntimeError("totally unexpected"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_remove_pages_execute_clicked()
                assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert mock_error.called
    assert not window.remove_pages_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_no_concurrent_remove_pages_operations(window, remove_pages_source_pdf, tmp_path):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("1")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(remove_pages_engine.remove_pages_from_pdf)
    with patch.object(remove_pages_engine, "remove_pages_from_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path) as mock_save:
            window._on_remove_pages_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            calls_before = mock_save.call_count
            window._on_remove_pages_execute_clicked()  # attempt a second run
            window.root.update()

            assert mock_save.call_count == calls_before, (
                "a second Remove Pages run must not open a second Save As dialog"
            )

            gate.release()
            assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_gui_remains_responsive_during_remove_pages(
    window, remove_pages_source_pdf, tmp_path
):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("1")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(remove_pages_engine.remove_pages_from_pdf)
    with patch.object(remove_pages_engine, "remove_pages_from_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_remove_pages_execute_clicked()
            assert gate.entered.wait(timeout=5)

            update_count = 0
            for _ in range(20):
                window.root.update()
                update_count += 1

            assert window.remove_pages_in_progress
            assert update_count == 20

            gate.release()
            assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_source_pdf_is_untouched_after_successful_removal(
    window, remove_pages_source_pdf, tmp_path
):
    original_bytes = remove_pages_source_pdf.read_bytes()

    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("1,3,5-7")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_remove_pages_execute_clicked()
        assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert remove_pages_source_pdf.read_bytes() == original_bytes

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Switching away safely / cross-tool regression
# ---------------------------------------------------------------------------

def test_switching_away_from_remove_pages_behaves_safely(window, remove_pages_source_pdf):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)
    window.remove_pages_selection_var.set("1,2")
    window.root.update()

    window._select_tool("split")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("remove_pages")
    window.root.update()

    # State survives switching away and back, exactly like Split's does.
    assert window.remove_pages_source is not None
    assert window.remove_pages_source.name == "document.pdf"
    assert window.remove_pages_selection_var.get() == "1,2"
    assert str(window.remove_pages_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_merge_compress_workspace_remains_functional(
    window, remove_pages_source_pdf, tmp_path
):
    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)

    with patch("file_manager.select_pdf_files", return_value=[a, b]):
        window._on_select_files_clicked()
        assert _pump_until(window, lambda: not window._import_in_progress)

    assert window.state.total_files == 2

    output = tmp_path / "merged.pdf"
    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5

    window._on_clear_all_clicked()
    window.root.update()


def test_existing_split_workspace_remains_functional(
    window, remove_pages_source_pdf, tmp_path
):
    import split_engine  # noqa: F401 (imported for parity/clarity with test_ui_split.py)

    window._select_tool("remove_pages")
    window.root.update()
    _reset_remove_pages_state(window)
    _select_remove_pages_file(window, remove_pages_source_pdf)

    window._select_tool("split")
    window.root.update()
    window.split_source = None
    window._update_split_source_label()
    window.split_mode_var.set("individual")
    window.split_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_split_button_state()
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=remove_pages_source_pdf):
        window._on_split_select_file_clicked()
        assert _pump_until(window, lambda: not window._split_import_in_progress)

    assert window.split_source is not None
    assert str(window.split_button["state"]) == "normal"

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    with patch("file_manager.select_output_folder", return_value=output_dir):
        window._on_split_execute_clicked()
        assert _pump_until(window, lambda: not window.split_in_progress)

    outputs = sorted(output_dir.glob("*.pdf"))
    assert len(outputs) == 10
    assert "Split completed successfully" in window.split_status_var.get()

    window._on_clear_all_clicked()
    window._select_tool("merge_compress")
    window.root.update()
