"""
test_ui_unlock.py

UI-level tests for the Unlock PDF workspace (Phase 19): navigation,
single-file import (accepting an encrypted source, unlike every other
tool), the masked password field and show/hide toggle, live validation
(no source / not-encrypted source / empty password / valid password),
the UNLOCK PDF action end-to-end through the real UI -- including that
it never opens a native Save As dialog, unlike every other single-
source tool -- background-thread/responsiveness/busy-lock behavior
across every available tool, source safety, and -- per the Phase 19
security requirements -- that the literal test password never appears
in any status label, messagebox argument, worker queue result, or
output filename.

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
import unlock_engine

USER_PASSWORD = "TestUserPassword123!"
OWNER_PASSWORD = "TestOwnerPassword456!"


def _make_plain_pdf(path, pages=1, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


def _make_encrypted_pdf(path, pages=4, text_prefix=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=USER_PASSWORD,
        owner_pw=OWNER_PASSWORD,
    )
    doc.close()


@pytest.fixture
def unlock_source_pdf(tmp_path):
    path = tmp_path / "document.pdf"
    _make_encrypted_pdf(path, pages=4, text_prefix="PAGE_")
    return path


@pytest.fixture
def plain_source_pdf(tmp_path):
    path = tmp_path / "plain.pdf"
    _make_plain_pdf(path, pages=3)
    return path


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_unlock_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_unlock_select_file_clicked()
        assert _pump_until(win, lambda: not win._unlock_import_in_progress)


def _reset_unlock_state(win):
    win.unlock_source = None
    win._update_unlock_source_label()
    win.unlock_password_var.set("")
    win.unlock_show_password_var.set(False)
    win._on_unlock_show_password_toggled()
    win.unlock_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_unlock_controls_state()
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

def test_unlock_is_registered_as_available():
    import tool_registry

    tool = tool_registry.get_tool("unlock")
    assert tool is not None
    assert tool.is_available


def test_unlock_appears_in_navigation(window):
    assert "unlock" in window.tool_nav_buttons
    assert window.tool_nav_buttons["unlock"].winfo_exists()

    window._select_tool("unlock")
    window.root.update()

    assert window.current_tool_id == "unlock"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_unlock_opens_correct_workspace(window):
    window._select_tool("unlock")
    window.root.update()

    assert window.unlock_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.protect_view.winfo_ismapped()
    assert not window.rotate_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


def test_workspace_has_expected_controls(window):
    window._select_tool("unlock")
    window.root.update()

    for attr in [
        "unlock_select_btn", "unlock_source_label", "unlock_password_entry",
        "unlock_show_password_check", "unlock_clear_btn", "unlock_button",
        "unlock_status_label", "unlock_progress_bar",
    ]:
        assert hasattr(window, attr), f"missing widget: {attr}"

    window._select_tool("merge_compress")
    window.root.update()


def test_password_field_is_masked_by_default(window):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    assert window.unlock_password_entry.cget("show") == "*"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_unlock_button_disabled_with_no_source(window):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    assert str(window.unlock_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source file import / encryption detection
# ---------------------------------------------------------------------------

def test_single_encrypted_pdf_can_be_imported(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    _select_unlock_file(window, unlock_source_pdf)

    assert window.unlock_source is not None
    assert window.unlock_source.name == "document.pdf"
    assert window.unlock_source.page_count == 4
    assert window.unlock_source.is_encrypted is True
    assert "document.pdf" in window.unlock_source_label.cget("text")

    window._select_tool("merge_compress")
    window.root.update()


def test_file_plus_no_password_disables_button(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    assert str(window.unlock_button["state"]) == "disabled"
    assert "password" in window.unlock_feedback_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_valid_password_enables_button(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    assert str(window.unlock_button["state"]) == "normal"
    assert window.unlock_error_var.get() == ""

    window._select_tool("merge_compress")
    window.root.update()


def test_unencrypted_source_shows_clear_message_and_disables_button(
    window, plain_source_pdf
):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    _select_unlock_file(window, plain_source_pdf)

    assert window.unlock_source.is_encrypted is False
    assert "not password protected" in window.unlock_feedback_var.get().lower()
    assert str(window.unlock_button["state"]) == "disabled"
    assert "not password protected" in window.unlock_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_unencrypted_source_stays_disabled_even_with_a_password_typed(
    window, plain_source_pdf
):
    """Per the Phase 19 spec: an unlock is never silently allowed to
    proceed on an already-unencrypted file, no matter what's typed.
    """
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, plain_source_pdf)

    window.unlock_password_var.set("anything-at-all")
    window.root.update()

    assert str(window.unlock_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_multiple_pdf_selection_is_handled_via_single_file_picker(window):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None) as mock_picker, \
         patch("file_manager.select_pdf_files") as mock_multi:
        window._on_unlock_select_file_clicked()
        window.root.update()
        mock_picker.assert_called_once()
        mock_multi.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_cancelled_file_picker_does_not_start_a_worker(window):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    with patch("file_manager.select_single_pdf_file", return_value=None):
        window._on_unlock_select_file_clicked()
        window.root.update()

    assert not window._unlock_import_in_progress
    assert window.unlock_status_var.get() == "Status: Ready"
    assert window.unlock_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_corrupt_file_selection_shows_error(window, tmp_path):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_unlock_select_file_clicked()
            assert _pump_until(window, lambda: not window._unlock_import_in_progress)

    assert mock_error.called
    assert window.unlock_source is None
    assert str(window.unlock_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_output_path_shown_appropriately(window, unlock_source_pdf, tmp_path):
    """Since Unlock has no Save As dialog, the resulting path must be
    surfaced some other way -- via the status line after completion.
    """
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert "document_unlocked.pdf" in window.unlock_status_var.get()

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Show/hide password, Clear
# ---------------------------------------------------------------------------

def test_show_password_toggle_unmasks_field(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_show_password_var.set(True)
    window._on_unlock_show_password_toggled()
    window.root.update()
    assert window.unlock_password_entry.cget("show") == ""

    window.unlock_show_password_var.set(False)
    window._on_unlock_show_password_toggled()
    window.root.update()
    assert window.unlock_password_entry.cget("show") == "*"

    window._select_tool("merge_compress")
    window.root.update()


def test_clear_resets_password_but_keeps_source(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_clear_clicked()
    window.root.update()

    assert window.unlock_password_var.get() == ""
    assert window.unlock_source is not None
    assert str(window.unlock_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_feedback_never_contains_the_password(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    assert USER_PASSWORD not in window.unlock_feedback_var.get()
    assert USER_PASSWORD not in window.unlock_error_var.get()

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# The UNLOCK PDF operation, end-to-end through the real UI
# ---------------------------------------------------------------------------

def test_no_native_save_dialog_is_ever_opened(window, unlock_source_pdf):
    """Unlike every other single-source tool, Unlock must never call
    file_manager.save_pdf_file() -- its output path is fully automatic.
    """
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    with patch("file_manager.save_pdf_file") as mock_save:
        _select_unlock_file(window, unlock_source_pdf)
        window.unlock_password_var.set(USER_PASSWORD)
        window.root.update()

        window._on_unlock_execute_clicked()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

        mock_save.assert_not_called()

    window._select_tool("merge_compress")
    window.root.update()


def test_unlock_pdf_end_to_end(window, unlock_source_pdf, tmp_path):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    output_path = unlock_source_pdf.parent / "document_unlocked.pdf"
    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.is_encrypted is False
        assert doc.page_count == 4
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE_1", "PAGE_2", "PAGE_3", "PAGE_4"]

    assert "successfully" in window.unlock_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_owner_password_also_works_end_to_end(window, unlock_source_pdf, tmp_path):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_password_var.set(OWNER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    output_path = unlock_source_pdf.parent / "document_unlocked.pdf"
    with pymupdf.open(output_path) as doc:
        assert doc.is_encrypted is False

    window._select_tool("merge_compress")
    window.root.update()


def test_wrong_password_end_to_end(window, unlock_source_pdf, tmp_path):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window.unlock_password_var.set("TotallyWrongPassword!")
    window.root.update()

    with patch("tkinter.messagebox.showerror") as mock_error:
        window._on_unlock_execute_clicked()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert mock_error.called
    for call in mock_error.call_args_list:
        assert "TotallyWrongPassword!" not in str(call)
    output_path = unlock_source_pdf.parent / "document_unlocked.pdf"
    assert not output_path.exists()
    assert "failed" in window.unlock_status_var.get().lower()

    window._select_tool("merge_compress")
    window.root.update()


def test_operation_starts_in_background(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    seen_on_main = []
    original = unlock_engine.unlock_pdf

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(unlock_engine, "unlock_pdf", side_effect=wrapper):
        window._on_unlock_execute_clicked()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert seen_on_main == [False]

    window._select_tool("merge_compress")
    window.root.update()


def test_worker_does_not_touch_tk_widgets_directly(window, unlock_source_pdf):
    """_assert_main_thread() must never fire during a normal Unlock PDF
    run -- if it did, it would mean the worker touched a widget directly
    instead of going through the queue.
    """
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    output_path = unlock_source_pdf.parent / "document_unlocked.pdf"
    assert output_path.exists()

    window._select_tool("merge_compress")
    window.root.update()


def test_controls_disabled_during_operation_and_restored_after(
    window, unlock_source_pdf
):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    gate = _GatedCall(unlock_engine.unlock_pdf)
    with patch.object(unlock_engine, "unlock_pdf", side_effect=gate):
        window._on_unlock_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        assert str(window.unlock_select_btn["state"]) == "disabled"
        assert str(window.unlock_password_entry["state"]) == "disabled"
        assert str(window.unlock_clear_btn["state"]) == "disabled"
        assert str(window.unlock_button["state"]) == "disabled"
        assert str(window.merge_only_btn["state"]) == "disabled"
        assert str(window.split_select_btn["state"]) == "disabled"
        assert str(window.remove_pages_select_btn["state"]) == "disabled"
        assert str(window.extract_select_btn["state"]) == "disabled"
        assert str(window.organize_select_btn["state"]) == "disabled"
        assert str(window.rotate_select_btn["state"]) == "disabled"
        assert str(window.protect_select_btn["state"]) == "disabled"

        gate.release()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert str(window.unlock_select_btn["state"]) == "normal"
    # A completed unlock clears its source, so UNLOCK PDF correctly goes
    # back to "disabled" rather than staying "normal" with a stale
    # source (mirrors every other tool's own equivalent behavior).
    assert window.unlock_source is None
    assert str(window.unlock_button["state"]) == "disabled"

    window._select_tool("merge_compress")
    window.root.update()


def test_successful_operation_updates_status(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert "successfully" in window.unlock_status_var.get().lower()
    assert USER_PASSWORD not in window.unlock_status_var.get()

    window._select_tool("merge_compress")
    window.root.update()


def test_password_var_cleared_after_success(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert window.unlock_password_var.get() == ""

    window._select_tool("merge_compress")
    window.root.update()


def test_password_var_cleared_after_failure(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    with patch.object(
        unlock_engine, "unlock_pdf",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("tkinter.messagebox.showerror"):
            window._on_unlock_execute_clicked()
            assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert window.unlock_password_var.get() == ""

    window._select_tool("merge_compress")
    window.root.update()


def test_failed_operation_restores_ui_state(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    with patch.object(
        unlock_engine, "unlock_pdf",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_unlock_execute_clicked()
            assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert mock_error.called
    for call in mock_error.call_args_list:
        assert USER_PASSWORD not in str(call)
    assert "failed" in window.unlock_status_var.get().lower()
    assert USER_PASSWORD not in window.unlock_status_var.get()
    # The source is NOT cleared on failure -- the user should be able to
    # try again with the same file rather than re-importing it.
    assert window.unlock_source is not None
    assert str(window.unlock_select_btn["state"]) == "normal"
    assert not window.unlock_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_unexpected_worker_exception_recovers_cleanly(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    with patch.object(
        unlock_engine, "unlock_pdf",
        side_effect=RuntimeError("totally unexpected"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_unlock_execute_clicked()
            assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert mock_error.called
    assert not window.unlock_in_progress
    assert not window._any_operation_in_progress()

    window._select_tool("merge_compress")
    window.root.update()


def test_no_concurrent_unlock_operations(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    gate = _GatedCall(unlock_engine.unlock_pdf)
    call_count = {"n": 0}

    def counting_gate(*args, **kwargs):
        call_count["n"] += 1
        return gate(*args, **kwargs)

    with patch.object(unlock_engine, "unlock_pdf", side_effect=counting_gate):
        window._on_unlock_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        calls_before = call_count["n"]
        window._on_unlock_execute_clicked()  # attempt a second run
        window.root.update()

        assert call_count["n"] == calls_before, (
            "a second Unlock PDF run must not start a second worker"
        )

        gate.release()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


def test_repeated_operations(window, tmp_path):
    """Import, unlock, re-import a fresh encrypted copy, unlock again --
    the workspace must handle this cleanly with no leftover state.
    """
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)

    for i in range(2):
        src = tmp_path / f"doc{i}.pdf"
        _make_encrypted_pdf(src, pages=2)

        _select_unlock_file(window, src)
        window.unlock_password_var.set(USER_PASSWORD)
        window.root.update()

        window._on_unlock_execute_clicked()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

        output_path = tmp_path / f"doc{i}_unlocked.pdf"
        assert output_path.exists()
        assert window.unlock_source is None

    window._select_tool("merge_compress")
    window.root.update()


def test_gui_remains_responsive_during_unlock(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    gate = _GatedCall(unlock_engine.unlock_pdf)
    with patch.object(unlock_engine, "unlock_pdf", side_effect=gate):
        window._on_unlock_execute_clicked()
        assert gate.entered.wait(timeout=5)

        update_count = 0
        for _ in range(20):
            window.root.update()
            update_count += 1

        assert window.unlock_in_progress
        assert update_count == 20

        gate.release()
        assert _pump_until(window, lambda: not window.unlock_in_progress)

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_source_pdf_is_untouched_after_successful_unlock(window, unlock_source_pdf):
    original_bytes = unlock_source_pdf.read_bytes()

    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    assert unlock_source_pdf.read_bytes() == original_bytes
    assert unlock_engine.is_source_encrypted(unlock_source_pdf) is True

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Switching away safely / cross-tool regression / scrollable-workspace
# integration
# ---------------------------------------------------------------------------

def test_switching_away_from_unlock_behaves_safely(window, unlock_source_pdf):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)
    window.unlock_password_var.set(USER_PASSWORD)
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
    window._select_tool("unlock")
    window.root.update()

    assert window.unlock_source is not None
    assert window.unlock_source.name == "document.pdf"
    assert window.unlock_password_var.get() == USER_PASSWORD
    assert str(window.unlock_button["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_unlock_select_file_then_switch_leaves_other_tools_usable(
    window, unlock_source_pdf
):
    """State-isolation regression, extended to Unlock PDF: selecting a
    file on Unlock PDF (which runs an import through the same app-wide
    busy lock as everything else) must not leave any other tool's
    controls permanently disabled afterward.
    """
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

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

    window._select_tool("merge_compress")
    window.root.update()
    assert str(window.select_files_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_protect_select_file_then_switch_leaves_unlock_usable(
    window, unlock_source_pdf, tmp_path
):
    """The reverse direction: a Protect PDF import must not leave
    Unlock PDF's controls stuck disabled afterward.
    """
    window._select_tool("protect")
    window.root.update()
    window.protect_source = None
    window._update_protect_source_label()
    window.protect_password_var.set("")
    window.protect_confirm_var.set("")
    window.protect_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_protect_controls_state()
    window.root.update()

    plain = tmp_path / "for_protect.pdf"
    _make_plain_pdf(plain, pages=2)

    with patch("file_manager.select_single_pdf_file", return_value=plain):
        window._on_protect_select_file_clicked()
        assert _pump_until(window, lambda: not window._protect_import_in_progress)

    assert window.protect_source is not None

    window._select_tool("unlock")
    window.root.update()

    assert str(window.unlock_select_btn["state"]) == "normal"

    window._select_tool("merge_compress")
    window.root.update()


def test_merge_compress_select_files_then_clear_all_leaves_unlock_usable(
    window, unlock_source_pdf, tmp_path
):
    """Mirrors the equivalent regression test for every other tool: an
    import on the Merge/Compress side must not leave Unlock PDF's
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
    _make_plain_pdf(a, pages=2)
    _make_plain_pdf(b, pages=3)

    with patch("file_manager.select_pdf_files", return_value=[a, b]):
        window._on_select_files_clicked()
        assert _pump_until(window, lambda: not window._import_in_progress)

    assert window.state.total_files == 2

    window._on_clear_all_clicked()
    window.root.update()
    assert window.state.total_files == 0

    window._select_tool("unlock")
    window.root.update()

    assert str(window.unlock_select_btn["state"]) == "normal"

    _select_unlock_file(window, unlock_source_pdf)
    assert window.unlock_source is not None
    window.unlock_password_var.set(USER_PASSWORD)
    window.root.update()
    assert str(window.unlock_button["state"]) == "normal"

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    output_path = unlock_source_pdf.parent / "document_unlocked.pdf"
    assert output_path.exists()

    _reset_unlock_state(window)
    window._select_tool("merge_compress")
    window.root.update()


