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
from typing import Dict, Iterable, List, Set, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

import file_manager
import models
import pdf_engine
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

        self._configure_window()
        self._configure_styles()
        self._build_layout()

    # ------------------------------------------------------------------
    # Window / style setup
    # ------------------------------------------------------------------

    def _configure_window(self) -> None:
        self.root.title(APP_NAME)
        self.root.geometry("780x820")
        self.root.minsize(680, 800)
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
        outer = tk.Frame(self.root, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=28, pady=24)

        self._build_header(outer)
        self._build_dropzone(outer)
        self._build_file_list(outer)
        self._build_summary_bar(outer)
        self._build_list_actions(outer)
        self._build_compression_controls(outer)
        self._build_action_buttons(outer)
        self._build_status_area(outer)

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
        """True if import, merge, compress, or merge+compress is
        currently running on a background thread. Used as a defense-in-
        depth guard in every click handler below -- the corresponding
        buttons are already disabled while an operation runs (see
        _set_controls_enabled), so this mainly protects against a stray
        double-click/Enter-key re-trigger or a direct programmatic call
        (as in tests) rather than something reachable through normal use.

        This is the single predicate every part of the UI (action
        buttons, per-row file-list controls, Clear All) agrees on for
        "is anything running right now" -- the Phase 9 "one consistent
        operation-state mechanism" requirement. Four separate booleans
        remain the underlying storage (rather than one combined
        enum/state field) because each operation's start/completion
        code already reads and writes its own flag in exactly one place,
        and unifying them into a single field would mean rewriting every
        worker's start/apply method for no behavioral difference --
        which Phase 9 explicitly says to avoid ("preserve rather than
        rewrite unnecessarily"). What actually matters for correctness --
        that at most one operation can be running, checked consistently
        everywhere -- is what this single method guarantees.
        """
        return (
            self._import_in_progress
            or self._merge_in_progress
            or self._compress_in_progress
            or self._mergecompress_in_progress
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
        if enabled:
            # Restore the file-count-dependent rules for the three
            # action buttons (a flat "enabled" isn't correct for them).
            # _update_button_states() also re-renders the file list so
            # per-row controls pick up the new busy state -- see there.
            self._update_button_states()
        else:
            # Re-render immediately so per-row Remove/Move Up/Move Down
            # become disabled the instant an operation starts (they read
            # _any_operation_in_progress() at row-build time).
            self._render_file_list()

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


def run() -> None:
    """Entry point used by app.py to start the GUI event loop."""
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()
