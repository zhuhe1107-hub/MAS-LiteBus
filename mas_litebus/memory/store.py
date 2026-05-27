from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas_litebus.runtime.protocol import new_id, now_ts
from mas_litebus.state.embedding import HashEmbedding, cosine


@dataclass
class MemoryUnit:
    memory_id: str
    source_agent: str
    created_at: str
    task_topic: str
    summary: str
    tags: list[str]
    evidence: list[str]
    vector: list[float]
    reuse_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "source_agent": self.source_agent,
            "created_at": self.created_at,
            "task_topic": self.task_topic,
            "summary": self.summary,
            "tags": self.tags,
            "evidence": self.evidence,
            "reuse_count": self.reuse_count,
        }


class SharedMemoryStore:
    def __init__(self, path: str | Path, embedder: HashEmbedding | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or HashEmbedding()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        # WAL allows the coordinator process and any worker subprocesses to read
        # concurrently while the summarizer worker writes (single-writer per
        # SQLite WAL semantics). The pragma is persisted on the file, so it
        # only needs to succeed once across the lifetime of the database.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL").fetchall()
        except sqlite3.DatabaseError:
            pass
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                source_agent TEXT NOT NULL,
                created_at TEXT NOT NULL,
                task_topic TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                reuse_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def write(
        self,
        source_agent: str,
        task_topic: str,
        summary: str,
        tags: list[str],
        evidence: list[str],
        vector: list[float] | None = None,
    ) -> MemoryUnit:
        vec = vector if vector is not None else self.embedder.encode(" ".join([task_topic, summary, *tags]))
        unit = MemoryUnit(
            memory_id=new_id("mem"),
            source_agent=source_agent,
            created_at=now_ts(),
            task_topic=task_topic,
            summary=summary,
            tags=tags,
            evidence=evidence,
            vector=vec,
        )
        self.conn.execute(
            """
            INSERT INTO memories (
                memory_id, source_agent, created_at, task_topic, summary,
                tags_json, evidence_json, vector_json, reuse_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unit.memory_id,
                unit.source_agent,
                unit.created_at,
                unit.task_topic,
                unit.summary,
                json.dumps(unit.tags, ensure_ascii=False),
                json.dumps(unit.evidence, ensure_ascii=False),
                json.dumps(unit.vector),
                unit.reuse_count,
            ),
        )
        self.conn.commit()
        return unit

    def _row_to_unit(self, row: sqlite3.Row) -> MemoryUnit:
        return MemoryUnit(
            memory_id=row["memory_id"],
            source_agent=row["source_agent"],
            created_at=row["created_at"],
            task_topic=row["task_topic"],
            summary=row["summary"],
            tags=json.loads(row["tags_json"]),
            evidence=json.loads(row["evidence_json"]),
            vector=json.loads(row["vector_json"]),
            reuse_count=row["reuse_count"],
        )

    def all(self) -> list[MemoryUnit]:
        rows = self.conn.execute("SELECT * FROM memories ORDER BY created_at").fetchall()
        return [self._row_to_unit(row) for row in rows]

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
        vector: list[float] | None = None,
        top_k: int = 3,
        min_score: float = 0.12,
    ) -> list[tuple[MemoryUnit, float, str]]:
        query_terms = set(query.lower().split())
        tag_set = {tag.lower() for tag in tags or []}
        query_vector = vector if vector is not None else self.embedder.encode(query)
        scored: list[tuple[MemoryUnit, float, str]] = []
        for unit in self.all():
            haystack = " ".join([unit.task_topic, unit.summary, " ".join(unit.tags)]).lower()
            keyword_score = sum(1 for term in query_terms if term and term in haystack) * 0.08
            tag_score = len(tag_set.intersection({tag.lower() for tag in unit.tags})) * 0.12
            semantic_score = cosine(query_vector, unit.vector)
            score = semantic_score + keyword_score + tag_score
            reason = "semantic"
            if tag_score > 0:
                reason = "tag"
            if keyword_score > 0:
                reason = "keyword"
            if score >= min_score:
                scored.append((unit, score, reason))
        scored.sort(key=lambda item: item[1], reverse=True)
        hits = scored[:top_k]
        for unit, _, _ in hits:
            self.conn.execute(
                "UPDATE memories SET reuse_count = reuse_count + 1 WHERE memory_id = ?",
                (unit.memory_id,),
            )
        self.conn.commit()
        return hits

