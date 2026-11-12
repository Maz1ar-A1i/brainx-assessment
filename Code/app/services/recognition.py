from __future__ import annotations

import re
from bs4 import BeautifulSoup

PROVIDER_MARKERS = {
    "recaptcha": (
        "google.com/recaptcha", "recaptcha.net/recaptcha", "g-recaptcha",
        "grecaptcha", "g-recaptcha-response", "recaptcha/api.js",
    ),
    "hcaptcha": (
        "hcaptcha.com/1/api.js", "js.hcaptcha.com", "newassets.hcaptcha.com",
        "h-captcha", "hcaptcha", "h-captcha-response",
    ),
    "turnstile": (
        "challenges.cloudflare.com/turnstile", "cf-turnstile",
        "cf-turnstile-response", "turnstile.render",
    ),
}

# Identify known providers which this application does not solve. Do not guess.
UNSUPPORTED_MARKERS = (
    "funcaptcha", "arkoselabs", "geetest", "gee-test", "friendlycaptcha",
    "frc-captcha", "keycaptcha", "prosopo", "captcha-delivery.com",
    "perimeterx", "px-captcha", "datadome", "awswaf", "kasada",
    "cf_chl_opt", "cdn-cgi/challenge-platform", "cloudflare.com/cdn-cgi/challenge",
)


def detect_provider_from_html(
    html: str,
    frame_urls: list[str] | None = None,
    resource_urls: list[str] | None = None,
) -> dict:
    """Recognize supported/known-unsupported CAPTCHA providers in page snapshots.

    This function detects provider markers only. It does not solve provider challenges,
    read response-token fields, click form controls, or submit forms.
    """
    browser_context_provided = frame_urls is not None or resource_urls is not None
    html = html or ""
    soup = BeautifulSoup(html, "html.parser")
    scripts = [str(tag.get("src", "")) for tag in soup.find_all("script")]
    iframes = [str(tag.get("src", "")) for tag in soup.find_all("iframe")]
    attributes: list[str] = []
    for tag in soup.find_all(True):
        for name in ("id", "class", "name", "title", "aria-label", "data-sitekey", "data-size", "data-callback"):
            value = tag.get(name)
            if isinstance(value, list):
                attributes.extend(str(v) for v in value)
            elif value is not None:
                attributes.append(str(value))

    frame_urls = frame_urls or []
    resource_urls = resource_urls or []
    source_text = "\n".join(scripts + iframes + frame_urls + resource_urls + attributes).lower()
    html_lower = html.lower()
    all_text = html_lower + "\n" + source_text

    matches = {
        provider: any(marker in all_text for marker in markers)
        for provider, markers in PROVIDER_MARKERS.items()
    }
    found = [provider for provider, present in matches.items() if present]
    unsupported = [marker for marker in UNSUPPORTED_MARKERS if marker in all_text]

    if len(found) > 1:
        provider = "multiple"
        challenge_type = "ambiguous_multiple_providers"
        supported = False
        next_action = "manual_review"
    elif len(found) == 1:
        provider = found[0]
        supported = True
        next_action = "official_provider_verification_or_owned_test_flow"
        if provider == "recaptcha":
            invisible = bool(re.search(r"data-size\s*=\s*['\"]?invisible", all_text))
            v3_signal = "grecaptcha.execute" in all_text or bool(
                re.search(r"recaptcha/api\.js[^\"']*[?&]render=(?!explicit(?:[&#\"'\s]|$))[^&#\"'\s]+", all_text)
            )
            if invisible:
                challenge_type = "recaptcha_v2_invisible"
            elif v3_signal:
                challenge_type = "recaptcha_v3"
            else:
                challenge_type = "recaptcha_v2_or_unspecified"
        elif provider == "hcaptcha":
            challenge_type = "hcaptcha"
        else:
            challenge_type = "cloudflare_turnstile"
    elif unsupported:
        provider = "unknown"
        challenge_type = "unsupported_provider"
        supported = False
        next_action = "manual_review_or_provider_documentation"
    elif "captcha" in all_text or soup.select(
        "[class*='captcha' i], [id*='captcha' i], [aria-label*='captcha' i]"
    ):
        provider = "unknown"
        challenge_type = "unclassified_captcha_marker"
        supported = False
        next_action = "manual_review"
    else:
        provider = "none"
        challenge_type = "no_supported_provider_marker"
        supported = True
        next_action = "continue_without_provider_captcha_if_inspection_completed"

    return {
        "detected": provider != "none",
        "provider": provider,
        "challenge_type": challenge_type,
        "supported": supported,
        "detection_method": "rendered_dom_frames_and_network_resources" if browser_context_provided else "html_dom_analysis",
        "confidence": None,
        "next_action": next_action,
        "matched_providers": found,
        "unsupported_markers": unsupported,
        "frame_urls": frame_urls,
        "resource_urls": resource_urls,
    }
