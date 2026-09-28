#!/usr/bin/env python3
"""Trusted-provider BYOK support shared by dk and the local dashboard.

The module deliberately has no third-party dependencies so the installer can
place it next to ``dk``/``serve.py`` on every supported platform.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 12
REASONING_EFFORTS = {
    "default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max"
}

MODEL_REASONING_PROFILES: dict[str, tuple[list[str], str]] = {
    "gpt-5-2025-08-07": (["low", "medium", "high"], "medium"),
    "gpt-5-mini-2025-08-07": (["low", "medium", "high"], "medium"),
    "gpt-5-nano-2025-08-07": (["low", "medium", "high"], "medium"),
    "gpt-5-codex": (["low", "medium", "high"], "medium"),
    "gpt-5.1": (["none", "low", "medium", "high"], "none"),
    "gpt-5.1-codex": (["low", "medium", "high"], "medium"),
    "gpt-5.1-codex-max": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.2": (["off", "low", "medium", "high", "xhigh"], "low"),
    "gpt-5.2-codex": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.3-codex": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.3-codex-fast": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.4": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.4-fast": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.4-mini": (["low", "medium", "high", "xhigh"], "high"),
    "gpt-5.4-mini-fast": (["low", "medium", "high", "xhigh"], "high"),
    "gpt-5.5": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.5-fast": (["low", "medium", "high", "xhigh"], "medium"),
    "gpt-5.5-pro": (["medium", "high", "xhigh"], "medium"),
    "gpt-5.6-sol": (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
    "gpt-5.6-sol-fast": (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
    "gpt-5.6-terra": (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
    "gpt-5.6-terra-flex": (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
    "gpt-5.6-luna": (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
    "gpt-5.6-luna-flex": (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
    "grok-4.6": (["low", "medium", "high", "xhigh"], "high"),
    "glm-4.6": (["none"], "none"),
    "glm-4.7": (["none"], "none"),
    "glm-5": (["none"], "none"),
    "glm-5.1": (["off", "high"], "high"),
    "glm-5.2": (["off", "high", "max"], "high"),
    "glm-5.2-fast": (["off", "high", "max"], "high"),
    "glm-5.3": (["low", "high", "max"], "max"),
    "glm-5.3-flash": (["low", "high", "max"], "high"),
    "kimi-k2.5": (["off", "high"], "high"),
    "kimi-k2.6": (["off", "high"], "high"),
    "kimi-k2.7-code": (["off", "high"], "high"),
    "kimi-k3": (["off", "low", "high", "max"], "high"),
    "deepseek-v4.1-flash": (["off", "low", "high", "max"], "high"),
    "deepseek-v4-flash-0731": (["off", "low", "high", "max"], "high"),
    "deepseek-v4-pro": (["off", "low", "high", "max"], "high"),
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "glm": {
        "id": "glm",
        "name": "GLM Coding Plan",
        "description": "Zhipu GLM models available to your Coding Plan key.",
        "modelsUrl": "https://open.bigmodel.cn/api/coding/paas/v4/models",
        "baseUrl": "https://open.bigmodel.cn/api/anthropic",
        "droidProvider": "anthropic",
        "authMode": "bearer",
    },
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek API",
        "description": "Models available from the official DeepSeek API.",
        "modelsUrl": "https://api.deepseek.com/models",
        "baseUrl": "https://api.deepseek.com/anthropic",
        "droidProvider": "anthropic",
        "authMode": "bearer",
    },
    "kimi": {
        "id": "kimi",
        "name": "Kimi Code Plan",
        "description": "Moonshot Kimi models available to your Code Plan key.",
        "modelsUrl": "https://api.kimi.com/coding/v1/models",
        "baseUrl": "https://api.kimi.com/coding/v1",
        "droidProvider": "generic-chat-completion-api",
    },
}


class ByokError(Exception):
    """An error safe to return to a UI or print without exposing a key."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"success": False, "code": self.code, "error": self.message}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _paths(
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path, Path]:
    factory = Path(factory_dir) if factory_dir else Path.home() / ".factory"
    oroio = Path(oroio_dir) if oroio_dir else Path.home() / ".oroio"
    return factory / "settings.json", factory / "config.json", oroio / "byok.json"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ByokError("invalid_config", f"Cannot read valid JSON from {path}.")
    if not isinstance(value, dict):
        raise ByokError("invalid_config", f"Expected a JSON object in {path}.")
    return value


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _state(path: Path) -> dict[str, Any]:
    value = _read_json(path, {"version": 1, "providers": {}})
    if not isinstance(value.get("providers"), dict):
        value["providers"] = {}
    value["version"] = 1
    return value


