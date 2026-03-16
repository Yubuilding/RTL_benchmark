from __future__ import annotations

import json
import mimetypes
import os
import threading
import traceback
from contextlib import contextmanager
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from rtl_benchmark.evaluator import Evaluator, list_case_artifacts, safe_name
from rtl_benchmark.leaderboard import rebuild_leaderboard_from_raw_results, summarize_cases, update_leaderboard
from rtl_benchmark.model_runner import DEFAULT_MAX_TOKENS, ModelRunner, normalize_max_tokens
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.types import CaseResult, ModelDescriptor, Problem, StageStatus
from rtl_benchmark.utils import ensure_dir, load_json, now_utc_iso, save_json, utc_run_id


ASSET_DIR = Path(__file__).resolve().parent / "webui"


PROVIDER_DEFAULTS = [
    {
        "key": "openai",
        "label": "OpenAI",
        "type": "openai",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "supports_base_url": True,
    },
    {
        "key": "anthropic",
        "label": "Anthropic",
        "type": "anthropic",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "version": "2023-06-01",
        "supports_base_url": True,
    },
    {
        "key": "gemini",
        "label": "Google Gemini",
        "type": "gemini",
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GEMINI_API_KEY",
        "supports_base_url": True,
    },
    {
        "key": "openrouter",
        "label": "OpenRouter",
        "type": "openrouter",
        "provider": "openrouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "supports_base_url": False,
    },
    {
        "key": "openai_compatible",
        "label": "OpenAI Compatible",
        "type": "openai",
        "provider": "openai_compatible",
        "base_url": "https://your-openai-compatible-endpoint/v1",
        "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
        "supports_base_url": True,
    },
    {
        "key": "huggingface",
        "label": "Hugging Face",
        "type": "huggingface",
        "provider": "huggingface",
        "api_key_env": "HF_TOKEN",
        "supports_base_url": False,
    },
]


