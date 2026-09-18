"""
protect_engine.py

Phase 18: Protect PDF processing -- password-protecting a PDF using
PyMuPDF's built-in encryption support, leaving the source completely
untouched. Nothing in this file imports tkinter.

Like split_engine.py, remove_pages_engine.py, extract_engine.py,
organize_engine.py, and rotate_engine.py, this module reuses
pdf_engine's existing validation and atomic-save machinery rather than
creating a second output-saving system: pdf_engine.validate_pdf() still
does the actual PDF-safety checks, and pdf_engine._atomic_save_pdf()
still does the crash-safe write -- it forwards arbitrary **save_kwargs
straight to PyMuPDF's own Document.save(), which is already how it
supports every other engine's needs, so encryption/permission/password
keyword arguments pass through it with no changes required there at
all.

ENCRYPTION
==========
Inspected the installed library before choosing anything (PyMuPDF
1.28.2, `import pymupdf`). Document.save() accepts an `encryption=`
argument with these options: PDF_ENCRYPT_NONE, PDF_ENCRYPT_RC4_40,
PDF_ENCRYPT_RC4_128, PDF_ENCRYPT_AES_128, PDF_ENCRYPT_AES_256. RC4 (both
widths) and plain AES-128 are legacy/weaker options kept only for
compatibility with old PDF readers; this module always uses
PDF_ENCRYPT_AES_256 (see ENCRYPTION_METHOD below) -- the strongest,
most modern option the installed library exposes. Nothing here invents
an encryption constant: every value referenced is read directly off the
`pymupdf` module, so if a future library version renames or removes one
this fails loudly (AttributeError) rather than silently using the wrong
number.

PASSWORD MODEL
===============
Only ONE password is ever collected from the user: this becomes the PDF
"user password" (PyMuPDF's `user_pw`) -- the password required to OPEN
the document at all. That is the whole password model the Phase 18 spec
asks for; no owner-password complexity is exposed to the user.

PyMuPDF's encryption model additionally supports a separate "owner
password" (`owner_pw`) that can override permission restrictions
without needing the user password. This project's Protect tool does not
implement or expose any "remove protection" / "change permissions"
workflow that would ever need to reproduce that password later, so
there is no legitimate reason to ask the user for a second password, or
to derive the owner password FROM the user password (which would make
it guessable and pointlessly weaken the file to a single known secret
in two disguises). Instead, protect_pdf() below generates a fresh,
cryptographically random owner password with Python's `secrets` module
(not the `random` module, which is not suitable for security purposes)
for every call, uses it only to satisfy PyMuPDF's API, and discards it
the moment the function returns -- it is never returned, logged, stored,
or included in any error message. Anyone who doesn't know the user
password cannot open the document at all, which is the actual
protection this tool promises; the random owner password only matters
for a workflow (editing permissions without the user password) this
tool doesn't offer, so its own secrecy is not user-facing -- it exists
purely because PyMuPDF's API requires *some* owner password to be set
before permission restrictions are meaningful, and a random one is
strictly safer than a predictable or empty one.

The user's own password is held only in local variables for the
duration of validate_password()/protect_pdf()'s own execution, is
passed straight into PyMuPDF's doc.save(), and is never written to a
log, an exception message, a filename, or a return value -- see
validate_password() and protect_pdf() below for exactly where it
travels.

PERMISSIONS
===========
Inspected before implementing: the installed PyMuPDF exposes
PDF_PERM_PRINT, PDF_PERM_PRINT_HQ, PDF_PERM_MODIFY, PDF_PERM_COPY,
PDF_PERM_ANNOTATE, PDF_PERM_FORM, PDF_PERM_ACCESSIBILITY, and
PDF_PERM_ASSEMBLE, and these are genuinely enforced by conforming PDF
readers against the encrypted output (this module's own tests verify
the output actually requires a password and that the wrong password is
rejected, using only PyMuPDF's public API -- see
tests/test_protect_engine.py). Per the Phase 18 spec's "keep the UI
simple" guidance, only three of these are exposed as user-facing on/off
choices -- printing, copying/extraction, and modifying -- via
build_permissions() below; the rest (accessibility/screen-reader
access, annotations, form fields, document assembly, and high-quality
printing) are always granted, since restricting them has little
practical value for a "give this file a password" tool and would only
add UI complexity. DEFAULT_PERMISSIONS grants everything, matching
PyMuPDF's own doc.save() default (permissions=4095) -- i.e. by default
the password is required to open the file at all, but the file behaves
like an unrestricted PDF once opened, which is "preserve the existing
default PDF permissions behavior" from the spec.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Callable, Optional

import pymupdf

import pdf_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class PasswordError(PDFEngineError):
    """Raised for a missing/whitespace-only password, or any other
    password-related validation failure. A PDFEngineError subclass,
    exactly like every other engine's own validation-error class.
    Its message must never contain the password itself -- and never
    does, since every raise site below uses a fixed, generic string.
    """


#: PyMuPDF's modern, strong encryption mode -- see the module
#: docstring's "ENCRYPTION" section above for why this (not RC4 or
#: plain AES-128) is what protect_pdf() always uses.
ENCRYPTION_METHOD = pymupdf.PDF_ENCRYPT_AES_256

#: The three permission flags exposed as user-facing on/off choices --
#: see build_permissions() and the module docstring's "PERMISSIONS"
#: section above.
PERM_PRINT = pymupdf.PDF_PERM_PRINT
PERM_COPY = pymupdf.PDF_PERM_COPY
PERM_MODIFY = pymupdf.PDF_PERM_MODIFY

#: Permissions always granted regardless of the user's choices --
#: every bit PyMuPDF's own doc.save() default (permissions=4095, i.e.
#: all twelve permission bits defined by the PDF spec) grants EXCEPT
#: the three above that are exposed as individual UI toggles. Computed
#: by masking the toggleable bits out of the literal default (rather
#: than reassembling it by hand from only the currently-named
#: PDF_PERM_* constants) so this can never silently drift from
#: PyMuPDF's actual default if a future library version adds more
#: named permission flags -- see the module docstring's "PERMISSIONS"
#: section for why these particular three are the ones exposed.
_ALWAYS_GRANTED_PERMISSIONS = 4095 & ~(PERM_PRINT | PERM_COPY | PERM_MODIFY)

#: Every permission granted -- PyMuPDF's own doc.save() default
#: (permissions=4095), used verbatim whenever protect_pdf() is called
#: without an explicit `permissions` argument. Always exactly equal to
#: build_permissions(True, True, True) -- see _ALWAYS_GRANTED_PERMISSIONS
#: above for why.
DEFAULT_PERMISSIONS = 4095


#: PDF's classic security handler (still used for the password padding/
#: derivation scheme even under AES) caps both the user and owner
#: password at 40 bytes; PyMuPDF enforces this in doc.save() with a
#: raw ValueError. validate_password() below checks it explicitly so
#: the user gets the project's normal human-readable PasswordError
#: instead of an unhandled library exception.
MAX_PASSWORD_LENGTH = 40


def validate_password(password: str) -> str:
    """Raises PasswordError for an empty or whitespace-only password,
    or one longer than MAX_PASSWORD_LENGTH characters (PyMuPDF's own
    hard limit -- see MAX_PASSWORD_LENGTH above). Otherwise returns
    `password` completely UNCHANGED -- deliberately NOT stripped: a
    password containing meaningful leading/trailing whitespace the
    user genuinely typed is preserved verbatim, since silently
    altering it would mean the password actually applied to the file
    differs from the one the user believes they set (and, critically,
    from the one they'd type again later to open it).
    """
    if not password or not password.strip():
        raise PasswordError("Enter a password.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password is too long -- please use {MAX_PASSWORD_LENGTH} "
            f"characters or fewer."
        )
    return password


def build_permissions(
    allow_printing: bool = True,
    allow_copying: bool = True,
    allow_modifying: bool = True,
) -> int:
    """Builds the PyMuPDF permissions bitmask protect_pdf() expects,
    from three simple, user-facing on/off choices. Always includes the
    baseline in _ALWAYS_GRANTED_PERMISSIONS -- see the module
    docstring's "PERMISSIONS" section.
    """
    permissions = _ALWAYS_GRANTED_PERMISSIONS
    if allow_printing:
        permissions |= PERM_PRINT
    if allow_copying:
        permissions |= PERM_COPY
    if allow_modifying:
        permissions |= PERM_MODIFY
    return permissions


def protect_pdf(
    source_path: Path,
    output_path: Path,
    user_password: str,
    permissions: Optional[int] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new, password-protected PDF at `output_path` containing
    every page of `source_path`, unchanged in content, order, and
    count, encrypted with AES-256 such that `user_password` is required
    to open it.

    - Rejects an empty or whitespace-only `user_password` via
      validate_password() before anything else runs.
    - Re-validates the source immediately (missing/corrupted/already-
      encrypted/directory) via pdf_engine.validate_pdf(), exactly like
      every other engine in this project does for its own inputs. In
      particular, a source that is ALREADY password-protected is
      rejected here (as EncryptedPDFError) rather than silently
      re-protected or silently overwriting its existing protection --
      this project has no "remove protection" step yet (that's a
      future Unlock tool, explicitly out of scope for Phase 18), so
      there is no safe, defined meaning for "protect an already-
      protected file" without first unlocking it.
    - Never modifies the source file: it is opened fresh, and the
      encrypted result is written to `output_path` -- a different
      file. The file on disk at `source_path` is never written to, so
      its bytes, page count, and page content are all untouched.
    - `permissions`, if omitted, defaults to DEFAULT_PERMISSIONS (every
      permission granted -- see the module docstring). Build a custom
      value with build_permissions() for anything more restrictive.
    - Writes the output atomically via pdf_engine's existing
      _atomic_save_pdf() -- never overwrites `output_path` partially,
      and never touches it at all if anything fails first.
    - progress_callback, if given, is called with short, real status
      messages ("Protecting PDF...", "Saving...") -- never a
      fabricated percentage, and never anything derived from the
      password.
    - See the module docstring's "PASSWORD MODEL" section for exactly
      how the (randomly generated, single-use, never-exposed) owner
      password is handled.

    Raises PasswordError (a PDFEngineError subclass) for invalid
    password input. Raises PDFEngineError for any other PDF/filesystem
    failure. No exception raised by this function ever contains
    `user_password`'s value.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    user_password = validate_password(user_password)

    if permissions is None:
        permissions = DEFAULT_PERMISSIONS

    pdf_engine.validate_pdf(source_path)

    if progress_callback:
        progress_callback("Protecting PDF...")

    # A random, single-use owner password -- see the module docstring's
    # "PASSWORD MODEL" section for why this is generated here rather
    # than asked of the user or derived from user_password.
    # secrets.token_urlsafe() is drawn from the OS's cryptographically
    # secure random source (unlike the `random` module, which must
    # never be used for anything security-related). PyMuPDF's save()
    # rejects any password longer than 40 characters, so 24 bytes is
    # used here (-> a 32-character token) rather than something larger:
    # comfortably under that limit while still far exceeding the
    # entropy this value's role actually requires.
    owner_password = secrets.token_urlsafe(24)

    try:
        with pymupdf.open(source_path) as doc:
            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(
                doc,
                output_path,
                encryption=ENCRYPTION_METHOD,
                owner_pw=owner_password,
                user_pw=user_password,
                permissions=permissions,
            )
    except PDFEngineError:
        raise
    except Exception as exc:
        # str(exc) here always comes from PyMuPDF/the filesystem
        # reporting what went wrong with the FILE (e.g. a read/write
        # failure) -- never anything derived from user_password or
        # owner_password, neither of which is ever interpolated into
        # any message this function raises.
        raise PDFEngineError(
            f"Failed to protect '{source_path.name}': {exc}"
        ) from exc
    finally:
        # Drops this function's own reference to the generated owner
        # password as soon as it's no longer needed. Python cannot
        # guarantee immediate, certain memory zeroing for an immutable
        # str (the interpreter may have made internal copies), but this
        # at least ensures protect_pdf() itself does not keep it
        # reachable via a local variable any longer than the save()
        # call required.
        del owner_password

    if progress_callback:
        progress_callback("Done.")

    return output_path
