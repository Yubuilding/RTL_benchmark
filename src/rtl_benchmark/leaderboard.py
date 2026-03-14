from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rtl_benchmark.utils import load_json, now_utc_iso, save_json


def summarize_cases(cases: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        grouped[case["model_id"]].append(case)

    rows: list[dict] = []
    for model_id, items in grouped.items():
        provider = items[0].get("provider", "unknown")
        total = len(items)
        passed = sum(1 for x in items if x.get("passed"))

        lint_exec = [x for x in items if x.get("lint", {}).get("status") != "skipped"]
        sim_exec = [x for x in items if x.get("simulation", {}).get("status") != "skipped"]
        synth_exec = [x for x in items if x.get("synthesis", {}).get("status") != "skipped"]

        lint_pass = sum(1 for x in lint_exec if x.get("lint", {}).get("status") == "pass")
        sim_pass = sum(1 for x in sim_exec if x.get("simulation", {}).get("status") == "pass")
        synth_pass = sum(1 for x in synth_exec if x.get("synthesis", {}).get("status") == "pass")

        mutation_rates = [x.get("mutation_kill_rate") for x in items if x.get("mutation_kill_rate") is not None]

        row = {
            "model_id": model_id,
            "provider": provider,
            "score": round(passed / total, 4) if total else 0.0,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "lint_pass_rate": round(lint_pass / len(lint_exec), 4) if lint_exec else None,
            "sim_pass_rate": round(sim_pass / len(sim_exec), 4) if sim_exec else None,
            "synth_pass_rate": round(synth_pass / len(synth_exec), 4) if synth_exec else None,
            "avg_mutation_kill_rate": round(sum(mutation_rates) / len(mutation_rates), 4) if mutation_rates else None,
            "cases": total,
        }
        rows.append(row)

    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


def update_leaderboard(
    leaderboard_path: str,
    run_id: str,
    rows: list[dict],
    scope: str = "suite",
    problem_ids: list[str] | None = None,
) -> dict:
    board = load_json(leaderboard_path, default={"updated_at": "", "models": []})

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
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)

    result = {"updated_at": now_utc_iso(), "models": merged}
    save_json(leaderboard_path, result)
    return result


def rebuild_leaderboard_from_raw_results(leaderboard_path: str, raw_results_dir: str) -> dict:
    board = {"updated_at": "", "models": []}
    raw_dir = Path(raw_results_dir)
    runs: list[dict] = []
    for path in raw_dir.glob("*.json"):
        data = load_json(path, default={})
        summary = list(data.get("summary", []))
        if not summary:
            continue
        runs.append(
            {
                "run_id": str(data.get("run_id", path.stem)),
                "started_at": str(data.get("started_at", "")),
                "scope": str(data.get("scope", "suite")),
                "problem_ids": list(data.get("problem_ids", [])),
                "summary": summary,
            }
        )

    runs.sort(key=lambda item: (item.get("started_at", ""), item["run_id"]))
    for item in runs:
        index = {m["model_id"]: m for m in board.get("models", [])}
        for row in item["summary"]:
            current = index.get(row["model_id"], {})
            merged = dict(row)
            merged["runs"] = int(current.get("runs", 0)) + 1
            merged["last_run_id"] = item["run_id"]
            merged["last_scope"] = item["scope"]
            merged["last_problem_count"] = len(item.get("problem_ids", []))
            merged["updated_at"] = item.get("started_at", "") or now_utc_iso()
            index[merged["model_id"]] = merged
        board["models"] = sorted(index.values(), key=lambda x: x.get("score", 0), reverse=True)

    board["updated_at"] = runs[-1]["started_at"] if runs and runs[-1].get("started_at") else ""
    current = load_json(leaderboard_path, default={"updated_at": "", "models": []})
    if current != board:
        save_json(leaderboard_path, board)
    return board
