"""
pdf_engine.py

All PDF processing lives here: validation, page counting, merging, and
compression. Nothing in this file should import tkinter or know anything
about the GUI.

Uses PyMuPDF (imported as `pymupdf`, the currently recommended import
name; the older `import fitz` still works identically but is considered
legacy by the library itself).

PHASE 7 NOTE: merge_pdfs() and compress_pdf() now write their output
through _atomic_save_pdf() instead of calling doc.save(output_path)
directly. This guarantees that if a save fails partway through (disk
full, permission denied, drive disconnected, process killed), the
destination path is left exactly as it was before the operation --
either untouched, or fully replaced with the complete new file. There is
never a state where a partial/corrupt file sits at the destination path.
Filesystem failures are also translated into a human-readable
PDFEngineError instead of letting a raw OSError escape to the caller.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

import pymupdf

from config import COMPRESSION_PRESETS, DEFAULT_COMPRESSION_LEVEL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PDFEngineError(Exception):
    """Base class for all engine-level errors. GUI code should catch this
    (not raw exceptions from PyMuPDF) and show a clean message.
    """


class InvalidPDFError(PDFEngineError):
    """Raised when a file is not a readable PDF at all."""


class EncryptedPDFError(PDFEngineError):
    """Raised when a PDF requires a password we don't have."""


# ---------------------------------------------------------------------------
# Atomic, crash-safe output writing
# ---------------------------------------------------------------------------

def _atomic_save_pdf(doc: "pymupdf.Document", output_path: Path, **save_kwargs) -> None:
    """Save `doc` to `output_path` atomically.

    Writes to a temporary file in the *same directory* as `output_path`
    first, and only replaces the real destination once that write has
    fully succeeded (via os.replace(), which is atomic on both Windows
    and POSIX when source and destination are on the same filesystem --
    using the same directory guarantees that). If anything goes wrong
    partway through, the temp file is removed and `output_path` is left
    exactly as it was before the call -- there is no window where a
    partial or corrupt file exists at the destination.

    Raises PDFEngineError (not a raw OSError) for filesystem failures --
    permission denied, an unwritable/missing destination folder, a full
    disk, etc. -- with a message a normal user can act on.
    """
    output_path = Path(output_path)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PDFEngineError(
            f"Could not create the destination folder "
            f"'{output_path.parent}': {exc.strerror or exc}."
        ) from exc

    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            suffix=".pdf", prefix=".tmp_", dir=str(output_path.parent)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)

        doc.save(tmp_path, **save_kwargs)
        os.replace(tmp_path, output_path)  # atomic: same directory/filesystem
        tmp_path = None  # successfully moved -- nothing left to clean up
    except OSError as exc:
        raise PDFEngineError(
            f"Could not save to '{output_path.name}': {exc.strerror or exc}. "
            f"Choose a different location and try again."
        ) from exc
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("Could not remove temp file: %s", tmp_path)


# ---------------------------------------------------------------------------
# Validation / inspection
# ---------------------------------------------------------------------------

def validate_pdf(path: Path) -> None:
    """Raise a PDFEngineError subclass if the file is not usable.

    Does not raise on success. This is intentionally strict: it opens the
    document to make sure PyMuPDF can actually parse it, not just that the
    extension is '.pdf'.
    """
    path = Path(path)

    if not path.exists():
        raise InvalidPDFError(f"File not found: {path}")

    if path.is_dir():
        raise InvalidPDFError(f"'{path.name}' is a folder, not a PDF file.")

    if path.suffix.lower() != ".pdf":
        raise InvalidPDFError(f"'{path.name}' is not a PDF file.")

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF raises its own error types
        raise InvalidPDFError(
            f"'{path.name}' could not be read. It may be corrupted."
        ) from exc

    try:
        if doc.is_encrypted:
            # needs_pass is True if a password is actually required to
            # open pages (some "encrypted" PDFs have an empty owner
            # password and are effectively readable).
            if doc.needs_pass:
                raise EncryptedPDFError(
                    f"'{path.name}' is password-protected and cannot be "
                    f"processed."
                )
    finally:
        doc.close()


