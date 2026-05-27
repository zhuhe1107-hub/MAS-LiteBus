"""Prompt templates for each Agent role under both wire modes.

The text-mode prompt embeds a verbose natural-language brief that mirrors the
cumulative NL transcript a non-protocol multi-agent system would carry.
The protocol-mode prompt embeds the same content as compact JSON, so the
LLM-reported `prompt_tokens` directly captures the wire-format savings.
"""

from __future__ import annotations

import json
from typing import Any


def _json_compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = (
    "你是多智能体系统中的 Planner. 收到一个任务时, 你需要把任务拆解成有序步骤. "
    "步骤的 action 取值必须来自集合 {retrieve, reuse_memory, execute, summarize}, "
    "通常顺序是 retrieve → execute → summarize; 当历史可复用记忆非空时, 在 retrieve 后插入一步 reuse_memory.\n\n"
    "**严格输出格式**: 一行 JSON 对象, 不要 markdown 代码块, 不要前置或后置任何说明文字. 例如:\n"
    "{\"steps\":[\"retrieve\",\"execute\",\"summarize\"],\"reasoning\":\"简短理由\"}\n"
    "字段说明: steps 是字符串列表 (action 取值从上述集合), reasoning 是一句话理由."
)


def planner_user_prompt(ctx: Any, memory_refs: list[str], memory_summaries: list[str], mode: str) -> str:
    if mode == "text":
        memo_block = (
            "可复用的历史记忆 (来自上一轮任务总结):\n"
            + "\n".join(f"  - {ref}: {summary}" for ref, summary in zip(memory_refs, memory_summaries))
            if memory_refs
            else "(本轮没有可复用的历史记忆)"
        )
        return (
            f"任务 ID: {ctx.task_id}\n"
            f"主题: {ctx.topic}\n"
            f"用户请求 (完整自然语言):\n{ctx.request}\n"
            f"标签: {', '.join(ctx.tags)}\n\n"
            f"{memo_block}\n\n"
            "请根据以上完整自然语言上下文, 给出 JSON 计划. 注意 reasoning 字段要解释为什么这样安排步骤."
        )
    return (
        "task=" + _json_compact({
            "id": ctx.task_id,
            "topic": ctx.topic,
            "request": ctx.request,
            "tags": ctx.tags,
            "memory_refs": memory_refs,
        })
    )


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

RETRIEVER_SYSTEM = (
    "你是多智能体系统中的 Retriever. 给定一个任务和一个候选证据列表 (corpus 标题 + 标签), "
    "你需要选出最相关的 1-3 条证据, 并简要给出选中理由.\n\n"
    "**严格输出格式**: 一行 JSON 对象, 不要 markdown 代码块, 不要前后任何说明文字. 例如:\n"
    "{\"selected_titles\":[\"openEuler Python service deployment\",\"systemd service checklist\"],\"reasoning\":\"简短理由\"}\n"
    "selected_titles 里的标题必须严格匹配候选证据列表中提供的标题字符串, 不要造新标题."
)


def retriever_user_prompt(
    ctx: Any,
    corpus_candidates: list[dict[str, Any]],
    memory_hits: list[dict[str, Any]],
    plan_payload: dict[str, Any] | str,
    mode: str,
) -> str:
    cand_block = "\n".join(
        f"  - {item['title']} (标签: {', '.join(item['tags'])})" for item in corpus_candidates
    )
    if mode == "text":
        mem_block = (
            "Planner 已经给出的自然语言计划:\n" + (plan_payload if isinstance(plan_payload, str) else _json_compact(plan_payload))
            + "\n\n命中的历史记忆 (NL 形式累积):\n"
            + "\n".join(f"  - {h.get('memory_id')}: {h.get('summary')}" for h in memory_hits)
        )
        return (
            f"任务: {ctx.topic}\n"
            f"用户请求: {ctx.request}\n"
            f"标签: {', '.join(ctx.tags)}\n\n"
            f"{mem_block}\n\n"
            f"候选证据列表:\n{cand_block}\n\n"
            "请挑选最相关的 1-3 条."
        )
    return _json_compact({
        "task": {"id": ctx.task_id, "topic": ctx.topic, "tags": ctx.tags, "request": ctx.request},
        "plan_refs": plan_payload if isinstance(plan_payload, dict) else {},
        "memory_hits": [{"id": h.get("memory_id"), "tags": h.get("tags")} for h in memory_hits],
        "candidates": [{"title": c["title"], "tags": c["tags"]} for c in corpus_candidates],
    })


