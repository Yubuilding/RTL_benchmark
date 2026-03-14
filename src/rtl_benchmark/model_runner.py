from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from rtl_benchmark.types import ModelDescriptor, Problem


class ModelRunner:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.temperature = float(cfg.get("temperature", 0.0))
        self.max_tokens = int(cfg.get("max_tokens", 1024))
        self.timeout_seconds = int(cfg.get("timeout_seconds", 60))

    def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
        if model.provider == "openrouter":
            generated = self._openrouter_generate(model.id, problem, feedback)
            if generated:
                return self._strip_markdown_fence(generated)
            return ""
        if model.provider == "huggingface":
            generated = self._huggingface_generate(model.id, problem, feedback)
            if generated:
                return self._strip_markdown_fence(generated)
            return ""
        if model.provider in {"openai", "openai_compatible"}:
            generated = self._openai_generate(model, problem, feedback)
            if generated:
                return self._strip_markdown_fence(generated)
            return ""
        if model.provider == "anthropic":
            generated = self._anthropic_generate(model, problem, feedback)
            if generated:
                return self._strip_markdown_fence(generated)
            return ""

        return self._mock_generate(model, problem)

    def _mock_generate(self, model: ModelDescriptor, problem: Problem) -> str:
        strong = model.capability.lower() in {"strong", "reference", "gold"}

        if strong:
            if problem.task_type == "rtl":
                return problem.reference_rtl
            if problem.task_type == "testbench":
                return problem.reference_tb

        if problem.task_type == "rtl":
            return (
                f"module {problem.top_module}();\n"
                "  // intentionally weak baseline output\n"
                "endmodule\n"
            )

        return (
            "module tb;\n"
            "  initial begin\n"
            "    $display(\"weak tb\");\n"
            "    $finish;\n"
            "  end\n"
            "endmodule\n"
        )

    def _openrouter_generate(self, model_id: str, problem: Problem, feedback: str) -> str:
        key = os.getenv("OPENROUTER_API_KEY", "")
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)

        payload = {
            "model": model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert RTL engineer. Return only code, no markdown.",
                },
                {"role": "user", "content": prompt},
            ],
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost",
                "X-Title": "rtl-benchmark",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return self._extract_openai_message(result)
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError):
            return ""

    def _huggingface_generate(self, model_id: str, problem: Problem, feedback: str) -> str:
        token = os.getenv("HF_TOKEN", "")
        if not token:
            return ""

        prompt = self._build_prompt(problem, feedback)
        encoded_model = urllib.parse.quote(model_id, safe="")
        url = f"https://api-inference.huggingface.co/models/{encoded_model}"

        payload = {
            "inputs": prompt,
            "parameters": {
                "temperature": self.temperature,
                "max_new_tokens": self.max_tokens,
                "return_full_text": False,
                "do_sample": self.temperature > 0,
            },
            "options": {
                "wait_for_model": True,
                "use_cache": False,
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return ""

        # Expected:
        # - [{"generated_text": "..."}]
        # - {"generated_text": "..."} for some backends
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                return str(first.get("generated_text", ""))
        if isinstance(result, dict):
            return str(result.get("generated_text", ""))
        return ""

    def _openai_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        base_url = str(model.raw.get("_base_url", "https://api.openai.com/v1")).rstrip("/")
        api_key_env = str(model.raw.get("_api_key_env", "OPENAI_API_KEY"))
        key = os.getenv(api_key_env, "")
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        payload = {
            "model": model.id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert RTL engineer. Return only code, no markdown.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return self._extract_openai_message(result)
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError):
            return ""

    def _anthropic_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        base_url = str(model.raw.get("_base_url", "https://api.anthropic.com")).rstrip("/")
        api_key_env = str(model.raw.get("_api_key_env", "ANTHROPIC_API_KEY"))
        api_version = str(model.raw.get("_anthropic_version", "2023-06-01"))
        key = os.getenv(api_key_env, "")
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        payload = {
            "model": model.id,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": "You are an expert RTL engineer. Return only code, no markdown.",
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            f"{base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": key,
                "anthropic-version": api_version,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return self._extract_anthropic_message(result)
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError):
            return ""

    def _build_prompt(self, problem: Problem, feedback: str) -> str:
        base = (
            f"Task type: {problem.task_type}\n"
            f"Language: {problem.language}\n"
            f"Top module: {problem.top_module}\n"
            f"Module header:\n{problem.module_header}\n\n"
            f"Requirement:\n{problem.prompt}\n"
            "Output constraints:\n"
            "- Return only valid HDL code.\n"
            "- Do not include markdown fences.\n"
            "- Do not add explanation text.\n"
        )
        if problem.task_type == "testbench":
            base += f"DUT module:\n{problem.golden_rtl}\n"

        if feedback:
            base += f"\nPrevious attempt feedback:\n{feedback}\n"

        return base

    def _strip_markdown_fence(self, text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped

        lines = stripped.splitlines()
        if not lines:
            return stripped

        start = 1
        if lines and lines[0].startswith("```"):
            start = 1

        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1

        return "\n".join(lines[start:end]).strip()

    def _extract_openai_message(self, payload: dict) -> str:
        choices = payload.get("choices", [])
        if not choices:
            return ""
        first = choices[0]
        message = first.get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text", "")))
            return "\n".join(x for x in texts if x).strip()
        return ""

    def _extract_anthropic_message(self, payload: dict) -> str:
        parts = payload.get("content", [])
        if not isinstance(parts, list):
            return ""
        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text", "")))
        return "\n".join(x for x in texts if x).strip()
