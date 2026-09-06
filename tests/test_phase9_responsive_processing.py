"""
test_phase9_responsive_processing.py

Dedicated Phase 9 test file for responsive background processing and
Tkinter thread-safety across all three operations (Merge Only, Compress
Only, Merge + Compress).

Where a test needs to deterministically catch an operation "mid-flight"
(to prove file-list mutation is blocked while running, or that a second
operation can't start concurrently), a threading.Event gate is injected
via monkeypatching the relevant pdf_engine function's side_effect, rather
than guessing with sleep() durations. This makes those tests
deterministic: the test controls exactly when the gated call is allowed
to proceed.

For plain "wait for the operation to finish" tests, the existing
event-loop-pumping pattern (root.update() in a bounded while loop) is
used, consistent with every other phase's test files -- it is
deterministic in outcome (bounded by a generous deadline, not asserting
on timing), it just isn't gated mid-operation.
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk

import pdf_engine
from ui import MainWindow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pdf_files(tmp_path):
    def make(path, pages):
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()

    make(tmp_path / "a.pdf", 2)
    make(tmp_path / "b.pdf", 3)
    make(tmp_path / "c.pdf", 1)

    return {
        "a": tmp_path / "a.pdf",
        "b": tmp_path / "b.pdf",
        "c": tmp_path / "c.pdf",
    }


def _import(win, paths):
    with patch("file_manager.select_pdf_files", return_value=paths):
        win._on_select_files_clicked()
        deadline = time.time() + 5
        while win._import_in_progress and time.time() < deadline:
            win.root.update()
            time.sleep(0.02)
        win.root.update()


def _pump_until(win, predicate, timeout=15):
    """Repeatedly calls root.update() until predicate() is True or the
    timeout elapses. Returns True if predicate became true.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        win.root.update()
        time.sleep(0.01)
    return False


class _GatedCall:
    """A deterministic gate for pausing a background worker mid-call.

    Wraps a real function so the FIRST call blocks on a threading.Event
    until the test explicitly releases it (via .release()), then calls
    through to the real function. `.entered` is set the instant the
    wrapped function is entered, so a test can wait for "the worker has
    genuinely started and is now blocked" before proceeding -- avoiding
    any reliance on sleep() to guess when a background thread has begun.
    """

    def __init__(self, real_func):
        self._real_func = real_func
        self._gate = threading.Event()
        self.entered = threading.Event()

    def release(self):
        self._gate.set()

    def __call__(self, *args, **kwargs):
        self.entered.set()
        self._gate.wait(timeout=10)
        return self._real_func(*args, **kwargs)


# ---------------------------------------------------------------------------
# 1. Every operation runs on a background thread (not the main thread)
# ---------------------------------------------------------------------------

