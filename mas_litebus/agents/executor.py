from __future__ import annotations

import contextlib
import io
from statistics import mean

from mas_litebus.agents.base import AgentContext, BaseAgent
from mas_litebus.runtime.protocol import Capability


SAMPLE_ROWS = [
    {"city": "A", "sales": 120, "cost": 80, "missing": ""},
    {"city": "B", "sales": 240, "cost": 160, "missing": "ok"},
    {"city": "C", "sales": 90, "cost": 95, "missing": ""},
    {"city": "A", "sales": 120, "cost": 80, "missing": ""},
]


class ExecutorAgent(BaseAgent):
    name = "executor"

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "python_exec",
                "code:string",
                "stdout:string,artifact:dict",
                "Run a small deterministic Python snippet in a restricted namespace.",
            ),
            Capability(
                "template_render",
                "topic:string,tags:list",
                "artifact:dict",
                "Generate deployment, systemd or data analysis artifacts.",
            ),
        ]

    def execute(self, ctx: AgentContext, evidence: list[dict[str, object]], memory_refs: list[str]) -> dict[str, object]:
        text = " ".join([ctx.topic, ctx.request, " ".join(ctx.tags)]).lower()
        if "csv" in text or "数据" in text or "分析" in text:
            artifact = self._run_csv_analysis()
        elif "systemd" in text:
            artifact = self._systemd_template()
        elif "脚本" in text or "部署" in text or "python web" in text:
            artifact = self._deployment_script()
        else:
            artifact = {
                "kind": "generic_checklist",
                "items": [
                    "复用已有任务拆解流程",
                    "优先检查输入、环境、执行日志和输出格式",
                    "将可复用策略写入共享记忆",
                ],
            }
        artifact["used_memory_refs"] = memory_refs
        artifact["evidence_titles"] = [str(item["title"]) for item in evidence]
        return {
            "status": "ok",
            "artifact": artifact,
            "stdout": self._stdout_for_artifact(artifact),
        }

    def _deployment_script(self) -> dict[str, object]:
        return {
            "kind": "deployment_script",
            "commands": [
                "sudo dnf install -y python3 python3-pip sqlite",
                "python3 -m venv .venv",
                "source .venv/bin/activate",
                "pip install -r requirements.txt",
                "python app.py",
            ],
            "checks": ["python3 --version", "pip --version", "ss -lntp"],
        }

    def _systemd_template(self) -> dict[str, object]:
        return {
            "kind": "systemd_unit",
            "unit": "[Unit]\nDescription=Python Web Service\nAfter=network.target\n\n[Service]\nWorkingDirectory=/opt/app\nExecStart=/opt/app/.venv/bin/python app.py\nRestart=always\n\n[Install]\nWantedBy=multi-user.target",
            "checks": [
                "sudo systemctl daemon-reload",
                "sudo systemctl enable demo.service",
                "sudo systemctl start demo.service",
                "sudo systemctl status demo.service",
            ],
        }

    def _run_csv_analysis(self) -> dict[str, object]:
        code = """
rows = SAMPLE_ROWS
sales = [row["sales"] for row in rows]
cost = [row["cost"] for row in rows]
missing_cells = sum(1 for row in rows for value in row.values() if value == "")
duplicates = len(rows) - len({tuple(sorted(row.items())) for row in rows})
result = {
    "row_count": len(rows),
    "avg_sales": round(mean(sales), 2),
    "avg_margin": round(mean([s - c for s, c in zip(sales, cost)]), 2),
    "missing_cells": missing_cells,
    "duplicates": duplicates,
}
print(result)
"""
        stdout = io.StringIO()
        namespace = {"SAMPLE_ROWS": SAMPLE_ROWS, "mean": mean, "result": None}
        with contextlib.redirect_stdout(stdout):
            exec(code, {"__builtins__": {"len": len, "sum": sum, "round": round, "print": print, "sorted": sorted, "tuple": tuple, "zip": zip}}, namespace)
        return {
            "kind": "csv_analysis",
            "code": code.strip(),
            "result": namespace["result"],
            "stdout": stdout.getvalue().strip(),
            "cleaning_steps": ["填补缺失值", "删除重复行", "检查 sales/cost 范围", "输出处理日志"],
        }

    def _stdout_for_artifact(self, artifact: dict[str, object]) -> str:
        if artifact["kind"] == "csv_analysis":
            return str(artifact.get("stdout", ""))
        return f"generated {artifact['kind']} with {len(artifact)} fields"

    def verbose_execution_text(self, ctx: AgentContext, result: dict[str, object]) -> str:
        return (
            f"执行智能体处理任务 {ctx.task_id}。执行状态：{result['status']}。"
            f"执行输出：{result['stdout']}。生成的结构化产物为：{result['artifact']}。"
            "在文本模式中，这些命令、检查项、执行输出、证据标题和记忆引用都会完整展开，"
            "以便下游总结智能体无需读取外部状态即可理解整个执行过程。"
        )

