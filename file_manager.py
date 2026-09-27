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


def select_image_files(parent=None) -> List[Path]:
    """Open the native multi-select Open dialog, restricted to image
    files -- the Images -> PDF (Phase 22) analogue of select_pdf_files()
    above, following the exact same "one filter, no All-files fallback"
    convention so the dialog itself steers the user toward valid
    selections.

    The extension list here (PNG/JPEG/BMP/TIFF/WEBP) is deliberately
    duplicated from, rather than imported from,
    images_to_pdf_engine.SUPPORTED_EXTENSIONS -- file_manager.py never
    imports any *_engine module (see this module's own docstring: it
    only ever hands back paths/dialog results, and no engine module is
    a dependency of it), exactly the same layering
    select_pdf_files()/save_pdf_file() already keep with pdf_engine.
    tests/test_images_to_pdf_engine.py asserts the two lists stay in
    sync so this duplication can't silently drift.

    As defense in depth -- a user can still type an arbitrary filename
    into the dialog's filename box -- images_to_pdf_engine re-validates
    every selected file (extension AND actual decodability) before it is
    added to the list.

    Returns an empty list if the user cancels -- a normal, expected
    outcome, not an error.
    """
    paths = filedialog.askopenfilenames(
        parent=parent,
        title="Select Image Files",
        filetypes=[
            ("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"),
        ],
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
    title: str = "Save Merged PDF As",
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

    `title` defaults to the original Merge-only wording so every
    existing caller is unaffected; Phase 14 (Remove Pages) is the first
    caller to pass a different, more accurate title for its own Save As
    dialog rather than this function growing a second, parallel
    save-dialog mechanism.
    """
    kwargs = dict(
        parent=parent,
        title=title,
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


def _find_collision_free_path_with_extension(
    output_dir: Path, base_name: str, extension: str,
    exclude: Optional[set] = None,
) -> Path:
    """The generic form of the collision-avoidance logic below: try
    "<base_name><extension>" first, then "<base_name> (1)<extension>",
    " (2)", etc., until a path that doesn't currently exist on disk AND
    isn't in `exclude` is found. `extension` must include the leading
    dot (e.g. ".png").

    `exclude`, when given, is checked in addition to (not instead of)
    the filesystem: it lets a caller building several output paths in
    one batch -- before any of them exist on disk yet -- avoid handing
    out the same "free" name twice. pdf_to_images_engine.py (Phase 23)
    is the one caller that needs this, for a page selection that
    renders the same PDF page more than once (e.g. "1-3,2-4"): without
    `exclude`, both would independently see "document_page_002.png" as
    unclaimed and collide with each other before either file is
    written. Every other caller in this module leaves `exclude` at its
    default of None, which reduces to exactly the original,
    filesystem-only behavior.

    _find_collision_free_path() (PDF-only, used by every *_engine.py's
    single-PDF-output naming) is a thin wrapper around this, added in
    Phase 23 so PDF -> Images' own multi-file, extension-varying output
    (generate_image_output_path() below) can share the exact same
    counting rule without duplicating it -- the wrapper exists rather
    than switching every existing caller over to pass ".pdf" explicitly,
    so none of those call sites need to change.
    """
    exclude = exclude or set()

    candidate = output_dir / f"{base_name}{extension}"
    if not candidate.exists() and candidate not in exclude:
        return candidate

    counter = 1
    while True:
        candidate = output_dir / f"{base_name} ({counter}){extension}"
        if not candidate.exists() and candidate not in exclude:
            return candidate
        counter += 1


def _find_collision_free_path(output_dir: Path, base_name: str) -> Path:
    """Shared collision-avoidance logic: try "<base_name>.pdf" first,
    then "<base_name> (1).pdf", " (2)", etc., until a path that doesn't
    currently exist is found. Used by generate_compressed_output_path(),
    generate_split_output_path(), and every other single-PDF-output
    generate_*_output_path() helper in this module, so the naming/
    collision rule lives in exactly one place. `base_name` is always a
    bare stem (+ suffix) with no extension of its own -- every existing
    caller already follows that convention.
    """
    return _find_collision_free_path_with_extension(output_dir, base_name, ".pdf")


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


def generate_image_output_path(
    source_path: Path, output_dir: Path, suffix: str, extension: str,
    exclude: Optional[set] = None,
) -> Path:
    """Generate a collision-safe destination path for one rendered page
    image (Phase 23, PDF -> Images), following the exact same naming/
    collision pattern as generate_split_output_path() above --
    "<stem><suffix><extension>", auto-incrementing with " (1)", " (2)",
    etc. if that name is already taken. Never returns a path that
    already exists on disk, nor one already in `exclude` -- see
    _find_collision_free_path_with_extension()'s own docstring for why
    a batch of same-named-page renders needs that second check too.

    `suffix` is produced by pdf_to_images_engine.py (e.g. "_page_003")
    exactly the way split_engine.py owns its own "_001"/"_001_pages_1-3"
    suffixes for generate_split_output_path() -- this function only
    owns filename sanitization, extension, and collision avoidance, not
    the page-numbering scheme itself. `extension` must include the
    leading dot (e.g. ".png", ".jpg").
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    stem = sanitize_windows_filename(source_path.stem)
    return _find_collision_free_path_with_extension(
        output_dir, f"{stem}{suffix}", extension, exclude=exclude,
    )


def generate_unlocked_output_path(source_path: Path, output_dir: Path) -> Path:
    """Generate a collision-safe destination path for an unlocked
    (decrypted) copy of `source_path` (Phase 19), following the exact
    same naming/collision pattern as generate_compressed_output_path()
    and generate_split_output_path() above: "<stem>_unlocked.pdf",
    auto-incrementing with " (1)", " (2)", etc. if that name is already
    taken. Never returns a path that already exists.

    Unlike Remove Pages/Extract Pages/Organize Pages/Rotate Pages/
    Protect PDF (which all use save_pdf_file()'s native Save As dialog,
    since a person actively choosing where a new file goes makes sense
    for those), Unlock PDF writes its output automatically next to the
    source, with no Save As dialog -- this is the Phase 19 spec's own
    explicit design (collision-safe automatic naming, "never overwrite
    automatically") rather than something invented here. `output_dir`
    is expected to be `source_path.parent` in normal use (see ui.py's
    _on_unlock_execute_clicked()), but is accepted as a parameter for
    the same testability reasons generate_compressed_output_path() and
    generate_split_output_path() do.
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    stem = sanitize_windows_filename(source_path.stem)
    return _find_collision_free_path(output_dir, f"{stem}_unlocked")


def generate_numbered_output_path(source_path: Path, output_dir: Path) -> Path:
    """Generate a collision-safe destination path for a page-numbered
    copy of `source_path` (Phase 20), following the exact same naming/
    collision pattern as generate_compressed_output_path(),
    generate_split_output_path(), and generate_unlocked_output_path()
    above: "<stem>_numbered.pdf", auto-incrementing with " (1)", " (2)",
    etc. if that name is already taken. Never returns a path that
    already exists.

    Like Unlock PDF (Phase 19), and unlike Remove Pages/Extract Pages/
    Organize Pages/Rotate Pages/Protect PDF, Page Numbers writes its
    output automatically next to the source, with no Save As dialog --
    the Phase 20 spec's own explicit collision-naming requirements
    ("document_numbered.pdf", " (1)", " (2)", "never overwrite
    automatically") only make sense without one, exactly as reasoned
    through for generate_unlocked_output_path() above. `output_dir` is
    expected to be `source_path.parent` in normal use (see ui.py's
    _on_page_numbers_execute_clicked()), but is accepted as a parameter
    for the same testability reasons every generate_*_output_path()
    function here does.
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    stem = sanitize_windows_filename(source_path.stem)
    return _find_collision_free_path(output_dir, f"{stem}_numbered")



def generate_watermarked_output_path(source_path: Path, output_dir: Path) -> Path:
    """Generate a collision-safe destination path for a watermarked copy
    of `source_path` (Phase 21), following the exact same naming/
    collision pattern as generate_compressed_output_path(),
    generate_split_output_path(), generate_unlocked_output_path() and
    generate_numbered_output_path() above: "<stem>_watermarked.pdf",
    auto-incrementing with " (1)", " (2)", etc. if that name is already
    taken. Never returns a path that already exists. The collision logic
    itself lives, once, in _find_collision_free_path().

    Like Unlock PDF (Phase 19) and Add Page Numbers (Phase 20), and
    unlike Remove Pages/Extract Pages/Organize Pages/Rotate Pages/
    Protect PDF, Add Watermark writes its output automatically next to
    the source, with no Save As dialog -- the Phase 21 spec's own
    explicit collision-naming requirements ("document_watermarked.pdf",
    " (1)", " (2)", "never overwrite automatically") only make sense
    without one, exactly as reasoned through for
    generate_unlocked_output_path() above. `output_dir` is expected to
    be `source_path.parent` in normal use (see ui.py's
    _on_watermark_execute_clicked()), but is accepted as a parameter for
    the same testability reasons every generate_*_output_path()
    function here does.
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)

    stem = sanitize_windows_filename(source_path.stem)
    return _find_collision_free_path(output_dir, f"{stem}_watermarked")
