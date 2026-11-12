# CaptchaLab AI — BrainX Technical Assessment

**Runtime:** CPython 3.12, 64-bit Windows 10/11  
**Backend:** FastAPI · **Browser automation:** Playwright Chromium · **Computer vision:** OpenCV + NumPy + Pillow · **Database:** SQLite

The application is local-first and CPU-only. The bundled custom solvers do not require Tesseract, a downloaded OCR model, a GPU, or a paid CAPTCHA-solving service. Gemini is an optional remote fallback for the synthetic text family only; it is not required to run the application or the three-family benchmark.

## 1. Assessment scope and what the application does

The assessment requires CAPTCHA recognition, at least one custom or authorized solving workflow, official provider token acquisition and server-side verification, structured failure handling/reporting, and a working demonstration. The supplied PDF restricts live tests to synthetic, local, candidate-owned or official provider test environments. It explicitly excludes contact-form automation and bulk submissions to unrelated third-party websites.

The supplied 200-row CSV is imported as **reference metadata**. It contains contact-page URLs and CAPTCHA labels. Importing it does not authorize the app to crawl all 200 websites. Module 01 accepts one target at a time and enforces an allowlist of local or explicitly authorized hosts. It does not submit contact forms or harvest response tokens.

## 2. Windows installation

Use a clean **64-bit Python 3.12 or 3.13** installation. Do not copy a `.venv` from another computer or Python version; compiled packages such as NumPy, Pydantic Core and Greenlet are interpreter-specific.

1. Extract the source folder.
2. Open PowerShell in `CaptchaLab_AI_Full`.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

The setup script selects Python 3.12 first, otherwise 3.13; checks that it is 64-bit; deletes and recreates `.venv`; installs pinned application/test dependencies; installs Playwright Chromium; and verifies imports for FastAPI, Pydantic Core, NumPy, OpenCV, Greenlet and Playwright. It creates `.env` from `.env.example` only if `.env` does not already exist.

4. Start the application:

```powershell
.\run.ps1
```

Or double-click `setup.bat` once and `run.bat` thereafter.

Open:

- Dashboard: <http://127.0.0.1:8000>
- API documentation: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

Stop the server with `Ctrl+C`.

### If Playwright Chromium is missing

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m playwright install --list
```

The Playwright Python package and the Chromium browser binary are separate installations. A missing browser is reported by `/health` as `chromium_binary_available: false` and `playwright_ready: false`.

### If a virtual environment has incompatible binaries

```powershell
if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Do not use Python 3.14 with this pinned dependency set. The project currently declares and tests Python 3.12–3.13.

## 3. Environment configuration

`.env.example` is the safe template. It contains public provider test keys and test secrets from the providers' published documentation; it must only be used for local testing. Copy it if needed:

```powershell
Copy-Item .env.example .env
notepad .env
```

Important settings:

```dotenv
ALLOWED_TARGET_HOSTS=localhost,127.0.0.1,::1,captchalab.test
ALLOWED_RESOURCE_HOSTS=www.google.com,google.com,www.gstatic.com,recaptcha.net,www.recaptcha.net,js.hcaptcha.com,hcaptcha.com,newassets.hcaptcha.com,challenges.cloudflare.com,cloudflare.com
APP_BASE_URL=http://127.0.0.1:8000
PROVIDER_TEST_HEADLESS=false
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.8-flash
GEMINI_TIMEOUT_SECONDS=12
GEMINI_FALLBACK_THRESHOLD=0.50
GEMINI_BILLING_TIER=free
GEMINI_INPUT_USD_PER_MILLION=0.75
GEMINI_OUTPUT_USD_PER_MILLION=3.75
```

Never use `ALLOWED_TARGET_HOSTS=*`. Wildcard entries are deliberately ignored. Add only a hostname that you own or are explicitly authorized to test. If that owned test site loads provider JavaScript from a separate CDN, add only the necessary asset hosts to `ALLOWED_RESOURCE_HOSTS`. Restart the application after editing `.env`.

### Gemini (optional)

Gemini is optional. The local OpenCV solver runs without a key. Google AI Studio keys may use the newer `AQ.` authorization-key format or the standard `AIza` format; this application sends the configured key in the `x-goog-api-key` request header. Do not reject a key solely because it does not start with `AIza`.

The optional Gemini fallback currently applies only to synthetic text transcription. It makes a remote request and may add latency or consume quota. The API does not return a per-request USD charge or calibrated confidence, so those values are reported as unknown. Do not place a real key in source code, screenshots, shared archives, or Git. If a key has previously been shared, revoke it and issue a new one.

## 4. Application modules

### Module 01 — URL-driven Playwright inspection

Input: one URL on localhost or an owned/authorized test host.

