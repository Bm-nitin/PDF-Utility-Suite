"""Application commands shared with the Windows WPF interface."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable

import app_logging
from config import APP_NAME, APP_VERSION, COMPRESSION_PRESETS
import extract_engine
import file_manager
import images_to_pdf_engine
from models import format_file_size
import organize_engine
import page_numbers_engine
import pdf_engine
import pdf_to_images_engine
import protect_engine
import remove_pages_engine
import rotate_engine
import split_engine
import thumbnail_preview
import tool_registry
import ui_theme
import unlock_engine
import update_checker
import watermark_engine


def field(key, label, default="", *, kind="entry", choices=(), when=None):
    pairs = [
        option if isinstance(option, tuple) else (option, str(option).replace("_", " ").title())
        for option in choices
    ]
    return {
        "key": key, "label": label, "default": default, "kind": kind,
        "choices": [{"value": value, "label": title} for value, title in pairs],
        "when": when,
    }


def page_fields():
    return (
        field("scope", "Pages", "all", choices=(("all", "All pages"), ("selected", "Selected pages"))),
        field("pages", "Page selection (e.g. 1-3, 5)", when={"scope": "selected"}),
    )


TOOLS = {
    "merge_compress": ("files", "save", "Merge & compress", (
        field("mode", "Operation", "merge_compress", choices=(
            ("merge", "Merge only"), ("compress", "Compress only"),
            ("merge_compress", "Merge & compress"),
        )),
        dict(
            field("level", "Compression", "recommended", choices=tuple(
                (key, value["label"]) for key, value in COMPRESSION_PRESETS.items()
            ), when={"mode": ["compress", "merge_compress"]}),
            descriptions={key: value["description"] for key, value in COMPRESSION_PRESETS.items()},
        ),
    )),
    "split": ("single", "folder", "Split PDF", (
        field("mode", "Split by", "individual", choices=(
            ("individual", "Each page"), ("every_n", "Every N pages"), ("ranges", "Custom ranges"),
        )),
        field("every_n", "Pages per part", "2", when={"mode": "every_n"}),
        field("ranges", "Page ranges (e.g. 1-3, 5-7)", when={"mode": "ranges"}),
    )),
    "remove_pages": ("single", "save", "Remove pages", (field("pages", "Pages to remove (e.g. 2, 4-6)"),)),
    "extract_pages": ("single", "save", "Extract pages", (field("pages", "Pages to extract (e.g. 1-3, 5)"),)),
    "organize_pages": ("reorder_pages", "save", "Save page order", (
        field("order", "Page order (e.g. 3, 1, 2)", kind="order"),
    )),
    "rotate": ("single", "save", "Rotate pages", (
        *page_fields(),
        field("direction", "Direction", rotate_engine.CLOCKWISE, choices=(
            rotate_engine.CLOCKWISE, rotate_engine.COUNTERCLOCKWISE,
        )),
        field("angle", "Angle", 90, choices=tuple((angle, f"{angle}°") for angle in rotate_engine.VALID_ANGLES)),
    )),
    "protect": ("single", "save", "Protect PDF", (
        field("password", "Password", kind="password"),
        field("confirm_password", "Confirm password", kind="password"),
        field("print", "Allow printing", True, kind="check"),
        field("copy", "Allow copying", True, kind="check"),
        field("modify", "Allow editing", True, kind="check"),
    )),
    "unlock": ("single", "near_source", "Unlock PDF", (
        field("password", "PDF password", kind="password"),
    )),
    "page_numbers": ("single", "near_source", "Add page numbers", (
        *page_fields(),
        field("number", "Starting number", page_numbers_engine.DEFAULT_START_NUMBER),
        field("position", "Position", page_numbers_engine.DEFAULT_POSITION, choices=page_numbers_engine.POSITIONS),
        field("size", "Font size (pt)", page_numbers_engine.DEFAULT_FONT_SIZE),
        field("margin", "Margin (pt)", page_numbers_engine.DEFAULT_MARGIN),
    )),
    "watermark": ("single", "near_source", "Add watermark", (
        field("text", "Watermark text", watermark_engine.DEFAULT_TEXT),
        *page_fields(),
        field("position", "Position", watermark_engine.DEFAULT_POSITION, choices=watermark_engine.POSITIONS),
        field("size", "Font size (pt)", watermark_engine.DEFAULT_FONT_SIZE),
        field("opacity", "Opacity (%)", watermark_engine.DEFAULT_OPACITY),
        field("rotation", "Rotation", watermark_engine.DEFAULT_ROTATION, choices=watermark_engine.ROTATIONS),
        field("color", "Color", watermark_engine.DEFAULT_COLOR_NAME, choices=(*watermark_engine.COLOR_PRESETS, "custom")),
        field("custom_color", "Custom color", "#808080", kind="color", when={"color": "custom"}),
    )),
    "images_to_pdf": ("images", "save", "Create PDF", (
        field("page_size", "Page size", images_to_pdf_engine.DEFAULT_PAGE_SIZE, choices=(
            ("a4", "A4"), ("letter", "Letter"), ("original", "Original image size"),
        )),
        field("margin", "Margin (pt)", images_to_pdf_engine.DEFAULT_MARGIN),
    )),
    "pdf_to_images": ("select_pages", "folder", "Export images", (
        field("scope", "Pages", "all", choices=(("all", "All pages"), ("selected", "Selected pages"))),
        field("pages", "Page selection (e.g. 1-3, 5)", kind="selection", when={"scope": "selected"}),
        field("format", "Image format", pdf_to_images_engine.DEFAULT_FORMAT, choices=(("png", "PNG"), ("jpeg", "JPEG"))),
        field("dpi", "Resolution (DPI)", pdf_to_images_engine.DEFAULT_DPI, kind="editable",
              choices=pdf_to_images_engine.DPI_CHOICES),
        field("quality", "JPEG quality (%)", pdf_to_images_engine.DEFAULT_JPEG_QUALITY, kind="editable",
              choices=pdf_to_images_engine.JPEG_QUALITY_CHOICES, when={"format": "jpeg"}),
    )),
}


def describe() -> dict:
    tools = []
    for tool in tool_registry.get_all_tools():
        if tool.is_available:
            source, destination, action, fields = TOOLS[tool.id]
            tools.append({
                "id": tool.id, "name": tool.name, "description": tool.description,
                "source": source, "destination": destination, "action": action, "fields": fields,
            })
    log_file = app_logging.log_path()
    return {
        "name": APP_NAME, "version": APP_VERSION,
        "log_path": str(log_file) if log_file else None,
        "theme": ui_theme.load_theme(ui_theme.settings_path()), "tools": tools,
    }


def inspect_inputs(tool: str, paths: list[str], existing: list[str], progress: Callable) -> dict:
    rows, errors, duplicates = [], [], []
    seen = {Path(path).resolve() for path in existing} if tool == "merge_compress" else set()
    for index, path in enumerate(map(Path, paths), 1):
        progress(f"Reading file {index} of {len(paths)}")
        try:
            if tool == "merge_compress":
                resolved = path.resolve()
                if resolved in seen:
                    duplicates.append(path.name)
                    continue
            if tool == "images_to_pdf":
                info = images_to_pdf_engine.get_image_info(path)
                info["details"] = f"{info['width']} × {info['height']}"
            else:
                read = unlock_engine.get_source_info if tool == "unlock" else pdf_engine.get_pdf_info
                info = read(path)
                pages = info["page_count"]
                info["details"] = "1 page" if pages == 1 else f"{pages} pages"
            rows.append(dict(info, size_label=format_file_size(info["size"])))
            if tool == "merge_compress":
                seen.add(resolved)
        except (pdf_engine.PDFEngineError, OSError) as exc:
            errors.append(f"{path.name} — {exc}")
    return {"rows": rows, "errors": errors, "duplicates": duplicates}


def default_filename(tool: str, paths: list[Path], options: dict) -> str:
    if tool == "merge_compress":
        mode = options.get("mode", "merge_compress")
        if mode == "compress":
            return paths[0].stem + "_compressed.pdf"
        return "merged.pdf" if mode == "merge" else "merged_compressed.pdf"
    if tool == "images_to_pdf":
        return "images.pdf"
    suffix = {
        "extract_pages": "extracted", "remove_pages": "pages_removed", "organize_pages": "organized",
        "rotate": "rotated", "protect": "protected",
    }[tool]
    return f"{paths[0].stem}_{suffix}.pdf"


def _compress(paths: list[Path], destination: Path, level: str, progress: Callable) -> dict:
    if len(paths) == 1:
        size = paths[0].stat().st_size
        progress("Compressing PDF…")
        result = pdf_engine.compress_pdf(paths[0], destination, level=level)
        return {"paths": [result], "sizes": [size, result.stat().st_size]}

    outputs, errors = [], []
    for index, path in enumerate(paths, 1):
        progress(f"Compressing file {index} of {len(paths)}")
        try:
            output = file_manager.generate_compressed_output_path(path, destination)
            outputs.append(pdf_engine.compress_pdf(path, output, level=level))
        except (pdf_engine.PDFEngineError, OSError) as exc:
            errors.append(f"{path.name} — {exc}")
    return {"paths": outputs, "errors": errors}


def _watermark_color(options: dict):
    name = options.get("color", watermark_engine.DEFAULT_COLOR_NAME)
    if name != "custom":
        return watermark_engine.COLOR_PRESETS[name]
    value = options["custom_color"].lstrip("#")
    if len(value) == 6:
        try:
            channels = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
            return watermark_engine.color_from_rgb255(*channels)
        except ValueError:
            pass
    raise pdf_engine.PDFEngineError("Enter a custom color as a six-digit hex value.")


def execute(tool: str, paths: list[Path], destination: Path | None, options: dict, progress: Callable) -> dict:
    if not paths:
        raise pdf_engine.PDFEngineError("Select an input file first.")
    if destination is None and TOOLS[tool][1] != "near_source":
        raise pdf_engine.PDFEngineError("Choose where to save the output.")
    if tool == "merge_compress":
        mode = options["mode"]
        if mode == "compress":
            return _compress(paths, destination, options["level"], progress)
        if len(paths) < 2:
            raise pdf_engine.PDFEngineError("Select at least two PDFs to merge.")
        if mode == "merge":
            return {"paths": [pdf_engine.merge_pdfs(paths, destination, progress)]}
        result = pdf_engine.merge_and_compress(paths, destination, options["level"], progress_callback=progress)
        return {"paths": [result["output_path"]], "sizes": [result["merged_size"], result["final_size"]]}
    if tool == "images_to_pdf":
        pdf_engine._require_separate_output(paths, destination)
        output = images_to_pdf_engine.images_to_pdf(
            paths, destination, options["page_size"], options["margin"], progress,
        )
        return {"paths": [output]}

    source = paths[0]
    if tool == "unlock":
        output = file_manager.generate_unlocked_output_path(source, source.parent)
        return {"paths": [unlock_engine.unlock_pdf(source, output, options["password"], progress)]}

    pdf_engine.validate_pdf(source)
    pages = pdf_engine.get_page_count(source)
    if TOOLS[tool][1] == "save":
        pdf_engine._require_separate_output([source], destination)
    selected = options.get("scope") == "selected"
    selection = options.get("pages", "")

    if tool == "split":
        mode = options["mode"]
        if mode == "individual":
            groups = split_engine.compute_individual_page_groups(pages)
        elif mode == "every_n":
            try:
                count = int(options["every_n"])
            except ValueError:
                raise pdf_engine.PDFEngineError("Enter a whole number of pages per part.") from None
            groups = split_engine.compute_every_n_page_groups(pages, count)
        else:
            groups = split_engine.parse_page_ranges(options["ranges"], pages)
        result = split_engine.split_pdf(source, groups, destination, mode == "ranges", progress)
        return {"paths": result["output_paths"]}
    if tool == "pdf_to_images":
        indices = pdf_to_images_engine.resolve_pages_to_render(selection, pages) if selected else None
        result = pdf_to_images_engine.render_pdf_to_images(
            source, destination, indices, options["format"], options["dpi"], options["quality"], progress,
        )
        return {"paths": result["output_paths"]}

    if tool == "remove_pages":
        indices = remove_pages_engine.resolve_pages_to_remove(selection, pages)
        output = remove_pages_engine.remove_pages_from_pdf(source, destination, indices, progress)
    elif tool == "extract_pages":
        indices = extract_engine.resolve_pages_to_extract(selection, pages)
        output = extract_engine.extract_pages_from_pdf(source, destination, indices, progress)
    elif tool == "organize_pages":
        indices = organize_engine.parse_page_order(options["order"], pages)
        output = organize_engine.organize_pages_from_pdf(source, destination, indices, progress)
    elif tool == "rotate":
        indices = rotate_engine.resolve_pages_to_rotate(selection, pages) if selected else list(range(pages))
        angle = rotate_engine.resolve_clockwise_degrees(options["direction"], int(options["angle"]))
        rotations = rotate_engine.build_rotation_map(indices, angle)
        output = rotate_engine.rotate_pages_in_pdf(source, destination, rotations, progress)
    elif tool == "protect":
        password = protect_engine.validate_password(options["password"])
        if password != options["confirm_password"]:
            raise pdf_engine.PDFEngineError("The passwords do not match.")
        permissions = protect_engine.build_permissions(options["print"], options["copy"], options["modify"])
        output = protect_engine.protect_pdf(source, destination, password, permissions, progress)
    elif tool == "page_numbers":
        indices = page_numbers_engine.resolve_pages_to_number(selection, pages) if selected else list(range(pages))
        output = file_manager.generate_numbered_output_path(source, source.parent)
        output = page_numbers_engine.add_page_numbers(
            source, output, indices,
            page_numbers_engine.validate_start_number(options["number"]),
            options["position"], page_numbers_engine.validate_font_size(options["size"]),
            page_numbers_engine.validate_margin(options["margin"]), progress,
        )
    else:
        indices = watermark_engine.resolve_pages_to_watermark(selection, pages) if selected else None
        output = file_manager.generate_watermarked_output_path(source, source.parent)
        output = watermark_engine.add_watermark(
            source, output, indices, options["text"], options["position"], options["size"],
            options["opacity"], options["rotation"], _watermark_color(options), progress,
        )
    return {"paths": [output]}


def dispatch(request: dict, progress: Callable[[str], None]):
    action = request["action"]
    if action == "describe":
        return describe()
    if action == "import":
        return inspect_inputs(request["tool"], request["paths"], request.get("existing", []), progress)
    if action == "thumbnail":
        path = Path(request["path"])
        if request["kind"] == "image":
            data = thumbnail_preview.render_image_file_thumbnail(path, 96)
        else:
            data = thumbnail_preview.render_pdf_page_thumbnail(path, request["page"], 96)
        return base64.b64encode(data).decode("ascii")
    if action == "plan_pages":
        parse = organize_engine.parse_page_order if request["tool"] == "organize_pages" else pdf_to_images_engine.resolve_pages_to_render
        return parse(request["text"], request["page_count"])
    if action == "theme":
        theme = request["theme"]
        if theme in ui_theme.THEMES:
            ui_theme.save_theme(ui_theme.settings_path(), theme)
        return None
    if action == "updates":
        return update_checker.check_for_update()
    if action == "open_logs":
        log_file = app_logging.log_path()
        if log_file is None:
            raise OSError("The log folder is unavailable.")
        try:
            os.startfile(log_file.parent)
        except OSError as exc:
            raise OSError("Could not open the logs folder.") from exc
        return None
    if action in ("filename", "execute"):
        paths = [Path(path) for path in request["paths"]]
        if action == "filename":
            return default_filename(request["tool"], paths, request["options"])
        destination = request.get("destination")
        return execute(
            request["tool"], paths, Path(destination) if destination else None, request["options"], progress,
        )
    raise ValueError("Unknown application command.")
