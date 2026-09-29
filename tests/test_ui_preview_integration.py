"""
test_ui_preview_integration.py

Phase 24.1: tests for the thumbnail preview / drag-and-drop reordering
grid as integrated into Organize Pages, Images -> PDF, and PDF ->
Images (the reusable component itself is covered by
test_thumbnail_preview.py).

Every drag here goes through the grid's REAL press -> motion -> release
handlers (with fake mouse events carrying real screen coordinates
computed from the live card geometry), not just the pure
reorder_item() helper, so the gap-index -> final-position conversion,
threshold, and callback wiring are all exercised for real.

The invariants under test are the ones the spec names: there is exactly
ONE source of truth per tool (organize_order_var / self.images_to_pdf_
files / pdf_to_images_selection_var), the grid only ever mirrors or
edits it, and the visual order always equals the order that the engine
actually receives.

Uses the shared session-scoped `window` fixture from tests/conftest.py
(one Tk root; no extra roots are created here).
"""

import io
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

import organize_engine
import pdf_engine
import pdf_to_images_engine
import thumbnail_preview


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_pdf(path, pages=5, size=(200, 300)):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((20, 60), f"PAGE{i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return Path(path)


def _page_texts(path):
    with pymupdf.open(path) as doc:
        return [p.get_text().strip() for p in doc]


def _pump_until(win, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


def _wait_thumbnails(win, grid, timeout=15):
    assert _pump_until(win, lambda: grid._pending_jobs == 0, timeout)


class _E:
    """A minimal fake mouse event carrying only what PreviewGrid reads."""

    def __init__(self, x_root, y_root):
        self.x_root = x_root
        self.y_root = y_root


def _real_drag(grid, item_id, gap_index, *, release=True):
    """Drive the genuine press/motion/release handlers: press on the
    card, move past the drag threshold, and release over the LEFT edge
    of the card at `gap_index` (the gap before that card in the
    ORIGINAL order; N = the gap after the last card)."""
    cols = grid._columns()
    row, col = divmod(gap_index, cols)
    tx = grid.winfo_rootx() + col * grid.card_width + 2
    ty = grid.winfo_rooty() + row * grid.card_height + 10
    sx, sy = grid.winfo_rootx() + 5, grid.winfo_rooty() + 5
    grid._on_press(item_id, _E(sx, sy))
    grid._on_motion(item_id, _E(sx + 40, sy + 40))
    if release:
        grid._on_release(item_id, _E(tx, ty))


def _png(width):
    buf = io.BytesIO()
    Image.new("RGB", (width, width), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _state(widget):
    return str(widget["state"])


def _real_photos(grid):
    """The photos actually rendered for cards (excluding the shared
    grey placeholder)."""
    return [p for p in grid._photo_images.values() if p is not grid._placeholder_photo]


# ===========================================================================
# ORGANIZE PAGES
# ===========================================================================

def _select_organize(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_organize_select_file_clicked()
        assert _pump_until(win, lambda: not win._organize_import_in_progress)


def _reset_organize(win):
    win.organize_in_progress = False
    win._organize_import_in_progress = False
    win.organize_source = None
    win._update_organize_source_label()
    win.organize_order_var.set("")
    win.organize_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_organize_controls_state()
    win.root.update()


@pytest.fixture
def organize(window, tmp_path):
    _reset_organize(window)
    window._select_tool("organize_pages")
    window.root.update()
    yield window
    _reset_organize(window)
    window.organize_preview_grid.set_thumbnail_loader(window._organize_thumbnail_loader)
    window._select_tool("merge_compress")
    window.root.update()


@pytest.fixture
def organize5(organize, tmp_path):
    src = _make_pdf(tmp_path / "document.pdf", pages=5)
    _select_organize(organize, src)
    grid = organize.organize_preview_grid
    _wait_thumbnails(organize, grid)
    return organize, grid, src


def test_organize_thumbnails_are_generated(organize5):
    win, grid, _src = organize5
    assert len(grid._cards) == 5
    photos = _real_photos(grid)
    assert len(photos) == 5  # one genuinely rendered thumbnail per page
    assert all(p.width() > 1 and p.height() > 1 for p in photos)


def test_organize_thumbnails_are_small_not_full_resolution(organize, tmp_path):
    src = _make_pdf(tmp_path / "big.pdf", pages=2, size=(2000, 3000))
    _select_organize(organize, src)
    grid = organize.organize_preview_grid
    _wait_thumbnails(organize, grid)
    for photo in _real_photos(grid):
        assert max(photo.width(), photo.height()) <= grid.thumb_size


def test_organize_page_number_labels(organize5):
    _win, grid, _src = organize5
    labels = [grid._cards[i].caption_label.cget("text") for i in grid.get_order()]
    assert labels == ["Page 1", "Page 2", "Page 3", "Page 4", "Page 5"]


def test_organize_initial_order(organize5):
    win, grid, _src = organize5
    assert grid.get_order() == [0, 1, 2, 3, 4]
    assert win.organize_order_var.get() == "1,2,3,4,5"
    assert win._organize_current_order == [0, 1, 2, 3, 4]


def test_organize_click_selects_and_syncs_the_listbox_and_move_buttons(organize5):
    win, grid, _src = organize5
    grid._on_press(2, _E(0, 0))
    grid._on_release(2, _E(0, 0))
    assert grid.get_selected() == 2
    assert win.organize_order_listbox.curselection() == (2,)
    assert _state(win.organize_move_up_btn) == "normal"
    assert _state(win.organize_move_down_btn) == "normal"


def test_organize_first_and_last_selection_disable_the_matching_move_button(organize5):
    win, grid, _src = organize5
    grid._on_press(0, _E(0, 0))
    grid._on_release(0, _E(0, 0))
    assert _state(win.organize_move_up_btn) == "disabled"
    grid._on_press(4, _E(0, 0))
    grid._on_release(4, _E(0, 0))
    assert _state(win.organize_move_down_btn) == "disabled"


def test_organize_listbox_selection_highlights_the_matching_thumbnail(organize5):
    win, grid, _src = organize5
    win.organize_order_listbox.selection_clear(0, tk.END)
    win.organize_order_listbox.selection_set(3)
    win._on_organize_listbox_select()
    assert grid.get_selected() == 3


def test_organize_drag_page5_before_page2_matches_the_spec_example(organize5):
    win, grid, _src = organize5
    _real_drag(grid, 4, 1)
    assert win.organize_order_var.get() == "1,5,2,3,4"
    assert grid.get_order() == [0, 4, 1, 2, 3]
    # ...and what the existing engine's own parser receives:
    assert organize_engine.parse_page_order(win.organize_order_var.get(), 5) == [0, 4, 1, 2, 3]
    assert win._organize_current_order == [0, 4, 1, 2, 3]


def test_organize_drag_updates_the_listbox_too(organize5):
    win, grid, _src = organize5
    _real_drag(grid, 4, 1)
    rows = list(win.organize_order_listbox.get(0, tk.END))
    assert rows == ["1.  Page 1", "2.  Page 5", "3.  Page 2", "4.  Page 3", "5.  Page 4"]


def test_organize_grid_is_rebuilt_from_the_text_model_after_a_drag(organize5):
    """The text entry stays the single source of truth: after a drag,
    the grid has been re-derived from organize_order_var, so typing the
    same order by hand gives an identical grid."""
    win, grid, _src = organize5
    _real_drag(grid, 4, 1)
    dragged = grid.get_order()
    win.organize_order_var.set("1,5,2,3,4")
    assert grid.get_order() == dragged


@pytest.mark.parametrize("item,gap,expected", [
    (4, 0, "5,1,2,3,4"),   # first-item insertion
    (0, 5, "2,3,4,5,1"),   # last-item insertion
    (1, 4, "1,3,4,2,5"),   # middle insertion (forward)
    (3, 1, "1,4,2,3,5"),   # middle insertion (backward)
])
def test_organize_insertion_positions(organize5, item, gap, expected):
    win, grid, _src = organize5
    _real_drag(grid, item, gap)
    assert win.organize_order_var.get() == expected


@pytest.mark.parametrize("item", [0, 2, 4])
def test_organize_dragging_an_item_onto_itself_changes_nothing(organize5, item):
    win, grid, _src = organize5
    _real_drag(grid, item, item)
    _real_drag(grid, item, item + 1)
    assert win.organize_order_var.get() == "1,2,3,4,5"
    assert grid.get_order() == [0, 1, 2, 3, 4]


def test_organize_multiple_reorders_compose(organize5):
    win, grid, _src = organize5
    _real_drag(grid, 4, 1)           # 1,5,2,3,4
    _real_drag(grid, 1, 5)           # page 2 to the end -> 1,5,3,4,2
    _real_drag(grid, 0, 3)           # page 1 before the 4th card -> 5,3,1,4,2
    assert win.organize_order_var.get() == "5,3,1,4,2"
    assert organize_engine.parse_page_order(win.organize_order_var.get(), 5) == [4, 2, 0, 3, 1]


def test_organize_drag_cancellation_leaves_the_order_untouched(organize5):
    win, grid, _src = organize5
    sx, sy = grid.winfo_rootx() + 5, grid.winfo_rooty() + 5
    grid._on_press(4, _E(sx, sy))
    grid._on_motion(4, _E(sx + 300, sy + 10))
    grid.cancel_drag()
    grid._on_release(4, _E(sx + 300, sy + 10))
    assert win.organize_order_var.get() == "1,2,3,4,5"
    assert grid.get_order() == [0, 1, 2, 3, 4]


def test_organize_reset_restores_the_original_order(organize5):
    win, grid, _src = organize5
    _real_drag(grid, 4, 1)
    assert win.organize_order_var.get() != "1,2,3,4,5"
    win._on_organize_reset_clicked()
    assert win.organize_order_var.get() == "1,2,3,4,5"
    assert grid.get_order() == [0, 1, 2, 3, 4]
    assert list(win.organize_order_listbox.get(0, tk.END))[1] == "2.  Page 2"


def test_organize_clear_empties_order_grid_and_listbox_but_keeps_the_source(organize5):
    win, grid, _src = organize5
    _real_drag(grid, 4, 1)
    win._on_organize_clear_clicked()
    assert win.organize_order_var.get() == ""
    assert grid.get_order() == []
    assert win.organize_order_listbox.size() == 0
    assert win.organize_source is not None
    # ...and Reset brings the whole thing back.
    win._on_organize_reset_clicked()
    assert grid.get_order() == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("bad", ["1,1,2,3,4", "1,2,3,4", "0,1,2,3,4", "1,2,3,4,6", "-1,2,3,4,5", "a,b"])
def test_organize_invalid_order_is_still_rejected_by_existing_validation(organize5, bad):
    win, grid, _src = organize5
    win.organize_order_var.set(bad)
    with pytest.raises(organize_engine.OrganizeOrderError) as exc_info:
        organize_engine.parse_page_order(bad, 5)
    assert win.organize_error_var.get() == str(exc_info.value)  # message unchanged
    assert _state(win.organize_button) == "disabled"
    assert grid.get_order() == []  # nothing misleading is previewed for an invalid order
    assert win._organize_current_order is None


def test_organize_fixing_an_invalid_order_brings_the_preview_back(organize5):
    win, grid, _src = organize5
    win.organize_order_var.set("1,1,2,3,4")
    assert grid.get_order() == []
    win.organize_order_var.set("2,1,3,4,5")
    assert grid.get_order() == [1, 0, 2, 3, 4]
    assert _state(win.organize_button) == "normal"


def test_organize_end_to_end_output_follows_the_dragged_order(organize5, tmp_path):
    win, grid, src = organize5
    before = src.read_bytes()
    _real_drag(grid, 4, 1)
    out = tmp_path / "organized.pdf"
    with patch("file_manager.save_pdf_file", return_value=out):
        win._on_organize_execute_clicked()
        assert _pump_until(win, lambda: not win.organize_in_progress)

    texts = _page_texts(out)
    assert [t.split()[0] for t in texts] == ["PAGE1", "PAGE5", "PAGE2", "PAGE3", "PAGE4"]
    assert src.read_bytes() == before  # the source was never touched


def test_organize_source_untouched_by_preview_and_drag_alone(organize5):
    win, grid, src = organize5
    before = src.read_bytes()
    mtime = src.stat().st_mtime_ns
    _real_drag(grid, 4, 1)
    _real_drag(grid, 0, 5)
    _wait_thumbnails(win, grid)
    assert src.read_bytes() == before
    assert src.stat().st_mtime_ns == mtime


def test_organize_drag_is_ignored_while_an_operation_is_running(organize5):
    win, grid, _src = organize5
    win._set_controls_enabled(False)
    try:
        assert grid.reorderable is False
        _real_drag(grid, 4, 1)
        assert win.organize_order_var.get() == "1,2,3,4,5"
        # Even a callback that slips through is refused while busy.
        win.organize_in_progress = True
        win._on_organize_grid_reordered([4, 0, 1, 2, 3])
        assert win.organize_order_var.get() == "1,2,3,4,5"
    finally:
        win.organize_in_progress = False
        win._set_controls_enabled(True)
    assert grid.reorderable is True


def test_organize_switching_to_a_different_pdf_rebuilds_the_grid(organize5, tmp_path):
    win, grid, _src = organize5
    other = _make_pdf(tmp_path / "three.pdf", pages=3)
    _select_organize(win, other)
    _wait_thumbnails(win, grid)
    assert grid.get_order() == [0, 1, 2]
    assert len(grid._cards) == 3


def test_organize_stale_worker_result_is_ignored(organize5):
    """A render still in flight for a PREVIOUS order must never be
    applied after the order changes."""
    win, grid, _src = organize5
    gate = threading.Event()
    mode = {"v": 1}

    def loader(item):
        if mode["v"] == 1:
            gate.wait(timeout=10)
            return _png(7)   # stale marker
        return _png(9)       # current marker

    grid.set_thumbnail_loader(loader)
    win.organize_order_var.set("2,1,3,4,5")   # generation A: jobs park in the worker
    win.root.update()
    time.sleep(0.05)
    mode["v"] = 2
    win.organize_order_var.set("3,2,1,4,5")   # generation B supersedes A
    gate.set()
    _wait_thumbnails(win, grid)

    widths = {p.width() for p in _real_photos(grid)}
    assert widths == {9}, f"stale (7px) thumbnails leaked into the current view: {widths}"
    assert grid.get_order() == [2, 1, 0, 3, 4]


def test_organize_worker_never_touches_tk(organize, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=4)
    main = threading.main_thread()
    offenders = []
    real_set = tk.Variable.set
    real_configure = tk.Misc._configure
    real_image_init = tk.Image.__init__

    def spy_set(self, value):
        if threading.current_thread() is not main:
            offenders.append("Variable.set")
        return real_set(self, value)

    def spy_configure(self, cmd, cnf, kw):
        if threading.current_thread() is not main:
            offenders.append("configure")
        return real_configure(self, cmd, cnf, kw)

    def spy_image_init(self, *a, **k):
        if threading.current_thread() is not main:
            offenders.append("Image.__init__")
        return real_image_init(self, *a, **k)

    loader_threads = []
    original_loader = organize._organize_thumbnail_loader

    def recording_loader(item):
        loader_threads.append(threading.current_thread() is main)
        return original_loader(item)

    organize.organize_preview_grid.set_thumbnail_loader(recording_loader)
    with patch.object(tk.Variable, "set", spy_set), \
         patch.object(tk.Misc, "_configure", spy_configure), \
         patch.object(tk.Image, "__init__", spy_image_init):
        _select_organize(organize, src)
        _wait_thumbnails(organize, organize.organize_preview_grid)
        _real_drag(organize.organize_preview_grid, 3, 0)
        _wait_thumbnails(organize, organize.organize_preview_grid)

    assert offenders == []
    assert loader_threads and not any(loader_threads)  # loader ran, off the main thread


@pytest.mark.parametrize("pages", [5, 20, 50, 120])
def test_organize_scales_to_large_documents(organize, tmp_path, pages):
    src = _make_pdf(tmp_path / f"p{pages}.pdf", pages=pages)
    _select_organize(organize, src)
    grid = organize.organize_preview_grid
    assert len(grid._cards) == pages
    _wait_thumbnails(organize, grid, timeout=60)
    assert len(_real_photos(grid)) == pages
    # A drag still works on a large grid: last page to the front.
    _real_drag(grid, pages - 1, 0)
    assert organize.organize_order_var.get().split(",")[:2] == [str(pages), "1"]
    assert len(organize.organize_order_var.get().split(",")) == pages


def test_organize_cache_is_reused_not_re_rendered(organize5):
    win, grid, _src = organize5
    calls = []
    real = thumbnail_preview.render_pdf_page_thumbnail

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    with patch.object(thumbnail_preview, "render_pdf_page_thumbnail", counting):
        _real_drag(grid, 4, 1)          # rebuilds every card...
        _wait_thumbnails(win, grid)
    assert calls == []                   # ...entirely from the cache


def test_organize_shared_cache_stays_bounded(window):
    cache = window._shared_thumbnail_cache
    for i in range(thumbnail_preview.DEFAULT_CACHE_CAPACITY * 2):
        cache.put(("bound-test", i), b"x")
    assert len(cache) <= thumbnail_preview.DEFAULT_CACHE_CAPACITY


# ===========================================================================
# IMAGES -> PDF
# ===========================================================================

def _solid(path, color, size=(240, 160)):
    Image.new("RGB", size, color).save(path)
    return Path(path)


def _select_images(win, paths):
    with patch("file_manager.select_image_files", return_value=list(paths)):
        win._on_images_to_pdf_select_clicked()
        assert _pump_until(win, lambda: not win._images_to_pdf_import_in_progress)


def _reset_images(win):
    win.images_to_pdf_in_progress = False
    win._images_to_pdf_import_in_progress = False
    win.images_to_pdf_files.clear()
    win.images_to_pdf_status_var.set("Status: Ready")
    win._render_images_to_pdf_list()
    win._update_button_states()
    win._update_images_to_pdf_controls_state()
    win.root.update()


@pytest.fixture
def images(window):
    _reset_images(window)
    window._select_tool("images_to_pdf")
    window.root.update()
    yield window
    _reset_images(window)
    window.images_to_pdf_preview_grid.set_thumbnail_loader(window._images_to_pdf_thumbnail_loader)
    window._select_tool("merge_compress")
    window.root.update()


COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)]


@pytest.fixture
def images5(images, tmp_path):
    paths = [_solid(tmp_path / f"img{i + 1}.png", c) for i, c in enumerate(COLORS)]
    _select_images(images, paths)
    grid = images.images_to_pdf_preview_grid
    _wait_thumbnails(images, grid)
    return images, grid, paths


def _names(win):
    return [f.name for f in win.images_to_pdf_files]


def _page_center_colors(pdf_path):
    colors = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=36)
            colors.append(pix.pixel(pix.width // 2, pix.height // 2))
    return colors


def _close(actual, expected, tol=12):
    return all(abs(a - e) <= tol for a, e in zip(actual[:3], expected))


def test_images_thumbnails_are_generated(images5):
    _win, grid, _paths = images5
    assert len(grid._cards) == 5
    assert len(_real_photos(grid)) == 5


def test_images_thumbnail_labels_are_the_filenames(images5):
    _win, grid, _paths = images5
    labels = [grid._cards[i].caption_label.cget("text") for i in grid.get_order()]
    assert labels == ["img1.png", "img2.png", "img3.png", "img4.png", "img5.png"]


def test_images_initial_order_matches_the_list(images5):
    win, grid, _paths = images5
    assert _names(win) == ["img1.png", "img2.png", "img3.png", "img4.png", "img5.png"]
    assert grid.get_order() == [id(f) for f in win.images_to_pdf_files]


def test_images_drag_image5_before_image2(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 1)
    assert _names(win) == ["img1.png", "img5.png", "img2.png", "img3.png", "img4.png"]


def test_images_internal_imagefile_objects_are_reordered_in_place_by_identity(images5):
    win, grid, _paths = images5
    the_list = win.images_to_pdf_files
    objs = list(the_list)
    _real_drag(grid, id(objs[4]), 1)
    assert win.images_to_pdf_files is the_list          # the same list object
    assert [id(f) for f in the_list] == [id(objs[i]) for i in (0, 4, 1, 2, 3)]
    assert grid.get_order() == [id(f) for f in the_list]  # visual == internal


def test_images_forward_drag(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[0]), 3)
    assert _names(win) == ["img2.png", "img3.png", "img1.png", "img4.png", "img5.png"]


def test_images_generated_pdf_follows_the_reordered_images(images5, tmp_path):
    win, grid, paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 1)        # img5 before img2
    _real_drag(grid, id(objs[0]), 5)        # img1 to the end
    expected = [COLORS[4], COLORS[1], COLORS[2], COLORS[3], COLORS[0]]

    out = tmp_path / "out.pdf"
    with patch("file_manager.save_pdf_file", return_value=out):
        win._on_images_to_pdf_execute_clicked()
        assert _pump_until(win, lambda: not win.images_to_pdf_in_progress)

    colors = _page_center_colors(out)
    assert len(colors) == 5
    for actual, want in zip(colors, expected):
        assert _close(actual, want), (colors, expected)


def test_images_worker_receives_exactly_the_displayed_order(images5, tmp_path):
    win, grid, paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 1)
    seen = []
    import images_to_pdf_engine
    real = images_to_pdf_engine.images_to_pdf

    def spy(input_paths, *a, **k):
        seen.append(list(input_paths))
        return real(input_paths, *a, **k)

    out = tmp_path / "out.pdf"
    with patch.object(images_to_pdf_engine, "images_to_pdf", side_effect=spy), \
         patch("file_manager.save_pdf_file", return_value=out):
        win._on_images_to_pdf_execute_clicked()
        assert _pump_until(win, lambda: not win.images_to_pdf_in_progress)
    assert seen == [[paths[0], paths[4], paths[1], paths[2], paths[3]]]


