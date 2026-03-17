from __future__ import annotations

import re
from pathlib import Path

from rtl_benchmark.utils import ensure_dir, save_json


RTLLM_DESCRIPTION = "design_description.txt"
RTLLM_TESTBENCH = "testbench.v"
RTLLM_REFERENCE_FILES = ("designer_RTL.v", "verified_verilog.v")
VERILOGEVAL_TASK_DIR = "dataset_spec-to-rtl"
VERILOGEVAL_PROMPT_SUFFIX = "_prompt.txt"


def import_rtllm_repo(src_root: str, dest_root: str, overwrite: bool = False) -> list[Path]:
    src_path = Path(src_root).expanduser().resolve()
    if not src_path.exists():
        raise ValueError(f"RTLLM source path does not exist: {src_path}")
    if not src_path.is_dir():
        raise ValueError(f"RTLLM source path is not a directory: {src_path}")

    outputs: list[Path] = []
    for design_dir in _find_rtllm_design_dirs(src_path):
        category, subcategory = infer_rtllm_taxonomy(design_dir, src_path)
        design_name = design_dir.name
        prompt = (design_dir / RTLLM_DESCRIPTION).read_text(encoding="utf-8").strip()
        testbench = (design_dir / RTLLM_TESTBENCH).read_text(encoding="utf-8").strip()
        reference_path = _find_reference_rtl_path(design_dir)
        reference_rtl_raw = reference_path.read_text(encoding="utf-8").strip()

        top_module = detect_prompt_module_name(prompt) or detect_dut_module_name(testbench) or detect_module_name(reference_rtl_raw) or normalize_slug(design_name)
        reference_rtl = rename_module_to(reference_rtl_raw, top_module, preferred_names=[f"verified_{top_module}", top_module])
        module_header = extract_module_header(reference_rtl, module_name=top_module)
        category_slug = normalize_slug(category)
        subcategory_slug = normalize_slug(subcategory)
        problem_id = f"rtllm_{category_slug}_{normalize_slug(design_name)}"
        category_path = category_slug if not subcategory else f"{category_slug}/{subcategory_slug}"
        difficulty = infer_rtllm_difficulty(category_path)

        payload = {
            "id": problem_id,
            "task_type": "rtl",
            "language": "verilog",
            "top_module": top_module,
            "source": "rtllm",
            "suite": "rtllm",
            "category": category_path,
            "track": infer_rtllm_track(category_path),
            "difficulty": difficulty,
            "prompt_style": "spec_to_rtl",
            "harness_type": "testbench_compare",
            "evaluation_targets": ["syntax", "functionality", "synthesis"],
            "exposure": "public",
            "tags": _dedupe_tags(["rtllm", category_slug, subcategory_slug, difficulty]),
            "module_header": module_header,
            "prompt": prompt,
            "testbench": testbench,
            "reference_rtl": reference_rtl,
        }

        out_dir = ensure_dir(Path(dest_root) / category_slug / (subcategory_slug if subcategory else ""))
        out_path = out_dir / f"{problem_id}.json"
        if out_path.exists() and not overwrite:
            raise ValueError(f"Refusing to overwrite existing benchmark file: {out_path}")
        save_json(out_path, payload)
        outputs.append(out_path)

    if not outputs:
        raise ValueError(f"No RTLLM design folders found under: {src_path}")
    return outputs


