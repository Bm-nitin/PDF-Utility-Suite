"""
test_file_manager.py

Phase 2 placeholder. Dialog-based functions are hard to unit test
directly (they block on real OS UI), so Phase 11 will focus tests on
the surrounding logic (path handling, cancel behavior contracts) rather
than driving the actual native dialog.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager  # noqa: E402


def test_module_imports():
    assert hasattr(file_manager, "select_pdf_files")
    assert hasattr(file_manager, "save_pdf_file")
    assert hasattr(file_manager, "confirm_overwrite_if_needed")
