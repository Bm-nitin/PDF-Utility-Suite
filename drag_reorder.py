"""Position animation and live drag feedback for Tk file lists."""

from __future__ import annotations

from time import monotonic
import tkinter as tk
from typing import Callable


class Slide:
    """Animate existing place-managed widgets to their next positions."""

    def __init__(self, owner: tk.Widget):
        self.owner = owner
        self._timer: str | None = None

    def stop(self) -> None:
        if self._timer is not None:
            self.owner.after_cancel(self._timer)
            self._timer = None

    def move(self, positions: list[tuple[tk.Widget, int, int]]) -> None:
        self.stop()
        moves = []
        for widget, x, y in positions:
            position = widget.place_info()
            start_x, start_y = int(position["x"]), int(position["y"])
            if (start_x, start_y) != (x, y):
                moves.append((widget, start_x, start_y, x, y))
        if not moves:
            return
        start = monotonic()

        def tick() -> None:
            progress = min(1, (monotonic() - start) / 0.13)
            eased = 1 - (1 - progress) ** 2
            for widget, start_x, start_y, x, y in moves:
                if widget.winfo_exists():
                    widget.place_configure(
                        x=round(start_x + (x - start_x) * eased),
                        y=round(start_y + (y - start_y) * eased),
                    )
            self._timer = self.owner.after(16, tick) if progress < 1 else None

        tick()


class FileRowDrag:
    """Lift a packed row and preview its new slot on the same file list."""

    def __init__(
        self, parent: tk.Frame, can_reorder: Callable[[], bool],
        commit: Callable[[int, int], None], colors: Callable[[], tuple[str, str]],
    ):
        self.parent = parent
        self.can_reorder = can_reorder
        self.commit = commit
        self.colors = colors
        self.rows: list[tk.Frame] = []
        self._slide = Slide(parent)
        self._row: tk.Frame | None = None
        self._slot: tk.Frame | None = None
        self._start_y = 0
        self._grab_y = 0
        parent.bind("<B1-Motion>", self.motion, add="+")
        parent.bind("<ButtonRelease-1>", self.release, add="+")
        parent.bind("<Escape>", self.cancel, add="+")
        parent.bind("<Unmap>", self.cancel, add="+")
        parent.bind("<Destroy>", lambda _event: self._slide.stop(), add="+")

    def reset(self) -> None:
        self.cancel()
        self.rows.clear()

    def add(self, row: tk.Frame, *handles: tk.Widget) -> None:
        self.rows.append(row)
        for handle in handles:
            handle.bind("<ButtonPress-1>", lambda event, item=row: self.press(item, event))
            handle.bind("<B1-Motion>", self.motion)
            handle.bind("<ButtonRelease-1>", self.release)

    def press(self, row: tk.Frame, event: tk.Event) -> None:
        if len(self.rows) > 1 and self.can_reorder():
            self._row = row
            self._start_y = event.y_root
            self._grab_y = event.y_root - row.winfo_rooty()

    def _lift(self) -> None:
        self._positions = [(row.winfo_y(), row.winfo_height()) for row in self.rows]
        self._centers = [y + height / 2 for y, height in self._positions]
        self._packing = [row.pack_info() for row in self.rows]
        self._parent_height = self.parent.cget("height")
        self._propagate = self.parent.pack_propagate()
        self._source = self.rows.index(self._row)
        self._target = self._source
        self.parent.grab_set()
        self.parent.focus_set()
        self.parent.configure(height=self.parent.winfo_height())
        self.parent.pack_propagate(False)
        for row, (y, height) in zip(self.rows, self._positions):
            row.pack_forget()
            row.place(x=0, y=y, relwidth=1, height=height)
        background, outline = self.colors()
        self._slot = tk.Frame(
            self.parent, bg=background, highlightbackground=outline, highlightthickness=1,
        )
        y, height = self._positions[self._source]
        self._slot.place(x=0, y=y, relwidth=1, height=height)
        self._row.configure(highlightbackground=outline, highlightthickness=1)
        self._row.lift()

    def motion(self, event: tk.Event) -> None:
        if self._row is None or not self.can_reorder():
            return
        if self._slot is None:
            if abs(event.y_root - self._start_y) < 6:
                return
            self._lift()

        top = event.y_root - self.parent.winfo_rooty() - self._grab_y
        self._row.place_configure(y=top)
        center = top + self._positions[self._source][1] / 2
        target = min(range(len(self.rows)), key=lambda index: abs(center - self._centers[index]))
        if target == self._target:
            return
        self._target = target
        y, height = self._positions[target]
        self._slot.place_configure(y=y, height=height)
        order = self.rows.copy()
        order.insert(target, order.pop(self._source))
        self._slide.move([
            (row, 0, y) for row, (y, _height) in zip(order, self._positions)
            if row is not self._row
        ])

    def release(self, event: tk.Event) -> None:
        if self._slot is None:
            self._row = None
            return
        x = event.x_root - self.parent.winfo_rootx()
        y = event.y_root - self.parent.winfo_rooty()
        inside = 0 <= x <= self.parent.winfo_width() and 0 <= y <= self.parent.winfo_height()
        if inside and self.can_reorder():
            self.motion(event)
            source, target = self._source, self._target
            self.cancel()
            if source != target:
                self.commit(source, target)
        else:
            self.cancel()

    def cancel(self, _event: tk.Event | None = None) -> None:
        if self._slot is not None:
            self._slide.stop()
            self._slot.destroy()
            self._slot = None
            self._row.configure(highlightthickness=0)
            for row, options in zip(self.rows, self._packing):
                row.place_forget()
                row.pack(**options)
            self.parent.configure(height=self._parent_height)
            self.parent.pack_propagate(self._propagate)
            if self.parent.grab_current() is self.parent:
                self.parent.grab_release()
        self._row = None