def import_verilogeval_repo(src_root: str, dest_root: str, overwrite: bool = False) -> list[Path]:
    src_path = Path(src_root).expanduser().resolve()
    if not src_path.exists():
        raise ValueError(f"VerilogEval source path does not exist: {src_path}")
    if not src_path.is_dir():
        raise ValueError(f"VerilogEval source path is not a directory: {src_path}")

    dataset_dir = src_path / VERILOGEVAL_TASK_DIR
    if not dataset_dir.exists():
        raise ValueError(f"VerilogEval dataset directory not found: {dataset_dir}")

    outputs: list[Path] = []
    for prompt_path in sorted(dataset_dir.glob(f"*{VERILOGEVAL_PROMPT_SUFFIX}")):
        stem = prompt_path.name[: -len(VERILOGEVAL_PROMPT_SUFFIX)]
        ref_path = dataset_dir / f"{stem}_ref.sv"
        test_path = dataset_dir / f"{stem}_test.sv"
        if not ref_path.exists() or not test_path.exists():
            continue

        prompt = prompt_path.read_text(encoding="utf-8").strip()
        testbench = test_path.read_text(encoding="utf-8").strip()
        reference_rtl_raw = ref_path.read_text(encoding="utf-8").strip()
        problem_slug = normalize_slug(strip_problem_prefix(stem))
        top_module = detect_prompt_module_name(prompt) or detect_dut_module_name(testbench) or "TopModule"
        reference_rtl = reference_rtl_raw
        module_header = extract_module_header(
            rename_module_to(reference_rtl_raw, top_module),
            module_name=top_module,
        )
        category = infer_verilogeval_category(problem_slug)
        difficulty = infer_verilogeval_difficulty(problem_slug, category)
        track = infer_verilogeval_track(problem_slug, category)
        problem_num = extract_problem_number(stem)
        problem_id = f"verilogeval_{problem_num}_{problem_slug}" if problem_num else f"verilogeval_{problem_slug}"

        payload = {
            "id": problem_id,
            "task_type": "rtl",
            "language": "systemverilog",
            "top_module": top_module,
            "source": "verilogeval",
            "suite": "verilogeval",
            "category": category,
            "track": track,
            "difficulty": difficulty,
            "prompt_style": "spec_to_rtl",
            "harness_type": "testbench_compare",
            "evaluation_targets": ["syntax", "functionality", "synthesis"],
            "exposure": "public",
            "tags": build_verilogeval_tags(problem_slug, category, difficulty),
            "module_header": module_header,
            "prompt": prompt,
            "testbench": testbench,
            "reference_rtl": reference_rtl,
        }

        out_dir = ensure_dir(Path(dest_root) / category)
        out_path = out_dir / f"{problem_id}.json"
        if out_path.exists() and not overwrite:
            raise ValueError(f"Refusing to overwrite existing benchmark file: {out_path}")
        save_json(out_path, payload)
        outputs.append(out_path)

    if not outputs:
        raise ValueError(f"No VerilogEval problems found under: {dataset_dir}")
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
    for pattern in ("verified_*.v", "verified*.sv", "designer_*.v", "designer*.sv", "*.ref.v", "*reference*.v"):
        matches = sorted(design_dir.glob(pattern))
        if matches:
            return matches[0]
    if required:
        raise ValueError(f"RTLLM design folder missing reference RTL file: {design_dir}")
    return None


def detect_module_name(rtl_text: str) -> str:
    match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", rtl_text)
    return match.group(1) if match else ""


def extract_module_header(rtl_text: str, module_name: str = "") -> str:
    if module_name.strip():
        match = re.search(
            rf"(module\s+{re.escape(module_name)}\b.*?;)",
            rtl_text,
            flags=re.DOTALL,
        )
        if match:
            header = match.group(1)
            return re.sub(r"\s+", " ", header).strip()
    match = re.search(r"(module\b.*?;)", rtl_text, flags=re.DOTALL)
    if not match:
        return ""
    header = match.group(1)
    return re.sub(r"\s+", " ", header).strip()


def rename_module_to(rtl_text: str, module_name: str, preferred_names: list[str] | None = None) -> str:
    if not module_name.strip():
        return rtl_text
    if re.search(rf"\bmodule\s+{re.escape(module_name)}\b", rtl_text):
        return rtl_text
    for preferred in preferred_names or []:
        if not preferred.strip():
            continue
        updated, count = re.subn(
            rf"(\bmodule\s+){re.escape(preferred)}\b",
            rf"\1{module_name}",
            rtl_text,
            count=1,
        )
        if count:
            return updated
    return re.sub(
        r"(\bmodule\s+)([A-Za-z_][A-Za-z0-9_$]*)",
        rf"\1{module_name}",
        rtl_text,
        count=1,
    )