def test_images_duplicate_paths_remain_distinguishable(images, tmp_path):
    a = _solid(tmp_path / "a.png", (255, 0, 0))
    b = _solid(tmp_path / "b.png", (0, 0, 255))
    _select_images(images, [a, a, b])
    grid = images.images_to_pdf_preview_grid
    _wait_thumbnails(images, grid)

    assert len(grid._cards) == 3
    assert len(set(grid.get_order())) == 3   # three distinct identities
    first, second, third = list(images.images_to_pdf_files)
    assert first is not second and first.path == second.path

    _real_drag(grid, id(second), 0)          # move the SECOND "a" to the front
    files = images.images_to_pdf_files
    assert files[0] is second and files[1] is first and files[2] is third


def test_images_duplicate_paths_both_reach_the_pdf(images, tmp_path):
    a = _solid(tmp_path / "a.png", (255, 0, 0))
    b = _solid(tmp_path / "b.png", (0, 0, 255))
    _select_images(images, [a, b, a])
    grid = images.images_to_pdf_preview_grid
    _wait_thumbnails(images, grid)
    objs = list(images.images_to_pdf_files)
    _real_drag(grid, id(objs[2]), 0)         # the second "a" first: a, a, b
    out = tmp_path / "out.pdf"
    with patch("file_manager.save_pdf_file", return_value=out):
        images._on_images_to_pdf_execute_clicked()
        assert _pump_until(images, lambda: not images.images_to_pdf_in_progress)
    colors = _page_center_colors(out)
    assert len(colors) == 3
    assert _close(colors[0], (255, 0, 0)) and _close(colors[1], (255, 0, 0))
    assert _close(colors[2], (0, 0, 255))


