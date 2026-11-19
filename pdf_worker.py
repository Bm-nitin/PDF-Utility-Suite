"""Run the desktop application's PyMuPDF work on a single background thread."""

from concurrent.futures import Future
from queue import SimpleQueue
from threading import Lock, Thread
from typing import Callable

_jobs = SimpleQueue()
_start_lock = Lock()


def submit(target: Callable, *args) -> Future:
    future = Future()
    with _start_lock:
        if not _thread.is_alive():
            _thread.start()
    _jobs.put((target, args, future))
    return future


def _run() -> None:
    while True:
        target, args, future = _jobs.get()
        try:
            if future.set_running_or_notify_cancel():
                future.set_result(target(*args))
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            # Release completed jobs and any bound UI objects while idle.
            del target, args, future


_thread = Thread(target=_run, name="PDF worker", daemon=True)
