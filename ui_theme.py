"""Light and dark colors and the saved appearance preference."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import tkinter as tk

THEMES = ("light", "dark")

_DARK_COLORS = {
    "#f5f6f8": "#101725",
    "#ffffff": "#182132",
    "#e0e2e7": "#303e56",
    "#1a1a1a": "#e5edf9",
    "#6b7280": "#b4c0d3",
    "#9ca3af": "#93a4bd",
    "#2563eb": "#90adff",
    "#1d4ed8": "#a9bdff",
    "#a8c2f7": "#405377",
    "#c7cad1": "#465875",
    "#eef0f3": "#232e42",
    "#e2e5ea": "#2d3b54",
    "#eef2ff": "#243758",
    "#e5e7eb": "#1d283a",
    "#dbeafe": "#2a4063",
    "#c0392b": "#ff9caa",
}

_COLOR_OPTIONS = (
    "-background", "-foreground", "-highlightbackground", "-highlightcolor",
    "-selectbackground", "-selectforeground",
)


def color(light_color: str, theme: str) -> str:
    return _DARK_COLORS.get(light_color, light_color) if theme == "dark" else light_color


def recolor_widgets(
    widget: tk.Misc, old_theme: str, new_theme: str, *, exclude: tuple[tk.Misc, ...] = (),
) -> None:
    """Repaint existing Tk widgets while leaving their state and layout intact."""
    if old_theme == new_theme:
        return
    replacements = {
        color(light, old_theme): color(light, new_theme) for light in _DARK_COLORS
    }

    excluded = {str(widget) for widget in exclude}
    call = widget.tk.call

    def visit(path: str) -> None:
        if path in excluded:
            return
        options = {
            entry[0]: str(entry[-1]).lower()
            for entry in call(path, "configure") if len(entry) == 5
        }
        updates = []
        for option in _COLOR_OPTIONS:
            original = options.get(option)
            if original in replacements:
                updates.extend((option, replacements[original]))
        if updates:
            call(path, "configure", *updates)
        # Ttk's dropdown lists are actual Tk widgets with no Python wrapper.
        for child in widget.tk.splitlist(call("winfo", "children", path)):
            visit(str(child))

    visit(str(widget))


def settings_path() -> Path:
    local_data = os.environ.get("LOCALAPPDATA")
    directory = Path(local_data) if local_data else Path.home() / ".config"
    return directory / "PDFUtilitySuite" / "settings.json"


def load_theme(path: Path) -> str:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "light"
    theme = settings.get("theme") if isinstance(settings, dict) else None
    return theme if theme in THEMES else "light"


def save_theme(path: Path, theme: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".settings-",
            suffix=".json", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"theme": theme}, stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
