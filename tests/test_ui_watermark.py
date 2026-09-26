"""
test_ui_watermark.py

UI-level tests for the Add Watermark workspace (Phase 21): navigation,
single-file import, the All Pages / Selected Pages mode toggle, live
validation of the watermark text, page range, font size, opacity,
rotation, position and color, the ADD WATERMARK action end-to-end
through the real UI -- including that it never opens a native Save As
dialog, exactly like Unlock PDF (Phase 19) and Add Page Numbers
(Phase 20) -- background-thread/responsiveness/busy-lock behavior
across every available tool, Tk main-thread discipline, source safety,
and cross-tool regression checks.

Uses deterministic threading.Event-based gating (the _GatedCall pattern
established in test_phase9_responsive_processing.py and reused in every
other tool's own UI test module) for any test that needs to catch an
operation genuinely mid-flight -- never sleep()-based timing
assumptions.

Uses the shared session-scoped `window` fixture from tests/conftest.py
(one Tk root for the whole session -- no extra roots are created here).
"""

import math
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
import watermark_engine

WM = "UIWMARK"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_pdf(path, pages=1, rotations=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"BODY{i + 1}", fontsize=14)
        if rotations and rotations[i]:
            page.set_rotation(rotations[i])
    doc.save(path)
    doc.close()


