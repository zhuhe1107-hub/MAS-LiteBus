from __future__ import annotations

from typing import Any

from mas_litebus.eval.metrics import Metrics
from mas_litebus.runtime.protocol import ProtocolMessage, TextMessage


class ProtocolBus:
    """Records and routes text/protocol messages for evaluation."""

    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics
        self.transcript: list[dict[str, Any]] = []

    def send_protocol(self, msg: ProtocolMessage) -> ProtocolMessage:
        payload = msg.to_json()
        self.metrics.message_count += 1
        self.metrics.protocol_chars += len(payload)
        for state_ref in msg.state_refs:
            self.metrics.state_transfer_count += 1
        self.transcript.append({"mode": "protocol", "payload": msg.to_dict()})
        return msg

    def send_text(self, msg: TextMessage) -> TextMessage:
        self.metrics.message_count += 1
        self.metrics.text_chars += len(msg.content)
        self.transcript.append(
            {
                "mode": "text",
                "payload": {
                    "msg_id": msg.msg_id,
                    "from": msg.sender,
                    "to": msg.receiver,
                    "task_id": msg.task_id,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                },
            }
        )
        return msg

    def record_state_bytes(self, size_bytes: int) -> None:
        self.metrics.state_bytes += size_bytes

