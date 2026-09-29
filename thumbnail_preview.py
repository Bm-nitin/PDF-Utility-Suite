"""
thumbnail_preview.py

Phase 24.1: a reusable visual thumbnail-preview and drag-and-drop
reordering component, shared by Organize Pages, Images -> PDF, and
PDF -> Images, rather than three independent implementations.

Kept as a single flat module (like every other module in this project
-- pdf_engine.py, organize_engine.py, etc. -- there is no existing
sub-package/UI-component directory structure to fit into, so this
follows that same flat convention rather than inventing a new
"preview/" package for it).

Three pieces:

- ThumbnailCache: a small, bounded, LRU in-memory cache so the same
  page/image is never re-rendered unnecessarily, and never grows
  without bound for a very large document.
- render_pdf_page_thumbnail() / render_image_file_thumbnail(): pure,
  read-only rendering functions (no tkinter) -- reusing PyMuPDF's own
  rendering (see pdf_to_images_engine.py's own docstring for the
  rotation/get_pixmap findings this reuses directly: get_pixmap()
  already accounts for the page's rotation, so a thumbnail always shows
  the page the way it's normally displayed) for PDF pages, and Pillow
  (already a project dependency since Phase 22) for image files, with
  the exact same transparency/EXIF/mode-normalization handling
  images_to_pdf_engine.py's own _prepare_image() already established,
  reused here rather than reinvented.
- PreviewGrid: the actual tkinter widget -- a wrapping grid of
  thumbnail "cards" supporting click-to-select, mouse drag-and-drop
  reordering with a live insertion indicator, and asynchronous,
  cancellable-by-generation thumbnail loading via a single background
  worker thread + queue.Queue + root.after() polling loop, so
  thumbnail decoding never blocks the Tk main thread and a stale result
  for a document/list that has since changed is always discarded
  rather than corrupting the current view.

Nothing in this module ever writes to a source PDF or image file --
every render function only ever opens its input for reading.
"""

from __future__ import annotations

import io
import logging
import queue
import threading
import tkinter as tk
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pymupdf

logger = logging.getLogger(__name__)

#: Default thumbnail width/height bound, in pixels -- within the
#: Phase 24.1 spec's recommended 120-180px range. Callers may pass a
#: different value; this is only the module's own default.
DEFAULT_THUMBNAIL_SIZE = 140

#: A generous cap on how many rendered thumbnails are kept in memory at
#: once. At a rendered size of ~140px, a PNG-encoded thumbnail is
#: typically a few KB to a few tens of KB, so even the cap's worst case
#: (a few MB total) is negligible -- this exists purely so an extremely
#: large document (thousands of pages) can't grow the cache without
#: bound, per the Phase 24.1 requirement.
DEFAULT_CACHE_CAPACITY = 400


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class ThumbnailCache:
    """A small, bounded, least-recently-used in-memory cache mapping an
    arbitrary hashable key to rendered thumbnail bytes (PNG). Not
    specific to PDFs or images -- both render functions below share one
    cache instance's worth of eviction bookkeeping via the keys their
    callers choose, but this class itself knows nothing about PDFs,
    images, or tkinter.

    Thread-safe: rendering happens on a background thread (see
    PreviewGrid below), so get()/put() may be called from a thread other
    than the one that constructed this cache.
    """

    def __init__(self, capacity: int = DEFAULT_CACHE_CAPACITY):
        if capacity <= 0:
            raise ValueError("capacity must be positive.")
        self._capacity = capacity
        self._data: "OrderedDict[Any, bytes]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[bytes]:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: Any, value: bytes) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# ---------------------------------------------------------------------------
# Rendering (pure, read-only, no tkinter)
# ---------------------------------------------------------------------------

def render_pdf_page_thumbnail(
    pdf_path: Path, page_index: int, max_size: int = DEFAULT_THUMBNAIL_SIZE,
) -> bytes:
    """Render page `page_index` (0-based) of the PDF at `pdf_path` to a
    small PNG thumbnail, at most `max_size` pixels on its longer side,
    preserving aspect ratio. Opens the source read-only and never
    modifies it.

    Reuses Page.get_pixmap()'s own rotation handling directly (see this
    module's docstring, and pdf_to_images_engine.py's own docstring for
    the underlying experimental verification): the page's /Rotate is
    already respected, so a rotated page's thumbnail shows it the way
    it's normally displayed, with no separate handling needed here.
    """
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        rect = page.rect
        longest = max(rect.width, rect.height) or 1.0
        scale = max_size / longest
        matrix = pymupdf.Matrix(scale, scale)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png")


