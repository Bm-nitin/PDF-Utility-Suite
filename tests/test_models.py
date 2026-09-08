"""
test_models.py

Direct unit tests for models.py -- PDFFile, AppState, and
format_file_size().

Phase 11 gap: models.py had zero dedicated tests. Its logic was only
ever exercised indirectly through UI integration tests (which do cover
real usage, but never pin down the pure-function edge cases directly:
byte-count boundaries in format_file_size, singular/plural page count
display, an AppState with a mix of known and unknown page counts, etc.).
These are cheap, fast, deterministic tests for logic that has no
tkinter/pymupdf dependency at all.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import AppState, PDFFile, format_file_size


# ---------------------------------------------------------------------------
# format_file_size
# ---------------------------------------------------------------------------

def test_zero_bytes():
    assert format_file_size(0) == "0 B"


def test_bytes_under_1kb():
    assert format_file_size(500) == "500 B"
    assert format_file_size(1023) == "1023 B"


def test_exactly_1kb_boundary():
    assert format_file_size(1024) == "1.0 KB"


def test_kb_range():
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(1536) == "1.5 KB"


def test_mb_range():
    assert format_file_size(1024 * 1024) == "1.0 MB"
    assert format_file_size(int(2.5 * 1024 * 1024)) == "2.5 MB"


def test_gb_range():
    assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"


def test_tb_range():
    assert format_file_size(1024 ** 4) == "1.0 TB"


def test_very_large_falls_back_to_pb():
    huge = 1024 ** 5 * 3
    result = format_file_size(huge)
    assert "PB" in result


# ---------------------------------------------------------------------------
# PDFFile
# ---------------------------------------------------------------------------

def test_pdffile_size_display_uses_format_file_size():
    f = PDFFile(path=Path("a.pdf"), name="a.pdf", size=2048)
    assert f.size_display == "2.0 KB"


def test_pdffile_page_count_display_singular():
    f = PDFFile(path=Path("a.pdf"), name="a.pdf", size=100, page_count=1)
    assert f.page_count_display == "1 page"


def test_pdffile_page_count_display_plural():
    f = PDFFile(path=Path("a.pdf"), name="a.pdf", size=100, page_count=5)
    assert f.page_count_display == "5 pages"


def test_pdffile_page_count_display_zero_is_plural():
    f = PDFFile(path=Path("a.pdf"), name="a.pdf", size=100, page_count=0)
    assert f.page_count_display == "0 pages"


def test_pdffile_page_count_display_unknown():
    f = PDFFile(path=Path("a.pdf"), name="a.pdf", size=100, page_count=None)
    assert f.page_count_display == "?"


def test_pdffile_defaults():
    f = PDFFile(path=Path("a.pdf"), name="a.pdf", size=100)
    assert f.page_count is None
    assert f.is_encrypted is False


# ---------------------------------------------------------------------------
# AppState
# ---------------------------------------------------------------------------

def test_appstate_empty_totals():
    state = AppState()
    assert state.total_files == 0
    assert state.total_pages == 0
    assert state.total_size == 0
    assert state.total_size_display == "0 B"


def test_appstate_totals_with_files():
    state = AppState()
    state.files.append(PDFFile(path=Path("a.pdf"), name="a.pdf", size=1000, page_count=3))
    state.files.append(PDFFile(path=Path("b.pdf"), name="b.pdf", size=2000, page_count=5))
    assert state.total_files == 2
    assert state.total_pages == 8
    assert state.total_size == 3000


def test_appstate_totals_with_unknown_page_count_mixed_in():
    """A file whose page_count is still None (e.g. mid-import, before
    get_pdf_info() has run) must not break total_pages -- it should
    contribute 0, not raise or produce None.
    """
    state = AppState()
    state.files.append(PDFFile(path=Path("a.pdf"), name="a.pdf", size=1000, page_count=3))
    state.files.append(PDFFile(path=Path("b.pdf"), name="b.pdf", size=2000, page_count=None))
    assert state.total_pages == 3


def test_appstate_default_compression_level():
    state = AppState()
    assert state.compression_level == "recommended"


def test_contains_path_true_for_exact_match(tmp_path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"x")
    state = AppState()
    state.files.append(PDFFile(path=target, name="report.pdf", size=1))
    assert state.contains_path(target) is True


def test_contains_path_false_for_same_name_different_folder(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    file_a = dir_a / "report.pdf"
    file_b = dir_b / "report.pdf"
    file_a.write_bytes(b"x")
    file_b.write_bytes(b"x")

    state = AppState()
    state.files.append(PDFFile(path=file_a, name="report.pdf", size=1))

    assert state.contains_path(file_a) is True
    assert state.contains_path(file_b) is False


def test_contains_path_false_on_empty_state(tmp_path):
    state = AppState()
    assert state.contains_path(tmp_path / "anything.pdf") is False


def test_appstate_files_list_is_independent_per_instance():
    """Dataclass field(default_factory=list) must give each AppState its
    own list -- a classic Python pitfall is a shared mutable default
    that would leak files between separate AppState instances.
    """
    state_a = AppState()
    state_b = AppState()
    state_a.files.append(PDFFile(path=Path("a.pdf"), name="a.pdf", size=1))
    assert state_b.files == []
