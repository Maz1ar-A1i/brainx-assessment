const siteKeys = JSON.parse(document.getElementById('site-keys').textContent || '{}');
const $ = (id) => document.getElementById(id);

let challengeBlob = null;
let challengeId = null;
let currentChallengeFamily = 'text';
let currentChallengePrompt = '';
let providerScripts = new Set();
let currentDatasetOffset = 0;
const DATASET_LIMIT = 15;
let currentDatasetSearch = '';

function busy(button, on, label = 'Working…') {
  if (on) {
    button.dataset.label = button.textContent;
    button.textContent = label;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
  }
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* -------------------------------------------------------------
 * 1. USER-FRIENDLY RESULT RENDERERS (NO RAW JSON DUMPS)
 * ------------------------------------------------------------- */

function createDetailsJson(data) {
  return `
    <details class="json-details">
      <summary>View Technical Details (JSON)</summary>
      <pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>
    </details>
  `;
}

function renderInspectResult(container, data, isError = false) {
  if (isError) {
    container.innerHTML = `
      <div class="result-card fail-border">
        <div class="card-header-row">
          <div class="card-header-left">
            <span class="badge badge-red">Inspection Failed</span>
            <span class="card-header-title">${escapeHtml(data.error_code || 'Error')}</span>
          </div>
        </div>
        <p style="margin:4px 0 10px;color:var(--red);font-weight:600;">
          ${escapeHtml(data.message || data.error || 'The requested URL could not be inspected.')}
        </p>
        ${createDetailsJson(data)}
      </div>
    `;
    return;
  }

  const detection = data.provider_detection || {};
  const inspectionStatus = data.inspection_status || 'completed';
  const isInconclusive = inspectionStatus.startsWith('inconclusive');
  const isDetected = detection.detected === true;
  const provider = detection.provider || (isInconclusive ? 'unknown' : 'none');
  const isUnsupported = ['unknown', 'multiple'].includes(provider) || detection.supported === false;
  const badgeClass = isInconclusive ? 'badge-amber' : (isDetected ? (isUnsupported ? 'badge-amber' : 'badge-green') : 'badge-blue');
  const badgeText = isInconclusive
    ? 'INSPECTION INCONCLUSIVE'
    : (isDetected ? `${provider.toUpperCase()} DETECTED` : 'NO SUPPORTED CAPTCHA MARKER');

  const formsCount = data.form_count ?? (data.forms ? data.forms.length : 0);
  const formsSummary = formsCount > 0
    ? (data.forms || []).map(f => `${f.method || 'GET'} ${f.action || '/'} (${f.field_count || 0} fields)`).join(', ')
    : 'No forms found on page';
  const inspectionNote = isInconclusive
    ? 'The browser did not complete a reliable inspection. This result must not be interpreted as a CAPTCHA-free page.'
    : (inspectionStatus === 'completed_with_warnings'
      ? 'The page was inspected, but one or more resources reported warnings. Review the technical details.'
      : 'Inspection completed. No form was submitted and no response token was collected.');

  container.innerHTML = `
    <div class="result-card ${isInconclusive ? 'warn-border' : (isDetected ? 'success-border' : '')}">
      <div class="card-header-row">
        <div class="card-header-left">
          <span class="badge ${badgeClass}">${escapeHtml(badgeText)}</span>
          <span class="card-header-title">${escapeHtml(data.page_title || 'Rendered Page')}</span>
        </div>
        <span class="card-latency">HTTP ${data.http_status || 200} · ${data.processing_time_ms || 0} ms</span>
      </div>

      <div class="kv-grid">
        <div class="kv-item">
          <span>Target Provider</span>
          <strong>${escapeHtml(provider.toUpperCase())}</strong>
        </div>
        <div class="kv-item">
          <span>Challenge Variant</span>
          <strong>${escapeHtml(detection.challenge_type || 'standard')}</strong>
        </div>
        <div class="kv-item">
          <span>Compliance & Safety</span>
          <strong style="color:var(--green);">✓ No form submission · No token collection</strong>
        </div>
        <div class="kv-item">
          <span>Detected Forms</span>
          <strong>${escapeHtml(formsSummary)}</strong>
        </div>
      </div>
      <p class="muted" style="margin:8px 0;">${escapeHtml(inspectionNote)}</p>
      ${data.warning_codes && data.warning_codes.length ? `<p class="muted" style="margin:4px 0;">Warnings: ${escapeHtml(data.warning_codes.join(', '))}</p>` : ''}

      ${detection.frame_urls && detection.frame_urls.length ? `
        <div style="margin:10px 0;font-size:12px;">
          <span style="color:var(--muted);font-weight:600;">Provider Scripts / Frames:</span>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;">
            ${detection.frame_urls.map(u => `<span class="tag">${escapeHtml(u)}</span>`).join('')}
          </div>
        </div>
      ` : ''}

      ${createDetailsJson(data)}
    </div>
  `;
}

function renderSolveResult(container, data, isError = false) {
  if (isError) {
    container.innerHTML = `
      <div class="result-card fail-border">
        <div class="card-header-row">
          <div class="card-header-left">
            <span class="badge badge-red">Solver Failed</span>
            <span class="card-header-title">${escapeHtml(data.error_code || 'Error')}</span>
          </div>
        </div>
        <p style="color:var(--red);margin:4px 0 10px;">${escapeHtml(data.message || data.error || 'Failed to solve challenge.')}</p>
        ${createDetailsJson(data)}
      </div>
    `;
    return;
  }

  const exactMatch = data.exact_match;
  const isUnverified = data.verification_status === 'unverified_ground_truth_missing';

  let badgeClass = 'badge-red';
  let badgeText = 'MISMATCH';
  let borderClass = 'fail-border';

  if (isUnverified) {
    badgeClass = 'badge-amber';
    badgeText = 'PREDICTED (UNVERIFIED UPLOAD)';
    borderClass = 'warn-border';
  } else if (exactMatch === true) {
    badgeClass = 'badge-green';
    badgeText = 'EXACT MATCH (100%)';
    borderClass = 'success-border';
  }

  const confidences = data.character_confidences || [];
  const textChars = (data.text || '').split('');
  const isOpenCv = String(data.method || '').startsWith('opencv');
  const confidenceLabel = Number.isFinite(data.confidence) ? `${Math.round(data.confidence * 100)}%` : 'Not calibrated';
  const confidenceTitle = data.confidence === null || data.confidence === undefined
    ? 'Confidence'
    : (isOpenCv ? 'Template similarity' : 'Confidence');
  const resultLabel = data.result_label || 'Predicted result';
  const costLabel = data.cost_usd === null || data.cost_usd === undefined
    ? (data.cost_note || 'Not returned by the provider')
    : (Number(data.cost_usd) === 0 && isOpenCv
      ? '$0.00 (Local CPU)'
      : (data.billing_tier === 'free' && Number(data.cost_usd) === 0
        ? '$0.00 (Gemini Free Tier)'
        : `$${Number(data.cost_usd).toFixed(6)}${data.cost_is_estimate ? ' estimated' : ''}`));

  const charMetersHtml = textChars.map((char, i) => {
    const conf = confidences[i] !== undefined ? Math.round(confidences[i] * 100) : 0;
    return `
      <div class="char-meter-box">
        <div class="char-meter-char">${escapeHtml(char)}</div>
        <div class="char-meter-pct">${conf}%</div>
        <div class="char-meter-bar-track">
          <div class="char-meter-bar-fill" style="width:${conf}%;"></div>
        </div>
      </div>
    `;
  }).join('');

  container.innerHTML = `
    <div class="result-card ${borderClass}">
      <div class="card-header-row">
        <div class="card-header-left">
          <span class="badge ${badgeClass}">${badgeText}</span>
          <span class="card-header-title">${escapeHtml(data.method || 'CAPTCHA Solver Result')}</span>
        </div>
        <span class="card-latency">${data.processing_time_ms || 0} ms · ${escapeHtml(costLabel)}</span>
      </div>

      <div class="solved-hero-box">
        <div>
          <div class="solved-label">${escapeHtml(resultLabel)}</div>
          <div class="solved-text-display">${escapeHtml(data.text || '')}</div>
        </div>
        <div style="text-align:right;">
          <div class="solved-label">${escapeHtml(confidenceTitle)}</div>
          <div style="font-size:22px;font-weight:800;color:var(--navy);">${escapeHtml(confidenceLabel)}</div>
        </div>
      </div>

      ${charMetersHtml ? `
        <div style="margin-bottom:6px;font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;">Character Confidence Breakdown:</div>
        <div class="char-meter-grid">${charMetersHtml}</div>
      ` : ''}
      ${data.cost_note ? `<p class="muted" style="font-size:11px;margin:4px 0 10px;">Cost note: ${escapeHtml(data.cost_note)}</p>` : ''}

      <div class="kv-grid">
        <div class="kv-item">
          <span>Challenge Family</span>
          <strong>${escapeHtml(data.challenge_type || 'synthetic')}</strong>
        </div>
        <div class="kv-item">
          <span>Engine</span>
          <strong>${escapeHtml(data.method || 'opencv_cpu')}</strong>
        </div>
        <div class="kv-item">
          <span>Status</span>
          <strong>${escapeHtml(data.verification_status || 'completed')}</strong>
        </div>
        ${data.expression ? `<div class="kv-item"><span>Recognized Expression</span><strong>${escapeHtml(data.expression)}</strong></div>` : ''}
        ${data.prompt ? `<div class="kv-item"><span>Task Prompt</span><strong>${escapeHtml(data.prompt)}</strong></div>` : ''}
        ${data.fallback_triggered ? `
          <div class="kv-item">
            <span>Fallback Engine</span>
            <strong style="color:var(--amber);">Vision LLM (OpenCV initial: ${Number.isFinite(data.opencv_confidence) ? Math.round(data.opencv_confidence * 100) + '%' : 'not calibrated'})</strong>
          </div>
        ` : ''}
        ${data.ground_truth_available && data.expected ? `
          <div class="kv-item">
            <span>Ground Truth</span>
            <strong style="color:${data.exact_match ? 'var(--green)' : 'var(--red)'};">${escapeHtml(data.expected)}</strong>
          </div>
        ` : ''}
      </div>

      ${data.llm_error ? `
        <div style="margin:10px 0;padding:10px 12px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;color:#991b1b;font-size:12px;">
          <strong>Vision LLM Note:</strong> Gemini fallback could not complete: <em>${escapeHtml(data.llm_error)}</em>.<br>
          <small style="color:#64748b;">Check that the Google AI Studio key is active, the Generative Language API is enabled, the configured model is available to your project, and your quota permits the request. Both standard and AQ-prefixed authorization keys use the x-goog-api-key request header.</small>
        </div>
      ` : ''}

      ${createDetailsJson(data)}
    </div>
  `;
}

function renderVerifyResult(container, data, isError = false) {
  if (isError) {
    container.innerHTML = `
      <div class="result-card fail-border">
        <div class="card-header-row">
          <div class="card-header-left">
            <span class="badge badge-red">Verification Error</span>
            <span class="card-header-title">${escapeHtml(data.error_code || 'Error')}</span>
          </div>
        </div>
        <p style="color:var(--red);margin:4px 0 10px;">${escapeHtml(data.message || data.error || 'Verification request failed.')}</p>
        ${createDetailsJson(data)}
      </div>
    `;
    return;
  }

  const isSuccess = data.success === true;
  const state = String(data.verification_state || '').toLowerCase();
  const isIncomplete = [
    'inconclusive', 'error', 'verification_error',
    'widget_page_navigation_timeout', 'widget_page_http_error', 'widget_load_timeout',
    'widget_load_error', 'awaiting_widget_interaction', 'siteverify_timeout',
    'widget_page_navigation_failed', 'widget_page_navigation_timeout'
  ].includes(state) || String(data.error_code || '').includes('timeout') ||
    ['provider_unavailable', 'provider_not_configured', 'invalid_provider_response', 'siteverify_network_error'].includes(String(data.error_code || ''));
  const badgeClass = isSuccess ? 'badge-green' : (isIncomplete ? 'badge-amber' : 'badge-red');
  const badgeText = isSuccess ? 'VERIFIED BY OFFICIAL API' : (isIncomplete ? 'TEST INCOMPLETE · NOT A PROVIDER REJECTION' : 'VERIFICATION REJECTED');
  const borderClass = isSuccess ? 'success-border' : (isIncomplete ? 'warn-border' : 'fail-border');

  const friendlyReason = isSuccess
    ? 'The provider accepted the response token through its official server-side verification API.'
    : (isIncomplete
      ? (data.message || 'Verification could not be completed. This is not evidence that the provider rejected the token.')
      : (data.message || (
      data.error_code === 'missing_token' ? 'No response token provided. Complete the challenge widget first.' :
      data.error_code === 'token_expired' ? 'Test token has expired. Obtain a fresh token.' :
      data.error_code === 'token_reused' ? 'Token was already verified once (replay protection).' :
      data.error_code === 'token_expired_or_reused' ? 'Token is either expired or already consumed.' :
      data.error_code === 'invalid_token' ? 'Token rejected by provider Siteverify.' :
      `Error code: ${data.error_code || 'unspecified'}`
    )));

  container.innerHTML = `
    <div class="result-card ${borderClass}">
      <div class="card-header-row">
        <div class="card-header-left">
          <span class="badge ${badgeClass}">${badgeText}</span>
          <span class="card-header-title">${escapeHtml((data.provider || '').toUpperCase())}</span>
        </div>
        <span class="card-latency">${data.processing_time_ms || 0} ms · $0.00 (Free Test Keys)</span>
      </div>

      <p style="margin:4px 0 12px;font-size:13px;color:${isSuccess ? 'var(--green)' : (isIncomplete ? 'var(--amber)' : 'var(--red)')};font-weight:600;">
        ${escapeHtml(friendlyReason)}
      </p>

      <div class="kv-grid">
        <div class="kv-item">
          <span>Hostname</span>
          <strong>${escapeHtml(data.hostname || 'Not returned by provider')}</strong>
        </div>
        <div class="kv-item">
          <span>Timestamp</span>
          <strong>${escapeHtml(data.challenge_ts || 'N/A')}</strong>
        </div>
      </div>

      ${createDetailsJson(data)}
    </div>
  `;
}

function renderBatchBenchmarkResult(container, data) {
  const exactAcc = Number(data.exact_match_accuracy_pct || 0);
  const familyRows = Object.entries(data.family_metrics || {}).map(([family, stats]) => `
    <div class="kv-item">
      <span>${escapeHtml(family.replace(/^synthetic_/, '').replace(/_/g, ' ').toUpperCase())}</span>
      <strong>${stats.exact}/${stats.total} (${stats.accuracy_pct ?? 'N/A'}%) · avg ${stats.average_latency_ms ?? 'N/A'} ms · p95 ${stats.p95_latency_ms ?? 'N/A'} ms</strong>
    </div>
  `).join('');

  container.innerHTML = `
    <div class="result-card success-border">
      <div class="card-header-row">
        <div class="card-header-left">
          <span class="badge badge-green">Three-Family Benchmark Complete</span>
          <span class="card-header-title">${Number(data.total_samples || 0)} evaluated / ${Number(data.corpus_samples || 0)} corpus samples</span>
        </div>
        <span class="card-latency">Warmup: ${Number(data.warmup_ms || 0)} ms · Cost: $0.00</span>
      </div>
      <div class="metrics-grid" style="margin:10px 0 16px;">
        <article class="metric-card"><span>Exact Match</span><strong class="green">${exactAcc}%</strong></article>
        <article class="metric-card"><span>p50 Latency</span><strong>${Number(data.p50_latency_ms || 0)} ms</strong></article>
        <article class="metric-card"><span>p95 Latency</span><strong>${Number(data.p95_latency_ms || 0)} ms</strong></article>
        <article class="metric-card"><span>Average Latency</span><strong>${Number(data.average_latency_ms || 0)} ms</strong></article>
        <article class="metric-card"><span>Text Character Accuracy</span><strong>${data.text_character_accuracy_pct === null ? 'N/A' : `${data.text_character_accuracy_pct}%`}</strong></article>
      </div>
      ${familyRows ? `<div style="margin-bottom:6px;font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;">Per-family results:</div><div class="kv-grid">${familyRows}</div>` : ''}
      <p class="muted">${escapeHtml(data.scope || 'Controlled synthetic challenge benchmark.')}</p>
      ${createDetailsJson(data)}
    </div>
  `;
}

/* -------------------------------------------------------------
 * 2. DATASET TABLE & PAGINATION
 * ------------------------------------------------------------- */

function safeHttpUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ''), window.location.origin);
    if (!['http:', 'https:'].includes(parsed.protocol)) return null;
    return parsed.href;
  } catch (_) {
    return null;
  }
}

