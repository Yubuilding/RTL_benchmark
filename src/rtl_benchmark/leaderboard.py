from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rtl_benchmark.scoring import compute_scored_run
from rtl_benchmark.utils import load_json, now_utc_iso, save_json


LEADERBOARD_SCOPES = {"suite", "selected_problems"}


def _empty_leaderboard() -> dict:
    return {"updated_at": "", "models": [], "slice_rankings": {}, "scoring_policy": {}}


def scope_updates_leaderboard(scope: str, custom_problem: bool = False) -> bool:
    return not custom_problem and str(scope or "suite") in LEADERBOARD_SCOPES


def _run_sort_key(started_at: str, run_id: str) -> tuple[str, str]:
    return started_at or "", run_id


def summarize_cases(
    cases: list[dict],
    problems: list[dict] | None = None,
    scoring_config: dict | None = None,
) -> list[dict]:
    return compute_scored_run(cases, problems=problems, scoring_config=scoring_config)["summary"]


def build_suite_leaderboard(
    cases: list[dict],
    problems: list[dict] | None = None,
    scoring_config: dict | None = None,
) -> dict:
    return compute_scored_run(cases, problems=problems, scoring_config=scoring_config)


def update_leaderboard(
    leaderboard_path: str,
    run_id: str,
    rows: list[dict],
    scope: str = "suite",
    problem_ids: list[str] | None = None,
    slice_rankings: dict | None = None,
    scoring_policy: dict | None = None,
    raw_results_dir: str = "",
    reset_after: str = "",
    custom_problem: bool = False,
) -> dict:
    if raw_results_dir:
        return rebuild_leaderboard_from_raw_results(leaderboard_path, raw_results_dir, reset_after=reset_after)

    if not scope_updates_leaderboard(scope, custom_problem=custom_problem):
        return load_json(leaderboard_path, default=_empty_leaderboard())

    board = load_json(leaderboard_path, default=_empty_leaderboard())

    index = {m["model_id"]: m for m in board.get("models", [])}
    for row in rows:
        current = index.get(row["model_id"], {})
        row["runs"] = int(current.get("runs", 0)) + 1
        row["last_run_id"] = run_id
        row["last_scope"] = scope
        row["last_problem_count"] = len(problem_ids or [])
        row["updated_at"] = now_utc_iso()
        index[row["model_id"]] = row

    merged = list(index.values())
    merged.sort(
        key=lambda x: (
            -float(x.get("score", 0.0)),
            -float(x.get("quality_score", 0.0)),
            -float(x.get("sim_pass_rate") or 0.0),
            str(x.get("model_id", "")),
        )
    )

    result = {
        "updated_at": now_utc_iso(),
        "models": merged,
        "slice_rankings": derive_slice_rankings_from_models(merged),
        "scoring_policy": scoring_policy or board.get("scoring_policy", {}),
    }
    save_json(leaderboard_path, result)
    return result


