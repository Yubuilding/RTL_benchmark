from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from rtl_benchmark.types import CaseResult, Problem, StageStatus
from rtl_benchmark.utils import ensure_dir, extract_hdl_code, tool_exists, validate_hdl_candidate


class Evaluator:
    def __init__(self, run_root: str, execution_config: dict | None = None):
        self.run_root = ensure_dir(run_root)
        config = execution_config or {}
        self.execution_mode = str(config.get("mode", "local"))
        self.timeout_seconds = int(config.get("timeout_seconds", 20))
        self.waveform_max_bytes = parse_byte_size(config.get("waveform_max_bytes"), 32 * 1024 * 1024)
        self.cleanup_intermediate_artifacts = bool(config.get("cleanup_intermediate_artifacts", True))
        self.docker_image = str(config.get("docker_image", "rtl-benchmark-tools:latest"))
        self.docker_binary = str(config.get("docker_binary", "docker"))
        self.container_workdir = str(config.get("container_workdir", "/workspace"))
        self.docker_network = str(config.get("docker_network", "none"))
        self.docker_read_only_rootfs = bool(config.get("docker_read_only_rootfs", True))
        self.docker_tmpfs_mounts = list(config.get("docker_tmpfs_mounts", ["/tmp"]))
        self.docker_security_opts = list(config.get("docker_security_opts", ["no-new-privileges:true"]))
        self.docker_cap_drop = list(config.get("docker_cap_drop", ["ALL"]))
        self.docker_pids_limit = int(config.get("docker_pids_limit", 256))
        self.docker_memory = str(config.get("docker_memory", "1g"))
        self.docker_cpus = str(config.get("docker_cpus", "1.0"))
        self._docker_preflight: StageStatus | None = None

    def evaluate(self, model_id: str, problem: Problem, candidate_code: str, attempt: int) -> CaseResult:
        case_dir = ensure_dir(self.run_root / safe_name(model_id) / problem.id / f"attempt_{attempt}")
        normalized_candidate = extract_hdl_code(candidate_code)
        invalid_reason = validate_hdl_candidate(normalized_candidate)
        if invalid_reason:
            return self._invalid_candidate_result(model_id, problem, attempt, case_dir, invalid_reason)

        if problem.task_type == "rtl":
            return self._eval_rtl(model_id, problem, normalized_candidate, attempt, case_dir)
        if problem.task_type == "testbench":
            return self._eval_tb(model_id, problem, normalized_candidate, attempt, case_dir)

        return CaseResult(
            model_id=model_id,
            problem_id=problem.id,
            task_type=problem.task_type,
            attempt=attempt,
            passed=False,
            lint=StageStatus(status="skipped", reason="unsupported task_type"),
            simulation=StageStatus(status="skipped", reason="unsupported task_type"),
            synthesis=StageStatus(status="skipped", reason="unsupported task_type"),
            feedback="Unsupported task type",
        )

    def _invalid_candidate_result(
        self,
        model_id: str,
        problem: Problem,
        attempt: int,
        case_dir: Path,
        reason: str,
    ) -> CaseResult:
        skipped = StageStatus(status="skipped", reason="invalid candidate")
        return CaseResult(
            model_id=model_id,
            problem_id=problem.id,
            task_type=problem.task_type,
            attempt=attempt,
            passed=False,
            lint=skipped,
            simulation=skipped,
            synthesis=skipped,
            feedback=f"invalid HDL candidate: {reason}",
            artifact_dir=str(case_dir.resolve()),
            artifacts=list_case_artifacts(case_dir),
        )

    def _eval_rtl(self, model_id: str, problem: Problem, rtl_code: str, attempt: int, case_dir: Path) -> CaseResult:
        dut = case_dir / "dut.sv"
        tb = case_dir / "tb.sv"
        dut.write_text(rtl_code, encoding="utf-8")
        tb.write_text(problem.testbench, encoding="utf-8")
        dump_scope = self._detect_module_name(tb)
        compile_inputs = [dut.name, tb.name]
        reference_file = self._write_reference_module(problem, case_dir)
        if reference_file is not None:
            compile_inputs.insert(1, reference_file.name)

        lint = self._run_lint(compile_inputs, case_dir)
        sim = self._run_sim(compile_inputs, case_dir, dump_scope=dump_scope)
        synth = self._run_synth(dut.name, problem.top_module, case_dir)
        artifacts = list_case_artifacts(case_dir)

        passed = sim.status == "pass" and (synth.status in {"pass", "skipped"}) and (lint.status in {"pass", "skipped"})
        feedback = build_feedback(lint, sim, synth)

        return CaseResult(
            model_id=model_id,
            problem_id=problem.id,
            task_type=problem.task_type,
            attempt=attempt,
            passed=passed,
            lint=lint,
            simulation=sim,
            synthesis=synth,
            feedback=feedback,
            artifact_dir=str(case_dir.resolve()),
            artifacts=artifacts,
        )

    def _eval_tb(self, model_id: str, problem: Problem, tb_code: str, attempt: int, case_dir: Path) -> CaseResult:
        tb = case_dir / "tb.sv"
        dut_golden = case_dir / "dut_golden.sv"

        tb.write_text(tb_code, encoding="utf-8")
        dut_golden.write_text(problem.golden_rtl, encoding="utf-8")
        dump_scope = self._detect_module_name(tb)

        lint = self._run_lint([dut_golden.name, tb.name], case_dir)

        golden_sim = self._run_sim([dut_golden.name, tb.name], case_dir, output_name="simv_golden", dump_scope=dump_scope)

        mutant_results: list[StageStatus] = []
        for idx, mutant in enumerate(problem.mutant_rtls, start=1):
            dut_mutant = case_dir / f"dut_mutant_{idx}.sv"
            dut_mutant.write_text(mutant, encoding="utf-8")
            mutant_results.append(
                self._run_sim(
                    [dut_mutant.name, tb.name],
                    case_dir,
                    output_name=f"simv_mutant_{idx}",
                    dump_scope=dump_scope,
                )
            )

        kills = sum(1 for m in mutant_results if m.status == "fail")
        kill_rate = (kills / len(mutant_results)) if mutant_results else None

        passed = (
            golden_sim.status == "pass"
            and kill_rate is not None
            and kill_rate >= problem.min_kill_rate
            and lint.status in {"pass", "skipped"}
        )

        synth = StageStatus(status="skipped", reason="synthesis is not used for testbench tasks")
        feedback = build_tb_feedback(lint, golden_sim, kill_rate, problem.min_kill_rate)
        artifacts = list_case_artifacts(case_dir)

        return CaseResult(
            model_id=model_id,
            problem_id=problem.id,
            task_type=problem.task_type,
            attempt=attempt,
            passed=passed,
            lint=lint,
            simulation=golden_sim,
            synthesis=synth,
            mutation_kill_rate=kill_rate,
            mutation_results=mutant_results,
            feedback=feedback,
            artifact_dir=str(case_dir.resolve()),
            artifacts=artifacts,
        )

    def _run_lint(self, sv_files: list[str], case_dir: Path) -> StageStatus:
        cmd = ["verilator", "--lint-only", "--timing", "-Wall", "-Wno-fatal", *sv_files]
        return self._run_tool(cmd, case_dir, "lint.log", required_tools=["verilator"])

    def _run_sim(
        self,
        sv_files: list[str],
        case_dir: Path,
        output_name: str = "simv",
        dump_scope: str = "",
    ) -> StageStatus:
        compile_inputs = list(sv_files)
        wave_support = self._write_wave_support(case_dir, dump_scope, output_name)
        if wave_support:
            compile_inputs.append(wave_support.name)
        wave_path = case_dir / f"{output_name}.vcd"

        try:
            compile_cmd = ["iverilog", "-g2012", "-o", output_name, *compile_inputs]
            compile_result = self._run_tool(
                compile_cmd,
                case_dir,
                f"{output_name}_compile.log",
                required_tools=["iverilog", "vvp"],
            )
            if compile_result.status != "pass":
                return compile_result

            run_cmd = ["vvp", output_name]
            if self.waveform_max_bytes > 0:
                run_cmd = self._wrap_with_file_limit(run_cmd, self.waveform_max_bytes)
            run_result = self._run_tool(run_cmd, case_dir, f"{output_name}_run.log", required_tools=["vvp"])
            return self._annotate_waveform_limit(run_result, wave_path)
        finally:
            if self.cleanup_intermediate_artifacts:
                self._cleanup_run_artifacts(case_dir, output_name)

    def _run_synth(self, dut_file: str, top_module: str, case_dir: Path) -> StageStatus:
        yosys_script = f"read_verilog -sv {dut_file}; synth -top {top_module}; stat"
        cmd = ["yosys", "-p", yosys_script]
        return self._run_tool(cmd, case_dir, "synth.log", required_tools=["yosys"])

    def _run_tool(self, cmd: list[str], case_dir: Path, log_name: str, required_tools: list[str]) -> StageStatus:
        if self.execution_mode == "docker":
            preflight = self.check_execution_backend()
            if preflight.status != "pass":
                return preflight
            docker_cmd = self._build_docker_cmd(cmd, case_dir)
            return run_cmd(docker_cmd, case_dir, log_name, timeout_seconds=self.timeout_seconds)

        missing = [tool for tool in required_tools if not tool_exists(tool)]
        if missing:
            return StageStatus(status="skipped", reason=f"missing tools: {', '.join(missing)}")
        return run_cmd(cmd, case_dir, log_name, timeout_seconds=self.timeout_seconds)

    def check_execution_backend(self) -> StageStatus:
        if self.execution_mode != "docker":
            return StageStatus(status="pass", reason="local execution mode")
        if self._docker_preflight is not None:
            return self._docker_preflight

        if not tool_exists(self.docker_binary):
            self._docker_preflight = StageStatus(status="skipped", reason=f"{self.docker_binary} not found")
            return self._docker_preflight

        inspect_cmd = [self.docker_binary, "image", "inspect", self.docker_image]
        result = run_cmd(inspect_cmd, self.run_root, "docker_preflight.log", timeout_seconds=self.timeout_seconds)
        if result.status == "pass":
            self._docker_preflight = StageStatus(status="pass", reason=f"docker image ready: {self.docker_image}")
            return self._docker_preflight

        detail = (result.stderr or result.stdout or "").lower()
        if "permission denied" in detail or "cannot connect" in detail or "docker daemon" in detail:
            self._docker_preflight = StageStatus(
                status="skipped",
                reason=f"docker daemon unavailable: {(result.stderr or result.stdout or 'unable to access daemon').strip()}",
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            return self._docker_preflight

        self._docker_preflight = StageStatus(
            status="skipped",
            reason=f"docker image not found: {self.docker_image}. Build it with: docker build -t {self.docker_image} .",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return self._docker_preflight

    def _build_docker_cmd(self, cmd: list[str], case_dir: Path) -> list[str]:
        case_dir_abs = str(case_dir.resolve())
        docker_cmd = [
            self.docker_binary,
            "run",
            "--rm",
            "-v",
            f"{case_dir_abs}:{self.container_workdir}",
            "-w",
            self.container_workdir,
        ]

        if self.docker_network:
            docker_cmd.extend(["--network", self.docker_network])
        if self.docker_read_only_rootfs:
            docker_cmd.append("--read-only")
        if self.docker_memory:
            docker_cmd.extend(["--memory", self.docker_memory])
        if self.docker_cpus:
            docker_cmd.extend(["--cpus", self.docker_cpus])
        if self.docker_pids_limit > 0:
            docker_cmd.extend(["--pids-limit", str(self.docker_pids_limit)])
        for mount in self.docker_tmpfs_mounts:
            docker_cmd.extend(["--tmpfs", mount])
        for sec in self.docker_security_opts:
            docker_cmd.extend(["--security-opt", sec])
        for cap in self.docker_cap_drop:
            docker_cmd.extend(["--cap-drop", cap])

        uid = getattr(os, "getuid", None)
        gid = getattr(os, "getgid", None)
        if callable(uid) and callable(gid):
            docker_cmd.extend(["-u", f"{uid()}:{gid()}"])

        docker_cmd.append(self.docker_image)
        docker_cmd.extend(cmd)
        return docker_cmd

    def _detect_module_name(self, source_path: Path) -> str:
        text = source_path.read_text(encoding="utf-8")
        matches = re.findall(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", text)
        if not matches:
            return ""
        for module_name in matches:
            if module_name.lower() in {"tb", "testbench"}:
                return module_name
        return matches[0]

    def _write_wave_support(self, case_dir: Path, dump_scope: str, output_name: str) -> Path | None:
        if not dump_scope:
            return None
        wave_file = case_dir / f"{output_name}.vcd"
        helper = case_dir / f"{output_name}_wave_dump.sv"
        helper.write_text(
            (
                "module _rtl_benchmark_wave_dump;\n"
                "  initial begin\n"
                f'    $dumpfile("{wave_file.name}");\n'
                f"    $dumpvars(0, {dump_scope});\n"
                "  end\n"
                "endmodule\n"
            ),
            encoding="utf-8",
        )
        return helper

    def _write_reference_module(self, problem: Problem, case_dir: Path) -> Path | None:
        reference_module = self._detect_reference_module_name(problem)
        if not reference_module:
            return None

        reference_rtl = problem.reference_rtl.strip()
        if not reference_rtl:
            return None
        if not self._rtl_defines_module(reference_rtl, reference_module):
            reference_rtl = self._rename_first_module(reference_rtl, reference_module)

        reference_path = case_dir / f"{reference_module}.sv"
        reference_path.write_text(reference_rtl, encoding="utf-8")
        return reference_path

    def _detect_reference_module_name(self, problem: Problem) -> str:
        testbench_text = problem.testbench
        if not testbench_text.strip():
            return ""

        ignored_module_names = {
            "module",
            "if",
            "else",
            "for",
            "while",
            "case",
            "task",
            "function",
            "begin",
            "end",
            "always",
            "initial",
            "final",
            "assign",
            problem.top_module,
        }
        ignored_lower = {"tb", "testbench", "stimulus_gen"}

        instantiation_re = re.compile(
            r"(?ms)^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\(.*?\)\s*;"
        )
        for match in instantiation_re.finditer(testbench_text):
            module_name = match.group(1)
            instance_name = match.group(2).lower()
            if module_name in ignored_module_names:
                continue
            if module_name.lower() in ignored_lower:
                continue
            if instance_name in ignored_module_names or instance_name in ignored_lower:
                continue
            if instance_name.startswith(("top_module", "dut", "uut", "stim")):
                continue
            return module_name
        return ""

    def _rtl_defines_module(self, rtl_text: str, module_name: str) -> bool:
        return bool(re.search(rf"\bmodule\s+{re.escape(module_name)}\b", rtl_text))

    def _rename_first_module(self, rtl_text: str, module_name: str) -> str:
        return re.sub(
            r"(\bmodule\s+)([A-Za-z_][A-Za-z0-9_$]*)",
            rf"\1{module_name}",
            rtl_text,
            count=1,
        )

    def _wrap_with_file_limit(self, cmd: list[str], max_bytes: int) -> list[str]:
        blocks = max(1, (max_bytes + 511) // 512)
        shell_cmd = f"ulimit -f {blocks}; exec {shlex.join(cmd)}"
        return ["/bin/sh", "-lc", shell_cmd]

    def _annotate_waveform_limit(self, result: StageStatus, wave_path: Path) -> StageStatus:
        if result.status == "pass" or self.waveform_max_bytes <= 0 or not wave_path.exists():
            return result
        try:
            size = wave_path.stat().st_size
        except OSError:
            return result
        if size < self.waveform_max_bytes:
            return result

        reason = f"waveform exceeded size limit ({format_byte_size(self.waveform_max_bytes)})"
        return StageStatus(
            status="fail",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            reason=reason,
        )

    def _cleanup_run_artifacts(self, case_dir: Path, output_name: str) -> None:
        for path in (
            case_dir / output_name,
            case_dir / f"{output_name}_wave_dump.sv",
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                continue


def run_cmd(cmd: list[str], cwd: Path, log_name: str, timeout_seconds: int = 20) -> StageStatus:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_seconds)
        status = "pass" if proc.returncode == 0 else "fail"
        out = proc.stdout[-8000:]
        err = proc.stderr[-8000:]

        log_path = cwd / log_name
        log_path.write_text(
            f"CMD: {' '.join(cmd)}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}\n",
            encoding="utf-8",
        )
        return StageStatus(status=status, returncode=proc.returncode, stdout=out, stderr=err)
    except FileNotFoundError as exc:
        return StageStatus(status="skipped", reason=str(exc))
    except PermissionError as exc:
        return StageStatus(status="skipped", reason=str(exc))
    except subprocess.TimeoutExpired:
        return StageStatus(status="fail", reason="timeout")


def build_feedback(lint: StageStatus, sim: StageStatus, synth: StageStatus) -> str:
    if sim.status == "fail":
        return trim_feedback("simulation failed", sim)
    if lint.status == "fail":
        return trim_feedback("lint failed", lint)
    if synth.status == "fail":
        return trim_feedback("synthesis failed", synth)
    if sim.status == "skipped":
        return f"simulation skipped: {sim.reason or 'stage skipped'}"
    return "passed"


def build_tb_feedback(lint: StageStatus, golden_sim: StageStatus, kill_rate: float | None, min_kill: float) -> str:
    if golden_sim.status == "skipped":
        return f"simulation skipped: {golden_sim.reason or 'stage skipped'}"
    if golden_sim.status != "pass":
        return trim_feedback("golden DUT failed under generated testbench", golden_sim)
    if kill_rate is None:
        return "no mutants configured"
    if kill_rate < min_kill:
        return f"mutation kill-rate too low: {kill_rate:.2f} < {min_kill:.2f}"
    if lint.status == "fail":
        return trim_feedback("lint failed", lint)
    return "passed"


def trim_feedback(prefix: str, stage: StageStatus) -> str:
    detail = (stage.stderr or stage.stdout or stage.reason).strip()
    detail = detail[:1200] if detail else ""
    return f"{prefix}: {detail}"


def safe_name(value: str) -> str:
    return value.replace("/", "__").replace(":", "_")


def parse_byte_size(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(0, value)

    text = str(value).strip().lower()
    if not text:
        return default
    match = re.fullmatch(r"(\d+)\s*([kmgt]?)(?:i)?(?:b)?", text)
    if not match:
        return default
    number = int(match.group(1))
    suffix = match.group(2)
    multipliers = {
        "": 1,
        "k": 1024,
        "m": 1024**2,
        "g": 1024**3,
        "t": 1024**4,
    }
    return max(0, number * multipliers[suffix])


def format_byte_size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}"
    return f"{value} B"


def list_case_artifacts(case_dir: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for path in sorted(case_dir.glob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        artifacts.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "kind": artifact_kind(suffix),
            }
        )
    return artifacts


def artifact_kind(suffix: str) -> str:
    if suffix in {".log", ".txt", ".json"}:
        return "log"
    if suffix in {".sv", ".v"}:
        return "source"
    if suffix in {".vcd", ".fst"}:
        return "waveform"
    return "binary"
