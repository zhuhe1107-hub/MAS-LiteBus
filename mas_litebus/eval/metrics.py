from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Metrics:
    mode: str
    started_at: float = field(default_factory=time.perf_counter)
    ended_at: float | None = None
    message_count: int = 0
    text_chars: int = 0
    protocol_chars: int = 0
    state_transfer_count: int = 0
    state_bytes: int = 0
    memory_search_count: int = 0
    memory_hit_count: int = 0
    retrieval_count: int = 0
    execution_count: int = 0
    tasks_completed: int = 0
    # IPC mode specifics
    ipc_send_count: int = 0
    ipc_recv_count: int = 0
    ipc_bytes_sent: int = 0
    ipc_round_trip_us_sum: int = 0
    shm_alloc_count: int = 0
    shm_attach_count: int = 0
    shm_peak_bytes: int = 0

    def finish(self) -> None:
        self.ended_at = time.perf_counter()

    @property
    def latency_seconds(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.perf_counter()
        return round(end - self.started_at, 6)

    @property
    def estimated_tokens(self) -> int:
        return int(round((self.text_chars + self.protocol_chars) / 1.8))

    @property
    def memory_hit_rate(self) -> float:
        if self.memory_search_count == 0:
            return 0.0
        return round(self.memory_hit_count / self.memory_search_count, 4)

    @property
    def ipc_round_trip_avg_us(self) -> float:
        if self.ipc_recv_count == 0:
            return 0.0
        return round(self.ipc_round_trip_us_sum / self.ipc_recv_count, 2)

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "mode": self.mode,
            "message_count": self.message_count,
            "text_chars": self.text_chars,
            "protocol_chars": self.protocol_chars,
            "estimated_tokens": self.estimated_tokens,
            "state_transfer_count": self.state_transfer_count,
            "state_bytes": self.state_bytes,
            "latency_seconds": self.latency_seconds,
            "memory_search_count": self.memory_search_count,
            "memory_hit_count": self.memory_hit_count,
            "memory_hit_rate": self.memory_hit_rate,
            "retrieval_count": self.retrieval_count,
            "execution_count": self.execution_count,
            "tasks_completed": self.tasks_completed,
            "ipc_send_count": self.ipc_send_count,
            "ipc_recv_count": self.ipc_recv_count,
            "ipc_bytes_sent": self.ipc_bytes_sent,
            "ipc_round_trip_us_sum": self.ipc_round_trip_us_sum,
            "ipc_round_trip_avg_us": self.ipc_round_trip_avg_us,
            "shm_alloc_count": self.shm_alloc_count,
            "shm_attach_count": self.shm_attach_count,
            "shm_peak_bytes": self.shm_peak_bytes,
        }

    def merge(self, other: "Metrics") -> None:
        self.message_count += other.message_count
        self.text_chars += other.text_chars
        self.protocol_chars += other.protocol_chars
        self.state_transfer_count += other.state_transfer_count
        self.state_bytes += other.state_bytes
        self.memory_search_count += other.memory_search_count
        self.memory_hit_count += other.memory_hit_count
        self.retrieval_count += other.retrieval_count
        self.execution_count += other.execution_count
        self.tasks_completed += other.tasks_completed
        self.ipc_send_count += other.ipc_send_count
        self.ipc_recv_count += other.ipc_recv_count
        self.ipc_bytes_sent += other.ipc_bytes_sent
        self.ipc_round_trip_us_sum += other.ipc_round_trip_us_sum
        self.shm_alloc_count += other.shm_alloc_count
        self.shm_attach_count += other.shm_attach_count
        if other.shm_peak_bytes > self.shm_peak_bytes:
            self.shm_peak_bytes = other.shm_peak_bytes

