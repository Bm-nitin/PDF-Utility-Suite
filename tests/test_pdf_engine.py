"""
test_pdf_engine.py

Phase 2 placeholder. Real test coverage (merge correctness, page order,
corrupted/encrypted PDFs, compression comparisons, etc.) is added in
Phase 11 per the development plan.
"""

import sys
from pathlib import Path

import pytest
from PIL import Image

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


@pytest.mark.parametrize("allow_encrypted", [False, True])
def test_validate_pdf_rejects_a_renamed_image(tmp_path, allow_encrypted):
    disguised = tmp_path / "image.pdf"
    with Image.new("RGB", (8, 8)) as image:
        image.save(disguised, format="PNG")

    with pytest.raises(pdf_engine.InvalidPDFError, match="is not a PDF file"):
        pdf_engine.validate_pdf(disguised, allow_encrypted=allow_encrypted)
