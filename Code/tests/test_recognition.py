from app.services.recognition import detect_provider_from_html


def test_recaptcha_detected():
    r = detect_provider_from_html('<script src="https://www.google.com/recaptcha/api.js"></script><div class="g-recaptcha"></div>')
    assert r["provider"] == "recaptcha"


def test_invisible_recaptcha_detected():
    r = detect_provider_from_html('<script src="https://www.google.com/recaptcha/api.js?render=explicit"></script><div class="g-recaptcha" data-size="invisible"></div>')
    assert r["challenge_type"] == "recaptcha_v2_invisible"


def test_hcaptcha_detected_in_frame():
    r = detect_provider_from_html('<html></html>', ["https://newassets.hcaptcha.com/captcha/v1/iframe"])
    assert r["provider"] == "hcaptcha"


def test_turnstile_detected():
    r = detect_provider_from_html('<div class="cf-turnstile"></div>')
    assert r["provider"] == "turnstile"


def test_multiple_providers_are_ambiguous():
    r = detect_provider_from_html('<div class="g-recaptcha"></div><div class="cf-turnstile"></div>')
    assert r["provider"] == "multiple" and not r["supported"]


def test_unknown_captcha_is_not_silently_supported():
    r = detect_provider_from_html('<div class="geetest_widget"></div>')
    assert r["provider"] == "unknown" and not r["supported"]


def test_no_captcha():
    r = detect_provider_from_html('<html><body><form></form></body></html>')
    assert r["provider"] == "none"


def test_plain_html_reports_html_detection_method():
    result = detect_provider_from_html("<html><body>hello</body></html>")
    assert result["detection_method"] == "html_dom_analysis"


def test_network_resource_url_can_identify_provider():
    result = detect_provider_from_html(
        "<html></html>",
        resource_urls=["https://challenges.cloudflare.com/turnstile/v0/api.js"],
    )
    assert result["provider"] == "turnstile"
    assert result["detection_method"] == "rendered_dom_frames_and_network_resources"


def test_recaptcha_v3_classification_does_not_mistake_explicit_render():
    explicit = detect_provider_from_html(
        '<script src="https://www.google.com/recaptcha/api.js?render=explicit"></script>'
    )
    v3 = detect_provider_from_html(
        '<script src="https://www.google.com/recaptcha/api.js?render=site-key"></script>'
    )
    assert explicit["challenge_type"] == "recaptcha_v2_or_unspecified"
    assert v3["challenge_type"] == "recaptcha_v3"
