"""
test_ui_pdf_to_images.py

UI-level tests for the PDF -> Images workspace (Phase 23): navigation,
single-file import, page count display, the All Pages/Selected Pages
mode toggle, live validation of the page range, format, DPI, and JPEG
quality, output-folder selection (the native folder dialog, chosen once
and persisted -- not opened per click), the CONVERT TO IMAGES action
end-to-end through the real UI, background-thread/responsiveness/busy-
lock behavior across every available tool, Tk main-thread discipline,
source safety, and cross-tool regression checks.

Uses the same deterministic threading.Event-based _GatedCall pattern as
test_ui_watermark.py/test_ui_images_to_pdf.py (duplicated here per this
project's established "keep each test file self-contained" convention)
and the shared session-scoped `window` fixture from tests/conftest.py.
"""

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import page_numbers_engine
import pdf_engine
import pdf_to_images_engine as pe


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_pdf(path, pages=5, size=(200, 300)):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((20, 50), f"PAGE{i + 1}", fontsize=14)
    doc.save(path)
    doc.close()
    return Path(path)


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_source(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_pdf_to_images_select_file_clicked()
        assert _pump_until(win, lambda: not win._pdf_to_images_import_in_progress)


def _choose_folder(win, folder):
    with patch("file_manager.select_output_folder", return_value=folder):
        win._on_pdf_to_images_choose_folder_clicked()
    win.root.update()


def _reset_pdf_to_images_state(win):
    win.pdf_to_images_in_progress = False
    win._pdf_to_images_import_in_progress = False
    win.pdf_to_images_source = None
    win._update_pdf_to_images_source_label()
    win.pdf_to_images_all_pages_var.set(True)
    win.pdf_to_images_selection_var.set("")
    win.pdf_to_images_format_var.set(pe.DEFAULT_FORMAT)
    win.pdf_to_images_dpi_var.set(f"{pe.DEFAULT_DPI:g}")
    win.pdf_to_images_jpeg_quality_var.set(str(pe.DEFAULT_JPEG_QUALITY))
    win.pdf_to_images_output_dir = None
    win._update_pdf_to_images_folder_label()
    win.pdf_to_images_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_pdf_to_images_controls_state()
    win.root.update()


@pytest.fixture(autouse=True)
def _clean_pdf_to_images_workspace(window):
    _reset_pdf_to_images_state(window)
    window._select_tool("pdf_to_images")
    window.root.update()
    yield
    _reset_pdf_to_images_state(window)
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


def _run_convert(win, timeout=15):
    win._on_pdf_to_images_execute_clicked()
    assert _pump_until(win, lambda: not win.pdf_to_images_in_progress, timeout)


def _state(widget):
    return str(widget["state"])


# ---------------------------------------------------------------------------
# 1-3. Registry / navigation / workspace
# ---------------------------------------------------------------------------

def test_pdf_to_images_is_registered_as_available_with_its_id():
    import tool_registry

    tool = tool_registry.get_tool("pdf_to_images")
    assert tool is not None
    assert tool.id == "pdf_to_images"
    assert tool.is_available


def test_pdf_to_images_appears_in_navigation(window):
    assert "pdf_to_images" in window.tool_nav_buttons
    assert window.tool_nav_buttons["pdf_to_images"].winfo_exists()


def test_pdf_to_images_is_selectable(window):
    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("pdf_to_images")
    window.root.update()
    assert window.current_tool_id == "pdf_to_images"


def test_selecting_pdf_to_images_opens_its_workspace_not_the_placeholder(window):
    assert window.pdf_to_images_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.images_to_pdf_view.winfo_ismapped()


def test_workspace_has_expected_controls(window):
    for attr in [
        "pdf_to_images_select_btn", "pdf_to_images_source_label",
        "pdf_to_images_all_pages_radio", "pdf_to_images_selected_pages_radio",
        "pdf_to_images_selection_entry", "pdf_to_images_format_radios",
        "pdf_to_images_dpi_combo", "pdf_to_images_quality_combo",
        "pdf_to_images_folder_btn", "pdf_to_images_folder_label",
        "pdf_to_images_button", "pdf_to_images_status_label",
        "pdf_to_images_progress_bar", "pdf_to_images_feedback_label",
        "pdf_to_images_error_label",
    ]:
        assert hasattr(window, attr), f"missing widget: {attr}"
    assert set(window.pdf_to_images_format_radios) == set(pe.FORMAT_CHOICES)


def test_workspace_title_and_button_label(window):
    texts = []

    def walk(widget):
        try:
            texts.append(str(widget.cget("text")))
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            walk(child)

    walk(window.pdf_to_images_view)
    joined = " ".join(texts).upper()
    assert "PDF" in joined and "IMAGES" in joined
    assert window.pdf_to_images_button.cget("text") == "CONVERT TO IMAGES"
    assert window.pdf_to_images_select_btn.cget("text") == "Select PDF File"


# ---------------------------------------------------------------------------
# Initial state / defaults
# ---------------------------------------------------------------------------

def test_button_disabled_with_no_source(window):
    assert window.pdf_to_images_source is None
    assert _state(window.pdf_to_images_button) == "disabled"
    assert "Select a PDF" in window.pdf_to_images_feedback_var.get()


def test_defaults_match_engine_defaults(window):
    assert window.pdf_to_images_all_pages_var.get() is True
    assert window.pdf_to_images_format_var.get() == "png"
    assert window.pdf_to_images_dpi_var.get() == "150"
    assert window.pdf_to_images_jpeg_quality_var.get() == "90"
    assert window.pdf_to_images_output_dir is None


def test_jpeg_quality_disabled_for_default_png_format(window):
    assert _state(window.pdf_to_images_quality_combo) == "disabled"


# ---------------------------------------------------------------------------
# 4-5. PDF selection / page count display
# ---------------------------------------------------------------------------

def test_single_pdf_can_be_selected(window, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=6)
    _select_source(window, src)

    assert window.pdf_to_images_source is not None
    assert window.pdf_to_images_source.name == "document.pdf"
    assert window.pdf_to_images_source.page_count == 6
    label = window.pdf_to_images_source_label.cget("text")
    assert "document.pdf" in label
    assert "6" in label


def test_page_count_reflected_in_feedback(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=9)
    _select_source(window, src)
    assert "All (9)" in window.pdf_to_images_feedback_var.get()


def test_cancelled_picker_does_not_start_a_worker(window):
    with patch("file_manager.select_single_pdf_file", return_value=None):
        with patch("threading.Thread") as thread:
            window._on_pdf_to_images_select_file_clicked()
    thread.assert_not_called()
    assert not window._pdf_to_images_import_in_progress
    assert window.pdf_to_images_status_var.get() == "Status: Ready"
    assert window.pdf_to_images_source is None


def test_selected_file_is_read_on_a_background_thread(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    seen = []
    original = pdf_engine.get_pdf_info

    def wrapper(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(pdf_engine, "get_pdf_info", side_effect=wrapper):
        _select_source(window, src)
    assert seen == [False]


def test_corrupt_file_selection_shows_error(window, tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_pdf_to_images_select_file_clicked()
            assert _pump_until(window, lambda: not window._pdf_to_images_import_in_progress)

    assert mock_error.called
    assert window.pdf_to_images_source is None
    assert _state(window.pdf_to_images_button) == "disabled"


def test_encrypted_file_selection_is_rejected(window, tmp_path):
    protected = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
              user_pw="pw", owner_pw="ownerpw")
    doc.close()

    with patch("file_manager.select_single_pdf_file", return_value=protected):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_pdf_to_images_select_file_clicked()
            assert _pump_until(window, lambda: not window._pdf_to_images_import_in_progress)

    assert mock_error.called
    assert "password" in mock_error.call_args.kwargs["message"].lower()
    assert window.pdf_to_images_source is None


def test_replacing_the_selected_file(window, tmp_path):
    first = _make_pdf(tmp_path / "first.pdf", pages=3)
    second = _make_pdf(tmp_path / "second.pdf", pages=7)
    _select_source(window, first)
    _select_source(window, second)
    assert window.pdf_to_images_source.name == "second.pdf"
    assert window.pdf_to_images_source.page_count == 7


# ---------------------------------------------------------------------------
# 6-9. Page mode / range visibility / validation
# ---------------------------------------------------------------------------

def test_all_pages_mode_disables_range_entry(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)
    assert window.pdf_to_images_all_pages_var.get() is True
    assert _state(window.pdf_to_images_selection_entry) == "disabled"


def test_selected_pages_mode_enables_range_entry(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)
    window.pdf_to_images_all_pages_var.set(False)
    window._update_pdf_to_images_mode_controls()
    assert _state(window.pdf_to_images_selection_entry) == "normal"


def test_selected_pages_empty_range_disables_button(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)
    window.pdf_to_images_all_pages_var.set(False)
    window._update_pdf_to_images_mode_controls()
    window.root.update()
    assert _state(window.pdf_to_images_button) == "disabled"
    assert "Enter pages" in window.pdf_to_images_feedback_var.get()


@pytest.mark.parametrize("text,count", [
    ("1-3", 3), ("1,3,5", 3), ("2-4", 3), (" 1 , 2 ", 2), ("1-3,2-4", 4),
])
def test_valid_page_range_enables_button(window, tmp_path, text, count):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    window.pdf_to_images_all_pages_var.set(False)
    window._update_pdf_to_images_mode_controls()
    window.pdf_to_images_selection_var.set(text)
    window.root.update()

    assert window.pdf_to_images_error_var.get() == ""
    assert _state(window.pdf_to_images_button) == "normal"
    assert f"{count}" in window.pdf_to_images_feedback_var.get()


@pytest.mark.parametrize("text", ["0", "abc", "7", "999", "3-1", "1,,2", "-2"])
def test_invalid_page_range_disables_button(window, tmp_path, text):
    src = _make_pdf(tmp_path / "d.pdf", pages=5)
    _select_source(window, src)
    window.pdf_to_images_all_pages_var.set(False)
    window._update_pdf_to_images_mode_controls()
    window.pdf_to_images_selection_var.set(text)
    window.root.update()
    assert window.pdf_to_images_error_var.get() != ""
    assert _state(window.pdf_to_images_button) == "disabled"


# ---------------------------------------------------------------------------
# 10-13. Format / JPEG quality visibility / DPI selection
# ---------------------------------------------------------------------------

def test_png_selection(window):
    window.pdf_to_images_format_radios["png"].invoke()
    assert window.pdf_to_images_format_var.get() == "png"
    assert _state(window.pdf_to_images_quality_combo) == "disabled"


def test_jpeg_selection_enables_quality_combo(window):
    window.pdf_to_images_format_radios["jpeg"].invoke()
    assert window.pdf_to_images_format_var.get() == "jpeg"
    assert _state(window.pdf_to_images_quality_combo) == "normal"


def test_switching_back_to_png_disables_quality_combo_again(window):
    window.pdf_to_images_format_radios["jpeg"].invoke()
    assert _state(window.pdf_to_images_quality_combo) == "normal"
    window.pdf_to_images_format_radios["png"].invoke()
    assert _state(window.pdf_to_images_quality_combo) == "disabled"


def test_format_radio_labels(window):
    labels = {k: str(r.cget("text")) for k, r in window.pdf_to_images_format_radios.items()}
    assert labels["png"] == "PNG"
    assert labels["jpeg"] == "JPEG"


def test_invalid_jpeg_quality_disables_button_only_when_jpeg_selected(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    window.pdf_to_images_jpeg_quality_var.set("999")
    window.root.update()
    assert _state(window.pdf_to_images_button) == "normal"  # PNG selected: irrelevant

    window.pdf_to_images_format_radios["jpeg"].invoke()
    window.root.update()
    assert _state(window.pdf_to_images_button) == "disabled"
    assert window.pdf_to_images_error_var.get() != ""


def test_valid_jpeg_quality_enables_button(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    window.pdf_to_images_format_radios["jpeg"].invoke()
    window.pdf_to_images_jpeg_quality_var.set("85")
    window.root.update()
    assert _state(window.pdf_to_images_button) == "normal"


@pytest.mark.parametrize("dpi", [str(d) for d in pe.DPI_CHOICES])
def test_each_dpi_preset_can_be_selected(window, tmp_path, dpi):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    window.pdf_to_images_dpi_combo.set(dpi)
    window.root.update()
    assert window.pdf_to_images_dpi_var.get() == dpi
    assert _state(window.pdf_to_images_button) == "normal"


def test_dpi_combobox_offers_the_recommended_presets(window):
    values = list(window.pdf_to_images_dpi_combo.cget("values"))
    assert values == [str(d) for d in pe.DPI_CHOICES]


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "601", "nan", "inf"])
def test_invalid_dpi_disables_button(window, tmp_path, bad):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    window.pdf_to_images_dpi_var.set(bad)
    window.root.update()
    assert _state(window.pdf_to_images_button) == "disabled"
    assert window.pdf_to_images_error_var.get() != ""


def test_custom_typed_dpi_is_accepted(window, tmp_path):
    """The DPI combobox is editable -- a value outside the preset list
    but within the valid range must still be accepted."""
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    window.pdf_to_images_dpi_var.set("120")
    window.root.update()
    assert _state(window.pdf_to_images_button) == "normal"


# ---------------------------------------------------------------------------
# 14. Output folder selection
# ---------------------------------------------------------------------------

def test_output_folder_selection(window, tmp_path):
    folder = tmp_path / "myoutput"
    _choose_folder(window, folder)
    assert window.pdf_to_images_output_dir == folder
    assert str(folder) in window.pdf_to_images_folder_label.cget("text")


def test_cancelling_folder_dialog_changes_nothing(window):
    with patch("file_manager.select_output_folder", return_value=None):
        window._on_pdf_to_images_choose_folder_clicked()
    assert window.pdf_to_images_output_dir is None
    assert "No output folder" in window.pdf_to_images_folder_label.cget("text")


def test_folder_dialog_ignored_while_busy(window, tmp_path):
    window.pdf_to_images_in_progress = True
    try:
        with patch("file_manager.select_output_folder") as dialog:
            window._on_pdf_to_images_choose_folder_clicked()
        dialog.assert_not_called()
    finally:
        window.pdf_to_images_in_progress = False


def test_no_output_folder_disables_action_even_with_valid_source(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)
    assert window.pdf_to_images_output_dir is None
    assert _state(window.pdf_to_images_button) == "disabled"
    assert "output folder" in window.pdf_to_images_feedback_var.get().lower()


def test_choosing_a_folder_after_source_enables_action(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)
    assert _state(window.pdf_to_images_button) == "disabled"
    _choose_folder(window, tmp_path / "out")
    assert _state(window.pdf_to_images_button) == "normal"


# ---------------------------------------------------------------------------
# 15-20. Enabled / disabled action
# ---------------------------------------------------------------------------

def test_no_pdf_disables_action(window, tmp_path):
    _choose_folder(window, tmp_path / "out")
    assert _state(window.pdf_to_images_button) == "disabled"


def test_valid_configuration_enables_action(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    assert _state(window.pdf_to_images_button) == "normal"
    assert "ready to convert" in window.pdf_to_images_feedback_var.get()


def test_clicking_disabled_button_never_starts_a_worker(window):
    with patch.object(pe, "render_pdf_to_images") as engine:
        window.pdf_to_images_button.invoke()
        window.root.update()
    engine.assert_not_called()
    assert not window.pdf_to_images_in_progress


def test_direct_call_with_no_source_is_a_noop(window, tmp_path):
    _choose_folder(window, tmp_path / "out")
    with patch.object(pe, "render_pdf_to_images") as engine:
        window._on_pdf_to_images_execute_clicked()
    engine.assert_not_called()


def test_direct_call_with_no_folder_is_a_noop(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)
    with patch.object(pe, "render_pdf_to_images") as engine:
        window._on_pdf_to_images_execute_clicked()
    engine.assert_not_called()


@pytest.mark.parametrize("setup", [
    lambda w: w.pdf_to_images_dpi_var.set("0"),
    lambda w: w.pdf_to_images_jpeg_quality_var.set("999") or w.pdf_to_images_format_var.set("jpeg"),
    lambda w: (w.pdf_to_images_all_pages_var.set(False), w.pdf_to_images_selection_var.set("99")),
])
def test_direct_call_with_invalid_input_does_not_launch_worker(window, tmp_path, setup):
    src = _make_pdf(tmp_path / "d.pdf", pages=5)
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    setup(window)
    window.root.update()

    with patch.object(pe, "render_pdf_to_images") as engine, \
         patch("threading.Thread") as thread, \
         patch("tkinter.messagebox.showerror") as error:
        window._on_pdf_to_images_execute_clicked()

    engine.assert_not_called()
    thread.assert_not_called()
    assert error.called
    assert not window.pdf_to_images_in_progress
    assert window.pdf_to_images_source is not None


# ---------------------------------------------------------------------------
# 21-23. Worker / progress
# ---------------------------------------------------------------------------

def test_worker_starts_and_flags_the_operation(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    gate = _GatedCall(pe.render_pdf_to_images)
    with patch.object(pe, "render_pdf_to_images", side_effect=gate):
        window._on_pdf_to_images_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        assert window.pdf_to_images_in_progress is True
        assert window._any_operation_in_progress()
        assert "Rendering" in window.pdf_to_images_status_var.get()
        assert str(window.pdf_to_images_progress_bar.cget("mode")) == "indeterminate"

        gate.release()
        assert _pump_until(window, lambda: not window.pdf_to_images_in_progress)

    assert not window._any_operation_in_progress()
    assert str(window.pdf_to_images_progress_bar.cget("mode")) == "determinate"


def test_processing_is_not_on_the_tk_main_thread(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    seen = []
    original = pe.render_pdf_to_images

    def wrapper(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(pe, "render_pdf_to_images", side_effect=wrapper):
        _run_convert(window)

    assert seen == [False]


def test_worker_receives_configured_options(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=5)
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    window.pdf_to_images_all_pages_var.set(False)
    window._update_pdf_to_images_mode_controls()
    window.pdf_to_images_selection_var.set("1-2,4")
    window.pdf_to_images_format_radios["jpeg"].invoke()
    window.pdf_to_images_dpi_var.set("200")
    window.pdf_to_images_jpeg_quality_var.set("77")
    window.root.update()

    calls = []
    original = pe.render_pdf_to_images

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    with patch.object(pe, "render_pdf_to_images", side_effect=spy):
        _run_convert(window)

    (args, kwargs), = calls
    source, output_dir, indices = args
    assert source == src
    assert output_dir == out
    assert indices == [0, 1, 3]
    assert kwargs["image_format"] == "jpeg"
    assert kwargs["dpi"] == 200.0
    assert kwargs["jpeg_quality"] == 77
    assert callable(kwargs["progress_callback"])


def test_all_pages_mode_passes_none_to_the_engine(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    calls = []
    original = pe.render_pdf_to_images

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    with patch.object(pe, "render_pdf_to_images", side_effect=spy):
        _run_convert(window)
    assert calls[0][2] is None


def test_progress_messages_reach_the_status_line(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    seen = []

    def spy_engine(source, output_dir, indices, **kwargs):
        kwargs["progress_callback"]("Rendering page 1 of 5")
        deadline = time.time() + 5
        while time.time() < deadline and not any("Rendering page" in s for s in seen):
            time.sleep(0.01)
        return {"output_paths": [], "total_images": 0}

    def observer():
        seen.append(window.pdf_to_images_status_var.get())
        window.root.after(20, observer)

    window.root.after(0, observer)
    with patch.object(pe, "render_pdf_to_images", side_effect=spy_engine):
        _run_convert(window)
    assert any("Rendering page" in s for s in seen)


# ---------------------------------------------------------------------------
# 24-26. Success / failure / output info
# ---------------------------------------------------------------------------

def test_end_to_end_all_pages(window, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=4)
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    _run_convert(window)

    files = sorted(out.glob("*.png"))
    assert len(files) == 4
    status = window.pdf_to_images_status_var.get()
    assert "4 images" in status
    assert str(out) in status


def test_end_to_end_selected_pages(window, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=10)
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    window.pdf_to_images_all_pages_var.set(False)
    window._update_pdf_to_images_mode_controls()
    window.pdf_to_images_selection_var.set("3-5,8")
    window.root.update()

    _run_convert(window)

    names = sorted(p.name for p in out.glob("*.png"))
    assert names == [
        "document_page_003.png", "document_page_004.png",
        "document_page_005.png", "document_page_008.png",
    ]


def test_end_to_end_jpeg_output(window, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=2)
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    window.pdf_to_images_format_radios["jpeg"].invoke()
    window.root.update()

    _run_convert(window)

    files = sorted(out.glob("*.jpg"))
    assert len(files) == 2


def test_success_clears_source_but_keeps_folder_and_settings(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    window.pdf_to_images_format_radios["jpeg"].invoke()
    window.pdf_to_images_dpi_var.set("200")
    window.root.update()

    _run_convert(window)

    assert window.pdf_to_images_source is None
    assert "No file selected" in window.pdf_to_images_source_label.cget("text")
    assert window.pdf_to_images_output_dir == out
    assert window.pdf_to_images_format_var.get() == "jpeg"
    assert window.pdf_to_images_dpi_var.get() == "200"


def test_source_untouched_after_success(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    before = src.read_bytes()
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    _run_convert(window)
    assert src.read_bytes() == before


def test_engine_failure_shows_error_and_restores_ui(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    with patch.object(
        pe, "render_pdf_to_images",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_convert(window)

    assert mock_error.called
    assert mock_error.call_args.kwargs["message"] == "simulated failure"
    assert "failed" in window.pdf_to_images_status_var.get().lower()
    assert window.pdf_to_images_source is not None  # not cleared on failure
    assert not window.pdf_to_images_in_progress
    assert not window._any_operation_in_progress()
    assert _state(window.pdf_to_images_select_btn) == "normal"
    assert _state(window.pdf_to_images_button) == "normal"


def test_unexpected_worker_exception_recovers_cleanly_without_traceback(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    with patch.object(pe, "render_pdf_to_images", side_effect=RuntimeError("totally unexpected")):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_convert(window)

    message = mock_error.call_args.kwargs["message"]
    assert "unexpected error" in message.lower()
    assert "totally unexpected" not in message
    assert "Traceback" not in message
    assert not window._any_operation_in_progress()


def test_retry_after_failure_succeeds(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)

    with patch.object(
        pe, "render_pdf_to_images", side_effect=pdf_engine.PDFEngineError("nope"),
    ):
        with patch("tkinter.messagebox.showerror"):
            _run_convert(window)

    _run_convert(window)

    assert any(out.glob("*.png"))
    assert "created" in window.pdf_to_images_status_var.get().lower()


# ---------------------------------------------------------------------------
# 27-29. Busy state / control restoration
# ---------------------------------------------------------------------------

def _all_pdf_to_images_controls(win):
    return [
        win.pdf_to_images_select_btn, win.pdf_to_images_all_pages_radio,
        win.pdf_to_images_selected_pages_radio, win.pdf_to_images_dpi_combo,
        win.pdf_to_images_folder_btn, win.pdf_to_images_button,
    ] + list(win.pdf_to_images_format_radios.values())


def test_all_pdf_to_images_controls_disabled_during_operation_and_others_too(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    gate = _GatedCall(pe.render_pdf_to_images)
    with patch.object(pe, "render_pdf_to_images", side_effect=gate):
        window._on_pdf_to_images_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        for control in _all_pdf_to_images_controls(window):
            assert _state(control) == "disabled", control
        assert _state(window.merge_only_btn) == "disabled"
        assert _state(window.select_files_btn) == "disabled"
        assert _state(window.watermark_select_btn) == "disabled"
        assert _state(window.images_to_pdf_select_btn) == "disabled"
        assert _state(window.page_numbers_select_btn) == "disabled"

        gate.release()
        assert _pump_until(window, lambda: not window.pdf_to_images_in_progress)


def test_other_tools_cannot_start_while_pdf_to_images_runs(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    gate = _GatedCall(pe.render_pdf_to_images)
    with patch.object(pe, "render_pdf_to_images", side_effect=gate):
        window._on_pdf_to_images_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        with patch("threading.Thread") as thread:
            window._on_page_numbers_execute_clicked()
            window._on_watermark_execute_clicked()
            window._on_images_to_pdf_execute_clicked()
            window._on_merge_only_clicked()
            window._on_pdf_to_images_execute_clicked()  # not re-entrant either
            window._on_pdf_to_images_select_file_clicked()
        thread.assert_not_called()

        gate.release()
        assert _pump_until(window, lambda: not window.pdf_to_images_in_progress)


def test_pdf_to_images_cannot_start_while_another_tool_runs(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

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

        window._select_tool("pdf_to_images")
        window.root.update()
        assert _state(window.pdf_to_images_button) == "disabled"
        assert _state(window.pdf_to_images_select_btn) == "disabled"

        with patch.object(pe, "render_pdf_to_images") as engine:
            window._on_pdf_to_images_execute_clicked()
        engine.assert_not_called()
        assert not window.pdf_to_images_in_progress

        gate.release()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    window._select_tool("pdf_to_images")
    window.root.update()
    assert window.pdf_to_images_source is not None
    assert _state(window.pdf_to_images_select_btn) == "normal"
    assert _state(window.pdf_to_images_button) == "normal"


def test_any_operation_predicate_includes_pdf_to_images_flags(window):
    assert not window._any_operation_in_progress()
    window.pdf_to_images_in_progress = True
    assert window._any_operation_in_progress()
    window.pdf_to_images_in_progress = False
    assert not window._any_operation_in_progress()

    window._pdf_to_images_import_in_progress = True
    assert window._any_operation_in_progress()
    window._pdf_to_images_import_in_progress = False
    assert not window._any_operation_in_progress()


def test_import_also_holds_the_global_busy_lock(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    gate = _GatedCall(pdf_engine.get_pdf_info)
    with patch("file_manager.select_single_pdf_file", return_value=src):
        with patch.object(pdf_engine, "get_pdf_info", side_effect=gate):
            window._on_pdf_to_images_select_file_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert window._pdf_to_images_import_in_progress
            assert window._any_operation_in_progress()
            assert _state(window.pdf_to_images_select_btn) == "disabled"
            assert _state(window.merge_only_btn) == "disabled"
            assert "Validating" in window.pdf_to_images_status_var.get()

            gate.release()
            assert _pump_until(window, lambda: not window._pdf_to_images_import_in_progress)

    assert not window._any_operation_in_progress()


def test_controls_restored_after_success(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)
    _run_convert(window)

    assert not window._any_operation_in_progress()
    assert _state(window.pdf_to_images_select_btn) == "normal"
    assert _state(window.pdf_to_images_all_pages_radio) == "normal"
    for radio in window.pdf_to_images_format_radios.values():
        assert _state(radio) == "normal"
    assert _state(window.pdf_to_images_dpi_combo) == "normal"
    assert _state(window.pdf_to_images_folder_btn) == "normal"
    assert _state(window.pdf_to_images_button) == "disabled"  # source consumed
    assert _state(window.select_files_btn) == "normal"
    assert _state(window.watermark_select_btn) == "normal"
    assert str(window.pdf_to_images_progress_bar.cget("mode")) == "determinate"


def test_controls_restored_after_failure(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    with patch.object(pe, "render_pdf_to_images", side_effect=pdf_engine.PDFEngineError("x")):
        with patch("tkinter.messagebox.showerror"):
            _run_convert(window)

    assert _state(window.pdf_to_images_select_btn) == "normal"
    assert _state(window.pdf_to_images_button) == "normal"
    assert _state(window.watermark_select_btn) == "normal"


@pytest.mark.parametrize("select_handler,import_flag,tool_id", [
    ("_on_watermark_select_file_clicked", "_watermark_import_in_progress", "watermark"),
    ("_on_page_numbers_select_file_clicked", "_page_numbers_import_in_progress", "page_numbers"),
])
def test_other_tools_import_restores_pdf_to_images_controls(
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
        assert _state(window.pdf_to_images_select_btn) == "disabled"
        assert _pump_until(window, lambda: not getattr(window, import_flag))

    assert not window._any_operation_in_progress()
    assert _state(window.pdf_to_images_select_btn) == "normal"
    assert _state(window.pdf_to_images_dpi_combo) == "normal"


# ---------------------------------------------------------------------------
# 30. Existing tools remain functional
# ---------------------------------------------------------------------------

def test_pdf_to_images_select_then_switch_leaves_other_tools_usable(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _select_source(window, src)

    for tool_id, attr in [
        ("watermark", "watermark_select_btn"),
        ("page_numbers", "page_numbers_select_btn"),
        ("images_to_pdf", "images_to_pdf_select_btn"),
        ("merge_compress", "select_files_btn"),
    ]:
        window._select_tool(tool_id)
        window.root.update()
        assert _state(getattr(window, attr)) == "normal", tool_id


def test_existing_watermark_workspace_remains_functional(window, tmp_path):
    import watermark_engine

    wsrc = tmp_path / "w.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(wsrc)
    doc.close()

    window._select_tool("watermark")
    window.root.update()
    window.watermark_source = None
    window._update_watermark_source_label()
    window.watermark_text_var.set(watermark_engine.DEFAULT_TEXT)

    with patch("file_manager.select_single_pdf_file", return_value=wsrc):
        window._on_watermark_select_file_clicked()
        assert _pump_until(window, lambda: not window._watermark_import_in_progress)
    window._on_watermark_execute_clicked()
    assert _pump_until(window, lambda: not window.watermark_in_progress)

    assert (tmp_path / "w_watermarked.pdf").exists()
    assert _state(window.pdf_to_images_select_btn) == "normal"


def test_converting_a_pdf_then_reconverting_the_output_images_does_not_break_anything(window, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=3)
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    _run_convert(window)

    assert len(list(out.glob("*.png"))) == 3
    assert _state(window.pdf_to_images_select_btn) == "normal"


# ---------------------------------------------------------------------------
# 31. Scrollable workspace integration
# ---------------------------------------------------------------------------

def test_pdf_to_images_view_lives_in_the_scrollable_container(window):
    assert window.pdf_to_images_view.winfo_parent() == str(window.workspace_container)
    assert window.pdf_to_images_view.winfo_ismapped()
    window.root.update_idletasks()


def test_switching_tools_repeatedly_does_not_break_the_workspace(window):
    for _ in range(3):
        for tool_id in ("page_numbers", "pdf_to_images", "watermark", "pdf_to_images", "merge_compress"):
            window._select_tool(tool_id)
            window.root.update()
    window._select_tool("pdf_to_images")
    window.root.update()
    assert window.pdf_to_images_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()


def test_settings_and_source_survive_switching_away_and_back(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    out = tmp_path / "out"
    _choose_folder(window, out)
    _select_source(window, src)
    window.pdf_to_images_format_radios["jpeg"].invoke()
    window.pdf_to_images_dpi_var.set("200")
    window.pdf_to_images_jpeg_quality_var.set("75")
    window.root.update()

    for tool_id in ("unlock", "protect", "rotate", "watermark", "page_numbers",
                    "images_to_pdf", "merge_compress"):
        window._select_tool(tool_id)
        window.root.update()
    window._select_tool("pdf_to_images")
    window.root.update()

    assert window.pdf_to_images_source is not None
    assert window.pdf_to_images_output_dir == out
    assert window.pdf_to_images_format_var.get() == "jpeg"
    assert window.pdf_to_images_dpi_var.get() == "200"
    assert window.pdf_to_images_jpeg_quality_var.get() == "75"
    assert _state(window.pdf_to_images_button) == "normal"


# ---------------------------------------------------------------------------
# 32. Repeated conversions
# ---------------------------------------------------------------------------

def test_repeated_conversions_on_different_files(window, tmp_path):
    out = tmp_path / "out"
    _choose_folder(window, out)
    for i in range(3):
        src = _make_pdf(tmp_path / f"doc{i}.pdf", pages=2)
        _select_source(window, src)
        _run_convert(window)
        assert not window._any_operation_in_progress()
        assert window.pdf_to_images_source is None

    assert len(list(out.glob("*.png"))) == 6


def test_repeated_conversions_on_the_same_file_get_collision_suffixes(window, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=1)
    out = tmp_path / "out"
    _choose_folder(window, out)

    for _ in range(3):
        _select_source(window, src)
        _run_convert(window)

    names = sorted(p.name for p in out.glob("*.png"))
    assert names == [
        "document_page_001 (1).png", "document_page_001 (2).png",
        "document_page_001.png",
    ]


# ---------------------------------------------------------------------------
# 33. Tk thread discipline
# ---------------------------------------------------------------------------

def test_no_tk_calls_from_worker_threads_during_a_full_run(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    out = tmp_path / "out"
    _choose_folder(window, out)

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

    with patch.object(tk.Variable, "set", spy_set), \
         patch.object(tk.Misc, "_configure", spy_configure):
        _select_source(window, src)
        _run_convert(window)

    assert offenders == []


def test_result_handlers_run_on_the_main_thread(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")

    threads = []
    orig_result = window._apply_pdf_to_images_result
    orig_import = window._apply_pdf_to_images_import_result

    def spy_result(item):
        threads.append(threading.current_thread() is threading.main_thread())
        return orig_result(item)

    def spy_import(result):
        threads.append(threading.current_thread() is threading.main_thread())
        return orig_import(result)

    with patch.object(window, "_apply_pdf_to_images_result", spy_result), \
         patch.object(window, "_apply_pdf_to_images_import_result", spy_import):
        _select_source(window, src)
        _run_convert(window)

    assert threads == [True, True]


def test_result_handlers_refuse_to_run_off_the_main_thread(window):
    errors = []

    def attempt(handler, payload):
        try:
            handler(payload)
        except RuntimeError as exc:
            errors.append(str(exc))

    for handler, payload in (
        (window._apply_pdf_to_images_result, {"success": True, "result": {"output_paths": [], "total_images": 0}}),
        (window._apply_pdf_to_images_import_result, {"success": False, "error": "x", "info": None}),
    ):
        thread = threading.Thread(target=attempt, args=(handler, payload))
        thread.start()
        thread.join(timeout=5)

    assert len(errors) == 2
    assert all("non-main thread" in e for e in errors)
    assert not window._any_operation_in_progress()


def test_worker_only_communicates_through_the_queue(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    puts = []
    real_put = window._pdf_to_images_queue.put

    def spy_put(item, *args, **kwargs):
        puts.append(item["type"])
        return real_put(item, *args, **kwargs)

    with patch.object(window._pdf_to_images_queue, "put", spy_put):
        _run_convert(window)

    assert puts[-1] == "done"
    assert set(puts) <= {"progress", "done"}
    assert "progress" in puts


def test_gui_remains_responsive_during_conversion(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf")
    _choose_folder(window, tmp_path / "out")
    _select_source(window, src)

    gate = _GatedCall(pe.render_pdf_to_images)
    with patch.object(pe, "render_pdf_to_images", side_effect=gate):
        window._on_pdf_to_images_execute_clicked()
        assert gate.entered.wait(timeout=5)

        update_count = 0
        for _ in range(20):
            window.root.update()
            update_count += 1
        assert window.pdf_to_images_in_progress
        assert update_count == 20

        window._select_tool("merge_compress")
        window.root.update()
        window._select_tool("pdf_to_images")
        window.root.update()

        gate.release()
        assert _pump_until(window, lambda: not window.pdf_to_images_in_progress)
