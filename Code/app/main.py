from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import Database
from app.services.browser_inspector import (
    TargetNotAllowed,
    inspect_authorized_url,
    run_provider_widget_test,
)
from app.services.dataset_import import DatasetImportError, parse_dataset_csv
from app.services.ocr import solve_arithmetic_image, solve_text_image, warmup_solver
from app.services.image_grid import generate_image_grid_challenge, solve_image_grid_challenge
from app.services.synthetic_detection import detect_synthetic_image_type
from app.services.providers import verify_provider_token
from app.services.recognition import detect_provider_from_html
from app.services.synthetic import generate_arithmetic_challenge, generate_text_challenge

logger = logging.getLogger("captchalab")
settings = get_settings()
database = Database(settings.database_path)
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
ARTIFACT_DIR = Path(settings.artifact_dir).resolve()
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    source_csv = PROJECT_DIR / "data" / "USA_Ecommerce_Platforms_200_Contact_Pages.csv"
    if source_csv.exists() and database.dataset_summary()["total_records"] == 0:
        try:
            rows = parse_dataset_csv(source_csv.read_bytes())
            database.replace_imported_sites(rows)
            logger.info("Seeded supplied dataset with %s records", len(rows))
        except Exception:
            # Keep the application startable, but do not hide an import failure.
            logger.exception("Could not seed the supplied CSV dataset")
    await asyncio.to_thread(warmup_solver)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    description=(
        "Playwright inspection for explicitly authorized pages, CPU-only synthetic CAPTCHA "
        "solving, and official provider token verification."
    ),
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/artifacts", StaticFiles(directory=ARTIFACT_DIR), name="artifacts")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class HtmlDetectionRequest(BaseModel):
    html: str = Field(min_length=1, max_length=1_000_000)


class BrowserInspectRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    show_browser: bool = False


class TokenVerificationRequest(BaseModel):
    token: str = Field(default="", max_length=10000)


def _site_keys() -> dict[str, str]:
    return {
        "recaptcha": settings.recaptcha_site_key,
        "hcaptcha": settings.hcaptcha_site_key,
        "turnstile": settings.turnstile_site_key,
    }


def _provider_config(provider: str) -> tuple[str, str, str]:
    values = {
        "recaptcha": (
            settings.recaptcha_site_key,
            settings.recaptcha_secret_key,
            "https://www.google.com/recaptcha/api.js?onload=onCaptchaLabProviderLoad&render=explicit",
        ),
        "hcaptcha": (
            settings.hcaptcha_site_key,
            settings.hcaptcha_secret_key,
            "https://js.hcaptcha.com/1/api.js?onload=onCaptchaLabProviderLoad&render=explicit",
        ),
        "turnstile": (
            settings.turnstile_site_key,
            settings.turnstile_secret_key,
            "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onCaptchaLabProviderLoad&render=explicit",
        ),
    }
    if provider not in values:
        raise KeyError(provider)
    return values[provider]


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "app_name": settings.app_name, "site_keys": _site_keys()},
    )


@app.get("/health")
async def health():
    playwright_installed = False
    browser_binary_available = False
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            playwright_installed = True
            executable = settings.chromium_executable or playwright.chromium.executable_path
            browser_binary_available = Path(executable).is_file()
    except Exception:
        logger.exception("Playwright health check failed")

    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "python_version": sys.version.split()[0],
        "playwright_installed": playwright_installed,
        "chromium_binary_available": browser_binary_available,
        "playwright_ready": playwright_installed and browser_binary_available,
        "ocr_engine": "opencv_multifont_template_matching_cpu",
        "external_ocr_binary_required": False,
        "allowed_target_hosts": sorted(settings.allowed_hosts),
        "unsafe_wildcard_ignored": settings.unsafe_wildcard_configured,
    }


@app.get("/api/v1/config")
async def public_config():
    # Only public site keys are ever returned to the browser.
    return {"site_keys": _site_keys()}


