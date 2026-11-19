from pathlib import Path
from time import monotonic, sleep
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

import models
import thumbnail_preview
import tool_registry
import ui_theme


def _point(widget, x=12, y=None):
    return SimpleNamespace(
        x_root=widget.winfo_rootx() + x,
        y_root=widget.winfo_rooty() + (widget.winfo_height() // 2 if y is None else y),
    )


def _finish_animation(root, slide):
    deadline = monotonic() + 2
    while slide._timer is not None and monotonic() < deadline:
        root.update()
        sleep(0.01)
    root.update()
    assert slide._timer is None


def test_settings_theme_carries_across_tools_and_persists(window, tmp_path, monkeypatch):
    preferences = tmp_path / "settings.json"
    monkeypatch.setattr(window, "_settings_path", preferences)
    original_tool = window.current_tool_id
    original_entry = window.rotate_selection_var.get()
    try:
        window._select_tool("rotate")
        window.rotate_selection_var.set("1-2")
        popup = window.root.tk.call("ttk::combobox::PopdownWindow", window.watermark_rotation_combo)
        listbox = f"{popup}.f.l"
        window.settings_btn.invoke()
        dialog = window._settings_window
        window.root.update()
        window.settings_btn.invoke()
        assert window._settings_window is dialog

        dark = next(
            widget for frame in dialog.winfo_children() for group in frame.winfo_children()
            for widget in group.winfo_children()
            if isinstance(widget, ttk.Radiobutton) and widget.cget("value") == "dark"
        )
        dark.invoke()
        assert window.current_tool_id == "rotate"
        assert window.rotate_selection_var.get() == "1-2"
        assert ui_theme.load_theme(preferences) == "dark"
        dark_background = ui_theme.color("#f5f6f8", "dark")
        assert window.root.cget("bg") == dialog.cget("bg") == dark_background
        assert window.root.tk.call(listbox, "cget", "-background") == ui_theme.color("#ffffff", "dark")

        for tool in tool_registry.get_all_tools():
            window._select_tool(tool.id)
            assert window.current_tool_id == tool.id
            visible = window.workspace_container.pack_slaves()
            assert len(visible) == 1
            assert visible[0].cget("bg") == dark_background

        window._set_appearance("light")
        assert ui_theme.load_theme(preferences) == "light"
        assert window.theme_var.get() == "light"
        assert window.root.cget("bg") == "#f5f6f8"
        assert window.root.tk.call(listbox, "cget", "-background") == "#ffffff"
    finally:
        window._set_appearance("light")
        if window._settings_window and window._settings_window.winfo_exists():
            window._settings_window.destroy()
        window.rotate_selection_var.set(original_entry)
        window._select_tool(original_tool)


def test_settings_opens_the_existing_update_checker(window):
    window.settings_btn.invoke()
    dialog = window._settings_window
    try:
        with (
            patch("update_checker.check_for_update", return_value={"update_available": False}) as check,
            patch("ui.messagebox.showinfo") as notice,
        ):
            window.settings_update_btn.invoke()
        check.assert_called_once_with()
        assert notice.call_args.kwargs["parent"] is dialog
    finally:
        dialog.destroy()


def test_merge_rows_shift_live_and_commit_only_on_drop(window):
    window._select_tool("merge_compress")
    window.state.files[:] = [
        models.PDFFile(Path(f"file{index}.pdf"), f"file{index}.pdf", 1024, 1)
        for index in range(3)
    ]
    window._update_button_states()
    window.root.update()
    original = window.state.files.copy()
    drag = window._file_drag

    try:
        first, middle, last = drag.rows
        start_y = first.winfo_y()
        target = _point(last)
        drag.press(first, _point(first))
        drag.motion(target)
        _finish_animation(window.root, drag._slide)
        assert middle.winfo_y() == start_y
        assert drag._slot is not None
        assert window.state.files == original

        drag.release(target)
        window.root.update()
        assert window.state.files == [original[1], original[2], original[0]]
        assert all(row.winfo_manager() == "pack" for row in drag.rows)
        assert drag.parent.grab_current() is None
    finally:
        drag.cancel()
        window.state.files.clear()
        window._update_button_states()


def test_thumbnails_shift_live_and_restore_positions_when_cancelled(window):
    grid = thumbnail_preview.PreviewGrid(window.root, thumb_size=80)
    grid.place(x=220, y=90, width=500)
    try:
        grid.set_items([thumbnail_preview.PreviewItem(index, str(index)) for index in range(3)])
        window.root.update()
        first, second, third = (grid._cards[index] for index in range(3))
        first_x = first.winfo_x()
        second_x = second.winfo_x()
        target = _point(third, x=third.winfo_width() - 5)

        grid._on_press(0, _point(first))
        grid._on_motion(0, target)
        _finish_animation(window.root, grid._slide)
        assert first.winfo_x() > second_x
        assert second.winfo_x() == first_x
        assert grid.get_order() == [0, 1, 2]

        grid.cancel_drag()
        window.root.update()
        assert second.winfo_x() == second_x
        assert not grid._insertion_indicator.place_info()
        assert grid.get_order() == [0, 1, 2]
    finally:
        grid.destroy()