def _provider(provider_id: str) -> dict[str, Any]:
    provider = PROVIDERS.get(str(provider_id).lower())
    if not provider:
        raise ByokError(
            "unknown_provider",
            "Unknown provider. Choose glm, deepseek, or kimi.",
        )
    return provider


def _as_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _first(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _bool_metadata(raw: dict[str, Any], names: Iterable[str]) -> bool | None:
    value = _first(raw, names)
    if isinstance(value, bool):
        return value
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, dict):
        value = _first(capabilities, names)
        if isinstance(value, bool):
            return value
    return None


def normalize_models(payload: Any) -> list[dict[str, Any]]:
    """Normalize the common OpenAI-like model-list variants."""
    rows: Any = payload
    if isinstance(payload, dict):
        rows = payload.get("data")
        if not isinstance(rows, list):
            rows = payload.get("models")
    if not isinstance(rows, list):
        raise ByokError("invalid_response", "The provider returned an invalid model list.")

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        model_id = _first(raw, ("id", "model", "model_id"))
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        if model_id in seen:
            continue
        seen.add(model_id)

        display = _first(raw, ("display_name", "displayName", "name"))
        model: dict[str, Any] = {
            "id": model_id,
            "displayName": display.strip() if isinstance(display, str) and display.strip() else model_id,
        }
        context = _as_positive_int(
            _first(raw, ("context_length", "contextLength", "context_window", "max_context_length", "input_token_limit"))
        )
        output = _as_positive_int(
            _first(raw, ("max_output_tokens", "maxOutputTokens", "output_token_limit"))
        )
        if context is not None:
            model["contextLength"] = context
        if output is not None:
            model["maxOutputTokens"] = output

        images = _bool_metadata(raw, ("supports_images", "supportsImages", "vision"))
        modalities = _first(raw, ("input_modalities", "inputModalities", "modalities"))
        if images is None and isinstance(modalities, list):
            images = any(str(item).lower() in ("image", "images", "vision") for item in modalities)
        if images is not None:
            model["supportsImages"] = images

        reasoning = _bool_metadata(raw, ("supports_reasoning", "supportsReasoning", "reasoning"))
        if reasoning is not None:
            model["supportsReasoning"] = reasoning

        recommended = _bool_metadata(raw, ("recommended", "is_recommended", "isRecommended", "latest"))
        if recommended is not None:
            model["recommended"] = recommended
        created = _as_positive_int(_first(raw, ("created", "created_at", "createdAt")))
        if created is not None:
            model["created"] = created
        models.append(model)

    if not models:
        raise ByokError("no_models", "The provider returned no usable models for this key.")

    recommended = [model for model in models if model.get("recommended") is True]
    if not recommended:
        dated = [model for model in models if "created" in model]
        choice = max(dated, key=lambda model: model["created"]) if dated else models[0]
        choice["recommended"] = True
    return models