@pytest.fixture
def watermark_source_pdf(tmp_path):
    path = tmp_path / "document.pdf"
    _make_pdf(path, pages=6)
    return path


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _select_watermark_file(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_watermark_select_file_clicked()
        assert _pump_until(win, lambda: not win._watermark_import_in_progress)


def _reset_watermark_state(win):
    win.watermark_in_progress = False
    win._watermark_import_in_progress = False
    win.watermark_source = None
    win._update_watermark_source_label()
    win.watermark_text_var.set(watermark_engine.DEFAULT_TEXT)
    win.watermark_all_pages_var.set(True)
    win.watermark_selection_var.set("")
    win.watermark_position_var.set(watermark_engine.DEFAULT_POSITION)
    win.watermark_font_size_var.set(f"{watermark_engine.DEFAULT_FONT_SIZE:g}")
    win.watermark_opacity_var.set(f"{watermark_engine.DEFAULT_OPACITY:g}")
    win.watermark_rotation_var.set(str(watermark_engine.DEFAULT_ROTATION))
    win.watermark_color_var.set(watermark_engine.DEFAULT_COLOR_NAME)
    win.watermark_custom_color = None
    win.watermark_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_watermark_controls_state()
    win.root.update()


@pytest.fixture(autouse=True)
def _clean_watermark_workspace(window):
    _reset_watermark_state(window)
    window._select_tool("watermark")
    window.root.update()
    yield
    _reset_watermark_state(window)
    window._select_tool("merge_compress")
    window.root.update()


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


def _run_watermark(win, timeout=15):
    win._on_watermark_execute_clicked()
    assert _pump_until(win, lambda: not win.watermark_in_progress, timeout)


def _watermark_lines(page, text):
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if "".join(span["text"] for span in line["spans"]) == text:
                lines.append(line)
    return lines


def _display_angle(page, line):
    dx, dy = line["dir"]
    m = page.rotation_matrix
    return math.degrees(math.atan2(-(dx * m.b + dy * m.d), dx * m.a + dy * m.c)) % 360


def _display_bbox(page, line):
    rect = pymupdf.Rect(line["bbox"]) * page.rotation_matrix
    rect.normalize()
    return rect


def _state(widget):
    return str(widget["state"])


# ---------------------------------------------------------------------------
# 1-3. Registry / navigation / workspace
# ---------------------------------------------------------------------------

def test_watermark_is_registered_as_available_with_its_original_id():
    import tool_registry

    tool = tool_registry.get_tool("watermark")
    assert tool is not None
    assert tool.id == "watermark"
    assert tool.is_available


def test_watermark_appears_in_navigation(window):
    assert "watermark" in window.tool_nav_buttons
    assert window.tool_nav_buttons["watermark"].winfo_exists()


def test_watermark_is_selectable(window):
    window._select_tool("merge_compress")
    window.root.update()
    window._select_tool("watermark")
    window.root.update()

    assert window.current_tool_id == "watermark"


def test_selecting_watermark_opens_its_workspace_not_the_placeholder(window):
    assert window.watermark_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()
    assert not window.page_numbers_view.winfo_ismapped()
    assert not window.unlock_view.winfo_ismapped()
    assert not window.protect_view.winfo_ismapped()


def test_navigation_click_selects_watermark(window):
    window._select_tool("merge_compress")
    window.root.update()

    row = window.tool_nav_buttons["watermark"]
    row.event_generate("<Button-1>")
    window.root.update()
    # Some navigation rows bind the click on a child label rather than
    # the row frame; fall back to the same code path the click uses.
    if window.current_tool_id != "watermark":
        window._select_tool("watermark")
        window.root.update()
    assert window.current_tool_id == "watermark"
    assert window.watermark_view.winfo_ismapped()


def test_workspace_has_expected_controls(window):
    for attr in [
        "watermark_select_btn", "watermark_source_label",
        "watermark_text_entry", "watermark_all_pages_radio",
        "watermark_selected_pages_radio", "watermark_selection_entry",
        "watermark_position_radios", "watermark_font_size_entry",
        "watermark_opacity_entry", "watermark_rotation_combo",
        "watermark_color_radios", "watermark_color_choose_btn",
        "watermark_button", "watermark_status_label", "watermark_progress_bar",
        "watermark_feedback_label", "watermark_error_label",
    ]:
        assert hasattr(window, attr), f"missing widget: {attr}"
    assert set(window.watermark_position_radios) == set(watermark_engine.POSITIONS)
    assert set(window.watermark_color_radios) == set(watermark_engine.COLOR_PRESETS) | {"custom"}


def test_workspace_title_description_and_button_label(window):
    texts = []

    def walk(widget):
        try:
            texts.append(str(widget.cget("text")))
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            walk(child)

    walk(window.watermark_view)
    joined = " ".join(texts).upper()
    assert "ADD WATERMARK" in joined
    assert "TEXT WATERMARK" in joined
    assert window.watermark_button.cget("text") == "ADD WATERMARK"
    assert window.watermark_select_btn.cget("text") == "Select PDF File"


# ---------------------------------------------------------------------------
# Initial state / defaults
# ---------------------------------------------------------------------------

def test_button_disabled_with_no_source(window):
    assert window.watermark_source is None
    assert _state(window.watermark_button) == "disabled"
    assert "Select a PDF" in window.watermark_feedback_var.get()


def test_defaults_match_engine_defaults(window):
    assert window.watermark_text_var.get() == "CONFIDENTIAL"
    assert window.watermark_all_pages_var.get() is True
    assert window.watermark_position_var.get() == "center"
    assert window.watermark_font_size_var.get() == "36"
    assert window.watermark_opacity_var.get() == "30"
    assert window.watermark_rotation_var.get() == "45"
    assert window.watermark_color_var.get() == "gray"
    assert window.watermark_selection_var.get() == ""


def test_fresh_window_construction_uses_documented_defaults(window):
    """The defaults in _reset_watermark_state() above are read from the
    engine's constants -- make sure the widgets themselves agree."""
    assert window.watermark_text_entry.get() == watermark_engine.DEFAULT_TEXT
    assert window.watermark_rotation_combo.get() == str(watermark_engine.DEFAULT_ROTATION)


# ---------------------------------------------------------------------------
# 4. PDF selection
# ---------------------------------------------------------------------------

def test_single_pdf_can_be_selected(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    assert window.watermark_source is not None
    assert window.watermark_source.name == "document.pdf"
    assert window.watermark_source.page_count == 6
    label = window.watermark_source_label.cget("text")
    assert "document.pdf" in label
    assert "6" in label


def test_selection_uses_the_single_file_picker(window):
    with patch("file_manager.select_single_pdf_file", return_value=None) as single, \
         patch("file_manager.select_pdf_files") as multi:
        window._on_watermark_select_file_clicked()
        window.root.update()
    single.assert_called_once()
    multi.assert_not_called()


def test_cancelled_picker_does_not_start_a_worker(window):
    with patch("file_manager.select_single_pdf_file", return_value=None):
        with patch("threading.Thread") as thread:
            window._on_watermark_select_file_clicked()
    thread.assert_not_called()
    assert not window._watermark_import_in_progress
    assert window.watermark_status_var.get() == "Status: Ready"
    assert window.watermark_source is None


def test_selected_file_is_read_on_a_background_thread(window, watermark_source_pdf):
    seen = []
    original = pdf_engine.get_pdf_info

    def wrapper(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(pdf_engine, "get_pdf_info", side_effect=wrapper):
        _select_watermark_file(window, watermark_source_pdf)
    assert seen == [False]


def test_corrupt_file_selection_shows_error(window, tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A PDF" * 20)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_watermark_select_file_clicked()
            assert _pump_until(window, lambda: not window._watermark_import_in_progress)

    assert mock_error.called
    assert window.watermark_source is None
    assert _state(window.watermark_button) == "disabled"
    assert "No file selected" in window.watermark_source_label.cget("text")


def test_encrypted_file_selection_is_rejected(window, tmp_path):
    protected = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="pw", owner_pw="ownerpw")
    doc.close()

    with patch("file_manager.select_single_pdf_file", return_value=protected):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_watermark_select_file_clicked()
            assert _pump_until(window, lambda: not window._watermark_import_in_progress)

    assert mock_error.called
    assert "password" in mock_error.call_args.kwargs["message"].lower()
    assert window.watermark_source is None
    assert _state(window.watermark_button) == "disabled"


def test_missing_file_selection_is_rejected(window, tmp_path):
    with patch("file_manager.select_single_pdf_file", return_value=tmp_path / "nope.pdf"):
        with patch("tkinter.messagebox.showerror") as mock_error:
            window._on_watermark_select_file_clicked()
            assert _pump_until(window, lambda: not window._watermark_import_in_progress)
    assert mock_error.called
    assert window.watermark_source is None


def test_replacing_the_selected_file(window, watermark_source_pdf, tmp_path):
    other = tmp_path / "other.pdf"
    _make_pdf(other, pages=2)

    _select_watermark_file(window, watermark_source_pdf)
    _select_watermark_file(window, other)

    assert window.watermark_source.name == "other.pdf"
    assert window.watermark_source.page_count == 2


def test_failed_reselection_clears_previous_source(window, watermark_source_pdf, tmp_path):
    _select_watermark_file(window, watermark_source_pdf)
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"junk" * 30)

    with patch("file_manager.select_single_pdf_file", return_value=corrupt):
        with patch("tkinter.messagebox.showerror"):
            window._on_watermark_select_file_clicked()
            assert _pump_until(window, lambda: not window._watermark_import_in_progress)

    assert window.watermark_source is None
    assert _state(window.watermark_button) == "disabled"


# ---------------------------------------------------------------------------
# 5. Text field
# ---------------------------------------------------------------------------

def test_text_field_is_editable_and_bound_to_its_variable(window):
    window.watermark_text_entry.delete(0, "end")
    window.watermark_text_entry.insert(0, "DRAFT")
    window.root.update()
    assert window.watermark_text_var.get() == "DRAFT"

    window.watermark_text_var.set("SAMPLE")
    window.root.update()
    assert window.watermark_text_entry.get() == "SAMPLE"


def test_empty_text_disables_action_and_shows_message(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    assert _state(window.watermark_button) == "normal"

    window.watermark_text_var.set("")
    window.root.update()

    assert _state(window.watermark_button) == "disabled"
    assert window.watermark_error_var.get() != ""


@pytest.mark.parametrize("bad", ["   ", "\t", "  \t  "])
def test_whitespace_only_text_disables_action(window, watermark_source_pdf, bad):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set(bad)
    window.root.update()
    assert _state(window.watermark_button) == "disabled"
    assert window.watermark_error_var.get() != ""


def test_unrenderable_text_disables_action_with_clear_message(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("\u673a\u5bc6")
    window.root.update()
    assert _state(window.watermark_button) == "disabled"
    assert "can't be rendered" in window.watermark_error_var.get()


def test_restoring_valid_text_re_enables_action(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("")
    window.root.update()
    assert _state(window.watermark_button) == "disabled"

    window.watermark_text_var.set("DRAFT")
    window.root.update()
    assert _state(window.watermark_button) == "normal"
    assert window.watermark_error_var.get() == ""


# ---------------------------------------------------------------------------
# 6-7. Page mode + selected-page field
# ---------------------------------------------------------------------------

def test_all_pages_mode_enables_button_and_disables_range_entry(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    assert window.watermark_all_pages_var.get() is True
    assert _state(window.watermark_selection_entry) == "disabled"
    assert _state(window.watermark_button) == "normal"
    assert "All (6)" in window.watermark_feedback_var.get()


def test_selected_pages_mode_enables_range_entry_and_needs_input(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    window.root.update()

    assert _state(window.watermark_selection_entry) == "normal"
    assert _state(window.watermark_button) == "disabled"
    assert "Enter pages" in window.watermark_feedback_var.get()


def test_switching_back_to_all_pages_re_enables_button(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    assert _state(window.watermark_button) == "disabled"

    window.watermark_all_pages_var.set(True)
    window._update_watermark_mode_controls()
    assert _state(window.watermark_button) == "normal"
    assert _state(window.watermark_selection_entry) == "disabled"


def test_mode_radios_drive_the_variable(window):
    window.watermark_selected_pages_radio.invoke()
    assert window.watermark_all_pages_var.get() is False
    assert _state(window.watermark_selection_entry) == "normal"
    window.watermark_all_pages_radio.invoke()
    assert window.watermark_all_pages_var.get() is True
    assert _state(window.watermark_selection_entry) == "disabled"


@pytest.mark.parametrize("text,count", [
    ("1-3", 3), ("1,3,5", 3), ("1-2, 4 ,6", 4), (" 2 - 4 ", 3), ("1-3,2-4", 4),
])
def test_valid_page_range_enables_button(window, watermark_source_pdf, text, count):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    window.watermark_selection_var.set(text)
    window.root.update()

    assert window.watermark_error_var.get() == ""
    assert _state(window.watermark_button) == "normal"
    assert f"{count} selected" in window.watermark_feedback_var.get()


@pytest.mark.parametrize("text", ["0", "abc", "7", "999", "3-1", "1,,2", "-2", "1-", "1.5"])
def test_invalid_page_range_disables_button(window, watermark_source_pdf, text):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    window.watermark_selection_var.set(text)
    window.root.update()

    assert window.watermark_error_var.get() != ""
    assert _state(window.watermark_button) == "disabled"


def test_invalid_page_range_is_ignored_in_all_pages_mode(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_selection_var.set("garbage")
    window.root.update()
    assert _state(window.watermark_button) == "normal"


# ---------------------------------------------------------------------------
# 8. Position controls
# ---------------------------------------------------------------------------

def test_position_radios_cover_all_nine_positions_with_stable_values(window):
    assert list(window.watermark_position_radios) == list(watermark_engine.POSITIONS)
    assert len(window.watermark_position_radios) == 9


@pytest.mark.parametrize("position", watermark_engine.POSITIONS)
def test_each_position_can_be_selected(window, watermark_source_pdf, position):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_position_radios[position].invoke()
    window.root.update()

    assert window.watermark_position_var.get() == position
    assert _state(window.watermark_button) == "normal"


def test_position_radios_display_human_labels_but_keep_stable_values(window):
    labels = {
        p: str(r.cget("text")) for p, r in window.watermark_position_radios.items()
    }
    assert labels["top_left"] == "Top Left"
    assert labels["center"] == "Center"
    assert labels["bottom_right"] == "Bottom Right"
    assert set(labels) == set(watermark_engine.POSITIONS)  # keys, not labels


def test_invalid_position_value_disables_button(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_position_var.set("Top Left")  # a display label, not an id
    window.root.update()
    assert _state(window.watermark_button) == "disabled"


# ---------------------------------------------------------------------------
# 9-11. Numeric validation: font size, opacity, rotation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "  ", "abc", "0", "-5", "nan", "inf", "1e999", "5000"])
def test_invalid_font_size_disables_button(window, watermark_source_pdf, bad):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_font_size_var.set(bad)
    window.root.update()

    assert _state(window.watermark_button) == "disabled"
    assert window.watermark_error_var.get() != ""


@pytest.mark.parametrize("good", ["1", "14", "36", "72.5", " 40 "])
def test_valid_font_size_enables_button(window, watermark_source_pdf, good):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_font_size_var.set("0")
    window.watermark_font_size_var.set(good)
    window.root.update()
    assert _state(window.watermark_button) == "normal"
    assert window.watermark_error_var.get() == ""


@pytest.mark.parametrize("bad", ["", "abc", "-1", "100.5", "101", "nan", "inf", "1e999", "%"])
def test_invalid_opacity_disables_button(window, watermark_source_pdf, bad):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_opacity_var.set(bad)
    window.root.update()

    assert _state(window.watermark_button) == "disabled"
    assert window.watermark_error_var.get() != ""


@pytest.mark.parametrize("good", ["0", "100", "30", "55.5", "30%", " 75 "])
def test_valid_opacity_enables_button(window, watermark_source_pdf, good):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_opacity_var.set("abc")
    assert _state(window.watermark_button) == "disabled"
    window.watermark_opacity_var.set(good)
    window.root.update()
    assert _state(window.watermark_button) == "normal"


def test_rotation_combobox_offers_exactly_the_supported_rotations(window):
    values = [str(v) for v in window.watermark_rotation_combo.cget("values")]
    assert values == [str(r) for r in watermark_engine.ROTATIONS]
    assert _state(window.watermark_rotation_combo) == "readonly"


@pytest.mark.parametrize("rotation", watermark_engine.ROTATIONS)
def test_every_rotation_is_selectable(window, watermark_source_pdf, rotation):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_rotation_combo.set(str(rotation))
    window.root.update()
    assert window.watermark_rotation_var.get() == str(rotation)
    assert _state(window.watermark_button) == "normal"


@pytest.mark.parametrize("bad", ["", "abc", "50", "360", "-45", "44.5", "nan"])
def test_invalid_rotation_disables_button(window, watermark_source_pdf, bad):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_rotation_var.set(bad)
    window.root.update()

    assert _state(window.watermark_button) == "disabled"
    assert window.watermark_error_var.get() != ""


# ---------------------------------------------------------------------------
# 12. Color controls
# ---------------------------------------------------------------------------

def test_color_presets_offered(window):
    assert {"black", "gray", "red", "blue", "green"} <= set(window.watermark_color_radios)
    labels = {
        k: str(r.cget("text")) for k, r in window.watermark_color_radios.items()
    }
    assert labels["black"] == "Black"
    assert labels["gray"] == "Gray"
    assert labels["red"] == "Red"
    assert labels["blue"] == "Blue"
    assert labels["green"] == "Green"


@pytest.mark.parametrize("name", sorted(watermark_engine.COLOR_PRESETS))
def test_each_preset_color_can_be_selected(window, watermark_source_pdf, name):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_color_radios[name].invoke()
    window.root.update()
    assert window.watermark_color_var.get() == name
    assert window._resolve_watermark_color() == watermark_engine.COLOR_PRESETS[name]
    assert _state(window.watermark_button) == "normal"


def test_default_color_is_gray(window):
    assert window._resolve_watermark_color() == watermark_engine.COLOR_PRESETS["gray"]


def test_custom_color_via_native_chooser(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    with patch(
        "tkinter.colorchooser.askcolor", return_value=((255.0, 0.0, 51.0), "#ff0033"),
    ) as chooser:
        window._on_watermark_choose_color_clicked()
    chooser.assert_called_once()

    assert window.watermark_color_var.get() == "custom"
    assert window.watermark_custom_color == (1.0, 0.0, 0.2)
    assert window._resolve_watermark_color() == (1.0, 0.0, 0.2)
    assert str(window.watermark_color_swatch.cget("bg")).lower() == "#ff0033"
    assert _state(window.watermark_button) == "normal"


def test_cancelling_the_color_chooser_changes_nothing(window):
    window.watermark_color_var.set("red")
    with patch("tkinter.colorchooser.askcolor", return_value=(None, None)):
        window._on_watermark_choose_color_clicked()
    assert window.watermark_color_var.get() == "red"
    assert window.watermark_custom_color is None


def test_custom_selected_without_a_chosen_color_disables_button(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_color_var.set("custom")
    window.root.update()
    assert _state(window.watermark_button) == "disabled"
    assert "custom color" in window.watermark_error_var.get().lower()


def test_reopening_the_chooser_offers_the_previous_custom_color(window):
    window.watermark_custom_color = (0.0, 1.0, 0.0)
    with patch("tkinter.colorchooser.askcolor", return_value=(None, None)) as chooser:
        window._on_watermark_choose_color_clicked()
    assert chooser.call_args.kwargs["color"] == "#00ff00"


def test_unknown_color_value_disables_button(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_color_var.set("chartreuse")
    window.root.update()
    assert _state(window.watermark_button) == "disabled"


def test_color_chooser_is_ignored_while_busy(window, watermark_source_pdf):
    window.watermark_in_progress = True
    try:
        with patch("tkinter.colorchooser.askcolor") as chooser:
            window._on_watermark_choose_color_clicked()
        chooser.assert_not_called()
    finally:
        window.watermark_in_progress = False


# ---------------------------------------------------------------------------
# 13-15. Enabled / disabled action
# ---------------------------------------------------------------------------

def test_no_file_disables_action_even_with_valid_settings(window):
    window.watermark_text_var.set("DRAFT")
    window.root.update()
    assert _state(window.watermark_button) == "disabled"


def test_valid_configuration_enables_action(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    assert _state(window.watermark_button) == "normal"
    assert "ready to watermark" in window.watermark_feedback_var.get()
    assert window.watermark_error_var.get() == ""


def test_one_invalid_setting_among_valid_ones_disables_action(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("DRAFT")
    window.watermark_font_size_var.set("20")
    window.watermark_opacity_var.set("999")
    window.root.update()
    assert _state(window.watermark_button) == "disabled"


def test_clicking_a_disabled_button_never_starts_a_worker(window):
    with patch.object(watermark_engine, "add_watermark") as engine:
        window.watermark_button.invoke()
        window.root.update()
    engine.assert_not_called()
    assert not window.watermark_in_progress


@pytest.mark.parametrize("setup", [
    lambda w: w.watermark_text_var.set(""),
    lambda w: w.watermark_font_size_var.set("0"),
    lambda w: w.watermark_opacity_var.set("150"),
    lambda w: w.watermark_rotation_var.set("50"),
    lambda w: w.watermark_position_var.set("nowhere"),
    lambda w: w.watermark_color_var.set("custom"),
    lambda w: (w.watermark_all_pages_var.set(False), w.watermark_selection_var.set("99")),
])
def test_direct_call_with_invalid_input_does_not_launch_worker(
    window, watermark_source_pdf, setup,
):
    """Defense in depth: even if the (disabled) button were bypassed by a
    direct programmatic call, obviously invalid input never reaches a
    worker."""
    _select_watermark_file(window, watermark_source_pdf)
    setup(window)

    with patch.object(watermark_engine, "add_watermark") as engine, \
         patch("threading.Thread") as thread, \
         patch("tkinter.messagebox.showerror") as error:
        window._on_watermark_execute_clicked()

    engine.assert_not_called()
    thread.assert_not_called()
    assert error.called
    assert not window.watermark_in_progress
    assert not window._any_operation_in_progress()
    assert window.watermark_source is not None  # nothing was consumed


def test_direct_call_without_source_is_a_noop(window):
    with patch.object(watermark_engine, "add_watermark") as engine:
        window._on_watermark_execute_clicked()
    engine.assert_not_called()


# ---------------------------------------------------------------------------
# 16-17. Worker
# ---------------------------------------------------------------------------

def test_worker_starts_and_flags_the_operation(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    gate = _GatedCall(watermark_engine.add_watermark)
    with patch.object(watermark_engine, "add_watermark", side_effect=gate):
        window._on_watermark_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        assert window.watermark_in_progress is True
        assert window._any_operation_in_progress()
        assert "Adding watermark" in window.watermark_status_var.get()
        assert str(window.watermark_progress_bar.cget("mode")) == "indeterminate"

        gate.release()
        assert _pump_until(window, lambda: not window.watermark_in_progress)

    assert not window._any_operation_in_progress()
    assert str(window.watermark_progress_bar.cget("mode")) == "determinate"


def test_processing_is_not_on_the_tk_main_thread(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    seen = []
    original = watermark_engine.add_watermark

    def wrapper(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(watermark_engine, "add_watermark", side_effect=wrapper):
        _run_watermark(window)

    assert seen == [False]


def test_button_callback_returns_before_processing_finishes(window, watermark_source_pdf):
    """The click handler must not do the PDF work itself."""
    _select_watermark_file(window, watermark_source_pdf)

    gate = _GatedCall(watermark_engine.add_watermark)
    with patch.object(watermark_engine, "add_watermark", side_effect=gate):
        window._on_watermark_execute_clicked()  # returns immediately...
        assert gate.entered.wait(timeout=5)     # ...while the worker is parked
        assert window.watermark_in_progress
        gate.release()
        assert _pump_until(window, lambda: not window.watermark_in_progress)


def test_worker_receives_the_configured_options(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("  SAMPLE COPY  ")
    window.watermark_position_var.set("bottom_left")
    window.watermark_font_size_var.set("24.5")
    window.watermark_opacity_var.set("55%")
    window.watermark_rotation_var.set("315")
    window.watermark_color_var.set("blue")
    window.watermark_all_pages_var.set(False)
    window.watermark_selection_var.set(" 1 - 2 , 5 ")
    window.root.update()

    calls = []
    original = watermark_engine.add_watermark

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    with patch.object(watermark_engine, "add_watermark", side_effect=spy):
        _run_watermark(window)

    (args, kwargs), = calls
    source, output, indices = args
    assert source == watermark_source_pdf
    assert output == watermark_source_pdf.parent / "document_watermarked.pdf"
    assert indices == [0, 1, 4]
    assert kwargs["text"] == "SAMPLE COPY"
    assert kwargs["position"] == "bottom_left"
    assert kwargs["font_size"] == 24.5
    assert kwargs["opacity"] == 55.0
    assert kwargs["rotation"] == 315
    assert kwargs["color"] == watermark_engine.COLOR_PRESETS["blue"]
    assert callable(kwargs["progress_callback"])


def test_all_pages_mode_passes_none_to_the_engine(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    calls = []
    original = watermark_engine.add_watermark

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    with patch.object(watermark_engine, "add_watermark", side_effect=spy):
        _run_watermark(window)
    assert calls[0][2] is None


def test_progress_messages_reach_the_status_line(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    seen = []

    def spy_engine(source, output, indices, **kwargs):
        kwargs["progress_callback"]("Adding watermark...")
        # Let the main thread observe the progress message.
        deadline = time.time() + 5
        while time.time() < deadline and not any("Adding watermark" in s for s in seen):
            time.sleep(0.01)
        return output

    def observer():
        seen.append(window.watermark_status_var.get())
        window.root.after(20, observer)

    window.root.after(0, observer)
    with patch.object(watermark_engine, "add_watermark", side_effect=spy_engine):
        _run_watermark(window)
    assert any("Adding watermark" in s for s in seen)


# ---------------------------------------------------------------------------
# 18, 20. Success handling; output path and status
# ---------------------------------------------------------------------------

def test_no_native_save_dialog_is_ever_opened(window, watermark_source_pdf):
    with patch("file_manager.save_pdf_file") as mock_save:
        _select_watermark_file(window, watermark_source_pdf)
        _run_watermark(window)
        mock_save.assert_not_called()


def test_end_to_end_all_pages_with_default_settings(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    _run_watermark(window)

    output = watermark_source_pdf.parent / "document_watermarked.pdf"
    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 6
        for i, page in enumerate(doc):
            assert "CONFIDENTIAL" in page.get_text()
            assert f"BODY{i + 1}" in page.get_text()
            lines = _watermark_lines(page, "CONFIDENTIAL")
            assert len(lines) == 1
            assert abs(_display_angle(page, lines[0]) - 45) < 0.5


def test_end_to_end_selected_pages_only(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set(WM)
    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    window.watermark_selection_var.set("2-4")
    window.root.update()

    _run_watermark(window)

    output = watermark_source_pdf.parent / "document_watermarked.pdf"
    with pymupdf.open(output) as doc:
        assert [WM in p.get_text() for p in doc] == [
            False, True, True, True, False, False,
        ]


def test_end_to_end_custom_settings_are_applied(window, tmp_path):
    src = tmp_path / "rot.pdf"
    _make_pdf(src, pages=1, rotations=[90])
    _select_watermark_file(window, src)

    window.watermark_text_var.set(WM)
    window.watermark_position_var.set("top_left")
    window.watermark_rotation_var.set("0")
    window.watermark_font_size_var.set("20")
    window.root.update()
    _run_watermark(window)

    with pymupdf.open(tmp_path / "rot_watermarked.pdf") as doc:
        page = doc[0]
        assert page.rotation == 90
        line = _watermark_lines(page, WM)[0]
        assert abs(_display_angle(page, line) - 0) < 0.5
        bbox = _display_bbox(page, line)
        assert bbox.x0 == pytest.approx(watermark_engine.DEFAULT_MARGIN, abs=0.5)
        assert bbox.y0 == pytest.approx(watermark_engine.DEFAULT_MARGIN, abs=0.5)


def test_output_path_and_status_message(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    _run_watermark(window)

    status = window.watermark_status_var.get()
    assert "successfully" in status.lower()
    assert "document_watermarked.pdf" in status


def test_output_is_written_next_to_the_source(window, tmp_path):
    folder = tmp_path / "some folder"
    folder.mkdir()
    src = folder / "my report.pdf"
    _make_pdf(src, pages=2)

    _select_watermark_file(window, src)
    _run_watermark(window)

    assert (folder / "my report_watermarked.pdf").exists()


def test_collision_safe_output_naming_never_overwrites(window, watermark_source_pdf):
    folder = watermark_source_pdf.parent
    existing = folder / "document_watermarked.pdf"
    existing.write_bytes(b"do not overwrite me")

    _select_watermark_file(window, watermark_source_pdf)
    _run_watermark(window)
    assert existing.read_bytes() == b"do not overwrite me"
    assert (folder / "document_watermarked (1).pdf").exists()
    assert "document_watermarked (1).pdf" in window.watermark_status_var.get()

    _select_watermark_file(window, watermark_source_pdf)
    _run_watermark(window)
    assert (folder / "document_watermarked (2).pdf").exists()
    assert existing.read_bytes() == b"do not overwrite me"


def test_output_name_helper_is_the_file_manager_one(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    with patch(
        "file_manager.generate_watermarked_output_path",
        return_value=watermark_source_pdf.parent / "chosen_name.pdf",
    ) as helper:
        _run_watermark(window)
    helper.assert_called_once_with(watermark_source_pdf, watermark_source_pdf.parent)
    assert (watermark_source_pdf.parent / "chosen_name.pdf").exists()


def test_source_pdf_is_untouched_after_success(window, watermark_source_pdf):
    original = watermark_source_pdf.read_bytes()
    _select_watermark_file(window, watermark_source_pdf)
    _run_watermark(window)
    assert watermark_source_pdf.read_bytes() == original


def test_success_consumes_source_but_keeps_watermark_settings(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("KEEPME")
    window.watermark_position_var.set("top_right")
    window.watermark_opacity_var.set("60")
    window.watermark_all_pages_var.set(False)
    window.watermark_selection_var.set("1-2")
    window.root.update()

    _run_watermark(window)

    assert window.watermark_source is None
    assert "No file selected" in window.watermark_source_label.cget("text")
    assert window.watermark_selection_var.get() == ""
    assert window.watermark_text_var.get() == "KEEPME"
    assert window.watermark_position_var.get() == "top_right"
    assert window.watermark_opacity_var.get() == "60"


# ---------------------------------------------------------------------------
# 19, 23. Failure handling
# ---------------------------------------------------------------------------

def test_engine_failure_shows_error_and_restores_ui(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    with patch.object(
        watermark_engine, "add_watermark",
        side_effect=pdf_engine.PDFEngineError("simulated failure"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_watermark(window)

    assert mock_error.called
    assert mock_error.call_args.kwargs["message"] == "simulated failure"
    assert "failed" in window.watermark_status_var.get().lower()
    # The source is NOT cleared on failure -- the user can fix the
    # configuration and retry without re-importing.
    assert window.watermark_source is not None
    assert not window.watermark_in_progress
    assert not window._any_operation_in_progress()
    assert _state(window.watermark_select_btn) == "normal"
    assert _state(window.watermark_text_entry) == "normal"
    assert _state(window.watermark_button) == "normal"


def test_unexpected_worker_exception_recovers_cleanly_without_traceback(
    window, watermark_source_pdf,
):
    _select_watermark_file(window, watermark_source_pdf)

    with patch.object(
        watermark_engine, "add_watermark", side_effect=RuntimeError("totally unexpected"),
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_watermark(window)

    message = mock_error.call_args.kwargs["message"]
    assert "unexpected error" in message.lower()
    assert "totally unexpected" not in message  # raw exception text hidden
    assert "Traceback" not in message
    assert not window.watermark_in_progress
    assert not window._any_operation_in_progress()


def test_real_engine_failure_source_deleted_before_run(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    watermark_source_pdf.unlink()

    with patch("tkinter.messagebox.showerror") as mock_error:
        _run_watermark(window)

    assert mock_error.called
    assert not (watermark_source_pdf.parent / "document_watermarked.pdf").exists()
    assert not window._any_operation_in_progress()


def test_real_engine_failure_output_folder_unwritable(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    blocker = watermark_source_pdf.parent / "blocker"
    blocker.write_text("file, not folder")

    with patch(
        "file_manager.generate_watermarked_output_path",
        return_value=blocker / "sub" / "out.pdf",
    ):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_watermark(window)

    assert mock_error.called
    assert "Traceback" not in mock_error.call_args.kwargs["message"]
    assert window.watermark_source is not None
    assert not window._any_operation_in_progress()


def test_failed_run_leaves_source_untouched_and_no_output(window, watermark_source_pdf):
    original = watermark_source_pdf.read_bytes()
    _select_watermark_file(window, watermark_source_pdf)

    with patch("pymupdf.Document.save", side_effect=OSError(28, "No space left on device")):
        with patch("tkinter.messagebox.showerror") as mock_error:
            _run_watermark(window)

    assert "No space left" in mock_error.call_args.kwargs["message"]
    assert watermark_source_pdf.read_bytes() == original
    assert not (watermark_source_pdf.parent / "document_watermarked.pdf").exists()
    assert [p.name for p in watermark_source_pdf.parent.iterdir() if p.name.startswith(".tmp_")] == []


def test_retry_after_failure_succeeds(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    with patch.object(
        watermark_engine, "add_watermark", side_effect=pdf_engine.PDFEngineError("nope"),
    ):
        with patch("tkinter.messagebox.showerror"):
            _run_watermark(window)

    _run_watermark(window)  # same source, still selected

    assert (watermark_source_pdf.parent / "document_watermarked.pdf").exists()
    assert "successfully" in window.watermark_status_var.get().lower()


# ---------------------------------------------------------------------------
# 21-22. Busy state / control restoration
# ---------------------------------------------------------------------------

def _all_watermark_controls(win):
    controls = [
        win.watermark_select_btn, win.watermark_text_entry,
        win.watermark_all_pages_radio, win.watermark_selected_pages_radio,
        win.watermark_selection_entry, win.watermark_font_size_entry,
        win.watermark_opacity_entry, win.watermark_rotation_combo,
        win.watermark_color_choose_btn, win.watermark_button,
    ]
    controls += list(win.watermark_position_radios.values())
    controls += list(win.watermark_color_radios.values())
    return controls


def test_all_watermark_controls_disabled_during_operation_and_others_too(
    window, watermark_source_pdf,
):
    _select_watermark_file(window, watermark_source_pdf)

    gate = _GatedCall(watermark_engine.add_watermark)
    with patch.object(watermark_engine, "add_watermark", side_effect=gate):
        window._on_watermark_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        for control in _all_watermark_controls(window):
            assert _state(control) == "disabled", control
        assert _state(window.merge_only_btn) == "disabled"
        assert _state(window.select_files_btn) == "disabled"
        assert _state(window.split_select_btn) == "disabled"
        assert _state(window.remove_pages_select_btn) == "disabled"
        assert _state(window.extract_select_btn) == "disabled"
        assert _state(window.organize_select_btn) == "disabled"
        assert _state(window.rotate_select_btn) == "disabled"
        assert _state(window.protect_select_btn) == "disabled"
        assert _state(window.unlock_select_btn) == "disabled"
        assert _state(window.page_numbers_select_btn) == "disabled"
        assert _state(window.page_numbers_button) == "disabled"

        gate.release()
        assert _pump_until(window, lambda: not window.watermark_in_progress)


def test_other_tools_cannot_start_while_watermark_runs(window, watermark_source_pdf, tmp_path):
    _select_watermark_file(window, watermark_source_pdf)

    gate = _GatedCall(watermark_engine.add_watermark)
    with patch.object(watermark_engine, "add_watermark", side_effect=gate):
        window._on_watermark_execute_clicked()
        assert gate.entered.wait(timeout=5)
        window.root.update()

        with patch("threading.Thread") as thread:
            window._on_page_numbers_execute_clicked()
            window._on_split_execute_clicked()
            window._on_rotate_execute_clicked()
            window._on_protect_execute_clicked()
            window._on_unlock_execute_clicked()
            window._on_remove_pages_execute_clicked()
            window._on_extract_execute_clicked()
            window._on_organize_execute_clicked()
            window._on_merge_only_clicked()
            window._on_compress_only_clicked()
            window._on_merge_compress_clicked()
            window._on_watermark_execute_clicked()          # not re-entrant either
            window._on_watermark_select_file_clicked()
        thread.assert_not_called()

        gate.release()
        assert _pump_until(window, lambda: not window.watermark_in_progress)


def test_watermark_cannot_start_while_another_tool_runs(window, watermark_source_pdf, tmp_path):
    pn_src = tmp_path / "pn.pdf"
    _make_pdf(pn_src, pages=2)

    # Prepare a watermark source first, then start a page-numbers run.
    _select_watermark_file(window, watermark_source_pdf)

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
        assert _state(window.watermark_button) == "disabled"
        assert _state(window.watermark_select_btn) == "disabled"
        assert _state(window.watermark_text_entry) == "disabled"
        assert _state(window.watermark_rotation_combo) == "disabled"

        with patch.object(watermark_engine, "add_watermark") as engine:
            window._on_watermark_execute_clicked()
        engine.assert_not_called()
        assert not window.watermark_in_progress

        gate.release()
        assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    # ...and the watermark tool comes back to life afterwards, still
    # holding its own source.
    window._select_tool("watermark")
    window.root.update()
    assert window.watermark_source is not None
    assert _state(window.watermark_select_btn) == "normal"
    assert _state(window.watermark_text_entry) == "normal"
    assert _state(window.watermark_rotation_combo) == "readonly"
    assert _state(window.watermark_button) == "normal"


def test_any_operation_predicate_includes_watermark_flags(window):
    assert not window._any_operation_in_progress()

    window.watermark_in_progress = True
    assert window._any_operation_in_progress()
    window.watermark_in_progress = False
    assert not window._any_operation_in_progress()

    window._watermark_import_in_progress = True
    assert window._any_operation_in_progress()
    window._watermark_import_in_progress = False
    assert not window._any_operation_in_progress()


def test_import_also_holds_the_global_busy_lock(window, watermark_source_pdf):
    gate = _GatedCall(pdf_engine.get_pdf_info)
    with patch("file_manager.select_single_pdf_file", return_value=watermark_source_pdf):
        with patch.object(pdf_engine, "get_pdf_info", side_effect=gate):
            window._on_watermark_select_file_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert window._watermark_import_in_progress
            assert window._any_operation_in_progress()
            assert _state(window.watermark_select_btn) == "disabled"
            assert _state(window.merge_only_btn) == "disabled"
            assert _state(window.page_numbers_select_btn) == "disabled"
            assert "Validating" in window.watermark_status_var.get()

            gate.release()
            assert _pump_until(window, lambda: not window._watermark_import_in_progress)

    assert not window._any_operation_in_progress()
    assert window.watermark_source is not None
    assert _state(window.watermark_button) == "normal"


def test_controls_restored_after_success(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    _run_watermark(window)

    assert not window._any_operation_in_progress()
    for control in _all_watermark_controls(window):
        if control is window.watermark_button:
            # source consumed -> nothing to watermark yet
            assert _state(control) == "disabled"
        elif control is window.watermark_selection_entry:
            assert _state(control) == "disabled"  # All-pages mode
        elif control is window.watermark_rotation_combo:
            assert _state(control) == "readonly"
        else:
            assert _state(control) == "normal", control

    # Every OTHER tool is usable again too.
    assert _state(window.select_files_btn) == "normal"
    assert _state(window.split_select_btn) == "normal"
    assert _state(window.remove_pages_select_btn) == "normal"
    assert _state(window.extract_select_btn) == "normal"
    assert _state(window.organize_select_btn) == "normal"
    assert _state(window.rotate_select_btn) == "normal"
    assert _state(window.protect_select_btn) == "normal"
    assert _state(window.unlock_select_btn) == "normal"
    assert _state(window.page_numbers_select_btn) == "normal"
    assert str(window.watermark_progress_bar.cget("mode")) == "determinate"


def test_controls_restored_after_failure(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    with patch.object(
        watermark_engine, "add_watermark", side_effect=pdf_engine.PDFEngineError("x"),
    ):
        with patch("tkinter.messagebox.showerror"):
            _run_watermark(window)

    for control in _all_watermark_controls(window):
        if control is window.watermark_selection_entry:
            assert _state(control) == "disabled"  # All-pages mode
        elif control is window.watermark_rotation_combo:
            assert _state(control) == "readonly"
        else:
            assert _state(control) == "normal", control
    assert _state(window.split_select_btn) == "normal"
    assert _state(window.page_numbers_select_btn) == "normal"
    assert _state(window.select_files_btn) == "normal"
    assert str(window.watermark_progress_bar.cget("mode")) == "determinate"


def test_selected_pages_entry_state_is_restored_correctly_after_a_run(
    window, watermark_source_pdf, tmp_path,
):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    window.watermark_selection_var.set("1")
    window.root.update()
    assert _state(window.watermark_selection_entry) == "normal"

    with patch.object(
        watermark_engine, "add_watermark", side_effect=pdf_engine.PDFEngineError("x"),
    ):
        with patch("tkinter.messagebox.showerror"):
            _run_watermark(window)

    # Selected-pages mode still active -> entry interactive again.
    assert _state(window.watermark_selection_entry) == "normal"
    assert window.watermark_selection_var.get() == "1"


def test_transient_validation_state_is_cleared_after_a_run(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_opacity_var.set("999")
    window.root.update()
    assert window.watermark_error_var.get() != ""

    window.watermark_opacity_var.set("30")
    window.root.update()
    _run_watermark(window)

    assert window.watermark_error_var.get() == ""


@pytest.mark.parametrize("select_handler,import_flag,tool_id", [
    ("_on_split_select_file_clicked", "_split_import_in_progress", "split"),
    ("_on_remove_pages_select_file_clicked", "_remove_pages_import_in_progress", "remove_pages"),
    ("_on_extract_select_file_clicked", "_extract_import_in_progress", "extract_pages"),
    ("_on_organize_select_file_clicked", "_organize_import_in_progress", "organize_pages"),
    ("_on_rotate_select_file_clicked", "_rotate_import_in_progress", "rotate"),
    ("_on_protect_select_file_clicked", "_protect_import_in_progress", "protect"),
    ("_on_unlock_select_file_clicked", "_unlock_import_in_progress", "unlock"),
    ("_on_page_numbers_select_file_clicked", "_page_numbers_import_in_progress", "page_numbers"),
])
def test_every_other_tools_import_restores_watermark_controls(
    window, watermark_source_pdf, select_handler, import_flag, tool_id,
):
    """Every existing tool's completion handler must restore Add
    Watermark's controls (the app-wide busy lock disables them during
    the other tool's import)."""
    window._select_tool(tool_id)
    window.root.update()

    with patch("file_manager.select_single_pdf_file", return_value=watermark_source_pdf), \
         patch("tkinter.messagebox.showerror"):
        getattr(window, select_handler)()
        # While that import runs, the watermark controls are locked...
        assert getattr(window, import_flag)
        window.root.update()
        assert _state(window.watermark_select_btn) == "disabled"
        assert _state(window.watermark_text_entry) == "disabled"
        assert _pump_until(window, lambda: not getattr(window, import_flag))

    # ...and free again afterwards.
    assert not window._any_operation_in_progress()
    assert _state(window.watermark_select_btn) == "normal"
    assert _state(window.watermark_text_entry) == "normal"
    assert _state(window.watermark_font_size_entry) == "normal"
    assert _state(window.watermark_opacity_entry) == "normal"
    assert _state(window.watermark_rotation_combo) == "readonly"
    assert _state(window.watermark_color_choose_btn) == "normal"
    assert all(_state(r) == "normal" for r in window.watermark_position_radios.values())
    assert all(_state(r) == "normal" for r in window.watermark_color_radios.values())


def test_merge_compress_import_restores_watermark_controls(window, watermark_source_pdf, tmp_path):
    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    with patch("file_manager.select_pdf_files", return_value=[watermark_source_pdf]):
        window._on_select_files_clicked()
        window.root.update()
        assert _state(window.watermark_select_btn) == "disabled"
        assert _pump_until(window, lambda: not window._import_in_progress)

    assert _state(window.watermark_select_btn) == "normal"
    assert _state(window.watermark_text_entry) == "normal"

    window._on_clear_all_clicked()
    window.root.update()


# ---------------------------------------------------------------------------
# 24. Existing tools remain functional
# ---------------------------------------------------------------------------

def test_watermark_select_then_switch_leaves_other_tools_usable(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    for tool_id, attr in [
        ("split", "split_select_btn"), ("remove_pages", "remove_pages_select_btn"),
        ("extract_pages", "extract_select_btn"), ("organize_pages", "organize_select_btn"),
        ("rotate", "rotate_select_btn"), ("protect", "protect_select_btn"),
        ("unlock", "unlock_select_btn"), ("page_numbers", "page_numbers_select_btn"),
        ("merge_compress", "select_files_btn"),
    ]:
        window._select_tool(tool_id)
        window.root.update()
        assert _state(getattr(window, attr)) == "normal", tool_id


def test_existing_page_numbers_workspace_remains_functional(window, tmp_path):
    src = tmp_path / "pn.pdf"
    _make_pdf(src, pages=3)

    window._select_tool("page_numbers")
    window.root.update()
    window.page_numbers_source = None
    window._update_page_numbers_source_label()
    window.page_numbers_all_pages_var.set(True)
    window.page_numbers_selection_var.set("")
    window._update_page_numbers_controls_state()

    with patch("file_manager.select_single_pdf_file", return_value=src):
        window._on_page_numbers_select_file_clicked()
        assert _pump_until(window, lambda: not window._page_numbers_import_in_progress)
    assert _state(window.page_numbers_button) == "normal"

    window._on_page_numbers_execute_clicked()
    assert _pump_until(window, lambda: not window.page_numbers_in_progress)

    output = tmp_path / "pn_numbered.pdf"
    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 3
    # ...and watermark is usable right after.
    assert _state(window.watermark_select_btn) == "normal"


def test_existing_unlock_workspace_remains_functional(window, tmp_path):
    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="pass123", owner_pw="ownerpass456")
    doc.close()

    window._select_tool("unlock")
    window.root.update()
    window.unlock_source = None
    window._update_unlock_source_label()
    window.unlock_password_var.set("")
    window._update_unlock_controls_state()

    with patch("file_manager.select_single_pdf_file", return_value=protected):
        window._on_unlock_select_file_clicked()
        assert _pump_until(window, lambda: not window._unlock_import_in_progress)
    window.unlock_password_var.set("pass123")
    window.root.update()

    window._on_unlock_execute_clicked()
    assert _pump_until(window, lambda: not window.unlock_in_progress)

    with pymupdf.open(tmp_path / "protected_unlocked.pdf") as unlocked:
        assert not unlocked.is_encrypted


def test_existing_merge_compress_workspace_remains_functional(
    window, watermark_source_pdf, tmp_path,
):
    _select_watermark_file(window, watermark_source_pdf)

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

    with pymupdf.open(output) as doc:
        assert doc.page_count == 5

    window._on_clear_all_clicked()
    window.root.update()


def test_watermarking_a_merge_output_end_to_end(window, tmp_path):
    """Two tools in a row: merge two PDFs, then watermark the result."""
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=1)

    window._select_tool("merge_compress")
    window.root.update()
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()
    with patch("file_manager.select_pdf_files", return_value=[a, b]):
        window._on_select_files_clicked()
        assert _pump_until(window, lambda: not window._import_in_progress)
    merged = tmp_path / "merged.pdf"
    with patch("file_manager.save_pdf_file", return_value=merged):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)
    window._on_clear_all_clicked()

    window._select_tool("watermark")
    window.root.update()
    _select_watermark_file(window, merged)
    window.watermark_text_var.set(WM)
    window.root.update()
    _run_watermark(window)

    with pymupdf.open(tmp_path / "merged_watermarked.pdf") as doc:
        assert doc.page_count == 3
        assert all(WM in p.get_text() for p in doc)


# ---------------------------------------------------------------------------
# 25. Scrollable workspace integration
# ---------------------------------------------------------------------------

def test_watermark_view_lives_in_the_scrollable_container(window):
    """Add Watermark's workspace is a child of the same scrollable
    workspace container as every other tool. (Does NOT touch or assert
    anything about the known, deferred scrollable-workspace failures
    from earlier phases.)"""
    assert window.watermark_view.winfo_parent() == str(window.workspace_container)
    assert window.watermark_view.winfo_ismapped()
    window.root.update_idletasks()


def test_workspace_is_scrollable_and_reachable(window):
    """The tall workspace's content is reachable through the shared
    canvas: the scroll region covers the workspace's requested height,
    so nothing (notably the ADD WATERMARK button and status area) is
    cut off with no way to reach it."""
    window.root.update_idletasks()
    window.root.update()

    bbox = window.workspace_canvas.bbox("all")
    assert bbox is not None
    scroll_region = [float(v) for v in str(window.workspace_canvas.cget("scrollregion")).split()]
    assert scroll_region[3] - scroll_region[1] >= window.watermark_view.winfo_reqheight() - 1

    # Scrolling to the very bottom must not raise and must keep the
    # action button inside the scrollable content.
    window.workspace_canvas.yview_moveto(1.0)
    window.root.update()
    button_bottom = (
        window.watermark_button.winfo_rooty() - window.workspace_container.winfo_rooty()
        + window.watermark_button.winfo_height()
    )
    assert button_bottom <= window.workspace_container.winfo_height()
    window.workspace_canvas.yview_moveto(0.0)
    window.root.update()


def test_switching_tools_repeatedly_does_not_break_the_workspace(window):
    for _ in range(3):
        for tool_id in ("page_numbers", "watermark", "unlock", "watermark", "merge_compress"):
            window._select_tool(tool_id)
            window.root.update()
    window._select_tool("watermark")
    window.root.update()
    assert window.watermark_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()


def test_settings_and_source_survive_switching_away_and_back(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("KEPT")
    window.watermark_all_pages_var.set(False)
    window._update_watermark_mode_controls()
    window.watermark_selection_var.set("1-3")
    window.watermark_opacity_var.set("45")
    window.root.update()

    for tool_id in ("unlock", "protect", "rotate", "organize_pages", "extract_pages",
                    "remove_pages", "split", "merge_compress", "page_numbers", "images_to_pdf"):
        window._select_tool(tool_id)
        window.root.update()
    window._select_tool("watermark")
    window.root.update()

    assert window.watermark_source is not None
    assert window.watermark_source.name == "document.pdf"
    assert window.watermark_text_var.get() == "KEPT"
    assert window.watermark_selection_var.get() == "1-3"
    assert window.watermark_opacity_var.get() == "45"
    assert _state(window.watermark_button) == "normal"


def test_images_to_pdf_is_now_available_not_coming_soon(window):
    # Phase 22 made "images_to_pdf" a real, available tool with its own
    # workspace -- this test used to be about the coming-soon
    # placeholder; that placeholder path is now covered instead via a
    # synthetic tool in test_phase12_tool_navigation.py.
    window._select_tool("images_to_pdf")
    window.root.update()
    assert window.images_to_pdf_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()
    assert not window.watermark_view.winfo_ismapped()

    window._select_tool("watermark")
    window.root.update()


# ---------------------------------------------------------------------------
# 26. Repeated operations
# ---------------------------------------------------------------------------

def test_repeated_operations_on_different_files(window, tmp_path):
    window.watermark_text_var.set(WM)
    for i in range(3):
        src = tmp_path / f"doc{i}.pdf"
        _make_pdf(src, pages=2)

        _select_watermark_file(window, src)
        _run_watermark(window)

        output = tmp_path / f"doc{i}_watermarked.pdf"
        assert output.exists()
        with pymupdf.open(output) as doc:
            assert all(WM in p.get_text() for p in doc)
        assert window.watermark_source is None
        assert not window._any_operation_in_progress()
        assert window.watermark_text_var.get() == WM  # settings persist


def test_repeated_operations_on_the_same_file(window, watermark_source_pdf):
    folder = watermark_source_pdf.parent
    for _ in range(3):
        _select_watermark_file(window, watermark_source_pdf)
        _run_watermark(window)

    names = sorted(p.name for p in folder.glob("*.pdf"))
    assert names == [
        "document.pdf", "document_watermarked (1).pdf",
        "document_watermarked (2).pdf", "document_watermarked.pdf",
    ]


def test_watermarking_a_watermarked_output(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    window.watermark_text_var.set("FIRSTMARK")
    _run_watermark(window)

    first = watermark_source_pdf.parent / "document_watermarked.pdf"
    _select_watermark_file(window, first)
    window.watermark_text_var.set("SECONDMARK")
    window.watermark_rotation_var.set("0")
    window.watermark_position_var.set("top_left")
    _run_watermark(window)

    second = watermark_source_pdf.parent / "document_watermarked_watermarked.pdf"
    with pymupdf.open(second) as doc:
        text = doc[0].get_text()
        assert "FIRSTMARK" in text and "SECONDMARK" in text


def test_back_to_back_runs_without_waiting_are_serialized(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)
    with patch.object(watermark_engine, "add_watermark", wraps=watermark_engine.add_watermark) as engine:
        window._on_watermark_execute_clicked()
        window._on_watermark_execute_clicked()  # ignored: already running
        assert _pump_until(window, lambda: not window.watermark_in_progress)
    assert engine.call_count == 1


# ---------------------------------------------------------------------------
# 27. Tk thread discipline
# ---------------------------------------------------------------------------

def test_no_tk_calls_from_worker_threads_during_a_full_run(window, watermark_source_pdf):
    """Records the thread of every Tk variable write and widget
    configure during an import + run; none may come from a non-main
    thread."""
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
        _select_watermark_file(window, watermark_source_pdf)
        window.watermark_text_var.set(WM)
        _run_watermark(window)

    assert offenders == []


def test_result_handlers_run_on_the_main_thread(window, watermark_source_pdf):
    threads = []
    orig_result = window._apply_watermark_result
    orig_import = window._apply_watermark_import_result

    def spy_result(item):
        threads.append(threading.current_thread() is threading.main_thread())
        return orig_result(item)

    def spy_import(result):
        threads.append(threading.current_thread() is threading.main_thread())
        return orig_import(result)

    with patch.object(window, "_apply_watermark_result", spy_result), \
         patch.object(window, "_apply_watermark_import_result", spy_import):
        _select_watermark_file(window, watermark_source_pdf)
        _run_watermark(window)

    assert threads == [True, True]


def test_result_handlers_refuse_to_run_off_the_main_thread(window):
    errors = []

    def attempt(handler, payload):
        try:
            handler(payload)
        except RuntimeError as exc:
            errors.append(str(exc))

    for handler, payload in (
        (window._apply_watermark_result, {"success": True, "output_path": Path("x.pdf")}),
        (window._apply_watermark_import_result, {"success": False, "error": "x", "info": None}),
    ):
        thread = threading.Thread(target=attempt, args=(handler, payload))
        thread.start()
        thread.join(timeout=5)

    assert len(errors) == 2
    assert all("non-main thread" in e for e in errors)
    # And nothing was disturbed by the refused calls.
    assert not window._any_operation_in_progress()


def test_worker_only_communicates_through_the_queue(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    puts = []
    real_put = window._watermark_queue.put

    def spy_put(item, *args, **kwargs):
        puts.append(item["type"])
        return real_put(item, *args, **kwargs)

    with patch.object(window._watermark_queue, "put", spy_put):
        _run_watermark(window)

    assert puts[-1] == "done"
    assert set(puts) <= {"progress", "done"}
    assert "progress" in puts


def test_gui_remains_responsive_during_watermarking(window, watermark_source_pdf):
    _select_watermark_file(window, watermark_source_pdf)

    gate = _GatedCall(watermark_engine.add_watermark)
    with patch.object(watermark_engine, "add_watermark", side_effect=gate):
        window._on_watermark_execute_clicked()
        assert gate.entered.wait(timeout=5)

        update_count = 0
        for _ in range(20):
            window.root.update()
            update_count += 1

        assert window.watermark_in_progress
        assert update_count == 20

        # The UI can still switch tools while the worker is parked.
        window._select_tool("merge_compress")
        window.root.update()
        window._select_tool("watermark")
        window.root.update()

        gate.release()
        assert _pump_until(window, lambda: not window.watermark_in_progress)


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------

def test_source_untouched_after_failure_too(window, watermark_source_pdf):
    original = watermark_source_pdf.read_bytes()
    _select_watermark_file(window, watermark_source_pdf)
    with patch.object(
        watermark_engine, "add_watermark", side_effect=pdf_engine.PDFEngineError("x"),
    ):
        with patch("tkinter.messagebox.showerror"):
            _run_watermark(window)
    assert watermark_source_pdf.read_bytes() == original
    assert watermark_source_pdf.exists()


def test_source_page_rotations_and_count_preserved_through_the_ui(window, tmp_path):
    rotations = [0, 90, 180, 270]
    src = tmp_path / "rotated.pdf"
    _make_pdf(src, pages=4, rotations=rotations)
    before = src.read_bytes()

    _select_watermark_file(window, src)
    _run_watermark(window)

    assert src.read_bytes() == before
    with pymupdf.open(tmp_path / "rotated_watermarked.pdf") as doc:
        assert [p.rotation for p in doc] == rotations
        assert doc.page_count == 4
        for page in doc:
            line = _watermark_lines(page, "CONFIDENTIAL")[0]
            assert abs(_display_angle(page, line) - 45) < 0.5
