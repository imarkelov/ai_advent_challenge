"""Конфигурация studio2: умолчания + data/config.json + .env."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "providers": {
        "ollama": {"base_url": "http://localhost:11435", "api_key": None},
        "gpustack": {"base_url": "https://gpustack.data.lmru.tech/v1", "api_key": None},
        "fake": {"base_url": None, "api_key": None},
    },
    "roles": {
        "qa-lead": {"provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.2},
        "researcher": {"provider": "ollama", "model": "qwen3.5:9b", "temperature": 0.2},
        "testwriter": {"provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.4},
        "debugger": {"provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.3},
        "reviewer": {"provider": "ollama", "model": "gemma4:12b", "temperature": 0.1},
    },
    "critic": {"must_differ_from_author": True},
    "android": {
        "sdk_dir": r"C:\Users\migor\AppData\Local\Android\Sdk",
        "avd": "Pixel_8_Pro_API_33",
        "repo_path": r"C:\Users\migor\StudioProjects\lmru--mobile-customer--app",
        "package": "ru.leroymerlin.mobile",
        "test_module": ":android:app",
    },
}

ENV_MAP = {
    ("providers", "gpustack", "base_url"): "GPUSTACK_BASE_URL",
    ("providers", "gpustack", "api_key"): "GPUSTACK_API_KEY",
    ("providers", "ollama", "base_url"): "OLLAMA_BASE_URL",
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(data_dir: str) -> dict:
    path = Path(data_dir) / "config.json"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            stored = {}
        if isinstance(stored, dict):
            cfg = _deep_merge(cfg, stored)
    for (section, provider, field), env_name in ENV_MAP.items():
        value = os.getenv(env_name)
        if value and not cfg[section][provider].get(field):
            cfg[section][provider][field] = value
    return cfg


def save_config(data_dir: str, cfg: dict) -> dict:
    path = Path(data_dir) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def resolve_provider(cfg: dict, role: str) -> dict:
    spec = cfg["roles"].get(role, {})
    name = spec.get("provider", "fake")
    provider = dict(cfg["providers"].get(name) or {"base_url": None, "api_key": None})
    provider["name"] = name
    provider["model"] = spec.get("model", "")
    provider["temperature"] = spec.get("temperature", 0.2)
    return provider
