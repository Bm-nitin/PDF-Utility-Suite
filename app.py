"""
app.py

Application entry point for PDF Merger & Compressor.

Responsibilities:
  - Set up logging.
  - Launch the GUI.
  - Catch unexpected top-level errors so the app never dumps a raw
    traceback in front of a normal user.

Keep this file small. All real logic lives in ui.py, pdf_engine.py,
file_manager.py, models.py, and config.py.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from config import BASE_DIR, LOG_FILE_NAME, TEMP_DIR, OUTPUT_DIR


def _setup_logging() -> None:
    log_path = BASE_DIR / LOG_FILE_NAME
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _ensure_directories() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    _setup_logging()
    logger = logging.getLogger(__name__)
    _ensure_directories()

    logger.info("Starting PDF Merger & Compressor")

    try:
        import ui
        ui.run()
    except Exception:
        logger.exception("Unhandled top-level application error")
        # A normal user should never see a raw traceback. In Phase 3+,
        # this will show a native messagebox before exiting; for now we
        # log it cleanly and exit with a non-zero status.
        return 1

    logger.info("Application closed normally")
    return 0


if __name__ == "__main__":
    sys.exit(main())
