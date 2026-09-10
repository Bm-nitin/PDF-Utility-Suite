"""
file_manager.py

Native Windows file selection and save dialogs, plus pure output-path
utilities (extension enforcement, Windows filename sanitization, and
collision-safe automatic naming). No PDF processing logic lives here --
that stays in pdf_engine.py.

Tkinter's filedialog module calls the native OS common dialog on Windows,
so these produce the real native "Open", "Save As", and "Select Folder"
windows, not custom-drawn tkinter widgets.

PHASE 7 NOTE: adds select_output_folder() and generate_compressed_output_path()
-- the output architecture Phase 8's Compress Only needs (one input PDF ->
Save As; multiple input PDFs -> pick a folder once, then each gets an
automatically generated, collision-safe "<name>_compressed.pdf" filename).
Neither is wired into ui.py yet; Phase 8 does that when compression
processing itself is implemented. Also hardens save_pdf_file() so its
returned path always ends in exactly one ".pdf" extension, even if the
native dialog were to return something else.

A note on time-of-check-to-time-of-use (TOCTOU): generate_compressed_output_path()
below checks Path.exists() to find a free filename, but a file could
theoretically be created at that exact path by something else between
that check and the actual write. This module only ever *picks* the path;
it never writes PDF content. The actual write goes through
pdf_engine._atomic_save_pdf(), which writes to a temp file and moves it
into place with os.replace() -- so even in the unlikely case of such a
race, the result is a clean, atomic replace rather than a corrupted
partial file, which is the property this project's requirements actually
call for.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from tkinter import filedialog

from config import DEFAULT_OUTPUT_NAME

# Characters that are invalid anywhere in a Windows filename (not path --
# this list deliberately excludes '/' and '\\', which are path separators
# handled by the fact that we only ever sanitize a single path *component*,
# such as a file stem, never a full path string).
_WINDOWS_INVALID_FILENAME_CHARS = '<>:"/\\|?*'

# Windows reserved device names -- invalid as a filename stem regardless
# of case or extension (e.g. "CON.pdf" is still invalid).
_WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def select_pdf_files(parent=None) -> List[Path]:
    """Open the native multi-select Open dialog, restricted to PDFs.

    Only a single "PDF files (*.pdf)" filter is offered -- no "All files"
    fallback -- so the dialog itself prevents selecting non-PDF files, per
    the Phase 4 requirement that only PDFs be selectable. As defense in
    depth, pdf_engine.validate_pdf() also re-checks the extension (and
    that the file actually parses as a PDF) before anything is added to
    the list, since a user could still type an arbitrary filename into
    the dialog's filename box.

    Returns an empty list if the user cancels -- this is a normal,
    expected outcome, not an error, and callers must not treat it as one.
    """
    paths = filedialog.askopenfilenames(
        parent=parent,
        title="Select PDF Files",
        filetypes=[("PDF files", "*.pdf")],
    )
    return [Path(p) for p in paths]


def select_single_pdf_file(parent=None) -> Optional[Path]:
    """Open the native SINGLE-file Open dialog, restricted to PDFs.

    Phase 13: Split PDF operates on exactly one source file at a time,
    which is a different selection semantic than Merge/Compress's
    multi-select (select_pdf_files() above) -- rather than reusing that
    dialog and discarding extra selections (confusing: the user could
    select three files and silently have two ignored), this is a
    dedicated single-file picker using askopenfilename (singular).

    Returns None if the user cancels -- a normal outcome, not an error.
    """
    path_str = filedialog.askopenfilename(
        parent=parent,
        title="Select PDF File",
        filetypes=[("PDF files", "*.pdf")],
    )
    if not path_str:
        return None
    return Path(path_str)


def save_pdf_file(
    parent=None,
    default_name: str = DEFAULT_OUTPUT_NAME,
    initial_dir: Optional[str] = None,
) -> Optional[Path]:
    """Open the native Save As dialog.

    Returns None if the user cancels, or if they somehow submit an empty
    filename -- both are treated as a normal return-to-idle, not an error.

    The returned path is guaranteed to end in exactly one ".pdf"
    extension (case preserved otherwise). tkinter's `defaultextension`
    only fills in an extension when the user types none at all -- if they
    explicitly type a different extension, Windows' native dialog leaves
    it as-is, so this is re-checked defensively here rather than trusted
    to the dialog alone.
    """
    kwargs = dict(
        parent=parent,
        title="Save Merged PDF As",
        defaultextension=".pdf",
        initialfile=default_name,
        filetypes=[("PDF files", "*.pdf")],
    )
    if initial_dir:
        kwargs["initialdir"] = initial_dir

    path_str = filedialog.asksaveasfilename(**kwargs)
    if not path_str or not path_str.strip():
        return None

    path = Path(path_str)
    if path.suffix.lower() != ".pdf":
        path = path.with_name(ensure_pdf_extension(path.name))
    return path


def select_output_folder(parent=None) -> Optional[Path]:
    """Native "Select Folder" dialog.

    This is the output-selection step for a future multi-file Compress
    Only batch (Phase 8): the user picks a destination folder once, and
    each input file's compressed output is auto-named into it via
    generate_compressed_output_path(). Not yet called anywhere in ui.py --
    added now per the Phase 7 requirement to prepare this architecture
    ahead of Phase 8's actual compression wiring.

    Returns None if the user cancels.
    """
    folder_str = filedialog.askdirectory(
        parent=parent,
        title="Select Output Folder",
        mustexist=True,
    )
    if not folder_str:
        return None
    return Path(folder_str)


def confirm_overwrite_if_needed(path: Path, parent=None) -> bool:
    """The native Save As dialog already asks the OS-level 'file exists,
    overwrite?' question on Windows, so this is a secondary, explicit
    safety check for cases where a path was constructed programmatically
    (not chosen through the dialog) and could silently clobber a file.

    In practice, generate_compressed_output_path() below never returns an
    already-existing path in the first place (it appends " (1)", " (2)",
    etc. until it finds a free name), so callers using that function for
    automatic batch naming should not need this check at all -- collision
    avoidance happens by construction, not by asking permission after the
    fact. This function remains available for any other programmatically
    constructed path that isn't run through that collision-safe generator.

    Returns True if it's safe to proceed with writing to `path`.
    """
    path = Path(path)
    if not path.exists():
        return True

    from tkinter import messagebox

    return messagebox.askyesno(
        title="File Already Exists",
        message=(
            f"'{path.name}' already exists.\n\n"
            f"Do you want to replace it?"
        ),
        parent=parent,
    )


# ---------------------------------------------------------------------------
# Output-path utilities (pure functions, no dialogs, no filesystem side
# effects other than the read-only Path.exists() checks needed to find a
# free filename)
# ---------------------------------------------------------------------------

def ensure_pdf_extension(filename: str) -> str:
    """Ensure `filename` ends in exactly one ".pdf" extension.

    Case-insensitive: "Report.PDF" is left alone (its case is preserved),
    but "Report" becomes "Report.pdf". Guards specifically against
    accidentally doubling the extension (e.g. never turns "merged.pdf"
    into "merged.pdf.pdf").
    """
    if filename.lower().endswith(".pdf"):
        return filename
    return filename + ".pdf"


def sanitize_windows_filename(name: str) -> str:
    """Make `name` safe to use as a Windows filename component (a stem or
    a full filename -- not a full path; do not pass path separators
    through this function).

    Only replaces characters that are *actually* invalid on Windows
    (`< > : " / \\ | ? *` and ASCII control characters), plus trims
    trailing dots/spaces (which Windows silently strips, and which can
    otherwise cause confusing mismatches between the name you asked for
    and the name Windows actually created). Everything else -- spaces,
    Unicode letters, accented characters, parentheses, hyphens -- passes
    through unchanged, per the Phase 7 requirement to sanitize only what's
    genuinely invalid rather than being over-aggressive.

    Windows' reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    are also invalid as a filename regardless of case or extension; these
    get an underscore prefix rather than being silently truncated away.
    """
    sanitized = "".join(
        "_" if (ch in _WINDOWS_INVALID_FILENAME_CHARS or ord(ch) < 32) else ch
        for ch in name
    )
    sanitized = sanitized.rstrip(" .")

    if not sanitized:
        sanitized = "output"

    stem_for_check = Path(sanitized).stem.upper()
    if stem_for_check in _WINDOWS_RESERVED_NAMES:
        sanitized = "_" + sanitized

    return sanitized


def _find_collision_free_path(output_dir: Path, base_name: str) -> Path:
    """Shared collision-avoidance logic: try "<base_name>.pdf" first,
    then "<base_name> (1).pdf", " (2)", etc., until a path that doesn't
    currently exist is found. Used by both generate_compressed_output_path()
    and generate_split_output_path() (Phase 13) so the naming/collision
    rule lives in exactly one place.
    """
    candidate = output_dir / ensure_pdf_extension(base_name)
    if not candidate.exists():
        return candidate

    counter = 1
    while True:
        candidate = output_dir / ensure_pdf_extension(f"{base_name} ({counter})")
        if not candidate.exists():
            return candidate
        counter += 1


def generate_compressed_output_path(input_path: Path, output_dir: Path) -> Path:
    """Generate a collision-safe destination path for a compressed copy
    of `input_path`, inside `output_dir`.

    Naming pattern: "<stem>_compressed.pdf"; if that already exists,
    "<stem>_compressed (1).pdf", then " (2)", and so on, until a name
    that doesn't currently exist is found. This is the single source of
    truth for Compress Only's automatic batch naming (Phase 8) -- adding
    it now, per the Phase 7 architecture requirement, keeps the naming
    logic in exactly one place rather than duplicating it between ui.py
    and pdf_engine.py.

    Never returns a path that already exists at the time of the call, so
    a caller using this function to pick output paths for a batch will
    never silently overwrite an existing file. (This is a check against
    the filesystem at call time, not a lock -- see the module-level note
    on TOCTOU below for why the actual save must still go through
    pdf_engine's atomic writer.)
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    stem = sanitize_windows_filename(input_path.stem)
    return _find_collision_free_path(output_dir, f"{stem}_compressed")


def generate_split_output_path(source_path: Path, output_dir: Path, suffix: str) -> Path:
    """Generate a collision-safe destination path for one Split PDF
    output file (Phase 13), following the exact same naming/collision
    pattern as generate_compressed_output_path() above: "<stem><suffix>.pdf",
    auto-incrementing with " (1)", " (2)", etc. if that name is already
    taken. Never returns a path that already exists.

    `suffix` is produced by split_engine.py (e.g. "_001" for individual-
    page/every-N-pages mode, or "_001_pages_1-3" for custom-range mode)
    -- this function only owns filename sanitization and collision
    avoidance, not the sequence-numbering/range-labeling scheme itself,
    keeping "what pages went where" (split_engine.py) separate from
    "how to turn that into a safe Windows filename" (this module).
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    stem = sanitize_windows_filename(source_path.stem)
    return _find_collision_free_path(output_dir, f"{stem}{suffix}")

