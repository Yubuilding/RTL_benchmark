from __future__ import annotations

from collections import defaultdict

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


def update_leaderboard(leaderboard_path: str, run_id: str, rows: list[dict]) -> dict:
    board = load_json(leaderboard_path, default={"updated_at": "", "models": []})

    index = {m["model_id"]: m for m in board.get("models", [])}
    for row in rows:
        current = index.get(row["model_id"], {})
        row["runs"] = int(current.get("runs", 0)) + 1
        row["last_run_id"] = run_id
        row["updated_at"] = now_utc_iso()
        index[row["model_id"]] = row

    merged = list(index.values())
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)

    result = {"updated_at": now_utc_iso(), "models": merged}
    save_json(leaderboard_path, result)
    return result
