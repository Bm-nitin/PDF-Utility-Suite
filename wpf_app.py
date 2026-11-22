"""Launch the Windows WPF window and connect it to the Python PDF engines."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

from app_logging import get_logger
import pdf_engine
import update_checker
import wpf_backend
import wpf_controller
import wpf_view

logger = get_logger(__name__)

_BOOTSTRAP = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$inputReader = [IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8)
$outputWriter = [IO.StreamWriter]::new([Console]::OpenStandardOutput(), $utf8)
$outputWriter.AutoFlush = $true
try {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms
    $source = $utf8.GetString([Convert]::FromBase64String($inputReader.ReadLine()))
    & ([ScriptBlock]::Create($source))
} catch {
    [Console]::Error.WriteLine($_.ToString())
    [Console]::Error.WriteLine($_.ScriptStackTrace)
    exit 1
}
"""


def interface_script(entry: str = "Start-Interface") -> str:
    palette = json.dumps(wpf_view.PALETTE)
    return "\n".join((
        "$xaml = @'\n" + wpf_view.XAML.strip() + "\n'@",
        "$palette = @'\n" + palette + "\n'@\n$palette = $palette | ConvertFrom-Json",
        wpf_controller.CONTROLLER, wpf_view.WINDOW_COMMANDS, wpf_view.REORDER, entry,
    ))


def _forward_errors(stream) -> None:
    for index, line in enumerate(stream):
        if index == 0:
            logger.error("The Windows interface reported an error")
        logger.debug("WPF: %s", line.rstrip())


def _serve(process: subprocess.Popen) -> None:
    for line in process.stdout:
        request = json.loads(line)
        request_id = request["id"]
        tool = request.get("tool")
        operation = tool if request["action"] == "execute" and tool in wpf_backend.TOOLS else None

        def send(kind: str, value) -> None:
            message = json.dumps({"id": request_id, "kind": kind, "value": value}, default=str)
            process.stdin.write(message + "\n")
            process.stdin.flush()

        if operation:
            logger.info("Started %s", operation)
        try:
            response = wpf_backend.dispatch(request, lambda text: send("progress", text))
        except BrokenPipeError:
            return
        except (pdf_engine.PDFEngineError, update_checker.UpdateCheckError, OSError) as exc:
            if operation:
                logger.warning("Failed %s", operation, exc_info=True)
            send("error", str(exc))
        except Exception:
            logger.exception("WPF command failed")
            send("error", "The operation could not be completed.")
        else:
            if operation:
                status = " with errors" if response.get("errors") else ""
                logger.info("Finished %s%s", operation, status)
            send("result", response)


def _start_powershell(arguments: list[str]) -> subprocess.Popen:
    def start() -> subprocess.Popen:
        return subprocess.Popen(
            arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    if not getattr(sys, "frozen", False):
        return start()

    # The system PowerShell process needs Windows DLLs ahead of bundled DLLs.
    import ctypes
    from ctypes import wintypes

    set_dll_directory = ctypes.windll.kernel32.SetDllDirectoryW
    set_dll_directory.argtypes = [wintypes.LPCWSTR]
    set_dll_directory.restype = wintypes.BOOL
    if not set_dll_directory(None):
        raise ctypes.WinError()
    try:
        return start()
    finally:
        set_dll_directory(sys._MEIPASS)


def run(*, entry: str = "Start-Interface") -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    bootstrap = base64.b64encode(_BOOTSTRAP.encode("utf-16-le")).decode("ascii")
    source = base64.b64encode(interface_script(entry).encode("utf-8")).decode("ascii")
    arguments = [
        str(powershell), "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", bootstrap,
    ]
    with _start_powershell(arguments) as process:
        errors = Thread(target=_forward_errors, args=(process.stderr,), daemon=True)
        errors.start()
        try:
            process.stdin.write(source + "\n")
            process.stdin.flush()
            _serve(process)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
            return_code = process.wait()
            errors.join()
        if return_code:
            raise RuntimeError(f"The WPF window exited with code {return_code}.")
