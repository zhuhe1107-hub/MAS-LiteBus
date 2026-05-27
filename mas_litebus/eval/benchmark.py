from __future__ import annotations

from pathlib import Path
from typing import Any

from mas_litebus.eval.report import write_report
from mas_litebus.runtime.engine import MultiAgentRuntime
from mas_litebus.runtime.task import load_tasks


def run_benchmark(
    mode: str,
    task_path: str | Path,
    output_dir: str | Path,
    rounds: int | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(task_path, rounds=rounds)
    results: dict[str, Any] = {}

    modes = ["text", "protocol"] if mode == "both" else [mode]
    for item in modes:
        memory_path = output / f"memory_{item}.sqlite3"
        if memory_path.exists():
            memory_path.unlink()
        runtime = MultiAgentRuntime(mode=item, memory_path=memory_path)
        try:
            results[item] = runtime.run_tasks(tasks)
        finally:
            runtime.close()

    if "text" in results and "protocol" in results:
        write_report(results, output)
    return results

