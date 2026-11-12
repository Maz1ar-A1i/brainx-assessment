"""Automatic recognition for the bundled controlled synthetic CAPTCHA families.

This classifier recognises only the documented text, arithmetic and 3x3
colour/shape grid generators. It is not a general classifier for arbitrary
third-party CAPTCHA screenshots.
"""
from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image

from app.services.image_grid import detect_image_grid_layout
from app.services.ocr import solve_arithmetic_image, solve_text_image

MIN_TEMPLATE_SIMILARITY = 0.78
MIN_SCORE_MARGIN = 0.08


def detect_synthetic_image_type(image_bytes: bytes) -> dict[str, Any]:
    """Return a family label only when the image matches a supported grammar.

    Text/arithmetic candidates use template similarity scores. These scores are
    pattern-match similarities, not calibrated probabilities. Grid detection
    uses the known 3x3 dimensions and nine-cell colour/shape structure.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = image.size
    except Exception as exc:
        raise ValueError("invalid_image") from exc

    if width < 100 or height < 30 or width * height > 4_000_000:
        raise ValueError("image_dimensions_not_allowed")

    grid_result = detect_image_grid_layout(image_bytes)
    if grid_result["detected"]:
        return {
            "detected": True,
            "challenge_type": "synthetic_image_grid",
            "family": "image-grid",
            "supported": True,
            "detection_method": "3x3_layout_and_cell_colour_shape_analysis",
            "confidence": None,
            "confidence_kind": "deterministic_layout_match",
            "evidence": grid_result["evidence"],
            "next_action": "provide_the_grid_instruction_then_run_the_image_grid_solver",
        }

    if abs((width / float(height)) - (420 / 110)) > 0.18:
        return _unsupported_result("unsupported_image_layout")

    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for family, solver, challenge_type in (
        ("text", solve_text_image, "synthetic_text"),
        ("arithmetic", solve_arithmetic_image, "synthetic_arithmetic"),
    ):
        try:
            result = solver(image_bytes)
            similarity = float(result.get("confidence") or 0.0)
            if not result.get("accepted_format") or similarity < MIN_TEMPLATE_SIMILARITY:
                continue
            if family == "text" and (len(result.get("text", "")) != 5):
                continue
            if family == "arithmetic" and not re.fullmatch(r"[1-9][+x][1-9]", result.get("expression", "")):
                continue
            candidates.append({
                "family": family,
                "challenge_type": challenge_type,
                "similarity": similarity,
                "prediction_preview": result.get("expression") if family == "arithmetic" else result.get("text"),
            })
        except (ValueError, TypeError) as exc:
            errors.append(str(exc)[:80])

    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    if not candidates:
        result = _unsupported_result("unrecognized_synthetic_image")
        result["candidate_errors"] = errors[:3]
        return result

    if len(candidates) > 1 and candidates[0]["similarity"] - candidates[1]["similarity"] < MIN_SCORE_MARGIN:
        return {
            "detected": False,
            "challenge_type": "ambiguous_synthetic_image",
            "family": "unknown",
            "supported": False,
            "detection_method": "opencv_template_candidate_comparison",
            "confidence": round(candidates[0]["similarity"], 4),
            "confidence_kind": "template_similarity_not_probability",
            "candidates": candidates,
            "next_action": "manual_review_or_select_family",
        }

    best = candidates[0]
    return {
        "detected": True,
        "challenge_type": best["challenge_type"],
        "family": best["family"],
        "supported": True,
        "detection_method": "opencv_multifont_template_candidate_comparison",
        "confidence": round(best["similarity"], 4),
        "confidence_kind": "template_similarity_not_probability",
        "evidence": {"candidate_scores": candidates},
        "next_action": "run_the_detected_family_solver",
    }


def _unsupported_result(code: str) -> dict[str, Any]:
    return {
        "detected": False,
        "challenge_type": "unsupported_or_unknown",
        "family": "unknown",
        "supported": False,
        "detection_method": "controlled_synthetic_family_classifier",
        "confidence": None,
        "confidence_kind": None,
        "error_code": code,
        "next_action": "manual_review",
    }
