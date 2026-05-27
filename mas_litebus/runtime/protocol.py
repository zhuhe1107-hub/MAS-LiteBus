from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass
class ProtocolMessage:
    """Compact structured message exchanged through the protocol bus."""

    action: str
    sender: str
    receiver: str
    task_id: str
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    capabilities: list[str] = field(default_factory=list)
    state_refs: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    reply_to: str | None = None
    msg_id: str = field(default_factory=lambda: new_id("msg"))
    timestamp: str = field(default_factory=now_ts)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "msg_id": self.msg_id,
            "task_id": self.task_id,
            "from": self.sender,
            "to": self.receiver,
            "action": self.action,
            "params": self.params,
            "capability_required": self.capabilities,
            "state_refs": self.state_refs,
            "memory_refs": self.memory_refs,
            "timestamp": self.timestamp,
        }
        if self.reply_to:
            data["reply_to"] = self.reply_to
        if self.result is not None:
            data["result"] = self.result
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass
class TextMessage:
    sender: str
    receiver: str
    task_id: str
    content: str
    msg_id: str = field(default_factory=lambda: new_id("txt"))
    timestamp: str = field(default_factory=now_ts)


@dataclass
class Capability:
    name: str
    inputs: str
    outputs: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "input": self.inputs,
            "output": self.outputs,
            "description": self.description,
        }

