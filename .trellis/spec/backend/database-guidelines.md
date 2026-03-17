# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

This project does not use a relational database, ORM, or migration tool.
Persistence is filesystem-based and uses JSON plus per-run artifact directories.

Treat these files as the persistence layer:

- benchmark definitions in `benchmarks/**/*.json`
- imported benchmark catalogs in `data/problem_catalogs/*.json`
- pipeline and UI config in `configs/*.json` and `.state/*.json`
- run history in `results/raw/*.json`
- leaderboard materialization in `results/leaderboard.json`
- evaluator artifacts in `results/runs/<run_id>/...`

---

## Query Patterns

### Read whole documents, then normalize in Python

- Use `load_json()` from `src/rtl_benchmark/utils.py` for JSON reads.
- Convert raw payloads into typed or normalized in-memory structures before downstream use.
- Examples:
  - `src/rtl_benchmark/problem_bank.py` turns benchmark JSON into `Problem` dataclasses.
  - `src/rtl_benchmark/webapp.py` reads raw result JSON, then enriches and re-scores it in `_enrich_history_detail()`.
  - `src/rtl_benchmark/leaderboard.py` rebuilds the leaderboard by reading every raw run and merging final cases in memory.

### Write through helpers and stable directory layouts

- Use `ensure_dir()` before writing directories.
- Use `save_json()` for persisted JSON so formatting stays consistent.
- Keep run artifacts under the existing hierarchy:
  `results/runs/<run_id>/<safe_model_id>/<problem_id>/attempt_<n>/`
- Examples:
  - `src/rtl_benchmark/pipeline.py` writes raw run records and API traces.
  - `src/rtl_benchmark/evaluator.py` writes case-local HDL files and logs.
  - `src/rtl_benchmark/webapp.py` persists incremental snapshots for in-progress jobs.

---

## Migrations

There are no formal migrations. Schema evolution is handled by additive JSON changes and tolerant readers.

Rules:

1. Prefer adding fields over renaming or removing fields.
2. When reading old files, always use `.get(..., default)` and reconstruct missing derived data where possible.
3. If a new field becomes required for correctness, update both:
   - the writer that emits new data
   - the reader that loads historical data
4. Add or update tests that cover historical or partial payloads.

Examples:

- `src/rtl_benchmark/problem_bank.py` infers `suite`, `track`, `difficulty`, `prompt_style`, `harness_type`, and `exposure` when the JSON file does not carry them.
- `src/rtl_benchmark/webapp.py` backfills missing `problems`, `artifact_dir`, and `artifacts` when opening older run records.
- `src/rtl_benchmark/leaderboard.py` rebuilds from raw runs rather than assuming `results/leaderboard.json` is the source of truth.

---

## Naming Conventions

For persisted JSON:

- Use `snake_case` keys.
- Use UTC timestamps in ISO 8601 with `Z` suffix via `now_utc_iso()`.
- Use path-like identifiers only where they are already established, such as `artifact_dir`, `run_root`, and `leaderboard_path`.
- Keep problem metadata keys aligned with the `Problem` dataclass: `source`, `suite`, `category`, `track`, `difficulty`, `tags`.
- Keep result metadata keys aligned with `CaseResult` and run summaries: `model_id`, `problem_id`, `attempt`, `passed`, `summary`, `slice_rankings`, `scoring_policy`.

---

## Common Mistakes

- Writing a new JSON file shape without updating the historical reader in `webapp.py` or `leaderboard.py`.
- Using string concatenation for paths instead of `Path`, which breaks absolute/relative handling.
- Overwriting imported benchmark files by default. Existing importer behavior refuses to overwrite unless `--overwrite` is passed.
- Treating `results/leaderboard.json` as authoritative; the long-term source of truth is `results/raw/*.json`.
