"""LLM backend abstraction.

Each Agent can optionally take an LLMBackend in its constructor. When the
backend is None the Agent uses its original deterministic template; when set,
the Agent issues a real LLM call and parses the response. The wire format
(text vs protocol) is shaped by which prompt template the Agent picks, so
both modes use the same backend but pay different token costs.
"""

from mas_litebus.llm.base import LLMBackend, LLMResponse

__all__ = ["LLMBackend", "LLMResponse"]
