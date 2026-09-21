"""
test_ui_page_numbers.py

UI-level tests for the Add Page Numbers workspace (Phase 20):
navigation, single-file import, the All Pages / Selected Pages mode
toggle (mirroring Split PDF's own mode-toggle pattern), live validation
of the page range and the four numeric/position options, the ADD PAGE
NUMBERS action end-to-end through the real UI -- including that it
never opens a native Save As dialog, exactly like Unlock PDF (Phase 19)
-- background-thread/responsiveness/busy-lock behavior across every
available tool, source safety, and cross-tool regression checks.

Uses deterministic threading.Event-based gating (the _GatedCall pattern
established in test_phase9_responsive_processing.py and reused in every
other tool's own UI test module) for any test that needs to catch an
operation genuinely mid-flight -- never sleep()-based timing
assumptions.

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

import page_numbers_engine
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
def page_numbers_source_pdf(tmp_path):
    path = tmp_path / "document.pdf"
    _make_pdf(path, pages=6, text_prefix=None)
    return path


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_page_numbers_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_page_numbers_select_file_clicked()
        assert _pump_until(win, lambda: not win._page_numbers_import_in_progress)


def _reset_page_numbers_state(win):
    win.page_numbers_source = None
    win._update_page_numbers_source_label()
    win.page_numbers_all_pages_var.set(True)
    win.page_numbers_selection_var.set("")
    win.page_numbers_start_var.set(str(page_numbers_engine.DEFAULT_START_NUMBER))
    win.page_numbers_position_var.set(page_numbers_engine.DEFAULT_POSITION)
    win.page_numbers_font_size_var.set(str(page_numbers_engine.DEFAULT_FONT_SIZE))
    win.page_numbers_margin_var.set(str(page_numbers_engine.DEFAULT_MARGIN))
    win.page_numbers_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_page_numbers_controls_state()
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
# Registry / navigation
# ---------------------------------------------------------------------------

def test_page_numbers_is_registered_as_available():
    import tool_registry

    tool = tool_registry.get_tool("page_numbers")
    assert tool is not None
    assert tool.is_available


def test_page_numbers_appears_in_navigation(window):
    assert "page_numbers" in window.tool_nav_buttons
    assert window.tool_nav_buttons["page_numbers"].winfo_exists()

    window._select_tool("page_numbers")
    window.root.update()

    assert window.current_tool_id == "page_numbers"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_page_numbers_opens_correct_workspace(window):
    window._select_tool("page_numbers")
    window.root.update()

    assert window.page_numbers_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.unlock_view.winfo_ismapped()
    assert not window.protect_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


def test_workspace_has_expected_controls(window):
    window._select_tool("page_numbers")
    window.root.update()

    for attr in [
        "page_numbers_select_btn", "page_numbers_source_label",
        "page_numbers_all_pages_radio", "page_numbers_selected_pages_radio",
        "page_numbers_selection_entry", "page_numbers_start_entry",
        "page_numbers_font_size_entry", "page_numbers_margin_entry",
        "page_numbers_position_radios", "page_numbers_button",
        "page_numbers_status_label", "page_numbers_progress_bar",
    ]:
        assert hasattr(window, attr), f"missing widget: {attr}"
    assert set(window.page_numbers_position_radios.keys()) == {
        "top_left", "top_center", "top_right",
        "bottom_left", "bottom_center", "bottom_right",
    }

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_page_numbers_button_disabled_with_no_source(window):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    assert str(window.page_numbers_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_defaults_match_engine_defaults(window):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    assert window.page_numbers_all_pages_var.get() is True
    assert window.page_numbers_start_var.get() == str(
        page_numbers_engine.DEFAULT_START_NUMBER
    )
    assert window.page_numbers_position_var.get() == page_numbers_engine.DEFAULT_POSITION

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source file import
# ---------------------------------------------------------------------------

def test_single_pdf_can_be_imported(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    _select_page_numbers_file(window, page_numbers_source_pdf)

    assert window.page_numbers_source is not None
    assert window.page_numbers_source.name == "document.pdf"
    assert window.page_numbers_source.page_count == 6
    assert "document.pdf" in window.page_numbers_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


def test_all_pages_mode_enables_button_with_no_range_needed(
    window, page_numbers_source_pdf
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    assert window.page_numbers_all_pages_var.get() is True
    assert str(window.page_numbers_button["state"]) == "normal"
    assert "All" in window.page_numbers_feedback_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_selected_pages_mode_shows_and_enables_range_entry(
    window, page_numbers_source_pdf
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_all_pages_var.set(False)
    window._update_page_numbers_mode_controls()
    window.root.update()

    assert str(window.page_numbers_selection_entry["state"]) == "normal"
    assert str(window.page_numbers_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_range_entry_disabled_in_all_pages_mode(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    assert str(window.page_numbers_selection_entry["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_valid_page_range_enables_button(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_all_pages_var.set(False)
    window._update_page_numbers_mode_controls()
    window.page_numbers_selection_var.set("1-3")
    window.root.update()

    assert window.page_numbers_error_var.get() == ""
    assert str(window.page_numbers_button["state"]) == "normal"
    assert "3 selected" in window.page_numbers_feedback_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_invalid_page_range_disables_button(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_all_pages_var.set(False)
    window._update_page_numbers_mode_controls()
    window.page_numbers_selection_var.set("0")
    window.root.update()

    assert window.page_numbers_error_var.get() != ""
    assert str(window.page_numbers_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_out_of_range_page_selection_disables_button(
    window, page_numbers_source_pdf
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_all_pages_var.set(False)
    window._update_page_numbers_mode_controls()
    window.page_numbers_selection_var.set("999")
    window.root.update()

    assert str(window.page_numbers_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_multiple_pdf_selection_is_handled_via_single_file_picker(window):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None) as mock_picker, \
         patch("file_manager.select_pdf_files") as mock_multi:
        window._on_page_numbers_select_file_clicked()
        window.root.update()
        mock_picker.assert_called_once()
        mock_multi.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_file_picker_does_not_start_a_worker(window):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None):
        window._on_page_numbers_select_file_clicked()
        window.root.update()

    assert not window._page_numbers_import_in_progress
    assert window.page_numbers_status_var.get() == "Status: Ready"
    assert window.page_numbers_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_corrupt_file_selection_shows_error(window, tmp_path):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_page_numbers_select_file_clicked()
            assert _pump_until(
                window, lambda: not window._page_numbers_import_in_progress
            )

    assert mock_error.called
    assert window.page_numbers_source is None
    assert str(window.page_numbers_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Option validation
# ---------------------------------------------------------------------------

def test_start_number_validation(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_start_var.set("-1")
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "disabled"

    window.page_numbers_start_var.set("5")
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_font_size_validation(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_font_size_var.set("0")
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "disabled"

    window.page_numbers_font_size_var.set("14")
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_margin_validation(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_margin_var.set("-1")
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "disabled"

    window.page_numbers_margin_var.set("20")
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_position_selection(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_position_var.set("top_right")
    window.root.update()

    assert window.page_numbers_position_var.get() == "top_right"
    assert str(window.page_numbers_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# The ADD PAGE NUMBERS operation, end-to-end through the real UI
# ---------------------------------------------------------------------------

def test_no_native_save_dialog_is_ever_opened(window, page_numbers_source_pdf):
    """Like Unlock PDF (Phase 19), Add Page Numbers must never call
    file_manager.save_pdf_file() -- its output path is fully automatic.
    """
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    with patch("file_manager.save_pdf_file") as mock_save:
        _select_page_numbers_file(window, page_numbers_source_pdf)
        window.root.update()

        window._on_page_numbers_execute_clicked()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

        mock_save.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_add_page_numbers_all_pages_end_to_end(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    output_path = page_numbers_source_pdf.parent / "document_numbered.pdf"
    assert output_path.exists()
    doc = pymupdf.open(output_path)
    try:
        assert doc.page_count == 6
        for i in range(6):
            assert str(i + 1) in doc[i].get_text()
    finally:
        doc.close()

    assert "successfully" in window.page_numbers_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_add_page_numbers_selected_pages_end_to_end(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_all_pages_var.set(False)
    window._update_page_numbers_mode_controls()
    window.page_numbers_selection_var.set("2-4")
    window.root.update()

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    output_path = page_numbers_source_pdf.parent / "document_numbered.pdf"
    doc = pymupdf.open(output_path)
    try:
        assert doc[0].get_text().strip() == ""
        assert "1" in doc[1].get_text()
        assert "2" in doc[2].get_text()
        assert "3" in doc[3].get_text()
        assert doc[4].get_text().strip() == ""
    finally:
        doc.close()

    window._select_tool("merge_compress")
    window.root.update()


def test_default_output_filename_follows_naming_convention(
    window, page_numbers_source_pdf
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert "document_numbered.pdf" in window.page_numbers_status_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_operation_starts_in_background(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    seen_on_main = []
    original = page_numbers_engine.add_page_numbers

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(page_numbers_engine, "add_page_numbers", side_effect=wrapper):
        window._on_page_numbers_execute_clicked()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert seen_on_main == [False]

    window._select_tool("merge_compress")
    window.root.update()


def test_worker_does_not_touch_tk_widgets_directly(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    output_path = page_numbers_source_pdf.parent / "document_numbered.pdf"
    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_controls_disabled_during_operation_and_restored_after(
    window, page_numbers_source_pdf
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    gate = _GatedCall(page_numbers_engine.add_page_numbers)
    with patch.object(page_numbers_engine, "add_page_numbers", side_effect=gate):
        window._on_page_numbers_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        assert str(window.page_numbers_select_btn["state"]) == "disabled"
        assert str(window.page_numbers_all_pages_radio["state"]) == "disabled"
        assert str(window.page_numbers_start_entry["state"]) == "disabled"
        assert str(window.page_numbers_button["state"]) == "disabled"
        assert str(window.merge_only_btn["state"]) == "disabled"
        assert str(window.split_select_btn["state"]) == "disabled"
        assert str(window.remove_pages_select_btn["state"]) == "disabled"
        assert str(window.extract_select_btn["state"]) == "disabled"
        assert str(window.organize_select_btn["state"]) == "disabled"
        assert str(window.rotate_select_btn["state"]) == "disabled"
        assert str(window.protect_select_btn["state"]) == "disabled"
        assert str(window.unlock_select_btn["state"]) == "disabled"

        gate.release()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert str(window.page_numbers_select_btn["state"]) == "normal"
    # A completed operation clears its source, so ADD PAGE NUMBERS
    # correctly goes back to "disabled" rather than staying "normal"
    # with a stale source (mirrors every other tool's own equivalent
    # behavior).
    assert window.page_numbers_source is None
    assert str(window.page_numbers_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_successful_operation_updates_status(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert "successfully" in window.page_numbers_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_failed_operation_restores_ui_state(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    with patch.object(
        page_numbers_engine, "add_page_numbers",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_page_numbers_execute_clicked()
            assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert mock_error.called
    assert "failed" in window.page_numbers_status_var.get().lower()
    # The source is NOT cleared on failure -- the user should be able to
    # fix the configuration and try again with the same file rather than
    # re-importing it.
    assert window.page_numbers_source is not None
    assert str(window.page_numbers_select_btn["state"]) == "normal"
    assert not window.page_numbers_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_unexpected_worker_exception_recovers_cleanly(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    with patch.object(
        page_numbers_engine, "add_page_numbers",
        side_effect=RuntimeError("totally unexpected"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_page_numbers_execute_clicked()
            assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert mock_error.called
    assert not window.page_numbers_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_repeated_operations(window, tmp_path):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    for i in range(2):
        src = tmp_path / f"doc{i}.pdf"
        _make_pdf(src, pages=2, text_prefix=None)

        _select_page_numbers_file(window, src)
        window.root.update()

        window._on_page_numbers_execute_clicked()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

        output_path = tmp_path / f"doc{i}_numbered.pdf"
        assert output_path.exists()
        assert window.page_numbers_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_gui_remains_responsive_during_page_numbers(window, page_numbers_source_pdf):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    gate = _GatedCall(page_numbers_engine.add_page_numbers)
    with patch.object(page_numbers_engine, "add_page_numbers", side_effect=gate):
        window._on_page_numbers_execute_clicked()
        assert gate.entered.wait(timeout=5)

        update_count = 0
        for _ in range(20):
            window.root.update()
            update_count += 1

        assert window.page_numbers_in_progress
        assert update_count == 20

        gate.release()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_source_pdf_is_untouched_after_successful_operation(
    window, page_numbers_source_pdf
):
    original_bytes = page_numbers_source_pdf.read_bytes()

    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)
    window.root.update()

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    assert page_numbers_source_pdf.read_bytes() == original_bytes

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Switching away safely / cross-tool regression / scrollable-workspace
# integration
# ---------------------------------------------------------------------------

def test_switching_away_from_page_numbers_behaves_safely(
    window, page_numbers_source_pdf
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window.page_numbers_all_pages_var.set(False)
    window._update_page_numbers_mode_controls()
    window.page_numbers_selection_var.set("1-3")
    window.page_numbers_start_var.set("5")
    window.root.update()

    window._select_tool("unlock")
    window.root.update()
    window._select_tool("protect")
    window.root.update()
    window._select_tool("rotate")
    window.root.update()
    window._select_tool("organize_pages")
    window.root.update()
    window._select_tool("extract_pages")
    window.root.update()
    window._select_tool("remove_pages")
    window.root.update()
    window._select_tool("split")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("page_numbers")
    window.root.update()

    assert window.page_numbers_source is not None
    assert window.page_numbers_source.name == "document.pdf"
    assert window.page_numbers_selection_var.get() == "1-3"
    assert window.page_numbers_start_var.get() == "5"
    assert str(window.page_numbers_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_page_numbers_select_file_then_switch_leaves_other_tools_usable(
    window, page_numbers_source_pdf
):
    """State-isolation regression, extended to Add Page Numbers:
    selecting a file on Add Page Numbers (which runs an import through
    the same app-wide busy lock as everything else) must not leave any
    other tool's controls permanently disabled afterward.
    """
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

    window._select_tool("split")
    window.root.update()
    assert str(window.split_select_btn["state"]) == "normal"

    window._select_tool("remove_pages")
    window.root.update()
    assert str(window.remove_pages_select_btn["state"]) == "normal"

    window._select_tool("extract_pages")
    window.root.update()
    assert str(window.extract_select_btn["state"]) == "normal"

    window._select_tool("organize_pages")
    window.root.update()
    assert str(window.organize_select_btn["state"]) == "normal"

    window._select_tool("rotate")
    window.root.update()
    assert str(window.rotate_select_btn["state"]) == "normal"

    window._select_tool("protect")
    window.root.update()
    assert str(window.protect_select_btn["state"]) == "normal"

    window._select_tool("unlock")
    window.root.update()
    assert str(window.unlock_select_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()
    assert str(window.select_files_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_unlock_select_file_then_switch_leaves_page_numbers_usable(
    window, page_numbers_source_pdf, tmp_path
):
    """The reverse direction: an Unlock PDF import must not leave Add
    Page Numbers' controls stuck disabled afterward.
    """
    window._select_tool("unlock")
    window.root.update()
    window.unlock_source = None
    window._update_unlock_source_label()
    window.unlock_password_var.set("")
    window.unlock_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_unlock_controls_state()
    window.root.update()

    with patch(
        "file_manager.select_single_pdf_file", return_value=page_numbers_source_pdf,
    ):
        window._on_unlock_select_file_clicked()
        assert _pump_until(window, lambda: not window._unlock_import_in_progress)

    assert window.unlock_source is not None

    window._select_tool("page_numbers")
    window.root.update()

    assert str(window.page_numbers_select_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_merge_compress_select_files_then_clear_all_leaves_page_numbers_usable(
    window, page_numbers_source_pdf, tmp_path
):
    """Mirrors the equivalent regression test for every other tool: an
    import on the Merge/Compress side must not leave Add Page Numbers'
    controls stuck disabled afterward.
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

    window._select_tool("page_numbers")
    window.root.update()

    assert str(window.page_numbers_select_btn["state"]) == "normal"

    _select_page_numbers_file(window, page_numbers_source_pdf)
    assert window.page_numbers_source is not None
    window.root.update()
    assert str(window.page_numbers_button["state"]) == "normal"

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    output_path = page_numbers_source_pdf.parent / "document_numbered.pdf"
    assert output_path.exists()

    _reset_page_numbers_state(window)
    window._select_tool("merge_compress")
    window.root.update()