def render_image_file_thumbnail(
    image_path: Path, max_size: int = DEFAULT_THUMBNAIL_SIZE,
) -> bytes:
    """Render a small PNG thumbnail of the image file at `image_path`,
    at most `max_size` pixels on its longer side, preserving aspect
    ratio. Opens the source read-only and never modifies it.

    Uses the exact same EXIF-orientation and transparency-onto-white
    handling as images_to_pdf_engine.py's own _prepare_image() (see
    that module's docstring for the reasoning and experimental
    verification behind each choice) -- a thumbnail should look like
    what will actually end up in the generated PDF, not a differently-
    processed preview of it. Pillow's own efficient .thumbnail() (which
    downsamples progressively rather than decoding at full resolution
    where the format allows it, e.g. JPEG) is used rather than loading
    the original at full size into memory just to shrink it afterward.
    """
    from PIL import Image, ImageOps  # local import: only this function needs Pillow

    with Image.open(image_path) as opened:
        opened.load()
        img = ImageOps.exif_transpose(opened) or opened

        has_transparency = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        )
        if has_transparency:
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# PreviewGrid widget
# ---------------------------------------------------------------------------

def _dispose_photo(photo: Optional["tk.PhotoImage"]) -> None:
    """Deterministically frees a tk.PhotoImage on the CALLING thread
    (which must be the Tk main thread), and neutralizes its own
    __del__.

    tkinter's Image.__del__ calls into Tcl to delete the image. If a
    PhotoImage is simply dropped, CPython may finalize it whenever the
    cyclic garbage collector next runs -- and that can happen on ANY
    thread that happens to allocate enough objects, including this
    widget's own background worker or an unrelated tool's import
    worker. A Tcl call from a non-main thread raises "RuntimeError:
    main thread is not in main loop" from inside __del__ (reported only
    as an unraisable-exception warning, but it also randomly disturbs
    whatever test/operation happened to be running -- observed as
    intermittent failures in unrelated test modules before this was
    added). Deleting the Tcl image explicitly here, on the main thread,
    and then clearing `name` (Image.__del__ is a no-op when `name` is
    falsy) makes the eventual finalization harmless regardless of which
    thread it runs on.
    """
    if photo is None:
        return
    try:
        photo.tk.call("image", "delete", photo.name)
    except Exception:
        pass
    try:
        photo.name = None
    except Exception:
        pass


@dataclass
class PreviewItem:
    """One thumbnail card's worth of data. `item_id` is the stable
    identity token PreviewGrid uses everywhere (selection, reordering,
    thumbnail-result matching) -- it must be hashable and unique within
    one PreviewGrid's current items, but is otherwise opaque to this
    module: a caller might use a 0-based page index (Organize Pages), a
    models.ImageFile instance itself (Images -> PDF, where identity --
    not value equality -- is already how that list's own Move Up/Down/
    Remove find a row, per that tool's own established convention), or
    a 1-based page number (PDF -> Images). `thumb_key` is whatever the
    caller's thumbnail-loader callback needs to actually render the
    thumbnail (often the same value as `item_id`, but kept separate
    since they don't always have to be).
    """

    item_id: Any
    label: str
    thumb_key: Any = None
    selected: bool = False


# Visual constants -- kept local to this module rather than imported
# from ui.py, so this component has no dependency on the app's own UI
# module (ui.py depends on this module, never the other way around).
_COLOR_BG = "#f5f6f8"
_COLOR_CARD = "#ffffff"
_COLOR_BORDER = "#e0e2e7"
_COLOR_TEXT_PRIMARY = "#1a1a1a"
_COLOR_TEXT_SECONDARY = "#6b7280"
_COLOR_ACCENT = "#2563eb"
_COLOR_DRAG_BG = "#dbeafe"
_COLOR_PLACEHOLDER = "#e5e7eb"


