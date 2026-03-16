from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rtl_benchmark.webapp import WebAppService
from rtl_benchmark.types import ModelDescriptor, Problem
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

    def test_load_leaderboard_ignores_selected_problem_history(self) -> None:
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
                        "summary": [
                            {
                                "model_id": "gemini-3.1-pro-preview",
                                "provider": "gemini",
                                "score": 0.5,
                                "pass_rate": 0.5,
                                "cases": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))
            board = service.load_leaderboard()

            self.assertEqual(board["models"], [])
            self.assertEqual(board["updated_at"], "")

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

    def test_only_suite_runs_update_leaderboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_base_config(tmp_path)
            service = WebAppService(str(config_path), str(tmp_path / "webui.json"))

            problems, scope, update_board = service._resolve_requested_problems(
                {"scope": "selected_problems", "problemIds": ["rtl_add8"]}
            )
            self.assertEqual(scope, "selected_problems")
            self.assertTrue(problems)
            self.assertFalse(update_board)

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


if __name__ == "__main__":
    unittest.main()
