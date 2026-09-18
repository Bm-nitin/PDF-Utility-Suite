"""
test_ui_protect.py

UI-level tests for the Protect PDF workspace (Phase 18): navigation,
single-file import, masked password/confirm-password fields, the
show/hide toggle, live validation (empty/whitespace/mismatched
passwords), the PROTECT PDF action end-to-end through the real UI,
background-thread/responsiveness/busy-lock behavior across every
available tool, source safety, and -- per the Phase 18 security
requirements -- that the literal test password never appears in any
status label, messagebox argument, worker queue result, or output
filename.

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

import pdf_engine
import protect_engine

TEST_PASSWORD = "TestPassword123!"


def _make_pdf(path, pages=1, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


@pytest.fixture
def protect_source_pdf(tmp_path):
    path = tmp_path / "document.pdf"
    _make_pdf(path, pages=4, text_prefix="PAGE_")
    return path


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_protect_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_protect_select_file_clicked()
        assert _pump_until(win, lambda: not win._protect_import_in_progress)


def _reset_protect_state(win):
    win.protect_source = None
    win._update_protect_source_label()
    win.protect_password_var.set("")
    win.protect_confirm_var.set("")
    win.protect_show_password_var.set(False)
    win._on_protect_show_password_toggled()
    win.protect_allow_printing_var.set(True)
    win.protect_allow_copying_var.set(True)
    win.protect_allow_modifying_var.set(True)
    win.protect_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_protect_controls_state()
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

def test_protect_is_registered_as_available():
    import tool_registry

    tool = tool_registry.get_tool("protect")
    assert tool is not None
    assert tool.is_available


def test_protect_appears_in_navigation(window):
    assert "protect" in window.tool_nav_buttons
    assert window.tool_nav_buttons["protect"].winfo_exists()

    window._select_tool("protect")
    window.root.update()

    assert window.current_tool_id == "protect"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_protect_opens_correct_workspace(window):
    window._select_tool("protect")
    window.root.update()

    assert window.protect_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.split_view.winfo_ismapped()
    assert not window.remove_pages_view.winfo_ismapped()
    assert not window.extract_view.winfo_ismapped()
    assert not window.organize_view.winfo_ismapped()
    assert not window.rotate_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


def test_workspace_has_expected_controls(window):
    window._select_tool("protect")
    window.root.update()

    for attr in [
        "protect_select_btn", "protect_source_label",
        "protect_password_entry", "protect_confirm_entry",
        "protect_show_password_check", "protect_clear_btn",
        "protect_allow_printing_check", "protect_allow_copying_check",
        "protect_allow_modifying_check", "protect_button",
        "protect_status_label", "protect_progress_bar",
    ]:
        assert hasattr(window, attr), f"missing widget: {attr}"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_protect_button_disabled_with_no_source(window):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_password_fields_masked_by_default(window):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    assert window.protect_password_entry.cget("show") == "*"
    assert window.protect_confirm_entry.cget("show") == "*"

    window._select_tool("merge_compress")
    window.root.update()


def test_permission_checkboxes_default_to_allowed(window):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    assert window.protect_allow_printing_var.get() is True
    assert window.protect_allow_copying_var.get() is True
    assert window.protect_allow_modifying_var.get() is True

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source file import / single-file picker
# ---------------------------------------------------------------------------

def test_single_pdf_can_be_imported(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    _select_protect_file(window, protect_source_pdf)

    assert window.protect_source is not None
    assert window.protect_source.name == "document.pdf"
    assert window.protect_source.page_count == 4
    assert "document.pdf" in window.protect_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


def test_multiple_pdf_selection_is_handled_via_single_file_picker(window):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None) as mock_picker, \
         patch("file_manager.select_pdf_files") as mock_multi:
        window._on_protect_select_file_clicked()
        window.root.update()
        mock_picker.assert_called_once()
        mock_multi.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_file_picker_does_not_start_a_worker(window):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None):
        window._on_protect_select_file_clicked()
        window.root.update()

    assert not window._protect_import_in_progress
    assert window.protect_status_var.get() == "Status: Ready"
    assert window.protect_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_corrupt_file_selection_shows_error(window, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_protect_select_file_clicked()
            assert _pump_until(window, lambda: not window._protect_import_in_progress)

    assert mock_error.called
    assert window.protect_source is None
    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_page_count_appears_after_import(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    _select_protect_file(window, protect_source_pdf)

    assert "4" in window.protect_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Live validation
# ---------------------------------------------------------------------------

def test_empty_password_disables_button(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set("")
    window.root.update()

    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_whitespace_only_password_shows_error(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set("   ")
    window.root.update()

    assert window.protect_error_var.get() != ""
    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_mismatched_passwords_shows_error(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set("SomethingElse!")
    window.root.update()

    assert "match" in window.protect_error_var.get().lower()
    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_valid_matching_passwords_enables_button(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    assert window.protect_error_var.get() == ""
    assert str(window.protect_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_confirmation_empty_disables_button(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.root.update()

    assert str(window.protect_button["state"]) == "disabled"
    assert "confirm" in window.protect_feedback_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_feedback_never_contains_the_password(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.root.update()
    assert TEST_PASSWORD not in window.protect_feedback_var.get()
    assert TEST_PASSWORD not in window.protect_error_var.get()

    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()
    assert TEST_PASSWORD not in window.protect_feedback_var.get()
    assert TEST_PASSWORD not in window.protect_error_var.get()

    window.protect_confirm_var.set("Mismatch!")
    window.root.update()
    assert TEST_PASSWORD not in window.protect_feedback_var.get()
    assert TEST_PASSWORD not in window.protect_error_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_show_password_toggle_unmasks_both_fields(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_show_password_var.set(True)
    window._on_protect_show_password_toggled()
    window.root.update()

    assert window.protect_password_entry.cget("show") == ""
    assert window.protect_confirm_entry.cget("show") == ""

    window.protect_show_password_var.set(False)
    window._on_protect_show_password_toggled()
    window.root.update()

    assert window.protect_password_entry.cget("show") == "*"
    assert window.protect_confirm_entry.cget("show") == "*"

    window._select_tool("merge_compress")
    window.root.update()


def test_clear_resets_passwords_and_permissions_but_keeps_source(
    window, protect_source_pdf
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.protect_allow_printing_var.set(False)
    window.root.update()

    window._on_protect_clear_clicked()
    window.root.update()

    assert window.protect_password_var.get() == ""
    assert window.protect_confirm_var.get() == ""
    assert window.protect_allow_printing_var.get() is True
    assert window.protect_source is not None  # source is untouched
    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# The PROTECT PDF operation, end-to-end through the real UI
# ---------------------------------------------------------------------------

def test_protect_pdf_end_to_end(window, protect_source_pdf, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.is_encrypted
        assert not doc.authenticate("wrong-password")
    doc2 = pymupdf.open(output_path)
    assert doc2.authenticate(TEST_PASSWORD)
    assert doc2.page_count == 4
    doc2.close()

    assert "successfully" in window.protect_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_permission_restriction_applied_end_to_end(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.protect_allow_printing_var.set(False)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    doc = pymupdf.open(output_path)
    doc.authenticate(TEST_PASSWORD)
    assert not (doc.permissions & pymupdf.PDF_PERM_PRINT)
    doc.close()

    window._select_tool("merge_compress")
    window.root.update()


def test_default_output_filename_follows_naming_convention(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path) as mock_save:
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert mock_save.call_args.kwargs["default_name"] == "document_protected.pdf"
    assert TEST_PASSWORD not in mock_save.call_args.kwargs["default_name"]

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_save_dialog_does_not_start_worker(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    with patch("file_manager.save_pdf_file", return_value=None):
        window._on_protect_execute_clicked()
        window.root.update()

    assert not window.protect_in_progress
    assert window.protect_status_var.get() == "Status: Ready"

    window._select_tool("merge_compress")
    window.root.update()


def test_no_native_output_dialog_before_execute_is_clicked(
    window, protect_source_pdf
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)

    with patch("file_manager.save_pdf_file") as mock_save:
        _select_protect_file(window, protect_source_pdf)
        window.protect_password_var.set(TEST_PASSWORD)
        window.protect_confirm_var.set(TEST_PASSWORD)
        window.root.update()

        mock_save.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_operation_starts_in_background(window, protect_source_pdf, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    seen_on_main = []
    original = protect_engine.protect_pdf

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    output_path = tmp_path / "output.pdf"
    with patch.object(protect_engine, "protect_pdf", side_effect=wrapper):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_protect_execute_clicked()
            assert _pump_until(window, lambda: not window.protect_in_progress)

    assert seen_on_main == [False]

    window._select_tool("merge_compress")
    window.root.update()


def test_worker_does_not_touch_tk_widgets_directly(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_controls_disabled_during_operation_and_restored_after(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(protect_engine.protect_pdf)
    with patch.object(protect_engine, "protect_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_protect_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert str(window.protect_select_btn["state"]) == "disabled"
            assert str(window.protect_password_entry["state"]) == "disabled"
            assert str(window.protect_confirm_entry["state"]) == "disabled"
            assert str(window.protect_clear_btn["state"]) == "disabled"
            assert str(window.protect_button["state"]) == "disabled"
            assert str(window.merge_only_btn["state"]) == "disabled"
            assert str(window.split_select_btn["state"]) == "disabled"
            assert str(window.remove_pages_select_btn["state"]) == "disabled"
            assert str(window.extract_select_btn["state"]) == "disabled"
            assert str(window.organize_select_btn["state"]) == "disabled"
            assert str(window.rotate_select_btn["state"]) == "disabled"

            gate.release()
            assert _pump_until(window, lambda: not window.protect_in_progress)

    assert str(window.protect_select_btn["state"]) == "normal"
    # A completed protection clears its source, so PROTECT PDF correctly
    # goes back to "disabled" rather than staying "normal" with a stale
    # source (mirrors every other tool's own equivalent behavior).
    assert window.protect_source is None
    assert str(window.protect_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_successful_operation_updates_status(window, protect_source_pdf, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert "successfully" in window.protect_status_var.get().lower()
    assert output_path.name in window.protect_status_var.get()
    assert TEST_PASSWORD not in window.protect_status_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_password_vars_cleared_after_successful_operation(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert window.protect_password_var.get() == ""
    assert window.protect_confirm_var.get() == ""

    window._select_tool("merge_compress")
    window.root.update()


def test_password_vars_cleared_after_failed_operation(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch.object(
        protect_engine, "protect_pdf",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror"):
                window._on_protect_execute_clicked()
                assert _pump_until(window, lambda: not window.protect_in_progress)

    assert window.protect_password_var.get() == ""
    assert window.protect_confirm_var.get() == ""

    window._select_tool("merge_compress")
    window.root.update()


def test_failed_operation_restores_ui_state(window, protect_source_pdf, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch.object(
        protect_engine, "protect_pdf",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_protect_execute_clicked()
                assert _pump_until(window, lambda: not window.protect_in_progress)

    assert mock_error.called
    # The messagebox call itself must never have been given the
    # password as an argument.
    for call in mock_error.call_args_list:
        assert TEST_PASSWORD not in str(call)
    assert "failed" in window.protect_status_var.get().lower()
    assert TEST_PASSWORD not in window.protect_status_var.get()
    # The source is NOT cleared on failure -- the user should be able to
    # try again with the same file rather than re-importing it.
    assert window.protect_source is not None
    assert str(window.protect_select_btn["state"]) == "normal"
    assert not window.protect_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_unexpected_worker_exception_recovers_cleanly(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch.object(
        protect_engine, "protect_pdf",
        side_effect=RuntimeError("totally unexpected"),
    ):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_protect_execute_clicked()
                assert _pump_until(window, lambda: not window.protect_in_progress)

    assert mock_error.called
    assert not window.protect_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_no_concurrent_protect_operations(window, protect_source_pdf, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(protect_engine.protect_pdf)
    with patch.object(protect_engine, "protect_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path) as mock_save:
            window._on_protect_execute_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            calls_before = mock_save.call_count
            window._on_protect_execute_clicked()  # attempt a second run
            window.root.update()

            assert mock_save.call_count == calls_before, (
                "a second Protect PDF run must not open a second Save As dialog"
            )

            gate.release()
            assert _pump_until(window, lambda: not window.protect_in_progress)

    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_gui_remains_responsive_during_protect(window, protect_source_pdf, tmp_path):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    gate = _GatedCall(protect_engine.protect_pdf)
    with patch.object(protect_engine, "protect_pdf", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output_path):
            window._on_protect_execute_clicked()
            assert gate.entered.wait(timeout=5)

            update_count = 0
            for _ in range(20):
                window.root.update()
                update_count += 1

            assert window.protect_in_progress
            assert update_count == 20

            gate.release()
            assert _pump_until(window, lambda: not window.protect_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_source_pdf_is_untouched_after_successful_protect(
    window, protect_source_pdf, tmp_path
):
    original_bytes = protect_source_pdf.read_bytes()

    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()

    output_path = tmp_path / "output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert protect_source_pdf.read_bytes() == original_bytes

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Switching away safely / cross-tool regression
# ---------------------------------------------------------------------------

def test_switching_away_from_protect_behaves_safely(window, protect_source_pdf):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
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
    window._select_tool("protect")
    window.root.update()

    assert window.protect_source is not None
    assert window.protect_source.name == "document.pdf"
    assert window.protect_password_var.get() == TEST_PASSWORD
    assert str(window.protect_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_protect_select_file_then_switch_leaves_other_tools_usable(
    window, protect_source_pdf, tmp_path
):
    """State-isolation regression, extended to Protect PDF: selecting a
    file on Protect PDF (which runs an import through the same app-wide
    busy lock as everything else) must not leave any other tool's
    controls permanently disabled afterward.
    """
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

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

    window._select_tool("merge_compress")
    window.root.update()
    assert str(window.select_files_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_rotate_select_file_then_switch_leaves_protect_usable(
    window, protect_source_pdf, tmp_path
):
    """The reverse direction: a Rotate Pages import must not leave
    Protect PDF's controls stuck disabled afterward.
    """
    window._select_tool("rotate")
    window.root.update()
    window.rotate_source = None
    window._update_rotate_source_label()
    window.rotate_selection_var.set("")
    window.rotate_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_rotate_controls_state()
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=protect_source_pdf):
        window._on_rotate_select_file_clicked()
        assert _pump_until(window, lambda: not window._rotate_import_in_progress)

    assert window.rotate_source is not None

    window._select_tool("protect")
    window.root.update()

    assert str(window.protect_select_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_merge_compress_select_files_then_clear_all_leaves_protect_usable(
    window, protect_source_pdf, tmp_path
):
    """Mirrors the equivalent regression test for every other tool: an
    import on the Merge/Compress side must not leave Protect PDF's
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

    window._select_tool("protect")
    window.root.update()

    assert str(window.protect_select_btn["state"]) == "normal"

    _select_protect_file(window, protect_source_pdf)
    assert window.protect_source is not None
    window.protect_password_var.set(TEST_PASSWORD)
    window.protect_confirm_var.set(TEST_PASSWORD)
    window.root.update()
    assert str(window.protect_button["state"]) == "normal"

    output_path = tmp_path / "protected.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert output_path.exists()

    _reset_protect_state(window)
    window._select_tool("merge_compress")
    window.root.update()


def test_existing_rotate_workspace_remains_functional(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

    window._select_tool("rotate")
    window.root.update()
    window.rotate_source = None
    window._update_rotate_source_label()
    window.rotate_selection_var.set("")
    window.rotate_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_rotate_controls_state()
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=protect_source_pdf):
        window._on_rotate_select_file_clicked()
        assert _pump_until(window, lambda: not window._rotate_import_in_progress)

    assert window.rotate_source is not None

    window.rotate_selection_var.set("1")
    window.root.update()
    assert str(window.rotate_button["state"]) == "normal"

    output_path = tmp_path / "rotated.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_rotate_execute_clicked()
        assert _pump_until(window, lambda: not window.rotate_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.page_count == 4

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_merge_compress_workspace_remains_functional(
    window, protect_source_pdf, tmp_path
):
    window._select_tool("protect")
    window.root.update()
    _reset_protect_state(window)
    _select_protect_file(window, protect_source_pdf)

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