async function loadDatasetRows() {
  const tbody = $('dataset-tbody');
  try {
    const query = new URLSearchParams({
      limit: String(DATASET_LIMIT),
      offset: String(currentDatasetOffset),
      q: currentDatasetSearch
    });
    const res = await fetch(`/api/v1/dataset/rows?${query.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail || data;
      throw new Error(detail.message || detail.error_code || `HTTP ${res.status}`);
    }

    const rows = Array.isArray(data.rows) ? data.rows : [];
    const total = Number.isInteger(data.total_records) ? data.total_records : rows.length;

    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:18px;color:var(--muted);">No matching platforms found.</td></tr>`;
      $('pagination-info').textContent = `0 of ${total} records`;
      $('prev-page-btn').disabled = true;
      $('next-page-btn').disabled = true;
      return;
    }

    tbody.innerHTML = rows.map(r => {
      const contactUrl = safeHttpUrl(r.contact_page);
      const contactCell = contactUrl
        ? `<a href="${escapeHtml(contactUrl)}" target="_blank" rel="noopener noreferrer">Contact page ↗</a>`
        : `<span class="muted">Invalid or missing URL</span>`;
      const captchaLabel = String(r.captcha_label || 'Unknown');
      const captchaIsNone = captchaLabel.trim().toLowerCase() === 'none';
      return `
        <tr>
          <td><strong>${escapeHtml(r.company)}</strong><br><small style="color:var(--muted);">${escapeHtml(r.website)}</small></td>
          <td><span class="tag">${escapeHtml(r.platform || 'Custom')}</span></td>
          <td>${escapeHtml(r.category || 'General')}</td>
          <td><span class="badge ${captchaIsNone ? 'badge-amber' : 'badge-blue'}">${escapeHtml(captchaLabel)}</span></td>
          <td>${contactCell}</td>
        </tr>`;
    }).join('');

    const start = currentDatasetOffset + 1;
    const end = currentDatasetOffset + rows.length;
    $('pagination-info').textContent = `Showing ${start}-${end} of ${total} records (filter: "${currentDatasetSearch || 'all'}")`;
    $('prev-page-btn').disabled = currentDatasetOffset === 0;
    $('next-page-btn').disabled = !data.has_more;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--red);">Failed to load dataset rows: ${escapeHtml(e.message)}</td></tr>`;
    $('pagination-info').textContent = 'Dataset unavailable';
    $('prev-page-btn').disabled = true;
    $('next-page-btn').disabled = true;
  }
}

