from __future__ import annotations

from pathlib import Path
from typing import Any

from mas_litebus.eval.accuracy import compute_accuracy
from mas_litebus.eval.report import write_report
from mas_litebus.llm.base import LLMBackend
from mas_litebus.runtime.engine import MultiAgentRuntime
from mas_litebus.runtime.ipc_engine import IPCMultiAgentRuntime
from mas_litebus.runtime.task import load_tasks


SUPPORTED_MODES = ("text", "text_v2", "text_with_memory", "protocol_no_memory", "protocol", "protocol_ipc")


def _resolve_modes(mode: str) -> list[str]:
    if mode == "all":
        return list(SUPPORTED_MODES)
    if mode == "both":
        # Backwards-compatible alias for the historical text/protocol pair.
        return ["text", "protocol"]
    if mode == "ablation":
        # Six modes that together support causation analysis (text<->memory<->protocol<->ipc).
        return list(SUPPORTED_MODES)
    if mode in SUPPORTED_MODES:
        return [mode]
    raise ValueError(
        f"unknown mode {mode!r}; expected one of: {', '.join(SUPPORTED_MODES)}, both, ablation, all"
    )


def _wipe_sqlite_files(output: Path, name: str) -> None:
    # SQLite WAL leaves -wal/-shm sibling files; clear them too so each run
    # starts with a clean memory database.
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = output / f"memory_{name}.sqlite3{suffix}"
        if candidate.exists():
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def _run_one(
    mode: str,
    memory_path: Path,
    tasks: list[Any],
    llm: LLMBackend | None = None,
) -> dict[str, Any]:
    if mode == "protocol_ipc":
        # Workers in IPC mode cannot share an in-process LLM client across fork,
        # so the IPC runtime keeps its deterministic Agent implementations.
        runtime: Any = IPCMultiAgentRuntime(memory_path=memory_path)
    else:
        runtime = MultiAgentRuntime(mode=mode, memory_path=memory_path, llm=llm)
    try:
        return runtime.run_tasks(tasks)
    finally:
        runtime.close()


def _aggregate_metrics(runs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not runs:
        return {}, {}
    keys: set[str] = set()
    for run in runs:
        keys.update(run.keys())
    means: dict[str, Any] = {}
    stds: dict[str, Any] = {}
    for key in keys:
        values = [run.get(key) for run in runs]
        # Strings or mixed values: keep first occurrence, std=0.
        try:
            nums = [float(v) for v in values if v is not None]
        except (TypeError, ValueError):
            means[key] = values[0]
            stds[key] = 0
            continue
        if not nums:
            means[key] = 0
            stds[key] = 0
            continue
        mean = sum(nums) / len(nums)
        variance = sum((x - mean) ** 2 for x in nums) / len(nums)
        std = variance ** 0.5
        if all(isinstance(v, int) for v in values if v is not None) and float(int(mean)) == mean:
            means[key] = int(mean)
        else:
            means[key] = round(mean, 6)
        stds[key] = round(std, 6)
    return means, stds


def run_benchmark(
    mode: str,
    task_path: str | Path,
    output_dir: str | Path,
    rounds: int | None = None,
    repeat: int = 1,
    llm: LLMBackend | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(task_path, rounds=rounds)
    results: dict[str, Any] = {}

    for item in _resolve_modes(mode):
        memory_path = output / f"memory_{item}.sqlite3"
        runs: list[dict[str, Any]] = []
        last_full: dict[str, Any] | None = None
        for _ in range(max(1, repeat)):
            _wipe_sqlite_files(output, item)
            last_full = _run_one(item, memory_path, tasks, llm=llm)
            runs.append(last_full["metrics"])
        means, stds = _aggregate_metrics(runs)
        results[item] = {
            "mode": item,
            "metrics": means,
            "metrics_std": stds,
            "runs": len(runs),
            "tasks": last_full["tasks"] if last_full else [],
            "transcript_sample": last_full.get("transcript_sample", []) if last_full else [],
        }
        # Memory retrieval accuracy is meaningful only for modes that actually
        # query shared memory. text / text_v2 / protocol_no_memory always
        # return memory_refs=[] and would score 0 by construction; we skip them.
        if item in {"text_with_memory", "protocol", "protocol_ipc"} and last_full:
            results[item]["memory_accuracy"] = compute_accuracy(last_full["tasks"], tasks)

    if len(results) >= 2:
        write_report(results, output)
    return results

