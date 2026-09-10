"""
test_ui_split.py

UI-level tests for the Split PDF workspace: navigation, single-file
import, all three split modes end-to-end through the real UI, invalid
input handling, background-thread/responsiveness/concurrency behavior,
and the specific new regression risk of switching away from Split and
back to Merge/Compress (and vice versa).

Uses deterministic threading.Event-based gating (the _GatedCall pattern
established in test_phase9_responsive_processing.py) for any test that
needs to catch an operation genuinely mid-flight -- never sleep()-based
timing assumptions, per the Phase 13 test-quality requirement.

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
import split_engine


def _make_pdf(path, pages=1, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


@pytest.fixture
def split_source_pdf(tmp_path):
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


def _select_split_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_split_select_file_clicked()
        assert _pump_until(win, lambda: not win._split_import_in_progress)


def _reset_split_state(win):
    win.split_source = None
    win._update_split_source_label()
    win.split_mode_var.set("individual")
    win.split_n_var.set("1")
    win.split_ranges_var.set("")
    win.split_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_split_button_state()
    win.root.update()


class _GatedCall:
    """Deterministic gate for pausing a background worker mid-call --
    see test_phase9_responsive_processing.py for the original
    introduction of this pattern. Duplicated here (not imported across
    test modules) to keep each test file self-contained, matching this
    project's existing convention of each test file owning its own
    small helpers.
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

def test_selecting_split_shows_split_workspace(window):
    window._select_tool("split")
    window.root.update()

    assert window.current_tool_id == "split"
    assert window.split_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


