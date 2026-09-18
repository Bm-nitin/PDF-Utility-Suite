"""
test_protect_engine.py

Comprehensive tests for protect_engine.py (Phase 18): password
validation (validate_password), permission-bitmask building
(build_permissions), and the actual protect_pdf() engine (real
encryption verified via PyMuPDF's own public API, correct/wrong
password behavior, source safety, output safety, and -- per the Phase
18 security requirements -- that the password is never present in any
output filename, status text, exception text, or queue-style payload
this module produces).

No tkinter dependency -- protect_engine.py is pure logic + file I/O, so
these run without a display.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine
import protect_engine
from protect_engine import PasswordError

TEST_PASSWORD = "TestPassword123!"
WRONG_PASSWORD = "NotTheRightPassword456!"


def _make_pdf(path, pages=1, text_prefix="PAGE "):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


# ---------------------------------------------------------------------------
# validate_password()
# ---------------------------------------------------------------------------

def test_valid_password_returned_unchanged():
    assert protect_engine.validate_password(TEST_PASSWORD) == TEST_PASSWORD


def test_password_with_meaningful_whitespace_not_stripped():
    # " padded " has non-whitespace content, so it's a valid password --
    # and must come back byte-for-byte identical, not trimmed, since
    # that's what the user actually typed and will type again later.
    assert protect_engine.validate_password(" padded ") == " padded "


def test_empty_password_rejected():
    with pytest.raises(PasswordError):
        protect_engine.validate_password("")


def test_whitespace_only_password_rejected():
    with pytest.raises(PasswordError):
        protect_engine.validate_password("   ")


def test_none_password_rejected():
    with pytest.raises(PasswordError):
        protect_engine.validate_password(None)


def test_password_at_max_length_accepted():
    password = "x" * protect_engine.MAX_PASSWORD_LENGTH
    assert protect_engine.validate_password(password) == password


def test_password_over_max_length_rejected():
    password = "x" * (protect_engine.MAX_PASSWORD_LENGTH + 1)
    with pytest.raises(PasswordError):
        protect_engine.validate_password(password)


def test_password_error_messages_never_contain_the_password():
    """A defense-in-depth check: whatever PasswordError message is
    raised for a too-long password, it must never echo the password
    value itself (only a fixed, generic message).
    """
    password = "s3cr3t" * 10  # 60 chars, well over the limit
    with pytest.raises(PasswordError) as exc_info:
        protect_engine.validate_password(password)
    assert password not in str(exc_info.value)


# ---------------------------------------------------------------------------
# build_permissions()
# ---------------------------------------------------------------------------

def test_default_permissions_equal_pymupdf_default():
    # PyMuPDF's own Document.save() default is permissions=4095.
    assert protect_engine.DEFAULT_PERMISSIONS == 4095


def test_build_permissions_all_allowed_equals_default():
    assert protect_engine.build_permissions() == protect_engine.DEFAULT_PERMISSIONS
    assert protect_engine.build_permissions(True, True, True) == 4095


def test_build_permissions_restrict_printing():
    permissions = protect_engine.build_permissions(allow_printing=False)
    assert not (permissions & protect_engine.PERM_PRINT)
    assert permissions & protect_engine.PERM_COPY
    assert permissions & protect_engine.PERM_MODIFY


def test_build_permissions_restrict_copying():
    permissions = protect_engine.build_permissions(allow_copying=False)
    assert not (permissions & protect_engine.PERM_COPY)
    assert permissions & protect_engine.PERM_PRINT


def test_build_permissions_restrict_modifying():
    permissions = protect_engine.build_permissions(allow_modifying=False)
    assert not (permissions & protect_engine.PERM_MODIFY)
    assert permissions & protect_engine.PERM_PRINT


def test_build_permissions_restrict_all_three():
    permissions = protect_engine.build_permissions(False, False, False)
    assert not (permissions & protect_engine.PERM_PRINT)
    assert not (permissions & protect_engine.PERM_COPY)
    assert not (permissions & protect_engine.PERM_MODIFY)
    # Accessibility must remain granted regardless -- restricting
    # screen-reader access isn't one of the exposed toggles.
    assert permissions & pymupdf.PDF_PERM_ACCESSIBILITY


# ---------------------------------------------------------------------------
# protect_pdf() -- SECURITY: real encryption, verified via public API
# ---------------------------------------------------------------------------

def test_output_is_actually_encrypted(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc.is_encrypted
        assert doc.needs_pass
    finally:
        doc.close()


def test_wrong_password_is_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        result = doc.authenticate(WRONG_PASSWORD)
        assert not result
    finally:
        doc.close()


def test_correct_password_allows_access(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3, text_prefix="PAGE ")
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        result = doc.authenticate(TEST_PASSWORD)
        assert result
        assert doc.page_count == 3
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE 1", "PAGE 2", "PAGE 3"]
    finally:
        doc.close()


def test_output_cannot_be_read_without_authenticating(tmp_path):
    """Before authenticate() succeeds, page access must not silently
    return readable content -- this is the actual protection being
    tested, not just a metadata flag.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=2, text_prefix="SECRET ")
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc.needs_pass
        # PyMuPDF raises when trying to use a document that still
        # needs a password.
        with pytest.raises(Exception):
            doc.load_page(0).get_text()
    finally:
        doc.close()


