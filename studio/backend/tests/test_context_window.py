"""Окно контекста модели: реальный num_ctx Ollama, а не максимум архитектуры."""
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import StudioAgent  # noqa: E402


def _agent(tmp_path, handler):
    d = tmp_path / "data"
    d.mkdir()
    a = StudioAgent(str(d))
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


def test_window_is_min_of_num_ctx_and_architecture(tmp_path):
    """Ollama: архитектура 40960, num_ctx 8192 → окно 8192 (реальное место)."""
    def handler(request):
        assert request.url.path == "/api/show"
        return httpx.Response(200, json={
            "model_info": {"qwen3.context_length": 40960},
            "parameters": "temperature 0.7\nnum_ctx 8192\n",
        })
    a = _agent(tmp_path, handler)
    assert a.model_context_window("local-model:latest") == 8192


def test_window_architecture_only(tmp_path):
    """num_ctx не задан — берём максимум архитектуры."""
    def handler(request):
        return httpx.Response(200, json={
            "model_info": {"llama.context_length": 32768},
            "parameters": "",
        })
    a = _agent(tmp_path, handler)
    assert a.model_context_window("local-model:latest") == 32768


def test_window_remote_model_uses_table(tmp_path):
    """Удалённая модель: таблица CONTEXT_LIMITS, а не Ollama."""
    def handler(request):
        raise AssertionError("удалённая модель не должна ходить в Ollama")
    a = _agent(tmp_path, handler)
    assert a.model_context_window("qwen3.8-27b") == 32768


def test_window_fallback_when_ollama_down(tmp_path):
    """Ollama недоступна — окно из справочника, а не None/падение."""
    def handler(request):
        raise httpx.ConnectError("no ollama")
    a = _agent(tmp_path, handler)
    window = a.model_context_window("glm-4.7-flash:q4_K_M")
    assert window == 202752     # CONTEXT_LIMITS, а не 0/None
