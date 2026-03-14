const state = {
  uiConfig: null,
  problems: [],
  history: [],
  leaderboard: { updated_at: "", models: [] },
  jobs: [],
  selectedRunId: "",
  pollTimer: null,
};

const dom = {
  providersGrid: document.getElementById("providersGrid"),
  refreshButton: document.getElementById("refreshButton"),
  saveConfigButton: document.getElementById("saveConfigButton"),
  runButton: document.getElementById("runButton"),
  temperatureInput: document.getElementById("temperatureInput"),
  maxTokensInput: document.getElementById("maxTokensInput"),
  generationTimeoutInput: document.getElementById("generationTimeoutInput"),
  executionModeSelect: document.getElementById("executionModeSelect"),
  executionTimeoutInput: document.getElementById("executionTimeoutInput"),
  dockerImageInput: document.getElementById("dockerImageInput"),
  dockerBinaryInput: document.getElementById("dockerBinaryInput"),
  problemPicker: document.getElementById("problemPicker"),
  customProblemForm: document.getElementById("customProblemForm"),
  jobsList: document.getElementById("jobsList"),
  historyList: document.getElementById("historyList"),
  resultDetail: document.getElementById("resultDetail"),
  leaderboardTable: document.getElementById("leaderboardTable"),
  leaderboardTimestamp: document.getElementById("leaderboardTimestamp"),
};

const customInputs = {
  id: document.getElementById("customIdInput"),
  task_type: document.getElementById("customTaskTypeSelect"),
  language: document.getElementById("customLanguageInput"),
  top_module: document.getElementById("customTopModuleInput"),
  module_header: document.getElementById("customModuleHeaderInput"),
  prompt: document.getElementById("customPromptInput"),
  testbench: document.getElementById("customTestbenchInput"),
  reference_rtl: document.getElementById("customReferenceRtlInput"),
  golden_rtl: document.getElementById("customGoldenRtlInput"),
  reference_tb: document.getElementById("customReferenceTbInput"),
  mutant_rtls_text: document.getElementById("customMutantsInput"),
};

function scopeValue() {
  const selected = document.querySelector('input[name="scope"]:checked');
  return selected ? selected.value : "suite";
}

function apiGet(path) {
  return fetch(path).then(checkResponse);
}

function apiPost(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(checkResponse);
}

function checkResponse(response) {
  if (!response.ok) {
    return response.json().catch(() => ({})).then((payload) => {
      throw new Error(payload.error || `${response.status} ${response.statusText}`);
    });
  }
  return response.json();
}

async function loadState(options = {}) {
  const preserveInputs = Boolean(options.preserveInputs);
  const payload = await apiGet("/api/state");
  state.uiConfig = payload.uiConfig;
  state.problems = payload.problems;
  state.history = payload.history;
  state.leaderboard = payload.leaderboard;
  state.jobs = payload.jobs;
  if (!preserveInputs) {
    syncFormFromState();
    render();
  } else {
    renderJobs();
    renderHistory();
    renderLeaderboard();
  }
  resetPolling();
}

function syncFormFromState() {
  const cfg = state.uiConfig;
  if (!cfg) {
    return;
  }
  dom.temperatureInput.value = cfg.generation.temperature;
  dom.maxTokensInput.value = cfg.generation.max_tokens;
  dom.generationTimeoutInput.value = cfg.generation.timeout_seconds;
  dom.executionModeSelect.value = cfg.execution.mode;
  dom.executionTimeoutInput.value = cfg.execution.timeout_seconds;
  dom.dockerImageInput.value = cfg.execution.docker_image;
  dom.dockerBinaryInput.value = cfg.execution.docker_binary;
}

function collectUiConfig() {
  return {
    providers: Array.from(dom.providersGrid.querySelectorAll(".provider-card")).map((card) => ({
      key: card.dataset.key,
      label: card.dataset.label,
      type: card.dataset.type,
      provider: card.dataset.provider,
      supports_base_url: card.dataset.supportsBaseUrl === "true",
      version: card.dataset.version || "",
      api_key_env: card.querySelector("[data-field='api_key_env']").value.trim(),
      api_key: card.querySelector("[data-field='api_key']").value.trim(),
      base_url: (card.querySelector("[data-field='base_url']") || { value: "" }).value.trim(),
      enabled: card.querySelector("[data-field='enabled']").checked,
      models: card
        .querySelector("[data-field='models']")
        .value.split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
    })),
    generation: {
      temperature: Number(dom.temperatureInput.value || 0),
      max_tokens: Number(dom.maxTokensInput.value || 1024),
      timeout_seconds: Number(dom.generationTimeoutInput.value || 60),
    },
    execution: {
      mode: dom.executionModeSelect.value,
      timeout_seconds: Number(dom.executionTimeoutInput.value || 30),
      docker_image: dom.dockerImageInput.value.trim(),
      docker_binary: dom.dockerBinaryInput.value.trim(),
    },
  };
}

