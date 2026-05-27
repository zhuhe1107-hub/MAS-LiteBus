from __future__ import annotations

import array
import atexit
import uuid
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from typing import Iterable

from mas_litebus.runtime.protocol import now_ts
from mas_litebus.state.embedding import HashEmbedding


FLOAT32_BYTES = 4
SHM_NAME_PREFIX = "mas_state_"


def vector_to_bytes(vector: Iterable[float]) -> bytes:
    return array.array("f", list(vector)).tobytes()


def bytes_to_vector(buf: bytes, dim: int) -> list[float]:
    arr = array.array("f")
    arr.frombytes(buf[: dim * FLOAT32_BYTES])
    return list(arr)


@dataclass
class SharedStateRef:
    """Lightweight metadata about a state held in shared memory."""

    state_id: str
    shm_name: str
    producer: str
    task_id: str
    dim: int
    created_at: str = field(default_factory=now_ts)

    @property
    def size_bytes(self) -> int:
        return self.dim * FLOAT32_BYTES

    def to_dict(self) -> dict[str, str | int]:
        return {
            "state_id": self.state_id,
            "shm_name": self.shm_name,
            "producer": self.producer,
            "task_id": self.task_id,
            "dim": self.dim,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "type": "embedding",
            "dtype": "float32",
        }


def _new_shm_name() -> str:
    # Keep names short and filesystem-safe; /dev/shm prefers names <= 250 chars
    # but we stay well under to be cross-platform friendly.
    return f"{SHM_NAME_PREFIX}{uuid.uuid4().hex[:16]}"


def _untrack(shm: shared_memory.SharedMemory) -> None:
    try:
        from multiprocessing import resource_tracker

        resource_tracker.unregister(shm._name, "shared_memory")
    except Exception:
        pass


class SharedStateStore:
    """Cross-process state store backed by `multiprocessing.shared_memory`.

    create() allocates a named SharedMemory block, writes a float32 vector,
    and returns a SharedStateRef containing the name. Receivers in any
    process call attach() to read the vector by name without copying through
    a socket payload — only the name (a short string) travels over the wire.

    The store is owned by the coordinator process. Workers may create or
    attach but should not unlink; the coordinator calls release_task() to
    free every block produced during a task and close_all() at shutdown.
    """

    def __init__(self, embedder: HashEmbedding | None = None) -> None:
        self.embedder = embedder or HashEmbedding()
        self._owned: dict[str, shared_memory.SharedMemory] = {}
        self._external_sizes: dict[str, int] = {}
        self._task_index: dict[str, list[str]] = {}
        self._alloc_count = 0
        self._attach_count = 0
        self._current_bytes = 0
        self._peak_bytes = 0
        atexit.register(self._atexit_cleanup)

    @property
    def alloc_count(self) -> int:
        return self._alloc_count

    @property
    def attach_count(self) -> int:
        return self._attach_count

    @property
    def peak_bytes(self) -> int:
        return self._peak_bytes

    def encode(self, text: str) -> list[float]:
        return self.embedder.encode(text)

    def create(
        self, vector: list[float], producer: str, task_id: str, track: bool = True
    ) -> SharedStateRef:
        body = vector_to_bytes(vector)
        name = _new_shm_name()
        shm = shared_memory.SharedMemory(create=True, size=len(body), name=name)
        shm.buf[: len(body)] = body
        if not track:
            # Caller (a worker) hands ownership to the coordinator; do not let
            # the local resource_tracker also try to unlink at process exit.
            _untrack(shm)
            shm.close()
        else:
            self._owned[name] = shm
        self._task_index.setdefault(task_id, []).append(name)
        self._alloc_count += 1
        self._current_bytes += len(body)
        if self._current_bytes > self._peak_bytes:
            self._peak_bytes = self._current_bytes
        ref = SharedStateRef(
            state_id=f"state-{uuid.uuid4().hex[:10]}",
            shm_name=name,
            producer=producer,
            task_id=task_id,
            dim=len(vector),
        )
        return ref

    def register_external(
        self, shm_name: str, dim: int, producer: str, task_id: str, state_id: str | None = None
    ) -> SharedStateRef:
        """Record a shm block created by another process.

        Used by the coordinator after a worker reports a newly allocated
        block. The coordinator does not hold an attachment yet; it just
        accounts for the bytes and remembers the name so release_task()
        can later unlink it.
        """
        size = dim * FLOAT32_BYTES
        self._external_sizes[shm_name] = size
        self._task_index.setdefault(task_id, []).append(shm_name)
        self._alloc_count += 1
        self._current_bytes += size
        if self._current_bytes > self._peak_bytes:
            self._peak_bytes = self._current_bytes
        return SharedStateRef(
            state_id=state_id or f"state-{uuid.uuid4().hex[:10]}",
            shm_name=shm_name,
            producer=producer,
            task_id=task_id,
            dim=dim,
        )

    def create_from_text(self, text: str, producer: str, task_id: str) -> SharedStateRef:
        return self.create(self.encode(text), producer, task_id)

    def attach(self, shm_name: str, dim: int) -> list[float]:
        """Open an existing shared memory block by name and copy out the vector.

        The returned list is a fresh Python list, so the caller is free to
        close the attachment immediately. We do not return a view because
        Python's memoryview on shared_memory has surprising lifecycle rules.
        """
        shm = shared_memory.SharedMemory(name=shm_name)
        try:
            self._attach_count += 1
            return bytes_to_vector(bytes(shm.buf[: dim * FLOAT32_BYTES]), dim)
        finally:
            shm.close()

    def release_task(self, task_id: str) -> int:
        names = self._task_index.pop(task_id, [])
        released = 0
        for name in names:
            owned = self._owned.pop(name, None)
            if owned is not None:
                self._current_bytes -= owned.size
                try:
                    owned.close()
                except (BufferError, OSError):
                    pass
                try:
                    owned.unlink()
                    released += 1
                except FileNotFoundError:
                    pass
                continue
            size = self._external_sizes.pop(name, None)
            if size is not None:
                self._current_bytes -= size
                try:
                    shm = shared_memory.SharedMemory(name=name)
                    shm.close()
                    shm.unlink()
                    released += 1
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
        return released

    def close_all(self) -> None:
        for task_id in list(self._task_index.keys()):
            self.release_task(task_id)

    def _atexit_cleanup(self) -> None:
        try:
            self.close_all()
        except Exception:
            pass