def test_merge_only_worker_runs_off_main_thread(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"
    seen_on_main = []
    original = pdf_engine.merge_pdfs

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(pdf_engine, "merge_pdfs", side_effect=wrapper):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_only_clicked()
            assert _pump_until(window, lambda: not window._merge_in_progress)

    assert seen_on_main, "merge_pdfs was never called"
    assert seen_on_main == [False], "merge_pdfs must run off the main thread"


def test_compress_only_worker_runs_off_main_thread(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"]])
    output = tmp_path / "out.pdf"
    seen_on_main = []
    original = pdf_engine.compress_pdf

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(pdf_engine, "compress_pdf", side_effect=wrapper):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_compress_only_clicked()
            assert _pump_until(window, lambda: not window._compress_in_progress)

    assert seen_on_main == [False]


def test_merge_compress_worker_runs_off_main_thread(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"
    seen_on_main = []
    original = pdf_engine.merge_and_compress

    def wrapper(*args, **kwargs):
        seen_on_main.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    with patch.object(pdf_engine, "merge_and_compress", side_effect=wrapper):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_compress_clicked()
            assert _pump_until(window, lambda: not window._mergecompress_in_progress)

    assert seen_on_main == [False]


# ---------------------------------------------------------------------------
# 2. GUI event loop remains responsive during a long operation
# ---------------------------------------------------------------------------

def test_gui_remains_responsive_during_merge(window, tmp_path):
    def make(path, pages):
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()

    p1 = tmp_path / "big1.pdf"
    p2 = tmp_path / "big2.pdf"
    make(p1, 150)
    make(p2, 150)

    _import(window, [p1, p2])
    output = tmp_path / "out.pdf"

    update_count = 0
    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        deadline = time.time() + 20
        while window._merge_in_progress and time.time() < deadline:
            window.root.update()
            update_count += 1
            time.sleep(0.005)

    assert update_count > 3, "main thread must keep pumping events during merge"
    assert output.exists()


# ---------------------------------------------------------------------------
# 3 & 7. Operation state: buttons disable/restore; file-list mutation
# controls are also disabled while busy; multiple operations can't run
# simultaneously; the operation uses the snapshot taken at start.
# ---------------------------------------------------------------------------

def test_all_action_buttons_disabled_while_merge_runs(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    gate = _GatedCall(pdf_engine.merge_pdfs)
    with patch.object(pdf_engine, "merge_pdfs", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_only_clicked()
            assert gate.entered.wait(timeout=5), "worker never started"
            window.root.update()

            assert str(window.merge_only_btn["state"]) == "disabled"
            assert str(window.compress_only_btn["state"]) == "disabled"
            assert str(window.merge_compress_btn["state"]) == "disabled"
            assert str(window.select_files_btn["state"]) == "disabled"
            assert str(window.add_more_btn["state"]) == "disabled"
            assert str(window.clear_all_btn["state"]) == "disabled"

            gate.release()
            assert _pump_until(window, lambda: not window._merge_in_progress)

    assert str(window.merge_only_btn["state"]) == "normal"


def test_file_list_mutation_controls_disabled_while_operation_runs(window, pdf_files, tmp_path):
    """Phase 9 requirement: file-list mutation controls (Remove/Move Up/
    Move Down/Clear All) must be disabled while an operation is running,
    to prevent the user from confusingly editing a list a background
    operation is (or was) working from.
    """
    _import(window, [pdf_files["a"], pdf_files["b"], pdf_files["c"]])
    output = tmp_path / "out.pdf"

    gate = _GatedCall(pdf_engine.merge_pdfs)
    with patch.object(pdf_engine, "merge_pdfs", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_only_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            assert str(window.clear_all_btn["state"]) == "disabled"

            rows = window.file_list_container.winfo_children()
            assert len(rows) == 3
            for row in rows:
                buttons = [
                    c for c in row.winfo_children()
                    if hasattr(c, "winfo_class") and c.winfo_class() == "TButton"
                ]
                for btn in buttons:
                    assert str(btn["state"]) == "disabled"

            before = list(window.state.files)
            window._on_remove_file(window.state.files[0])
            window._on_move_file_up(window.state.files[1])
            window._on_clear_all_clicked()
            window.root.update()
            assert window.state.files == before, "file list must be unchanged while busy"

            gate.release()
            assert _pump_until(window, lambda: not window._merge_in_progress)

    rows = window.file_list_container.winfo_children()
    for row in rows:
        buttons = [
            c for c in row.winfo_children()
            if hasattr(c, "winfo_class") and c.winfo_class() == "TButton"
        ]
        remove_btn = buttons[-1]
        assert str(remove_btn["state"]) == "normal"


def test_multiple_operations_cannot_start_simultaneously(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    gate = _GatedCall(pdf_engine.merge_pdfs)
    with patch.object(pdf_engine, "merge_pdfs", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output) as mock_save, \
             patch("file_manager.select_output_folder") as mock_folder:
            window._on_merge_only_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()

            calls_before = mock_save.call_count

            window._on_compress_only_clicked()
            window._on_merge_compress_clicked()
            window._on_merge_only_clicked()
            window.root.update()

            assert mock_save.call_count == calls_before, (
                "no additional Save As dialog should have been triggered"
            )
            mock_folder.assert_not_called()

            gate.release()
            assert _pump_until(window, lambda: not window._merge_in_progress)

    assert output.exists()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 5  # confirms only the ONE real merge ran


def test_operation_uses_snapshot_not_live_state_if_list_changes_underneath(window, pdf_files, tmp_path):
    """Even bypassing the UI-level disable (which the previous test
    already proves is effective), the operation itself must be immune:
    it captured a plain list of Path objects before starting, so
    mutating window.state.files afterward cannot change what gets
    processed.
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    gate = _GatedCall(pdf_engine.merge_pdfs)
    with patch.object(pdf_engine, "merge_pdfs", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_only_clicked()
            assert gate.entered.wait(timeout=5)

            window.state.files.append(window.state.files[0])

            gate.release()
            assert _pump_until(window, lambda: not window._merge_in_progress)

    with pymupdf.open(output) as doc:
        assert doc.page_count == 5  # original 2-file (2pp+3pp) snapshot


# ---------------------------------------------------------------------------
# 5 & 9. Completion / failure handling; worker exceptions don't crash
# ---------------------------------------------------------------------------

def test_successful_operation_restores_controls_and_preserves_file_list(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    names_before = [f.name for f in window.state.files]
    output = tmp_path / "out.pdf"

    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)

    assert [f.name for f in window.state.files] == names_before
    assert str(window.merge_only_btn["state"]) == "normal"
    assert str(window.compress_only_btn["state"]) == "normal"
    assert str(window.merge_compress_btn["state"]) == "normal"


def test_unexpected_worker_exception_does_not_crash_and_recovers(window, pdf_files, tmp_path):
    """A non-PDFEngineError exception raised inside the worker (e.g. a
    genuine bug, or an OS-level surprise) must still be caught, reported
    cleanly, and the UI must recover -- never propagate as an unhandled
    exception that could crash the app or leave controls stuck disabled.
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pdf_engine, "merge_pdfs", side_effect=ValueError("boom -- totally unexpected")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror") as mock_error:
                window._on_merge_only_clicked()
                assert _pump_until(window, lambda: not window._merge_in_progress)

    assert mock_error.called
    assert "failed" in window.status_var.get().lower()
    assert str(window.merge_only_btn["state"]) == "normal", "controls must not be left stuck disabled"
    assert str(window.select_files_btn["state"]) == "normal"
    assert not output.exists(), "no partial output should exist after a worker exception"


def test_unexpected_exception_in_compress_batch_does_not_crash(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with patch.object(pdf_engine, "compress_pdf", side_effect=ValueError("boom")):
        with patch("file_manager.select_output_folder", return_value=output_dir):
            with patch("tkinter.messagebox.showwarning") as mock_warn:
                window._on_compress_only_clicked()
                assert _pump_until(window, lambda: not window._compress_in_progress)

    assert mock_warn.called
    assert "could not be compressed" in window.status_var.get()
    assert str(window.compress_only_btn["state"]) == "normal"


def test_cancelled_save_as_does_not_start_a_worker(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"]])

    with patch("file_manager.save_pdf_file", return_value=None):
        window._on_merge_only_clicked()
        window.root.update()

    assert not window._merge_in_progress
    assert window.status_var.get() == "Status: Ready"


def test_cancelled_folder_picker_does_not_start_a_worker(window, pdf_files):
    _import(window, [pdf_files["a"], pdf_files["b"]])

    with patch("file_manager.select_output_folder", return_value=None):
        window._on_compress_only_clicked()
        window.root.update()

    assert not window._compress_in_progress
    assert window.status_var.get() == "Status: Ready"


# ---------------------------------------------------------------------------
# 2 & 9. No Tkinter widget is touched from a worker thread
# ---------------------------------------------------------------------------

def test_apply_merge_result_raises_if_called_off_main_thread(window):
    """Direct enforcement of the "no widget touched from a worker
    thread" requirement: _apply_merge_result (and its siblings) assert
    they're running on the main thread. Calling one from a real
    background thread must raise, proving the guard is live -- not just
    documentation.
    """
    error_holder = []

    def call_from_thread():
        try:
            window._apply_merge_result({
                "type": "done", "success": True,
                "output_path": Path("x.pdf"), "error": None,
            })
        except RuntimeError as exc:
            error_holder.append(exc)

    t = threading.Thread(target=call_from_thread)
    t.start()
    t.join(timeout=5)

    assert error_holder, "expected a RuntimeError when called off the main thread"
    assert "non-main thread" in str(error_holder[0])


def test_normal_completion_flow_does_not_trigger_main_thread_guard(window, pdf_files, tmp_path):
    """Sanity check: the guard added above must NOT fire during a normal
    run, where _apply_merge_result is only ever invoked by
    _poll_merge_queue via root.after() (main thread).
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch("file_manager.save_pdf_file", return_value=output):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)

    assert output.exists()  # got here without the guard raising


# ---------------------------------------------------------------------------
# 4. Progress reporting -- merge now reports real per-file progress
# ---------------------------------------------------------------------------

def test_merge_only_reports_per_file_progress(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"], pdf_files["c"]])
    output = tmp_path / "out.pdf"

    seen = []
    original_set = window.status_var.set

    def tracking_set(value):
        seen.append(value)
        original_set(value)

    window.status_var.set = tracking_set
    try:
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_only_clicked()
            assert _pump_until(window, lambda: not window._merge_in_progress)
    finally:
        window.status_var.set = original_set

    assert any("file 1 of 3" in m for m in seen)
    assert any("file 3 of 3" in m for m in seen)


def test_merge_compress_reports_merge_and_compress_stages(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    seen = []
    original_set = window.status_var.set

    def tracking_set(value):
        seen.append(value)
        original_set(value)

    window.status_var.set = tracking_set
    try:
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_compress_clicked()
            assert _pump_until(window, lambda: not window._mergecompress_in_progress)
    finally:
        window.status_var.set = original_set

    assert any("file 1 of 2" in m for m in seen)
    assert any("Compressing" in m for m in seen)


def test_progress_bar_uses_indeterminate_mode_not_fake_percentage(window, pdf_files, tmp_path):
    """Per requirement 4: progress that can't be accurately measured
    must use indeterminate mode, never a fabricated percentage.
    """
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    gate = _GatedCall(pdf_engine.merge_pdfs)
    with patch.object(pdf_engine, "merge_pdfs", side_effect=gate):
        with patch("file_manager.save_pdf_file", return_value=output):
            window._on_merge_only_clicked()
            assert gate.entered.wait(timeout=5)
            window.root.update()
            assert str(window.progress_bar["mode"]) == "indeterminate"
            gate.release()
            assert _pump_until(window, lambda: not window._merge_in_progress)


# ---------------------------------------------------------------------------
# 8. Output safety preserved (atomic save, no source modification)
# ---------------------------------------------------------------------------

def test_no_partial_output_after_worker_exception_merge(window, pdf_files, tmp_path):
    _import(window, [pdf_files["a"], pdf_files["b"]])
    output = tmp_path / "out.pdf"

    with patch.object(pymupdf.Document, "save", side_effect=OSError("disk full")):
        with patch("file_manager.save_pdf_file", return_value=output):
            with patch("tkinter.messagebox.showerror"):
                window._on_merge_only_clicked()
                assert _pump_until(window, lambda: not window._merge_in_progress)

    assert not output.exists()
    assert list(tmp_path.glob(".tmp_*")) == []


def test_sources_untouched_after_full_operation_cycle(window, pdf_files, tmp_path):
    originals = {name: p.read_bytes() for name, p in pdf_files.items()}
    _import(window, [pdf_files["a"], pdf_files["b"], pdf_files["c"]])

    with patch("file_manager.save_pdf_file", return_value=tmp_path / "out.pdf"):
        window._on_merge_only_clicked()
        assert _pump_until(window, lambda: not window._merge_in_progress)

    for name, path in pdf_files.items():
        assert path.read_bytes() == originals[name]
