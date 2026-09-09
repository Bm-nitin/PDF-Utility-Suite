"""
test_tool_registry.py

Direct tests for tool_registry.py -- the Tool model and central
registry introduced in Phase 12. Pure data/logic, no tkinter dependency,
so these run without a display.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tool_registry
from tool_registry import Tool, STATUS_AVAILABLE, STATUS_COMING_SOON


# ---------------------------------------------------------------------------
# Tool model
# ---------------------------------------------------------------------------

def test_tool_has_required_fields():
    t = Tool(id="x", name="X Tool", description="Does X.")
    assert t.id == "x"
    assert t.name == "X Tool"
    assert t.description == "Does X."


def test_tool_defaults_to_available():
    t = Tool(id="x", name="X", description="d")
    assert t.status == STATUS_AVAILABLE
    assert t.is_available is True


def test_tool_coming_soon_is_not_available():
    t = Tool(id="x", name="X", description="d", status=STATUS_COMING_SOON)
    assert t.is_available is False


def test_tool_default_category():
    t = Tool(id="x", name="X", description="d")
    assert t.category == "general"


def test_tool_is_immutable():
    t = Tool(id="x", name="X", description="d")
    try:
        t.name = "changed"
        assert False, "Tool should be frozen/immutable"
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Registry contents
# ---------------------------------------------------------------------------

def test_merge_compress_is_the_only_available_tool():
    """Phase 12 explicitly does not implement any new PDF operations --
    Merge & Compress must be the only tool marked available.
    """
    available = [t for t in tool_registry.get_all_tools() if t.is_available]
    assert len(available) == 1
    assert available[0].id == "merge_compress"


def test_all_ten_future_tools_are_registered_as_coming_soon():
    expected_ids = {
        "split", "remove_pages", "extract_pages", "organize_pages",
        "rotate", "protect", "unlock", "page_numbers", "watermark",
        "images_to_pdf",
    }
    coming_soon_ids = {
        t.id for t in tool_registry.get_all_tools() if not t.is_available
    }
    assert coming_soon_ids == expected_ids


def test_registry_has_exactly_eleven_tools():
    assert len(tool_registry.get_all_tools()) == 11


def test_all_tool_ids_are_unique():
    tools = tool_registry.get_all_tools()
    ids = [t.id for t in tools]
    assert len(ids) == len(set(ids))


def test_all_tools_have_non_empty_name_and_description():
    for t in tool_registry.get_all_tools():
        assert t.name.strip()
        assert t.description.strip()


def test_default_tool_id_is_merge_compress():
    assert tool_registry.DEFAULT_TOOL_ID == "merge_compress"


def test_default_tool_id_resolves_to_an_available_tool():
    tool = tool_registry.get_tool(tool_registry.DEFAULT_TOOL_ID)
    assert tool is not None
    assert tool.is_available


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def test_get_tool_returns_correct_tool():
    tool = tool_registry.get_tool("merge_compress")
    assert tool is not None
    assert tool.name == "Merge & Compress"


def test_get_tool_returns_none_for_unknown_id():
    assert tool_registry.get_tool("this_tool_does_not_exist") is None


def test_get_tool_returns_none_for_empty_string():
    assert tool_registry.get_tool("") is None


def test_get_tool_is_case_sensitive():
    """IDs are internal, stable identifiers, not display text -- no
    case-insensitive matching should be implied.
    """
    assert tool_registry.get_tool("MERGE_COMPRESS") is None


def test_get_all_tools_returns_a_copy_not_the_internal_list():
    tools = tool_registry.get_all_tools()
    tools.append(Tool(id="fake", name="Fake", description="d"))
    assert tool_registry.get_tool("fake") is None
    assert len(tool_registry.get_all_tools()) == 11


def test_get_tools_by_category():
    organize_tools = tool_registry.get_tools_by_category("organize")
    ids = {t.id for t in organize_tools}
    assert ids == {"split", "remove_pages", "extract_pages", "organize_pages", "rotate"}


def test_get_tools_by_category_unknown_category_returns_empty():
    assert tool_registry.get_tools_by_category("nonexistent_category") == []


def test_split_tool_specifically_registered():
    tool = tool_registry.get_tool("split")
    assert tool is not None
    assert tool.name == "Split PDF"
    assert tool.status == STATUS_COMING_SOON


def test_images_to_pdf_tool_specifically_registered():
    tool = tool_registry.get_tool("images_to_pdf")
    assert tool is not None
    assert not tool.is_available
