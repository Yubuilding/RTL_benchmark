from __future__ import annotations

import copy
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from rtl_benchmark.types import ModelDescriptor, Problem
from rtl_benchmark.utils import extract_hdl_code, now_utc_iso, validate_hdl_candidate


DEFAULT_MAX_TOKENS = 1024
MAX_GENERATION_TOKENS = 8192


def normalize_max_tokens(value: object, default: int = DEFAULT_MAX_TOKENS) -> int:
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(tokens, MAX_GENERATION_TOKENS))


class ModelRunner:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.temperature = float(cfg.get("temperature", 0.0))
        self.max_tokens = normalize_max_tokens(cfg.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.timeout_seconds = int(cfg.get("timeout_seconds", 60))
        self.last_error = ""
        self.last_trace: dict[str, object] = {}
        self._trace_started_monotonic = 0.0

    def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
        self.last_error = ""
        self.last_trace = {}
        if model.provider == "openrouter":
            generated = self._openrouter_generate(model, problem, feedback)
            return self._finalize_generated_output(generated)
        if model.provider == "huggingface":
            generated = self._huggingface_generate(model, problem, feedback)
            return self._finalize_generated_output(generated)
        if model.provider in {"openai", "openai_compatible"}:
            generated = self._openai_generate(model, problem, feedback)
            return self._finalize_generated_output(generated)
        if model.provider == "anthropic":
            generated = self._anthropic_generate(model, problem, feedback)
            return self._finalize_generated_output(generated)
        if model.provider == "gemini":
            generated = self._gemini_generate(model, problem, feedback)
            return self._finalize_generated_output(generated)

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

    def _openrouter_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        key = self._resolve_api_key(model, "OPENROUTER_API_KEY")
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        max_tokens = self._problem_max_tokens(problem)

        payload = {
            "model": model.id,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert RTL engineer. Return only code, no markdown.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        base_url = str(model.raw.get("_base_url", "https://openrouter.ai/api/v1")).rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "rtl-benchmark",
        }
        self._start_trace(
            provider="openrouter",
            model_id=model.id,
            url=url,
            payload=payload,
            headers=headers,
            conversation=self._normalize_openai_messages(payload["messages"]),
        )

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8")
                result = json.loads(raw_text)
            extracted = self._extract_openai_message(result)
            self._finish_trace(raw_text=raw_text, payload=result, assistant_output=extracted, status_code=resp.status)
            return extracted
        except urllib.error.HTTPError as exc:
            body = self._read_http_error_body(exc)
            self._fail_trace(body=body, status_code=exc.code)
            self.last_error = self._describe_http_error(exc, body)
            return ""
        except (urllib.error.URLError, http.client.HTTPException, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
            return ""

    def _huggingface_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        token = self._resolve_api_key(model, "HF_TOKEN")
        if not token:
            return ""

        prompt = self._build_prompt(problem, feedback)
        max_tokens = self._problem_max_tokens(problem)
        encoded_model = urllib.parse.quote(model.id, safe="")
        url = f"https://api-inference.huggingface.co/models/{encoded_model}"

        payload = {
            "inputs": prompt,
            "parameters": {
                "temperature": self.temperature,
                "max_new_tokens": max_tokens,
                "return_full_text": False,
                "do_sample": self.temperature > 0,
            },
            "options": {
                "wait_for_model": True,
                "use_cache": False,
            },
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._start_trace(
            provider="huggingface",
            model_id=model.id,
            url=url,
            payload=payload,
            headers=headers,
            conversation=[{"role": "user", "content": prompt}],
        )

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8")
                result = json.loads(raw_text)
        except urllib.error.HTTPError as exc:
            body = self._read_http_error_body(exc)
            self._fail_trace(body=body, status_code=exc.code)
            self.last_error = self._describe_http_error(exc, body)
            return ""
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
            return ""

        # Expected:
        # - [{"generated_text": "..."}]
        # - {"generated_text": "..."} for some backends
        extracted = ""
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                extracted = str(first.get("generated_text", ""))
        elif isinstance(result, dict):
            extracted = str(result.get("generated_text", ""))
        self._finish_trace(raw_text=raw_text, payload=result, assistant_output=extracted, status_code=resp.status)
        return extracted

    def _openai_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        base_url = str(model.raw.get("_base_url", "https://api.openai.com/v1")).rstrip("/")
        api_key_env = str(model.raw.get("_api_key_env", "OPENAI_API_KEY"))
        key = self._resolve_api_key(model, api_key_env)
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        max_tokens = self._problem_max_tokens(problem)
        payload = {
            "model": model.id,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert RTL engineer. Return only code, no markdown.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        self._start_trace(
            provider=model.provider,
            model_id=model.id,
            url=url,
            payload=payload,
            headers=headers,
            conversation=self._normalize_openai_messages(payload["messages"]),
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8")
                result = json.loads(raw_text)
            extracted = self._extract_openai_message(result)
            self._finish_trace(raw_text=raw_text, payload=result, assistant_output=extracted, status_code=resp.status)
            return extracted
        except urllib.error.HTTPError as exc:
            body = self._read_http_error_body(exc)
            self._fail_trace(body=body, status_code=exc.code)
            self.last_error = self._describe_http_error(exc, body)
            return ""
        except (urllib.error.URLError, http.client.HTTPException, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
            return ""

    def _anthropic_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        base_url = str(model.raw.get("_base_url", "https://api.anthropic.com")).rstrip("/")
        api_key_env = str(model.raw.get("_api_key_env", "ANTHROPIC_API_KEY"))
        api_version = str(model.raw.get("_anthropic_version", "2023-06-01"))
        key = self._resolve_api_key(model, api_key_env)
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        max_tokens = self._problem_max_tokens(problem)
        payload = {
            "model": model.id,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "system": "You are an expert RTL engineer. Return only code, no markdown.",
            "messages": [{"role": "user", "content": prompt}],
        }
        url = f"{base_url}/v1/messages"
        headers = {
            "x-api-key": key,
            "anthropic-version": api_version,
            "content-type": "application/json",
        }
        self._start_trace(
            provider="anthropic",
            model_id=model.id,
            url=url,
            payload=payload,
            headers=headers,
            conversation=self._normalize_anthropic_messages(payload),
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8")
                result = json.loads(raw_text)
            extracted = self._extract_anthropic_message(result)
            self._finish_trace(raw_text=raw_text, payload=result, assistant_output=extracted, status_code=resp.status)
            return extracted
        except urllib.error.HTTPError as exc:
            body = self._read_http_error_body(exc)
            self._fail_trace(body=body, status_code=exc.code)
            self.last_error = self._describe_http_error(exc, body)
            return ""
        except (urllib.error.URLError, http.client.HTTPException, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
            return ""

    def _gemini_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        base_url = str(model.raw.get("_base_url", "https://generativelanguage.googleapis.com/v1beta")).rstrip("/")
        api_key_env = str(model.raw.get("_api_key_env", "GEMINI_API_KEY"))
        key = self._resolve_api_key(model, api_key_env)
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        max_tokens = self._problem_max_tokens(problem)
        model_id = self._normalize_gemini_model_id(model.id)
        payload = {
            "system_instruction": {
                "parts": [{"text": "You are an expert RTL engineer. Return only code, no markdown."}]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "text/plain",
            },
        }
        encoded_model = urllib.parse.quote(model_id, safe="")
        url = f"{base_url}/models/{encoded_model}:generateContent"
        headers = {
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        }
        self._start_trace(
            provider="gemini",
            model_id=model.id,
            url=url,
            payload=payload,
            headers=headers,
            conversation=self._normalize_gemini_messages(payload),
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8")
                result = json.loads(raw_text)
            extracted = self._extract_gemini_message(result)
            self._finish_trace(raw_text=raw_text, payload=result, assistant_output=extracted, status_code=resp.status)
            return extracted
        except urllib.error.HTTPError as exc:
            body = self._read_http_error_body(exc)
            self._fail_trace(body=body, status_code=exc.code)
            self.last_error = self._describe_http_error(exc, body)
            return ""
        except (urllib.error.URLError, http.client.HTTPException, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
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

    def _problem_max_tokens(self, problem: Problem) -> int:
        reference = self._expected_solution_text(problem)
        if not reference:
            return self.max_tokens

        reference_lines = [line for line in reference.splitlines() if line.strip()]
        reference_chars = len(reference)
        prompt_chars = len(problem.prompt or "")
        header_chars = len(problem.module_header or "")

        estimated_tokens = int(round(reference_chars / 3.2))
        structural_margin = len(reference_lines) * 6
        prompt_margin = min(96, prompt_chars // 80)
        header_margin = min(48, header_chars // 12)

        task_floor = 160 if problem.task_type == "rtl" else 256
        task_buffer = 64 if problem.task_type == "rtl" else 128
        if problem.task_type == "testbench":
            task_buffer += min(128, len(problem.mutant_rtls or []) * 16)

        target = estimated_tokens + structural_margin + prompt_margin + header_margin + task_buffer
        target = max(task_floor, target)

        difficulty = str(problem.difficulty or "").strip().lower()
        difficulty_scale = {
            "easy": 1.0,
            "medium": 1.15,
            "hard": 1.35,
            "adhoc": 1.5,
        }.get(difficulty, 1.1)
        adjusted = int(round(target * difficulty_scale))
        return max(1, min(self.max_tokens, adjusted))

    def _expected_solution_text(self, problem: Problem) -> str:
        if problem.task_type == "testbench":
            return str(problem.reference_tb or "").strip()
        return str(problem.reference_rtl or "").strip()

    def _resolve_api_key(self, model: ModelDescriptor, env_name: str) -> str:
        inline_key = str(model.raw.get("_api_key", "")).strip()
        if inline_key:
            return inline_key
        return os.getenv(env_name, "")

    def _finalize_generated_output(self, text: str) -> str:
        if not text.strip():
            if not self.last_error:
                self.last_error = self._diagnose_missing_output()
            if self.last_trace:
                self.last_trace["error"] = self.last_error
            return ""
        extracted = extract_hdl_code(text)
        issue = validate_hdl_candidate(extracted)
        if issue:
            if not self.last_error:
                self.last_error = issue
            if self.last_trace:
                self.last_trace["error"] = self.last_error
            return ""
        return extracted

    def _diagnose_missing_output(self) -> str:
        response = self.last_trace.get("response", {}) if isinstance(self.last_trace, dict) else {}
        payload = response.get("payload", {}) if isinstance(response, dict) else {}
        if isinstance(payload, dict):
            openai_like = self._diagnose_openai_like_missing_output(payload)
            if openai_like:
                return openai_like
            anthropic_like = self._diagnose_anthropic_missing_output(payload)
            if anthropic_like:
                return anthropic_like
            gemini_like = self._diagnose_gemini_missing_output(payload)
            if gemini_like:
                return gemini_like
        return "provider returned no assistant code content"

    def _diagnose_openai_like_missing_output(self, payload: dict[str, object]) -> str:
        choices = payload.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {}) if isinstance(first.get("message", {}), dict) else {}
        finish_reason = str(first.get("finish_reason", "") or "").strip()
        content = message.get("content")
        reasoning = message.get("reasoning")
        reasoning_text = self._message_content_to_text(reasoning)
        if content is None and reasoning_text:
            reason_suffix = f" (finish_reason={finish_reason})" if finish_reason else ""
            return (
                f"provider returned no assistant code content{reason_suffix}; "
                f"response contained reasoning only ({len(reasoning_text)} chars)"
            )
        if content is None and finish_reason == "length":
            return "provider returned no assistant code content (finish_reason=length; output likely truncated before code)"
        if content is None:
            reason_suffix = f" (finish_reason={finish_reason})" if finish_reason else ""
            return f"provider returned no assistant code content{reason_suffix}"
        return ""

    def _diagnose_anthropic_missing_output(self, payload: dict[str, object]) -> str:
        content = payload.get("content", [])
        if not isinstance(content, list):
            return ""
        text_parts = []
        thinking_parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "") or "")
            if part_type == "text":
                text_parts.append(str(part.get("text", "")))
            elif part_type == "thinking":
                thinking_parts.append(str(part.get("thinking", "")))
        text = "\n".join(item for item in text_parts if item).strip()
        thinking = "\n".join(item for item in thinking_parts if item).strip()
        stop_reason = str(payload.get("stop_reason", "") or "").strip()
        if not text and thinking:
            reason_suffix = f" (stop_reason={stop_reason})" if stop_reason else ""
            return (
                f"provider returned no assistant code content{reason_suffix}; "
                f"response contained thinking only ({len(thinking)} chars)"
            )
        return ""

    def _diagnose_gemini_missing_output(self, payload: dict[str, object]) -> str:
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return ""
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        finish_reason = str(first.get("finishReason", "") or "").strip()
        content = first.get("content", {}) if isinstance(first.get("content", {}), dict) else {}
        parts = content.get("parts", []) if isinstance(content.get("parts", []), list) else []
        text = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("text")).strip()
        if not text and finish_reason:
            return f"provider returned no assistant code content (finish_reason={finish_reason})"
        return ""

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

    def _extract_gemini_message(self, payload: dict) -> str:
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return ""
        first = candidates[0]
        content = first.get("content", {})
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            return ""
        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict) and part.get("text"):
                texts.append(str(part.get("text", "")))
        return "\n".join(x for x in texts if x).strip()

    def _normalize_gemini_model_id(self, model_id: str) -> str:
        value = (model_id or "").strip()
        if value.startswith("models/"):
            return value[len("models/") :]
        return value

    def _start_trace(
        self,
        provider: str,
        model_id: str,
        url: str,
        payload: dict[str, object],
        headers: dict[str, str],
        conversation: list[dict[str, str]],
    ) -> None:
        self._trace_started_monotonic = time.monotonic()
        self.last_trace = {
            "provider": provider,
            "model_id": model_id,
            "timing": {
                "started_at": now_utc_iso(),
                "finished_at": "",
                "duration_seconds": 0.0,
            },
            "request": {
                "url": url,
                "headers": self._sanitize_headers(headers),
                "payload": copy.deepcopy(payload),
            },
            "conversation": copy.deepcopy(conversation),
            "response": {},
            "metrics": {},
            "error": "",
        }

    def _finish_trace(
        self,
        raw_text: str,
        payload: object,
        assistant_output: str = "",
        status_code: int | None = None,
    ) -> None:
        if not self.last_trace:
            return
        duration_seconds = self._trace_duration_seconds()
        response = {
            "status_code": status_code,
            "payload": payload,
            "raw_text": raw_text,
        }
        if assistant_output:
            response["assistant_output"] = assistant_output
            conversation = self.last_trace.get("conversation", [])
            if isinstance(conversation, list):
                conversation.append({"role": "assistant", "content": assistant_output})
        self.last_trace["response"] = response
        self.last_trace["timing"] = {
            "started_at": str((self.last_trace.get("timing", {}) or {}).get("started_at", "")),
            "finished_at": now_utc_iso(),
            "duration_seconds": duration_seconds,
        }
        self.last_trace["metrics"] = self._build_trace_metrics(payload, assistant_output, duration_seconds)
        self.last_trace["error"] = ""
        self._trace_started_monotonic = 0.0

    def _fail_trace(self, error: str = "", body: str = "", status_code: int | None = None) -> None:
        if not self.last_trace:
            return
        duration_seconds = self._trace_duration_seconds()
        response_payload: object = {}
        if body.strip():
            try:
                response_payload = json.loads(body)
            except json.JSONDecodeError:
                response_payload = {}
        self.last_trace["response"] = {
            "status_code": status_code,
            "payload": response_payload,
            "raw_text": body,
        }
        self.last_trace["timing"] = {
            "started_at": str((self.last_trace.get("timing", {}) or {}).get("started_at", "")),
            "finished_at": now_utc_iso(),
            "duration_seconds": duration_seconds,
        }
        self.last_trace["metrics"] = self._build_trace_metrics(response_payload, "", duration_seconds)
        self.last_trace["error"] = error
        self._trace_started_monotonic = 0.0

    def _trace_duration_seconds(self) -> float:
        if self._trace_started_monotonic <= 0:
            return 0.0
        return round(max(0.0, time.monotonic() - self._trace_started_monotonic), 3)

    def _build_trace_metrics(self, payload: object, assistant_output: str, duration_seconds: float) -> dict[str, object]:
        metrics: dict[str, object] = {
            "duration_seconds": round(max(0.0, float(duration_seconds or 0.0)), 3),
            "output_chars": len(assistant_output or ""),
        }
        usage = self._extract_response_usage(payload)
        metrics.update(usage)
        completion_tokens = usage.get("completion_tokens")
        token_rate_source = "provider"
        if completion_tokens is None and assistant_output.strip():
            completion_tokens = max(1, round(len(assistant_output) / 4))
            metrics["estimated_completion_tokens"] = completion_tokens
            token_rate_source = "estimated_chars"
        if completion_tokens is not None and duration_seconds > 0:
            metrics["output_tokens_per_second"] = round(float(completion_tokens) / duration_seconds, 2)
            metrics["token_rate_source"] = token_rate_source
        if assistant_output.strip() and duration_seconds > 0:
            metrics["output_chars_per_second"] = round(len(assistant_output) / duration_seconds, 1)
        return metrics

    def _extract_response_usage(self, payload: object) -> dict[str, int]:
        if not isinstance(payload, dict):
            return {}
        usage: dict[str, int] = {}
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            prompt_tokens = self._coerce_usage_int(raw_usage.get("prompt_tokens"))
            completion_tokens = self._coerce_usage_int(raw_usage.get("completion_tokens"))
            total_tokens = self._coerce_usage_int(raw_usage.get("total_tokens"))
            if prompt_tokens is None:
                prompt_tokens = self._coerce_usage_int(raw_usage.get("input_tokens"))
            if completion_tokens is None:
                completion_tokens = self._coerce_usage_int(raw_usage.get("output_tokens"))
            if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
                total_tokens = prompt_tokens + completion_tokens
            if prompt_tokens is not None:
                usage["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                usage["completion_tokens"] = completion_tokens
            if total_tokens is not None:
                usage["total_tokens"] = total_tokens

        usage_metadata = payload.get("usageMetadata")
        if isinstance(usage_metadata, dict):
            prompt_tokens = self._coerce_usage_int(usage_metadata.get("promptTokenCount"))
            completion_tokens = self._coerce_usage_int(usage_metadata.get("candidatesTokenCount"))
            total_tokens = self._coerce_usage_int(usage_metadata.get("totalTokenCount"))
            if prompt_tokens is not None:
                usage["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                usage["completion_tokens"] = completion_tokens
            if total_tokens is not None:
                usage["total_tokens"] = total_tokens
        return usage

    def _coerce_usage_int(self, value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(0, parsed)

    def _sanitize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        redacted: dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in {"authorization", "x-api-key", "x-goog-api-key"}:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
        return redacted

    def _normalize_openai_messages(self, messages: list[dict[str, object]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for message in messages:
            normalized.append(
                {
                    "role": str(message.get("role", "user")),
                    "content": self._message_content_to_text(message.get("content", "")),
                }
            )
        return normalized

    def _normalize_anthropic_messages(self, payload: dict[str, object]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        system_text = self._message_content_to_text(payload.get("system", ""))
        if system_text:
            normalized.append({"role": "system", "content": system_text})
        for message in payload.get("messages", []):
            if isinstance(message, dict):
                normalized.append(
                    {
                        "role": str(message.get("role", "user")),
                        "content": self._message_content_to_text(message.get("content", "")),
                    }
                )
        return normalized

    def _normalize_gemini_messages(self, payload: dict[str, object]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        system_instruction = payload.get("system_instruction", {})
        if isinstance(system_instruction, dict):
            parts = system_instruction.get("parts", [])
            if isinstance(parts, list):
                system_text = "\n".join(
                    self._message_content_to_text(part.get("text", ""))
                    for part in parts
                    if isinstance(part, dict) and part.get("text")
                ).strip()
                if system_text:
                    normalized.append({"role": "system", "content": system_text})
        for item in payload.get("contents", []):
            if not isinstance(item, dict):
                continue
            parts = item.get("parts", [])
            content = ""
            if isinstance(parts, list):
                content = "\n".join(
                    self._message_content_to_text(part.get("text", ""))
                    for part in parts
                    if isinstance(part, dict) and part.get("text")
                ).strip()
            normalized.append({"role": str(item.get("role", "user")), "content": content})
        return normalized

    def _message_content_to_text(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        texts.append(str(item.get("text", "")))
                    elif item.get("text"):
                        texts.append(str(item.get("text", "")))
                elif item is not None:
                    texts.append(str(item))
            return "\n".join(text for text in texts if text).strip()
        if isinstance(content, dict):
            if content.get("text"):
                return str(content.get("text", ""))
            return json.dumps(content, ensure_ascii=False, indent=2)
        return "" if content is None else str(content)

    def _read_http_error_body(self, exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    def _describe_http_error(self, exc: urllib.error.HTTPError, body: str) -> str:
        detail = f": {body}" if body else ""
        return f"HTTP {exc.code} {exc.reason}{detail}"

    def _describe_request_error(self, exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            return self._describe_http_error(exc, self._read_http_error_body(exc))
        if isinstance(exc, urllib.error.URLError):
            return f"network error: {exc.reason}"
        if isinstance(exc, http.client.HTTPException):
            return f"network error: {exc}"
        if isinstance(exc, TimeoutError):
            return "request timed out"
        if isinstance(exc, json.JSONDecodeError):
            return f"invalid JSON response: {exc}"
        return str(exc)