def test_split_button_disabled_with_no_source(window):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)

    assert str(window.split_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source file import
# ---------------------------------------------------------------------------

def test_selecting_a_file_populates_source_and_enables_button(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)

    _select_split_file(window, split_source_pdf)

    assert window.split_source is not None
    assert window.split_source.name == "document.pdf"
    assert window.split_source.page_count == 10
    assert str(window.split_button["state"]) == "normal"
    assert "document.pdf" in window.split_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_file_picker_does_not_start_a_worker(window):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None):
        window._on_split_select_file_clicked()
        window.root.update()

    assert not window._split_import_in_progress
    assert window.split_status_var.get() == "Status: Ready"
    assert window.split_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_corrupt_file_selection_shows_error(window, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_split_select_file_clicked()
            assert _pump_until(window, lambda: not window._split_import_in_progress)

    assert mock_error.called
    assert window.split_source is None
    assert str(window.split_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# All three split modes, end-to-end through the real UI
# ---------------------------------------------------------------------------

def test_individual_pages_mode_end_to_end(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    window.split_mode_var.set("individual")
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


def test_every_n_pages_mode_end_to_end(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    window.split_mode_var.set("every_n")
    window.split_n_var.set("3")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("file_manager.select_output_folder", return_value=output_dir):
        window._on_split_execute_clicked()
        assert _pump_until(window, lambda: not window.split_in_progress)

    outputs = sorted(output_dir.glob("*.pdf"))
    assert len(outputs) == 4  # 10 pages / 3 = 3,3,3,1

    window._select_tool("merge_compress")
    window.root.update()


def test_custom_ranges_mode_end_to_end(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    window.split_mode_var.set("custom")
    window.split_ranges_var.set("1-3,5,7-9")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("file_manager.select_output_folder", return_value=output_dir):
        window._on_split_execute_clicked()
        assert _pump_until(window, lambda: not window.split_in_progress)

    outputs = sorted(p.name for p in output_dir.glob("*.pdf"))
    assert outputs == [
        "document_001_pages_1-3.pdf",
        "document_002_pages_5.pdf",
        "document_003_pages_7-9.pdf",
    ]

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Invalid input handling
# ---------------------------------------------------------------------------

def test_invalid_custom_range_shows_error_and_does_not_start_worker(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    window.split_mode_var.set("custom")
    window.split_ranges_var.set("abc")

    with patch("file_manager.select_output_folder") as mock_folder:
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_split_execute_clicked()
            window.root.update()

    mock_folder.assert_not_called()  # invalid input caught before any dialog
    assert mock_error.called
    assert not window.split_in_progress

    window._select_tool("merge_compress")
    window.root.update()


def test_invalid_n_rejected_cleanly(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    window.split_mode_var.set("every_n")
    window.split_n_var.set("not_a_number")

    with patch("file_manager.select_output_folder") as mock_folder:
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_split_execute_clicked()
            window.root.update()

    mock_folder.assert_not_called()
    assert mock_error.called

    window._select_tool("merge_compress")
    window.root.update()


def test_zero_n_rejected_cleanly(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    window.split_mode_var.set("every_n")
    window.split_n_var.set("0")

    with patch("file_manager.select_output_folder") as mock_folder:
        with patch("tkinter.messagebox.showerror"):
            window._on_split_execute_clicked()
            window.root.update()

    mock_folder.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_page_beyond_document_count_rejected_cleanly(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)  # 10 pages

    window.split_mode_var.set("custom")
    window.split_ranges_var.set("50")

    with patch("file_manager.select_output_folder") as mock_folder:
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_split_execute_clicked()
            window.root.update()

    mock_folder.assert_not_called()
    assert mock_error.called

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancelled_output_folder_picker_does_not_start_worker(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    with patch("file_manager.select_output_folder", return_value=None):
        window._on_split_execute_clicked()
        window.root.update()

    assert not window.split_in_progress
    assert window.split_status_var.get() == "Status: Ready"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Background processing / threading / responsiveness / concurrency
# ---------------------------------------------------------------------------

def test_split_worker_runs_off_main_thread(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    seen_on_main = []
    original = split_engine.split_pdf

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch.object(split_engine, "split_pdf", side_effect=wrapper):
        with patch("file_manager.select_output_folder", return_value=output_dir):
            window._on_split_execute_clicked()
            assert _pump_until(window, lambda: not window.split_in_progress)

    assert seen_on_main == [False]

    window._select_tool("merge_compress")
    window.root.update()


def test_controls_disabled_while_split_runs_and_restored_after(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    gate = _GatedCall(split_engine.split_pdf)
    with patch.object(split_engine, "split_pdf", side_effect=gate):
        with patch("file_manager.select_output_folder", return_value=output_dir):
            window._on_split_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert str(window.split_select_btn["state"]) == "disabled"
            assert str(window.split_individual_radio["state"]) == "disabled"
            assert str(window.split_button["state"]) == "disabled"
            assert str(window.merge_only_btn["state"]) == "disabled"

            gate.release()
            assert _pump_until(window, lambda: not window.split_in_progress)

    assert str(window.split_select_btn["state"]) == "normal"
    assert str(window.split_button["state"]) == "normal"  # source still selected

    window._select_tool("merge_compress")
    window.root.update()


def test_gui_remains_responsive_during_split(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    gate = _GatedCall(split_engine.split_pdf)
    with patch.object(split_engine, "split_pdf", side_effect=gate):
        with patch("file_manager.select_output_folder", return_value=output_dir):
            window._on_split_execute_clicked()
            assert gate.entered.wait(timeout=5)

            update_count = 0
            for _ in range(20):
                window.root.update()
                update_count += 1

            assert window.split_in_progress
            assert update_count == 20

            gate.release()
            assert _pump_until(window, lambda: not window.split_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


def test_no_concurrent_split_operations(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    gate = _GatedCall(split_engine.split_pdf)
    with patch.object(split_engine, "split_pdf", side_effect=gate):
        with patch("file_manager.select_output_folder", return_value=output_dir) as mock_folder:
            window._on_split_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            calls_before = mock_folder.call_count
            window._on_split_execute_clicked()  # attempt a second split
            window.root.update()

            assert mock_folder.call_count == calls_before, (
                "a second split must not open a second output-folder dialog"
            )

            gate.release()
            assert _pump_until(window, lambda: not window.split_in_progress)

    outputs = list(output_dir.glob("*.pdf"))
    assert len(outputs) == 10  # exactly one split's worth of output

    window._select_tool("merge_compress")
    window.root.update()


def test_unexpected_worker_exception_recovers_cleanly(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch.object(split_engine, "split_pdf", side_effect=RuntimeError("totally unexpected")):
        with patch("file_manager.select_output_folder", return_value=output_dir):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_split_execute_clicked()
                assert _pump_until(window, lambda: not window.split_in_progress)

    assert mock_error.called
    assert "failed" in window.split_status_var.get().lower()
    assert str(window.split_button["state"]) == "normal"
    assert not window.split_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_successful_split_restores_controls_based_on_state(window, split_source_pdf, tmp_path):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch("file_manager.select_output_folder", return_value=output_dir):
        window._on_split_execute_clicked()
        assert _pump_until(window, lambda: not window.split_in_progress)

    assert str(window.split_select_btn["state"]) == "normal"
    assert str(window.split_button["state"]) == "normal"  # source still set

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Cross-tool state preservation (the Phase 13 new risk area)
# ---------------------------------------------------------------------------

def test_split_source_survives_switching_to_merge_compress_and_back(window, split_source_pdf):
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

    assert window.split_source is not None

    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("split")
    window.root.update()

    assert window.split_source is not None
    assert window.split_source.name == "document.pdf"
    assert str(window.split_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_merge_compress_still_works_after_using_split(window, split_source_pdf, tmp_path):
    """The core Phase 13 regression check: does Merge Only still work
    correctly after the Split workspace has been built, used, and
    switched away from?
    """
    window._select_tool("split")
    window.root.update()
    _reset_split_state(window)
    _select_split_file(window, split_source_pdf)

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
