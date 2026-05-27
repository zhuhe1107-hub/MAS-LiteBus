from __future__ import annotations

from pathlib import Path
from typing import Any

from mas_litebus.agents.base import AgentContext
from mas_litebus.agents.executor import ExecutorAgent
from mas_litebus.agents.planner import PlannerAgent
from mas_litebus.agents.retriever import RetrieverAgent
from mas_litebus.agents.summarizer import SummarizerAgent
from mas_litebus.eval.metrics import Metrics
from mas_litebus.llm.base import LLMBackend
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.runtime.bus import ProtocolBus
from mas_litebus.runtime.protocol import ProtocolMessage, TextMessage
from mas_litebus.runtime.task import Task
from mas_litebus.state.embedding import StateStore


class MultiAgentRuntime:
    def __init__(
        self,
        mode: str,
        memory_path: str | Path,
        llm: LLMBackend | None = None,
    ) -> None:
        if mode not in {"text", "text_v2", "text_with_memory", "protocol_no_memory", "protocol"}:
            raise ValueError(
                "mode must be one of text / text_v2 / text_with_memory / protocol_no_memory / protocol"
            )
        self.mode = mode
        self.llm = llm
        self.metrics = Metrics(mode=mode)
        self.bus = ProtocolBus(self.metrics)
        self.states = StateStore()
        self.memory = SharedMemoryStore(memory_path, self.states.embedder)
        self.planner = PlannerAgent(llm=llm)
        self.retriever = RetrieverAgent(self.memory, self.states, llm=llm)
        self.executor = ExecutorAgent(llm=llm)
        self.summarizer = SummarizerAgent(self.memory, self.states, llm=llm)
        self.agents = [self.planner, self.retriever, self.executor, self.summarizer]
        self.task_results: list[dict[str, Any]] = []
        self._handshake_done = False

    @property
    def _llm_mode(self) -> str | None:
        """Map runtime mode to wire-style prompt template ('text' / 'protocol')."""
        if self.llm is None:
            return None
        if self.mode in {"protocol", "protocol_no_memory"}:
            return "protocol"
        return "text"

    def close(self) -> None:
        self.memory.close()

    def run_tasks(self, tasks: list[Task]) -> dict[str, Any]:
        if self.mode in {"protocol", "protocol_no_memory"}:
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
        elif self.mode == "protocol_no_memory":
            result = self._run_protocol_no_memory_task(ctx)
        elif self.mode == "text_v2":
            result = self._run_text_v2_task(ctx)
        elif self.mode == "text_with_memory":
            result = self._run_text_with_memory_task(ctx)
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
        memory_summaries = [unit.summary for unit, _, _ in memory_hits]

        plan = self.planner.plan(
            ctx, memory_refs,
            llm_mode=self._llm_mode,
            memory_summaries=memory_summaries,
            metrics=self.metrics,
        )
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
        retrieval = self.retriever.retrieve(
            ctx, use_memory=True, skip_local=skip_local,
            llm_mode=self._llm_mode,
            plan_payload={"steps": [step["action"] for step in plan["steps"]], "topic": ctx.topic},
            metrics=self.metrics,
        )
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

        execution = self.executor.execute(
            ctx, retrieval["items"], memory_refs,
            llm_mode=self._llm_mode,
            retr_payload={"items": [str(it["title"]) for it in retrieval["items"]], "state_id": retr_state.state_id},
            metrics=self.metrics,
        )
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
            llm_mode=self._llm_mode,
            metrics=self.metrics,
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
        # In LLM mode, exercise the planner LLM first so its tokens are
        # counted; verbose_plan_text still drives the bus payload.
        plan = (
            self.planner.plan(ctx, [], llm_mode=self._llm_mode, metrics=self.metrics)
            if self.llm is not None
            else None
        )
        plan_text = self.planner.verbose_plan_text(ctx)
        self.bus.send_text(TextMessage("planner", "retriever", ctx.task_id, plan_text))

        retrieval = self.retriever.retrieve(
            ctx, use_memory=False, skip_local=False,
            llm_mode=self._llm_mode,
            plan_payload=plan,
            metrics=self.metrics,
        )
        self.metrics.retrieval_count += 1
        retrieval_text = self.retriever.verbose_retrieval_text(ctx, retrieval)
        retrieval_context = "\n".join([plan_text, retrieval_text])
        self.bus.send_text(TextMessage("retriever", "executor", ctx.task_id, retrieval_context))

        execution = self.executor.execute(
            ctx, retrieval["items"], memory_refs=[],
            llm_mode=self._llm_mode,
            retr_payload=retrieval_context if self._llm_mode == "text" else {"items": [it["title"] for it in retrieval["items"]]},
            metrics=self.metrics,
        )
        self.metrics.execution_count += 1
        execution_text = self.executor.verbose_execution_text(ctx, execution)
        execution_context = "\n".join([plan_text, retrieval_text, execution_text])
        self.bus.send_text(TextMessage("executor", "summarizer", ctx.task_id, execution_context))

        summary = self.summarizer.summarize(
            ctx, retrieval["items"], execution, memory_hits=[],
            llm_mode=self._llm_mode,
            metrics=self.metrics,
        )
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

    def _run_text_v2_task(self, ctx: AgentContext) -> dict[str, Any]:
        # Reasonable NL baseline: each step sends only its own description,
        # downstream agents do NOT receive accumulated history. Equivalent to
        # common multi-agent systems that pass per-step natural-language briefs
        # instead of full conversation logs. Memory is still disabled here so
        # the comparison to protocol mode isolates structured-vs-NL format.
        plan = (
            self.planner.plan(ctx, [], llm_mode=self._llm_mode, metrics=self.metrics)
            if self.llm is not None
            else None
        )
        plan_text = self.planner.verbose_plan_text(ctx)
        self.bus.send_text(TextMessage("planner", "retriever", ctx.task_id, plan_text))

        retrieval = self.retriever.retrieve(
            ctx, use_memory=False, skip_local=False,
            llm_mode=self._llm_mode, plan_payload=plan, metrics=self.metrics,
        )
        self.metrics.retrieval_count += 1
        retrieval_text = self.retriever.verbose_retrieval_text(ctx, retrieval)
        self.bus.send_text(TextMessage("retriever", "executor", ctx.task_id, retrieval_text))

        execution = self.executor.execute(
            ctx, retrieval["items"], memory_refs=[],
            llm_mode=self._llm_mode,
            retr_payload=retrieval_text if self._llm_mode == "text" else {"items": [it["title"] for it in retrieval["items"]]},
            metrics=self.metrics,
        )
        self.metrics.execution_count += 1
        execution_text = self.executor.verbose_execution_text(ctx, execution)
        self.bus.send_text(TextMessage("executor", "summarizer", ctx.task_id, execution_text))

        summary = self.summarizer.summarize(
            ctx, retrieval["items"], execution, memory_hits=[],
            llm_mode=self._llm_mode, metrics=self.metrics,
        )
        summary_text = self.summarizer.verbose_summary_text(ctx, summary)
        self.bus.send_text(TextMessage("summarizer", "runtime", ctx.task_id, summary_text))
        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "mode": "text_v2",
            "memory_refs": [],
            "new_memory_id": summary["memory_id"],
            "state_refs": [],
            "summary": summary["summary"],
            "skipped_local_retrieval": False,
        }

    def _run_text_with_memory_task(self, ctx: AgentContext) -> dict[str, Any]:
        # Ablation: NL communication (per-step, like text_v2) but WITH shared
        # memory reuse. Isolates the contribution of memory reuse from the
        # contribution of switching to structured protocol.
        query_vector = self.states.embedder.encode(" ".join([ctx.topic, ctx.request, *ctx.tags]))
        self.metrics.memory_search_count += 1
        memory_hits = self.memory.search(ctx.request, ctx.tags, query_vector, top_k=3)
        if memory_hits:
            self.metrics.memory_hit_count += 1
        memory_refs = [unit.memory_id for unit, _, _ in memory_hits]
        memory_summaries = [unit.summary for unit, _, _ in memory_hits]
        skip_local = len(memory_refs) >= 2

        plan = (
            self.planner.plan(
                ctx, memory_refs,
                llm_mode=self._llm_mode, memory_summaries=memory_summaries, metrics=self.metrics,
            )
            if self.llm is not None
            else None
        )
        plan_text = self.planner.verbose_plan_text(ctx)
        self.bus.send_text(TextMessage("planner", "retriever", ctx.task_id, plan_text))

        retrieval = self.retriever.retrieve(
            ctx, use_memory=True, skip_local=skip_local,
            llm_mode=self._llm_mode, plan_payload=plan, metrics=self.metrics,
        )
        if not skip_local:
            self.metrics.retrieval_count += 1
        retrieval_text = self.retriever.verbose_retrieval_text(ctx, retrieval)
        self.bus.send_text(TextMessage("retriever", "executor", ctx.task_id, retrieval_text))

        execution = self.executor.execute(
            ctx, retrieval["items"], memory_refs=memory_refs,
            llm_mode=self._llm_mode,
            retr_payload=retrieval_text if self._llm_mode == "text" else {"items": [it["title"] for it in retrieval["items"]]},
            metrics=self.metrics,
        )
        self.metrics.execution_count += 1
        execution_text = self.executor.verbose_execution_text(ctx, execution)
        self.bus.send_text(TextMessage("executor", "summarizer", ctx.task_id, execution_text))

        summary = self.summarizer.summarize(
            ctx, retrieval["items"], execution, retrieval["memory_hits"],
            llm_mode=self._llm_mode, metrics=self.metrics,
        )
        summary_text = self.summarizer.verbose_summary_text(ctx, summary)
        self.bus.send_text(TextMessage("summarizer", "runtime", ctx.task_id, summary_text))
        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "mode": "text_with_memory",
            "memory_refs": memory_refs,
            "new_memory_id": summary["memory_id"],
            "state_refs": [],
            "summary": summary["summary"],
            "skipped_local_retrieval": skip_local,
        }

    def _run_protocol_no_memory_task(self, ctx: AgentContext) -> dict[str, Any]:
        # Ablation: structured protocol with state_refs, but NO shared memory.
        # Coordinator does not search history, retriever does not pull from
        # memory, summarizer does not persist. Isolates the protocol+state
        # contribution from the memory-reuse contribution.
        query_state = self.states.create(
            " ".join([ctx.topic, ctx.request, *ctx.tags]),
            producer="planner",
            task_id=ctx.task_id,
        )
        self.bus.record_state_bytes(query_state.size_bytes)

        plan = self.planner.plan(
            ctx, memory_refs=[],
            llm_mode=self._llm_mode, metrics=self.metrics,
        )
        self.bus.send_protocol(
            ProtocolMessage(
                action="task.plan",
                sender="planner",
                receiver="runtime",
                task_id=ctx.task_id,
                params={"topic": ctx.topic, "steps": [step["action"] for step in plan["steps"]]},
                capabilities=["task_decomposition"],
                state_refs=[query_state.state_id],
                memory_refs=[],
            )
        )
        self.bus.record_state_bytes(query_state.size_bytes)

        retrieval = self.retriever.retrieve(
            ctx, use_memory=False, skip_local=False,
            llm_mode=self._llm_mode,
            plan_payload={"steps": [step["action"] for step in plan["steps"]]},
            metrics=self.metrics,
        )
        self.metrics.retrieval_count += 1
        retr_state = retrieval["state"]
        self.bus.send_protocol(
            ProtocolMessage(
                action="retrieve.result",
                sender="retriever",
                receiver="executor",
                task_id=ctx.task_id,
                params={"query": ctx.request, "top_k": 3, "skip_local": False},
                result={
                    "items": [item["title"] for item in retrieval["items"]],
                    "memory_hits": [],
                },
                capabilities=["keyword_search"],
                state_refs=[retr_state.state_id],
                memory_refs=[],
            )
        )
        self.bus.record_state_bytes(retr_state.size_bytes)

        execution = self.executor.execute(
            ctx, retrieval["items"], memory_refs=[],
            llm_mode=self._llm_mode,
            retr_payload={"items": [str(it["title"]) for it in retrieval["items"]], "state_id": retr_state.state_id},
            metrics=self.metrics,
        )
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
                memory_refs=[],
            )
        )
        self.bus.record_state_bytes(retr_state.size_bytes)

        summary = self.summarizer.summarize(
            ctx, retrieval["items"], execution, memory_hits=[], write_memory=False,
            llm_mode=self._llm_mode, metrics=self.metrics,
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
                capabilities=["summary_generation"],
                state_refs=[sum_state.state_id],
                memory_refs=[],
            )
        )
        self.bus.record_state_bytes(sum_state.size_bytes)

        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "mode": "protocol_no_memory",
            "memory_refs": [],
            "new_memory_id": summary["memory_id"],
            "state_refs": [query_state.state_id, retr_state.state_id, sum_state.state_id],
            "summary": summary["summary"],
            "skipped_local_retrieval": False,
        }
