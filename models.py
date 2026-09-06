"""
models.py

Application data structures. No GUI code and no direct PDF-library calls
belong here -- this module just describes shapes of data used elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class PDFFile:
    """Represents a single imported PDF and its known metadata.

    `size` is in bytes. `page_count` is None until it has actually been
    read from the file (we never guess or fake it).
    """

    path: Path
    name: str
    size: int
    page_count: Optional[int] = None
    is_encrypted: bool = False

    @property
    def size_display(self) -> str:
        return format_file_size(self.size)

    @property
    def page_count_display(self) -> str:
        if self.page_count is None:
            return "?"
        return f"{self.page_count} page{'s' if self.page_count != 1 else ''}"


@dataclass
class AppState:
    """Holds the full mutable state of the application at runtime."""

    files: List[PDFFile] = field(default_factory=list)
    compression_level: str = "recommended"
    is_processing: bool = False

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_pages(self) -> int:
        return sum(f.page_count or 0 for f in self.files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def total_size_display(self) -> str:
        return format_file_size(self.total_size)

    def contains_path(self, path: Path) -> bool:
        """Duplicate check based on resolved absolute path, per spec:
        the same path twice is a duplicate; the same filename from two
        different folders is NOT a duplicate.
        """
        resolved = path.resolve()
        return any(f.path.resolve() == resolved for f in self.files)


def format_file_size(size_bytes: int) -> str:
    """Human-readable file size, e.g. '2.4 MB'."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    units = ["KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PB"
