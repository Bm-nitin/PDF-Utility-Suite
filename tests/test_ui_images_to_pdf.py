"""
test_ui_images_to_pdf.py

UI-level tests for the Images -> PDF workspace (Phase 22): navigation,
multi-image import, ordered-list rendering, Move Up/Move Down/Remove/
Clear All, duplicate-selection preservation, page-size and margin live
validation, the CREATE PDF action end-to-end through the real UI --
including that it DOES open a native Save As dialog (unlike Watermark/
Page Numbers/Unlock's auto-named output) -- background-thread/
responsiveness/busy-lock behavior across every available tool, Tk
main-thread discipline, source safety, and cross-tool regression checks.

Uses the same deterministic threading.Event-based _GatedCall pattern as
test_ui_watermark.py (duplicated here per this project's established
"keep each test file self-contained" convention) and the shared
session-scoped `window` fixture from tests/conftest.py.
"""

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import images_to_pdf_engine as ie
import page_numbers_engine
import pdf_engine


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_image(path, size=(300, 200), color=(200, 30, 30), fmt=None):
    fmt = fmt or {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP",
                  "webp": "WEBP"}[path.suffix.lstrip(".").lower()]
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


@pytest.fixture
def three_images(tmp_path):
    return [
        _make_image(tmp_path / "a.png", color=(255, 0, 0)),
        _make_image(tmp_path / "b.png", color=(0, 255, 0)),
        _make_image(tmp_path / "c.png", color=(0, 0, 255)),
    ]


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_images(win, paths):
    with patch("file_manager.select_image_files", return_value=paths):
        win._on_images_to_pdf_select_clicked()
        assert _pump_until(win, lambda: not win._images_to_pdf_import_in_progress)


def _reset_images_to_pdf_state(win):
    win.images_to_pdf_in_progress = False
    win._images_to_pdf_import_in_progress = False
    win.images_to_pdf_files.clear()
    win.images_to_pdf_page_size_var.set(ie.DEFAULT_PAGE_SIZE)
    win.images_to_pdf_margin_var.set(f"{ie.DEFAULT_MARGIN:g}")
    win.images_to_pdf_status_var.set("Status: Ready")
    win._render_images_to_pdf_list()
    win._update_button_states()
    win._update_images_to_pdf_controls_state()
    win.root.update()


@pytest.fixture(autouse=True)
def _clean_images_to_pdf_workspace(window):
    _reset_images_to_pdf_state(window)
    window._select_tool("images_to_pdf")
    window.root.update()
    yield
    _reset_images_to_pdf_state(window)
    window._select_tool("merge_compress")
    window.root.update()


