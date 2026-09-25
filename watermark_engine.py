"""
watermark_engine.py

Phase 21: Add Watermark processing -- overlaying a single line of text
onto a chosen set of pages, at one of nine positions, with a chosen font
size, opacity, rotation and color, correctly placed and oriented for each
page's existing rotation, leaving the source completely untouched.
Nothing in this file imports tkinter.

Reuses split_engine.parse_page_ranges() for page-selection text parsing
(exactly like remove_pages_engine.py, extract_engine.py, rotate_engine.py
and page_numbers_engine.py do) and, like every other engine in this
project, reuses pdf_engine's existing validation and atomic-save
machinery rather than creating a second output-saving system.

Text watermarks only. Image watermarks are deliberately out of scope for
this phase.

PYMUPDF API -- INSPECTED AND VERIFIED EXPERIMENTALLY (PyMuPDF 1.28.2)
=======================================================================
Nothing below is assumed from memory; each point was confirmed against
the installed library, either by reading its source or by rendering a
page to a pixmap and measuring where the ink actually landed (see
tests/test_watermark_engine.py, which encodes these findings).

- Page.rect is the DISPLAYED (rotation-aware) page rectangle: a 200x300
  page with /Rotate 90 reports a 300x200 rect. Page.mediabox/cropbox
  are NOT rotation-aware.
- Page.insert_text()'s `point` is in the page's UNROTATED content-stream
  space (y down, origin at the crop box's top-left), NOT in displayed
  space. This module therefore always computes placement in DISPLAY
  space (from Page.rect, so "top left" always means the visual top left)
  and converts the result with `display_point * page.derotation_matrix`
  before calling insert_text(). Verified for 0/90/180/270 degree pages,
  including pages whose crop box/media box have a non-zero origin.
- Two independent rotations are involved and must not be confused:
    1. the page's own /Rotate (page.rotation, clockwise, multiples of
       90), which the viewer applies to everything on the page; and
    2. the watermark's requested rotation (counter-clockwise degrees as
       seen by the person looking at the displayed page).
  To make text appear rotated by `theta` in the DISPLAYED page, the
  glyphs must be rotated by `theta + page.rotation` in content space
  (verified by rendering: a 45 degree watermark on a 90 degree page
  displays at 45 degrees, not 135).
- insert_text()'s own `rotate` argument only accepts multiples of 90.
  Arbitrary angles use its `morph=(fixpoint, Matrix)` argument instead;
  the fixpoint must be a pymupdf.Point (a plain tuple raises
  AttributeError). Both routes were verified to place the text
  identically, so multiples of 90 use `rotate=` (exact, no floating-
  point noise in the content stream) and everything else uses `morph`.
- `fill_opacity` / `stroke_opacity` on insert_text() create an
  ExtGState with the requested constant alpha (0.0 = invisible,
  1.0 = opaque).
- insert_text()'s default `overlay=True` draws into a NEW content stream
  appended after the existing ones, and Shape.commit() calls
  Page.wrap_contents() first so the graphics state is balanced. Nothing
  is rasterized, and existing text/images/vectors are never rewritten.
- pymupdf.get_text_length() measures a string for a base-14 font;
  pymupdf.Font("helv") exposes the font's ascender/descender, used here
  to find the text's vertical extent.
- Page.get_text()/search_for() report coordinates in UNROTATED content
  space; multiply by page.rotation_matrix to get displayed coordinates.
- The base-14 "helv" font used here is a simple (non-embedded) font that
  can only represent printable Latin-1 characters (U+0020-U+007E and
  U+00A0-U+00FF). Text containing anything else (e.g. CJK, Cyrillic,
  Greek, "smart" quotes, the euro sign) would silently render as
  placeholder dots, so validate_text() rejects it with a clear message
  instead of writing a wrong watermark.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import pymupdf

import pdf_engine
import split_engine
from pdf_engine import PDFEngineError

logger = logging.getLogger(__name__)


class WatermarkOptionsError(PDFEngineError):
    """Raised for invalid watermark text, position, font size, opacity,
    rotation, color, or page selection. A PDFEngineError subclass,
    exactly like every other engine's own domain-specific error class.
    """


# ---------------------------------------------------------------------------
# Public constants (the engine API contract -- NOT UI display text)
# ---------------------------------------------------------------------------

#: The nine supported positions -- stable internal identifiers.
POSITIONS = (
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
)

#: Supported watermark rotations, in degrees, counter-clockwise as seen on
#: the displayed page (45 = the classic bottom-left-to-top-right diagonal).
ROTATIONS = (0, 45, 90, 135, 180, 225, 270, 315)

#: Predefined colors as normalized (0.0-1.0) RGB tuples -- the
#: deterministic representation PyMuPDF itself expects. Keys are stable
#: identifiers; the UI maps them to display labels.
COLOR_PRESETS = {
    "black": (0.0, 0.0, 0.0),
    "gray": (0.5, 0.5, 0.5),
    "red": (0.85, 0.0, 0.0),
    "blue": (0.0, 0.2, 0.85),
    "green": (0.0, 0.55, 0.0),
}

DEFAULT_TEXT = "CONFIDENTIAL"
DEFAULT_POSITION = "center"
DEFAULT_FONT_SIZE = 36.0
DEFAULT_OPACITY = 30.0  # percent, 0-100
DEFAULT_ROTATION = 45
DEFAULT_COLOR_NAME = "gray"
DEFAULT_COLOR = COLOR_PRESETS[DEFAULT_COLOR_NAME]

#: Distance kept between the watermark's (rotated) bounding box and the
#: page edge for left/right/top/bottom positions, in points (0.5 inch).
DEFAULT_MARGIN = 36.0

#: Practical upper bound on font size, in points. Not arbitrary: PDF
#: viewers clip or reject coordinates far beyond this, and the largest
#: page PDF viewers support is 14400 pt on a side.
MAX_FONT_SIZE = 1000.0

_FONT_NAME = "helv"  # a standard PDF14 font -- always available, no font file needed

_font_metrics_cache: Optional[Tuple[float, float]] = None


# ---------------------------------------------------------------------------
# Option validation (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def _is_finite(value: float) -> bool:
    return math.isfinite(value)


def _to_float(value, label: str, strip_suffixes: Sequence[str] = ()) -> float:
    """Converts a str/int/float into a finite float, or raises
    WatermarkOptionsError with a message naming `label`.
    """
    if value is None or isinstance(value, bool):
        raise WatermarkOptionsError(f"{label} must be a number.")
    if isinstance(value, str):
        stripped = value.strip()
        for suffix in strip_suffixes:
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)].strip()
        if not stripped:
            raise WatermarkOptionsError(f"Enter {label.lower()}.")
        try:
            number = float(stripped)
        except ValueError:
            raise WatermarkOptionsError(f"{label} must be a number.")
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise WatermarkOptionsError(f"{label} must be a number.")
    if not _is_finite(number):
        raise WatermarkOptionsError(f"{label} must be a number.")
    return number


def validate_text(text) -> str:
    """Returns the watermark text with surrounding whitespace removed.

    Rejects empty/whitespace-only text, non-string input, control
    characters (including line breaks -- the watermark is a single
    line), and characters the built-in Helvetica font cannot represent.
    Interior text is preserved exactly as typed (including repeated
    spaces); there is no arbitrary length limit.
    """
    if not isinstance(text, str):
        raise WatermarkOptionsError("Enter the watermark text.")
    stripped = text.strip()
    if not stripped:
        raise WatermarkOptionsError("Enter the watermark text.")

    for ch in stripped:
        code = ord(ch)
        if code < 32 or 127 <= code < 160:
            raise WatermarkOptionsError(
                "Watermark text must be a single line without control "
                "characters."
            )
    unsupported = sorted({
        ch for ch in stripped if not (32 <= ord(ch) <= 126 or 160 <= ord(ch) <= 255)
    })
    if unsupported:
        shown = " ".join(unsupported[:5])
        raise WatermarkOptionsError(
            f"Watermark text contains characters that can't be rendered "
            f"({shown}). Use letters, digits and punctuation from the "
            f"standard Latin character set."
        )
    # U+00AD (soft hyphen) is in range but is not rendered by the font.
    if "\u00ad" in stripped:
        raise WatermarkOptionsError(
            "Watermark text contains a soft hyphen, which can't be rendered."
        )
    return stripped


def validate_font_size(value) -> float:
    """Parses a font size (points). Rejects empty, non-numeric, zero,
    negative, NaN/infinite values, and values above MAX_FONT_SIZE.
    """
    number = _to_float(value, "Font size")
    if number <= 0:
        raise WatermarkOptionsError("Font size must be greater than zero.")
    if number > MAX_FONT_SIZE:
        raise WatermarkOptionsError(
            f"Font size cannot be larger than {MAX_FONT_SIZE:g}."
        )
    return number


def validate_opacity(value) -> float:
    """Parses an opacity PERCENTAGE (0-100 inclusive). A trailing '%' is
    tolerated in string input. Rejects empty, non-numeric, NaN/infinite
    and out-of-range values.
    """
    number = _to_float(value, "Opacity", strip_suffixes=("%",))
    if number < 0 or number > 100:
        raise WatermarkOptionsError("Opacity must be between 0 and 100.")
    return number


def opacity_to_alpha(opacity_percent: float) -> float:
    """Converts a validated 0-100 percentage to PyMuPDF's 0.0-1.0 alpha."""
    return validate_opacity(opacity_percent) / 100.0


