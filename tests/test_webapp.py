from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rtl_benchmark.webapp import WebAppService


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


if __name__ == "__main__":
    unittest.main()
