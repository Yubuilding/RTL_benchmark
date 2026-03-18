from __future__ import annotations

import http.client
import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from rtl_benchmark.evaluator import Evaluator
from rtl_benchmark.importers import import_rtllm_repo, import_verilogeval_repo
from rtl_benchmark.leaderboard import rebuild_leaderboard_from_raw_results
from rtl_benchmark.model_runner import ModelRunner
from rtl_benchmark.model_sources import discover_models
from rtl_benchmark.pipeline import BenchmarkPipeline
from rtl_benchmark.problem_bank import load_problems
from rtl_benchmark.scoring import compute_scored_run
from rtl_benchmark.types import ModelDescriptor, Problem, StageStatus
from rtl_benchmark.utils import save_json


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
        self.assertTrue({"RTLLM", "VerilogEval", "RTL-Repo", "PyHDL-Eval", "AutoBench", "CorrectBench"}.issubset(names))
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

    def test_import_rtllm_repo_supports_official_v2_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            design_dir = tmp_path / "RTLLM" / "Arithmetic" / "Adder" / "adder_8bit"
            design_dir.mkdir(parents=True, exist_ok=True)
            (design_dir / "design_description.txt").write_text("Design adder_8bit.", encoding="utf-8")
            (design_dir / "testbench.v").write_text("module testbench; endmodule\n", encoding="utf-8")
            (design_dir / "verified_adder_8bit.v").write_text(
                "module adder_8bit(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule\n",
                encoding="utf-8",
            )

            outputs = import_rtllm_repo(str(tmp_path / "RTLLM"), str(tmp_path / "benchmarks" / "rtllm"))

            self.assertEqual(len(outputs), 1)
            payload = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["id"], "rtllm_arithmetic_adder_8bit")
            self.assertEqual(payload["category"], "arithmetic/adder")
            self.assertEqual(payload["track"], "arithmetic")
            self.assertEqual(payload["difficulty"], "medium")

    def test_import_verilogeval_repo_converts_spec_to_rtl_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dataset_dir = tmp_path / "verilog-eval" / "dataset_spec-to-rtl"
            dataset_dir.mkdir(parents=True, exist_ok=True)
            (dataset_dir / "Prob140_fsm_hdlc_prompt.txt").write_text(
                "Implement a module named TopModule.\nCreate an HDLC framing FSM.\n",
                encoding="utf-8",
            )
            (dataset_dir / "Prob140_fsm_hdlc_ref.sv").write_text(
                "module RefModule(input clk, input reset, input in, output disc, output flag, output err); endmodule\n",
                encoding="utf-8",
            )
            (dataset_dir / "Prob140_fsm_hdlc_test.sv").write_text(
                "module tb;\nRefModule good1(.clk(clk), .reset(reset), .in(in), .disc(), .flag(), .err());\n"
                "TopModule uut(.clk(clk), .reset(reset), .in(in), .disc(), .flag(), .err());\nendmodule\n",
                encoding="utf-8",
            )

            outputs = import_verilogeval_repo(str(tmp_path / "verilog-eval"), str(tmp_path / "benchmarks" / "verilogeval"))

            self.assertEqual(len(outputs), 1)
            payload = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["id"], "verilogeval_140_fsm_hdlc")
            self.assertEqual(payload["source"], "verilogeval")
            self.assertEqual(payload["suite"], "verilogeval")
            self.assertEqual(payload["category"], "control")
            self.assertEqual(payload["track"], "control")
            self.assertEqual(payload["difficulty"], "hard")
            self.assertEqual(payload["top_module"], "TopModule")
            self.assertIn("module TopModule", payload["module_header"])
            self.assertIn("module RefModule", payload["reference_rtl"])

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
    def test_runner_clamps_excessive_max_tokens(self) -> None:
        runner = ModelRunner({"max_tokens": 1048576})
        self.assertEqual(runner.max_tokens, 8192)

    def test_runner_extracts_hdl_from_fenced_response_with_preamble(self) -> None:
        runner = ModelRunner({})
        generated = runner._finalize_generated_output(
            "Here is the code:\n```verilog\nmodule add8(input [7:0] a, input [7:0] b, output [8:0] sum);\n"
            "  assign sum = a + b;\nendmodule\n```\nThis should work."
        )
        self.assertEqual(
            generated,
            "module add8(input [7:0] a, input [7:0] b, output [8:0] sum);\n  assign sum = a + b;\nendmodule",
        )

    def test_runner_rejects_non_hdl_or_truncated_output(self) -> None:
        runner = ModelRunner({})

        self.assertEqual(runner._finalize_generated_output("I cannot solve this."), "")
        self.assertIn("does not contain a Verilog module", runner.last_error)

        runner.last_error = ""
        self.assertEqual(runner._finalize_generated_output("module add8(input a, output b); assign b = a;"), "")
        self.assertIn("missing endmodule", runner.last_error)

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

    def test_gemini_remote_disconnect_is_reported_as_request_error(self) -> None:
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
        model = ModelDescriptor(id="gemini-2.5-flash", provider="gemini", raw={"_api_key": "inline-secret"})

        with patch.object(
            urllib.request,
            "urlopen",
            side_effect=http.client.RemoteDisconnected("Remote end closed connection without response"),
        ):
            self.assertEqual(runner.generate(model, problem), "")

        self.assertEqual(runner.last_error, "network error: Remote end closed connection without response")
        self.assertEqual(runner.last_trace["error"], runner.last_error)
        self.assertIsNone(runner.last_trace["response"]["status_code"])

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

    def test_openai_trace_captures_full_request_and_response(self) -> None:
        runner = ModelRunner({"max_tokens": 128, "temperature": 0.0})
        problem = Problem(
            id="rtl_add8",
            task_type="rtl",
            language="verilog",
            prompt="Implement add8.",
            top_module="add8",
            reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a+b; endmodule",
            testbench="module tb; endmodule",
        )
        model = ModelDescriptor(
            id="gpt-4.1-mini",
            provider="openai",
            raw={"_base_url": "https://api.openai.com/v1", "_api_key_env": "OPENAI_API_KEY"},
        )

        class FakeResponse:
            status = 200

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "usage": {
                            "prompt_tokens": 120,
                            "completion_tokens": 48,
                            "total_tokens": 168,
                        },
                        "choices": [
                            {
                                "message": {
                                    "content": "module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule"
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            with patch.object(urllib.request, "urlopen", return_value=FakeResponse()):
                generated = runner.generate(model, problem)

        self.assertIn("module add8", generated)
        self.assertEqual(runner.last_trace["provider"], "openai")
        self.assertEqual(runner.last_trace["model_id"], "gpt-4.1-mini")
        self.assertEqual(runner.last_trace["request"]["headers"]["Authorization"], "<redacted>")
        self.assertEqual(runner.last_trace["request"]["payload"]["model"], "gpt-4.1-mini")
        self.assertEqual(runner.last_trace["conversation"][0]["role"], "system")
        self.assertEqual(runner.last_trace["conversation"][1]["role"], "user")
        self.assertEqual(runner.last_trace["conversation"][-1]["role"], "assistant")
        self.assertIn("module add8", runner.last_trace["response"]["assistant_output"])
        self.assertIn("\"choices\"", runner.last_trace["response"]["raw_text"])
        self.assertEqual(runner.last_trace["metrics"]["prompt_tokens"], 120)
        self.assertEqual(runner.last_trace["metrics"]["completion_tokens"], 48)
        self.assertEqual(runner.last_trace["metrics"]["total_tokens"], 168)
        self.assertGreaterEqual(runner.last_trace["metrics"]["duration_seconds"], 0.0)
        self.assertIn("output_chars", runner.last_trace["metrics"])

    def test_openai_generation_accepts_model_scoped_api_key(self) -> None:
        runner = ModelRunner({"max_tokens": 128, "temperature": 0.0})
        problem = Problem(
            id="rtl_add8",
            task_type="rtl",
            language="verilog",
            prompt="Implement add8.",
            top_module="add8",
            reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a+b; endmodule",
            testbench="module tb; endmodule",
        )
        model = ModelDescriptor(
            id="gpt-4.1-mini",
            provider="openai",
            raw={"_base_url": "https://api.openai.com/v1", "_api_key_env": "OPENAI_API_KEY", "_api_key": "inline-secret"},
        )

        class FakeResponse:
            status = 200

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule"
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        with patch.object(urllib.request, "urlopen", return_value=FakeResponse()):
            generated = runner.generate(model, problem)

        self.assertIn("module add8", generated)
        self.assertEqual(runner.last_trace["model_id"], "gpt-4.1-mini")

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


class EvaluatorTests(unittest.TestCase):
    def test_invalid_candidate_is_rejected_before_tool_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problem = Problem(
                id="rtl_add8",
                task_type="rtl",
                language="verilog",
                prompt="Implement add8.",
                top_module="add8",
                reference_rtl="module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule",
                testbench="module tb; endmodule",
            )
            evaluator = Evaluator(tmp)

            with patch("rtl_benchmark.evaluator.run_cmd") as run_cmd:
                result = evaluator.evaluate(
                    model_id="bad/model",
                    problem=problem,
                    candidate_code="I think the answer should be an adder.",
                    attempt=1,
                )

            run_cmd.assert_not_called()
            self.assertFalse(result.passed)
            self.assertEqual(result.lint.status, "skipped")
            self.assertEqual(result.simulation.status, "skipped")
            self.assertEqual(result.synthesis.status, "skipped")
            self.assertIn("invalid HDL candidate", result.feedback)

    def test_eval_rtl_writes_reference_module_when_testbench_uses_external_golden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problem = Problem(
                id="verilogeval_demo",
                task_type="rtl",
                language="systemverilog",
                prompt="Implement TopModule.",
                top_module="TopModule",
                reference_rtl="module TopModule(input logic a, output logic y); assign y = a; endmodule\n",
                testbench=(
                    "module tb;\n"
                    "  logic a;\n"
                    "  logic y_ref;\n"
                    "  logic y_dut;\n"
                    "  RefModule good1(.a(a), .y(y_ref));\n"
                    "  TopModule uut(.a(a), .y(y_dut));\n"
                    "endmodule\n"
                ),
            )
            evaluator = Evaluator(tmp)
            commands: list[tuple[list[str], str]] = []

            def fake_run_cmd(cmd: list[str], cwd: Path, log_name: str, timeout_seconds: int = 20) -> StageStatus:
                commands.append((cmd, log_name))
                return StageStatus(status="pass")

            with patch("rtl_benchmark.evaluator.tool_exists", return_value=True):
                with patch("rtl_benchmark.evaluator.run_cmd", side_effect=fake_run_cmd):
                    result = evaluator.evaluate(
                        model_id="openrouter/hunter-alpha",
                        problem=problem,
                        candidate_code="module TopModule(input logic a, output logic y); assign y = a; endmodule\n",
                        attempt=1,
                    )

            case_dir = Path(result.artifact_dir)
            reference_file = case_dir / "RefModule.sv"

            self.assertTrue(result.passed)
            self.assertTrue(reference_file.exists())
            self.assertIn("module RefModule", reference_file.read_text(encoding="utf-8"))
            self.assertTrue(any(log_name == "lint.log" and "RefModule.sv" in cmd for cmd, log_name in commands))
            self.assertTrue(any(log_name == "simv_compile.log" and "RefModule.sv" in cmd for cmd, log_name in commands))

    def test_eval_rtl_does_not_treat_procedural_begin_if_as_reference_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problem = Problem(
                id="verilogeval_begin_false_positive",
                task_type="rtl",
                language="systemverilog",
                prompt="Implement TopModule.",
                top_module="TopModule",
                reference_rtl="module TopModule(input logic a, output logic y); assign y = a; endmodule\n",
                testbench=(
                    "module stimulus_gen(input clk, output logic a);\n"
                    "endmodule\n"
                    "module tb;\n"
                    "  logic clk;\n"
                    "  logic a;\n"
                    "  logic y_ref;\n"
                    "  logic y_dut;\n"
                    "  stimulus_gen stim1(.clk(clk), .a(a));\n"
                    "  RefModule good1(.a(a), .y(y_ref));\n"
                    "  TopModule uut(.a(a), .y(y_dut));\n"
                    "  always @(posedge clk) begin\n"
                    "    if (y_ref !== y_dut)\n"
                    "    begin if (1) y_ref <= y_dut; end\n"
                    "  end\n"
                    "endmodule\n"
                ),
            )
            evaluator = Evaluator(tmp)
            commands: list[tuple[list[str], str]] = []

            def fake_run_cmd(cmd: list[str], cwd: Path, log_name: str, timeout_seconds: int = 20) -> StageStatus:
                commands.append((cmd, log_name))
                return StageStatus(status="pass")

            with patch("rtl_benchmark.evaluator.tool_exists", return_value=True):
                with patch("rtl_benchmark.evaluator.run_cmd", side_effect=fake_run_cmd):
                    result = evaluator.evaluate(
                        model_id="openrouter/hunter-alpha",
                        problem=problem,
                        candidate_code="module TopModule(input logic a, output logic y); assign y = a; endmodule\n",
                        attempt=1,
                    )

            case_dir = Path(result.artifact_dir)
            reference_file = case_dir / "RefModule.sv"

            self.assertTrue(result.passed)
            self.assertTrue(reference_file.exists())
            self.assertFalse((case_dir / "begin.sv").exists())
            self.assertTrue(any(log_name == "lint.log" and "RefModule.sv" in cmd for cmd, log_name in commands))
            self.assertFalse(any("begin.sv" in cmd for cmd, _ in commands))

    def test_run_sim_wraps_vvp_with_waveform_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            evaluator = Evaluator(tmp, {"waveform_max_bytes": "1m"})

            commands: list[list[str]] = []

            def fake_run_cmd(cmd: list[str], cwd: Path, log_name: str, timeout_seconds: int = 20) -> StageStatus:
                commands.append(cmd)
                return StageStatus(status="pass")

            with patch("rtl_benchmark.evaluator.tool_exists", return_value=True):
                with patch("rtl_benchmark.evaluator.run_cmd", side_effect=fake_run_cmd):
                    result = evaluator._run_sim(["dut.sv", "tb.sv"], case_dir, dump_scope="tb")

            self.assertEqual(result.status, "pass")
            self.assertEqual(commands[0][:4], ["iverilog", "-g2012", "-o", "simv"])
            self.assertEqual(commands[1][:2], ["/bin/sh", "-lc"])
            self.assertIn("ulimit -f 2048", commands[1][2])
            self.assertIn("exec vvp simv", commands[1][2])

    def test_detect_module_name_prefers_tb_over_helper_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "tb.sv"
            source_path.write_text(
                "module stimulus_gen(input clk);\n"
                "endmodule\n"
                "module tb;\n"
                "endmodule\n",
                encoding="utf-8",
            )

            evaluator = Evaluator(tmp)

            self.assertEqual(evaluator._detect_module_name(source_path), "tb")

    def test_run_sim_reports_waveform_limit_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            evaluator = Evaluator(tmp, {"waveform_max_bytes": 1024})
            wave_path = case_dir / "simv.vcd"

            def fake_run_cmd(cmd: list[str], cwd: Path, log_name: str, timeout_seconds: int = 20) -> StageStatus:
                if log_name == "simv_compile.log":
                    return StageStatus(status="pass")
                wave_path.write_bytes(b"x" * 1024)
                return StageStatus(status="fail", returncode=153)

            with patch("rtl_benchmark.evaluator.tool_exists", return_value=True):
                with patch("rtl_benchmark.evaluator.run_cmd", side_effect=fake_run_cmd):
                    result = evaluator._run_sim(["dut.sv", "tb.sv"], case_dir, dump_scope="tb")

            self.assertEqual(result.status, "fail")
            self.assertIn("waveform exceeded size limit", result.reason)
            self.assertTrue(wave_path.exists())

    def test_cleanup_run_artifacts_preserves_logs_sources_and_waveforms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            keep_paths = [
                case_dir / "simv.vcd",
                case_dir / "simv_compile.log",
                case_dir / "dut.sv",
                case_dir / "tb.sv",
            ]
            drop_paths = [
                case_dir / "simv",
                case_dir / "simv_wave_dump.sv",
            ]

            for path in keep_paths + drop_paths:
                path.write_text(path.name, encoding="utf-8")

            Evaluator(tmp)._cleanup_run_artifacts(case_dir, "simv")

            for path in keep_paths:
                self.assertTrue(path.exists(), path.name)
            for path in drop_paths:
                self.assertFalse(path.exists(), path.name)


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


class ScoringTests(unittest.TestCase):
    def _sample_problems(self) -> list[dict]:
        return [
            {
                "id": "src1_easy",
                "task_type": "rtl",
                "source": "src1",
                "suite": "suite1",
                "track": "rtl_core",
                "difficulty": "easy",
                "exposure": "public",
                "tags": ["basic"],
            },
            {
                "id": "src1_medium",
                "task_type": "rtl",
                "source": "src1",
                "suite": "suite1",
                "track": "rtl_core",
                "difficulty": "medium",
                "exposure": "public",
                "tags": ["fsm"],
            },
            {
                "id": "src1_hard",
                "task_type": "rtl",
                "source": "src1",
                "suite": "suite1",
                "track": "control",
                "difficulty": "hard",
                "exposure": "public",
                "tags": ["fsm"],
            },
            {
                "id": "src2_easy",
                "task_type": "rtl",
                "source": "src2",
                "suite": "suite2",
                "track": "rtl_core",
                "difficulty": "easy",
                "exposure": "public",
                "tags": ["basic"],
            },
            {
                "id": "tb_mutation",
                "task_type": "testbench",
                "source": "src2",
                "suite": "suite2",
                "track": "verification",
                "difficulty": "medium",
                "exposure": "public",
                "tags": ["verification"],
            },
        ]

    def _rtl_case(self, model_id: str, problem_id: str, passed: bool, sim_status: str = "pass", synth_status: str = "pass", lint_status: str = "pass") -> dict:
        return {
            "model_id": model_id,
            "provider": "mock",
            "problem_id": problem_id,
            "task_type": "rtl",
            "attempt": 1,
            "passed": passed,
            "lint": {"status": lint_status},
            "simulation": {"status": sim_status},
            "synthesis": {"status": synth_status},
            "feedback": "ok",
        }

    def test_source_budgets_are_equal_after_weight_normalization(self) -> None:
        cases = [
            self._rtl_case("model_a", "src1_easy", True),
            self._rtl_case("model_a", "src1_medium", True),
            self._rtl_case("model_a", "src1_hard", True),
            self._rtl_case("model_a", "src2_easy", True),
            {
                "model_id": "model_a",
                "provider": "mock",
                "problem_id": "tb_mutation",
                "task_type": "testbench",
                "attempt": 1,
                "passed": True,
                "lint": {"status": "pass"},
                "simulation": {"status": "pass"},
                "synthesis": {"status": "skipped"},
                "mutation_kill_rate": 1.0,
                "feedback": "ok",
            },
        ]
        scored = compute_scored_run(cases, problems=self._sample_problems())
        final_cases = scored["final_cases"]
        src1_weight = sum(case["problem_weight"] for case in final_cases if case["problem_source"] == "src1")
        src2_weight = sum(case["problem_weight"] for case in final_cases if case["problem_source"] == "src2")
        self.assertAlmostEqual(src1_weight, 0.5, places=4)
        self.assertAlmostEqual(src2_weight, 0.5, places=4)

    def test_harder_problems_receive_higher_weights_within_source(self) -> None:
        cases = [
            self._rtl_case("model_a", "src1_easy", True),
            self._rtl_case("model_a", "src1_medium", True),
            self._rtl_case("model_a", "src1_hard", True),
        ]
        scored = compute_scored_run(cases, problems=self._sample_problems())
        weights = {case["problem_id"]: case["problem_weight"] for case in scored["final_cases"]}
        self.assertLess(weights["src1_easy"], weights["src1_medium"])
        self.assertLess(weights["src1_medium"], weights["src1_hard"])

    def test_skipped_stages_are_renormalized_for_quality_score(self) -> None:
        cases = [
            self._rtl_case("model_a", "src1_easy", False, sim_status="pass", synth_status="skipped", lint_status="skipped"),
        ]
        scored = compute_scored_run(cases, problems=self._sample_problems())
        self.assertEqual(scored["final_cases"][0]["quality_points"], 1.0)

    def test_testbench_quality_uses_mutation_kill_rate(self) -> None:
        cases = [
            {
                "model_id": "model_a",
                "provider": "mock",
                "problem_id": "tb_mutation",
                "task_type": "testbench",
                "attempt": 1,
                "passed": False,
                "lint": {"status": "pass"},
                "simulation": {"status": "pass"},
                "synthesis": {"status": "skipped"},
                "mutation_kill_rate": 0.4,
                "feedback": "mutation weak",
            }
        ]
        scored = compute_scored_run(cases, problems=self._sample_problems())
        self.assertAlmostEqual(scored["final_cases"][0]["quality_points"], 0.7, places=4)

    def test_strength_and_weakness_detection_is_stable(self) -> None:
        cases = []
        for problem_id in ("src1_easy", "src1_medium", "src1_hard"):
            cases.append(self._rtl_case("strong_fsm", problem_id, True))
            cases.append(self._rtl_case("weak_fsm", problem_id, False, sim_status="fail"))
        for problem_id in ("src2_easy", "tb_mutation"):
            cases.append(self._rtl_case("strong_fsm", problem_id, False, sim_status="fail"))
            cases.append(self._rtl_case("weak_fsm", problem_id, True))
        scored = compute_scored_run(cases, problems=self._sample_problems(), scoring_config={"profile_min_cases": 2})
        rows = {row["model_id"]: row for row in scored["summary"]}
        self.assertTrue(rows["strong_fsm"]["strengths"])
        self.assertTrue(rows["weak_fsm"]["weaknesses"])
        self.assertIn("擅长", rows["strong_fsm"]["profile_summary"])
        self.assertIn("薄弱于", rows["weak_fsm"]["profile_summary"])

    def test_rebuild_leaderboard_recomputes_scored_fields_from_raw_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            leaderboard_path = tmp_path / "leaderboard.json"
            save_json(
                raw_dir / "suite.json",
                {
                    "run_id": "suite",
                    "started_at": "2026-03-15T10:00:00Z",
                    "scope": "suite",
                    "problem_ids": ["src1_easy", "src1_medium", "src1_hard"],
                    "problems": self._sample_problems(),
                    "cases": [
                        self._rtl_case("model_a", "src1_easy", True),
                        self._rtl_case("model_a", "src1_medium", False, sim_status="fail"),
                        self._rtl_case("model_a", "src1_hard", True),
                    ],
                    "summary": [{"model_id": "model_a", "score": 0.5}],
                },
            )

            board = rebuild_leaderboard_from_raw_results(str(leaderboard_path), str(raw_dir))

            self.assertTrue(board["models"])
            self.assertIn("weighted_pass_score", board["models"][0])
            self.assertIn("quality_score", board["models"][0])
            self.assertIn("slice_rankings", board)
            self.assertIn("scoring_policy", board)

    def test_rebuild_leaderboard_includes_selected_problems_and_deduplicates_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            leaderboard_path = tmp_path / "leaderboard.json"
            problems = self._sample_problems()

            save_json(
                raw_dir / "suite.json",
                {
                    "run_id": "suite",
                    "started_at": "2026-03-15T10:00:00Z",
                    "scope": "suite",
                    "problem_ids": ["src1_easy", "src1_medium"],
                    "problems": problems,
                    "cases": [
                        self._rtl_case("model_a", "src1_easy", True),
                        self._rtl_case("model_a", "src1_medium", False, sim_status="fail"),
                    ],
                },
            )
            save_json(
                raw_dir / "selected.json",
                {
                    "run_id": "selected",
                    "started_at": "2026-03-15T11:00:00Z",
                    "scope": "selected_problems",
                    "problem_ids": ["src1_hard"],
                    "problems": problems,
                    "cases": [self._rtl_case("model_a", "src1_hard", True)],
                },
            )
            save_json(
                raw_dir / "selected_retry.json",
                {
                    "run_id": "selected_retry",
                    "started_at": "2026-03-15T12:00:00Z",
                    "scope": "selected_problems",
                    "problem_ids": ["src1_easy"],
                    "problems": problems,
                    "cases": [self._rtl_case("model_a", "src1_easy", False, sim_status="fail")],
                },
            )

            board = rebuild_leaderboard_from_raw_results(str(leaderboard_path), str(raw_dir))

            self.assertEqual(len(board["models"]), 1)
            model = board["models"][0]
            self.assertEqual(model["model_id"], "model_a")
            self.assertEqual(model["cases"], 3)
            self.assertEqual(model["runs"], 3)
            self.assertEqual(model["last_run_id"], "selected_retry")
            self.assertEqual(model["last_scope"], "selected_problems")
            self.assertEqual(model["last_problem_count"], 1)
            self.assertAlmostEqual(model["pass_rate"], 1 / 3, places=4)

    def test_rebuild_leaderboard_skips_malformed_raw_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            leaderboard_path = tmp_path / "leaderboard.json"

            save_json(
                raw_dir / "valid.json",
                {
                    "run_id": "valid",
                    "started_at": "2026-03-15T10:00:00Z",
                    "scope": "suite",
                    "problem_ids": ["src1_easy"],
                    "problems": self._sample_problems(),
                    "cases": [self._rtl_case("model_a", "src1_easy", True)],
                },
            )
            (raw_dir / "broken.json").write_text('{"run_id": "broken", "cases": [', encoding="utf-8")

            board = rebuild_leaderboard_from_raw_results(str(leaderboard_path), str(raw_dir))

            self.assertEqual(len(board["models"]), 1)
            self.assertEqual(board["models"][0]["model_id"], "model_a")
            self.assertEqual(board["models"][0]["runs"], 1)


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
