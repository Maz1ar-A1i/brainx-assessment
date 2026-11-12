from __future__ import annotations

import uuid
from typing import Any

import httpx

PROVIDER_URLS = {
    "recaptcha": "https://www.google.com/recaptcha/api/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
}


def _normalise_provider_errors(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value)[:100] for value in values[:10]]


def _map_provider_errors(codes: list[str]) -> str | None:
    """Map provider-specific error codes to stable application errors."""
    mapping = {
        "missing-input-secret": "provider_secret_missing",
        "invalid-input-secret": "provider_secret_invalid",
        "missing-input-response": "missing_token",
        "invalid-input-response": "invalid_token",
        "expired-input-response": "token_expired",
        "already-seen-response": "token_reused",
        # Google/Cloudflare intentionally combine expiration and replay in this code.
        "timeout-or-duplicate": "token_expired_or_reused",
        "bad-request": "malformed_verification_request",
        "not-using-dummy-passcode": "test_key_mismatch",
        "sitekey-secret-mismatch": "sitekey_secret_mismatch",
        "internal-error": "provider_internal_error",
    }
    for code in codes:
        if code in mapping:
            return mapping[code]
    return None


async def verify_provider_token(provider: str, token: str, *, secret: str,
                                site_key: str = "", timeout_seconds: float = 8.0) -> dict[str, Any]:
    """Use the provider's official server-side siteverify API.

    The token and secret are never included in returned values or logs. No
    automatic retry is attempted after a timeout: a provider may have consumed
    the token before the response was lost.
    """
    if provider not in PROVIDER_URLS:
        return {"success": False, "error_code": "unsupported_provider"}
    if not isinstance(token, str) or not token.strip():
        return {"success": False, "error_code": "missing_token"}
    if len(token) > 10000:
        return {"success": False, "error_code": "token_too_large"}
    if not secret:
        return {"success": False, "error_code": "provider_not_configured"}

    timeout = httpx.Timeout(timeout_seconds, connect=min(4.0, timeout_seconds))
    endpoint = PROVIDER_URLS[provider]
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if provider == "turnstile":
                response = await client.post(endpoint, json={
                    "secret": secret,
                    "response": token,
                    "idempotency_key": str(uuid.uuid4()),
                })
            else:
                data = {"secret": secret, "response": token}
                if provider == "hcaptcha" and site_key:
                    data["sitekey"] = site_key
                response = await client.post(endpoint, data=data)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
                return {"success": False, "provider": provider, "error_code": "invalid_provider_response"}
            error_codes = _normalise_provider_errors(payload.get("error-codes"))
            mapped_error = _map_provider_errors(error_codes)
            return {
                "success": payload["success"],
                "provider": provider,
                "error_codes": error_codes,
                "hostname": str(payload.get("hostname", ""))[:255] or None,
                "challenge_ts": str(payload.get("challenge_ts", ""))[:80] or None,
                "error_code": None if payload["success"] else (mapped_error or "provider_rejected_token"),
            }
    except httpx.TimeoutException:
        return {"success": False, "provider": provider, "error_code": "verification_timeout"}
    except (httpx.HTTPError, ValueError):
        return {"success": False, "provider": provider, "error_code": "provider_unavailable"}
