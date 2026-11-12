from fastapi.testclient import TestClient
from app.main import app


def test_health_and_home():
    with TestClient(app) as client:
        assert client.get("/health").json()["external_ocr_binary_required"] is False
        assert client.get("/").status_code == 200


def test_missing_provider_token_handled():
    with TestClient(app) as client:
        response = client.post("/api/v1/providers/recaptcha/verify", json={"token":""})
        assert response.status_code == 200
        assert response.json()["error_code"] == "missing_token"


def test_synthetic_generate_and_solve():
    with TestClient(app) as client:
        generated = client.post("/api/v1/synthetic/text/generate")
        assert generated.status_code == 200
        challenge_id = generated.headers["X-Challenge-ID"]
        files = {"file": ("challenge.png", generated.content, "image/png")}
        solved = client.post("/api/v1/solve/text", files=files, data={"challenge_id": challenge_id})
        assert solved.status_code == 200
        body = solved.json()
        assert body["success"] is True
        assert body["exact_match"] is True
        assert body["verification_status"] == "exact_match"


def test_browser_target_allowlist():
    with TestClient(app) as client:
        response = client.post("/api/v1/browser/inspect", json={"url":"https://not-allowlisted.invalid/contact"})
        assert response.status_code == 403


def test_csv_is_seeded_on_first_start():
    with TestClient(app) as client:
        summary = client.get("/api/v1/dataset/summary").json()
        assert summary["total_records"] == 200


def test_demo_provider_fixture_renders_local_form():
    with TestClient(app) as client:
        response = client.get("/demo/contact/turnstile")
        assert response.status_code == 200
        assert "provider-widget" in response.text
        assert "providers/${provider}/verify" in response.text
        assert "1x0000000000000000000000000000000AA" not in response.text


def test_playwright_url_scan_route_calls_inspector(monkeypatch):
    import app.main as main_module

    async def fake_inspect(url, **kwargs):
        return {
            "success": True, "report_id": "test", "requested_url": url,
            "final_url": url, "http_status": 200, "page_title": "Owned test form",
            "provider_detection": {
                "detected": True, "provider": "turnstile", "challenge_type": "turnstile",
                "supported": True, "detection_method": "playwright_rendered_dom_scripts_and_iframes",
                "confidence": None, "next_action": "official_provider_test_or_owned_site_verification",
                "matched_providers": ["turnstile"], "unsupported_markers": [], "frame_urls": [],
            },
            "form_count": 1, "forms": [{"method": "POST", "action": "/contact", "field_count": 3, "field_types": ["text", "email", "textarea"]}],
            "screenshot_url": None, "automation": "playwright_chromium",
            "forms_submitted": False, "tokens_collected": False,
        }

    monkeypatch.setattr(main_module, "inspect_authorized_url", fake_inspect)
    with TestClient(app) as client:
        response = client.post("/api/v1/browser/inspect", json={"url":"http://127.0.0.1:8000/demo/contact/turnstile"})
        assert response.status_code == 200
        result = response.json()
        assert result["provider_detection"]["provider"] == "turnstile"
        assert result["automation"] == "playwright_chromium"
        assert result["forms_submitted"] is False


def test_uploaded_image_without_ground_truth_is_not_counted_as_solved():
    with TestClient(app) as client:
        generated = client.post("/api/v1/synthetic/text/generate")
        files = {"file": ("challenge.png", generated.content, "image/png")}
        solved = client.post("/api/v1/solve/text", files=files)
        assert solved.status_code == 200
        body = solved.json()
        assert body["exact_match"] is None
        assert body["ground_truth_available"] is False
        assert body["success"] is False
        assert body["verification_status"] == "unverified_ground_truth_missing"
        assert body["error_code"] == "ground_truth_unavailable"


