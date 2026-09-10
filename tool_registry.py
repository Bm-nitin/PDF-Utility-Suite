"""
tool_registry.py

A clean, central representation of the application's tools -- what the
app is capable of doing, independent of any specific tool's UI or
processing logic.

Deliberately a single flat module rather than a `tools/` package: the
rest of this project is flat (models.py, config.py, etc.), and a Tool
dataclass plus a short lookup table doesn't need package machinery.
Following the "do not over-engineer this" guidance directly.

This module has zero dependency on tkinter, pdf_engine, or any other
part of the app -- it's pure data and pure functions, fully testable
without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# ---------------------------------------------------------------------------
# Tool model
# ---------------------------------------------------------------------------

# Two statuses are all this project currently needs. "available" means the
# tool has a real, working UI and processing behind it right now.
# "coming_soon" means it's on the roadmap (see the master plan's tool list)
# but not implemented yet -- selecting it shows a placeholder, not a broken
# feature.
STATUS_AVAILABLE = "available"
STATUS_COMING_SOON = "coming_soon"


@dataclass(frozen=True)
class Tool:
    """A single application tool/operation.

    Frozen (immutable) because tools are static, fixed data -- nothing
    in the running application should ever need to mutate a Tool after
    the registry is built.
    """

    id: str
    name: str
    description: str
    status: str = STATUS_AVAILABLE
    category: str = "general"

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_AVAILABLE


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# One entry per operation the app supports or plans to support. Adding a
# future tool (Rotate, Watermark, etc.) to the app starts here: add one Tool
# entry with status=STATUS_COMING_SOON now, and flip it to
# STATUS_AVAILABLE once that tool's real UI/processing lands in a later
# phase -- no other file needs to know the tool's name, id, or
# description hardcoded anywhere else.

MERGE_COMPRESS = Tool(
    id="merge_compress",
    name="Merge & Compress",
    description=(
        "Merge multiple PDFs into one file, compress PDFs to reduce "
        "their size, or do both in a single step."
    ),
    status=STATUS_AVAILABLE,
    category="combine",
)

SPLIT = Tool(
    id="split",
    name="Split PDF",
    description="Split a PDF into multiple smaller files.",
    status=STATUS_AVAILABLE,
    category="organize",
)

REMOVE_PAGES = Tool(
    id="remove_pages",
    name="Remove Pages",
    description="Delete specific pages from a PDF.",
    status=STATUS_COMING_SOON,
    category="organize",
)

EXTRACT_PAGES = Tool(
    id="extract_pages",
    name="Extract Pages",
    description="Save specific pages of a PDF as a new file.",
    status=STATUS_COMING_SOON,
    category="organize",
)

ORGANIZE_PAGES = Tool(
    id="organize_pages",
    name="Organize Pages",
    description="Reorder, rotate, or remove pages within a single PDF.",
    status=STATUS_COMING_SOON,
    category="organize",
)

ROTATE = Tool(
    id="rotate",
    name="Rotate PDF",
    description="Rotate one or more pages of a PDF.",
    status=STATUS_COMING_SOON,
    category="organize",
)

PROTECT = Tool(
    id="protect",
    name="Protect PDF",
    description="Add a password to a PDF to restrict access.",
    status=STATUS_COMING_SOON,
    category="security",
)

UNLOCK = Tool(
    id="unlock",
    name="Unlock PDF",
    description="Remove a known password from a PDF.",
    status=STATUS_COMING_SOON,
    category="security",
)

PAGE_NUMBERS = Tool(
    id="page_numbers",
    name="Add Page Numbers",
    description="Add page numbers to every page of a PDF.",
    status=STATUS_COMING_SOON,
    category="edit",
)

WATERMARK = Tool(
    id="watermark",
    name="Add Watermark",
    description="Overlay a text or image watermark on every page of a PDF.",
    status=STATUS_COMING_SOON,
    category="edit",
)

IMAGES_TO_PDF = Tool(
    id="images_to_pdf",
    name="Images \u2192 PDF",
    description="Combine one or more images into a new PDF file.",
    status=STATUS_COMING_SOON,
    category="create",
)

_ALL_TOOLS: List[Tool] = [
    MERGE_COMPRESS,
    SPLIT,
    REMOVE_PAGES,
    EXTRACT_PAGES,
    ORGANIZE_PAGES,
    ROTATE,
    PROTECT,
    UNLOCK,
    PAGE_NUMBERS,
    WATERMARK,
    IMAGES_TO_PDF,
]

# Fail loudly and immediately at import time if a future edit ever
# introduces a duplicate id -- much easier to diagnose than a subtle
# "wrong tool selected" bug discovered later.
assert len({t.id for t in _ALL_TOOLS}) == len(_ALL_TOOLS), (
    "Duplicate tool id in tool_registry._ALL_TOOLS"
)

DEFAULT_TOOL_ID = MERGE_COMPRESS.id


def get_all_tools() -> List[Tool]:
    """Returns every registered tool, in display order. Returns a copy
    so callers can't accidentally mutate the registry's internal list.
    """
    return list(_ALL_TOOLS)


def get_tool(tool_id: str) -> Optional[Tool]:
    """Looks up a tool by id. Returns None for an unknown id -- this is
    a normal, expected outcome (e.g. a stale/typo'd id), not an error
    condition, so callers should handle None rather than catching an
    exception.
    """
    for tool in _ALL_TOOLS:
        if tool.id == tool_id:
            return tool
    return None


def get_tools_by_category(category: str) -> List[Tool]:
    return [t for t in _ALL_TOOLS if t.category == category]
