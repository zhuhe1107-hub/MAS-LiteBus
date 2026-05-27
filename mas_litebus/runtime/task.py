from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Task:
    task_id: str
    group: str
    topic: str
    request: str
    tags: list[str]
    gold_prior_task_ids: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Task":
        return cls(
            task_id=str(data["task_id"]),
            group=str(data["group"]),
            topic=str(data["topic"]),
            request=str(data["request"]),
            tags=[str(item) for item in data["tags"]],
            gold_prior_task_ids=[str(item) for item in (data.get("gold_prior_task_ids") or [])],
        )


def load_tasks(path: str | Path, rounds: int | None = None) -> list[Task]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = [Task.from_dict(item) for item in data["tasks"]]
    if rounds is not None:
        return tasks[:rounds]
    return tasks