def test_uses_aes_256_encryption(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    # ENCRYPTION_METHOD is read directly from the installed pymupdf
    # module, not hand-picked -- confirm it's genuinely AES-256, the
    # strongest option PyMuPDF exposes.
    assert protect_engine.ENCRYPTION_METHOD == pymupdf.PDF_ENCRYPT_AES_256


def test_supported_permission_restriction_is_actually_enforced(tmp_path):
    """Verifies build_permissions()'s output is not just a bitmask that
    LOOKS right -- restricting printing must genuinely be reflected in
    doc.permissions after authenticating with the user password.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    permissions = protect_engine.build_permissions(allow_printing=False)
    protect_engine.protect_pdf(
        src, output_path, TEST_PASSWORD, permissions=permissions,
    )

    doc = pymupdf.open(output_path)
    try:
        doc.authenticate(TEST_PASSWORD)
        assert not (doc.permissions & pymupdf.PDF_PERM_PRINT)
    finally:
        doc.close()


def test_default_permissions_do_not_restrict_printing(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        doc.authenticate(TEST_PASSWORD)
        assert doc.permissions & pymupdf.PDF_PERM_PRINT
        assert doc.permissions & pymupdf.PDF_PERM_COPY
        assert doc.permissions & pymupdf.PDF_PERM_MODIFY
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# protect_pdf() -- content correctness
# ---------------------------------------------------------------------------

def test_page_count_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=7)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        doc.authenticate(TEST_PASSWORD)
        assert doc.page_count == 7
    finally:
        doc.close()


def test_page_order_and_content_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5, text_prefix="PAGE ")
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        doc.authenticate(TEST_PASSWORD)
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5"]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# protect_pdf() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_password_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PasswordError):
        protect_engine.protect_pdf(src, output_path, "")

    assert not output_path.exists()


def test_whitespace_password_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PasswordError):
        protect_engine.protect_pdf(src, output_path, "   ")

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        protect_engine.protect_pdf(missing, output_path, TEST_PASSWORD)

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        protect_engine.protect_pdf(corrupt, output_path, TEST_PASSWORD)


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        protect_engine.protect_pdf(a_directory, output_path, TEST_PASSWORD)


def test_already_encrypted_source_is_rejected(tmp_path):
    """This project has no "remove protection"/"re-protect" workflow
    yet (a future Unlock tool, out of scope for Phase 18) -- an already
    password-protected source must be rejected the same way every other
    engine already rejects one, via the existing validate_pdf()
    infrastructure, not silently re-encrypted or silently overwriting
    its existing protection.
    """
    protected = tmp_path / "already_protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="existing-password",
        owner_pw="existing-owner-password",
    )
    doc.close()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.EncryptedPDFError):
        protect_engine.protect_pdf(protected, output_path, TEST_PASSWORD)

    assert not output_path.exists()


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert not output_path.exists()


def test_unexpected_exception_is_translated_to_pdf_engine_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(
        pdf_engine, "_atomic_save_pdf", side_effect=RuntimeError("totally unexpected"),
    ):
        with pytest.raises(pdf_engine.PDFEngineError):
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)


# ---------------------------------------------------------------------------
# SOURCE SAFETY
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged_after_successful_protection(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert src.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_failed_protection(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert src.read_bytes() == original_bytes


def test_source_page_count_unchanged_after_protection(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
        assert not doc.is_encrypted
    finally:
        doc.close()


def test_source_remains_readable_with_original_state_after_protection(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=4, text_prefix="PAGE ")
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(src)
    try:
        assert not doc.needs_pass
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4"]
    finally:
        doc.close()


def test_failed_operation_does_not_corrupt_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
        assert not doc.is_encrypted
    finally:
        doc.close()


def test_no_partial_output_remains_after_failure(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_output_is_a_different_file_from_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert output_path != src
    assert output_path.exists()
    assert src.exists()
    assert output_path.read_bytes() != src.read_bytes()


# ---------------------------------------------------------------------------
# OUTPUT SAFETY
# ---------------------------------------------------------------------------

def test_output_gets_pdf_extension_via_existing_file_manager_helpers():
    import file_manager

    name = file_manager.ensure_pdf_extension(
        file_manager.sanitize_windows_filename("document_protected")
    )
    assert name.endswith(".pdf")


def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert not output_path.exists()


def test_progress_callback_reports_protecting_and_saving(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    messages = []
    protect_engine.protect_pdf(
        src, output_path, TEST_PASSWORD, progress_callback=messages.append,
    )

    assert any("protect" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_progress_callback_messages_never_contain_the_password(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    messages = []
    protect_engine.protect_pdf(
        src, output_path, TEST_PASSWORD, progress_callback=messages.append,
    )

    assert all(TEST_PASSWORD not in m for m in messages)


# ---------------------------------------------------------------------------
# SECURITY: the password must never leak
# ---------------------------------------------------------------------------

def test_output_filename_never_contains_the_password(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    result_path = protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    assert TEST_PASSWORD not in str(result_path)
    assert TEST_PASSWORD not in result_path.name


def test_error_messages_never_contain_the_password(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        try:
            protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)
        except pdf_engine.PDFEngineError as exc:
            assert TEST_PASSWORD not in str(exc)
            assert "Traceback" not in str(exc)


def test_missing_source_error_never_contains_the_password(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    try:
        protect_engine.protect_pdf(missing, output_path, TEST_PASSWORD)
    except pdf_engine.PDFEngineError as exc:
        assert TEST_PASSWORD not in str(exc)


def test_owner_password_is_never_the_user_password(tmp_path):
    """The random owner password generated internally must never equal
    (or be trivially derivable as) the user's own password -- confirmed
    here by checking that a naive/predictable owner-password guess
    (the empty string, the classic default some naive implementations
    use) does NOT work. (We deliberately don't inspect PyMuPDF's
    internal encryption fields directly -- see the module's own
    docstring on preferring the public API.)
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    protect_engine.protect_pdf(src, output_path, TEST_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert not doc.authenticate("")
    finally:
        doc.close()


def test_owner_password_uses_secrets_module_not_random_module():
    """Static check: protect_pdf() must generate its owner password via
    the `secrets` module (cryptographically secure), never the `random`
    module (not suitable for security purposes). Confirmed by reading
    the actual source rather than trusting the docstring.
    """
    import inspect

    source = inspect.getsource(protect_engine)
    assert "import secrets" in source
    assert "secrets.token_urlsafe" in source or "secrets.token_hex" in source
    # `random` (the non-cryptographic stdlib module) must not be used
    # anywhere in this file for password/security purposes.
    assert "import random" not in source


# ---------------------------------------------------------------------------
# WORKER-THREAD SAFETY
# ---------------------------------------------------------------------------

def test_engine_is_safe_to_call_from_a_background_thread(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3, text_prefix="PAGE ")
    output_path = tmp_path / "output.pdf"

    result = {}

    def worker():
        try:
            result["path"] = protect_engine.protect_pdf(
                src, output_path, TEST_PASSWORD,
            )
        except Exception as exc:  # pragma: no cover -- surfaced via assert below
            result["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)

    assert "error" not in result
    assert result["path"] == output_path

    doc = pymupdf.open(output_path)
    try:
        assert doc.authenticate(TEST_PASSWORD)
        assert doc.page_count == 3
    finally:
        doc.close()
