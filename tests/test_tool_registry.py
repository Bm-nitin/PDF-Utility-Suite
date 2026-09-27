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

def test_all_twelve_tools_are_available():
    """Phase 13 made Split PDF the second real, working tool; Phase 14
    made Remove Pages the third; Phase 15 made Extract Pages the fourth;
    Phase 16 made Organize Pages the fifth; Phase 17 made Rotate the
    sixth; Phase 18 made Protect PDF the seventh; Phase 19 made Unlock
    PDF the eighth; Phase 20 made Add Page Numbers the ninth; Phase 21
    made Add Watermark the tenth; Phase 22 made Images \u2192 PDF the
    eleventh; Phase 23 makes PDF \u2192 Images the twelfth -- there is
    no coming_soon tool in the registry.
    """
    available_ids = {t.id for t in tool_registry.get_all_tools() if t.is_available}
    assert available_ids == {
        "merge_compress", "split", "remove_pages", "extract_pages",
        "organize_pages", "rotate", "protect", "unlock", "page_numbers",
        "watermark", "images_to_pdf", "pdf_to_images",
    }


def test_no_tool_is_registered_as_coming_soon_anymore():
    coming_soon_ids = {
        t.id for t in tool_registry.get_all_tools() if not t.is_available
    }
    assert coming_soon_ids == set()


def test_registry_has_exactly_twelve_tools():
    assert len(tool_registry.get_all_tools()) == 12


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
    assert len(tool_registry.get_all_tools()) == 12


def test_get_tools_by_category():
    organize_tools = tool_registry.get_tools_by_category("organize")
    ids = {t.id for t in organize_tools}
    assert ids == {"split", "extract_pages", "organize_pages", "rotate"}


def test_get_tools_by_category_pdf_contains_remove_pages():
    pdf_tools = tool_registry.get_tools_by_category("PDF")
    ids = {t.id for t in pdf_tools}
    assert ids == {"remove_pages"}


def test_get_tools_by_category_security_contains_protect_and_unlock():
    security_tools = tool_registry.get_tools_by_category("security")
    ids = {t.id for t in security_tools}
    assert ids == {"protect", "unlock"}


def test_get_tools_by_category_edit_contains_page_numbers_and_watermark():
    edit_tools = tool_registry.get_tools_by_category("edit")
    ids = {t.id for t in edit_tools}
    assert ids == {"page_numbers", "watermark"}


def test_get_tools_by_category_unknown_category_returns_empty():
    assert tool_registry.get_tools_by_category("nonexistent_category") == []


def test_split_tool_specifically_registered():
    tool = tool_registry.get_tool("split")
    assert tool is not None
    assert tool.name == "Split PDF"
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available


def test_images_to_pdf_tool_specifically_registered():
    tool = tool_registry.get_tool("images_to_pdf")
    assert tool is not None
    assert not tool.is_available


def test_remove_pages_tool_specifically_registered():
    tool = tool_registry.get_tool("remove_pages")
    assert tool is not None
    assert tool.name == "Remove Pages"
    assert tool.description == "Remove selected pages from a PDF"
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    assert tool.category == "PDF"


def test_extract_pages_tool_specifically_registered():
    tool = tool_registry.get_tool("extract_pages")
    assert tool is not None
    assert tool.name == "Extract Pages"
    assert tool.description == "Save specific pages of a PDF as a new file."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Category is deliberately left as its pre-existing "organize"
    # value (it was never "PDF") -- Phase 15 only flips its status from
    # coming_soon to available; it does not change the tool's id or
    # category, per the "keep its existing tool id" / "do not redesign
    # existing architecture" requirements. test_get_tools_by_category
    # below (unchanged from before Phase 15) already locks this in.
    assert tool.category == "organize"


def test_organize_pages_tool_specifically_registered():
    tool = tool_registry.get_tool("organize_pages")
    assert tool is not None
    assert tool.name == "Organize Pages"
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Category and id both left unchanged from their pre-existing
    # values -- Phase 16 only flips status from coming_soon to
    # available (see the equivalent note on Extract Pages above).
    # test_get_tools_by_category (unchanged from before Phase 16)
    # already locks this in.
    assert tool.category == "organize"


def test_rotate_tool_specifically_registered():
    tool = tool_registry.get_tool("rotate")
    assert tool is not None
    assert tool.name == "Rotate PDF"
    assert tool.description == "Rotate one or more pages of a PDF."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Category and id both left unchanged from their pre-existing
    # values -- Phase 17 only flips status from coming_soon to
    # available (see the equivalent notes on Extract/Organize Pages
    # above). test_get_tools_by_category (unchanged from before
    # Phase 17) already locks this in.
    assert tool.category == "organize"


