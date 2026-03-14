from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rtl_benchmark.evaluator import Evaluator
from rtl_benchmark.model_runner import ModelRunner
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.pipeline import BenchmarkPipeline
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.types import ModelDescriptor, Problem


ROOT = Path("/Users/gary/RTL_benchmark")


class ProblemBankTests(unittest.TestCase):
    def test_load_problems_supports_absolute_glob(self) -> None:
        problems = load_problems(str(ROOT / "benchmarks/**/*.json"))
        ids = {problem.id for problem in problems}
        self.assertEqual(ids, {"rtl_add8", "rtl_edge_detect", "tb_mod3_counter"})


class DiscoveryTests(unittest.TestCase):
    def test_discover_can_be_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            feed_path = tmp_path / "models.json"
            state_path = tmp_path / "known.json"
            feed_path.write_text(
                json.dumps([{"id": "mock/example-v1", "provider": "mock", "released_at": "2026-03-01T00:00:00Z"}]),
                encoding="utf-8",
            )
            state_path.write_text(json.dumps({"known_model_ids": []}), encoding="utf-8")

            models = discover_models(
                sources=[{"type": "file_feed", "path": str(feed_path)}],
                state_path=str(state_path),
                include_known=False,
                selection={},
                update_state=False,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([model.id for model in models], ["mock/example-v1"])
            self.assertEqual(state["known_model_ids"], [])

    def test_explicit_model_lists_ignore_known_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_path = tmp_path / "known.json"
            state_path.write_text(json.dumps({"known_model_ids": ["gpt-4.1-mini"]}), encoding="utf-8")

            models = discover_models(
                sources=[
                    {
                        "type": "openai",
                        "provider": "openai",
                        "models": [{"id": "gpt-4.1-mini"}],
                    }
                ],
                state_path=str(state_path),
                include_known=False,
                selection={},
                update_state=True,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([model.id for model in models], ["gpt-4.1-mini"])
            self.assertEqual(state["known_model_ids"], ["gpt-4.1-mini"])

    def test_gemini_discovery_normalizes_model_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_path = tmp_path / "known.json"
            state_path.write_text(json.dumps({"known_model_ids": []}), encoding="utf-8")

            payload = {
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }

            with patch("rtl_benchmark.model_sources._fetch_json", return_value=payload):
                with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                    models = discover_models(
                        sources=[{"type": "gemini", "enabled": True}],
                        state_path=str(state_path),
                        include_known=False,
                        selection={},
                        update_state=False,
                    )

            self.assertEqual([model.id for model in models], ["gemini-2.5-flash"])
            self.assertEqual([model.provider for model in models], ["gemini"])


class RunnerTests(unittest.TestCase):
    def test_real_provider_failure_does_not_fallback_to_mock(self) -> None:
        runner = ModelRunner({})
        problem = Problem(
            id="rtl_add8",
            task_type="rtl",
            language="verilog",
            prompt="Implement add8.",
            top_module="add8",
            reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a+b; endmodule",
            testbench="module tb; endmodule",
        )
        model = ModelDescriptor(id="gpt-4.1-mini", provider="openai")
        self.assertEqual(runner.generate(model, problem), "")

    def test_gemini_provider_failure_does_not_fallback_to_mock(self) -> None:
        runner = ModelRunner({})
        problem = Problem(
            id="rtl_add8",
            task_type="rtl",
            language="verilog",
            prompt="Implement add8.",
            top_module="add8",
            reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a+b; endmodule",
            testbench="module tb; endmodule",
        )
        model = ModelDescriptor(id="gemini-2.5-flash", provider="gemini")
        self.assertEqual(runner.generate(model, problem), "")

    def test_extract_gemini_message_collects_text_parts(self) -> None:
        runner = ModelRunner({})
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "module add8("},
                            {"text": ");\nendmodule"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(runner._extract_gemini_message(payload), "module add8(\n);\nendmodule")


class PipelineTests(unittest.TestCase):
    def test_generation_failure_is_recorded_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_path = tmp_path / "state.json"
            config_path = tmp_path / "config.json"
            state_path.write_text(json.dumps({"known_model_ids": []}), encoding="utf-8")

            config = {
                "problem_glob": str(ROOT / "benchmarks/**/*.json"),
                "max_iterations": 1,
                "generation": {"temperature": 0.0, "max_tokens": 128, "timeout_seconds": 5},
                "selection": {"providers": ["openai"]},
                "run_root": str(tmp_path / "runs"),
                "raw_results_dir": str(tmp_path / "raw"),
                "leaderboard_path": str(tmp_path / "leaderboard.json"),
                "state_path": str(state_path),
                "sources": [
                    {
                        "type": "openai",
                        "enabled": True,
                        "provider": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "api_key_env": "OPENAI_API_KEY",
                        "models": [{"id": "gpt-4.1-mini"}],
                    }
                ],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = BenchmarkPipeline(str(config_path)).run(include_known=False)

            self.assertEqual(len(result["models"]), 1)
            self.assertEqual(len(result["cases"]), 3)
            self.assertTrue(all(case["feedback"].startswith("generation failed:") for case in result["cases"]))
            self.assertTrue(all(case["lint"]["status"] == "skipped" for case in result["cases"]))

    def test_explicit_model_lists_run_on_every_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_path = tmp_path / "state.json"
            config_path = tmp_path / "config.json"
            state_path.write_text(json.dumps({"known_model_ids": []}), encoding="utf-8")

            config = {
                "problem_glob": str(ROOT / "benchmarks/**/*.json"),
                "max_iterations": 1,
                "generation": {"temperature": 0.0, "max_tokens": 128, "timeout_seconds": 5},
                "selection": {"providers": ["openai"]},
                "run_root": str(tmp_path / "runs"),
                "raw_results_dir": str(tmp_path / "raw"),
                "leaderboard_path": str(tmp_path / "leaderboard.json"),
                "state_path": str(state_path),
                "sources": [
                    {
                        "type": "openai",
                        "enabled": True,
                        "provider": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "api_key_env": "OPENAI_API_KEY",
                        "models": [{"id": "gpt-4.1-mini"}],
                    }
                ],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            first = BenchmarkPipeline(str(config_path)).run(include_known=False)
            second = BenchmarkPipeline(str(config_path)).run(include_known=False)
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(len(first["models"]), 1)
            self.assertEqual(len(second["models"]), 1)
            self.assertEqual(state["known_model_ids"], [])


class DockerExecutionTests(unittest.TestCase):
    def test_docker_command_mounts_case_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            evaluator = Evaluator(
                str(case_dir),
                {
                    "mode": "docker",
                    "docker_binary": "docker",
                    "docker_image": "rtl-benchmark-tools:latest",
                    "container_workdir": "/workspace",
                    "docker_network": "none",
                    "docker_read_only_rootfs": True,
                    "docker_tmpfs_mounts": ["/tmp"],
                    "docker_security_opts": ["no-new-privileges:true"],
                    "docker_cap_drop": ["ALL"],
                    "docker_pids_limit": 256,
                    "docker_memory": "1g",
                    "docker_cpus": "1.0",
                },
            )

            cmd = evaluator._build_docker_cmd(["verilator", "--lint-only", "dut.sv"], case_dir)

            self.assertEqual(cmd[0], "docker")
            self.assertIn("run", cmd)
            self.assertIn("rtl-benchmark-tools:latest", cmd)
            self.assertIn(f"{case_dir.resolve()}:/workspace", cmd)
            self.assertIn("--network", cmd)
            self.assertIn("--read-only", cmd)
            self.assertIn("--tmpfs", cmd)
            self.assertIn("--security-opt", cmd)
            self.assertIn("--cap-drop", cmd)
            self.assertIn("--pids-limit", cmd)
            self.assertIn("--memory", cmd)
            self.assertIn("--cpus", cmd)
            self.assertEqual(cmd[-3:], ["verilator", "--lint-only", "dut.sv"])

    def test_docker_preflight_reports_missing_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evaluator = Evaluator(
                tmp,
                {
                    "mode": "docker",
                    "docker_binary": "docker-does-not-exist",
                    "docker_image": "rtl-benchmark-tools:latest",
                },
            )
            result = evaluator.check_execution_backend()
            self.assertEqual(result.status, "skipped")
            self.assertIn("docker-does-not-exist not found", result.reason)


if __name__ == "__main__":
    unittest.main()