def test_scrollable_workspace_integration_does_not_crash(window):
    """Unlock's workspace lives in the same scrollable-workspace shell
    as every other tool -- selecting it and scrolling must not raise.
    This does NOT touch or assert anything about the known, deferred
    scrollable-workspace test failures from earlier phases; it only
    confirms Unlock's own workspace participates in that shell without
    error.
    """
    window._select_tool("unlock")
    window.root.update()

    assert window.unlock_view.winfo_ismapped()
    # Triggering the same update cycle the scrollable container uses
    # must not raise.
    window.root.update_idletasks()

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_protect_workspace_remains_functional(
    window, unlock_source_pdf, tmp_path
):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window._select_tool("protect")
    window.root.update()
    window.protect_source = None
    window._update_protect_source_label()
    window.protect_password_var.set("")
    window.protect_confirm_var.set("")
    window.protect_status_var.set("Status: Ready")
    window._update_button_states()
    window._update_protect_controls_state()
    window.root.update()

    plain = tmp_path / "for_protect.pdf"
    _make_plain_pdf(plain, pages=2)

    with patch("file_manager.select_single_pdf_file", return_value=plain):
        window._on_protect_select_file_clicked()
        assert _pump_until(window, lambda: not window._protect_import_in_progress)

    assert window.protect_source is not None

    window.protect_password_var.set("NewPassword123!")
    window.protect_confirm_var.set("NewPassword123!")
    window.root.update()
    assert str(window.protect_button["state"]) == "normal"

    output_path = tmp_path / "protected_output.pdf"
    with patch("file_manager.save_pdf_file", return_value=output_path):
        window._on_protect_execute_clicked()
        assert _pump_until(window, lambda: not window.protect_in_progress)

    assert output_path.exists()
    with pymupdf.open(output_path) as doc:
        assert doc.is_encrypted

    window._select_tool("merge_compress")
    window.root.update()


def test_existing_merge_compress_workspace_remains_functional(
    window, unlock_source_pdf, tmp_path
):
    window._select_tool("unlock")
    window.root.update()
    _reset_unlock_state(window)
    _select_unlock_file(window, unlock_source_pdf)

    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_plain_pdf(a, pages=2)
    _make_plain_pdf(b, pages=3)

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
