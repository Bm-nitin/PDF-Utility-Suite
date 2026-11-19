import pymupdf
import pytest
from PIL import Image

import pdf_engine
import protect_engine
import tool_registry
import wpf_backend


def _source(path, texts):
    with pymupdf.open() as document:
        for text in texts:
            document.new_page().insert_text((72, 72), text)
        document.save(path)
    return path


def _defaults(tool):
    return {field["key"]: field["default"] for field in wpf_backend.TOOLS[tool][3]}


def _execute(tool, paths, destination=None, **overrides):
    return wpf_backend.execute(tool, paths, destination, _defaults(tool) | overrides, lambda _text: None)


def test_wpf_describes_every_registered_tool():
    ids = [tool["id"] for tool in wpf_backend.describe()["tools"]]
    assert ids == [tool.id for tool in tool_registry.get_all_tools() if tool.is_available]


@pytest.mark.parametrize("mode", ["merge", "compress", "merge_compress"])
def test_wpf_merge_and_compression_workflows(tmp_path, mode):
    first = _source(tmp_path / "a.pdf", ["Alpha"])
    second = _source(tmp_path / "b.pdf", ["Beta"])
    destination = tmp_path if mode == "compress" else tmp_path / "output.pdf"
    result = _execute("merge_compress", [second, first], destination, mode=mode)
    assert all(path.is_file() for path in result["paths"])
    if mode == "compress":
        assert [path.name for path in result["paths"]] == ["b_compressed.pdf", "a_compressed.pdf"]
    else:
        with pymupdf.open(destination) as document:
            assert [page.get_text().strip() for page in document] == ["Beta", "Alpha"]


@pytest.mark.parametrize(
    "tool, options, expected",
    [
        ("remove_pages", {"pages": "2"}, ["Alpha", "Gamma"]),
        ("extract_pages", {"pages": "1,3"}, ["Alpha", "Gamma"]),
        ("organize_pages", {"order": "3,1,2"}, ["Gamma", "Alpha", "Beta"]),
        ("rotate", {"scope": "selected", "pages": "2", "angle": 90}, ["Alpha", "Beta", "Gamma"]),
    ],
)
def test_wpf_page_editing_workflows(tmp_path, tool, options, expected):
    source = _source(tmp_path / "source.pdf", ["Alpha", "Beta", "Gamma"])
    original = source.read_bytes()
    destination = tmp_path / "output.pdf"
    result = _execute(tool, [source], destination, **options)
    assert result["paths"] == [destination]
    with pymupdf.open(destination) as document:
        assert [page.get_text().strip() for page in document] == expected
        if tool == "rotate":
            assert [page.rotation for page in document] == [0, 90, 0]
    assert source.read_bytes() == original


def test_wpf_split_pdf_with_custom_ranges(tmp_path):
    source = _source(tmp_path / "source.pdf", ["Alpha", "Beta", "Gamma"])
    result = _execute("split", [source], tmp_path, mode="ranges", ranges="1-2,3")
    assert len(result["paths"]) == 2
    with pymupdf.open(result["paths"][0]) as part:
        assert part.page_count == 2


def test_wpf_protect_and_unlock_require_the_password(tmp_path):
    source = _source(tmp_path / "source.pdf", ["Alpha"])
    encrypted = tmp_path / "protected.pdf"
    _execute("protect", [source], encrypted, password="demo-pass", confirm_password="demo-pass", copy=False)
    with pymupdf.open(encrypted) as document:
        assert document.is_encrypted
        assert document.authenticate("demo-pass")
        assert not document.permissions & protect_engine.PERM_COPY
    assert wpf_backend.inspect_inputs("unlock", [str(encrypted)], [], lambda _message: None)["rows"][0]["is_encrypted"]
    with pytest.raises(pdf_engine.PDFEngineError, match="password"):
        _execute("unlock", [encrypted], password="incorrect")
    output = _execute("unlock", [encrypted], password="demo-pass")["paths"][0]
    with pymupdf.open(output) as document:
        assert not document.is_encrypted
        assert document[0].get_text().strip() == "Alpha"


@pytest.mark.parametrize(
    "tool, options, text",
    [
        ("page_numbers", {"number": "42"}, "42"),
        ("watermark", {"text": "DRAFT", "color": "custom", "custom_color": "#554477"}, "DRAFT"),
    ],
)
def test_wpf_stamping_workflows(tmp_path, tool, options, text):
    source = _source(tmp_path / "source.pdf", ["Alpha"])
    output = _execute(tool, [source], **options)["paths"][0]
    with pymupdf.open(output) as document:
        assert text in document[0].get_text()


def test_wpf_image_conversion_in_both_directions(tmp_path):
    pixels = tmp_path / "sample.png"
    with Image.new("RGB", (100, 80), "blue") as image:
        image.save(pixels)
    pdf = tmp_path / "images.pdf"
    _execute("images_to_pdf", [pixels, pixels], pdf)
    with pymupdf.open(pdf) as document:
        assert document.page_count == 2
    result = _execute("pdf_to_images", [pdf], tmp_path, scope="selected", pages="2, 1, 2", format="jpeg")
    assert len(result["paths"]) == len(set(result["paths"])) == 3
    assert all(path.suffix == ".jpg" and path.exists() for path in result["paths"])


def test_wpf_rejects_same_file_output_and_duplicate_merge_input(tmp_path):
    source = _source(tmp_path / "source.pdf", ["Alpha", "Beta"])
    result = wpf_backend.inspect_inputs(
        "merge_compress", [str(source), str(source)], [], lambda _message: None,
    )
    assert len(result["rows"]) == 1
    assert result["duplicates"] == [source.name]
    with pytest.raises(pdf_engine.PDFEngineError, match="different from the input"):
        _execute("rotate", [source], source)