function collectRunRequest() {
  const scope = scopeValue();
  return {
    scope,
    uiConfig: collectUiConfig(),
    problemIds:
      scope === "selected_problems"
        ? Array.from(dom.problemPicker.querySelectorAll("input[data-problem-id]:checked")).map((item) => item.dataset.problemId)
        : [],
    customProblem:
      scope === "custom_problem"
        ? Object.fromEntries(Object.entries(customInputs).map(([key, input]) => [key, input.value]))
        : {},
  };
}

function render() {
  renderProviders();
  renderProblems();
  renderJobs();
  renderHistory();
  renderLeaderboard();
  renderScope();
}

function renderProviders() {
  dom.providersGrid.innerHTML = state.uiConfig.providers
    .map((provider) => {
      const baseUrlField = provider.supports_base_url
        ? `
          <label>
            Base URL
            <input data-field="base_url" type="text" value="${escapeHtml(provider.base_url || "")}" />
          </label>
        `
        : "";
      return `
        <article
          class="provider-card"
          data-key="${escapeHtml(provider.key)}"
          data-label="${escapeHtml(provider.label)}"
          data-type="${escapeHtml(provider.type)}"
          data-provider="${escapeHtml(provider.provider)}"
          data-version="${escapeHtml(provider.version || "")}"
          data-supports-base-url="${provider.supports_base_url ? "true" : "false"}"
        >
          <div class="provider-top">
            <h3>${escapeHtml(provider.label)}</h3>
            <label class="scope-pill">
              <input data-field="enabled" type="checkbox" ${provider.enabled ? "checked" : ""} />
              Enabled
            </label>
          </div>
          <div class="provider-meta mono">${escapeHtml(provider.provider)}</div>
          <label>
            API Key Env
            <input data-field="api_key_env" type="text" value="${escapeHtml(provider.api_key_env || "")}" />
          </label>
          <label>
            API Key
            <input data-field="api_key" type="password" value="${escapeHtml(provider.api_key || "")}" placeholder="Stored locally in .state/webui_config.json" />
          </label>
          ${baseUrlField}
          <label>
            Models
            <textarea data-field="models" rows="6" placeholder="One model id per line">${escapeHtml(
              (provider.models || []).join("\n"),
            )}</textarea>
          </label>
        </article>
      `;
    })
    .join("");
}

function renderProblems() {
  dom.problemPicker.innerHTML = state.problems
    .map(
      (problem) => `
        <label class="problem-row">
          <input type="checkbox" data-problem-id="${escapeHtml(problem.id)}" />
          <div>
            <p class="problem-title">${escapeHtml(problem.id)}</p>
            <div class="problem-meta">${escapeHtml(problem.group)} · ${escapeHtml(problem.task_type)} · ${escapeHtml(
              problem.top_module,
            )}</div>
            <div class="stack-subtitle">${escapeHtml(problem.prompt)}</div>
          </div>
        </label>
      `,
    )
    .join("");
}

function renderScope() {
  const custom = scopeValue() === "custom_problem";
  dom.customProblemForm.classList.toggle("hidden", !custom);
  dom.problemPicker.classList.toggle("hidden", scopeValue() !== "selected_problems");
}

function renderJobs() {
  if (!state.jobs.length) {
    dom.jobsList.innerHTML = `<div class="stack-card empty-state">No live jobs yet.</div>`;
    return;
  }

  dom.jobsList.innerHTML = state.jobs
    .map((job) => {
      const progress = job.progress || {};
      const summary = job.result && job.result.summary ? `${job.result.summary.length} models summarised` : "pending";
      return `
        <article class="stack-card">
          <div class="stack-head">
            <strong class="mono">${escapeHtml(job.job_id)}</strong>
            <span class="status-chip status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
          </div>
          <div class="stack-subtitle">${escapeHtml(progress.message || "")}</div>
          <div class="stack-subtitle mono">${escapeHtml(progress.model_id || "")} ${escapeHtml(progress.problem_id || "")}</div>
          <div class="stack-subtitle">Submitted ${escapeHtml(job.submitted_at || "")} · ${escapeHtml(summary)}</div>
          <div class="stack-actions">
            ${job.result ? `<button class="mini-button" data-view-run="${escapeHtml(job.result.run_id)}">View Result</button>` : ""}
          </div>
        </article>
      `;
    })
    .join("");
}

