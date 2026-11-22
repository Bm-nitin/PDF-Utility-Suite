"""Persistent, local diagnostics for PDF Utility Suite."""

from __future__ import annotations

from contextlib import contextmanager
from copy import copy
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from config import LOG_FILE_NAME, user_data_dir

_logger = logging.getLogger("PDFUtilitySuite")
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_MAX_LOG_BYTES = 1024 * 1024
_LOG_BACKUPS = 3


def get_logger(module_name: str) -> logging.Logger:
    return _logger.getChild(module_name)


def log_path() -> Path | None:
    for handler in _logger.handlers:
        if isinstance(handler, _LogFileHandler):
            return Path(handler.baseFilename)
    return None


@contextmanager
def _rotation_lock(path: Path):
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class _LogFileHandler(RotatingFileHandler):
    def __init__(self, path: Path):
        self.lock_path = path.with_name(".app.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with _rotation_lock(self.lock_path), path.open("a", encoding="utf-8"):
            pass
        super().__init__(
            path, maxBytes=_MAX_LOG_BYTES, backupCount=_LOG_BACKUPS,
            encoding="utf-8", delay=True,
        )

    def emit(self, record: logging.LogRecord) -> None:
        # Release the log file after each write so another Windows process can rotate it.
        try:
            with _rotation_lock(self.lock_path):
                try:
                    super().emit(record)
                finally:
                    if self.stream is not None:
                        self.stream.close()
                        self.stream = None
        except OSError:
            self.handleError(record)


class _LogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Console handlers may render the original traceback for local development.
        safe_record = copy(record)
        safe_record.exc_text = None
        safe_record.stack_info = None
        return super().format(safe_record)

    def formatException(self, exc_info) -> str:
        return exc_info[0].__name__


def setup_logging() -> None:
    if log_path() is not None:
        return
    _logger.setLevel(logging.INFO)
    if sys.stdout is not None:
        logging.basicConfig(level=logging.INFO, format=_FORMAT, stream=sys.stdout)

    directories = [user_data_dir()]
    if getattr(sys, "frozen", False):
        directories.insert(0, Path(sys.executable).resolve().parent)
    for directory in directories:
        try:
            handler = _LogFileHandler(directory / "Logs" / LOG_FILE_NAME)
        except OSError:
            continue
        handler.setLevel(logging.INFO)
        handler.setFormatter(_LogFormatter(_FORMAT))
        _logger.addHandler(handler)
        return
    _logger.warning("Could not create the application log")
