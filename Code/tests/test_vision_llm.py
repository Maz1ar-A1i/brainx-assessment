import io

import pytest
from PIL import Image

from app.services.vision_llm import solve_with_vision_llm


def _png_bytes():
    image = Image.new("RGB", (80, 30), (255, 255, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_aq_authorization_key_uses_x_goog_api_key_header(monkeypatch):
    import app.services.vision_llm as module

    seen = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": "AB123"}]}}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 10, "thoughtsTokenCount": 2},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            seen["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json, headers):
            seen["url"] = url
            seen["headers"] = headers
            seen["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    result = await solve_with_vision_llm(
        _png_bytes(),
        api_key="AQ.test-authorization-key",
        model="gemini-3.8-flash",
    )
    assert result["text"] == "AB123"
    assert result["confidence"] is None
    assert result["cost_usd"] == 0.0
    assert result["billing_tier"] == "free"
    assert result["usage_tokens"] == {"input": 1000, "output": 12}
    assert seen["headers"]["x-goog-api-key"] == "AQ.test-authorization-key"
    assert "Authorization" not in seen["headers"]
    assert seen["url"].endswith("/models/gemini-3.8-flash:generateContent")
    assert seen["payload"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_gemini_missing_key_returns_clear_error():
    with pytest.raises(ValueError, match="gemini_api_key_not_configured"):
        await solve_with_vision_llm(_png_bytes(), api_key="")


@pytest.mark.asyncio
async def test_gemini_rejects_unparseable_model_output(monkeypatch):
    import app.services.vision_llm as module

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "The answer might be AB123."}]}}]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(ValueError, match="gemini_unparseable_output"):
        await solve_with_vision_llm(_png_bytes(), api_key="AQ.test")


@pytest.mark.asyncio
async def test_gemini_paid_tier_estimates_cost_from_usage_metadata(monkeypatch):
    import app.services.vision_llm as module

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": "AB123"}]}}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 10, "thoughtsTokenCount": 2},
            }

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await solve_with_vision_llm(
        _png_bytes(), api_key="AQ.test", billing_tier="paid",
        input_usd_per_million=0.75, output_usd_per_million=3.75,
    )
    expected = (1000 * 0.75 + 12 * 3.75) / 1_000_000
    assert result["cost_usd"] == round(expected, 10)
    assert result["cost_is_estimate"] is True


@pytest.mark.asyncio
async def test_gemini_paid_cost_unknown_without_usage_metadata(monkeypatch):
    import app.services.vision_llm as module

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "AB123"}]}}]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await solve_with_vision_llm(_png_bytes(), api_key="AQ.test", billing_tier="paid")
    assert result["cost_usd"] is None
    assert result["cost_is_estimate"] is False
