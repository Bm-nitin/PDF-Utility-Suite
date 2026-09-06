"""
config.py

Central configuration for PDF Merger & Compressor.

Keep all constants, presets, and tunable values here so they are not
scattered throughout the codebase.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------

APP_NAME = "PDF Merger & Compressor"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = (".pdf",)

DEFAULT_OUTPUT_NAME = "merged.pdf"

# Files larger than this (in bytes) trigger a soft warning to the user
# before processing, not a hard block. 200 MB default.
MAX_FILE_SIZE_WARNING = 200 * 1024 * 1024

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

ICON_PATH = ASSETS_DIR / "icon.ico"
LOGO_PATH = ASSETS_DIR / "logo.png"

# ---------------------------------------------------------------------------
# Compression presets
# ---------------------------------------------------------------------------
# These map to real PyMuPDF save() options. Effectiveness always depends on
# the source PDF's content (text-only PDFs will barely shrink; image-heavy
# or previously-unoptimized PDFs can shrink significantly). We never promise
# a fixed percentage anywhere in the UI or engine.

COMPRESSION_LEVELS = ("low", "recommended", "maximum")

COMPRESSION_PRESETS = {
    "low": {
        "label": "Low",
        "description": "Fast, minimal quality loss. Cleans up structure only.",
        "garbage": 1,       # light garbage collection of unused objects
        "deflate": True,    # compress streams
        "deflate_images": False,
        "deflate_fonts": True,
        "clean": True,
        "image_recompress": False,
        "image_quality": None,
    },
    "recommended": {
        "label": "Recommended",
        "description": "Balanced size reduction with negligible visual impact.",
        "garbage": 3,
        "deflate": True,
        "deflate_images": True,
        "deflate_fonts": True,
        "clean": True,
        "image_recompress": True,
        "image_quality": 80,
    },
    "maximum": {
        "label": "Maximum Compression",
        "description": "Smallest possible file. May reduce image quality.",
        "garbage": 4,
        "deflate": True,
        "deflate_images": True,
        "deflate_fonts": True,
        "clean": True,
        "image_recompress": True,
        "image_quality": 55,
    },
}

DEFAULT_COMPRESSION_LEVEL = "recommended"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE_NAME = "pdf_merger_compressor.log"
