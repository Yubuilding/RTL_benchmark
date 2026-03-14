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
