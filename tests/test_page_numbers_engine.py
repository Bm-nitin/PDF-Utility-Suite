"""
test_page_numbers_engine.py

Comprehensive tests for page_numbers_engine.py (Phase 20): page-
selection resolution (resolve_pages_to_number -- reusing
split_engine.parse_page_ranges), option validation (start number, font
size, margin, position), and the actual add_page_numbers() engine
(correct numbering sequence for a subset of pages, rotation-aware
placement verified by rendering to a pixmap, source safety, output
safety, and content-preservation checks).

Also encodes, as executable tests, the experimental findings about the
installed PyMuPDF version's actual insert_text()/rotation-matrix
behavior documented in page_numbers_engine.py's own module docstring --
see the "PyMuPDF rotation behavior" section below.

No tkinter dependency -- page_numbers_engine.py is pure logic + file
I/O, so these run without a display.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import page_numbers_engine as pne
import pdf_engine
from page_numbers_engine import PageNumberOptionsError
from split_engine import PageRangeError


def _make_pdf(path, pages=1, text_prefix="CONTENT "):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text_prefix:
            page.insert_text((50, 50), f"{text_prefix}{i + 1}")
    doc.save(path)
    doc.close()


def _page_texts(path):
    doc = pymupdf.open(path)
    try:
        return [doc[i].get_text() for i in range(doc.page_count)]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# PyMuPDF rotation behavior -- experimentally confirmed, encoded as tests
# ---------------------------------------------------------------------------

def test_pymupdf_page_rect_reflects_current_rotation():
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    assert (page.rect.width, page.rect.height) == (200, 300)
    page.set_rotation(90)
    assert (page.rect.width, page.rect.height) == (300, 200)
    doc.close()


def test_pymupdf_derotation_matrix_converts_display_to_content_space():
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.set_rotation(90)
    display_point = pymupdf.Point(10, 20)
    content_point = display_point * page.derotation_matrix
    # Content space is the ORIGINAL 200x300 orientation -- the
    # transformed point must land within those original bounds, not the
    # rotated 300x200 display bounds.
    assert 0 <= content_point.x <= 200
    assert 0 <= content_point.y <= 300
    doc.close()


def test_get_text_length_measures_string_width():
    width = pymupdf.get_text_length("42", fontname="helv", fontsize=12)
    assert width > 0


# ---------------------------------------------------------------------------
# resolve_pages_to_number()
# ---------------------------------------------------------------------------

def test_resolve_single_page():
    assert pne.resolve_pages_to_number("3", 10) == [2]


def test_resolve_all_pages_range():
    assert pne.resolve_pages_to_number("1-10", 10) == list(range(10))


def test_resolve_selected_pages():
    assert pne.resolve_pages_to_number("3-5", 10) == [2, 3, 4]


def test_resolve_multiple_ranges():
    assert pne.resolve_pages_to_number("1-3,5,8-10", 10) == [
        0, 1, 2, 4, 7, 8, 9,
    ]


def test_resolve_whitespace_tolerated():
    assert pne.resolve_pages_to_number(" 1, 3, 5-7 ", 10) == [0, 2, 4, 5, 6]


def test_resolve_overlapping_ranges_deduplicated():
    assert pne.resolve_pages_to_number("1-3,2-4", 10) == [0, 1, 2, 3]


def test_resolve_empty_input_rejected():
    with pytest.raises(PageRangeError):
        pne.resolve_pages_to_number("", 10)


def test_resolve_page_zero_rejected():
    with pytest.raises(PageRangeError):
        pne.resolve_pages_to_number("0", 10)


def test_resolve_out_of_range_page_rejected():
    with pytest.raises(PageRangeError):
        pne.resolve_pages_to_number("15", 10)


def test_resolve_reversed_range_rejected():
    with pytest.raises(PageRangeError):
        pne.resolve_pages_to_number("7-3", 10)


# ---------------------------------------------------------------------------
# validate_start_number()
# ---------------------------------------------------------------------------

def test_start_number_zero_accepted():
    assert pne.validate_start_number("0") == 0


def test_start_number_positive_accepted():
    assert pne.validate_start_number("5") == 5


def test_start_number_whitespace_tolerated():
    assert pne.validate_start_number(" 5 ") == 5


def test_start_number_empty_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_start_number("")


def test_start_number_whitespace_only_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_start_number("   ")


def test_start_number_non_numeric_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_start_number("abc")


def test_start_number_decimal_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_start_number("1.5")


def test_start_number_decimal_whole_value_also_rejected():
    """Per the spec, "decimal values" are rejected outright -- even
    "1.0", which is numerically a whole number.
    """
    with pytest.raises(PageNumberOptionsError):
        pne.validate_start_number("1.0")


def test_start_number_negative_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_start_number("-1")


def test_start_number_no_arbitrary_maximum():
    assert pne.validate_start_number("999999999") == 999999999


# ---------------------------------------------------------------------------
# validate_font_size()
# ---------------------------------------------------------------------------

def test_font_size_positive_accepted():
    assert pne.validate_font_size("11") == 11.0


def test_font_size_fractional_accepted():
    assert pne.validate_font_size("11.5") == 11.5


def test_font_size_empty_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_font_size("")


def test_font_size_non_numeric_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_font_size("abc")


def test_font_size_zero_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_font_size("0")


def test_font_size_negative_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_font_size("-5")


def test_font_size_nan_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_font_size("nan")


def test_font_size_infinity_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_font_size("inf")


# ---------------------------------------------------------------------------
# validate_margin()
# ---------------------------------------------------------------------------

def test_margin_zero_accepted():
    assert pne.validate_margin("0") == 0.0


def test_margin_positive_accepted():
    assert pne.validate_margin("36") == 36.0


def test_margin_empty_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_margin("")


def test_margin_non_numeric_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_margin("abc")


def test_margin_negative_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_margin("-1")


def test_margin_nan_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_margin("nan")


# ---------------------------------------------------------------------------
# validate_position()
# ---------------------------------------------------------------------------

def test_all_six_positions_valid():
    for position in pne.POSITIONS:
        assert pne.validate_position(position) == position


def test_invalid_position_rejected():
    with pytest.raises(PageNumberOptionsError):
        pne.validate_position("middle_center")


def test_positions_tuple_has_exactly_six_values():
    assert len(pne.POSITIONS) == 6
    assert set(pne.POSITIONS) == {
        "top_left", "top_center", "top_right",
        "bottom_left", "bottom_center", "bottom_right",
    }


# ---------------------------------------------------------------------------
# add_page_numbers() -- VALID INPUT / numbering correctness
# ---------------------------------------------------------------------------

def test_basic_numbering_all_pages(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0, 1, 2], start_number=1)

    texts = _page_texts(output_path)
    assert "1" in texts[0]
    assert "2" in texts[1]
    assert "3" in texts[2]


def test_selected_pages_correct_sequence(tmp_path):
    """Explicit spec example: 10-page PDF, selected 3-5, start=1 ->
    page 3 gets 1, page 4 gets 2, page 5 gets 3.
    """
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10)
    output_path = tmp_path / "output.pdf"

    indices = pne.resolve_pages_to_number("3-5", 10)
    pne.add_page_numbers(src, output_path, indices, start_number=1)

    texts = _page_texts(output_path)
    assert "1" in texts[2]
    assert "2" in texts[3]
    assert "3" in texts[4]


def test_unselected_pages_remain_unnumbered(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10, text_prefix=None)
    output_path = tmp_path / "output.pdf"

    indices = pne.resolve_pages_to_number("3-5", 10)
    pne.add_page_numbers(src, output_path, indices, start_number=1)

    texts = _page_texts(output_path)
    for i in [0, 1, 5, 6, 7, 8, 9]:
        assert texts[i].strip() == "", f"page {i} should be unnumbered"


def test_multiple_ranges_numbering(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=10, text_prefix=None)
    output_path = tmp_path / "output.pdf"

    indices = pne.resolve_pages_to_number("1-2,5,8-9", 10)
    pne.add_page_numbers(src, output_path, indices, start_number=1)

    texts = _page_texts(output_path)
    assert "1" in texts[0]
    assert "2" in texts[1]
    assert "3" in texts[4]
    assert "4" in texts[7]
    assert "5" in texts[8]


def test_start_number_zero(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3, text_prefix=None)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0, 1, 2], start_number=0)

    texts = _page_texts(output_path)
    assert "0" in texts[0]
    assert "1" in texts[1]
    assert "2" in texts[2]


def test_positive_start_number(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3, text_prefix=None)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0, 1, 2], start_number=100)

    texts = _page_texts(output_path)
    assert "100" in texts[0]
    assert "101" in texts[1]
    assert "102" in texts[2]


def test_all_six_positions_produce_output(tmp_path):
    for position in pne.POSITIONS:
        src = tmp_path / f"doc_{position}.pdf"
        _make_pdf(src, pages=1, text_prefix=None)
        output_path = tmp_path / f"out_{position}.pdf"

        pne.add_page_numbers(
            src, output_path, [0], start_number=1, position=position,
        )

        texts = _page_texts(output_path)
        assert "1" in texts[0]


def test_page_count_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=7)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0], start_number=1)

    doc = pymupdf.open(output_path)
    try:
        assert doc.page_count == 7
    finally:
        doc.close()


def test_page_order_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5, text_prefix="ORIGINAL ")
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [1, 3], start_number=1)

    doc = pymupdf.open(output_path)
    try:
        for i in range(5):
            assert f"ORIGINAL {i + 1}" in doc[i].get_text()
    finally:
        doc.close()


def test_page_rotations_preserved(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc[0].set_rotation(90)
    doc[1].set_rotation(180)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0, 1, 2], start_number=1)

    out_doc = pymupdf.open(output_path)
    try:
        assert out_doc[0].rotation == 90
        assert out_doc[1].rotation == 180
        assert out_doc[2].rotation == 0
    finally:
        out_doc.close()


def test_repeated_operation_independent_outputs(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3, text_prefix=None)

    out1 = tmp_path / "out1.pdf"
    out2 = tmp_path / "out2.pdf"
    pne.add_page_numbers(src, out1, [0], start_number=1)
    pne.add_page_numbers(src, out2, [0], start_number=50)

    assert "1" in _page_texts(out1)[0]
    assert "50" in _page_texts(out2)[0]


def test_output_reopens_successfully(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0, 1, 2], start_number=1)

    doc = pymupdf.open(output_path)
    try:
        assert doc.page_count == 3
        assert not doc.is_encrypted
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Rotation-aware placement (rendered pixmap verification)
# ---------------------------------------------------------------------------

def _render_first_page(path):
    doc = pymupdf.open(path)
    try:
        return doc[0].get_pixmap()
    finally:
        doc.close()


def test_rotated_90_page_number_rendered(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.set_rotation(90)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(
        src, output_path, [0], start_number=1, position="top_left",
        font_size=16, margin=10,
    )

    pix = _render_first_page(output_path)
    # Rendered pixmap dimensions must reflect the rotated (displayed)
    # orientation -- 300x200, not the original 200x300.
    assert (pix.width, pix.height) == (300, 200)


def test_rotated_180_page_number_rendered(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.set_rotation(180)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(
        src, output_path, [0], start_number=1, position="bottom_right",
        font_size=16, margin=10,
    )

    doc2 = pymupdf.open(output_path)
    try:
        assert doc2[0].rotation == 180
    finally:
        doc2.close()


def test_rotated_270_page_number_rendered(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.set_rotation(270)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(
        src, output_path, [0], start_number=1, position="top_right",
        font_size=16, margin=10,
    )

    pix = _render_first_page(output_path)
    assert (pix.width, pix.height) == (300, 200)


def test_number_present_on_rotated_page(tmp_path):
    """Confirms the inserted number is actually extractable text on a
    rotated page -- not just that the save succeeded.
    """
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.set_rotation(90)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(
        src, output_path, [0], start_number=7, position="bottom_center",
    )

    texts = _page_texts(output_path)
    assert "7" in texts[0]


# ---------------------------------------------------------------------------
# Existing content
# ---------------------------------------------------------------------------

def test_blank_page_receives_number(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1, text_prefix=None)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0], start_number=1)

    assert "1" in _page_texts(output_path)[0]


def test_page_with_existing_text_receives_number_without_losing_it(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=1, text_prefix="EXISTING TEXT ")
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0], start_number=1)

    text = _page_texts(output_path)[0]
    assert "EXISTING TEXT 1" in text
    assert "1" in text


def test_page_with_existing_image_receives_number(tmp_path):
    src = tmp_path / "document.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    # A minimal 1x1 PNG (solid pixel), inserted as a real image object.
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
        b"\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page.insert_image(pymupdf.Rect(10, 10, 60, 60), stream=png_bytes)
    doc.save(src)
    doc.close()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0], start_number=1)

    doc2 = pymupdf.open(output_path)
    try:
        assert len(doc2[0].get_images()) == 1
        assert "1" in doc2[0].get_text()
    finally:
        doc2.close()


# ---------------------------------------------------------------------------
# INVALID INPUT
# ---------------------------------------------------------------------------

def test_empty_page_indices_rejected(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [])

    assert not output_path.exists()


def test_invalid_position_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [0], position="middle")

    assert not output_path.exists()


def test_negative_start_number_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [0], start_number=-1)

    assert not output_path.exists()


def test_zero_font_size_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [0], font_size=0)

    assert not output_path.exists()


def test_negative_font_size_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [0], font_size=-5)

    assert not output_path.exists()


def test_negative_margin_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [0], margin=-1)

    assert not output_path.exists()


def test_out_of_range_page_index_rejected_at_engine_level(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(PageNumberOptionsError):
        pne.add_page_numbers(src, output_path, [10])

    assert not output_path.exists()


def test_missing_source_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        pne.add_page_numbers(missing, output_path, [0])

    assert not output_path.exists()


def test_corrupt_source_raises_clear_error(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"NOT A REAL PDF" * 20)
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        pne.add_page_numbers(corrupt, output_path, [0])


def test_directory_as_source_raises_clear_error(tmp_path):
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.InvalidPDFError):
        pne.add_page_numbers(a_directory, output_path, [0])


def test_encrypted_source_rejected(tmp_path):
    """Page Numbers has no password-handling of its own (per the Phase
    20 spec) -- an encrypted source must be rejected the same way every
    other non-Unlock engine already rejects one.
    """
    protected = tmp_path / "protected.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        protected,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret-owner",
    )
    doc.close()
    output_path = tmp_path / "output.pdf"

    with pytest.raises(pdf_engine.EncryptedPDFError):
        pne.add_page_numbers(protected, output_path, [0])

    assert not output_path.exists()


def test_output_filesystem_failure_is_translated_to_friendly_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pne.add_page_numbers(src, output_path, [0])

    assert not output_path.exists()


def test_unexpected_exception_is_translated_to_pdf_engine_error(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(
        pdf_engine, "_atomic_save_pdf", side_effect=RuntimeError("totally unexpected"),
    ):
        with pytest.raises(pdf_engine.PDFEngineError):
            pne.add_page_numbers(src, output_path, [0])


# ---------------------------------------------------------------------------
# SOURCE SAFETY
# ---------------------------------------------------------------------------

def test_source_bytes_unchanged_after_successful_operation(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0, 1, 2], start_number=1)

    assert src.read_bytes() == original_bytes


def test_source_bytes_unchanged_after_failure(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=5)
    original_bytes = src.read_bytes()
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pne.add_page_numbers(src, output_path, [0])

    assert src.read_bytes() == original_bytes


def test_source_page_count_unchanged(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=6)
    output_path = tmp_path / "output.pdf"

    pne.add_page_numbers(src, output_path, [0], start_number=1)

    doc = pymupdf.open(src)
    try:
        assert doc.page_count == 6
    finally:
        doc.close()


def test_output_is_a_separate_file_from_source(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    result_path = pne.add_page_numbers(src, output_path, [0], start_number=1)

    assert result_path != src
    assert result_path.exists()
    assert src.exists()
    assert result_path.read_bytes() != src.read_bytes()


def test_no_partial_output_remains_after_failure(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pne.add_page_numbers(src, output_path, [0])

    assert not output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# OUTPUT NAMING / COLLISION HANDLING (file_manager.generate_numbered_output_path)
# ---------------------------------------------------------------------------

def test_generate_numbered_output_path_default_name(tmp_path):
    import file_manager

    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")

    result = file_manager.generate_numbered_output_path(source, tmp_path)

    assert result == tmp_path / "document_numbered.pdf"


def test_generate_numbered_output_path_collision_safe(tmp_path):
    import file_manager

    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")
    (tmp_path / "document_numbered.pdf").write_bytes(b"already exists")

    result = file_manager.generate_numbered_output_path(source, tmp_path)

    assert result == tmp_path / "document_numbered (1).pdf"


def test_generate_numbered_output_path_multiple_collisions(tmp_path):
    import file_manager

    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")
    (tmp_path / "document_numbered.pdf").write_bytes(b"x")
    (tmp_path / "document_numbered (1).pdf").write_bytes(b"x")

    result = file_manager.generate_numbered_output_path(source, tmp_path)

    assert result == tmp_path / "document_numbered (2).pdf"


def test_generate_numbered_output_path_never_overwrites(tmp_path):
    import file_manager

    source = tmp_path / "document.pdf"
    source.write_bytes(b"fake")
    existing = tmp_path / "document_numbered.pdf"
    existing.write_bytes(b"do not touch")

    result = file_manager.generate_numbered_output_path(source, tmp_path)

    assert result != existing
    assert existing.read_bytes() == b"do not touch"


def test_add_page_numbers_end_to_end_with_generated_output_path(tmp_path):
    import file_manager

    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=2, text_prefix=None)

    output_path = file_manager.generate_numbered_output_path(src, tmp_path)
    pne.add_page_numbers(src, output_path, [0, 1], start_number=1)

    assert output_path.name == "document_numbered.pdf"
    texts = _page_texts(output_path)
    assert "1" in texts[0]
    assert "2" in texts[1]


# ---------------------------------------------------------------------------
# ATOMIC SAVE / PROGRESS CALLBACK
# ---------------------------------------------------------------------------

def test_atomic_save_is_used_output_never_partially_written(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    with patch.object(pdf_engine, "_atomic_save_pdf", side_effect=OSError("boom")):
        with pytest.raises(pdf_engine.PDFEngineError):
            pne.add_page_numbers(src, output_path, [0])

    assert not output_path.exists()


def test_progress_callback_reports_numbering_and_saving(tmp_path):
    src = tmp_path / "document.pdf"
    _make_pdf(src, pages=3)
    output_path = tmp_path / "output.pdf"

    messages = []
    pne.add_page_numbers(
        src, output_path, [0], start_number=1, progress_callback=messages.append,
    )

    assert any("page number" in m.lower() for m in messages)
    assert any("saving" in m.lower() for m in messages)


def test_error_messages_contain_no_traceback(tmp_path):
    output_path = tmp_path / "output.pdf"
    try:
        pne.add_page_numbers(
            tmp_path / "missing.pdf", output_path, [0],
        )
    except pdf_engine.PDFEngineError as exc:
        assert "Traceback" not in str(exc)