@app.post("/api/v1/detect/html")
async def detect_html(payload: HtmlDetectionRequest):
    started = time.perf_counter()
    result = detect_provider_from_html(payload.html)
    latency = (time.perf_counter() - started) * 1000
    status = "failed" if result["provider"] in {"multiple", "unknown"} else "success"
    database.add_attempt(
        provider=result["provider"],
        challenge_type=result["challenge_type"],
        method="html_dom_analysis",
        status=status,
        latency_ms=latency,
    )
    result["processing_time_ms"] = round(latency, 2)
    return result


@app.post("/api/v1/detect/synthetic-image")
async def detect_synthetic_image(file: UploadFile = File(...)):
    """Identify one of the bundled synthetic families from an uploaded image."""
    if not file.filename:
        raise HTTPException(status_code=400, detail={"error_code": "missing_file"})
    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/bmp", "application/octet-stream"}
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail={"error_code": "unsupported_file_type"})
    contents = await file.read(settings.max_upload_bytes + 1)
    if not contents:
        raise HTTPException(status_code=400, detail={"error_code": "empty_file"})
    if len(contents) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail={"error_code": "file_too_large"})

    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(detect_synthetic_image_type, contents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_code": str(exc)[:120]}) from exc
    except Exception as exc:
        logger.exception("Synthetic image-family recognition failed")
        raise HTTPException(status_code=500, detail={"error_code": "synthetic_detection_failed"}) from exc

    latency = (time.perf_counter() - started) * 1000
    database.add_attempt(
        provider="custom",
        challenge_type=result.get("challenge_type", "unsupported_or_unknown"),
        method="synthetic_family_auto_detection",
        status="success" if result.get("detected") else "failed",
        latency_ms=latency,
        confidence=result.get("confidence"),
        cost_usd=0.0,
        error_code=result.get("error_code"),
    )
    result["processing_time_ms"] = round(latency, 2)
    return result


@app.post("/api/v1/browser/inspect")
async def browser_inspect(payload: BrowserInspectRequest):
    started = time.perf_counter()
    try:
        result = await inspect_authorized_url(
            payload.url,
            allowed_hosts=settings.allowed_hosts,
            resource_hosts=settings.resource_hosts,
            timeout_ms=settings.browser_timeout_ms,
            artifact_dir=str(ARTIFACT_DIR),
            executable_path=settings.chromium_executable or None,
            headless=not payload.show_browser,
        )
    except TargetNotAllowed as exc:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "target_not_allowlisted", "message": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("Playwright inspection failed")
        message = str(exc).lower()
        if "executable doesn't exist" in message or "browser_type.launch" in message:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "playwright_browser_not_installed",
                    "setup_command": ".\\.venv\\Scripts\\python.exe -m playwright install chromium",
                },
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "browser_inspection_failed",
                "message": "The browser could not complete the inspection. Check the app log for the diagnostic detail.",
            },
        ) from exc

    latency = (time.perf_counter() - started) * 1000
    detection = result["provider_detection"]
    inspection_status = result.get("inspection_status", "completed")
    if inspection_status.startswith("inconclusive"):
        attempt_status = "inconclusive"
    elif detection["provider"] in {"multiple", "unknown"}:
        attempt_status = "failed"
    else:
        attempt_status = "success"
    database.add_attempt(
        provider=detection["provider"],
        challenge_type=detection["challenge_type"],
        method="playwright_rendered_dom_frames_network",
        status=attempt_status,
        latency_ms=latency,
        error_code=inspection_status if attempt_status == "inconclusive" else None,
    )
    result["processing_time_ms"] = round(latency, 2)
    return result


