from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

from mas_litebus.agents.base import AgentContext
from mas_litebus.agents.executor import ExecutorAgent
from mas_litebus.agents.planner import PlannerAgent
from mas_litebus.agents.retriever import RetrieverAgent
from mas_litebus.agents.summarizer import SummarizerAgent
from mas_litebus.ipc.shm_store import SharedStateRef, SharedStateStore
from mas_litebus.ipc.socket_bus import SocketServer
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.state.embedding import HashEmbedding


@dataclass
class WorkerStateHandle:
    """Drop-in stand-in for StateObject when a worker allocates shared memory.

    Carries the cached vector so SummarizerAgent.summarize() can still pass
    it to memory.write() without re-attaching the shared memory segment.
    """

    state_id: str
    shm_name: str
    vector: list[float]
    dim: int
    size_bytes: int


class WorkerStateStore:
    """In-process StateStore replacement that allocates float32 shm blocks.

    Each create() reports a SharedStateRef whose lifetime is owned by the
    coordinator; the worker stores cached vectors locally so existing Agent
    code that touches `state.vector` keeps working without an attach.
    """

    def __init__(self, embedder: HashEmbedding | None = None) -> None:
        self.embedder = embedder or HashEmbedding()
        self._shared = SharedStateStore(embedder=self.embedder)
        self.pending_refs: list[SharedStateRef] = []

    def create(self, text: str, producer: str, task_id: str) -> WorkerStateHandle:
        vector = self.embedder.encode(text)
        ref = self._shared.create(vector, producer=producer, task_id=task_id, track=False)
        self.pending_refs.append(ref)
        return WorkerStateHandle(
            state_id=ref.state_id,
            shm_name=ref.shm_name,
            vector=vector,
            dim=ref.dim,
            size_bytes=ref.size_bytes,
        )

    def drain_refs(self) -> list[dict[str, Any]]:
        out = [
            {"shm_name": r.shm_name, "state_id": r.state_id, "dim": r.dim, "producer": r.producer}
            for r in self.pending_refs
        ]
        self.pending_refs.clear()
        return out


AGENT_TYPES = {"planner", "retriever", "executor", "summarizer"}


def _build_agent(agent_type: str, memory: SharedMemoryStore | None, states: WorkerStateStore):
    if agent_type == "planner":
        return PlannerAgent()
    if agent_type == "retriever":
        assert memory is not None and states is not None
        return RetrieverAgent(memory, states)  # type: ignore[arg-type]
    if agent_type == "executor":
        return ExecutorAgent()
    if agent_type == "summarizer":
        assert memory is not None and states is not None
        return SummarizerAgent(memory, states)  # type: ignore[arg-type]
    raise ValueError(f"unknown agent_type: {agent_type}")


def _build_ctx(params: dict[str, Any], task_id: str) -> AgentContext:
    return AgentContext(
        task_id=task_id,
        topic=params["topic"],
        request=params["request"],
        tags=list(params.get("tags", [])),
    )


def _handle_plan(agent: PlannerAgent, params: dict[str, Any], task_id: str) -> dict[str, Any]:
    ctx = _build_ctx(params, task_id)
    plan = agent.plan(ctx, list(params.get("memory_refs", [])))
    return {
        "steps": [step["action"] for step in plan["steps"]],
        "topic": plan["topic"],
        "memory_refs": plan["memory_refs"],
    }


def _handle_retrieve(
    agent: RetrieverAgent, params: dict[str, Any], task_id: str, states: WorkerStateStore
) -> dict[str, Any]:
    ctx = _build_ctx(params, task_id)
    use_memory = bool(params.get("use_memory", True))
    skip_local = bool(params.get("skip_local", False))
    result = agent.retrieve(ctx, use_memory=use_memory, skip_local=skip_local)
    state: WorkerStateHandle = result["state"]
    items = [
        {"title": str(item["title"]), "tags": list(item["tags"])}
        for item in result["items"]
    ]
    return {
        "items": items,
        "memory_hits": result["memory_hits"],
        "retr_state": {
            "state_id": state.state_id,
            "shm_name": state.shm_name,
            "dim": state.dim,
            "size_bytes": state.size_bytes,
        },
    }


def _handle_execute(agent: ExecutorAgent, params: dict[str, Any], task_id: str) -> dict[str, Any]:
    ctx = _build_ctx(params, task_id)
    evidence_titles = list(params.get("evidence_titles", []))
    # Reconstruct minimal evidence dicts so ExecutorAgent.execute can read titles.
    evidence = [{"title": title, "text": "", "tags": []} for title in evidence_titles]
    memory_refs = list(params.get("memory_refs", []))
    result = agent.execute(ctx, evidence, memory_refs)
    artifact = result["artifact"]
    return {
        "status": result["status"],
        "artifact": {
            "kind": artifact.get("kind"),
            "used_memory_refs": list(artifact.get("used_memory_refs", [])),
            "evidence_titles": list(artifact.get("evidence_titles", [])),
        },
        "stdout_chars": len(str(result.get("stdout", ""))),
    }


