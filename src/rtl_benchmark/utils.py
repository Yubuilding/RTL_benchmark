from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_CANDIDATE_CHARS = 200_000
FENCED_CODE_RE = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
MODULE_BLOCK_RE = re.compile(r"\bmodule\b.*?\bendmodule\b", re.DOTALL)
MODULE_DECL_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b")
ENDMODULE_RE = re.compile(r"\bendmodule\b")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return {} if default is None else default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    temp = Path(temp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(p)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def tool_exists(tool: str) -> bool:
    return shutil.which(tool) is not None


def extract_hdl_code(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""

    fence_matches = [match.strip() for match in FENCED_CODE_RE.findall(stripped) if match.strip()]
    if fence_matches:
        module_fences = [match for match in fence_matches if MODULE_DECL_RE.search(match)]
        selected = module_fences or fence_matches
        return "\n\n".join(selected).strip()

    module_blocks = [match.strip() for match in MODULE_BLOCK_RE.findall(stripped) if match.strip()]
    if module_blocks:
        return "\n\n".join(module_blocks).strip()

    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()

    return stripped


def validate_hdl_candidate(candidate_code: str, max_chars: int = MAX_CANDIDATE_CHARS) -> str:
    stripped = (candidate_code or "").strip()
    if not stripped:
        return "candidate is empty"
    if len(stripped) > max_chars:
        return f"candidate is too large ({len(stripped)} chars > {max_chars})"
    if "```" in stripped:
        return "candidate still contains markdown fences"

    module_count = len(MODULE_DECL_RE.findall(stripped))
    endmodule_count = len(ENDMODULE_RE.findall(stripped))
    if module_count == 0:
        return "candidate does not contain a Verilog module declaration"
    if endmodule_count == 0:
        return "candidate is missing endmodule (possible truncated output)"
    if endmodule_count < module_count:
        return "candidate has unclosed module declarations (possible truncated output)"
    return ""
