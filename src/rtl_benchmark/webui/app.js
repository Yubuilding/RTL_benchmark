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
  selectedProblemIds: new Set(),
  expandedModels: new Set(),
  artifactViewer: null,
  problemSearch: "",
  pollTimer: null,
};

function byId(id) {
  return document.getElementById(id);
}

const dom = {
  providersGrid: byId("providersGrid"),
  refreshButton: byId("refreshButton"),
  saveConfigButton: byId("saveConfigButton"),
  runButton: byId("runButton"),
  temperatureInput: byId("temperatureInput"),
  maxTokensInput: byId("maxTokensInput"),
  generationTimeoutInput: byId("generationTimeoutInput"),
  executionModeSelect: byId("executionModeSelect"),
  executionTimeoutInput: byId("executionTimeoutInput"),
  dockerImageInput: byId("dockerImageInput"),
  dockerBinaryInput: byId("dockerBinaryInput"),
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
  state.problems = payload.problems || [];
  state.problemStats = payload.problemStats || { total: 0, sources: 0, categories: 0 };
  state.history = payload.history || [];
  state.leaderboard = payload.leaderboard || { updated_at: "", models: [] };
  state.jobs = payload.jobs || [];
  pruneSelectedProblems();

  if (!preserveInputs) {
    syncFormFromState();
  }
  render();

  if (page === "results") {
    const desiredRunId = state.selectedRunId || currentRunFromUrl() || (state.history[0] && state.history[0].run_id) || "";
    if (desiredRunId) {
      await viewRun(desiredRunId, { pushHistory: false });
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
  return {
    providers: Array.from((dom.providersGrid || document.createElement("div")).querySelectorAll(".provider-card")).map((card) => ({
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
      temperature: Number((dom.temperatureInput || { value: 0 }).value || 0),
      max_tokens: Number((dom.maxTokensInput || { value: 1024 }).value || 1024),
      timeout_seconds: Number((dom.generationTimeoutInput || { value: 60 }).value || 60),
    },
    execution: {
      mode: (dom.executionModeSelect || { value: "docker" }).value,
      timeout_seconds: Number((dom.executionTimeoutInput || { value: 30 }).value || 30),
      docker_image: (dom.dockerImageInput || { value: "" }).value.trim(),
      docker_binary: (dom.dockerBinaryInput || { value: "" }).value.trim(),
    },
  };
}

function collectRunRequest() {
  const scope = scopeValue();
  return {
    scope,
    uiConfig: collectUiConfig(),
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

function render() {
  renderProviders();
  renderProblems();
  renderScope();
  renderJobs();
  renderHistory();
  renderLeaderboard();
  if (page === "home") {
    renderHomeRunDetail();
  } else {
    renderResultsPage();
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

function renderProblems() {
  if (!dom.problemPicker) {
    return;
  }
  const query = state.problemSearch.trim().toLowerCase();
  const filtered = state.problems.filter((problem) => {
    const haystack = [problem.id, problem.prompt, problem.source, problem.category, problem.task_type, problem.top_module]
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
                                <div class="problem-meta">${escapeHtml(problem.top_module || "n/a")} · ${escapeHtml(
                                  problem.language || "n/a",
                                )}</div>
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
            ${
              job.result
                ? `<button class="mini-button" data-view-run="${escapeHtml(job.result.run_id)}">Preview Result</button>
                   <a class="mini-link" href="/results?run=${encodeURIComponent(job.result.run_id)}">Open Results Page</a>`
                : ""
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function renderHistory() {
  const container = page === "results" ? dom.resultsHistoryList : dom.historyList;
  if (!container) {
    return;
  }
  if (!state.history.length) {
    container.innerHTML = `<div class="stack-card empty-state">No recorded tests yet.</div>`;
    return;
  }

  container.innerHTML = state.history
    .map((item) => {
      const summary = item.summary || [];
      const top = summary[0] ? `${summary[0].model_id} ${formatRate(summary[0].score)}` : "no summary";
      const active = state.selectedRunId === item.run_id ? "history-card-active" : "";
      return `
        <article class="stack-card ${active}">
          <div class="stack-head">
            <strong class="mono">${escapeHtml(item.run_id)}</strong>
            <span class="status-chip status-completed">${escapeHtml(item.scope || "suite")}</span>
          </div>
          <div class="stack-subtitle">${escapeHtml(item.started_at || "")} · ${item.model_count} models · ${item.case_count} cases</div>
          <div class="stack-subtitle">Top row: ${escapeHtml(top)}</div>
          <div class="stack-actions">
            <button class="mini-button" data-view-run="${escapeHtml(item.run_id)}">Preview</button>
            ${page === "home" ? `<a class="mini-link" href="/results?run=${encodeURIComponent(item.run_id)}">Results Page</a>` : ""}
          </div>
        </article>
      `;
    })
    .join("");
}

function renderLeaderboard() {
  const table = page === "results" ? dom.resultsLeaderboardTable : dom.leaderboardTable;
  const timestamp = page === "results" ? dom.resultsLeaderboardTimestamp : dom.leaderboardTimestamp;
  if (!table || !timestamp) {
    return;
  }
  const leaderboard = state.leaderboard || { models: [] };
  timestamp.textContent = leaderboard.updated_at ? `Updated ${leaderboard.updated_at}` : "No leaderboard updates yet.";

  const rows = leaderboard.models || [];
  if (!rows.length) {
    table.innerHTML = `<div class="stack-card empty-state">Run a full suite benchmark to populate the local leaderboard.</div>`;
    return;
  }

  table.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th>Provider</th>
          <th>Pass Rate</th>
          <th>Cases</th>
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
                <td>${formatRate(row.pass_rate)}</td>
                <td>${row.cases || 0}</td>
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
  dom.resultsOverview.innerHTML = renderOverviewCards(payload.overview || {});
  dom.resultsRunMeta.textContent = `${payload.scope || "run"} · ${payload.started_at || ""} → ${payload.finished_at || ""}`;
  renderResultsModelSummary();
  renderCaseConsole();
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
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Provider</th>
            <th>Pass</th>
            <th>Fail</th>
            <th>Pass Rate</th>
            <th>Cases</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${models
            .map((model) => {
              const expanded = state.expandedModels.has(model.model_id);
              return `
                <tr>
                  <td class="mono">${escapeHtml(model.model_id)}</td>
                  <td>${escapeHtml(model.provider || "")}</td>
                  <td>${model.passed}</td>
                  <td>${model.failed}</td>
                  <td>${formatRate(model.pass_rate)}</td>
                  <td>${model.cases}</td>
                  <td><button class="mini-button" data-toggle-model="${escapeHtml(model.model_id)}">${expanded ? "Hide" : "Detail"}</button></td>
                </tr>
                ${
                  expanded
                    ? `<tr class="expand-row"><td colspan="7">${renderModelCasesTable(model)}</td></tr>`
                    : ""
                }
              `;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderModelCasesTable(model) {
  return `
    <div class="model-case-table">
      <table>
        <thead>
          <tr>
            <th>Problem</th>
            <th>Source</th>
            <th>Lint</th>
            <th>Sim</th>
            <th>Synth</th>
            <th>Mutation</th>
            <th>Attempt</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${model.items
            .map((item) => {
              const key = caseKey(item);
              return `
                <tr class="${state.selectedCaseKey === key ? "selected-row" : ""}">
                  <td class="mono">${escapeHtml(item.problem_id)}</td>
                  <td>${escapeHtml(item.problem_source || (item.problem || {}).source || "")} / ${escapeHtml(
                    item.problem_category || (item.problem || {}).category || "",
                  )}</td>
                  <td>${renderStageBadge(item.lint)}</td>
                  <td>${renderStageBadge(item.simulation)}</td>
                  <td>${renderStageBadge(item.synthesis)}</td>
                  <td>${formatOptionalRate(item.mutation_kill_rate)}</td>
                  <td>${item.attempt || 0}</td>
                  <td><button class="mini-button" data-select-case="${escapeHtml(key)}" data-model-id="${escapeHtml(
                    model.model_id,
                  )}">Open Console</button></td>
                </tr>
              `;
            })
            .join("")}
        </tbody>
      </table>
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
      <div class="metric-card"><span class="metric-label">Mutation Kill</span><strong>${formatOptionalRate(item.mutation_kill_rate)}</strong></div>
      <div class="metric-card"><span class="metric-label">Artifact Dir</span><strong class="mono">${escapeHtml(item.artifact_dir || "n/a")}</strong></div>
    </div>

    ${renderTextSection("Problem Prompt", problem.prompt)}
    ${renderTextSection("Module Header", problem.module_header)}
    ${renderCodeSection("Model Answer", item.candidate_code)}
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
  if (status === "running") {
    return "running";
  }
  if (status === "queued") {
    return "queued";
  }
  return "skipped";
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

async function viewRun(runId, options = {}) {
  const payload = await apiGet(`/api/history/${encodeURIComponent(runId)}`);
  state.selectedRunId = runId;
  state.selectedRun = payload;
  state.artifactViewer = null;
  state.expandedModels = new Set();
  if (payload.model_results && payload.model_results.length) {
    state.expandedModels.add(payload.model_results[0].model_id);
  }
  if (page === "results" && options.pushHistory !== false) {
    window.history.replaceState({}, "", `/results?run=${encodeURIComponent(runId)}`);
  }
  state.selectedCaseKey = "";
  ensureSelectedCase();
  render();
  revealSelectedRun();
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

function currentRunFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get("run") || "";
}

function revealSelectedRun() {
  const target = page === "results" ? dom.resultsConsole || dom.resultsOverview : dom.resultDetail;
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
    dom.refreshButton.addEventListener("click", () => loadState().catch(showError));
  }
  if (dom.saveConfigButton) {
    dom.saveConfigButton.addEventListener("click", () => saveConfig().catch(showError));
  }
  if (dom.runButton) {
    dom.runButton.addEventListener("click", () => runBenchmark().catch(showError));
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
    }
  });
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const runId = target.dataset.viewRun;
    if (runId) {
      viewRun(runId).catch(showError);
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

    const modelId = target.dataset.toggleModel;
    if (modelId) {
      if (state.expandedModels.has(modelId)) {
        state.expandedModels.delete(modelId);
      } else {
        state.expandedModels.add(modelId);
      }
      renderResultsModelSummary();
      return;
    }

    const caseSelection = target.dataset.selectCase;
    if (caseSelection) {
      state.selectedCaseKey = caseSelection;
      if (target.dataset.modelId) {
        state.expandedModels.add(target.dataset.modelId);
      }
      state.artifactViewer = null;
      renderCaseConsole();
      renderResultsModelSummary();
      return;
    }

    const artifactPath = target.dataset.viewArtifact;
    if (artifactPath) {
      viewArtifact(artifactPath, target.dataset.artifactName || "", target.dataset.caseKey || "").catch(showError);
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

function formatRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatOptionalRate(value) {
  return value === null || value === undefined ? "n/a" : formatRate(value);
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
