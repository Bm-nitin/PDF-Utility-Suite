"""
unlock_engine.py

Phase 19: Unlock PDF processing -- authenticating against a password-
protected PDF with a user-supplied password and producing a genuinely
decrypted copy, leaving the source completely untouched. Nothing in
this file imports tkinter.

Like split_engine.py, remove_pages_engine.py, extract_engine.py,
organize_engine.py, rotate_engine.py, and protect_engine.py, this
module reuses pdf_engine's existing validation and atomic-save
machinery rather than creating a second output-saving system --
pdf_engine.validate_pdf() and pdf_engine._atomic_save_pdf() still do
the actual PDF-safety checks and the crash-safe write.

PYMUPDF API -- VERIFIED, NOT ASSUMED
=====================================
Before writing anything here, the installed library (PyMuPDF 1.28.2,
`import pymupdf`) was inspected directly and its actual behavior
confirmed experimentally (see tests/test_unlock_engine.py for the
tests that encode these findings):

- Document.is_encrypted vs Document.needs_pass are DIFFERENT signals,
  and the difference matters here. is_encrypted reflects the
  document's CURRENT lock state: True before a successful
  authenticate() call, and -- confirmed experimentally -- it becomes
  False immediately after a successful authenticate(). needs_pass, by
  contrast, was observed to remain True even after a successful
  authenticate() call on this library version -- it appears to record
  "this file's format has an encryption dictionary" rather than "this
  document is currently locked". Because of this, is_encrypted (not
  needs_pass) is what this module treats as authoritative for "is
  there still something to unlock" both before and after
  authenticate().
- Document.authenticate(password) returns 0 for a failed attempt, and
  a nonzero value for success. On the installed version, authenticating
  with the correct USER password returned 2 and authenticating with
  the correct OWNER password returned 4 -- two different nonzero
  values. This module does not depend on which nonzero value comes
  back (`if not doc.authenticate(password)` is the only branch that
  matters): whichever password the user has, if it authenticates the
  document at all, that's sufficient to read and re-save its content,
  which is everything an "unlock a copy" operation needs. If a PDF's
  user and owner passwords differ, whichever one the person actually
  has and types in works.
- Document.page_count, and even page rotation, are readable on a
  freshly opened, NOT-yet-authenticated Document in this library
  version -- confirmed experimentally. get_source_info() below relies
  on this to show the page count before the user has typed a password
  at all; it does NOT rely on it to read actual page CONTENT, which
  does require successful authentication first (also confirmed
  experimentally: iterating page text before authenticate() succeeds
  raises).
- A PDF with an EMPTY user password (freely readable, but with a real,
  different owner password restricting permissions) was confirmed to
  report is_encrypted == False on this library version -- PyMuPDF
  itself treats such a file as "not encrypted" for is_encrypted's
  purposes. This module inherits that same judgment by using
  pdf_engine.is_encrypted() (which reads doc.is_encrypted) as its own
  single source of truth for "does this PDF need unlocking" -- see
  is_source_encrypted() below -- rather than re-deciding the question
  with different logic.
- Saving with `encryption=pymupdf.PDF_ENCRYPT_NONE` (a real, named
  constant read directly off the installed pymupdf module -- not
  invented) on an authenticated Document produces output that is
  confirmed, experimentally, to be genuinely unencrypted: reopening it
  reports is_encrypted == False and needs_pass == 0, and every page
  opens with no password at all.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import pymupdf

import pdf_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class UnlockError(PDFEngineError):
    """Base class for Unlock-specific failures. A PDFEngineError
    subclass, exactly like every other engine's own domain-specific
    error classes (split_engine.PageRangeError,
    remove_pages_engine.PageRangeError,
    organize_engine.OrganizeOrderError, rotate_engine.RotationError,
    protect_engine.PasswordError) -- callers can catch this specific
    class or the general PDFEngineError depending on how much detail
    they need.
    """


class NotEncryptedError(UnlockError):
    """Raised when the selected PDF is not password-protected -- there
    is nothing to unlock. Per the Phase 19 spec: this must never be
    silently treated as a successful no-op copy.
    """


class IncorrectPasswordError(UnlockError):
    """Raised when the supplied password -- including an empty one --
    does not successfully authenticate against the PDF. Its message is
    always a fixed, generic string; it never echoes the password that
    was tried (see unlock_pdf()'s own docstring for the password-
    handling discipline this module follows throughout).
    """


def is_source_encrypted(path: Path) -> bool:
    """True if `path` is currently password-protected, per PyMuPDF's
    own is_encrypted judgment (see this module's docstring for why
    is_encrypted, not needs_pass, is what's authoritative here, and why
    a PDF with only an empty user password is correctly treated as
    "not encrypted"). Thin wrapper around pdf_engine.is_encrypted() --
    not reimplemented -- so Unlock's notion of "encrypted" can never
    silently drift from the rest of the project's.
    """
    return pdf_engine.is_encrypted(path)


def get_source_info(path: Path) -> dict:
    """Return a small dict of metadata for the Unlock workspace to show
    right after import -- same shape as pdf_engine.get_pdf_info(), but
    usable on an ENCRYPTED source, which get_pdf_info() deliberately
    rejects (see pdf_engine.validate_pdf()'s allow_encrypted parameter,
    added in Phase 19 specifically for this).

    Raises PDFEngineError subclasses for a missing/corrupted/directory
    path, exactly like get_pdf_info() does -- the only difference is
    that an encrypted PDF is accepted here rather than rejected, since
    accepting one is the entire point of this tool.
    """
    path = Path(path)
    pdf_engine.validate_pdf(path, allow_encrypted=True)

    size = path.stat().st_size
    page_count = pdf_engine.get_page_count(path)
    encrypted = is_source_encrypted(path)

    return {
        "path": path,
        "name": path.name,
        "size": size,
        "page_count": page_count,
        "is_encrypted": encrypted,
    }


def _require_password(password: str) -> str:
    """Raises IncorrectPasswordError for an empty or whitespace-only
    password. Otherwise returns `password` completely UNCHANGED --
    deliberately NOT stripped, for the same reason
    protect_engine.validate_password() doesn't strip: the password
    must match, character for character, whatever the file was
    originally protected with, and silently trimming it would make a
    password with meaningful whitespace impossible to enter correctly.
    """
    if not password or not password.strip():
        raise IncorrectPasswordError("Enter a password.")
    return password


def unlock_pdf(
    source_path: Path,
    output_path: Path,
    password: str,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new, fully decrypted PDF at `output_path` containing
    every page of `source_path`, unchanged in content, order, count,
    and rotation, with encryption removed entirely.

    - Rejects an empty or whitespace-only `password` via
      _require_password() before anything else runs.
    - Re-validates the source immediately via
      pdf_engine.validate_pdf(source_path, allow_encrypted=True) --
      missing/corrupted/directory sources are rejected exactly like
      every other engine's own inputs, but (unlike every other engine)
      an encrypted source is exactly what this tool expects, not
      something to reject.
    - Raises NotEncryptedError, without ever opening a decrypt
      operation, if the source is not actually password-protected (see
      is_source_encrypted()) -- per the Phase 19 spec, this is never
      silently treated as "nothing to do, just copy the file".
    - Authenticates with `password` via PyMuPDF's own
      Document.authenticate(); raises IncorrectPasswordError
      (`password` itself never appears in that exception's message) if
      it fails -- see this module's docstring for exactly what
      authenticate()'s return value means on the installed library
      version, confirmed experimentally rather than assumed.
    - Never modifies the source file: it is opened fresh, authenticated
      in memory, and the decrypted result is written to `output_path`
      -- a different file. The file on disk at `source_path` is never
      written to, so it remains exactly as protected as it was before
      this call.
    - Saves with `encryption=pymupdf.PDF_ENCRYPT_NONE` -- confirmed
      experimentally (see this module's docstring) to produce a
      genuinely unencrypted file: no password of any kind is needed to
      open it afterward.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Unlocking PDF...", "Saving...") -- never a fabricated
      percentage, and never anything derived from the password.

    Raises UnlockError (a PDFEngineError subclass; either
    NotEncryptedError or IncorrectPasswordError specifically) for the
    cases above. Raises PDFEngineError for any other PDF/filesystem
    failure. No exception raised by this function ever contains
    `password`'s value.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    password = _require_password(password)

    pdf_engine.validate_pdf(source_path, allow_encrypted=True)

    if not is_source_encrypted(source_path):
        raise NotEncryptedError(
            f"'{source_path.name}' is not password-protected."
        )

    if progress_callback:
        progress_callback("Unlocking PDF...")

    try:
        with pymupdf.open(source_path) as doc:
            # doc.authenticate() returns 0 for a failed attempt and a
            # nonzero value for any successful one (user- or owner-
            # level -- see this module's docstring). Only the truthy/
            # falsy distinction is used here; the password itself is
            # never logged or included in any message below.
            if not doc.authenticate(password):
                raise IncorrectPasswordError("Incorrect password.")

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(
                doc, output_path, encryption=pymupdf.PDF_ENCRYPT_NONE,
            )
    except PDFEngineError:
        raise
    except Exception as exc:
        # str(exc) here always comes from PyMuPDF/the filesystem
        # reporting what went wrong with the FILE -- never anything
        # derived from `password`, which is never interpolated into
        # any message this function raises.
        raise PDFEngineError(
            f"Failed to unlock '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