Playwright Chromium:

- Navigates to the requested URL and renders JavaScript-driven content.
- Collects rendered DOM snapshots, accessible frame URLs, script/resource URLs, and limited network/response diagnostics.
- Inspects form metadata only: method, sanitized action URL, field count, and field types. It does not collect field values.
- Recognizes Google reCAPTCHA, hCaptcha and Cloudflare Turnstile markers.
- Reports known unsupported providers, multiple-provider ambiguity, and unknown CAPTCHA-like markers instead of guessing.
- Saves a screenshot when possible and reports blocked/failed critical resources.
- Distinguishes a completed “no supported provider marker” result from `inconclusive_*` states caused by navigation, HTTP, resource or frame errors. An incomplete scan is not called CAPTCHA-free.

By default the scanner runs headlessly. Tick **Show Chromium window** in Module 01 to see the browser while inspecting. The URL allowlist is a security boundary against arbitrary browser navigation/SSRF; it is not a missing Playwright feature. The CSV's 200 URLs are reference metadata, not an instruction to crawl or submit all 200 unrelated websites.

This module does not click contact-form submit buttons, submit forms, collect provider response tokens, or bypass anti-bot controls on unrelated sites.

### Module 02 — Three-family synthetic CAPTCHA solver

The synthetic dataset now contains three different task families, not merely different visual styles of the same text task:

| Family | Task | Solver |
|---|---|---|
| `synthetic_text` | Transcribe five coloured/rotated characters | OpenCV segmentation + multi-font template matching |
| `synthetic_arithmetic` | Read a digit/operator/digit expression and calculate its answer | Visual glyph recognition + arithmetic |
| `synthetic_image_grid` | Select 1-based cells matching a colour-and-shape prompt in a 3×3 grid | HSV colour segmentation + contour/shape classification |

The image-grid generator uses a documented controlled vocabulary of six colours and three geometric shapes. It is a distinct computer-vision task, but it is **not** general photo recognition. None of these families reproduces Google reCAPTCHA, hCaptcha, Cloudflare Turnstile or their production challenge systems.

The bundled solvers run locally on CPU using OpenCV, NumPy and Pillow. No Tesseract executable, downloaded OCR model or GPU is required. The upload workflow includes **Identify CAPTCHA Type**, which automatically classifies images matching one of the three controlled synthetic families before the candidate runs its family solver. The classifier returns unsupported/ambiguous for images outside those documented generator patterns; it does not claim general CAPTCHA recognition. Generated challenge IDs are stored server-side with family, prompt and ground truth. The user-uploaded file name is never trusted as ground truth. A prediction for an upload without a valid server-issued challenge ID is labelled **unverified** and is not counted as a successful solve.

Generate a balanced corpus of 300 images (100 per family):

```powershell
.\.venv\Scripts\python.exe scripts\generate_synthetic_dataset.py --samples 300 --seed 20261112
```

Run the complete benchmark:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py --samples 300
```

Or regenerate and benchmark in one command:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark.py --samples 300 --regenerate --seed 20261112
```

If the clean submission package contains the labels manifest but not generated PNGs, the evaluator automatically rebuilds the default seeded corpus before evaluating it. Report exact-match accuracy separately by family. Benchmark values measure only the bundled controlled generators and matching solvers; they are not a generalization claim for arbitrary real-world CAPTCHA images.

### Module 03 — Official provider token acquisition and verification

This module demonstrates both parts required by the assessment:

1. Load the provider's official **test widget** on a local page.
2. The widget's own callback issues a response token. The app labels this **token acquired — not yet verified**.
3. Send the token to the FastAPI backend.
4. The backend calls that provider's official server-side Siteverify endpoint with the test secret.
5. Display `verified` only when the provider returns `success: true`. Provider rejection is `rejected`; network/timeout/configuration failures are `inconclusive`.

Supported providers:

- Google reCAPTCHA v2 test integration
- hCaptcha test integration
- Cloudflare Turnstile test integration

Local fixtures:

- `http://127.0.0.1:8000/demo/contact/recaptcha`
- `http://127.0.0.1:8000/demo/contact/hcaptcha`
- `http://127.0.0.1:8000/demo/contact/turnstile`

The **Playwright-assisted provider test** opens a separate visible Chromium window by default. Some provider test widgets may issue a token automatically; a checkbox-style test widget may require you to complete the visible test interaction. Playwright waits for the widget callback and then for the backend Siteverify result. This is an assisted end-to-end integration test, not an autonomous solution to an arbitrary CAPTCHA challenge. If the widget loads but no callback occurs, the result is `awaiting_widget_interaction`; if a token is issued but Siteverify does not complete, the result is `siteverify_timeout` or `inconclusive`. These are not reported as provider token rejection.

