from __future__ import annotations

from dataclasses import asdict

from rtl_benchmark.evaluator import Evaluator
from rtl_benchmark.leaderboard import summarize_cases, update_leaderboard
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

        problems = load_problems(self.config["problem_glob"])
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
                        result = self._generation_failed_result(model.id, problem.id, problem.task_type, attempt)
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
                case_records.append(row)

        summary = summarize_cases(case_records)

        run_result = {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "models": model_records,
            "cases": case_records,
            "summary": summary,
        }

        raw_dir = ensure_dir(self.config["raw_results_dir"])
        save_json(raw_dir / f"{run_id}.json", run_result)

        update_leaderboard(self.config["leaderboard_path"], run_id, summary)
        return run_result

    def _generation_failed_result(self, model_id: str, problem_id: str, task_type: str, attempt: int) -> CaseResult:
        skipped = StageStatus(status="skipped", reason="generation failed")
        return CaseResult(
            model_id=model_id,
            problem_id=problem_id,
            task_type=task_type,
            attempt=attempt,
            passed=False,
            lint=skipped,
            simulation=skipped,
            synthesis=skipped,
            feedback="generation failed: provider returned no HDL code",
        )
