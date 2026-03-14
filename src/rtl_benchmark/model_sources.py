from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from rtl_benchmark.types import ModelDescriptor
from rtl_benchmark.utils import load_json, save_json


def _fetch_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return load_json_bytes(resp.read())


def load_json_bytes(raw: bytes) -> dict:
    import json

    return json.loads(raw.decode("utf-8"))


def from_file_feed(path: str) -> list[ModelDescriptor]:
    items = load_json(path, default=[])
    models: list[ModelDescriptor] = []
    for item in items:
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue

        models.append(
            ModelDescriptor(
                id=model_id,
                provider=str(item.get("provider", "file_feed")),
                released_at=str(item.get("released_at", "")),
                capability=str(item.get("capability", classify_capability(model_id))),
                raw=item,
            )
        )
    return models


def from_static_models(
    models_data: list[dict],
    provider: str,
    base_url: str = "",
    api_key_env: str = "",
    anthropic_version: str = "",
) -> list[ModelDescriptor]:
    models: list[ModelDescriptor] = []
    for item in models_data:
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue

        raw = dict(item)
        raw["_selection_mode"] = "pinned"
        if base_url:
            raw["_base_url"] = base_url.rstrip("/")
        if api_key_env:
            raw["_api_key_env"] = api_key_env
        if anthropic_version:
            raw["_anthropic_version"] = anthropic_version

        models.append(
            ModelDescriptor(
                id=model_id,
                provider=provider,
                released_at=str(item.get("released_at", item.get("created", item.get("created_at", "")))),
                capability=str(item.get("capability", classify_capability(model_id))),
                raw=raw,
            )
        )

    return models


def from_huggingface(
    limit: int = 50,
    window_hours: int = 24,
    query: str = "text-generation",
    id_contains: list[str] | None = None,
) -> list[ModelDescriptor]:
    token = os.getenv("HF_TOKEN", "")
    params = urllib.parse.urlencode(
        {
            "sort": "lastModified",
            "direction": "-1",
            "limit": str(limit),
            "full": "true",
            "search": query,
        }
    )
    url = f"https://huggingface.co/api/models?{params}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        payload = _fetch_json(url, headers=headers)
    except Exception:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    models: list[ModelDescriptor] = []

    for item in payload:
        last_modified = str(item.get("lastModified", ""))
        if not _is_recent(last_modified, cutoff):
            continue

        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        if not _matches_keywords(model_id, id_contains):
            continue

        models.append(
            ModelDescriptor(
                id=model_id,
                provider="huggingface",
                released_at=last_modified,
                capability=classify_capability(model_id),
                raw=item,
            )
        )

    return models


def from_openrouter(window_hours: int = 24, id_contains: list[str] | None = None) -> list[ModelDescriptor]:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        return []

    headers = {"Authorization": f"Bearer {key}", "HTTP-Referer": "https://localhost", "X-Title": "rtl-benchmark"}
    url = "https://openrouter.ai/api/v1/models"
    try:
        payload = _fetch_json(url, headers=headers)
    except urllib.error.URLError:
        return []
    except Exception:
        return []

    models: list[ModelDescriptor] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    for item in payload.get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        if not _matches_keywords(model_id, id_contains):
            continue

        created = str(item.get("created", ""))
        if not _is_recent(created, cutoff):
            continue

        models.append(
            ModelDescriptor(
                id=model_id,
                provider="openrouter",
                released_at=created,
                capability=classify_capability(model_id),
                raw=item,
            )
        )

    return models


def from_openai(
    base_url: str = "https://api.openai.com/v1",
    api_key_env: str = "OPENAI_API_KEY",
    provider: str = "openai",
    window_hours: int = 24,
    id_contains: list[str] | None = None,
) -> list[ModelDescriptor]:
    key = os.getenv(api_key_env, "")
    if not key:
        return []

    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        payload = _fetch_json(url, headers=headers)
    except Exception:
        return []

    models: list[ModelDescriptor] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    for item in payload.get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        if not _matches_keywords(model_id, id_contains):
            continue

        created = str(item.get("created", ""))
        if created and not _is_recent(created, cutoff):
            continue

        raw = dict(item)
        raw["_base_url"] = base_url.rstrip("/")
        raw["_api_key_env"] = api_key_env
        models.append(
            ModelDescriptor(
                id=model_id,
                provider=provider,
                released_at=created,
                capability=classify_capability(model_id),
                raw=raw,
            )
        )

    return models


