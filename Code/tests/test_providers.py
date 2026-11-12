import pytest
from app.services.providers import verify_provider_token


@pytest.mark.asyncio
async def test_missing_token_is_rejected_without_network():
    result = await verify_provider_token("recaptcha", "", secret="test")
    assert result == {"success": False, "error_code": "missing_token"}


@pytest.mark.asyncio
async def test_unconfigured_provider_is_reported():
    result = await verify_provider_token("turnstile", "some-token", secret="")
    assert result["error_code"] == "provider_not_configured"


@pytest.mark.asyncio
async def test_oversized_token_is_rejected():
    result = await verify_provider_token("hcaptcha", "x" * 10001, secret="test")
    assert result["error_code"] == "token_too_large"


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected():
    result = await verify_provider_token("unknown", "token", secret="test")
    assert result["error_code"] == "unsupported_provider"


def test_provider_error_mapping_is_specific():
    from app.services.providers import _map_provider_errors
    assert _map_provider_errors(["expired-input-response"]) == "token_expired"
    assert _map_provider_errors(["already-seen-response"]) == "token_reused"
    assert _map_provider_errors(["timeout-or-duplicate"]) == "token_expired_or_reused"
    assert _map_provider_errors(["invalid-input-response"]) == "invalid_token"


@pytest.mark.asyncio
async def test_recaptcha_uses_official_form_post(monkeypatch):
    import app.services.providers as module

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"success": True, "hostname": "test.example", "challenge_ts": "2026-11-11T00:00:00Z"}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            assert url == module.PROVIDER_URLS["recaptcha"]
            assert kwargs["data"] == {"secret": "test-secret", "response": "fake-test-token"}
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await module.verify_provider_token("recaptcha", "fake-test-token", secret="test-secret")
    assert result["success"] is True
    assert result["hostname"] == "test.example"


@pytest.mark.asyncio
async def test_turnstile_uses_json_siteverify_and_idempotency(monkeypatch):
    import app.services.providers as module

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"success": False, "error-codes": ["timeout-or-duplicate"]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            assert url == module.PROVIDER_URLS["turnstile"]
            assert kwargs["json"]["secret"] == "test-secret"
            assert kwargs["json"]["response"] == "token"
            assert len(kwargs["json"]["idempotency_key"]) == 36
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await module.verify_provider_token("turnstile", "token", secret="test-secret")
    assert result["success"] is False
    assert result["error_code"] == "token_expired_or_reused"


def test_secret_configuration_errors_are_not_classified_as_token_rejection(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main_module

    async def fake_verify(*args, **kwargs):
        return {
            "success": False,
            "provider": "recaptcha",
            "error_code": "provider_secret_invalid",
        }

    monkeypatch.setattr(main_module, "verify_provider_token", fake_verify)
    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/v1/providers/recaptcha/verify",
            json={"token": "test-token"},
        )
    assert response.status_code == 200
    assert response.json()["verification_state"] == "inconclusive"