The manual **Load Official Widget → Verify Token** workflow is available separately. A widget callback alone never counts as verification. The app does not log or persist raw tokens or secrets and does not automatically retry the same token after a timeout, because the provider may already have consumed it. Test keys are public test credentials and provide no production anti-bot protection; never use them in production.

### Module 04 — Supplied CSV import, metrics and benchmark

The supplied `USA_Ecommerce_Platforms_200_Contact_Pages.csv` is imported automatically at first startup when the database is empty. The dashboard also supports re-importing a compatible CSV.

The importer supports UTF-8/UTF-8-BOM, UTF-16 and Windows-1252; normalizes known header aliases; validates required columns, rows and file size; and replaces the dataset transactionally. The dashboard reports upload errors instead of silently ignoring them.

Expected distribution in the supplied file:

| CAPTCHA label | Records |
|---|---:|
| reCAPTCHA | 103 |
| Invisible reCAPTCHA | 30 |
| Turnstile | 15 |
| hCaptcha | 6 |
| none | 46 |

CLI import:

```powershell
.\.venv\Scripts\python.exe scripts\import_csv.py
```

Or import another compatible CSV:

```powershell
.\.venv\Scripts\python.exe scripts\import_csv.py "C:\path\to\your.csv"
```

The CSV is metadata, not evidence that a live site has been scanned. No external URLs are automatically visited.

SQLite records total/success/failure/unverified/inconclusive attempts, challenge/provider, method, processing time, confidence when meaningful, and cost when known. Unknown remote-model cost is not falsely displayed as a measured `$0`. Raw provider tokens, secrets and form field values are not stored in attempt logs. Pytest uses a temporary database and artifact folder so tests do not pollute the app's local database.

## 5. Important API routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/health` | Runtime, Playwright and Chromium readiness |
| POST | `/api/v1/browser/inspect` | Playwright inspection of an allowlisted URL |
| POST | `/api/v1/browser/test-provider/{provider}` | Playwright-assisted local provider widget + Siteverify test |
| GET | `/demo/contact/{provider}` | Local provider test fixture |
| POST | `/api/v1/detect/html` | Provider recognition from supplied HTML |
| POST | `/api/v1/detect/synthetic-image` | Auto-classify a supported synthetic text/arithmetic/image-grid image |
| POST | `/api/v1/synthetic/{family}/generate` | Generate text, arithmetic or image-grid challenge |
| POST | `/api/v1/solve/{family}` | Solve selected synthetic family |
| POST | `/api/v1/providers/{provider}/verify` | Official provider Siteverify request |
| POST | `/api/v1/dataset/import` | Import supplied/custom CSV |
| GET | `/api/v1/dataset/summary` | Dataset distribution |
| GET | `/api/v1/dataset/rows` | Search and paginate imported metadata |
| POST | `/api/v1/dataset/evaluate-synthetic` | Run the balanced synthetic benchmark |
| GET | `/api/v1/metrics` | Attempt, latency and cost metrics |

## 6. Automated tests

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite covers API health/home, 200-row CSV seed/import/distribution, pagination, all three synthetic families, automatic synthetic-image family identification, exact-match/ground-truth behavior, provider marker recognition, allowlist handling, SQLite migrations, Gemini AQ key header behavior, provider timeout vs token rejection/configuration errors, and Playwright route configuration. Unit/API tests do not substitute for a live Windows Chromium run or live external provider widget test.

## 7. Clean submission packaging

Never send `.env`, `.venv`, local databases, logs, caches, screenshots or generated image files to the evaluator. Create a clean source archive with:

```powershell
.\package_submission.ps1
```

The script creates `CaptchaLab_AI_Submission.zip` in the parent directory and excludes local secrets, virtual environments, database files, logs, caches, screenshots and generated PNGs. It retains the synthetic `labels.csv` manifest and generator; the benchmark regenerates the deterministic image corpus when needed.

## 8. Suggested 4-minute demo

1. Show the CSV dashboard and verify the 200-row distribution.
2. Inspect the local Turnstile fixture in Module 01; show the fresh inspection result and screenshot. Tick **Show Chromium window** if you want the browser to be visible.
3. Generate and solve one sample from each of the three synthetic families; show each family name, prediction, exact-match result and latency.
4. Open an official provider widget, acquire the callback token, then verify it through the backend Siteverify API. Run the Playwright-assisted widget test and complete a visible checkbox interaction if required.
5. Show a missing/invalid token case, an inconclusive network/timeout case, and the attempt metrics.

Do not claim universal CAPTCHA solving, production CAPTCHA bypass, or accuracy outside the controlled synthetic benchmark.
