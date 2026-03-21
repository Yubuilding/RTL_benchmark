const page = document.body.dataset.page || "home";

const state = {
  uiConfig: null,
  problems: [],
  problemStats: { total: 0, sources: 0, categories: 0 },
  history: [],
  leaderboard: { updated_at: "", models: [] },
  jobs: [],
  selectedRunId: "",
  selectedRun: null,
  selectedCaseKey: "",
  compareModelA: "",
  compareModelB: "",
  leaderboardCompare: null,
  selectedProblemIds: new Set(),
  selectedRunModelKeys: new Set(),
  expandedModels: new Set(),
  expandedLeaderboardBreakdowns: new Set(),
  artifactViewer: null,
  problemSearch: "",
  pollTimer: null,
};

function captureScrollSnapshot() {
  const containers = {};
  document.querySelectorAll("[data-scroll-key]").forEach((element) => {
    const key = String(element.dataset.scrollKey || "").trim();
    if (!key) {
      return;
    }
    containers[key] = {
      top: element.scrollTop,
      left: element.scrollLeft,
    };
  });
  return {
    windowX: window.scrollX,
    windowY: window.scrollY,
    containers,
  };
}

function restoreScrollSnapshot(snapshot) {
  if (!snapshot) {
    return;
  }
  document.querySelectorAll("[data-scroll-key]").forEach((element) => {
    const key = String(element.dataset.scrollKey || "").trim();
    const saved = snapshot.containers ? snapshot.containers[key] : null;
    if (!saved) {
      return;
    }
    element.scrollTop = Number(saved.top || 0);
    element.scrollLeft = Number(saved.left || 0);
  });
  window.scrollTo(Number(snapshot.windowX || 0), Number(snapshot.windowY || 0));
}

function preserveScroll(renderFn) {
  const snapshot = captureScrollSnapshot();
  renderFn();
  restoreScrollSnapshot(snapshot);
}

function byId(id) {
  return document.getElementById(id);
}

const dom = {
  providersGrid: byId("providersGrid"),
  refreshButton: byId("refreshButton"),
  saveConfigButton: byId("saveConfigButton"),
  runButton: byId("runButton"),
  resetLeaderboardButton: byId("resetLeaderboardButton"),
  temperatureInput: byId("temperatureInput"),
  maxTokensInput: byId("maxTokensInput"),
  generationTimeoutInput: byId("generationTimeoutInput"),
  executionModeSelect: byId("executionModeSelect"),
  executionTimeoutInput: byId("executionTimeoutInput"),
  dockerImageInput: byId("dockerImageInput"),
  dockerBinaryInput: byId("dockerBinaryInput"),
  runModelPicker: byId("runModelPicker"),
  problemPicker: byId("problemPicker"),
  problemSearchInput: byId("problemSearchInput"),
  problemStats: byId("problemStats"),
  customProblemForm: byId("customProblemForm"),
  jobsList: byId("jobsList"),
  historyList: byId("historyList"),
  resultDetail: byId("resultDetail"),
  leaderboardTable: byId("leaderboardTable"),
  leaderboardTimestamp: byId("leaderboardTimestamp"),
  resultsHistoryList: byId("resultsHistoryList"),
  resultsOverview: byId("resultsOverview"),
  resultsRunMeta: byId("resultsRunMeta"),
  resultsModelSummary: byId("resultsModelSummary"),
  resultsConsole: byId("resultsConsole"),
  resultsLeaderboardTable: byId("resultsLeaderboardTable"),
  resultsLeaderboardTimestamp: byId("resultsLeaderboardTimestamp"),
  leaderboardHistoryList: byId("leaderboardHistoryList"),
  leaderboardPageTable: byId("leaderboardPageTable"),
  leaderboardPageTimestamp: byId("leaderboardPageTimestamp"),
  leaderboardCompareRunMeta: byId("leaderboardCompareRunMeta"),
  leaderboardCompareControls: byId("leaderboardCompareControls"),
  leaderboardCompareSummary: byId("leaderboardCompareSummary"),
  leaderboardCompareTable: byId("leaderboardCompareTable"),
};

