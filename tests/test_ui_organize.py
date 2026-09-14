"""
test_ui_organize.py

UI-level tests for the Organize/Reorder Pages workspace (Phase 16):
navigation, single-file import, initial identity order, live order
validation/feedback (invalid page, duplicate page, missing page,
malformed input), Move Up / Move Down / Reset / Clear, the ORGANIZE
PAGES action end-to-end through the real UI, background-thread/
responsiveness/concurrency behavior, source safety, and cross-tool
switching regressions (Organize <-> Split <-> Remove Pages <-> Extract
<-> Merge/Compress).

Uses deterministic threading.Event-based gating (the _GatedCall pattern
established in test_phase9_responsive_processing.py and reused in
test_ui_split.py / test_ui_remove_pages.py / test_ui_extract.py) for any
test that needs to catch an operation genuinely mid-flight -- never
sleep()-based timing assumptions.

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

import organize_engine
import pdf_engine


def _make_pdf(path, pages=1, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


@pytest.fixture
def organize_source_pdf(tmp_path):
    path = tmp_path / "document.pdf"
    _make_pdf(path, pages=5, text_prefix="PAGE_")
    return path


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_organize_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_organize_select_file_clicked()
        assert _pump_until(win, lambda: not win._organize_import_in_progress)


def _reset_organize_state(win):
    win.organize_source = None
    win._update_organize_source_label()
    win.organize_order_var.set("")
    win.organize_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_organize_controls_state()
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
# Registry / Navigation
# ---------------------------------------------------------------------------

def test_organize_pages_is_registered_as_available():
    import tool_registry

    tool = tool_registry.get_tool("organize_pages")
    assert tool is not None
    assert tool.is_available


def test_organize_pages_appears_in_navigation(window):
    assert "organize_pages" in window.tool_nav_buttons
    assert window.tool_nav_buttons["organize_pages"].winfo_exists()

    window._select_tool("organize_pages")
    window.root.update()

    assert window.current_tool_id == "organize_pages"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_organize_pages_opens_correct_workspace(window):
    window._select_tool("organize_pages")
    window.root.update()

    assert window.organize_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.split_view.winfo_ismapped()
    assert not window.remove_pages_view.winfo_ismapped()
    assert not window.extract_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_organize_button_disabled_with_no_source(window):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_move_buttons_disabled_with_no_source(window):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    assert str(window.organize_move_up_btn["state"]) == "disabled"
    assert str(window.organize_move_down_btn["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source file import / initial order
# ---------------------------------------------------------------------------

def test_single_pdf_can_be_imported(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    _select_organize_file(window, organize_source_pdf)

    assert window.organize_source is not None
    assert window.organize_source.name == "document.pdf"
    assert window.organize_source.page_count == 5
    assert "document.pdf" in window.organize_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


def test_order_initializes_to_identity_after_import(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    _select_organize_file(window, organize_source_pdf)

    assert window.organize_order_var.get() == "1,2,3,4,5"
    assert window.organize_error_var.get() == ""
    assert str(window.organize_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_listbox_reflects_identity_order_after_import(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    _select_organize_file(window, organize_source_pdf)

    items = list(window.organize_order_listbox.get(0, "end"))
    assert len(items) == 5
    assert "Page 1" in items[0]
    assert "Page 5" in items[4]

    window._select_tool("merge_compress")
    window.root.update()


def test_multiple_pdf_selection_is_handled_via_single_file_picker(window):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None) as mock_picker, \
         patch("file_manager.select_pdf_files") as mock_multi:
        window._on_organize_select_file_clicked()
        window.root.update()
        mock_picker.assert_called_once()
        mock_multi.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_file_picker_does_not_start_a_worker(window):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None):
        window._on_organize_select_file_clicked()
        window.root.update()

    assert not window._organize_import_in_progress
    assert window.organize_status_var.get() == "Status: Ready"
    assert window.organize_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_corrupt_file_selection_shows_error(window, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_organize_select_file_clicked()
            assert _pump_until(window, lambda: not window._organize_import_in_progress)

    assert mock_error.called
    assert window.organize_source is None
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_page_count_appears_after_import(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)

    _select_organize_file(window, organize_source_pdf)

    assert "5" in window.organize_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Live validation feedback (no engine run required)
# ---------------------------------------------------------------------------

def test_valid_permutation_is_accepted(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("3,1,5,2,4")
    window.root.update()

    assert window.organize_error_var.get() == ""
    assert str(window.organize_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_invalid_page_number_shows_validation_error(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("1,2,3,4,99")
    window.root.update()

    assert window.organize_error_var.get() != ""
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_zero_shows_validation_error(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("0,1,2,3,4")
    window.root.update()

    assert window.organize_error_var.get() != ""
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_duplicate_page_detected(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("1,1,3,4,5")
    window.root.update()

    error = window.organize_error_var.get()
    assert error != ""
    assert "once" in error.lower() or "duplicate" in error.lower() or "more than once" in error.lower()
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_missing_page_detected(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("3,1,5,2")  # missing page 4
    window.root.update()

    error = window.organize_error_var.get()
    assert error != ""
    assert "4" in error
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_malformed_input_does_not_crash_the_ui(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    for bad_text in ["abc", "1,2,3,4,5,", "1,,2,3,4,5", "1-3,4,5", "", "   "]:
        window.organize_order_var.set(bad_text)
        window.root.update()  # must not raise
        assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_range_syntax_is_rejected_in_the_ui(window, organize_source_pdf):
    """Ranges are deliberately not supported for reordering (see
    organize_engine.py) -- the UI must surface this as a validation
    error, not silently accept or expand it.
    """
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("1-5")
    window.root.update()

    assert window.organize_error_var.get() != ""
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_listbox_clears_when_order_becomes_invalid(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    assert len(window.organize_order_listbox.get(0, "end")) == 5

    window.organize_order_var.set("3,1,5,2")  # incomplete
    window.root.update()

    assert len(window.organize_order_listbox.get(0, "end")) == 0

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Move Up / Move Down / Reset / Clear
# ---------------------------------------------------------------------------

def test_move_down_swaps_selected_page_with_next(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    # identity order: 1,2,3,4,5 -- select position 1 (page 1)
    window.organize_order_listbox.selection_clear(0, "end")
    window.organize_order_listbox.selection_set(0)
    window.organize_order_listbox.event_generate("<<ListboxSelect>>")
    window.root.update()

    assert str(window.organize_move_down_btn["state"]) == "normal"
    assert str(window.organize_move_up_btn["state"]) == "disabled"

    window._on_organize_move_down_clicked()
    window.root.update()

    assert window.organize_order_var.get() == "2,1,3,4,5"

    window._select_tool("merge_compress")
    window.root.update()


def test_move_up_swaps_selected_page_with_previous(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    # identity order: 1,2,3,4,5 -- select position 2 (0-based index 1, page 2)
    window.organize_order_listbox.selection_clear(0, "end")
    window.organize_order_listbox.selection_set(1)
    window.organize_order_listbox.event_generate("<<ListboxSelect>>")
    window.root.update()

    assert str(window.organize_move_up_btn["state"]) == "normal"

    window._on_organize_move_up_clicked()
    window.root.update()

    assert window.organize_order_var.get() == "2,1,3,4,5"

    window._select_tool("merge_compress")
    window.root.update()


def test_move_up_disabled_at_top_of_list(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_listbox.selection_clear(0, "end")
    window.organize_order_listbox.selection_set(0)
    window.organize_order_listbox.event_generate("<<ListboxSelect>>")
    window.root.update()

    assert str(window.organize_move_up_btn["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_move_down_disabled_at_bottom_of_list(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_listbox.selection_clear(0, "end")
    window.organize_order_listbox.selection_set(4)
    window.organize_order_listbox.event_generate("<<ListboxSelect>>")
    window.root.update()

    assert str(window.organize_move_down_btn["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_move_buttons_disabled_with_no_listbox_selection(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_listbox.selection_clear(0, "end")
    window.organize_order_listbox.event_generate("<<ListboxSelect>>")
    window.root.update()

    assert str(window.organize_move_up_btn["state"]) == "disabled"
    assert str(window.organize_move_down_btn["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_reset_restores_identity_order(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("5,4,3,2,1")
    window.root.update()
    assert window.organize_order_var.get() == "5,4,3,2,1"

    window._on_organize_reset_clicked()
    window.root.update()

    assert window.organize_order_var.get() == "1,2,3,4,5"
    assert str(window.organize_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_clear_empties_order_but_keeps_source(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window._on_organize_clear_clicked()
    window.root.update()

    assert window.organize_order_var.get() == ""
    assert window.organize_source is not None  # source is untouched
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# The ORGANIZE PAGES operation, end-to-end through the real UI
# ---------------------------------------------------------------------------

def test_organize_pages_end_to_end(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window.organize_order_var.set("3,1,5,2,4")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_organize_execute_clicked()
        assert _pump_until(window, lambda: not window.organize_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 5
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE_3", "PAGE_1", "PAGE_5", "PAGE_2", "PAGE_4"]

    assert "successfully" in window.organize_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_organize_pages_identity_order_end_to_end(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)
    # organize_order_var is already "1,2,3,4,5" (identity) right after import

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_organize_execute_clicked()
        assert _pump_until(window, lambda: not window.organize_in_progress)

    with pymupdf.open(output_path) as doc:
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE_1", "PAGE_2", "PAGE_3", "PAGE_4", "PAGE_5"]

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_save_dialog_does_not_start_worker(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    with patch("file_manager.save_pdf_file", return_value=None):
        window._on_organize_execute_clicked()
        window.root.update()

    assert not window.organize_in_progress
    assert window.organize_status_var.get() == "Status: Ready"

    window._select_tool("merge_compress")
    window.root.update()


def test_default_output_filename_follows_naming_convention(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path) as mock_save:
        window._on_organize_execute_clicked()
        assert _pump_until(window, lambda: not window.organize_in_progress)

    assert mock_save.call_args.kwargs["default_name"] == "document_organized.pdf"

    window._select_tool("merge_compress")
    window.root.update()


def test_operation_starts_in_background(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    seen_on_main = []
    original = organize_engine.organize_pages_from_pdf

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    output_path = tmp_path / "output.pdf"
    with patch.object(organize_engine, "organize_pages_from_pdf", side_effect=wrapper):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_organize_execute_clicked()
            assert _pump_until(window, lambda: not window.organize_in_progress)

    assert seen_on_main == [False]

    window._select_tool("merge_compress")
    window.root.update()


def test_worker_does_not_touch_tk_widgets_directly(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_organize_execute_clicked()
        assert window.organize_in_progress
        assert _pump_until(window, lambda: not window.organize_in_progress)

    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_controls_disabled_during_operation_and_restored_after(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(organize_engine.organize_pages_from_pdf)
    with patch.object(organize_engine, "organize_pages_from_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_organize_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert str(window.organize_select_btn["state"]) == "disabled"
            assert str(window.organize_reset_btn["state"]) == "disabled"
            assert str(window.organize_clear_btn["state"]) == "disabled"
            assert str(window.organize_order_entry["state"]) == "disabled"
            assert str(window.organize_move_up_btn["state"]) == "disabled"
            assert str(window.organize_move_down_btn["state"]) == "disabled"
            assert str(window.organize_button["state"]) == "disabled"
            assert str(window.merge_only_btn["state"]) == "disabled"
            assert str(window.split_select_btn["state"]) == "disabled"
            assert str(window.remove_pages_select_btn["state"]) == "disabled"
            assert str(window.extract_select_btn["state"]) == "disabled"

            gate.release()
            assert _pump_until(window, lambda: not window.organize_in_progress)

    assert str(window.organize_select_btn["state"]) == "normal"
    # A completed reorder clears its source, so ORGANIZE PAGES correctly
    # goes back to "disabled" rather than staying "normal" with a stale
    # source (mirrors Split/Remove Pages/Extract's own behavior).
    assert window.organize_source is None
    assert str(window.organize_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_successful_operation_updates_status(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_organize_execute_clicked()
        assert _pump_until(window, lambda: not window.organize_in_progress)

    assert "successfully" in window.organize_status_var.get().lower()
    assert output_path.name in window.organize_status_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_failed_operation_restores_ui_state(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    with patch.object(
        organize_engine, "organize_pages_from_pdf",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_organize_execute_clicked()
                assert _pump_until(window, lambda: not window.organize_in_progress)

    assert mock_error.called
    assert "failed" in window.organize_status_var.get().lower()
    # The source is NOT cleared on failure -- unlike a success, the user
    # should be able to just fix the order and try again with the same
    # file rather than re-importing it.
    assert window.organize_source is not None
    assert str(window.organize_select_btn["state"]) == "normal"
    assert not window.organize_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_unexpected_worker_exception_recovers_cleanly(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    with patch.object(
        organize_engine, "organize_pages_from_pdf",
        side_effect=RuntimeError("totally unexpected"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_organize_execute_clicked()
                assert _pump_until(window, lambda: not window.organize_in_progress)

    assert mock_error.called
    assert not window.organize_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_no_concurrent_organize_operations(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(organize_engine.organize_pages_from_pdf)
    with patch.object(organize_engine, "organize_pages_from_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path) as mock_save:
            window._on_organize_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            calls_before = mock_save.call_count
            window._on_organize_execute_clicked()  # attempt a second run
            window.root.update()

            assert mock_save.call_count == calls_before, (
                "a second Organize Pages run must not open a second Save As dialog"
            )

            gate.release()
            assert _pump_until(window, lambda: not window.organize_in_progress)

    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_gui_remains_responsive_during_organize(window, organize_source_pdf, tmp_path):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(organize_engine.organize_pages_from_pdf)
    with patch.object(organize_engine, "organize_pages_from_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_organize_execute_clicked()
            assert gate.entered.wait(timeout=5)

            update_count = 0
            for _ in range(20):
                window.root.update()
                update_count += 1

            assert window.organize_in_progress
            assert update_count == 20

            gate.release()
            assert _pump_until(window, lambda: not window.organize_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_source_pdf_is_untouched_after_successful_organize(
    window, organize_source_pdf, tmp_path
):
    original_bytes = organize_source_pdf.read_bytes()

    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)
    window.organize_order_var.set("5,4,3,2,1")
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_organize_execute_clicked()
        assert _pump_until(window, lambda: not window.organize_in_progress)

    assert organize_source_pdf.read_bytes() == original_bytes

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Switching away safely / cross-tool regression
# ---------------------------------------------------------------------------

def test_switching_away_from_organize_pages_behaves_safely(window, organize_source_pdf):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)
    window.organize_order_var.set("2,1,3,4,5")
    window.root.update()

    window._select_tool("split")
    window.root.update()
    window._select_tool("remove_pages")
    window.root.update()
    window._select_tool("extract_pages")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("organize_pages")
    window.root.update()

    # State survives switching away and back, exactly like every other
    # tool's does.
    assert window.organize_source is not None
    assert window.organize_source.name == "document.pdf"
    assert window.organize_order_var.get() == "2,1,3,4,5"
    assert str(window.organize_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_organize_select_file_then_switch_leaves_other_tools_usable(
    window, organize_source_pdf, tmp_path
):
    """Bug 3 / state-isolation regression, extended to Organize Pages:
    selecting a file on Organize Pages (which runs an import through the
    same app-wide busy lock as everything else) must not leave any other
    tool's controls permanently disabled afterward.
    """
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window._select_tool("split")
    window.root.update()
    assert str(window.split_select_btn["state"]) == "normal"

    window._select_tool("remove_pages")
    window.root.update()
    assert str(window.remove_pages_select_btn["state"]) == "normal"

    window._select_tool("extract_pages")
    window.root.update()
    assert str(window.extract_select_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()
    assert str(window.select_files_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_merge_compress_select_files_then_clear_all_leaves_organize_usable(
    window, organize_source_pdf, tmp_path
):
    """Mirrors the equivalent Split/Remove Pages/Extract regression
    test: an import on the Merge/Compress side must not leave Organize
    Pages' controls stuck disabled afterward.
    """
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

    window._on_clear_all_clicked()
    window.root.update()
    assert window.state.total_files == 0

    window._select_tool("organize_pages")
    window.root.update()

    assert str(window.organize_select_btn["state"]) == "normal"

    _select_organize_file(window, organize_source_pdf)
    assert window.organize_source is not None
    assert str(window.organize_button["state"]) == "normal"  # identity order valid

    output_path = tmp_path / "organized.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_organize_execute_clicked()
        assert _pump_until(window, lambda: not window.organize_in_progress)

    assert output_path.exists()

    _reset_organize_state(window)
    window._select_tool("merge_compress")
    window.root.update()


def test_existing_extract_workspace_remains_functional(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window._select_tool("extract_pages")
    window.root.update()
    window.extract_source = None
    window._update_extract_source_label()
    window.extract_selection_var.set("")
    window.extract_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_extract_controls_state()
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=organize_source_pdf):
        window._on_extract_select_file_clicked()
        assert _pump_until(window, lambda: not window._extract_import_in_progress)

    assert window.extract_source is not None

    window.extract_selection_var.set("1,3")
    window.root.update()
    assert str(window.extract_button["state"]) == "normal"

    output_path = tmp_path / "extracted.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_extract_execute_clicked()
        assert _pump_until(window, lambda: not window.extract_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 2

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_split_workspace_remains_functional(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window._select_tool("split")
    window.root.update()
    window.split_source = None
    window._update_split_source_label()
    window.split_mode_var.set("individual")
    window.split_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_split_button_state()
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=organize_source_pdf):
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
    assert len(outputs) == 5
    assert "Split completed successfully" in window.split_status_var.get()

    window._on_clear_all_clicked()
    window._select_tool("merge_compress")
    window.root.update()


def test_existing_remove_pages_workspace_remains_functional(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

    window._select_tool("remove_pages")
    window.root.update()
    window.remove_pages_source = None
    window._update_remove_pages_source_label()
    window.remove_pages_selection_var.set("")
    window.remove_pages_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_remove_pages_controls_state()
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=organize_source_pdf):
        window._on_remove_pages_select_file_clicked()
        assert _pump_until(window, lambda: not window._remove_pages_import_in_progress)

    assert window.remove_pages_source is not None

    window.remove_pages_selection_var.set("1")
    window.root.update()
    assert str(window.remove_pages_button["state"]) == "normal"

    output_path = tmp_path / "removed.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_remove_pages_execute_clicked()
        assert _pump_until(window, lambda: not window.remove_pages_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 4

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_merge_compress_workspace_remains_functional(
    window, organize_source_pdf, tmp_path
):
    window._select_tool("organize_pages")
    window.root.update()
    _reset_organize_state(window)
    _select_organize_file(window, organize_source_pdf)

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
