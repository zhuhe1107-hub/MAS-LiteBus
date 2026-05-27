"""Memory-retrieval accuracy metrics.

Per task, the dataset records `gold_prior_task_ids` — the historical tasks a
correct memory-reuse system *should* hit. After a run, each task has produced
a new memory_id and (in memory-enabled modes) a list of `memory_refs` that
were actually retrieved. We translate gold task ids to gold memory ids via
the run's task->memory mapping, then score precision / recall / F1 / MRR.
"""

from __future__ import annotations

from typing import Any

from mas_litebus.runtime.task import Task


def _load_gold(tasks: list[Task]) -> dict[str, list[str]]:
    return {task.task_id: list(task.gold_prior_task_ids) for task in tasks}


def compute_accuracy(
    task_results: list[dict[str, Any]],
    tasks: list[Task],
) -> dict[str, Any]:
    """Score memory retrieval against gold prior-task annotations.

    Cold-start tasks (those with empty `gold_prior_task_ids`) are excluded
    from the macro averages so the score reflects only the cases where some
    historical memory was actually expected to be reused.
    """
    gold_map = _load_gold(tasks)
    task_to_mem: dict[str, str] = {}
    for tr in task_results:
        tid = str(tr.get("task_id", ""))
        mid = str(tr.get("new_memory_id", "") or "")
        if tid and mid:
            task_to_mem[tid] = mid

    per_task: list[dict[str, Any]] = []
    p_sum = 0.0
    r_sum = 0.0
    f1_sum = 0.0
    mrr_sum = 0.0
    counted = 0

    for tr in task_results:
        tid = str(tr.get("task_id", ""))
        gold_task_ids = gold_map.get(tid, [])
        retrieved_list = [str(m) for m in tr.get("memory_refs", []) if m]
        gold_memory_ids = [task_to_mem.get(g, "") for g in gold_task_ids]
        gold_memory_set = {m for m in gold_memory_ids if m}

        if not gold_memory_set:
            per_task.append(
                {
                    "task_id": tid,
                    "gold_prior_task_ids": gold_task_ids,
                    "gold_memory_ids": [],
                    "retrieved": retrieved_list,
                    "skipped": True,
                    "reason": "cold start (no gold prior)" if not gold_task_ids else "gold prior had no memory",
                }
            )
            continue

        retrieved_set = set(retrieved_list)
        tp = len(retrieved_set & gold_memory_set)
        precision = tp / len(retrieved_set) if retrieved_set else 0.0
        recall = tp / len(gold_memory_set)
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        rr = 0.0
        for rank, mem in enumerate(retrieved_list, start=1):
            if mem in gold_memory_set:
                rr = 1.0 / rank
                break

        per_task.append(
            {
                "task_id": tid,
                "gold_prior_task_ids": gold_task_ids,
                "gold_memory_ids": sorted(gold_memory_set),
                "retrieved": retrieved_list,
                "tp": tp,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "reciprocal_rank": round(rr, 4),
                "skipped": False,
            }
        )
        p_sum += precision
        r_sum += recall
        f1_sum += f1
        mrr_sum += rr
        counted += 1

    macro: dict[str, Any]
    if counted:
        macro = {
            "scored_tasks": counted,
            "total_tasks_with_gold": sum(1 for tr in task_results if gold_map.get(str(tr.get("task_id", "")), [])),
            "macro_precision_at_3": round(p_sum / counted, 4),
            "macro_recall_at_3": round(r_sum / counted, 4),
            "macro_f1_at_3": round(f1_sum / counted, 4),
            "mrr": round(mrr_sum / counted, 4),
        }
    else:
        macro = {
            "scored_tasks": 0,
            "total_tasks_with_gold": 0,
            "macro_precision_at_3": 0.0,
            "macro_recall_at_3": 0.0,
            "macro_f1_at_3": 0.0,
            "mrr": 0.0,
        }
    return {"summary": macro, "per_task": per_task}
