from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rtl_benchmark.evaluator import Evaluator
from rtl_benchmark.importers import import_rtllm_repo
from rtl_benchmark.model_runner import ModelRunner
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.pipeline import BenchmarkPipeline
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.types import ModelDescriptor, Problem


ROOT = Path("/Users/gary/RTL_benchmark")


class ProblemBankTests(unittest.TestCase):
    def test_external_problem_catalogs_are_well_formed(self) -> None:
        catalog_root = ROOT / "data" / "problem_catalogs"
        self.assertTrue((catalog_root / "hdlbits_index.json").exists())
        self.assertTrue((catalog_root / "open_rtl_benchmarks.json").exists())

        hdlbits = json.loads((catalog_root / "hdlbits_index.json").read_text(encoding="utf-8"))
        self.assertEqual(hdlbits["name"], "HDLBits")
        self.assertEqual(hdlbits["mirror_policy"], "link_only")
        self.assertEqual(hdlbits["license_status"], "unverified")
        self.assertTrue(hdlbits["topic_groups"])

        open_catalog = json.loads((catalog_root / "open_rtl_benchmarks.json").read_text(encoding="utf-8"))
        names = {item["name"] for item in open_catalog}
        self.assertTrue({"RTLLM", "RTL-Repo", "PyHDL-Eval", "AutoBench", "CorrectBench"}.issubset(names))
        self.assertTrue(all(item["license_status"] == "verified" for item in open_catalog))

    def test_import_rtllm_repo_converts_local_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_root = tmp_path / "RTLLM"
            design_dir = src_root / "Arithmetic" / "Add8"
            design_dir.mkdir(parents=True, exist_ok=True)
            (design_dir / "design_description.txt").write_text(
                "Design a module named add8 with inputs a, b and output sum.",
                encoding="utf-8",
            )
            (design_dir / "testbench.v").write_text("module tb; endmodule\n", encoding="utf-8")
            (design_dir / "verified_verilog.v").write_text(
                "module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule\n",
                encoding="utf-8",
            )

            outputs = import_rtllm_repo(str(src_root), str(tmp_path / "benchmarks" / "rtllm"))

            self.assertEqual(len(outputs), 1)
            payload = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["id"], "rtllm_arithmetic_add8")
            self.assertEqual(payload["source"], "rtllm")
            self.assertEqual(payload["suite"], "rtllm")
            self.assertEqual(payload["category"], "arithmetic")
            self.assertEqual(payload["track"], "arithmetic")
            self.assertEqual(payload["top_module"], "add8")
            self.assertEqual(payload["difficulty"], "medium")
            self.assertIn("Design a module named add8", payload["prompt"])
            self.assertIn("module add8", payload["reference_rtl"])

    def test_load_problems_supports_absolute_glob(self) -> None:
        problems = load_problems(str(ROOT / "benchmarks/**/*.json"))
        ids = {problem.id for problem in problems}
        expected_ids = {
            "rtl_add8",
            "rtl_edge_detect",
            "tb_mod3_counter",
            "hdlbits_vector_rev8",
            "hdlbits_popcount3",
            "hdlbits_mux9_16",
            "hdlbits_shift4",
            "hdlbits_edge_capture8",
            "hdlbits_count10",
            "industrial_valid_ready_slice",
            "industrial_rr_arb4",
        }
        self.assertTrue(expected_ids.issubset(ids))
        self.assertEqual(len(ids), len(problems))

        by_id = {problem.id: problem for problem in problems}
        self.assertEqual(by_id["hdlbits_vector_rev8"].source, "hdlbits")
        self.assertEqual(by_id["hdlbits_vector_rev8"].suite, "hdlbits")
        self.assertEqual(by_id["hdlbits_vector_rev8"].category, "vectors")
        self.assertEqual(by_id["hdlbits_vector_rev8"].difficulty, "easy")
        self.assertEqual(by_id["hdlbits_vector_rev8"].harness_type, "testbench_compare")
        self.assertEqual(by_id["hdlbits_vector_rev8"].evaluation_targets, ["syntax", "functionality", "synthesis"])
        self.assertEqual(by_id["hdlbits_vector_rev8"].exposure, "public")
        self.assertEqual(by_id["tb_mod3_counter"].track, "verification")
        self.assertEqual(by_id["tb_mod3_counter"].prompt_style, "spec_to_testbench")
        self.assertEqual(by_id["tb_mod3_counter"].harness_type, "mutation")
        self.assertEqual(by_id["hdlbits_count10"].category, "counters")
        self.assertEqual(by_id["industrial_valid_ready_slice"].track, "protocol")
        self.assertEqual(by_id["industrial_valid_ready_slice"].difficulty, "hard")
        self.assertEqual(by_id["industrial_valid_ready_slice"].exposure, "curated")
        self.assertEqual(by_id["industrial_rr_arb4"].track, "control")

    def test_load_problems_supports_metadata_filters(self) -> None:
        problems = load_problems(
            str(ROOT / "benchmarks/**/*.json"),
            {
                "sources": ["hdlbits"],
                "tracks": ["rtl_core"],
                "difficulties": ["easy"],
                "tags_any": ["combinational", "mux"],
            },
        )

        self.assertEqual({problem.id for problem in problems}, {"hdlbits_vector_rev8", "hdlbits_popcount3", "hdlbits_mux9_16"})

    def test_problem_filters_can_select_industrial_subset(self) -> None:
        problems = load_problems(
            str(ROOT / "benchmarks/**/*.json"),
            {
                "sources": ["industrial"],
                "tracks": ["protocol", "control"],
                "difficulties": ["hard"],
            },
        )

        self.assertEqual({problem.id for problem in problems}, {"industrial_valid_ready_slice", "industrial_rr_arb4"})


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

    def test_describe_http_error_includes_response_body(self) -> None:
        import io
        import urllib.error

        runner = ModelRunner({})
        err = urllib.error.HTTPError(
            url="https://example.com",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"model not found"}}'),
        )
        detail = runner._describe_request_error(err)
        self.assertIn("HTTP 404 Not Found", detail)
        self.assertIn("model not found", detail)


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
            expected_problem_count = len(load_problems(config["problem_glob"]))

            self.assertEqual(len(result["models"]), 1)
            self.assertEqual(len(result["cases"]), expected_problem_count)
            self.assertEqual(len(result["problems"]), expected_problem_count)
            self.assertEqual(result["problem_ids"], [problem["id"] for problem in result["problems"]])
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

    def test_generation_failure_includes_provider_error_detail(self) -> None:
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

            pipe = BenchmarkPipeline(str(config_path))
            pipe.model_runner.last_error = "HTTP 404 Not Found: model missing"
            pipe.model_runner.generate = lambda model, problem, feedback="": ""  # type: ignore[assignment]

            result = pipe.run(include_known=False)

            self.assertTrue(all("HTTP 404 Not Found" in case["feedback"] for case in result["cases"]))


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
