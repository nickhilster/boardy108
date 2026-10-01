"""Composite caption text onto a (possibly remixed) meme base image.

Pure local operation: no network, no GPU, deterministic. This is the one
module that needs Pillow (see requirements.txt) -- PanelForge's first real
dependency, added because stdlib has no real font-rendering support.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import pipeline_config

_WINDOWS_IMPACT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\Impact.ttf"),
    Path(r"C:\Windows\Fonts\impact.ttf"),
]

_MARGIN_RATIO = 0.04
_STROKE_RATIO = 0.06


@dataclass(frozen=True)
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def resolve_font_path(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise pipeline_config.ConfigError(f"meme_font does not exist: {path}")
        return path
    configured = pipeline_config.get("meme_font")
    if configured is not None:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise pipeline_config.ConfigError(f"meme_font does not exist: {path}")
        return path
    for candidate in _WINDOWS_IMPACT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise pipeline_config.ConfigError(
        "No caption font found. Set PANELFORGE_MEME_FONT, add \"meme_font\" to a "
        "config file, or install Impact where this pipeline can find it -- see "
        "pipeline_config.py."
    )


def layout_boxes(box_count: int, image_size: tuple[int, int]) -> list[Box]:
    """Classic meme layout: box 0 on top, the last box on the bottom, any
    extra boxes stacked evenly between them. Imgflip's own get_memes response
    doesn't expose per-box coordinates either, so this deterministic default
    matches how imgflip's own caption_image behaves for the common case."""
    if box_count < 1:
        raise ValueError("box_count must be at least 1")
    width, height = image_size
    margin = int(height * _MARGIN_RATIO)
    band_height = max(1, int(height * 0.22))

    if box_count == 1:
        return [Box(margin, margin, width - margin, margin + band_height)]

    boxes = [Box(margin, margin, width - margin, margin + band_height)]
    boxes.append(Box(margin, height - margin - band_height, width - margin, height - margin))
    if box_count > 2:
        inner_top = boxes[0].y1
        inner_bottom = boxes[1].y0
        available = max(0, inner_bottom - inner_top)
        slot_height = available // (box_count - 1)
        for index in range(box_count - 2):
            y0 = inner_top + index * slot_height
            boxes.insert(1 + index, Box(margin, y0, width - margin, y0 + max(1, slot_height - margin)))
    return boxes[:box_count]


def _fit_font(draw: ImageDraw.ImageDraw, text: str, box: Box, font_path: Path) -> ImageFont.FreeTypeFont:
    size = box.height
    while size > 8:
        font = ImageFont.truetype(str(font_path), size)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=max(1, int(size * _STROKE_RATIO)))
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= box.width and text_height <= box.height:
            return font
        size -= 2
    return ImageFont.truetype(str(font_path), 8)


def compose(
    base_image_path: Path | str,
    captions: list[dict[str, Any]],
    *,
    output_path: Path | str,
    box_count: int,
    font_path: Path | str | None = None,
    uppercase: bool = True,
) -> Path:
    resolved_font = resolve_font_path(font_path)
    image = Image.open(base_image_path).convert("RGB")
    boxes = layout_boxes(box_count, image.size)
    draw = ImageDraw.Draw(image)

    by_index = {entry["box_index"]: entry["text"] for entry in captions}
    for index, box in enumerate(boxes):
        text = by_index.get(index, "")
        if not text.strip():
            continue
        rendered_text = text.strip().upper() if uppercase else text.strip()
        font = _fit_font(draw, rendered_text, box, resolved_font)
        stroke_width = max(1, int(font.size * _STROKE_RATIO))
        bbox = draw.textbbox((0, 0), rendered_text, font=font, stroke_width=stroke_width)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = box.x0 + max(0, (box.width - text_width) // 2) - bbox[0]
        y = box.y0 + max(0, (box.height - text_height) // 2) - bbox[1]
        draw.text(
            (x, y),
            rendered_text,
            font=font,
            fill="white",
            stroke_width=stroke_width,
            stroke_fill="black",
        )

    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        os.close(handle)
        image.save(temp_name, format="PNG")
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return destination
