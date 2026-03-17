from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any


DEFAULT_SCORING_POLICY: dict[str, Any] = {
    "rank_mode": "weighted_pass",
    "source_budget_mode": "equal",
    "difficulty_weights": {"easy": 1.0, "medium": 1.5, "hard": 2.5, "adhoc": 1.0},
    "track_weights": {
        "rtl_core": 1.0,
        "arithmetic": 1.05,
        "memory": 1.1,
        "control": 1.2,
        "protocol": 1.2,
        "verification": 1.1,
    },
    "suite_weights": {},
    "exposure_weights": {},
    "tag_weights": {},
    "quality_stage_weights": {
        "rtl": {"lint": 0.15, "simulation": 0.65, "synthesis": 0.20},
        "testbench": {"lint": 0.10, "simulation": 0.40, "mutation": 0.50},
    },
    "skip_policy": "renormalize_executed_stages",
    "profile_min_cases": 3,
    "profile_min_global_weight": 0.03,
    "top_tags_limit": 25,
    "highlights_per_model": 3,
}

SLICE_DIMENSIONS = ("source", "track", "difficulty", "category", "tag")
LEADERBOARD_SLICE_DIMENSIONS = ("source", "track", "difficulty", "tag")
DIMENSION_PLURALS = {
    "source": "sources",
    "track": "tracks",
    "difficulty": "difficulties",
    "category": "categories",
    "tag": "tags",
}


def normalize_scoring_config(scoring_config: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_SCORING_POLICY)
    if not scoring_config:
        return normalized
    _deep_merge(normalized, scoring_config)
    return normalized