def validate_rotation(value) -> int:
    """Parses a rotation in degrees. Only the eight values in ROTATIONS
    are supported. A trailing degree sign is tolerated in string input.
    """
    number = _to_float(value, "Rotation", strip_suffixes=("\u00b0",))
    if number not in ROTATIONS:
        allowed = ", ".join(str(r) for r in ROTATIONS)
        raise WatermarkOptionsError(f"Rotation must be one of: {allowed}.")
    return int(number)


def validate_position(position) -> str:
    """Raises WatermarkOptionsError unless `position` is one of POSITIONS."""
    if not isinstance(position, str) or position not in POSITIONS:
        raise WatermarkOptionsError(f"'{position}' is not a valid position.")
    return position


def validate_color(color) -> Tuple[float, float, float]:
    """Validates a normalized RGB color: exactly three finite numbers,
    each within 0.0-1.0. Returns a tuple of floats.
    """
    try:
        components = tuple(color)
    except TypeError:
        raise WatermarkOptionsError(
            "Color must be three RGB values between 0 and 1."
        )
    if len(components) != 3:
        raise WatermarkOptionsError(
            "Color must be three RGB values between 0 and 1."
        )
    result = []
    for component in components:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise WatermarkOptionsError(
                "Color must be three RGB values between 0 and 1."
            )
        number = float(component)
        if not _is_finite(number) or number < 0.0 or number > 1.0:
            raise WatermarkOptionsError(
                "Color must be three RGB values between 0 and 1."
            )
        result.append(number)
    return (result[0], result[1], result[2])


