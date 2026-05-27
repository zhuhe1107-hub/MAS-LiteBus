from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def improvement(base: float, current: float) -> float:
    if base <= 0:
        return 0.0
    return max(0.0, 1.0 - current / base)


def build_summary(results: dict[str, Any]) -> dict[str, Any]:
    text = results.get("text", {}).get("metrics", {})
    protocol = results.get("protocol", {}).get("metrics", {})
    return {
        "text": text,
        "protocol": protocol,
        "improvements": {
            "token_saving": improvement(text.get("estimated_tokens", 0), protocol.get("estimated_tokens", 0)),
            "text_char_saving": improvement(text.get("text_chars", 0), protocol.get("protocol_chars", 0)),
            "latency_improvement": improvement(text.get("latency_seconds", 0), protocol.get("latency_seconds", 0)),
            "retrieval_reduction": improvement(text.get("retrieval_count", 0), protocol.get("retrieval_count", 0)),
            "message_reduction": improvement(text.get("message_count", 0), protocol.get("message_count", 0)),
        },
    }


def write_report(results: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = build_summary(results)
    (out / "benchmark_summary.json").write_text(
        json.dumps({"results": results, "summary": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "benchmark_report.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    text = summary["text"]
    protocol = summary["protocol"]
    imp = summary["improvements"]
    rows = [
        ("Agent 消息数", text.get("message_count", 0), protocol.get("message_count", 0), pct(imp["message_reduction"])),
        ("文本字符数", text.get("text_chars", 0), protocol.get("protocol_chars", 0), pct(imp["text_char_saving"])),
        ("估算 token", text.get("estimated_tokens", 0), protocol.get("estimated_tokens", 0), pct(imp["token_saving"])),
        ("状态传递次数", text.get("state_transfer_count", 0), protocol.get("state_transfer_count", 0), "新增"),
        ("embedding 数据量 bytes", text.get("state_bytes", 0), protocol.get("state_bytes", 0), "新增"),
        ("总耗时秒", text.get("latency_seconds", 0), protocol.get("latency_seconds", 0), pct(imp["latency_improvement"])),
        ("记忆命中率", text.get("memory_hit_rate", 0), protocol.get("memory_hit_rate", 0), f"+{pct(protocol.get('memory_hit_rate', 0))}"),
        ("检索次数", text.get("retrieval_count", 0), protocol.get("retrieval_count", 0), pct(imp["retrieval_reduction"])),
    ]
    table = "\n".join(f"| {name} | {base} | {curr} | {gain} |" for name, base, curr, gain in rows)
    return f"""# MAS-LiteBus Benchmark Report

## 对比结果

| 指标 | 纯文本模式 | 结构化协议模式 | 提升 |
|---|---:|---:|---:|
{table}

## 结论

结构化协议模式将 Agent 之间的大段自然语言传递压缩为动作、参数、结果、能力、状态引用和记忆引用。
系统通过哈希语义向量实现非文本状态传递，并在连续任务中使用共享记忆减少重复检索。

本报告由 `scripts/run_benchmark.py` 自动生成。
"""