def _download_models(
    provider: dict[str, Any],
    api_key: str,
    opener: Callable[..., Any] | None = None,
    trusted_only: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ByokError("invalid_key", "Enter an API key.")
    parsed_url = urlsplit(provider["modelsUrl"])
    trusted_hosts = {"open.bigmodel.cn", "api.deepseek.com", "api.kimi.com"}
    if (
        parsed_url.scheme not in ({"https"} if trusted_only else {"http", "https"})
        or not parsed_url.hostname
        or (trusted_only and parsed_url.hostname not in trusted_hosts)
    ):
        raise ByokError("invalid_provider", "The provider endpoint is not trusted.")
    request = urllib.request.Request(
        provider["modelsUrl"],
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Accept": "application/json",
            "User-Agent": "oroio-byok/1",
        },
        method="GET",
    )
    open_url = opener or urllib.request.build_opener(_NoRedirect()).open
    try:
        with open_url(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise ByokError("invalid_key", "The provider rejected this API key (401).")
        if error.code == 403:
            raise ByokError("forbidden", "This API key cannot list models (403).")
        if error.code == 429:
            raise ByokError("rate_limited", "The provider is rate limiting requests. Try again later.")
        raise ByokError("provider_error", f"The provider returned HTTP {error.code}.")
    except (TimeoutError, socket.timeout):
        raise ByokError("timeout", "The provider request timed out.")
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise ByokError("timeout", "The provider request timed out.")
        raise ByokError("network_error", "Could not connect to the provider.")
    except OSError:
        raise ByokError("network_error", "Could not connect to the provider.")
    except Exception:
        raise ByokError("network_error", "Could not connect to the provider.")

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ByokError("invalid_response", "The provider response was too large.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ByokError("invalid_response", "The provider returned invalid JSON.")
    return normalize_models(payload)


def _model_id(entry: Any) -> str:
    return str(entry.get("model", "")) if isinstance(entry, dict) else ""


def _settings_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = config.get("customModels", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _legacy_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = config.get("custom_models", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _entry_base_url(entry: dict[str, Any], source: str) -> str:
    return str(entry.get("baseUrl" if source == "settings" else "base_url", ""))


def _entry_api_key(entry: dict[str, Any], source: str) -> str:
    return str(entry.get("apiKey" if source == "settings" else "api_key", ""))


def _entry_reasoning_effort(entry: dict[str, Any], source: str) -> str | None:
    direct = entry.get("reasoningEffort" if source == "settings" else "reasoning_effort")
    if isinstance(direct, str) and direct in REASONING_EFFORTS - {"default"}:
        return direct
    extra_args = entry.get("extraArgs" if source == "settings" else "extra_args")
    if not isinstance(extra_args, dict):
        return None
    effort = extra_args.get("reasoning_effort")
    return effort if isinstance(effort, str) and effort in REASONING_EFFORTS - {"default"} else None


def _base_model_id(model_id: str) -> str | None:
    normalized = model_id.strip().lower()
    family_id = normalized.rsplit("/", 1)[-1]
    aliases = {
        "gpt-5.6": "gpt-5.6-sol",
        "gpt-5.6-latest": "gpt-5.6-sol",
    }
    alias = aliases.get(family_id)
    if alias:
        return alias
    if family_id in MODEL_REASONING_PROFILES:
        return family_id
    for candidate in sorted(MODEL_REASONING_PROFILES, key=len, reverse=True):
        if candidate in family_id:
            return candidate
    return None


def _reasoning_profile(model_id: str) -> dict[str, Any] | None:
    normalized = model_id.strip().lower()
    family_id = normalized.rsplit("/", 1)[-1]
    base_model_id = _base_model_id(normalized)
    profile_id = base_model_id or family_id
    if profile_id not in MODEL_REASONING_PROFILES:
        return None
    efforts, default = MODEL_REASONING_PROFILES[profile_id]
    return {"efforts": efforts, "default": default, "baseModelId": base_model_id}


def _add_reasoning_profile(item: dict[str, Any]) -> None:
    profile = _reasoning_profile(str(item.get("id", "")))
    if not profile:
        return
    item["reasoningEfforts"] = profile["efforts"]
    item["defaultReasoningEffort"] = profile["default"]


def _legacy_view(entry: dict[str, Any], source: str) -> dict[str, Any]:
    if source == "legacy":
        return dict(entry)
    result: dict[str, Any] = {
        "model": entry.get("model", ""),
        "base_url": entry.get("baseUrl", ""),
        "api_key": entry.get("apiKey", ""),
        "provider": entry.get("provider", "generic-chat-completion-api"),
    }
    mapping = {
        "displayName": "model_display_name",
        "maxOutputTokens": "max_tokens",
        "reasoningEffort": "reasoning_effort",
        "enableThinking": "enable_thinking",
        "thinkingMaxTokens": "thinking_max_tokens",
        "baseModelId": "base_model_id",
        "extraArgs": "extra_args",
        "extraHeaders": "extra_headers",
    }
    for current, legacy in mapping.items():
        if current in entry:
            result[legacy] = entry[current]
    if isinstance(entry.get("noImageSupport"), bool):
        result["supports_images"] = not entry["noImageSupport"]
    return result


def _current_view(entry: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(previous or {})
    mapping = {
        "model": "model",
        "model_display_name": "displayName",
        "base_url": "baseUrl",
        "api_key": "apiKey",
        "provider": "provider",
        "max_tokens": "maxOutputTokens",
        "reasoning_effort": "reasoningEffort",
        "enable_thinking": "enableThinking",
        "thinking_max_tokens": "thinkingMaxTokens",
        "base_model_id": "baseModelId",
        "extra_args": "extraArgs",
        "extra_headers": "extraHeaders",
    }
    for legacy, current in mapping.items():
        if legacy in entry:
            result[current] = entry[legacy]
    if "supports_images" in entry:
        result["noImageSupport"] = not bool(entry["supports_images"])
    return result


def _provider_state(metadata: dict[str, Any], provider_id: str) -> dict[str, Any]:
    value = metadata.get("providers", {}).get(provider_id, {})
    return value if isinstance(value, dict) else {}


def _managed_ids(provider_state: dict[str, Any]) -> list[str]:
    rows = provider_state.get("managedModels", [])
    return [str(row) for row in rows if isinstance(row, str) and row] if isinstance(rows, list) else []


def _find_managed_entries(
    provider: dict[str, Any],
    provider_state: dict[str, Any],
    settings: dict[str, Any],
    legacy: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    managed = set(_managed_ids(provider_state))
    locations = provider_state.get("locations", {})
    locations = locations if isinstance(locations, dict) else {}
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for source, rows in (("settings", _settings_models(settings)), ("legacy", _legacy_models(legacy))):
        for entry in rows:
            model_id = _model_id(entry)
            if model_id not in managed or model_id in found:
                continue
            expected_source = locations.get(model_id)
            if expected_source in ("settings", "legacy") and expected_source != source:
                continue
            if _entry_base_url(entry, source) == provider["baseUrl"]:
                found[model_id] = (source, entry)
    return found


def list_providers(
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    metadata = _state(state_path)
    result: list[dict[str, Any]] = []
    for provider_id, provider in PROVIDERS.items():
        saved = _provider_state(metadata, provider_id)
        managed = _managed_ids(saved)
        found = _find_managed_entries(provider, saved, settings, legacy)
        item = dict(provider)
        item.update(
            {
                "configured": bool(managed and found),
                "managedModelIds": managed,
                "unavailableModelIds": list(saved.get("unavailable", [])) if isinstance(saved.get("unavailable"), list) else [],
                "lastDiscoveredAt": saved.get("lastDiscoveredAt"),
            }
        )
        result.append(item)
    return result


def _merge_discovery(
    provider_id: str,
    models: list[dict[str, Any]],
    settings: dict[str, Any],
    legacy: dict[str, Any],
    metadata: dict[str, Any],
    provider_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = provider_override or _provider(provider_id)
    saved = _provider_state(metadata, provider_id)
    managed = _managed_ids(saved)
    found = _find_managed_entries(provider, saved, settings, legacy)
    current_ids = {model["id"] for model in models}
    configured = bool(managed and found)

    output: list[dict[str, Any]] = []
    for model in models:
        item = dict(model)
        _add_reasoning_profile(item)
        source_entry = found.get(model["id"])
        if source_entry:
            effort = _entry_reasoning_effort(source_entry[1], source_entry[0])
            if effort:
                item["reasoningEffort"] = effort
        item["selected"] = model["id"] in managed if configured else bool(model.get("recommended"))
        item["isNew"] = configured and model["id"] not in managed
        item["unavailable"] = False
        output.append(item)

    known = saved.get("knownModels", [])
    known_by_id = {
        item.get("id"): item
        for item in known
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(known, list) else {}
    for model_id in managed:
        if model_id in current_ids:
            continue
        item = dict(known_by_id.get(model_id, {"id": model_id, "displayName": model_id}))
        _add_reasoning_profile(item)
        source_entry = found.get(model_id)
        if source_entry:
            view = _legacy_view(source_entry[1], source_entry[0])
            item["displayName"] = view.get("model_display_name") or model_id
            if view.get("max_tokens"):
                item["maxOutputTokens"] = view["max_tokens"]
            effort = _entry_reasoning_effort(source_entry[1], source_entry[0])
            if effort:
                item["reasoningEffort"] = effort
        item.update({"selected": True, "isNew": False, "unavailable": True})
        output.append(item)

    return {
        "success": True,
        "provider": provider_id,
        "configured": configured,
        "models": output,
        "recommendedModelIds": [model["id"] for model in models if model.get("recommended")],
        "managedModelIds": managed,
    }


def discover(
    provider_id: str,
    api_key: str,
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    provider_id = provider_id.lower()
    provider = _provider(provider_id)
    models = _download_models(provider, api_key, opener=opener)
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    return _merge_discovery(
        provider_id,
        models,
        _read_json(settings_path, {}),
        _read_json(legacy_path, {}),
        _state(state_path),
    )


def _make_entry(
    provider: dict[str, Any],
    model: dict[str, Any],
    api_key: str,
    source: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source == "legacy":
        entry = dict(previous or {})
        for key in ("max_tokens", "supports_images"):
            entry.pop(key, None)
        entry.update(
            {
                "model": model["id"],
                "model_display_name": model.get("displayName", model["id"]),
                "base_url": provider["baseUrl"],
                "api_key": api_key,
                "provider": provider["droidProvider"],
            }
        )
        if "maxOutputTokens" in model:
            entry["max_tokens"] = model["maxOutputTokens"]
        if "supportsImages" in model:
            entry["supports_images"] = model["supportsImages"]
        return entry

    entry = dict(previous or {})
    for key in ("maxOutputTokens", "noImageSupport"):
        entry.pop(key, None)
    entry.update(
        {
            "model": model["id"],
            "displayName": model.get("displayName", model["id"]),
            "baseUrl": provider["baseUrl"],
            "apiKey": api_key,
            "provider": provider["droidProvider"],
        }
    )
    if provider.get("authMode"):
        entry["authMode"] = provider["authMode"]
    if "maxOutputTokens" in model:
        entry["maxOutputTokens"] = model["maxOutputTokens"]
    if model.get("supportsImages") is False:
        entry["noImageSupport"] = True
    return entry


def _sync(
    provider_id: str,
    api_key: str,
    selected_model_ids: list[str],
    discovered_models: list[dict[str, Any]],
    factory_dir: str | os.PathLike[str] | None,
    oroio_dir: str | os.PathLike[str] | None,
    provider_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = provider_override or _provider(provider_id)
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    metadata = _state(state_path)
    saved = _provider_state(metadata, provider_id)
    managed = _managed_ids(saved)
    found = _find_managed_entries(provider, saved, settings, legacy)

    if not isinstance(selected_model_ids, list) or not selected_model_ids:
        raise ByokError("invalid_selection", "Select at least one model, or remove the provider.")
    selected: list[str] = []
    for model_id in selected_model_ids:
        if not isinstance(model_id, str) or not model_id or model_id in selected:
            continue
        selected.append(model_id)
    if not selected:
        raise ByokError("invalid_selection", "Select at least one model, or remove the provider.")

    discovered_by_id = {model["id"]: model for model in discovered_models}
    allowed = set(discovered_by_id) | set(managed)
    invalid = [model_id for model_id in selected if model_id not in allowed]
    if invalid:
        raise ByokError("invalid_selection", "One or more selected models are not available from this provider.")

    old_managed = set(managed)
    settings_rows = _settings_models(settings)
    legacy_rows = _legacy_models(legacy)
    settings_rows = [
        entry for entry in settings_rows
        if not (_model_id(entry) in old_managed and _entry_base_url(entry, "settings") == provider["baseUrl"])
    ]
    legacy_rows = [
        entry for entry in legacy_rows
        if not (_model_id(entry) in old_managed and _entry_base_url(entry, "legacy") == provider["baseUrl"])
    ]

    old_known = saved.get("knownModels", [])
    old_known_by_id = {
        item.get("id"): item
        for item in old_known
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(old_known, list) else {}
    locations: dict[str, str] = {}
    unavailable: list[str] = []
    for model_id in selected:
        previous_info = found.get(model_id)
        source = previous_info[0] if previous_info else "settings"
        previous = previous_info[1] if previous_info else None
        model = discovered_by_id.get(model_id)
        if model is None:
            model = dict(old_known_by_id.get(model_id, {"id": model_id, "displayName": model_id}))
            unavailable.append(model_id)
        entry = _make_entry(provider, model, api_key.strip(), source, previous)
        profile = _reasoning_profile(model_id)
        if source == "settings" and profile and profile.get("baseModelId"):
            entry["baseModelId"] = profile["baseModelId"]
        if source == "legacy":
            legacy_rows.append(entry)
        else:
            settings_rows.append(entry)
        locations[model_id] = source

    settings["customModels"] = settings_rows
    if legacy_path.exists() or any(source == "legacy" for source in locations.values()):
        legacy["custom_models"] = legacy_rows

    known_models = [dict(model) for model in discovered_models]
    for model_id in unavailable:
        known_models.append(dict(old_known_by_id.get(model_id, {"id": model_id, "displayName": model_id})))
    metadata["providers"][provider_id] = {
        "managedModels": selected,
        "locations": locations,
        "knownModels": known_models,
        "lastDiscoveredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "unavailable": unavailable,
    }

    _atomic_write_json(settings_path, settings)
    if legacy_path.exists() or any(source == "legacy" for source in locations.values()):
        _atomic_write_json(legacy_path, legacy)
    _atomic_write_json(state_path, metadata)
    return {
        "success": True,
        "provider": provider_id,
        "managedModelIds": selected,
        "unavailableModelIds": unavailable,
    }


def normalize_openai_base_url(base_url: str) -> str:
    """Validate and normalize an OpenAI-compatible runtime base URL."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise ByokError("invalid_base_url", "Enter an OpenAI-compatible Base URL.")
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        raise ByokError("invalid_base_url", "Enter a valid OpenAI-compatible Base URL.")
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ByokError(
            "invalid_base_url",
            "Base URL must be an HTTP(S) URL without credentials, query parameters, or fragments.",
        )
    host = parsed.hostname.lower().encode("idna").decode("ascii")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        raise ByokError("invalid_base_url", "Base URL contains an invalid port.")
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (parsed.scheme.lower() == "http" and port == 80)
    netloc = f"{host}:{port}" if port is not None and not default_port else host
    path = parsed.path.rstrip("/")
    if path.endswith("/models"):
        path = path[:-7].rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _openai_compatible_provider(base_url: str) -> tuple[str, dict[str, Any]]:
    normalized = normalize_openai_base_url(base_url)
    endpoint_id = "openai-compatible:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return endpoint_id, {
        "id": endpoint_id,
        "name": "OpenAI-compatible",
        "modelsUrl": normalized + "/models",
        "baseUrl": normalized,
        "droidProvider": "generic-chat-completion-api",
    }


def discover_openai_compatible(
    base_url: str,
    api_key: str,
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    endpoint_id, provider = _openai_compatible_provider(base_url)
    models = _download_models(provider, api_key, opener=opener, trusted_only=False)
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    result = _merge_discovery(
        endpoint_id,
        models,
        _read_json(settings_path, {}),
        _read_json(legacy_path, {}),
        _state(state_path),
        provider_override=provider,
    )
    result["baseUrl"] = provider["baseUrl"]
    return result


def apply_openai_compatible(
    base_url: str,
    api_key: str,
    selected_model_ids: list[str],
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    endpoint_id, provider = _openai_compatible_provider(base_url)
    models = _download_models(provider, api_key, opener=opener, trusted_only=False)
    result = _sync(
        endpoint_id,
        api_key,
        selected_model_ids,
        models,
        factory_dir,
        oroio_dir,
        provider_override=provider,
    )
    result["baseUrl"] = provider["baseUrl"]
    return result


def apply(
    provider_id: str,
    api_key: str,
    selected_model_ids: list[str],
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    provider_id = provider_id.lower()
    provider = _provider(provider_id)
    if not isinstance(api_key, str) or not api_key.strip():
        api_key = _saved_key(provider_id, factory_dir, oroio_dir)
    models = _download_models(provider, api_key, opener=opener)
    return _sync(provider_id, api_key, selected_model_ids, models, factory_dir, oroio_dir)


def _saved_key(
    provider_id: str,
    factory_dir: str | os.PathLike[str] | None,
    oroio_dir: str | os.PathLike[str] | None,
) -> str:
    provider = _provider(provider_id)
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    saved = _provider_state(_state(state_path), provider_id)
    found = _find_managed_entries(provider, saved, settings, legacy)
    for model_id in _managed_ids(saved):
        item = found.get(model_id)
        if item:
            key = _entry_api_key(item[1], item[0])
            if key:
                return key
    raise ByokError("not_configured", f"{provider['name']} is not configured.")


def refresh(
    provider_id: str,
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    provider_id = provider_id.lower()
    provider = _provider(provider_id)
    key = _saved_key(provider_id, factory_dir, oroio_dir)
    models = _download_models(provider, key, opener=opener)
    _, _, state_path = _paths(factory_dir, oroio_dir)
    selected = _managed_ids(_provider_state(_state(state_path), provider_id))
    result = _sync(provider_id, key, selected, models, factory_dir, oroio_dir)
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    result["models"] = _merge_discovery(
        provider_id,
        models,
        _read_json(settings_path, {}),
        _read_json(legacy_path, {}),
        _state(state_path),
    )["models"]
    return result


def remove_provider(
    provider_id: str,
    factory_dir: str | os.PathLike[str] | None = None,
    oroio_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    provider_id = provider_id.lower()
    provider = _provider(provider_id)
    settings_path, legacy_path, state_path = _paths(factory_dir, oroio_dir)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    metadata = _state(state_path)
    saved = _provider_state(metadata, provider_id)
    managed = set(_managed_ids(saved))

    if managed:
        settings["customModels"] = [
            entry for entry in _settings_models(settings)
            if not (_model_id(entry) in managed and _entry_base_url(entry, "settings") == provider["baseUrl"])
        ]
        if legacy_path.exists():
            legacy["custom_models"] = [
                entry for entry in _legacy_models(legacy)
                if not (_model_id(entry) in managed and _entry_base_url(entry, "legacy") == provider["baseUrl"])
            ]
        _atomic_write_json(settings_path, settings)
        if legacy_path.exists():
            _atomic_write_json(legacy_path, legacy)
    metadata["providers"].pop(provider_id, None)
    _atomic_write_json(state_path, metadata)
    return {"success": True, "provider": provider_id, "removedModelIds": sorted(managed)}


def list_custom_models(
    factory_dir: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    settings_path, legacy_path, _ = _paths(factory_dir, None)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    current = _settings_models(settings)
    current_ids = {_model_id(entry) for entry in current}
    return (
        [_legacy_view(entry, "settings") for entry in current]
        + [_legacy_view(entry, "legacy") for entry in _legacy_models(legacy) if _model_id(entry) not in current_ids]
    )


def _visible_entries(settings: dict[str, Any], legacy: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    current = _settings_models(settings)
    current_ids = {_model_id(entry) for entry in current}
    return ([('settings', entry) for entry in current]
            + [('legacy', entry) for entry in _legacy_models(legacy) if _model_id(entry) not in current_ids])


def remove_custom_model(index: int, factory_dir: str | os.PathLike[str] | None = None) -> None:
    settings_path, legacy_path, _ = _paths(factory_dir, None)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    visible = _visible_entries(settings, legacy)
    if index < 0 or index >= len(visible):
        raise ByokError("invalid_index", "Model index is out of range.")
    source, target = visible[index]
    if source == "settings":
        settings["customModels"] = [entry for entry in _settings_models(settings) if entry is not target]
        _atomic_write_json(settings_path, settings)
    else:
        legacy["custom_models"] = [entry for entry in _legacy_models(legacy) if entry is not target]
        _atomic_write_json(legacy_path, legacy)


def update_custom_model(
    index: int,
    model: dict[str, Any],
    factory_dir: str | os.PathLike[str] | None = None,
) -> None:
    if not isinstance(model, dict):
        raise ByokError("invalid_config", "Model config is required.")
    settings_path, legacy_path, _ = _paths(factory_dir, None)
    settings = _read_json(settings_path, {})
    legacy = _read_json(legacy_path, {})
    visible = _visible_entries(settings, legacy)
    if index < 0:
        rows = _settings_models(settings)
        rows.append(_current_view(model))
        settings["customModels"] = rows
        _atomic_write_json(settings_path, settings)
        return
    if index >= len(visible):
        raise ByokError("invalid_index", "Model index is out of range.")
    source, target = visible[index]
    if source == "settings":
        rows = _settings_models(settings)
        rows[rows.index(target)] = _current_view(model, target)
        settings["customModels"] = rows
        _atomic_write_json(settings_path, settings)
    else:
        updated = dict(target)
        updated.update(model)
        rows = _legacy_models(legacy)
        rows[rows.index(target)] = updated
        legacy["custom_models"] = rows
        _atomic_write_json(legacy_path, legacy)


def parse_selection(value: str, count: int, defaults: Iterable[int] = ()) -> list[int]:
    value = value.strip()
    if not value:
        return sorted(set(defaults))
    selected: set[int] = set()
    try:
        for part in value.split(","):
            part = part.strip()
            if "-" in part:
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start > end:
                    start, end = end, start
                selected.update(range(start, end + 1))
            else:
                selected.add(int(part))
    except ValueError:
        raise ByokError("invalid_selection", "Use model numbers such as 1,3-5.")
    if any(index < 1 or index > count for index in selected):
        raise ByokError("invalid_selection", f"Choose model numbers from 1 to {count}.")
    return sorted(selected)


def _choose_provider() -> str:
    print("选择平台:")
    for index, provider in enumerate(PROVIDERS.values(), 1):
        print(f"  {index}. {provider['name']} ({provider['id']})")
    choice = input("平台序号: ").strip()
    try:
        return list(PROVIDERS)[int(choice) - 1]
    except (ValueError, IndexError):
        raise ByokError("unknown_provider", "请选择有效的平台序号。")


def _cli_setup(provider_id: str | None) -> int:
    if not sys.stdin.isatty():
        raise ByokError("interactive_required", "byok setup requires an interactive terminal.")
    provider_id = provider_id.lower() if provider_id else _choose_provider()
    provider = _provider(provider_id)
    current = next(item for item in list_providers() if item["id"] == provider_id)
    if current["configured"]:
        answer = input(f"{provider['name']} 已配置；继续会替换该平台的 API Key。继续? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消。")
            return 0
    key = getpass.getpass("API Key（输入隐藏）: ").strip()
    result = discover(provider_id, key)
    models = result["models"]
    defaults = [index for index, model in enumerate(models, 1) if model.get("selected")]
    print("\n可用模型:")
    for index, model in enumerate(models, 1):
        flags = []
        if model.get("recommended"):
            flags.append("推荐")
        if model.get("isNew"):
            flags.append("新增")
        if model.get("unavailable"):
            flags.append("不可用，保留")
        marker = "x" if index in defaults else " "
        suffix = f" [{' / '.join(flags)}]" if flags else ""
        print(f"  [{marker}] {index}. {model['displayName']} ({model['id']}){suffix}")
    default_text = ",".join(str(index) for index in defaults)
    raw = input(f"选择模型（如 1,3-5；回车使用默认 {default_text}）: ")
    chosen = parse_selection(raw, len(models), defaults)
    selected_ids = [models[index - 1]["id"] for index in chosen]
    apply(provider_id, key, selected_ids)
    print(f"已配置 {provider['name']}: {', '.join(selected_ids)}")
    return 0


def _cli_list() -> int:
    rows = list_providers()
    print("官方 BYOK 平台:")
    for row in rows:
        status = "已配置" if row["configured"] else "未配置"
        models = ", ".join(row["managedModelIds"]) or "-"
        unavailable = f"；不可用: {', '.join(row['unavailableModelIds'])}" if row["unavailableModelIds"] else ""
        print(f"  {row['id']:<9} {status:<4} 模型: {models}{unavailable}")
    return 0


def _cli_refresh(provider_id: str | None) -> int:
    ids = [provider_id.lower()] if provider_id else [row["id"] for row in list_providers() if row["configured"]]
    if not ids:
        print("尚未配置官方 BYOK 平台。")
        return 0
    failures = 0
    for item in ids:
        try:
            result = refresh(item)
            suffix = f"；不可用但保留: {', '.join(result['unavailableModelIds'])}" if result["unavailableModelIds"] else ""
            print(f"已刷新 {item}: {', '.join(result['managedModelIds'])}{suffix}")
        except ByokError as error:
            failures += 1
            print(f"刷新 {item} 失败: {error.message}", file=sys.stderr)
    return 1 if failures else 0


def _cli_remove(provider_id: str) -> int:
    provider = _provider(provider_id.lower())
    if sys.stdin.isatty():
        answer = input(f"删除 {provider['name']} 管理的全部模型? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消。")
            return 0
    result = remove_provider(provider_id)
    print(f"已删除 {provider['name']} 管理的 {len(result['removedModelIds'])} 个模型。")
    return 0


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dk byok", description="配置官方 Coding Plan / API BYOK")
    sub = parser.add_subparsers(dest="command", required=True)
    setup_parser = sub.add_parser("setup", help="交互配置平台")
    setup_parser.add_argument("provider", nargs="?", choices=tuple(PROVIDERS))
    sub.add_parser("list", help="列出平台配置")
    refresh_parser = sub.add_parser("refresh", help="刷新模型列表")
    refresh_parser.add_argument("provider", nargs="?", choices=tuple(PROVIDERS))
    remove_parser = sub.add_parser("remove", help="删除平台配置")
    remove_parser.add_argument("provider", choices=tuple(PROVIDERS))
    args = parser.parse_args(argv)
    if args.command == "setup":
        return _cli_setup(args.provider)
    if args.command == "list":
        return _cli_list()
    if args.command == "refresh":
        return _cli_refresh(args.provider)
    if args.command == "remove":
        return _cli_remove(args.provider)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(cli_main())
    except ByokError as error:
        print(f"错误: {error.message}", file=sys.stderr)
        raise SystemExit(1)