def from_anthropic(
    base_url: str = "https://api.anthropic.com",
    api_key_env: str = "ANTHROPIC_API_KEY",
    provider: str = "anthropic",
    window_hours: int = 24,
    id_contains: list[str] | None = None,
    version: str = "2023-06-01",
) -> list[ModelDescriptor]:
    key = os.getenv(api_key_env, "")
    if not key:
        return []

    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {
        "x-api-key": key,
        "anthropic-version": version,
        "content-type": "application/json",
    }
    try:
        payload = _fetch_json(url, headers=headers)
    except Exception:
        return []

    models: list[ModelDescriptor] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    for item in payload.get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        if not _matches_keywords(model_id, id_contains):
            continue

        created = str(item.get("created_at", item.get("created", "")))
        if created and not _is_recent(created, cutoff):
            continue

        raw = dict(item)
        raw["_base_url"] = base_url.rstrip("/")
        raw["_api_key_env"] = api_key_env
        raw["_anthropic_version"] = version
        models.append(
            ModelDescriptor(
                id=model_id,
                provider=provider,
                released_at=created,
                capability=classify_capability(model_id),
                raw=raw,
            )
        )

    return models


def from_gemini(
    base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    api_key_env: str = "GEMINI_API_KEY",
    provider: str = "gemini",
    page_size: int = 100,
    id_contains: list[str] | None = None,
) -> list[ModelDescriptor]:
    key = os.getenv(api_key_env, "")
    if not key:
        return []

    params = urllib.parse.urlencode({"pageSize": str(page_size)})
    url = f"{base_url.rstrip('/')}/models?{params}"
    headers = {"x-goog-api-key": key}
    try:
        payload = _fetch_json(url, headers=headers)
    except Exception:
        return []

    models: list[ModelDescriptor] = []
    for item in payload.get("models", []):
        model_id = _normalize_gemini_model_id(str(item.get("name", "")))
        if not model_id:
            continue
        if not _matches_keywords(model_id, id_contains):
            continue

        methods = item.get("supportedGenerationMethods", [])
        if methods and "generateContent" not in methods:
            continue

        raw = dict(item)
        raw["_base_url"] = base_url.rstrip("/")
        raw["_api_key_env"] = api_key_env
        models.append(
            ModelDescriptor(
                id=model_id,
                provider=provider,
                released_at="",
                capability=classify_capability(model_id),
                raw=raw,
            )
        )

    return models