@app.post("/api/v1/browser/test-provider/{provider}")
async def playwright_provider_test(provider: str):
    """Run the official widget on our own local fixture and observe Siteverify.

    The default is headed mode. For checkbox-style test widgets, the candidate
    completes the visible widget in the Chromium window opened on this machine.
    """
    provider = provider.lower()
    if provider not in {"recaptcha", "hcaptcha", "turnstile"}:
        raise HTTPException(status_code=404, detail={"error_code": "unsupported_provider"})
    started = time.perf_counter()
    try:
        result = await run_provider_widget_test(
            provider,
            settings.app_base_url.rstrip("/"),
            widget_timeout_ms=int(settings.provider_widget_timeout_seconds * 1000),
            interaction_timeout_ms=int(settings.provider_interaction_timeout_seconds * 1000),
            verification_timeout_ms=int(settings.provider_verification_timeout_seconds * 1000),
            executable_path=settings.chromium_executable or None,
            headless=settings.provider_test_headless,
        )
    except Exception as exc:
        logger.exception("Official provider widget test failed")
        message = str(exc).lower()
        if "executable doesn't exist" in message or "browser_type.launch" in message:
            raise HTTPException(
                status_code=503,
                detail={"error_code": "playwright_browser_not_installed", "setup_command": ".\\.venv\\Scripts\\python.exe -m playwright install chromium"},
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={"error_code": "playwright_provider_test_failed"},
        ) from exc

    latency = (time.perf_counter() - started) * 1000
    if result.get("success") is True:
        attempt_status = "success"
    elif result.get("verification_state") in {"failed", "rejected"}:
        # A provider rejection is a real negative verification result.
        attempt_status = "failed"
    else:
        # Widget/network/configuration/timeouts are not proof of an invalid token.
        attempt_status = "inconclusive"
    database.add_attempt(
        provider=provider,
        challenge_type=f"{provider}_official_test_widget",
        method="playwright_widget_plus_siteverify",
        status=attempt_status,
        latency_ms=latency,
        error_code=result.get("error_code"),
    )
    result["processing_time_ms"] = round(latency, 2)
    result["cost_usd"] = 0.0
    return result


@app.get("/demo/contact/{provider}", response_class=HTMLResponse)
async def provider_demo(request: Request, provider: str):
    provider = provider.lower()
    if provider not in {"recaptcha", "hcaptcha", "turnstile"}:
        raise HTTPException(status_code=404, detail="Unsupported provider")
    site_key, _secret, script_url = _provider_config(provider)
    return templates.TemplateResponse(
        request=request,
        name="demo_contact.html",
        context={"request": request, "provider": provider, "site_key": site_key, "script_url": script_url},
    )


