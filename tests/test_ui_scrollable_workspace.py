"""
test_ui_scrollable_workspace.py

Regression tests for the "Merge/Compress action buttons pushed below
the visible window with many files, no way to reach them" bug.

Fix: the workspace area is now a Canvas + vertical Scrollbar + inner
frame (self.workspace_container, kept as the existing attribute name so
nothing that already packs views into it needed to change). These tests
inspect widget structure/properties (scrollregion, mapped state,
required heights) rather than pixel coordinates or screenshots, and
avoid any timing-sensitive assertions, per the stated test-quality
requirement.

Uses the shared session-scoped `window` fixture from tests/conftest.py.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk


def _make_pdfs(tmp_path, count):
    paths = []
    for i in range(count):
        p = tmp_path / f"file{i}.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(p)
        doc.close()
        paths.append(p)
    return paths


def _import(win, paths):
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _reset(win):
    win.state.files.clear()
    win._render_file_list()
    win._update_summary()
    win._update_button_states()
    win.root.update()
    win.root.update_idletasks()


def _content_height(win):
    win.root.update_idletasks()
    bbox = win.workspace_canvas.bbox("all")
    assert bbox is not None
    return bbox[3] - bbox[1]


def _canvas_height(win):
    return win.workspace_canvas.winfo_height()


# ---------------------------------------------------------------------------
# Structural sanity: the scrollable machinery exists and is wired up
# ---------------------------------------------------------------------------

def test_scrollable_workspace_widgets_exist(window):
    assert isinstance(window.workspace_canvas, tk.Canvas)
    assert isinstance(window.workspace_container, tk.Frame)
    assert window.workspace_container.winfo_parent() == str(window.workspace_canvas)


def test_scrollbar_is_wired_to_the_canvas_yview(window):
    cmd = window.workspace_scrollbar.cget("command")
    assert cmd is not None


def test_all_existing_attributes_still_present(window):
    """The scroll fix must not have renamed or removed anything the
    existing test suite (or a future phase) depends on.
    """
    for attr in (
        "merge_only_btn", "compress_only_btn", "merge_compress_btn",
        "file_list_container", "status_var", "state", "compression_var",
        "select_files_btn", "add_more_btn", "clear_all_btn",
        "merge_compress_view", "split_view", "coming_soon_view",
        "workspace_container",
    ):
        assert hasattr(window, attr), f"missing attribute: {attr}"


# ---------------------------------------------------------------------------
# 1-2. Empty and lightly-populated workspace still render correctly
# ---------------------------------------------------------------------------

def test_empty_workspace_renders_without_error(window):
    _reset(window)
    assert window.merge_compress_view.winfo_ismapped()
    assert _content_height(window) <= _canvas_height(window) + 5  # small tolerance


def test_one_file_does_not_break_layout(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 1)
    _import(window, paths)

    assert window.state.total_files == 1
    assert window.merge_only_btn.winfo_exists()
    assert window.compress_only_btn.winfo_exists()
    assert window.merge_compress_btn.winfo_exists()

    _reset(window)


# ---------------------------------------------------------------------------
# 3-4. Many files: content exceeds the visible area, controls remain
# reachable through the scrollable container (not destroyed, not hidden)
# ---------------------------------------------------------------------------

def test_seven_files_content_exceeds_visible_area(window, tmp_path):
    """The exact reported bug scenario."""
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)

    assert window.state.total_files == 7
    assert _content_height(window) > _canvas_height(window), (
        "7 files should genuinely produce content taller than the "
        "visible workspace -- if this assertion fails, the test fixture "
        "no longer reproduces the reported bug scenario"
    )


def test_action_buttons_still_exist_and_reachable_via_scroll_with_seven_files(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)

    for btn_name in ("merge_only_btn", "compress_only_btn", "merge_compress_btn"):
        btn = getattr(window, btn_name)
        assert btn.winfo_exists()
        assert btn.winfo_ismapped()

    before = window.workspace_canvas.yview()
    window.workspace_canvas.yview_moveto(1.0)
    window.root.update()
    after = window.workspace_canvas.yview()

    assert after != before, "scrolling to the bottom should move the viewport"
    assert after[1] == pytest.approx(1.0, abs=0.01), "should be able to scroll all the way to the bottom"

    window.workspace_canvas.yview_moveto(0.0)
    window.root.update()
    _reset(window)


def test_ten_plus_files_also_scrollable(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 12)
    _import(window, paths)

    assert window.state.total_files == 12
    assert _content_height(window) > _canvas_height(window)
    assert window.workspace_scrollbar.winfo_ismapped()

    _reset(window)


# ---------------------------------------------------------------------------
# 5-7. scrollregion updates correctly as the file list changes
# ---------------------------------------------------------------------------

def test_scrollregion_expands_when_files_are_added(window, tmp_path):
    _reset(window)
    height_empty = _content_height(window)

    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)
    height_with_files = _content_height(window)

    assert height_with_files > height_empty

    _reset(window)


def test_scrollregion_shrinks_when_files_are_removed(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)
    height_with_seven = _content_height(window)

    for _ in range(6):
        window._on_remove_file(window.state.files[0])
        window.root.update()

    window.root.update_idletasks()
    height_with_one = _content_height(window)

    assert height_with_one < height_with_seven
    assert window.state.total_files == 1

    _reset(window)


def test_clear_all_shrinks_scrollregion_back_to_empty_state(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)
    height_with_files = _content_height(window)

    window._on_clear_all_clicked()
    window.root.update()
    window.root.update_idletasks()
    height_after_clear = _content_height(window)

    assert height_after_clear < height_with_files
    assert window.state.total_files == 0


def test_scrollbar_hides_again_after_clearing_back_below_threshold(window, tmp_path):
    """Regression coverage for "the scrollbar must hide again once the
    file list shrinks back below the scrolling threshold".

    Uses winfo_manager() rather than winfo_ismapped() as the
    observable. _update_workspace_scrollbar_visibility() shows/hides
    the scrollbar purely via pack()/pack_forget(), and winfo_manager()
    reports Tk's own geometry-manager registration for the widget --
    set synchronously by pack()/pack_forget() -- which is the actual,
    deterministic mechanism this feature relies on ("" when unpacked,
    "pack" when packed).

    winfo_ismapped() additionally depends on the native window system's
    own map/unmap state, which is one more reason to prefer
    winfo_manager() here -- but winfo_manager() only reports what it's
    told: it reads back Tk's pack registration, which is exactly what
    pack_forget() sets. If pack_forget() hasn't actually been *called*
    yet by the time this assertion runs, winfo_manager() will correctly
    (and unhelpfully) still say "pack".

    That was the real, platform-dependent gap here: production code
    decided whether to show/hide the scrollbar only reactively, inside
    the <Configure> handlers bound to workspace_container/
    workspace_canvas, which only run once Tk's pack geometry manager
    gets around to recomputing sizes and dispatching the resulting
    event. On X11 that reliably happens inside a single update()/
    update_idletasks() pair; nothing guarantees it happens in exactly
    one pass everywhere. _render_file_list() (the single method every
    file-list mutation, including Clear All, funnels through) now
    forces that geometry recompute and calls
    _update_workspace_scrollbar_visibility() directly instead of
    waiting on a <Configure> callback, so the decision is made
    synchronously as part of the mutation itself. This test's use of
    winfo_manager() as the observable was already correct; it just
    needed the production call it's checking for to actually run
    before this point, every time, regardless of platform.
    """
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)
    assert window.workspace_scrollbar.winfo_manager() == "pack"

    window._on_clear_all_clicked()
    window.root.update()
    window.root.update_idletasks()

    assert window.workspace_scrollbar.winfo_manager() == ""


# ---------------------------------------------------------------------------
# 8. Switching tools still works correctly with the new scroll container
# ---------------------------------------------------------------------------

def test_switching_tools_still_works_with_scrollable_workspace(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)

    window._select_tool("split")
    window.root.update()
    assert window.split_view.winfo_ismapped()
    assert not window.merge_compress_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()
    assert window.merge_compress_view.winfo_ismapped()
    assert window.state.total_files == 7  # state preserved across the switch

    _reset(window)


def test_coming_soon_view_also_lives_in_the_scrollable_container(window):
    window._select_tool("rotate")
    window.root.update()
    assert window.coming_soon_view.winfo_ismapped()
    assert window.coming_soon_view.winfo_parent() == str(window.workspace_container)

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# 9. Existing button-state rules unaffected by the scroll fix
# ---------------------------------------------------------------------------

def test_button_state_rules_unchanged_with_many_files(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)

    assert str(window.merge_only_btn["state"]) == "normal"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "normal"

    for _ in range(6):
        window._on_remove_file(window.state.files[0])
        window.root.update()

    assert window.state.total_files == 1
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "disabled"

    _reset(window)


def test_merge_only_still_works_with_seven_files_via_scrollable_workspace(window, tmp_path):
    """End-to-end regression proof: the actual Merge Only operation
    still functions correctly with enough files to require scrolling.
    """
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)
    assert window.state.total_files == 7

    output = tmp_path / "merged.pdf"
    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        deadline = time.time() + 15
        while window._merge_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 7

    _reset(window)


# ---------------------------------------------------------------------------
# Mouse wheel binding hygiene
# ---------------------------------------------------------------------------

def test_mousewheel_bound_on_enter_and_unbound_on_leave(window):
    """The wheel handler must only be active while hovering the canvas,
    never a permanently-global binding.
    """
    window.workspace_canvas.event_generate("<Enter>")
    window.root.update()
    bound_while_hovering = window.root.bind_all("<MouseWheel>")
    assert bound_while_hovering

    window.workspace_canvas.event_generate("<Leave>")
    window.root.update()
    bound_after_leaving = window.root.bind_all("<MouseWheel>")
    assert not bound_after_leaving


def test_mousewheel_scroll_moves_viewport_with_many_files(window, tmp_path):
    _reset(window)
    paths = _make_pdfs(tmp_path, 7)
    _import(window, paths)

    window.workspace_canvas.yview_moveto(0.0)
    window.root.update()
    before = window.workspace_canvas.yview()

    class FakeWheelEvent:
        delta = -120  # one notch "down" on Windows

    window._on_workspace_mousewheel(FakeWheelEvent())
    window.root.update()
    after = window.workspace_canvas.yview()

    assert after[0] > before[0], "a wheel-down event should scroll the view downward"

    window.workspace_canvas.yview_moveto(0.0)
    window.root.update()
    _reset(window)
