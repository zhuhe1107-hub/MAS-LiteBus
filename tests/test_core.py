from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mas_litebus.eval.benchmark import run_benchmark
from mas_litebus.memory.store import SharedMemoryStore
from mas_litebus.runtime.protocol import ProtocolMessage
from mas_litebus.state.embedding import HashEmbedding, StateStore, cosine


ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def test_protocol_message_contains_required_fields(self) -> None:
        msg = ProtocolMessage(
            action="retrieve",
            sender="planner",
            receiver="retriever",
            task_id="T1",
            params={"query": "openEuler"},
            capabilities=["keyword_search"],
            state_refs=["state-1"],
        )
        data = msg.to_dict()
        self.assertEqual(data["action"], "retrieve")
        self.assertEqual(data["from"], "planner")
        self.assertIn("params", data)
        self.assertIn("capability_required", data)
        self.assertIn("state_refs", data)

    def test_embedding_similarity(self) -> None:
        embedder = HashEmbedding(dim=64)
        a = embedder.encode("Python deployment openEuler systemd")
        b = embedder.encode("openEuler Python service deployment")
        c = embedder.encode("CSV missing values duplicates")
        self.assertGreater(cosine(a, b), cosine(a, c))

    def test_state_store_creates_non_text_state(self) -> None:
        store = StateStore(HashEmbedding(dim=32))
        state = store.create("hello state", "tester", "T1")
        self.assertEqual(state.dim, 32)
        self.assertEqual(state.size_bytes, 128)
        self.assertEqual(store.fetch(state.state_id).state_id, state.state_id)

    def test_memory_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedMemoryStore(Path(tmp) / "memory.sqlite3", HashEmbedding(dim=64))
            memory.write(
                "summarizer",
                "openEuler deployment",
                "Use systemd and venv for Python service deployment.",
                ["openEuler", "Python", "systemd"],
                ["doc"],
            )
            hits = memory.search("Python service deployment", ["openEuler"], top_k=1)
            memory.close()
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0][1], 0)

    def test_benchmark_runs_ten_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = run_benchmark(
                "both",
                ROOT / "tasks" / "continuous_tasks.json",
                tmp,
                rounds=10,
            )
        self.assertEqual(results["text"]["metrics"]["tasks_completed"], 10)
        self.assertEqual(results["protocol"]["metrics"]["tasks_completed"], 10)
        self.assertGreater(results["protocol"]["metrics"]["state_transfer_count"], 0)


if __name__ == "__main__":
    unittest.main()

