# Backend Development Guidelines

> Backend conventions for the RTL benchmark pipeline.

---

## Overview

This repository is a Python backend-first project with a small static web UI.
There is no web framework, ORM, queue, or database server. The core architecture is:

- `argparse` CLI entrypoints in `src/rtl_benchmark/cli.py`
- orchestration in `src/rtl_benchmark/pipeline.py`
- provider discovery and generation in `src/rtl_benchmark/model_sources.py` and `src/rtl_benchmark/model_runner.py`
- HDL evaluation and artifact generation in `src/rtl_benchmark/evaluator.py`
- filesystem-backed persistence in JSON files under `benchmarks/`, `results/`, `data/`, and `.state/`
- leaderboard and result reshaping in `src/rtl_benchmark/leaderboard.py`, `src/rtl_benchmark/scoring.py`, and `src/rtl_benchmark/webapp.py`

Design for deterministic local execution first. Network access is optional and isolated to model discovery and inference providers.

---

## Guidelines Index

| Guide | Description | Project Reality |
|-------|-------------|-----------------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | Single Python package plus static web assets |
| [Database Guidelines](./database-guidelines.md) | Persistence and state management | Filesystem JSON state, no SQL database |
| [Error Handling](./error-handling.md) | Error types and propagation | `ValueError`, `StageStatus`, and explicit exit codes |
| [Quality Guidelines](./quality-guidelines.md) | Code standards and review rules | Deterministic, test-backed, benchmark-safe changes |
| [Logging Guidelines](./logging-guidelines.md) | Logging and diagnostics | Artifact logs plus concise CLI/web responses |

---

## Working Rules

1. Keep orchestration near the entrypoint. `cli.py` and `webapp.py` assemble configs and call focused modules; they should not absorb evaluator or provider internals.
2. Prefer `Path`, dataclasses, and JSON helpers over ad hoc string handling. See `src/rtl_benchmark/utils.py` and `src/rtl_benchmark/types.py`.
3. Treat benchmark metadata as part of the contract. Fields like `source`, `suite`, `track`, `difficulty`, and `tags` flow into scoring and UI views.
4. Preserve reproducibility. New behavior should remain testable without remote services unless the feature is explicitly provider-specific.
5. Favor tolerant readers and explicit writers. Many result files are reopened later by the leaderboard and web UI, so schema additions should be additive.

---

## Canonical Examples

- CLI boundary and exit-code handling: `src/rtl_benchmark/cli.py`
- Run orchestration and persistence: `src/rtl_benchmark/pipeline.py`
- Problem normalization and validation: `src/rtl_benchmark/problem_bank.py`
- External tool execution and case artifacts: `src/rtl_benchmark/evaluator.py`
- Long-lived state reconstruction from raw runs: `src/rtl_benchmark/leaderboard.py`
- Incremental run snapshots and HTTP error mapping: `src/rtl_benchmark/webapp.py`

---

**Language**: Keep Trellis docs in English and describe current repository behavior, not aspirational patterns.
