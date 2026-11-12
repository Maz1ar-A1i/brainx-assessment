import pytest
from app.services.browser_inspector import (
    _clean_report_url,
    _inspection_result_error,
    _redact_url_for_detection,
    validate_target_url,
)
from app.services.recognition import detect_provider_from_html


def test_report_url_redacts_credentials_query_values_and_fragments():
    url = "https://user:password@example.org/contact?token=secret&x=1#fragment"
    assert _clean_report_url(url) == "https://example.org/contact"


def test_detection_url_keeps_safe_parameter_name_but_redacts_value():
    url = "https://www.google.com/recaptcha/api.js?render=private-site-key&hl=en&secret=do-not-show"
    redacted = _redact_url_for_detection(url)
    assert "render=REDACTED" in redacted
    assert "hl=REDACTED" in redacted
    assert "private-site-key" not in redacted
    assert "do-not-show" not in redacted


def test_recaptcha_v3_resource_query_is_detected_without_exposing_site_key():
    url = _redact_url_for_detection(
        "https://www.google.com/recaptcha/api.js?render=actual-site-key"
    )
    result = detect_provider_from_html("<html></html>", resource_urls=[url])
    assert result["provider"] == "recaptcha"
    assert result["challenge_type"] == "recaptcha_v3"
    assert "actual-site-key" not in str(result)


def test_inconclusive_navigation_result_has_consistent_schema():
    result = _inspection_result_error(
        "https://example.org/contact?session=secret",
        "navigation_failed",
        "Could not load page",
    )
    assert result["inspection_status"] == "inconclusive_navigation"
    assert result["provider_detection"]["provider"] == "unknown"
    assert result["success"] is False
    assert "session=secret" not in str(result)


def test_localhost_ipv6_is_supported_by_allowlist():
    assert validate_target_url("http://[::1]:8000/contact", {"::1"}) == "http://[::1]:8000/contact"


class _FakeResponse:
    status = 200


class _FakeFrame:
    url = "http://127.0.0.1/test"

    def __init__(self, html: str, forms=None):
        self._html = html
        self._forms = forms or []

    async def content(self):
        return self._html

    async def evaluate(self, _script):
        return self._forms


class _FakePage:
    def __init__(self, frame):
        self.url = frame.url
        self.frames = [frame]

    def on(self, *_args):
        return None

    def set_default_timeout(self, *_args):
        return None

    async def route(self, *_args):
        return None

    async def goto(self, *_args, **_kwargs):
        return _FakeResponse()

    async def evaluate(self, *_args):
        return None

    async def wait_for_timeout(self, *_args):
        return None

    async def title(self):
        return "Local owned test page"

    async def screenshot(self, *, path, **_kwargs):
        from pathlib import Path
        Path(path).write_bytes(b"fake-png")


class _FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page

    async def close(self):
        return None


class _FakeBrowser:
    def __init__(self, context):
        self._context = context

    async def new_context(self, **_kwargs):
        return self._context

    async def close(self):
        return None


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def launch(self, **_kwargs):
        return self._browser


class _FakePlaywrightManager:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_browser_inspector_marks_contact_page_without_form_inconclusive(monkeypatch, tmp_path):
    import app.services.browser_inspector as module

    frame = _FakeFrame("<html><body><h1>Contact</h1></body></html>")
    page = _FakePage(frame)
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    monkeypatch.setattr(module, "async_playwright", lambda: _FakePlaywrightManager(browser))

    result = await module.inspect_authorized_url(
        "http://127.0.0.1/contact",
        allowed_hosts={"127.0.0.1"},
        timeout_ms=1000,
        artifact_dir=str(tmp_path),
    )
    assert result["inspection_status"] == "inconclusive_no_form"
    assert result["success"] is False
    assert "no_form_found_on_contact_page" in result["warning_codes"]


@pytest.mark.asyncio
async def test_browser_inspector_detects_provider_in_rendered_script_and_form(monkeypatch, tmp_path):
    import app.services.browser_inspector as module

    html = '<html><head><script src="https://www.google.com/recaptcha/api.js?render=explicit"></script></head><body><form><input type="email"></form><div class="g-recaptcha"></div></body></html>'
    forms = [{"method": "POST", "action": "http://127.0.0.1/submit?secret=redact", "field_count": 1, "field_types": [{"tag": "input", "type": "email", "required": False}]}]
    frame = _FakeFrame(html, forms)
    page = _FakePage(frame)
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    monkeypatch.setattr(module, "async_playwright", lambda: _FakePlaywrightManager(browser))

    result = await module.inspect_authorized_url(
        "http://127.0.0.1/contact",
        allowed_hosts={"127.0.0.1"},
        timeout_ms=1000,
        artifact_dir=str(tmp_path),
    )
    assert result["success"] is True
    assert result["inspection_status"] == "completed"
    assert result["provider_detection"]["provider"] == "recaptcha"
    assert result["form_count"] == 1
    assert "secret=redact" not in str(result)