def rebuild_leaderboard_from_raw_results(
    leaderboard_path: str,
    raw_results_dir: str,
    reset_after: str = "",
) -> dict:
    board = _empty_leaderboard()
    raw_dir = Path(raw_results_dir)
    runs: list[dict] = []
    for path in raw_dir.glob("*.json"):
        data = load_json(path, default={})
        started_at = str(data.get("started_at", ""))
        scope = str(data.get("scope", "suite"))
        if not scope_updates_leaderboard(scope, custom_problem=bool(data.get("custom_problem", False))):
            continue
        if reset_after and started_at and started_at < reset_after:
            continue
        cases = list(data.get("cases", []))
        if not cases:
            continue
        scored = build_suite_leaderboard(
            cases=cases,
            problems=list(data.get("problems", [])),
            scoring_config=dict(data.get("scoring_policy", {})),
        )
        runs.append(
            {
                "run_id": str(data.get("run_id", path.stem)),
                "started_at": started_at,
                "scope": scope,
                "problem_ids": list(data.get("problem_ids", [])),
                "final_cases": scored["final_cases"],
                "scoring_policy": scored["scoring_policy"],
            }
        )

    runs.sort(key=lambda item: _run_sort_key(str(item.get("started_at", "")), str(item.get("run_id", ""))))

    final_cases_by_problem: dict[tuple[str, str], dict] = {}
    model_run_counts: dict[str, int] = defaultdict(int)
    model_last_meta: dict[str, dict] = {}
    scoring_policy: dict = {}

    for item in runs:
        seen_models: set[str] = set()
        for case in item.get("final_cases", []):
            model_id = str(case.get("model_id", "")).strip()
            problem_id = str(case.get("problem_id", "")).strip()
            if not model_id or not problem_id:
                continue
            final_cases_by_problem[(model_id, problem_id)] = dict(case)
            seen_models.add(model_id)

        for model_id in seen_models:
            model_run_counts[model_id] += 1
            current = model_last_meta.get(model_id)
            candidate = {
                "run_id": item["run_id"],
                "scope": item["scope"],
                "problem_count": len(item.get("problem_ids", [])),
                "started_at": item.get("started_at", ""),
            }
            if current is None or _run_sort_key(candidate["started_at"], candidate["run_id"]) >= _run_sort_key(
                current.get("started_at", ""),
                current.get("run_id", ""),
            ):
                model_last_meta[model_id] = candidate

        scoring_policy = item.get("scoring_policy", {}) or scoring_policy

    merged_cases = sorted(
        final_cases_by_problem.values(),
        key=lambda case: (str(case.get("model_id", "")), str(case.get("problem_id", ""))),
    )

    if merged_cases:
        scored = build_suite_leaderboard(cases=merged_cases, scoring_config=scoring_policy)
        models = list(scored["summary"])
        for row in models:
            model_id = str(row.get("model_id", ""))
            meta = model_last_meta.get(model_id, {})
            row["runs"] = int(model_run_counts.get(model_id, 0))
            row["last_run_id"] = str(meta.get("run_id", ""))
            row["last_scope"] = str(meta.get("scope", ""))
            row["last_problem_count"] = int(meta.get("problem_count", 0) or 0)
            row["updated_at"] = str(meta.get("started_at", "")) or now_utc_iso()

        board = {
            "updated_at": runs[-1]["started_at"] if runs and runs[-1].get("started_at") else "",
            "models": models,
            "slice_rankings": scored["slice_rankings"],
            "scoring_policy": scored["scoring_policy"],
        }

    current = load_json(leaderboard_path, default=_empty_leaderboard())
    if current != board:
        save_json(leaderboard_path, board)
    return board


def derive_slice_rankings_from_models(models: list[dict]) -> dict:
    groups = {"sources": {}, "tracks": {}, "difficulties": {}, "tags": {}}
    for model in models:
        for group in groups:
            for entry in model.get("breakdowns", {}).get(group, []):
                value = str(entry.get("value", ""))
                if not value:
                    continue
                groups[group].setdefault(value, []).append(
                    {
                        "model_id": model.get("model_id", ""),
                        "provider": model.get("provider", ""),
                        "score": entry.get("slice_weighted_pass_rate", 0.0),
                        "weighted_pass_score": entry.get("slice_weighted_pass_rate", 0.0),
                        "quality_score": entry.get("slice_quality_score", 0.0),
                        "cases": entry.get("cases", 0),
                        "global_weight_mass": entry.get("global_weight_mass", 0.0),
                        "label": entry.get("label", ""),
                        "value": value,
                    }
                )
    for group, values in groups.items():
        for value, rows in values.items():
            rows.sort(
                key=lambda row: (
                    -float(row.get("weighted_pass_score", 0.0)),
                    -float(row.get("quality_score", 0.0)),
                    str(row.get("model_id", "")),
                )
            )
    return groups
