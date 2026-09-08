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
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)

    # _setup_logging() is mocked out here (and below) because it's
    # orthogonal to what these tests actually verify -- main()'s control
    # flow (return codes, exception handling, ordering). Calling the
    # real _setup_logging() repeatedly across several tests in one
    # process, each against a different ephemeral tmp_path, creates a
    # real logging.FileHandler every time; logging.basicConfig() is a
    # no-op after its first real call in a process, so most of those
    # handler objects are immediately orphaned, and when Python's GC
    # later finalizes one against a tmp_path pytest has already deleted,
    # it raises a benign-but-noisy PytestUnraisableExceptionWarning under
    # `-W error`. This was found and fixed during Phase 11 exactly
    # because the -W error stress-run this project relies on (since
    # Phase 9) caught it -- confirming that check's value.
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
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)

    with patch.object(app, "_setup_logging"):
        with patch("ui.run", side_effect=RuntimeError("simulated startup failure")):
            result = app.main()  # must not raise

    assert result == 1


def test_main_creates_directories_before_launching_ui(tmp_path, monkeypatch):
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)

    seen_dirs_exist_at_launch = {}

    def fake_run():
        seen_dirs_exist_at_launch["temp"] = temp_dir.is_dir()
        seen_dirs_exist_at_launch["output"] = output_dir.is_dir()

    with patch.object(app, "_setup_logging"):
        with patch("ui.run", side_effect=fake_run):
            app.main()

    assert seen_dirs_exist_at_launch == {"temp": True, "output": True}


def test_setup_logging_actually_configures_a_file_handler(tmp_path, monkeypatch):
    """Separate, dedicated test for _setup_logging() itself (the thing
    the other tests above deliberately avoid exercising repeatedly) --
    called exactly once, matching real usage, so it doesn't hit the
    orphaned-handler issue described above.
    """
    import logging

    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    log_path = tmp_path / app.LOG_FILE_NAME

    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    try:
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)

        app._setup_logging()

        assert log_path.exists() or any(
            isinstance(h, logging.FileHandler) for h in root_logger.handlers
        )
    finally:
        for h in root_logger.handlers[:]:
            h.close()
            root_logger.removeHandler(h)
        for h in original_handlers:
            root_logger.addHandler(h)
        root_logger.setLevel(original_level)


def test_log_file_name_is_defined_and_reasonable():
    assert config.LOG_FILE_NAME.endswith(".log")
    assert "/" not in config.LOG_FILE_NAME and "\\" not in config.LOG_FILE_NAME
