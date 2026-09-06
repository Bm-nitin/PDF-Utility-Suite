"""
test_pdf_engine.py

Phase 2 placeholder. Real test coverage (merge correctness, page order,
corrupted/encrypted PDFs, compression comparisons, etc.) is added in
Phase 11 per the development plan.
"""

import sys
from pathlib import Path

# Allow running tests directly without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdf_engine  # noqa: E402  (import after sys.path fix, intentional)


def test_module_imports():
    """Sanity check: the engine module imports cleanly and exposes the
    functions the rest of the application depends on.
    """
    assert hasattr(pdf_engine, "validate_pdf")
    assert hasattr(pdf_engine, "get_page_count")
    assert hasattr(pdf_engine, "get_pdf_info")
    assert hasattr(pdf_engine, "merge_pdfs")
    assert hasattr(pdf_engine, "compress_pdf")
    assert hasattr(pdf_engine, "merge_and_compress")
