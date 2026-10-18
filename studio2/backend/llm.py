"""LLM-клиент: fake (детерминированный), ollama и gpustack (OpenAI-совместимые)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

import httpx

from config import resolve_provider

FAKE_RESPONSES = {
    "qa-lead": '{"status": "done", "artifacts": [], "summary": "SDD готовы", "question": null}',
    "researcher": '{"status": "done", "artifacts": ["kb/architecture.md", "kb/patterns.md"], "summary": "Проект изучен", "question": null}',
    "testwriter": '{"status": "done", "artifacts": ["test_generated/AddNewAddressTest.kt"], "summary": "Тест написан", "question": null}',
    "debugger": '{"status": "done", "artifacts": [], "summary": "Тест исправлен", "question": null}',
    "reviewer": '{"status": "done", "artifacts": ["review_report.md"], "summary": "green", "question": null}',
    "device-inspector": '{"status": "done", "artifacts": ["kb/screens/add-new-address.md"], "summary": "Экран снят", "question": null}',
}
DEFAULT_FAKE = '{"status": "done", "artifacts": [], "summary": "ok", "question": null}'


class LlmError(RuntimeError):
    pass


class FakeProvider:
    @staticmethod
    def models() -> list[str]:
        return ["fake-1"]

    @staticmethod
    def response_for(role: str) -> str:
        return FAKE_RESPONSES.get(role, DEFAULT_FAKE)


class LlmClient:
    def __init__(self, config: dict, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._transport = transport
        self.log_dir: Path | None = None
        self._seq = 0

    def _provider(self, role: str) -> dict:
        return resolve_provider(self.config, role)

    def chat(self, role: str, messages: list[dict], system: str | None = None) -> str:
        provider = self._provider(role)
        if provider["name"] == "fake":
            return FakeProvider.response_for(role)
        return self._http_chat(provider, role, messages, system)

    def stream(self, role: str, messages: list[dict],
               system: str | None = None) -> Iterator[str]:
        provider = self._provider(role)
        if provider["name"] == "fake":
            text = FakeProvider.response_for(role)
        else:
            text = self._http_chat(provider, role, messages, system)
        for i in range(0, len(text), 24):
            yield text[i:i + 24]

    def _http_chat(self, provider: dict, role: str, messages: list[dict],
                   system: str | None) -> str:
        base = (provider.get("base_url") or "").rstrip("/")
        if not base:
            raise LlmError(f"для провайдера {provider['name']} не задан base_url")
        payload = {
            "model": provider["model"],
            "temperature": provider.get("temperature", 0.2),
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        }
        headers = {}
        if provider.get("api_key"):
            headers["Authorization"] = f"Bearer {provider['api_key']}"
        last_error: Exception | None = None
        with httpx.Client(transport=self._transport, timeout=180.0) as client:
            for _ in range(2):
                try:
                    response = client.post(f"{base}/v1/chat/completions",
                                           json=payload, headers=headers)
                    if response.status_code >= 400:
                        raise LlmError(
                            f"LLM вернула {response.status_code}: {response.text[:300]}"
                        )
                    content = response.json()["choices"][0]["message"]["content"]
                    self._log(role, payload, content)
                    return content
                except (httpx.HTTPError, KeyError, ValueError, LlmError) as exc:
                    last_error = exc
                    if isinstance(exc, LlmError):
                        break
        raise LlmError(f"LLM недоступна: {last_error}")

    def _log(self, role: str, payload: dict, content: str) -> None:
        if not self.log_dir:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._seq += 1
        safe = {k: v for k, v in payload.items() if k != "messages"}
        safe.update(role=role, at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    response=content[:2000])
        (self.log_dir / f"llm_{role}_{self._seq}.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _provider_config(self, provider_name: str) -> dict:
        provider = self.config["providers"].get(provider_name) or {}
        base = (provider.get("base_url") or "").rstrip("/")
        if not base:
            raise LlmError(f"провайдер {provider_name} не настроен")
        headers = {}
        if provider.get("api_key"):
            headers["Authorization"] = f"Bearer {provider['api_key']}"
        return {"base": base, "headers": headers}

    def list_models(self, provider_name: str) -> list[str]:
        if provider_name == "fake":
            return FakeProvider.models()
        meta = self._provider_config(provider_name)
        with httpx.Client(transport=self._transport, timeout=20.0) as client:
            try:
                response = client.get(f"{meta['base']}/v1/models", headers=meta["headers"])
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise LlmError(
                    f"не удалось получить модели {provider_name}: {exc}"
                ) from exc
        return [item["id"] for item in data.get("data", []) if item.get("id")]

    def test_model(self, role: str) -> dict:
        provider = self._provider(role)
        try:
            reply = self.chat(role, [{"role": "user", "content": "ping"}])
        except LlmError as exc:
            return {"ok": False, "provider": provider["name"],
                    "model": provider["model"], "error": str(exc)}
        return {"ok": True, "provider": provider["name"],
                "model": provider["model"], "reply": reply[:80]}
