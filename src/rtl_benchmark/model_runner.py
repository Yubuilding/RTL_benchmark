from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from rtl_benchmark.types import ModelDescriptor, Problem


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

    def generate(self, model: ModelDescriptor, problem: Problem, feedback: str = "") -> str:
        self.last_error = ""
        self.last_trace = {}
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
        if model.provider == "gemini":
            generated = self._gemini_generate(model, problem, feedback)
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
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "rtl-benchmark",
        }
        self._start_trace(
            provider="openrouter",
            model_id=model_id,
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
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
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
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._start_trace(
            provider="huggingface",
            model_id=model_id,
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
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
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
            self._fail_trace(error=self._describe_request_error(exc))
            self.last_error = self._describe_request_error(exc)
            return ""

    def _gemini_generate(self, model: ModelDescriptor, problem: Problem, feedback: str) -> str:
        base_url = str(model.raw.get("_base_url", "https://generativelanguage.googleapis.com/v1beta")).rstrip("/")
        api_key_env = str(model.raw.get("_api_key_env", "GEMINI_API_KEY"))
        key = os.getenv(api_key_env, "")
        if not key:
            return ""

        prompt = self._build_prompt(problem, feedback)
        model_id = self._normalize_gemini_model_id(model.id)
        payload = {
            "system_instruction": {
                "parts": [{"text": "You are an expert RTL engineer. Return only code, no markdown."}]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
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
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as exc:
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
        self.last_trace = {
            "provider": provider,
            "model_id": model_id,
            "request": {
                "url": url,
                "headers": self._sanitize_headers(headers),
                "payload": copy.deepcopy(payload),
            },
            "conversation": copy.deepcopy(conversation),
            "response": {},
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
        self.last_trace["error"] = ""

    def _fail_trace(self, error: str = "", body: str = "", status_code: int | None = None) -> None:
        if not self.last_trace:
            return
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
        self.last_trace["error"] = error

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
        if isinstance(exc, TimeoutError):
            return "request timed out"
        if isinstance(exc, json.JSONDecodeError):
            return f"invalid JSON response: {exc}"
        return str(exc)
