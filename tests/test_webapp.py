from __future__ import annotations

import http.client
import json
import threading
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from rtl_benchmark.types import CaseResult, ModelDescriptor, Problem, StageStatus
from rtl_benchmark.webapp import RunExecutionError, WebAppService
from rtl_benchmark.utils import save_json


ROOT = Path("/Users/gary/RTL_benchmark")


class WebAppServiceTests(unittest.TestCase):
    def _write_base_config(self, root: Path) -> Path:
        config = {
            "problem_glob": str(ROOT / "benchmarks/**/*.json"),
            "max_iterations": 1,
            "generation": {"temperature": 0.0, "max_tokens": 256, "timeout_seconds": 15},
            "execution": {
                "mode": "docker",
                "timeout_seconds": 30,
                "docker_binary": "docker",
                "docker_image": "rtl-benchmark-tools:latest",
            },
            "run_root": str(root / "runs"),
            "raw_results_dir": str(root / "raw"),
            "leaderboard_path": str(root / "leaderboard.json"),
            "state_path": str(root / "known.json"),
            "sources": [
                {
                    "type": "openai",
                    "enabled": True,
                    "provider": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "OPENAI_API_KEY",
                    "models": [{"id": "gpt-4.1-mini"}],
                },
                {
                    "type": "gemini",
                    "enabled": False,
                    "provider": "gemini",
                    "base_url": "https://generativelanguage.googleapis.com/v1beta",
                    "api_key_env": "GEMINI_API_KEY",
                    "models": [{"id": "gemini-2.5-flash"}],
                },
            ],
        }
        path = root / "pipeline.web.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_default_ui_config_includes_provider_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            ui_config_path = tmp_path / "webui.json"

            service = WebAppService(str(config_path), str(ui_config_path))
            config = service.load_ui_config()

            provider_keys = [item["key"] for item in config["providers"]]
            self.assertIn("openai", provider_keys)
            self.assertIn("gemini", provider_keys)
            openai = next(item for item in config["providers"] if item["key"] == "openai")
            self.assertEqual(openai["models"], ["gpt-4.1-mini"])

    def test_list_problems_exposes_source_and_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))

            problems = service.list_problems()

            self.assertTrue(problems)
            first = problems[0]
            self.assertIn("source", first)
            self.assertIn("category", first)
            self.assertIn("suite", first)
            self.assertIn("track", first)
            self.assertIn("difficulty", first)
            self.assertIn("harness_type", first)
            self.assertIn("evaluation_targets", first)
            self.assertIn("has_harness", first)

    def test_history_is_sorted_by_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "older.json").write_text(
                json.dumps({"run_id": "older", "started_at": "2026-03-14T10:00:00Z", "models": [], "cases": [], "summary": []}),
                encoding="utf-8",
            )
            (raw_dir / "newer.json").write_text(
                json.dumps({"run_id": "newer", "started_at": "2026-03-14T11:00:00Z", "models": [], "cases": [], "summary": []}),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            history = service.list_history(limit=10)

            self.assertEqual([item["run_id"] for item in history[:2]], ["newer", "older"])

    def test_history_detail_is_enriched_with_problem_snapshots_and_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            run_root = tmp_path / "runs" / "demo_run" / "mock__rtl-strong-v1" / "rtl_add8" / "attempt_1"
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "simv_run.log").write_text("PASS", encoding="utf-8")

            (raw_dir / "demo_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "demo_run",
                        "started_at": "2026-03-14T11:00:00Z",
                        "finished_at": "2026-03-14T11:02:00Z",
                        "models": [{"id": "mock/rtl-strong-v1", "provider": "mock"}],
                        "problem_ids": ["rtl_add8"],
                        "cases": [
                            {
                                "model_id": "mock/rtl-strong-v1",
                                "provider": "mock",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "passed",
                                "candidate_code": "module add8; endmodule",
                            }
                        ],
                        "summary": [],
                    }
                ),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            detail = service.load_history_detail("demo_run")

            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertIn("overview", detail)
            self.assertEqual(detail["overview"]["case_count"], 1)
            self.assertIn("problems", detail)
            self.assertTrue(detail["problems"])
            self.assertIn("problem", detail["cases"][0])
            self.assertTrue(detail["cases"][0]["artifacts"])

    def test_load_leaderboard_includes_selected_problem_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "gemini_selected.json").write_text(
                json.dumps(
                    {
                        "run_id": "gemini_selected",
                        "started_at": "2026-03-14T14:22:33Z",
                        "scope": "selected_problems",
                        "problem_ids": ["rtl_add8"],
                        "problems": [
                            {
                                "id": "rtl_add8",
                                "task_type": "rtl",
                                "source": "rtl",
                                "suite": "rtl",
                                "category": "baseline",
                                "track": "rtl_core",
                                "difficulty": "easy",
                                "exposure": "public",
                                "tags": ["basic"],
                            }
                        ],
                        "cases": [
                            {
                                "model_id": "gemini-3.1-pro-preview",
                                "provider": "gemini",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "passed",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            save_json(service.leaderboard_state_path, {"reset_at": ""})
            board = service.load_leaderboard()

            self.assertEqual(len(board["models"]), 1)
            self.assertEqual(board["models"][0]["model_id"], "gemini-3.1-pro-preview")
            self.assertEqual(board["models"][0]["last_scope"], "selected_problems")
            self.assertEqual(board["updated_at"], "2026-03-14T14:22:33Z")

    def test_compare_models_uses_final_attempt_per_problem_and_groups_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "compare_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "compare_run",
                        "started_at": "2026-03-15T09:00:00Z",
                        "finished_at": "2026-03-15T09:04:00Z",
                        "models": [
                            {"id": "model_a", "provider": "mock"},
                            {"id": "model_b", "provider": "mock"},
                        ],
                        "problem_ids": ["rtl_add8", "rtl_edge_detect", "industrial_rr_arb4"],
                        "cases": [
                            {
                                "model_id": "model_a",
                                "provider": "mock",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": False,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "fail"},
                                "synthesis": {"status": "skipped"},
                                "feedback": "first attempt failed",
                            },
                            {
                                "model_id": "model_a",
                                "provider": "mock",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 2,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "fixed on retry",
                            },
                            {
                                "model_id": "model_b",
                                "provider": "mock",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": False,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "fail"},
                                "synthesis": {"status": "fail"},
                                "feedback": "still broken",
                            },
                            {
                                "model_id": "model_a",
                                "provider": "mock",
                                "problem_id": "rtl_edge_detect",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": False,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "fail"},
                                "synthesis": {"status": "pass"},
                                "feedback": "edge case missed",
                            },
                            {
                                "model_id": "model_b",
                                "provider": "mock",
                                "problem_id": "rtl_edge_detect",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "passes",
                            },
                            {
                                "model_id": "model_b",
                                "provider": "mock",
                                "problem_id": "industrial_rr_arb4",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "present only on model_b",
                            },
                        ],
                        "summary": [],
                    }
                ),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            compare = service.compare_models("compare_run", "model_a", "model_b")

            self.assertIsNotNone(compare)
            assert compare is not None
            self.assertEqual(compare["compare_mode"], "leaderboard")
            self.assertEqual(compare["model_a_run_id"], "compare_run")
            self.assertEqual(compare["model_b_run_id"], "compare_run")
            self.assertEqual(compare["summary"]["total_cases"], 3)
            self.assertEqual(compare["summary"]["comparable_cases"], 2)
            self.assertEqual(compare["summary"]["a_only_pass"], 1)
            self.assertEqual(compare["summary"]["b_only_pass"], 1)
            self.assertEqual(compare["summary"]["missing_a"], 1)
            self.assertEqual(compare["summary"]["model_a_passed"], 1)
            self.assertEqual(compare["summary"]["model_b_passed"], 2)

            add8_row = next(item for item in compare["rows"] if item["problem_id"] == "rtl_add8")
            self.assertEqual(add8_row["outcome"], "a_only_pass")
            self.assertEqual(add8_row["model_a"]["attempt"], 2)
            self.assertEqual(add8_row["model_a"]["status"], "pass")
            self.assertEqual(add8_row["model_b"]["status"], "fail")
            self.assertEqual(add8_row["model_a"]["run_id"], "compare_run")
            self.assertIn("sources", compare["slice_comparison"])
            self.assertIn("difficulties", compare["slice_comparison"])
            self.assertIn("tags", compare["slice_comparison"])

    def test_compare_models_uses_leaderboard_aggregate_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "run_a_old.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_a_old",
                        "started_at": "2026-03-15T09:00:00Z",
                        "finished_at": "2026-03-15T09:03:00Z",
                        "scope": "suite",
                        "models": [{"id": "model_a", "provider": "mock"}],
                        "problem_ids": ["rtl_add8"],
                        "cases": [
                            {
                                "model_id": "model_a",
                                "provider": "mock",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "good",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (raw_dir / "run_a_new.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_a_new",
                        "started_at": "2026-03-16T09:00:00Z",
                        "finished_at": "2026-03-16T09:04:00Z",
                        "scope": "suite",
                        "models": [{"id": "model_a", "provider": "mock"}],
                        "problem_ids": ["rtl_edge_detect"],
                        "cases": [
                            {
                                "model_id": "model_a",
                                "provider": "mock",
                                "problem_id": "rtl_edge_detect",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": False,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "fail"},
                                "synthesis": {"status": "pass"},
                                "feedback": "missed edge",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (raw_dir / "run_b.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_b",
                        "started_at": "2026-03-16T10:00:00Z",
                        "finished_at": "2026-03-16T10:05:00Z",
                        "scope": "suite",
                        "models": [{"id": "model_b", "provider": "mock"}],
                        "problem_ids": ["rtl_add8", "industrial_rr_arb4"],
                        "cases": [
                            {
                                "model_id": "model_b",
                                "provider": "mock",
                                "problem_id": "rtl_add8",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": False,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "fail"},
                                "synthesis": {"status": "fail"},
                                "feedback": "broken add8",
                            },
                            {
                                "model_id": "model_b",
                                "provider": "mock",
                                "problem_id": "industrial_rr_arb4",
                                "task_type": "rtl",
                                "attempt": 1,
                                "passed": True,
                                "lint": {"status": "pass"},
                                "simulation": {"status": "pass"},
                                "synthesis": {"status": "pass"},
                                "feedback": "present only on model_b",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            save_json(service.leaderboard_state_path, {"reset_at": ""})
            compare = service.compare_models("run_a_new", "model_a", "model_b")

            self.assertIsNotNone(compare)
            assert compare is not None
            self.assertEqual(compare["compare_mode"], "leaderboard")
            self.assertEqual(compare["requested_run_id"], "run_a_new")
            self.assertEqual(compare["model_a_run_id"], "run_a_new")
            self.assertEqual(compare["model_b_run_id"], "run_b")
            self.assertEqual(compare["summary"]["total_cases"], 3)
            self.assertEqual(compare["summary"]["comparable_cases"], 1)
            self.assertEqual(compare["summary"]["a_only_pass"], 1)
            self.assertEqual(compare["summary"]["b_only_pass"], 0)
            self.assertEqual(compare["summary"]["both_fail"], 0)
            self.assertEqual(compare["summary"]["missing_a"], 1)
            self.assertEqual(compare["summary"]["missing_b"], 1)

            add8_row = next(item for item in compare["rows"] if item["problem_id"] == "rtl_add8")
            self.assertEqual(add8_row["model_a"]["run_id"], "run_a_old")
            self.assertEqual(add8_row["model_b"]["run_id"], "run_b")
            self.assertEqual(add8_row["outcome"], "a_only_pass")

    def test_reset_leaderboard_sets_new_baseline_and_hides_older_suite_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "suite_old.json").write_text(
                json.dumps(
                    {
                        "run_id": "suite_old",
                        "started_at": "2026-03-14T09:00:00Z",
                        "scope": "suite",
                        "problem_ids": ["rtl_add8"],
                        "summary": [
                            {
                                "model_id": "suite_model",
                                "provider": "mock",
                                "score": 1.0,
                                "pass_rate": 1.0,
                                "cases": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            save_json(service.leaderboard_state_path, {"reset_at": "2026-03-15T00:00:00Z"})

            board = service.load_leaderboard()
            self.assertEqual(board["models"], [])
            self.assertEqual(board["reset_at"], "2026-03-15T00:00:00Z")

            reset_board = service.reset_leaderboard()
            self.assertEqual(reset_board["models"], [])
            self.assertTrue(reset_board["reset_at"])

    def test_custom_problem_without_harness_skips_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))

            problem = service._custom_problem_from_payload(
                {
                    "id": "custom_prompt_only",
                    "task_type": "rtl",
                    "language": "verilog",
                    "top_module": "adder",
                    "prompt": "Implement a simple adder.",
                }
            )
            can_evaluate, reason = service._can_evaluate_problem(problem)

            self.assertFalse(can_evaluate)
            self.assertIn("testbench", reason)

    def test_selected_problem_runs_update_leaderboard_but_custom_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))

            problems, scope, update_board = service._resolve_requested_problems(
                {"scope": "selected_problems", "problemIds": ["rtl_add8"]}
            )
            self.assertEqual(scope, "selected_problems")
            self.assertTrue(problems)
            self.assertTrue(update_board)

            custom, custom_scope, custom_update_board = service._resolve_requested_problems(
                {"scope": "custom_problem", "customProblem": {"prompt": "Implement x", "top_module": "x"}}
            )
            self.assertEqual(custom_scope, "custom_problem")
            self.assertEqual(len(custom), 1)
            self.assertFalse(custom_update_board)

            suite, suite_scope, suite_update_board = service._resolve_requested_problems({"scope": "suite"})
            self.assertEqual(suite_scope, "suite")
            self.assertTrue(suite)
            self.assertTrue(suite_update_board)

    def test_write_run_result_keeps_snapshot_when_leaderboard_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            run_result = {
                "run_id": "resilient_run",
                "started_at": "2026-03-18T05:40:30Z",
                "finished_at": "",
                "status": "running",
                "error": "",
                "source": "webui",
                "scope": "selected_problems",
                "custom_problem": False,
                "problem_ids": ["rtl_add8"],
                "problems": [
                    {
                        "id": "rtl_add8",
                        "task_type": "rtl",
                        "source": "rtl",
                        "suite": "rtl",
                        "category": "baseline",
                        "track": "rtl_core",
                        "difficulty": "easy",
                        "exposure": "public",
                        "tags": ["basic"],
                    }
                ],
                "models": [{"id": "gpt-4.1-mini", "provider": "openai"}],
                "cases": [],
                "summary": [],
                "slice_rankings": {},
                "scoring_policy": {},
                "run_root": str(tmp_path / "runs" / "resilient_run"),
            }

            with patch("rtl_benchmark.webapp.update_leaderboard", side_effect=RuntimeError("boom")):
                service._write_run_result(run_result, update_board=True)

            saved = json.loads((tmp_path / "raw" / "resilient_run.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["run_id"], "resilient_run")
            self.assertEqual(saved["status"], "running")

    def test_ui_config_clamps_excessive_max_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))

            saved = service.save_ui_config(
                {
                    "providers": [],
                    "generation": {"temperature": 0.0, "max_tokens": 1048576, "timeout_seconds": 15},
                    "execution": {"mode": "docker", "timeout_seconds": 30},
                }
            )

            self.assertEqual(saved["generation"]["max_tokens"], 8192)

    def test_start_job_persists_sanitized_request_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.jobs_state_path = tmp_path / "web_jobs.json"

            request = {
                "scope": "selected_problems",
                "problemIds": [],
                "selectedModels": [{"provider": "openai", "model_id": "gpt-4.1-mini"}],
                "uiConfig": {
                    "providers": [
                        {
                            "key": "openai",
                            "provider": "openai",
                            "enabled": True,
                            "api_key_env": "OPENAI_API_KEY",
                            "api_key": "inline-secret",
                            "base_url": "https://api.openai.com/v1",
                            "models": ["gpt-4.1-mini"],
                        }
                    ],
                    "generation": {},
                    "execution": {},
                },
            }

            with patch("rtl_benchmark.webapp.threading.Thread") as thread_cls:
                thread_cls.return_value.is_alive.return_value = False
                job = service.start_job(request)

            saved = json.loads(service.jobs_state_path.read_text(encoding="utf-8"))
            saved_provider = saved["jobs"][0]["request"]["uiConfig"]["providers"][0]
            stored_provider = service._get_job(job["job_id"])["request"]["uiConfig"]["providers"][0]

            self.assertEqual(saved_provider["api_key"], "")
            self.assertEqual(stored_provider["api_key"], "")

    def test_load_jobs_state_strips_request_api_key_and_rehydrates_runtime_ui_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            ui_config_path = tmp_path / "webui.json"
            service = WebAppService(str(config_path), str(ui_config_path))
            service.jobs_state_path = tmp_path / "web_jobs.json"
            service.save_ui_config(
                {
                    "providers": [
                        {
                            "key": "openai",
                            "provider": "openai",
                            "enabled": True,
                            "api_key_env": "OPENAI_API_KEY",
                            "api_key": "inline-secret",
                            "base_url": "https://api.openai.com/v1",
                            "models": ["gpt-4.1-mini"],
                        }
                    ],
                    "generation": {},
                    "execution": {},
                }
            )
            save_json(
                service.jobs_state_path,
                {
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "failed",
                            "submitted_at": "2026-03-17T13:18:12Z",
                            "started_at": "2026-03-17T13:18:12Z",
                            "updated_at": "2026-03-17T13:43:37Z",
                            "finished_at": "2026-03-17T13:43:37Z",
                            "error": "forced stop",
                            "progress": {"message": "failed"},
                            "request": {
                                "scope": "selected_problems",
                                "problemIds": [],
                                "selectedModels": [{"provider": "openai", "model_id": "gpt-4.1-mini"}],
                                "uiConfig": {
                                    "providers": [
                                        {
                                            "key": "openai",
                                            "provider": "openai",
                                            "enabled": True,
                                            "api_key_env": "OPENAI_API_KEY",
                                            "api_key": "inline-secret",
                                            "base_url": "https://api.openai.com/v1",
                                            "models": ["gpt-4.1-mini"],
                                        }
                                    ],
                                    "generation": {},
                                    "execution": {},
                                },
                            },
                            "resolved_models": [{"id": "gpt-4.1-mini", "provider": "openai"}],
                        }
                    ]
                },
            )

            restarted = WebAppService(str(config_path), str(ui_config_path))
            restarted.jobs_state_path = tmp_path / "web_jobs.json"
            restarted._jobs = restarted._load_jobs_state()

            stored_request = restarted._get_job("job-1")["request"]
            stored_provider = stored_request["uiConfig"]["providers"][0]
            runtime_provider = restarted._resolve_runtime_ui_config(stored_request)["providers"][0]

            self.assertEqual(stored_provider["api_key"], "")
            self.assertEqual(runtime_provider["api_key"], "inline-secret")

    def test_load_or_discover_job_models_respects_run_selection_and_does_not_persist_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {"job_id": "job-1", "status": "queued", "progress": {}}

            ui_config = service._normalize_ui_config(
                {
                    "providers": [
                        {
                            "key": "openai",
                            "provider": "openai",
                            "enabled": True,
                            "api_key_env": "OPENAI_API_KEY",
                            "api_key": "inline-secret",
                            "base_url": "https://api.openai.com/v1",
                            "models": ["gpt-4.1-mini", "o3-mini"],
                        }
                    ],
                    "generation": {},
                    "execution": {},
                }
            )

            models = service._load_or_discover_job_models(
                "job-1",
                ui_config,
                [{"provider": "openai", "model_id": "o3-mini"}],
            )
            saved_job = service._get_job("job-1")

            self.assertEqual([model.id for model in models], ["o3-mini"])
            self.assertEqual(models[0].raw["_api_key"], "inline-secret")
            self.assertEqual(saved_job["resolved_models"][0]["id"], "o3-mini")
            self.assertNotIn("_api_key", saved_job["resolved_models"][0]["raw"])

    def test_execute_run_persists_api_trace_for_case_console(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {"job_id": "job-1", "progress": {}, "status": "running"}

            models = [ModelDescriptor(id="gpt-4.1-mini", provider="openai")]
            problems = [
                Problem(
                    id="custom_prompt_only",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement a simple adder.",
                    top_module="adder",
                )
            ]

            class FakeRunner:
                def __init__(self, config: dict | None = None):
                    self.last_error = ""
                    self.last_trace = {}

                def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
                    self.last_trace = {
                        "provider": model.provider,
                        "model_id": model.id,
                        "conversation": [
                            {"role": "system", "content": "Return only code."},
                            {"role": "user", "content": problem.prompt},
                            {"role": "assistant", "content": "module adder; endmodule"},
                        ],
                        "request": {"url": "https://api.openai.com/v1/chat/completions", "payload": {"model": model.id}},
                        "response": {"status_code": 200, "payload": {"choices": []}, "raw_text": "{\"choices\": []}"},
                        "error": "",
                    }
                    return "module adder; endmodule"

            with patch("rtl_benchmark.webapp.ModelRunner", FakeRunner):
                result = service._execute_run(
                    ui_config={"providers": [], "generation": {}, "execution": {}},
                    models=models,
                    problems=problems,
                    scope="custom_problem",
                    custom_problem=True,
                    update_board=False,
                    job_id="job-1",
                )

            self.assertEqual(len(result["cases"]), 1)
            case = result["cases"][0]
            self.assertIn("api_trace", case)
            self.assertEqual(case["api_trace"]["conversation"][-1]["role"], "assistant")
            self.assertTrue(case["artifact_dir"])
            self.assertTrue(any(item["name"] == "api_trace.json" for item in case["artifacts"]))
            trace_path = Path(case["artifact_dir"]) / "api_trace.json"
            self.assertTrue(trace_path.exists())
            saved_trace = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_trace["request"]["payload"]["model"], "gpt-4.1-mini")

    def test_execute_run_gemini_disconnect_becomes_generation_failed_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {"job_id": "job-1", "progress": {}, "status": "running"}

            models = [ModelDescriptor(id="gemini-2.5-flash", provider="gemini", raw={"_api_key": "inline-secret"})]
            problems = [
                Problem(
                    id="custom_prompt_only",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement a simple adder.",
                    top_module="adder",
                )
            ]

            with patch(
                "rtl_benchmark.model_runner.urllib.request.urlopen",
                side_effect=http.client.RemoteDisconnected("Remote end closed connection without response"),
            ):
                result = service._execute_run(
                    ui_config={"providers": [], "generation": {}, "execution": {}},
                    models=models,
                    problems=problems,
                    scope="custom_problem",
                    custom_problem=True,
                    update_board=False,
                    job_id="job-1",
                )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["cases"]), 1)
            case = result["cases"][0]
            self.assertFalse(case["passed"])
            self.assertIn("generation failed", case["feedback"])
            self.assertIn("network error: Remote end closed connection without response", case["feedback"])
            self.assertEqual(case["api_trace"]["error"], "network error: Remote end closed connection without response")
            trace_path = Path(case["artifact_dir"]) / "api_trace.json"
            self.assertTrue(trace_path.exists())

    def test_execute_run_failure_persists_partial_result_for_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {"job_id": "job-1", "progress": {}, "status": "running"}

            models = [ModelDescriptor(id="openrouter/hunter-alpha", provider="openrouter")]
            problems = [
                Problem(
                    id="custom_ok",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement add8.",
                    top_module="add8",
                    testbench="module tb; endmodule\n",
                    reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule\n",
                ),
                Problem(
                    id="custom_boom",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement boom.",
                    top_module="boom",
                    testbench="module tb; endmodule\n",
                    reference_rtl="module boom; endmodule\n",
                ),
            ]

            class FakeRunner:
                def __init__(self, config: dict | None = None):
                    self.last_error = ""
                    self.last_trace = {}

                def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
                    if problem.id == "custom_ok":
                        return "module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule"
                    return "module boom; endmodule"

            class FakeEvaluator:
                def __init__(self, run_root: str, config: dict | None = None):
                    self.run_root = run_root

                def evaluate(self, model_id: str, problem: Problem, candidate_code: str, attempt: int) -> CaseResult:
                    if problem.id == "custom_boom":
                        raise RuntimeError("forced evaluator crash")
                    passed = StageStatus(status="pass")
                    return CaseResult(
                        model_id=model_id,
                        problem_id=problem.id,
                        task_type=problem.task_type,
                        attempt=attempt,
                        passed=True,
                        lint=passed,
                        simulation=passed,
                        synthesis=StageStatus(status="skipped"),
                        feedback="passed",
                    )

            with patch("rtl_benchmark.webapp.ModelRunner", FakeRunner):
                with patch("rtl_benchmark.webapp.Evaluator", FakeEvaluator):
                    with self.assertRaises(RunExecutionError) as captured:
                        service._execute_run(
                            ui_config={"providers": [], "generation": {}, "execution": {"mode": "local"}},
                            models=models,
                            problems=problems,
                            scope="suite",
                            custom_problem=False,
                            update_board=False,
                            job_id="job-1",
                        )

            partial = captured.exception.partial_result
            self.assertIsNotNone(partial)
            assert partial is not None
            self.assertEqual(partial["status"], "failed")
            self.assertIn("forced evaluator crash", partial["error"])
            self.assertEqual(len(partial["cases"]), 1)
            self.assertEqual(partial["cases"][0]["problem_id"], "custom_ok")
            self.assertTrue((tmp_path / "raw" / f"{partial['run_id']}.json").exists())

    def test_execute_run_saves_running_snapshot_after_each_completed_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {"job_id": "job-1", "progress": {}, "status": "running"}

            models = [ModelDescriptor(id="openrouter/hunter-alpha", provider="openrouter")]
            problems = [
                Problem(
                    id="custom_ok",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement add8.",
                    top_module="add8",
                    testbench="module tb; endmodule\n",
                    reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule\n",
                ),
                Problem(
                    id="custom_boom",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement boom.",
                    top_module="boom",
                    testbench="module tb; endmodule\n",
                    reference_rtl="module boom; endmodule\n",
                ),
            ]
            seen_snapshots: list[dict[str, object]] = []

            class FakeRunner:
                def __init__(self, config: dict | None = None):
                    self.last_error = ""
                    self.last_trace = {}

                def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
                    self.last_trace = {
                        "metrics": {
                            "duration_seconds": 6.0,
                            "completion_tokens": 240,
                            "output_tokens_per_second": 40.0,
                        }
                    }
                    return f"module {problem.top_module}; endmodule"

            class FakeEvaluator:
                def __init__(self, run_root: str, config: dict | None = None):
                    self.run_root = run_root

                def evaluate(self, model_id: str, problem: Problem, candidate_code: str, attempt: int) -> CaseResult:
                    if problem.id == "custom_boom":
                        run_id = str(service._jobs["job-1"].get("run_id", ""))
                        raw_path = tmp_path / "raw" / f"{run_id}.json"
                        if raw_path.exists():
                            seen_snapshots.append(json.loads(raw_path.read_text(encoding="utf-8")))
                        raise RuntimeError("stop after checkpoint")
                    passed = StageStatus(status="pass")
                    return CaseResult(
                        model_id=model_id,
                        problem_id=problem.id,
                        task_type=problem.task_type,
                        attempt=attempt,
                        passed=True,
                        lint=passed,
                        simulation=passed,
                        synthesis=StageStatus(status="skipped"),
                        feedback="passed",
                    )

            with patch("rtl_benchmark.webapp.ModelRunner", FakeRunner):
                with patch("rtl_benchmark.webapp.Evaluator", FakeEvaluator):
                    with self.assertRaises(RunExecutionError):
                        service._execute_run(
                            ui_config={"providers": [], "generation": {}, "execution": {"mode": "local"}},
                            models=models,
                            problems=problems,
                            scope="suite",
                            custom_problem=False,
                            update_board=False,
                            job_id="job-1",
                        )

            self.assertTrue(seen_snapshots)
            snapshot = seen_snapshots[0]
            self.assertEqual(snapshot["status"], "running")
            self.assertEqual(len(snapshot["cases"]), 1)
            self.assertEqual(snapshot["cases"][0]["problem_id"], "custom_ok")

    def test_execute_run_honors_pause_request_and_persists_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {
                "job_id": "job-1",
                "progress": {},
                "status": "running",
                "pause_requested": False,
            }

            models = [ModelDescriptor(id="openrouter/hunter-alpha", provider="openrouter")]
            problems = [
                Problem(
                    id="custom_ok",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement add8.",
                    top_module="add8",
                    testbench="module tb; endmodule\n",
                    reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule\n",
                ),
                Problem(
                    id="custom_later",
                    task_type="rtl",
                    language="verilog",
                    prompt="Implement later.",
                    top_module="later",
                    testbench="module tb; endmodule\n",
                    reference_rtl="module later; endmodule\n",
                ),
            ]

            class FakeRunner:
                def __init__(self, config: dict | None = None):
                    self.last_error = ""
                    self.last_trace = {}

                def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
                    self.last_trace = {
                        "metrics": {
                            "duration_seconds": 6.0,
                            "completion_tokens": 240,
                            "output_tokens_per_second": 40.0,
                        }
                    }
                    return f"module {problem.top_module}; endmodule"

            class FakeEvaluator:
                def __init__(self, run_root: str, config: dict | None = None):
                    self.run_root = run_root

                def evaluate(self, model_id: str, problem: Problem, candidate_code: str, attempt: int) -> CaseResult:
                    if problem.id == "custom_ok":
                        service.pause_job("job-1")
                    passed = StageStatus(status="pass")
                    return CaseResult(
                        model_id=model_id,
                        problem_id=problem.id,
                        task_type=problem.task_type,
                        attempt=attempt,
                        passed=True,
                        lint=passed,
                        simulation=passed,
                        synthesis=passed,
                        feedback="passed",
                    )

            with patch("rtl_benchmark.webapp.ModelRunner", FakeRunner):
                with patch("rtl_benchmark.webapp.Evaluator", FakeEvaluator):
                    result = service._execute_run(
                        ui_config={"providers": [], "generation": {}, "execution": {"mode": "local"}},
                        models=models,
                        problems=problems,
                        scope="suite",
                        custom_problem=False,
                        update_board=False,
                        job_id="job-1",
                    )

            job = service._get_job("job-1")
            raw_path = tmp_path / "raw" / f"{result['run_id']}.json"
            snapshot = json.loads(raw_path.read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "paused")
            self.assertEqual(snapshot["status"], "paused")
            self.assertEqual(len(result["cases"]), 1)
            self.assertEqual(result["cases"][0]["problem_id"], "custom_ok")
            self.assertEqual(job["status"], "paused")
            self.assertEqual(job["progress"]["completed_cases"], 1)
            self.assertEqual(job["progress"]["total_cases"], 2)
            self.assertEqual(job["progress"]["remaining_cases"], 1)
            self.assertEqual(job["progress"]["percent"], 50.0)
            self.assertGreater(job["progress"]["eta"]["seconds"], 0)
            self.assertEqual(job["progress"]["eta"]["confidence"], "medium")
            self.assertIn("tok/s", job["progress"]["eta"]["basis"])
            self.assertFalse(job["pause_requested"])

    def test_eta_estimate_accounts_for_token_rate_and_problem_complexity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            easy = Problem(
                id="easy_case",
                task_type="rtl",
                language="verilog",
                prompt="Implement add8.",
                top_module="add8",
                testbench="module tb; endmodule\n",
                reference_rtl="module add8; endmodule\n",
                difficulty="easy",
            )
            hard = Problem(
                id="hard_case",
                task_type="rtl",
                language="verilog",
                prompt="Implement a pipelined divider with validation hooks and corner-case handling.",
                top_module="divider",
                testbench="module tb; // wider harness\nendmodule\n",
                reference_rtl="module divider; // reference\nendmodule\n",
                difficulty="hard",
                mutant_rtls=["module divider; endmodule\n", "module divider; endmodule\n"],
            )

            eta_state = service._new_eta_state({"generation": {"timeout_seconds": 20}, "execution": {"timeout_seconds": 10}})
            eta_state = service._record_eta_observation(
                eta_state=eta_state,
                problem=easy,
                attempts=1,
                generation_seconds=8.0,
                evaluation_seconds=4.0,
                case_seconds=12.0,
                output_tokens=240,
                token_seconds=8.0,
            )

            eta = service._estimate_eta(eta_state, [hard])

            self.assertGreater(eta["seconds"], 0)
            self.assertEqual(eta["confidence"], "medium")
            self.assertAlmostEqual(eta["token_rate_tps"], 30.0, places=1)
            self.assertGreater(eta["complexity_factor"], 1.0)

    def test_list_jobs_marks_dead_running_thread_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service._jobs["job-1"] = {
                "job_id": "job-1",
                "status": "running",
                "submitted_at": "2026-03-16T15:43:01Z",
                "finished_at": "",
                "error": "",
                "progress": {"message": "running", "model_id": "openrouter/hunter-alpha", "problem_id": "rtllm_arithmetic_div_16bit", "attempt": 2},
            }
            service._job_threads["job-1"] = threading.Thread(target=lambda: None)

            jobs = service.list_jobs()

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "failed")
            self.assertTrue(jobs[0]["finished_at"])
            self.assertIn("worker thread ended", jobs[0]["error"])

    def test_list_jobs_marks_stalled_live_thread_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.base_config["job_stale_seconds"] = 60
            service._jobs["job-1"] = {
                "job_id": "job-1",
                "status": "running",
                "submitted_at": "2026-03-16T15:43:01Z",
                "started_at": "2026-03-16T15:43:05Z",
                "updated_at": "2026-03-16T15:44:05Z",
                "finished_at": "",
                "error": "",
                "progress": {
                    "message": "running",
                    "model_id": "openrouter/hunter-alpha",
                    "problem_id": "rtllm_arithmetic_radix2_div",
                    "attempt": 2,
                },
            }

            class AliveThread:
                def is_alive(self) -> bool:
                    return True

            service._job_threads["job-1"] = AliveThread()  # type: ignore[assignment]

            jobs = service.list_jobs()

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "failed")
            self.assertTrue(jobs[0]["finished_at"])
            self.assertIn("job stalled with no progress update", jobs[0]["error"])
            self.assertEqual(jobs[0]["progress"]["problem_id"], "rtllm_arithmetic_radix2_div")

    def test_stalled_job_promotes_partial_snapshot_to_failed_and_keeps_leaderboard_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.base_config["job_stale_seconds"] = 60
            save_json(service.leaderboard_state_path, {"reset_at": ""})
            run_id = "stalled_run"
            save_json(
                tmp_path / "raw" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "started_at": "2026-03-16T15:43:05Z",
                    "finished_at": "",
                    "status": "running",
                    "error": "",
                    "source": "webui",
                    "scope": "suite",
                    "custom_problem": False,
                    "problem_ids": ["rtl_add8"],
                    "problems": [
                        {
                            "id": "rtl_add8",
                            "task_type": "rtl",
                            "source": "rtl",
                            "suite": "rtl",
                            "category": "baseline",
                            "track": "rtl_core",
                            "difficulty": "easy",
                            "exposure": "public",
                            "tags": ["basic"],
                        }
                    ],
                    "models": [{"id": "openrouter/hunter-alpha", "provider": "openrouter"}],
                    "cases": [
                        {
                            "model_id": "openrouter/hunter-alpha",
                            "provider": "openrouter",
                            "problem_id": "rtl_add8",
                            "task_type": "rtl",
                            "attempt": 1,
                            "passed": True,
                            "lint": {"status": "pass"},
                            "simulation": {"status": "pass"},
                            "synthesis": {"status": "pass"},
                            "feedback": "passed",
                            "problem_source": "rtl",
                            "problem_category": "baseline",
                            "problem_suite": "rtl",
                            "problem_track": "rtl_core",
                            "problem_difficulty": "easy",
                        }
                    ],
                    "summary": [],
                    "slice_rankings": {},
                    "scoring_policy": {},
                    "run_root": str(tmp_path / "runs" / run_id),
                },
            )
            service._jobs["job-1"] = {
                "job_id": "job-1",
                "run_id": run_id,
                "status": "running",
                "submitted_at": "2026-03-16T15:43:01Z",
                "started_at": "2026-03-16T15:43:05Z",
                "updated_at": "2026-03-16T15:44:05Z",
                "finished_at": "",
                "error": "",
                "progress": {
                    "message": "running",
                    "model_id": "openrouter/hunter-alpha",
                    "problem_id": "rtl_add8",
                    "attempt": 1,
                },
            }

            class AliveThread:
                def is_alive(self) -> bool:
                    return True

            service._job_threads["job-1"] = AliveThread()  # type: ignore[assignment]

            jobs = service.list_jobs()
            snapshot = json.loads((tmp_path / "raw" / f"{run_id}.json").read_text(encoding="utf-8"))
            board = service.load_leaderboard()

            self.assertEqual(jobs[0]["status"], "failed")
            self.assertIsNotNone(jobs[0]["result"])
            self.assertEqual(jobs[0]["result"]["status"], "failed")
            self.assertEqual(len(jobs[0]["result"]["cases"]), 1)
            self.assertEqual(snapshot["status"], "failed")
            self.assertIn("job stalled with no progress update", snapshot["error"])
            self.assertEqual(len(board["models"]), 1)
            self.assertEqual(board["models"][0]["model_id"], "openrouter/hunter-alpha")

    def test_restart_recovers_persisted_job_and_marks_orphaned_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.jobs_state_path = tmp_path / "web_jobs.json"
            save_json(service.leaderboard_state_path, {"reset_at": ""})
            run_id = "resume_after_restart"
            save_json(
                tmp_path / "raw" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "started_at": "2026-03-16T15:43:05Z",
                    "finished_at": "",
                    "status": "running",
                    "error": "",
                    "source": "webui",
                    "scope": "selected_problems",
                    "custom_problem": False,
                    "problem_ids": ["rtl_add8"],
                    "problems": [
                        {
                            "id": "rtl_add8",
                            "task_type": "rtl",
                            "source": "rtl",
                            "suite": "rtl",
                            "category": "baseline",
                            "track": "rtl_core",
                            "difficulty": "easy",
                            "exposure": "public",
                            "tags": ["basic"],
                        }
                    ],
                    "models": [{"id": "openrouter/hunter-alpha", "provider": "openrouter"}],
                    "cases": [
                        {
                            "model_id": "openrouter/hunter-alpha",
                            "provider": "openrouter",
                            "problem_id": "rtl_add8",
                            "task_type": "rtl",
                            "attempt": 1,
                            "passed": True,
                            "lint": {"status": "pass"},
                            "simulation": {"status": "pass"},
                            "synthesis": {"status": "pass"},
                            "feedback": "passed",
                            "problem_source": "rtl",
                            "problem_category": "baseline",
                            "problem_suite": "rtl",
                            "problem_track": "rtl_core",
                            "problem_difficulty": "easy",
                        }
                    ],
                    "summary": [],
                    "slice_rankings": {},
                    "scoring_policy": {},
                    "run_root": str(tmp_path / "runs" / run_id),
                },
            )
            save_json(
                service.jobs_state_path,
                {
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "running",
                            "submitted_at": "2026-03-16T15:43:01Z",
                            "started_at": "2026-03-16T15:43:05Z",
                            "updated_at": "2026-03-16T15:43:30Z",
                            "finished_at": "",
                            "error": "",
                            "progress": {
                                "message": "running",
                                "model_id": "openrouter/hunter-alpha",
                                "problem_id": "rtl_add8",
                                "attempt": 1,
                            },
                            "request": {"scope": "selected_problems", "problemIds": ["rtl_add8"]},
                            "run_id": run_id,
                            "run_root": str(tmp_path / "runs" / run_id),
                            "resolved_models": [{"id": "openrouter/hunter-alpha", "provider": "openrouter"}],
                        }
                    ]
                },
            )

            restarted = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            restarted.jobs_state_path = tmp_path / "web_jobs.json"
            restarted._jobs = restarted._load_jobs_state()

            jobs = restarted.list_jobs()
            board = restarted.load_leaderboard()
            snapshot = json.loads((tmp_path / "raw" / f"{run_id}.json").read_text(encoding="utf-8"))

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "failed")
            self.assertIn("worker thread ended", jobs[0]["error"])
            self.assertIsNotNone(jobs[0]["result"])
            self.assertEqual(jobs[0]["result"]["run_id"], run_id)
            self.assertEqual(snapshot["status"], "failed")
            self.assertEqual(len(board["models"]), 1)
            self.assertEqual(board["models"][0]["model_id"], "openrouter/hunter-alpha")

    def test_resume_job_continues_same_run_and_skips_completed_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.jobs_state_path = tmp_path / "web_jobs.json"
            problem_ids = [item["id"] for item in service.list_problems()[:2]]
            self.assertEqual(len(problem_ids), 2)
            first_problem, second_problem = problem_ids
            run_id = "resume_same_run"
            save_json(
                tmp_path / "raw" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "started_at": "2026-03-16T15:43:05Z",
                    "finished_at": "2026-03-16T15:44:00Z",
                    "status": "failed",
                    "error": "forced stop",
                    "source": "webui",
                    "scope": "selected_problems",
                    "custom_problem": False,
                    "problem_ids": problem_ids,
                    "problems": [asdict(service._load_problem_map()[problem_id]) for problem_id in problem_ids],
                    "models": [{"id": "gpt-4.1-mini", "provider": "openai"}],
                    "cases": [
                        {
                            "model_id": "gpt-4.1-mini",
                            "provider": "openai",
                            "problem_id": first_problem,
                            "task_type": "rtl",
                            "attempt": 1,
                            "passed": True,
                            "lint": {"status": "pass"},
                            "simulation": {"status": "pass"},
                            "synthesis": {"status": "pass"},
                            "feedback": "passed",
                        }
                    ],
                    "summary": [],
                    "slice_rankings": {},
                    "scoring_policy": {},
                    "run_root": str(tmp_path / "runs" / run_id),
                },
            )
            save_json(
                service.jobs_state_path,
                {
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "failed",
                            "submitted_at": "2026-03-16T15:43:01Z",
                            "started_at": "2026-03-16T15:43:05Z",
                            "updated_at": "2026-03-16T15:44:00Z",
                            "finished_at": "2026-03-16T15:44:00Z",
                            "error": "forced stop",
                            "progress": {
                                "message": "failed",
                                "model_id": "gpt-4.1-mini",
                                "problem_id": second_problem,
                                "attempt": 1,
                            },
                            "request": {
                                "scope": "selected_problems",
                                "problemIds": problem_ids,
                                "uiConfig": {"providers": [], "generation": {}, "execution": {"mode": "local"}},
                            },
                            "run_id": run_id,
                            "run_root": str(tmp_path / "runs" / run_id),
                            "resolved_models": [{"id": "gpt-4.1-mini", "provider": "openai"}],
                        }
                    ]
                },
            )

            resumed = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            resumed.jobs_state_path = tmp_path / "web_jobs.json"
            resumed._jobs = resumed._load_jobs_state()
            executed_problem_ids: list[str] = []

            class FakeRunner:
                def __init__(self, config: dict | None = None):
                    self.last_error = ""
                    self.last_trace = {}

                def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
                    executed_problem_ids.append(problem.id)
                    return f"module {problem.top_module or 'dut'}; endmodule"

            class FakeEvaluator:
                def __init__(self, run_root: str, config: dict | None = None):
                    self.run_root = run_root

                def evaluate(self, model_id: str, problem: Problem, candidate_code: str, attempt: int) -> CaseResult:
                    passed = StageStatus(status="pass")
                    return CaseResult(
                        model_id=model_id,
                        problem_id=problem.id,
                        task_type=problem.task_type,
                        attempt=attempt,
                        passed=True,
                        lint=passed,
                        simulation=passed,
                        synthesis=passed,
                        feedback="passed after resume",
                    )

            with patch("rtl_benchmark.webapp.ModelRunner", FakeRunner):
                with patch("rtl_benchmark.webapp.Evaluator", FakeEvaluator):
                    resumed.resume_job("job-1")
                    worker = resumed._job_threads["job-1"]
                    worker.join(timeout=5)

            jobs = resumed.list_jobs()
            snapshot = json.loads((tmp_path / "raw" / f"{run_id}.json").read_text(encoding="utf-8"))

            self.assertFalse(worker.is_alive())
            self.assertEqual(executed_problem_ids, [second_problem])
            self.assertEqual(jobs[0]["status"], "completed")
            self.assertEqual(jobs[0]["result"]["run_id"], run_id)
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual({case["problem_id"] for case in snapshot["cases"]}, {first_problem, second_problem})

    def test_rerun_case_updates_existing_job_snapshot_with_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.jobs_state_path = tmp_path / "web_jobs.json"
            problem_ids = [item["id"] for item in service.list_problems()[:2]]
            self.assertEqual(len(problem_ids), 2)
            failed_problem, passed_problem = problem_ids
            run_id = "rerun_failed_case"
            save_json(
                tmp_path / "raw" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "started_at": "2026-03-16T15:43:05Z",
                    "finished_at": "2026-03-16T15:44:00Z",
                    "status": "failed",
                    "error": "forced stop",
                    "source": "webui",
                    "scope": "selected_problems",
                    "custom_problem": False,
                    "problem_ids": problem_ids,
                    "problems": [asdict(service._load_problem_map()[problem_id]) for problem_id in problem_ids],
                    "models": [{"id": "gpt-4.1-mini", "provider": "openai"}],
                    "cases": [
                        {
                            "model_id": "gpt-4.1-mini",
                            "provider": "openai",
                            "problem_id": failed_problem,
                            "task_type": "rtl",
                            "attempt": 1,
                            "passed": False,
                            "lint": {"status": "pass"},
                            "simulation": {"status": "fail"},
                            "synthesis": {"status": "pass"},
                            "feedback": "first try failed",
                        },
                        {
                            "model_id": "gpt-4.1-mini",
                            "provider": "openai",
                            "problem_id": passed_problem,
                            "task_type": "rtl",
                            "attempt": 1,
                            "passed": True,
                            "lint": {"status": "pass"},
                            "simulation": {"status": "pass"},
                            "synthesis": {"status": "pass"},
                            "feedback": "already good",
                        },
                    ],
                    "summary": [],
                    "slice_rankings": {},
                    "scoring_policy": {},
                    "run_root": str(tmp_path / "runs" / run_id),
                },
            )
            save_json(
                service.jobs_state_path,
                {
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "failed",
                            "submitted_at": "2026-03-16T15:43:01Z",
                            "started_at": "2026-03-16T15:43:05Z",
                            "updated_at": "2026-03-16T15:44:00Z",
                            "finished_at": "2026-03-16T15:44:00Z",
                            "error": "forced stop",
                            "progress": {
                                "message": "failed",
                                "model_id": "gpt-4.1-mini",
                                "problem_id": failed_problem,
                                "attempt": 1,
                            },
                            "request": {
                                "scope": "selected_problems",
                                "problemIds": problem_ids,
                                "uiConfig": {"providers": [], "generation": {}, "execution": {"mode": "local"}},
                            },
                            "run_id": run_id,
                            "run_root": str(tmp_path / "runs" / run_id),
                            "resolved_models": [{"id": "gpt-4.1-mini", "provider": "openai"}],
                        }
                    ]
                },
            )
            service._jobs = service._load_jobs_state()
            executed_problem_ids: list[str] = []

            class FakeRunner:
                def __init__(self, config: dict | None = None):
                    self.last_error = ""
                    self.last_trace = {}

                def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
                    executed_problem_ids.append(problem.id)
                    return f"module {problem.top_module or 'dut'}; endmodule"

            class FakeEvaluator:
                def __init__(self, run_root: str, config: dict | None = None):
                    self.run_root = run_root

                def evaluate(self, model_id: str, problem: Problem, candidate_code: str, attempt: int) -> CaseResult:
                    passed = StageStatus(status="pass")
                    return CaseResult(
                        model_id=model_id,
                        problem_id=problem.id,
                        task_type=problem.task_type,
                        attempt=attempt,
                        passed=True,
                        lint=passed,
                        simulation=passed,
                        synthesis=passed,
                        feedback="passed after rerun",
                    )

            with patch("rtl_benchmark.webapp.ModelRunner", FakeRunner):
                with patch("rtl_benchmark.webapp.Evaluator", FakeEvaluator):
                    service.rerun_case("job-1", "gpt-4.1-mini", failed_problem)
                    worker = service._job_threads["job-1"]
                    worker.join(timeout=5)

            jobs = service.list_jobs()
            snapshot = json.loads((tmp_path / "raw" / f"{run_id}.json").read_text(encoding="utf-8"))
            final_cases = {
                case["problem_id"]: case
                for case in snapshot["cases"]
                if case["problem_id"] in {failed_problem, passed_problem}
            }

            self.assertFalse(worker.is_alive())
            self.assertEqual(executed_problem_ids, [failed_problem])
            self.assertEqual(jobs[0]["status"], "completed")
            self.assertEqual(jobs[0]["result"]["run_id"], run_id)
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(len([case for case in snapshot["cases"] if case["problem_id"] == failed_problem]), 2)
            self.assertEqual(final_cases[failed_problem]["attempt"], 2)
            self.assertTrue(final_cases[failed_problem]["passed"])
            self.assertEqual(final_cases[passed_problem]["attempt"], 1)

    def test_delete_job_removes_saved_run_and_rebuilds_leaderboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            service.jobs_state_path = tmp_path / "web_jobs.json"
            save_json(service.leaderboard_state_path, {"reset_at": ""})
            run_id = "failed_job_to_delete"
            run_root = tmp_path / "runs" / run_id
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "marker.txt").write_text("x", encoding="utf-8")
            save_json(
                tmp_path / "raw" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "started_at": "2026-03-16T15:43:05Z",
                    "finished_at": "2026-03-16T15:44:00Z",
                    "status": "failed",
                    "error": "forced stop",
                    "source": "webui",
                    "scope": "selected_problems",
                    "custom_problem": False,
                    "problem_ids": ["rtl_add8"],
                    "problems": [
                        {
                            "id": "rtl_add8",
                            "task_type": "rtl",
                            "source": "rtl",
                            "suite": "rtl",
                            "category": "baseline",
                            "track": "rtl_core",
                            "difficulty": "easy",
                            "exposure": "public",
                            "tags": ["basic"],
                        }
                    ],
                    "models": [{"id": "openrouter/hunter-alpha", "provider": "openrouter"}],
                    "cases": [
                        {
                            "model_id": "openrouter/hunter-alpha",
                            "provider": "openrouter",
                            "problem_id": "rtl_add8",
                            "task_type": "rtl",
                            "attempt": 1,
                            "passed": True,
                            "lint": {"status": "pass"},
                            "simulation": {"status": "pass"},
                            "synthesis": {"status": "pass"},
                            "feedback": "passed",
                            "problem_source": "rtl",
                            "problem_category": "baseline",
                            "problem_suite": "rtl",
                            "problem_track": "rtl_core",
                            "problem_difficulty": "easy",
                        }
                    ],
                    "summary": [],
                    "slice_rankings": {},
                    "scoring_policy": {},
                    "run_root": str(run_root),
                },
            )
            save_json(
                service.jobs_state_path,
                {
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "failed",
                            "submitted_at": "2026-03-16T15:43:01Z",
                            "started_at": "2026-03-16T15:43:05Z",
                            "updated_at": "2026-03-16T15:44:00Z",
                            "finished_at": "2026-03-16T15:44:00Z",
                            "error": "forced stop",
                            "progress": {"message": "failed", "model_id": "", "problem_id": "", "attempt": 0},
                            "request": {"scope": "selected_problems", "problemIds": ["rtl_add8"]},
                            "run_id": run_id,
                            "run_root": str(run_root),
                            "resolved_models": [{"id": "openrouter/hunter-alpha", "provider": "openrouter"}],
                        }
                    ]
                },
            )
            service._jobs = service._load_jobs_state()

            board_before = service.load_leaderboard()
            deleted = service.delete_job("job-1")
            board_after = service.load_leaderboard()

            self.assertTrue(deleted)
            self.assertEqual(len(board_before["models"]), 1)
            self.assertEqual(board_after["models"], [])
            self.assertFalse((tmp_path / "raw" / f"{run_id}.json").exists())
            self.assertFalse(run_root.exists())
            self.assertEqual(service.list_jobs(), [])
            self.assertEqual(service.list_history(limit=10), [])


if __name__ == "__main__":
    unittest.main()
