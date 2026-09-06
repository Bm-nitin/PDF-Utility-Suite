"""
Shared pytest fixtures for tests that need a real, live tkinter
MainWindow.

This file provides one Tk root for the entire pytest session.
"""

import sys
from pathlib import Path
import tkinter as tk

import pytest

# Ensure the project root (parent of tests/) is importable when pytest
# loads this conftest.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui import MainWindow


@pytest.fixture(scope="session")
def _shared_root_window():
    """One Tk root and one MainWindow for the entire test session."""
    root = tk.Tk()
    win = MainWindow(root)
    root.update()

    yield win

    root.destroy()


@pytest.fixture
def window(_shared_root_window):
    """Reset the shared window to a clean state before each test."""
    win = _shared_root_window

    win.state.files.clear()
    win._render_file_list()
    win._update_summary()
    win._update_button_states()
    win.status_var.set("Status: Ready")
    win.root.update()

    yield win