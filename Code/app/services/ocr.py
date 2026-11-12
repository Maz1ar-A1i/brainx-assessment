"""CPU-only OpenCV template OCR for owned synthetic text/math challenges."""
from __future__ import annotations

import io
import time
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

from app.services.synthetic import (
    ALPHABET, ARITHMETIC_CHARSET, FONT_SIZES, ROTATIONS, available_fonts, make_glyph_mask,
)

NORMALIZED_SIZE = 32
TEXT_THRESHOLD = 145


def _decode(image_bytes: bytes) -> np.ndarray:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if image.width < 100 or image.height < 30 or image.width * image.height > 4_000_000:
            raise ValueError("image_dimensions_not_allowed")
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("invalid_image") from exc


def _normalize(mask: np.ndarray) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8) * 255
    ys, xs = np.where(binary > 0)
    if len(xs) == 0:
        raise ValueError("empty_glyph")
    crop = binary[ys.min():ys.max()+1, xs.min():xs.max()+1]
    h, w = crop.shape
    scale = (NORMALIZED_SIZE - 4) / max(h, w)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    canvas = np.zeros((NORMALIZED_SIZE, NORMALIZED_SIZE), dtype=np.uint8)
    x, y = (NORMALIZED_SIZE - nw) // 2, (NORMALIZED_SIZE - nh) // 2
    canvas[y:y+nh, x:x+nw] = resized
    return (canvas >= 110).astype(np.uint8)


@lru_cache(maxsize=4)
def _template_bank(charset: str) -> tuple[np.ndarray, list[str]]:
    masks: list[np.ndarray] = []
    labels: list[str] = []
    for font_path in available_fonts():
        for size in FONT_SIZES:
            for angle in ROTATIONS:
                for char in charset:
                    glyph = np.asarray(make_glyph_mask(char, font_path, size, angle))
                    masks.append(_normalize((glyph > 105).astype(np.uint8)))
                    labels.append(char)
    return np.stack(masks, axis=0).astype(np.uint8), labels


def _extract_slot(gray: np.ndarray, slot_index: int, slot_count: int) -> np.ndarray:
    slot_width = gray.shape[1] / float(slot_count)
    left = int(round(slot_index * slot_width))
    right = int(round((slot_index + 1) * slot_width))
    crop = gray[:, left:right]
    mask = (crop < TEXT_THRESHOLD).astype(np.uint8)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    active_mask = opened if opened.sum() >= 20 else mask

    count, labels, stats, _ = cv2.connectedComponentsWithStats(active_mask, connectivity=8)
    if count <= 1:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if count <= 1:
            raise ValueError("no_glyph_detected")
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    cleaned = (labels == largest).astype(np.uint8)
    if int(cleaned.sum()) < 12:
        raise ValueError("no_glyph_detected")
    return _normalize(cleaned)


def _recognize_fixed_slots(image_bytes: bytes, charset: str, slot_count: int) -> dict:
    started = time.perf_counter()
    gray = _decode(image_bytes)
    target_ratio = 420 / 110
    if abs((gray.shape[1] / gray.shape[0]) - target_ratio) > 0.18:
        raise ValueError("unsupported_synthetic_layout")

    templates, labels = _template_bank(charset)
    label_array = np.asarray(labels)
    decoded: list[str] = []
    confidences: list[float] = []
    for slot in range(slot_count):
        glyph = _extract_slot(gray, slot, slot_count)
        intersection = np.logical_and(templates, glyph).sum(axis=(1, 2)).astype(np.float32)
        union = np.logical_or(templates, glyph).sum(axis=(1, 2)).astype(np.float32)
        scores = intersection / np.maximum(union, 1.0)
        best_idx = int(np.argmax(scores))
        decoded.append(str(label_array[best_idx]))
        confidences.append(float(scores[best_idx]))

    text = "".join(decoded)
    elapsed = (time.perf_counter() - started) * 1000
    similarity = float(np.mean(confidences))
    return {
        "text": text,
        "confidence": round(similarity, 4),
        "character_confidences": [round(value, 4) for value in confidences],
        "accepted_format": similarity >= 0.35,
        "method": "opencv_multifont_template_matching_cpu",
        "processing_time_ms": round(elapsed, 2),
    }


def solve_text_image(image_bytes: bytes) -> dict:
    """Transcribe the five-slot synthetic text family."""
    result = _recognize_fixed_slots(image_bytes, ALPHABET, 5)
    result["accepted_format"] = len(result["text"]) == 5 and result["accepted_format"]
    result["result_label"] = "Transcribed characters"
    return result


def solve_arithmetic_image(image_bytes: bytes) -> dict:
    """Recognize a visual digit/operator/digit expression and calculate it."""
    started = time.perf_counter()
    recognized = _recognize_fixed_slots(image_bytes, ARITHMETIC_CHARSET, 3)
    expression = recognized["text"]
    if len(expression) != 3 or not expression[0].isdigit() or not expression[2].isdigit():
        raise ValueError("unrecognized_arithmetic_expression")
    left, operator, right = int(expression[0]), expression[1], int(expression[2])
    if operator == "+":
        answer = str(left + right)
    elif operator.lower() == "x":
        answer = str(left * right)
    else:
        raise ValueError("unsupported_arithmetic_operator")
    recognized.update({
        "text": answer,
        "expression": expression,
        "expression_confidences": recognized.get("character_confidences", []),
        # This confidence describes visual expression transcription similarity,
        # not a calibrated probability that the arithmetic result is correct.
        "confidence": recognized["confidence"],
        "character_confidences": [],
        "accepted_format": True,
        "method": "opencv_glyph_recognition_plus_arithmetic_cpu",
        "result_label": "Calculated answer",
        "processing_time_ms": round((time.perf_counter() - started) * 1000, 2),
    })
    return recognized


def warmup_solver() -> None:
    """Build text and arithmetic template caches during app startup."""
    _template_bank(ALPHABET)
    _template_bank(ARITHMETIC_CHARSET)