function renderDatasetSummary(dataset) {
  const target = $('dataset-summary-cards');
  if (!target) return;
  const labels = Array.isArray(dataset.captcha_distribution) ? dataset.captcha_distribution : [];
  const countFor = (predicate) => labels
    .filter(item => predicate(String(item.label || '').trim().toLowerCase()))
    .reduce((sum, item) => sum + Number(item.count || 0), 0);
  const recaptcha = countFor(label => label === 'recaptcha' || label === 'recaptcha v2' || label === 'recaptcha v3');
  const invisible = countFor(label => label.includes('invisible'));
  const turnstile = countFor(label => label.includes('turnstile'));
  const hcaptcha = countFor(label => label.includes('hcaptcha'));
  const none = countFor(label => label === 'none' || label === 'no captcha');
  const unknown = Math.max(0, Number(dataset.total_records || 0) - recaptcha - invisible - turnstile - hcaptcha - none);
  const cards = [
    ['Total records', Number(dataset.total_records || 0)],
    ['reCAPTCHA', recaptcha],
    ['Invisible reCAPTCHA', invisible],
    ['Turnstile', turnstile],
    ['hCaptcha', hcaptcha],
    ['None', none]
  ];
  if (unknown) cards.push(['Other / unknown', unknown]);
  target.innerHTML = cards.map(([label, count]) =>
    `<div class="summary-pill"><span>${escapeHtml(label)}:</span> <strong>${Number(count)}</strong></div>`
  ).join('');
}

