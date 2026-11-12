from __future__ import annotations

import re
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import (
    async_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from app.services.recognition import detect_provider_from_html

PROVIDER_RESOURCE_HOSTS = {
    "google.com", "www.google.com", "www.gstatic.com", "recaptcha.net",
    "www.recaptcha.net", "js.hcaptcha.com", "hcaptcha.com",
    "newassets.hcaptcha.com", "challenges.cloudflare.com", "cloudflare.com",
}
CRITICAL_RESOURCE_TYPES = {"document", "script", "xhr", "fetch"}
SAFE_QUERY_KEYS = {"render", "onload", "hl", "size", "theme", "appearance", "isolated"}


class TargetNotAllowed(ValueError):
    """Raised when a URL is outside the explicitly authorized-host allowlist."""


def _host_matches(host: str, allowed: set[str]) -> bool:
    host = (host or "").lower().strip(".")
    safe_allowed = {
        item.lower().strip(".") for item in allowed
        if item and item.lower().strip(".") not in {"*", "all"}
    }
    return any(host == item or host.endswith("." + item) for item in safe_allowed)


def _clean_report_url(url: str) -> str:
    """Return a display-safe URL: no credentials, query values, or fragment."""
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.hostname:
            # Relative form actions are safe to return without query values.
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        host = parts.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except Exception:
        return ""


def _redact_url_for_detection(url: str) -> str:
    """Keep safe query parameter names (e.g. reCAPTCHA render=) but redact values."""
    try:
        parts = urlsplit(url)
        safe_pairs = [
            (key, "REDACTED")
            for key, _value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() in SAFE_QUERY_KEYS
        ]
        safe_query = urlencode(safe_pairs)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))
    except Exception:
        return _clean_report_url(url)


def validate_target_url(url: str, allowed_hosts: set[str]) -> str:
    candidate = (url or "").strip()
    try:
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise TargetNotAllowed("Only absolute HTTP(S) URLs are accepted.")
        if parts.username or parts.password:
            raise TargetNotAllowed("URLs containing embedded credentials are not allowed.")
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not _host_matches(host, allowed_hosts):
            raise TargetNotAllowed(
                "Target host is not allowlisted. Add only a website you own or are authorized to test to ALLOWED_TARGET_HOSTS."
            )
        # Preserve query parameters only for the in-memory browser navigation.
        return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))
    except TargetNotAllowed:
        raise
    except Exception as exc:
        raise TargetNotAllowed("Invalid target URL.") from exc


def _resource_allowed(url: str, allowed_hosts: set[str], resource_hosts: set[str] | None = None) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() in {"data", "blob", "about"}:
            return True
        if parts.scheme.lower() not in {"http", "https"}:
            return False
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        return (
            _host_matches(host, allowed_hosts)
            or _host_matches(host, PROVIDER_RESOURCE_HOSTS)
            or _host_matches(host, resource_hosts or set())
        )
    except Exception:
        return False


def _safe_browser_error_code(exc: BaseException) -> str:
    """Expose only a browser/network error identifier, never a raw call log or URL."""
    message = str(exc)
    match = re.search(r"net::(ERR_[A-Z0-9_]+)", message)
    if match:
        return match.group(1)[:80]
    if isinstance(exc, PlaywrightTimeoutError):
        return "PLAYWRIGHT_TIMEOUT"
    return type(exc).__name__[:80]


def _inspection_result_error(
    url: str, code: str, message: str, browser_error_code: str | None = None
) -> dict:
    return {
        "success": False,
        "inspection_status": "inconclusive_navigation",
        "error_code": code,
        "message": message,
        "browser_error_code": browser_error_code,
        "requested_url": _clean_report_url(url),
        "final_url": None,
        "http_status": None,
        "page_title": None,
        "provider_detection": {
            "detected": False,
            "provider": "unknown",
            "challenge_type": "inspection_inconclusive",
            "supported": False,
            "detection_method": "playwright_rendered_dom_frames_and_network_resources",
            "confidence": None,
            "next_action": "retry_or_manual_review",
            "matched_providers": [],
            "unsupported_markers": [],
            "frame_urls": [],
            "resource_urls": [],
        },
        "form_count": 0,
        "forms": [],
        "blocked_critical_resources": [],
        "failed_critical_resources": [],
        "http_error_resources": [],
        "frame_collection_errors": [],
        "screenshot_url": None,
        "automation": "playwright_chromium",
        "forms_submitted": False,
        "tokens_collected": False,
    }


