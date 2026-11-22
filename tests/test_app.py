"""
test_app.py

Direct tests for app.py -- the application entry point.

Phase 11 gap: app.py had zero test coverage of any kind before this
file. Its logic is small (set up logging, ensure directories exist,
launch the GUI, catch unhandled top-level exceptions) but was completely
unverified.

main() calls ui.run(), which creates a real Tk() root and calls
mainloop() -- a genuinely blocking call in production. These tests mock
ui.run() so they can verify main()'s control flow (return codes,
exception handling) without ever starting a real, blocking GUI event
loop.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app
import app_logging
import config


def test_ensure_directories_creates_temp_and_output(tmp_path, monkeypatch):
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)

    assert not temp_dir.exists()
    assert not output_dir.exists()

    app._ensure_directories()

    assert temp_dir.is_dir()
    assert output_dir.is_dir()


def test_ensure_directories_is_safe_to_call_when_dirs_already_exist(tmp_path, monkeypatch):
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    temp_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)

    app._ensure_directories()  # must not raise on already-existing dirs

    assert temp_dir.is_dir()
    assert output_dir.is_dir()


def test_main_returns_zero_on_normal_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(app, "OUTPUT_DIR", tmp_path / "output")

    with patch.object(app, "_setup_logging"):
        with patch("ui.run", return_value=None):
            result = app.main()

    assert result == 0


def test_main_returns_one_and_does_not_raise_on_unhandled_exception(tmp_path, monkeypatch):
    """The whole point of app.py's try/except around ui.run(): a normal
    user must never see a raw traceback if something inside the GUI
    layer fails catastrophically at startup.
    """
    monkeypatch.setattr(app, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(app, "OUTPUT_DIR", tmp_path / "output")

    with patch.object(app, "_setup_logging"):
        with patch("ui.run", side_effect=RuntimeError("simulated startup failure")):
            result = app.main()  # must not raise

    assert result == 1


def test_main_creates_directories_before_launching_ui(tmp_path, monkeypatch):
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)

    seen_dirs_exist_at_launch = {}

    def fake_run():
        seen_dirs_exist_at_launch["temp"] = temp_dir.is_dir()
        seen_dirs_exist_at_launch["output"] = output_dir.is_dir()

    with patch.object(app, "_setup_logging"):
        with patch("ui.run", side_effect=fake_run):
            app.main()

    assert seen_dirs_exist_at_launch == {"temp": True, "output": True}


def test_frozen_app_writes_persistent_log_beside_exe(tmp_path, monkeypatch, isolated_logging):
    install = tmp_path / "Custom installation"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "PDF-Merger-Compressor.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    log_file = install / "Logs" / "app.txt"

    app._setup_logging()
    isolated_logging.info("First launch")
    for handler in isolated_logging.handlers[:]:
        isolated_logging.removeHandler(handler)
        handler.close()
    app._setup_logging()
    isolated_logging.info("Second launch")

    assert app_logging.log_path() == log_file
    text = log_file.read_text(encoding="utf-8")
    assert "First launch" in text
    assert "Second launch" in text


def test_logging_falls_back_if_install_folder_is_unwritable(tmp_path, monkeypatch, isolated_logging):
    install = tmp_path / "Installation"
    install.mkdir()
    (install / "Logs").touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "app.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    app._setup_logging()

    assert app_logging.log_path() == tmp_path / "PDFUtilitySuite" / "Logs" / config.LOG_FILE_NAME
    assert app_logging.log_path().is_file()


def test_log_keeps_error_type_without_document_details(tmp_path, monkeypatch, isolated_logging):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app._setup_logging()

    try:
        raise OSError("Private.pdf with password secret")
    except OSError:
        isolated_logging.exception("PDF operation failed")

    text = app_logging.log_path().read_text(encoding="utf-8")
    assert "PDF operation failed" in text
    assert "OSError" in text
    assert "Private.pdf" not in text
    assert "secret" not in text


def test_log_rotation_keeps_recent_backups(tmp_path, monkeypatch, isolated_logging):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_logging, "_MAX_LOG_BYTES", 240)
    app._setup_logging()
    for index in range(30):
        isolated_logging.info("Diagnostic event %s", index)

    log_file = app_logging.log_path()
    assert "Diagnostic event 29" in log_file.read_text(encoding="utf-8")
    assert {path.name for path in log_file.parent.glob("app.txt*")} == {
        "app.txt", "app.txt.1", "app.txt.2", "app.txt.3",
    }