def color_from_rgb255(red: int, green: int, blue: int) -> Tuple[float, float, float]:
    """Converts 0-255 integer channels (e.g. from a color chooser) to the
    normalized RGB tuple the engine takes.
    """
    channels = (red, green, blue)
    for channel in channels:
        if isinstance(channel, bool) or not isinstance(channel, int) \
                or channel < 0 or channel > 255:
            raise WatermarkOptionsError(
                "Color channels must be whole numbers between 0 and 255."
            )
    return (red / 255.0, green / 255.0, blue / 255.0)


# ---------------------------------------------------------------------------
# Page-selection resolution (pure: no tkinter, no filesystem I/O)
# ---------------------------------------------------------------------------

def resolve_pages_to_watermark(text: str, page_count: int) -> List[int]:
    """Parse a comma-separated, 1-based page/range selection string
    (e.g. "1-3, 5, 7-9") into a sorted, de-duplicated list of 0-based
    page indices, reusing split_engine.parse_page_ranges() for the
    actual syntax. Set semantics: a page named twice is watermarked once
    (stamping twice would double the ink and darken the watermark).

    Example:
        resolve_pages_to_watermark("3-5", 10) -> [2, 3, 4]
    """
    groups = split_engine.parse_page_ranges(text, page_count)
    return sorted({index for group in groups for index in group})