def get_page_count(path: Path) -> int:
    """Return the page count of a PDF. Assumes validate_pdf() already
    passed; will raise InvalidPDFError again if not.
    """
    path = Path(path)
    try:
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception as exc:
        raise InvalidPDFError(
            f"Could not read page count for '{path.name}'."
        ) from exc


def is_encrypted(path: Path) -> bool:
    path = Path(path)
    try:
        with pymupdf.open(path) as doc:
            return bool(doc.is_encrypted)
    except Exception:
        return False


def get_pdf_info(path: Path) -> dict:
    """Return a small dict of metadata used to populate a PDFFile model.
    Raises PDFEngineError subclasses on failure -- callers (file_manager /
    ui) decide how to present that to the user.
    """
    path = Path(path)
    validate_pdf(path)  # raises on encrypted / corrupted / missing

    size = path.stat().st_size
    page_count = get_page_count(path)

    return {
        "path": path,
        "name": path.name,
        "size": size,
        "page_count": page_count,
        "is_encrypted": False,  # validate_pdf already rejected true-encrypted
    }


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def merge_pdfs(
    input_files: List[Path],
    output_path: Path,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Merge PDFs in the exact given order into a single output PDF.

    - Never modifies source files (opened read-only, only read from).
    - Preserves page size/orientation because insert_pdf() copies pages
      as-is; nothing is resized or rotated.
    - Properly closes every document, including on error, via context
      managers / try-finally.

    progress_callback, if given, is called once per input file with a
    short message like "Merging file 2 of 5: report.pdf" immediately
    before that file is read -- real, coarse-grained progress (we
    genuinely know which file is currently being processed), not a fake
    percentage. Optional and backward compatible: existing callers that
    don't pass it are unaffected.
    """
    input_files = [Path(p) for p in input_files]
    output_path = Path(output_path)

    if not input_files:
        raise PDFEngineError("No files to merge.")

    total = len(input_files)
    merged = pymupdf.open()  # new, empty document
    try:
        for index, src_path in enumerate(input_files, start=1):
            if progress_callback:
                progress_callback(
                    f"Merging file {index} of {total}: {src_path.name}"
                )
            validate_pdf(src_path)  # re-check: files could vanish/change
            try:
                with pymupdf.open(src_path) as src_doc:
                    merged.insert_pdf(src_doc)
            except EncryptedPDFError:
                raise
            except Exception as exc:
                raise PDFEngineError(
                    f"Failed to merge '{src_path.name}': the file may be "
                    f"corrupted."
                ) from exc

        _atomic_save_pdf(merged, output_path)
    finally:
        merged.close()

    return output_path


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

def compress_pdf(
    input_path: Path,
    output_path: Path,
    level: str = DEFAULT_COMPRESSION_LEVEL,
) -> Path:
    """Compress a PDF using one of the presets in config.COMPRESSION_PRESETS.

    Strategy:
      1. Always apply PyMuPDF's structural optimization on save
         (garbage collection, stream deflation, cleanup) -- this is safe
         and lossless.
      2. For 'recommended' and 'maximum', additionally recompress embedded
         raster images to a target JPEG quality, which is lossy but is
         the main lever for image-heavy PDFs.

    If the resulting file is NOT smaller than the input, the original
    (structurally-optimized) result is still returned -- callers should
    compare sizes themselves and inform the user honestly (see
    merge_and_compress()). We do not claim a guaranteed reduction.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if level not in COMPRESSION_PRESETS:
        raise PDFEngineError(f"Unknown compression level: {level}")

    # Phase 10: re-validate before processing, exactly like merge_pdfs()
    # already does for each of its inputs. This is what turns "the file
    # vanished/became corrupted/became password-protected since it was
    # imported" into the same clear, specific message
    # (InvalidPDFError/EncryptedPDFError) used everywhere else in the
    # app, instead of a generic "Compression failed: <raw pymupdf
    # internal error>" that could otherwise surface from deeper inside
    # this function for the same underlying problem.
    validate_pdf(input_path)

    preset = COMPRESSION_PRESETS[level]

    try:
        doc = pymupdf.open(input_path)
    except Exception as exc:
        raise InvalidPDFError(
            f"Could not open '{input_path.name}' for compression."
        ) from exc

    try:
        if preset["image_recompress"]:
            _recompress_images(doc, preset["image_quality"])

        _atomic_save_pdf(
            doc,
            output_path,
            garbage=preset["garbage"],
            deflate=preset["deflate"],
            deflate_images=preset["deflate_images"],
            deflate_fonts=preset["deflate_fonts"],
            clean=preset["clean"],
        )
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Compression failed: {exc}"
        ) from exc
    finally:
        doc.close()

    return output_path


