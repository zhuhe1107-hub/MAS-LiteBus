from __future__ import annotations

from mas_litebus.agents.base import AgentContext, BaseAgent
from mas_litebus.eval.metrics import Metrics
from mas_litebus.llm.base import LLMBackend
from mas_litebus.llm.parse import extract_json
from mas_litebus.llm.prompts import PLANNER_SYSTEM, planner_user_prompt
from mas_litebus.runtime.protocol import Capability


class PlannerAgent(BaseAgent):
    name = "planner"

    def __init__(self, llm: LLMBackend | None = None) -> None:
        self.llm = llm

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "task_decomposition",
                "request:string,tags:list",
                "steps:list",
                "Split a complex task into retrieval, execution and summarization steps.",
            ),
            Capability(
                "memory_reuse_decision",
                "memory_hits:list",
                "memory_refs:list",
                "Decide whether historical memory can be reused.",
            ),
        ]

    def plan(
        self,
        ctx: AgentContext,
        memory_refs: list[str],
        *,
        llm_mode: str | None = None,
        memory_summaries: list[str] | None = None,
        metrics: Metrics | None = None,
    ) -> dict[str, object]:
        if self.llm is not None and llm_mode in {"text", "protocol"}:
            try:
                return self._llm_plan(ctx, memory_refs, memory_summaries or [], llm_mode, metrics)
            except Exception:
                if metrics is not None:
                    metrics.llm_parse_failures += 1
                # Fall through to template — never let an Agent crash the run.
        return self._template_plan(ctx, memory_refs)

    def _template_plan(self, ctx: AgentContext, memory_refs: list[str]) -> dict[str, object]:
        steps = [
            {"agent": "retriever", "action": "retrieve", "goal": "collect evidence and reusable memories"},
            {"agent": "executor", "action": "execute", "goal": "run deterministic tool or CodeAct-style Python snippet"},
            {"agent": "summarizer", "action": "summarize", "goal": "produce final answer and write shared memory"},
        ]
        if memory_refs:
            steps.insert(
                1,
                {
                    "agent": "retriever",
                    "action": "reuse_memory",
                    "goal": "inject memory references into downstream context",
                },
            )
        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "steps": steps,
            "memory_refs": memory_refs,
        }

    def _llm_plan(
        self,
        ctx: AgentContext,
        memory_refs: list[str],
        memory_summaries: list[str],
        llm_mode: str,
        metrics: Metrics | None,
    ) -> dict[str, object]:
        assert self.llm is not None
        user = planner_user_prompt(ctx, memory_refs, memory_summaries, llm_mode)
        # Ollama's format=json was measured to 4x latency on llama3:8b without
        # reducing parse failures (see commit history). We rely on the strict
        # format reminder in PLANNER_SYSTEM + the lenient JSON extractor instead.
        resp = self.llm.chat(PLANNER_SYSTEM, user, temperature=0.0, max_tokens=512)
        if metrics is not None:
            metrics.record_llm(resp)
        data = extract_json(resp.text)
        raw_steps = data.get("steps", ["retrieve", "execute", "summarize"])
        if memory_refs and "reuse_memory" not in raw_steps:
            raw_steps = list(raw_steps)
            if "retrieve" in raw_steps:
                idx = raw_steps.index("retrieve")
                raw_steps.insert(idx + 1, "reuse_memory")
            else:
                raw_steps.insert(0, "reuse_memory")
        steps_full = [{"agent": _step_agent(s), "action": s, "goal": str(s)} for s in raw_steps]
        return {
            "task_id": ctx.task_id,
            "topic": ctx.topic,
            "steps": steps_full,
            "memory_refs": memory_refs,
            "reasoning": data.get("reasoning", ""),
        }


    def verbose_plan_text(self, ctx: AgentContext) -> str:
        return (
            f"任务 {ctx.task_id} 的主题是“{ctx.topic}”。我将先理解用户请求：{ctx.request}。"
            "接下来我会安排检索智能体收集相关背景资料、历史经验和证据；"
            "然后安排执行智能体根据任务类型运行必要的分析或代码；"
            "最后安排总结智能体整合检索证据、执行结果、风险提示和可复用经验，"
            "形成可交付结果。为了保持上下文完整，我会在文本模式中完整描述这些步骤、"
            "输入、预期输出和依赖关系。"
        )


def _step_agent(action: str) -> str:
    mapping = {
        "retrieve": "retriever",
        "reuse_memory": "retriever",
        "execute": "executor",
        "summarize": "summarizer",
    }
    return mapping.get(action, "runtime")

    def verbose_plan_text(self, ctx: AgentContext) -> str:
        return (
            f"任务 {ctx.task_id} 的主题是“{ctx.topic}”。我将先理解用户请求：{ctx.request}。"
            "接下来我会安排检索智能体收集相关背景资料、历史经验和证据；"
            "然后安排执行智能体根据任务类型运行必要的分析或代码；"
            "最后安排总结智能体整合检索证据、执行结果、风险提示和可复用经验，"
            "形成可交付结果。为了保持上下文完整，我会在文本模式中完整描述这些步骤、"
            "输入、预期输出和依赖关系。"
        )