class _GatedCall:
    """Deterministic gate for pausing a background worker mid-call --
    see test_phase9_responsive_processing.py for the original
    introduction of this pattern.
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


def _run_create_pdf(win, output_path, timeout=15):
    with patch("file_manager.save_pdf_file", return_value=output_path):
        win._on_images_to_pdf_execute_clicked()
        assert _pump_until(win, lambda: not win.images_to_pdf_in_progress, timeout)


def _state(widget):
    return str(widget["state"])


def _row_names(win):
    return [f.name for f in win.images_to_pdf_files]


# ---------------------------------------------------------------------------
# 1-3. Registry / navigation / workspace
# ---------------------------------------------------------------------------

def test_images_to_pdf_is_registered_as_available_with_its_original_id():
    import tool_registry

    tool = tool_registry.get_tool("images_to_pdf")
    assert tool is not None
    assert tool.id == "images_to_pdf"
    assert tool.is_available


def test_images_to_pdf_appears_in_navigation(window):
    assert "images_to_pdf" in window.tool_nav_buttons
    assert window.tool_nav_buttons["images_to_pdf"].winfo_exists()


def test_images_to_pdf_is_selectable(window):
    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("images_to_pdf")
    window.root.update()
    assert window.current_tool_id == "images_to_pdf"


def test_selecting_images_to_pdf_opens_its_workspace_not_the_placeholder(window):
    assert window.images_to_pdf_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.watermark_view.winfo_ismapped()


def test_workspace_has_expected_controls(window):
    for attr in [
        "images_to_pdf_select_btn", "images_to_pdf_count_label",
        "images_to_pdf_list_container", "images_to_pdf_clear_btn",
        "images_to_pdf_page_size_radios", "images_to_pdf_margin_entry",
        "images_to_pdf_button", "images_to_pdf_status_label",
        "images_to_pdf_progress_bar", "images_to_pdf_feedback_label",
        "images_to_pdf_error_label",
    ]:
        assert hasattr(window, attr), f"missing widget: {attr}"
    assert set(window.images_to_pdf_page_size_radios) == set(ie.PAGE_SIZE_CHOICES)


def test_workspace_title_and_button_label(window):
    texts = []

    def walk(widget):
        try:
            texts.append(str(widget.cget("text")))
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            walk(child)

    walk(window.images_to_pdf_view)
    joined = " ".join(texts).upper()
    assert "IMAGES" in joined and "PDF" in joined
    assert window.images_to_pdf_button.cget("text") == "CREATE PDF"
    assert window.images_to_pdf_select_btn.cget("text") == "Select Images"


# ---------------------------------------------------------------------------
# Initial state / defaults
# ---------------------------------------------------------------------------

def test_button_disabled_with_no_images(window):
    assert window.images_to_pdf_files == []
    assert _state(window.images_to_pdf_button) == "disabled"
    assert "Select" in window.images_to_pdf_feedback_var.get()


def test_defaults_match_engine_defaults(window):
    assert window.images_to_pdf_page_size_var.get() == "a4"
    assert window.images_to_pdf_margin_var.get() == "36"


def test_clear_all_disabled_with_no_images(window):
    assert _state(window.images_to_pdf_clear_btn) == "disabled"


# ---------------------------------------------------------------------------
# 4-6. Image selection / file rows / ordering
# ---------------------------------------------------------------------------

def test_images_can_be_selected(window, three_images):
    _select_images(window, three_images)

    assert len(window.images_to_pdf_files) == 3
    assert _row_names(window) == ["a.png", "b.png", "c.png"]
    assert "3 images selected" in window.images_to_pdf_count_label.cget("text")


def test_selection_uses_the_image_picker_not_the_pdf_picker(window):
    with patch("file_manager.select_image_files", return_value=[]) as picker, \
         patch("file_manager.select_pdf_files") as pdf_picker:
        window._on_images_to_pdf_select_clicked()
        window.root.update()
    picker.assert_called_once()
    pdf_picker.assert_not_called()


def test_cancelled_picker_does_not_start_a_worker(window):
    with patch("file_manager.select_image_files", return_value=[]):
        with patch("threading.Thread") as thread:
            window._on_images_to_pdf_select_clicked()
    thread.assert_not_called()
    assert not window._images_to_pdf_import_in_progress
    assert window.images_to_pdf_status_var.get() == "Status: Ready"
    assert window.images_to_pdf_files == []


def test_file_rows_appear_for_each_selected_image(window, three_images):
    _select_images(window, three_images)
    row_count = len(window.images_to_pdf_list_container.winfo_children())
    assert row_count == 3


def test_empty_state_message_when_no_images(window):
    text = None
    for child in window.images_to_pdf_list_container.winfo_children():
        text = str(child.cget("text"))
    assert text is not None and "No images added" in text


def test_selection_order_is_preserved_not_sorted(window, tmp_path):
    paths = [
        _make_image(tmp_path / "zeta.png", color=(1, 0, 0)),
        _make_image(tmp_path / "alpha.png", color=(0, 1, 0)),
        _make_image(tmp_path / "mid.png", color=(0, 0, 1)),
    ]
    _select_images(window, paths)
    assert _row_names(window) == ["zeta.png", "alpha.png", "mid.png"]


def test_images_selected_on_a_background_thread(window, three_images):
    seen = []
    original = ie.get_image_info

    def wrapper(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(ie, "get_image_info", side_effect=wrapper):
        _select_images(window, three_images)
    assert seen == [False, False, False]


def test_corrupt_image_selection_shows_warning_but_keeps_good_ones(window, tmp_path):
    good = _make_image(tmp_path / "good.png")
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"NOT AN IMAGE" * 20)

    with patch("file_manager.select_image_files", return_value=[good, bad]):
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            window._on_images_to_pdf_select_clicked()
            assert _pump_until(window, lambda: not window._images_to_pdf_import_in_progress)

    assert mock_warn.called
    assert _row_names(window) == ["good.png"]
    assert _state(window.images_to_pdf_button) == "normal"


def test_all_images_invalid_leaves_button_disabled(window, tmp_path):
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"junk" * 20)
    with patch("file_manager.select_image_files", return_value=[bad]):
        with patch("tkinter.messagebox.showwarning"):
            window._on_images_to_pdf_select_clicked()
            assert _pump_until(window, lambda: not window._images_to_pdf_import_in_progress)
    assert window.images_to_pdf_files == []
    assert _state(window.images_to_pdf_button) == "disabled"


def test_replacing_selection_appends_rather_than_replacing(window, three_images, tmp_path):
    _select_images(window, three_images[:2])
    extra = _make_image(tmp_path / "extra.png", color=(9, 9, 9))
    _select_images(window, [extra])
    assert _row_names(window) == ["a.png", "b.png", "extra.png"]


# ---------------------------------------------------------------------------
# 11. Duplicate selection preserved
# ---------------------------------------------------------------------------

def test_duplicate_image_selection_is_preserved_as_two_rows(window, tmp_path):
    path = _make_image(tmp_path / "dup.png")
    _select_images(window, [path, path])

    assert len(window.images_to_pdf_files) == 2
    assert _row_names(window) == ["dup.png", "dup.png"]
    # Two distinct objects, not the same one referenced twice.
    assert window.images_to_pdf_files[0] is not window.images_to_pdf_files[1]


def test_duplicate_selection_across_two_picks(window, tmp_path):
    path = _make_image(tmp_path / "dup.png")
    _select_images(window, [path])
    _select_images(window, [path])
    assert _row_names(window) == ["dup.png", "dup.png"]


# ---------------------------------------------------------------------------
# 7-10. Move Up / Move Down / Remove / Clear All
# ---------------------------------------------------------------------------

def test_move_up(window, three_images):
    _select_images(window, three_images)
    second = window.images_to_pdf_files[1]
    window._on_move_image_up(second)
    assert _row_names(window) == ["b.png", "a.png", "c.png"]


def test_move_down(window, three_images):
    _select_images(window, three_images)
    first = window.images_to_pdf_files[0]
    window._on_move_image_down(first)
    assert _row_names(window) == ["b.png", "a.png", "c.png"]


def test_move_up_on_first_item_is_a_noop(window, three_images):
    _select_images(window, three_images)
    first = window.images_to_pdf_files[0]
    window._on_move_image_up(first)
    assert _row_names(window) == ["a.png", "b.png", "c.png"]


def test_move_down_on_last_item_is_a_noop(window, three_images):
    _select_images(window, three_images)
    last = window.images_to_pdf_files[-1]
    window._on_move_image_down(last)
    assert _row_names(window) == ["a.png", "b.png", "c.png"]


def test_first_row_up_button_disabled_last_row_down_button_disabled(window, three_images):
    _select_images(window, three_images)
    rows = window.images_to_pdf_list_container.winfo_children()
    first_up = [c for c in rows[0].winfo_children() if str(c.cget("text")) == "\u25b2"][0]
    last_down = [c for c in rows[-1].winfo_children() if str(c.cget("text")) == "\u25bc"][0]
    assert _state(first_up) == "disabled"
    assert _state(last_down) == "disabled"


def test_move_up_down_use_identity_not_value_equality(window, tmp_path):
    """Two rows for the same path must be independently movable -- a
    value-equality-based lookup would be ambiguous between them."""
    path = _make_image(tmp_path / "dup.png")
    _select_images(window, [path, path])
    second_instance = window.images_to_pdf_files[1]

    window._on_move_image_up(second_instance)
    # Still two rows named "dup.png"; the move must not have errored or
    # silently done nothing to the wrong (first) instance.
    assert len(window.images_to_pdf_files) == 2
    assert window.images_to_pdf_files[0] is second_instance


def test_remove_single_image(window, three_images):
    _select_images(window, three_images)
    middle = window.images_to_pdf_files[1]
    window._on_remove_image(middle)
    assert _row_names(window) == ["a.png", "c.png"]


def test_remove_updates_count_label_and_feedback(window, three_images):
    _select_images(window, three_images)
    window._on_remove_image(window.images_to_pdf_files[0])
    window.root.update()
    assert "2 images selected" in window.images_to_pdf_count_label.cget("text")
    assert "2" in window.images_to_pdf_feedback_var.get()


def test_removing_last_image_disables_create_pdf(window, tmp_path):
    path = _make_image(tmp_path / "only.png")
    _select_images(window, [path])
    window._on_remove_image(window.images_to_pdf_files[0])
    window.root.update()
    assert window.images_to_pdf_files == []
    assert _state(window.images_to_pdf_button) == "disabled"
    assert _state(window.images_to_pdf_clear_btn) == "disabled"


def test_remove_uses_identity_removes_correct_duplicate(window, tmp_path):
    path = _make_image(tmp_path / "dup.png")
    _select_images(window, [path, path])
    first_instance = window.images_to_pdf_files[0]
    second_instance = window.images_to_pdf_files[1]

    window._on_remove_image(first_instance)

    assert len(window.images_to_pdf_files) == 1
    assert window.images_to_pdf_files[0] is second_instance


def test_clear_all(window, three_images):
    _select_images(window, three_images)
    window._on_images_to_pdf_clear_clicked()
    window.root.update()
    assert window.images_to_pdf_files == []
    assert _state(window.images_to_pdf_clear_btn) == "disabled"
    assert _state(window.images_to_pdf_button) == "disabled"
    assert "Cleared all images (3 removed)" in window.images_to_pdf_status_var.get()


def test_clear_all_enabled_once_images_present(window, three_images):
    assert _state(window.images_to_pdf_clear_btn) == "disabled"
    _select_images(window, three_images)
    assert _state(window.images_to_pdf_clear_btn) == "normal"


def test_move_remove_clear_blocked_while_busy(window, three_images):
    _select_images(window, three_images)
    window.images_to_pdf_in_progress = True
    try:
        first = window.images_to_pdf_files[0]
        window._on_move_image_down(first)
        window._on_remove_image(first)
        window._on_images_to_pdf_clear_clicked()
        assert _row_names(window) == ["a.png", "b.png", "c.png"]
    finally:
        window.images_to_pdf_in_progress = False


# ---------------------------------------------------------------------------
# 12-13. Page size / margin validation
# ---------------------------------------------------------------------------

def test_page_size_radios_cover_all_three_choices(window):
    assert list(window.images_to_pdf_page_size_radios) == list(ie.PAGE_SIZE_CHOICES)


@pytest.mark.parametrize("page_size", ie.PAGE_SIZE_CHOICES)
def test_each_page_size_can_be_selected(window, three_images, page_size):
    _select_images(window, three_images)
    window.images_to_pdf_page_size_radios[page_size].invoke()
    window.root.update()
    assert window.images_to_pdf_page_size_var.get() == page_size
    assert _state(window.images_to_pdf_button) == "normal"


def test_page_size_radio_labels(window):
    labels = {
        k: str(r.cget("text")) for k, r in window.images_to_pdf_page_size_radios.items()
    }
    assert labels["a4"] == "A4"
    assert labels["letter"] == "Letter"
    assert labels["original"] == "Original Image Ratio"


@pytest.mark.parametrize("bad", ["", "  ", "abc", "-1", "nan", "inf"])
def test_invalid_margin_disables_button(window, three_images, bad):
    _select_images(window, three_images)
    window.images_to_pdf_margin_var.set(bad)
    window.root.update()
    assert _state(window.images_to_pdf_button) == "disabled"
    assert window.images_to_pdf_error_var.get() != ""


@pytest.mark.parametrize("good", ["0", "36", "72.5", " 20 "])
def test_valid_margin_enables_button(window, three_images, good):
    _select_images(window, three_images)
    window.images_to_pdf_margin_var.set("-5")
    assert _state(window.images_to_pdf_button) == "disabled"
    window.images_to_pdf_margin_var.set(good)
    window.root.update()
    assert _state(window.images_to_pdf_button) == "normal"
    assert window.images_to_pdf_error_var.get() == ""


def test_excessive_margin_for_a4_disables_button(window, three_images):
    _select_images(window, three_images)
    base_w, base_h = ie.PAGE_SIZES["a4"]
    window.images_to_pdf_margin_var.set(str(min(base_w, base_h)))
    window.root.update()
    assert _state(window.images_to_pdf_button) == "disabled"
    assert window.images_to_pdf_error_var.get() != ""


def test_large_margin_never_disables_original_ratio(window, three_images):
    _select_images(window, three_images)
    window.images_to_pdf_page_size_radios["original"].invoke()
    window.images_to_pdf_margin_var.set("500")
    window.root.update()
    assert _state(window.images_to_pdf_button) == "normal"


# ---------------------------------------------------------------------------
# 14-15. Enabled / disabled action
# ---------------------------------------------------------------------------

def test_no_images_disables_create_pdf(window):
    window.images_to_pdf_margin_var.set("36")
    window.root.update()
    assert _state(window.images_to_pdf_button) == "disabled"


def test_valid_images_enable_create_pdf(window, three_images):
    _select_images(window, three_images)
    assert _state(window.images_to_pdf_button) == "normal"
    assert "ready to create PDF" in window.images_to_pdf_feedback_var.get()


def test_clicking_disabled_button_never_starts_a_worker(window):
    with patch.object(ie, "images_to_pdf") as engine:
        window.images_to_pdf_button.invoke()
        window.root.update()
    engine.assert_not_called()
    assert not window.images_to_pdf_in_progress


def test_direct_call_with_no_images_is_a_noop(window):
    with patch.object(ie, "images_to_pdf") as engine, \
         patch("file_manager.save_pdf_file") as dialog:
        window._on_images_to_pdf_execute_clicked()
    engine.assert_not_called()
    dialog.assert_not_called()


def test_direct_call_with_invalid_margin_shows_error_and_never_opens_dialog(window, three_images):
    _select_images(window, three_images)
    window.images_to_pdf_margin_var.set("-10")
    window.root.update()

    with patch.object(ie, "images_to_pdf") as engine, \
         patch("file_manager.save_pdf_file") as dialog, \
         patch("tkinter.messagebox.showerror") as error:
        window._on_images_to_pdf_execute_clicked()

    engine.assert_not_called()
    dialog.assert_not_called()
    assert error.called
    assert not window.images_to_pdf_in_progress
    assert len(window.images_to_pdf_files) == 3  # nothing consumed


# ---------------------------------------------------------------------------
# 16-17. Worker
# ---------------------------------------------------------------------------

def test_save_as_dialog_is_opened_on_create_pdf(window, three_images, tmp_path):
    """Images -> PDF, unlike Watermark/Page Numbers/Unlock, DOES open a
    native Save As dialog -- this is the one point of deliberate
    divergence from the auto-named-output convention those tools use.
    """
    _select_images(window, three_images)
    output = tmp_path / "chosen.pdf"
    with patch("file_manager.save_pdf_file", return_value=output) as dialog:
        window._on_images_to_pdf_execute_clicked()
        assert _pump_until(window, lambda: not window.images_to_pdf_in_progress)
    dialog.assert_called_once()
    assert dialog.call_args.kwargs["default_name"] == "images.pdf"
    assert output.exists()


def test_cancelling_save_dialog_does_not_start_a_worker(window, three_images):
    _select_images(window, three_images)
    with patch("file_manager.save_pdf_file", return_value=None):
        with patch.object(ie, "images_to_pdf") as engine:
            window._on_images_to_pdf_execute_clicked()
            window.root.update()
    engine.assert_not_called()
    assert not window.images_to_pdf_in_progress
    assert window.images_to_pdf_status_var.get() == "Status: Ready"
    assert len(window.images_to_pdf_files) == 3  # selection untouched


def test_worker_starts_and_flags_the_operation(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    gate = _GatedCall(ie.images_to_pdf)
    with patch.object(ie, "images_to_pdf", side_effect=gate), \
         patch("file_manager.save_pdf_file", return_value=output):
        window._on_images_to_pdf_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        assert window.images_to_pdf_in_progress is True
        assert window._any_operation_in_progress()
        assert "Creating PDF" in window.images_to_pdf_status_var.get()
        assert str(window.images_to_pdf_progress_bar.cget("mode")) == "indeterminate"

        gate.release()
        assert _pump_until(window, lambda: not window.images_to_pdf_in_progress)

    assert not window._any_operation_in_progress()
    assert str(window.images_to_pdf_progress_bar.cget("mode")) == "determinate"


def test_processing_is_not_on_the_tk_main_thread(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    seen = []
    original = ie.images_to_pdf

    def wrapper(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(ie, "images_to_pdf", side_effect=wrapper):
        _run_create_pdf(window, output)

    assert seen == [False]


def test_button_callback_returns_before_processing_finishes(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    gate = _GatedCall(ie.images_to_pdf)
    with patch.object(ie, "images_to_pdf", side_effect=gate), \
         patch("file_manager.save_pdf_file", return_value=output):
        window._on_images_to_pdf_execute_clicked()
        assert gate.entered.wait(timeout=5)
        assert window.images_to_pdf_in_progress
        gate.release()
        assert _pump_until(window, lambda: not window.images_to_pdf_in_progress)


def test_worker_receives_configured_options_in_displayed_order(window, tmp_path):
    paths = [
        _make_image(tmp_path / "z.png", color=(1, 0, 0)),
        _make_image(tmp_path / "a.png", color=(0, 1, 0)),
    ]
    _select_images(window, paths)
    window.images_to_pdf_page_size_radios["letter"].invoke()
    window.images_to_pdf_margin_var.set("20")
    window.root.update()

    calls = []
    original = ie.images_to_pdf

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    output = tmp_path / "out.pdf"
    with patch.object(ie, "images_to_pdf", side_effect=spy):
        _run_create_pdf(window, output)

    (args, kwargs), = calls
    input_paths, output_arg = args[0], args[1]
    assert input_paths == paths  # displayed order preserved
    assert output_arg == output
    assert kwargs["page_size"] == "letter"
    assert kwargs["margin"] == 20.0
    assert callable(kwargs["progress_callback"])


# ---------------------------------------------------------------------------
# 18. Progress / status updates
# ---------------------------------------------------------------------------

def test_progress_messages_reach_the_status_line(window, three_images, tmp_path):
    _select_images(window, three_images)
    seen = []

    def spy_engine(paths, output, **kwargs):
        kwargs["progress_callback"]("Converting image 1 of 3: a.png")
        deadline = time.time() + 5
        while time.time() < deadline and not any("Converting image" in s for s in seen):
            time.sleep(0.01)
        return output

    def observer():
        seen.append(window.images_to_pdf_status_var.get())
        window.root.after(20, observer)

    output = tmp_path / "out.pdf"
    window.root.after(0, observer)
    with patch.object(ie, "images_to_pdf", side_effect=spy_engine):
        _run_create_pdf(window, output)
    assert any("Converting image" in s for s in seen)


# ---------------------------------------------------------------------------
# 19-20. Success handling
# ---------------------------------------------------------------------------

def test_end_to_end_create_pdf(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "myimages.pdf"
    _run_create_pdf(window, output)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 3

    status = window.images_to_pdf_status_var.get()
    assert "successfully" in status.lower()
    assert "myimages.pdf" in status


def test_success_clears_the_image_list(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    _run_create_pdf(window, output)

    assert window.images_to_pdf_files == []
    assert _state(window.images_to_pdf_clear_btn) == "disabled"
    assert _state(window.images_to_pdf_button) == "disabled"


def test_success_keeps_page_size_and_margin_settings(window, three_images, tmp_path):
    _select_images(window, three_images)
    window.images_to_pdf_page_size_radios["letter"].invoke()
    window.images_to_pdf_margin_var.set("50")
    window.root.update()

    output = tmp_path / "out.pdf"
    _run_create_pdf(window, output)

    assert window.images_to_pdf_page_size_var.get() == "letter"
    assert window.images_to_pdf_margin_var.get() == "50"


def test_source_images_untouched_after_success(window, three_images, tmp_path):
    originals = {p: p.read_bytes() for p in three_images}
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    _run_create_pdf(window, output)

    for p, data in originals.items():
        assert p.exists()
        assert p.read_bytes() == data


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_engine_failure_shows_error_and_restores_ui(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    with patch.object(
        ie, "images_to_pdf", side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_create_pdf(window, output)

    assert mock_error.called
    assert mock_error.call_args.kwargs["message"] == "simulated failure"
    assert "failed" in window.images_to_pdf_status_var.get().lower()
    # The image list is NOT cleared on failure -- the user can fix the
    # configuration and retry without re-selecting everything.
    assert len(window.images_to_pdf_files) == 3
    assert not window.images_to_pdf_in_progress
    assert not window._any_operation_in_progress()
    assert _state(window.images_to_pdf_select_btn) == "normal"
    assert _state(window.images_to_pdf_button) == "normal"


def test_unexpected_worker_exception_recovers_cleanly_without_traceback(
    window, three_images, tmp_path,
):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    with patch.object(ie, "images_to_pdf", side_effect=RuntimeError("totally unexpected")):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_create_pdf(window, output)

    message = mock_error.call_args.kwargs["message"]
    assert "unexpected error" in message.lower()
    assert "totally unexpected" not in message
    assert "Traceback" not in message
    assert not window.images_to_pdf_in_progress
    assert not window._any_operation_in_progress()


def test_real_failure_one_image_deleted_before_run(window, three_images, tmp_path):
    _select_images(window, three_images)
    three_images[1].unlink()
    output = tmp_path / "out.pdf"

    with patch("tkinter.messagebox.showerror") as mock_error:
        _run_create_pdf(window, output)

    assert mock_error.called
    assert not output.exists()
    assert not window._any_operation_in_progress()


def test_no_temp_files_left_after_failure(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    with patch("pymupdf.Document.save", side_effect=OSError(28, "No space left on device")):
        with patch("tkinter.messagebox.showerror"):
            _run_create_pdf(window, output)
    assert not output.exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")] == []


def test_retry_after_failure_succeeds(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    with patch.object(ie, "images_to_pdf", side_effect=pdf_engine.PDFEngineError("nope")):
        with patch("tkinter.messagebox.showerror"):
            _run_create_pdf(window, output)

    _run_create_pdf(window, output)  # same images, still selected

    assert output.exists()
    assert "successfully" in window.images_to_pdf_status_var.get().lower()


# ---------------------------------------------------------------------------
# 21-22. Busy state / control restoration
# ---------------------------------------------------------------------------

def _all_images_to_pdf_controls(win):
    return [
        win.images_to_pdf_select_btn, win.images_to_pdf_clear_btn,
        win.images_to_pdf_margin_entry, win.images_to_pdf_button,
    ] + list(win.images_to_pdf_page_size_radios.values())


def test_all_images_to_pdf_controls_disabled_during_operation_and_others_too(
    window, three_images, tmp_path,
):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    gate = _GatedCall(ie.images_to_pdf)
    with patch.object(ie, "images_to_pdf", side_effect=gate), \
         patch("file_manager.save_pdf_file", return_value=output):
        window._on_images_to_pdf_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        for control in _all_images_to_pdf_controls(window):
            assert _state(control) == "disabled", control
        assert _state(window.merge_only_btn) == "disabled"
        assert _state(window.select_files_btn) == "disabled"
        assert _state(window.watermark_select_btn) == "disabled"
        assert _state(window.watermark_button) == "disabled"
        assert _state(window.page_numbers_select_btn) == "disabled"
        assert _state(window.page_numbers_button) == "disabled"

        # Per-row Move/Remove also disabled while busy.
        for row in window.images_to_pdf_list_container.winfo_children():
            for child in row.winfo_children():
                if str(child.cget("text")) in ("\u25b2", "\u25bc", "Remove"):
                    assert _state(child) == "disabled"

        gate.release()
        assert _pump_until(window, lambda: not window.images_to_pdf_in_progress)


def test_other_tools_cannot_start_while_images_to_pdf_runs(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    gate = _GatedCall(ie.images_to_pdf)
    with patch.object(ie, "images_to_pdf", side_effect=gate), \
         patch("file_manager.save_pdf_file", return_value=output):
        window._on_images_to_pdf_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        with patch("threading.Thread") as thread:
            window._on_page_numbers_execute_clicked()
            window._on_watermark_execute_clicked()
            window._on_merge_only_clicked()
            window._on_images_to_pdf_execute_clicked()  # not re-entrant either
            window._on_images_to_pdf_select_clicked()
        thread.assert_not_called()

        gate.release()
        assert _pump_until(window, lambda: not window.images_to_pdf_in_progress)


def test_images_to_pdf_cannot_start_while_another_tool_runs(window, three_images, tmp_path):
    _select_images(window, three_images)

    pn_src = tmp_path / "pn.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(pn_src)
    doc.close()

    window._select_tool("page_numbers")
    window.root.update()
    with patch("file_manager.select_single_pdf_file", return_value=pn_src):
        window._on_page_numbers_select_file_clicked()
        assert _pump_until(window, lambda: not window._page_numbers_import_in_progress)

    gate = _GatedCall(page_numbers_engine.add_page_numbers)
    with patch.object(page_numbers_engine, "add_page_numbers", side_effect=gate):
        window._on_page_numbers_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        assert window._any_operation_in_progress()
        window._select_tool("images_to_pdf")
        window.root.update()
        assert _state(window.images_to_pdf_button) == "disabled"
        assert _state(window.images_to_pdf_select_btn) == "disabled"

        with patch.object(ie, "images_to_pdf") as engine:
            window._on_images_to_pdf_execute_clicked()
        engine.assert_not_called()
        assert not window.images_to_pdf_in_progress

        gate.release()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    window._select_tool("images_to_pdf")
    window.root.update()
    assert len(window.images_to_pdf_files) == 3
    assert _state(window.images_to_pdf_select_btn) == "normal"
    assert _state(window.images_to_pdf_button) == "normal"


def test_any_operation_predicate_includes_images_to_pdf_flags(window):
    assert not window._any_operation_in_progress()
    window.images_to_pdf_in_progress = True
    assert window._any_operation_in_progress()
    window.images_to_pdf_in_progress = False
    assert not window._any_operation_in_progress()

    window._images_to_pdf_import_in_progress = True
    assert window._any_operation_in_progress()
    window._images_to_pdf_import_in_progress = False
    assert not window._any_operation_in_progress()


def test_import_also_holds_the_global_busy_lock(window, three_images):
    gate = _GatedCall(ie.get_image_info)
    with patch("file_manager.select_image_files", return_value=three_images):
        with patch.object(ie, "get_image_info", side_effect=gate):
            window._on_images_to_pdf_select_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert window._images_to_pdf_import_in_progress
            assert window._any_operation_in_progress()
            assert _state(window.images_to_pdf_select_btn) == "disabled"
            assert _state(window.merge_only_btn) == "disabled"
            assert "Validating" in window.images_to_pdf_status_var.get()

            gate.release()
            assert _pump_until(window, lambda: not window._images_to_pdf_import_in_progress)

    assert not window._any_operation_in_progress()


def test_controls_restored_after_success(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    _run_create_pdf(window, output)

    assert not window._any_operation_in_progress()
    assert _state(window.images_to_pdf_select_btn) == "normal"
    assert _state(window.images_to_pdf_margin_entry) == "normal"
    for radio in window.images_to_pdf_page_size_radios.values():
        assert _state(radio) == "normal"
    assert _state(window.images_to_pdf_button) == "disabled"  # list now empty
    assert _state(window.images_to_pdf_clear_btn) == "disabled"

    # Every OTHER tool is usable again too.
    assert _state(window.select_files_btn) == "normal"
    assert _state(window.watermark_select_btn) == "normal"
    assert _state(window.page_numbers_select_btn) == "normal"
    assert str(window.images_to_pdf_progress_bar.cget("mode")) == "determinate"


def test_controls_restored_after_failure(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    with patch.object(ie, "images_to_pdf", side_effect=pdf_engine.PDFEngineError("x")):
        with patch("tkinter.messagebox.showerror"):
            _run_create_pdf(window, output)

    assert _state(window.images_to_pdf_select_btn) == "normal"
    assert _state(window.images_to_pdf_button) == "normal"
    assert _state(window.images_to_pdf_clear_btn) == "normal"
    assert _state(window.watermark_select_btn) == "normal"
    assert str(window.images_to_pdf_progress_bar.cget("mode")) == "determinate"


@pytest.mark.parametrize("select_handler,import_flag,tool_id", [
    ("_on_watermark_select_file_clicked", "_watermark_import_in_progress", "watermark"),
    ("_on_page_numbers_select_file_clicked", "_page_numbers_import_in_progress", "page_numbers"),
])
def test_other_tools_import_restores_images_to_pdf_controls(
    window, select_handler, import_flag, tool_id, tmp_path,
):
    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(src)
    doc.close()

    window._select_tool(tool_id)
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=src), \
         patch("tkinter.messagebox.showerror"):
        getattr(window, select_handler)()
        assert getattr(window, import_flag)
        window.root.update()
        assert _state(window.images_to_pdf_select_btn) == "disabled"
        assert _pump_until(window, lambda: not getattr(window, import_flag))

    assert not window._any_operation_in_progress()
    assert _state(window.images_to_pdf_select_btn) == "normal"
    assert _state(window.images_to_pdf_margin_entry) == "normal"


def test_merge_compress_import_restores_images_to_pdf_controls(window, tmp_path):
    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    src = tmp_path / "s.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(src)
    doc.close()

    with patch("file_manager.select_pdf_files", return_value=[src]):
        window._on_select_files_clicked()
        window.root.update()
        assert _state(window.images_to_pdf_select_btn) == "disabled"
        assert _pump_until(window, lambda: not window._import_in_progress)

    assert _state(window.images_to_pdf_select_btn) == "normal"
    window._on_clear_all_clicked()
    window.root.update()


# ---------------------------------------------------------------------------
# 24. Existing tools remain functional
# ---------------------------------------------------------------------------

def test_images_to_pdf_select_then_switch_leaves_other_tools_usable(window, three_images):
    _select_images(window, three_images)
    for tool_id, attr in [
        ("watermark", "watermark_select_btn"),
        ("page_numbers", "page_numbers_select_btn"),
        ("unlock", "unlock_select_btn"),
        ("merge_compress", "select_files_btn"),
    ]:
        window._select_tool(tool_id)
        window.root.update()
        assert _state(getattr(window, attr)) == "normal", tool_id


def test_existing_merge_compress_workspace_remains_functional(window, three_images, tmp_path):
    _select_images(window, three_images)

    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    for p, n in ((a, 2), (b, 3)):
        doc = pymupdf.open()
        for _ in range(n):
            doc.new_page()
        doc.save(p)
        doc.close()

    with patch("file_manager.select_pdf_files", return_value=[a, b]):
        window._on_select_files_clicked()
        assert _pump_until(window, lambda: not window._import_in_progress)

    output = tmp_path / "merged.pdf"
    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)

    with pymupdf.open(output) as doc:
        assert doc.page_count == 5

    window._on_clear_all_clicked()
    window.root.update()


def test_existing_watermark_workspace_remains_functional(window, tmp_path):
    import watermark_engine

    src = tmp_path / "s.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(src)
    doc.close()

    window._select_tool("watermark")
    window.root.update()
    window.watermark_source = None
    window._update_watermark_source_label()
    window.watermark_text_var.set(watermark_engine.DEFAULT_TEXT)

    with patch("file_manager.select_single_pdf_file", return_value=src):
        window._on_watermark_select_file_clicked()
        assert _pump_until(window, lambda: not window._watermark_import_in_progress)

    window._on_watermark_execute_clicked()
    assert _pump_until(window, lambda: not window.watermark_in_progress)

    output = tmp_path / "s_watermarked.pdf"
    assert output.exists()
    # ...and images-to-pdf is usable right after.
    assert _state(window.images_to_pdf_select_btn) == "normal"


def test_converting_images_then_merging_the_result(window, three_images, tmp_path):
    """Two tools in a row: convert images to a PDF, then merge that PDF
    with another one."""
    _select_images(window, three_images)
    converted = tmp_path / "converted.pdf"
    _run_create_pdf(window, converted)

    other = tmp_path / "other.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(other)
    doc.close()

    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    with patch("file_manager.select_pdf_files", return_value=[converted, other]):
        window._on_select_files_clicked()
        assert _pump_until(window, lambda: not window._import_in_progress)

    merged = tmp_path / "merged.pdf"
    with patch("file_manager.save_pdf_file", return_value=merged):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)

    with pymupdf.open(merged) as doc:
        assert doc.page_count == 5  # 3 image pages + 2 from other.pdf

    window._on_clear_all_clicked()
    window.root.update()


# ---------------------------------------------------------------------------
# 25. Scrollable workspace integration
# ---------------------------------------------------------------------------

def test_images_to_pdf_view_lives_in_the_scrollable_container(window):
    assert window.images_to_pdf_view.winfo_parent() == str(window.workspace_container)
    assert window.images_to_pdf_view.winfo_ismapped()
    window.root.update_idletasks()


def test_switching_tools_repeatedly_does_not_break_the_workspace(window):
    for _ in range(3):
        for tool_id in ("page_numbers", "images_to_pdf", "watermark", "images_to_pdf", "merge_compress"):
            window._select_tool(tool_id)
            window.root.update()
    window._select_tool("images_to_pdf")
    window.root.update()
    assert window.images_to_pdf_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()


def test_selections_and_settings_survive_switching_away_and_back(window, three_images):
    _select_images(window, three_images)
    window.images_to_pdf_page_size_radios["letter"].invoke()
    window.images_to_pdf_margin_var.set("50")
    window.root.update()

    for tool_id in ("unlock", "protect", "rotate", "watermark", "page_numbers", "merge_compress"):
        window._select_tool(tool_id)
        window.root.update()
    window._select_tool("images_to_pdf")
    window.root.update()

    assert _row_names(window) == ["a.png", "b.png", "c.png"]
    assert window.images_to_pdf_page_size_var.get() == "letter"
    assert window.images_to_pdf_margin_var.get() == "50"
    assert _state(window.images_to_pdf_button) == "normal"


# ---------------------------------------------------------------------------
# 26. Repeated conversion
# ---------------------------------------------------------------------------

def test_repeated_conversions_on_different_images(window, tmp_path):
    for i in range(3):
        path = _make_image(tmp_path / f"img{i}.png", color=(i * 10, 0, 0))
        _select_images(window, [path])
        output = tmp_path / f"out{i}.pdf"
        _run_create_pdf(window, output)

        assert output.exists()
        with pymupdf.open(output) as doc:
            assert doc.page_count == 1
        assert window.images_to_pdf_files == []
        assert not window._any_operation_in_progress()


def test_repeated_conversions_keep_page_size_setting(window, tmp_path):
    window.images_to_pdf_page_size_radios["original"].invoke()
    for i in range(2):
        path = _make_image(tmp_path / f"img{i}.png")
        _select_images(window, [path])
        output = tmp_path / f"out{i}.pdf"
        _run_create_pdf(window, output)
    assert window.images_to_pdf_page_size_var.get() == "original"


# ---------------------------------------------------------------------------
# 27. Tk thread discipline
# ---------------------------------------------------------------------------

def test_no_tk_calls_from_worker_threads_during_a_full_run(window, three_images, tmp_path):
    offenders = []
    main = threading.main_thread()

    real_set = tk.Variable.set
    real_configure = tk.Misc._configure

    def spy_set(self, value):
        if threading.current_thread() is not main:
            offenders.append(("Variable.set", threading.current_thread().name))
        return real_set(self, value)

    def spy_configure(self, cmd, cnf, kw):
        if threading.current_thread() is not main:
            offenders.append(("configure", threading.current_thread().name))
        return real_configure(self, cmd, cnf, kw)

    output = tmp_path / "out.pdf"
    with patch.object(tk.Variable, "set", spy_set), \
         patch.object(tk.Misc, "_configure", spy_configure):
        _select_images(window, three_images)
        _run_create_pdf(window, output)

    assert offenders == []


def test_result_handlers_run_on_the_main_thread(window, three_images, tmp_path):
    threads = []
    orig_result = window._apply_images_to_pdf_result
    orig_import = window._apply_images_to_pdf_import_results

    def spy_result(item):
        threads.append(threading.current_thread() is threading.main_thread())
        return orig_result(item)

    def spy_import(results):
        threads.append(threading.current_thread() is threading.main_thread())
        return orig_import(results)

    output = tmp_path / "out.pdf"
    with patch.object(window, "_apply_images_to_pdf_result", spy_result), \
         patch.object(window, "_apply_images_to_pdf_import_results", spy_import):
        _select_images(window, three_images)
        _run_create_pdf(window, output)

    assert threads == [True, True]


def test_result_handlers_refuse_to_run_off_the_main_thread(window):
    errors = []

    def attempt(handler, payload):
        try:
            handler(payload)
        except RuntimeError as exc:
            errors.append(str(exc))

    for handler, payload in (
        (window._apply_images_to_pdf_result, {"success": True, "output_path": Path("x.pdf")}),
        (window._apply_images_to_pdf_import_results, {"added": [], "errors": []}),
    ):
        thread = threading.Thread(target=attempt, args=(handler, payload))
        thread.start()
        thread.join(timeout=5)

    assert len(errors) == 2
    assert all("non-main thread" in e for e in errors)
    assert not window._any_operation_in_progress()


def test_worker_only_communicates_through_the_queue(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"
    puts = []
    real_put = window._images_to_pdf_queue.put

    def spy_put(item, *args, **kwargs):
        puts.append(item["type"])
        return real_put(item, *args, **kwargs)

    with patch.object(window._images_to_pdf_queue, "put", spy_put):
        _run_create_pdf(window, output)

    assert puts[-1] == "done"
    assert set(puts) <= {"progress", "done"}
    assert "progress" in puts


def test_gui_remains_responsive_during_conversion(window, three_images, tmp_path):
    _select_images(window, three_images)
    output = tmp_path / "out.pdf"

    gate = _GatedCall(ie.images_to_pdf)
    with patch.object(ie, "images_to_pdf", side_effect=gate), \
         patch("file_manager.save_pdf_file", return_value=output):
        window._on_images_to_pdf_execute_clicked()
        assert gate.entered.wait(timeout=5)

        update_count = 0
        for _ in range(20):
            window.root.update()
            update_count += 1
        assert window.images_to_pdf_in_progress
        assert update_count == 20

        window._select_tool("merge_compress")
        window.root.update()
        window._select_tool("images_to_pdf")
        window.root.update()

        gate.release()
        assert _pump_until(window, lambda: not window.images_to_pdf_in_progress)