const customInputs = {
  id: byId("customIdInput"),
  task_type: byId("customTaskTypeSelect"),
  language: byId("customLanguageInput"),
  top_module: byId("customTopModuleInput"),
  module_header: byId("customModuleHeaderInput"),
  prompt: byId("customPromptInput"),
  testbench: byId("customTestbenchInput"),
  reference_rtl: byId("customReferenceRtlInput"),
  golden_rtl: byId("customGoldenRtlInput"),
  reference_tb: byId("customReferenceTbInput"),
  mutant_rtls_text: byId("customMutantsInput"),
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

function apiDelete(path) {
  return fetch(path, { method: "DELETE" }).then(checkResponse);
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
  const previousSelectedRunId = state.selectedRunId;
  const payload = await apiGet("/api/state");
  state.uiConfig = payload.uiConfig;
  state.problems = payload.problems || [];
  state.problemStats = payload.problemStats || { total: 0, sources: 0, categories: 0 };
  state.history = payload.history || [];
  state.leaderboard = payload.leaderboard || { updated_at: "", models: [] };
  state.jobs = payload.jobs || [];
  pruneSelectedProblems();

  syncRunModelSelection({ reset: !preserveInputs });

  if (!preserveInputs) {
    syncFormFromState();
  }
  render();

  if (page === "results" || page === "leaderboard") {
    const desiredRunId = state.selectedRunId || currentRunFromUrl() || (state.history[0] && state.history[0].run_id) || "";
    if (desiredRunId) {
      await viewRun(desiredRunId, {
        pushHistory: false,
        reveal: options.revealSelectedRun === true || (!previousSelectedRunId || previousSelectedRunId !== desiredRunId),
      });
    }
  }
  resetPolling();
}

function syncFormFromState() {
  const cfg = state.uiConfig;
  if (!cfg) {
    return;
  }
  if (dom.temperatureInput) dom.temperatureInput.value = cfg.generation.temperature;
  if (dom.maxTokensInput) dom.maxTokensInput.value = cfg.generation.max_tokens;
  if (dom.generationTimeoutInput) dom.generationTimeoutInput.value = cfg.generation.timeout_seconds;
  if (dom.executionModeSelect) dom.executionModeSelect.value = cfg.execution.mode;
  if (dom.executionTimeoutInput) dom.executionTimeoutInput.value = cfg.execution.timeout_seconds;
  if (dom.dockerImageInput) dom.dockerImageInput.value = cfg.execution.docker_image;
  if (dom.dockerBinaryInput) dom.dockerBinaryInput.value = cfg.execution.docker_binary;
}

function collectUiConfig() {
  const existingConfig = state.uiConfig || { providers: [], generation: {}, execution: {} };
  const providerCards = Array.from((dom.providersGrid || document.createElement("div")).querySelectorAll(".provider-card"));
  const providers =
    providerCards.length > 0
      ? providerCards.map((card) => ({
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
        }))
      : existingConfig.providers || [];
  return {
    providers,
    generation: {
      temperature: dom.temperatureInput ? Number(dom.temperatureInput.value || 0) : Number(existingConfig.generation.temperature || 0),
      max_tokens: dom.maxTokensInput
        ? Number(dom.maxTokensInput.value || 1024)
        : Number(existingConfig.generation.max_tokens || 1024),
      timeout_seconds: dom.generationTimeoutInput
        ? Number(dom.generationTimeoutInput.value || 60)
        : Number(existingConfig.generation.timeout_seconds || 60),
    },
    execution: {
      mode: dom.executionModeSelect ? dom.executionModeSelect.value : String(existingConfig.execution.mode || "docker"),
      timeout_seconds: dom.executionTimeoutInput
        ? Number(dom.executionTimeoutInput.value || 30)
        : Number(existingConfig.execution.timeout_seconds || 30),
      docker_image: dom.dockerImageInput ? dom.dockerImageInput.value.trim() : String(existingConfig.execution.docker_image || ""),
      docker_binary: dom.dockerBinaryInput ? dom.dockerBinaryInput.value.trim() : String(existingConfig.execution.docker_binary || ""),
    },
  };
}

function collectRunRequest() {
  const scope = scopeValue();
  const selectedModels = availableRunModels()
    .filter((item) => state.selectedRunModelKeys.has(item.key))
    .map((item) => ({ provider: item.provider, model_id: item.modelId }));
  return {
    scope,
    uiConfig: collectUiConfig(),
    selectedModels,
    problemIds: scope === "selected_problems" ? Array.from(state.selectedProblemIds) : [],
    customProblem:
      scope === "custom_problem"
        ? Object.fromEntries(Object.entries(customInputs).map(([key, input]) => [key, input ? input.value : ""]))
        : {},
  };
}

function pruneSelectedProblems() {
  const validIds = new Set(state.problems.map((problem) => problem.id));
  state.selectedProblemIds = new Set(Array.from(state.selectedProblemIds).filter((problemId) => validIds.has(problemId)));
}

function availableRunModels() {
  const providers = (((state.uiConfig || {}).providers) || []).filter((provider) => provider.enabled);
  return providers.flatMap((provider) =>
    (provider.models || [])
      .map((modelId) => String(modelId || "").trim())
      .filter(Boolean)
      .map((modelId) => ({
        key: runModelKey(provider.provider, modelId),
        provider: provider.provider,
        providerLabel: provider.label || provider.provider,
        modelId,
      })),
  );
}

function runModelKey(provider, modelId) {
  return `${String(provider || "").trim()}::${String(modelId || "").trim()}`;
}

function syncRunModelSelection(options = {}) {
  const reset = Boolean(options.reset);
  const models = availableRunModels();
  const validKeys = new Set(models.map((item) => item.key));
  if (reset || !state.selectedRunModelKeys.size) {
    state.selectedRunModelKeys = new Set(models.map((item) => item.key));
    return;
  }
  state.selectedRunModelKeys = new Set(Array.from(state.selectedRunModelKeys).filter((key) => validKeys.has(key)));
  if (!state.selectedRunModelKeys.size && models.length) {
    state.selectedRunModelKeys = new Set(models.map((item) => item.key));
  }
}

function render() {
  preserveScroll(() => renderView());
}

function renderView() {
  renderProviders();
  renderRunModelPicker();
  renderProblems();
  renderScope();
  renderJobs();
  renderHistory();
  renderLeaderboard();
  if (page === "home") {
    renderHomeRunDetail();
  } else if (page === "results") {
    renderResultsPage();
  } else if (page === "leaderboard") {
    renderLeaderboardPage();
  }
}

function renderProviders() {
  if (!dom.providersGrid || !state.uiConfig) {
    return;
  }
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

function renderRunModelPicker() {
  if (!dom.runModelPicker) {
    return;
  }
  const models = availableRunModels();
  if (!models.length) {
    dom.runModelPicker.innerHTML = `
      <div class="empty-state">
        No enabled models are available for runs. Configure models on the <a class="inline-link" href="/config">Config</a> page first.
      </div>
    `;
    return;
  }

  const grouped = groupBy(models, (item) => item.providerLabel);
  const selectedCount = Array.from(state.selectedRunModelKeys).filter((key) => models.some((item) => item.key === key)).length;

  dom.runModelPicker.innerHTML = `
    <div class="run-model-toolbar">
      <div class="problem-stats">
        <span>Selected <strong>${selectedCount}</strong> / ${models.length}</span>
        <span>${Object.keys(grouped).length} providers</span>
        <span>Jobs with different model selections can run in parallel.</span>
      </div>
      <div class="stack-actions">
        <button class="mini-button" data-select-all-run-models="true">Select All</button>
        <button class="mini-button" data-clear-all-run-models="true">Clear All</button>
      </div>
    </div>
    <div class="run-model-groups">
      ${Object.entries(grouped)
        .map(
          ([providerLabel, items]) => `
            <section class="run-model-group">
              <div class="source-head">
                <div>
                  <p class="problem-title">${escapeHtml(providerLabel)}</p>
                  <div class="problem-meta">${items.length} models</div>
                </div>
                <div class="stack-actions">
                  <button class="mini-button" data-select-run-provider="${escapeHtml(items[0].provider)}">Select Provider</button>
                  <button class="mini-button" data-clear-run-provider="${escapeHtml(items[0].provider)}">Clear Provider</button>
                </div>
              </div>
              <div class="token-list run-model-token-list">
                ${items
                  .map(
                    (item) => `
                      <label class="run-model-chip ${state.selectedRunModelKeys.has(item.key) ? "run-model-chip-active" : ""}">
                        <input
                          type="checkbox"
                          data-run-model-key="${escapeHtml(item.key)}"
                          ${state.selectedRunModelKeys.has(item.key) ? "checked" : ""}
                        />
                        <span class="mono">${escapeHtml(item.modelId)}</span>
                      </label>
                    `,
                  )
                  .join("")}
              </div>
            </section>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderProblems() {
  if (!dom.problemPicker) {
    return;
  }
  const query = state.problemSearch.trim().toLowerCase();
  const filtered = state.problems.filter((problem) => {
    const haystack = [
      problem.id,
      problem.prompt,
      problem.source,
      problem.category,
      problem.suite,
      problem.track,
      problem.difficulty,
      problem.harness_type,
      problem.task_type,
      problem.top_module,
      ...(problem.tags || []),
      ...(problem.evaluation_targets || []),
    ]
      .join(" ")
      .toLowerCase();
    return !query || haystack.includes(query);
  });
  const grouped = groupProblems(filtered);

  if (dom.problemStats) {
    dom.problemStats.innerHTML = `
      <span>Selected <strong>${state.selectedProblemIds.size}</strong> / ${state.problemStats.total || state.problems.length}</span>
      <span>${state.problemStats.sources || grouped.length} sources</span>
      <span>${state.problemStats.categories || countCategories(grouped)} categories</span>
      <span>${filtered.length} visible</span>
    `;
  }

  if (!filtered.length) {
    dom.problemPicker.innerHTML = `<div class="empty-state">No problems matched the current filter.</div>`;
    return;
  }

  dom.problemPicker.innerHTML = grouped
    .map(
      (sourceGroup) => `
        <section class="problem-source">
          <div class="source-head">
            <div>
              <p class="problem-title">${escapeHtml(sourceGroup.source)}</p>
              <div class="problem-meta">${sourceGroup.total} problems · ${sourceGroup.categories.length} categories</div>
            </div>
            <div class="stack-actions">
              <button class="mini-button" data-select-source="${escapeHtml(sourceGroup.source)}">Select Source</button>
              <button class="mini-button" data-clear-source="${escapeHtml(sourceGroup.source)}">Clear Source</button>
            </div>
          </div>
          <div class="source-categories">
            ${sourceGroup.categories
              .map(
                (categoryGroup) => `
                  <section class="problem-category">
                    <div class="category-head">
                      <strong>${escapeHtml(categoryGroup.category)}</strong>
                      <span class="problem-meta">${categoryGroup.items.length} items</span>
                    </div>
                    <div class="problem-list">
                      ${categoryGroup.items
                        .map(
                          (problem) => `
                            <label class="problem-row">
                              <input
                                type="checkbox"
                                data-problem-id="${escapeHtml(problem.id)}"
                                ${state.selectedProblemIds.has(problem.id) ? "checked" : ""}
                              />
                              <div class="problem-copy">
                                <div class="problem-line">
                                  <p class="problem-title">${escapeHtml(problem.id)}</p>
                                  <span class="problem-badge">${escapeHtml(problem.task_type)}</span>
                                </div>
                                <div class="problem-meta">
                                  ${escapeHtml(problem.top_module || "n/a")} · ${escapeHtml(problem.language || "n/a")} ·
                                  ${escapeHtml(problem.suite || "n/a")} · ${escapeHtml(problem.difficulty || "n/a")}
                                </div>
                                <div class="problem-meta">
                                  ${escapeHtml(problem.track || "n/a")} · ${escapeHtml(problem.harness_type || "n/a")}
                                </div>
                                <div class="stack-subtitle">${escapeHtml(problem.prompt)}</div>
                                <div class="problem-foot mono">${escapeHtml(problem.path || "")}</div>
                              </div>
                            </label>
                          `,
                        )
                        .join("")}
                    </div>
                  </section>
                `,
              )
              .join("")}
          </div>
        </section>
      `,
    )
    .join("");
}

function renderScope() {
  const custom = scopeValue() === "custom_problem";
  if (dom.customProblemForm) {
    dom.customProblemForm.classList.toggle("hidden", !custom);
  }
  if (dom.problemPicker) {
    dom.problemPicker.classList.toggle("hidden", scopeValue() !== "selected_problems");
  }
}

function renderJobs() {
  if (!dom.jobsList) {
    return;
  }
  if (!state.jobs.length) {
    dom.jobsList.innerHTML = `<div class="stack-card empty-state">No live jobs yet.</div>`;
    return;
  }

  dom.jobsList.innerHTML = state.jobs
    .map((job) => {
      const progress = job.progress || {};
      const hasResult = Boolean(job.result && job.result.run_id);
      const progressText = renderJobProgressText(progress);
      const etaText = renderJobEtaText(progress);
      const summary =
        job.status === "failed"
          ? hasResult
            ? `partial result · ${(job.result.summary || []).length} models summarised`
            : "failed before result was saved"
          : job.status === "paused"
            ? hasResult
              ? `paused · ${(job.result.summary || []).length} models summarised`
              : "paused before result was saved"
          : hasResult
            ? `${job.result.summary.length} models summarised`
            : "pending";
      const errorSummary = summarizeError(job.error || "");
      return `
        <article class="stack-card">
          <div class="stack-head">
            <strong class="mono">${escapeHtml(job.job_id)}</strong>
            <span class="status-chip status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
          </div>
          <div class="stack-subtitle">${escapeHtml(progress.message || "")}</div>
          <div class="stack-subtitle mono">${escapeHtml(progress.model_id || "")} ${escapeHtml(progress.problem_id || "")}</div>
          ${renderJobProgressBar(progress)}
          <div class="stack-subtitle">${escapeHtml(progressText)}</div>
          ${etaText ? `<div class="stack-subtitle">${escapeHtml(etaText)}</div>` : ""}
          <div class="stack-subtitle">Submitted ${escapeHtml(job.submitted_at || "")} · ${escapeHtml(summary)}</div>
          ${
            job.status === "failed" && errorSummary
              ? `
                <div class="job-error-summary">${escapeHtml(errorSummary)}</div>
                <details class="job-error-details">
                  <summary>Show failure details</summary>
                  <pre>${escapeHtml(job.error || "")}</pre>
                </details>
              `
              : ""
          }
          ${renderJobFailedCases(job)}
          <div class="stack-actions">
            ${
              hasResult
                ? `<button class="mini-button" data-view-run="${escapeHtml(job.result.run_id)}">Preview Result</button>
                   <a class="mini-link" href="/results?run=${encodeURIComponent(job.result.run_id)}">Open Results Page</a>`
                : ""
            }
            ${
              job.status === "failed" || job.status === "paused"
                ? `<button class="mini-button" data-resume-job="${escapeHtml(job.job_id)}">Continue Run</button>`
                : ""
            }
            ${
              job.status === "running" || job.status === "queued"
                ? `<button class="mini-button" data-pause-job="${escapeHtml(job.job_id)}">Pause</button>`
                : ""
            }
            ${
              job.status !== "running" && job.status !== "queued"
                ? `<button class="mini-button" data-delete-job="${escapeHtml(job.job_id)}">Delete Job</button>`
                : ""
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function renderHistory() {
  const container =
    page === "results" ? dom.resultsHistoryList : page === "leaderboard" ? dom.leaderboardHistoryList : dom.historyList;
  if (!container) {
    return;
  }
  if (!state.history.length) {
    container.innerHTML = `<div class="stack-card empty-state">No recorded tests yet.</div>`;
    return;
  }

  container.innerHTML = state.history
    .map((item) => {
      const compact = page !== "home";
      const summary = item.summary || [];
      const top = summary[0] ? `${summary[0].model_id} ${formatRate(summary[0].score)}` : "no summary";
      const active = state.selectedRunId === item.run_id ? "history-card-active" : "";
      const runStatus = item.status || "completed";
      const meta = compact
        ? `${escapeHtml(item.started_at || "")} · ${item.model_count} models · ${item.case_count} cases`
        : `${escapeHtml(item.started_at || "")} · ${item.model_count} models · ${item.case_count} cases`;
      return `
        <article class="stack-card ${active} ${compact ? "history-compact-card" : ""}">
          <div class="stack-head">
            <strong class="mono">${escapeHtml(item.run_id)}</strong>
            <span class="status-chip status-${escapeHtml(statusToChip(runStatus))}">${escapeHtml(runStatus)}</span>
          </div>
          <div class="stack-subtitle">${meta} · ${escapeHtml(item.scope || "suite")}</div>
          <div class="stack-subtitle">${compact ? `Best: ${escapeHtml(top)}` : `Top row: ${escapeHtml(top)}`}</div>
          ${
            runStatus === "failed" && item.error
              ? `<div class="job-error-summary">${escapeHtml(summarizeError(item.error))}</div>`
              : ""
          }
          <div class="stack-actions">
            <button class="mini-button" data-view-run="${escapeHtml(item.run_id)}">${compact ? "Open" : "Preview"}</button>
            ${
              page === "home"
                ? `<a class="mini-link" href="/results?run=${encodeURIComponent(item.run_id)}">Results Page</a>
                   <a class="mini-link" href="/leaderboard?run=${encodeURIComponent(item.run_id)}">Compare Page</a>`
                : ""
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function renderLeaderboard() {
  const table =
    page === "results" ? dom.resultsLeaderboardTable : page === "leaderboard" ? dom.leaderboardPageTable : dom.leaderboardTable;
  const timestamp =
    page === "results"
      ? dom.resultsLeaderboardTimestamp
      : page === "leaderboard"
        ? dom.leaderboardPageTimestamp
        : dom.leaderboardTimestamp;
  if (!table || !timestamp) {
    return;
  }
  const leaderboard = state.leaderboard || { models: [] };
  const parts = [];
  if (leaderboard.updated_at) {
    parts.push(`Updated ${leaderboard.updated_at}`);
  } else {
    parts.push("No leaderboard updates yet.");
  }
  if (leaderboard.reset_at) {
    parts.push(`Reset baseline ${leaderboard.reset_at}`);
  }
  timestamp.textContent = parts.join(" · ");

  const rows = leaderboard.models || [];
  if (!rows.length) {
    table.innerHTML = `<div class="stack-card empty-state">Run benchmark problems to populate the leaderboard.</div>`;
    return;
  }

  const mainTable = `
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th>Provider</th>
          <th>Weighted Score</th>
          <th>Raw Pass</th>
          <th>Quality</th>
          <th>Cases</th>
          <th>Profile</th>
          <th>Runs</th>
          <th>Last Run</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row) => `
              <tr>
                <td>
                  <div class="mono">${escapeHtml(row.model_id)}</div>
                  ${renderHighlightTokens(row.top_tags || [])}
                </td>
                <td>${escapeHtml(row.provider || "")}</td>
                <td>${formatRate(row.score)}</td>
                <td>${formatRate(row.pass_rate)}</td>
                <td>${formatRate(row.quality_score)}</td>
                <td>${row.cases || 0}</td>
                <td>
                  <div class="leaderboard-profile-summary">${escapeHtml(row.profile_summary || "n/a")}</div>
                  <details class="leaderboard-breakdown" data-leaderboard-breakdown-key="${escapeHtml(
                    leaderboardBreakdownKey(row),
                  )}" ${state.expandedLeaderboardBreakdowns.has(leaderboardBreakdownKey(row)) ? "open" : ""}>
                    <summary>Open Slice Breakdown</summary>
                    ${renderLeaderboardBreakdown(row)}
                  </details>
                </td>
                <td>${row.runs || 0}</td>
                <td class="mono">${escapeHtml(row.last_run_id || "")}</td>
                <td>
                  ${
                    Number(row.failed_cases || 0) > 0
                      ? `<button class="mini-button" data-leaderboard-rerun-model="${escapeHtml(row.model_id)}" data-leaderboard-rerun-provider="${escapeHtml(
                          row.provider || "",
                        )}">Rerun ${row.failed_cases} Failed Cases</button>`
                      : ""
                  }
                  ${
                    row.last_run_id
                      ? `${Number(row.failed_cases || 0) > 0 ? " " : ""}<a class="mini-link" href="/leaderboard?run=${encodeURIComponent(row.last_run_id)}">Compare</a>`
                      : ""
                  }
                </td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
  const sliceSections = renderSliceRankingSections(leaderboard.slice_rankings || {});
  table.innerHTML = mainTable + sliceSections;
}

function leaderboardBreakdownKey(row) {
  return `${String(row.provider || "").trim()}::${String(row.model_id || "").trim()}`;
}

function renderHomeRunDetail() {
  if (!dom.resultDetail) {
    return;
  }
  if (!state.selectedRun) {
    dom.resultDetail.innerHTML = `Select a history row or wait for a live run to complete.`;
    dom.resultDetail.classList.add("empty-state");
    return;
  }
  dom.resultDetail.classList.remove("empty-state");
  const payload = state.selectedRun;
  const overview = payload.overview || {};
  const cases = (payload.cases || []).slice(0, 6);
  dom.resultDetail.innerHTML = `
    <div class="panel-head">
      <div>
        <p class="eyebrow">Run Detail</p>
        <h2>${escapeHtml(payload.run_id || state.selectedRunId)}</h2>
      </div>
      <div class="stack-actions">
        <a class="mini-link" href="/results?run=${encodeURIComponent(payload.run_id || state.selectedRunId)}">Open Results Page</a>
      </div>
    </div>
    ${renderOverviewCards(overview)}
    <div class="table-shell" style="margin-top: 16px;">
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Problem</th>
            <th>Result</th>
            <th>Feedback</th>
          </tr>
        </thead>
        <tbody>
          ${cases
            .map(
              (item) => `
                <tr>
                  <td class="mono">${escapeHtml(item.model_id)}</td>
                  <td class="mono">${escapeHtml(item.problem_id)}</td>
                  <td>${renderInlineStatus(item.passed ? "pass" : "fail")}</td>
                  <td>${escapeHtml(item.feedback || "")}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderResultsPage() {
  if (!dom.resultsOverview || !dom.resultsModelSummary || !dom.resultsConsole || !dom.resultsRunMeta) {
    return;
  }
  if (!state.selectedRun) {
    dom.resultsOverview.innerHTML = `<div class="empty-state">Select a recorded run to inspect model-by-model outcomes.</div>`;
    dom.resultsRunMeta.textContent = "";
    dom.resultsModelSummary.innerHTML = `<div class="empty-state">No run selected.</div>`;
    dom.resultsConsole.innerHTML = `Select a run and a case to inspect prompts, outputs, harness code, logs, and waveforms.`;
    dom.resultsConsole.classList.add("empty-state");
    return;
  }
  dom.resultsConsole.classList.remove("empty-state");
  const payload = state.selectedRun;
  dom.resultsOverview.innerHTML = renderResultsOverview(payload);
  dom.resultsRunMeta.textContent = `${payload.scope || "run"} · ${payload.started_at || ""} → ${payload.finished_at || ""}`;
  renderResultsModelSummary();
  renderCaseConsole();
}

function renderLeaderboardPage() {
  if (!dom.leaderboardCompareControls || !dom.leaderboardCompareSummary || !dom.leaderboardCompareTable || !dom.leaderboardCompareRunMeta) {
    return;
  }
  const modelRows = (state.leaderboard && state.leaderboard.models) || [];
  const compare = state.leaderboardCompare;

  if (!modelRows.length) {
    dom.leaderboardCompareRunMeta.textContent = "";
    dom.leaderboardCompareControls.innerHTML = `<div class="empty-state">Run benchmark problems to compare models on the leaderboard.</div>`;
    dom.leaderboardCompareSummary.innerHTML = "";
    dom.leaderboardCompareTable.innerHTML = "";
    return;
  }

  dom.leaderboardCompareRunMeta.textContent = renderLeaderboardCompareMeta(compare);
  dom.leaderboardCompareControls.innerHTML = renderLeaderboardCompareControls(modelRows);

  if (!state.compareModelA || !state.compareModelB) {
    dom.leaderboardCompareSummary.innerHTML = `<div class="empty-state">Pick two different models from the leaderboard.</div>`;
    dom.leaderboardCompareTable.innerHTML = "";
    return;
  }

  if (!compare) {
    dom.leaderboardCompareSummary.innerHTML = `<div class="empty-state">Loading compare view for the selected models.</div>`;
    dom.leaderboardCompareTable.innerHTML = "";
    return;
  }

  dom.leaderboardCompareSummary.innerHTML = renderLeaderboardCompareSummary(compare);
  dom.leaderboardCompareTable.innerHTML = renderLeaderboardCompareGroups(compare);
}

function renderResultsModelSummary() {
  if (!dom.resultsModelSummary || !state.selectedRun) {
    return;
  }
  const models = state.selectedRun.model_results || [];
  if (!models.length) {
    dom.resultsModelSummary.innerHTML = `<div class="empty-state">No case rows found for this run.</div>`;
    return;
  }

  dom.resultsModelSummary.innerHTML = `
    <div class="results-sidebar-head">
      <div>
        <p class="eyebrow">Navigator</p>
        <h2>Models And Cases</h2>
      </div>
      <div class="problem-meta">${models.length} models</div>
    </div>
    <div class="results-model-list" data-scroll-key="results-model-list">
      ${models
        .map((model) => {
          const expanded = state.expandedModels.has(model.model_id);
          return `
            <article class="model-nav-card ${expanded ? "model-nav-card-active" : ""}">
              <div class="model-nav-head">
                <div class="model-nav-copy">
                  <strong class="mono">${escapeHtml(model.model_id)}</strong>
                  <div class="problem-meta">${escapeHtml(model.provider || "")}</div>
                </div>
                <button class="mini-button" data-toggle-model="${escapeHtml(model.model_id)}">${expanded ? "Hide" : "Detail"}</button>
              </div>
              <div class="model-nav-metrics">
                <span>${model.passed} pass</span>
                <span>${model.failed} fail</span>
                <span>${formatRate(model.pass_rate)}</span>
                <span>${model.cases} cases</span>
                <span>W ${formatRate(model.weighted_pass_score)}</span>
                <span>Q ${formatRate(model.quality_score)}</span>
              </div>
              <div class="stack-subtitle">${escapeHtml(model.profile_summary || "")}</div>
              ${expanded ? renderModelCasesTable(model) : ""}
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderModelCasesTable(model) {
  return `
    <div class="case-nav-scroll" data-scroll-key="results-cases-${escapeHtml(model.model_id)}">
      <div class="case-nav-list">
      ${model.items
        .map((item) => {
          const key = caseKey(item);
          const selected = state.selectedCaseKey === key;
          return `
            <article class="case-nav-entry">
              <button
                class="case-nav-button ${selected ? "case-nav-button-active" : ""}"
                data-select-case="${escapeHtml(key)}"
                data-model-id="${escapeHtml(model.model_id)}"
              >
                <div class="case-nav-top">
                  <strong class="mono">${escapeHtml(item.problem_id)}</strong>
                  ${renderInlineStatus(item.passed ? "pass" : "fail")}
                </div>
                <div class="case-nav-meta">
                  <span>${escapeHtml(item.problem_source || (item.problem || {}).source || "")}</span>
                  <span>${escapeHtml(item.problem_category || (item.problem || {}).category || "")}</span>
                  <span>${escapeHtml(item.problem_suite || (item.problem || {}).suite || "")}</span>
                  <span>${escapeHtml(item.problem_difficulty || (item.problem || {}).difficulty || "")}</span>
                </div>
                <div class="case-nav-stages">
                  <span>L ${shortStage(item.lint)}</span>
                  <span>S ${shortStage(item.simulation)}</span>
                  <span>Y ${shortStage(item.synthesis)}</span>
                  <span>A ${item.attempt || 0}</span>
                  <span>M ${formatOptionalRate(item.mutation_kill_rate)}</span>
                </div>
              </button>
            </article>
          `;
        })
        .join("")}
      </div>
    </div>
  `;
}

function renderCaseConsole() {
  if (!dom.resultsConsole || !state.selectedRun) {
    return;
  }
  const item = ensureSelectedCase();
  if (!item) {
    dom.resultsConsole.innerHTML = `Select a case row to inspect the full prompt, generated code, harness, logs, and artifacts.`;
    return;
  }
  const problem = item.problem || {};
  const harness = item.task_type === "rtl" ? problem.testbench : problem.reference_tb;
  const reference = item.task_type === "rtl" ? problem.reference_rtl : problem.golden_rtl;
  const mutants = Array.isArray(problem.mutant_rtls) ? problem.mutant_rtls : [];
  const apiTrace = item.api_trace || {};
  const viewer = state.artifactViewer && state.artifactViewer.caseKey === caseKey(item) ? state.artifactViewer : null;

  dom.resultsConsole.innerHTML = `
    <div class="console-head">
      <div>
        <p class="eyebrow">Case Console</p>
        <h2>${escapeHtml(item.model_id)} / ${escapeHtml(item.problem_id)}</h2>
      </div>
      <div class="console-meta">
        ${renderInlineStatus(item.passed ? "pass" : "fail")}
        <span class="problem-meta">attempt ${item.attempt || 0}</span>
        <span class="problem-meta">${escapeHtml(item.provider || "")}</span>
      </div>
    </div>

    <div class="metric-grid compact-grid">
      <div class="metric-card"><span class="metric-label">Source</span><strong>${escapeHtml(problem.source || item.problem_source || "")}</strong></div>
      <div class="metric-card"><span class="metric-label">Category</span><strong>${escapeHtml(problem.category || item.problem_category || "")}</strong></div>
      <div class="metric-card"><span class="metric-label">Suite</span><strong>${escapeHtml(problem.suite || item.problem_suite || "")}</strong></div>
      <div class="metric-card"><span class="metric-label">Track</span><strong>${escapeHtml(problem.track || item.problem_track || "")}</strong></div>
      <div class="metric-card"><span class="metric-label">Difficulty</span><strong>${escapeHtml(problem.difficulty || item.problem_difficulty || "")}</strong></div>
      <div class="metric-card"><span class="metric-label">Mutation Kill</span><strong>${formatOptionalRate(item.mutation_kill_rate)}</strong></div>
      <div class="metric-card"><span class="metric-label">Artifact Dir</span><strong class="mono">${escapeHtml(item.artifact_dir || "n/a")}</strong></div>
    </div>

    ${renderTextSection("Problem Prompt", problem.prompt)}
    ${renderTextSection("Module Header", problem.module_header)}
    ${renderCodeSection("Model Answer", item.candidate_code)}
    ${renderCodeSection("API Conversation Log", formatConversationLog(apiTrace.conversation))}
    ${renderCodeSection("API Request Log", formatJsonBlock(apiTrace.request))}
    ${renderCodeSection("API Response Log", formatJsonBlock(apiTrace.response))}
    ${renderCodeSection("API Error", apiTrace.error)}
    ${renderCodeSection(item.task_type === "rtl" ? "Evaluation Testbench" : "Reference Testbench", harness)}
    ${renderCodeSection(item.task_type === "rtl" ? "Reference RTL" : "Golden RTL", reference)}
    ${mutants.length ? mutants.map((mutant, idx) => renderCodeSection(`Mutant RTL ${idx + 1}`, mutant)).join("") : ""}

    <section class="console-section">
      <div class="console-section-head">
        <h3>Stage Outcomes</h3>
      </div>
      <div class="stage-grid">
        ${renderStageCard("Lint", item.lint)}
        ${renderStageCard("Simulation", item.simulation)}
        ${renderStageCard("Synthesis", item.synthesis)}
        ${
          item.mutation_results && item.mutation_results.length
            ? item.mutation_results.map((stage, idx) => renderStageCard(`Mutant ${idx + 1}`, stage)).join("")
            : ""
        }
      </div>
    </section>

    <section class="console-section">
      <div class="console-section-head">
        <h3>Artifacts</h3>
      </div>
      <div class="artifact-list">
        ${(item.artifacts || [])
          .map(
            (artifact) => `
              <div class="artifact-row">
                <div>
                  <strong>${escapeHtml(artifact.name)}</strong>
                  <div class="problem-meta">${escapeHtml(artifact.kind || "file")} · ${formatBytes(artifact.size || 0)}</div>
                  <div class="problem-foot mono">${escapeHtml(artifact.path || "")}</div>
                </div>
                <div class="stack-actions">
                  <button class="mini-button" data-view-artifact="${escapeHtml(artifact.path || "")}" data-artifact-name="${escapeHtml(
                    artifact.name || "",
                  )}" data-case-key="${escapeHtml(caseKey(item))}">View</button>
                  <a class="mini-link" target="_blank" href="/api/artifact?path=${encodeURIComponent(artifact.path || "")}">${
                    artifact.kind === "waveform" ? "Open Waveform" : "Open File"
                  }</a>
                </div>
              </div>
            `,
          )
          .join("") || `<div class="empty-state">No artifacts recorded for this case.</div>`}
      </div>
      ${
        viewer
          ? `
            <div class="artifact-viewer">
              <div class="console-section-head">
                <h3>${escapeHtml(viewer.name || "Artifact Viewer")}</h3>
              </div>
              <pre>${escapeHtml(viewer.content || viewer.error || "")}</pre>
            </div>
          `
          : ""
      }
    </section>
  `;
}

function renderOverviewCards(overview) {
  return `
    <div class="metric-grid">
      <div class="metric-card"><span class="metric-label">Models</span><strong>${overview.model_count || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Problems</span><strong>${overview.problem_count || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Cases</span><strong>${overview.case_count || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Passed</span><strong>${overview.passed_cases || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Failed</span><strong>${overview.failed_cases || 0}</strong></div>
    </div>
  `;
}

function renderResultsOverview(payload) {
  const overview = payload.overview || {};
  const models = payload.model_results || [];
  const problems = payload.problems || [];
  const passRate = overview.case_count ? formatRate((overview.passed_cases || 0) / overview.case_count) : "n/a";
  const providers = Array.from(new Set(models.map((item) => item.provider).filter(Boolean)));
  const modelIds = models.map((item) => item.model_id);
  const problemIds = problems.map((item) => item.id);
  const failingCases = (payload.cases || []).filter((item) => !item.passed).slice(0, 4);

  return `
    <div class="overview-shell">
      <div class="metric-grid overview-metrics">
        <div class="metric-card"><span class="metric-label">Models</span><strong>${overview.model_count || 0}</strong></div>
        <div class="metric-card"><span class="metric-label">Problems</span><strong>${overview.problem_count || 0}</strong></div>
        <div class="metric-card"><span class="metric-label">Cases</span><strong>${overview.case_count || 0}</strong></div>
        <div class="metric-card"><span class="metric-label">Passed</span><strong>${overview.passed_cases || 0}</strong></div>
        <div class="metric-card"><span class="metric-label">Failed</span><strong>${overview.failed_cases || 0}</strong></div>
        <div class="metric-card"><span class="metric-label">Pass Rate</span><strong>${passRate}</strong></div>
      </div>

      <div class="overview-detail-grid">
        <section class="metric-card detail-card">
          <span class="metric-label">Run Shape</span>
          <div class="detail-list">
            <div><strong>Run ID</strong><span class="mono">${escapeHtml(payload.run_id || "n/a")}</span></div>
            <div><strong>Scope</strong><span>${escapeHtml(payload.scope || "n/a")}</span></div>
            <div><strong>Providers</strong><span>${escapeHtml(providers.join(", ") || "n/a")}</span></div>
            <div><strong>Started</strong><span class="mono">${escapeHtml(payload.started_at || "n/a")}</span></div>
            <div><strong>Finished</strong><span class="mono">${escapeHtml(payload.finished_at || "n/a")}</span></div>
          </div>
        </section>

        <section class="metric-card detail-card">
          <span class="metric-label">Models In Run</span>
          <div class="token-list">
            ${modelIds.length ? modelIds.map((id) => `<span class="token mono">${escapeHtml(id)}</span>`).join("") : `<span class="problem-meta">No model rows.</span>`}
          </div>
        </section>

        <section class="metric-card detail-card">
          <span class="metric-label">Problems In Run</span>
          <div class="token-list">
            ${problemIds.length ? problemIds.map((id) => `<span class="token mono">${escapeHtml(id)}</span>`).join("") : `<span class="problem-meta">No problem snapshot.</span>`}
          </div>
        </section>

        <section class="metric-card detail-card">
          <span class="metric-label">Failing Cases</span>
          <div class="detail-list">
            ${
              failingCases.length
                ? failingCases
                    .map(
                      (item) =>
                        `<div><strong class="mono">${escapeHtml(item.model_id)} / ${escapeHtml(item.problem_id)}</strong><span>${escapeHtml(
                          item.feedback || "failed",
                        )}</span></div>`,
                    )
                    .join("")
                : `<div><strong>Summary</strong><span>No failing cases in this run.</span></div>`
            }
          </div>
        </section>
      </div>
    </div>
  `;
}

function renderLeaderboardCompareControls(models) {
  const options = models
    .map(
      (item) =>
        `<option value="${escapeHtml(item.model_id)}" ${item.model_id === state.compareModelA ? "selected" : ""}>${escapeHtml(
          compareModelOptionLabel(item),
        )}</option>`,
    )
    .join("");
  const secondOptions = models
    .map(
      (item) =>
        `<option value="${escapeHtml(item.model_id)}" ${item.model_id === state.compareModelB ? "selected" : ""}>${escapeHtml(
          compareModelOptionLabel(item),
        )}</option>`,
    )
    .join("");

  return `
    <div class="compare-toolbar">
      <label>
        Model A
        <select id="compareModelASelect">${options}</select>
      </label>
      <button class="mini-button compare-swap-button" data-swap-compare-models="true">Swap</button>
      <label>
        Model B
        <select id="compareModelBSelect">${secondOptions}</select>
      </label>
    </div>
  `;
}

function renderLeaderboardCompareSummary(compare) {
  const summary = compare.summary || {};
  const labels = compareLabels(compare);
  return `
    <div class="metric-grid compare-metrics">
      <div class="metric-card"><span class="metric-label">Comparable Cases</span><strong>${summary.comparable_cases || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">${escapeHtml(labels.aShort)} Passed</span><strong>${summary.model_a_passed || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">${escapeHtml(labels.bShort)} Passed</span><strong>${summary.model_b_passed || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Same Outcome</span><strong>${summary.same_outcome_cases || 0}</strong></div>
      <div class="metric-card compare-metric-highlight compare-metric-a"><span class="metric-label">${escapeHtml(labels.aShort)} Only Pass</span><strong>${summary.a_only_pass || 0}</strong></div>
      <div class="metric-card compare-metric-highlight compare-metric-b"><span class="metric-label">${escapeHtml(labels.bShort)} Only Pass</span><strong>${summary.b_only_pass || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Both Fail</span><strong>${summary.both_fail || 0}</strong></div>
      <div class="metric-card"><span class="metric-label">Missing Cases</span><strong>${(summary.missing_a || 0) + (summary.missing_b || 0)}</strong></div>
    </div>
    ${renderLeaderboardSliceComparisons(compare)}
  `;
}

function renderLeaderboardCompareGroups(compare) {
  const labels = compareLabels(compare);
  const groups = [
    { key: "a_only_pass", title: `${labels.a} passed but ${labels.b} failed` },
    { key: "b_only_pass", title: `${labels.b} passed but ${labels.a} failed` },
    { key: "both_fail", title: "Both models failed" },
    { key: "both_pass", title: "Both models passed" },
    { key: "missing", title: "Missing coverage" },
  ];
  const rows = compare.rows || [];
  const rendered = groups
    .map((group) => {
      const items = rows.filter((row) => row.outcome === group.key);
      if (!items.length) {
        return "";
      }
      return `
        <section class="compare-group">
          <div class="compare-group-head">
            <div>
              <p class="eyebrow">Outcome Bucket</p>
              <h3>${escapeHtml(group.title)}</h3>
            </div>
            <span class="problem-meta">${items.length} cases</span>
          </div>
          <div class="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Problem</th>
                  <th>Metadata</th>
                  <th>${escapeHtml(labels.a)}</th>
                  <th>${escapeHtml(labels.b)}</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                ${items.map((row) => renderLeaderboardCompareRow(compare, row)).join("")}
              </tbody>
            </table>
          </div>
        </section>
      `;
    })
    .join("");

  return rendered || `<div class="empty-state">No comparable case rows for the current selection.</div>`;
}

function renderLeaderboardSliceComparisons(compare) {
  const groups = [
    { key: "sources", title: "Source Comparison" },
    { key: "difficulties", title: "Difficulty Comparison" },
    { key: "tags", title: "Tag Comparison" },
  ];
  const content = groups
    .map((group) => {
      const rows = (compare.slice_comparison || {})[group.key] || [];
      if (!rows.length) {
        return "";
      }
      return `
        <section class="compare-group">
          <div class="compare-group-head">
            <div>
              <p class="eyebrow">Slice View</p>
              <h3>${escapeHtml(group.title)}</h3>
            </div>
            <span class="problem-meta">${rows.length} slices</span>
          </div>
          <div class="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Slice</th>
                  <th>A Score</th>
                  <th>B Score</th>
                  <th>Delta</th>
                  <th>Coverage</th>
                </tr>
              </thead>
              <tbody>
                ${rows
                  .map(
                    (row) => `
                      <tr>
                        <td>${escapeHtml(row.label || row.value || "n/a")}</td>
                        <td>${formatRate(row.model_a_score)}</td>
                        <td>${formatRate(row.model_b_score)}</td>
                        <td>${formatSignedRate(row.delta)}</td>
                        <td>
                          <div class="compare-meta">
                            <span>A ${row.model_a_cases || 0} / ${formatRate(row.model_a_weight)}</span>
                            <span>B ${row.model_b_cases || 0} / ${formatRate(row.model_b_weight)}</span>
                          </div>
                        </td>
                      </tr>
                    `,
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </section>
      `;
    })
    .join("");
  return content ? `<div class="compare-slice-shell">${content}</div>` : "";
}

function renderLeaderboardCompareRow(compare, row) {
  return `
    <tr>
      <td class="mono">${escapeHtml(row.problem_id)}</td>
      <td>
        <div class="compare-meta">
          <span>${escapeHtml(row.source || "n/a")}</span>
          <span>${escapeHtml(row.category || "n/a")}</span>
          <span>${escapeHtml(row.track || "n/a")}</span>
          <span>${escapeHtml(row.suite || "n/a")}</span>
          <span>${escapeHtml(row.difficulty || "n/a")}</span>
          ${(row.tags || []).length ? `<span>${escapeHtml((row.tags || []).slice(0, 3).join(", "))}</span>` : ""}
        </div>
      </td>
      <td>${renderCompareCaseCell(compare, row.model_a)}</td>
      <td>${renderCompareCaseCell(compare, row.model_b)}</td>
      <td>${renderCompareOutcome(row.outcome)}</td>
    </tr>
  `;
}

function renderCompareCaseCell(compare, entry) {
  if (!entry || !entry.present) {
    return `<div class="compare-case"><span class="status-chip status-skipped">missing</span></div>`;
  }
  const stageLine = `L ${shortStage({ status: entry.lint_status })} · S ${shortStage({ status: entry.simulation_status })} · Y ${shortStage({
    status: entry.synthesis_status,
  })}`;
  const runId = entry.run_id || compare.run_id || "";
  const showRunId = runId && runId !== compare.run_id;
  const openLink = entry.case_key
    ? `<a class="mini-link" href="/results?run=${encodeURIComponent(runId)}&case=${encodeURIComponent(entry.case_key)}">Open</a>`
    : "";
  return `
    <div class="compare-case">
      <div class="compare-case-top">
        ${renderInlineStatus(entry.status)}
        <span class="problem-meta">attempt ${entry.attempt || 0}</span>
      </div>
      ${showRunId ? `<div class="problem-meta mono">${escapeHtml(runId)}</div>` : ""}
      <div class="problem-meta mono">${escapeHtml(stageLine)}</div>
      <div class="compare-feedback">${escapeHtml(entry.feedback || "No feedback.")}</div>
      ${openLink}
    </div>
  `;
}

function renderCompareOutcome(outcome) {
  if (outcome === "a_only_pass") {
    return `<span class="status-chip status-completed">A only</span>`;
  }
  if (outcome === "b_only_pass") {
    return `<span class="status-chip status-completed">B only</span>`;
  }
  if (outcome === "both_pass") {
    return `<span class="status-chip status-completed">both pass</span>`;
  }
  if (outcome === "both_fail") {
    return `<span class="status-chip status-failed">both fail</span>`;
  }
  return `<span class="status-chip status-skipped">missing</span>`;
}

function compareLabels(compare) {
  return {
    a: compare.model_a || "Model A",
    b: compare.model_b || "Model B",
    aShort: shortenModelId(compare.model_a || "A"),
    bShort: shortenModelId(compare.model_b || "B"),
  };
}

function compareModelOptionLabel(item) {
  const provider = String((item && item.provider) || "").trim();
  const modelId = String((item && item.model_id) || "").trim();
  return provider ? `${modelId} · ${provider}` : modelId;
}

function renderLeaderboardCompareMeta(compare) {
  if (!compare) {
    return "Leaderboard aggregate across all benchmark problems.";
  }
  if (compare.compare_mode === "leaderboard") {
    return `Leaderboard aggregate · latest coverage from all recorded runs · A ${compare.model_a_run_id || "n/a"} · B ${compare.model_b_run_id || "n/a"}`;
  }
  if (compare.compare_mode === "same_run" && compare.run_id) {
    return `Same run · ${compare.run_id} · ${compare.started_at || ""} → ${compare.finished_at || ""}`;
  }
  return `Compare view · A ${compare.model_a_run_id || "n/a"} · B ${compare.model_b_run_id || "n/a"}`;
}

function renderLeaderboardBreakdown(row) {
  const groups = [
    { key: "sources", title: "Sources" },
    { key: "tracks", title: "Tracks" },
    { key: "difficulties", title: "Difficulties" },
    { key: "tags", title: "Tags" },
  ];
  return groups
    .map((group) => {
      const entries = (((row || {}).breakdowns || {})[group.key] || []).slice(0, group.key === "tags" ? 8 : 6);
      if (!entries.length) {
        return "";
      }
      return `
        <section class="leaderboard-breakdown-group">
          <div class="leaderboard-breakdown-title">${escapeHtml(group.title)}</div>
          <div class="leaderboard-breakdown-scroll">
            <div class="leaderboard-breakdown-items">
              ${entries
                .map(
                  (entry) => `
                    <article class="leaderboard-breakdown-item">
                      <div class="leaderboard-breakdown-label">${escapeHtml(displaySliceLabel(entry))}</div>
                      <div class="leaderboard-breakdown-metrics">
                        <span>${formatRate(entry.slice_weighted_pass_rate)}</span>
                        <span>Q ${formatRate(entry.slice_quality_score)}</span>
                        <span>${formatSignedRate(entry.lift_vs_model_overall)}</span>
                      </div>
                      <div class="leaderboard-breakdown-meta">${entry.cases || 0} cases · weight ${formatRate(entry.global_weight_mass)}</div>
                    </article>
                  `,
                )
                .join("")}
            </div>
          </div>
        </section>
      `;
    })
    .join("");
}

function renderSliceRankingSections(sliceRankings) {
  const groups = [
    { key: "sources", title: "Source Leaderboards" },
    { key: "tracks", title: "Track Leaderboards" },
    { key: "difficulties", title: "Difficulty Leaderboards" },
    { key: "tags", title: "Tag Leaderboards" },
  ];
  const sections = groups
    .map((group) => {
      const values = sliceRankings[group.key] || {};
      const keys = Object.keys(values);
      if (!keys.length) {
        return "";
      }
      return `
        <section class="compare-group" style="margin-top: 18px;">
          <div class="compare-group-head">
            <div>
              <p class="eyebrow">Slice Leaderboard</p>
              <h3>${escapeHtml(group.title)}</h3>
            </div>
          </div>
          ${keys
            .slice(0, group.key === "tags" ? 8 : 12)
            .map(
              (value) => `
                <div class="table-shell" style="margin-top: 12px;">
                  <div class="stack-subtitle"><strong>${escapeHtml(value)}</strong></div>
                  <table>
                    <thead>
                      <tr>
                        <th>Model</th>
                        <th>Score</th>
                        <th>Quality</th>
                        <th>Cases</th>
                        <th>Weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${(values[value] || [])
                        .slice(0, 5)
                        .map(
                          (row) => `
                            <tr>
                              <td class="mono">${escapeHtml(row.model_id || "")}</td>
                              <td>${formatRate(row.weighted_pass_score)}</td>
                              <td>${formatRate(row.quality_score)}</td>
                              <td>${row.cases || 0}</td>
                              <td>${formatRate(row.global_weight_mass)}</td>
                            </tr>
                          `,
                        )
                        .join("")}
                    </tbody>
                  </table>
                </div>
              `,
            )
            .join("")}
        </section>
      `;
    })
    .join("");
  return sections ? `<div class="leaderboard-slice-sections">${sections}</div>` : "";
}

function renderHighlightTokens(items) {
  if (!items || !items.length) {
    return `<div class="leaderboard-tag-list"><span class="token">n/a</span></div>`;
  }
  return `
    <div class="leaderboard-tag-list">
      ${items
        .slice(0, 4)
        .map((item) => `<span class="token leaderboard-tag-token">${escapeHtml(displaySliceLabel(item))}</span>`)
        .join("")}
    </div>
  `;
}

function displaySliceLabel(item) {
  const raw = String((item && (item.value || item.label)) || item || "").trim();
  if (!raw) {
    return "n/a";
  }
  return raw
    .replace(/^标签\s+/u, "")
    .replace(/^题源\s+/u, "")
    .replace(/^能力\s+/u, "")
    .replace(/^难度\s+/u, "")
    .replace(/^类别\s+/u, "");
}

function formatSignedRate(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "n/a";
  }
  const numeric = Number(value);
  return `${numeric >= 0 ? "+" : ""}${(numeric * 100).toFixed(1)}%`;
}

function renderTextSection(title, text) {
  if (!text) {
    return "";
  }
  return `
    <section class="console-section">
      <div class="console-section-head">
        <h3>${escapeHtml(title)}</h3>
      </div>
      <div class="console-text">${escapeHtml(text)}</div>
    </section>
  `;
}

function renderCodeSection(title, code) {
  if (!code) {
    return "";
  }
  return `
    <section class="console-section">
      <div class="console-section-head">
        <h3>${escapeHtml(title)}</h3>
      </div>
      <pre>${escapeHtml(code)}</pre>
    </section>
  `;
}

function formatConversationLog(messages) {
  if (!Array.isArray(messages) || !messages.length) {
    return "";
  }
  return messages
    .map((message, index) => {
      const role = String(message.role || "unknown").toUpperCase();
      const content = typeof message.content === "string" ? message.content : formatJsonBlock(message.content);
      return `[${index + 1}] ${role}\n${content || ""}`.trim();
    })
    .join("\n\n----------------------------------------\n\n");
}

function formatJsonBlock(value) {
  if (!value) {
    return "";
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch (error) {
    return String(value);
  }
}

function renderStageCard(title, stage) {
  const entry = stage || {};
  const detail = [entry.reason, entry.stderr, entry.stdout].filter(Boolean).join("\n\n");
  return `
    <article class="stage-card">
      <div class="stack-head">
        <strong>${escapeHtml(title)}</strong>
        ${renderStageBadge(entry)}
      </div>
      <div class="problem-meta">returncode: ${entry.returncode === null || entry.returncode === undefined ? "n/a" : entry.returncode}</div>
      <pre>${escapeHtml(detail || "No additional stage output.")}</pre>
    </article>
  `;
}

function renderStageBadge(stage) {
  const status = (stage && stage.status) || "unknown";
  return `<span class="status-chip status-${escapeHtml(statusToChip(status))}">${escapeHtml(status)}</span>`;
}

function renderInlineStatus(status) {
  return `<span class="status-chip status-${escapeHtml(statusToChip(status))}">${escapeHtml(status)}</span>`;
}

function statusToChip(status) {
  if (status === "pass" || status === "completed") {
    return "completed";
  }
  if (status === "fail" || status === "failed") {
    return "failed";
  }
  if (status === "paused") {
    return "paused";
  }
  if (status === "running") {
    return "running";
  }
  if (status === "queued") {
    return "queued";
  }
  return "skipped";
}

function renderJobProgressBar(progress) {
  const total = Number(progress.total_cases || 0);
  if (!total) {
    return "";
  }
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  return `
    <div class="job-progress">
      <div class="job-progress-bar">
        <span class="job-progress-fill" style="width:${percent.toFixed(1)}%"></span>
      </div>
      <div class="job-progress-stats">
        <span>${Math.round(percent)}%</span>
        <span>${progress.completed_cases || 0}/${total} cases</span>
      </div>
    </div>
  `;
}

function renderJobProgressText(progress) {
  const total = Number(progress.total_cases || 0);
  const completed = Number(progress.completed_cases || 0);
  const remaining = Number(progress.remaining_cases || 0);
  if (!total) {
    return "Preparing run plan.";
  }
  return `Completed ${completed}/${total} cases · Remaining ${remaining} · Attempt ${progress.attempt || 0}`;
}

function renderJobEtaText(progress) {
  const eta = progress.eta || {};
  if (!eta.label) {
    return "";
  }
  const confidence = eta.confidence ? ` · ${eta.confidence} confidence` : "";
  const basis = eta.basis ? ` · ${eta.basis}` : "";
  return `ETA ${eta.label}${confidence}${basis}`;
}

function acknowledgeInteraction(element) {
  if (!(element instanceof HTMLElement)) {
    return;
  }
  element.classList.remove("button-ack");
  void element.offsetWidth;
  element.classList.add("button-ack");
  window.setTimeout(() => element.classList.remove("button-ack"), 260);
}

function setButtonState(button, label, className = "") {
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  if (!button.dataset.defaultLabel) {
    button.dataset.defaultLabel = button.textContent || "";
  }
  button.textContent = label;
  button.classList.remove("button-busy", "button-success");
  if (className) {
    button.classList.add(className);
  }
}

function resetButtonState(button) {
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  button.disabled = false;
  button.classList.remove("button-busy", "button-success");
  button.textContent = button.dataset.defaultLabel || button.textContent || "";
}

function showToast(message, tone = "success") {
  let stack = document.getElementById("toastStack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toastStack";
    stack.className = "toast-stack";
    document.body.appendChild(stack);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone}`;
  toast.textContent = message;
  stack.appendChild(toast);
  window.requestAnimationFrame(() => toast.classList.add("toast-visible"));
  window.setTimeout(() => {
    toast.classList.remove("toast-visible");
    window.setTimeout(() => toast.remove(), 180);
  }, 1800);
}

async function saveConfig(button) {
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Saving...", "button-busy");
  }
  try {
    const uiConfig = collectUiConfig();
    const payload = await apiPost("/api/config", uiConfig);
    state.uiConfig = payload.uiConfig;
    syncFormFromState();
    syncRunModelSelection({ reset: true });
    renderProviders();
    renderRunModelPicker();
    if (button instanceof HTMLButtonElement) {
      button.disabled = false;
      setButtonState(button, "Saved", "button-success");
      window.setTimeout(() => resetButtonState(button), 1200);
    }
    showToast("Local config saved.", "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function runBenchmark(button) {
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Launching...", "button-busy");
  }
  try {
    const request = collectRunRequest();
    if (!request.selectedModels.length) {
      resetButtonState(button);
      window.alert("Select at least one enabled model for this run.");
      return;
    }
    if (request.scope === "selected_problems" && !request.problemIds.length) {
      resetButtonState(button);
      window.alert("Pick at least one problem.");
      return;
    }
    if (request.scope === "custom_problem" && !request.customProblem.prompt.trim()) {
      resetButtonState(button);
      window.alert("Paste a custom prompt first.");
      return;
    }

    await apiPost("/api/run", request);
    await loadState({ preserveInputs: true });
    if (button instanceof HTMLButtonElement) {
      button.disabled = false;
      setButtonState(button, "Queued", "button-success");
      window.setTimeout(() => resetButtonState(button), 1200);
    }
    showToast("Benchmark job queued.", "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function refreshState(button) {
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Refreshing...", "button-busy");
  }
  try {
    await loadState({ preserveInputs: true });
    if (button instanceof HTMLButtonElement) {
      button.disabled = false;
      setButtonState(button, "Refreshed", "button-success");
      window.setTimeout(() => resetButtonState(button), 900);
    }
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function resumeJob(jobId, button) {
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Resuming...", "button-busy");
  }
  try {
    await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/resume`, {});
    await loadState({ preserveInputs: true });
    showToast("Job resumed.", "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function pauseJob(jobId, button) {
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Pausing...", "button-busy");
  }
  try {
    await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/pause`, {});
    await loadState({ preserveInputs: true });
    showToast("Pause requested. The job will stop after the current case.", "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function rerunLeaderboardFailures(modelId, provider, failedCases, button) {
  if (Number(failedCases || 0) <= 0) {
    throw new Error("No failed cases are available for rerun.");
  }
  const confirmed = window.confirm(`Create a new job to rerun ${failedCases} failed leaderboard cases for ${modelId}?`);
  if (!confirmed) {
    return;
  }
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Queued...", "button-busy");
  }
  try {
    const payload = await apiPost("/api/leaderboard/rerun-failures", {
      model_id: modelId,
      provider,
    });
    await loadState({ preserveInputs: true });
    showToast(`Queued new job ${((payload || {}).job || {}).job_id || ""} for ${modelId}.`, "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function deleteJob(jobId, button) {
  const confirmed = window.confirm("Delete this job and remove its saved run snapshot?");
  if (!confirmed) {
    return;
  }
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Deleting...", "button-busy");
  }
  try {
    await apiDelete(`/api/jobs/${encodeURIComponent(jobId)}`);
    await loadState({ preserveInputs: true });
    showToast("Job deleted.", "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function resetLeaderboardData(button) {
  const confirmed = window.confirm("Reset the suite leaderboard to zero? Existing run history will be kept.");
  if (!confirmed) {
    return;
  }
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    setButtonState(button, "Resetting...", "button-busy");
  }
  try {
    const payload = await apiPost("/api/leaderboard/reset", {});
    state.leaderboard = payload.leaderboard || { updated_at: "", models: [] };
    state.leaderboardCompare = null;
    preserveScroll(() => {
      renderLeaderboard();
      renderLeaderboardPage();
    });
    if (button instanceof HTMLButtonElement) {
      button.disabled = false;
      setButtonState(button, "Reset", "button-success");
      window.setTimeout(() => resetButtonState(button), 1200);
    }
    showToast("Leaderboard reset. New suite runs will accumulate from now on.", "success");
  } catch (error) {
    resetButtonState(button);
    throw error;
  }
}

async function viewRun(runId, options = {}) {
  const previousRunId = state.selectedRunId;
  const sameRun = previousRunId === runId;
  const previousExpandedModels = new Set(state.expandedModels);
  const previousSelectedCaseKey = state.selectedCaseKey;
  const previousArtifactViewer = state.artifactViewer;
  const payload = await apiGet(`/api/history/${encodeURIComponent(runId)}`);
  state.selectedRunId = runId;
  state.selectedRun = payload;
  if (sameRun) {
    const availableModels = new Set((payload.model_results || []).map((item) => item.model_id));
    state.expandedModels = new Set(Array.from(previousExpandedModels).filter((modelId) => availableModels.has(modelId)));
    if (!state.expandedModels.size && payload.model_results && payload.model_results.length) {
      state.expandedModels.add(payload.model_results[0].model_id);
    }
  } else {
    state.expandedModels = new Set();
    if (payload.model_results && payload.model_results.length) {
      state.expandedModels.add(payload.model_results[0].model_id);
    }
  }
  if (page === "leaderboard") {
    syncCompareSelection();
  }
  state.selectedCaseKey = page === "results" ? (sameRun ? previousSelectedCaseKey || currentCaseFromUrl() : currentCaseFromUrl()) : "";
  ensureSelectedCase();
  state.artifactViewer =
    sameRun && previousArtifactViewer && previousArtifactViewer.caseKey === state.selectedCaseKey ? previousArtifactViewer : null;
  if (page === "results") {
    updateResultsUrl(options);
  } else if (page === "leaderboard") {
    updateLeaderboardUrl(options);
    await loadLeaderboardCompare(options);
  }
  render();
  if (options.reveal !== false) {
    revealSelectedRun();
  }
}

async function loadLeaderboardCompare(options = {}) {
  if (page !== "leaderboard") {
    return;
  }
  if (!state.compareModelA || !state.compareModelB || state.compareModelA === state.compareModelB) {
    state.leaderboardCompare = null;
    preserveScroll(() => renderLeaderboardPage());
    return;
  }
  const params = new URLSearchParams();
  if (state.selectedRunId) {
    params.set("run", state.selectedRunId);
  }
  params.set("a", state.compareModelA);
  params.set("b", state.compareModelB);
  const payload = await apiGet(`/api/leaderboard/compare?${params.toString()}`);
  state.leaderboardCompare = payload;
  updateLeaderboardUrl(options);
  preserveScroll(() => renderLeaderboardPage());
}

async function viewArtifact(path, name, caseKeyValue) {
  try {
    const response = await fetch(`/api/artifact?path=${encodeURIComponent(path)}`);
    if (!response.ok) {
      throw new Error(`Unable to load artifact: ${name}`);
    }
    const content = await response.text();
    state.artifactViewer = { path, name, content, caseKey: caseKeyValue };
  } catch (error) {
    state.artifactViewer = { path, name, error: error.message || String(error), caseKey: caseKeyValue };
  }
  renderCaseConsole();
}

function ensureSelectedCase() {
  const cases = (state.selectedRun && state.selectedRun.cases) || [];
  if (!cases.length) {
    return null;
  }
  let item = cases.find((entry) => caseKey(entry) === state.selectedCaseKey);
  if (!item) {
    item = cases[0];
    state.selectedCaseKey = caseKey(item);
  }
  return item;
}

function caseKey(item) {
  return [item.model_id, item.problem_id, item.attempt].join("::");
}

function syncCompareSelection() {
  const models = ((state.leaderboard && state.leaderboard.models) || []).map((item) => item.model_id);
  const fromUrl = currentCompareModelsFromUrl();
  if (!models.length) {
    state.compareModelA = "";
    state.compareModelB = "";
    state.leaderboardCompare = null;
    return;
  }

  if (!state.compareModelA || !models.includes(state.compareModelA)) {
    state.compareModelA = models.includes(fromUrl.modelA) ? fromUrl.modelA : models[0];
  }
  if (!state.compareModelB || !models.includes(state.compareModelB) || state.compareModelB === state.compareModelA) {
    state.compareModelB = models.includes(fromUrl.modelB) && fromUrl.modelB !== state.compareModelA ? fromUrl.modelB : models[1] || "";
  }
  if (state.compareModelA === state.compareModelB && models.length > 1) {
    state.compareModelB = models.find((item) => item !== state.compareModelA) || "";
  }
}

function currentRunFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get("run") || "";
}

function currentCaseFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get("case") || "";
}

function currentCompareModelsFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return {
    modelA: params.get("a") || "",
    modelB: params.get("b") || "",
  };
}

function updateResultsUrl(options = {}) {
  if (page !== "results" || options.pushHistory === false || !state.selectedRunId) {
    return;
  }
  const params = new URLSearchParams();
  params.set("run", state.selectedRunId);
  if (state.selectedCaseKey) {
    params.set("case", state.selectedCaseKey);
  }
  window.history.replaceState({}, "", `/results?${params.toString()}`);
}

function updateLeaderboardUrl(options = {}) {
  if (page !== "leaderboard" || options.pushHistory === false) {
    return;
  }
  const params = new URLSearchParams();
  if (state.selectedRunId) {
    params.set("run", state.selectedRunId);
  }
  if (state.compareModelA) {
    params.set("a", state.compareModelA);
  }
  if (state.compareModelB) {
    params.set("b", state.compareModelB);
  }
  window.history.replaceState({}, "", `/leaderboard?${params.toString()}`);
}

function revealSelectedRun() {
  const target =
    page === "results"
      ? dom.resultsConsole || dom.resultsOverview
      : page === "leaderboard"
        ? dom.leaderboardCompareSummary || dom.leaderboardCompareControls
        : dom.resultDetail;
  if (!target) {
    return;
  }
  target.scrollIntoView({ behavior: "smooth", block: "start" });
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
  if (dom.refreshButton) {
    dom.refreshButton.addEventListener("click", (event) => refreshState(event.currentTarget).catch(showError));
  }
  if (dom.saveConfigButton) {
    dom.saveConfigButton.addEventListener("click", (event) => saveConfig(event.currentTarget).catch(showError));
  }
  if (dom.runButton) {
    dom.runButton.addEventListener("click", (event) => runBenchmark(event.currentTarget).catch(showError));
  }
  if (dom.resetLeaderboardButton) {
    dom.resetLeaderboardButton.addEventListener("click", (event) => resetLeaderboardData(event.currentTarget).catch(showError));
  }
  if (dom.problemSearchInput) {
    dom.problemSearchInput.addEventListener("input", (event) => {
      state.problemSearch = event.target.value || "";
      renderProblems();
    });
  }
  document.querySelectorAll('input[name="scope"]').forEach((radio) => {
    radio.addEventListener("change", renderScope);
  });
  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.matches("input[data-problem-id]")) {
      const problemId = target.dataset.problemId;
      if (!problemId) {
        return;
      }
      if (target.checked) {
        state.selectedProblemIds.add(problemId);
      } else {
        state.selectedProblemIds.delete(problemId);
      }
      renderProblems();
      return;
    }

    if (target.matches("input[data-run-model-key]")) {
      const modelKey = target.dataset.runModelKey;
      if (!modelKey) {
        return;
      }
      if (target.checked) {
        state.selectedRunModelKeys.add(modelKey);
      } else {
        state.selectedRunModelKeys.delete(modelKey);
      }
      renderRunModelPicker();
      return;
    }

    if (target.id === "compareModelASelect") {
      state.compareModelA = target.value || "";
      state.leaderboardCompare = null;
      loadLeaderboardCompare().catch(showError);
      return;
    }

    if (target.id === "compareModelBSelect") {
      state.compareModelB = target.value || "";
      state.leaderboardCompare = null;
      loadLeaderboardCompare().catch(showError);
    }
  });
  document.addEventListener("toggle", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLDetailsElement) || !target.matches("details[data-leaderboard-breakdown-key]")) {
      return;
    }
    const key = target.dataset.leaderboardBreakdownKey;
    if (!key) {
      return;
    }
    if (target.open) {
      state.expandedLeaderboardBreakdowns.add(key);
    } else {
      state.expandedLeaderboardBreakdowns.delete(key);
    }
  });
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const interactive = target.closest("button, a.nav-link, a.mini-link");
    acknowledgeInteraction(interactive);

    const runId = target.dataset.viewRun;
    if (runId) {
      viewRun(runId).catch(showError);
      return;
    }

    const resumeJobId = target.dataset.resumeJob;
    if (resumeJobId) {
      resumeJob(resumeJobId, target).catch(showError);
      return;
    }

    const pauseJobId = target.dataset.pauseJob;
    if (pauseJobId) {
      pauseJob(pauseJobId, target).catch(showError);
      return;
    }

    const deleteJobId = target.dataset.deleteJob;
    if (deleteJobId) {
      deleteJob(deleteJobId, target).catch(showError);
      return;
    }

    const sourceToSelect = target.dataset.selectSource;
    if (sourceToSelect) {
      state.problems.filter((problem) => problem.source === sourceToSelect).forEach((problem) => state.selectedProblemIds.add(problem.id));
      renderProblems();
      return;
    }

    const sourceToClear = target.dataset.clearSource;
    if (sourceToClear) {
      state.problems
        .filter((problem) => problem.source === sourceToClear)
        .forEach((problem) => state.selectedProblemIds.delete(problem.id));
      renderProblems();
      return;
    }

    if (target.dataset.selectAllRunModels) {
      state.selectedRunModelKeys = new Set(availableRunModels().map((item) => item.key));
      renderRunModelPicker();
      return;
    }

    if (target.dataset.clearAllRunModels) {
      state.selectedRunModelKeys = new Set();
      renderRunModelPicker();
      return;
    }

    const providerToSelect = target.dataset.selectRunProvider;
    if (providerToSelect) {
      availableRunModels()
        .filter((item) => item.provider === providerToSelect)
        .forEach((item) => state.selectedRunModelKeys.add(item.key));
      renderRunModelPicker();
      return;
    }

    const providerToClear = target.dataset.clearRunProvider;
    if (providerToClear) {
      availableRunModels()
        .filter((item) => item.provider === providerToClear)
        .forEach((item) => state.selectedRunModelKeys.delete(item.key));
      renderRunModelPicker();
      return;
    }

    const modelId = target.dataset.toggleModel;
    if (modelId) {
      if (state.expandedModels.has(modelId)) {
        state.expandedModels.delete(modelId);
      } else {
        state.expandedModels.add(modelId);
      }
      preserveScroll(() => renderResultsModelSummary());
      return;
    }

    const caseSelection = target.dataset.selectCase;
    if (caseSelection) {
      state.selectedCaseKey = caseSelection;
      if (target.dataset.modelId) {
        state.expandedModels.add(target.dataset.modelId);
      }
      state.artifactViewer = null;
      updateResultsUrl();
      preserveScroll(() => {
        renderCaseConsole();
        renderResultsModelSummary();
      });
      return;
    }

    const artifactPath = target.dataset.viewArtifact;
    if (artifactPath) {
      viewArtifact(artifactPath, target.dataset.artifactName || "", target.dataset.caseKey || "").catch(showError);
      return;
    }

    const leaderboardRerunModel = target.dataset.leaderboardRerunModel;
    if (leaderboardRerunModel) {
      const row = ((state.leaderboard && state.leaderboard.models) || []).find(
        (item) =>
          String((item && item.model_id) || "").trim() === leaderboardRerunModel &&
          String((item && item.provider) || "").trim() === String(target.dataset.leaderboardRerunProvider || "").trim(),
      );
      rerunLeaderboardFailures(
        leaderboardRerunModel,
        target.dataset.leaderboardRerunProvider || "",
        Number((row && row.failed_cases) || 0),
        target,
      ).catch(showError);
      return;
    }

    if (target.dataset.swapCompareModels) {
      const current = state.compareModelA;
      state.compareModelA = state.compareModelB;
      state.compareModelB = current;
      state.leaderboardCompare = null;
      loadLeaderboardCompare().catch(showError);
    }
  });
}

function groupProblems(problems) {
  const sources = new Map();
  problems.forEach((problem) => {
    const source = problem.source || "local";
    const category = problem.category || "uncategorized";
    if (!sources.has(source)) {
      sources.set(source, { source, total: 0, categories: new Map() });
    }
    const sourceGroup = sources.get(source);
    sourceGroup.total += 1;
    if (!sourceGroup.categories.has(category)) {
      sourceGroup.categories.set(category, { category, items: [] });
    }
    sourceGroup.categories.get(category).items.push(problem);
  });
  return Array.from(sources.values())
    .map((sourceGroup) => ({
      source: sourceGroup.source,
      total: sourceGroup.total,
      categories: Array.from(sourceGroup.categories.values()).sort((a, b) => a.category.localeCompare(b.category)),
    }))
    .sort((a, b) => a.source.localeCompare(b.source));
}

function countCategories(grouped) {
  return grouped.reduce((count, source) => count + source.categories.length, 0);
}

function selectFinalCases(rows) {
  const final = new Map();
  (rows || []).forEach((item) => {
    const modelId = String((item && item.model_id) || "").trim();
    const problemId = String((item && item.problem_id) || "").trim();
    if (!modelId || !problemId) {
      return;
    }
    const key = `${modelId}::${problemId}`;
    const current = final.get(key);
    const currentAttempt = current ? Number(current.attempt || 0) : -1;
    const attempt = Number(item.attempt || 0);
    if (!current || attempt >= currentAttempt) {
      final.set(key, item);
    }
  });
  return Array.from(final.values());
}

function failedFinalCases(job) {
  if (!job || !job.result) {
    return [];
  }
  return selectFinalCases((job.result && job.result.cases) || [])
    .filter((item) => item && item.passed === false)
    .sort((a, b) => `${a.model_id}/${a.problem_id}`.localeCompare(`${b.model_id}/${b.problem_id}`));
}

function renderJobFailedCases(job) {
  if (!job || !job.result) {
    return "";
  }
  const failedCases = failedFinalCases(job);
  if (!failedCases.length) {
    return "";
  }
  return `
    <div class="job-failed-cases">
      <div class="problem-meta">Failed Cases</div>
      <div class="job-failed-case-list">
        ${failedCases
          .slice(0, 10)
          .map(
            (item) => `
              <div class="job-failed-case-row">
                <div>
                  <strong class="mono">${escapeHtml(item.model_id)} / ${escapeHtml(item.problem_id)}</strong>
                  <div class="problem-meta">${escapeHtml(item.feedback || "failed")}</div>
                </div>
              </div>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function formatRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatOptionalRate(value) {
  return value === null || value === undefined ? "n/a" : formatRate(value);
}

function shortStage(stage) {
  const status = ((stage && stage.status) || "n/a").toLowerCase();
  if (status === "pass") {
    return "PASS";
  }
  if (status === "fail") {
    return "FAIL";
  }
  if (status === "skipped") {
    return "SKIP";
  }
  return status.toUpperCase();
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function shortenModelId(value) {
  const text = String(value || "");
  if (text.length <= 18) {
    return text;
  }
  return `${text.slice(0, 8)}...${text.slice(-7)}`;
}

function summarizeError(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  const [firstLine] = text.split("\n");
  return firstLine.trim();
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

function groupBy(items, getKey) {
  return items.reduce((groups, item) => {
    const key = getKey(item);
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(item);
    return groups;
  }, {});
}

bindEvents();
loadState().catch(showError);
