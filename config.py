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

# NOTE: "image_recompress"/"image_quality" below describe the INTENDED
# behavior of "recommended" and "maximum" -- lossy recompression of
# embedded raster images. A confirmed image-corruption bug (see
# pdf_engine.py's LOSSY_IMAGE_RECOMPRESSION_ENABLED and
# _recompress_images() docstrings) means that code path is currently
# disabled for every preset at the pdf_engine.py level, regardless of
# these values, so none of the three presets below actually changes
# image quality right now -- all three perform structural/lossless
# optimization only (the "garbage"/"deflate"/"clean" settings), differing
# only in how aggressive that structural cleanup is. The "description"
# strings reflect that current, temporary reality rather than the
# target behavior, so the UI never claims a lossy effect that isn't
# happening; update them back once LOSSY_IMAGE_RECOMPRESSION_ENABLED is
# safely re-enabled.
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
        "description": (
            "Balanced structural cleanup. Image quality is currently "
            "left untouched."
        ),
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
        "description": (
            "Most aggressive structural cleanup. Image quality is "
            "currently left untouched."
        ),
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
