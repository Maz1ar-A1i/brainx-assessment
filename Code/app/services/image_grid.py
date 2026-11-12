"""Owned synthetic 3x3 image-grid CAPTCHA generator and CPU CV solver.

The task is deliberately constrained: each cell contains one filled geometric
shape from a documented colour/shape vocabulary. It demonstrates image
classification and spatial selection, not recognition of arbitrary photographs
or production provider challenge solving.
"""
from __future__ import annotations

import io
import math
import random
import re
import uuid
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

IMAGE_SIZE = 420
GRID_DIMENSION = 3
CELL_SIZE = IMAGE_SIZE // GRID_DIMENSION
COLORS: dict[str, tuple[int, int, int]] = {
    "red": (225, 45, 55),
    "orange": (235, 125, 25),
    "yellow": (235, 190, 25),
    "green": (35, 165, 85),
    "blue": (40, 105, 220),
    "purple": (145, 65, 185),
}
SHAPES = ("circle", "square", "triangle")


def _draw_shape(draw: ImageDraw.ImageDraw, shape: str, cx: int, cy: int,
                color: tuple[int, int, int], size: int) -> None:
    half = size // 2
    if shape == "circle":
        draw.ellipse((cx-half, cy-half, cx+half, cy+half), fill=color)
    elif shape == "square":
        draw.rounded_rectangle((cx-half, cy-half, cx+half, cy+half), radius=4, fill=color)
    elif shape == "triangle":
        draw.polygon([(cx, cy-half-3), (cx+half+2, cy+half), (cx-half-2, cy+half)], fill=color)
    else:
        raise ValueError("unsupported_shape")


def generate_image_grid_challenge(seed: int | None = None) -> tuple[bytes, str, str, str]:
    """Create a colourful 3x3 shape grid and a natural-language target prompt."""
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    target_index = rng.randrange(9)
    target_color = rng.choice(tuple(COLORS))
    target_shape = rng.choice(SHAPES)

    cells: list[tuple[str, str]] = []
    for index in range(9):
        if index == target_index:
            cells.append((target_color, target_shape))
        else:
            cells.append((rng.choice(tuple(COLORS)), rng.choice(SHAPES)))

    # Guarantee at least one target and a non-trivial selection task.
    extra_candidates = [i for i in range(9) if i != target_index]
    rng.shuffle(extra_candidates)
    for index in extra_candidates[:rng.randint(1, 2)]:
        if rng.random() < 0.65:
            cells[index] = (target_color, target_shape)

    image = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), (241, 244, 249))
    draw = ImageDraw.Draw(image)
    for index, (color_name, shape_name) in enumerate(cells):
        row, col = divmod(index, GRID_DIMENSION)
        x0, y0 = col * CELL_SIZE, row * CELL_SIZE
        x1, y1 = x0 + CELL_SIZE, y0 + CELL_SIZE
        bg = rng.choice(((250, 251, 253), (245, 248, 251), (248, 247, 252), (246, 250, 248)))
        draw.rectangle((x0+2, y0+2, x1-2, y1-2), fill=bg, outline=(180, 190, 205), width=2)
        # Low-contrast cell texture and small corner marks; they should not
        # compete with the high-saturation foreground object.
        for _ in range(5):
            px = rng.randint(x0+10, x1-10)
            py = rng.randint(y0+10, y1-10)
            draw.ellipse((px-1, py-1, px+1, py+1), fill=(220, 225, 232))
        cx = x0 + CELL_SIZE // 2 + rng.randint(-4, 4)
        cy = y0 + CELL_SIZE // 2 + rng.randint(-4, 4)
        _draw_shape(draw, shape_name, cx, cy, COLORS[color_name], size=rng.randint(56, 68))

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    plural = {"circle": "circles", "square": "squares", "triangle": "triangles"}[target_shape]
    prompt = f"Select all {target_color} {plural}."
    answer = ",".join(str(i+1) for i, pair in enumerate(cells) if pair == (target_color, target_shape))
    return output.getvalue(), answer, str(uuid.uuid4()), prompt


def _parse_prompt(prompt: str) -> tuple[str, str]:
    text = (prompt or "").lower()
    color_match = re.search(r"\b(red|orange|yellow|green|blue|purple)\b", text)
    shape_match = re.search(r"\b(circles?|squares?|triangles?)\b", text)
    if not color_match or not shape_match:
        raise ValueError("image_grid_prompt_must_name_a_supported_colour_and_shape")
    shape = shape_match.group(1).rstrip("s")
    if shape not in SHAPES:
        raise ValueError("unsupported_image_grid_shape")
    return color_match.group(1), shape


