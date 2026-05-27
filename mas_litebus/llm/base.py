from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    """Wraps a backend reply with real tokenizer-reported counts.

    `prompt_tokens` and `completion_tokens` come straight from the backend
    (Ollama's `prompt_eval_count` / `eval_count`, Anthropic's usage block),
    so the benchmark report can show real LLM token cost instead of the
    chars/1.8 estimate used by the deterministic baseline.
    """

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    extra: dict = field(default_factory=dict)


class LLMBackend(ABC):
    """Stateless backend interface used by every Agent.

    Implementations must return real tokenizer counts so the benchmark can
    measure communication efficiency in actual LLM tokens rather than
    character heuristics.
    """

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        raise NotImplementedError
