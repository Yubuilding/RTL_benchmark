# Logging Guidelines

> How logging is done in this project.

---

## Overview

This project does not use a centralized Python `logging` configuration today.
Diagnostics are split across three channels:

- concise human-readable CLI output via `print()` in `src/rtl_benchmark/cli.py`
- per-tool artifact logs written to the case directory by `src/rtl_benchmark/evaluator.py::run_cmd()`
- persisted JSON state such as raw runs, leaderboard snapshots, and API traces

The standard is to record enough structured state to debug benchmark outcomes without turning normal CLI usage into a log flood.

---

## Log Levels

There is no formal runtime log-level system yet. Use these equivalents:

- user-facing summary:
  short `print()` lines in CLI commands and `serve_webapp()`
- diagnostic artifacts:
  full subprocess stdout/stderr in evaluator log files
- failure state:
  `error` fields in run/job JSON payloads
- deep provider diagnostics:
  `api_trace.json` files per case when generation was attempted

If a future change adds the `logging` module, keep the same separation: compact console output, detailed artifact logs, and structured persisted state.

---

## Structured Logging

### Canonical structured outputs

- `results/raw/<run_id>.json`
- `results/leaderboard.json`
- `.state/webui_config.json`
- `.state/leaderboard_state.json`
- per-case `api_trace.json`
- per-case tool logs such as `lint.log`, `simv_compile.log`, `simv_run.log`, and `synth.log`

### Required fields for persisted run state

When touching run persistence, preserve the current shape built in `pipeline.py` and `webapp.py`:

- `run_id`, `started_at`, `finished_at`
- `source`, `scope`, `status`, `error`
- `models`, `cases`, `summary`
- `problem_ids`, `problems`
- `slice_rankings`, `scoring_policy`
- `run_root`

### Formatting rules

- Use UTC timestamps from `now_utc_iso()`.
- Keep JSON pretty-printed with `save_json()`.
- Write full subprocess output to artifact files, but trim `stdout` and `stderr` in `StageStatus` for in-memory payloads.

---

## What to Log

Log or persist the following:

- command-level summaries for CLI commands
- exact tool command lines and full tool output in evaluator log files
- API request/response traces needed to debug provider integration behavior
- run lifecycle state for web jobs, including partial failures
- leaderboard rebuild outputs derived from raw runs
- enough metadata to reconstruct where a case came from: model, problem, attempt, provider, artifact directory

Examples:

- `src/rtl_benchmark/evaluator.py::run_cmd()` writes the exact command, stdout, and stderr to disk.
- `src/rtl_benchmark/pipeline.py::_persist_api_trace()` stores per-attempt provider traces.
- `src/rtl_benchmark/webapp.py::_update_job()` and `_persist_run_snapshot()` keep job and run progress observable.

---

## What NOT to Log

- Raw API keys from the UI config or environment.
- Entire environment dumps.
- Large HDL payloads in general-purpose console output.
- Duplicate copies of the same large log content in both JSON state and artifact files.
- Request headers containing credentials. Provider traces should be careful about secrets if expanded in the future.

Notes:

- `webapp.py` temporarily injects provider keys into environment variables for a run. Do not add debug prints around those values.
- HTTP request failures may store descriptive error bodies, but scrub any future trace fields that could echo credentials.