# ---------------------------------------------------------------------------
# Rotation-aware placement geometry
# ---------------------------------------------------------------------------

def _font_metrics() -> Tuple[float, float]:
    """(ascender, descender) of the watermark font, in em units."""
    global _font_metrics_cache
    if _font_metrics_cache is None:
        font = pymupdf.Font(_FONT_NAME)
        _font_metrics_cache = (float(font.ascender), float(font.descender))
    return _font_metrics_cache


def compute_watermark_geometry(
    page_width: float, page_height: float, text_width: float, font_size: float,
    position: str, rotation: int, margin: float = DEFAULT_MARGIN,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Pure geometry: given the DISPLAYED page size and the text's
    unrotated width, returns ((start_x, start_y), (center_x, center_y))
    in DISPLAYED coordinates (y down, origin at the visual top-left):

    - center: where the watermark's box is centered; and
    - start: where the text baseline must START so that, once the text
      is drawn rotated counter-clockwise by `rotation` degrees, its box
      is centered on `center`.

    The (rotated) bounding box is aligned to the requested position with
    `margin` to the page edges; if the box is too large to fit inside
    the margins along an axis, it is centered on that axis instead so a
    normal configuration is never pushed off the page.
    """
    ascender, descender = _font_metrics()
    box_height = (ascender - descender) * font_size
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    half_w = (abs(text_width * cos_t) + abs(box_height * sin_t)) / 2.0
    half_h = (abs(text_width * sin_t) + abs(box_height * cos_t)) / 2.0

    if position.endswith("_left"):
        cx = margin + half_w
    elif position.endswith("_right"):
        cx = page_width - margin - half_w
    else:  # *_center and "center"
        cx = page_width / 2.0

    if position.startswith("top_"):
        cy = margin + half_h
    elif position.startswith("bottom_"):
        cy = page_height - margin - half_h
    else:  # center_* and "center"
        cy = page_height / 2.0

    # Too big to honor the margins on an axis -> center on that axis.
    if 2 * half_w + 2 * margin > page_width:
        cx = page_width / 2.0
    if 2 * half_h + 2 * margin > page_height:
        cy = page_height / 2.0

    # Unrotated baseline start relative to the box center: the box spans
    # (ascender - descender) * fs vertically, the baseline being
    # `ascender * fs` below the box top, i.e. this far below the center:
    baseline_offset = (ascender + descender) / 2.0 * font_size
    rel_x = -text_width / 2.0
    rel_y = baseline_offset

    # Rotate that offset counter-clockwise (as seen on screen, y down).
    rot_x = rel_x * cos_t + rel_y * sin_t
    rot_y = -rel_x * sin_t + rel_y * cos_t

    return (cx + rot_x, cy + rot_y), (cx, cy)


def _insert_watermark(
    page: "pymupdf.Page", text: str, font_size: float, alpha: float,
    rotation: int, position: str, color: Tuple[float, float, float],
    margin: float,
) -> None:
    """Draws the watermark onto `page` as a real text overlay -- see this
    module's docstring for the experimentally-verified reasoning behind
    the derotation_matrix conversion and the `page.rotation` term below.
    """
    display_rect = page.rect
    text_width = pymupdf.get_text_length(
        text, fontname=_FONT_NAME, fontsize=font_size,
    )
    (start_x, start_y), _center = compute_watermark_geometry(
        display_rect.width, display_rect.height, text_width, font_size,
        position, rotation, margin,
    )

    content_point = pymupdf.Point(start_x, start_y) * page.derotation_matrix
    # Glyph rotation in content space = requested (displayed) rotation
    # plus the page's own rotation -- two separate concepts.
    content_angle = (rotation + page.rotation) % 360

    kwargs = dict(
        fontsize=font_size,
        fontname=_FONT_NAME,
        color=color,
        fill=color,
        fill_opacity=alpha,
        stroke_opacity=alpha,
        overlay=True,
    )
    if content_angle % 90 == 0:
        kwargs["rotate"] = int(content_angle)
    else:
        kwargs["morph"] = (content_point, pymupdf.Matrix(float(content_angle)))

    page.insert_text(content_point, text, **kwargs)


# ---------------------------------------------------------------------------
# Add Watermark engine
# ---------------------------------------------------------------------------

def add_watermark(
    source_path: Path,
    output_path: Path,
    page_indices: Optional[List[int]] = None,
    text: str = DEFAULT_TEXT,
    position: str = DEFAULT_POSITION,
    font_size: float = DEFAULT_FONT_SIZE,
    opacity: float = DEFAULT_OPACITY,
    rotation: int = DEFAULT_ROTATION,
    color: Tuple[float, float, float] = DEFAULT_COLOR,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Write a new PDF at `output_path` containing every page of
    `source_path`, unchanged, except that each 0-based index in
    `page_indices` (None = every page) receives the text watermark.
    Unselected pages are left completely untouched.

    - `opacity` is a PERCENTAGE (0-100); `rotation` is degrees
      counter-clockwise as displayed (one of ROTATIONS); `color` is a
      normalized (0.0-1.0) RGB tuple (see COLOR_PRESETS).
    - Re-validates the source via pdf_engine.validate_pdf(): missing,
      corrupt, non-PDF, directory and password-protected sources are
      rejected exactly like every other engine's inputs -- an encrypted
      source is never opened or bypassed here (this tool has no password
      handling of its own).
    - Refuses an `output_path` that is the same file as `source_path`.
    - Re-validates every index against the document's just-opened page
      count, in case the source changed since it was selected.
    - Never modifies the source: it is opened read-only in memory and
      the result is written to a different file.
    - Places the watermark using rotation-aware coordinates and inserts
      it as an overlay (no rasterizing, no page rebuilding); existing
      metadata, page order and page rotations are preserved.
    - Writes atomically via pdf_engine._atomic_save_pdf().
    - progress_callback, if given, receives short, real status messages
      -- never a fabricated percentage.

    Raises WatermarkOptionsError (a PDFEngineError subclass) for invalid
    options or page indices; PDFEngineError for any other PDF/filesystem
    failure.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    clean_text = validate_text(text)
    validate_position(position)
    font_size = validate_font_size(font_size)
    opacity = validate_opacity(opacity)
    rotation = validate_rotation(rotation)
    color = validate_color(color)
    alpha = opacity_to_alpha(opacity)

    if page_indices is not None and len(page_indices) == 0:
        raise WatermarkOptionsError("No pages selected for the watermark.")

    pdf_engine.validate_pdf(source_path)

    try:
        same_file = source_path.resolve() == output_path.resolve()
    except OSError:
        same_file = False
    if same_file:
        raise WatermarkOptionsError(
            "The output file must be different from the source file."
        )

    if progress_callback:
        progress_callback("Adding watermark...")

    try:
        with pymupdf.open(source_path) as doc:
            page_count = doc.page_count

            if page_indices is None:
                target_indices = list(range(page_count))
            else:
                target_indices = sorted(set(page_indices))
                out_of_range = [
                    index for index in target_indices
                    if not isinstance(index, int) or index < 0 or index >= page_count
                ]
                if out_of_range:
                    raise WatermarkOptionsError(
                        "The selected pages no longer match this document "
                        "-- it may have changed since it was selected."
                    )

            for index in target_indices:
                _insert_watermark(
                    doc[index], clean_text, font_size, alpha, rotation,
                    position, color, DEFAULT_MARGIN,
                )

            if progress_callback:
                progress_callback("Saving...")

            pdf_engine._atomic_save_pdf(doc, output_path)
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(
            f"Failed to add a watermark to '{source_path.name}': {exc}"
        ) from exc

    if progress_callback:
        progress_callback("Done.")

    return output_path
