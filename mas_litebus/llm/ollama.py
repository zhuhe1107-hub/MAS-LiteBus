from __future__ import annotations

import time
from typing import Any

import httpx

from mas_litebus.llm.base import LLMBackend, LLMResponse


class OllamaBackend(LLMBackend):
    """Talks to a local Ollama server over its /api/chat endpoint.

    Ollama reports `prompt_eval_count` (real prompt tokens after Qwen's BPE)
    and `eval_count` (completion tokens) per response, so the benchmark can
    add up token-level communication cost without a separate tokenizer.
    """

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2:7b",
        host: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        # No proxy: the Ollama server is local; an HTTP proxy in the
        # environment would otherwise try (and fail) to relay our request.
        self._client = httpx.Client(timeout=timeout, trust_env=False)

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens),
            },
        }
        if json_mode:
            # Ollama's grammar-constrained JSON mode forces the next-token
            # sampler to follow a JSON schema, eliminating "wrapping in
            # markdown fence" / "prefix with explanation" failure modes.
            payload["format"] = "json"
        t0 = time.perf_counter()
        resp = self._client.post(f"{self.host}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        text = (data.get("message") or {}).get("content", "")
        return LLMResponse(
            text=text,
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
            latency_ms=round(elapsed_ms, 2),
            model=self.model,
            extra={
                "load_duration_ns": data.get("load_duration", 0),
                "prompt_eval_duration_ns": data.get("prompt_eval_duration", 0),
                "eval_duration_ns": data.get("eval_duration", 0),
            },
        )

    def close(self) -> None:
        self._client.close()