class WebAppService:
    def __init__(self, base_config_path: str, ui_config_path: str = ""):
        self.base_config_path = Path(base_config_path).resolve()
        self.base_config = load_json(self.base_config_path)
        self.repo_root = self.base_config_path.parent.parent
        default_ui_path = self.repo_root / ".state" / "webui_config.json"
        self.ui_config_path = Path(ui_config_path).resolve() if ui_config_path else default_ui_path
        self.leaderboard_state_path = self.repo_root / ".state" / "leaderboard_state.json"
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()

    def load_ui_config(self) -> dict[str, Any]:
        if self.ui_config_path.exists():
            cfg = load_json(self.ui_config_path, default={})
            return self._normalize_ui_config(cfg)

        cfg = self._default_ui_config()
        self.save_ui_config(cfg)
        return cfg

    def save_ui_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_ui_config(config)
        save_json(self.ui_config_path, normalized)
        return normalized

    def get_state(self) -> dict[str, Any]:
        problems = self.list_problems()
        return {
            "uiConfig": self.load_ui_config(),
            "problems": problems,
            "problemStats": {
                "total": len(problems),
                "sources": len({item["source"] for item in problems}),
                "categories": len({f"{item['source']}::{item['category']}" for item in problems}),
            },
            "history": self.list_history(limit=20),
            "leaderboard": self.load_leaderboard(),
            "jobs": self.list_jobs(),
            "baseConfigPath": str(self.base_config_path),
        }

    def list_problems(self) -> list[dict[str, Any]]:
        glob_pattern = str(self.base_config.get("problem_glob", "benchmarks/**/*.json"))
        problem_filters = dict(self.base_config.get("problem_filters", {}))
        entries: list[dict[str, Any]] = []
        for problem in load_problems(glob_pattern, problem_filters):
            entries.append(
                {
                    "id": problem.id,
                    "task_type": problem.task_type,
                    "language": problem.language,
                    "top_module": problem.top_module,
                    "prompt": problem.prompt,
                    "source": problem.source,
                    "category": problem.category or "uncategorized",
                    "suite": problem.suite,
                    "track": problem.track,
                    "difficulty": problem.difficulty,
                    "prompt_style": problem.prompt_style,
                    "harness_type": problem.harness_type,
                    "evaluation_targets": list(problem.evaluation_targets),
                    "exposure": problem.exposure,
                    "tags": list(problem.tags),
                    "path": problem.path,
                    "has_harness": bool(problem.testbench or problem.reference_tb or problem.golden_rtl),
                }
            )
        entries.sort(key=lambda item: (item["source"], item["category"], item["id"]))
        return entries

    def list_history(self, limit: int = 20) -> list[dict[str, Any]]:
        raw_dir = ensure_dir(self.base_config.get("raw_results_dir", "results/raw"))
        items: list[dict[str, Any]] = []
        for path in sorted(raw_dir.glob("*.json"), reverse=True):
            data = load_json(path, default={})
            run_id = str(data.get("run_id", path.stem))
            items.append(
                {
                    "run_id": run_id,
                    "path": str(path),
                    "started_at": str(data.get("started_at", "")),
                    "finished_at": str(data.get("finished_at", "")),
                    "source": str(data.get("source", "pipeline")),
                    "scope": str(data.get("scope", "suite")),
                    "custom_problem": bool(data.get("custom_problem", False)),
                    "problem_ids": list(data.get("problem_ids", [])),
                    "model_count": len(data.get("models", [])),
                    "case_count": len(data.get("cases", [])),
                    "summary": list(data.get("summary", [])),
                }
            )
        items.sort(key=lambda item: item.get("started_at", "") or item["run_id"], reverse=True)
        return items[:limit]

    def load_history_detail(self, run_id: str) -> dict[str, Any] | None:
        raw_dir = ensure_dir(self.base_config.get("raw_results_dir", "results/raw"))
        direct = raw_dir / f"{run_id}.json"
        if direct.exists():
            return self._enrich_history_detail(load_json(direct, default={}))

        for path in raw_dir.glob("*.json"):
            data = load_json(path, default={})
            if str(data.get("run_id", "")) == run_id:
                return self._enrich_history_detail(data)
        return None

    def compare_models(self, run_id: str, model_a: str, model_b: str) -> dict[str, Any] | None:
        detail = self.load_history_detail(run_id)
        if detail is None:
            return None

        left = str(model_a).strip()
        right = str(model_b).strip()
        if not left or not right:
            raise ValueError("model_a and model_b are required")
        if left == right:
            raise ValueError("model_a and model_b must be different")

        available_models = [str(item.get("model_id", "")) for item in detail.get("model_results", []) if item.get("model_id")]
        if left not in available_models:
            raise ValueError(f"model not found in run: {left}")
        if right not in available_models:
            raise ValueError(f"model not found in run: {right}")

        case_index = self._index_final_cases(detail.get("cases", []))
        left_cases = case_index.get(left, {})
        right_cases = case_index.get(right, {})
        all_problem_ids = sorted(set(left_cases) | set(right_cases))

        rows = [self._build_compare_row(problem_id, left_cases.get(problem_id), right_cases.get(problem_id)) for problem_id in all_problem_ids]
        outcome_order = {"a_only_pass": 0, "b_only_pass": 1, "both_fail": 2, "both_pass": 3, "missing": 4}
        rows.sort(key=lambda item: (outcome_order.get(str(item.get("outcome", "")), 99), str(item.get("problem_id", ""))))

        comparable = sum(1 for row in rows if row["model_a"]["present"] and row["model_b"]["present"])
        both_pass = sum(1 for row in rows if row["outcome"] == "both_pass")
        both_fail = sum(1 for row in rows if row["outcome"] == "both_fail")
        a_only_pass = sum(1 for row in rows if row["outcome"] == "a_only_pass")
        b_only_pass = sum(1 for row in rows if row["outcome"] == "b_only_pass")
        missing_a = sum(1 for row in rows if not row["model_a"]["present"])
        missing_b = sum(1 for row in rows if not row["model_b"]["present"])

        return {
            "run_id": str(detail.get("run_id", run_id)),
            "started_at": str(detail.get("started_at", "")),
            "finished_at": str(detail.get("finished_at", "")),
            "model_a": left,
            "model_b": right,
            "available_models": available_models,
            "summary": {
                "total_cases": len(rows),
                "comparable_cases": comparable,
                "same_outcome_cases": both_pass + both_fail,
                "model_a_passed": sum(1 for row in rows if row["model_a"]["status"] == "pass"),
                "model_b_passed": sum(1 for row in rows if row["model_b"]["status"] == "pass"),
                "both_pass": both_pass,
                "both_fail": both_fail,
                "a_only_pass": a_only_pass,
                "b_only_pass": b_only_pass,
                "missing_a": missing_a,
                "missing_b": missing_b,
            },
            "rows": rows,
        }

    def load_leaderboard(self) -> dict[str, Any]:
        board = rebuild_leaderboard_from_raw_results(
            self.base_config.get("leaderboard_path", "results/leaderboard.json"),
            self.base_config.get("raw_results_dir", "results/raw"),
            reset_after=self._leaderboard_reset_at(),
        )
        board["reset_at"] = self._leaderboard_reset_at()
        return board

    def reset_leaderboard(self) -> dict[str, Any]:
        reset_at = now_utc_iso()
        save_json(self.leaderboard_state_path, {"reset_at": reset_at})
        board = {"updated_at": "", "models": [], "reset_at": reset_at}
        save_json(self.base_config.get("leaderboard_path", "results/leaderboard.json"), board)
        return board

    def load_artifact(self, raw_path: str) -> tuple[bytes, str, str] | None:
        target = Path(raw_path).expanduser().resolve()
        allowed_roots = [
            ensure_dir(self.base_config.get("run_root", "results/runs")).resolve(),
            ensure_dir(self.base_config.get("raw_results_dir", "results/raw")).resolve(),
        ]
        if not any(self._is_within(target, root) for root in allowed_roots):
            return None
        if not target.exists() or not target.is_file():
            return None
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return target.read_bytes(), content_type, target.name

    def _enrich_history_detail(self, detail: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(detail)
        problem_map = self._load_problem_map()
        saved_problems = {
            str(item.get("id", "")): item for item in enriched.get("problems", []) if str(item.get("id", "")).strip()
        }

        if not saved_problems:
            saved_problems = {
                problem_id: asdict(problem)
                for problem_id, problem in problem_map.items()
                if problem_id in set(enriched.get("problem_ids", []))
            }
            enriched["problems"] = list(saved_problems.values())

        cases: list[dict[str, Any]] = []
        for case in enriched.get("cases", []):
            row = dict(case)
            problem_id = str(row.get("problem_id", ""))
            if problem_id and problem_id not in saved_problems and problem_id in problem_map:
                saved_problems[problem_id] = asdict(problem_map[problem_id])
            row["problem"] = saved_problems.get(problem_id, {})
            row["artifact_dir"] = row.get("artifact_dir") or self._infer_case_dir(enriched, row)
            artifact_dir = str(row.get("artifact_dir", "")).strip()
            if artifact_dir and not row.get("artifacts"):
                case_dir = Path(artifact_dir)
                if case_dir.exists():
                    row["artifacts"] = list_case_artifacts(case_dir)
            cases.append(row)

        enriched["cases"] = cases
        enriched["problems"] = list(saved_problems.values())
        enriched["model_results"] = self._summarize_model_results(cases)
        enriched["overview"] = self._build_run_overview(enriched, cases)
        return enriched

    def _load_problem_map(self) -> dict[str, Problem]:
        problems = load_problems(
            str(self.base_config.get("problem_glob", "benchmarks/**/*.json")),
            self.base_config.get("problem_filters", {}),
        )
        return {problem.id: problem for problem in problems}

    def _infer_case_dir(self, detail: dict[str, Any], case: dict[str, Any]) -> str:
        run_root = str(detail.get("run_root", "")).strip()
        if not run_root:
            base_run_root = ensure_dir(self.base_config.get("run_root", "results/runs"))
            run_id = str(detail.get("run_id", "")).strip()
            run_root = str((base_run_root / run_id).resolve())
        attempt = int(case.get("attempt", 0) or 0)
        if not run_root or attempt <= 0:
            return ""
        model_id = str(case.get("model_id", "")).strip()
        problem_id = str(case.get("problem_id", "")).strip()
        if not model_id or not problem_id:
            return ""
        return str((Path(run_root) / safe_name(model_id) / problem_id / f"attempt_{attempt}").resolve())

    def _summarize_model_results(self, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for case in cases:
            grouped.setdefault(str(case.get("model_id", "")), []).append(case)

        rows: list[dict[str, Any]] = []
        for model_id, items in grouped.items():
            passed = sum(1 for item in items if item.get("passed"))
            failed = sum(1 for item in items if item.get("passed") is False)
            rows.append(
                {
                    "model_id": model_id,
                    "provider": items[0].get("provider", ""),
                    "cases": len(items),
                    "passed": passed,
                    "failed": failed,
                    "pass_rate": round(passed / len(items), 4) if items else 0.0,
                    "items": items,
                }
            )

        rows.sort(key=lambda item: (-item["pass_rate"], item["model_id"]))
        return rows

    def _build_run_overview(self, detail: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
        passed = sum(1 for case in cases if case.get("passed"))
        return {
            "run_id": str(detail.get("run_id", "")),
            "model_count": len({str(case.get("model_id", "")) for case in cases}),
            "problem_count": len({str(case.get("problem_id", "")) for case in cases}),
            "case_count": len(cases),
            "passed_cases": passed,
            "failed_cases": len(cases) - passed,
        }

    def _index_final_cases(self, cases: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
        index: dict[str, dict[str, dict[str, Any]]] = {}
        for case in cases:
            model_id = str(case.get("model_id", "")).strip()
            problem_id = str(case.get("problem_id", "")).strip()
            if not model_id or not problem_id:
                continue
            bucket = index.setdefault(model_id, {})
            current = bucket.get(problem_id)
            current_attempt = int(current.get("attempt", 0) or 0) if current else -1
            attempt = int(case.get("attempt", 0) or 0)
            if current is None or attempt >= current_attempt:
                bucket[problem_id] = case
        return index

    def _build_compare_row(
        self,
        problem_id: str,
        left_case: dict[str, Any] | None,
        right_case: dict[str, Any] | None,
    ) -> dict[str, Any]:
        seed = left_case or right_case or {}
        problem = dict(seed.get("problem", {}))
        source = str(problem.get("source") or seed.get("problem_source", ""))
        category = str(problem.get("category") or seed.get("problem_category", ""))
        suite = str(problem.get("suite") or seed.get("problem_suite", ""))
        difficulty = str(problem.get("difficulty") or seed.get("problem_difficulty", ""))

        left = self._serialize_compare_case(left_case)
        right = self._serialize_compare_case(right_case)
        if left["present"] and right["present"]:
            if left["status"] == "pass" and right["status"] == "fail":
                outcome = "a_only_pass"
            elif left["status"] == "fail" and right["status"] == "pass":
                outcome = "b_only_pass"
            elif left["status"] == "pass" and right["status"] == "pass":
                outcome = "both_pass"
            else:
                outcome = "both_fail"
        else:
            outcome = "missing"

        return {
            "problem_id": problem_id,
            "source": source,
            "category": category,
            "suite": suite,
            "difficulty": difficulty,
            "outcome": outcome,
            "model_a": left,
            "model_b": right,
        }

    def _serialize_compare_case(self, case: dict[str, Any] | None) -> dict[str, Any]:
        if not case:
            return {
                "present": False,
                "status": "missing",
                "passed": None,
                "attempt": None,
                "feedback": "",
                "case_key": "",
                "lint_status": "missing",
                "simulation_status": "missing",
                "synthesis_status": "missing",
            }

        passed = bool(case.get("passed"))
        return {
            "present": True,
            "status": "pass" if passed else "fail",
            "passed": passed,
            "attempt": int(case.get("attempt", 0) or 0),
            "feedback": str(case.get("feedback", "")),
            "case_key": self._case_key(case),
            "lint_status": self._stage_status(case.get("lint")),
            "simulation_status": self._stage_status(case.get("simulation")),
            "synthesis_status": self._stage_status(case.get("synthesis")),
        }

    def _case_key(self, case: dict[str, Any]) -> str:
        return "::".join(
            [
                str(case.get("model_id", "")),
                str(case.get("problem_id", "")),
                str(int(case.get("attempt", 0) or 0)),
            ]
        )

    def _stage_status(self, stage: dict[str, Any] | None) -> str:
        return str((stage or {}).get("status", "missing"))

    def _is_within(self, target: Path, root: Path) -> bool:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            return False

    def _leaderboard_reset_at(self) -> str:
        return str(load_json(self.leaderboard_state_path, default={}).get("reset_at", "")).strip()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item.get("submitted_at", ""), reverse=True)
        return jobs[:10]

    def start_job(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = utc_run_id()
        job = {
            "job_id": job_id,
            "status": "queued",
            "submitted_at": now_utc_iso(),
            "started_at": "",
            "finished_at": "",
            "error": "",
            "progress": {"message": "queued", "model_id": "", "problem_id": "", "attempt": 0},
            "request": request,
            "result": None,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run_job, args=(job_id, request), daemon=True)
        thread.start()
        return job

    def _run_job(self, job_id: str, request: dict[str, Any]) -> None:
        self._update_job(job_id, status="running", started_at=now_utc_iso(), progress={"message": "loading"})
        try:
            ui_config = self._normalize_ui_config(request.get("uiConfig") or self.load_ui_config())
            problems, scope, update_board = self._resolve_requested_problems(request)
            if not problems:
                raise ValueError("No problems selected.")

            sources = self._build_sources(ui_config)
            models = discover_models(
                sources=sources,
                state_path=self.base_config.get("state_path", ".state/known_models.json"),
                include_known=True,
                selection={},
                update_state=False,
            )
            if not models:
                raise ValueError("No models available. Enable a provider and add at least one model.")

            result = self._execute_run(
                ui_config=ui_config,
                models=models,
                problems=problems,
                scope=scope,
                custom_problem=scope == "custom_problem",
                update_board=update_board,
                job_id=job_id,
            )
            self._update_job(job_id, status="completed", finished_at=now_utc_iso(), result=result)
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                finished_at=now_utc_iso(),
                error=f"{exc}\n{traceback.format_exc()}",
            )

    def _execute_run(
        self,
        ui_config: dict[str, Any],
        models: list[ModelDescriptor],
        problems: list[Problem],
        scope: str,
        custom_problem: bool,
        update_board: bool,
        job_id: str,
    ) -> dict[str, Any]:
        run_id = utc_run_id()
        started_at = now_utc_iso()
        run_root = ensure_dir(self.base_config.get("run_root", "results/runs")) / run_id
        evaluator = Evaluator(str(run_root), ui_config.get("execution", {}))
        model_runner = ModelRunner(ui_config.get("generation", {}))
        max_iterations = int(self.base_config.get("max_iterations", 1))

        case_records: list[dict[str, Any]] = []
        model_records: list[dict[str, Any]] = []

        with self._provider_env(ui_config):
            for model in models:
                model_records.append(asdict(model))
                for problem in problems:
                    feedback = ""
                    final = None
                    can_evaluate, reason = self._can_evaluate_problem(problem)

                    for attempt in range(1, max_iterations + 1):
                        self._update_job(
                            job_id,
                            progress={
                                "message": "running",
                                "model_id": model.id,
                                "problem_id": problem.id,
                                "attempt": attempt,
                            },
                        )
                        candidate = model_runner.generate(model, problem, feedback=feedback)
                        if not candidate.strip():
                            result = self._generation_failed_result(
                                model.id,
                                problem.id,
                                problem.task_type,
                                attempt,
                                detail=model_runner.last_error,
                            )
                        elif can_evaluate:
                            result = evaluator.evaluate(model.id, problem, candidate, attempt)
                        else:
                            result = self._evaluation_skipped_result(
                                model_id=model.id,
                                problem_id=problem.id,
                                task_type=problem.task_type,
                                attempt=attempt,
                                reason=reason,
                            )

                        final = result
                        feedback = result.feedback
                        row = asdict(result)
                        row["provider"] = model.provider
                        row["candidate_code"] = candidate
                        row["problem_source"] = problem.source
                        row["problem_category"] = problem.category
                        row["problem_suite"] = problem.suite
                        row["problem_track"] = problem.track
                        row["problem_difficulty"] = problem.difficulty
                        row["api_trace"] = dict(model_runner.last_trace)
                        self._persist_api_trace(
                            run_root=run_root,
                            row=row,
                            model_id=model.id,
                            problem_id=problem.id,
                            attempt=attempt,
                        )
                        case_records.append(row)
                        if result.passed or not can_evaluate:
                            break

                    if final is None:
                        continue

        summary = summarize_cases(case_records)
        run_result = {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "source": "webui",
            "scope": scope,
            "custom_problem": custom_problem,
            "problem_filters": dict(self.base_config.get("problem_filters", {})),
            "problem_ids": [problem.id for problem in problems],
            "problems": [asdict(problem) for problem in problems],
            "models": model_records,
            "cases": case_records,
            "summary": summary,
            "run_root": str(run_root.resolve()),
        }

        raw_dir = ensure_dir(self.base_config.get("raw_results_dir", "results/raw"))
        save_json(raw_dir / f"{run_id}.json", run_result)

        if update_board:
            update_leaderboard(
                self.base_config.get("leaderboard_path", "results/leaderboard.json"),
                run_id,
                summary,
                scope=scope,
                problem_ids=[problem.id for problem in problems],
            )

        return run_result

    def _persist_api_trace(
        self,
        run_root: Path,
        row: dict[str, Any],
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

    def _resolve_requested_problems(self, request: dict[str, Any]) -> tuple[list[Problem], str, bool]:
        scope = str(request.get("scope", "suite"))
        all_problems = {
            problem.id: problem
            for problem in load_problems(self.base_config["problem_glob"], self.base_config.get("problem_filters", {}))
        }

        if scope == "selected_problems":
            selected_ids = list(request.get("problemIds", []))
            problems = [all_problems[problem_id] for problem_id in selected_ids if problem_id in all_problems]
            return problems, scope, False

        if scope == "custom_problem":
            custom_payload = dict(request.get("customProblem", {}))
            problem = self._custom_problem_from_payload(custom_payload)
            return [problem], scope, False

        return list(all_problems.values()), "suite", True

    def _custom_problem_from_payload(self, payload: dict[str, Any]) -> Problem:
        task_type = str(payload.get("task_type", "rtl")).strip() or "rtl"
        problem_id = str(payload.get("id", "")).strip() or f"custom_{utc_run_id().lower()}"
        mutant_text = str(payload.get("mutant_rtls_text", ""))
        mutant_rtls = [chunk.strip() for chunk in mutant_text.split("\n---\n") if chunk.strip()]
        return Problem(
            id=problem_id,
            task_type=task_type,
            language=str(payload.get("language", "verilog")).strip() or "verilog",
            prompt=str(payload.get("prompt", "")).strip(),
            top_module=str(payload.get("top_module", "")).strip(),
            source="custom",
            category="adhoc",
            suite="custom",
            track="verification" if task_type == "testbench" else "rtl_core",
            difficulty="adhoc",
            prompt_style="spec_to_testbench" if task_type == "testbench" else "spec_to_rtl",
            harness_type="mutation" if task_type == "testbench" else "testbench_compare",
            evaluation_targets=["syntax", "functionality", "mutation"]
            if task_type == "testbench"
            else ["syntax", "functionality", "synthesis"],
            exposure="private",
            module_header=str(payload.get("module_header", "")).strip(),
            testbench=str(payload.get("testbench", "")).strip(),
            reference_rtl=str(payload.get("reference_rtl", "")).strip(),
            reference_tb=str(payload.get("reference_tb", "")).strip(),
            golden_rtl=str(payload.get("golden_rtl", "")).strip(),
            mutant_rtls=mutant_rtls,
            min_kill_rate=float(payload.get("min_kill_rate", 0.5) or 0.5),
        )

    def _can_evaluate_problem(self, problem: Problem) -> tuple[bool, str]:
        if not problem.prompt.strip():
            return False, "custom prompt is empty"
        if problem.task_type == "rtl":
            if not problem.testbench.strip():
                return False, "RTL evaluation requires a testbench"
            if not problem.reference_rtl.strip():
                return False, "RTL evaluation requires reference RTL"
            return True, ""
        if problem.task_type == "testbench":
            if not problem.golden_rtl.strip():
                return False, "testbench evaluation requires golden RTL"
            if not problem.reference_tb.strip():
                return False, "testbench evaluation requires a reference testbench"
            if not problem.mutant_rtls:
                return False, "testbench evaluation requires at least one mutant RTL"
            return True, ""
        return False, f"unsupported task type: {problem.task_type}"

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

    def _evaluation_skipped_result(
        self,
        model_id: str,
        problem_id: str,
        task_type: str,
        attempt: int,
        reason: str,
    ) -> CaseResult:
        skipped = StageStatus(status="skipped", reason=reason)
        return CaseResult(
            model_id=model_id,
            problem_id=problem_id,
            task_type=task_type,
            attempt=attempt,
            passed=False,
            lint=skipped,
            simulation=skipped,
            synthesis=skipped,
            feedback=f"evaluation skipped: {reason}",
        )

    def _default_ui_config(self) -> dict[str, Any]:
        base_sources = list(self.base_config.get("sources", []))
        providers: list[dict[str, Any]] = []
        for default in PROVIDER_DEFAULTS:
            source = self._find_source(base_sources, default["type"], default["provider"])
            providers.append(self._provider_from_source(default, source))

        execution = dict(self.base_config.get("execution", {}))
        generation = dict(self.base_config.get("generation", {}))

        return {
            "providers": providers,
            "generation": {
                "temperature": float(generation.get("temperature", 0.0)),
                "max_tokens": normalize_max_tokens(generation.get("max_tokens", DEFAULT_MAX_TOKENS)),
                "timeout_seconds": int(generation.get("timeout_seconds", 60)),
            },
            "execution": {
                "mode": str(execution.get("mode", "docker")),
                "timeout_seconds": int(execution.get("timeout_seconds", 30)),
                "docker_binary": str(execution.get("docker_binary", "docker")),
                "docker_image": str(execution.get("docker_image", "rtl-benchmark-tools:latest")),
            },
        }

    def _normalize_ui_config(self, config: dict[str, Any]) -> dict[str, Any]:
        defaults = self._default_ui_config()
        incoming = dict(config or {})
        providers_by_key = {item["key"]: item for item in incoming.get("providers", []) if item.get("key")}

        providers: list[dict[str, Any]] = []
        for default in defaults["providers"]:
            merged = dict(default)
            merged.update(providers_by_key.get(default["key"], {}))
            merged["enabled"] = bool(merged.get("enabled", False))
            merged["models"] = [str(item).strip() for item in merged.get("models", []) if str(item).strip()]
            merged["api_key"] = str(merged.get("api_key", ""))
            providers.append(merged)

        generation = dict(defaults["generation"])
        generation.update(incoming.get("generation", {}))
        execution = dict(defaults["execution"])
        execution.update(incoming.get("execution", {}))

        return {
            "providers": providers,
            "generation": {
                "temperature": float(generation.get("temperature", 0.0)),
                "max_tokens": normalize_max_tokens(generation.get("max_tokens", DEFAULT_MAX_TOKENS)),
                "timeout_seconds": int(generation.get("timeout_seconds", 60)),
            },
            "execution": {
                "mode": str(execution.get("mode", "docker")),
                "timeout_seconds": int(execution.get("timeout_seconds", 30)),
                "docker_binary": str(execution.get("docker_binary", "docker")),
                "docker_image": str(execution.get("docker_image", "rtl-benchmark-tools:latest")),
            },
        }

    def _provider_from_source(self, default: dict[str, Any], source: dict[str, Any] | None) -> dict[str, Any]:
        item = dict(default)
        item["enabled"] = bool(source.get("enabled", False)) if source else False
        item["models"] = [
            str(model.get("id", "")).strip()
            for model in (source or {}).get("models", [])
            if str(model.get("id", "")).strip()
        ]
        item["api_key"] = ""
        if source and source.get("base_url"):
            item["base_url"] = str(source.get("base_url", item.get("base_url", "")))
        if source and source.get("api_key_env"):
            item["api_key_env"] = str(source.get("api_key_env", item.get("api_key_env", "")))
        if source and source.get("version"):
            item["version"] = str(source.get("version"))
        return item

    def _find_source(self, sources: list[dict[str, Any]], source_type: str, provider: str) -> dict[str, Any] | None:
        for source in sources:
            if str(source.get("type", "")) != source_type:
                continue
            if str(source.get("provider", source_type)) != provider:
                continue
            return source
        return None

    def _build_sources(self, ui_config: dict[str, Any]) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []

        for source in self.base_config.get("sources", []):
            if source.get("type") == "file_feed":
                sources.append(dict(source))

        for provider in ui_config.get("providers", []):
            if not provider.get("enabled"):
                continue
            source = {
                "type": provider["type"],
                "enabled": True,
                "provider": provider["provider"],
                "api_key_env": provider["api_key_env"],
                "models": [{"id": model_id} for model_id in provider.get("models", []) if model_id],
            }
            if provider.get("supports_base_url") and provider.get("base_url"):
                source["base_url"] = provider["base_url"]
            if provider.get("version"):
                source["version"] = provider["version"]
            sources.append(source)

        return sources

    @contextmanager
    def _provider_env(self, ui_config: dict[str, Any]):
        previous: dict[str, str | None] = {}
        try:
            for provider in ui_config.get("providers", []):
                env_name = str(provider.get("api_key_env", "")).strip()
                api_key = str(provider.get("api_key", "")).strip()
                if not env_name or not api_key:
                    continue
                previous[env_name] = os.environ.get(env_name)
                os.environ[env_name] = api_key
            yield
        finally:
            for env_name, old_value in previous.items():
                if old_value is None:
                    os.environ.pop(env_name, None)
                else:
                    os.environ[env_name] = old_value

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._jobs_lock:
            current = self._jobs.get(job_id, {})
            current.update(updates)
            self._jobs[job_id] = current


class WebAppRequestHandler(BaseHTTPRequestHandler):
    service: WebAppService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_asset("index.html", "text/html; charset=utf-8")
            return
        if path == "/config":
            self._serve_asset("config.html", "text/html; charset=utf-8")
            return
        if path == "/leaderboard":
            self._serve_asset("leaderboard.html", "text/html; charset=utf-8")
            return
        if path == "/results":
            self._serve_asset("results.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._serve_asset("app.js", "application/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self._serve_asset("styles.css", "text/css; charset=utf-8")
            return
        if path == "/api/state":
            self._send_json(self.service.get_state())
            return
        if path == "/api/artifact":
            query = parse_qs(parsed.query)
            raw_path = unquote(query.get("path", [""])[0])
            payload = self.service.load_artifact(raw_path)
            if payload is None:
                self._send_json({"error": "artifact not found"}, status=HTTPStatus.NOT_FOUND)
                return
            raw, content_type, filename = payload
            self._send_raw(raw, content_type, filename)
            return
        if path == "/api/leaderboard/compare":
            query = parse_qs(parsed.query)
            run_id = unquote(query.get("run", [""])[0]).strip()
            model_a = unquote(query.get("a", [""])[0]).strip()
            model_b = unquote(query.get("b", [""])[0]).strip()
            if not run_id:
                self._send_json({"error": "run is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                payload = self.service.compare_models(run_id, model_a, model_b)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if payload is None:
                self._send_json({"error": "run not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
            return
        if path.startswith("/api/history/"):
            run_id = unquote(path.rsplit("/", 1)[-1])
            detail = self.service.load_history_detail(run_id)
            if detail is None:
                self._send_json({"error": "run not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(detail)
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json_body()

        if path == "/api/config":
            saved = self.service.save_ui_config(data)
            self._send_json({"ok": True, "uiConfig": saved})
            return
        if path == "/api/run":
            job = self.service.start_job(data)
            self._send_json({"ok": True, "job": job}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/leaderboard/reset":
            board = self.service.reset_leaderboard()
            self._send_json({"ok": True, "leaderboard": board})
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve_asset(self, name: str, content_type: str) -> None:
        asset_path = ASSET_DIR / name
        if not asset_path.exists():
            self._send_json({"error": f"missing asset: {name}"}, status=HTTPStatus.NOT_FOUND)
            return
        raw = asset_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_raw(self, raw: bytes, content_type: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)


def serve_webapp(base_config_path: str, host: str = "127.0.0.1", port: int = 8787, ui_config_path: str = "") -> None:
    service = WebAppService(base_config_path=base_config_path, ui_config_path=ui_config_path)

    handler = type("BoundWebAppRequestHandler", (WebAppRequestHandler,), {"service": service})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"RTL benchmark web UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
