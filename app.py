"""
app.py

Application entry point for PDF Utility Suite.

Responsibilities:
  - Set up logging.
  - Launch the GUI.
  - Catch unexpected top-level errors so the app never dumps a raw
    traceback in front of a normal user.

Keep this file small. All real logic lives in ui.py, pdf_engine.py,
file_manager.py, models.py, and config.py.
"""

from __future__ import annotations

import sys

from app_logging import get_logger, setup_logging as _setup_logging
from config import APP_MUTEX_NAME, APP_VERSION, TEMP_DIR, OUTPUT_DIR


def _mark_app_running() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    import ctypes
    from ctypes import wintypes

    create_mutex = ctypes.windll.kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    # Inno Setup checks these names until Windows closes the handles at process exit.
    create_mutex(None, False, APP_MUTEX_NAME)
    create_mutex(None, False, f"Global\\{APP_MUTEX_NAME}")


def _ensure_directories() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    _mark_app_running()
    _setup_logging()
    logger = get_logger(__name__)
    logger.info("Starting PDF Utility Suite version %s", APP_VERSION)

    try:
        _ensure_directories()
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