class PreviewGrid(tk.Frame):
    """A wrapping grid of thumbnail cards with click-to-select and
    mouse drag-and-drop reordering.

    Usage:
        grid = PreviewGrid(parent, thumb_size=140, on_reorder=..., on_select=...)
        grid.set_thumbnail_loader(lambda item: render_pdf_page_thumbnail(path, item.thumb_key))
        grid.pack(fill="both", expand=True)
        grid.set_items([PreviewItem(item_id=0, label="Page 1", thumb_key=0), ...])

    Threading: `set_thumbnail_loader`'s callable is invoked on a single
    background worker thread owned by this widget -- it must not touch
    any tkinter widget, and should simply return PNG bytes (or raise,
    for a thumbnail that safely falls back to a placeholder). Every
    actual widget update happens back on the main thread, via a
    root.after() poll loop that is only ever scheduled while work is
    outstanding (see _schedule_poll_if_needed()), so no idle timer keeps
    running once every requested thumbnail has arrived.

    Reorder-on-drop is exposed as reorder_item()/_reorder_item(), a pure
    list-manipulation method independent of any real mouse event, so it
    is directly, deterministically callable from tests -- exactly the
    same "call the handler method directly rather than simulate exact
    pixel-perfect mouse motion" convention this project's own tests
    already use for Move Up/Down (see test_ui_organize.py).
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        thumb_size: int = DEFAULT_THUMBNAIL_SIZE,
        reorderable: bool = True,
        multiselect: bool = False,
        on_reorder: Optional[Callable[[List[Any]], None]] = None,
        on_select: Optional[Callable[[Any], None]] = None,
        on_delete: Optional[Callable[[Any], None]] = None,
        cache: Optional[ThumbnailCache] = None,
        bg: str = _COLOR_BG,
    ):
        super().__init__(parent, bg=bg)
        self.thumb_size = thumb_size
        self.card_width = thumb_size + 24
        self.card_height = thumb_size + 46
        self.reorderable = reorderable
        self.multiselect = multiselect
        self._on_reorder = on_reorder
        self._on_select = on_select
        self._on_delete = on_delete
        self.cache = cache if cache is not None else ThumbnailCache()

        self._items: List[PreviewItem] = []
        self._cards: Dict[Any, tk.Frame] = {}
        self._photo_images: Dict[Any, "tk.PhotoImage"] = {}
        self._selected_ids: set = set()

        self._loader: Optional[Callable[[PreviewItem], bytes]] = None
        self._generation = 0
        self._job_queue: "queue.Queue" = queue.Queue()
        self._result_queue: "queue.Queue" = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._pending_jobs = 0
        self._poll_scheduled = False
        self._destroyed = False

        self._press_item: Optional[Any] = None
        self._press_xy = (0, 0)
        self._dragging = False
        self._drag_target_index: Optional[int] = None

        self._insertion_indicator = tk.Frame(self, bg=_COLOR_ACCENT, width=3)

        self._placeholder_photo: Optional["tk.PhotoImage"] = None

        self.bind("<Configure>", self._on_container_configure)
        self.bind("<Destroy>", self._on_widget_destroyed)
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<BackSpace>", self._on_delete_key)

    # -- public API ---------------------------------------------------

    def set_thumbnail_loader(self, loader: Callable[[PreviewItem], bytes]) -> None:
        self._loader = loader

    def set_items(self, items: List[PreviewItem]) -> None:
        """Replace the full set of displayed items, in order. Any
        thumbnail-loading result for a PREVIOUS call to set_items() that
        arrives after this call is discarded (see the generation check
        in _poll_results()) -- this is what protects the grid from a
        stale worker result if the underlying document/list changes
        (or the tool is switched away and back) while a render was still
        in flight.
        """
        self._generation += 1
        gen = self._generation

        for card in self._cards.values():
            card.destroy()
        self._cards.clear()
        for photo in self._photo_images.values():
            if photo is not self._placeholder_photo:  # shared; reused by new cards
                _dispose_photo(photo)
        self._photo_images.clear()

        self._items = list(items)
        valid_ids = {item.item_id for item in self._items}
        self._selected_ids &= valid_ids

        self._build_cards()
        self._layout_cards()

        if self._loader is not None:
            for item in self._items:
                self._pending_jobs += 1
                self._job_queue.put((gen, item))
            self._ensure_worker_running()
            self._schedule_poll_if_needed()

    def get_order(self) -> List[Any]:
        return [item.item_id for item in self._items]

    def select(self, item_id: Any, *, notify: bool = False) -> None:
        """Marks `item_id` selected (replacing any previous selection,
        unless multiselect=True). `notify=False` (the default) is for a
        caller synchronizing the grid FROM some other already-changed
        state (e.g. Organize Pages' own listbox selection) without
        triggering on_select again; a real click always uses
        notify=True internally.
        """
        if item_id not in {item.item_id for item in self._items}:
            return
        if not self.multiselect:
            self._selected_ids = {item_id}
        else:
            self._selected_ids.add(item_id)
        self._refresh_card_styles()
        if notify and self._on_select:
            self._on_select(item_id)

    def toggle_selected(self, item_id: Any) -> None:
        """multiselect-mode only: flips whether `item_id` is selected."""
        if item_id in self._selected_ids:
            self._selected_ids.discard(item_id)
        else:
            self._selected_ids.add(item_id)
        self._refresh_card_styles()

    def set_selected_ids(self, item_ids) -> None:
        """Replace the full selected-set directly (used by PDF -> Images
        to mirror the range-text-derived selection onto the grid,
        one-directionally, without going through select()/toggle_selected()).
        """
        valid_ids = {item.item_id for item in self._items}
        self._selected_ids = set(item_ids) & valid_ids
        self._refresh_card_styles()

    def get_selected(self) -> Optional[Any]:
        return next(iter(self._selected_ids), None)

    def get_selected_ids(self) -> set:
        return set(self._selected_ids)

    def reorder_item(self, item_id: Any, target_index: int) -> None:
        """Moves `item_id` so it ends up at `target_index` in the
        resulting order (0-based, clamped to a valid position), then
        rebuilds the layout and calls on_reorder() with the new full
        order of item_ids. Dragging an item onto its own current
        position is always a well-defined no-op-in-effect call (the
        order is unchanged and on_reorder() still fires with the
        unchanged order) rather than a special case to avoid.

        This is the single place actual reordering happens -- both real
        mouse drag-drop and any test call go through this method (or,
        for a real drag, the private _finish_drag() wrapper below,
        which is a thin wrapper over exactly this).
        """
        ids = [item.item_id for item in self._items]
        if item_id not in ids:
            return
        old_index = ids.index(item_id)
        ids.pop(old_index)
        target_index = max(0, min(target_index, len(ids)))
        ids.insert(target_index, item_id)

        by_id = {item.item_id: item for item in self._items}
        self._items = [by_id[i] for i in ids]
        self._layout_cards()

        if self._on_reorder:
            self._on_reorder(ids)

    def destroy(self) -> None:
        """Stops the background worker and any scheduled after()
        callback before tearing down the widget -- see the Phase 24.1
        "do not allow background timers/after callbacks to survive
        widget destruction" requirement.
        """
        self._destroyed = True
        self._generation += 1
        try:
            self._job_queue.put(None)  # wakes the worker so it can exit
        except Exception:
            pass
        self._dispose_all_photos()
        super().destroy()

    def _dispose_all_photos(self) -> None:
        for photo in self._photo_images.values():
            _dispose_photo(photo)
        self._photo_images.clear()
        _dispose_photo(self._placeholder_photo)
        self._placeholder_photo = None

    # -- internal: destroy hook from the <Destroy> binding -------------

    def _on_widget_destroyed(self, _event=None) -> None:
        self._destroyed = True
        self._generation += 1

    # -- internal: worker thread ----------------------------------------

    def _ensure_worker_running(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Runs on a background thread. Never touches any tkinter
        widget -- only calls the caller-supplied loader and puts a
        plain tuple on the thread-safe result queue.
        """
        while True:
            job = self._job_queue.get()
            if job is None:
                return
            generation, item = job
            try:
                data = self._loader(item) if self._loader is not None else None
            except Exception:
                logger.warning(
                    "Thumbnail rendering failed for %r", item.item_id, exc_info=True,
                )
                data = None
            self._result_queue.put((generation, item.item_id, data))

    def _schedule_poll_if_needed(self) -> None:
        if self._destroyed or self._poll_scheduled:
            return
        self._poll_scheduled = True
        self.after(30, self._poll_results)

    def _poll_results(self) -> None:
        self._poll_scheduled = False
        if self._destroyed:
            return
        try:
            while True:
                generation, item_id, data = self._result_queue.get_nowait()
                self._pending_jobs = max(0, self._pending_jobs - 1)
                if generation == self._generation:
                    self._apply_thumbnail(item_id, data)
        except queue.Empty:
            pass
        if not self._destroyed and self._pending_jobs > 0:
            self._schedule_poll_if_needed()

    def _apply_thumbnail(self, item_id: Any, data: Optional[bytes]) -> None:
        card = self._cards.get(item_id)
        if card is None:
            return  # item no longer displayed (removed/reordered away mid-load)
        photo = self._decode_photo(data) if data is not None else self._get_placeholder()
        previous = self._photo_images.get(item_id)
        self._photo_images[item_id] = photo  # keep a reference -- Tk drops GC'd images
        card.image_label.configure(image=photo)
        if previous is not None and previous is not photo \
                and previous is not self._placeholder_photo:
            _dispose_photo(previous)

    @staticmethod
    def _decode_photo(data: bytes) -> "tk.PhotoImage":
        try:
            return tk.PhotoImage(data=data)
        except Exception:
            logger.warning("Could not decode a rendered thumbnail", exc_info=True)
            return PreviewGrid._blank_photo()

    def _get_placeholder(self) -> "tk.PhotoImage":
        if self._placeholder_photo is None:
            self._placeholder_photo = self._blank_photo(self.thumb_size)
        return self._placeholder_photo

    @staticmethod
    def _blank_photo(size: int = DEFAULT_THUMBNAIL_SIZE) -> "tk.PhotoImage":
        photo = tk.PhotoImage(width=size, height=size)
        photo.put(_COLOR_PLACEHOLDER, to=(0, 0, size, size))
        return photo

    # -- internal: layout -------------------------------------------------

    def _on_container_configure(self, _event=None) -> None:
        self._layout_cards()

    def _columns(self) -> int:
        width = self.winfo_width()
        if width <= 1:
            width = self.card_width
        return max(1, width // self.card_width)

    def _build_cards(self) -> None:
        for item in self._items:
            self._cards[item.item_id] = self._build_card(item)

    def _build_card(self, item: PreviewItem) -> tk.Frame:
        card = tk.Frame(
            self, bg=_COLOR_CARD, highlightthickness=2,
            highlightbackground=_COLOR_BORDER, highlightcolor=_COLOR_BORDER,
        )
        image_label = tk.Label(
            card, bg=_COLOR_CARD, image=self._get_placeholder(),
        )
        image_label.pack(pady=(6, 2))
        caption = tk.Label(
            card, text=item.label, font=("Segoe UI", 8), bg=_COLOR_CARD,
            fg=_COLOR_TEXT_SECONDARY, wraplength=self.thumb_size,
        )
        caption.pack(pady=(0, 4))

        card.image_label = image_label  # type: ignore[attr-defined]
        card.caption_label = caption  # type: ignore[attr-defined]

        item_id = item.item_id
        for widget in (card, image_label, caption):
            widget.bind("<ButtonPress-1>", lambda e, i=item_id: self._on_press(i, e))
            widget.bind("<B1-Motion>", lambda e, i=item_id: self._on_motion(i, e))
            widget.bind("<ButtonRelease-1>", lambda e, i=item_id: self._on_release(i, e))
        return card

    def _layout_cards(self) -> None:
        cols = self._columns()
        for index, item in enumerate(self._items):
            row, col = divmod(index, cols)
            x = col * self.card_width + 6
            y = row * self.card_height + 6
            card = self._cards[item.item_id]
            card.place(
                x=x, y=y,
                width=self.card_width - 12, height=self.card_height - 12,
            )
        rows = -(-len(self._items) // cols) if self._items else 0
        total_height = rows * self.card_height + 12
        self.configure(height=total_height)
        self._refresh_card_styles()

    def _refresh_card_styles(self) -> None:
        for item in self._items:
            card = self._cards.get(item.item_id)
            if card is None:
                continue
            if item.item_id in self._selected_ids:
                card.configure(highlightbackground=_COLOR_ACCENT, highlightcolor=_COLOR_ACCENT)
            else:
                card.configure(highlightbackground=_COLOR_BORDER, highlightcolor=_COLOR_BORDER)

    # -- internal: drag and drop -------------------------------------------

    def _on_press(self, item_id: Any, event: "tk.Event") -> None:
        self._press_item = item_id
        self._press_xy = (event.x_root, event.y_root)
        self._dragging = False
        self.focus_set()  # so a following Delete/Backspace key reaches this widget
        self.select(item_id, notify=True)

    def _on_motion(self, item_id: Any, event: "tk.Event") -> None:
        if self._press_item != item_id or not self.reorderable:
            return
        dx = event.x_root - self._press_xy[0]
        dy = event.y_root - self._press_xy[1]
        if not self._dragging and (abs(dx) >= 6 or abs(dy) >= 6):
            self._dragging = True
            card = self._cards.get(item_id)
            if card is not None:
                card.configure(bg=_COLOR_DRAG_BG)
                card.image_label.configure(bg=_COLOR_DRAG_BG)
                card.caption_label.configure(bg=_COLOR_DRAG_BG)
        if self._dragging:
            local_x = event.x_root - self.winfo_rootx()
            local_y = event.y_root - self.winfo_rooty()
            target_index = self._compute_insertion_index(local_x, local_y)
            self._show_insertion_indicator(target_index)

    def _on_release(self, item_id: Any, event: "tk.Event") -> None:
        was_dragging = self._dragging
        card = self._cards.get(item_id)
        if card is not None:
            card.configure(bg=_COLOR_CARD)
            card.image_label.configure(bg=_COLOR_CARD)
            card.caption_label.configure(bg=_COLOR_CARD)
        self._hide_insertion_indicator()

        if was_dragging:
            local_x = event.x_root - self.winfo_rootx()
            local_y = event.y_root - self.winfo_rooty()
            gap_index = self._compute_insertion_index(local_x, local_y)
            # _compute_insertion_index() returns a GAP position in the
            # ORIGINAL list (0 = before the first card ... N = after the
            # last). reorder_item() takes the item's FINAL position, and
            # removes the item before inserting -- so dropping into a gap
            # that lies after the item's own current slot must shift down
            # by one, or a forward drag lands one card too far.
            ids = [item.item_id for item in self._items]
            old_index = ids.index(item_id) if item_id in ids else 0
            final_index = gap_index - 1 if gap_index > old_index else gap_index
            self.reorder_item(item_id, final_index)

        self._dragging = False
        self._press_item = None

    def _on_delete_key(self, _event=None) -> None:
        if not self._on_delete:
            return
        selected = self.get_selected()
        if selected is not None:
            self._on_delete(selected)

    def cancel_drag(self) -> None:
        """Cancels an in-progress drag without reordering -- e.g. if the
        widget loses focus, the tool is switched away, or a test needs
        to abandon a drag cleanly. Restores the dragged card's normal
        appearance and hides the insertion indicator; the order is left
        exactly as it was.
        """
        if self._press_item is not None:
            card = self._cards.get(self._press_item)
            if card is not None:
                card.configure(bg=_COLOR_CARD)
                card.image_label.configure(bg=_COLOR_CARD)
                card.caption_label.configure(bg=_COLOR_CARD)
        self._hide_insertion_indicator()
        self._dragging = False
        self._press_item = None

    def _compute_insertion_index(self, local_x: int, local_y: int) -> int:
        cols = self._columns()
        col = max(0, min(cols - 1, local_x // self.card_width))
        row = max(0, local_y // self.card_height)
        index = row * cols + col
        # Left/right half of the hovered card decides "before" vs
        # "after" that card, for a more precise drop target.
        offset_in_card = local_x - col * self.card_width
        if offset_in_card > self.card_width / 2:
            index += 1
        return max(0, min(index, len(self._items)))

    def _show_insertion_indicator(self, target_index: int) -> None:
        self._drag_target_index = target_index
        cols = self._columns()
        row, col = divmod(target_index, cols) if cols else (0, 0)
        if col >= cols:
            row, col = row + 1, 0
        x = col * self.card_width + 3
        y = row * self.card_height + 6
        self._insertion_indicator.place(
            x=x, y=y, width=3, height=self.card_height - 12,
        )
        self._insertion_indicator.lift()

    def _hide_insertion_indicator(self) -> None:
        self._drag_target_index = None
        self._insertion_indicator.place_forget()
