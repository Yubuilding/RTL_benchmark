# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

Quality in this repository means benchmark integrity, deterministic behavior, and inspectable results.
The code is intentionally lightweight, but changes still need to preserve:

- reproducibility
- provenance of benchmark content
- recoverable run artifacts
- stable JSON contracts for leaderboard and UI consumers
- test coverage for behavior changes

Follow `CONTRIBUTING.md` in addition to the rules below.

---

## Forbidden Patterns

- Silent benchmark-schema drift.
  If you change benchmark JSON fields or run-result payloads, update readers and tests in the same patch.
- Hidden network dependence in tests.
  Tests should use local fixtures, `tempfile`, and `unittest.mock`, as in `tests/test_core.py` and `tests/test_webapp.py`.
- Provider-specific logic spread across unrelated modules.
  Keep discovery in `model_sources.py` and generation in `model_runner.py`.
- Stringly-typed path assembly when `Path` is available.
- Throwing away tool output that is needed for failure diagnosis.
- Logging or persisting secrets from provider config or environment variables.

### Usually avoid

- broad `except Exception` without either re-raising, persisting context, or converting to a structured error state
- one-off JSON read/write code instead of `load_json()` and `save_json()`
- large rewrites when a small focused patch matches existing style

---

## Required Patterns

### Data and persistence

- Use `dataclass` types from `src/rtl_benchmark/types.py` for shared domain structures.
- Use `load_json()`, `save_json()`, `ensure_dir()`, and UTC helper functions from `src/rtl_benchmark/utils.py`.
- Preserve deterministic run directory layout and artifact naming.

### Error handling and observability

- Convert expected external failures into `StageStatus`, feedback strings, or structured job errors.
- Keep enough metadata to rebuild state from `results/raw/*.json`.
- Store full tool logs on disk when subprocesses are involved.

### Documentation

- Update `README.md` or config docs when CLI behavior, config shape, or UI workflow changes.
- Document provenance and licensing expectations for new benchmark content.

### Scope control

- Keep patches focused. Infra, benchmark content, UI, and docs should be separated when practical.
- Prefer extending current patterns over introducing new frameworks.

---

## Testing Requirements

Behavior changes require tests or a clear reason why a test is not practical.

### Normal expectation

- Add or update `unittest` coverage under `tests/`.
- Use `tempfile.TemporaryDirectory()` for filesystem-backed behavior.
- Use `unittest.mock.patch` for provider/network behavior.
- Keep tests deterministic and offline.

### What should be covered

- benchmark loading, schema defaults, and filters:
  see `tests/test_core.py`
- importer behavior and overwrite protections:
  see `tests/test_core.py`
- evaluator status mapping and persisted outputs when behavior changes:
  add tests around `StageStatus` outcomes
- webapp request/state logic, leaderboard rebuilds, or comparison behavior:
  see `tests/test_webapp.py`

### When docs are enough

Pure documentation-only edits do not need test runs, but they should stay accurate to the current codebase.

---

## Code Review Checklist

Reviewers should check:

- Does the change preserve benchmark reproducibility and integrity?
- Are new JSON fields additive and backward-tolerant?
- Is the code in the right module, or is responsibility leaking across boundaries?
- Are artifact paths, run summaries, and leaderboard behavior still coherent?
- Are provider keys or other secrets protected?
- Are tests updated for changed behavior?
- Is user-facing behavior documented in `README.md` or another appropriate file?
