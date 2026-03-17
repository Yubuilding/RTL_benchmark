# Journal - gary (Part 1)

> AI development session journal
> Started: 2026-03-17

---

## Session 1: Bootstrap Trellis project docs

**Date**: 2026-03-17
**Task**: 00-bootstrap-guidelines

### Summary

Replaced Trellis boilerplate with repository-specific backend guidelines and initialized the workspace journal/index entries.

### Main Changes

- Filled `.trellis/spec/backend/*.md` with conventions for the RTL benchmark pipeline.
- Customized thinking guides for this codebase's pipeline, webapp, artifact, and raw-result flows.
- Initialized workspace records in `.trellis/workspace/index.md` and `.trellis/workspace/gary/index.md`.
- Recorded this session so future Trellis runs have non-empty workspace context.

### Git Commits

| Hash | Message |
|------|---------|
| `none` | not committed |

### Testing

- [OK] Documentation checked against current code in `src/rtl_benchmark/` and `tests/`

### Status

[OK] **Completed**

### Next Steps

- Keep `.trellis/spec/` updated when run-result JSON contracts or module boundaries change.
- Add more project-specific frontend notes if the bundled web UI grows materially.

---

## Session 2: Persist Failed Jobs And Resume Suite Runs

**Date**: 2026-03-17
**Task**: 03-17-partial-suite-results-resume

### Summary

Added persistent web job storage so failed benchmark jobs survive server restarts, completed cases from failed runs continue to feed the leaderboard, and users can resume or delete failed jobs from the UI.

### Main Changes

- Persisted web jobs to `.state/web_jobs.json` and reconciled orphaned queued/running jobs into failed jobs after restart.
- Reused the original `run_id` and raw snapshot when resuming a failed job, skipping already completed `model_id + problem_id` pairs.
- Added delete-job cleanup for raw snapshots and run artifacts, followed by leaderboard rebuild.
- Added web UI actions for `Continue Run` and `Delete Job`.
- Added regression tests for restart recovery, same-run resume, and delete-job leaderboard cleanup.

### Git Commits

| Hash | Message |
|------|---------|
| `none` | not committed |

### Testing

- [OK] `PYTHONPATH=src python3 -m unittest tests.test_webapp`
- [OK] `PYTHONPATH=src python3 -m unittest tests.test_core`

### Status

[OK] **Completed**

### Next Steps

- Manual UI smoke test on a real failed suite run would validate the new resume/delete interactions end to end.

---

## Session 3: Add Run-Time Model Selection And Fix VerilogEval Reference Modules

**Date**: 2026-03-17
**Task**: 03-17-run-model-selection-verilogeval-fix

### Summary

Separated benchmark-time model selection from config-time provider setup, removed web job credential sharing through global environment variables, and fixed VerilogEval RTL evaluation so `RefModule` is available during lint/simulation.

### Main Changes

- Preserved VerilogEval reference RTL module names during import while keeping `module_header` aligned to the requested DUT module.
- Updated the evaluator to emit a reference RTL file when the testbench instantiates an external golden module such as `RefModule`.
- Added run-time model selection on the benchmark page and passed selected models through the webapp job request path.
- Attached provider API keys to per-job runtime model descriptors instead of mutating global environment variables, while keeping saved job/run metadata secret-free.
- Added regression tests for VerilogEval reference-module handling, inline model-scoped API keys, and run-time model filtering.

### Git Commits

| Hash | Message |
|------|---------|
| `none` | not committed |

### Testing

- [OK] `PYTHONPATH=src python3 -m unittest tests.test_core tests.test_webapp`

### Status

[OK] **Completed**

### Next Steps

- Run a manual web UI smoke test with two concurrent jobs that select different models from the same configured provider list.
- Consider rejecting same-id cross-provider selections earlier in the UI if users frequently benchmark mirrored model ids from multiple providers.

---

## Session 4: Add Pausable Jobs, Progress Bars, And Compact Leaderboard Breakdown UI

**Date**: 2026-03-17
**Task**: 03-17-pausable-jobs-progress-breakdown-ui

### Summary

Added cooperative pause support for web benchmark jobs, exposed structured case-progress in the jobs panel, and redesigned leaderboard tag/breakdown rendering so the table is denser and easier to scan.

### Main Changes

- Added `pause_requested` and structured progress counters to persisted web job state, plus a `POST /api/jobs/<id>/pause` control path.
- Updated run execution so a pause request is honored at a safe checkpoint and the run snapshot is persisted with status `paused`.
- Added progress bars and pause/continue controls to the jobs list UI.
- Reworked leaderboard top tags into compact chips and replaced the old long-form breakdown text blocks with grouped metric cards.
- Added regression coverage for paused runs and persisted progress accounting.

### Git Commits

| Hash | Message |
|------|---------|
| `none` | not committed |

### Testing

- [OK] `PYTHONPATH=src python3 -m unittest tests.test_webapp tests.test_core`

### Status

[OK] **Completed**

### Next Steps

- Manual browser verification would confirm the new pause action timing against a real long-running provider call.
- If users need hard-stop semantics later, that should be a separate action from cooperative pause because the failure modes are different.

---

## Session 5: Add ETA Telemetry For Live Benchmark Jobs

**Date**: 2026-03-17
**Task**: 03-17-pausable-jobs-progress-breakdown-ui

### Summary

Extended live web jobs with ETA estimates driven by provider latency traces, normalized token usage, and per-problem complexity heuristics, then surfaced that ETA in the jobs panel.

### Main Changes

- Added request timing and normalized token-usage metrics to `ModelRunner.last_trace`, including provider-native usage fields and fallback estimated completion tokens.
- Added persisted `eta_state` plus progress-level ETA snapshots to web job state so pause/resume and restarts keep learned pacing data.
- Estimated remaining time from observed generation/evaluation latency, token throughput, and problem complexity units derived from difficulty, prompt size, harness size, and mutation load.
- Rendered ETA, confidence, and basis text in the jobs panel without changing the existing progress-bar layout.
- Added regression coverage for trace metrics and ETA estimation.

### Git Commits

| Hash | Message |
|------|---------|
| `none` | not committed |

### Testing

- [OK] `PYTHONPATH=src python3 -m unittest tests.test_webapp tests.test_core`

### Status

[OK] **Completed**

### Next Steps

- Manual browser verification should confirm ETA stability against real provider latency rather than mocked instant responses.
- If ETA drift becomes noticeable on very heterogeneous suites, consider learning per-suite or per-track calibration factors instead of a single global complexity fit.
