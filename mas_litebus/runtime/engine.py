from __future__ import annotations

from pathlib import Path
from typing import Any

from mas_litebus.agents.base import AgentContext
from mas_litebus.agents.executor import ExecutorAgent
from mas_litebus.agents.planner import PlannerAgent
from mas_litebus.agents.retriever import RetrieverAgent
from mas_litebus.agents.summarizer import SummarizerAgent
from mas_litebus.eval.metrics import Metrics
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.runtime.bus import ProtocolBus
from mas_litebus.runtime.protocol import ProtocolMessage, TextMessage
from mas_litebus.runtime.task import Task
from mas_litebus.state.embedding import StateStore


class MultiAgentRuntime:
    def __init__(self, mode: str, memory_path: str | Path) -> None:
        if mode not in {"text", "protocol"}:
            raise ValueError("mode must be 'text' or 'protocol'")
        self.mode = mode
        self.metrics = Metrics(mode=mode)
        self.bus = ProtocolBus(self.metrics)
        self.states = StateStore()
        self.memory = SharedMemoryStore(memory_path, self.states.embedder)
        self.planner = PlannerAgent()
        self.retriever = RetrieverAgent(self.memory, self.states)
        self.executor = ExecutorAgent()
        self.summarizer = SummarizerAgent(self.memory, self.states)
        self.agents = [self.planner, self.retriever, self.executor, self.summarizer]
        self.task_results: list[dict[str, Any]] = []
        self._handshake_done = False

    def close(self) -> None:
        self.memory.close()

    def run_tasks(self, tasks: list[Task]) -> dict[str, Any]:
        if self.mode == "protocol":
            self._protocol_handshake("runtime")
        for task in tasks:
            self.run_task(task)
        self.metrics.finish()
        return {
            "mode": self.mode,
            "metrics": self.metrics.to_dict(),
            "tasks": self.task_results,
            "transcript_sample": self.bus.transcript[:12],
        }

    def run_task(self, task: Task) -> dict[str, Any]:
        ctx = AgentContext(task.task_id, task.topic, task.request, task.tags)
        if self.mode == "protocol":
            result = self._run_protocol_task(ctx)
        else:
            result = self._run_text_task(ctx)
        self.metrics.tasks_completed += 1
        self.task_results.append(result)
        return result

    def _run_protocol_task(self, ctx: AgentContext) -> dict[str, Any]:
        query_state = self.states.create(
            " ".join([ctx.topic, ctx.request, *ctx.tags]),
            producer="planner",
            task_id=ctx.task_id,
        )
        self.bus.record_state_bytes(query_state.size_bytes)

        self.metrics.memory_search_count += 1
        memory_hits = self.memory.search(ctx.request, ctx.tags, query_state.vector, top_k=3)
        if memory_hits:
            self.metrics.memory_hit_count += 1
        memory_refs = [unit.memory_id for unit, _, _ in memory_hits]

        plan = self.planner.plan(ctx, memory_refs)
        self.bus.send_protocol(
            ProtocolMessage(
                action="task.plan",
                sender="planner",
                receiver="runtime",
                task_id=ctx.task_id,
                params={"topic": ctx.topic, "steps": [step["action"] for step in plan["steps"]]},
                capabilities=["task_decomposition"],
                state_refs=[query_state.state_id],
                memory_refs=memory_refs,
            )
        )
        self.bus.record_state_bytes(query_state.size_bytes)

        # Reuse two or more memories as a signal to skip local corpus search.
        skip_local = len(memory_refs) >= 2
        retrieval = self.retriever.retrieve(ctx, use_memory=True, skip_local=skip_local)
        if not skip_local:
            self.metrics.retrieval_count += 1
        retr_state = retrieval["state"]
        self.bus.send_protocol(
            ProtocolMessage(
                action="retrieve.result",
                sender="retriever",
                receiver="executor",
                task_id=ctx.task_id,
                params={"query": ctx.request, "top_k": 3, "skip_local": skip_local},
                result={
                    "items": [item["title"] for item in retrieval["items"]],
                    "memory_hits": [hit["memory_id"] for hit in retrieval["memory_hits"]],
                },
                capabilities=["keyword_search", "semantic_memory_search"],
                state_refs=[retr_state.state_id],
                memory_refs=memory_refs,
            )
        )
        self.bus.record_state_bytes(retr_state.size_bytes)

        execution = self.executor.execute(ctx, retrieval["items"], memory_refs)
        self.metrics.execution_count += 1
        self.bus.send_protocol(
            ProtocolMessage(
                action="execute.result",
                sender="executor",
                receiver="summarizer",
                task_id=ctx.task_id,
                result={
                    "status": execution["status"],
                    "artifact_kind": execution["artifact"]["kind"],
                    "stdout_chars": len(str(execution["stdout"])),
                },
                capabilities=["python_exec", "template_render"],
                state_refs=[retr_state.state_id],
                memory_refs=memory_refs,
            )
        )
        self.bus.record_state_bytes(retr_state.size_bytes)

        summary = self.summarizer.summarize(
            ctx,
            retrieval["items"],
            execution,
            retrieval["memory_hits"],
        )
        sum_state = summary["state"]
        self.bus.send_protocol(
            ProtocolMessage(
                action="summarize.result",
                sender="summarizer",
                receiver="runtime",
                task_id=ctx.task_id,
                result={
                    "memory_id": summary["memory_id"],
                    "summary_chars": len(summary["summary"]),
                    "strategy_code": execution["artifact"]["kind"],
                },
                capabilities=["summary_generation", "memory_write"],
                state_refs=[sum_state.state_id],
                memory_refs=[summary["memory_id"]],
            )
        )
        self.bus.record_state_bytes(sum_state.size_bytes)

        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "mode": "protocol",
            "memory_refs": memory_refs,
            "new_memory_id": summary["memory_id"],
            "state_refs": [query_state.state_id, retr_state.state_id, sum_state.state_id],
            "summary": summary["summary"],
            "skipped_local_retrieval": skip_local,
        }

    def _protocol_handshake(self, task_id: str) -> None:
        if self._handshake_done:
            return
        for agent in self.agents:
            caps = [cap.name for cap in agent.capabilities()]
            self.bus.send_protocol(
                ProtocolMessage(
                    action="handshake",
                    sender=agent.name,
                    receiver="runtime",
                    task_id=task_id,
                    params={
                        "protocol_version": "1.0",
                        "agent_type": agent.name,
                        "capabilities": caps,
                    },
                )
            )
        self._handshake_done = True

    def _run_text_task(self, ctx: AgentContext) -> dict[str, Any]:
        plan_text = self.planner.verbose_plan_text(ctx)
        self.bus.send_text(TextMessage("planner", "retriever", ctx.task_id, plan_text))

        retrieval = self.retriever.retrieve(ctx, use_memory=False, skip_local=False)
        self.metrics.retrieval_count += 1
        retrieval_text = self.retriever.verbose_retrieval_text(ctx, retrieval)
        retrieval_context = "\n".join([plan_text, retrieval_text])
        self.bus.send_text(TextMessage("retriever", "executor", ctx.task_id, retrieval_context))

        execution = self.executor.execute(ctx, retrieval["items"], memory_refs=[])
        self.metrics.execution_count += 1
        execution_text = self.executor.verbose_execution_text(ctx, execution)
        execution_context = "\n".join([plan_text, retrieval_text, execution_text])
        self.bus.send_text(TextMessage("executor", "summarizer", ctx.task_id, execution_context))

        summary = self.summarizer.summarize(ctx, retrieval["items"], execution, memory_hits=[])
        summary_text = self.summarizer.verbose_summary_text(ctx, summary)
        summary_context = "\n".join([plan_text, retrieval_text, execution_text, summary_text])
        self.bus.send_text(TextMessage("summarizer", "runtime", ctx.task_id, summary_context))
        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "mode": "text",
            "memory_refs": [],
            "new_memory_id": summary["memory_id"],
            "state_refs": [],
            "summary": summary["summary"],
            "skipped_local_retrieval": False,
        }
