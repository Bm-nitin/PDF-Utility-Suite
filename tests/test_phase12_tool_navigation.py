"""
test_phase12_tool_navigation.py

Dedicated Phase 12 tests for the multi-tool navigation shell added to
MainWindow: switching tools, unknown-tool handling, and -- the specific
new risk this phase introduces -- that switching away from and back to
Merge & Compress preserves application state (imported files, status)
rather than losing it.

Uses the shared session-scoped `window` fixture from tests/conftest.py,
same as every other UI test file since Phase 5.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tool_registry


@pytest.fixture
def pdf_files(tmp_path):
    def make(path, pages=1):
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()

    make(tmp_path / "a.pdf", 2)
    make(tmp_path / "b.pdf", 3)
    return {"a": tmp_path / "a.pdf", "b": tmp_path / "b.pdf"}


def _import(win, paths):
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


# ---------------------------------------------------------------------------
# Default state / navigation basics
# ---------------------------------------------------------------------------

def test_default_tool_on_startup_is_merge_compress(window):
    assert window.current_tool_id == "merge_compress"
    assert window.merge_compress_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()


def test_nav_sidebar_lists_every_registered_tool(window):
    assert set(window.tool_nav_buttons.keys()) == {
        t.id for t in tool_registry.get_all_tools()
    }


# ---------------------------------------------------------------------------
# Switching tools
# ---------------------------------------------------------------------------

def test_selecting_a_coming_soon_tool_shows_placeholder(window):
    # Uses "organize_pages" rather than "split", "remove_pages", or
    # "extract_pages" -- Phase 13 made Split, Phase 14 made Remove
    # Pages, and Phase 15 made Extract Pages real, available tools with
    # their own dedicated workspaces, so none of them is a valid example
    # of the generic coming-soon placeholder anymore.
    window._select_tool("organize_pages")
    window.root.update()

    assert window.current_tool_id == "organize_pages"
    assert not window.merge_compress_view.winfo_ismapped()
    assert window.coming_soon_view.winfo_ismapped()
    assert window.coming_soon_title_label.cget("text") == "Organize Pages"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_a_different_coming_soon_tool_updates_placeholder_text(window):
    window._select_tool("rotate")
    window.root.update()
    assert window.coming_soon_title_label.cget("text") == "Rotate PDF"

    window._select_tool("watermark")
    window.root.update()
    assert window.coming_soon_title_label.cget("text") == "Add Watermark"

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_merge_compress_after_a_coming_soon_tool_restores_workspace(window):
    window._select_tool("protect")
    window.root.update()
    assert not window.merge_compress_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()

    assert window.current_tool_id == "merge_compress"
    assert window.merge_compress_view.winfo_ismapped()
    assert not window.coming_soon_view.winfo_ismapped()


def test_every_registered_tool_can_be_selected_without_error(window):
    """Every tool in the registry, available or not, must be selectable
    without raising -- this is what would catch a typo'd id or a
    forgotten placeholder-view update if a future tool is added.
    """
    for tool in tool_registry.get_all_tools():
        window._select_tool(tool.id)
        window.root.update()
        assert window.current_tool_id == tool.id

    window._select_tool("merge_compress")
    window.root.update()


# ---------------------------------------------------------------------------
# Unknown tool handling
# ---------------------------------------------------------------------------

def test_selecting_unknown_tool_id_is_a_safe_noop(window):
    window._select_tool("merge_compress")
    window.root.update()

    window._select_tool("this_tool_does_not_exist")
    window.root.update()

    assert window.current_tool_id == "merge_compress"
    assert window.merge_compress_view.winfo_ismapped()


def test_selecting_unknown_tool_id_while_on_a_coming_soon_tool_is_a_noop(window):
    # "organize_pages" rather than "split"/"extract_pages" -- see the
    # note in test_selecting_a_coming_soon_tool_shows_placeholder above.
    window._select_tool("organize_pages")
    window.root.update()

    window._select_tool("totally_bogus_id")
    window.root.update()

    assert window.current_tool_id == "organize_pages"
    assert window.coming_soon_view.winfo_ismapped()

    window._select_tool("merge_compress")
    window.root.update()


def test_selecting_empty_string_tool_id_is_a_safe_noop(window):
    window._select_tool("merge_compress")
    window.root.update()

    window._select_tool("")
    window.root.update()

    assert window.current_tool_id == "merge_compress"


# ---------------------------------------------------------------------------
# State preservation across tool switches (the new Phase 12 risk area)
# ---------------------------------------------------------------------------

def test_file_list_survives_switching_away_and_back(window, pdf_files):
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()
    window.root.update()

    _import(window, [pdf_files["a"], pdf_files["b"]])
    assert window.state.total_files == 2

    window._select_tool("split")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()

    assert window.state.total_files == 2
    assert [f.name for f in window.state.files] == ["a.pdf", "b.pdf"]
    assert window.files_count_var.get() == "Files: 2"

    window._on_clear_all_clicked()
    window.root.update()


def test_status_message_survives_switching_away_and_back(window, pdf_files):
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    _import(window, [pdf_files["a"]])
    status_after_import = window.status_var.get()
    assert "Added" in status_after_import

    window._select_tool("watermark")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()

    assert window.status_var.get() == status_after_import

    window._on_clear_all_clicked()
    window.root.update()


def test_merge_only_still_works_after_switching_tools_and_back(window, pdf_files, tmp_path):
    """The specific new regression risk Phase 12 introduces: does the
    actual Merge Only operation still work correctly after the user has
    navigated away to a different tool and back? Proves the widget
    reparenting under the new navigation shell didn't break any of the
    existing merge machinery.
    """
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    _import(window, [pdf_files["a"], pdf_files["b"]])

    window._select_tool("organize_pages")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()

    output = tmp_path / "out.pdf"
    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        deadline = time.time() + 15
        while window._merge_in_progress and time.time() < deadline:
            window.root.update()
            time.sleep(0.02)
        window.root.update()

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5

    window._on_clear_all_clicked()
    window.root.update()


def test_button_states_recomputed_correctly_after_returning_to_merge_compress(window, pdf_files):
    window.state.files.clear()
    window._render_file_list()
    window._update_summary()
    window._update_button_states()

    _import(window, [pdf_files["a"]])  # exactly 1 file
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_only_btn["state"]) == "disabled"

    window._select_tool("unlock")
    window.root.update()
    window._select_tool("merge_compress")
    window.root.update()

    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.merge_compress_btn["state"]) == "disabled"

    window._on_clear_all_clicked()
    window.root.update()
