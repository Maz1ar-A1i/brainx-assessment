import pytest
from app.services.browser_inspector import TargetNotAllowed, validate_target_url


def test_localhost_is_allowed_by_default():
    assert validate_target_url("http://127.0.0.1:8000/demo", {"localhost", "127.0.0.1", "::1"})


def test_authorized_domain_is_allowed():
    assert validate_target_url("https://test.example.org/contact", {"example.org"})


def test_unlisted_domain_is_rejected():
    with pytest.raises(TargetNotAllowed):
        validate_target_url("https://third-party.example/contact", {"localhost"})


def test_embedded_credentials_are_rejected():
    with pytest.raises(TargetNotAllowed):
        validate_target_url("https://user:pass@example.org", {"example.org"})


def test_non_http_url_is_rejected():
    with pytest.raises(TargetNotAllowed):
        validate_target_url("file:///etc/passwd", {"localhost"})


def test_wildcard_does_not_allow_every_target():
    with pytest.raises(TargetNotAllowed):
        validate_target_url("https://arbitrary.example/contact", {"*", "localhost"})