def test_csv_bulk_import_and_summary():
    from pathlib import Path

    csv_path = Path(__file__).resolve().parents[1] / "data" / "USA_Ecommerce_Platforms_200_Contact_Pages.csv"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/dataset/import",
            files={"file": (csv_path.name, csv_path.read_bytes(), "text/csv")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["imported_records"] == 200
        summary = client.get("/api/v1/dataset/summary").json()
        counts = {row["label"].lower(): row["count"] for row in summary["captcha_distribution"]}
        assert summary["total_records"] == 200
        assert counts["recaptcha"] == 103
        assert counts["invisible recaptcha"] == 30
        assert counts["turnstile"] == 15
        assert counts["hcaptcha"] == 6
        assert counts["none"] == 46


def test_csv_import_returns_actionable_error_for_wrong_headers():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/dataset/import",
            files={"file": ("wrong.csv", b"foo,bar\na,b\n", "text/csv")},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == "missing_required_columns"


def test_dataset_rows_pagination_has_real_total():
    with TestClient(app) as client:
        response = client.get("/api/v1/dataset/rows?limit=15&offset=0")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 15
        assert payload["total_records"] == 200
        assert payload["has_more"] is True


def test_health_distinguishes_playwright_package_from_browser_binary():
    with TestClient(app) as client:
        payload = client.get("/health").json()
        assert "playwright_ready" in payload
        assert isinstance(payload["playwright_ready"], bool)


def test_arithmetic_family_generate_and_solve():
    with TestClient(app) as client:
        generated = client.post("/api/v1/synthetic/arithmetic/generate")
        assert generated.status_code == 200
        challenge_id = generated.headers["X-Challenge-ID"]
        assert generated.headers["X-Challenge-Type"] == "synthetic_arithmetic"
        solved = client.post(
            "/api/v1/solve/arithmetic",
            files={"file": ("math.png", generated.content, "image/png")},
            data={"challenge_id": challenge_id, "engine": "opencv"},
        )
        assert solved.status_code == 200
        body = solved.json()
        assert body["challenge_type"] == "synthetic_arithmetic"
        assert body["success"] is True
        assert body["exact_match"] is True
        assert body["expression"]


def test_image_grid_family_generate_and_solve():
    with TestClient(app) as client:
        generated = client.post("/api/v1/synthetic/image-grid/generate")
        assert generated.status_code == 200
        challenge_id = generated.headers["X-Challenge-ID"]
        prompt = generated.headers["X-Challenge-Prompt"]
        assert generated.headers["X-Challenge-Type"] == "synthetic_image_grid"
        solved = client.post(
            "/api/v1/solve/image-grid",
            files={"file": ("grid.png", generated.content, "image/png")},
            data={"challenge_id": challenge_id},
        )
        assert solved.status_code == 200
        body = solved.json()
        assert body["challenge_type"] == "synthetic_image_grid"
        assert body["prompt"] == prompt
        assert body["success"] is True
        assert body["exact_match"] is True
        assert body["confidence"] is None
        assert body["selected_cells"]


def test_image_grid_upload_requires_task_prompt():
    with TestClient(app) as client:
        generated = client.post("/api/v1/synthetic/image-grid/generate")
        response = client.post(
            "/api/v1/solve/image-grid",
            files={"file": ("grid.png", generated.content, "image/png")},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error_code"] == "image_grid_prompt_required"


def test_synthetic_challenge_id_cannot_be_used_with_another_family():
    with TestClient(app) as client:
        generated = client.post("/api/v1/synthetic/text/generate")
        response = client.post(
            "/api/v1/solve/arithmetic",
            files={"file": ("text.png", generated.content, "image/png")},
            data={"challenge_id": generated.headers["X-Challenge-ID"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == "challenge_family_mismatch"


def test_provider_e2e_uses_configured_visible_browser_and_reports_stages(monkeypatch):
    import app.main as main_module

    async def fake_widget_test(provider, base_url, **kwargs):
        assert provider == "recaptcha"
        assert base_url == main_module.settings.app_base_url.rstrip("/")
        assert kwargs["headless"] is False
        assert kwargs["widget_timeout_ms"] > 0
        assert kwargs["interaction_timeout_ms"] > 0
        return {
            "success": False,
            "provider": provider,
            "verification_state": "awaiting_widget_interaction",
            "error_code": "provider_widget_interaction_timeout",
            "widget_ready": True,
            "token_received": False,
            "siteverify_called": False,
        }

    monkeypatch.setattr(main_module, "run_provider_widget_test", fake_widget_test)
    with TestClient(app) as client:
        response = client.post("/api/v1/browser/test-provider/recaptcha")
        assert response.status_code == 200
        body = response.json()
        assert body["verification_state"] == "awaiting_widget_interaction"
        assert body["token_received"] is False
        assert body["siteverify_called"] is False


def test_demo_fixture_exposes_provider_e2e_stage_attributes():
    with TestClient(app) as client:
        response = client.get("/demo/contact/recaptcha")
        assert response.status_code == 200
        assert 'data-token-state="not_received"' in response.text
        assert 'data-siteverify-state="not_started"' in response.text
        assert "onCaptchaLabProviderScriptError" in response.text


def test_provider_verification_transport_timeout_is_inconclusive(monkeypatch):
    import app.main as main_module

    async def fake_verify(*args, **kwargs):
        return {"success": False, "provider": "recaptcha", "error_code": "verification_timeout"}

    monkeypatch.setattr(main_module, "verify_provider_token", fake_verify)
    with TestClient(app) as client:
        response = client.post("/api/v1/providers/recaptcha/verify", json={"token": "dummy-token"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["verification_state"] == "inconclusive"
        assert payload["error_code"] == "verification_timeout"


def test_provider_rejected_token_is_distinguished_from_transport_error(monkeypatch):
    import app.main as main_module

    async def fake_verify(*args, **kwargs):
        return {"success": False, "provider": "recaptcha", "error_code": "invalid_token", "error_codes": ["invalid-input-response"]}

    monkeypatch.setattr(main_module, "verify_provider_token", fake_verify)
    with TestClient(app) as client:
        response = client.post("/api/v1/providers/recaptcha/verify", json={"token": "invalid-token"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["verification_state"] == "rejected"
        assert payload["error_code"] == "invalid_token"


def test_playwright_siteverify_timeout_is_not_counted_as_provider_rejection(monkeypatch):
    import app.main as main_module

    async def fake_widget_test(provider, base_url, **kwargs):
        return {
            "success": False, "provider": provider,
            "verification_state": "siteverify_timeout",
            "error_code": "provider_siteverify_timeout",
            "widget_ready": True, "token_received": True, "siteverify_called": True,
        }

    monkeypatch.setattr(main_module, "run_provider_widget_test", fake_widget_test)
    with TestClient(app) as client:
        response = client.post("/api/v1/browser/test-provider/recaptcha")
        assert response.status_code == 200
        assert response.json()["verification_state"] == "siteverify_timeout"
        metrics = client.get("/api/v1/metrics").json()
        assert metrics["inconclusive_attempts"] >= 1
