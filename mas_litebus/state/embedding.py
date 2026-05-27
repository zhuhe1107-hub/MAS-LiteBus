from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from mas_litebus.runtime.protocol import new_id, now_ts


TOKEN_RE = re.compile(r"[A-Za-z0-9_+.-]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class HashEmbedding:
    """Deterministic lightweight semantic vectorizer.

    It avoids external model downloads while still providing a non-text vector
    representation for protocol experiments. Each token is hashed into a fixed
    dimension and L2-normalized.
    """

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def encode(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    av = list(a)
    bv = list(b)
    if not av or not bv or len(av) != len(bv):
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    an = math.sqrt(sum(x * x for x in av))
    bn = math.sqrt(sum(y * y for y in bv))
    if an == 0 or bn == 0:
        return 0.0
    return dot / (an * bn)


@dataclass
class StateObject:
    state_id: str
    producer: str
    vector: list[float]
    source_summary: str
    task_id: str
    kind: str = "embedding"
    dtype: str = "float32"
    created_at: str = field(default_factory=now_ts)

    @property
    def dim(self) -> int:
        return len(self.vector)

    @property
    def size_bytes(self) -> int:
        return len(self.vector) * 4

    def metadata(self) -> dict[str, str | int]:
        return {
            "state_id": self.state_id,
            "producer": self.producer,
            "type": self.kind,
            "dim": self.dim,
            "dtype": self.dtype,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "task_id": self.task_id,
        }


class StateStore:
    def __init__(self, embedder: HashEmbedding | None = None) -> None:
        self.embedder = embedder or HashEmbedding()
        self._states: dict[str, StateObject] = {}

    def create(self, text: str, producer: str, task_id: str) -> StateObject:
        state = StateObject(
            state_id=new_id("state"),
            producer=producer,
            vector=self.embedder.encode(text),
            source_summary=text[:240],
            task_id=task_id,
        )
        self._states[state.state_id] = state
        return state

    def put_vector(
        self, vector: list[float], producer: str, task_id: str, source_summary: str
    ) -> StateObject:
        state = StateObject(
            state_id=new_id("state"),
            producer=producer,
            vector=vector,
            source_summary=source_summary[:240],
            task_id=task_id,
        )
        self._states[state.state_id] = state
        return state

    def fetch(self, state_id: str) -> StateObject:
        return self._states[state_id]

    def all_states(self) -> list[StateObject]:
        return list(self._states.values())