async def inspect_authorized_url(
    url: str,
    *,
    allowed_hosts: set[str],
    timeout_ms: int,
    artifact_dir: str,
    executable_path: str | None = None,
    resource_hosts: set[str] | None = None,
    headless: bool = True,
) -> dict:
    """Inspect one explicitly authorized URL; never submit forms or collect tokens."""
    safe_url = validate_target_url(url, allowed_hosts)
    artifacts = Path(artifact_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    report_id = uuid.uuid4().hex
    screenshot_file = artifacts / f"scan_{report_id}.png"

    request_urls: list[str] = []
    failed_requests: list[dict] = []
    blocked_requests: list[dict] = []
    http_error_resources: list[dict] = []
    page_errors: list[str] = []
    frame_errors: list[dict] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless, executable_path=executable_path or None)
        context = None
        try:
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000},
                java_script_enabled=True,
                accept_downloads=False,
            )
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            page.on("request", lambda req: request_urls.append(req.url) if len(request_urls) < 1500 else None)
            page.on("requestfailed", lambda req: failed_requests.append({
                "url": req.url,
                "resource_type": req.resource_type,
                "error": (req.failure or "request_failed")[:160],
            }) if len(failed_requests) < 300 else None)
            page.on("pageerror", lambda exc: page_errors.append(type(exc).__name__) if len(page_errors) < 30 else None)

            def on_response(response):
                try:
                    request = response.request
                    if response.status >= 400 and request.resource_type in CRITICAL_RESOURCE_TYPES:
                        if len(http_error_resources) < 200:
                            http_error_resources.append({
                                "url": response.url,
                                "resource_type": request.resource_type,
                                "status": int(response.status),
                            })
                except Exception:
                    return

            page.on("response", on_response)

            async def guard_route(route):
                request = route.request
                if _resource_allowed(request.url, allowed_hosts, resource_hosts):
                    await route.continue_()
                else:
                    if len(blocked_requests) < 300:
                        blocked_requests.append({"url": request.url, "resource_type": request.resource_type})
                    await route.abort("blockedbyclient")

            await page.route("**/*", guard_route)
            response = None
            navigation_error: str | None = None
            browser_error_code: str | None = None
            try:
                response = await page.goto(safe_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                navigation_error = "browser_navigation_timeout"
                browser_error_code = _safe_browser_error_code(exc)
            except PlaywrightError as exc:
                navigation_error = "browser_navigation_failed"
                browser_error_code = _safe_browser_error_code(exc)

            if page.url and page.url != "about:blank":
                # Trigger common lazy-loaded form/widget content; never interact with form controls.
                for _ in range(4):
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                        await page.wait_for_timeout(250)
                    except PlaywrightError:
                        break
                try:
                    await page.evaluate("window.scrollTo(0, 0);")
                    await page.wait_for_timeout(650)
                except PlaywrightError:
                    pass

            if not page.url or page.url == "about:blank":
                return _inspection_result_error(
                    safe_url,
                    navigation_error or "page_not_loaded",
                    "The browser could not load the target page. Check URL access, network policy and allowlisted resources.",
                    browser_error_code,
                )

            title = await page.title()
            final_url = page.url
            frame_urls_raw = sorted({
                frame.url for frame in page.frames if frame.url and frame.url != "about:blank"
            })
            frame_snapshots: list[str] = []
            forms_info: list[dict] = []

            for frame in page.frames:
                try:
                    frame_html = await frame.content()
                    if frame_html:
                        frame_snapshots.append(frame_html[:500_000])
                    frame_forms = await frame.evaluate("""() => Array.from(document.querySelectorAll('form')).map(form => {
                        const elements = Array.from(form.querySelectorAll('input, textarea, select'));
                        return {
                            method: (form.getAttribute('method') || 'GET').toUpperCase(),
                            action: form.getAttribute('action') || window.location.href,
                            field_count: elements.length,
                            field_types: elements.slice(0, 30).map(el => ({
                                tag: el.tagName.toLowerCase(),
                                type: (el.getAttribute('type') || 'text').toLowerCase(),
                                required: el.hasAttribute('required')
                            }))
                        };
                    })""")
                    for form_info in (frame_forms or []):
                        form_info["action"] = _clean_report_url(form_info.get("action", ""))
                        forms_info.append(form_info)
                except Exception as exc:
                    frame_errors.append({
                        "frame_url": _clean_report_url(getattr(frame, "url", "")),
                        "error_type": type(exc).__name__,
                    })

            # Deduplicate forms discovered in same-origin frame traversal.
            deduped_forms = []
            seen_forms = set()
            for form in forms_info:
                key = (form.get("method"), form.get("action"), form.get("field_count"), tuple(
                    (x.get("tag"), x.get("type")) for x in form.get("field_types", [])
                ))
                if key not in seen_forms:
                    seen_forms.add(key)
                    deduped_forms.append(form)
            forms_info = deduped_forms

            combined_html = "\n".join(frame_snapshots)
            safe_frame_urls = [_clean_report_url(value) for value in frame_urls_raw]
            detection_resource_urls = [_redact_url_for_detection(value) for value in request_urls]
            safe_resource_urls = [_clean_report_url(value) for value in request_urls]
            detection = detect_provider_from_html(
                combined_html,
                frame_urls=safe_frame_urls,
                resource_urls=detection_resource_urls,
            )

            blocked_critical = [item for item in blocked_requests if item["resource_type"] in CRITICAL_RESOURCE_TYPES]
            failed_critical = [item for item in failed_requests if item["resource_type"] in CRITICAL_RESOURCE_TYPES]
            http_critical = [item for item in http_error_resources if item["resource_type"] in CRITICAL_RESOURCE_TYPES]
            http_status = response.status if response is not None else None
            warning_codes: list[str] = []
            if navigation_error:
                warning_codes.append(navigation_error)
            if http_status is None:
                warning_codes.append("no_main_document_response")
            elif http_status >= 400:
                warning_codes.append("http_error_response")
            if blocked_critical:
                warning_codes.append("critical_resources_blocked")
            if failed_critical:
                warning_codes.append("critical_resources_failed")
            if http_critical:
                warning_codes.append("critical_resources_http_error")
            if page_errors:
                warning_codes.append("page_javascript_errors")
            if frame_errors:
                warning_codes.append("frame_collection_errors")

            provider = detection.get("provider", "unknown")
            scan_status = "completed"
            if provider == "none" and warning_codes:
                if "http_error_response" in warning_codes:
                    scan_status = "inconclusive_http_error"
                elif any(code in warning_codes for code in (
                    "critical_resources_blocked", "critical_resources_failed", "critical_resources_http_error"
                )):
                    scan_status = "inconclusive_resources"
                elif "frame_collection_errors" in warning_codes:
                    scan_status = "inconclusive_frame_collection"
                elif "page_javascript_errors" in warning_codes:
                    scan_status = "inconclusive_page_script_error"
                else:
                    scan_status = "inconclusive_navigation"
            elif provider == "none" and len(forms_info) == 0:
                scan_status = "inconclusive_no_form"
                warning_codes.append("no_form_found_on_contact_page")
            elif provider != "none" and warning_codes:
                scan_status = "completed_with_warnings"

            if provider in {"recaptcha", "hcaptcha", "turnstile"}:
                next_step = (
                    "Provider recognized. Continue only through the provider's official test widget and server-side "
                    "verification workflow on an owned or authorized test page."
                )
            elif provider in {"unknown", "multiple"}:
                next_step = "A challenge was found but is unsupported or ambiguous; record it for manual review."
            elif scan_status.startswith("inconclusive"):
                next_step = "Inspection was incomplete; do not interpret this result as 'no CAPTCHA'. Retry after fixing page/resource access."
            else:
                next_step = "A form was found, but no supported provider marker appeared in the completed inspection."

            try:
                await page.screenshot(path=str(screenshot_file), full_page=True, animations="disabled")
                screenshot_url = f"/artifacts/{screenshot_file.name}"
            except PlaywrightError:
                screenshot_url = None

            return {
                "success": not scan_status.startswith("inconclusive"),
                "inspection_status": scan_status,
                "report_id": report_id,
                "requested_url": _clean_report_url(safe_url),
                "final_url": _clean_report_url(final_url),
                "http_status": http_status,
                "page_title": title or _clean_report_url(safe_url),
                "provider_detection": detection,
                "form_count": len(forms_info),
                "forms": forms_info[:30],
                "workflow_next_step": next_step,
                "warning_codes": sorted(set(warning_codes)),
                "blocked_critical_resources": [
                    {"url": _clean_report_url(item["url"]), "resource_type": item["resource_type"]}
                    for item in blocked_critical[:50]
                ],
                "failed_critical_resources": [
                    {"url": _clean_report_url(item["url"]), "resource_type": item["resource_type"], "error": item["error"]}
                    for item in failed_critical[:50]
                ],
                "http_error_resources": [
                    {"url": _clean_report_url(item["url"]), "resource_type": item["resource_type"], "status": item["status"]}
                    for item in http_critical[:50]
                ],
                "frame_collection_errors": frame_errors[:30],
                "page_error_count": len(page_errors),
                "screenshot_url": screenshot_url,
                "automation": "playwright_chromium",
                "browser_mode": "headless" if headless else "visible",
                "forms_submitted": False,
                "tokens_collected": False,
            }
        finally:
            if context is not None:
                await context.close()
            await browser.close()


async def run_provider_widget_test(
    provider: str,
    base_url: str,
    *,
    widget_timeout_ms: int = 25000,
    interaction_timeout_ms: int = 90000,
    verification_timeout_ms: int = 18000,
    executable_path: str | None = None,
    headless: bool = False,
) -> dict:
    """Run an owned provider test page and report each E2E stage separately.

    This deliberately does not solve or click third-party challenges. For a
    visible provider widget such as reCAPTCHA v2, the candidate completes the
    official test widget in the opened local Chromium window. The widget page
    sends its callback token directly to our own backend's Siteverify adapter;
    this function observes only status attributes, never the token itself.
    """
    if provider not in {"recaptcha", "hcaptcha", "turnstile"}:
        return {
            "success": False,
            "provider": provider,
            "verification_state": "unsupported_provider",
            "error_code": "unsupported_provider",
        }

    target = f"{base_url.rstrip('/')}/demo/contact/{provider}"
    request_failures: list[dict] = []
    page_errors: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            executable_path=executable_path or None,
        )
        context = None
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
                accept_downloads=False,
            )
            page = await context.new_page()
            page.on(
                "requestfailed",
                lambda request: request_failures.append({
                    "host": urlsplit(request.url).hostname or "unknown",
                    "resource_type": request.resource_type,
                    "error": (request.failure or "request_failed")[:120],
                }) if len(request_failures) < 30 else None,
            )
            page.on(
                "pageerror",
                lambda error: page_errors.append(type(error).__name__) if len(page_errors) < 20 else None,
            )

            try:
                response = await page.goto(
                    target,
                    wait_until="domcontentloaded",
                    timeout=widget_timeout_ms,
                )
            except PlaywrightTimeoutError as exc:
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "widget_page_navigation_timeout",
                    "error_code": "provider_test_page_navigation_timeout",
                    "target_url": target,
                    "browser_error_code": _safe_browser_error_code(exc),
                    "widget_ready": False,
                    "token_received": False,
                    "siteverify_called": False,
                    "diagnostics": {"failed_request_count": len(request_failures), "page_error_types": page_errors},
                }
            except PlaywrightError as exc:
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "widget_page_navigation_failed",
                    "error_code": "provider_test_page_navigation_failed",
                    "message": "Playwright could not navigate to the local provider test fixture.",
                    "target_url": target,
                    "browser_error_code": _safe_browser_error_code(exc),
                    "widget_ready": False,
                    "token_received": False,
                    "siteverify_called": False,
                    "diagnostics": {"failed_requests": request_failures, "page_error_types": page_errors},
                }

            if response is not None and response.status >= 400:
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "widget_page_http_error",
                    "error_code": "provider_test_page_http_error",
                    "http_status": response.status,
                    "target_url": target,
                    "widget_ready": False,
                    "token_received": False,
                    "siteverify_called": False,
                }

            # First wait for provider script/render initialization. This separates
            # network/widget setup errors from a widget that is awaiting interaction.
            try:
                await page.wait_for_function(
                    """() => {
                        const el = document.querySelector('#verification-state');
                        if (!el) return false;
                        return ['widget_ready','verifying','success','failed','error','widget_load_error'].includes(el.dataset.status || '');
                    }""",
                    timeout=widget_timeout_ms,
                )
            except PlaywrightTimeoutError:
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "widget_load_timeout",
                    "error_code": "provider_widget_load_timeout",
                    "message": "The provider JavaScript did not initialize. Check network access, browser resource failures and the configured public site key.",
                    "target_url": target,
                    "widget_ready": False,
                    "token_received": False,
                    "siteverify_called": False,
                    "diagnostics": {"failed_requests": request_failures, "page_error_types": page_errors},
                }

            initial_state = await page.locator("#verification-state").get_attribute("data-status")
            if initial_state == "widget_load_error":
                message = await page.locator("#verification-state").inner_text()
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "widget_load_error",
                    "error_code": "provider_widget_script_error",
                    "message": message,
                    "target_url": target,
                    "widget_ready": False,
                    "token_received": False,
                    "siteverify_called": False,
                    "diagnostics": {"failed_requests": request_failures, "page_error_types": page_errors},
                }

            # Some official test widgets may issue their token automatically. Others
            # (notably the reCAPTCHA v2 checkbox) require a user click. Headed mode
            # lets the candidate complete that official test widget.
            try:
                await page.wait_for_function(
                    """() => {
                        const el = document.querySelector('#verification-state');
                        if (!el) return false;
                        return el.dataset.tokenState === 'received' ||
                               ['failed','error','widget_load_error'].includes(el.dataset.status || '');
                    }""",
                    timeout=interaction_timeout_ms,
                )
            except PlaywrightTimeoutError:
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "awaiting_widget_interaction",
                    "error_code": "provider_widget_interaction_timeout",
                    "message": "The widget loaded but did not issue a token before the interaction timeout. Complete the visible provider test widget in the Chromium window and retry.",
                    "target_url": target,
                    "widget_ready": True,
                    "token_received": False,
                    "siteverify_called": False,
                    "headless": headless,
                    "diagnostics": {"failed_requests": request_failures, "page_error_types": page_errors},
                }

            token_state = await page.locator("#verification-state").get_attribute("data-token-state")
            if token_state != "received":
                state = await page.locator("#verification-state").get_attribute("data-status")
                message = await page.locator("#verification-state").inner_text()
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": state or "widget_error",
                    "error_code": "provider_widget_callback_error",
                    "message": message,
                    "target_url": target,
                    "widget_ready": True,
                    "token_received": False,
                    "siteverify_called": False,
                }

            # The widget page performs the backend Siteverify request. Observe its
            # terminal state and never extract the token into the Playwright process.
            try:
                await page.wait_for_function(
                    """() => {
                        const el = document.querySelector('#verification-state');
                        return el && ['success','failed','error'].includes(el.dataset.siteverifyState || '');
                    }""",
                    timeout=verification_timeout_ms,
                )
            except PlaywrightTimeoutError:
                return {
                    "success": False,
                    "provider": provider,
                    "browser_automation": "playwright_chromium",
                    "verification_state": "siteverify_timeout",
                    "error_code": "provider_siteverify_timeout",
                    "message": "A provider token was acquired, but the local backend did not complete the server-side Siteverify request in time.",
                    "target_url": target,
                    "widget_ready": True,
                    "token_received": True,
                    "siteverify_called": True,
                    "headless": headless,
                }

            state_element = page.locator("#verification-state")
            siteverify_state = await state_element.get_attribute("data-siteverify-state")
            error_code = await state_element.get_attribute("data-error-code")
            message = await state_element.inner_text()
            hostname = await state_element.get_attribute("data-hostname")
            challenge_ts = await state_element.get_attribute("data-challenge-ts")

            return {
                "success": siteverify_state == "success",
                "provider": provider,
                "browser_automation": "playwright_chromium",
                "verification_state": siteverify_state or "unknown",
                "error_code": error_code or None,
                "message": message,
                "target_url": target,
                "widget_ready": True,
                "token_received": True,
                "siteverify_called": True,
                "hostname": hostname or None,
                "challenge_ts": challenge_ts or None,
                "headless": headless,
                "diagnostics": {"failed_request_count": len(request_failures), "page_error_types": page_errors},
            }
        finally:
            if context is not None:
                await context.close()
            await browser.close()