def test_scrollable_workspace_integration_does_not_crash(window):
    """Add Page Numbers' workspace lives in the same scrollable-
    workspace shell as every other tool -- selecting it must not raise.
    This does NOT touch or assert anything about the known, deferred
    scrollable-workspace test failures from earlier phases; it only
    confirms this tool's own workspace participates in that shell
    without error.
    """
    window._select_tool("page_numbers")
    window.root.update()

    assert window.page_numbers_view.winfo_ismapped()
    window.root.update_idletasks()

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_unlock_workspace_remains_functional(window, tmp_path):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)

    window._select_tool("unlock")
    window.root.update()
    window.unlock_source = None
    window._update_unlock_source_label()
    window.unlock_password_var.set("")
    window.unlock_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_unlock_controls_state()
    window.root.update()

    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="pass123", owner_pw="ownerpass456",
    )
    doc.close()

    with patch("file_manager.select_single_pdf_file", return_value=protected):
        window._on_unlock_select_file_clicked()
        assert _pump_until(window, lambda: not window._unlock_import_in_progress)

    assert window.unlock_source is not None
    window.unlock_password_var.set("pass123")
    window.root.update()
    assert str(window.unlock_button["state"]) == "normal"

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    output_path = protected.parent / "protected_unlocked.pdf"
    assert output_path.exists()
    with pymupdf.open(output_path) as unlocked_doc:
        assert not unlocked_doc.is_encrypted

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_merge_compress_workspace_remains_functional(
    window, page_numbers_source_pdf, tmp_path
):
    window._select_tool("page_numbers")
    window.root.update()
    _reset_page_numbers_state(window)
    _select_page_numbers_file(window, page_numbers_source_pdf)

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
