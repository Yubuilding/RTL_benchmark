# Error Handling

> How errors are handled in this project.

---

## Overview

Error handling depends on the boundary:

- invalid local inputs or malformed benchmark content: raise `ValueError`
- external tool and subprocess outcomes: return `StageStatus` instead of raising
- expected CLI user mistakes: print a clear message and return a non-zero exit code
- web request validation issues: return JSON `400` or `404`
- long-running web jobs: catch exceptions, persist a failed snapshot, and attach the partial result when available

Avoid crashing the whole process for an expected single-case failure. Most of the benchmark loop is designed to continue and record what failed.

---

## Error Types

### `ValueError`

Use for invalid repository data, invalid arguments, or unsupported local state.

Examples:

- `src/rtl_benchmark/problem_bank.py` raises `ValueError` for missing required benchmark fields or unsupported `task_type`.
- `src/rtl_benchmark/importers.py` raises `ValueError` for missing source directories, missing reference RTL, or overwrite attempts without `--overwrite`.
- `src/rtl_benchmark/webapp.py` raises `ValueError` from `compare_models()` and `_run_job()` when request parameters are invalid.

### `StageStatus`

Use for evaluator and subprocess stages where failure is a normal result to capture, not a control-flow exception.

Examples:

- `src/rtl_benchmark/evaluator.py::run_cmd()` returns `StageStatus(status="fail" | "skipped" | "pass")`.
- Docker preflight errors become `StageStatus(status="skipped", reason=...)` instead of raising.
- Invalid HDL candidates return a skipped-result `CaseResult` with explanatory feedback.

### `RunExecutionError`

Use only when the web job should fail as a whole but a partial run snapshot should survive.

Example:

- `src/rtl_benchmark/webapp.py::_execute_run()` catches a mid-run exception, persists failed output, and re-raises `RunExecutionError(partial_result=...)`.

---

## Error Handling Patterns

### Validate early at the boundary

- Validate problem files during load, not later inside scoring or UI code.
- Validate import paths before any write.
- Validate request parameters before doing expensive run work.

### Convert external failures into structured state

- Provider HTTP failures in `src/rtl_benchmark/model_runner.py` set `last_error` and return `""`; the pipeline converts that into a generation-failed `CaseResult`.
- Tool failures in `src/rtl_benchmark/evaluator.py` become `StageStatus` objects plus artifact log files.
- Missing local tools are generally marked `skipped`, not fatal to the entire process.

### Keep feedback actionable

- Prefer explicit reasons such as `missing tools: verilator, yosys` or `candidate is missing endmodule`.
- Preserve stderr/stdout tails in `StageStatus` for debugging.
- Use trimmed user-facing feedback strings rather than dumping full logs to stdout.

### Let the run continue when possible

- One model/problem attempt can fail without aborting the entire benchmark suite.
- The web job persists snapshots after each attempt so partial progress is recoverable.

---

## API Error Responses

The web UI returns simple JSON error payloads, not a rich problem-details schema.

Patterns in `src/rtl_benchmark/webapp.py`:

- bad request: `{"error": "<message>"}` with `HTTPStatus.BAD_REQUEST`
- missing resource: `{"error": "run not found"}` or `{"error": "artifact not found"}` with `404`
- successful responses carry domain payloads, not wrapper envelopes except where the route already uses `{ "ok": true, ... }`

Do not introduce a second response style for one endpoint unless there is a strong compatibility reason.

---

## Common Mistakes

- Raising generic exceptions for expected benchmark validation problems instead of `ValueError`.
- Swallowing provider or subprocess errors without storing enough context for feedback or artifacts.
- Returning inconsistent HTTP shapes across endpoints.
- Throwing hard failures for missing local EDA tools when the established behavior is to mark stages as `skipped` and still record the case.
