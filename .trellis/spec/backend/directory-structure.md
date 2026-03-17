# Directory Structure

> How backend code is organized in this project.

---

## Overview

Backend code lives in one Python package: `src/rtl_benchmark/`.
The package is organized by responsibility, not by layer folders:

- command surface: `cli.py`
- orchestration: `pipeline.py`, `webapp.py`
- data loading and normalization: `problem_bank.py`, `importers.py`
- provider integration: `model_sources.py`, `model_runner.py`
- execution and grading: `evaluator.py`
- result aggregation: `leaderboard.py`, `scoring.py`
- shared primitives: `types.py`, `utils.py`

Static frontend assets are bundled under `src/rtl_benchmark/webui/`, but the backend still owns serving and API contracts through `webapp.py`.

---

## Directory Layout

```
src/
└── rtl_benchmark/
    ├── cli.py
    ├── evaluator.py
    ├── importers.py
    ├── leaderboard.py
    ├── model_runner.py
    ├── model_sources.py
    ├── pipeline.py
    ├── problem_bank.py
    ├── scoring.py
    ├── types.py
    ├── utils.py
    ├── webapp.py
    └── webui/

benchmarks/                # Benchmark case JSON files
configs/                   # Pipeline config presets
data/problem_catalogs/     # External benchmark catalog metadata
results/                   # Leaderboard, raw run data, evaluator artifacts
tests/                     # unittest coverage for core and web paths
```

---

## Module Organization

### Put new code where the primary responsibility already exists

- Add CLI flags and subcommands in `src/rtl_benchmark/cli.py`, but keep the command body thin.
- Add benchmark loading, schema inference, or filtering rules in `src/rtl_benchmark/problem_bank.py`.
- Add repo-to-benchmark conversion logic in `src/rtl_benchmark/importers.py`.
- Add provider listing logic in `src/rtl_benchmark/model_sources.py`.
- Add provider generation request/response handling in `src/rtl_benchmark/model_runner.py`.
- Add simulator, lint, synthesis, Docker, or artifact behavior in `src/rtl_benchmark/evaluator.py`.
- Add scoring aggregation or leaderboard rebuild rules in `src/rtl_benchmark/scoring.py` or `src/rtl_benchmark/leaderboard.py`.
- Add HTTP endpoints, job state, UI-config persistence, and run snapshots in `src/rtl_benchmark/webapp.py`.

### Keep shared primitives centralized

- Shared dataclasses belong in `src/rtl_benchmark/types.py`.
- Small reusable helpers belong in `src/rtl_benchmark/utils.py`.
- Do not create one-off helpers inside CLI or web handlers when the same logic is useful to pipeline or tests.

### Keep data and assets out of code modules

- Benchmark definitions belong under `benchmarks/**/*.json`, not hardcoded into Python.
- User-editable config presets belong under `configs/`.
- Static HTML/CSS/JS assets belong under `src/rtl_benchmark/webui/`.

---

## Naming Conventions

### Files and modules

- Use `snake_case.py` module names.
- Name modules after a responsibility, not a generic layer name. Existing examples: `problem_bank.py`, `model_runner.py`, `leaderboard.py`.
- Keep top-level package flat until a true subpackage is justified by size or ownership.

### Functions and data

- Use verb-first helpers for actions: `load_problems`, `discover_models`, `update_leaderboard`, `serve_webapp`.
- Use `Path` for filesystem values and convert to `str` only at serialization or subprocess boundaries.
- Use explicit JSON field names that match current outputs: `run_id`, `problem_id`, `artifact_dir`, `raw_results_dir`, `leaderboard_path`.

---

## Examples

- Good CLI/orchestrator split:
  `src/rtl_benchmark/cli.py` calls `BenchmarkPipeline`, `Evaluator`, importer helpers, and `serve_webapp` instead of embedding that logic.
- Good normalization boundary:
  `src/rtl_benchmark/problem_bank.py` converts raw JSON into a `Problem` dataclass and fills inferred metadata in one place.
- Good artifact ownership:
  `src/rtl_benchmark/evaluator.py` owns per-case files, logs, wave dumps, and Docker command wrapping.
- Good backend plus UI contract module:
  `src/rtl_benchmark/webapp.py` keeps request parsing, run snapshots, and leaderboard rebuild behavior together.

### Common placement mistakes

- Do not add provider-specific logic to `pipeline.py`; put it in `model_sources.py` or `model_runner.py`.
- Do not write raw JSON directly from many modules when `save_json()` already exists.
- Do not put benchmark schema inference into tests or scripts; keep it in `problem_bank.py` or importer helpers.