def test_images_remove_after_reorder(images5, tmp_path):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 1)                 # 1,5,2,3,4
    win._on_remove_image(objs[4])                    # drop img5
    assert _names(win) == ["img1.png", "img2.png", "img3.png", "img4.png"]
    assert grid.get_order() == [id(f) for f in win.images_to_pdf_files]
    assert len(grid._cards) == 4


def test_images_delete_key_removes_the_selected_thumbnail(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    grid._on_press(id(objs[2]), _E(0, 0))
    grid._on_release(id(objs[2]), _E(0, 0))
    grid._on_delete_key()
    assert _names(win) == ["img1.png", "img2.png", "img4.png", "img5.png"]


def test_images_clear_after_reorder(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 0)
    win._on_images_to_pdf_clear_clicked()
    assert win.images_to_pdf_files == []
    assert grid.get_order() == []
    assert _state(win.images_to_pdf_button) == "disabled"


def test_images_existing_row_move_buttons_still_work_and_resync_the_grid(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    win._on_move_image_up(objs[3])
    assert _names(win) == ["img1.png", "img2.png", "img4.png", "img3.png", "img5.png"]
    assert grid.get_order() == [id(f) for f in win.images_to_pdf_files]


def test_images_drag_then_row_move_stay_consistent(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 0)                 # 5,1,2,3,4
    win._on_move_image_down(objs[4])                 # 1,5,2,3,4
    assert _names(win) == ["img1.png", "img5.png", "img2.png", "img3.png", "img4.png"]
    assert grid.get_order() == [id(f) for f in win.images_to_pdf_files]


def test_images_sources_unchanged_by_preview_drag_and_conversion(images5, tmp_path):
    win, grid, paths = images5
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    objs = list(win.images_to_pdf_files)
    _real_drag(grid, id(objs[4]), 1)
    out = tmp_path / "out.pdf"
    with patch("file_manager.save_pdf_file", return_value=out):
        win._on_images_to_pdf_execute_clicked()
        assert _pump_until(win, lambda: not win.images_to_pdf_in_progress)
    for p, (data, mtime) in before.items():
        assert p.read_bytes() == data
        assert p.stat().st_mtime_ns == mtime


def test_images_large_image_thumbnail_does_not_use_original_dimensions(images, tmp_path):
    big = tmp_path / "huge.png"
    Image.new("RGB", (4000, 3000), (30, 60, 90)).save(big)
    _select_images(images, [big])
    grid = images.images_to_pdf_preview_grid
    _wait_thumbnails(images, grid)
    (photo,) = _real_photos(grid)
    assert max(photo.width(), photo.height()) <= grid.thumb_size
    assert photo.width() < 4000 and photo.height() < 3000
    assert images.images_to_pdf_files[0].width == 4000  # the model keeps true dimensions


def test_images_exif_rotated_jpeg_thumbnail_uses_display_orientation(images, tmp_path):
    path = tmp_path / "rot.jpg"
    im = Image.new("RGB", (200, 100), (200, 30, 30))   # landscape pixels...
    exif = im.getexif()
    exif[0x0112] = 6                                     # ...displayed rotated to portrait
    im.save(path, format="JPEG", exif=exif)
    _select_images(images, [path])
    grid = images.images_to_pdf_preview_grid
    _wait_thumbnails(images, grid)
    (photo,) = _real_photos(grid)
    assert photo.height() > photo.width()


def test_images_unrenderable_thumbnail_gets_a_placeholder_and_keeps_its_identity(images, tmp_path):
    good = _solid(tmp_path / "good.png", (255, 0, 0))
    flaky = _solid(tmp_path / "flaky.png", (0, 255, 0))
    _select_images(images, [good, flaky])
    objs = list(images.images_to_pdf_files)
    grid = images.images_to_pdf_preview_grid

    real = thumbnail_preview.render_image_file_thumbnail

    def selective(path, *a, **k):
        if Path(path).name == "flaky.png":
            raise ValueError("cannot render")
        return real(path, *a, **k)

    images.images_to_pdf_preview_grid.set_thumbnail_loader(
        lambda item: selective(item.thumb_key.path, grid.thumb_size)
    )
    images._render_images_to_pdf_list()
    _wait_thumbnails(images, grid)

    assert len(grid._cards) == 2                       # nothing dropped
    assert grid.get_order() == [id(objs[0]), id(objs[1])]  # nothing reordered
    assert grid._photo_images[id(objs[1])] is grid._placeholder_photo
    assert images.images_to_pdf_files == objs          # model untouched


def test_images_drag_is_ignored_while_an_operation_is_running(images5):
    win, grid, _paths = images5
    objs = list(win.images_to_pdf_files)
    win._set_controls_enabled(False)
    try:
        assert grid.reorderable is False
        _real_drag(grid, id(objs[4]), 0)
        assert _names(win)[0] == "img1.png"
        win.images_to_pdf_in_progress = True
        win._on_images_to_pdf_grid_reordered([id(f) for f in reversed(objs)])
        assert _names(win)[0] == "img1.png"
    finally:
        win.images_to_pdf_in_progress = False
        win._set_controls_enabled(True)
    assert grid.reorderable is True


def test_images_stale_worker_result_is_ignored(images5):
    win, grid, paths = images5
    gate = threading.Event()
    mode = {"v": 1}

    def loader(item):
        if mode["v"] == 1:
            gate.wait(timeout=10)
            return _png(7)
        return _png(9)

    grid.set_thumbnail_loader(loader)
    win._render_images_to_pdf_list()          # generation A parks in the worker
    time.sleep(0.05)
    mode["v"] = 2
    win._render_images_to_pdf_list()          # generation B supersedes it
    gate.set()
    _wait_thumbnails(win, grid)
    assert {p.width() for p in _real_photos(grid)} == {9}


@pytest.mark.parametrize("count", [5, 20, 50])
def test_images_scale_to_many_images(images, tmp_path, count):
    paths = [_solid(tmp_path / f"i{n:03d}.png", (n * 5 % 256, 100, 200), (60, 40))
             for n in range(count)]
    _select_images(images, paths)
    grid = images.images_to_pdf_preview_grid
    assert len(grid._cards) == count
    _wait_thumbnails(images, grid, timeout=60)
    assert len(_real_photos(grid)) == count
    objs = list(images.images_to_pdf_files)
    _real_drag(grid, id(objs[-1]), 0)
    assert images.images_to_pdf_files[0] is objs[-1]


def test_images_worker_never_touches_tk(images, tmp_path):
    paths = [_solid(tmp_path / f"i{n}.png", COLORS[n]) for n in range(3)]
    main = threading.main_thread()
    offenders = []
    real_set = tk.Variable.set
    real_configure = tk.Misc._configure
    real_image_init = tk.Image.__init__

    def spy_set(self, value):
        if threading.current_thread() is not main:
            offenders.append("Variable.set")
        return real_set(self, value)

    def spy_configure(self, cmd, cnf, kw):
        if threading.current_thread() is not main:
            offenders.append("configure")
        return real_configure(self, cmd, cnf, kw)

    def spy_image_init(self, *a, **k):
        if threading.current_thread() is not main:
            offenders.append("Image.__init__")
        return real_image_init(self, *a, **k)

    with patch.object(tk.Variable, "set", spy_set), \
         patch.object(tk.Misc, "_configure", spy_configure), \
         patch.object(tk.Image, "__init__", spy_image_init):
        _select_images(images, paths)
        grid = images.images_to_pdf_preview_grid
        _wait_thumbnails(images, grid)
        _real_drag(grid, grid.get_order()[2], 0)
        _wait_thumbnails(images, grid)
    assert offenders == []


# ===========================================================================
# PDF -> IMAGES
# ===========================================================================

def _select_p2i(win, path):
    with patch("file_manager.select_single_pdf_file", return_value=path):
        win._on_pdf_to_images_select_file_clicked()
        assert _pump_until(win, lambda: not win._pdf_to_images_import_in_progress)


def _reset_p2i(win):
    win.pdf_to_images_in_progress = False
    win._pdf_to_images_import_in_progress = False
    win.pdf_to_images_source = None
    win._update_pdf_to_images_source_label()
    win.pdf_to_images_all_pages_var.set(True)
    win.pdf_to_images_selection_var.set("")
    win.pdf_to_images_output_dir = None
    win._update_pdf_to_images_folder_label()
    win.pdf_to_images_status_var.set("Status: Ready")
    win._update_button_states()
    win._update_pdf_to_images_controls_state()
    win.root.update()


@pytest.fixture
def p2i(window):
    _reset_p2i(window)
    window._select_tool("pdf_to_images")
    window.root.update()
    yield window
    _reset_p2i(window)
    window.pdf_to_images_preview_grid.set_thumbnail_loader(window._pdf_to_images_thumbnail_loader)
    window._select_tool("merge_compress")
    window.root.update()


@pytest.fixture
def p2i6(p2i, tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf", pages=6)
    _select_p2i(p2i, src)
    grid = p2i.pdf_to_images_preview_grid
    _wait_thumbnails(p2i, grid)
    return p2i, grid, src


def _click(grid, item_id):
    grid._on_press(item_id, _E(0, 0))
    grid._on_release(item_id, _E(0, 0))


def _is_highlighted(grid, item_id):
    card = grid._cards[item_id]
    return str(card.cget("highlightbackground")).lower() == thumbnail_preview._COLOR_ACCENT.lower()


def test_p2i_page_thumbnails_are_generated(p2i6):
    _win, grid, _src = p2i6
    assert len(grid._cards) == 6
    assert len(_real_photos(grid)) == 6
    assert [grid._cards[i].caption_label.cget("text") for i in grid.get_order()] == \
        [f"Page {n}" for n in range(1, 7)]


def test_p2i_all_pages_mode_selects_every_thumbnail(p2i6):
    win, grid, _src = p2i6
    assert win.pdf_to_images_all_pages_var.get() is True
    assert grid.get_selected_ids() == set(range(6))
    assert all(_is_highlighted(grid, i) for i in range(6))


@pytest.mark.parametrize("text,expected", [
    ("2-3", {1, 2}), ("1,3,5", {0, 2, 4}), (" 6 ", {5}), ("1-3,2-4", {0, 1, 2, 3}),
])
def test_p2i_selected_pages_mirror_the_existing_range_model(p2i6, text, expected):
    win, grid, _src = p2i6
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set(text)
    assert grid.get_selected_ids() == expected
    resolved = pdf_to_images_engine.resolve_pages_to_render(text, 6)
    assert set(resolved) == expected   # the grid shows exactly what will render


def test_p2i_selected_and_unselected_visual_state(p2i6):
    win, grid, _src = p2i6
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set("2,4")
    assert _is_highlighted(grid, 1) and _is_highlighted(grid, 3)
    assert not any(_is_highlighted(grid, i) for i in (0, 2, 4, 5))


def test_p2i_click_in_all_pages_mode_switches_to_a_single_selected_page(p2i6):
    win, grid, _src = p2i6
    _click(grid, 2)
    assert win.pdf_to_images_all_pages_var.get() is False
    assert win.pdf_to_images_selection_var.get() == "3"
    assert grid.get_selected_ids() == {2}


def test_p2i_click_toggles_pages_in_and_out(p2i6):
    win, grid, _src = p2i6
    _click(grid, 2)
    _click(grid, 4)
    assert win.pdf_to_images_selection_var.get() == "3,5"
    _click(grid, 2)                     # toggle page 3 back off
    assert win.pdf_to_images_selection_var.get() == "5"
    assert grid.get_selected_ids() == {4}


def test_p2i_click_preserves_the_typed_page_order(p2i6):
    """The established semantics: typed order IS render order. A click
    must append/remove without silently re-sorting what was typed."""
    win, grid, _src = p2i6
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set("5,3")
    _click(grid, 0)                                   # add page 1
    assert win.pdf_to_images_selection_var.get() == "5,3,1"
    resolved = pdf_to_images_engine.resolve_pages_to_render(
        win.pdf_to_images_selection_var.get(), 6)
    assert resolved == [4, 2, 0]                      # render order = typed order


def test_p2i_click_preserves_typed_ranges_as_far_as_possible(p2i6):
    win, grid, _src = p2i6
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set("1-3,6")
    _click(grid, 1)                                   # remove page 2
    assert win.pdf_to_images_selection_var.get() == "1,3,6"
    _click(grid, 1)                                   # add it back at the end
    assert win.pdf_to_images_selection_var.get() == "1,3,6,2"


def test_p2i_consecutive_runs_are_compressed_in_the_existing_range_syntax(p2i6):
    win, grid, _src = p2i6
    for i in (0, 1, 2, 3):
        _click(grid, i)
    assert win.pdf_to_images_selection_var.get() == "1-4"


def test_p2i_clearing_the_selection_clears_the_highlight_and_disables_convert(p2i6, tmp_path):
    win, grid, _src = p2i6
    win.pdf_to_images_output_dir = tmp_path / "out"
    _click(grid, 1)
    assert grid.get_selected_ids() == {1}
    _click(grid, 1)                                   # toggle the only page off
    assert win.pdf_to_images_selection_var.get() == ""
    assert grid.get_selected_ids() == set()
    assert not any(_is_highlighted(grid, i) for i in range(6))
    assert _state(win.pdf_to_images_button) == "disabled"


def test_p2i_invalid_range_text_clears_the_highlight_rather_than_showing_stale_state(p2i6):
    win, grid, _src = p2i6
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set("2-3")
    assert grid.get_selected_ids() == {1, 2}
    win.pdf_to_images_selection_var.set("2-99")
    assert grid.get_selected_ids() == set()
    assert win.pdf_to_images_error_var.get() != ""


def test_p2i_switching_back_to_all_pages_reselects_everything(p2i6):
    win, grid, _src = p2i6
    _click(grid, 1)
    win.pdf_to_images_all_pages_var.set(True)
    win._update_pdf_to_images_mode_controls()
    assert grid.get_selected_ids() == set(range(6))


def test_p2i_changing_the_source_pdf_rebuilds_the_grid(p2i6, tmp_path):
    win, grid, _src = p2i6
    other = _make_pdf(tmp_path / "other.pdf", pages=3)
    _select_p2i(win, other)
    _wait_thumbnails(win, grid)
    assert len(grid._cards) == 3
    assert grid.get_selected_ids() == {0, 1, 2}


def test_p2i_grid_clears_when_the_source_is_consumed_after_a_conversion(p2i6, tmp_path):
    win, grid, _src = p2i6
    win.pdf_to_images_output_dir = tmp_path / "out"
    win._update_pdf_to_images_feedback()
    win._on_pdf_to_images_execute_clicked()
    assert _pump_until(win, lambda: not win.pdf_to_images_in_progress)
    assert win.pdf_to_images_source is None
    assert grid.get_order() == []


def test_p2i_stale_thumbnail_result_is_ignored(p2i6, tmp_path):
    win, grid, _src = p2i6
    gate = threading.Event()
    mode = {"v": 1}

    def loader(item):
        if mode["v"] == 1:
            gate.wait(timeout=10)
            return _png(7)
        return _png(9)

    grid.set_thumbnail_loader(loader)
    first = _make_pdf(tmp_path / "first.pdf", pages=4)
    _select_p2i(win, first)                 # generation A parks in the worker
    time.sleep(0.05)
    mode["v"] = 2
    second = _make_pdf(tmp_path / "second.pdf", pages=5)
    _select_p2i(win, second)                # generation B supersedes it
    gate.set()
    _wait_thumbnails(win, grid)
    assert len(grid._cards) == 5
    assert {p.width() for p in _real_photos(grid)} == {9}


def test_p2i_is_not_reorderable_and_a_drag_never_changes_selection_semantics(p2i6):
    """A "drag" gesture on this non-reorderable grid can never change
    the page ORDER (there is none to change) -- the press itself still
    behaves as an ordinary click-toggle, exactly like clicking normally
    would (see _on_pdf_to_images_thumbnail_clicked()), since a click and
    the start of a drag are indistinguishable until motion occurs."""
    win, grid, _src = p2i6
    assert grid.reorderable is False
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set("1,2,3")
    _real_drag(grid, 5, 0)
    assert grid.get_order() == [0, 1, 2, 3, 4, 5]
    # The press toggled page 6 in (an ordinary click, not a reorder);
    # the range syntax is still the existing, unmodified grammar.
    assert set(pdf_to_images_engine.resolve_pages_to_render(
        win.pdf_to_images_selection_var.get(), 6)) == {0, 1, 2, 5}


def test_p2i_export_semantics_unchanged_end_to_end(p2i6, tmp_path):
    """Render order/filenames still follow the existing typed-range
    model exactly, regardless of how the selection was produced."""
    win, grid, _src = p2i6
    out = tmp_path / "out"
    win.pdf_to_images_output_dir = out
    win.pdf_to_images_all_pages_var.set(False)
    win._update_pdf_to_images_mode_controls()
    win.pdf_to_images_selection_var.set("5,3")
    _click(grid, 0)                                    # -> "5,3,1"
    win._on_pdf_to_images_execute_clicked()
    assert _pump_until(win, lambda: not win.pdf_to_images_in_progress)
    names = sorted(p.name for p in out.glob("*.png"))
    assert names == ["doc_page_001.png", "doc_page_003.png", "doc_page_005.png"]


def test_p2i_click_ignored_while_busy_and_grid_resyncs_to_the_text(p2i6):
    win, grid, _src = p2i6
    win.pdf_to_images_in_progress = True
    try:
        _click(grid, 2)   # the grid marks it, but the handler must resync
    finally:
        win.pdf_to_images_in_progress = False
    assert win.pdf_to_images_all_pages_var.get() is True
    assert grid.get_selected_ids() == set(range(6))   # still mirrors "All pages"


@pytest.mark.parametrize("pages", [5, 20, 50, 120])
def test_p2i_scales_to_large_documents(p2i, tmp_path, pages):
    src = _make_pdf(tmp_path / f"p{pages}.pdf", pages=pages)
    _select_p2i(p2i, src)
    grid = p2i.pdf_to_images_preview_grid
    assert len(grid._cards) == pages
    _wait_thumbnails(p2i, grid, timeout=60)
    assert len(_real_photos(grid)) == pages
    assert grid.get_selected_ids() == set(range(pages))


def test_p2i_source_untouched_by_preview_and_selection(p2i6):
    win, grid, src = p2i6
    before = src.read_bytes()
    mtime = src.stat().st_mtime_ns
    _click(grid, 1)
    _click(grid, 3)
    _wait_thumbnails(win, grid)
    assert src.read_bytes() == before
    assert src.stat().st_mtime_ns == mtime


def test_p2i_worker_never_touches_tk(p2i, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=4)
    main = threading.main_thread()
    offenders = []
    real_set = tk.Variable.set
    real_configure = tk.Misc._configure
    real_image_init = tk.Image.__init__

    def spy_set(self, value):
        if threading.current_thread() is not main:
            offenders.append("Variable.set")
        return real_set(self, value)

    def spy_configure(self, cmd, cnf, kw):
        if threading.current_thread() is not main:
            offenders.append("configure")
        return real_configure(self, cmd, cnf, kw)

    def spy_image_init(self, *a, **k):
        if threading.current_thread() is not main:
            offenders.append("Image.__init__")
        return real_image_init(self, *a, **k)

    with patch.object(tk.Variable, "set", spy_set), \
         patch.object(tk.Misc, "_configure", spy_configure), \
         patch.object(tk.Image, "__init__", spy_image_init):
        _select_p2i(p2i, src)
        grid = p2i.pdf_to_images_preview_grid
        _wait_thumbnails(p2i, grid)
        _click(grid, 1)
        _wait_thumbnails(p2i, grid)
    assert offenders == []


# ===========================================================================
# Cross-tool
# ===========================================================================

def test_no_preview_timer_keeps_running_once_thumbnails_are_loaded(organize5):
    win, grid, _src = organize5
    time.sleep(0.1)
    win.root.update()
    assert grid._poll_scheduled is False
    assert grid._pending_jobs == 0


def test_switching_tools_mid_render_does_not_break_anything(organize, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=30)
    _select_organize(organize, src)
    for tool_id in ("watermark", "images_to_pdf", "pdf_to_images", "merge_compress", "organize_pages"):
        organize._select_tool(tool_id)
        organize.root.update()
    grid = organize.organize_preview_grid
    _wait_thumbnails(organize, grid, timeout=60)
    assert len(grid._cards) == 30
    assert len(_real_photos(grid)) == 30


def test_all_three_grids_are_independent_widgets_sharing_one_bounded_cache(window):
    grids = [window.organize_preview_grid, window.images_to_pdf_preview_grid,
             window.pdf_to_images_preview_grid]
    assert len({id(g) for g in grids}) == 3
    assert all(g.cache is window._shared_thumbnail_cache for g in grids)
    assert grids[0].reorderable is True and grids[1].reorderable is True
    assert grids[2].reorderable is False
