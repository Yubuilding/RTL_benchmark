from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Problem:
    id: str
    task_type: str
    language: str
    prompt: str
    top_module: str
    source: str = "local"
    category: str = ""
    suite: str = ""
    track: str = ""
    difficulty: str = ""
    prompt_style: str = ""
    harness_type: str = ""
    evaluation_targets: list[str] = field(default_factory=list)
    exposure: str = ""
    tags: list[str] = field(default_factory=list)
    path: str = ""
    module_header: str = ""
    testbench: str = ""
    reference_rtl: str = ""
    reference_tb: str = ""
    golden_rtl: str = ""
    mutant_rtls: list[str] = field(default_factory=list)
    min_kill_rate: float = 0.5


@dataclass
class ModelDescriptor:
    id: str
    provider: str
    released_at: str = ""
    capability: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageStatus:
    status: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""


@dataclass
class CaseResult:
    model_id: str
    problem_id: str
    task_type: str
    attempt: int
    passed: bool
    lint: StageStatus
    simulation: StageStatus
    synthesis: StageStatus
    mutation_kill_rate: float | None = None
    mutation_results: list[StageStatus] = field(default_factory=list)
    feedback: str = ""
    artifact_dir: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    started_at: str
    finished_at: str
    models: list[dict[str, Any]]
    cases: list[dict[str, Any]]