function renderHistory() {
  if (!state.history.length) {
    dom.historyList.innerHTML = `<div class="stack-card empty-state">No recorded tests yet.</div>`;
    return;
  }

  dom.historyList.innerHTML = state.history
    .map((item) => {
      const summary = item.summary || [];
      const top = summary[0] ? `${summary[0].model_id} ${summary[0].score}` : "no summary";
      return `
        <article class="stack-card">
          <div class="stack-head">
            <strong class="mono">${escapeHtml(item.run_id)}</strong>
            <span class="status-chip status-completed">${escapeHtml(item.scope || "suite")}</span>
          </div>
          <div class="stack-subtitle">${escapeHtml(item.started_at || "")} · ${item.model_count} models · ${item.case_count} cases</div>
          <div class="stack-subtitle">Top row: ${escapeHtml(top)}</div>
          <div class="stack-actions">
            <button class="mini-button" data-view-run="${escapeHtml(item.run_id)}">Inspect</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderLeaderboard() {
  const leaderboard = state.leaderboard || { models: [] };
  dom.leaderboardTimestamp.textContent = leaderboard.updated_at
    ? `Updated ${leaderboard.updated_at}`
    : "No leaderboard updates yet.";

  const rows = leaderboard.models || [];
  if (!rows.length) {
    dom.leaderboardTable.innerHTML = `<div class="stack-card empty-state">Run a full suite benchmark to populate the local leaderboard.</div>`;
    return;
  }

  dom.leaderboardTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th>Provider</th>
          <th>Score</th>
          <th>Pass Rate</th>
          <th>Runs</th>
          <th>Last Run</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row) => `
              <tr>
                <td class="mono">${escapeHtml(row.model_id)}</td>
                <td>${escapeHtml(row.provider || "")}</td>
                <td>${formatMetric(row.score)}</td>
                <td>${formatMetric(row.pass_rate)}</td>
                <td>${row.runs || 0}</td>
                <td class="mono">${escapeHtml(row.last_run_id || "")}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

async function saveConfig() {
  const uiConfig = collectUiConfig();
  const payload = await apiPost("/api/config", uiConfig);
  state.uiConfig = payload.uiConfig;
  syncFormFromState();
  renderProviders();
}

async function runBenchmark() {
  const request = collectRunRequest();
  if (request.scope === "selected_problems" && !request.problemIds.length) {
    window.alert("Pick at least one problem.");
    return;
  }
  if (request.scope === "custom_problem" && !request.customProblem.prompt.trim()) {
    window.alert("Paste a custom prompt first.");
    return;
  }

  await apiPost("/api/run", request);
  await loadState();
}

async function viewRun(runId) {
  const payload = await apiGet(`/api/history/${encodeURIComponent(runId)}`);
  state.selectedRunId = runId;
  renderRunDetail(payload);
}

function renderRunDetail(payload) {
  const summary = (payload.summary || [])
    .map(
      (row) =>
        `<tr><td class="mono">${escapeHtml(row.model_id)}</td><td>${formatMetric(row.score)}</td><td>${formatMetric(
          row.pass_rate,
        )}</td><td>${row.cases || 0}</td></tr>`,
    )
    .join("");

  const cases = (payload.cases || [])
    .slice(0, 8)
    .map(
      (item) => `
        <article class="stack-card">
          <div class="stack-head">
            <strong class="mono">${escapeHtml(item.model_id)} / ${escapeHtml(item.problem_id)}</strong>
            <span class="status-chip status-${escapeHtml(item.passed ? "completed" : "failed")}">${item.passed ? "pass" : "fail"}</span>
          </div>
          <div class="stack-subtitle">${escapeHtml(item.feedback || "")}</div>
          <pre>${escapeHtml(item.candidate_code || "")}</pre>
        </article>
      `,
    )
    .join("");

  dom.resultDetail.innerHTML = `
    <div class="panel-head">
      <div>
        <p class="eyebrow">Run Detail</p>
        <h2>${escapeHtml(payload.run_id || state.selectedRunId)}</h2>
      </div>
      <div class="panel-note">${escapeHtml(payload.scope || "")} · ${escapeHtml(payload.started_at || "")}</div>
    </div>
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Score</th>
            <th>Pass Rate</th>
            <th>Cases</th>
          </tr>
        </thead>
        <tbody>${summary}</tbody>
      </table>
    </div>
    <div class="stack-list" style="margin-top: 16px;">${cases || `<div class="empty-state">No case details.</div>`}</div>
  `;
}

function resetPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  const active = state.jobs.some((job) => job.status === "running" || job.status === "queued");
  if (active) {
    state.pollTimer = window.setInterval(() => {
      loadState({ preserveInputs: true }).catch(showError);
    }, 2500);
  }
}

function bindEvents() {
  dom.refreshButton.addEventListener("click", () => loadState().catch(showError));
  dom.saveConfigButton.addEventListener("click", () => saveConfig().catch(showError));
  dom.runButton.addEventListener("click", () => runBenchmark().catch(showError));
  document.querySelectorAll('input[name="scope"]').forEach((radio) => {
    radio.addEventListener("change", renderScope);
  });
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const runId = target.dataset.viewRun;
    if (runId) {
      viewRun(runId).catch(showError);
    }
  });
}

function formatMetric(value) {
  return value === null || value === undefined ? "n/a" : Number(value).toFixed(2);
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showError(error) {
  console.error(error);
  window.alert(error.message || String(error));
}

bindEvents();
loadState().catch(showError);
