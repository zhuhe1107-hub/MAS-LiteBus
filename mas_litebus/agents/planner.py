from __future__ import annotations

from mas_litebus.agents.base import AgentContext, BaseAgent
from mas_litebus.runtime.protocol import Capability


class PlannerAgent(BaseAgent):
    name = "planner"

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

    def plan(self, ctx: AgentContext, memory_refs: list[str]) -> dict[str, object]:
        steps = [
            {
                "agent": "retriever",
                "action": "retrieve",
                "goal": "collect evidence and reusable memories",
            },
            {
                "agent": "executor",
                "action": "execute",
                "goal": "run deterministic tool or CodeAct-style Python snippet",
            },
            {
                "agent": "summarizer",
                "action": "summarize",
                "goal": "produce final answer and write shared memory",
            },
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

    def verbose_plan_text(self, ctx: AgentContext) -> str:
        return (
            f"任务 {ctx.task_id} 的主题是“{ctx.topic}”。我将先理解用户请求：{ctx.request}。"
            "接下来我会安排检索智能体收集相关背景资料、历史经验和证据；"
            "然后安排执行智能体根据任务类型运行必要的分析或代码；"
            "最后安排总结智能体整合检索证据、执行结果、风险提示和可复用经验，"
            "形成可交付结果。为了保持上下文完整，我会在文本模式中完整描述这些步骤、"
            "输入、预期输出和依赖关系。"
        )

