from __future__ import annotations

import glob
from pathlib import Path

from rtl_benchmark.types import Problem
from rtl_benchmark.utils import load_json


REQUIRED_FIELDS = {"id", "task_type", "language", "prompt", "top_module"}


def load_problems(glob_pattern: str, filters: dict | None = None) -> list[Problem]:
    files = resolve_problem_files(glob_pattern)
    problems: list[Problem] = []
    for path in files:
        data = load_json(path)
        missing = REQUIRED_FIELDS.difference(data.keys())
        if missing:
            raise ValueError(f"Problem file {path} missing fields: {sorted(missing)}")

        source, category = infer_problem_taxonomy(path)
        task_type = str(data["task_type"])
        problem = Problem(
            id=str(data["id"]),
            task_type=task_type,
            language=str(data["language"]),
            prompt=str(data["prompt"]),
            top_module=str(data["top_module"]),
            source=str(data.get("source", source)).strip() or source,
            category=str(data.get("category", category)).strip() or category,
            suite=str(data.get("suite", infer_problem_suite(path, source))).strip() or infer_problem_suite(path, source),
            track=str(data.get("track", infer_problem_track(task_type, category))).strip()
            or infer_problem_track(task_type, category),
            difficulty=str(data.get("difficulty", infer_problem_difficulty(path, source, task_type))).strip()
            or infer_problem_difficulty(path, source, task_type),
            prompt_style=str(data.get("prompt_style", infer_prompt_style(task_type))).strip()
            or infer_prompt_style(task_type),
            harness_type=str(data.get("harness_type", infer_harness_type(task_type))).strip()
            or infer_harness_type(task_type),
            evaluation_targets=normalize_list(data.get("evaluation_targets", infer_evaluation_targets(task_type))),
            exposure=str(data.get("exposure", infer_problem_exposure(source))).strip() or infer_problem_exposure(source),
            tags=[str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()],
            path=str(path.resolve()),
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

    problems = filter_problems(problems, filters or {})

    if not problems:
        raise ValueError(f"No benchmark problems found for pattern: {glob_pattern}")

    return problems


def resolve_problem_files(glob_pattern: str) -> list[Path]:
    pattern_path = Path(glob_pattern)
    if pattern_path.is_absolute():
        return [Path(p) for p in sorted(glob.glob(glob_pattern, recursive=True))]
    return sorted(Path(".").glob(glob_pattern))


def infer_problem_taxonomy(path: Path) -> tuple[str, str]:
    parts = list(path.parts)
    if "benchmarks" in parts:
        rel_parts = parts[parts.index("benchmarks") + 1 :]
    else:
        rel_parts = parts[-3:]

    folders = rel_parts[:-1]
    if not folders:
        return "local", "uncategorized"
    if len(folders) == 1:
        return "local", folders[0]
    return folders[0], "/".join(folders[1:])


def infer_problem_suite(path: Path, source: str) -> str:
    if source == "hdlbits":
        return "hdlbits"
    if source == "local":
        top_folder = path.parts[path.parts.index("benchmarks") + 1] if "benchmarks" in path.parts else ""
        if top_folder in {"rtl", "testbench"}:
            return "starter"
    return source or "custom"


def infer_problem_track(task_type: str, category: str) -> str:
    category_lc = category.lower()
    if task_type == "testbench":
        return "verification"
    if "protocol" in category_lc:
        return "protocol"
    if "fsm" in category_lc:
        return "control"
    return "rtl_core"


def infer_problem_difficulty(path: Path, source: str, task_type: str) -> str:
    if task_type == "testbench":
        return "medium"
    if source == "hdlbits":
        category = path.parent.name.lower()
        if category in {"vectors", "arithmetic", "muxes"}:
            return "easy"
        return "medium"
    return "easy"


def infer_prompt_style(task_type: str) -> str:
    if task_type == "testbench":
        return "spec_to_testbench"
    return "spec_to_rtl"


def infer_harness_type(task_type: str) -> str:
    if task_type == "testbench":
        return "mutation"
    return "testbench_compare"


def infer_evaluation_targets(task_type: str) -> list[str]:
    if task_type == "testbench":
        return ["syntax", "functionality", "mutation"]
    return ["syntax", "functionality", "synthesis"]


def infer_problem_exposure(source: str) -> str:
    return "public" if source in {"hdlbits", "chipdev", "verilogeval", "rtllm", "turtle"} else "curated"


def normalize_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def filter_problems(problems: list[Problem], filters: dict) -> list[Problem]:
    if not filters:
        return problems

    ids = _normalize_filter_values(filters.get("ids"))
    task_types = _normalize_filter_values(filters.get("task_types"))
    sources = _normalize_filter_values(filters.get("sources"))
    suites = _normalize_filter_values(filters.get("suites"))
    tracks = _normalize_filter_values(filters.get("tracks"))
    categories = _normalize_filter_values(filters.get("categories"))
    difficulties = _normalize_filter_values(filters.get("difficulties"))
    exposure = _normalize_filter_values(filters.get("exposure"))
    tags_any = _normalize_filter_values(filters.get("tags_any"))
    tags_all = _normalize_filter_values(filters.get("tags_all"))

    selected: list[Problem] = []
    for problem in problems:
        problem_tags = {tag.casefold() for tag in problem.tags}
        if ids and problem.id.casefold() not in ids:
            continue
        if task_types and problem.task_type.casefold() not in task_types:
            continue
        if sources and problem.source.casefold() not in sources:
            continue
        if suites and problem.suite.casefold() not in suites:
            continue
        if tracks and problem.track.casefold() not in tracks:
            continue
        if categories and problem.category.casefold() not in categories:
            continue
        if difficulties and problem.difficulty.casefold() not in difficulties:
            continue
        if exposure and problem.exposure.casefold() not in exposure:
            continue
        if tags_any and problem_tags.isdisjoint(tags_any):
            continue
        if tags_all and not tags_all.issubset(problem_tags):
            continue
        selected.append(problem)
    return selected


def _normalize_filter_values(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip().casefold() for value in values if str(value).strip()}


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
