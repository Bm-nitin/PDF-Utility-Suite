"""
test_rotate_engine.py

Comprehensive tests for rotate_engine.py (Phase 17): page-selection
resolution (resolve_pages_to_rotate -- built on split_engine's range
parser, sorted/de-duplicated like remove_pages_engine's own resolver),
direction/angle resolution (resolve_clockwise_degrees), and the actual
rotate_pages_in_pdf() engine (cumulative rotation, normalization, valid
input, invalid input, source safety, output safety).

No tkinter dependency -- rotate_engine.py is pure logic + file I/O, so
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
import rotate_engine
from rotate_engine import RotationError
from split_engine import PageRangeError


def _make_pdf(path, pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()


def _rotations(path):
    doc = pymupdf.open(path)
    try:
        return [doc[i].rotation for i in range(doc.page_count)]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# resolve_pages_to_rotate() -- selection parsing (reused parser)
# ---------------------------------------------------------------------------

def test_resolve_single_page():
    assert rotate_engine.resolve_pages_to_rotate("3", 10) == [2]


def test_resolve_multiple_pages():
    assert rotate_engine.resolve_pages_to_rotate("2,4,6", 10) == [1, 3, 5]


def test_resolve_range():
    assert rotate_engine.resolve_pages_to_rotate("1-3", 10) == [0, 1, 2]


def test_resolve_multiple_ranges():
    assert rotate_engine.resolve_pages_to_rotate("1-3,5,7-9", 10) == [
        0, 1, 2, 4, 6, 7, 8,
    ]


def test_resolve_whitespace_tolerated():
    assert rotate_engine.resolve_pages_to_rotate(" 1, 3, 5-7 ", 10) == [
        0, 2, 4, 5, 6,
    ]


def test_resolve_overlapping_ranges_deduplicated():
    """Rotate must not rotate the same page twice just because it
    appeared in two overlapping range tokens (e.g. "1-3,2-4") -- see
    the Phase 17 spec's explicit warning against this.
    """
    assert rotate_engine.resolve_pages_to_rotate("1-3,2-4", 10) == [0, 1, 2, 3]


def test_resolve_result_is_sorted_regardless_of_input_order():
    assert rotate_engine.resolve_pages_to_rotate("7,1,4", 10) == [0, 3, 6]


def test_resolve_empty_input_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("", 10)


def test_resolve_page_zero_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("0", 10)


def test_resolve_negative_page_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("-2", 10)


def test_resolve_non_numeric_token_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("abc", 10)


def test_resolve_malformed_range_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("1-3-5", 10)


def test_resolve_reversed_range_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("7-3", 10)


def test_resolve_out_of_range_page_rejected():
    with pytest.raises(PageRangeError):
        rotate_engine.resolve_pages_to_rotate("15", 10)


# ---------------------------------------------------------------------------
# resolve_clockwise_degrees() -- direction/angle semantics
# ---------------------------------------------------------------------------

def test_clockwise_90():
    assert rotate_engine.resolve_clockwise_degrees("clockwise", 90) == 90


def test_clockwise_180():
    assert rotate_engine.resolve_clockwise_degrees("clockwise", 180) == 180


def test_clockwise_270():
    assert rotate_engine.resolve_clockwise_degrees("clockwise", 270) == 270


def test_counterclockwise_90_equals_270():
    assert rotate_engine.resolve_clockwise_degrees("counterclockwise", 90) == 270


def test_counterclockwise_180_equals_180():
    assert rotate_engine.resolve_clockwise_degrees("counterclockwise", 180) == 180


def test_counterclockwise_270_equals_90():
    assert rotate_engine.resolve_clockwise_degrees("counterclockwise", 270) == 90


def test_angle_zero_rejected():
    with pytest.raises(RotationError):
        rotate_engine.resolve_clockwise_degrees("clockwise", 0)


def test_negative_angle_rejected():
    with pytest.raises(RotationError):
        rotate_engine.resolve_clockwise_degrees("clockwise", -90)


def test_arbitrary_non_multiple_of_90_rejected():
    with pytest.raises(RotationError):
        rotate_engine.resolve_clockwise_degrees("clockwise", 45)


def test_invalid_direction_rejected():
    with pytest.raises(RotationError):
        rotate_engine.resolve_clockwise_degrees("sideways", 90)


def test_build_rotation_map():
    assert rotate_engine.build_rotation_map([0, 2, 4], 90) == {
        0: 90, 2: 90, 4: 90,
    }


def test_build_rotation_map_empty_selection():
    assert rotate_engine.build_rotation_map([], 90) == {}


# ---------------------------------------------------------------------------
# rotate_pages_in_pdf() -- VALID INPUT / rotation correctness
# ---------------------------------------------------------------------------

def test_rotate_one_page_clockwise_90(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert _rotations(output_path) == [90, 0, 0]


def test_rotate_clockwise_180(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 180})

    assert _rotations(output_path) == [180, 0, 0]


def test_rotate_clockwise_270(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 270})

    assert _rotations(output_path) == [270, 0, 0]


def test_rotate_counterclockwise_90_via_resolved_degrees(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    degrees = rotate_engine.resolve_clockwise_degrees("counterclockwise", 90)
    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: degrees})

    assert _rotations(output_path) == [270, 0, 0]


def test_rotate_counterclockwise_180_via_resolved_degrees(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    degrees = rotate_engine.resolve_clockwise_degrees("counterclockwise", 180)
    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: degrees})

    assert _rotations(output_path) == [180, 0, 0]


def test_rotate_counterclockwise_270_via_resolved_degrees(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    degrees = rotate_engine.resolve_clockwise_degrees("counterclockwise", 270)
    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: degrees})

    assert _rotations(output_path) == [90, 0, 0]


def test_existing_rotation_plus_new_rotation_cumulative(tmp_path):
    """Explicit spec example: existing rotation 90 + clockwise 90 = 180
    (not blindly replaced with 90).
    """
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc[0].set_rotation(90)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert _rotations(output_path) == [180]


def test_existing_rotation_270_plus_clockwise_90_wraps_to_zero(tmp_path):
    """Explicit spec example: existing rotation 270 + clockwise 90 = 0."""
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc[0].set_rotation(270)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert _rotations(output_path) == [0]


def test_existing_rotation_zero_plus_counterclockwise_90_is_270(tmp_path):
    """Explicit spec example: existing rotation 0 + counter-clockwise 90
    = 270.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1)
    output_path = tmp_path / "output.pdf"

    degrees = rotate_engine.resolve_clockwise_degrees("counterclockwise", 90)
    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: degrees})

    assert _rotations(output_path) == [270]