def _recompress_images(doc: "pymupdf.Document", quality: int) -> None:
    """Re-encode embedded raster images at a lower JPEG quality, in place,
    within an already-open document (before save()). Skips images that
    fail to process individually rather than aborting the whole document.
    """
    for page_index in range(doc.page_count):
        page = doc[page_index]
        try:
            images = page.get_images(full=True)
        except Exception:
            continue

        for img in images:
            xref = img[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
                # CMYK / other non-RGB colorspaces need conversion before
                # JPEG re-encoding.
                if pix.colorspace and pix.colorspace.n not in (1, 3):
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                jpeg_bytes = pix.tobytes("jpeg", jpg_quality=quality)
                doc.update_stream(xref, jpeg_bytes)
                pix = None
            except Exception:
                # Skip images that can't be safely recompressed (e.g.
                # images with transparency/masks, unusual color spaces).
                # We never let a single bad image abort compression.
                logger.debug(
                    "Skipped recompressing image xref=%s on page %s",
                    xref, page_index,
                )
                continue


# ---------------------------------------------------------------------------
# Combined operation
# ---------------------------------------------------------------------------

def merge_and_compress(
    input_files: List[Path],
    output_path: Path,
    level: str = DEFAULT_COMPRESSION_LEVEL,
    temp_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """High-level convenience used by the GUI worker thread.

    Merges to a temp file, then compresses that temp file to output_path.
    Returns a result dict with size comparison info so the UI can show an
    honest summary (including the case where compression didn't help).

    progress_callback, if given, receives per-file progress during the
    merge stage (forwarded straight from merge_pdfs()'s own
    progress_callback -- e.g. "Merging file 2 of 5: report.pdf"), then a
    single "Compressing..." message for the compression stage. No fake
    percentages anywhere.
    """
    import tempfile
    import uuid

    input_files = [Path(p) for p in input_files]
    output_path = Path(output_path)

    def report(msg: str):
        if progress_callback:
            progress_callback(msg)

    temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
    temp_dir.mkdir(parents=True, exist_ok=True)
    merged_temp_path = temp_dir / f"_merge_{uuid.uuid4().hex}.pdf"

    try:
        merge_pdfs(input_files, merged_temp_path, progress_callback=report)

        merged_size = merged_temp_path.stat().st_size

        report("Compressing...")
        compress_pdf(merged_temp_path, output_path, level=level)

        final_size = output_path.stat().st_size

        report("Done.")
        return {
            "output_path": output_path,
            "merged_size": merged_size,
            "final_size": final_size,
            "size_reduced": final_size < merged_size,
            "bytes_saved": merged_size - final_size,
        }
    finally:
        # Always clean up the intermediate merged file, success or failure.
        if merged_temp_path.exists():
            try:
                merged_temp_path.unlink()
            except OSError:
                logger.warning(
                    "Could not delete temp file: %s", merged_temp_path
                )
