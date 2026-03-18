from __future__ import annotations

import json
import mimetypes
import os
import shutil
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from rtl_benchmark.evaluator import Evaluator, list_case_artifacts, safe_name
from rtl_benchmark.leaderboard import (
    build_suite_leaderboard,
    rebuild_leaderboard_from_raw_results,
    scope_updates_leaderboard,
    update_leaderboard,
)
from rtl_benchmark.model_runner import DEFAULT_MAX_TOKENS, ModelRunner, normalize_max_tokens
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.scoring import normalize_scoring_config, select_final_cases
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


class RunExecutionError(RuntimeError):
    def __init__(self, message: str, partial_result: dict[str, Any] | None = None):
        super().__init__(message)
        self.partial_result = partial_result


class WebAppService:
    def __init__(self, base_config_path: str, ui_config_path: str = ""):
        self.base_config_path = Path(base_config_path).resolve()
        self.base_config = load_json(self.base_config_path)
        self.repo_root = self.base_config_path.parent.parent
        default_ui_path = self.repo_root / ".state" / "webui_config.json"
        self.ui_config_path = Path(ui_config_path).resolve() if ui_config_path else default_ui_path
        self.leaderboard_state_path = self.repo_root / ".state" / "leaderboard_state.json"
        self.jobs_state_path = self.repo_root / ".state" / "web_jobs.json"
        self._jobs: dict[str, dict[str, Any]] = self._load_jobs_state()
        self._job_threads: dict[str, threading.Thread] = {}
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
            "scoringPolicy": normalize_scoring_config(self.base_config.get("scoring", {})),
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
                    "status": str(data.get("status", "completed")),
                    "scope": str(data.get("scope", "suite")),
                    "error": str(data.get("error", "")),
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
            "slice_comparison": self._build_slice_comparison(detail, left, right),
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
        board = {"updated_at": "", "models": [], "slice_rankings": {}, "scoring_policy": {}, "reset_at": reset_at}
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

        enriched["problems"] = list(saved_problems.values())
        scored = build_suite_leaderboard(
            cases,
            problems=enriched["problems"],
            scoring_config=dict(enriched.get("scoring_policy") or self.base_config.get("scoring", {})),
        )
        enriched["cases"] = scored["cases"]
        enriched["summary"] = scored["summary"]
        enriched["slice_rankings"] = scored["slice_rankings"]
        enriched["scoring_policy"] = scored["scoring_policy"]
        enriched["model_results"] = self._summarize_model_results(scored["cases"], scored["summary"])
        enriched["overview"] = self._build_run_overview(enriched, scored["cases"])
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

    def _summarize_model_results(self, cases: list[dict[str, Any]], summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for case in select_final_cases(cases):
            grouped.setdefault(str(case.get("model_id", "")), []).append(case)

        summary_index = {str(row.get("model_id", "")): row for row in summary_rows}
        rows: list[dict[str, Any]] = []
        for model_id, items in grouped.items():
            summary = summary_index.get(model_id, {})
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
                    "score": summary.get("score", 0.0),
                    "weighted_pass_score": summary.get("weighted_pass_score", 0.0),
                    "quality_score": summary.get("quality_score", 0.0),
                    "strengths": list(summary.get("strengths", [])),
                    "weaknesses": list(summary.get("weaknesses", [])),
                    "profile_summary": str(summary.get("profile_summary", "")),
                    "breakdowns": dict(summary.get("breakdowns", {})),
                    "top_tags": list(summary.get("top_tags", [])),
                    "items": items,
                }
            )

        rows.sort(key=lambda item: (-float(item.get("score", 0.0)), -float(item.get("quality_score", 0.0)), item["model_id"]))
        return rows

    def _build_run_overview(self, detail: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
        final_cases = select_final_cases(cases)
        passed = sum(1 for case in final_cases if case.get("passed"))
        return {
            "run_id": str(detail.get("run_id", "")),
            "model_count": len({str(case.get("model_id", "")) for case in final_cases}),
            "problem_count": len({str(case.get("problem_id", "")) for case in final_cases}),
            "case_count": len(final_cases),
            "passed_cases": passed,
            "failed_cases": len(final_cases) - passed,
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
            "track": str(problem.get("track") or seed.get("problem_track", "")),
            "difficulty": difficulty,
            "tags": list(problem.get("tags") or seed.get("problem_tags", [])),
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

    def _build_slice_comparison(self, detail: dict[str, Any], model_a: str, model_b: str) -> dict[str, list[dict[str, Any]]]:
        summary_rows = {str(row.get("model_id", "")): row for row in detail.get("summary", [])}
        left = summary_rows.get(model_a, {})
        right = summary_rows.get(model_b, {})
        return {
            "sources": self._compare_breakdown_group(left, right, "sources"),
            "difficulties": self._compare_breakdown_group(left, right, "difficulties"),
            "tags": self._compare_breakdown_group(left, right, "tags"),
        }

    def _compare_breakdown_group(self, left: dict[str, Any], right: dict[str, Any], group: str) -> list[dict[str, Any]]:
        left_items = {str(item.get("value", "")): item for item in left.get("breakdowns", {}).get(group, [])}
        right_items = {str(item.get("value", "")): item for item in right.get("breakdowns", {}).get(group, [])}
        values = sorted(set(left_items) | set(right_items))
        rows: list[dict[str, Any]] = []
        for value in values:
            left_item = left_items.get(value)
            right_item = right_items.get(value)
            label = str((left_item or right_item or {}).get("label", value))
            left_score = float((left_item or {}).get("slice_weighted_pass_rate", 0.0))
            right_score = float((right_item or {}).get("slice_weighted_pass_rate", 0.0))
            rows.append(
                {
                    "value": value,
                    "label": label,
                    "model_a_score": round(left_score, 4),
                    "model_b_score": round(right_score, 4),
                    "delta": round(left_score - right_score, 4),
                    "model_a_cases": int((left_item or {}).get("cases", 0)),
                    "model_b_cases": int((right_item or {}).get("cases", 0)),
                    "model_a_weight": round(float((left_item or {}).get("global_weight_mass", 0.0)), 4),
                    "model_b_weight": round(float((right_item or {}).get("global_weight_mass", 0.0)), 4),
                }
            )
        rows.sort(key=lambda row: (-abs(float(row.get("delta", 0.0))), -max(float(row.get("model_a_weight", 0.0)), float(row.get("model_b_weight", 0.0))), row["label"]))
        return rows

    def _is_within(self, target: Path, root: Path) -> bool:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            return False

    def _leaderboard_reset_at(self) -> str:
        return str(load_json(self.leaderboard_state_path, default={}).get("reset_at", "")).strip()

    def list_jobs(self) -> list[dict[str, Any]]:
        self._reconcile_jobs()
        with self._jobs_lock:
            jobs = [self._with_job_result(dict(job)) for job in self._jobs.values()]
        jobs.sort(key=lambda item: item.get("submitted_at", ""), reverse=True)
        return jobs[:10]

    def start_job(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = utc_run_id()
        submitted_at = now_utc_iso()
        safe_request = self._sanitize_job_request(request)
        job = {
            "job_id": job_id,
            "status": "queued",
            "submitted_at": submitted_at,
            "started_at": "",
            "finished_at": "",
            "updated_at": submitted_at,
            "error": "",
            "progress": self._build_progress(message="queued"),
            "eta_state": self._new_eta_state(safe_request.get("uiConfig", {})),
            "pause_requested": False,
            "request": safe_request,
            "result": None,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
            self._persist_jobs_locked()

        thread = threading.Thread(target=self._run_job, args=(job_id, request), daemon=True)
        with self._jobs_lock:
            self._job_threads[job_id] = thread
        thread.start()
        return job

    def pause_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            status = str(job.get("status", ""))
            if status not in {"running", "queued"}:
                raise ValueError("job is not running")
            progress = job.get("progress", {}) if isinstance(job.get("progress"), dict) else {}
            job["pause_requested"] = True
            job["progress"] = self._build_progress(
                message="pause requested",
                model_id=str(progress.get("model_id", "")),
                problem_id=str(progress.get("problem_id", "")),
                attempt=int(progress.get("attempt", 0) or 0),
                completed_cases=int(progress.get("completed_cases", 0) or 0),
                total_cases=int(progress.get("total_cases", 0) or 0),
                eta=progress.get("eta"),
            )
            self._persist_jobs_locked()
            payload = self._with_job_result(dict(job))
        return payload

    def resume_job(self, job_id: str) -> dict[str, Any]:
        request: dict[str, Any]
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            status = str(job.get("status", ""))
            if status in {"running", "queued"}:
                worker = self._job_threads.get(job_id)
                if worker is not None and worker.is_alive():
                    raise ValueError("job is still running")
            if status == "completed":
                raise ValueError("completed job does not need resume")
            request = dict(job.get("request", {}))
            if not request:
                raise ValueError("job request is missing")
            job["status"] = "queued"
            job["started_at"] = str(job.get("started_at", ""))
            job["finished_at"] = ""
            job["error"] = ""
            existing_progress = job.get("progress", {}) if isinstance(job.get("progress"), dict) else {}
            job["progress"] = self._build_progress(
                message="queued",
                completed_cases=int(existing_progress.get("completed_cases", 0) or 0),
                total_cases=int(existing_progress.get("total_cases", 0) or 0),
                eta=existing_progress.get("eta"),
            )
            job["pause_requested"] = False
            job["resume_count"] = int(job.get("resume_count", 0) or 0) + 1
            job["eta_state"] = self._normalize_eta_state(job.get("eta_state", {}), request.get("uiConfig", {}))
            job.pop("result", None)
            self._persist_jobs_locked()
            payload = self._with_job_result(dict(job))

        thread = threading.Thread(target=self._run_job, args=(job_id, request), daemon=True)
        with self._jobs_lock:
            self._job_threads[job_id] = thread
        thread.start()
        return payload

    def delete_job(self, job_id: str) -> bool:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            status = str(job.get("status", ""))
            worker = self._job_threads.get(job_id)
            if status in {"running", "queued"} and worker is not None and worker.is_alive():
                raise ValueError("cannot delete a running job")
            run_id = str(job.get("run_id", "")).strip()
            run_root = str(job.get("run_root", "")).strip()
            del self._jobs[job_id]
            self._job_threads.pop(job_id, None)
            self._persist_jobs_locked()

        raw_snapshot = self._load_run_snapshot(run_id)
        if run_id:
            raw_path = ensure_dir(self.base_config.get("raw_results_dir", "results/raw")) / f"{run_id}.json"
            if raw_path.exists():
                raw_path.unlink()
        if run_root:
            self._delete_run_root(run_root)
        if raw_snapshot and scope_updates_leaderboard(
            str(raw_snapshot.get("scope", "suite")),
            custom_problem=bool(raw_snapshot.get("custom_problem", False)),
        ):
            rebuild_leaderboard_from_raw_results(
                self.base_config.get("leaderboard_path", "results/leaderboard.json"),
                self.base_config.get("raw_results_dir", "results/raw"),
                reset_after=self._leaderboard_reset_at(),
            )
        return True

    def _run_job(self, job_id: str, request: dict[str, Any]) -> None:
        existing = self._get_job(job_id)
        progress = existing.get("progress", {}) if isinstance(existing.get("progress"), dict) else {}
        self._update_job(
            job_id,
            status="running",
            started_at=now_utc_iso(),
            progress=self._build_progress(message="loading", eta=progress.get("eta")),
            eta_state=self._normalize_eta_state(existing.get("eta_state", {}), request.get("uiConfig", {})),
        )
        try:
            ui_config = self._resolve_runtime_ui_config(request)
            problems, scope, update_board = self._resolve_requested_problems(request)
            if not problems:
                raise ValueError("No problems selected.")

            models = self._load_or_discover_job_models(job_id, ui_config, request.get("selectedModels", []))
            if not models:
                raise ValueError("No models available. Select at least one enabled model for this run.")

            result = self._execute_run(
                ui_config=ui_config,
                models=models,
                problems=problems,
                scope=scope,
                custom_problem=scope == "custom_problem",
                update_board=update_board,
                job_id=job_id,
            )
            final_status = str(result.get("status", "completed") or "completed")
            update_payload: dict[str, Any] = {
                "status": final_status,
                "result": result,
                "pause_requested": False,
            }
            if final_status == "completed":
                update_payload["finished_at"] = now_utc_iso()
            self._update_job(job_id, **update_payload)
        except Exception as exc:
            partial_result = getattr(exc, "partial_result", None)
            current = self._get_job(job_id)
            progress = current.get("progress", {}) if isinstance(current.get("progress"), dict) else {}
            self._update_job(
                job_id,
                status="failed",
                finished_at=now_utc_iso(),
                progress=self._build_progress(
                    message="failed",
                    model_id=str(progress.get("model_id", "")),
                    problem_id=str(progress.get("problem_id", "")),
                    attempt=int(progress.get("attempt", 0) or 0),
                    completed_cases=int(progress.get("completed_cases", 0) or 0),
                    total_cases=int(progress.get("total_cases", 0) or 0),
                    eta=progress.get("eta"),
                ),
                error=f"{exc}\n{traceback.format_exc()}",
                result=partial_result,
                pause_requested=False,
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
        existing_job = self._get_job(job_id)
        existing_run_id = str(existing_job.get("run_id", "")).strip()
        snapshot = self._load_run_snapshot(existing_run_id) if existing_run_id else None

        run_id = existing_run_id or utc_run_id()
        started_at = str((snapshot or {}).get("started_at", "")).strip() or now_utc_iso()
        run_root_value = str((snapshot or {}).get("run_root", "")).strip() or str(existing_job.get("run_root", "")).strip()
        if run_root_value:
            run_root = ensure_dir(Path(run_root_value))
        else:
            run_root = ensure_dir(self.base_config.get("run_root", "results/runs")) / run_id
        run_root = ensure_dir(run_root)

        model_records = [self._serialize_model_record(model) for model in models]
        if snapshot and snapshot.get("models"):
            saved_models = {
                self._model_selection_key(str(item.get("provider", "")), str(item.get("id", ""))): item
                for item in snapshot.get("models", [])
                if str(item.get("id", "")).strip()
            }
            model_records = [
                saved_models.get(self._model_selection_key(model.provider, model.id), self._serialize_model_record(model))
                for model in models
            ]
        self._update_job(
            job_id,
            run_id=run_id,
            run_root=str(run_root.resolve()),
            resolved_models=model_records,
        )
        evaluator = Evaluator(str(run_root), ui_config.get("execution", {}))
        model_runner = ModelRunner(ui_config.get("generation", {}))
        max_iterations = int(self.base_config.get("max_iterations", 1))

        case_records = list((snapshot or {}).get("cases", []))
        problem_snapshots = list((snapshot or {}).get("problems", [])) or [asdict(problem) for problem in problems]
        completed_cases = select_final_cases(case_records)
        completed_keys = {
            (str(case.get("model_id", "")).strip(), str(case.get("problem_id", "")).strip())
            for case in completed_cases
            if str(case.get("model_id", "")).strip() and str(case.get("problem_id", "")).strip()
        }
        completed_keys = {
            (model_id, problem_id)
            for model_id, problem_id in completed_keys
            if model_id in {model.id for model in models} and problem_id in {problem.id for problem in problems}
        }
        total_cases = len(models) * len(problems)
        eta_state = self._normalize_eta_state(self._get_job(job_id).get("eta_state", {}), ui_config)
        initial_eta = self._estimate_eta(eta_state, self._remaining_eta_problems(models, problems, completed_keys))
        self._update_job(
            job_id,
            progress=self._build_progress(
                message="running",
                completed_cases=len(completed_keys),
                total_cases=total_cases,
                eta=initial_eta,
            ),
            eta_state=eta_state,
        )

        try:
            for model in models:
                for problem in problems:
                    paused_result = self._pause_run_if_requested(
                        job_id=job_id,
                        run_id=run_id,
                        started_at=started_at,
                        scope=scope,
                        custom_problem=custom_problem,
                        run_root=run_root,
                        problems=problem_snapshots,
                        model_records=model_records,
                        case_records=case_records,
                        update_board=update_board,
                        completed_cases=len(completed_keys),
                        total_cases=total_cases,
                        eta=self._estimate_eta(eta_state, self._remaining_eta_problems(models, problems, completed_keys)),
                    )
                    if paused_result is not None:
                        return paused_result
                    if (model.id, problem.id) in completed_keys:
                        continue
                    feedback = ""
                    final = None
                    can_evaluate, reason = self._can_evaluate_problem(problem)
                    case_started_at = time.monotonic()
                    case_generation_seconds = 0.0
                    case_evaluation_seconds = 0.0
                    case_output_tokens = 0
                    case_token_seconds = 0.0

                    for attempt in range(1, max_iterations + 1):
                        attempt_eta = self._estimate_eta(eta_state, self._remaining_eta_problems(models, problems, completed_keys))
                        self._update_job(
                            job_id,
                            progress=self._build_progress(
                                message="running",
                                model_id=model.id,
                                problem_id=problem.id,
                                attempt=attempt,
                                completed_cases=len(completed_keys),
                                total_cases=total_cases,
                                eta=attempt_eta,
                            ),
                        )
                        attempt_started_at = time.monotonic()
                        candidate = model_runner.generate(model, problem, feedback=feedback)
                        trace_metrics = (
                            model_runner.last_trace.get("metrics", {})
                            if isinstance(model_runner.last_trace.get("metrics"), dict)
                            else {}
                        )
                        generation_seconds = max(
                            0.0,
                            self._coerce_progress_float(trace_metrics.get("duration_seconds"), 0.0),
                        )
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

                        attempt_elapsed = max(generation_seconds, time.monotonic() - attempt_started_at)
                        evaluation_seconds = max(0.0, attempt_elapsed - generation_seconds)
                        completion_tokens = self._coerce_progress_int(trace_metrics.get("completion_tokens"), -1)
                        if completion_tokens < 0:
                            completion_tokens = self._coerce_progress_int(trace_metrics.get("estimated_completion_tokens"), 0)
                        case_generation_seconds += generation_seconds
                        case_evaluation_seconds += evaluation_seconds
                        case_output_tokens += max(0, completion_tokens)
                        if generation_seconds > 0 and completion_tokens > 0:
                            case_token_seconds += generation_seconds
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
                        self._persist_run_snapshot(
                            run_id=run_id,
                            started_at=started_at,
                            scope=scope,
                            custom_problem=custom_problem,
                            run_root=run_root,
                            problems=problem_snapshots,
                            model_records=model_records,
                            case_records=case_records,
                            status="running",
                            error="",
                            update_board=update_board,
                        )
                        if result.passed or not can_evaluate:
                            break

                    if final is None:
                        continue
                    case_seconds = max(0.0, time.monotonic() - case_started_at)
                    eta_state = self._record_eta_observation(
                        eta_state=eta_state,
                        problem=problem,
                        attempts=int(getattr(final, "attempt", 0) or 0),
                        generation_seconds=case_generation_seconds,
                        evaluation_seconds=case_evaluation_seconds,
                        case_seconds=case_seconds,
                        output_tokens=case_output_tokens,
                        token_seconds=case_token_seconds,
                    )
                    completed_keys.add((model.id, problem.id))
                    remaining_eta = self._estimate_eta(eta_state, self._remaining_eta_problems(models, problems, completed_keys))
                    self._update_job(
                        job_id,
                        progress=self._build_progress(
                            message="running",
                            model_id=model.id,
                            problem_id=problem.id,
                            attempt=int(getattr(final, "attempt", 0) or 0),
                            completed_cases=len(completed_keys),
                            total_cases=total_cases,
                            eta=remaining_eta,
                        ),
                        eta_state=eta_state,
                    )

            run_result = self._persist_run_snapshot(
                run_id=run_id,
                started_at=started_at,
                scope=scope,
                custom_problem=custom_problem,
                run_root=run_root,
                problems=problem_snapshots,
                model_records=model_records,
                case_records=case_records,
                status="completed",
                error="",
                update_board=update_board,
            )

            return run_result
        except Exception as exc:
            partial_result = self._persist_run_snapshot(
                run_id=run_id,
                started_at=started_at,
                scope=scope,
                custom_problem=custom_problem,
                run_root=run_root,
                problems=problem_snapshots,
                model_records=model_records,
                case_records=case_records,
                status="failed",
                error=str(exc),
                update_board=update_board,
            )
            raise RunExecutionError(str(exc), partial_result=partial_result) from exc

    def _build_run_result(
        self,
        run_id: str,
        started_at: str,
        scope: str,
        custom_problem: bool,
        run_root: Path,
        problems: list[dict[str, Any]],
        model_records: list[dict[str, Any]],
        case_records: list[dict[str, Any]],
        status: str,
        error: str,
        finished_at: str = "",
    ) -> dict[str, Any]:
        scored = build_suite_leaderboard(
            case_records,
            problems=problems,
            scoring_config=self.base_config.get("scoring", {}),
        )
        run_result = {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "error": error,
            "source": "webui",
            "scope": scope,
            "custom_problem": custom_problem,
            "problem_filters": dict(self.base_config.get("problem_filters", {})),
            "problem_ids": [str(problem.get("id", "")) for problem in problems],
            "problems": problems,
            "models": model_records,
            "cases": scored["cases"],
            "summary": scored["summary"],
            "slice_rankings": scored["slice_rankings"],
            "scoring_policy": scored["scoring_policy"],
            "run_root": str(run_root.resolve()),
        }
        return run_result

    def _persist_run_snapshot(
        self,
        run_id: str,
        started_at: str,
        scope: str,
        custom_problem: bool,
        run_root: Path,
        problems: list[dict[str, Any]],
        model_records: list[dict[str, Any]],
        case_records: list[dict[str, Any]],
        status: str,
        error: str,
        update_board: bool,
    ) -> dict[str, Any]:
        run_result = self._build_run_result(
            run_id=run_id,
            started_at=started_at,
            scope=scope,
            custom_problem=custom_problem,
            run_root=run_root,
            problems=problems,
            model_records=model_records,
            case_records=case_records,
            status=status,
            error=error,
            finished_at=now_utc_iso() if status in {"completed", "failed"} else "",
        )
        self._write_run_result(run_result, update_board=update_board)
        return run_result

    def _write_run_result(self, run_result: dict[str, Any], update_board: bool) -> None:
        raw_dir = ensure_dir(self.base_config.get("raw_results_dir", "results/raw"))
        save_json(raw_dir / f"{run_result['run_id']}.json", run_result)
        if not update_board:
            return
        try:
            update_leaderboard(
                self.base_config.get("leaderboard_path", "results/leaderboard.json"),
                str(run_result.get("run_id", "")),
                list(run_result.get("summary", [])),
                scope=str(run_result.get("scope", "suite")),
                problem_ids=list(run_result.get("problem_ids", [])),
                slice_rankings=dict(run_result.get("slice_rankings", {})),
                scoring_policy=dict(run_result.get("scoring_policy", {})),
                raw_results_dir=self.base_config.get("raw_results_dir", "results/raw"),
                reset_after=self._leaderboard_reset_at(),
                custom_problem=bool(run_result.get("custom_problem", False)),
            )
        except Exception:
            # Raw run snapshots are the source of truth. If leaderboard rebuild fails,
            # keep the run persisted and let a later refresh rebuild derived state.
            return

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
            return problems, scope, scope_updates_leaderboard(scope)

        if scope == "custom_problem":
            custom_payload = dict(request.get("customProblem", {}))
            problem = self._custom_problem_from_payload(custom_payload)
            return [problem], scope, scope_updates_leaderboard(scope, custom_problem=True)

        return list(all_problems.values()), "suite", scope_updates_leaderboard("suite")

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

    def _new_eta_state(self, ui_config: Any = None) -> dict[str, Any]:
        config = ui_config if isinstance(ui_config, dict) else {}
        generation = config.get("generation", {}) if isinstance(config.get("generation"), dict) else {}
        execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}
        generation_timeout = max(1, int(generation.get("timeout_seconds", 60) or 60))
        execution_timeout = max(1, int(execution.get("timeout_seconds", 20) or 20))
        return {
            "generation_timeout_seconds": generation_timeout,
            "execution_timeout_seconds": execution_timeout,
            "baseline_generation_seconds": round(max(4.0, min(90.0, generation_timeout * 0.35)), 1),
            "baseline_evaluation_seconds": round(max(3.0, min(60.0, execution_timeout * 0.7)), 1),
            "observed_cases": 0,
            "observed_attempts": 0,
            "total_generation_seconds": 0.0,
            "total_evaluation_seconds": 0.0,
            "total_case_seconds": 0.0,
            "total_complexity_units": 0.0,
            "total_output_tokens": 0,
            "token_seconds": 0.0,
        }

    def _normalize_eta_state(self, value: Any, ui_config: Any = None) -> dict[str, Any]:
        baseline = self._new_eta_state(ui_config)
        payload = value if isinstance(value, dict) else {}
        baseline["generation_timeout_seconds"] = max(
            1,
            self._coerce_progress_int(payload.get("generation_timeout_seconds"), baseline["generation_timeout_seconds"]),
        )
        baseline["execution_timeout_seconds"] = max(
            1,
            self._coerce_progress_int(payload.get("execution_timeout_seconds"), baseline["execution_timeout_seconds"]),
        )
        baseline["baseline_generation_seconds"] = round(
            max(1.0, self._coerce_progress_float(payload.get("baseline_generation_seconds"), baseline["baseline_generation_seconds"])),
            1,
        )
        baseline["baseline_evaluation_seconds"] = round(
            max(1.0, self._coerce_progress_float(payload.get("baseline_evaluation_seconds"), baseline["baseline_evaluation_seconds"])),
            1,
        )
        baseline["observed_cases"] = max(0, self._coerce_progress_int(payload.get("observed_cases"), 0))
        baseline["observed_attempts"] = max(0, self._coerce_progress_int(payload.get("observed_attempts"), 0))
        baseline["total_generation_seconds"] = round(
            max(0.0, self._coerce_progress_float(payload.get("total_generation_seconds"), 0.0)),
            3,
        )
        baseline["total_evaluation_seconds"] = round(
            max(0.0, self._coerce_progress_float(payload.get("total_evaluation_seconds"), 0.0)),
            3,
        )
        baseline["total_case_seconds"] = round(max(0.0, self._coerce_progress_float(payload.get("total_case_seconds"), 0.0)), 3)
        baseline["total_complexity_units"] = round(
            max(0.0, self._coerce_progress_float(payload.get("total_complexity_units"), 0.0)),
            3,
        )
        baseline["total_output_tokens"] = max(0, self._coerce_progress_int(payload.get("total_output_tokens"), 0))
        baseline["token_seconds"] = round(max(0.0, self._coerce_progress_float(payload.get("token_seconds"), 0.0)), 3)
        return baseline

    def _normalize_eta_progress(self, value: Any) -> dict[str, Any]:
        payload = value if isinstance(value, dict) else {}
        seconds = max(0, self._coerce_progress_int(payload.get("seconds"), 0))
        return {
            "seconds": seconds,
            "label": str(payload.get("label", self._format_eta_seconds(seconds) if seconds else "")),
            "confidence": str(payload.get("confidence", "")),
            "basis": str(payload.get("basis", "")),
            "token_rate_tps": round(max(0.0, self._coerce_progress_float(payload.get("token_rate_tps"), 0.0)), 2),
            "avg_case_seconds": round(max(0.0, self._coerce_progress_float(payload.get("avg_case_seconds"), 0.0)), 1),
            "avg_generation_seconds": round(max(0.0, self._coerce_progress_float(payload.get("avg_generation_seconds"), 0.0)), 1),
            "avg_evaluation_seconds": round(max(0.0, self._coerce_progress_float(payload.get("avg_evaluation_seconds"), 0.0)), 1),
            "complexity_factor": round(max(0.0, self._coerce_progress_float(payload.get("complexity_factor"), 0.0)), 2),
        }

    def _build_progress(
        self,
        message: str,
        model_id: str = "",
        problem_id: str = "",
        attempt: int = 0,
        completed_cases: int = 0,
        total_cases: int = 0,
        eta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        completed = max(0, int(completed_cases or 0))
        total = max(0, int(total_cases or 0))
        remaining = max(0, total - completed)
        percent = round((completed / total) * 100.0, 1) if total else 0.0
        return {
            "message": str(message or ""),
            "model_id": str(model_id or ""),
            "problem_id": str(problem_id or ""),
            "attempt": int(attempt or 0),
            "completed_cases": completed,
            "total_cases": total,
            "remaining_cases": remaining,
            "percent": percent,
            "eta": self._normalize_eta_progress(eta),
        }

    def _remaining_eta_problems(
        self,
        models: list[ModelDescriptor],
        problems: list[Problem],
        completed_keys: set[tuple[str, str]],
    ) -> list[Problem]:
        remaining: list[Problem] = []
        for model in models:
            for problem in problems:
                if (model.id, problem.id) in completed_keys:
                    continue
                remaining.append(problem)
        return remaining

    def _problem_complexity_units(self, problem: Problem) -> float:
        difficulty_units = {
            "easy": 0.85,
            "medium": 1.0,
            "hard": 1.25,
        }
        units = difficulty_units.get(str(problem.difficulty or "").strip().lower(), 1.0)
        prompt_size = len(problem.prompt or "")
        harness_size = len(problem.testbench or problem.reference_tb or "")
        reference_size = len(problem.reference_rtl or problem.golden_rtl or "")
        units += min(0.35, prompt_size / 5000.0)
        units += min(0.30, harness_size / 7000.0)
        units += min(0.25, reference_size / 9000.0)
        units += 0.12 * min(4, len(problem.mutant_rtls or []))
        units += 0.05 * min(4, len(problem.evaluation_targets or []))
        if str(problem.task_type or "") == "testbench":
            units += 0.2
        return round(max(0.65, units), 3)

    def _record_eta_observation(
        self,
        eta_state: dict[str, Any],
        problem: Problem,
        attempts: int,
        generation_seconds: float,
        evaluation_seconds: float,
        case_seconds: float,
        output_tokens: int,
        token_seconds: float,
    ) -> dict[str, Any]:
        normalized = self._normalize_eta_state(eta_state)
        complexity_units = self._problem_complexity_units(problem)
        normalized["observed_cases"] += 1
        normalized["observed_attempts"] += max(1, int(attempts or 0))
        normalized["total_generation_seconds"] = round(
            float(normalized["total_generation_seconds"]) + max(0.0, generation_seconds),
            3,
        )
        normalized["total_evaluation_seconds"] = round(
            float(normalized["total_evaluation_seconds"]) + max(0.0, evaluation_seconds),
            3,
        )
        normalized["total_case_seconds"] = round(float(normalized["total_case_seconds"]) + max(0.0, case_seconds), 3)
        normalized["total_complexity_units"] = round(
            float(normalized["total_complexity_units"]) + max(0.1, complexity_units),
            3,
        )
        normalized["total_output_tokens"] = int(normalized["total_output_tokens"]) + max(0, int(output_tokens or 0))
        normalized["token_seconds"] = round(float(normalized["token_seconds"]) + max(0.0, token_seconds), 3)
        return normalized

    def _estimate_eta(self, eta_state: dict[str, Any], remaining_problems: list[Problem]) -> dict[str, Any]:
        normalized = self._normalize_eta_state(eta_state)
        if not remaining_problems:
            return self._normalize_eta_progress({"seconds": 0, "label": "0s", "confidence": "high", "basis": "complete"})

        observed_cases = max(0, int(normalized["observed_cases"]))
        total_complexity = max(0.0, float(normalized["total_complexity_units"]))
        baseline_generation = float(normalized["baseline_generation_seconds"])
        baseline_evaluation = float(normalized["baseline_evaluation_seconds"])
        token_seconds = max(0.0, float(normalized["token_seconds"]))
        total_output_tokens = max(0, int(normalized["total_output_tokens"]))

        if observed_cases > 0 and total_complexity > 0:
            avg_case_seconds = float(normalized["total_case_seconds"]) / observed_cases
            avg_generation_seconds = float(normalized["total_generation_seconds"]) / observed_cases
            avg_evaluation_seconds = float(normalized["total_evaluation_seconds"]) / observed_cases
            generation_per_unit = float(normalized["total_generation_seconds"]) / total_complexity
            evaluation_per_unit = float(normalized["total_evaluation_seconds"]) / total_complexity
            avg_complexity = total_complexity / observed_cases
        else:
            avg_case_seconds = baseline_generation + baseline_evaluation
            avg_generation_seconds = baseline_generation
            avg_evaluation_seconds = baseline_evaluation
            generation_per_unit = baseline_generation
            evaluation_per_unit = baseline_evaluation
            avg_complexity = 1.0

        token_rate_tps = (total_output_tokens / token_seconds) if token_seconds > 0 and total_output_tokens > 0 else 0.0
        output_tokens_per_unit = (total_output_tokens / total_complexity) if total_complexity > 0 and total_output_tokens > 0 else 0.0

        predicted_seconds = 0.0
        remaining_complexity = 0.0
        for problem in remaining_problems:
            complexity_units = self._problem_complexity_units(problem)
            remaining_complexity += complexity_units
            generation_prediction = complexity_units * generation_per_unit
            if token_rate_tps > 0 and output_tokens_per_unit > 0:
                token_generation_prediction = (complexity_units * output_tokens_per_unit) / token_rate_tps
                generation_prediction = (0.65 * token_generation_prediction) + (0.35 * generation_prediction)
            evaluation_prediction = complexity_units * evaluation_per_unit
            predicted_seconds += generation_prediction + evaluation_prediction

        first_complexity = self._problem_complexity_units(remaining_problems[0])
        confidence = "high" if observed_cases >= 3 else "medium" if observed_cases >= 1 else "low"
        basis = (
            f"{token_rate_tps:.1f} tok/s · gen {avg_generation_seconds:.1f}s · "
            f"eval {avg_evaluation_seconds:.1f}s · cx {first_complexity / max(0.1, avg_complexity):.2f}x"
            if token_rate_tps > 0
            else f"gen {avg_generation_seconds:.1f}s · eval {avg_evaluation_seconds:.1f}s · "
            f"cx {first_complexity / max(0.1, avg_complexity):.2f}x"
        )
        return self._normalize_eta_progress(
            {
                "seconds": int(round(max(0.0, predicted_seconds))),
                "label": self._format_eta_seconds(int(round(max(0.0, predicted_seconds)))),
                "confidence": confidence,
                "basis": basis,
                "token_rate_tps": token_rate_tps,
                "avg_case_seconds": avg_case_seconds,
                "avg_generation_seconds": avg_generation_seconds,
                "avg_evaluation_seconds": avg_evaluation_seconds,
                "complexity_factor": first_complexity / max(0.1, avg_complexity),
            }
        )

    def _format_eta_seconds(self, seconds: int) -> str:
        total = max(0, int(seconds or 0))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"

    def _coerce_progress_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _coerce_progress_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _pause_run_if_requested(
        self,
        job_id: str,
        run_id: str,
        started_at: str,
        scope: str,
        custom_problem: bool,
        run_root: Path,
        problems: list[dict[str, Any]],
        model_records: list[dict[str, Any]],
        case_records: list[dict[str, Any]],
        update_board: bool,
        completed_cases: int,
        total_cases: int,
        eta: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self._is_pause_requested(job_id):
            return None

        paused_result = self._persist_run_snapshot(
            run_id=run_id,
            started_at=started_at,
            scope=scope,
            custom_problem=custom_problem,
            run_root=run_root,
            problems=problems,
            model_records=model_records,
            case_records=case_records,
            status="paused",
            error="",
            update_board=update_board,
        )
        self._update_job(
            job_id,
            status="paused",
            finished_at="",
            progress=self._build_progress(
                message="paused",
                completed_cases=completed_cases,
                total_cases=total_cases,
                eta=eta,
            ),
            result=paused_result,
            pause_requested=False,
        )
        return paused_result

    def _is_pause_requested(self, job_id: str) -> bool:
        with self._jobs_lock:
            job = self._jobs.get(job_id, {})
            return bool(job.get("pause_requested", False))

    def _load_jobs_state(self) -> dict[str, dict[str, Any]]:
        payload = load_json(self.jobs_state_path, default={"jobs": []})
        rows = payload.get("jobs", []) if isinstance(payload, dict) else []
        jobs: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id", "")).strip()
            if not job_id:
                continue
            jobs[job_id] = self._normalize_loaded_job(row)
        return jobs

    def _normalize_loaded_job(self, row: dict[str, Any]) -> dict[str, Any]:
        progress = row.get("progress", {}) if isinstance(row.get("progress"), dict) else {}
        request = self._sanitize_job_request(row.get("request", {}) if isinstance(row.get("request"), dict) else {})
        return {
            "job_id": str(row.get("job_id", "")).strip(),
            "status": str(row.get("status", "queued") or "queued"),
            "submitted_at": str(row.get("submitted_at", "")),
            "started_at": str(row.get("started_at", "")),
            "finished_at": str(row.get("finished_at", "")),
            "updated_at": str(row.get("updated_at", "")),
            "error": str(row.get("error", "")),
            "progress": {
                "message": str(progress.get("message", "")),
                "model_id": str(progress.get("model_id", "")),
                "problem_id": str(progress.get("problem_id", "")),
                "attempt": int(progress.get("attempt", 0) or 0),
                "completed_cases": int(progress.get("completed_cases", 0) or 0),
                "total_cases": int(progress.get("total_cases", 0) or 0),
                "remaining_cases": int(progress.get("remaining_cases", 0) or 0),
                "percent": float(progress.get("percent", 0.0) or 0.0),
                "eta": self._normalize_eta_progress(progress.get("eta", {})),
            },
            "request": dict(request),
            "run_id": str(row.get("run_id", "")),
            "run_root": str(row.get("run_root", "")),
            "resolved_models": list(row.get("resolved_models", [])) if isinstance(row.get("resolved_models"), list) else [],
            "resume_count": int(row.get("resume_count", 0) or 0),
            "eta_state": self._normalize_eta_state(row.get("eta_state", {}), request.get("uiConfig", {})),
            "pause_requested": bool(row.get("pause_requested", False)),
        }

    def _persist_jobs_locked(self) -> None:
        rows = []
        for job in self._jobs.values():
            row = self._normalize_loaded_job(job)
            rows.append(row)
        rows.sort(key=lambda item: item.get("submitted_at", ""), reverse=True)
        save_json(self.jobs_state_path, {"jobs": rows})

    def _with_job_result(self, job: dict[str, Any]) -> dict[str, Any]:
        result = self._load_run_snapshot(str(job.get("run_id", "")).strip())
        if result is not None:
            job["result"] = result
        elif isinstance(job.get("result"), dict):
            job["result"] = dict(job["result"])
        else:
            job["result"] = None
        return job

    def _get_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            return dict(self._jobs.get(job_id, {}))

    def _load_or_discover_job_models(
        self,
        job_id: str,
        ui_config: dict[str, Any],
        selected_models_payload: list[Any] | None = None,
    ) -> list[ModelDescriptor]:
        existing_job = self._get_job(job_id)
        saved_models = existing_job.get("resolved_models", [])
        if isinstance(saved_models, list) and saved_models:
            models = []
            for item in saved_models:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id", "")).strip()
                provider = str(item.get("provider", "")).strip()
                if not model_id or not provider:
                    continue
                models.append(
                    ModelDescriptor(
                        id=model_id,
                        provider=provider,
                        released_at=str(item.get("released_at", "")),
                        capability=str(item.get("capability", "unknown") or "unknown"),
                        raw=dict(item.get("raw", {})) if isinstance(item.get("raw"), dict) else {},
                    )
                )
            if models:
                filtered = self._filter_models_for_request(models, selected_models_payload)
                if filtered:
                    return self._attach_runtime_credentials(filtered, ui_config)

        sources = self._build_sources(ui_config)
        models = discover_models(
            sources=sources,
            state_path=self.base_config.get("state_path", ".state/known_models.json"),
            include_known=True,
            selection={},
            update_state=False,
        )
        models = self._filter_models_for_request(models, selected_models_payload)
        models = self._attach_runtime_credentials(models, ui_config)
        if models:
            self._update_job(job_id, resolved_models=[self._serialize_model_record(model) for model in models])
        return models

    def _resolve_runtime_ui_config(self, request: dict[str, Any]) -> dict[str, Any]:
        ui_config = self._normalize_ui_config(request.get("uiConfig") or self.load_ui_config())
        saved_config = self.load_ui_config()
        saved_by_provider = {
            str(provider.get("provider", "")).strip(): provider
            for provider in saved_config.get("providers", [])
            if str(provider.get("provider", "")).strip()
        }
        for provider in ui_config.get("providers", []):
            if str(provider.get("api_key", "")).strip():
                continue
            saved = saved_by_provider.get(str(provider.get("provider", "")).strip())
            if saved is not None:
                provider["api_key"] = str(saved.get("api_key", ""))
        return ui_config

    def _sanitize_job_request(self, request: dict[str, Any]) -> dict[str, Any]:
        safe = dict(request) if isinstance(request, dict) else {}
        safe["uiConfig"] = self._sanitize_job_ui_config(safe.get("uiConfig", {}))
        return safe

    def _sanitize_job_ui_config(self, config: Any) -> dict[str, Any]:
        safe = self._normalize_ui_config(config if isinstance(config, dict) else {})
        for provider in safe.get("providers", []):
            if isinstance(provider, dict):
                provider["api_key"] = ""
        return safe

    def _normalize_selected_models(self, payload: list[Any] | None) -> list[tuple[str, str]]:
        normalized: list[tuple[str, str]] = []
        for item in payload or []:
            provider = ""
            model_id = ""
            if isinstance(item, dict):
                provider = str(item.get("provider", "")).strip()
                model_id = str(item.get("model_id", item.get("id", ""))).strip()
            else:
                model_id = str(item).strip()
            if model_id:
                normalized.append((provider, model_id))
        return normalized

    def _filter_models_for_request(
        self,
        models: list[ModelDescriptor],
        selected_models_payload: list[Any] | None,
    ) -> list[ModelDescriptor]:
        selected = self._normalize_selected_models(selected_models_payload)
        if not selected:
            filtered = list(models)
        else:
            exact_keys = {
                self._model_selection_key(provider, model_id)
                for provider, model_id in selected
                if provider and model_id
            }
            loose_ids = {model_id for provider, model_id in selected if not provider and model_id}
            filtered = [
                model
                for model in models
                if self._model_selection_key(model.provider, model.id) in exact_keys or model.id in loose_ids
            ]

        seen_ids: dict[str, str] = {}
        for model in filtered:
            previous_provider = seen_ids.get(model.id)
            if previous_provider is not None and previous_provider != model.provider:
                raise ValueError(f"Selected models must have unique ids across providers: {model.id}")
            seen_ids[model.id] = model.provider
        return filtered

    def _attach_runtime_credentials(self, models: list[ModelDescriptor], ui_config: dict[str, Any]) -> list[ModelDescriptor]:
        providers = {
            str(provider.get("provider", "")).strip(): provider
            for provider in ui_config.get("providers", [])
            if str(provider.get("provider", "")).strip()
        }
        attached: list[ModelDescriptor] = []
        for model in models:
            provider = providers.get(model.provider, {})
            raw = dict(model.raw)
            api_key = str(provider.get("api_key", "")).strip()
            if api_key:
                raw["_api_key"] = api_key
            base_url = str(provider.get("base_url", "")).strip()
            if base_url:
                raw["_base_url"] = base_url.rstrip("/")
            api_key_env = str(provider.get("api_key_env", "")).strip()
            if api_key_env:
                raw["_api_key_env"] = api_key_env
            version = str(provider.get("version", "")).strip()
            if version:
                raw["_anthropic_version"] = version
            attached.append(
                ModelDescriptor(
                    id=model.id,
                    provider=model.provider,
                    released_at=model.released_at,
                    capability=model.capability,
                    raw=raw,
                )
            )
        return attached

    def _serialize_model_record(self, model: ModelDescriptor) -> dict[str, Any]:
        record = asdict(model)
        record["raw"] = self._sanitize_model_raw(record.get("raw", {}))
        return record

    def _sanitize_model_raw(self, raw: Any) -> dict[str, Any]:
        safe = dict(raw) if isinstance(raw, dict) else {}
        safe.pop("_api_key", None)
        safe.pop("api_key", None)
        return safe

    def _model_selection_key(self, provider: str, model_id: str) -> str:
        return f"{provider.strip()}::{model_id.strip()}"

    def _delete_run_root(self, run_root: str) -> None:
        target = Path(run_root).expanduser().resolve()
        allowed_root = ensure_dir(self.base_config.get("run_root", "results/runs")).resolve()
        if not self._is_within(target, allowed_root):
            raise ValueError("run root is outside the allowed output directory")
        if target.exists():
            shutil.rmtree(target)

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._jobs_lock:
            current = self._jobs.get(job_id, {})
            current.update(updates)
            current["updated_at"] = now_utc_iso()
            self._jobs[job_id] = current
            self._persist_jobs_locked()

    def _reconcile_jobs(self) -> None:
        changed = False
        with self._jobs_lock:
            for job_id, job in self._jobs.items():
                status = str(job.get("status", ""))
                if status not in {"running", "queued"}:
                    continue
                worker = self._job_threads.get(job_id)
                if worker is not None and worker.is_alive():
                    stale_seconds = self._job_stale_seconds(job)
                    last_update = self._parse_job_timestamp(
                        str(job.get("updated_at") or job.get("started_at") or job.get("submitted_at") or "")
                    )
                    if stale_seconds > 0 and last_update is not None:
                        age_seconds = (datetime.now(timezone.utc) - last_update).total_seconds()
                        if age_seconds <= stale_seconds:
                            continue
                        progress = job.get("progress", {}) if isinstance(job.get("progress"), dict) else {}
                        job["status"] = "failed"
                        job["finished_at"] = str(job.get("finished_at", "")) or now_utc_iso()
                        job["updated_at"] = now_utc_iso()
                        changed = True
                        if not str(job.get("error", "")).strip():
                            job["error"] = (
                                f"job stalled with no progress update for {int(age_seconds)}s "
                                f"(threshold {stale_seconds}s)"
                            )
                        job["progress"] = {
                            **self._build_progress(
                                message="failed",
                                model_id=str(progress.get("model_id", "")),
                                problem_id=str(progress.get("problem_id", "")),
                                attempt=int(progress.get("attempt", 0) or 0),
                                completed_cases=int(progress.get("completed_cases", 0) or 0),
                                total_cases=int(progress.get("total_cases", 0) or 0),
                                eta=progress.get("eta"),
                            )
                        }
                        snapshot = self._load_run_snapshot(str(job.get("run_id", "")).strip())
                        if snapshot is not None:
                            snapshot["status"] = "failed"
                            snapshot["finished_at"] = str(snapshot.get("finished_at", "")) or now_utc_iso()
                            snapshot["error"] = str(job.get("error", "")).strip()
                            self._write_run_result(snapshot, update_board=True)
                            job["result"] = snapshot
                    continue

                run_id = str(job.get("run_id", "")).strip()
                job["status"] = "failed"
                job["finished_at"] = str(job.get("finished_at", "")) or now_utc_iso()
                job["updated_at"] = now_utc_iso()
                changed = True
                if not str(job.get("error", "")).strip():
                    job["error"] = "worker thread ended without reporting completion"
                progress = job.get("progress", {}) if isinstance(job.get("progress"), dict) else {}
                job["progress"] = self._build_progress(
                    message="failed",
                    completed_cases=int(progress.get("completed_cases", 0) or 0),
                    total_cases=int(progress.get("total_cases", 0) or 0),
                    eta=progress.get("eta"),
                )
                snapshot = self._load_run_snapshot(run_id)
                if snapshot is not None:
                    snapshot["status"] = "failed"
                    snapshot["finished_at"] = str(snapshot.get("finished_at", "")) or str(job.get("finished_at", ""))
                    snapshot["error"] = str(job.get("error", "")).strip()
                    self._write_run_result(snapshot, update_board=True)
                    job["result"] = snapshot
            if changed:
                self._persist_jobs_locked()

    def _job_stale_seconds(self, job: dict[str, Any]) -> int:
        configured = int(self.base_config.get("job_stale_seconds", 0) or 0)
        if configured > 0:
            return configured

        request = job.get("request", {}) if isinstance(job.get("request"), dict) else {}
        ui_config = request.get("uiConfig", {}) if isinstance(request.get("uiConfig"), dict) else {}
        generation_cfg = ui_config.get("generation", {}) if isinstance(ui_config.get("generation"), dict) else {}
        execution_cfg = ui_config.get("execution", {}) if isinstance(ui_config.get("execution"), dict) else {}

        base_generation = self.base_config.get("generation", {}) if isinstance(self.base_config.get("generation"), dict) else {}
        base_execution = self.base_config.get("execution", {}) if isinstance(self.base_config.get("execution"), dict) else {}
        generation_timeout = int(generation_cfg.get("timeout_seconds", base_generation.get("timeout_seconds", 60)) or 60)
        execution_timeout = int(execution_cfg.get("timeout_seconds", base_execution.get("timeout_seconds", 20)) or 20)

        # Keep this generous because one attempt can include compile, run, synthesis,
        # and several mutant simulations before the web job reports progress again.
        return max(1800, generation_timeout + execution_timeout * 50)

    def _parse_job_timestamp(self, value: str) -> datetime | None:
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    def _load_run_snapshot(self, run_id: str) -> dict[str, Any] | None:
        if not run_id:
            return None
        raw_path = ensure_dir(self.base_config.get("raw_results_dir", "results/raw")) / f"{run_id}.json"
        if not raw_path.exists():
            return None
        return load_json(raw_path, default={})


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
        if path.startswith("/api/jobs/") and path.endswith("/resume"):
            job_id = unquote(path[len("/api/jobs/") : -len("/resume")]).strip("/")
            if not job_id:
                self._send_json({"error": "job id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                job = self.service.resume_job(job_id)
            except KeyError:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, "job": job}, status=HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/jobs/") and path.endswith("/pause"):
            job_id = unquote(path[len("/api/jobs/") : -len("/pause")]).strip("/")
            if not job_id:
                self._send_json({"error": "job id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                job = self.service.pause_job(job_id)
            except KeyError:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, "job": job}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/leaderboard/reset":
            board = self.service.reset_leaderboard()
            self._send_json({"ok": True, "leaderboard": board})
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/jobs/"):
            job_id = unquote(path[len("/api/jobs/") :]).strip("/")
            if not job_id:
                self._send_json({"error": "job id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                deleted = self.service.delete_job(job_id)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if not deleted:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True})
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