def test_rotation_result_always_normalized_to_0_90_180_270(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc[0].set_rotation(270)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 270})

    result = _rotations(output_path)[0]
    assert result in (0, 90, 180, 270)
    assert result == 180  # 270 + 270 = 540 % 360 = 180


def test_rotate_multiple_pages(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90, 2: 180, 4: 270})

    assert _rotations(output_path) == [90, 0, 180, 0, 270]


def test_non_selected_pages_remain_unchanged(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page()
    doc[3].set_rotation(90)  # page 4 pre-rotated
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    # Page 4's pre-existing rotation must be untouched -- it wasn't in
    # the rotations dict at all.
    assert _rotations(output_path) == [90, 0, 0, 90]


def test_rotate_all_pages(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=4)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(
        src, output_path, {0: 90, 1: 90, 2: 90, 3: 90},
    )

    assert _rotations(output_path) == [90, 90, 90, 90]


def test_page_count_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {2: 90})

    doc = pymupdf.open(output_path)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_page_order_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((50, 50), f"PAGE {i + 1}")
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {1: 90, 3: 180})

    out_doc = pymupdf.open(output_path)
    try:
        texts = [out_doc[i].get_text().strip() for i in range(out_doc.page_count)]
    finally:
        out_doc.close()
    assert texts == ["PAGE 1", "PAGE 2", "PAGE 3", "PAGE 4", "PAGE 5"]


