"""Optional Gemini Vision fallback for candidate-controlled synthetic challenges.

Uses the official Gemini API key header. It does not interact with provider
widgets, retrieve provider tokens, or bypass anti-bot challenges on third-party sites.
"""
from __future__ import annotations

import base64
import io
import re
import time
from typing import Any

import httpx
from PIL import Image

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _image_mime_type(image_bytes: bytes) -> str:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            fmt = (image.format or "").upper()
    except Exception as exc:
        raise ValueError("invalid_image") from exc
    mime = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
    }.get(fmt)
    if not mime:
        raise ValueError("unsupported_image_format")
    return mime


def _safe_model_name(model: str) -> str:
    candidate = (model or "gemini-3.8-flash").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,100}", candidate):
        raise ValueError("invalid_gemini_model_name")
    return candidate


async def solve_with_vision_llm(
    image_bytes: bytes,
    api_key: str,
    model: str = "gemini-3.8-flash",
    timeout_seconds: float = 12.0,
    billing_tier: str = "free",
    input_usd_per_million: float = 0.75,
    output_usd_per_million: float = 3.75,
) -> dict[str, Any]:
    """Ask Gemini to transcribe an owned synthetic image.

    AQ-prefixed AI Studio authorization keys and standard API keys are both
    passed through x-goog-api-key. No Bearer-header fallback or model probing
    is performed, so one configured request has predictable latency.
    """
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("gemini_api_key_not_configured")
    if len(clean_key) > 512:
        raise ValueError("invalid_gemini_api_key_format")

    model_name = _safe_model_name(model)
    mime_type = _image_mime_type(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "contents": [{
            "parts": [
                {
                    "text": (
                        "Transcribe the visible characters in this candidate-controlled synthetic test image. "
                        "Return only the characters, with no spaces, explanation, Markdown or punctuation. "
                        "Do not guess if the characters are not legible."
                    )
                },
                {"inline_data": {"mime_type": mime_type, "data": encoded}},
            ]
        }],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 24},
    }

    started = time.perf_counter()
    timeout = httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))
    url = GEMINI_ENDPOINT.format(model=model_name)
    headers = {
        "x-goog-api-key": clean_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise ValueError("gemini_timeout") from exc
    except httpx.HTTPError as exc:
        raise ValueError("gemini_network_error") from exc

    if response.status_code in (401, 403):
        raise ValueError("gemini_auth_or_permission_error")
    if response.status_code == 404:
        raise ValueError("gemini_model_not_found_or_unavailable")
    if response.status_code == 429:
        raise ValueError("gemini_quota_or_rate_limit")
    if response.status_code >= 500:
        raise ValueError("gemini_service_unavailable")
    if response.status_code >= 400:
        raise ValueError(f"gemini_request_rejected_{response.status_code}")

    try:
        data = response.json()
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        raw_text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        usage = data.get("usageMetadata") or {}
        prompt_tokens = usage.get("promptTokenCount")
        candidate_tokens = usage.get("candidatesTokenCount")
        thinking_tokens = usage.get("thoughtsTokenCount", 0)
        if prompt_tokens is not None:
            prompt_tokens = max(0, int(prompt_tokens))
        if candidate_tokens is not None:
            candidate_tokens = max(0, int(candidate_tokens))
        thinking_tokens = max(0, int(thinking_tokens or 0))
        output_tokens = (
            candidate_tokens + thinking_tokens
            if candidate_tokens is not None else None
        )
    except (ValueError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("gemini_invalid_response") from exc

    # Strict response validation prevents the UI from presenting explanatory text
    # as a successfully recognized answer. Confidence is intentionally not fabricated.
    cleaned = re.sub(r"\s+", "", raw_text).strip().upper()
    cleaned = cleaned.strip("`'\".,:;!?")
    if not re.fullmatch(r"[A-Z0-9]{4,8}", cleaned):
        raise ValueError("gemini_unparseable_output")

    elapsed_ms = (time.perf_counter() - started) * 1000
    tier = (billing_tier or "unknown").strip().lower()
    cost_usd: float | None = None
    cost_note = "Billing tier is unknown; no per-request charge estimate was calculated."
    if tier == "free":
        # The configured model's published Free Tier is no-charge while the
        # project remains within free-tier availability and quota.
        cost_usd = 0.0
        cost_note = "Estimated $0.00 under the configured Gemini Free Tier; quota/availability still applies."
    elif tier == "paid" and prompt_tokens is not None and output_tokens is not None:
        estimated = (
            prompt_tokens * float(input_usd_per_million)
            + output_tokens * float(output_usd_per_million)
        ) / 1_000_000.0
        cost_usd = round(estimated, 10)
        cost_note = (
            f"Estimated from usageMetadata at ${input_usd_per_million}/1M input and "
            f"${output_usd_per_million}/1M output tokens; not an invoice amount."
        )
    elif tier == "paid":
        cost_note = "Paid billing tier selected, but usageMetadata token counts were not returned; cost is unknown."

    return {
        "text": cleaned,
        "confidence": None,
        "character_confidences": [],
        "accepted_format": True,
        "method": f"gemini_vision_{model_name}",
        "processing_time_ms": round(elapsed_ms, 2),
        "billing_tier": tier,
        "usage_tokens": {
            "input": prompt_tokens,
            "output": output_tokens,
        },
        "cost_usd": cost_usd,
        "cost_is_estimate": tier == "paid" and cost_usd is not None,
        "cost_note": cost_note,
    }