def _handle_summarize(
    agent: SummarizerAgent, params: dict[str, Any], task_id: str
) -> dict[str, Any]:
    ctx = _build_ctx(params, task_id)
    evidence_titles = list(params.get("evidence_titles", []))
    evidence = [{"title": title} for title in evidence_titles]
    artifact_kind = params.get("artifact_kind", "generic_checklist")
    stdout_text = params.get("stdout", "")
    memory_hits = list(params.get("memory_hits", []))
    execution = {
        "status": "ok",
        "artifact": {
            "kind": artifact_kind,
            "used_memory_refs": list(params.get("memory_refs", [])),
            "evidence_titles": evidence_titles,
        },
        "stdout": stdout_text,
    }
    result = agent.summarize(ctx, evidence, execution, memory_hits)
    state: WorkerStateHandle = result["state"]
    return {
        "summary": result["summary"],
        "strategy": result["strategy"],
        "memory_id": result["memory_id"],
        "sum_state": {
            "state_id": state.state_id,
            "shm_name": state.shm_name,
            "dim": state.dim,
            "size_bytes": state.size_bytes,
        },
    }


def _handle_ping(agent_type: str, agent) -> dict[str, Any]:
    return {
        "agent_type": agent_type,
        "pid": os.getpid(),
        "protocol_version": "1.0",
        "capabilities": [cap.name for cap in agent.capabilities()],
    }


def agent_worker_main(
    agent_type: str,
    socket_path: str,
    memory_path: str,
    ready_signal_path: str | None = None,
) -> None:
    if agent_type not in AGENT_TYPES:
        raise ValueError(f"unknown agent_type: {agent_type}")

    states = WorkerStateStore()
    memory: SharedMemoryStore | None = None
    if agent_type in {"retriever", "summarizer"}:
        memory = SharedMemoryStore(memory_path, states.embedder)
        # Enable WAL so retriever reads do not block summarizer writes across procs.
        try:
            memory.conn.execute("PRAGMA journal_mode=WAL")
            memory.conn.commit()
        except Exception:
            pass

    agent = _build_agent(agent_type, memory, states)
    server = SocketServer(socket_path)
    if ready_signal_path is not None:
        with open(ready_signal_path, "w") as fh:
            fh.write(str(os.getpid()))

    # Ignore SIGINT in workers; coordinator drives shutdown explicitly.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        server.accept()
        while True:
            try:
                msg = server.recv()
            except ConnectionError:
                break
            action = msg.get("action")
            task_id = str(msg.get("task_id", ""))
            params = msg.get("params", {})
            response: dict[str, Any] = {
                "action": f"{action}.result" if action else "error",
                "task_id": task_id,
            }
            try:
                if action == "ping":
                    response["result"] = _handle_ping(agent_type, agent)
                elif action == "plan" and agent_type == "planner":
                    response["result"] = _handle_plan(agent, params, task_id)
                elif action == "retrieve" and agent_type == "retriever":
                    response["result"] = _handle_retrieve(agent, params, task_id, states)
                elif action == "execute" and agent_type == "executor":
                    response["result"] = _handle_execute(agent, params, task_id)
                elif action == "summarize" and agent_type == "summarizer":
                    response["result"] = _handle_summarize(agent, params, task_id)
                elif action == "shutdown":
                    response["result"] = {"ok": True}
                else:
                    response["action"] = "error"
                    response["result"] = {
                        "code": "unsupported",
                        "message": f"agent {agent_type} cannot handle {action}",
                    }
                response["allocs"] = states.drain_refs()
                server.send(response)
                if action == "shutdown":
                    break
            except Exception as exc:  # noqa: BLE001 — last-resort isolation
                response["action"] = "error"
                response["result"] = {"code": "exception", "message": repr(exc)}
                response["allocs"] = states.drain_refs()
                try:
                    server.send(response)
                except Exception:
                    pass
    finally:
        if memory is not None:
            memory.close()
        server.close()


if __name__ == "__main__":
    # Allow launching via `python -m mas_litebus.ipc.worker` for ad-hoc tests.
    if len(sys.argv) < 4:
        print("usage: python -m mas_litebus.ipc.worker <agent_type> <socket_path> <memory_path>")
        sys.exit(2)
    agent_worker_main(sys.argv[1], sys.argv[2], sys.argv[3])