def discover_models(
    sources: list[dict],
    state_path: str,
    include_known: bool = False,
    selection: dict | None = None,
    update_state: bool = True,
) -> list[ModelDescriptor]:
    state = load_json(state_path, default={"known_model_ids": []})
    known = set(state.get("known_model_ids", []))

    discovered: list[ModelDescriptor] = []

    for source in sources:
        src_type = source.get("type")
        enabled = source.get("enabled", True)
        if not enabled:
            continue

        if src_type == "file_feed":
            discovered.extend(from_file_feed(source["path"]))
        elif "models" in source:
            discovered.extend(
                from_static_models(
                    models_data=list(source.get("models", [])),
                    provider=str(source.get("provider", src_type)),
                    base_url=str(source.get("base_url", "")),
                    api_key_env=str(source.get("api_key_env", "")),
                    anthropic_version=str(source.get("version", "")),
                )
            )
        elif src_type == "huggingface":
            discovered.extend(
                from_huggingface(
                    limit=int(source.get("limit", 50)),
                    window_hours=int(source.get("window_hours", 24)),
                    query=str(source.get("query", "text-generation")),
                    id_contains=list(source.get("id_contains", [])),
                )
            )
        elif src_type == "openrouter":
            discovered.extend(
                from_openrouter(
                    window_hours=int(source.get("window_hours", 24)),
                    id_contains=list(source.get("id_contains", [])),
                )
            )
        elif src_type == "openai":
            discovered.extend(
                from_openai(
                    base_url=str(source.get("base_url", "https://api.openai.com/v1")),
                    api_key_env=str(source.get("api_key_env", "OPENAI_API_KEY")),
                    provider=str(source.get("provider", "openai")),
                    window_hours=int(source.get("window_hours", 24)),
                    id_contains=list(source.get("id_contains", [])),
                )
            )
        elif src_type == "anthropic":
            discovered.extend(
                from_anthropic(
                    base_url=str(source.get("base_url", "https://api.anthropic.com")),
                    api_key_env=str(source.get("api_key_env", "ANTHROPIC_API_KEY")),
                    provider=str(source.get("provider", "anthropic")),
                    window_hours=int(source.get("window_hours", 24)),
                    id_contains=list(source.get("id_contains", [])),
                    version=str(source.get("version", "2023-06-01")),
                )
            )
        elif src_type == "gemini":
            discovered.extend(
                from_gemini(
                    base_url=str(source.get("base_url", "https://generativelanguage.googleapis.com/v1beta")),
                    api_key_env=str(source.get("api_key_env", "GEMINI_API_KEY")),
                    provider=str(source.get("provider", "gemini")),
                    page_size=int(source.get("page_size", 100)),
                    id_contains=list(source.get("id_contains", [])),
                )
            )

    dedup: dict[str, ModelDescriptor] = {}
    for model in discovered:
        existing = dedup.get(model.id)
        if existing and _is_pinned_model(existing) and not _is_pinned_model(model):
            continue
        dedup[model.id] = model
    ordered = list(dedup.values())
    ordered = apply_selection_filters(ordered, selection or {})
    ordered.sort(key=sort_key_release_time, reverse=True)

    if include_known:
        selected = ordered
    else:
        selected = [m for m in ordered if _is_pinned_model(m) or m.id not in known]

    max_models = int((selection or {}).get("max_models", 0))
    if max_models > 0:
        selected = selected[:max_models]

    if update_state:
        known.update(m.id for m in ordered if not _is_pinned_model(m))
        save_json(state_path, {"known_model_ids": sorted(known)})

    return selected


def classify_capability(model_id: str) -> str:
    low = model_id.lower()
    hard_keywords = ("rtl", "verilog", "systemverilog", "hdl", "hardware")
    if any(k in low for k in hard_keywords):
        return "strong"
    return "unknown"


def apply_selection_filters(models: list[ModelDescriptor], selection: dict) -> list[ModelDescriptor]:
    include_any = [x.lower() for x in selection.get("include_any", []) if x]
    exclude_any = [x.lower() for x in selection.get("exclude_any", []) if x]
    providers = {x.lower() for x in selection.get("providers", []) if x}

    filtered: list[ModelDescriptor] = []
    for model in models:
        model_id_low = model.id.lower()
        provider_low = model.provider.lower()

        if providers and provider_low not in providers:
            continue
        if include_any and not any(token in model_id_low for token in include_any):
            continue
        if exclude_any and any(token in model_id_low for token in exclude_any):
            continue
        filtered.append(model)

    return filtered


def sort_key_release_time(model: ModelDescriptor) -> float:
    return release_to_timestamp(model.released_at)


def release_to_timestamp(text: str) -> float:
    value = (text or "").strip()
    if not value:
        return 0.0

    try:
        return float(value)
    except ValueError:
        pass

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return 0.0


def _is_recent(released_at: str, cutoff: datetime) -> bool:
    if not released_at:
        return True
    ts = release_to_timestamp(released_at)
    if ts <= 0:
        return True
    return ts >= cutoff.timestamp()


def _matches_keywords(model_id: str, id_contains: list[str] | None) -> bool:
    keys = [x.lower() for x in (id_contains or []) if x]
    if not keys:
        return True
    value = model_id.lower()
    return any(k in value for k in keys)


def _is_pinned_model(model: ModelDescriptor) -> bool:
    return bool(model.raw.get("_selection_mode") == "pinned")


def _normalize_gemini_model_id(model_name: str) -> str:
    value = (model_name or "").strip()
    if value.startswith("models/"):
        return value[len("models/") :]
    return value
