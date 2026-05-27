from __future__ import annotations

import multiprocessing as mp
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from mas_litebus.agents.base import AgentContext
from mas_litebus.eval.metrics import Metrics
from mas_litebus.ipc.shm_store import SharedStateRef, SharedStateStore
from mas_litebus.ipc.socket_bus import SocketClient, encode_frame
from mas_litebus.ipc.worker import agent_worker_main
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.runtime.bus import ProtocolBus
from mas_litebus.runtime.protocol import ProtocolMessage
from mas_litebus.runtime.task import Task
from mas_litebus.state.embedding import HashEmbedding


AGENT_ORDER = ("planner", "retriever", "executor", "summarizer")


class IPCMultiAgentRuntime:
    """Cross-process protocol coordinator.

    Forks one subprocess per agent role, exchanges length-prefixed JSON frames
    over AF_UNIX sockets, and passes embedding vectors through named POSIX
    shared-memory blocks. Logical protocol-message accounting goes through the
    same ProtocolBus + Metrics path as in-process protocol mode so report.py
    can compare them apples-to-apples.
    """

    mode = "protocol_ipc"

    def __init__(self, memory_path: str | Path, ctx_method: str = "fork") -> None:
        # protocol_ipc relies on AF_UNIX, fork(), and POSIX shared memory via
        # /dev/shm — all Linux/macOS-only. On Windows the runtime would fail
        # later with cryptic errors when `socket.AF_UNIX` is missing or the
        # forked workers cannot reuse parent state, so we fail fast here.
        if platform.system() not in {"Linux", "Darwin"}:
            raise RuntimeError(
                f"protocol_ipc mode requires Linux or macOS (got {platform.system()!r}); "
                "use --mode protocol or one of the text-family modes on this platform."
            )
        self.metrics = Metrics(mode=self.mode)
        self.bus = ProtocolBus(self.metrics)
        self.embedder = HashEmbedding()
        self.coord_states = SharedStateStore(self.embedder)
        self.memory = SharedMemoryStore(memory_path, self.embedder)
        self._mp_ctx = mp.get_context(ctx_method)
        self._tmp_dir = tempfile.mkdtemp(prefix="mas_litebus_ipc_")
        self.workers: dict[str, dict[str, Any]] = {}
        self.task_results: list[dict[str, Any]] = []
        self._handshake_done = False
        self._closed = False
        try:
            self._spawn_all_workers(str(memory_path))
        except Exception:
            self.close()
            raise

    # ----- worker lifecycle -----------------------------------------------

    def _spawn_all_workers(self, memory_path: str) -> None:
        for agent_type in AGENT_ORDER:
            sock_path = os.path.join(self._tmp_dir, f"{agent_type}.sock")
            ready_path = os.path.join(self._tmp_dir, f"{agent_type}.ready")
            proc = self._mp_ctx.Process(
                target=agent_worker_main,
                args=(agent_type, sock_path, memory_path, ready_path),
                daemon=False,
            )
            proc.start()
            deadline = time.time() + 5.0
            while not os.path.exists(ready_path):
                if time.time() > deadline:
                    raise RuntimeError(f"{agent_type} worker did not become ready within 5s")
                if not proc.is_alive():
                    raise RuntimeError(f"{agent_type} worker exited before ready (code {proc.exitcode})")
                time.sleep(0.01)
            client = SocketClient(sock_path)
            client.connect()
            self.workers[agent_type] = {
                "proc": proc,
                "client": client,
                "socket_path": sock_path,
                "ready_path": ready_path,
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for agent_type, info in self.workers.items():
            client: SocketClient | None = info.get("client")
            proc: mp.process.BaseProcess | None = info.get("proc")
            if client is not None:
                try:
                    client.send({"action": "shutdown", "task_id": "", "params": {}})
                    client.recv()
                except Exception:
                    pass
                client.close()
            if proc is not None:
                proc.join(timeout=3)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=2)
        try:
            self.memory.close()
        except Exception:
            pass
        try:
            self.coord_states.close_all()
        except Exception:
            pass
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ----- IPC primitive --------------------------------------------------

    def _ipc_call(self, agent_type: str, action: str, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one request to a worker and return its response. Updates IPC metrics."""
        info = self.workers[agent_type]
        client: SocketClient = info["client"]
        request = {"action": action, "task_id": task_id, "params": params}
        frame_bytes = len(encode_frame(request))
        t0 = time.perf_counter_ns()
        client.send(request)
        self.metrics.ipc_send_count += 1
        self.metrics.ipc_bytes_sent += frame_bytes
        try:
            response = client.recv()
        finally:
            elapsed_us = (time.perf_counter_ns() - t0) // 1000
            self.metrics.ipc_round_trip_us_sum += elapsed_us
        self.metrics.ipc_recv_count += 1
        # Pull worker-side state allocations into coordinator's task index so
        # release_task() can unlink them after the task finishes. We do NOT
        # call bus.record_state_bytes here; per-state-reference accounting
        # happens once per logical send_protocol() below so the totals stay
        # comparable with in-process protocol mode.
        for alloc in response.get("allocs", []):
            self.coord_states.register_external(
                shm_name=alloc["shm_name"],
                dim=int(alloc["dim"]),
                producer=str(alloc["producer"]),
                task_id=task_id or "handshake",
                state_id=alloc.get("state_id"),
            )
            self.metrics.shm_alloc_count += 1
            self.metrics.shm_peak_bytes = max(
                self.metrics.shm_peak_bytes, self.coord_states.peak_bytes
            )
        if response.get("action") == "error":
            raise RuntimeError(f"worker error from {agent_type}: {response.get('result')}")
        return response

    # ----- task pipeline --------------------------------------------------

    def run_tasks(self, tasks: list[Task]) -> dict[str, Any]:
        self._handshake_all()
        for task in tasks:
            self.run_task(task)
        self.metrics.finish()
        # Reflect peak shm bytes one last time in case allocations outlived the loop.
        self.metrics.shm_peak_bytes = max(
            self.metrics.shm_peak_bytes, self.coord_states.peak_bytes
        )
        return {
            "mode": self.mode,
            "metrics": self.metrics.to_dict(),
            "tasks": self.task_results,
            "transcript_sample": self.bus.transcript[:12],
        }

    def _handshake_all(self) -> None:
        if self._handshake_done:
            return
        for agent_type in AGENT_ORDER:
            resp = self._ipc_call(agent_type, "ping", "handshake", {})
            caps = list(resp["result"]["capabilities"])
            self.bus.send_protocol(
                ProtocolMessage(
                    action="handshake",
                    sender=agent_type,
                    receiver="runtime",
                    task_id="handshake",
                    params={
                        "protocol_version": resp["result"]["protocol_version"],
                        "agent_type": agent_type,
                        "worker_pid": resp["result"]["pid"],
                        "capabilities": caps,
                    },
                )
            )
        self._handshake_done = True

    def run_task(self, task: Task) -> dict[str, Any]:
        ctx = AgentContext(task.task_id, task.topic, task.request, task.tags)
        result = self._run_task(ctx)
        self.metrics.tasks_completed += 1
        self.task_results.append(result)
        # All shm blocks allocated for this task are now unreferenced — unlink
        # them from /dev/shm so the next task starts clean.
        self.coord_states.release_task(ctx.task_id)
        self.coord_states.release_task("handshake")  # no-op except on first task
        return result

    def _run_task(self, ctx: AgentContext) -> dict[str, Any]:
        # Coordinator-side query embedding lives in shared memory; only its
        # short shm_name (~20 chars) crosses the socket to the workers.
        query_text = " ".join([ctx.topic, ctx.request, *ctx.tags])
        query_vector = self.embedder.encode(query_text)
        query_ref = self.coord_states.create(
            query_vector, producer="coordinator", task_id=ctx.task_id, track=True
        )
        self.metrics.shm_alloc_count += 1
        self.metrics.shm_peak_bytes = max(
            self.metrics.shm_peak_bytes, self.coord_states.peak_bytes
        )
        self.bus.record_state_bytes(query_ref.size_bytes)

        self.metrics.memory_search_count += 1
        memory_hits = self.memory.search(ctx.request, ctx.tags, query_vector, top_k=3)
        if memory_hits:
            self.metrics.memory_hit_count += 1
        memory_refs = [unit.memory_id for unit, _, _ in memory_hits]
        memory_hits_payload = [
            {
                "memory_id": unit.memory_id,
                "score": round(score, 4),
                "reason": reason,
                "summary": unit.summary,
                "tags": list(unit.tags),
            }
            for unit, score, reason in memory_hits
        ]

        # ---- planner -----------------------------------------------------
        plan_resp = self._ipc_call(
            "planner",
            "plan",
            ctx.task_id,
            {
                "topic": ctx.topic,
                "request": ctx.request,
                "tags": ctx.tags,
                "memory_refs": memory_refs,
            },
        )
        plan_result = plan_resp["result"]
        self.bus.send_protocol(
            ProtocolMessage(
                action="task.plan",
                sender="planner",
                receiver="runtime",
                task_id=ctx.task_id,
                params={"topic": ctx.topic, "steps": plan_result["steps"]},
                capabilities=["task_decomposition"],
                state_refs=[query_ref.state_id],
                memory_refs=memory_refs,
            )
        )
        self.bus.record_state_bytes(query_ref.size_bytes)

        # ---- retriever ---------------------------------------------------
        skip_local = len(memory_refs) >= 2
        retrieve_resp = self._ipc_call(
            "retriever",
            "retrieve",
            ctx.task_id,
            {
                "topic": ctx.topic,
                "request": ctx.request,
                "tags": ctx.tags,
                "query_shm_name": query_ref.shm_name,
                "query_dim": query_ref.dim,
                "use_memory": True,
                "skip_local": skip_local,
            },
        )
        retr_result = retrieve_resp["result"]
        if not skip_local:
            self.metrics.retrieval_count += 1
        retr_state_id = retr_result["retr_state"]["state_id"]
        retr_shm_name = retr_result["retr_state"]["shm_name"]
        retr_state_bytes = int(retr_result["retr_state"]["size_bytes"])
        evidence_titles = [item["title"] for item in retr_result["items"]]
        self.bus.send_protocol(
            ProtocolMessage(
                action="retrieve.result",
                sender="retriever",
                receiver="executor",
                task_id=ctx.task_id,
                params={"query": ctx.request, "top_k": 3, "skip_local": skip_local},
                result={
                    "items": evidence_titles,
                    "memory_hits": [h["memory_id"] for h in retr_result["memory_hits"]],
                },
                capabilities=["keyword_search", "semantic_memory_search"],
                state_refs=[retr_state_id],
                memory_refs=memory_refs,
            )
        )
        self.bus.record_state_bytes(retr_state_bytes)

        # ---- executor ----------------------------------------------------
        execute_resp = self._ipc_call(
            "executor",
            "execute",
            ctx.task_id,
            {
                "topic": ctx.topic,
                "request": ctx.request,
                "tags": ctx.tags,
                "evidence_titles": evidence_titles,
                "memory_refs": memory_refs,
            },
        )
        exec_result = execute_resp["result"]
        self.metrics.execution_count += 1
        self.bus.send_protocol(
            ProtocolMessage(
                action="execute.result",
                sender="executor",
                receiver="summarizer",
                task_id=ctx.task_id,
                result={
                    "status": exec_result["status"],
                    "artifact_kind": exec_result["artifact"]["kind"],
                    "stdout_chars": exec_result["stdout_chars"],
                },
                capabilities=["python_exec", "template_render"],
                state_refs=[retr_state_id],
                memory_refs=memory_refs,
            )
        )
        self.bus.record_state_bytes(retr_state_bytes)

        # ---- summarizer --------------------------------------------------
        summarize_resp = self._ipc_call(
            "summarizer",
            "summarize",
            ctx.task_id,
            {
                "topic": ctx.topic,
                "request": ctx.request,
                "tags": ctx.tags,
                "evidence_titles": evidence_titles,
                "artifact_kind": exec_result["artifact"]["kind"],
                "stdout": "",  # already counted via stdout_chars in execute_resp
                "memory_hits": retr_result["memory_hits"],
                "memory_refs": memory_refs,
            },
        )
        sum_result = summarize_resp["result"]
        sum_state_id = sum_result["sum_state"]["state_id"]
        sum_shm_name = sum_result["sum_state"]["shm_name"]
        sum_state_bytes = int(sum_result["sum_state"]["size_bytes"])
        self.bus.send_protocol(
            ProtocolMessage(
                action="summarize.result",
                sender="summarizer",
                receiver="runtime",
                task_id=ctx.task_id,
                result={
                    "memory_id": sum_result["memory_id"],
                    "summary_chars": len(sum_result["summary"]),
                    "strategy_code": exec_result["artifact"]["kind"],
                },
                capabilities=["summary_generation", "memory_write"],
                state_refs=[sum_state_id],
                memory_refs=[sum_result["memory_id"]],
            )
        )
        self.bus.record_state_bytes(sum_state_bytes)

        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "mode": self.mode,
            "memory_refs": memory_refs,
            "new_memory_id": sum_result["memory_id"],
            "state_refs": [query_ref.state_id, retr_state_id, sum_state_id],
            "shm_names": [query_ref.shm_name, retr_shm_name, sum_shm_name],
            "summary": sum_result["summary"],
            "skipped_local_retrieval": skip_local,
        }
