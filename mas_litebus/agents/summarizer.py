from __future__ import annotations

from mas_litebus.agents.base import AgentContext, BaseAgent
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.runtime.protocol import Capability
from mas_litebus.state.embedding import StateStore


class SummarizerAgent(BaseAgent):
    name = "summarizer"

    def __init__(self, memory: SharedMemoryStore, states: StateStore) -> None:
        self.memory = memory
        self.states = states

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "summary_generation",
                "evidence:list,artifact:dict",
                "summary:string",
                "Generate final task summary.",
            ),
            Capability(
                "memory_write",
                "summary:string,tags:list",
                "memory_id:string",
                "Persist reusable experience into shared memory.",
            ),
        ]

    def summarize(
        self,
        ctx: AgentContext,
        evidence: list[dict[str, object]],
        execution: dict[str, object],
        memory_hits: list[dict[str, object]],
    ) -> dict[str, object]:
        artifact = execution["artifact"]
        evidence_titles = [str(item["title"]) for item in evidence]
        reused = [hit["memory_id"] for hit in memory_hits]
        summary = (
            f"{ctx.topic}: 完成任务拆解、证据检索和执行验证。"
            f"关键证据包括 {', '.join(evidence_titles) or '无'}。"
            f"执行产物类型为 {artifact.get('kind')}。"
        )
        if reused:
            summary += f" 复用了历史记忆 {', '.join(reused)}。"
        else:
            summary += " 未命中可复用记忆，本轮产出将沉淀为新经验。"
        strategy = self._strategy_from_artifact(artifact)
        full_memory_summary = f"{summary} 可复用策略：{strategy}"
        state = self.states.create(full_memory_summary, producer=self.name, task_id=ctx.task_id)
        memory = self.memory.write(
            source_agent=self.name,
            task_topic=ctx.topic,
            summary=full_memory_summary,
            tags=ctx.tags + [str(artifact.get("kind"))],
            evidence=evidence_titles + [str(execution["stdout"])],
            vector=state.vector,
        )
        return {
            "summary": summary,
            "strategy": strategy,
            "memory_id": memory.memory_id,
            "state": state,
        }

    def _strategy_from_artifact(self, artifact: dict[str, object]) -> str:
        kind = artifact.get("kind")
        if kind == "deployment_script":
            return "部署类任务优先固定 Python 环境、依赖安装命令和运行检查项。"
        if kind == "systemd_unit":
            return "服务化任务复用 systemd 模板，并固定 daemon-reload、enable、start、status 检查链。"
        if kind == "csv_analysis":
            return "数据分析任务复用字段质量检查、缺失值统计、重复行检测和摘要模板。"
        return "通用任务复用规划、检索、执行、总结和记忆写入的流水线。"

    def verbose_summary_text(self, ctx: AgentContext, result: dict[str, object]) -> str:
        return (
            f"总结智能体完成任务 {ctx.task_id} 的最终汇总。结论：{result['summary']}。"
            f"沉淀的可复用策略是：{result['strategy']}。"
            f"新写入的共享记忆编号为 {result['memory_id']}。"
            "这条记忆包含来源智能体、创建时间、任务主题、摘要描述、标签、证据链和向量状态，"
            "后续关联任务可通过关键词、标签或语义相似度直接复用。"
        )