def _classify_color(hues: np.ndarray) -> str:
    if hues.size == 0:
        return "unknown"
    hue = float(np.median(hues))
    if hue < 7 or hue >= 172:
        return "red"
    if hue < 20:
        return "orange"
    if hue < 34:
        return "yellow"
    if hue < 82:
        return "green"
    if hue < 132:
        return "blue"
    return "purple"


def _classify_shape(contour: np.ndarray) -> str:
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return "unknown"
    approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
    vertices = len(approx)
    if vertices == 3:
        return "triangle"
    if vertices == 4:
        x, y, width, height = cv2.boundingRect(approx)
        ratio = width / max(1.0, float(height))
        return "square" if 0.72 <= ratio <= 1.38 else "unknown"
    area = cv2.contourArea(contour)
    circularity = (4.0 * math.pi * area) / max(perimeter * perimeter, 1.0)
    if vertices >= 6 and circularity >= 0.72:
        return "circle"
    return "unknown"


def detect_image_grid_layout(image_bytes: bytes) -> dict[str, Any]:
    """Check whether an image matches the controlled 3x3 colour/shape grid.

    It deliberately requires the bundled fixed-size layout and a recognised
    saturated geometric object in at least eight of nine cells. It is not a
    general-purpose photo-grid CAPTCHA detector.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise ValueError("invalid_image") from exc
    if image.size != (IMAGE_SIZE, IMAGE_SIZE):
        return {"detected": False, "evidence": {"reason": "dimensions_not_matching_grid"}}

    hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
    recognised_cells: list[dict[str, Any]] = []
    for index in range(9):
        row, col = divmod(index, GRID_DIMENSION)
        x0, y0 = col * CELL_SIZE + 12, row * CELL_SIZE + 12
        x1, y1 = (col + 1) * CELL_SIZE - 12, (row + 1) * CELL_SIZE - 12
        crop = hsv[y0:y1, x0:x1]
        mask = cv2.inRange(crop, np.array([0, 95, 70], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(contour) < 350:
            continue
        x, y, width, height = cv2.boundingRect(contour)
        local_mask = mask[y:y+height, x:x+width] > 0
        colour = _classify_color(crop[y:y+height, x:x+width, 0][local_mask])
        shape = _classify_shape(contour)
        if colour in COLORS and shape in SHAPES:
            recognised_cells.append({"cell": index + 1, "colour": colour, "shape": shape})

    detected = len(recognised_cells) >= 8
    return {
        "detected": detected,
        "evidence": {
            "image_dimensions": [IMAGE_SIZE, IMAGE_SIZE],
            "recognised_geometric_cells": len(recognised_cells),
            "required_cells": 8,
            "cells": recognised_cells,
        },
    }


def solve_image_grid_challenge(image_bytes: bytes, prompt: str) -> dict[str, Any]:
    """Classify the colour/shape in each grid cell and return 1-based positions."""
    started = __import__("time").perf_counter()
    target_color, target_shape = _parse_prompt(prompt)
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise ValueError("invalid_image") from exc
    if image.size != (IMAGE_SIZE, IMAGE_SIZE):
        raise ValueError("unsupported_image_grid_dimensions")

    rgb = np.asarray(image)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    recognized: list[dict[str, Any]] = []
    selected: list[int] = []

    for index in range(9):
        row, col = divmod(index, 3)
        x0, y0 = col * CELL_SIZE + 12, row * CELL_SIZE + 12
        x1, y1 = (col + 1) * CELL_SIZE - 12, (row + 1) * CELL_SIZE - 12
        crop = hsv[y0:y1, x0:x1]
        # Foreground objects are saturated. Cell backgrounds, borders and
        # decorations are low-saturation and excluded from this mask.
        mask = cv2.inRange(crop, np.array([0, 95, 70], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            recognized.append({"cell": index + 1, "color": "unknown", "shape": "unknown"})
            continue
        contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(contour) < 350:
            recognized.append({"cell": index + 1, "color": "unknown", "shape": "unknown"})
            continue
        x, y, width, height = cv2.boundingRect(contour)
        local_mask = mask[y:y+height, x:x+width] > 0
        local_hues = crop[y:y+height, x:x+width, 0][local_mask]
        color = _classify_color(local_hues)
        shape = _classify_shape(contour)
        record = {"cell": index + 1, "color": color, "shape": shape}
        recognized.append(record)
        if color == target_color and shape == target_shape:
            selected.append(index + 1)

    text = ",".join(str(value) for value in selected)
    elapsed = (__import__("time").perf_counter() - started) * 1000
    return {
        "text": text,
        "result_label": "Selected grid cells (1–9)",
        "confidence": None,
        "character_confidences": [],
        "accepted_format": True,
        "method": "opencv_hsv_contour_grid_classifier_cpu",
        "target_color": target_color,
        "target_shape": target_shape,
        "selected_cells": selected,
        "cell_classifications": recognized,
        "processing_time_ms": round(elapsed, 2),
    }
