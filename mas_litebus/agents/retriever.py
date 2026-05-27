from __future__ import annotations

import hashlib

from mas_litebus.agents.base import AgentContext, BaseAgent
from mas_litebus.eval.metrics import Metrics
from mas_litebus.llm.base import LLMBackend
from mas_litebus.llm.parse import extract_json
from mas_litebus.llm.prompts import RETRIEVER_SYSTEM, retriever_user_prompt
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.runtime.protocol import Capability
from mas_litebus.state.embedding import StateStore


CORPUS = [
    {
        "title": "openEuler Python service deployment",
        "tags": ["openEuler", "Python", "deployment", "systemd"],
        "text": "openEuler 24.03 上部署 Python Web 服务时，推荐使用 dnf 安装 python3、python3-pip 和 sqlite，创建 venv 固定依赖版本，并通过 systemd 管理长期运行服务。",
    },
    {
        "title": "systemd service checklist",
        "tags": ["systemd", "deployment", "testing"],
        "text": "systemd 单元通常包含 WorkingDirectory、ExecStart、Restart、Environment 和 User 字段，部署后使用 systemctl daemon-reload、enable、start、status 验证。",
    },
    {
        "title": "Python dependency troubleshooting",
        "tags": ["Python", "venv", "dependency"],
        "text": "Python 部署排障应检查解释器版本、虚拟环境路径、pip 源、依赖锁定文件、端口占用和日志输出，优先保证可重复安装。",
    },
    {
        "title": "CSV quality analysis",
        "tags": ["CSV", "data", "analysis"],
        "text": "CSV 数据质量分析应统计字段类型、缺失率、重复行、数值范围、异常值和分类字段基数，并输出清洗建议。",
    },
    {
        "title": "Data cleaning strategy",
        "tags": ["CSV", "Python", "cleaning"],
        "text": "数据清洗代码应包含读取、空值处理、重复删除、类型转换、异常值标记和处理日志，保证每一步可追踪。",
    },
    {
        "title": "Report generation pattern",
        "tags": ["report", "summary", "template"],
        "text": "自动报告建议包含任务目标、输入数据、执行步骤、关键发现、证据链、风险和后续建议，便于跨任务复用。",
    },
]


class RetrieverAgent(BaseAgent):
    name = "retriever"

    def __init__(
        self,
        memory: SharedMemoryStore,
        states: StateStore,
        llm: LLMBackend | None = None,
    ) -> None:
        self.memory = memory
        self.states = states
        self.llm = llm

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "keyword_search",
                "query:string,tags:list",
                "items:list",
                "Search local corpus by keywords and tags.",
            ),
            Capability(
                "semantic_memory_search",
                "state_ref:string",
                "memory_hits:list",
                "Search shared memory with embedding similarity.",
            ),
        ]

    def retrieve(
        self,
        ctx: AgentContext,
        use_memory: bool,
        skip_local: bool = False,
        *,
        llm_mode: str | None = None,
        plan_payload: object = None,
        metrics: Metrics | None = None,
    ) -> dict[str, object]:
        query_vector = self.states.embedder.encode(" ".join([ctx.topic, ctx.request, *ctx.tags]))
        memory_hits_raw = []
        if use_memory:
            memory_hits_raw = self.memory.search(ctx.request, ctx.tags, query_vector, top_k=3)

        memory_hits = [
            {
                "memory_id": unit.memory_id,
                "score": round(score, 4),
                "reason": reason,
                "summary": unit.summary,
                "tags": unit.tags,
            }
            for unit, score, reason in memory_hits_raw
        ]

        evidence = []
        if not skip_local:
            self._simulate_local_index_scan(ctx)
            evidence = self._rank_corpus(ctx)
            # Optional LLM rerank/selection. The scoring above runs first so
            # the LLM never sees the full corpus — it picks from a 3-item
            # shortlist, keeping the prompt length predictable.
            if self.llm is not None and llm_mode in {"text", "protocol"} and evidence:
                try:
                    evidence = self._llm_select(ctx, evidence, memory_hits, plan_payload, llm_mode, metrics)
                except Exception:
                    if metrics is not None:
                        metrics.llm_parse_failures += 1
                    # keep keyword-scored evidence

        evidence_text = "\n".join(item["text"] for item in evidence)
        memory_text = "\n".join(hit["summary"] for hit in memory_hits)
        state = self.states.create(
            "\n".join([ctx.topic, evidence_text, memory_text]),
            producer=self.name,
            task_id=ctx.task_id,
        )
        return {
            "items": evidence,
            "memory_hits": memory_hits,
            "state": state,
        }

    def _rank_corpus(self, ctx: AgentContext) -> list[dict[str, object]]:
        scored = []
        task_words = set((ctx.topic + " " + ctx.request + " " + " ".join(ctx.tags)).lower().split())
        tag_set = {tag.lower() for tag in ctx.tags}
        for item in CORPUS:
            haystack = " ".join([item["title"], item["text"], " ".join(item["tags"])]).lower()
            keyword_score = sum(1 for word in task_words if word and word in haystack)
            tag_score = len(tag_set.intersection({tag.lower() for tag in item["tags"]})) * 3
            score = keyword_score + tag_score
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:3]]

    def _llm_select(
        self,
        ctx: AgentContext,
        candidates: list[dict[str, object]],
        memory_hits: list[dict[str, object]],
        plan_payload: object,
        llm_mode: str,
        metrics: Metrics | None,
    ) -> list[dict[str, object]]:
        assert self.llm is not None
        user = retriever_user_prompt(ctx, candidates, memory_hits, plan_payload, llm_mode)
        resp = self.llm.chat(RETRIEVER_SYSTEM, user, temperature=0.0, max_tokens=400)
        if metrics is not None:
            metrics.record_llm(resp)
        data = extract_json(resp.text)
        selected_titles = [str(t) for t in data.get("selected_titles", [])]
        by_title = {str(c["title"]): c for c in candidates}
        chosen = [by_title[t] for t in selected_titles if t in by_title][:3]
        return chosen or candidates

    def verbose_retrieval_text(self, ctx: AgentContext, result: dict[str, object]) -> str:
        items = result["items"]
        hits = result["memory_hits"]
        evidence_text = "\n".join(
            f"证据 {idx + 1}：{item['title']}。内容：{item['text']}。标签：{', '.join(item['tags'])}。"
            for idx, item in enumerate(items)
        )
        hit_text = "\n".join(
            f"历史记忆 {idx + 1}：{hit['memory_id']}，命中原因 {hit['reason']}，摘要：{hit['summary']}。"
            for idx, hit in enumerate(hits)
        )
        if not hit_text:
            hit_text = "本轮没有可复用历史记忆，因此需要完整检索和重新整理证据。"
        return (
            f"针对任务 {ctx.task_id}“{ctx.topic}”，检索智能体完成了资料检索。"
            f"{evidence_text}{hit_text}"
            "为了确保下游智能体不丢失语义，文本模式还会重复转述任务目标、约束条件、"
            f"检索关键词 {ctx.request}、标签集合 {ctx.tags}、证据选择理由、后续执行建议和风险提示。"
            "这些自然语言说明在多轮协作中会反复出现，是文本基线通信开销较高的主要来源。"
            "这些内容将在文本模式中作为完整上下文传递给执行和总结智能体。"
        )

    def _simulate_local_index_scan(self, ctx: AgentContext) -> None:
        seed = " ".join([ctx.task_id, ctx.topic, ctx.request, *ctx.tags]).encode("utf-8")
        digest = seed
        for _ in range(15000):
            digest = hashlib.blake2b(digest, digest_size=32).digest()