/* -------------------------------------------------------------
 * 3. METRICS & REFRESH
 * ------------------------------------------------------------- */

async function refresh() {
  try {
    const responses = await Promise.all([
      fetch('/api/v1/metrics'),
      fetch('/health'),
      fetch('/api/v1/dataset/summary')
    ]);
    for (const response of responses) {
      if (!response.ok) throw new Error(`Dashboard request failed: HTTP ${response.status}`);
    }
    const [metrics, health, dataset] = await Promise.all(responses.map(response => response.json()));

    $('metric-total').textContent = metrics.total_attempts ?? 0;
    $('metric-success').textContent = metrics.successful_attempts ?? 0;
    $('metric-failed').textContent = metrics.failed_attempts ?? 0;
    $('metric-unverified').textContent = metrics.unverified_attempts ?? 0;
    if ($('metric-inconclusive')) $('metric-inconclusive').textContent = metrics.inconclusive_attempts ?? 0;
    $('metric-latency').textContent = `${metrics.average_latency_ms ?? 0} ms`;
    if ($('metric-cost')) $('metric-cost').textContent = `$${Number(metrics.estimated_cost_usd ?? 0).toFixed(6)}`;
    if ($('metric-cost-note')) $('metric-cost-note').textContent = `Unknown cost attempts: ${Number(metrics.cost_unknown_attempts ?? 0)}`;
    $('health-status').textContent = health.status === 'ok' ? 'Healthy' : 'Unavailable';
    $('playwright-status').textContent = health.playwright_ready
      ? 'Ready (Chromium installed)'
      : (health.playwright_installed ? 'Installed · Chromium missing' : 'Missing');

    renderDatasetSummary(dataset);
  } catch (e) {
    $('health-status').textContent = 'Unavailable';
    $('playwright-status').textContent = 'Status unavailable';
    console.error('Dashboard refresh failed:', e);
  }
}

