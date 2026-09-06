"""
test_output_path_utils.py

Phase 7 tests for the pure output-path utilities in file_manager.py:
ensure_pdf_extension(), sanitize_windows_filename(), and
generate_compressed_output_path(). These are plain functions with no
tkinter dependency, so they run without a display.

Covers, per the Phase 7 requirements:
  - .pdf extension is always present, never doubled (no "merged.pdf.pdf").
  - Filenames with spaces and Unicode characters pass through unchanged.
  - Only genuinely-invalid Windows filename characters are sanitized.
  - Windows reserved device names are handled.
  - Collision-safe automatic naming: "<name>_compressed.pdf", then
    " (1)", " (2)", etc., and it never returns a path that already
    exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from file_manager import (
    ensure_pdf_extension,
    generate_compressed_output_path,
    sanitize_windows_filename,
)


# ---------------------------------------------------------------------------
# ensure_pdf_extension
# ---------------------------------------------------------------------------

def test_adds_extension_when_missing():
    assert ensure_pdf_extension("merged") == "merged.pdf"


def test_does_not_double_extension():
    assert ensure_pdf_extension("merged.pdf") == "merged.pdf"


def test_extension_check_is_case_insensitive_but_preserves_case():
    assert ensure_pdf_extension("Merged.PDF") == "Merged.PDF"
    assert ensure_pdf_extension("Merged.Pdf") == "Merged.Pdf"


def test_handles_filename_with_spaces():
    assert ensure_pdf_extension("my merged file") == "my merged file.pdf"


def test_handles_unicode_filename():
    assert ensure_pdf_extension("résumé final") == "résumé final.pdf"
    assert ensure_pdf_extension("報告書") == "報告書.pdf"


# ---------------------------------------------------------------------------
# sanitize_windows_filename
# ---------------------------------------------------------------------------

def test_leaves_valid_names_unchanged():
    assert sanitize_windows_filename("report_final") == "report_final"
    assert sanitize_windows_filename("My Report (v2)") == "My Report (v2)"


def test_preserves_spaces():
    assert sanitize_windows_filename("my merged file") == "my merged file"


def test_preserves_unicode():
    assert sanitize_windows_filename("résumé") == "résumé"
    assert sanitize_windows_filename("報告書") == "報告書"
    assert sanitize_windows_filename("Über_Report") == "Über_Report"


def test_replaces_only_genuinely_invalid_characters():
    # < > : " / \ | ? * are invalid on Windows; everything else (hyphens,
    # underscores, parentheses, ampersands, apostrophes) must survive.
    dirty = 'report<1>:"a/b\\c|d?e*f'
    result = sanitize_windows_filename(dirty)
    for bad_char in '<>:"/\\|?*':
        assert bad_char not in result
    # Characters NOT in the invalid set must be untouched.
    assert sanitize_windows_filename("a & b's report - final") == "a & b's report - final"


def test_trims_trailing_dots_and_spaces():
    # Windows silently strips trailing dots/spaces; trimming them
    # ourselves avoids a mismatch between the requested and actual name.
    assert sanitize_windows_filename("report.") == "report"
    assert sanitize_windows_filename("report   ") == "report"


def test_trailing_dots_and_spaces_mixed():
    result = sanitize_windows_filename("report...   ")
    assert not result.endswith(" ")
    assert not result.endswith(".")


def test_empty_or_fully_invalid_name_falls_back():
    assert sanitize_windows_filename("") == "output"
    assert sanitize_windows_filename("...") == "output"
    assert sanitize_windows_filename("   ") == "output"


def test_reserved_device_names_are_prefixed():
    for reserved in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
        result = sanitize_windows_filename(reserved)
        assert result != reserved
        assert result.upper().lstrip("_") == reserved

    # Case-insensitivity of the reserved-name check.
    assert sanitize_windows_filename("con") != "con"


def test_non_reserved_name_containing_reserved_substring_is_untouched():
    # "CONFIDENTIAL" contains "CON" but is not itself a reserved name.
    assert sanitize_windows_filename("CONFIDENTIAL") == "CONFIDENTIAL"


# ---------------------------------------------------------------------------
# generate_compressed_output_path
# ---------------------------------------------------------------------------

def test_basic_compressed_name(tmp_path):
    result = generate_compressed_output_path(tmp_path / "report.pdf", tmp_path)
    assert result == tmp_path / "report_compressed.pdf"


def test_never_returns_an_existing_path(tmp_path):
    (tmp_path / "report_compressed.pdf").write_bytes(b"existing content")

    result = generate_compressed_output_path(tmp_path / "report.pdf", tmp_path)

    assert result == tmp_path / "report_compressed (1).pdf"
    assert not result.exists()
    # The original existing file must be untouched by merely generating a
    # new candidate name for it.
    assert (tmp_path / "report_compressed.pdf").read_bytes() == b"existing content"


def test_increments_through_multiple_collisions(tmp_path):
    (tmp_path / "report_compressed.pdf").write_bytes(b"1")
    (tmp_path / "report_compressed (1).pdf").write_bytes(b"2")
    (tmp_path / "report_compressed (2).pdf").write_bytes(b"3")

    result = generate_compressed_output_path(tmp_path / "report.pdf", tmp_path)

    assert result == tmp_path / "report_compressed (3).pdf"
    assert not result.exists()


def test_two_different_source_folders_same_filename_do_not_collide_incorrectly(tmp_path):
    """Two distinct input files that happen to share a filename (e.g.
    "report.pdf" from two different source folders) must each get a safe,
    distinct output name when compressed into the same destination folder.
    """
    dir_a = tmp_path / "dirA"
    dir_b = tmp_path / "dirB"
    dir_a.mkdir()
    dir_b.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    first = generate_compressed_output_path(dir_a / "report.pdf", output_dir)
    assert first == output_dir / "report_compressed.pdf"

    # Simulate the first file having actually been written before the
    # second one is processed.
    first.write_bytes(b"first file's compressed output")

    second = generate_compressed_output_path(dir_b / "report.pdf", output_dir)
    assert second == output_dir / "report_compressed (1).pdf"
    assert second != first


def test_handles_filename_with_spaces(tmp_path):
    result = generate_compressed_output_path(tmp_path / "my merged file.pdf", tmp_path)
    assert result == tmp_path / "my merged file_compressed.pdf"


def test_handles_unicode_filename(tmp_path):
    result = generate_compressed_output_path(tmp_path / "résumé.pdf", tmp_path)
    assert result == tmp_path / "résumé_compressed.pdf"


def test_output_always_ends_in_pdf(tmp_path):
    result = generate_compressed_output_path(tmp_path / "report.pdf", tmp_path)
    assert result.suffix.lower() == ".pdf"
    assert not result.name.lower().endswith(".pdf.pdf")


def test_sanitizes_invalid_characters_from_source_stem(tmp_path):
    # A source filename containing a character that's invalid in a
    # *destination* filename context should still be handled cleanly.
    # (On a real Windows filesystem the source file couldn't itself
    # contain e.g. '?' in its name, but this proves the generator is
    # defensive regardless of what stem it's given.)
    weird_source = Path("report?final.pdf")
    result = generate_compressed_output_path(weird_source, tmp_path)
    assert "?" not in result.name
    assert result.parent == tmp_path