def test_protect_tool_specifically_registered():
    tool = tool_registry.get_tool("protect")
    assert tool is not None
    assert tool.name == "Protect PDF"
    assert tool.description == "Add a password to a PDF to restrict access."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Category and id both left unchanged from their pre-existing
    # values -- Phase 18 only flips status from coming_soon to
    # available (see the equivalent notes on Extract/Organize/Rotate
    # above). test_get_tools_by_category_security_contains_protect_and_
    # unlock (unchanged from before Phase 18) already locks this in.
    assert tool.category == "security"


def test_unlock_tool_specifically_registered():
    tool = tool_registry.get_tool("unlock")
    assert tool is not None
    assert tool.name == "Unlock PDF"
    assert tool.description == "Remove a known password from a PDF."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Category and id both left unchanged from their pre-existing
    # values -- Phase 19 only flips status from coming_soon to
    # available (see the equivalent notes on Extract/Organize/Rotate/
    # Protect above).
    # test_get_tools_by_category_security_contains_protect_and_unlock
    # (unchanged from before Phase 19) already locks this in.
    assert tool.category == "security"


def test_page_numbers_tool_specifically_registered():
    tool = tool_registry.get_tool("page_numbers")
    assert tool is not None
    assert tool.name == "Add Page Numbers"
    assert tool.description == "Add page numbers to every page of a PDF."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Category and id both left unchanged from their pre-existing
    # values -- Phase 20 only flips status from coming_soon to
    # available (see the equivalent notes on Extract/Organize/Rotate/
    # Protect/Unlock above).
    # test_get_tools_by_category_edit_contains_page_numbers_and_
    # watermark (unchanged from before Phase 20) already locks this in.
    assert tool.category == "edit"


def test_watermark_tool_specifically_registered():
    tool = tool_registry.get_tool("watermark")
    assert tool is not None
    assert tool.name == "Add Watermark"
    assert tool.description == "Overlay a text watermark on the pages of a PDF."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Id, name and category are all left unchanged from their
    # pre-existing values -- Phase 21 flips status from coming_soon to
    # available (and corrects the description, which previously
    # promised image watermarks and "every page" only; this tool is
    # text-only and supports page selection).
    # test_get_tools_by_category_edit_contains_page_numbers_and_watermark
    # (unchanged from before Phase 21) already locks the category in.
    assert tool.category == "edit"


def test_watermark_keeps_its_position_in_registry_order():
    ids = [t.id for t in tool_registry.get_all_tools()]
    assert ids.index("page_numbers") + 1 == ids.index("watermark")
    assert ids.index("watermark") + 1 == ids.index("images_to_pdf")


def test_images_to_pdf_tool_specifically_registered():
    tool = tool_registry.get_tool("images_to_pdf")
    assert tool is not None
    assert tool.name == "Images \u2192 PDF"
    assert tool.description == "Combine one or more images into a new PDF file."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    # Id and name are unchanged from the pre-existing placeholder --
    # Phase 22 only flips status from coming_soon to available.
    assert tool.category == "create"


def test_images_to_pdf_keeps_its_registry_position_after_watermark():
    ids = [t.id for t in tool_registry.get_all_tools()]
    assert ids.index("watermark") + 1 == ids.index("images_to_pdf")
    # No longer the last entry as of Phase 23 -- see
    # test_pdf_to_images_keeps_its_registry_position_after_images_to_pdf
    # below for the tool that took over that position.
    assert ids.index("images_to_pdf") == len(ids) - 2


def test_pdf_to_images_tool_specifically_registered():
    tool = tool_registry.get_tool("pdf_to_images")
    assert tool is not None
    assert tool.id == "pdf_to_images"
    assert tool.name == "PDF \u2192 Images"
    assert tool.description == "Render the pages of a PDF as PNG or JPEG image files."
    assert tool.status == STATUS_AVAILABLE
    assert tool.is_available
    assert tool.category == "create"


def test_pdf_to_images_keeps_its_registry_position_after_images_to_pdf():
    ids = [t.id for t in tool_registry.get_all_tools()]
    assert ids.index("images_to_pdf") + 1 == ids.index("pdf_to_images")
    assert ids.index("pdf_to_images") == len(ids) - 1
