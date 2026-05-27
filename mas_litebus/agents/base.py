from __future__ import annotations

from dataclasses import dataclass

from mas_litebus.runtime.protocol import Capability


@dataclass
class AgentContext:
    task_id: str
    topic: str
    request: str
    tags: list[str]


class BaseAgent:
    name = "base"

    def capabilities(self) -> list[Capability]:
        return []