def infer_rtllm_taxonomy(design_dir: Path, src_root: Path) -> tuple[str, str]:
    rel_parts = design_dir.relative_to(src_root).parts
    if len(rel_parts) >= 3:
        return rel_parts[0], rel_parts[1]
    if len(rel_parts) >= 2:
        return rel_parts[0], ""
    return design_dir.parent.name, ""


def infer_rtllm_track(category_slug: str) -> str:
    root = category_slug.split("/", 1)[0]
    if root == "control":
        return "control"
    if root == "memory":
        return "memory"
    if root == "arithmetic":
        return "arithmetic"
    return "rtl_core"


def infer_rtllm_difficulty(category_slug: str) -> str:
    root = category_slug.split("/", 1)[0]
    if root in {"control", "memory"}:
        return "hard"
    if "divider" in category_slug or "multiplier" in category_slug or "risc_v" in category_slug:
        return "hard"
    return "medium"


def strip_problem_prefix(value: str) -> str:
    return re.sub(r"^Prob\d+_", "", value)


def extract_problem_number(value: str) -> str:
    match = re.match(r"Prob(\d+)_", value)
    return match.group(1) if match else ""


def detect_prompt_module_name(prompt_text: str) -> str:
    patterns = (
        r"\bmodule named\s+([A-Za-z_][A-Za-z0-9_$]*)\b",
        r"\bModule name:\s*([A-Za-z_][A-Za-z0-9_$]*)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt_text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def detect_dut_module_name(testbench_text: str) -> str:
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\(", testbench_text):
        module_name = match.group(1)
        instance_name = match.group(2).lower()
        if module_name in {"module", "if", "for", "while", "case", "task", "function", "RefModule"}:
            continue
        if module_name.lower() in {"tb", "testbench", "stimulus_gen"}:
            continue
        if instance_name.startswith(("top_module", "dut", "uut")):
            return module_name
    return ""


def infer_verilogeval_category(problem_slug: str) -> str:
    tokens = slug_tokens(problem_slug)
    if any(token.startswith(("add", "sub", "popcount")) for token in tokens) or any(
        token in {"reduction", "eq2", "hadd", "fadd", "addsubz"} for token in tokens
    ):
        return "arithmetic"
    if "fsm" in problem_slug or any(
        keyword in problem_slug for keyword in ("lemmings", "ps2", "gshare", "rule90", "rule110", "hdlc", "serial", "conwaylife")
    ):
        return "control"
    if any(keyword in problem_slug for keyword in ("count", "timer", "lfsr", "shift", "dff", "edge", "rotate", "history", "ringer", "thermostat")):
        return "sequential"
    return "combinational"


def infer_verilogeval_track(problem_slug: str, category: str) -> str:
    if category == "control":
        return "control"
    if category == "arithmetic":
        return "arithmetic"
    if any(keyword in problem_slug for keyword in ("lfsr", "dff", "shift", "count", "timer")):
        return "memory"
    return "rtl_core"


def infer_verilogeval_difficulty(problem_slug: str, category: str) -> str:
    tokens = slug_tokens(problem_slug)
    hard_keywords = (
        "conwaylife",
        "gshare",
        "fancytimer",
        "count_clock",
        "hdlc",
        "ps2data",
        "ps2",
        "lemmings",
        "fsm_serial",
        "rule110",
        "timer",
        "lfsr32",
        "popcount255",
        "rotate100",
        "counter_2bc",
    )
    easy_keywords = {
        "zero",
        "wire",
        "wire_decl",
        "vector",
        "notgate",
        "norgate",
        "xnorgate",
        "andgate",
        "mux2to1",
        "conditional",
        "7420",
    }
    if any(keyword in problem_slug for keyword in hard_keywords):
        return "hard"
    if category in {"control", "sequential", "arithmetic"}:
        return "medium"
    if any(token in easy_keywords for token in tokens):
        return "easy"
    return "medium"


def build_verilogeval_tags(problem_slug: str, category: str, difficulty: str) -> list[str]:
    tokens = [token for token in problem_slug.split("_") if token and not token.isdigit()]
    return _dedupe_tags(["verilogeval", category, difficulty, *tokens[:4]])


def _dedupe_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def slug_tokens(problem_slug: str) -> list[str]:
    return [token for token in problem_slug.split("_") if token]


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "unknown"
