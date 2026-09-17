"""
ui.py

All GUI construction and UI state management for PDF Merger & Compressor.

PHASE 4 NOTE: "Select PDF Files" and "Add More" now open the real native
Windows file picker (via file_manager.py), validate each selected PDF
(via pdf_engine.py), reject duplicates by resolved path, and populate the
file list with real filenames, sizes, and page counts. Validation runs on
a background thread so the GUI doesn't freeze while pages are counted --
results are handed back to the main thread through a queue and applied
via root.after(), per Tkinter's threading rules.

Clear All and the action buttons become enabled once files exist; Clear
All has been fully implemented since Phase 5.

PHASE 6 NOTE: The single "MERGE & COMPRESS" placeholder is now replaced
with three independent buttons -- MERGE ONLY, COMPRESS ONLY, and
MERGE + COMPRESS -- per the corrected requirement that merging and
compression are two independent operations. Only MERGE ONLY is wired to
real logic in this phase: it calls pdf_engine.merge_pdfs() directly and
never routes through merge_and_compress(). COMPRESS ONLY and
MERGE + COMPRESS exist as correctly-enabled/disabled placeholders whose
click handlers are still stubs -- their real processing is Phase 7/8.

The compression preset selector (Low/Recommended/Maximum) stays visually
enabled at all times rather than being dynamically grayed based on which
action button might be clicked next. There is no "selected operation"
concept in this three-independent-buttons design (unlike a mode-selector
UI, which was deliberately rejected in favor of three buttons) -- each
button is just always available when the file count allows it. "Inert
for MERGE ONLY" is satisfied functionally: _on_merge_only_clicked() never
reads compression_var, so changing the preset has zero effect on a merge
-- not by graying the control out, which would need to be re-enabled
again the moment COMPRESS ONLY ships in Phase 8 for no real benefit now.

PHASE 5 NOTE: Each file row now has working Remove, Move Up, and Move
Down controls. There is no separate "select a row" concept in this UI
(rows are plain Frames, not Listbox entries), so Move Up/Down are
per-row arrow buttons rather than a shared control acting on some
externally-tracked selection -- this mirrors the existing per-row Remove
button pattern from Phase 4 rather than introducing new selection-state
machinery that Phase 5 doesn't otherwise need.

Every row control is bound to the specific PDFFile object by identity
(not by row index), so remove/move always act on the correct file even
after the list has been reordered and row indices have shifted -- see
_find_index() below.

Drag-and-drop import (listed under Phase 4 in the original master plan)
and drag-and-drop reordering (listed under Phase 5 in the original master
plan) are deliberately NOT implemented in this pass -- both were left out
of the explicit requirements given for their respective phases, and plain
Tkinter has no built-in OS drag-and-drop support; it would need an extra
dependency (e.g. tkinterdnd2) that hasn't been approved yet. Flagging
this rather than silently adding a dependency or silently skipping a
spec'd feature.

This module must never contain PDF merge/compress algorithms directly --
it calls into pdf_engine.py and file_manager.py for that.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

import extract_engine
import file_manager
import models
import organize_engine
import pdf_engine
import remove_pages_engine
import rotate_engine
import split_engine
import tool_registry
from config import APP_NAME, APP_VERSION

# ---------------------------------------------------------------------------
# Color palette -- clean modern light theme
# ---------------------------------------------------------------------------

COLOR_BG = "#f5f6f8"
COLOR_CARD = "#ffffff"
COLOR_BORDER = "#e0e2e7"
COLOR_TEXT_PRIMARY = "#1a1a1a"
COLOR_TEXT_SECONDARY = "#6b7280"
COLOR_TEXT_MUTED = "#9ca3af"
COLOR_ACCENT = "#2563eb"
COLOR_ACCENT_HOVER = "#1d4ed8"
COLOR_ACCENT_DISABLED = "#a8c2f7"
COLOR_DROPZONE_BORDER = "#c7cad1"
COLOR_SECONDARY_BTN_BG = "#eef0f3"
COLOR_SECONDARY_BTN_FG = "#1a1a1a"


# ---------------------------------------------------------------------------
# Pure import-classification logic
# ---------------------------------------------------------------------------
# Deliberately kept free of any tkinter dependency so it can be unit tested
# without a display (see tests/test_ui_import.py). This is the function the
# background worker thread calls; it never touches widgets.

def prepare_import(
    paths: Iterable[Path],
    existing_paths: Set[Path],
) -> Dict[str, list]:
    """Validate and classify a batch of candidate PDF paths.

    `existing_paths` should be the set of already-imported files' resolved
    paths (i.e. duplicate detection is by exact path, not filename, per
    the Phase 4 requirement -- two different folders' "report.pdf" are NOT
    duplicates of each other).

    Returns:
        {
            "added": [models.PDFFile, ...],       # in selection order
            "skipped_duplicates": [Path, ...],
            "errors": [(Path, str), ...],          # (path, human-readable reason)
        }

    Never raises: every per-file failure is caught and classified so one
    bad file can't abort the whole batch.
    """
    added: List[models.PDFFile] = []
    skipped_duplicates: List[Path] = []
    errors: List[Tuple[Path, str]] = []

    seen = set(existing_paths)

    for raw_path in paths:
        try:
            path = Path(raw_path)
            resolved = path.resolve()
        except Exception:
            # Defense in depth: Path construction/resolution can fail on
            # genuinely malformed input (e.g. certain reserved names or
            # malformed UNC paths on Windows). One bad path must not
            # abort the rest of the batch, same as any other per-file
            # failure below.
            errors.append((
                Path(str(raw_path)),
                f"'{raw_path}' is not a valid file path.",
            ))
            continue

        if resolved in seen:
            skipped_duplicates.append(path)
            continue

        try:
            info = pdf_engine.get_pdf_info(path)
            pdf_file = models.PDFFile(**info)
        except pdf_engine.PDFEngineError as exc:
            errors.append((path, str(exc)))
            continue
        except Exception:
            # Defense in depth: pdf_engine should only raise PDFEngineError
            # subclasses, but a truly unexpected error must still not
            # crash the batch or leak a raw traceback to the user.
            errors.append(
                (path, f"'{path.name}' could not be read due to an "
                       f"unexpected error.")
            )
            continue

        added.append(pdf_file)
        seen.add(resolved)

    return {
        "added": added,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors,
    }


class MainWindow:
    """Owns the root Tk window and all widgets."""

    def __init__(self, root: tk.Tk):
        self.root = root

        # Real application state (Phase 4+).
        self.state = models.AppState()

        # Background import worker communicates back to the main thread
        # through this queue; only the main thread ever touches widgets.
        self._import_queue: "queue.Queue[dict]" = queue.Queue()
        self._import_in_progress = False

        # Phase 6: separate queue/flag for the merge operation, kept
        # distinct from import so each has its own clear lifecycle, even
        # though both are prevented from running concurrently (see
        # _set_controls_enabled).
        self._merge_queue: "queue.Queue[dict]" = queue.Queue()
        self._merge_in_progress = False

        # Phase 8: separate queues/flags for Compress Only and
        # Merge + Compress, following the same pattern as import/merge.
        # Each queue carries typed items ({"type": "progress", ...} or
        # {"type": "done", ...}) so a single poll loop can both surface
        # incremental status updates (e.g. "Compressing file 2 of 5...")
        # and detect completion, without needing separate flags for
        # "is there a progress update" vs "is the operation done".
        self._compress_queue: "queue.Queue[dict]" = queue.Queue()
        self._compress_in_progress = False

        self._mergecompress_queue: "queue.Queue[dict]" = queue.Queue()
        self._mergecompress_in_progress = False

        self.compression_var = tk.StringVar(value="recommended")
        self.status_var = tk.StringVar(value="Status: Ready")
        self.files_count_var = tk.StringVar(value="Files: 0")
        self.pages_count_var = tk.StringVar(value="Pages: 0")
        self.size_var = tk.StringVar(value="Size: 0 B")

        # Phase 12: which tool is currently shown in the workspace.
        # Starts on the default tool (Merge & Compress) so the app opens
        # exactly where it always has.
        self.current_tool_id: str = tool_registry.DEFAULT_TOOL_ID
        self.tool_nav_buttons: Dict[str, tk.Widget] = {}

        # Phase 13: Split PDF's own state, deliberately separate from
        # self.state (the Merge/Compress multi-file AppState) -- Split
        # operates on exactly one source file at a time, which is a
        # different shape of state, not a one-file-list special case of
        # the multi-file list. Kept as a plain models.PDFFile (reusing
        # that existing model) rather than a new one-off type.
        self.split_source: Optional[models.PDFFile] = None
        self.split_mode_var = tk.StringVar(value="individual")
        self.split_n_var = tk.StringVar(value="1")
        self.split_ranges_var = tk.StringVar(value="")
        self.split_status_var = tk.StringVar(value="Status: Ready")

        self._split_import_queue: "queue.Queue[dict]" = queue.Queue()
        self._split_import_in_progress = False
        self._split_queue: "queue.Queue[dict]" = queue.Queue()
        self.split_in_progress = False

        # Phase 14: Remove Pages' own state, deliberately separate from
        # self.state and from Split's self.split_source -- like Split,
        # it operates on exactly one source file at a time, but its
        # second piece of state (a page/range selection string to
        # validate live as the user types, rather than a split mode) is
        # its own genuinely different shape, not a variant of either
        # existing tool's state.
        self.remove_pages_source: Optional[models.PDFFile] = None
        self.remove_pages_selection_var = tk.StringVar(value="")
        self.remove_pages_status_var = tk.StringVar(value="Status: Ready")

        self._remove_pages_import_queue: "queue.Queue[dict]" = queue.Queue()
        self._remove_pages_import_in_progress = False
        self._remove_pages_queue: "queue.Queue[dict]" = queue.Queue()
        self.remove_pages_in_progress = False

        # Phase 15: Extract Pages' own state, deliberately separate from
        # self.state, self.split_source, and self.remove_pages_source --
        # like Split and Remove Pages, it operates on exactly one source
        # file at a time. Its shape (a source file + a live-validated
        # page/range selection string) is closest to Remove Pages', but
        # it is NOT the same tool with the selection inverted -- see
        # extract_engine.py's module docstring for the order/duplicate-
        # preserving semantic that makes it genuinely different.
        self.extract_source: Optional[models.PDFFile] = None
        self.extract_selection_var = tk.StringVar(value="")
        self.extract_status_var = tk.StringVar(value="Status: Ready")

        self._extract_import_queue: "queue.Queue[dict]" = queue.Queue()
        self._extract_import_in_progress = False
        self._extract_queue: "queue.Queue[dict]" = queue.Queue()
        self.extract_in_progress = False

        # Phase 16: Organize/Reorder Pages' own state, deliberately
        # separate from every other tool's -- shape-wise closest to
        # Extract Pages' (single source + a live-validated order text),
        # but the text represents a *complete permutation* of every
        # source page rather than a subset. self._organize_current_order
        # is the 0-based order most recently successfully parsed from
        # organize_order_var's text -- it's what Move Up/Move Down
        # operate on (see _on_organize_move_up_clicked() /
        # _on_organize_move_down_clicked()); it is kept in sync with the
        # text any time the text parses successfully (see
        # _update_organize_feedback()), and is None whenever the text
        # does not currently represent a valid, complete order.
        self.organize_source: Optional[models.PDFFile] = None
        self.organize_order_var = tk.StringVar(value="")
        self.organize_status_var = tk.StringVar(value="Status: Ready")
        self._organize_current_order: Optional[List[int]] = None

        self._organize_import_queue: "queue.Queue[dict]" = queue.Queue()
        self._organize_import_in_progress = False
        self._organize_queue: "queue.Queue[dict]" = queue.Queue()
        self.organize_in_progress = False

        # Phase 17: Rotate Pages' own state, deliberately separate from
        # every other tool's -- shape-wise closest to Remove/Extract
        # Pages' (single source + a live-validated page-selection text,
        # reusing split_engine.parse_page_ranges() via rotate_engine.
        # resolve_pages_to_rotate() -- see that module's docstring),
        # plus a direction (clockwise/counterclockwise) and an angle
        # (90/180/270) choice that together resolve to a single
        # clockwise-degrees delta applied to every selected page.
        self.rotate_source: Optional[models.PDFFile] = None
        self.rotate_selection_var = tk.StringVar(value="")
        self.rotate_direction_var = tk.StringVar(value=rotate_engine.CLOCKWISE)
        self.rotate_angle_var = tk.IntVar(value=90)
        self.rotate_status_var = tk.StringVar(value="Status: Ready")

        self._rotate_import_queue: "queue.Queue[dict]" = queue.Queue()
        self._rotate_import_in_progress = False
        self._rotate_queue: "queue.Queue[dict]" = queue.Queue()
        self.rotate_in_progress = False

        self._configure_window()
        self._configure_styles()
        self._build_layout()

    # ------------------------------------------------------------------
    # Window / style setup
    # ------------------------------------------------------------------

    def _configure_window(self) -> None:
        self.root.title(APP_NAME)
        # Phase 12: widened to fit the new tool navigation sidebar. The
        # Merge & Compress workspace itself keeps its original size and
        # layout -- only the window grew to make room alongside it.
        self.root.geometry("960x820")
        self.root.minsize(860, 800)
        self.root.configure(bg=COLOR_BG)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        # 'clam' is the most style-able built-in ttk theme and looks
        # consistent across Windows versions.
        style.theme_use("clam")

        style.configure(
            "Primary.TButton",
            background=COLOR_ACCENT,
            foreground="#ffffff",
            font=("Segoe UI", 11, "bold"),
            padding=(18, 10),
            borderwidth=0,
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", COLOR_ACCENT_DISABLED),
                ("active", COLOR_ACCENT_HOVER),
            ],
            foreground=[("disabled", "#f0f4fe")],
        )

        style.configure(
            "Secondary.TButton",
            background=COLOR_SECONDARY_BTN_BG,
            foreground=COLOR_SECONDARY_BTN_FG,
            font=("Segoe UI", 10),
            padding=(14, 8),
            borderwidth=0,
        )
        style.map(
            "Secondary.TButton",
            background=[
                ("disabled", "#f5f6f8"),
                ("active", "#e2e5ea"),
            ],
            foreground=[("disabled", COLOR_TEXT_MUTED)],
        )

        # Outlined variant for the two non-primary action buttons (MERGE
        # ONLY / COMPRESS ONLY), visually distinct from both the solid
        # Primary style (used for MERGE + COMPRESS, the convenience
        # all-in-one action) and the small gray Secondary style (used for
        # Add More/Clear All/per-row Remove/Move).
        style.configure(
            "ActionSecondary.TButton",
            background="#ffffff",
            foreground=COLOR_ACCENT,
            font=("Segoe UI", 11, "bold"),
            padding=(14, 10),
            borderwidth=1,
            relief="solid",
            bordercolor=COLOR_ACCENT,
        )
        style.map(
            "ActionSecondary.TButton",
            background=[
                ("disabled", "#f5f6f8"),
                ("active", "#eef2ff"),
            ],
            foreground=[("disabled", COLOR_TEXT_MUTED)],
            bordercolor=[("disabled", COLOR_BORDER)],
        )

        style.configure(
            "Compression.TRadiobutton",
            background=COLOR_CARD,
            foreground=COLOR_TEXT_PRIMARY,
            font=("Segoe UI", 10),
        )

        style.configure(
            "Card.TFrame",
            background=COLOR_CARD,
        )

        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor="#e5e7eb",
            background=COLOR_ACCENT,
            thickness=8,
            borderwidth=0,
        )

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        # Phase 12: a persistent navigation sidebar on the left, and a
        # workspace area on the right whose content switches based on
        # the selected tool. The Merge & Compress workspace below is
        # built exactly as it always has been (same methods, same
        # widgets, same attribute names) -- it's just now placed inside
        # self.merge_compress_view instead of directly under the root.
        #
        # Bug fix (post-Phase 13): with enough imported files, the
        # Merge/Compress workspace's content (file list + summary +
        # compression card + action buttons + status) can grow taller
        # than the visible window, pushing MERGE ONLY / COMPRESS ONLY /
        # MERGE + COMPRESS below the bottom edge with no way to reach
        # them. The fix is the standard Tkinter "scrollable frame"
        # pattern: workspace_container (an existing attribute name, kept
        # exactly as other code already expects it) is now the inner
        # frame embedded in a Canvas via create_window(), with a
        # vertical Scrollbar alongside it. Nothing about what gets
        # packed INTO workspace_container changes -- merge_compress_view,
        # split_view, and coming_soon_view are still built and
        # shown/hidden exactly as before; only their ultimate container
        # gained scrolling. The sidebar built by _build_tool_navigation()
        # is a sibling of the scrollable area, not inside it, so it
        # never scrolls away.
        shell = tk.Frame(self.root, bg=COLOR_BG)
        shell.pack(fill="both", expand=True)

        self._build_tool_navigation(shell)
        self._build_scrollable_workspace(shell)

        self.merge_compress_view = tk.Frame(self.workspace_container, bg=COLOR_BG)

        outer = self.merge_compress_view
        self._build_header(outer)
        self._build_dropzone(outer)
        self._build_file_list(outer)
        self._build_summary_bar(outer)
        self._build_list_actions(outer)
        self._build_compression_controls(outer)
        self._build_action_buttons(outer)
        self._build_status_area(outer)

        self._build_coming_soon_view(self.workspace_container)
        self._build_split_workspace(self.workspace_container)
        self._build_remove_pages_workspace(self.workspace_container)
        self._build_extract_workspace(self.workspace_container)
        self._build_organize_workspace(self.workspace_container)
        self._build_rotate_workspace(self.workspace_container)

        self._select_tool(self.current_tool_id)

    def _build_scrollable_workspace(self, parent: tk.Widget) -> None:
        """Builds the Canvas + vertical Scrollbar + inner-frame structure
        that makes the workspace area vertically scrollable.

        self.workspace_container (the inner frame) is what every tool
        view is built into and packed/unpacked from -- exactly as
        before this fix. Only its parent changed, from being packed
        directly into `shell` to being embedded in a Canvas.
        """
        workspace_outer = tk.Frame(parent, bg=COLOR_BG)
        workspace_outer.pack(side="left", fill="both", expand=True)

        self.workspace_canvas = tk.Canvas(
            workspace_outer, bg=COLOR_BG, highlightthickness=0, bd=0,
        )
        self.workspace_scrollbar = ttk.Scrollbar(
            workspace_outer, orient="vertical",
            command=self.workspace_canvas.yview,
        )
        self.workspace_canvas.configure(yscrollcommand=self.workspace_scrollbar.set)
        self.workspace_canvas.pack(side="left", fill="both", expand=True)
        # The scrollbar is shown/hidden on demand by
        # _update_workspace_scrollbar_visibility() -- not packed here
        # unconditionally, so it doesn't appear when content already
        # fits (e.g. the empty-state screen, or just a couple of files).

        self.workspace_container = tk.Frame(self.workspace_canvas, bg=COLOR_BG)
        self._workspace_window_id = self.workspace_canvas.create_window(
            (0, 0), window=self.workspace_container, anchor="nw",
        )

        # Standard scrollregion-tracking pattern: whenever the inner
        # frame's actual required size changes (e.g. a file row was
        # added/removed, changing merge_compress_view's height), update
        # the canvas's scrollregion so scrolling range always matches
        # the real content -- no explicit "recalculate scrolling" calls
        # needed anywhere in the existing file-list/import/operation
        # code, since this fires automatically via Tkinter's own
        # geometry propagation.
        self.workspace_container.bind("<Configure>", self._on_workspace_content_configure)
        # Keep the embedded frame's width matched to the canvas's own
        # visible width, so content expands horizontally to fill the
        # available workspace width (only vertical scrolling is added).
        self.workspace_canvas.bind("<Configure>", self._on_workspace_canvas_configure)

        # Mouse wheel: bound only while the pointer is actually over the
        # workspace canvas (attached on <Enter>, detached on <Leave>),
        # never a permanently-global handler -- this is the standard
        # idiom for reliable cross-widget wheel scrolling in Tkinter
        # (a plain per-widget <MouseWheel> bind is not reliably
        # delivered when a child widget is under the pointer), and
        # because it's only active during hover, it cannot interfere
        # with scrolling in any other widget.
        self.workspace_canvas.bind("<Enter>", self._bind_workspace_mousewheel)
        self.workspace_canvas.bind("<Leave>", self._unbind_workspace_mousewheel)
        # Ensure the (hover-scoped) global wheel binding can never
        # outlive the canvas it was created for.
        self.workspace_canvas.bind("<Destroy>", self._unbind_workspace_mousewheel)

    def _on_workspace_content_configure(self, _event=None) -> None:
        self.workspace_canvas.configure(scrollregion=self.workspace_canvas.bbox("all"))
        self._update_workspace_scrollbar_visibility()

    def _on_workspace_canvas_configure(self, event) -> None:
        self.workspace_canvas.itemconfigure(self._workspace_window_id, width=event.width)
        self._update_workspace_scrollbar_visibility()

    def _update_workspace_scrollbar_visibility(self) -> None:
        """Shows the scrollbar only when the current content is
        genuinely taller than the visible workspace area, and hides it
        otherwise (e.g. the empty-state screen, or a short file list) --
        per the "hide/disable it when content fits" guidance. Purely
        cosmetic: scrolling itself (mouse wheel, dragging an already-
        visible scrollbar) works the same regardless of this.
        """
        bbox = self.workspace_canvas.bbox("all")
        if not bbox:
            return
        content_height = bbox[3] - bbox[1]
        canvas_height = self.workspace_canvas.winfo_height()
        needs_scrollbar = content_height > canvas_height
        # Deliberately NOT winfo_ismapped(): that reflects the native
        # window system's own map/unmap state, which on Windows is not
        # guaranteed to be in sync with Tk's internal pack bookkeeping
        # at the moment this runs. winfo_manager() reports Tk's own
        # geometry-manager registration for the widget -- exactly what
        # pack()/pack_forget() toggle below -- so it's the reliable,
        # synchronous source of truth for "is this widget currently
        # packed", regardless of platform.
        is_shown = self.workspace_scrollbar.winfo_manager() == "pack"
        if needs_scrollbar and not is_shown:
            self.workspace_scrollbar.pack(side="right", fill="y")
        elif not needs_scrollbar and is_shown:
            self.workspace_scrollbar.pack_forget()

    def _bind_workspace_mousewheel(self, _event=None) -> None:
        self.workspace_canvas.bind_all("<MouseWheel>", self._on_workspace_mousewheel)

    def _unbind_workspace_mousewheel(self, _event=None) -> None:
        self.workspace_canvas.unbind_all("<MouseWheel>")

    def _on_workspace_mousewheel(self, event) -> None:
        # Windows delivers <MouseWheel> with event.delta in multiples of
        # 120 (positive = wheel up, negative = wheel down); translate
        # that into a small number of scroll "units" for the canvas.
        self.workspace_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_tool_navigation(self, parent: tk.Widget) -> None:
        """A simple, always-visible sidebar listing every registered
        tool (tool_registry.get_all_tools()), so switching tools never
        means scattering tool names through ui.py -- this is the only
        place that reads the registry to build the tool list.
        """
        nav = tk.Frame(parent, bg=COLOR_CARD, width=200)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)  # keep the fixed width regardless of content
        self._nav_frame = nav

        title = tk.Label(
            nav,
            text="PDF TOOLS",
            font=("Segoe UI", 10, "bold"),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_MUTED,
            anchor="w",
        )
        title.pack(fill="x", padx=16, pady=(20, 8))

        for tool in tool_registry.get_all_tools():
            self._build_tool_nav_button(nav, tool)

    def _build_tool_nav_button(self, parent: tk.Widget, tool: "tool_registry.Tool") -> None:
        is_selected = tool.id == self.current_tool_id
        fg = COLOR_ACCENT if is_selected else (
            COLOR_TEXT_PRIMARY if tool.is_available else COLOR_TEXT_MUTED
        )
        bg = "#eef2ff" if is_selected else COLOR_CARD

        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x")

        label_text = tool.name if tool.is_available else f"{tool.name}"
        btn = tk.Label(
            row,
            text=label_text,
            font=("Segoe UI", 10, "bold" if is_selected else "normal"),
            bg=bg,
            fg=fg,
            anchor="w",
            padx=16,
            pady=10,
            cursor="hand2",
        )
        btn.pack(fill="x")
        btn.bind("<Button-1>", lambda _event, tid=tool.id: self._select_tool(tid))

        if not tool.is_available:
            badge = tk.Label(
                row,
                text="Coming soon",
                font=("Segoe UI", 8),
                bg=bg,
                fg=COLOR_TEXT_MUTED,
                anchor="w",
                padx=16,
            )
            badge.pack(fill="x", pady=(0, 6))

        self.tool_nav_buttons[tool.id] = row

    def _build_coming_soon_view(self, parent: tk.Widget) -> None:
        """A single, generic placeholder view shared by every
        not-yet-implemented tool -- Phase 12 builds this once, per the
        instruction to create architecture/placeholders without
        implementing the future operations themselves. Its text is
        updated per-tool by _select_tool()/_update_coming_soon_view().
        """
        self.coming_soon_view = tk.Frame(parent, bg=COLOR_BG)

        inner = tk.Frame(self.coming_soon_view, bg=COLOR_BG)
        inner.pack(expand=True)

        self.coming_soon_title_label = tk.Label(
            inner,
            text="",
            font=("Segoe UI", 18, "bold"),
            bg=COLOR_BG,
            fg=COLOR_TEXT_PRIMARY,
        )
        self.coming_soon_title_label.pack(pady=(0, 8))

        self.coming_soon_desc_label = tk.Label(
            inner,
            text="",
            font=("Segoe UI", 11),
            bg=COLOR_BG,
            fg=COLOR_TEXT_SECONDARY,
            wraplength=420,
            justify="center",
        )
        self.coming_soon_desc_label.pack(pady=(0, 16))

        badge = tk.Label(
            inner,
            text="COMING SOON",
            font=("Segoe UI", 9, "bold"),
            bg="#eef0f3",
            fg=COLOR_TEXT_MUTED,
            padx=12,
            pady=6,
        )
        badge.pack()

    def _update_coming_soon_view(self, tool: "tool_registry.Tool") -> None:
        self.coming_soon_title_label.configure(text=tool.name)
        self.coming_soon_desc_label.configure(text=tool.description)

    def _build_split_workspace(self, parent: tk.Widget) -> None:
        """Phase 13: the Split PDF tool's dedicated workspace. Kept
        entirely separate from the Merge/Compress workspace's widgets
        and state (self.split_source, not self.state) -- Split operates
        on exactly one source file at a time, a genuinely different
        shape of state, not a one-file special case of the multi-file
        list. Visual style matches the existing cards/buttons so the
        app still feels like one consistent application.
        """
        self.split_view = tk.Frame(parent, bg=COLOR_BG)
        outer = self.split_view

        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header, text="SPLIT PDF", font=("Segoe UI", 19, "bold"),
            bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Split one PDF into multiple files -- locally, no upload.",
            font=("Segoe UI", 10), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(2, 0))

        # Source file card
        source_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        source_card.pack(fill="x", pady=(0, 16))
        source_inner = tk.Frame(source_card, bg=COLOR_CARD)
        source_inner.pack(fill="x", padx=18, pady=16)

        self.split_select_btn = ttk.Button(
            source_inner, text="Select PDF File", style="Primary.TButton",
            command=self._on_split_select_file_clicked,
        )
        self.split_select_btn.pack(side="left")

        self.split_source_label = tk.Label(
            source_inner, text="No file selected.",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        )
        self.split_source_label.pack(side="left", padx=(16, 0))

        # Split mode card
        mode_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        mode_card.pack(fill="x", pady=(0, 16))
        mode_inner = tk.Frame(mode_card, bg=COLOR_CARD)
        mode_inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            mode_inner, text="Split Mode", font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        self.split_individual_radio = ttk.Radiobutton(
            mode_inner, text="Individual pages (one PDF per page)",
            value="individual", variable=self.split_mode_var,
            style="Compression.TRadiobutton",
            command=self._update_split_mode_controls,
        )
        self.split_individual_radio.pack(anchor="w", pady=2)

        every_n_row = tk.Frame(mode_inner, bg=COLOR_CARD)
        every_n_row.pack(fill="x", pady=2)
        self.split_every_n_radio = ttk.Radiobutton(
            every_n_row, text="Every", value="every_n",
            variable=self.split_mode_var, style="Compression.TRadiobutton",
            command=self._update_split_mode_controls,
        )
        self.split_every_n_radio.pack(side="left")
        self.split_n_entry = ttk.Entry(
            every_n_row, textvariable=self.split_n_var, width=5,
        )
        self.split_n_entry.pack(side="left", padx=(6, 6))
        tk.Label(
            every_n_row, text="pages", font=("Segoe UI", 10),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(side="left")

        custom_row = tk.Frame(mode_inner, bg=COLOR_CARD)
        custom_row.pack(fill="x", pady=(2, 0))
        self.split_custom_radio = ttk.Radiobutton(
            custom_row, text="Custom ranges:", value="custom",
            variable=self.split_mode_var, style="Compression.TRadiobutton",
            command=self._update_split_mode_controls,
        )
        self.split_custom_radio.pack(side="left")
        self.split_ranges_entry = ttk.Entry(
            custom_row, textvariable=self.split_ranges_var, width=24,
        )
        self.split_ranges_entry.pack(side="left", padx=(6, 0))

        tk.Label(
            mode_inner,
            text="Example: 1-3, 5, 7-9  (each range becomes its own file)",
            font=("Segoe UI", 9), bg=COLOR_CARD, fg=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(6, 0))

        # Action
        action_wrapper = tk.Frame(outer, bg=COLOR_BG)
        action_wrapper.pack(fill="x", pady=(0, 16))
        self.split_button = ttk.Button(
            action_wrapper, text="SPLIT PDF", style="Primary.TButton",
            command=self._on_split_execute_clicked, state="disabled",
        )
        self.split_button.pack(fill="x", ipady=4)

        # Status
        status_frame = tk.Frame(outer, bg=COLOR_BG)
        status_frame.pack(fill="x")
        self.split_status_label = tk.Label(
            status_frame, textvariable=self.split_status_var,
            font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self.split_status_label.pack(fill="x", pady=(0, 6))
        self.split_progress_bar = ttk.Progressbar(
            status_frame, style="App.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", value=0,
        )
        self.split_progress_bar.pack(fill="x")

        self._update_split_mode_controls()

    def _update_split_mode_controls(self) -> None:
        """Only the entry matching the currently-selected split mode is
        interactive -- avoids the confusing appearance of an "Every N
        pages" box that's editable while "Custom ranges" is selected.
        """
        mode = self.split_mode_var.get()
        self.split_n_entry.configure(state="normal" if mode == "every_n" else "disabled")
        self.split_ranges_entry.configure(state="normal" if mode == "custom" else "disabled")

    def _update_split_button_state(self) -> None:
        self.split_button.configure(
            state="normal" if self.split_source is not None else "disabled"
        )

    def _update_split_controls_state(self) -> None:
        """The single place that restores Split PDF's own controls to
        their correct enabled state once no operation is running --
        select-file button and mode radios are always re-enabled; the
        N/ranges entries follow whichever mode is currently selected
        (via _update_split_mode_controls()); the Split button depends
        on whether a source file is currently selected (via
        _update_split_button_state()). Mirrors the role
        _update_button_states() already plays for Merge/Compress's own
        select/add/clear buttons -- one consolidated place so a
        completion handler can't forget to re-enable one of these
        controls, which is exactly the bug this method fixes (Split's
        select-file button and mode radios were previously never
        explicitly re-enabled after an operation completed).
        """
        self.split_select_btn.configure(state="normal")
        self.split_individual_radio.configure(state="normal")
        self.split_every_n_radio.configure(state="normal")
        self.split_custom_radio.configure(state="normal")
        self._update_split_mode_controls()
        self._update_split_button_state()

    def _update_split_source_label(self) -> None:
        if self.split_source is None:
            self.split_source_label.configure(text="No file selected.")
        else:
            self.split_source_label.configure(
                text=(
                    f"{self.split_source.name}  \u2014  "
                    f"{self.split_source.page_count_display}, "
                    f"{self.split_source.size_display}"
                )
            )

    def _build_remove_pages_workspace(self, parent: tk.Widget) -> None:
        """Phase 14: the Remove Pages tool's dedicated workspace.

        Follows the exact same shape as _build_split_workspace() above
        (its own state, its own view frame, same cards/buttons/status
        styling) -- reusing the established visual language rather than
        inventing a new one, per the Phase 14 requirement. Like Split,
        this operates on exactly one source file at a time
        (self.remove_pages_source), kept separate from self.state.
        """
        self.remove_pages_view = tk.Frame(parent, bg=COLOR_BG)
        outer = self.remove_pages_view

        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header, text="REMOVE PAGES", font=("Segoe UI", 19, "bold"),
            bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Remove selected pages from a PDF -- locally, no upload.",
            font=("Segoe UI", 10), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(2, 0))

        # Source file card
        source_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        source_card.pack(fill="x", pady=(0, 16))
        source_inner = tk.Frame(source_card, bg=COLOR_CARD)
        source_inner.pack(fill="x", padx=18, pady=16)

        self.remove_pages_select_btn = ttk.Button(
            source_inner, text="Select PDF File", style="Primary.TButton",
            command=self._on_remove_pages_select_file_clicked,
        )
        self.remove_pages_select_btn.pack(side="left")

        self.remove_pages_source_label = tk.Label(
            source_inner, text="No file selected.",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        )
        self.remove_pages_source_label.pack(side="left", padx=(16, 0))

        # Page-selection card
        selection_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        selection_card.pack(fill="x", pady=(0, 16))
        selection_inner = tk.Frame(selection_card, bg=COLOR_CARD)
        selection_inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            selection_inner, text="Pages to Remove",
            font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        entry_row = tk.Frame(selection_inner, bg=COLOR_CARD)
        entry_row.pack(fill="x")
        self.remove_pages_selection_entry = ttk.Entry(
            entry_row, textvariable=self.remove_pages_selection_var, width=30,
        )
        self.remove_pages_selection_entry.pack(side="left")
        self.remove_pages_clear_btn = ttk.Button(
            entry_row, text="Clear Selection", style="Secondary.TButton",
            command=self._on_remove_pages_clear_selection_clicked,
        )
        self.remove_pages_clear_btn.pack(side="left", padx=(8, 0))

        tk.Label(
            selection_inner,
            text="Example: 1,3,5-7  (1-based page numbers, inclusive ranges)",
            font=("Segoe UI", 9), bg=COLOR_CARD, fg=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(6, 10))

        # Live preview/summary -- Phase 14 requirement 7: no engine run
        # needed just to validate basic selection syntax, so this is
        # driven entirely by remove_pages_engine.resolve_pages_to_remove()
        # (pure parsing, no PDF write) via the StringVar trace below.
        self.remove_pages_feedback_var = tk.StringVar(value="")
        self.remove_pages_feedback_label = tk.Label(
            selection_inner, textvariable=self.remove_pages_feedback_var,
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
            justify="left", anchor="w",
        )
        self.remove_pages_feedback_label.pack(fill="x")

        self.remove_pages_error_var = tk.StringVar(value="")
        self.remove_pages_error_label = tk.Label(
            selection_inner, textvariable=self.remove_pages_error_var,
            font=("Segoe UI", 9), bg=COLOR_CARD, fg="#c0392b",
            justify="left", anchor="w", wraplength=520,
        )
        self.remove_pages_error_label.pack(fill="x", pady=(4, 0))

        # Action
        action_wrapper = tk.Frame(outer, bg=COLOR_BG)
        action_wrapper.pack(fill="x", pady=(0, 16))
        self.remove_pages_button = ttk.Button(
            action_wrapper, text="REMOVE PAGES", style="Primary.TButton",
            command=self._on_remove_pages_execute_clicked, state="disabled",
        )
        self.remove_pages_button.pack(fill="x", ipady=4)

        # Status
        status_frame = tk.Frame(outer, bg=COLOR_BG)
        status_frame.pack(fill="x")
        self.remove_pages_status_label = tk.Label(
            status_frame, textvariable=self.remove_pages_status_var,
            font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self.remove_pages_status_label.pack(fill="x", pady=(0, 6))
        self.remove_pages_progress_bar = ttk.Progressbar(
            status_frame, style="App.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", value=0,
        )
        self.remove_pages_progress_bar.pack(fill="x")

        # Live validation feedback as the user types -- never requires
        # the engine (the actual PDF write) to run just to show
        # "Pages to remove: ... / Pages selected: N / Pages remaining: X"
        # or a validation error for bad syntax.
        self.remove_pages_selection_var.trace_add(
            "write", self._on_remove_pages_selection_changed
        )
        self._update_remove_pages_feedback()

    def _on_remove_pages_selection_changed(self, *_args) -> None:
        self._update_remove_pages_feedback()

    def _update_remove_pages_feedback(self) -> None:
        """The single place that keeps the Pages-to-remove preview, the
        validation error message, and the REMOVE PAGES button's enabled
        state all in sync with the current selection text -- driven
        purely by remove_pages_engine.resolve_pages_to_remove() (no PDF
        write), so bad input is caught instantly and can never crash the
        UI (every PageRangeError is caught right here).
        """
        text = self.remove_pages_selection_var.get()
        self.remove_pages_error_var.set("")

        if self.remove_pages_source is None:
            self.remove_pages_feedback_var.set("Select a PDF file first.")
            self.remove_pages_button.configure(state="disabled")
            return

        page_count = self.remove_pages_source.page_count or 0

        if not text.strip():
            self.remove_pages_feedback_var.set(
                f"Pages remaining: {page_count} of {page_count}"
            )
            self.remove_pages_button.configure(state="disabled")
            return

        try:
            indices = remove_pages_engine.resolve_pages_to_remove(text, page_count)
        except split_engine.PageRangeError as exc:
            self.remove_pages_error_var.set(str(exc))
            self.remove_pages_feedback_var.set(f"Pages to remove: {text.strip()}")
            self.remove_pages_button.configure(state="disabled")
            return

        remaining = page_count - len(indices)
        self.remove_pages_feedback_var.set(
            f"Pages to remove: {text.strip()}\n"
            f"Pages selected: {len(indices)}\n"
            f"Pages remaining: {remaining}"
        )
        self.remove_pages_button.configure(
            state="disabled" if self._any_operation_in_progress() else "normal"
        )

    def _update_remove_pages_controls_state(self) -> None:
        """The single place that restores Remove Pages' own controls to
        their correct enabled state once no operation is running --
        mirrors _update_split_controls_state()'s role for Split PDF.
        """
        self.remove_pages_select_btn.configure(state="normal")
        self.remove_pages_clear_btn.configure(state="normal")
        self.remove_pages_selection_entry.configure(state="normal")
        self._update_remove_pages_feedback()

    def _update_remove_pages_source_label(self) -> None:
        if self.remove_pages_source is None:
            self.remove_pages_source_label.configure(text="No file selected.")
        else:
            self.remove_pages_source_label.configure(
                text=(
                    f"{self.remove_pages_source.name}  \u2014  "
                    f"{self.remove_pages_source.page_count_display}, "
                    f"{self.remove_pages_source.size_display}"
                )
            )

    def _build_extract_workspace(self, parent: tk.Widget) -> None:
        """Phase 15: the Extract Pages tool's dedicated workspace.

        Follows the exact same shape as _build_remove_pages_workspace()
        above (its own state, its own view frame, same cards/buttons/
        status styling) -- reusing the established visual language
        rather than inventing a new one. Like Split and Remove Pages,
        this operates on exactly one source file at a time
        (self.extract_source), kept separate from self.state.
        """
        self.extract_view = tk.Frame(parent, bg=COLOR_BG)
        outer = self.extract_view

        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header, text="EXTRACT PAGES", font=("Segoe UI", 19, "bold"),
            bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Save specific pages of a PDF as a new file -- locally, no upload.",
            font=("Segoe UI", 10), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(2, 0))

        # Source file card
        source_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        source_card.pack(fill="x", pady=(0, 16))
        source_inner = tk.Frame(source_card, bg=COLOR_CARD)
        source_inner.pack(fill="x", padx=18, pady=16)

        self.extract_select_btn = ttk.Button(
            source_inner, text="Select PDF File", style="Primary.TButton",
            command=self._on_extract_select_file_clicked,
        )
        self.extract_select_btn.pack(side="left")

        self.extract_source_label = tk.Label(
            source_inner, text="No file selected.",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        )
        self.extract_source_label.pack(side="left", padx=(16, 0))

        # Page-selection card
        selection_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        selection_card.pack(fill="x", pady=(0, 16))
        selection_inner = tk.Frame(selection_card, bg=COLOR_CARD)
        selection_inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            selection_inner, text="Pages to Extract",
            font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        entry_row = tk.Frame(selection_inner, bg=COLOR_CARD)
        entry_row.pack(fill="x")
        self.extract_selection_entry = ttk.Entry(
            entry_row, textvariable=self.extract_selection_var, width=30,
        )
        self.extract_selection_entry.pack(side="left")
        self.extract_clear_btn = ttk.Button(
            entry_row, text="Clear Selection", style="Secondary.TButton",
            command=self._on_extract_clear_selection_clicked,
        )
        self.extract_clear_btn.pack(side="left", padx=(8, 0))

        tk.Label(
            selection_inner,
            text=(
                "Example: 2,4  or  1-3,5,7-9  (1-based page numbers; "
                "pages are extracted in the order you list them)"
            ),
            font=("Segoe UI", 9), bg=COLOR_CARD, fg=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(6, 10))

        # Live preview/summary -- no engine run needed just to validate
        # basic selection syntax, so this is driven entirely by
        # extract_engine.resolve_pages_to_extract() (pure parsing, no
        # PDF write) via the StringVar trace below.
        self.extract_feedback_var = tk.StringVar(value="")
        self.extract_feedback_label = tk.Label(
            selection_inner, textvariable=self.extract_feedback_var,
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
            justify="left", anchor="w",
        )
        self.extract_feedback_label.pack(fill="x")

        self.extract_error_var = tk.StringVar(value="")
        self.extract_error_label = tk.Label(
            selection_inner, textvariable=self.extract_error_var,
            font=("Segoe UI", 9), bg=COLOR_CARD, fg="#c0392b",
            justify="left", anchor="w", wraplength=520,
        )
        self.extract_error_label.pack(fill="x", pady=(4, 0))

        # Action
        action_wrapper = tk.Frame(outer, bg=COLOR_BG)
        action_wrapper.pack(fill="x", pady=(0, 16))
        self.extract_button = ttk.Button(
            action_wrapper, text="EXTRACT PAGES", style="Primary.TButton",
            command=self._on_extract_execute_clicked, state="disabled",
        )
        self.extract_button.pack(fill="x", ipady=4)

        # Status
        status_frame = tk.Frame(outer, bg=COLOR_BG)
        status_frame.pack(fill="x")
        self.extract_status_label = tk.Label(
            status_frame, textvariable=self.extract_status_var,
            font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self.extract_status_label.pack(fill="x", pady=(0, 6))
        self.extract_progress_bar = ttk.Progressbar(
            status_frame, style="App.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", value=0,
        )
        self.extract_progress_bar.pack(fill="x")

        # Live validation feedback as the user types -- never requires
        # the engine (the actual PDF write) to run just to show
        # "Pages to extract: ... / Pages selected: N" or a validation
        # error for bad syntax.
        self.extract_selection_var.trace_add(
            "write", self._on_extract_selection_changed
        )
        self._update_extract_feedback()

    def _on_extract_selection_changed(self, *_args) -> None:
        self._update_extract_feedback()

    def _update_extract_feedback(self) -> None:
        """The single place that keeps the Pages-to-extract preview, the
        validation error message, and the EXTRACT PAGES button's enabled
        state all in sync with the current selection text -- driven
        purely by extract_engine.resolve_pages_to_extract() (no PDF
        write), so bad input is caught instantly and can never crash the
        UI (every PageRangeError is caught right here).
        """
        text = self.extract_selection_var.get()
        self.extract_error_var.set("")

        if self.extract_source is None:
            self.extract_feedback_var.set("Select a PDF file first.")
            self.extract_button.configure(state="disabled")
            return

        page_count = self.extract_source.page_count or 0

        if not text.strip():
            self.extract_feedback_var.set("Enter at least one page or page range.")
            self.extract_button.configure(state="disabled")
            return

        try:
            indices = extract_engine.resolve_pages_to_extract(text, page_count)
        except split_engine.PageRangeError as exc:
            self.extract_error_var.set(str(exc))
            self.extract_feedback_var.set(f"Pages to extract: {text.strip()}")
            self.extract_button.configure(state="disabled")
            return

        self.extract_feedback_var.set(
            f"Pages to extract: {text.strip()}\n"
            f"Pages selected: {len(indices)}"
        )
        self.extract_button.configure(
            state="disabled" if self._any_operation_in_progress() else "normal"
        )

    def _update_extract_controls_state(self) -> None:
        """The single place that restores Extract Pages' own controls to
        their correct enabled state once no operation is running --
        mirrors _update_split_controls_state()'s and
        _update_remove_pages_controls_state()'s role for their own
        tools.
        """
        self.extract_select_btn.configure(state="normal")
        self.extract_clear_btn.configure(state="normal")
        self.extract_selection_entry.configure(state="normal")
        self._update_extract_feedback()

    def _update_extract_source_label(self) -> None:
        if self.extract_source is None:
            self.extract_source_label.configure(text="No file selected.")
        else:
            self.extract_source_label.configure(
                text=(
                    f"{self.extract_source.name}  \u2014  "
                    f"{self.extract_source.page_count_display}, "
                    f"{self.extract_source.size_display}"
                )
            )

    def _build_organize_workspace(self, parent: tk.Widget) -> None:
        """Phase 16: the Organize/Reorder Pages tool's dedicated
        workspace.

        Follows the same overall shape as _build_extract_workspace()
        above (its own state, its own view frame, same cards/buttons/
        status styling), with one addition: a read-only Listbox that
        mirrors the current order as "Page N" rows, driven by Move Up /
        Move Down / Reset, alongside the free-text order entry that
        Split/Remove Pages/Extract all already use for live-validated
        input. The text entry stays the single source of truth (it is
        what actually gets parsed and sent to the engine); the listbox
        is just a friendlier view onto it for the Move Up/Down/Reset
        controls this phase specifically requires, so there is no risk
        of the two ever silently disagreeing -- every edit, from either
        surface, always goes back through organize_order_var and
        _update_organize_feedback() (see there).
        """
        self.organize_view = tk.Frame(parent, bg=COLOR_BG)
        outer = self.organize_view

        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header, text="ORGANIZE PAGES", font=("Segoe UI", 19, "bold"),
            bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Reorder the pages of a PDF and save as a new file -- locally, no upload.",
            font=("Segoe UI", 10), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(2, 0))

        # Source file card
        source_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        source_card.pack(fill="x", pady=(0, 16))
        source_inner = tk.Frame(source_card, bg=COLOR_CARD)
        source_inner.pack(fill="x", padx=18, pady=16)

        self.organize_select_btn = ttk.Button(
            source_inner, text="Select PDF File", style="Primary.TButton",
            command=self._on_organize_select_file_clicked,
        )
        self.organize_select_btn.pack(side="left")

        self.organize_source_label = tk.Label(
            source_inner, text="No file selected.",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        )
        self.organize_source_label.pack(side="left", padx=(16, 0))

        # Order card: text entry (source of truth) + listbox preview +
        # Move Up / Move Down / Reset / Clear
        order_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        order_card.pack(fill="both", expand=True, pady=(0, 16))
        order_inner = tk.Frame(order_card, bg=COLOR_CARD)
        order_inner.pack(fill="both", expand=True, padx=18, pady=14)

        tk.Label(
            order_inner, text="Page Order",
            font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        entry_row = tk.Frame(order_inner, bg=COLOR_CARD)
        entry_row.pack(fill="x")
        self.organize_order_entry = ttk.Entry(
            entry_row, textvariable=self.organize_order_var, width=40,
        )
        self.organize_order_entry.pack(side="left", fill="x", expand=True)
        self.organize_reset_btn = ttk.Button(
            entry_row, text="Reset", style="Secondary.TButton",
            command=self._on_organize_reset_clicked,
        )
        self.organize_reset_btn.pack(side="left", padx=(8, 0))
        self.organize_clear_btn = ttk.Button(
            entry_row, text="Clear", style="Secondary.TButton",
            command=self._on_organize_clear_clicked,
        )
        self.organize_clear_btn.pack(side="left", padx=(8, 0))

        tk.Label(
            order_inner,
            text=(
                "Every page must appear exactly once, e.g. \"3,1,5,2,4\" "
                "for a 5-page document. No ranges -- list each page "
                "individually. Reset restores 1,2,3,...  (no change)."
            ),
            font=("Segoe UI", 9), bg=COLOR_CARD, fg=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(6, 10))

        body_row = tk.Frame(order_inner, bg=COLOR_CARD)
        body_row.pack(fill="both", expand=True)

        listbox_frame = tk.Frame(body_row, bg=COLOR_CARD)
        listbox_frame.pack(side="left", fill="both", expand=True)
        tk.Label(
            listbox_frame, text="Resulting order:",
            font=("Segoe UI", 9, "bold"), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor="w")
        listbox_scroll = ttk.Scrollbar(listbox_frame, orient="vertical")
        self.organize_order_listbox = tk.Listbox(
            listbox_frame, height=8, exportselection=False,
            yscrollcommand=listbox_scroll.set,
        )
        listbox_scroll.configure(command=self.organize_order_listbox.yview)
        self.organize_order_listbox.pack(side="left", fill="both", expand=True, pady=(4, 0))
        listbox_scroll.pack(side="left", fill="y", pady=(4, 0))
        self.organize_order_listbox.bind(
            "<<ListboxSelect>>", self._on_organize_listbox_select
        )

        move_buttons = tk.Frame(body_row, bg=COLOR_CARD)
        move_buttons.pack(side="left", fill="y", padx=(12, 0))
        self.organize_move_up_btn = ttk.Button(
            move_buttons, text="Move Up", style="Secondary.TButton",
            command=self._on_organize_move_up_clicked, state="disabled",
        )
        self.organize_move_up_btn.pack(fill="x", pady=(4, 4))
        self.organize_move_down_btn = ttk.Button(
            move_buttons, text="Move Down", style="Secondary.TButton",
            command=self._on_organize_move_down_clicked, state="disabled",
        )
        self.organize_move_down_btn.pack(fill="x")

        self.organize_feedback_var = tk.StringVar(value="")
        self.organize_feedback_label = tk.Label(
            order_inner, textvariable=self.organize_feedback_var,
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
            justify="left", anchor="w",
        )
        self.organize_feedback_label.pack(fill="x", pady=(10, 0))

        self.organize_error_var = tk.StringVar(value="")
        self.organize_error_label = tk.Label(
            order_inner, textvariable=self.organize_error_var,
            font=("Segoe UI", 9), bg=COLOR_CARD, fg="#c0392b",
            justify="left", anchor="w", wraplength=520,
        )
        self.organize_error_label.pack(fill="x", pady=(4, 0))

        # Action
        action_wrapper = tk.Frame(outer, bg=COLOR_BG)
        action_wrapper.pack(fill="x", pady=(0, 16))
        self.organize_button = ttk.Button(
            action_wrapper, text="ORGANIZE PAGES", style="Primary.TButton",
            command=self._on_organize_execute_clicked, state="disabled",
        )
        self.organize_button.pack(fill="x", ipady=4)

        # Status
        status_frame = tk.Frame(outer, bg=COLOR_BG)
        status_frame.pack(fill="x")
        self.organize_status_label = tk.Label(
            status_frame, textvariable=self.organize_status_var,
            font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self.organize_status_label.pack(fill="x", pady=(0, 6))
        self.organize_progress_bar = ttk.Progressbar(
            status_frame, style="App.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", value=0,
        )
        self.organize_progress_bar.pack(fill="x")

        # Live validation feedback as the user types -- driven purely by
        # organize_engine.parse_page_order() (no PDF write), exactly
        # like Split/Remove Pages/Extract's own entry fields.
        self.organize_order_var.trace_add(
            "write", self._on_organize_order_changed
        )
        self._update_organize_feedback()

    def _on_organize_order_changed(self, *_args) -> None:
        self._update_organize_feedback()

    def _update_organize_feedback(self) -> None:
        """The single place that keeps the resulting-order listbox, the
        validation error message, the Move Up/Move Down button states,
        and the ORGANIZE PAGES button's enabled state all in sync with
        the current order text -- driven purely by
        organize_engine.parse_page_order() (no PDF write), so bad input
        is caught instantly and can never crash the UI (every
        OrganizeOrderError is caught right here).
        """
        text = self.organize_order_var.get()
        self.organize_error_var.set("")

        if self.organize_source is None:
            self._organize_current_order = None
            self._rebuild_organize_listbox(None)
            self.organize_feedback_var.set("Select a PDF file first.")
            self.organize_button.configure(state="disabled")
            self._update_organize_move_buttons_state()
            return

        page_count = self.organize_source.page_count or 0

        if not text.strip():
            self._organize_current_order = None
            self._rebuild_organize_listbox(None)
            self.organize_feedback_var.set("Enter a complete page order.")
            self.organize_button.configure(state="disabled")
            self._update_organize_move_buttons_state()
            return

        try:
            order = organize_engine.parse_page_order(text, page_count)
        except organize_engine.OrganizeOrderError as exc:
            self._organize_current_order = None
            self._rebuild_organize_listbox(None)
            self.organize_error_var.set(str(exc))
            self.organize_feedback_var.set(f"Requested order: {text.strip()}")
            self.organize_button.configure(state="disabled")
            self._update_organize_move_buttons_state()
            return

        self._organize_current_order = order
        self._rebuild_organize_listbox(order)
        is_identity = order == list(range(page_count))
        self.organize_feedback_var.set(
            f"Requested order: {text.strip()}\n"
            f"{page_count} page{'s' if page_count != 1 else ''} placed"
            + (" (no change from original order)" if is_identity else ".")
        )
        self.organize_button.configure(
            state="disabled" if self._any_operation_in_progress() else "normal"
        )
        self._update_organize_move_buttons_state()

    def _rebuild_organize_listbox(self, order: Optional[List[int]]) -> None:
        selection = self.organize_order_listbox.curselection()
        selected_index = selection[0] if selection else None

        self.organize_order_listbox.delete(0, tk.END)
        if not order:
            return
        for position, page_index in enumerate(order, start=1):
            self.organize_order_listbox.insert(
                tk.END, f"{position}.  Page {page_index + 1}"
            )
        if selected_index is not None and selected_index < len(order):
            self.organize_order_listbox.selection_set(selected_index)

    def _on_organize_listbox_select(self, _event=None) -> None:
        self._update_organize_move_buttons_state()

    def _update_organize_move_buttons_state(self) -> None:
        """Move Up/Move Down are only meaningful when the order is
        currently valid (there is a coherent list to move within) AND a
        row is selected AND no operation is running -- otherwise both
        are disabled. Being at the very top/bottom of the list disables
        just that one direction.
        """
        busy = self._any_operation_in_progress()
        selection = self.organize_order_listbox.curselection()
        order = self._organize_current_order

        if busy or order is None or not selection:
            self.organize_move_up_btn.configure(state="disabled")
            self.organize_move_down_btn.configure(state="disabled")
            return

        index = selection[0]
        self.organize_move_up_btn.configure(
            state="disabled" if index <= 0 else "normal"
        )
        self.organize_move_down_btn.configure(
            state="disabled" if index >= len(order) - 1 else "normal"
        )

    def _on_organize_move_up_clicked(self) -> None:
        self._organize_move_selected(-1)

    def _on_organize_move_down_clicked(self) -> None:
        self._organize_move_selected(1)

    def _organize_move_selected(self, delta: int) -> None:
        if self._any_operation_in_progress() or self._organize_current_order is None:
            return
        selection = self.organize_order_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = index + delta
        order = list(self._organize_current_order)
        if new_index < 0 or new_index >= len(order):
            return

        order[index], order[new_index] = order[new_index], order[index]
        # Setting the StringVar fires _on_organize_order_changed(),
        # which re-parses this exact text, repopulates
        # self._organize_current_order, and rebuilds the listbox -- the
        # text entry stays the single source of truth even for a
        # button-driven change (see _build_organize_workspace()'s
        # docstring).
        self.organize_order_var.set(",".join(str(p + 1) for p in order))
        self.organize_order_listbox.selection_clear(0, tk.END)
        self.organize_order_listbox.selection_set(new_index)
        self.organize_order_listbox.see(new_index)
        self._update_organize_move_buttons_state()

    def _on_organize_reset_clicked(self) -> None:
        if self._any_operation_in_progress() or self.organize_source is None:
            return
        page_count = self.organize_source.page_count or 0
        self.organize_order_var.set(organize_engine.identity_order(page_count))

    def _on_organize_clear_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        # Only resets the order text, not the selected source PDF -- the
        # user most likely wants to try a different order against the
        # same file, not re-pick the file too (mirrors Extract Pages'
        # own Clear Selection behavior).
        self.organize_order_var.set("")

    def _update_organize_controls_state(self) -> None:
        """The single place that restores Organize Pages' own controls
        to their correct enabled state once no operation is running --
        mirrors _update_extract_controls_state()'s role for its own
        tool.
        """
        self.organize_select_btn.configure(state="normal")
        self.organize_reset_btn.configure(state="normal")
        self.organize_clear_btn.configure(state="normal")
        self.organize_order_entry.configure(state="normal")
        self.organize_order_listbox.configure(state="normal")
        self._update_organize_feedback()

    def _update_organize_source_label(self) -> None:
        if self.organize_source is None:
            self.organize_source_label.configure(text="No file selected.")
        else:
            self.organize_source_label.configure(
                text=(
                    f"{self.organize_source.name}  \u2014  "
                    f"{self.organize_source.page_count_display}, "
                    f"{self.organize_source.size_display}"
                )
            )

    def _build_rotate_workspace(self, parent: tk.Widget) -> None:
        """Phase 17: the Rotate Pages tool's dedicated workspace.

        Follows the same overall shape as _build_remove_pages_workspace()/
        _build_extract_workspace() (own state, own view frame, same
        card/status/progress styling, a live-validated page-selection
        text entry reusing split_engine's range syntax via
        rotate_engine.resolve_pages_to_rotate()), plus a Rotation card
        with Direction/Angle radio buttons mirroring Split PDF's own
        Radiobutton pattern (self.split_individual_radio etc.) rather
        than inventing a new control style.
        """
        self.rotate_view = tk.Frame(parent, bg=COLOR_BG)
        outer = self.rotate_view

        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header, text="ROTATE PAGES", font=("Segoe UI", 19, "bold"),
            bg=COLOR_BG, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Rotate selected pages of a PDF and save as a new file -- locally, no upload.",
            font=("Segoe UI", 10), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(2, 0))

        # Source file card
        source_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        source_card.pack(fill="x", pady=(0, 16))
        source_inner = tk.Frame(source_card, bg=COLOR_CARD)
        source_inner.pack(fill="x", padx=18, pady=16)

        self.rotate_select_btn = ttk.Button(
            source_inner, text="Select PDF File", style="Primary.TButton",
            command=self._on_rotate_select_file_clicked,
        )
        self.rotate_select_btn.pack(side="left")

        self.rotate_source_label = tk.Label(
            source_inner, text="No file selected.",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        )
        self.rotate_source_label.pack(side="left", padx=(16, 0))

        # Page-selection card
        selection_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        selection_card.pack(fill="x", pady=(0, 16))
        selection_inner = tk.Frame(selection_card, bg=COLOR_CARD)
        selection_inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            selection_inner, text="Pages to Rotate",
            font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        entry_row = tk.Frame(selection_inner, bg=COLOR_CARD)
        entry_row.pack(fill="x")
        self.rotate_selection_entry = ttk.Entry(
            entry_row, textvariable=self.rotate_selection_var, width=30,
        )
        self.rotate_selection_entry.pack(side="left")
        self.rotate_clear_btn = ttk.Button(
            entry_row, text="Clear Selection", style="Secondary.TButton",
            command=self._on_rotate_clear_selection_clicked,
        )
        self.rotate_clear_btn.pack(side="left", padx=(8, 0))

        tk.Label(
            selection_inner,
            text="Example: 1,3,5-7  (1-based, inclusive ranges)",
            font=("Segoe UI", 9), bg=COLOR_CARD, fg=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(6, 10))

        # Live preview/summary -- no engine run needed just to validate
        # basic selection syntax, driven entirely by rotate_engine.
        # resolve_pages_to_rotate() (pure parsing, no PDF write) via the
        # StringVar trace below.
        self.rotate_feedback_var = tk.StringVar(value="")
        self.rotate_feedback_label = tk.Label(
            selection_inner, textvariable=self.rotate_feedback_var,
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
            justify="left", anchor="w",
        )
        self.rotate_feedback_label.pack(fill="x")

        self.rotate_error_var = tk.StringVar(value="")
        self.rotate_error_label = tk.Label(
            selection_inner, textvariable=self.rotate_error_var,
            font=("Segoe UI", 9), bg=COLOR_CARD, fg="#c0392b",
            justify="left", anchor="w", wraplength=520,
        )
        self.rotate_error_label.pack(fill="x", pady=(4, 0))

        # Rotation card: Direction + Angle
        rotation_card = tk.Frame(
            outer, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        rotation_card.pack(fill="x", pady=(0, 16))
        rotation_inner = tk.Frame(rotation_card, bg=COLOR_CARD)
        rotation_inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            rotation_inner, text="Rotation", font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        direction_row = tk.Frame(rotation_inner, bg=COLOR_CARD)
        direction_row.pack(fill="x", pady=(0, 6))
        self.rotate_clockwise_radio = ttk.Radiobutton(
            direction_row, text="Clockwise", value=rotate_engine.CLOCKWISE,
            variable=self.rotate_direction_var,
            style="Compression.TRadiobutton",
            command=self._on_rotate_option_changed,
        )
        self.rotate_clockwise_radio.pack(side="left")
        self.rotate_counterclockwise_radio = ttk.Radiobutton(
            direction_row, text="Counter-clockwise",
            value=rotate_engine.COUNTERCLOCKWISE,
            variable=self.rotate_direction_var,
            style="Compression.TRadiobutton",
            command=self._on_rotate_option_changed,
        )
        self.rotate_counterclockwise_radio.pack(side="left", padx=(16, 0))

        angle_row = tk.Frame(rotation_inner, bg=COLOR_CARD)
        angle_row.pack(fill="x")
        self.rotate_angle_radios = {}
        for angle in rotate_engine.VALID_ANGLES:
            radio = ttk.Radiobutton(
                angle_row, text=f"{angle}\u00b0", value=angle,
                variable=self.rotate_angle_var,
                style="Compression.TRadiobutton",
                command=self._on_rotate_option_changed,
            )
            radio.pack(side="left", padx=(0 if angle == 90 else 16, 0))
            self.rotate_angle_radios[angle] = radio

        # Action
        action_wrapper = tk.Frame(outer, bg=COLOR_BG)
        action_wrapper.pack(fill="x", pady=(0, 16))
        self.rotate_button = ttk.Button(
            action_wrapper, text="ROTATE PAGES", style="Primary.TButton",
            command=self._on_rotate_execute_clicked, state="disabled",
        )
        self.rotate_button.pack(fill="x", ipady=4)

        # Status
        status_frame = tk.Frame(outer, bg=COLOR_BG)
        status_frame.pack(fill="x")
        self.rotate_status_label = tk.Label(
            status_frame, textvariable=self.rotate_status_var,
            font=("Segoe UI", 9), bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self.rotate_status_label.pack(fill="x", pady=(0, 6))
        self.rotate_progress_bar = ttk.Progressbar(
            status_frame, style="App.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", value=0,
        )
        self.rotate_progress_bar.pack(fill="x")

        self.rotate_selection_var.trace_add(
            "write", self._on_rotate_selection_changed
        )
        self._update_rotate_feedback()

    def _on_rotate_selection_changed(self, *_args) -> None:
        self._update_rotate_feedback()

    def _on_rotate_option_changed(self) -> None:
        """Direction/Angle radio buttons don't change the page
        SELECTION, but the effective-rotation summary they're part of
        (see _update_rotate_feedback()) needs to be refreshed whenever
        either changes -- e.g. switching from Clockwise 90 to Counter-
        clockwise 270 doesn't change which pages are selected, but does
        change what "Rotation: ..." should say.
        """
        self._update_rotate_feedback()

    def _update_rotate_feedback(self) -> None:
        """The single place that keeps the Pages-to-rotate preview, the
        effective-rotation summary, the validation error message, and
        the ROTATE PAGES button's enabled state all in sync with the
        current selection text and direction/angle choice -- driven
        purely by rotate_engine.resolve_pages_to_rotate() (no PDF
        write), so bad input is caught instantly and can never crash
        the UI (every PageRangeError is caught right here).
        """
        text = self.rotate_selection_var.get()
        self.rotate_error_var.set("")

        direction = self.rotate_direction_var.get()
        angle = self.rotate_angle_var.get()
        direction_label = (
            "Clockwise" if direction == rotate_engine.CLOCKWISE
            else "Counter-clockwise"
        )
        rotation_summary = f"Rotation: {direction_label} {angle}\u00b0"

        if self.rotate_source is None:
            self.rotate_feedback_var.set("Select a PDF file first.")
            self.rotate_button.configure(state="disabled")
            return

        page_count = self.rotate_source.page_count or 0

        if not text.strip():
            self.rotate_feedback_var.set(
                f"Enter pages to rotate.\n{rotation_summary}"
            )
            self.rotate_button.configure(state="disabled")
            return

        try:
            indices = rotate_engine.resolve_pages_to_rotate(text, page_count)
        except split_engine.PageRangeError as exc:
            self.rotate_error_var.set(str(exc))
            self.rotate_feedback_var.set(
                f"Pages to rotate: {text.strip()}\n{rotation_summary}"
            )
            self.rotate_button.configure(state="disabled")
            return

        self.rotate_feedback_var.set(
            f"Pages to rotate: {text.strip()}\n"
            f"Pages selected: {len(indices)}\n"
            f"{rotation_summary}"
        )
        self.rotate_button.configure(
            state="disabled" if self._any_operation_in_progress() else "normal"
        )

    def _update_rotate_controls_state(self) -> None:
        """The single place that restores Rotate Pages' own controls to
        their correct enabled state once no operation is running --
        mirrors _update_extract_controls_state()'s role for its own
        tool.
        """
        self.rotate_select_btn.configure(state="normal")
        self.rotate_clear_btn.configure(state="normal")
        self.rotate_selection_entry.configure(state="normal")
        self.rotate_clockwise_radio.configure(state="normal")
        self.rotate_counterclockwise_radio.configure(state="normal")
        for radio in self.rotate_angle_radios.values():
            radio.configure(state="normal")
        self._update_rotate_feedback()

    def _update_rotate_source_label(self) -> None:
        if self.rotate_source is None:
            self.rotate_source_label.configure(text="No file selected.")
        else:
            self.rotate_source_label.configure(
                text=(
                    f"{self.rotate_source.name}  \u2014  "
                    f"{self.rotate_source.page_count_display}, "
                    f"{self.rotate_source.size_display}"
                )
            )

    def _select_tool(self, tool_id: str) -> None:
        """Switches the workspace to show the given tool. Unknown tool
        ids are a safe no-op -- selecting a tool that doesn't exist
        (e.g. a stale id) must never crash or blank the workspace.
        """
        tool = tool_registry.get_tool(tool_id)
        if tool is None:
            return

        self.current_tool_id = tool_id

        self.merge_compress_view.pack_forget()
        self.split_view.pack_forget()
        self.remove_pages_view.pack_forget()
        self.extract_view.pack_forget()
        self.organize_view.pack_forget()
        self.rotate_view.pack_forget()
        self.coming_soon_view.pack_forget()

        if tool_id == "merge_compress":
            self.merge_compress_view.pack(fill="both", expand=True, padx=28, pady=24)
        elif tool_id == "split":
            self.split_view.pack(fill="both", expand=True, padx=28, pady=24)
        elif tool_id == "remove_pages":
            self.remove_pages_view.pack(fill="both", expand=True, padx=28, pady=24)
        elif tool_id == "extract_pages":
            self.extract_view.pack(fill="both", expand=True, padx=28, pady=24)
        elif tool_id == "organize_pages":
            self.organize_view.pack(fill="both", expand=True, padx=28, pady=24)
        elif tool_id == "rotate":
            self.rotate_view.pack(fill="both", expand=True, padx=28, pady=24)
        else:
            # Covers every coming_soon tool, and defensively covers a
            # future "available" tool that doesn't have its own
            # dedicated view wired up yet -- falling back to the
            # coming-soon placeholder is safer than showing a blank
            # workspace.
            self._update_coming_soon_view(tool)
            self.coming_soon_view.pack(fill="both", expand=True, padx=28, pady=24)

        self._refresh_tool_nav_highlight()

    def _refresh_tool_nav_highlight(self) -> None:
        """Rebuilds the sidebar so the currently-selected tool is
        visually highlighted. Simpler and less error-prone than trying
        to update colors on a variable number of already-built label
        widgets in place, and this sidebar is small/cheap to rebuild.
        """
        for row in self.tool_nav_buttons.values():
            row.destroy()
        self.tool_nav_buttons.clear()
        for tool in tool_registry.get_all_tools():
            self._build_tool_nav_button(self._nav_frame, tool)

    def _build_header(self, parent: tk.Widget) -> None:
        header = tk.Frame(parent, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 18))

        title = tk.Label(
            header,
            text=APP_NAME.upper(),
            font=("Segoe UI", 19, "bold"),
            bg=COLOR_BG,
            fg=COLOR_TEXT_PRIMARY,
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            header,
            text="Merge and compress PDF files locally -- no upload, no account.",
            font=("Segoe UI", 10),
            bg=COLOR_BG,
            fg=COLOR_TEXT_SECONDARY,
        )
        subtitle.pack(anchor="w", pady=(2, 0))

    def _build_dropzone(self, parent: tk.Widget) -> None:
        # The bordered frame is the intended drag-and-drop target, but
        # OS-level drag-and-drop registration is not implemented yet (see
        # module docstring) -- only the "Select PDF Files" button below
        # is functional in this phase.
        wrapper = tk.Frame(
            parent,
            bg=COLOR_CARD,
            highlightbackground=COLOR_DROPZONE_BORDER,
            highlightthickness=2,
            bd=0,
        )
        wrapper.pack(fill="x", pady=(0, 16))

        inner = tk.Frame(wrapper, bg=COLOR_CARD)
        inner.pack(fill="both", expand=True, padx=20, pady=28)

        drop_label = tk.Label(
            inner,
            text="Drop PDF files here",
            font=("Segoe UI", 12),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_SECONDARY,
        )
        drop_label.pack()

        or_label = tk.Label(
            inner,
            text="OR",
            font=("Segoe UI", 9, "bold"),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_MUTED,
        )
        or_label.pack(pady=(10, 10))

        self.select_files_btn = ttk.Button(
            inner,
            text="Select PDF Files",
            style="Primary.TButton",
            command=self._on_select_files_clicked,
        )
        self.select_files_btn.pack()

    def _build_file_list(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
            bd=0,
        )
        card.pack(fill="both", expand=True, pady=(0, 12))

        # Column header row. Real per-file rows (added below it in
        # _render_file_list) follow this same column layout.
        header_row = tk.Frame(card, bg=COLOR_CARD)
        header_row.pack(fill="x", padx=16, pady=(14, 6))

        tk.Label(
            header_row, text="#", font=("Segoe UI", 9, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_MUTED, width=3, anchor="w",
        ).pack(side="left")
        tk.Label(
            header_row, text="FILENAME", font=("Segoe UI", 9, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_MUTED, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            header_row, text="SIZE", font=("Segoe UI", 9, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_MUTED, width=10, anchor="w",
        ).pack(side="left")
        tk.Label(
            header_row, text="PAGES", font=("Segoe UI", 9, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT_MUTED, width=8, anchor="w",
        ).pack(side="left")
        tk.Label(
            header_row, text="", font=("Segoe UI", 9, "bold"),
            bg=COLOR_CARD, width=16,
        ).pack(side="left")

        separator = tk.Frame(card, bg=COLOR_BORDER, height=1)
        separator.pack(fill="x", padx=16)

        # Rows are rendered into this container by _render_file_list().
        # It shows the empty-state message when self.state.files is empty,
        # and one row per PDFFile otherwise.
        self.file_list_container = tk.Frame(card, bg=COLOR_CARD)
        self.file_list_container.pack(fill="both", expand=True, padx=16, pady=8)

        self.empty_state_label = tk.Label(
            self.file_list_container,
            text="No PDF files added yet.\nUse Select PDF Files or drop files above.",
            font=("Segoe UI", 10),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_MUTED,
            justify="center",
        )
        self.empty_state_label.pack(expand=True, pady=40)

    def _build_summary_bar(self, parent: tk.Widget) -> None:
        summary = tk.Frame(parent, bg=COLOR_BG)
        summary.pack(fill="x", pady=(0, 12))

        for var in (self.files_count_var, self.pages_count_var, self.size_var):
            tk.Label(
                summary,
                textvariable=var,
                font=("Segoe UI", 10, "bold"),
                bg=COLOR_BG,
                fg=COLOR_TEXT_PRIMARY,
            ).pack(side="left", padx=(0, 24))

    def _build_list_actions(self, parent: tk.Widget) -> None:
        actions = tk.Frame(parent, bg=COLOR_BG)
        actions.pack(fill="x", pady=(0, 20))

        # Both start disabled (empty list) and are enabled by
        # _update_button_states() once at least one file is imported.
        # Add More is fully functional (Phase 4); Clear All's click
        # handler is still a stub -- real clearing is Phase 5.
        self.add_more_btn = ttk.Button(
            actions,
            text="Add More",
            style="Secondary.TButton",
            command=self._on_add_more_clicked,
            state="disabled",
        )
        self.add_more_btn.pack(side="left", padx=(0, 10))

        self.clear_all_btn = ttk.Button(
            actions,
            text="Clear All",
            style="Secondary.TButton",
            command=self._on_clear_all_clicked,
            state="disabled",
        )
        self.clear_all_btn.pack(side="left")

    def _build_compression_controls(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
            bd=0,
        )
        card.pack(fill="x", pady=(0, 20))

        inner = tk.Frame(card, bg=COLOR_CARD)
        inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            inner,
            text="Compression Level",
            font=("Segoe UI", 11, "bold"),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 8))

        options_row = tk.Frame(inner, bg=COLOR_CARD)
        options_row.pack(fill="x")

        levels = [
            ("low", "Low"),
            ("recommended", "Recommended"),
            ("maximum", "Maximum"),
        ]
        for value, label in levels:
            ttk.Radiobutton(
                options_row,
                text=label,
                value=value,
                variable=self.compression_var,
                style="Compression.TRadiobutton",
            ).pack(side="left", padx=(0, 24))

    def _build_action_buttons(self, parent: tk.Widget) -> None:
        wrapper = tk.Frame(parent, bg=COLOR_BG)
        wrapper.pack(fill="x", pady=(0, 16))

        # Three independent buttons -- merging and compression are
        # separate operations (Phase 6 correction). Enabled state depends
        # on file count via _update_button_states():
        #   0 files -> all three disabled
        #   1 file  -> only Compress Only enabled (merging one file is
        #              meaningless)
        #   2+      -> all three enabled
        self.merge_only_btn = ttk.Button(
            wrapper,
            text="MERGE ONLY",
            style="ActionSecondary.TButton",
            command=self._on_merge_only_clicked,
            state="disabled",
        )
        self.merge_only_btn.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        # Click handler is still a stub -- real compression wiring is
        # Phase 8. Present and correctly enabled/disabled now per the
        # Phase 6 architecture requirement.
        self.compress_only_btn = ttk.Button(
            wrapper,
            text="COMPRESS ONLY",
            style="ActionSecondary.TButton",
            command=self._on_compress_only_clicked,
            state="disabled",
        )
        self.compress_only_btn.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        # Click handler is still a stub -- real merge_and_compress()
        # wiring is Phase 8.
        self.merge_compress_btn = ttk.Button(
            wrapper,
            text="MERGE + COMPRESS",
            style="Primary.TButton",
            command=self._on_merge_compress_clicked,
            state="disabled",
        )
        self.merge_compress_btn.pack(side="left", fill="x", expand=True, ipady=4)

    def _build_status_area(self, parent: tk.Widget) -> None:
        status_frame = tk.Frame(parent, bg=COLOR_BG)
        status_frame.pack(fill="x")

        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            bg=COLOR_BG,
            fg=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self.status_label.pack(fill="x", pady=(0, 6))

        # Determinate progress bar at 0. Real progress reporting (and the
        # decision between determinate/indeterminate per-operation) is
        # implemented in Phase 9 alongside the worker-thread wiring.
        self.progress_bar = ttk.Progressbar(
            status_frame,
            style="App.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            value=0,
        )
        self.progress_bar.pack(fill="x")

    # ------------------------------------------------------------------
    # File import (Phase 4)
    # ------------------------------------------------------------------

    def _any_operation_in_progress(self) -> bool:
        """True if import, merge, compress, merge+compress, a Split PDF
        operation, or a Remove Pages operation is currently running on a
        background thread. Used as a defense-in-depth guard in every
        click handler below -- the corresponding buttons are already
        disabled while an operation runs (see _set_controls_enabled), so
        this mainly protects against a stray double-click/Enter-key
        re-trigger or a direct programmatic call (as in tests) rather
        than something reachable through normal use.

        This is the single predicate every part of the UI (action
        buttons, per-row file-list controls, Clear All, Split PDF's own
        controls, Remove Pages' own controls -- Phase 14, Extract
        Pages' own controls -- Phase 15, Organize Pages' own controls
        -- Phase 16, and Rotate Pages' own controls -- Phase 17) agrees
        on for "is anything running right now" -- the Phase 9 "one
        consistent operation-state mechanism" requirement, now covering
        all six tool workspaces. Every tool is treated as mutually
        exclusive with every other tool too (not just within itself):
        only one background PDF operation runs at a time app-wide,
        which is the simplest, safest policy and avoids two threads
        touching PyMuPDF concurrently (see the Phase 5 delivery notes
        on multi-threaded PyMuPDF fragility).
        """
        return (
            self._import_in_progress
            or self._merge_in_progress
            or self._compress_in_progress
            or self._mergecompress_in_progress
            or self._split_import_in_progress
            or self.split_in_progress
            or self._remove_pages_import_in_progress
            or self.remove_pages_in_progress
            or self._extract_import_in_progress
            or self.extract_in_progress
            or self._organize_import_in_progress
            or self.organize_in_progress
            or self._rotate_import_in_progress
            or self.rotate_in_progress
        )

    def _assert_main_thread(self) -> None:
        """Defensive invariant: raises if called from any thread other
        than the one that created the Tk root.

        Every method that touches a tkinter widget must run on the main
        thread. Structurally, that's already guaranteed here -- worker
        threads (_import_worker, _merge_worker, _compress_single_worker,
        _compress_batch_worker, _merge_compress_worker) only ever call
        pdf_engine/file_manager functions and put plain dicts on a
        thread-safe queue.Queue; they never call a _apply_*_result or
        _poll_*_queue method directly, and those are the only methods
        that update widgets after a background operation. This assertion
        makes that invariant self-enforcing: if a future change
        accidentally has a worker call one of those methods directly
        instead of going through the queue, this fails loudly and
        immediately in testing rather than silently corrupting widget
        state or crashing unpredictably later.
        """
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(
                "UI state was touched from a non-main thread. All widget "
                "updates must happen on the main thread via the "
                "queue + root.after() pattern."
            )

    def _on_select_files_clicked(self) -> None:
        self._open_picker_and_import()

    def _on_add_more_clicked(self) -> None:
        self._open_picker_and_import()

    def _open_picker_and_import(self) -> None:
        if self._any_operation_in_progress():
            # Buttons are disabled during any operation, so this is only
            # a safety net against, e.g., a stray Enter-key re-trigger.
            return

        paths = file_manager.select_pdf_files(parent=self.root)
        if not paths:
            # Cancelling the dialog is a normal outcome, not an error.
            self.status_var.set("Status: Ready")
            return

        self._start_import(paths)

    def _start_import(self, paths: List[Path]) -> None:
        self._import_in_progress = True
        self._set_controls_enabled(False)

        count = len(paths)
        self.status_var.set(
            f"Status: Validating {count} file{'s' if count != 1 else ''}..."
        )
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)

        existing_paths = {f.path.resolve() for f in self.state.files}

        worker = threading.Thread(
            target=self._import_worker,
            args=(paths, existing_paths),
            daemon=True,
        )
        worker.start()

        self.root.after(80, self._poll_import_queue)

    def _import_worker(
        self,
        paths: List[Path],
        existing_paths: Set[Path],
    ) -> None:
        """Runs on a background thread. Must not touch any tkinter widget
        -- only pdf_engine (pure/file-system work) and the thread-safe
        queue are used here.

        Phase 10: wraps prepare_import() in a top-level try/except as
        defense in depth. prepare_import() already catches every
        per-file failure internally (see its own docstring), but if a
        future change ever introduces a bug that lets something escape
        anyway, this is what stands between that and a permanently
        hung "Validating..." state: without it, an uncaught exception
        here would never reach the queue, _poll_import_queue would spin
        forever waiting for an item that never arrives, and
        _import_in_progress would stay True forever -- freezing the
        import-related buttons for the rest of the session.
        """
        try:
            results = prepare_import(paths, existing_paths)
        except Exception:
            results = {
                "added": [],
                "skipped_duplicates": [],
                "errors": [
                    (Path(str(p)), "An unexpected error occurred while importing this file.")
                    for p in paths
                ],
            }
        self._import_queue.put(results)

    def _poll_import_queue(self) -> None:
        try:
            results = self._import_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_import_queue)
            return

        self._apply_import_results(results)

    def _apply_import_results(self, results: dict) -> None:
        self._assert_main_thread()
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)

        added: List[models.PDFFile] = results["added"]
        skipped_duplicates: List[Path] = results["skipped_duplicates"]
        errors: List[Tuple[Path, str]] = results["errors"]

        for pdf_file in added:
            self.state.files.append(pdf_file)

        self._render_file_list()
        self._update_summary()

        self._import_in_progress = False
        self._update_button_states()  # also restores select/add-more state
        # Bug fix: import shares the single app-wide busy lock with
        # Split PDF and Remove Pages (_set_controls_enabled(False) at the
        # start of _start_import() disables their controls too), so
        # completion must restore both as well -- otherwise they stay
        # disabled until an operation of their own happens to run.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

        self.status_var.set(f"Status: {self._summarize_import(added, skipped_duplicates, errors)}")

        if errors:
            self._show_import_errors(errors)

    @staticmethod
    def _summarize_import(
        added: List[models.PDFFile],
        skipped_duplicates: List[Path],
        errors: List[Tuple[Path, str]],
    ) -> str:
        parts = []
        if added:
            parts.append(f"Added {len(added)} file{'s' if len(added) != 1 else ''}.")
        if skipped_duplicates:
            n = len(skipped_duplicates)
            parts.append(f"Skipped {n} duplicate{'s' if n != 1 else ''}.")
        if errors:
            n = len(errors)
            parts.append(f"{n} file{'s' if n != 1 else ''} could not be added.")
        if not parts:
            parts.append("No files added.")
        return " ".join(parts)

    def _show_import_errors(self, errors: List[Tuple[Path, str]]) -> None:
        # Native messagebox, not a raw traceback -- each line is the
        # human-readable message pdf_engine already produced (e.g.
        # "'x.pdf' is password-protected and cannot be processed.").
        lines = [f"- {reason}" for _path, reason in errors[:10]]
        if len(errors) > 10:
            lines.append(f"...and {len(errors) - 10} more.")
        messagebox.showwarning(
            title="Some files could not be added",
            message="\n".join(lines),
            parent=self.root,
        )

    # ------------------------------------------------------------------
    # File list rendering / summary / button state
    # ------------------------------------------------------------------

    def _render_file_list(self) -> None:
        for child in self.file_list_container.winfo_children():
            child.destroy()

        if not self.state.files:
            self.empty_state_label = tk.Label(
                self.file_list_container,
                text="No PDF files added yet.\nUse Select PDF Files or drop files above.",
                font=("Segoe UI", 10),
                bg=COLOR_CARD,
                fg=COLOR_TEXT_MUTED,
                justify="center",
            )
            self.empty_state_label.pack(expand=True, pady=40)
            return

        # NOTE (known limitation): this container does not scroll. With a
        # large number of files, rows below the card's visible area will
        # be clipped. Preserving merge order is unaffected -- this is a
        # display-only limitation. Scrolling is reasonable to add in
        # Phase 12 (UI polish) rather than here.
        for index, pdf_file in enumerate(self.state.files, start=1):
            self._build_file_row(self.file_list_container, index, pdf_file)

    def _build_file_row(
        self,
        parent: tk.Widget,
        index: int,
        pdf_file: models.PDFFile,
    ) -> None:
        is_first = index == 1
        is_last = index == len(self.state.files)
        # Phase 9: while any long-running operation is in progress, every
        # file-list mutation control is disabled -- not because the
        # running operation would actually read live state.files (it
        # always works from the plain-Path snapshot captured before it
        # started; see _on_merge_only_clicked/_on_compress_only_clicked/
        # _on_merge_compress_clicked), but because letting the user edit
        # the displayed list while something is processing a now-stale
        # copy of it is confusing and is exactly what requirement 3/7
        # ask to prevent. The busy flag is the single source of truth
        # for this and is kept in sync by _set_controls_enabled().
        busy = self._any_operation_in_progress()

        row = tk.Frame(parent, bg=COLOR_CARD)
        row.pack(fill="x", pady=3)

        tk.Label(
            row, text=str(index), width=3, anchor="w",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        tk.Label(
            row, text=pdf_file.name, anchor="w",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
        ).pack(side="left", fill="x", expand=True)

        tk.Label(
            row, text=pdf_file.size_display, width=10, anchor="w",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        tk.Label(
            row, text=pdf_file.page_count_display, width=8, anchor="w",
            font=("Segoe UI", 10), bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        # Each control below is bound to `pdf_file` by identity via the
        # `pf=pdf_file` default-argument trick (required in a loop: without
        # it, every row's lambda would close over the same loop variable
        # and all buttons would end up acting on the last file in the
        # list). This is what guarantees Remove/Move act on the correct
        # file even after the list has been reordered and this row's
        # `index` has changed since the button was created.
        up_btn = ttk.Button(
            row, text="\u25b2", style="Secondary.TButton", width=2,
            command=lambda pf=pdf_file: self._on_move_file_up(pf),
            state="disabled" if (is_first or busy) else "normal",
        )
        up_btn.pack(side="left", padx=(6, 0))

        down_btn = ttk.Button(
            row, text="\u25bc", style="Secondary.TButton", width=2,
            command=lambda pf=pdf_file: self._on_move_file_down(pf),
            state="disabled" if (is_last or busy) else "normal",
        )
        down_btn.pack(side="left", padx=(2, 0))

        remove_btn = ttk.Button(
            row, text="Remove", style="Secondary.TButton", width=8,
            command=lambda pf=pdf_file: self._on_remove_file(pf),
            state="disabled" if busy else "normal",
        )
        remove_btn.pack(side="left", padx=(6, 0))

    def _update_summary(self) -> None:
        self.files_count_var.set(f"Files: {self.state.total_files}")
        self.pages_count_var.set(f"Pages: {self.state.total_pages}")
        self.size_var.set(f"Size: {self.state.total_size_display}")

    def _update_button_states(self) -> None:
        has_files = bool(self.state.files)
        count = len(self.state.files)

        self.select_files_btn.configure(state="normal")
        self.add_more_btn.configure(state="normal" if has_files else "disabled")
        self.clear_all_btn.configure(state="normal" if has_files else "disabled")

        # Per the Phase 6 requirement:
        #   0 files -> all three disabled
        #   1 file  -> only Compress Only enabled (merging a single file
        #              is meaningless, so Merge Only and Merge+Compress
        #              stay disabled)
        #   2+      -> all three enabled
        if count == 0:
            merge_only_state = "disabled"
            compress_only_state = "disabled"
            merge_compress_state = "disabled"
        elif count == 1:
            merge_only_state = "disabled"
            compress_only_state = "normal"
            merge_compress_state = "disabled"
        else:
            merge_only_state = "normal"
            compress_only_state = "normal"
            merge_compress_state = "normal"

        self.merge_only_btn.configure(state=merge_only_state)
        self.compress_only_btn.configure(state=compress_only_state)
        self.merge_compress_btn.configure(state=merge_compress_state)

        # Phase 9: this method is the one every operation's completion
        # handler calls to restore button state, so it's also the one
        # reliable place to refresh per-row file-list controls back to
        # enabled (they were disabled by _set_controls_enabled(False)
        # when the operation started, and their state is only
        # recomputed when a row is rebuilt).
        self._render_file_list()

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Temporarily disables every top-level action button, plus every
        per-row file-list control (Remove/Move Up/Move Down) and Clear
        All, during an import or a merge/compress operation.

        This is the single place that toggles "is the app busy" for the
        whole UI (Phase 9 requirement 3's "one consistent operation-state
        mechanism"): every _on_*_clicked handler calls this at the start
        and end of its operation, and _any_operation_in_progress() (read
        by _build_file_row when it decides a row's button states) is the
        single predicate every part of the UI agrees on for "is anything
        running right now".
        """
        state = "normal" if enabled else "disabled"
        self.select_files_btn.configure(state=state)
        self.add_more_btn.configure(state=state)
        self.clear_all_btn.configure(state=state)
        self.merge_only_btn.configure(state=state)
        self.compress_only_btn.configure(state=state)
        self.merge_compress_btn.configure(state=state)

        # Phase 13: Split PDF's own controls are governed by the SAME
        # single busy flag -- a Merge/Compress operation disables Split's
        # controls too, and vice versa, per _any_operation_in_progress()'s
        # app-wide mutual-exclusion policy.
        self.split_select_btn.configure(state=state)
        self.split_individual_radio.configure(state=state)
        self.split_every_n_radio.configure(state=state)
        self.split_custom_radio.configure(state=state)
        self.split_n_entry.configure(state=state)
        self.split_ranges_entry.configure(state=state)

        # Phase 14: Remove Pages' own controls follow the same busy flag
        # too, for the same reason Split's do -- see the class docstring
        # above _any_operation_in_progress().
        self.remove_pages_select_btn.configure(state=state)
        self.remove_pages_clear_btn.configure(state=state)
        self.remove_pages_selection_entry.configure(state=state)

        # Phase 15: Extract Pages' own controls follow the same busy
        # flag too, for the same reason Split's and Remove Pages' do.
        self.extract_select_btn.configure(state=state)
        self.extract_clear_btn.configure(state=state)
        self.extract_selection_entry.configure(state=state)

        # Phase 16: Organize Pages' own controls follow the same busy
        # flag too, for the same reason every other tool's do. Move Up/
        # Move Down are additionally gated on selection + order validity
        # (see _update_organize_move_buttons_state()) -- setting them
        # blanket-disabled here while busy is still correct, since
        # _update_organize_controls_state() (called below when
        # re-enabling) re-derives their real state afterward rather than
        # leaving them blanket "normal".
        self.organize_select_btn.configure(state=state)
        self.organize_reset_btn.configure(state=state)
        self.organize_clear_btn.configure(state=state)
        self.organize_order_entry.configure(state=state)
        self.organize_move_up_btn.configure(state="disabled")
        self.organize_move_down_btn.configure(state="disabled")

        # Phase 17: Rotate Pages' own controls follow the same busy
        # flag too, for the same reason every other tool's do.
        self.rotate_select_btn.configure(state=state)
        self.rotate_clear_btn.configure(state=state)
        self.rotate_selection_entry.configure(state=state)
        self.rotate_clockwise_radio.configure(state=state)
        self.rotate_counterclockwise_radio.configure(state=state)
        for radio in self.rotate_angle_radios.values():
            radio.configure(state=state)

        if enabled:
            # Restore the file-count-dependent rules for the three
            # action buttons (a flat "enabled" isn't correct for them).
            # _update_button_states() also re-renders the file list so
            # per-row controls pick up the new busy state -- see there.
            self._update_button_states()
            # Re-disable whichever of split_n_entry/split_ranges_entry
            # doesn't match the currently selected mode -- the blanket
            # "normal" above would otherwise leave both entries active
            # regardless of which split mode is actually selected.
            self._update_split_controls_state()
            # Re-evaluate the current page selection -- the blanket
            # "normal" above would otherwise leave REMOVE PAGES enabled
            # even for an empty/invalid selection.
            self._update_remove_pages_controls_state()
            # Same re-evaluation for EXTRACT PAGES -- the blanket
            # "normal" above would otherwise leave it enabled even for
            # an empty/invalid selection or no source selected.
            self._update_extract_controls_state()
            # Same re-evaluation for ORGANIZE PAGES -- also re-derives
            # Move Up/Move Down's real state from the current selection
            # and order validity, rather than leaving them blanket
            # "disabled" forever.
            self._update_organize_controls_state()
            # Same re-evaluation for ROTATE PAGES -- the blanket
            # "normal" above would otherwise leave it enabled even for
            # an empty/invalid selection or no source selected.
            self._update_rotate_controls_state()
        else:
            # Re-render immediately so per-row Remove/Move Up/Move Down
            # become disabled the instant an operation starts (they read
            # _any_operation_in_progress() at row-build time).
            self._render_file_list()
            self.split_button.configure(state="disabled")
            self.remove_pages_button.configure(state="disabled")
            self.extract_button.configure(state="disabled")
            self.organize_button.configure(state="disabled")
            self.rotate_button.configure(state="disabled")

    # ------------------------------------------------------------------
    # File list mutation (Phase 5: remove / reorder / clear)
    # ------------------------------------------------------------------

    def _find_index(self, pdf_file: models.PDFFile) -> int:
        """Locate a file's current position by object identity (`is`),
        not value equality and not a row index captured at render time.
        Row indices shift on every reorder/remove, so any control that
        instead remembered "row 3" at the moment it was built would
        silently act on the wrong file after the list changed under it --
        this is what the Phase 5 "must not accidentally remove another
        file after reordering" requirement is guarding against.

        Raises ValueError if the file is no longer in the list (e.g. a
        stale button click racing a concurrent removal); callers treat
        that as a no-op rather than crashing.
        """
        for i, f in enumerate(self.state.files):
            if f is pdf_file:
                return i
        raise ValueError("File is no longer in the list.")

    def _on_remove_file(self, pdf_file: models.PDFFile) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; the Remove button should already be disabled

        try:
            index = self._find_index(pdf_file)
        except ValueError:
            return  # already removed (e.g. double-click race) -- no-op

        removed_name = self.state.files[index].name
        del self.state.files[index]

        self._render_file_list()
        self._update_summary()
        self._update_button_states()
        self.status_var.set(f"Status: Removed '{removed_name}'.")

    def _on_move_file_up(self, pdf_file: models.PDFFile) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; the Up button should already be disabled

        try:
            index = self._find_index(pdf_file)
        except ValueError:
            return

        if index == 0:
            return  # already first -- button should be disabled anyway

        files = self.state.files
        files[index - 1], files[index] = files[index], files[index - 1]

        self._render_file_list()
        self.status_var.set(f"Status: Moved '{pdf_file.name}' up.")

    def _on_move_file_down(self, pdf_file: models.PDFFile) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; the Down button should already be disabled

        try:
            index = self._find_index(pdf_file)
        except ValueError:
            return

        if index == len(self.state.files) - 1:
            return  # already last -- button should be disabled anyway

        files = self.state.files
        files[index + 1], files[index] = files[index], files[index + 1]

        self._render_file_list()
        self.status_var.set(f"Status: Moved '{pdf_file.name}' down.")

    def _on_clear_all_clicked(self) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; Clear All should already be disabled

        if not self.state.files:
            return

        count = len(self.state.files)
        self.state.files.clear()

        self._render_file_list()
        self._update_summary()
        self._update_button_states()
        self.status_var.set(
            f"Status: Cleared all files ({count} removed)."
        )

    # ------------------------------------------------------------------
    # Merge Only (Phase 6)
    # ------------------------------------------------------------------

    def _on_merge_only_clicked(self) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; the button should already be disabled

        # Defensive guard mirroring the button's enabled state (0/1 files
        # -> disabled) -- this can't normally be reached through the UI,
        # but _on_merge_only_clicked() must never merge a meaningless
        # single-file or empty selection if it is ever called directly.
        if len(self.state.files) < 2:
            return

        # Snapshot the exact displayed order as plain Path objects before
        # opening the dialog. The user can't mutate the list while a
        # native modal dialog is open, but capturing it now (rather than
        # re-reading self.state.files after the dialog returns) keeps the
        # two steps -- "what to merge" and "where to save it" -- cleanly
        # separated and makes the intent explicit in the code.
        input_paths = [f.path for f in self.state.files]

        output_path = file_manager.save_pdf_file(parent=self.root)
        if output_path is None:
            # Cancelling Save As is a normal outcome, not an error.
            self.status_var.set("Status: Ready")
            return

        self._start_merge(input_paths, output_path)

    def _start_merge(self, input_paths: List[Path], output_path: Path) -> None:
        self._merge_in_progress = True
        self._set_controls_enabled(False)

        self.status_var.set(
            f"Status: Merging {len(input_paths)} files..."
        )
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)

        worker = threading.Thread(
            target=self._merge_worker,
            args=(input_paths, output_path),
            daemon=True,
        )
        worker.start()

        self.root.after(80, self._poll_merge_queue)

    def _merge_worker(self, input_paths: List[Path], output_path: Path) -> None:
        """Runs on a background thread. Calls pdf_engine.merge_pdfs()
        directly -- never merge_and_compress() -- so a Merge Only run
        performs no compression whatsoever. Must not touch any tkinter
        widget; only the thread-safe queue is used to report back.

        progress_callback puts real per-file progress on the same queue
        (Phase 9: "report meaningful progress if practical" for Merge),
        using the same typed-item pattern ({"type": "progress"/"done"})
        already established for Compress Only and Merge + Compress, so
        _poll_merge_queue can use the identical drain-loop shape as the
        other three pollers.
        """
        def report(message: str) -> None:
            self._merge_queue.put({"type": "progress", "message": message})

        try:
            pdf_engine.merge_pdfs(input_paths, output_path, progress_callback=report)
            self._merge_queue.put({
                "type": "done",
                "success": True,
                "output_path": output_path,
                "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._merge_queue.put({
                "type": "done",
                "success": False,
                "output_path": output_path,
                "error": str(exc),
            })
        except Exception:
            # Defense in depth: never let an unexpected exception escape
            # the worker thread (it would otherwise be silently lost) or
            # surface a raw traceback to the user.
            self._merge_queue.put({
                "type": "done",
                "success": False,
                "output_path": output_path,
                "error": "An unexpected error occurred while merging.",
            })

    def _poll_merge_queue(self) -> None:
        try:
            while True:
                item = self._merge_queue.get_nowait()
                if item["type"] == "progress":
                    self.status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_merge_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_merge_queue)

    def _apply_merge_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)

        self._merge_in_progress = False
        self._update_button_states()  # also restores select/add/clear state
        # Bug fix: see the matching comment in _apply_import_results().
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

        if result["success"]:
            output_path: Path = result["output_path"]
            self.status_var.set(
                f"Status: Merged {len(self.state.files)} files into "
                f"'{output_path.name}'."
            )
        else:
            self.status_var.set("Status: Merge failed.")
            messagebox.showerror(
                title="Merge Failed",
                message=result["error"],
                parent=self.root,
            )

    # ------------------------------------------------------------------
    # Compress Only (Phase 8)
    # ------------------------------------------------------------------
    #
    # Calls pdf_engine.compress_pdf() directly -- never merge_and_compress()
    # -- so Compress Only never merges files together, per the corrected
    # Phase 6 requirement. Two distinct output flows, chosen by file count:
    #   - Exactly 1 file:  native Save As dialog (default name
    #     "<stem>_compressed.pdf"), matching Merge Only's pattern of
    #     relying on the OS's own overwrite confirmation.
    #   - 2+ files: a single "select output folder" dialog, then each file
    #     is compressed independently to its own collision-safe
    #     "<stem>_compressed.pdf" (or " (1)", " (2)", ...) inside that
    #     folder, via file_manager.generate_compressed_output_path(). No
    #     per-file Save As dialogs, and no file is ever merged with
    #     another -- N inputs always produce N separate output files.

    def _on_compress_only_clicked(self) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; the button should already be disabled

        if not self.state.files:
            return  # defensive guard; button is disabled at 0 files

        # Snapshot the current file list and compression level before any
        # dialog opens, for the same reason Merge Only does: keeps "what
        # to compress" cleanly separated from "where to put it".
        pdf_files = list(self.state.files)
        level = self.compression_var.get()

        if len(pdf_files) == 1:
            single = pdf_files[0]
            default_name = file_manager.ensure_pdf_extension(
                f"{Path(single.path).stem}_compressed"
            )
            output_path = file_manager.save_pdf_file(
                parent=self.root, default_name=default_name
            )
            if output_path is None:
                self.status_var.set("Status: Ready")
                return
            self._start_compress_single(single.path, output_path, level)
        else:
            output_dir = file_manager.select_output_folder(parent=self.root)
            if output_dir is None:
                self.status_var.set("Status: Ready")
                return
            self._start_compress_batch(
                [f.path for f in pdf_files], output_dir, level
            )

    def _start_compress_single(
        self, input_path: Path, output_path: Path, level: str
    ) -> None:
        self._compress_in_progress = True
        self._set_controls_enabled(False)

        self.status_var.set("Status: Compressing...")
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)

        worker = threading.Thread(
            target=self._compress_single_worker,
            args=(input_path, output_path, level),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_compress_queue)

    def _compress_single_worker(
        self, input_path: Path, output_path: Path, level: str
    ) -> None:
        """Runs on a background thread. Calls pdf_engine.compress_pdf()
        directly -- never merge_and_compress() or merge_pdfs(). Must not
        touch any tkinter widget; only the thread-safe queue is used.
        """
        try:
            before_size = Path(input_path).stat().st_size
            pdf_engine.compress_pdf(input_path, output_path, level=level)
            after_size = Path(output_path).stat().st_size
            self._compress_queue.put({
                "type": "done_single",
                "success": True,
                "output_path": output_path,
                "before_size": before_size,
                "after_size": after_size,
                "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._compress_queue.put({
                "type": "done_single",
                "success": False,
                "output_path": output_path,
                "error": str(exc),
            })
        except Exception:
            self._compress_queue.put({
                "type": "done_single",
                "success": False,
                "output_path": output_path,
                "error": "An unexpected error occurred while compressing.",
            })

    def _start_compress_batch(
        self, input_paths: List[Path], output_dir: Path, level: str
    ) -> None:
        self._compress_in_progress = True
        self._set_controls_enabled(False)

        self.status_var.set(
            f"Status: Compressing {len(input_paths)} files..."
        )
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)

        worker = threading.Thread(
            target=self._compress_batch_worker,
            args=(input_paths, output_dir, level),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_compress_queue)

    def _compress_batch_worker(
        self, input_paths: List[Path], output_dir: Path, level: str
    ) -> None:
        """Runs on a background thread. Compresses each file to its own
        output via pdf_engine.compress_pdf() -- never merging any of them
        together. One failing file does not abort the rest of the batch,
        mirroring the same resilience pattern used for Phase 4 import.

        Output paths are generated one at a time, immediately before each
        file is compressed (not all upfront) -- see
        file_manager.generate_compressed_output_path()'s module-level
        note on why this ordering matters for correct collision detection
        when two different source files share a filename.
        """
        total = len(input_paths)
        succeeded: List[dict] = []
        failed: List[Tuple[str, str]] = []

        for index, input_path in enumerate(input_paths, start=1):
            input_path = Path(input_path)
            self._compress_queue.put({
                "type": "progress",
                "message": f"Compressing file {index} of {total}...",
            })
            try:
                output_path = file_manager.generate_compressed_output_path(
                    input_path, output_dir
                )
                before_size = input_path.stat().st_size
                pdf_engine.compress_pdf(input_path, output_path, level=level)
                after_size = output_path.stat().st_size
                succeeded.append({
                    "name": input_path.name,
                    "output_path": output_path,
                    "before_size": before_size,
                    "after_size": after_size,
                    "reduced": after_size < before_size,
                })
            except pdf_engine.PDFEngineError as exc:
                failed.append((input_path.name, str(exc)))
            except Exception:
                failed.append((
                    input_path.name,
                    "An unexpected error occurred while compressing this file.",
                ))

        self._compress_queue.put({
            "type": "done_batch",
            "succeeded": succeeded,
            "failed": failed,
            "output_dir": output_dir,
        })

    def _poll_compress_queue(self) -> None:
        try:
            while True:
                item = self._compress_queue.get_nowait()
                if item["type"] == "progress":
                    self.status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done_single":
                    self._apply_compress_single_result(item)
                    return
                elif item["type"] == "done_batch":
                    self._apply_compress_batch_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_compress_queue)

    def _apply_compress_single_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)

        self._compress_in_progress = False
        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results().
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

        if result["success"]:
            output_path: Path = result["output_path"]
            before = models.format_file_size(result["before_size"])
            after = models.format_file_size(result["after_size"])
            if result["after_size"] < result["before_size"]:
                self.status_var.set(
                    f"Status: Compressed '{output_path.name}' "
                    f"({before} -> {after})."
                )
            else:
                # Per the spec: never claim a guaranteed reduction. If
                # compression didn't shrink the file, say so plainly
                # rather than hiding it.
                self.status_var.set(
                    f"Status: Compressed '{output_path.name}', but the "
                    f"file did not get smaller ({before} -> {after})."
                )
        else:
            self.status_var.set("Status: Compression failed.")
            messagebox.showerror(
                title="Compression Failed",
                message=result["error"],
                parent=self.root,
            )

    def _apply_compress_batch_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)

        self._compress_in_progress = False
        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results().
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

        succeeded: List[dict] = result["succeeded"]
        failed: List[Tuple[str, str]] = result["failed"]

        parts = []
        if succeeded:
            parts.append(
                f"Compressed {len(succeeded)} file"
                f"{'s' if len(succeeded) != 1 else ''}."
            )
            no_reduction = sum(1 for s in succeeded if not s["reduced"])
            if no_reduction:
                parts.append(
                    f"{no_reduction} did not get smaller."
                )
        if failed:
            parts.append(
                f"{len(failed)} file{'s' if len(failed) != 1 else ''} "
                f"could not be compressed."
            )
        if not parts:
            parts.append("No files compressed.")

        self.status_var.set("Status: " + " ".join(parts))

        if failed:
            lines = [f"- {name}: {reason}" for name, reason in failed[:10]]
            if len(failed) > 10:
                lines.append(f"...and {len(failed) - 10} more.")
            messagebox.showwarning(
                title="Some files could not be compressed",
                message="\n".join(lines),
                parent=self.root,
            )

    # ------------------------------------------------------------------
    # Merge + Compress (Phase 8)
    # ------------------------------------------------------------------
    #
    # Calls pdf_engine.merge_and_compress() directly -- the one operation
    # for which merging and compression are deliberately chained, per the
    # Phase 6 correction's explicit "convenience operation" carve-out.
    # Always produces exactly one output file, since merging always
    # collapses the input files into one document first.

    def _on_merge_compress_clicked(self) -> None:
        if self._any_operation_in_progress():
            return  # a safety net; the button should already be disabled

        if len(self.state.files) < 2:
            return  # defensive guard mirroring the button's enabled state

        input_paths = [f.path for f in self.state.files]
        level = self.compression_var.get()

        output_path = file_manager.save_pdf_file(
            parent=self.root, default_name="merged_compressed.pdf"
        )
        if output_path is None:
            self.status_var.set("Status: Ready")
            return

        self._start_merge_compress(input_paths, output_path, level)

    def _start_merge_compress(
        self, input_paths: List[Path], output_path: Path, level: str
    ) -> None:
        self._mergecompress_in_progress = True
        self._set_controls_enabled(False)

        self.status_var.set(
            f"Status: Merging {len(input_paths)} files..."
        )
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(12)

        worker = threading.Thread(
            target=self._merge_compress_worker,
            args=(input_paths, output_path, level),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_merge_compress_queue)

    def _merge_compress_worker(
        self, input_paths: List[Path], output_path: Path, level: str
    ) -> None:
        """Runs on a background thread. Calls
        pdf_engine.merge_and_compress() directly, which internally merges
        to a temp file and then compresses that temp file to
        output_path -- both steps are pdf_engine's existing, already-
        tested implementation from Phase 2/7; nothing about the
        algorithm changes here, only its UI wiring.

        The progress_callback runs on this same background thread (it's
        called synchronously from inside merge_and_compress); it only
        puts a message on the thread-safe queue, never touching a
        tkinter widget directly.
        """
        def report(message: str) -> None:
            self._mergecompress_queue.put({
                "type": "progress",
                "message": message,
            })

        try:
            result = pdf_engine.merge_and_compress(
                input_paths, output_path, level=level,
                progress_callback=report,
            )
            self._mergecompress_queue.put({
                "type": "done",
                "success": True,
                "result": result,
                "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._mergecompress_queue.put({
                "type": "done",
                "success": False,
                "result": None,
                "error": str(exc),
            })
        except Exception:
            self._mergecompress_queue.put({
                "type": "done",
                "success": False,
                "result": None,
                "error": "An unexpected error occurred while merging and compressing.",
            })

    def _poll_merge_compress_queue(self) -> None:
        try:
            while True:
                item = self._mergecompress_queue.get_nowait()
                if item["type"] == "progress":
                    self.status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_merge_compress_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_merge_compress_queue)

    def _apply_merge_compress_result(self, item: dict) -> None:
        self._assert_main_thread()
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)

        self._mergecompress_in_progress = False
        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results().
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

        if item["success"]:
            result: dict = item["result"]
            output_path: Path = result["output_path"]
            before = models.format_file_size(result["merged_size"])
            after = models.format_file_size(result["final_size"])
            if result["size_reduced"]:
                self.status_var.set(
                    f"Status: Merged and compressed "
                    f"{len(self.state.files)} files into "
                    f"'{output_path.name}' ({before} -> {after})."
                )
            else:
                self.status_var.set(
                    f"Status: Merged and compressed into "
                    f"'{output_path.name}', but the file did not get "
                    f"smaller ({before} -> {after})."
                )
        else:
            self.status_var.set("Status: Merge + Compress failed.")
            messagebox.showerror(
                title="Merge + Compress Failed",
                message=item["error"],
                parent=self.root,
            )

    # ------------------------------------------------------------------
    # Split PDF: source file selection (Phase 13)
    # ------------------------------------------------------------------
    #
    # Deliberately its own small import flow rather than reusing the
    # Merge/Compress multi-file import machinery (prepare_import(),
    # self.state) -- Split needs exactly one current source file, not a
    # list, and coupling it to Merge-specific state would tie two
    # genuinely different concerns together for no benefit. It reuses
    # the same underlying pieces that matter (pdf_engine.get_pdf_info()
    # for validation/metadata, the same background-thread+queue pattern,
    # file_manager for the native dialog) without duplicating any of
    # their logic.

    def _on_split_select_file_clicked(self) -> None:
        if self._any_operation_in_progress():
            return

        path = file_manager.select_single_pdf_file(parent=self.root)
        if path is None:
            self.split_status_var.set("Status: Ready")
            return

        self._start_split_import(path)

    def _start_split_import(self, path: Path) -> None:
        self._split_import_in_progress = True
        self._set_controls_enabled(False)

        self.split_status_var.set("Status: Validating file...")
        self.split_progress_bar.configure(mode="indeterminate")
        self.split_progress_bar.start(12)

        worker = threading.Thread(
            target=self._split_import_worker, args=(path,), daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_split_import_queue)

    def _split_import_worker(self, path: Path) -> None:
        """Runs on a background thread. Only calls pdf_engine (pure
        file-system work) and puts a plain dict on the thread-safe
        queue -- never touches a tkinter widget directly.
        """
        try:
            info = pdf_engine.get_pdf_info(path)
            self._split_import_queue.put({
                "success": True, "info": info, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._split_import_queue.put({
                "success": False, "info": None, "error": str(exc),
            })
        except Exception:
            self._split_import_queue.put({
                "success": False, "info": None,
                "error": "An unexpected error occurred while reading this file.",
            })

    def _poll_split_import_queue(self) -> None:
        try:
            result = self._split_import_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_split_import_queue)
            return
        self._apply_split_import_result(result)

    def _apply_split_import_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.split_progress_bar.stop()
        self.split_progress_bar.configure(mode="determinate", value=0)
        self._split_import_in_progress = False

        if result["success"]:
            self.split_source = models.PDFFile(**result["info"])
            self._update_split_source_label()
            self.split_status_var.set(f"Status: Selected '{self.split_source.name}'.")
        else:
            self.split_source = None
            self._update_split_source_label()
            self.split_status_var.set("Status: Could not read that file.")
            messagebox.showerror(
                title="Invalid PDF", message=result["error"], parent=self.root,
            )

        self._update_button_states()
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    # ------------------------------------------------------------------
    # Split PDF: the actual split operation (Phase 13)
    # ------------------------------------------------------------------

    def _parse_split_n(self) -> int:
        text = self.split_n_var.get().strip()
        try:
            n = int(text)
        except ValueError:
            raise split_engine.PageRangeError(
                f"'{text}' is not a valid number of pages. Enter a "
                f"whole number of at least 1."
            ) from None
        if n < 1:
            raise split_engine.PageRangeError(
                "Enter a number of pages of at least 1."
            )
        return n

    def _on_split_execute_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        if self.split_source is None:
            return  # defensive; button should be disabled without a source

        mode = self.split_mode_var.get()
        page_count = self.split_source.page_count or 0

        try:
            if mode == "individual":
                groups = split_engine.compute_individual_page_groups(page_count)
                include_range_label = False
            elif mode == "every_n":
                n = self._parse_split_n()
                groups = split_engine.compute_every_n_page_groups(page_count, n)
                include_range_label = False
            else:  # "custom"
                groups = split_engine.parse_page_ranges(
                    self.split_ranges_var.get(), page_count
                )
                include_range_label = True
        except split_engine.PageRangeError as exc:
            self.split_status_var.set(f"Status: {exc}")
            messagebox.showerror(
                title="Invalid Split Settings", message=str(exc), parent=self.root,
            )
            return

        output_dir = file_manager.select_output_folder(parent=self.root)
        if output_dir is None:
            self.split_status_var.set("Status: Ready")
            return

        self._start_split(self.split_source.path, groups, include_range_label, output_dir)

    def _start_split(
        self,
        source_path: Path,
        groups: List[List[int]],
        include_range_label: bool,
        output_dir: Path,
    ) -> None:
        self.split_in_progress = True
        self._set_controls_enabled(False)

        self.split_status_var.set("Status: Preparing split...")
        self.split_progress_bar.configure(mode="indeterminate")
        self.split_progress_bar.start(12)

        worker = threading.Thread(
            target=self._split_worker,
            args=(source_path, groups, include_range_label, output_dir),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_split_queue)

    def _split_worker(
        self,
        source_path: Path,
        groups: List[List[int]],
        include_range_label: bool,
        output_dir: Path,
    ) -> None:
        """Runs on a background thread. Calls split_engine.split_pdf()
        directly. Must not touch any tkinter widget; only the
        thread-safe queue is used to report back.
        """
        def report(message: str) -> None:
            self._split_queue.put({"type": "progress", "message": message})

        try:
            result = split_engine.split_pdf(
                source_path, groups, output_dir,
                include_range_label=include_range_label,
                progress_callback=report,
            )
            self._split_queue.put({
                "type": "done", "success": True, "result": result, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._split_queue.put({
                "type": "done", "success": False, "result": None, "error": str(exc),
            })
        except Exception:
            self._split_queue.put({
                "type": "done", "success": False, "result": None,
                "error": "An unexpected error occurred while splitting.",
            })

    def _poll_split_queue(self) -> None:
        try:
            while True:
                item = self._split_queue.get_nowait()
                if item["type"] == "progress":
                    self.split_status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_split_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_split_queue)

    def _apply_split_result(self, item: dict) -> None:
        self._assert_main_thread()
        self.split_progress_bar.stop()
        self.split_progress_bar.configure(mode="determinate", value=0)

        self.split_in_progress = False

        if item["success"]:
            result = item["result"]
            n = result["total_parts"]
            self.split_status_var.set(
                f"Status: Split completed successfully. Created {n} "
                f"file{'s' if n != 1 else ''}."
            )
            # Bug fix: a completed split has fully consumed its source.
            # Clear it (and its label) so the workspace returns to its
            # non-file-selected initial state -- otherwise the same
            # source stayed attached to the workspace after a
            # successful split, letting a user accidentally re-split
            # (or just be confused by) a file they already finished
            # with. Must happen before _update_split_controls_state()
            # below so the Split button correctly goes back to
            # "disabled" (it depends on self.split_source).
            self.split_source = None
            self._update_split_source_label()
        else:
            self.split_status_var.set("Status: Split failed.")
            messagebox.showerror(
                title="Split Failed", message=item["error"], parent=self.root,
            )

        self._update_button_states()
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    # ------------------------------------------------------------------
    # Remove Pages: source file import (Phase 14)
    # ------------------------------------------------------------------

    def _on_remove_pages_select_file_clicked(self) -> None:
        if self._any_operation_in_progress():
            return

        path = file_manager.select_single_pdf_file(parent=self.root)
        if path is None:
            self.remove_pages_status_var.set("Status: Ready")
            return

        self._start_remove_pages_import(path)

    def _start_remove_pages_import(self, path: Path) -> None:
        self._remove_pages_import_in_progress = True
        self._set_controls_enabled(False)

        self.remove_pages_status_var.set("Status: Validating file...")
        self.remove_pages_progress_bar.configure(mode="indeterminate")
        self.remove_pages_progress_bar.start(12)

        worker = threading.Thread(
            target=self._remove_pages_import_worker, args=(path,), daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_remove_pages_import_queue)

    def _remove_pages_import_worker(self, path: Path) -> None:
        """Runs on a background thread. Only calls pdf_engine (pure
        file-system work) and puts a plain dict on the thread-safe
        queue -- never touches a tkinter widget directly.
        """
        try:
            info = pdf_engine.get_pdf_info(path)
            self._remove_pages_import_queue.put({
                "success": True, "info": info, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._remove_pages_import_queue.put({
                "success": False, "info": None, "error": str(exc),
            })
        except Exception:
            self._remove_pages_import_queue.put({
                "success": False, "info": None,
                "error": "An unexpected error occurred while reading this file.",
            })

    def _poll_remove_pages_import_queue(self) -> None:
        try:
            result = self._remove_pages_import_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_remove_pages_import_queue)
            return
        self._apply_remove_pages_import_result(result)

    def _apply_remove_pages_import_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.remove_pages_progress_bar.stop()
        self.remove_pages_progress_bar.configure(mode="determinate", value=0)
        self._remove_pages_import_in_progress = False

        if result["success"]:
            self.remove_pages_source = models.PDFFile(**result["info"])
            self._update_remove_pages_source_label()
            self.remove_pages_status_var.set(
                f"Status: Selected '{self.remove_pages_source.name}'."
            )
        else:
            self.remove_pages_source = None
            self._update_remove_pages_source_label()
            self.remove_pages_status_var.set("Status: Could not read that file.")
            messagebox.showerror(
                title="Invalid PDF", message=result["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Remove Pages' import shares the same app-wide busy lock, so its
        # completion must restore Split's controls too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    def _on_remove_pages_clear_selection_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        # Only resets the page-selection text, not the selected source
        # PDF -- the user most likely wants to try a different selection
        # against the same file, not re-pick the file too. Setting the
        # StringVar fires the trace in _build_remove_pages_workspace(),
        # which refreshes the preview/error/button state automatically.
        self.remove_pages_selection_var.set("")

    # ------------------------------------------------------------------
    # Remove Pages: the actual remove-pages operation (Phase 14)
    # ------------------------------------------------------------------

    def _on_remove_pages_execute_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        if self.remove_pages_source is None:
            return  # defensive; button should be disabled without a source

        page_count = self.remove_pages_source.page_count or 0
        text = self.remove_pages_selection_var.get()

        try:
            indices = remove_pages_engine.resolve_pages_to_remove(text, page_count)
        except split_engine.PageRangeError as exc:
            self.remove_pages_error_var.set(str(exc))
            self.remove_pages_status_var.set(f"Status: {exc}")
            messagebox.showerror(
                title="Invalid Page Selection", message=str(exc), parent=self.root,
            )
            return

        # Sensible, project-consistent default filename for the native
        # Save As dialog -- reusing file_manager's existing sanitization/
        # extension helpers rather than inventing a second naming
        # mechanism (Phase 14 requirement 8).
        default_name = file_manager.ensure_pdf_extension(
            file_manager.sanitize_windows_filename(
                f"{self.remove_pages_source.path.stem}_without_pages"
            )
        )
        output_path = file_manager.save_pdf_file(
            parent=self.root,
            default_name=default_name,
            title="Save PDF Without Removed Pages As",
        )
        if output_path is None:
            self.remove_pages_status_var.set("Status: Ready")
            return

        self._start_remove_pages(self.remove_pages_source.path, indices, output_path)

    def _start_remove_pages(
        self, source_path: Path, pages_to_remove: List[int], output_path: Path,
    ) -> None:
        self.remove_pages_in_progress = True
        self._set_controls_enabled(False)

        self.remove_pages_status_var.set("Status: Removing pages...")
        self.remove_pages_progress_bar.configure(mode="indeterminate")
        self.remove_pages_progress_bar.start(12)

        worker = threading.Thread(
            target=self._remove_pages_worker,
            args=(source_path, pages_to_remove, output_path),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_remove_pages_queue)

    def _remove_pages_worker(
        self, source_path: Path, pages_to_remove: List[int], output_path: Path,
    ) -> None:
        """Runs on a background thread. Calls
        remove_pages_engine.remove_pages_from_pdf() directly. Must not
        touch any tkinter widget; only the thread-safe queue is used to
        report back.
        """
        def report(message: str) -> None:
            self._remove_pages_queue.put({"type": "progress", "message": message})

        try:
            result_path = remove_pages_engine.remove_pages_from_pdf(
                source_path, output_path, pages_to_remove,
                progress_callback=report,
            )
            self._remove_pages_queue.put({
                "type": "done", "success": True,
                "output_path": result_path, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._remove_pages_queue.put({
                "type": "done", "success": False,
                "output_path": None, "error": str(exc),
            })
        except Exception:
            self._remove_pages_queue.put({
                "type": "done", "success": False, "output_path": None,
                "error": "An unexpected error occurred while removing pages.",
            })

    def _poll_remove_pages_queue(self) -> None:
        try:
            while True:
                item = self._remove_pages_queue.get_nowait()
                if item["type"] == "progress":
                    self.remove_pages_status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_remove_pages_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_remove_pages_queue)

    def _apply_remove_pages_result(self, item: dict) -> None:
        self._assert_main_thread()
        self.remove_pages_progress_bar.stop()
        self.remove_pages_progress_bar.configure(mode="determinate", value=0)

        self.remove_pages_in_progress = False

        if item["success"]:
            output_path = item["output_path"]
            self.remove_pages_status_var.set(
                f"Status: Pages removed successfully. Saved to "
                f"'{output_path.name}'."
            )
            # A completed removal has fully consumed its source, mirroring
            # Split PDF's own post-completion behavior (see
            # _apply_split_result): clear it (and the selection text) so
            # the workspace returns to its non-file-selected initial
            # state, rather than leaving a stale source attached that
            # invites an accidental repeat operation on a file that's
            # already been processed. Must happen before
            # _update_remove_pages_controls_state() below so REMOVE
            # PAGES correctly goes back to "disabled" (it depends on
            # self.remove_pages_source).
            self.remove_pages_source = None
            self._update_remove_pages_source_label()
            self.remove_pages_selection_var.set("")
        else:
            self.remove_pages_status_var.set("Status: Remove Pages failed.")
            messagebox.showerror(
                title="Remove Pages Failed", message=item["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Remove Pages shares the same app-wide busy lock, so its
        # completion must restore Split's controls too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    # ------------------------------------------------------------------
    # Extract Pages: source file import (Phase 15)
    # ------------------------------------------------------------------

    def _on_extract_select_file_clicked(self) -> None:
        if self._any_operation_in_progress():
            return

        path = file_manager.select_single_pdf_file(parent=self.root)
        if path is None:
            self.extract_status_var.set("Status: Ready")
            return

        self._start_extract_import(path)

    def _start_extract_import(self, path: Path) -> None:
        self._extract_import_in_progress = True
        self._set_controls_enabled(False)

        self.extract_status_var.set("Status: Validating file...")
        self.extract_progress_bar.configure(mode="indeterminate")
        self.extract_progress_bar.start(12)

        worker = threading.Thread(
            target=self._extract_import_worker, args=(path,), daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_extract_import_queue)

    def _extract_import_worker(self, path: Path) -> None:
        """Runs on a background thread. Only calls pdf_engine (pure
        file-system work) and puts a plain dict on the thread-safe
        queue -- never touches a tkinter widget directly.
        """
        try:
            info = pdf_engine.get_pdf_info(path)
            self._extract_import_queue.put({
                "success": True, "info": info, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._extract_import_queue.put({
                "success": False, "info": None, "error": str(exc),
            })
        except Exception:
            self._extract_import_queue.put({
                "success": False, "info": None,
                "error": "An unexpected error occurred while reading this file.",
            })

    def _poll_extract_import_queue(self) -> None:
        try:
            result = self._extract_import_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_extract_import_queue)
            return
        self._apply_extract_import_result(result)

    def _apply_extract_import_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.extract_progress_bar.stop()
        self.extract_progress_bar.configure(mode="determinate", value=0)
        self._extract_import_in_progress = False

        if result["success"]:
            self.extract_source = models.PDFFile(**result["info"])
            self._update_extract_source_label()
            self.extract_status_var.set(
                f"Status: Selected '{self.extract_source.name}'."
            )
        else:
            self.extract_source = None
            self._update_extract_source_label()
            self.extract_status_var.set("Status: Could not read that file.")
            messagebox.showerror(
                title="Invalid PDF", message=result["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Extract Pages' import shares the same app-wide busy lock, so
        # its completion must restore Split's and Remove Pages' controls
        # too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    def _on_extract_clear_selection_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        # Only resets the page-selection text, not the selected source
        # PDF -- the user most likely wants to try a different selection
        # against the same file, not re-pick the file too. Setting the
        # StringVar fires the trace in _build_extract_workspace(), which
        # refreshes the preview/error/button state automatically.
        self.extract_selection_var.set("")

    # ------------------------------------------------------------------
    # Extract Pages: the actual extract operation (Phase 15)
    # ------------------------------------------------------------------

    def _on_extract_execute_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        if self.extract_source is None:
            return  # defensive; button should be disabled without a source

        page_count = self.extract_source.page_count or 0
        text = self.extract_selection_var.get()

        try:
            indices = extract_engine.resolve_pages_to_extract(text, page_count)
        except split_engine.PageRangeError as exc:
            self.extract_error_var.set(str(exc))
            self.extract_status_var.set(f"Status: {exc}")
            messagebox.showerror(
                title="Invalid Page Selection", message=str(exc), parent=self.root,
            )
            return

        # Sensible, project-consistent default filename for the native
        # Save As dialog -- reusing file_manager's existing sanitization/
        # extension helpers rather than inventing a second naming
        # mechanism, exactly like Remove Pages does.
        default_name = file_manager.ensure_pdf_extension(
            file_manager.sanitize_windows_filename(
                f"{self.extract_source.path.stem}_extracted"
            )
        )
        output_path = file_manager.save_pdf_file(
            parent=self.root,
            default_name=default_name,
            title="Save Extracted Pages As",
        )
        if output_path is None:
            self.extract_status_var.set("Status: Ready")
            return

        self._start_extract(self.extract_source.path, indices, output_path)

    def _start_extract(
        self, source_path: Path, page_indices: List[int], output_path: Path,
    ) -> None:
        self.extract_in_progress = True
        self._set_controls_enabled(False)

        self.extract_status_var.set("Status: Extracting pages...")
        self.extract_progress_bar.configure(mode="indeterminate")
        self.extract_progress_bar.start(12)

        worker = threading.Thread(
            target=self._extract_worker,
            args=(source_path, page_indices, output_path),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_extract_queue)

    def _extract_worker(
        self, source_path: Path, page_indices: List[int], output_path: Path,
    ) -> None:
        """Runs on a background thread. Calls
        extract_engine.extract_pages_from_pdf() directly. Must not touch
        any tkinter widget; only the thread-safe queue is used to report
        back.
        """
        def report(message: str) -> None:
            self._extract_queue.put({"type": "progress", "message": message})

        try:
            result_path = extract_engine.extract_pages_from_pdf(
                source_path, output_path, page_indices,
                progress_callback=report,
            )
            self._extract_queue.put({
                "type": "done", "success": True,
                "output_path": result_path, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._extract_queue.put({
                "type": "done", "success": False,
                "output_path": None, "error": str(exc),
            })
        except Exception:
            self._extract_queue.put({
                "type": "done", "success": False, "output_path": None,
                "error": "An unexpected error occurred while extracting pages.",
            })

    def _poll_extract_queue(self) -> None:
        try:
            while True:
                item = self._extract_queue.get_nowait()
                if item["type"] == "progress":
                    self.extract_status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_extract_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_extract_queue)

    def _apply_extract_result(self, item: dict) -> None:
        self._assert_main_thread()
        self.extract_progress_bar.stop()
        self.extract_progress_bar.configure(mode="determinate", value=0)

        self.extract_in_progress = False

        if item["success"]:
            output_path = item["output_path"]
            self.extract_status_var.set(
                f"Status: Pages extracted successfully. Saved to "
                f"'{output_path.name}'."
            )
            # A completed extraction has fully consumed its source,
            # mirroring Split PDF's and Remove Pages' own post-
            # completion behavior: clear it (and the selection text) so
            # the workspace returns to its non-file-selected initial
            # state, rather than leaving a stale source attached that
            # invites an accidental repeat operation on a file that's
            # already been processed. Must happen before
            # _update_extract_controls_state() below so EXTRACT PAGES
            # correctly goes back to "disabled" (it depends on
            # self.extract_source).
            self.extract_source = None
            self._update_extract_source_label()
            self.extract_selection_var.set("")
        else:
            self.extract_status_var.set("Status: Extract Pages failed.")
            messagebox.showerror(
                title="Extract Pages Failed", message=item["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Extract Pages shares the same app-wide busy lock, so its
        # completion must restore Split's and Remove Pages' controls
        # too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    # ------------------------------------------------------------------
    # Organize/Reorder Pages: source file import (Phase 16)
    # ------------------------------------------------------------------

    def _on_organize_select_file_clicked(self) -> None:
        if self._any_operation_in_progress():
            return

        path = file_manager.select_single_pdf_file(parent=self.root)
        if path is None:
            self.organize_status_var.set("Status: Ready")
            return

        self._start_organize_import(path)

    def _start_organize_import(self, path: Path) -> None:
        self._organize_import_in_progress = True
        self._set_controls_enabled(False)

        self.organize_status_var.set("Status: Validating file...")
        self.organize_progress_bar.configure(mode="indeterminate")
        self.organize_progress_bar.start(12)

        worker = threading.Thread(
            target=self._organize_import_worker, args=(path,), daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_organize_import_queue)

    def _organize_import_worker(self, path: Path) -> None:
        """Runs on a background thread. Only calls pdf_engine (pure
        file-system work) and puts a plain dict on the thread-safe
        queue -- never touches a tkinter widget directly.
        """
        try:
            info = pdf_engine.get_pdf_info(path)
            self._organize_import_queue.put({
                "success": True, "info": info, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._organize_import_queue.put({
                "success": False, "info": None, "error": str(exc),
            })
        except Exception:
            self._organize_import_queue.put({
                "success": False, "info": None,
                "error": "An unexpected error occurred while reading this file.",
            })

    def _poll_organize_import_queue(self) -> None:
        try:
            result = self._organize_import_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_organize_import_queue)
            return
        self._apply_organize_import_result(result)

    def _apply_organize_import_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.organize_progress_bar.stop()
        self.organize_progress_bar.configure(mode="determinate", value=0)
        self._organize_import_in_progress = False

        if result["success"]:
            self.organize_source = models.PDFFile(**result["info"])
            self._update_organize_source_label()
            # Initial state (Phase 16 requirement 5): the order starts
            # as "1,2,3,...,N" -- i.e. "no change" -- which the user can
            # then modify via the entry, Move Up/Move Down, or Reset.
            self.organize_order_var.set(
                organize_engine.identity_order(self.organize_source.page_count or 0)
            )
            self.organize_status_var.set(
                f"Status: Selected '{self.organize_source.name}'."
            )
        else:
            self.organize_source = None
            self._update_organize_source_label()
            self.organize_order_var.set("")
            self.organize_status_var.set("Status: Could not read that file.")
            messagebox.showerror(
                title="Invalid PDF", message=result["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Organize Pages' import shares the same app-wide busy lock, so
        # its completion must restore every other tool's controls too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    # ------------------------------------------------------------------
    # Organize/Reorder Pages: the actual reorder operation (Phase 16)
    # ------------------------------------------------------------------

    def _on_organize_execute_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        if self.organize_source is None:
            return  # defensive; button should be disabled without a source

        page_count = self.organize_source.page_count or 0
        text = self.organize_order_var.get()

        try:
            order = organize_engine.parse_page_order(text, page_count)
        except organize_engine.OrganizeOrderError as exc:
            self.organize_error_var.set(str(exc))
            self.organize_status_var.set(f"Status: {exc}")
            messagebox.showerror(
                title="Invalid Page Order", message=str(exc), parent=self.root,
            )
            return

        # Sensible, project-consistent default filename for the native
        # Save As dialog -- reusing file_manager's existing sanitization/
        # extension helpers rather than inventing a second naming
        # mechanism, exactly like Extract Pages and Remove Pages do.
        default_name = file_manager.ensure_pdf_extension(
            file_manager.sanitize_windows_filename(
                f"{self.organize_source.path.stem}_organized"
            )
        )
        output_path = file_manager.save_pdf_file(
            parent=self.root,
            default_name=default_name,
            title="Save Reordered Pages As",
        )
        if output_path is None:
            self.organize_status_var.set("Status: Ready")
            return

        self._start_organize(self.organize_source.path, order, output_path)

    def _start_organize(
        self, source_path: Path, page_order: List[int], output_path: Path,
    ) -> None:
        self.organize_in_progress = True
        self._set_controls_enabled(False)

        self.organize_status_var.set("Status: Reordering pages...")
        self.organize_progress_bar.configure(mode="indeterminate")
        self.organize_progress_bar.start(12)

        worker = threading.Thread(
            target=self._organize_worker,
            args=(source_path, page_order, output_path),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_organize_queue)

    def _organize_worker(
        self, source_path: Path, page_order: List[int], output_path: Path,
    ) -> None:
        """Runs on a background thread. Calls
        organize_engine.organize_pages_from_pdf() directly. Must not
        touch any tkinter widget; only the thread-safe queue is used to
        report back.
        """
        def report(message: str) -> None:
            self._organize_queue.put({"type": "progress", "message": message})

        try:
            result_path = organize_engine.organize_pages_from_pdf(
                source_path, output_path, page_order,
                progress_callback=report,
            )
            self._organize_queue.put({
                "type": "done", "success": True,
                "output_path": result_path, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._organize_queue.put({
                "type": "done", "success": False,
                "output_path": None, "error": str(exc),
            })
        except Exception:
            self._organize_queue.put({
                "type": "done", "success": False, "output_path": None,
                "error": "An unexpected error occurred while reordering pages.",
            })

    def _poll_organize_queue(self) -> None:
        try:
            while True:
                item = self._organize_queue.get_nowait()
                if item["type"] == "progress":
                    self.organize_status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_organize_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_organize_queue)

    def _apply_organize_result(self, item: dict) -> None:
        self._assert_main_thread()
        self.organize_progress_bar.stop()
        self.organize_progress_bar.configure(mode="determinate", value=0)

        self.organize_in_progress = False

        if item["success"]:
            output_path = item["output_path"]
            self.organize_status_var.set(
                f"Status: Pages reordered successfully. Saved to "
                f"'{output_path.name}'."
            )
            # A completed reorder has fully consumed its source,
            # mirroring Split PDF's, Remove Pages', and Extract Pages'
            # own post-completion behavior: clear it (and the order
            # text) so the workspace returns to its non-file-selected
            # initial state, rather than leaving a stale source attached
            # that invites an accidental repeat operation on a file
            # that's already been processed. Must happen before
            # _update_organize_controls_state() below so ORGANIZE PAGES
            # correctly goes back to "disabled" (it depends on
            # self.organize_source).
            self.organize_source = None
            self._update_organize_source_label()
            self.organize_order_var.set("")
        else:
            self.organize_status_var.set("Status: Organize Pages failed.")
            messagebox.showerror(
                title="Organize Pages Failed", message=item["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Organize Pages shares the same app-wide busy lock, so its
        # completion must restore every other tool's controls too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    # ------------------------------------------------------------------
    # Rotate Pages: source file import (Phase 17)
    # ------------------------------------------------------------------

    def _on_rotate_select_file_clicked(self) -> None:
        if self._any_operation_in_progress():
            return

        path = file_manager.select_single_pdf_file(parent=self.root)
        if path is None:
            self.rotate_status_var.set("Status: Ready")
            return

        self._start_rotate_import(path)

    def _start_rotate_import(self, path: Path) -> None:
        self._rotate_import_in_progress = True
        self._set_controls_enabled(False)

        self.rotate_status_var.set("Status: Validating file...")
        self.rotate_progress_bar.configure(mode="indeterminate")
        self.rotate_progress_bar.start(12)

        worker = threading.Thread(
            target=self._rotate_import_worker, args=(path,), daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_rotate_import_queue)

    def _rotate_import_worker(self, path: Path) -> None:
        """Runs on a background thread. Only calls pdf_engine (pure
        file-system work) and puts a plain dict on the thread-safe
        queue -- never touches a tkinter widget directly.
        """
        try:
            info = pdf_engine.get_pdf_info(path)
            self._rotate_import_queue.put({
                "success": True, "info": info, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._rotate_import_queue.put({
                "success": False, "info": None, "error": str(exc),
            })
        except Exception:
            self._rotate_import_queue.put({
                "success": False, "info": None,
                "error": "An unexpected error occurred while reading this file.",
            })

    def _poll_rotate_import_queue(self) -> None:
        try:
            result = self._rotate_import_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_rotate_import_queue)
            return
        self._apply_rotate_import_result(result)

    def _apply_rotate_import_result(self, result: dict) -> None:
        self._assert_main_thread()
        self.rotate_progress_bar.stop()
        self.rotate_progress_bar.configure(mode="determinate", value=0)
        self._rotate_import_in_progress = False

        if result["success"]:
            self.rotate_source = models.PDFFile(**result["info"])
            self._update_rotate_source_label()
            self.rotate_status_var.set(
                f"Status: Selected '{self.rotate_source.name}'."
            )
            # Phase 17 requirement 6: after import, page selection stays
            # empty (the user must explicitly choose which pages to
            # rotate -- pages are never auto-selected), while rotation
            # controls (direction/angle) are already enabled with their
            # existing default (Clockwise 90) ready to use.
            self.rotate_selection_var.set("")
        else:
            self.rotate_source = None
            self._update_rotate_source_label()
            self.rotate_selection_var.set("")
            self.rotate_status_var.set("Status: Could not read that file.")
            messagebox.showerror(
                title="Invalid PDF", message=result["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Rotate Pages' import shares the same app-wide busy lock, so
        # its completion must restore every other tool's controls too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()

    def _on_rotate_clear_selection_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        # Only resets the page-selection text, not the selected source
        # PDF or the direction/angle choice -- the user most likely
        # wants to try a different page selection against the same file
        # and rotation settings, not start over completely (mirrors
        # Remove Pages'/Extract Pages' own Clear Selection behavior).
        self.rotate_selection_var.set("")

    # ------------------------------------------------------------------
    # Rotate Pages: the actual rotate operation (Phase 17)
    # ------------------------------------------------------------------

    def _on_rotate_execute_clicked(self) -> None:
        if self._any_operation_in_progress():
            return
        if self.rotate_source is None:
            return  # defensive; button should be disabled without a source

        page_count = self.rotate_source.page_count or 0
        text = self.rotate_selection_var.get()

        try:
            indices = rotate_engine.resolve_pages_to_rotate(text, page_count)
        except split_engine.PageRangeError as exc:
            self.rotate_error_var.set(str(exc))
            self.rotate_status_var.set(f"Status: {exc}")
            messagebox.showerror(
                title="Invalid Page Selection", message=str(exc), parent=self.root,
            )
            return

        degrees = rotate_engine.resolve_clockwise_degrees(
            self.rotate_direction_var.get(), self.rotate_angle_var.get(),
        )
        rotations = rotate_engine.build_rotation_map(indices, degrees)

        # Sensible, project-consistent default filename for the native
        # Save As dialog -- reusing file_manager's existing sanitization/
        # extension helpers rather than inventing a second naming
        # mechanism, exactly like Remove/Extract/Organize Pages do.
        default_name = file_manager.ensure_pdf_extension(
            file_manager.sanitize_windows_filename(
                f"{self.rotate_source.path.stem}_rotated"
            )
        )
        output_path = file_manager.save_pdf_file(
            parent=self.root,
            default_name=default_name,
            title="Save Rotated PDF As",
        )
        if output_path is None:
            self.rotate_status_var.set("Status: Ready")
            return

        self._start_rotate(self.rotate_source.path, rotations, output_path)

    def _start_rotate(
        self, source_path: Path, rotations: Dict[int, int], output_path: Path,
    ) -> None:
        self.rotate_in_progress = True
        self._set_controls_enabled(False)

        self.rotate_status_var.set("Status: Rotating pages...")
        self.rotate_progress_bar.configure(mode="indeterminate")
        self.rotate_progress_bar.start(12)

        worker = threading.Thread(
            target=self._rotate_worker,
            args=(source_path, rotations, output_path),
            daemon=True,
        )
        worker.start()
        self.root.after(80, self._poll_rotate_queue)

    def _rotate_worker(
        self, source_path: Path, rotations: Dict[int, int], output_path: Path,
    ) -> None:
        """Runs on a background thread. Calls
        rotate_engine.rotate_pages_in_pdf() directly. Must not touch any
        tkinter widget; only the thread-safe queue is used to report
        back.
        """
        def report(message: str) -> None:
            self._rotate_queue.put({"type": "progress", "message": message})

        try:
            result_path = rotate_engine.rotate_pages_in_pdf(
                source_path, output_path, rotations,
                progress_callback=report,
            )
            self._rotate_queue.put({
                "type": "done", "success": True,
                "output_path": result_path, "error": None,
            })
        except pdf_engine.PDFEngineError as exc:
            self._rotate_queue.put({
                "type": "done", "success": False,
                "output_path": None, "error": str(exc),
            })
        except Exception:
            self._rotate_queue.put({
                "type": "done", "success": False, "output_path": None,
                "error": "An unexpected error occurred while rotating pages.",
            })

    def _poll_rotate_queue(self) -> None:
        try:
            while True:
                item = self._rotate_queue.get_nowait()
                if item["type"] == "progress":
                    self.rotate_status_var.set(f"Status: {item['message']}")
                elif item["type"] == "done":
                    self._apply_rotate_result(item)
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll_rotate_queue)

    def _apply_rotate_result(self, item: dict) -> None:
        self._assert_main_thread()
        self.rotate_progress_bar.stop()
        self.rotate_progress_bar.configure(mode="determinate", value=0)

        self.rotate_in_progress = False

        if item["success"]:
            output_path = item["output_path"]
            self.rotate_status_var.set(
                f"Status: Pages rotated successfully. Saved to "
                f"'{output_path.name}'."
            )
            # A completed rotation has fully consumed its source,
            # mirroring Split PDF's/Remove Pages'/Extract Pages'/
            # Organize Pages' own post-completion behavior: clear it
            # (and the selection text) so the workspace returns to its
            # non-file-selected initial state, rather than leaving a
            # stale source attached that invites an accidental repeat
            # operation on a file that's already been processed. Must
            # happen before _update_rotate_controls_state() below so
            # ROTATE PAGES correctly goes back to "disabled" (it depends
            # on self.rotate_source).
            self.rotate_source = None
            self._update_rotate_source_label()
            self.rotate_selection_var.set("")
        else:
            self.rotate_status_var.set("Status: Rotate Pages failed.")
            messagebox.showerror(
                title="Rotate Pages Failed", message=item["error"], parent=self.root,
            )

        self._update_button_states()
        # Bug fix: see the matching comment in _apply_import_results() --
        # Rotate Pages shares the same app-wide busy lock, so its
        # completion must restore every other tool's controls too.
        self._update_split_controls_state()
        self._update_remove_pages_controls_state()
        self._update_extract_controls_state()
        self._update_organize_controls_state()
        self._update_rotate_controls_state()


def run() -> None:
    """Entry point used by app.py to start the GUI event loop."""
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()