@app.post("/api/v1/synthetic/{family}/generate")
async def generate_synthetic(family: str):
    family = family.lower()
    if family == "text":
        image_bytes, answer, challenge_id = generate_text_challenge()
        challenge_type = "synthetic_text"
        prompt = "Transcribe the five characters shown in the image."
    elif family in {"arithmetic", "math"}:
        image_bytes, answer, challenge_id, prompt = generate_arithmetic_challenge()
        family = "arithmetic"
        challenge_type = "synthetic_arithmetic"
    elif family in {"image-grid", "grid"}:
        image_bytes, answer, challenge_id, prompt = generate_image_grid_challenge()
        family = "image-grid"
        challenge_type = "synthetic_image_grid"
    else:
        raise HTTPException(status_code=404, detail={"error_code": "unsupported_synthetic_family"})

    database.save_synthetic_challenge(
        challenge_id,
        answer,
        challenge_type=challenge_type,
        prompt=prompt,
    )
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "X-Challenge-ID": challenge_id,
            "X-Challenge-Type": challenge_type,
            "X-Challenge-Prompt": prompt,
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/v1/solve/{family}")
async def solve_synthetic(
    family: str,
    file: UploadFile = File(...),
    challenge_id: str | None = Form(default=None),
    engine: str = Form(default="auto"),
    prompt: str = Form(default=""),
):
    family = family.lower()
    family_map = {
        "text": "synthetic_text",
        "arithmetic": "synthetic_arithmetic",
        "math": "synthetic_arithmetic",
        "image-grid": "synthetic_image_grid",
        "grid": "synthetic_image_grid",
    }
    challenge_type = family_map.get(family)
    if not challenge_type:
        raise HTTPException(status_code=404, detail={"error_code": "unsupported_synthetic_family"})
    if not file.filename:
        raise HTTPException(status_code=400, detail={"error_code": "missing_file"})
    if engine not in {"auto", "opencv", "gemini"}:
        raise HTTPException(status_code=422, detail={"error_code": "unsupported_solver_engine"})
    if engine == "gemini" and family != "text":
        raise HTTPException(
            status_code=422,
            detail={"error_code": "gemini_fallback_currently_supports_text_family_only"},
        )

    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/bmp", "application/octet-stream"}
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail={"error_code": "unsupported_file_type"})
    contents = await file.read(settings.max_upload_bytes + 1)
    if not contents:
        raise HTTPException(status_code=400, detail={"error_code": "empty_file"})
    if len(contents) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail={"error_code": "file_too_large"})

    stored_challenge = database.get_synthetic_challenge(challenge_id) if challenge_id else None
    if stored_challenge and stored_challenge["challenge_type"] != challenge_type:
        raise HTTPException(status_code=409, detail={"error_code": "challenge_family_mismatch"})
    effective_prompt = str(stored_challenge.get("prompt") or "") if stored_challenge else prompt.strip()
    if challenge_type == "synthetic_image_grid" and not effective_prompt:
        raise HTTPException(status_code=422, detail={"error_code": "image_grid_prompt_required"})

    started = time.perf_counter()
    result: dict[str, Any] | None = None
    opencv_error: str | None = None

    solver_by_type = {
        "synthetic_text": solve_text_image,
        "synthetic_arithmetic": solve_arithmetic_image,
    }
    try:
        if challenge_type == "synthetic_image_grid":
            result = await asyncio.to_thread(solve_image_grid_challenge, contents, effective_prompt)
        else:
            solver = solver_by_type[challenge_type]
            if engine != "gemini":
                result = await asyncio.to_thread(solver, contents)
    except ValueError as exc:
        opencv_error = str(exc)[:120]
    except Exception:
        logger.exception("Synthetic CV solver failed")
        opencv_error = "opencv_solver_error"

    should_use_gemini = challenge_type == "synthetic_text" and (
        engine == "gemini" or (
            engine == "auto"
            and (result is None or float(result.get("confidence") or 0.0) < settings.gemini_fallback_threshold)
            and bool(settings.gemini_api_key)
        )
    )
    if engine == "gemini" and not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail={"error_code": "gemini_not_configured"})
    if should_use_gemini:
        try:
            from app.services.vision_llm import solve_with_vision_llm
            llm_result = await solve_with_vision_llm(
                contents,
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                timeout_seconds=settings.gemini_timeout_seconds,
                billing_tier=settings.gemini_billing_tier,
                input_usd_per_million=settings.gemini_input_usd_per_million,
                output_usd_per_million=settings.gemini_output_usd_per_million,
            )
            if result is not None:
                llm_result["fallback_triggered"] = True
                llm_result["opencv_prediction"] = result.get("text")
                llm_result["opencv_confidence"] = result.get("confidence")
            result = llm_result
        except ValueError as exc:
            logger.warning("Gemini fallback was not successful: %s", str(exc)[:120])
            if result is None:
                raise HTTPException(status_code=502, detail={"error_code": str(exc)[:120]}) from exc
            result["llm_error"] = str(exc)[:120]
        except Exception as exc:
            logger.exception("Gemini fallback failed")
            if result is None:
                raise HTTPException(status_code=502, detail={"error_code": "gemini_request_failed"}) from exc
            result["llm_error"] = "gemini_request_failed"

    if result is None:
        raise HTTPException(
            status_code=422,
            detail={"error_code": opencv_error or "unsupported_synthetic_image"},
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    expected = str(stored_challenge["answer"]) if stored_challenge else None
    exact_match = result["text"] == expected if expected is not None else None
    if expected is None:
        accepted = False
        outcome = "unverified"
        verification_status = "unverified_ground_truth_missing"
        error_code = "ground_truth_unavailable"
    else:
        accepted = bool(result.get("accepted_format") and exact_match is True)
        outcome = "success" if accepted else "failed"
        verification_status = "exact_match" if accepted else "exact_match_failed"
        error_code = None if accepted else "incorrect_synthetic_answer"

    method = str(result.get("method", "unknown"))
    raw_cost = result.get("cost_usd")
    if method.startswith("opencv"):
        recorded_cost = 0.0
    elif isinstance(raw_cost, (int, float)):
        recorded_cost = float(raw_cost)
    else:
        recorded_cost = None

    database.add_attempt(
        provider="custom",
        challenge_type=challenge_type,
        method=method,
        status=outcome,
        confidence=result.get("confidence"),
        latency_ms=elapsed_ms,
        cost_usd=recorded_cost,
        error_code=error_code,
    )
    result.update({
        "success": accepted,
        "verification_status": verification_status,
        "ground_truth_available": expected is not None,
        "expected": expected,
        "challenge_type": challenge_type,
        "prompt": effective_prompt,
        "provider": "custom",
        "exact_match": exact_match,
        "cost_usd": recorded_cost,
        "error_code": error_code,
        "processing_time_ms": round(elapsed_ms, 2),
    })
    if opencv_error and result.get("fallback_triggered"):
        result["opencv_error"] = opencv_error
    return result


@app.post("/api/v1/providers/{provider}/verify")
async def verify_token(provider: str, payload: TokenVerificationRequest):
    provider = provider.lower()
    if provider not in {"recaptcha", "hcaptcha", "turnstile"}:
        raise HTTPException(status_code=404, detail={"error_code": "unsupported_provider"})
    secrets = {
        "recaptcha": settings.recaptcha_secret_key,
        "hcaptcha": settings.hcaptcha_secret_key,
        "turnstile": settings.turnstile_secret_key,
    }
    sitekeys = {
        "recaptcha": settings.recaptcha_site_key,
        "hcaptcha": settings.hcaptcha_site_key,
        "turnstile": settings.turnstile_site_key,
    }
    started = time.perf_counter()
    result = await verify_provider_token(
        provider,
        payload.token,
        secret=secrets[provider],
        site_key=sitekeys[provider],
        timeout_seconds=settings.provider_timeout_seconds,
    )
    latency = (time.perf_counter() - started) * 1000
    error_codes = result.get("error_codes", [])
    error_code = result.get("error_code") or (error_codes[0] if error_codes else None)
    inconclusive_errors = {
        "provider_not_configured",
        "verification_timeout",
        "provider_unavailable",
        "invalid_provider_response",
        "provider_internal_error",
        "provider_secret_missing",
        "provider_secret_invalid",
        "malformed_verification_request",
        "sitekey_secret_mismatch",
    }
    if result.get("success") is True:
        verification_state = "verified"
        attempt_status = "success"
    elif error_code in inconclusive_errors:
        verification_state = "inconclusive"
        attempt_status = "inconclusive"
    else:
        verification_state = "rejected"
        attempt_status = "failed"
    database.add_attempt(
        provider=provider,
        challenge_type=f"{provider}_official_token",
        method="official_siteverify_api",
        status=attempt_status,
        latency_ms=latency,
        cost_usd=0.0,
        error_code=error_code,
    )
    result["verification_state"] = verification_state
    result["processing_time_ms"] = round(latency, 2)
    result["cost_usd"] = 0.0
    return result


@app.get("/api/v1/metrics")
async def metrics():
    return database.metrics()


@app.get("/api/v1/dataset/summary")
async def dataset_summary():
    return database.dataset_summary()


@app.get("/api/v1/dataset/rows")
async def get_dataset_rows(
    limit: int = Query(default=15, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=200),
):
    rows = database.get_imported_sites(limit=limit, offset=offset, query=q)
    total = database.count_imported_sites(query=q)
    return {
        "rows": rows,
        "count": len(rows),
        "total_records": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
    }


@app.post("/api/v1/dataset/evaluate-synthetic")
async def evaluate_synthetic_dataset(samples: int = Query(default=300, ge=3, le=5000)):
    from scripts.evaluate_dataset import run_evaluation

    return await asyncio.to_thread(run_evaluation, max_samples=samples)


@app.post("/api/v1/dataset/import")
async def import_dataset(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail={"error_code": "csv_required"})
    contents = await file.read(settings.max_csv_bytes + 1)
    if len(contents) > settings.max_csv_bytes:
        raise HTTPException(status_code=413, detail={"error_code": "csv_too_large"})
    try:
        rows = parse_dataset_csv(contents)
    except DatasetImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected CSV import failure")
        raise HTTPException(status_code=400, detail={"error_code": "invalid_csv"}) from exc

    count = database.replace_imported_sites(rows)
    summary = database.dataset_summary()
    return {
        "success": True,
        "imported_records": count,
        "total_records": summary["total_records"],
        "summary": summary,
        "message": f"Successfully imported {count} CSV records.",
    }