def test_full_selection_to_engine_pipeline(tmp_path):
    """End-to-end: resolve a text selection + direction/angle, then feed
    straight into rotate_pages_in_pdf() -- the exact path the UI takes.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    indices = rotate_engine.resolve_pages_to_rotate("1,3,5", 5)
    degrees = rotate_engine.resolve_clockwise_degrees("clockwise", 90)
    rotations = rotate_engine.build_rotation_map(indices, degrees)
    rotate_engine.rotate_pages_in_pdf(src, output_path, rotations)

    assert _rotations(output_path) == [90, 0, 90, 0, 90]


# ---------------------------------------------------------------------------
# rotate_pages_in_pdf() -- INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_rotations_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(RotationError):
        rotate_engine.rotate_pages_in_pdf(src, output_path, {})

    assert not output_path.exists()


def test_invalid_rotation_value_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(RotationError):
        rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 45})

    assert not output_path.exists()


def test_zero_rotation_value_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(RotationError):
        rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 0})

    assert not output_path.exists()


def test_negative_rotation_value_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(RotationError):
        rotate_engine.rotate_pages_in_pdf(src, output_path, {0: -90})

    assert not output_path.exists()


def test_out_of_range_page_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(RotationError):
        rotate_engine.rotate_pages_in_pdf(src, output_path, {10: 90})

    assert not output_path.exists()


def test_negative_page_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(RotationError):
        rotate_engine.rotate_pages_in_pdf(src, output_path, {-1: 90})

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        rotate_engine.rotate_pages_in_pdf(missing, output_path, {0: 90})

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        rotate_engine.rotate_pages_in_pdf(corrupt, output_path, {0: 90})


def test_encrypted_source_raises_clear_error(tmp_path):
    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret",
    )
    doc.close()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.EncryptedPDFError):
        rotate_engine.rotate_pages_in_pdf(protected, output_path, {0: 90})


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        rotate_engine.rotate_pages_in_pdf(a_directory, output_path, {0: 90})


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# SOURCE SAFETY
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged_after_successful_rotation(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90, 2: 180})

    assert src.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_failed_rotation(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert src.read_bytes() == original_bytes


def test_source_page_count_unchanged_after_rotation(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90, 3: 270})

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_source_page_rotations_unchanged_after_rotation(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc[0].set_rotation(90)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90, 1: 180})

    # Source's own rotations must be exactly as before this call.
    assert _rotations(src) == [90, 0, 0]


def test_failed_operation_does_not_corrupt_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
        assert doc[0].rotation == 0
    finally:
        doc.close()


def test_no_partial_output_remains_after_failure(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_output_is_a_different_file_from_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

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
        file_manager.sanitize_windows_filename("document_rotated")
    )
    assert name.endswith(".pdf")


def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            rotate_engine.rotate_pages_in_pdf(src, output_path, {0: 90})

    assert not output_path.exists()


def test_progress_callback_reports_rotating_and_saving(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    messages = []
    rotate_engine.rotate_pages_in_pdf(
        src, output_path, {0: 90}, progress_callback=messages.append,
    )

    assert any("rotat" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_error_messages_contain_no_traceback(tmp_path):
    output_path = tmp_path / "output.pdf"
    try:
        rotate_engine.rotate_pages_in_pdf(
            tmp_path / "missing.pdf", output_path, {0: 90},
        )
    except pdf_engine.PDFEngineError as exc:
        assert "Traceback" not in str(exc)


# ---------------------------------------------------------------------------
# WORKER-THREAD SAFETY
# ---------------------------------------------------------------------------

def test_engine_is_safe_to_call_from_a_background_thread(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    output_path = tmp_path / "output.pdf"

    result = {}

    def worker():
        try:
            result["path"] = rotate_engine.rotate_pages_in_pdf(
                src, output_path, {0: 90, 2: 180},
            )
        except Exception as exc:  # pragma: no cover -- surfaced via assert below
            result["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)

    assert "error" not in result
    assert result["path"] == output_path
    assert _rotations(output_path) == [90, 0, 180, 0, 0]


# ---------------------------------------------------------------------------
# REGRESSION: this module must not affect split_engine/other engines
# ---------------------------------------------------------------------------

def test_split_engine_parse_page_ranges_still_importable_and_unchanged():
    import split_engine

    assert split_engine.parse_page_ranges("1,3,5-7", 10) == [[0], [2], [4, 5, 6]]