def summarize_cases(
    cases: list[dict[str, Any]],
    problems: list[dict[str, Any]] | None = None,
    scoring_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return compute_scored_run(cases, problems=problems, scoring_config=scoring_config)["summary"]


def compute_scored_run(
    cases: list[dict[str, Any]],
    problems: list[dict[str, Any]] | None = None,
    scoring_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring_policy = normalize_scoring_config(scoring_config)
    problem_index = _build_problem_index(cases, problems or [])
    tag_keep = _select_top_tags(problem_index.values(), int(scoring_policy.get("top_tags_limit", 25)))
    problem_weights = _build_problem_weights(problem_index, scoring_policy)

    annotated_cases = [
        _annotate_case(dict(case), problem_index.get(str(case.get("problem_id", "")).strip(), {}), problem_weights, scoring_policy, tag_keep)
        for case in cases
    ]
    final_cases = _select_final_cases(annotated_cases)

    grouped_cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in final_cases:
        grouped_cases[str(case.get("model_id", ""))].append(case)

    summary_rows: list[dict[str, Any]] = []
    slice_rows_by_dimension: dict[str, dict[str, list[dict[str, Any]]]] = {
        DIMENSION_PLURALS[dimension]: defaultdict(list) for dimension in LEADERBOARD_SLICE_DIMENSIONS
    }
    field_averages = _compute_field_averages(final_cases, tag_keep)

    for model_id, items in grouped_cases.items():
        row = _build_model_summary(model_id, items, scoring_policy, field_averages, tag_keep)
        summary_rows.append(row)
        for plural, entries in _build_slice_rankings_for_model(row).items():
            for value, slice_row in entries.items():
                slice_rows_by_dimension[plural][value].append(slice_row)

    summary_rows.sort(
        key=lambda row: (
            -float(row.get("score", 0.0)),
            -float(row.get("quality_score", 0.0)),
            -float(row.get("sim_pass_rate") or 0.0),
            str(row.get("model_id", "")),
        )
    )

    for idx, row in enumerate(summary_rows, start=1):
        row["rank"] = idx

    slice_rankings = _finalize_slice_rankings(slice_rows_by_dimension)
    return {
        "scoring_policy": scoring_policy,
        "cases": annotated_cases,
        "final_cases": final_cases,
        "summary": summary_rows,
        "slice_rankings": slice_rankings,
    }


def select_final_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _select_final_cases(cases)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)


def _build_problem_index(cases: list[dict[str, Any]], problems: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for problem in problems:
        problem_id = str(problem.get("id", "")).strip()
        if not problem_id:
            continue
        index[problem_id] = _normalize_problem_snapshot(problem)

    for case in cases:
        problem_id = str(case.get("problem_id", "")).strip()
        if not problem_id or problem_id in index:
            continue
        index[problem_id] = _normalize_problem_snapshot(
            {
                "id": problem_id,
                "task_type": case.get("task_type", ""),
                "source": case.get("problem_source", ""),
                "category": case.get("problem_category", ""),
                "suite": case.get("problem_suite", ""),
                "track": case.get("problem_track", ""),
                "difficulty": case.get("problem_difficulty", ""),
                "exposure": case.get("problem_exposure", ""),
                "tags": case.get("problem_tags", []),
            }
        )
    return index


def _normalize_problem_snapshot(problem: dict[str, Any]) -> dict[str, Any]:
    task_type = str(problem.get("task_type", "")).strip() or "rtl"
    return {
        "id": str(problem.get("id", "")).strip(),
        "task_type": task_type,
        "source": str(problem.get("source", "")).strip() or "local",
        "category": str(problem.get("category", "")).strip() or "uncategorized",
        "suite": str(problem.get("suite", "")).strip() or "custom",
        "track": str(problem.get("track", "")).strip() or ("verification" if task_type == "testbench" else "rtl_core"),
        "difficulty": str(problem.get("difficulty", "")).strip() or "easy",
        "exposure": str(problem.get("exposure", "")).strip() or "curated",
        "tags": [str(tag).strip() for tag in problem.get("tags", []) if str(tag).strip()],
    }


def _select_top_tags(problems: Any, limit: int) -> set[str]:
    counts: dict[str, int] = defaultdict(int)
    for problem in problems:
        for tag in problem.get("tags", []):
            counts[str(tag)] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {tag for tag, _ in ordered[: max(limit, 0)]}


def _build_problem_weights(problem_index: dict[str, dict[str, Any]], scoring_policy: dict[str, Any]) -> dict[str, float]:
    if not problem_index:
        return {}

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for problem in problem_index.values():
        grouped[str(problem.get("source", "local"))].append(problem)

    source_count = len(grouped) or 1
    source_budget = 1.0 / source_count
    weights: dict[str, float] = {}
    for source, problems in grouped.items():
        factors = {problem["id"]: _problem_factor(problem, scoring_policy) for problem in problems}
        total = sum(factors.values()) or float(len(problems) or 1)
        for problem in problems:
            problem_id = problem["id"]
            weights[problem_id] = round(source_budget * factors[problem_id] / total, 8)
    return weights


def _problem_factor(problem: dict[str, Any], scoring_policy: dict[str, Any]) -> float:
    difficulty = str(problem.get("difficulty", "easy"))
    track = str(problem.get("track", "rtl_core"))
    suite = str(problem.get("suite", "custom"))
    exposure = str(problem.get("exposure", "curated"))
    factor = 1.0
    factor *= float(scoring_policy.get("difficulty_weights", {}).get(difficulty, 1.0))
    factor *= float(scoring_policy.get("track_weights", {}).get(track, 1.0))
    factor *= float(scoring_policy.get("suite_weights", {}).get(suite, 1.0))
    factor *= float(scoring_policy.get("exposure_weights", {}).get(exposure, 1.0))
    for tag in problem.get("tags", []):
        if tag in scoring_policy.get("tag_weights", {}):
            factor *= float(scoring_policy["tag_weights"][tag])
    return factor


def _annotate_case(
    case: dict[str, Any],
    problem: dict[str, Any],
    problem_weights: dict[str, float],
    scoring_policy: dict[str, Any],
    tag_keep: set[str],
) -> dict[str, Any]:
    problem_id = str(case.get("problem_id", "")).strip()
    case["problem_source"] = str(problem.get("source", case.get("problem_source", "local")) or "local")
    case["problem_category"] = str(problem.get("category", case.get("problem_category", "uncategorized")) or "uncategorized")
    case["problem_suite"] = str(problem.get("suite", case.get("problem_suite", "custom")) or "custom")
    case["problem_track"] = str(problem.get("track", case.get("problem_track", "rtl_core")) or "rtl_core")
    case["problem_difficulty"] = str(problem.get("difficulty", case.get("problem_difficulty", "easy")) or "easy")
    tags = [tag for tag in problem.get("tags", []) if tag in tag_keep]
    case["problem_tags"] = tags
    case["problem_exposure"] = str(problem.get("exposure", case.get("problem_exposure", "")))
    case["problem_weight"] = round(float(problem_weights.get(problem_id, 0.0)), 8)
    case["pass_points"] = 1.0 if bool(case.get("passed")) else 0.0
    case["quality_points"] = round(_quality_points(case, problem, scoring_policy), 4)
    return case


def _quality_points(case: dict[str, Any], problem: dict[str, Any], scoring_policy: dict[str, Any]) -> float:
    task_type = str(problem.get("task_type") or case.get("task_type", "rtl"))
    if task_type == "testbench":
        weights = scoring_policy.get("quality_stage_weights", {}).get("testbench", {})
        values = {
            "lint": _stage_value(case.get("lint")),
            "simulation": _stage_value(case.get("simulation")),
            "mutation": _mutation_value(case.get("mutation_kill_rate")),
        }
    else:
        weights = scoring_policy.get("quality_stage_weights", {}).get("rtl", {})
        values = {
            "lint": _stage_value(case.get("lint")),
            "simulation": _stage_value(case.get("simulation")),
            "synthesis": _stage_value(case.get("synthesis")),
        }

    executed = {name: value for name, value in values.items() if value is not None}
    if not executed:
        return 0.0

    denominator = sum(float(weights.get(name, 0.0)) for name in executed)
    if denominator <= 0:
        denominator = float(len(executed))
        return sum(executed.values()) / denominator
    numerator = sum(float(weights.get(name, 0.0)) * float(value) for name, value in executed.items())
    return numerator / denominator


def _stage_value(stage: Any) -> float | None:
    status = str((stage or {}).get("status", "")).strip()
    if status == "pass":
        return 1.0
    if status == "fail":
        return 0.0
    return None


def _mutation_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _select_final_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    final: dict[tuple[str, str], dict[str, Any]] = {}
    for case in cases:
        model_id = str(case.get("model_id", "")).strip()
        problem_id = str(case.get("problem_id", "")).strip()
        if not model_id or not problem_id:
            continue
        key = (model_id, problem_id)
        attempt = int(case.get("attempt", 0) or 0)
        current = final.get(key)
        current_attempt = int(current.get("attempt", 0) or 0) if current else -1
        if current is None or attempt >= current_attempt:
            final[key] = case
    return sorted(final.values(), key=lambda case: (str(case.get("model_id", "")), str(case.get("problem_id", ""))))


def _compute_field_averages(final_cases: list[dict[str, Any]], tag_keep: set[str]) -> dict[str, dict[str, dict[str, float]]]:
    field_totals: dict[str, dict[str, dict[str, float]]] = {dimension: defaultdict(lambda: {"pass": 0.0, "quality": 0.0, "mass": 0.0}) for dimension in SLICE_DIMENSIONS}
    for case in final_cases:
        weight = float(case.get("problem_weight", 0.0))
        if weight <= 0:
            continue
        for dimension, value in _iter_case_slices(case, tag_keep):
            bucket = field_totals[dimension][value]
            bucket["pass"] += weight * float(case.get("pass_points", 0.0))
            bucket["quality"] += weight * float(case.get("quality_points", 0.0))
            bucket["mass"] += weight

    result: dict[str, dict[str, dict[str, float]]] = {dimension: {} for dimension in SLICE_DIMENSIONS}
    for dimension, values in field_totals.items():
        for value, totals in values.items():
            mass = totals["mass"]
            result[dimension][value] = {
                "slice_weighted_pass_rate": round(totals["pass"] / mass, 4) if mass else 0.0,
                "slice_quality_score": round(totals["quality"] / mass, 4) if mass else 0.0,
            }
    return result


def _build_model_summary(
    model_id: str,
    items: list[dict[str, Any]],
    scoring_policy: dict[str, Any],
    field_averages: dict[str, dict[str, dict[str, float]]],
    tag_keep: set[str],
) -> dict[str, Any]:
    provider = str(items[0].get("provider", "unknown")) if items else "unknown"
    total = len(items)
    weighted_pass_score = round(sum(float(item.get("problem_weight", 0.0)) * float(item.get("pass_points", 0.0)) for item in items), 4)
    weighted_quality = round(sum(float(item.get("problem_weight", 0.0)) * float(item.get("quality_points", 0.0)) for item in items), 4)
    weighted_case_mass = round(sum(float(item.get("problem_weight", 0.0)) for item in items), 4)
    overall_weighted_pass_rate = round(weighted_pass_score / weighted_case_mass, 4) if weighted_case_mass else 0.0
    overall_weighted_quality_rate = round(weighted_quality / weighted_case_mass, 4) if weighted_case_mass else 0.0
    passed = sum(1 for item in items if item.get("passed"))

    lint_exec = [item for item in items if str(item.get("lint", {}).get("status", "")) != "skipped"]
    sim_exec = [item for item in items if str(item.get("simulation", {}).get("status", "")) != "skipped"]
    synth_exec = [item for item in items if str(item.get("synthesis", {}).get("status", "")) != "skipped"]

    mutation_rates = [item.get("mutation_kill_rate") for item in items if item.get("mutation_kill_rate") is not None]

    row = {
        "model_id": model_id,
        "provider": provider,
        "score": weighted_pass_score,
        "weighted_pass_score": weighted_pass_score,
        "quality_score": weighted_quality,
        "weighted_case_mass": weighted_case_mass,
        "overall_weighted_pass_rate": overall_weighted_pass_rate,
        "overall_weighted_quality_rate": overall_weighted_quality_rate,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "lint_pass_rate": round(sum(1 for item in lint_exec if item.get("lint", {}).get("status") == "pass") / len(lint_exec), 4)
        if lint_exec
        else None,
        "sim_pass_rate": round(sum(1 for item in sim_exec if item.get("simulation", {}).get("status") == "pass") / len(sim_exec), 4)
        if sim_exec
        else None,
        "synth_pass_rate": round(sum(1 for item in synth_exec if item.get("synthesis", {}).get("status") == "pass") / len(synth_exec), 4)
        if synth_exec
        else None,
        "avg_mutation_kill_rate": round(sum(float(rate) for rate in mutation_rates) / len(mutation_rates), 4) if mutation_rates else None,
        "cases": total,
    }

    breakdowns = _build_breakdowns(items, row, field_averages, scoring_policy, tag_keep)
    strengths, weaknesses = _extract_highlights(breakdowns, scoring_policy)
    row["breakdowns"] = breakdowns
    row["strengths"] = strengths
    row["weaknesses"] = weaknesses
    row["top_tags"] = breakdowns["tags"][: min(8, len(breakdowns["tags"]))]
    row["profile_summary"] = _profile_summary(strengths, weaknesses)
    return row


def _build_breakdowns(
    items: list[dict[str, Any]],
    overall_row: dict[str, Any],
    field_averages: dict[str, dict[str, dict[str, float]]],
    scoring_policy: dict[str, Any],
    tag_keep: set[str],
) -> dict[str, list[dict[str, Any]]]:
    raw: dict[str, dict[str, list[dict[str, Any]]]] = {dimension: defaultdict(list) for dimension in SLICE_DIMENSIONS}
    for item in items:
        for dimension, value in _iter_case_slices(item, tag_keep):
            raw[dimension][value].append(item)

    breakdowns: dict[str, list[dict[str, Any]]] = {}
    for dimension in SLICE_DIMENSIONS:
        entries: list[dict[str, Any]] = []
        for value, slice_items in raw[dimension].items():
            mass = sum(float(item.get("problem_weight", 0.0)) for item in slice_items)
            if mass <= 0:
                continue
            sim_exec = [item for item in slice_items if str(item.get("simulation", {}).get("status", "")) != "skipped"]
            synth_exec = [item for item in slice_items if str(item.get("synthesis", {}).get("status", "")) != "skipped"]
            mutation_rates = [item.get("mutation_kill_rate") for item in slice_items if item.get("mutation_kill_rate") is not None]
            slice_pass = sum(float(item.get("problem_weight", 0.0)) * float(item.get("pass_points", 0.0)) for item in slice_items) / mass
            slice_quality = sum(float(item.get("problem_weight", 0.0)) * float(item.get("quality_points", 0.0)) for item in slice_items) / mass
            field_average = field_averages.get(dimension, {}).get(value, {})
            entry = {
                "dimension": dimension,
                "value": value,
                "label": _slice_label(dimension, value),
                "cases": len(slice_items),
                "global_weight_mass": round(mass, 4),
                "slice_weighted_pass_rate": round(slice_pass, 4),
                "slice_quality_score": round(slice_quality, 4),
                "sim_pass_rate": round(
                    sum(1 for item in sim_exec if item.get("simulation", {}).get("status") == "pass") / len(sim_exec), 4
                )
                if sim_exec
                else None,
                "synth_pass_rate": round(
                    sum(1 for item in synth_exec if item.get("synthesis", {}).get("status") == "pass") / len(synth_exec), 4
                )
                if synth_exec
                else None,
                "avg_mutation_kill_rate": round(sum(float(rate) for rate in mutation_rates) / len(mutation_rates), 4)
                if mutation_rates
                else None,
                "lift_vs_model_overall": round(slice_pass - float(overall_row.get("overall_weighted_pass_rate", 0.0)), 4),
                "lift_vs_field_average": round(slice_pass - float(field_average.get("slice_weighted_pass_rate", 0.0)), 4),
            }
            entries.append(entry)

        entries.sort(key=lambda entry: (-float(entry.get("global_weight_mass", 0.0)), -float(entry.get("slice_weighted_pass_rate", 0.0)), str(entry.get("value", ""))))
        breakdowns[DIMENSION_PLURALS.get(dimension, f"{dimension}s")] = entries
    return breakdowns


def _extract_highlights(breakdowns: dict[str, list[dict[str, Any]]], scoring_policy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    min_cases = int(scoring_policy.get("profile_min_cases", 3))
    min_mass = float(scoring_policy.get("profile_min_global_weight", 0.03))
    limit = int(scoring_policy.get("highlights_per_model", 3))
    all_entries = [entry for entries in breakdowns.values() for entry in entries]
    eligible = [entry for entry in all_entries if int(entry.get("cases", 0)) >= min_cases and float(entry.get("global_weight_mass", 0.0)) >= min_mass]

    strengths = [
        entry
        for entry in eligible
        if float(entry.get("lift_vs_model_overall", 0.0)) >= 0.10 and float(entry.get("lift_vs_field_average", 0.0)) >= 0.05
    ]
    weaknesses = [
        entry
        for entry in eligible
        if float(entry.get("lift_vs_model_overall", 0.0)) <= -0.10 and float(entry.get("lift_vs_field_average", 0.0)) <= -0.05
    ]

    strengths.sort(key=lambda entry: (-float(entry.get("lift_vs_model_overall", 0.0)), -float(entry.get("global_weight_mass", 0.0)), str(entry.get("label", ""))))
    weaknesses.sort(key=lambda entry: (float(entry.get("lift_vs_model_overall", 0.0)), -float(entry.get("global_weight_mass", 0.0)), str(entry.get("label", ""))))
    return strengths[: max(limit, 0)], weaknesses[: max(limit, 0)]


def _profile_summary(strengths: list[dict[str, Any]], weaknesses: list[dict[str, Any]]) -> str:
    strong_text = "、".join(str(entry.get("label", "")) for entry in strengths) or "暂无显著强项"
    weak_text = "、".join(str(entry.get("label", "")) for entry in weaknesses) or "暂无显著弱项"
    return f"擅长 {strong_text}；薄弱于 {weak_text}"


def _build_slice_rankings_for_model(row: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    rankings: dict[str, dict[str, dict[str, Any]]] = {DIMENSION_PLURALS[dimension]: {} for dimension in LEADERBOARD_SLICE_DIMENSIONS}
    for plural, entries in row.get("breakdowns", {}).items():
        if plural not in rankings:
            continue
        for entry in entries:
            rankings[plural][str(entry.get("value", ""))] = {
                "model_id": row.get("model_id", ""),
                "provider": row.get("provider", ""),
                "score": entry.get("slice_weighted_pass_rate", 0.0),
                "weighted_pass_score": entry.get("slice_weighted_pass_rate", 0.0),
                "quality_score": entry.get("slice_quality_score", 0.0),
                "cases": entry.get("cases", 0),
                "global_weight_mass": entry.get("global_weight_mass", 0.0),
                "label": entry.get("label", ""),
                "value": entry.get("value", ""),
            }
    return rankings


def _finalize_slice_rankings(slice_rows: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    rankings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for plural, values in slice_rows.items():
        rankings[plural] = {}
        for value, rows in values.items():
            ordered = sorted(
                rows,
                key=lambda row: (
                    -float(row.get("weighted_pass_score", 0.0)),
                    -float(row.get("quality_score", 0.0)),
                    str(row.get("model_id", "")),
                ),
            )
            rankings[plural][value] = ordered
    return rankings


def _iter_case_slices(case: dict[str, Any], tag_keep: set[str]) -> list[tuple[str, str]]:
    slices = [
        ("source", str(case.get("problem_source", case.get("source", "")) or "local")),
        ("track", str(case.get("problem_track", "") or "rtl_core")),
        ("difficulty", str(case.get("problem_difficulty", "") or "easy")),
        ("category", str(case.get("problem_category", "") or "uncategorized")),
    ]
    tags = [str(tag) for tag in case.get("problem_tags", []) if str(tag) in tag_keep]
    slices.extend(("tag", tag) for tag in tags)
    return slices


def _slice_label(dimension: str, value: str) -> str:
    if dimension == "source":
        return f"题源 {value}"
    if dimension == "track":
        return f"能力 {value}"
    if dimension == "difficulty":
        return f"难度 {value}"
    if dimension == "category":
        return f"类别 {value}"
    return f"标签 {value}"
