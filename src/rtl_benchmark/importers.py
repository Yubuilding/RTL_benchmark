from __future__ import annotations

import re
from pathlib import Path

from rtl_benchmark.utils import ensure_dir, save_json


RTLLM_DESCRIPTION = "design_description.txt"
RTLLM_TESTBENCH = "testbench.v"
RTLLM_REFERENCE_FILES = ("designer_RTL.v", "verified_verilog.v")


def import_rtllm_repo(src_root: str, dest_root: str, overwrite: bool = False) -> list[Path]:
    src_path = Path(src_root).expanduser().resolve()
    if not src_path.exists():
        raise ValueError(f"RTLLM source path does not exist: {src_path}")
    if not src_path.is_dir():
        raise ValueError(f"RTLLM source path is not a directory: {src_path}")

    outputs: list[Path] = []
    for design_dir in _find_rtllm_design_dirs(src_path):
        category = design_dir.parent.name
        design_name = design_dir.name
        prompt = (design_dir / RTLLM_DESCRIPTION).read_text(encoding="utf-8").strip()
        testbench = (design_dir / RTLLM_TESTBENCH).read_text(encoding="utf-8").strip()
        reference_path = _find_reference_rtl_path(design_dir)
        reference_rtl = reference_path.read_text(encoding="utf-8").strip()

        top_module = detect_module_name(reference_rtl) or normalize_slug(design_name)
        module_header = extract_module_header(reference_rtl)
        category_slug = normalize_slug(category)
        problem_id = f"rtllm_{category_slug}_{normalize_slug(design_name)}"
        difficulty = infer_rtllm_difficulty(category_slug)

        payload = {
            "id": problem_id,
            "task_type": "rtl",
            "language": "verilog",
            "top_module": top_module,
            "source": "rtllm",
            "suite": "rtllm",
            "category": category_slug,
            "track": infer_rtllm_track(category_slug),
            "difficulty": difficulty,
            "prompt_style": "spec_to_rtl",
            "harness_type": "testbench_compare",
            "evaluation_targets": ["syntax", "functionality", "synthesis"],
            "exposure": "public",
            "tags": ["rtllm", category_slug, difficulty],
            "module_header": module_header,
            "prompt": prompt,
            "testbench": testbench,
            "reference_rtl": reference_rtl,
        }

        out_dir = ensure_dir(Path(dest_root) / category_slug)
        out_path = out_dir / f"{problem_id}.json"
        if out_path.exists() and not overwrite:
            raise ValueError(f"Refusing to overwrite existing benchmark file: {out_path}")
        save_json(out_path, payload)
        outputs.append(out_path)

    if not outputs:
        raise ValueError(f"No RTLLM design folders found under: {src_path}")
    return outputs


def _find_rtllm_design_dirs(src_root: Path) -> list[Path]:
    matches: list[Path] = []
    for path in sorted(src_root.rglob(RTLLM_DESCRIPTION)):
        design_dir = path.parent
        if not (design_dir / RTLLM_TESTBENCH).exists():
            continue
        if _find_reference_rtl_path(design_dir, required=False) is None:
            continue
        matches.append(design_dir)
    return matches


def _find_reference_rtl_path(design_dir: Path, required: bool = True) -> Path | None:
    for filename in RTLLM_REFERENCE_FILES:
        candidate = design_dir / filename
        if candidate.exists():
            return candidate
    if required:
        raise ValueError(f"RTLLM design folder missing reference RTL file: {design_dir}")
    return None


def detect_module_name(rtl_text: str) -> str:
    match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", rtl_text)
    return match.group(1) if match else ""


def extract_module_header(rtl_text: str) -> str:
    match = re.search(r"(module\b.*?;)", rtl_text, flags=re.DOTALL)
    if not match:
        return ""
    header = match.group(1)
    return re.sub(r"\s+", " ", header).strip()


def infer_rtllm_track(category_slug: str) -> str:
    if category_slug == "control":
        return "control"
    if category_slug == "memory":
        return "memory"
    if category_slug == "arithmetic":
        return "arithmetic"
    return "rtl_core"


def infer_rtllm_difficulty(category_slug: str) -> str:
    if category_slug in {"control", "memory"}:
        return "hard"
    return "medium"


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "unknown"