# ---------------------------------------------------------------------------
# Executor (CodeAct)
# ---------------------------------------------------------------------------

EXECUTOR_SYSTEM = (
    "你是多智能体系统中的 Executor. 你需要根据任务和检索证据写一小段 Python 代码, "
    "代码只能用 Python 标准库 (不要 pandas/numpy/requests). 代码末尾必须 print 一个 JSON 字典作为最终产物. "
    "产物字典里 kind 字段必须从这五个选一个: deployment_script / systemd_unit / csv_analysis / report_template / generic_checklist.\n\n"
    "**严格按以下格式输出, 不要任何额外文字, 不要 markdown 代码块标记**:\n\n"
    "{\"artifact_kind\": \"<five_choices_above>\", \"reasoning\": \"<short reasoning>\"}\n"
    "---CODE---\n"
    "<your raw Python source code, no fences, no escaping>\n\n"
    "JSON 头部只包含 artifact_kind 和 reasoning 两个字段; 真正的 Python 代码写在 ---CODE--- 之后, "
    "不需要塞进 JSON 字符串里, 也不需要转义引号. 末尾 print(json.dumps({...})) 把产物字典打印出来供沙箱捕获."
)


def executor_user_prompt(
    ctx: Any,
    evidence_titles: list[str],
    retr_payload: dict[str, Any] | str,
    mode: str,
) -> str:
    if mode == "text":
        return (
            f"任务: {ctx.topic}\n"
            f"用户请求 (完整 NL): {ctx.request}\n"
            f"标签: {', '.join(ctx.tags)}\n\n"
            f"Retriever 已经选出的证据 (NL 描述):\n  - "
            + "\n  - ".join(evidence_titles)
            + "\n\n上游累积上下文:\n"
            + (retr_payload if isinstance(retr_payload, str) else _json_compact(retr_payload))
            + "\n\n请写出 Python 代码并 print 最终 JSON 产物."
        )
    return _json_compact({
        "task": {"id": ctx.task_id, "topic": ctx.topic, "tags": ctx.tags},
        "evidence_titles": evidence_titles,
        "retrieve_state_ref": retr_payload if isinstance(retr_payload, dict) else {},
    })


# ---------------------------------------------------------------------------
# Summarizer
# ---------------------------------------------------------------------------

SUMMARIZER_SYSTEM = (
    "你是多智能体系统中的 Summarizer. 综合任务、证据、执行产物和可复用历史记忆, "
    "输出最终总结和一句可复用策略 (供以后类似任务复用).\n\n"
    "**严格输出格式**: 一行 JSON 对象, 不要 markdown 代码块, 不要前后任何说明文字. 例如:\n"
    "{\"summary\":\"任务最终结论的一段中文总结\",\"strategy\":\"未来类似任务可直接复用的一句话策略\",\"tags\":[\"tag1\",\"tag2\"]}\n"
    "summary 是面向用户的最终交付文字, strategy 是可在共享记忆库中检索复用的关键经验."
)


def summarizer_user_prompt(
    ctx: Any,
    evidence_titles: list[str],
    artifact_kind: str,
    execution_stdout: str,
    memory_hits: list[dict[str, Any]],
    mode: str,
) -> str:
    if mode == "text":
        memo = "\n".join(f"  - {h.get('memory_id')}: {h.get('summary')}" for h in memory_hits) or "  (无)"
        return (
            f"任务: {ctx.topic}\n"
            f"请求 (完整 NL): {ctx.request}\n"
            f"标签: {', '.join(ctx.tags)}\n\n"
            f"证据标题:\n  - " + "\n  - ".join(evidence_titles) + "\n\n"
            f"Executor 产出类型: {artifact_kind}\n"
            f"执行 stdout (节选):\n{execution_stdout[:400]}\n\n"
            f"命中的可复用历史记忆:\n{memo}\n\n"
            "请生成最终总结、可复用策略和未来检索可能用到的标签集合."
        )
    return _json_compact({
        "task": {"id": ctx.task_id, "topic": ctx.topic, "tags": ctx.tags},
        "evidence_titles": evidence_titles,
        "artifact_kind": artifact_kind,
        "stdout_excerpt": execution_stdout[:200],
        "memory_refs": [h.get("memory_id") for h in memory_hits],
    })
