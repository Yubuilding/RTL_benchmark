from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from rtl_benchmark.evaluator import Evaluator, list_case_artifacts, safe_name
from rtl_benchmark.leaderboard import build_suite_leaderboard, update_leaderboard
from rtl_benchmark.model_runner import ModelRunner
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.types import CaseResult, StageStatus
from rtl_benchmark.utils import ensure_dir, load_json, now_utc_iso, save_json, utc_run_id


class BenchmarkPipeline:
    def __init__(self, config_path: str):
        self.config = load_json(config_path)
        self.model_runner = ModelRunner(self.config.get("generation", {}))

    def run(self, include_known: bool = False) -> dict:
        started_at = now_utc_iso()
        run_id = utc_run_id()

        problem_filters = dict(self.config.get("problem_filters", {}))
        problems = load_problems(self.config["problem_glob"], problem_filters)
        models = discover_models(
            sources=self.config.get("sources", []),
            state_path=self.config["state_path"],
            include_known=include_known,
            selection=self.config.get("selection", {}),
            update_state=True,
        )

        if not models:
            return {
                "run_id": run_id,
                "started_at": started_at,
                "finished_at": now_utc_iso(),
                "models": [],
                "cases": [],
                "summary": [],
            }

        run_root = ensure_dir(self.config["run_root"]) / run_id
        evaluator = Evaluator(str(run_root), self.config.get("execution", {}))

        max_iterations = int(self.config.get("max_iterations", 1))
        case_records: list[dict] = []
        model_records: list[dict] = []

        for model in models:
            model_records.append(asdict(model))
            for problem in problems:
                feedback = ""
                final = None

                for attempt in range(1, max_iterations + 1):
                    candidate = self.model_runner.generate(model, problem, feedback=feedback)
                    if not candidate.strip():
                        result = self._generation_failed_result(
                            model.id,
                            problem.id,
                            problem.task_type,
                            attempt,
                            detail=self.model_runner.last_error,
                        )
                    else:
                        result = evaluator.evaluate(model.id, problem, candidate, attempt)
                    final = result
                    feedback = result.feedback
                    if result.passed:
                        break

                if final is None:
                    continue

                row = asdict(final)
                row["provider"] = model.provider
                row["candidate_code"] = candidate
                row["problem_source"] = problem.source
                row["problem_category"] = problem.category
                row["problem_suite"] = problem.suite
                row["problem_track"] = problem.track
                row["problem_difficulty"] = problem.difficulty
                row["api_trace"] = dict(self.model_runner.last_trace)
                self._persist_api_trace(
                    run_root=run_root,
                    row=row,
                    model_id=model.id,
                    problem_id=problem.id,
                    attempt=attempt,
                )
                case_records.append(row)

        scored = build_suite_leaderboard(
            case_records,
            problems=[asdict(problem) for problem in problems],
            scoring_config=self.config.get("scoring", {}),
        )
        case_records = scored["cases"]
        summary = scored["summary"]

        run_result = {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "source": "pipeline",
            "scope": "suite",
            "problem_filters": problem_filters,
            "problem_ids": [problem.id for problem in problems],
            "problems": [asdict(problem) for problem in problems],
            "models": model_records,
            "cases": case_records,
            "summary": summary,
            "slice_rankings": scored["slice_rankings"],
            "scoring_policy": scored["scoring_policy"],
            "run_root": str(run_root.resolve()),
        }

        raw_dir = ensure_dir(self.config["raw_results_dir"])
        save_json(raw_dir / f"{run_id}.json", run_result)

        update_leaderboard(
            self.config["leaderboard_path"],
            run_id,
            summary,
            scope="suite",
            problem_ids=[problem.id for problem in problems],
            slice_rankings=scored["slice_rankings"],
            scoring_policy=scored["scoring_policy"],
            raw_results_dir=self.config["raw_results_dir"],
        )
        return run_result

    def _persist_api_trace(
        self,
        run_root: Path,
        row: dict,
        model_id: str,
        problem_id: str,
        attempt: int,
    ) -> None:
        trace = row.get("api_trace")
        if not trace:
            return
        artifact_dir = str(row.get("artifact_dir", "")).strip()
        case_dir = Path(artifact_dir) if artifact_dir else run_root / safe_name(model_id) / problem_id / f"attempt_{attempt}"
        case_dir = ensure_dir(case_dir)
        save_json(case_dir / "api_trace.json", trace)
        row["artifact_dir"] = str(case_dir.resolve())
        row["artifacts"] = list_case_artifacts(case_dir)

    def _generation_failed_result(
        self,
        model_id: str,
        problem_id: str,
        task_type: str,
        attempt: int,
        detail: str = "",
    ) -> CaseResult:
        skipped = StageStatus(status="skipped", reason="generation failed")
        feedback = "generation failed: provider returned no HDL code"
        if detail:
            feedback = f"{feedback}; {detail}"
        return CaseResult(
            model_id=model_id,
            problem_id=problem_id,
            task_type=task_type,
            attempt=attempt,
            passed=False,
            lint=skipped,
            simulation=skipped,
            synthesis=skipped,
            feedback=feedback,
        )
