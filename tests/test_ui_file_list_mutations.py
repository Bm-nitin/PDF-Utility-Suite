"""
test_ui_file_list_mutations.py

Phase 5 tests for file-list mutation behavior: remove, move up, move
down, and clear all -- including the specific requirement that these
operations act on the correct file by identity, not by a stale row
index, even after the list has been reordered.

These tests drive real tkinter widgets (MainWindow), so they need a
display. They're written to work under Xvfb on Linux (as used in this
project's development sandbox) and natively on Windows, where a real
display is always present.

The native file dialog (file_manager.select_pdf_files) is mocked so
these tests don't depend on interactive OS UI -- everything downstream
of that (validation, list mutation, rendering, totals, button states)
is real production code, not a test double.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk
from tkinter import ttk

from ui import MainWindow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pdf_files(tmp_path):
    """Four distinct PDFs, including two different files that share a
    filename (in different folders) -- the case most likely to expose an
    index-based (rather than identity-based) removal/reorder bug.
    """
    (tmp_path / "dirA").mkdir()

    def make(path, pages, w=612, h=792):
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page(width=w, height=h)
        doc.save(path)
        doc.close()

    make(tmp_path / "alpha.pdf", 2)
    make(tmp_path / "beta.pdf", 3)
    make(tmp_path / "report.pdf", 4)
    make(tmp_path / "dirA" / "report.pdf", 5)  # same filename, different folder

    return {
        "alpha": tmp_path / "alpha.pdf",
        "beta": tmp_path / "beta.pdf",
        "report_root": tmp_path / "report.pdf",
        "report_dirA": tmp_path / "dirA" / "report.pdf",
    }


def _import(win, paths):
    """Drives the same code path a real click does, with only the native
    OS dialog mocked out.
    """
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _button_states(row):
    buttons = [c for c in row.winfo_children() if isinstance(c, ttk.Button)]
    up, down, remove = buttons
    return str(up["state"]), str(down["state"]), str(remove["state"])


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------

def test_remove_deletes_only_the_targeted_file(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"], pdf_files["report_root"]])
    assert window.state.total_files == 3

    target = window.state.files[1]  # beta.pdf
    window._on_remove_file(target)
    window.root.update()

    names = [f.name for f in window.state.files]
    assert names == ["alpha.pdf", "report.pdf"]
    assert window.state.total_files == 2


def test_remove_after_reorder_removes_correct_file_not_by_stale_index(window, pdf_files):
    """This is the exact scenario called out in the Phase 5 requirements:
    the Remove control must operate on the specific file, and must not
    accidentally remove a different file after the list has been
    reordered.
    """
    _import(window, [
        pdf_files["report_root"],  # index 0: "report.pdf" (root)
        pdf_files["beta"],         # index 1: "beta.pdf"
        pdf_files["report_dirA"],  # index 2: "report.pdf" (dirA) -- same NAME as index 0
    ])
    assert window.state.total_files == 3

    root_report = window.state.files[0]
    dirA_report = window.state.files[2]
    assert root_report.name == dirA_report.name == "report.pdf"
    assert root_report.path != dirA_report.path

    # Reorder: move the dirA report all the way to the front.
    window._on_move_file_up(dirA_report)
    window._on_move_file_up(dirA_report)
    window.root.update()
    assert window.state.files[0] is dirA_report

    # Now remove the dirA report specifically, by its object reference.
    window._on_remove_file(dirA_report)
    window.root.update()

    remaining = window.state.files
    assert dirA_report not in remaining, "targeted file was not removed"
    assert root_report in remaining, "the WRONG same-named file was removed"
    assert window.state.total_files == 2


def test_remove_updates_totals_and_button_states(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"]])
    assert window.files_count_var.get() == "Files: 2"

    window._on_remove_file(window.state.files[0])
    window.root.update()
    assert window.files_count_var.get() == "Files: 1"
    assert window.pages_count_var.get() == "Pages: 3"  # beta.pdf has 3 pages
    # Phase 6 rule: with exactly 1 file, only Compress Only is enabled.
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "disabled"

    window._on_remove_file(window.state.files[0])
    window.root.update()
    assert window.files_count_var.get() == "Files: 0"
    assert str(window.add_more_btn["state"]) == "disabled"
    assert str(window.clear_all_btn["state"]) == "disabled"
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "disabled"
    assert str(window.merge_compress_btn["state"]) == "disabled"


def test_remove_is_a_safe_noop_if_file_already_gone(window, pdf_files):
    _import(window, [pdf_files["alpha"]])
    target = window.state.files[0]

    window._on_remove_file(target)
    window.root.update()
    assert window.state.total_files == 0

    # Simulate a stale/duplicate callback firing again for the same
    # (now-removed) file -- must not raise or corrupt state.
    window._on_remove_file(target)
    window.root.update()
    assert window.state.total_files == 0


# ---------------------------------------------------------------------------
# Move up / move down
# ---------------------------------------------------------------------------

def test_move_up_swaps_with_previous(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"], pdf_files["report_root"]])
    beta = window.state.files[1]

    window._on_move_file_up(beta)
    window.root.update()

    assert [f.name for f in window.state.files] == ["beta.pdf", "alpha.pdf", "report.pdf"]


def test_move_down_swaps_with_next(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"], pdf_files["report_root"]])
    alpha = window.state.files[0]

    window._on_move_file_down(alpha)
    window.root.update()

    assert [f.name for f in window.state.files] == ["beta.pdf", "alpha.pdf", "report.pdf"]


def test_move_up_at_top_is_a_noop(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"]])
    first = window.state.files[0]

    window._on_move_file_up(first)
    window.root.update()

    assert [f.name for f in window.state.files] == ["alpha.pdf", "beta.pdf"]


def test_move_down_at_bottom_is_a_noop(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"]])
    last = window.state.files[-1]

    window._on_move_file_down(last)
    window.root.update()

    assert [f.name for f in window.state.files] == ["alpha.pdf", "beta.pdf"]


def test_move_does_not_change_totals(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"]])
    before = (
        window.files_count_var.get(),
        window.pages_count_var.get(),
        window.size_var.get(),
    )

    window._on_move_file_up(window.state.files[1])
    window.root.update()

    after = (
        window.files_count_var.get(),
        window.pages_count_var.get(),
        window.size_var.get(),
    )
    assert before == after


def test_edge_row_buttons_disabled_at_top_and_bottom(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"], pdf_files["report_root"]])
    rows = window.file_list_container.winfo_children()
    assert len(rows) == 3

    up0, down0, remove0 = _button_states(rows[0])
    up1, down1, remove1 = _button_states(rows[1])
    up2, down2, remove2 = _button_states(rows[2])

    assert up0 == "disabled"  # first row can't move up
    assert down0 == "normal"
    assert up1 == "normal"
    assert down1 == "normal"
    assert up2 == "normal"
    assert down2 == "disabled"  # last row can't move down
    # Remove is always available regardless of position.
    assert remove0 == remove1 == remove2 == "normal"


def test_edge_states_update_after_reorder(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"], pdf_files["report_root"]])
    first_file = window.state.files[0]

    window._on_move_file_down(first_file)
    window.root.update()

    rows = window.file_list_container.winfo_children()
    up0, _, _ = _button_states(rows[0])
    assert up0 == "disabled", "the new first row's Up button must become disabled"


# ---------------------------------------------------------------------------
# Clear All
# ---------------------------------------------------------------------------

def test_clear_all_empties_list_and_resets_totals(window, pdf_files):
    _import(window, [pdf_files["alpha"], pdf_files["beta"], pdf_files["report_root"]])
    assert window.state.total_files == 3

    window._on_clear_all_clicked()
    window.root.update()

    assert window.state.total_files == 0
    assert window.files_count_var.get() == "Files: 0"
    assert window.pages_count_var.get() == "Pages: 0"
    assert window.size_var.get() == "Size: 0 B"


def test_clear_all_disables_dependent_buttons(window, pdf_files):
    _import(window, [pdf_files["alpha"]])
    window._on_clear_all_clicked()
    window.root.update()

    assert str(window.add_more_btn["state"]) == "disabled"
    assert str(window.clear_all_btn["state"]) == "disabled"
    assert str(window.merge_only_btn["state"]) == "disabled"
    assert str(window.compress_only_btn["state"]) == "disabled"
    assert str(window.merge_compress_btn["state"]) == "disabled"
    assert str(window.select_files_btn["state"]) == "normal"  # always available


def test_clear_all_restores_empty_state_message(window, pdf_files):
    _import(window, [pdf_files["alpha"]])
    window._on_clear_all_clicked()
    window.root.update()

    children = window.file_list_container.winfo_children()
    assert len(children) == 1
    assert isinstance(children[0], tk.Label)


def test_clear_all_on_empty_list_is_a_safe_noop(window):
    assert window.state.total_files == 0
    window._on_clear_all_clicked()
    window.root.update()
    assert window.state.total_files == 0


def test_source_files_untouched_by_list_mutations(window, pdf_files):
    """Removing/reordering/clearing operates on in-memory PDFFile
    records only -- the underlying files on disk must never be touched.
    """
    original_bytes = {
        name: path.read_bytes() for name, path in pdf_files.items()
    }

    _import(window, list(pdf_files.values()))
    window._on_move_file_up(window.state.files[-1])
    window.root.update()
    window._on_remove_file(window.state.files[0])
    window.root.update()
    window._on_clear_all_clicked()
    window.root.update()

    for name, path in pdf_files.items():
        assert path.read_bytes() == original_bytes[name], f"{name} was modified on disk"