/* -------------------------------------------------------------
 * 4. EVENT LISTENERS & WORKFLOWS
 * ------------------------------------------------------------- */

// URL Preset chips
document.querySelectorAll('.chip-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $('target-url').value = btn.dataset.url;
  });
});

// Module 01: Inspect URL
$('inspect-button').addEventListener('click', async () => {
  const button = $('inspect-button');
  busy(button, true, $('show-browser').checked ? 'Opening visible Chromium…' : 'Running headless Chromium…');
  $('scan-screenshot').innerHTML = '';
  const resultContainer = $('inspect-result');

  try {
    const response = await fetch('/api/v1/browser/inspect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: $('target-url').value.trim(),
        show_browser: $('show-browser').checked
      })
    });
    const data = await response.json();
    renderInspectResult(resultContainer, response.ok ? data : (data.detail || data), !response.ok);

    if (response.ok && data.screenshot_url) {
      const img = document.createElement('img');
      img.src = data.screenshot_url;
      img.alt = 'Playwright page screenshot';
      $('scan-screenshot').appendChild(img);
    }
    await refresh();
  } catch (e) {
    renderInspectResult(resultContainer, { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 02: Challenge-family selector and generator
function syncSolverEngineOptions() {
  const family = $('challenge-family').value;
  const gemini = $('engine-select').querySelector('option[value="gemini"]');
  if (gemini) {
    gemini.disabled = family !== 'text';
    if (family !== 'text' && $('engine-select').value === 'gemini') $('engine-select').value = 'opencv';
  }
  $('challenge-prompt').placeholder = family === 'image-grid'
    ? 'For uploaded images: Select all blue circles.'
    : (family === 'arithmetic' ? 'Generated arithmetic prompt is supplied automatically.' : 'Transcribe the characters shown.');
}

$('challenge-family').addEventListener('change', () => {
  currentChallengeFamily = $('challenge-family').value;
  challengeBlob = null;
  challengeId = null;
  currentChallengePrompt = '';
  $('challenge-prompt').value = '';
  $('captcha-upload').value = '';
  $('solve-button').disabled = true;
  $('challenge-preview').textContent = 'Generate or upload a challenge from the selected family.';
  $('solve-result').textContent = 'Choose Generate Challenge to create a server-tracked sample.';
  syncSolverEngineOptions();
});
$('challenge-family').addEventListener('change', syncSolverEngineOptions);
$('engine-select').addEventListener('change', syncSolverEngineOptions);
syncSolverEngineOptions();

$('generate-button').addEventListener('click', async () => {
  const button = $('generate-button');
  currentChallengeFamily = $('challenge-family').value;
  busy(button, true, 'Generating…');
  try {
    const response = await fetch(`/api/v1/synthetic/${encodeURIComponent(currentChallengeFamily)}/generate`, { method: 'POST' });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = body.detail || body;
      throw new Error(detail.message || detail.error_code || `HTTP ${response.status}`);
    }
    challengeBlob = await response.blob();
    challengeId = response.headers.get('X-Challenge-ID');
    currentChallengePrompt = response.headers.get('X-Challenge-Prompt') || '';
    $('challenge-prompt').value = currentChallengePrompt;
    const objectUrl = URL.createObjectURL(challengeBlob);
    $('challenge-preview').innerHTML = '';
    const img = document.createElement('img');
    img.src = objectUrl;
    img.alt = `${currentChallengeFamily} synthetic CAPTCHA challenge`;
    $('challenge-preview').appendChild(img);
    $('solve-button').disabled = false;
    $('solve-result').innerHTML = `<div class="result-card"><p style="margin:0;color:var(--navy);font-weight:600;">${escapeHtml(currentChallengePrompt || 'Challenge generated.')} Click <strong>Run Family Solver</strong> to process it locally.</p><small class="muted">Challenge family: ${escapeHtml(currentChallengeFamily)} · Ground truth remains server-side.</small></div>`;
  } catch (e) {
    renderSolveResult($('solve-result'), { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 02: Solve generated challenge
$('solve-button').addEventListener('click', async () => {
  if (!challengeBlob || !challengeId) return;
  const button = $('solve-button');
  busy(button, true, 'Running family solver…');
  try {
    const form = new FormData();
    form.append('file', new File([challengeBlob], 'challenge.png', { type: 'image/png' }));
    form.append('challenge_id', challengeId);
    form.append('engine', $('engine-select').value);
    form.append('prompt', $('challenge-prompt').value || currentChallengePrompt);
    const response = await fetch(`/api/v1/solve/${encodeURIComponent(currentChallengeFamily)}`, { method: 'POST', body: form });
    const data = await response.json();
    renderSolveResult($('solve-result'), response.ok ? data : (data.detail || data), !response.ok);
    await refresh();
  } catch (e) {
    renderSolveResult($('solve-result'), { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 02: File Input Change (Instant Preview of Uploaded Challenge)
$('captcha-upload').addEventListener('change', () => {
  const file = $('captcha-upload').files[0];
  if (file) {
    const objectUrl = URL.createObjectURL(file);
    $('challenge-preview').innerHTML = '';
    const img = document.createElement('img');
    img.src = objectUrl;
    img.alt = `Uploaded image: ${file.name}`;
    $('challenge-preview').appendChild(img);
    $('solve-result').innerHTML = `
      <div class="result-card">
        <p style="margin:0;color:var(--navy);font-weight:600;">
          Selected file: <strong>${escapeHtml(file.name)}</strong>. Click <strong>Solve Upload</strong> to run the CV solver.
        </p>
      </div>
    `;
    challengeBlob = null;
    challengeId = null;
    $('solve-button').disabled = true;
  }
});

// Module 02: Automatically identify the supported synthetic family of an uploaded image.
$('detect-upload-button').addEventListener('click', async () => {
  const file = $('captcha-upload').files[0];
  if (!file) {
    $('solve-result').innerHTML = `<div class="result-card fail-border"><p style="margin:0;color:var(--red);">Choose an image first.</p></div>`;
    return;
  }

  const button = $('detect-upload-button');
  busy(button, true, 'Identifying…');
  try {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch('/api/v1/detect/synthetic-image', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail || data;
      throw new Error(detail.message || detail.error_code || `HTTP ${response.status}`);
    }

    if (!data.detected) {
      $('solve-result').innerHTML = `
        <div class="result-card warn-border">
          <span class="badge badge-amber">Unsupported or ambiguous challenge</span>
          <p>${escapeHtml(data.error_code || data.challenge_type || 'The image did not match a supported synthetic family.')}</p>
          <p class="muted">This classifier supports only the documented synthetic text, arithmetic and 3×3 shape-grid generators. It does not infer arbitrary production CAPTCHA formats.</p>
          ${createDetailsJson(data)}
        </div>`;
      await refresh();
      return;
    }

    const familyMap = {
      synthetic_text: 'text',
      synthetic_arithmetic: 'arithmetic',
      synthetic_image_grid: 'image-grid'
    };
    const selectedFamily = familyMap[data.challenge_type];
    if (!selectedFamily) throw new Error('The detector returned an unknown challenge family.');
    $('challenge-family').value = selectedFamily;
    currentChallengeFamily = selectedFamily;
    challengeBlob = null;
    challengeId = null;
    currentChallengePrompt = '';
    syncSolverEngineOptions();
    if (selectedFamily === 'image-grid') {
      $('challenge-prompt').value = '';
      $('challenge-prompt').placeholder = 'Required: e.g. Select all blue circles.';
    } else {
      $('challenge-prompt').value = selectedFamily === 'arithmetic'
        ? 'Calculate the expression shown in the image.'
        : 'Transcribe the five characters shown in the image.';
    }
    $('solve-result').innerHTML = `
      <div class="result-card success-border">
        <span class="badge badge-green">Challenge family identified</span>
        <div class="kv-grid">
          <div class="kv-item"><span>Detected type</span><strong>${escapeHtml(data.challenge_type)}</strong></div>
          <div class="kv-item"><span>Detection method</span><strong>${escapeHtml(data.detection_method)}</strong></div>
          <div class="kv-item"><span>Template similarity</span><strong>${data.confidence === null ? 'Not applicable' : `${(Number(data.confidence) * 100).toFixed(1)}% (not a probability)`}</strong></div>
          <div class="kv-item"><span>Latency</span><strong>${Number(data.processing_time_ms || 0).toFixed(2)} ms</strong></div>
        </div>
        <p class="muted">Family selected: ${escapeHtml(selectedFamily)}. ${selectedFamily === 'image-grid' ? 'Enter the image-grid instruction, then click Solve Upload.' : 'Click Solve Upload to process the image.'}</p>
        ${createDetailsJson(data)}
      </div>`;
    await refresh();
  } catch (e) {
    renderSolveResult($('solve-result'), { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 02: Upload & Solve
$('upload-solve-button').addEventListener('click', async () => {
  const file = $('captcha-upload').files[0];
  if (!file) {
    $('solve-result').innerHTML = `<div class="result-card fail-border"><p style="margin:0;color:var(--red);">Please choose an image file first.</p></div>`;
    return;
  }

  // Ensure preview is displaying the uploaded image
  const objectUrl = URL.createObjectURL(file);
  $('challenge-preview').innerHTML = '';
  const img = document.createElement('img');
  img.src = objectUrl;
  img.alt = `Uploaded image: ${file.name}`;
  $('challenge-preview').appendChild(img);

  const button = $('upload-solve-button');
  busy(button, true, 'Processing upload…');
  try {
    const form = new FormData();
    form.append('file', file);
    form.append('engine', $('engine-select') ? $('engine-select').value : 'auto');
    form.append('prompt', $('challenge-prompt').value || currentChallengePrompt);
    const response = await fetch(`/api/v1/solve/${encodeURIComponent($('challenge-family').value)}`, { method: 'POST', body: form });
    const data = await response.json();
    renderSolveResult($('solve-result'), response.ok ? data : (data.detail || data), !response.ok);
    await refresh();
  } catch (e) {
    renderSolveResult($('solve-result'), { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 03: Provider Widget Loader
function waitFor(check, count = 80) {
  return new Promise((resolve, reject) => {
    let i = 0;
    const timer = setInterval(() => {
      if (check()) {
        clearInterval(timer);
        resolve();
      } else if (++i >= count) {
        clearInterval(timer);
        reject(new Error('Provider script initialization timed out'));
      }
    }, 150);
  });
}

async function loadProvider(provider) {
  const srcs = {
    recaptcha: 'https://www.google.com/recaptcha/api.js?render=explicit',
    hcaptcha: 'https://js.hcaptcha.com/1/api.js?render=explicit',
    turnstile: 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
  };
  const ready = {
    recaptcha: () => window.grecaptcha && window.grecaptcha.render,
    hcaptcha: () => window.hcaptcha && window.hcaptcha.render,
    turnstile: () => window.turnstile && window.turnstile.render
  };

  if (!ready[provider]()) {
    await new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = srcs[provider];
      script.async = true;
      script.defer = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Failed to load ${provider} script`));
      document.head.appendChild(script);
    });
    await waitFor(ready[provider]);
  }
}

$('load-widget-button').addEventListener('click', async () => {
  const provider = $('provider-select').value;
  const button = $('load-widget-button');
  const widgetBox = $('provider-widget');
  busy(button, true, 'Loading widget…');
  widgetBox.innerHTML = '';
  $('provider-token').value = '';

  try {
    await loadProvider(provider);
    const siteKey = siteKeys[provider];
    if (!siteKey) throw new Error(`Site key for ${provider} not configured`);

    const containerId = `widget-${provider}-${Date.now()}`;
    const holder = document.createElement('div');
    holder.id = containerId;
    widgetBox.appendChild(holder);

    const callback = (token) => {
      // A widget callback means only that the provider issued a response token.
      // It is NOT a successful verification until the backend calls siteverify.
      $('provider-token').value = token;
      $('verify-result').innerHTML = `
        <div class="result-card warn-border">
          <span class="badge badge-amber">TOKEN ACQUIRED · NOT YET VERIFIED</span>
          <p class="muted">${escapeHtml(provider)} issued a response token. Click <strong>Verify Token</strong> to send it to this application's backend for official server-side verification.</p>
        </div>`;
    };
    const expired = () => {
      $('provider-token').value = '';
      $('verify-result').innerHTML = `<div class="result-card warn-border"><span class="badge badge-amber">TOKEN EXPIRED</span><p class="muted">Complete the provider widget again to obtain a fresh token.</p></div>`;
    };
    const errored = () => {
      $('provider-token').value = '';
      $('verify-result').innerHTML = `<div class="result-card fail-border"><span class="badge badge-red">WIDGET ERROR</span><p class="muted">The provider widget reported an error. Reload the widget and retry.</p></div>`;
    };

    if (provider === 'recaptcha') {
      window.grecaptcha.render(containerId, { sitekey: siteKey, callback, 'expired-callback': expired, 'error-callback': errored });
    } else if (provider === 'hcaptcha') {
      window.hcaptcha.render(containerId, { sitekey: siteKey, callback, 'expired-callback': expired, 'error-callback': errored });
    } else if (provider === 'turnstile') {
      window.turnstile.render(`#${containerId}`, { sitekey: siteKey, callback, 'expired-callback': expired, 'error-callback': errored });
    }
  } catch (e) {
    widgetBox.innerHTML = `<span style="color:var(--red);">Failed to render widget: ${escapeHtml(e.message)}</span>`;
  } finally {
    busy(button, false);
  }
});

// Module 03: Verify Token
$('verify-button').addEventListener('click', async () => {
  const provider = $('provider-select').value;
  const token = $('provider-token').value.trim();
  const button = $('verify-button');
  busy(button, true, 'Verifying…');
  try {
    const response = await fetch(`/api/v1/providers/${provider}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    });
    const data = await response.json();
    renderVerifyResult($('verify-result'), response.ok ? data : (data.detail || data), !response.ok);
    await refresh();
  } catch (e) {
    renderVerifyResult($('verify-result'), { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 03: Playwright Provider Test
$('playwright-provider-test').addEventListener('click', async () => {
  const provider = $('provider-select').value;
  const button = $('playwright-provider-test');
  busy(button, true, 'Opening Chromium test window…');
  $('playwright-result').innerHTML = '<div class="result-card">A separate Chromium window will open. This is a Playwright-assisted test: complete the official test widget manually if it asks for a click. Playwright observes the callback state and the backend Siteverify result. No form is submitted.</div>';
  try {
    const response = await fetch(`/api/v1/browser/test-provider/${provider}`, { method: 'POST' });
    const data = await response.json();
    renderVerifyResult($('playwright-result'), response.ok ? data : (data.detail || data), !response.ok);
    await refresh();
  } catch (e) {
    renderVerifyResult($('playwright-result'), { error: e.message }, true);
  } finally {
    busy(button, false);
  }
});

// Module 04: Tabs
$('tab-btn-dataset').addEventListener('click', () => {
  $('tab-btn-dataset').classList.add('active');
  $('tab-btn-batch').classList.remove('active');
  $('tab-content-dataset').classList.add('active');
  $('tab-content-batch').classList.remove('active');
});

$('tab-btn-batch').addEventListener('click', () => {
  $('tab-btn-batch').classList.add('active');
  $('tab-btn-dataset').classList.remove('active');
  $('tab-content-batch').classList.add('active');
  $('tab-content-dataset').classList.remove('active');
});

// Module 04: Search & Pagination
$('dataset-search').addEventListener('input', (e) => {
  currentDatasetSearch = e.target.value;
  currentDatasetOffset = 0;
  loadDatasetRows();
});

$('prev-page-btn').addEventListener('click', () => {
  if (currentDatasetOffset >= DATASET_LIMIT) {
    currentDatasetOffset -= DATASET_LIMIT;
    loadDatasetRows();
  }
});

$('next-page-btn').addEventListener('click', () => {
  currentDatasetOffset += DATASET_LIMIT;
  loadDatasetRows();
});

$('custom-csv-btn').addEventListener('click', () => {
  $('csv-file').click();
});

$('csv-file').addEventListener('change', async () => {
  const fileInput = $('csv-file');
  const file = fileInput.files && fileInput.files[0];
  const resultBox = $('dataset-result');
  if (!file) return;
  resultBox.style.display = 'block';
  resultBox.innerHTML = `<div class="result-card">Importing ${escapeHtml(file.name)}…</div>`;
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/v1/dataset/import', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success !== true) {
      const detail = data.detail || data;
      const message = detail.message || detail.error_code || data.message || `HTTP ${res.status}`;
      throw new Error(message);
    }
    currentDatasetOffset = 0;
    currentDatasetSearch = '';
    $('dataset-search').value = '';
    resultBox.innerHTML = `
      <div class="result-card success-border">
        <span class="badge badge-green">CSV IMPORTED</span>
        <strong>${Number(data.imported_records || 0)} records imported successfully.</strong>
        <p class="muted">The dataset now contains ${Number(data.total_records || 0)} records. CSV records are metadata; they are not automatically crawled or submitted.</p>
      </div>`;
    await loadDatasetRows();
    await refresh();
  } catch (e) {
    resultBox.innerHTML = `
      <div class="result-card fail-border">
        <span class="badge badge-red">CSV IMPORT FAILED</span>
        <p style="color:var(--red);margin:6px 0;">${escapeHtml(e.message)}</p>
        <p class="muted">Check that the file is a CSV and includes Company, Website, Platform, Captcha and Contact Page columns.</p>
      </div>`;
  } finally {
    // Reset so choosing the same CSV again triggers another change event.
    fileInput.value = '';
  }
});

// Module 04: Batch Benchmark Button
$('run-batch-eval-btn').addEventListener('click', async () => {
  const button = $('run-batch-eval-btn');
  const resultBox = $('batch-eval-result');
  busy(button, true, 'Running 300-sample benchmark…');
  try {
    const response = await fetch('/api/v1/dataset/evaluate-synthetic?samples=300', { method: 'POST' });
    const data = await response.json();
    renderBatchBenchmarkResult(resultBox, data);
  } catch (e) {
    resultBox.innerHTML = `<div class="result-card fail-border"><p style="color:var(--red);">Batch evaluation failed: ${escapeHtml(e.message)}</p></div>`;
  } finally {
    busy(button, false);
  }
});

// Refresh button
$('refresh-metrics').addEventListener('click', refresh);

// Initial Load
document.addEventListener('DOMContentLoaded', () => {
  refresh();
  loadDatasetRows();
});
