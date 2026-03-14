from __future__ import annotations

import glob
from pathlib import Path

from rtl_benchmark.types import Problem
from rtl_benchmark.utils import load_json


REQUIRED_FIELDS = {"id", "task_type", "language", "prompt", "top_module"}


def load_problems(glob_pattern: str) -> list[Problem]:
    files = resolve_problem_files(glob_pattern)
    problems: list[Problem] = []
    for path in files:
        data = load_json(path)
        missing = REQUIRED_FIELDS.difference(data.keys())
        if missing:
            raise ValueError(f"Problem file {path} missing fields: {sorted(missing)}")

        problem = Problem(
            id=str(data["id"]),
            task_type=str(data["task_type"]),
            language=str(data["language"]),
            prompt=str(data["prompt"]),
            top_module=str(data["top_module"]),
            module_header=str(data.get("module_header", "")),
            testbench=str(data.get("testbench", "")),
            reference_rtl=str(data.get("reference_rtl", "")),
            reference_tb=str(data.get("reference_tb", "")),
            golden_rtl=str(data.get("golden_rtl", "")),
            mutant_rtls=list(data.get("mutant_rtls", [])),
            min_kill_rate=float(data.get("min_kill_rate", 0.5)),
        )
        validate_problem(problem, path)
        problems.append(problem)

    if not problems:
        raise ValueError(f"No benchmark problems found for pattern: {glob_pattern}")

    return problems


def resolve_problem_files(glob_pattern: str) -> list[Path]:
    pattern_path = Path(glob_pattern)
    if pattern_path.is_absolute():
        return [Path(p) for p in sorted(glob.glob(glob_pattern, recursive=True))]
    return sorted(Path(".").glob(glob_pattern))


def validate_problem(problem: Problem, path: Path) -> None:
    if problem.task_type == "rtl":
        if not problem.testbench.strip():
            raise ValueError(f"RTL problem file {path} missing non-empty testbench")
        if not problem.reference_rtl.strip():
            raise ValueError(f"RTL problem file {path} missing non-empty reference_rtl")
        return

    if problem.task_type == "testbench":
        if not problem.golden_rtl.strip():
            raise ValueError(f"Testbench problem file {path} missing non-empty golden_rtl")
        if not problem.reference_tb.strip():
            raise ValueError(f"Testbench problem file {path} missing non-empty reference_tb")
        if not problem.mutant_rtls:
            raise ValueError(f"Testbench problem file {path} missing mutant_rtls")
        return

    raise ValueError(f"Problem file {path} has unsupported task_type: {problem.task_type}")
