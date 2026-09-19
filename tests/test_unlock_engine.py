"""
test_unlock_engine.py

Comprehensive tests for unlock_engine.py (Phase 19): encryption
detection (is_source_encrypted, get_source_info -- usable on an
encrypted source, unlike pdf_engine.get_pdf_info()), and the actual
unlock_pdf() engine (real decryption verified via PyMuPDF's own public
API, correct/wrong/empty password behavior, an already-unencrypted
source correctly rejected rather than silently copied, source safety,
output safety, and -- per the Phase 19 security requirements -- that
the password is never present in any output filename, status text,
exception text, or queue-style payload this module produces).

Also encodes, as executable tests, the experimental findings about the
installed PyMuPDF version's actual authenticate()/is_encrypted/
needs_pass behavior documented in unlock_engine.py's own module
docstring -- see the "PyMuPDF API behavior" section below.

No tkinter dependency -- unlock_engine.py is pure logic + file I/O, so
these run without a display.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager
import pdf_engine
import unlock_engine
from unlock_engine import IncorrectPasswordError, NotEncryptedError

USER_PASSWORD = "TestUserPassword123!"
OWNER_PASSWORD = "TestOwnerPassword456!"
WRONG_PASSWORD = "NotTheRightPassword789!"


def _make_plain_pdf(path, pages=1, text_prefix="PAGE "):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


def _make_encrypted_pdf(
    path, pages=3, text_prefix="PAGE ", user_pw=USER_PASSWORD,
    owner_pw=OWNER_PASSWORD, rotate_first_page=True, metadata=None,
):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    if rotate_first_page and pages > 0:
        doc[0].set_rotation(90)
    if metadata:
        doc.set_metadata(metadata)
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=user_pw,
        owner_pw=owner_pw,
    )
    doc.close()


# ---------------------------------------------------------------------------
# PyMuPDF API behavior -- experimentally confirmed, encoded as tests
# ---------------------------------------------------------------------------

def test_pymupdf_is_encrypted_true_before_authentication(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)

    doc = pymupdf.open(protected)
    try:
        assert doc.is_encrypted
        assert doc.needs_pass
    finally:
        doc.close()


def test_pymupdf_is_encrypted_becomes_false_after_successful_authenticate(tmp_path):
    """Confirms the specific, easy-to-get-wrong behavior documented in
    unlock_engine.py's module docstring: is_encrypted (not needs_pass)
    is the reliable "still locked" signal after authenticate().
    """
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)

    doc = pymupdf.open(protected)
    try:
        result = doc.authenticate(USER_PASSWORD)
        assert result  # nonzero == success
        assert doc.is_encrypted is False
    finally:
        doc.close()


def test_pymupdf_authenticate_returns_zero_for_wrong_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)

    doc = pymupdf.open(protected)
    try:
        assert doc.authenticate(WRONG_PASSWORD) == 0
    finally:
        doc.close()


def test_pymupdf_authenticate_accepts_either_user_or_owner_password(tmp_path):
    """Confirms unlock_pdf() is right not to distinguish which nonzero
    result it got -- both the user and owner password must
    authenticate successfully on this library version.
    """
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)

    doc_user = pymupdf.open(protected)
    try:
        assert doc_user.authenticate(USER_PASSWORD)
    finally:
        doc_user.close()

    doc_owner = pymupdf.open(protected)
    try:
        assert doc_owner.authenticate(OWNER_PASSWORD)
    finally:
        doc_owner.close()


def test_pymupdf_page_count_readable_before_authentication(tmp_path):
    """get_source_info() relies on this to show a page count before any
    password has been entered.
    """
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=5)

    doc = pymupdf.open(protected)
    try:
        assert doc.page_count == 5
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# is_source_encrypted() / get_source_info()
# ---------------------------------------------------------------------------

def test_is_source_encrypted_true_for_protected_pdf(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)

    assert unlock_engine.is_source_encrypted(protected) is True


def test_is_source_encrypted_false_for_plain_pdf(tmp_path):
    plain = tmp_path / "plain.pdf"
    _make_plain_pdf(plain)

    assert unlock_engine.is_source_encrypted(plain) is False


def test_get_source_info_on_encrypted_pdf(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=4)

    info = unlock_engine.get_source_info(protected)

    assert info["name"] == "protected.pdf"
    assert info["page_count"] == 4
    assert info["is_encrypted"] is True


def test_get_source_info_on_plain_pdf(tmp_path):
    plain = tmp_path / "plain.pdf"
    _make_plain_pdf(plain, pages=2)

    info = unlock_engine.get_source_info(plain)

    assert info["page_count"] == 2
    assert info["is_encrypted"] is False


def test_get_source_info_missing_file_raises(tmp_path):
    with pytest.raises(pdf_engine.InvalidPDFError):
        unlock_engine.get_source_info(tmp_path / "does_not_exist.pdf")


def test_get_source_info_corrupt_file_raises(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)

    with pytest.raises(pdf_engine.InvalidPDFError):
        unlock_engine.get_source_info(corrupt)


def test_get_pdf_info_still_rejects_encrypted_pdfs():
    """Regression guard: the allow_encrypted parameter added to
    pdf_engine.validate_pdf() for Phase 19 must default to False, so
    every OTHER engine's existing behavior (rejecting encrypted
    sources) is completely unaffected.
    """
    import inspect

    sig = inspect.signature(pdf_engine.validate_pdf)
    assert sig.parameters["allow_encrypted"].default is False


# ---------------------------------------------------------------------------
# unlock_pdf() -- VALID INPUT / decryption correctness
# ---------------------------------------------------------------------------

def test_correct_password_produces_decrypted_output(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc.is_encrypted is False
        assert doc.needs_pass == 0
    finally:
        doc.close()


def test_output_opens_without_a_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    # No authenticate() call at all -- must simply open and be usable.
    doc = pymupdf.open(output_path)
    try:
        text = doc[0].get_text()
        assert isinstance(text, str)
    finally:
        doc.close()


def test_output_is_not_encrypted(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert unlock_engine.is_source_encrypted(output_path) is False


def test_owner_password_also_successfully_unlocks(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, OWNER_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc.is_encrypted is False
    finally:
        doc.close()


def test_page_count_preserved(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=6)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_page_order_and_content_preserved(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=5, text_prefix="PAGE ")
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        texts = [doc[i].get_text().strip() for i in range(doc.page_count)]
        assert texts == ["PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5"]
    finally:
        doc.close()


def test_page_rotation_preserved(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=3, rotate_first_page=True)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc[0].rotation == 90
        assert doc[1].rotation == 0
    finally:
        doc.close()


def test_metadata_preserved_where_practical(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(
        protected, pages=1, metadata={"title": "MyTitle", "author": "MyAuthor"},
    )
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    doc = pymupdf.open(output_path)
    try:
        assert doc.metadata.get("title") == "MyTitle"
        assert doc.metadata.get("author") == "MyAuthor"
    finally:
        doc.close()


def test_repeated_unlock_operations_are_independent(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=2)

    out1 = tmp_path / "out1.pdf"
    out2 = tmp_path / "out2.pdf"
    unlock_engine.unlock_pdf(protected, out1, USER_PASSWORD)
    unlock_engine.unlock_pdf(protected, out2, USER_PASSWORD)

    for out in (out1, out2):
        doc = pymupdf.open(out)
        try:
            assert doc.is_encrypted is False
            assert doc.page_count == 2
        finally:
            doc.close()

    # Source must still be encrypted after two separate unlock runs.
    assert unlock_engine.is_source_encrypted(protected) is True


# ---------------------------------------------------------------------------
# unlock_pdf() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_wrong_password_rejected(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(IncorrectPasswordError):
        unlock_engine.unlock_pdf(protected, output_path, WRONG_PASSWORD)

    assert not output_path.exists()


def test_empty_password_rejected(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(IncorrectPasswordError):
        unlock_engine.unlock_pdf(protected, output_path, "")

    assert not output_path.exists()


def test_whitespace_only_password_rejected(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(IncorrectPasswordError):
        unlock_engine.unlock_pdf(protected, output_path, "   ")

    assert not output_path.exists()


def test_already_unencrypted_source_is_rejected(tmp_path):
    plain = tmp_path / "plain.pdf"
    _make_plain_pdf(plain)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(NotEncryptedError):
        unlock_engine.unlock_pdf(plain, output_path, "any-password")

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        unlock_engine.unlock_pdf(missing, output_path, USER_PASSWORD)

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        unlock_engine.unlock_pdf(corrupt, output_path, USER_PASSWORD)


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        unlock_engine.unlock_pdf(a_directory, output_path, USER_PASSWORD)


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert not output_path.exists()


def test_unexpected_exception_is_translated_to_pdf_engine_error(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with patch.object(
        pdf_engine, "_atomic_save_pdf", side_effect=RuntimeError("totally unexpected"),
    ):
        with pytest.raises(pdf_engine.PDFEngineError):
            unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)


# ---------------------------------------------------------------------------
# SOURCE SAFETY
# ---------------------------------------------------------------------------

def test_source_exists_after_successful_unlock(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert protected.exists()


def test_source_bytes_unchanged_after_successful_unlock(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    original_bytes = protected.read_bytes()
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert protected.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_wrong_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    original_bytes = protected.read_bytes()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(IncorrectPasswordError):
        unlock_engine.unlock_pdf(protected, output_path, WRONG_PASSWORD)

    assert protected.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_filesystem_failure(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    original_bytes = protected.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert protected.read_bytes() == original_bytes


def test_source_page_count_unchanged_after_unlock(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=7)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    doc = pymupdf.open(protected)
    try:
        assert doc.page_count == 7
    finally:
        doc.close()


def test_source_remains_encrypted_after_successful_unlock(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert unlock_engine.is_source_encrypted(protected) is True


def test_output_is_a_separate_file_from_source(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    result_path = unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert result_path != protected
    assert result_path.exists()
    assert protected.exists()
    assert result_path.read_bytes() != protected.read_bytes()


def test_no_partial_output_remains_after_failure(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# OUTPUT COLLISION / NAMING (file_manager.generate_unlocked_output_path)
# ---------------------------------------------------------------------------

def test_generate_unlocked_output_path_default_name(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")

    result = file_manager.generate_unlocked_output_path(source, tmp_path)

    assert result == tmp_path / "document_unlocked.pdf"


def test_generate_unlocked_output_path_collision_safe(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")
    (tmp_path / "document_unlocked.pdf").write_bytes(b"already exists")

    result = file_manager.generate_unlocked_output_path(source, tmp_path)

    assert result == tmp_path / "document_unlocked (1).pdf"


def test_generate_unlocked_output_path_multiple_collisions(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")
    (tmp_path / "document_unlocked.pdf").write_bytes(b"x")
    (tmp_path / "document_unlocked (1).pdf").write_bytes(b"x")

    result = file_manager.generate_unlocked_output_path(source, tmp_path)

    assert result == tmp_path / "document_unlocked (2).pdf"


def test_generate_unlocked_output_path_never_overwrites(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")
    existing = tmp_path / "document_unlocked.pdf"
    existing.write_bytes(b"do not touch")

    result = file_manager.generate_unlocked_output_path(source, tmp_path)

    assert result != existing
    assert existing.read_bytes() == b"do not touch"


def test_unlock_pdf_end_to_end_with_generated_output_path(tmp_path):
    protected = tmp_path / "document.pdf"
    _make_encrypted_pdf(protected, pages=2)

    output_path = file_manager.generate_unlocked_output_path(protected, tmp_path)
    unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert output_path.name == "document_unlocked.pdf"
    doc = pymupdf.open(output_path)
    try:
        assert doc.is_encrypted is False
        assert doc.page_count == 2
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# OUTPUT SAFETY / ATOMIC SAVE
# ---------------------------------------------------------------------------

def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert not output_path.exists()


def test_progress_callback_reports_unlocking_and_saving(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    messages = []
    unlock_engine.unlock_pdf(
        protected, output_path, USER_PASSWORD, progress_callback=messages.append,
    )

    assert any("unlock" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_progress_callback_messages_never_contain_the_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    messages = []
    unlock_engine.unlock_pdf(
        protected, output_path, USER_PASSWORD, progress_callback=messages.append,
    )

    assert all(USER_PASSWORD not in m for m in messages)


# ---------------------------------------------------------------------------
# SECURITY: the password must never leak
# ---------------------------------------------------------------------------

def test_output_path_never_contains_the_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    result_path = unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)

    assert USER_PASSWORD not in str(result_path)
    assert USER_PASSWORD not in result_path.name


def test_wrong_password_error_never_contains_the_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(IncorrectPasswordError) as exc_info:
        unlock_engine.unlock_pdf(protected, output_path, WRONG_PASSWORD)

    assert WRONG_PASSWORD not in str(exc_info.value)


def test_error_messages_never_contain_the_password(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        try:
            unlock_engine.unlock_pdf(protected, output_path, USER_PASSWORD)
        except pdf_engine.PDFEngineError as exc:
            assert USER_PASSWORD not in str(exc)
            assert "Traceback" not in str(exc)


def test_missing_source_error_never_contains_the_password(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    try:
        unlock_engine.unlock_pdf(missing, output_path, USER_PASSWORD)
    except pdf_engine.PDFEngineError as exc:
        assert USER_PASSWORD not in str(exc)


# ---------------------------------------------------------------------------
# WORKER-THREAD SAFETY
# ---------------------------------------------------------------------------

def test_engine_is_safe_to_call_from_a_background_thread(tmp_path):
    protected = tmp_path / "protected.pdf"
    _make_encrypted_pdf(protected, pages=3, text_prefix="PAGE ")
    output_path = tmp_path / "output.pdf"

    result = {}

    def worker():
        try:
            result["path"] = unlock_engine.unlock_pdf(
                protected, output_path, USER_PASSWORD,
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
        assert doc.is_encrypted is False
        assert doc.page_count == 3
    finally:
        doc.close()
