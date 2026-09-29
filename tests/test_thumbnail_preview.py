"""
test_thumbnail_preview.py

Focused tests for the reusable Phase 24.1 preview infrastructure
(thumbnail_preview.py): ThumbnailCache's bounded-LRU behavior, the pure
PDF/image thumbnail renderers, and PreviewGrid's item management,
selection, reorder logic, async thumbnail loading (including stale-
result/generation protection), and safe-placeholder-on-failure
behavior.

Uses the shared session-scoped `window` fixture from tests/conftest.py
purely for its live Tk root (no new Tk() root is created here, per this
project's "don't create unnecessary Tk roots" convention); each test
builds and tears down its own PreviewGrid.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thumbnail_preview import (
    DEFAULT_CACHE_CAPACITY,
    PreviewGrid,
    PreviewItem,
    ThumbnailCache,
    render_image_file_thumbnail,
    render_pdf_page_thumbnail,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf(path, pages=5, size=(200, 300), rotations=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((20, 50), f"PAGE{i + 1}", fontsize=16)
        if rotations and rotations[i]:
            page.set_rotation(rotations[i])
    doc.save(path)
    doc.close()
    return Path(path)


def _pump_until(root, predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        root.update()
        time.sleep(0.01)
    return False


@pytest.fixture
def grid(window):
    g = PreviewGrid(window.root, thumb_size=100)
    g.pack()
    window.root.update()
    yield g
    if g.winfo_exists():
        g.destroy()
    window.root.update()


# ---------------------------------------------------------------------------
# ThumbnailCache
# ---------------------------------------------------------------------------

def test_cache_basic_get_put():
    cache = ThumbnailCache(capacity=10)
    assert cache.get("a") is None
    cache.put("a", b"data")
    assert cache.get("a") == b"data"


def test_cache_default_capacity():
    assert DEFAULT_CACHE_CAPACITY > 0


def test_cache_rejects_non_positive_capacity():
    with pytest.raises(ValueError):
        ThumbnailCache(capacity=0)
    with pytest.raises(ValueError):
        ThumbnailCache(capacity=-1)


def test_cache_evicts_least_recently_used_when_full():
    cache = ThumbnailCache(capacity=3)
    cache.put("a", b"1")
    cache.put("b", b"2")
    cache.put("c", b"3")
    cache.put("d", b"4")  # evicts "a" (least recently used)
    assert cache.get("a") is None
    assert cache.get("b") == b"2"
    assert cache.get("c") == b"3"
    assert cache.get("d") == b"4"
    assert len(cache) == 3


def test_cache_get_refreshes_recency():
    cache = ThumbnailCache(capacity=2)
    cache.put("a", b"1")
    cache.put("b", b"2")
    cache.get("a")  # "a" is now most-recently-used
    cache.put("c", b"3")  # evicts "b", not "a"
    assert cache.get("a") == b"1"
    assert cache.get("b") is None
    assert cache.get("c") == b"3"


def test_cache_never_grows_without_bound(tmp_path):
    cache = ThumbnailCache(capacity=50)
    for i in range(500):
        cache.put(i, b"x" * 100)
    assert len(cache) == 50


def test_cache_clear():
    cache = ThumbnailCache(capacity=10)
    cache.put("a", b"1")
    cache.clear()
    assert len(cache) == 0
    assert cache.get("a") is None


def test_cache_is_thread_safe_under_concurrent_use():
    import threading

    cache = ThumbnailCache(capacity=100)
    errors = []

    def worker(n):
        try:
            for i in range(200):
                cache.put(f"{n}-{i}", b"x")
                cache.get(f"{n}-{i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []


# ---------------------------------------------------------------------------
# render_pdf_page_thumbnail()
# ---------------------------------------------------------------------------

def test_pdf_thumbnail_is_valid_png_within_size_bound(tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=1, size=(400, 600))
    data = render_pdf_page_thumbnail(src, 0, max_size=120)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(__import__("io").BytesIO(data))
    img.load()
    assert max(img.size) <= 120
    assert img.width / img.height == pytest.approx(400 / 600, rel=0.05)


def test_pdf_thumbnail_is_deliberately_small_not_full_resolution(tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=1, size=(2000, 3000))
    data = render_pdf_page_thumbnail(src, 0, max_size=140)
    img = Image.open(__import__("io").BytesIO(data))
    img.load()
    assert max(img.size) <= 140


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_thumbnail_respects_rotation(tmp_path, rotation):
    """A thumbnail's aspect ratio must swap for a 90/270 degree page,
    exactly like the full-size renders in pdf_to_images_engine.py."""
    src = _make_pdf(tmp_path / "d.pdf", pages=1, size=(200, 300), rotations=[rotation])
    data = render_pdf_page_thumbnail(src, 0, max_size=100)
    img = Image.open(__import__("io").BytesIO(data))
    img.load()
    if rotation in (90, 270):
        assert img.width > img.height
    else:
        assert img.height > img.width


def test_pdf_thumbnail_does_not_modify_the_source(tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=3)
    before = src.read_bytes()
    render_pdf_page_thumbnail(src, 1, max_size=100)
    assert src.read_bytes() == before


def test_pdf_thumbnail_correct_page_selected(tmp_path):
    """Different pages produce genuinely different thumbnail bytes."""
    src = _make_pdf(tmp_path / "d.pdf", pages=3)
    t0 = render_pdf_page_thumbnail(src, 0, max_size=100)
    t1 = render_pdf_page_thumbnail(src, 1, max_size=100)
    assert t0 != t1


# ---------------------------------------------------------------------------
# render_image_file_thumbnail()
# ---------------------------------------------------------------------------

def test_image_thumbnail_is_valid_png_within_size_bound(tmp_path):
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (1000, 500), (200, 50, 50)).save(path, format="JPEG")
    data = render_image_file_thumbnail(path, max_size=120)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(__import__("io").BytesIO(data))
    img.load()
    assert max(img.size) <= 120


def test_image_thumbnail_does_not_load_full_original_dimensions_directly(tmp_path):
    path = tmp_path / "huge.png"
    Image.new("RGB", (4000, 3000), (10, 10, 10)).save(path)
    data = render_image_file_thumbnail(path, max_size=140)
    img = Image.open(__import__("io").BytesIO(data))
    img.load()
    assert max(img.size) <= 140
    assert len(data) < 200_000  # nowhere near a 4000x3000 raw/PNG size


def test_image_thumbnail_transparent_png_composited_onto_white(tmp_path):
    path = tmp_path / "trans.png"
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(path)
    data = render_image_file_thumbnail(path, max_size=100)
    img = Image.open(__import__("io").BytesIO(data)).convert("RGB")
    img.load()
    assert img.getpixel((img.width // 2, img.height // 2)) == (255, 255, 255)


def test_image_thumbnail_does_not_modify_source(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (100, 100), (1, 2, 3)).save(path)
    before = path.read_bytes()
    render_image_file_thumbnail(path, max_size=50)
    assert path.read_bytes() == before


def test_image_thumbnail_corrupt_file_raises_rather_than_silently_succeeding(tmp_path):
    """PreviewGrid's own worker is what turns this into a safe
    placeholder (see the PreviewGrid tests below) -- the render
    function itself is expected to raise for bad input, not swallow it."""
    path = tmp_path / "bad.png"
    path.write_bytes(b"not an image" * 10)
    with pytest.raises(Exception):
        render_image_file_thumbnail(path, max_size=100)


# ---------------------------------------------------------------------------
# PreviewGrid: items / layout
# ---------------------------------------------------------------------------

def test_set_items_creates_one_card_per_item(grid):
    grid.set_items([PreviewItem(item_id=i, label=f"Item {i}") for i in range(4)])
    assert len(grid._cards) == 4
    assert grid.get_order() == [0, 1, 2, 3]


def test_set_items_replaces_previous_items(grid):
    grid.set_items([PreviewItem(item_id=0, label="a")])
    grid.set_items([PreviewItem(item_id=1, label="b"), PreviewItem(item_id=2, label="c")])
    assert grid.get_order() == [1, 2]
    assert len(grid._cards) == 2


def test_set_items_empty_list_clears_grid(grid):
    grid.set_items([PreviewItem(item_id=0, label="a")])
    grid.set_items([])
    assert grid.get_order() == []
    assert len(grid._cards) == 0


def test_card_labels_reflect_item_labels(grid):
    grid.set_items([PreviewItem(item_id=0, label="Page 1")])
    card = grid._cards[0]
    assert card.caption_label.cget("text") == "Page 1"


# ---------------------------------------------------------------------------
# PreviewGrid: selection
# ---------------------------------------------------------------------------

def test_select_sets_selected_item(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid.select(1)
    assert grid.get_selected() == 1


def test_select_replaces_previous_selection_when_not_multiselect(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid.select(0)
    grid.select(2)
    assert grid.get_selected_ids() == {2}


def test_select_unknown_id_is_a_noop(grid):
    grid.set_items([PreviewItem(item_id=0, label="a")])
    grid.select(999)
    assert grid.get_selected() is None


def test_select_with_notify_calls_on_select():
    calls = []
    import tkinter as tk

    root = tk._default_root
    g = PreviewGrid(root, thumb_size=80, on_select=calls.append)
    g.set_items([PreviewItem(item_id=0, label="a")])
    g.select(0, notify=True)
    assert calls == [0]
    g.destroy()


def test_select_without_notify_does_not_call_on_select(grid):
    calls = []
    grid._on_select = calls.append
    grid.set_items([PreviewItem(item_id=0, label="a")])
    grid.select(0, notify=False)
    assert calls == []


def test_multiselect_toggle(grid):
    grid.multiselect = True
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid.toggle_selected(0)
    grid.toggle_selected(1)
    assert grid.get_selected_ids() == {0, 1}
    grid.toggle_selected(0)
    assert grid.get_selected_ids() == {1}


def test_set_selected_ids_replaces_the_whole_set(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(5)])
    grid.set_selected_ids({1, 3})
    assert grid.get_selected_ids() == {1, 3}
    grid.set_selected_ids({4})
    assert grid.get_selected_ids() == {4}


def test_set_selected_ids_ignores_ids_not_present(grid):
    grid.set_items([PreviewItem(item_id=0, label="a")])
    grid.set_selected_ids({0, 999})
    assert grid.get_selected_ids() == {0}


def test_selection_pruned_when_item_removed_via_set_items(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid.select(1)
    grid.set_items([PreviewItem(item_id=0, label="a"), PreviewItem(item_id=2, label="c")])
    assert grid.get_selected() is None  # 1 no longer exists


def test_card_highlight_reflects_selection(grid):
    grid.set_items([PreviewItem(item_id=0, label="a"), PreviewItem(item_id=1, label="b")])
    grid.select(0)
    assert str(grid._cards[0].cget("highlightbackground")).lower() != str(
        grid._cards[1].cget("highlightbackground")
    ).lower()


# ---------------------------------------------------------------------------
# PreviewGrid: reorder
# ---------------------------------------------------------------------------

def test_reorder_move_last_to_first(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(5)])
    grid.reorder_item(4, 0)
    assert grid.get_order() == [4, 0, 1, 2, 3]


def test_reorder_move_first_to_last(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(5)])
    grid.reorder_item(0, 5)
    assert grid.get_order() == [1, 2, 3, 4, 0]


def test_reorder_middle_insertion(grid):
    """Drag page 5 (index 4) before page 2 (index 1) -- the example
    from the Phase 24.1 spec itself: [1,2,3,4,5] -> [1,5,2,3,4]."""
    grid.set_items([PreviewItem(item_id=n, label=str(n)) for n in (1, 2, 3, 4, 5)])
    grid.reorder_item(5, 1)
    assert grid.get_order() == [1, 5, 2, 3, 4]


def test_reorder_onto_its_own_current_position_is_a_no_op(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(4)])
    grid.reorder_item(2, 2)
    assert grid.get_order() == [0, 1, 2, 3]


def test_reorder_calls_on_reorder_with_new_order():
    calls = []
    import tkinter as tk

    root = tk._default_root
    g = PreviewGrid(root, thumb_size=80, on_reorder=calls.append)
    g.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    g.reorder_item(0, 2)
    assert calls == [[1, 2, 0]]
    g.destroy()


def test_reorder_unknown_id_is_a_noop(grid):
    grid.set_items([PreviewItem(item_id=0, label="a"), PreviewItem(item_id=1, label="b")])
    grid.reorder_item(999, 0)
    assert grid.get_order() == [0, 1]


def test_multiple_reorders_compose_correctly(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(5)])
    grid.reorder_item(4, 0)  # [4,0,1,2,3]
    grid.reorder_item(2, 4)  # move id=2 to end -> [4,0,1,3,2]
    assert grid.get_order() == [4, 0, 1, 3, 2]


def test_reorder_target_index_out_of_bounds_is_clamped(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid.reorder_item(0, 999)
    assert grid.get_order() == [1, 2, 0]
    grid.reorder_item(2, -50)
    assert grid.get_order() == [2, 1, 0]


def test_drag_cancel_does_not_change_order(grid):
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid._press_item = 0
    grid._dragging = True
    grid.cancel_drag()
    assert grid.get_order() == [0, 1, 2]
    assert grid._press_item is None
    assert grid._dragging is False


def test_non_reorderable_grid_ignores_motion_based_dragging(grid):
    """reorderable=False grids (e.g. PDF -> Images' preview) must never
    let a drag actually reorder -- verified at the same level real mouse
    motion would trigger it, by directly exercising the motion handler
    with reorderable disabled."""
    grid.reorderable = False
    grid.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(3)])
    grid._press_item = 0
    grid._press_xy = (0, 0)

    class FakeEvent:
        x_root = 500
        y_root = 500

    grid._on_motion(0, FakeEvent())
    assert grid._dragging is False
    assert grid.get_order() == [0, 1, 2]


# ---------------------------------------------------------------------------
# PreviewGrid: async thumbnail loading
# ---------------------------------------------------------------------------

def test_thumbnails_load_asynchronously_and_populate_cards(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=3)
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(lambda item: render_pdf_page_thumbnail(src, item.thumb_key, 80))
        g.set_items([PreviewItem(item_id=i, label=str(i), thumb_key=i) for i in range(3)])

        assert _pump_until(window.root, lambda: g._pending_jobs == 0)
        for item_id in (0, 1, 2):
            assert item_id in g._photo_images
    finally:
        g.destroy()
        window.root.update()


def test_thumbnail_loader_never_called_on_the_main_thread(window, tmp_path):
    import threading

    src = _make_pdf(tmp_path / "d.pdf", pages=2)
    seen = []

    def loader(item):
        seen.append(threading.current_thread() is threading.main_thread())
        return render_pdf_page_thumbnail(src, item.thumb_key, 80)

    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(loader)
        g.set_items([PreviewItem(item_id=i, label=str(i), thumb_key=i) for i in range(2)])
        assert _pump_until(window.root, lambda: g._pending_jobs == 0)
    finally:
        g.destroy()
        window.root.update()

    assert seen == [False, False]


def test_failed_thumbnail_gets_a_safe_placeholder_not_a_crash(window):
    def bad_loader(item):
        raise ValueError("simulated render failure")

    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(bad_loader)
        g.set_items([PreviewItem(item_id=0, label="a")])
        assert _pump_until(window.root, lambda: g._pending_jobs == 0)
        # The card still exists (item identity preserved) and got SOME
        # image assigned (the placeholder), not left in a broken state.
        assert 0 in g._cards
        assert 0 in g._photo_images
    finally:
        g.destroy()
        window.root.update()


def test_stale_thumbnail_result_after_set_items_is_ignored(window, tmp_path):
    """If set_items() is called again (a new document/list) before an
    in-flight render for the OLD items finishes, that old result must
    never be applied -- it could otherwise show the wrong image, or
    crash trying to update a card that no longer exists."""
    src = _make_pdf(tmp_path / "d.pdf", pages=1)
    release = __import__("threading").Event()

    def slow_loader(item):
        release.wait(timeout=5)
        return render_pdf_page_thumbnail(src, item.thumb_key, 80)

    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(slow_loader)
        g.set_items([PreviewItem(item_id="old", label="old", thumb_key=0)])
        window.root.update()  # let the worker pick up the job and block

        # Replace items entirely while the old render is still blocked.
        g.set_items([PreviewItem(item_id="new", label="new", thumb_key=0)])
        release.set()  # let the stale render finish now

        assert _pump_until(window.root, lambda: "new" in g._photo_images)
        assert "old" not in g._cards  # old card was torn down
        # No crash, no stray entry for the id that no longer exists.
        time.sleep(0.1)
        window.root.update()
        assert "old" not in g._photo_images
    finally:
        g.destroy()
        window.root.update()


def test_destroy_stops_the_poll_loop(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=1)
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    g.set_thumbnail_loader(lambda item: render_pdf_page_thumbnail(src, 0, 80))
    g.set_items([PreviewItem(item_id=0, label="a", thumb_key=0)])
    g.destroy()
    window.root.update()
    # After destroy(), the generation was bumped and _destroyed is set,
    # so even if a result arrives late it is dropped without error.
    assert g._destroyed is True
    time.sleep(0.1)
    window.root.update()  # must not raise


def test_no_poll_loop_runs_when_idle(window):
    """Once every requested thumbnail has arrived, the widget must stop
    rescheduling after() -- not leave a perpetual idle timer running."""
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(lambda item: b"")
        g.set_items([PreviewItem(item_id=0, label="a")])
        assert _pump_until(window.root, lambda: g._pending_jobs == 0)
        time.sleep(0.1)
        assert g._poll_scheduled is False
    finally:
        g.destroy()
        window.root.update()


def test_delete_key_calls_on_delete_for_selected_item():
    calls = []
    import tkinter as tk

    root = tk._default_root
    g = PreviewGrid(root, thumb_size=80, on_delete=calls.append)
    g.set_items([PreviewItem(item_id=0, label="a"), PreviewItem(item_id=1, label="b")])
    g.select(1, notify=False)
    g._on_delete_key()
    assert calls == [1]
    g.destroy()


def test_delete_key_with_nothing_selected_is_a_noop():
    calls = []
    import tkinter as tk

    root = tk._default_root
    g = PreviewGrid(root, thumb_size=80, on_delete=calls.append)
    g.set_items([PreviewItem(item_id=0, label="a")])
    g._on_delete_key()
    assert calls == []
    g.destroy()


def test_no_thumbnail_loader_set_uses_placeholder_immediately(grid):
    """A grid with no loader configured yet must still render usable
    (placeholder) cards rather than erroring."""
    grid.set_items([PreviewItem(item_id=0, label="a")])
    # No exception, and a card exists with a placeholder image already
    # assigned at construction time.
    assert 0 in grid._cards
    assert grid._cards[0].image_label.cget("image") != ""


# ---------------------------------------------------------------------------
# Photo-image lifetime (regression: GC finalizing Tk images off the main
# thread raised "main thread is not in main loop" and intermittently
# disturbed unrelated tests)
# ---------------------------------------------------------------------------

def test_set_items_disposes_previous_photos_deterministically(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=2)
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(lambda item: render_pdf_page_thumbnail(src, item.thumb_key, 80))
        g.set_items([PreviewItem(item_id=i, label=str(i), thumb_key=i) for i in range(2)])
        assert _pump_until(window.root, lambda: g._pending_jobs == 0)
        old_photos = list(g._photo_images.values())
        assert old_photos

        g.set_items([])
        # Every previous (non-placeholder) photo was explicitly
        # disposed on the main thread: its name was cleared, so a later
        # GC-time __del__ (on any thread) is a harmless no-op.
        assert all(p.name is None for p in old_photos)
    finally:
        g.destroy()
        window.root.update()


def test_placeholder_survives_set_items_and_stays_usable(window):
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    try:
        g.set_thumbnail_loader(lambda item: (_ for _ in ()).throw(ValueError("x")))
        g.set_items([PreviewItem(item_id=0, label="a")])
        assert _pump_until(window.root, lambda: g._pending_jobs == 0)
        placeholder = g._placeholder_photo
        assert placeholder is not None

        g.set_items([PreviewItem(item_id=1, label="b")])
        assert g._placeholder_photo is placeholder
        assert placeholder.name is not None  # NOT disposed while still in use
        assert g._cards[1].image_label.cget("image") != ""
    finally:
        g.destroy()
        window.root.update()


def test_destroy_disposes_all_photos(window, tmp_path):
    src = _make_pdf(tmp_path / "d.pdf", pages=2)
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    g.set_thumbnail_loader(lambda item: render_pdf_page_thumbnail(src, item.thumb_key, 80))
    g.set_items([PreviewItem(item_id=i, label=str(i), thumb_key=i) for i in range(2)])
    assert _pump_until(window.root, lambda: g._pending_jobs == 0)
    photos = list(g._photo_images.values()) + [g._placeholder_photo]
    g.destroy()
    window.root.update()
    assert all(p is None or p.name is None for p in photos)


def test_photo_finalization_off_the_main_thread_is_harmless(window, tmp_path):
    """Simulates the original failure: an image finalized (its __del__
    run) from a non-main thread after disposal must not raise."""
    import threading

    src = _make_pdf(tmp_path / "d.pdf", pages=1)
    g = PreviewGrid(window.root, thumb_size=80)
    g.pack()
    window.root.update()
    g.set_thumbnail_loader(lambda item: render_pdf_page_thumbnail(src, 0, 80))
    g.set_items([PreviewItem(item_id=0, label="a", thumb_key=0)])
    assert _pump_until(window.root, lambda: g._pending_jobs == 0)
    photo = g._photo_images[0]
    g.set_items([])  # disposes on the main thread

    errors = []

    def finalize_elsewhere():
        try:
            photo.__del__()
        except Exception as exc:  # would be RuntimeError before the fix
            errors.append(exc)

    t = threading.Thread(target=finalize_elsewhere)
    t.start()
    t.join(timeout=5)
    assert errors == []
    g.destroy()
    window.root.update()


# ---------------------------------------------------------------------------
# Real mouse path: press -> motion -> release (not just reorder_item())
# ---------------------------------------------------------------------------

class _E:
    """A minimal fake mouse event carrying only what PreviewGrid reads."""

    def __init__(self, x_root, y_root):
        self.x_root = x_root
        self.y_root = y_root


def _real_drag(grid, item_id, gap_index, *, move=True, release=True):
    """Drive the genuine press/motion/release handlers. `gap_index` is
    the gap (0..N, in the ORIGINAL order) whose left edge the pointer is
    released over -- i.e. "drop before original card #gap_index"."""
    cols = grid._columns()
    row, col = divmod(gap_index, cols)
    tx = grid.winfo_rootx() + col * grid.card_width + 2
    ty = grid.winfo_rooty() + row * grid.card_height + 10
    sx, sy = grid.winfo_rootx() + 5, grid.winfo_rooty() + 5
    grid._on_press(item_id, _E(sx, sy))
    if move:
        grid._on_motion(item_id, _E(sx + 40, sy + 40))
    if release:
        grid._on_release(item_id, _E(tx, ty))


@pytest.fixture
def wide_grid(window):
    window.root.geometry("900x500")
    g = PreviewGrid(window.root, thumb_size=100)
    g.pack(fill="x")
    window.root.update()
    g.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(5)])
    window.root.update()
    yield g
    g.destroy()
    window.root.update()


def test_real_drag_backward_lands_before_target(wide_grid):
    _real_drag(wide_grid, 4, 1)  # last card into the gap before card #1
    assert wide_grid.get_order() == [0, 4, 1, 2, 3]


def test_real_drag_forward_lands_in_the_dropped_gap_not_one_too_far(wide_grid):
    _real_drag(wide_grid, 0, 3)  # first card into the gap before card #3
    assert wide_grid.get_order() == [1, 2, 0, 3, 4]


def test_real_drag_to_the_very_end(wide_grid):
    _real_drag(wide_grid, 0, 5)  # the gap after the last card
    assert wide_grid.get_order() == [1, 2, 3, 4, 0]


def test_real_drag_to_the_very_start(wide_grid):
    _real_drag(wide_grid, 3, 0)
    assert wide_grid.get_order() == [3, 0, 1, 2, 4]


@pytest.mark.parametrize("item", [0, 2, 4])
def test_real_drag_onto_own_slot_leaves_order_unchanged(wide_grid, item):
    _real_drag(wide_grid, item, item)      # gap immediately before itself
    assert wide_grid.get_order() == [0, 1, 2, 3, 4]
    _real_drag(wide_grid, item, item + 1)  # gap immediately after itself
    assert wide_grid.get_order() == [0, 1, 2, 3, 4]


def test_click_without_reaching_the_drag_threshold_only_selects(wide_grid):
    calls = []
    wide_grid._on_reorder = calls.append
    _real_drag(wide_grid, 2, 0, move=False)  # press + release, no motion
    assert wide_grid.get_order() == [0, 1, 2, 3, 4]
    assert wide_grid.get_selected() == 2
    assert calls == []  # a plain click never reorders


def test_real_drag_shows_then_hides_the_insertion_indicator(wide_grid):
    sx, sy = wide_grid.winfo_rootx() + 5, wide_grid.winfo_rooty() + 5
    wide_grid._on_press(3, _E(sx, sy))
    wide_grid._on_motion(3, _E(sx + 200, sy + 10))
    assert wide_grid._drag_target_index is not None
    assert wide_grid._insertion_indicator.winfo_ismapped() or \
        wide_grid._insertion_indicator.place_info()
    wide_grid.cancel_drag()
    assert wide_grid._drag_target_index is None
    assert not wide_grid._insertion_indicator.place_info()
    assert wide_grid.get_order() == [0, 1, 2, 3, 4]


def test_real_drag_cancel_mid_drag_then_release_does_not_reorder(wide_grid):
    sx, sy = wide_grid.winfo_rootx() + 5, wide_grid.winfo_rooty() + 5
    wide_grid._on_press(0, _E(sx, sy))
    wide_grid._on_motion(0, _E(sx + 300, sy + 10))
    wide_grid.cancel_drag()
    wide_grid._on_release(0, _E(sx + 300, sy + 10))  # stray release afterwards
    assert wide_grid.get_order() == [0, 1, 2, 3, 4]


def test_real_drag_disabled_when_not_reorderable(wide_grid):
    wide_grid.reorderable = False
    _real_drag(wide_grid, 4, 0)
    assert wide_grid.get_order() == [0, 1, 2, 3, 4]


def test_real_drag_fires_on_reorder_exactly_once_with_full_order(wide_grid):
    calls = []
    wide_grid._on_reorder = calls.append
    _real_drag(wide_grid, 4, 1)
    assert calls == [[0, 4, 1, 2, 3]]


@pytest.fixture
def narrow_grid(window):
    """A grid constrained to a fixed 270px-wide holder, so it wraps into
    multiple rows (2 columns) regardless of the app window's own
    minimum size."""
    import tkinter as tk

    holder = tk.Frame(window.root, width=270, height=700)
    holder.pack_propagate(False)
    holder.pack(side="left", anchor="nw")
    g = PreviewGrid(holder, thumb_size=100)
    g.pack(fill="x")
    window.root.update()
    g.set_items([PreviewItem(item_id=i, label=str(i)) for i in range(6)])
    window.root.update()
    yield g
    g.destroy()
    holder.destroy()
    window.root.update()


def test_narrow_grid_actually_wraps_into_multiple_rows(narrow_grid):
    assert narrow_grid._columns() == 2
    assert int(narrow_grid.cget("height")) >= 3 * narrow_grid.card_height


def test_real_drag_across_rows_backward(narrow_grid):
    _real_drag(narrow_grid, 5, 1)  # bottom-right card to the gap before card #1 (row 0)
    assert narrow_grid.get_order() == [0, 5, 1, 2, 3, 4]


def test_real_drag_across_rows_forward(narrow_grid):
    _real_drag(narrow_grid, 0, 4)  # top-left card to the gap before card #4 (row 2)
    assert narrow_grid.get_order() == [1, 2, 3, 0, 4, 5]


def test_real_drag_to_end_of_a_wrapped_grid(narrow_grid):
    _real_drag(narrow_grid, 1, 6)
    assert narrow_grid.get_order() == [0, 2, 3, 4, 5, 1]
