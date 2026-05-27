from __future__ import annotations

import multiprocessing as mp
import os
import platform
import tempfile
import time
import unittest
from pathlib import Path


# protocol_ipc relies on AF_UNIX, fork(), and POSIX shared memory via /dev/shm.
# Windows lacks all three; macOS is missing a complete multiprocessing/AF_UNIX
# story on some Python builds. Skip the whole suite outside Linux so reviewers
# running the test suite on non-target platforms do not see false failures.
_REQUIRES_LINUX = unittest.skipUnless(
    platform.system() == "Linux",
    f"IPC tests require Linux (AF_UNIX + fork + /dev/shm); current platform: {platform.system()}",
)

from mas_litebus.ipc.shm_store import SharedStateStore
from mas_litebus.ipc.socket_bus import (
    SocketClient,
    SocketServer,
    encode_frame,
    recv_framed,
    send_framed,
)
from mas_litebus.ipc.worker import agent_worker_main
from mas_litebus.runtime.ipc_engine import IPCMultiAgentRuntime
from mas_litebus.runtime.task import load_tasks
from mas_litebus.state.embedding import HashEmbedding


ROOT = Path(__file__).resolve().parents[1]


def _echo_server(path: str, ready_path: str) -> None:
    server = SocketServer(path)
    Path(ready_path).write_text(str(os.getpid()))
    server.accept()
    while True:
        try:
            msg = server.recv()
        except ConnectionError:
            break
        if msg.get("action") == "shutdown":
            server.send({"ok": True})
            break
        server.send({"echo": msg})
    server.close()


def _reader_proc(shm_name: str, dim: int, queue: "mp.Queue") -> None:
    store = SharedStateStore(HashEmbedding(dim=dim))
    queue.put(store.attach(shm_name, dim))


@_REQUIRES_LINUX
class SocketBusTests(unittest.TestCase):
    def test_encode_frame_includes_length_header(self) -> None:
        frame = encode_frame({"hello": "world"})
        # 4-byte length prefix + JSON body
        self.assertGreater(len(frame), 4)
        body_len = int.from_bytes(frame[:4], "big")
        self.assertEqual(body_len, len(frame) - 4)

    def test_roundtrip_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "echo.sock")
            ready = os.path.join(tmp, "echo.ready")
            proc = mp.Process(target=_echo_server, args=(sock, ready))
            proc.start()
            try:
                deadline = time.time() + 3
                while not os.path.exists(ready) and time.time() < deadline:
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(ready))
                client = SocketClient(sock)
                client.connect()
                client.send({"msg": "你好", "n": 7})
                response = client.recv()
                self.assertEqual(response["echo"]["msg"], "你好")
                self.assertEqual(response["echo"]["n"], 7)
                client.send({"action": "shutdown"})
                client.recv()
                client.close()
            finally:
                proc.join(timeout=3)
        self.assertEqual(proc.exitcode, 0)


@_REQUIRES_LINUX
class SharedStateStoreTests(unittest.TestCase):
    def test_create_and_attach_cross_process(self) -> None:
        emb = HashEmbedding(dim=64)
        store = SharedStateStore(emb)
        try:
            vec = emb.encode("openEuler python deployment")
            ref = store.create(vec, producer="t", task_id="T1")
            self.assertTrue(ref.shm_name.startswith("mas_state_"))
            self.assertEqual(ref.dim, 64)
            self.assertEqual(ref.size_bytes, 256)
            queue: "mp.Queue" = mp.Queue()
            reader = mp.Process(target=_reader_proc, args=(ref.shm_name, ref.dim, queue))
            reader.start()
            received = queue.get(timeout=3)
            reader.join(timeout=2)
            self.assertEqual(len(received), 64)
            max_diff = max(abs(a - b) for a, b in zip(received, vec))
            # float64 source → float32 storage: round-trip error must stay
            # well below cosine-similarity noise floor.
            self.assertLess(max_diff, 1e-6)
            self.assertEqual(store.alloc_count, 1)
            self.assertEqual(store.peak_bytes, 256)
        finally:
            store.close_all()
        # Block must be unlinked after release.
        self.assertFalse(any(n.startswith("mas_state_") for n in os.listdir("/dev/shm")))

    def test_external_register_and_release(self) -> None:
        store = SharedStateStore(HashEmbedding(dim=32))
        try:
            # Allocate untracked in this process, hand ownership to the store.
            ref = store.create([0.0] * 32, producer="worker", task_id="T2", track=False)
            store.register_external(ref.shm_name, ref.dim, producer="worker", task_id="T2", state_id=ref.state_id)
            self.assertEqual(store.alloc_count, 2)  # local + external
            released = store.release_task("T2")
            self.assertGreaterEqual(released, 1)
        finally:
            store.close_all()


@_REQUIRES_LINUX
class WorkerProcessTests(unittest.TestCase):
    def test_planner_worker_handles_ping_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "planner.sock")
            mem = os.path.join(tmp, "mem.sqlite3")
            ready = os.path.join(tmp, "planner.ready")
            proc = mp.Process(target=agent_worker_main, args=("planner", sock, mem, ready))
            proc.start()
            try:
                deadline = time.time() + 3
                while not os.path.exists(ready) and time.time() < deadline:
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(ready))
                client = SocketClient(sock)
                client.connect()

                client.send({"action": "ping", "task_id": "h", "params": {}})
                ping = client.recv()
                self.assertEqual(ping["result"]["agent_type"], "planner")
                self.assertIn("task_decomposition", ping["result"]["capabilities"])

                client.send(
                    {
                        "action": "plan",
                        "task_id": "T1",
                        "params": {
                            "topic": "openEuler deploy",
                            "request": "run service",
                            "tags": ["openEuler"],
                            "memory_refs": ["mem-x"],
                        },
                    }
                )
                plan = client.recv()
                self.assertIn("retrieve", plan["result"]["steps"])
                self.assertIn("reuse_memory", plan["result"]["steps"])

                client.send({"action": "shutdown", "task_id": "", "params": {}})
                client.recv()
                client.close()
            finally:
                proc.join(timeout=3)
        self.assertEqual(proc.exitcode, 0)


@_REQUIRES_LINUX
class IPCRuntimeTests(unittest.TestCase):
    def test_ten_tasks_with_real_processes(self) -> None:
        tasks = load_tasks(ROOT / "tasks" / "continuous_tasks.json", rounds=10)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = IPCMultiAgentRuntime(memory_path=Path(tmp) / "mem.sqlite3")
            try:
                result = runtime.run_tasks(tasks)
            finally:
                runtime.close()
        metrics = result["metrics"]
        self.assertEqual(metrics["tasks_completed"], 10)
        self.assertEqual(metrics["state_transfer_count"], 40)
        self.assertEqual(metrics["state_bytes"], 25600)
        self.assertGreater(metrics["ipc_send_count"], 0)
        self.assertEqual(metrics["ipc_send_count"], metrics["ipc_recv_count"])
        self.assertGreater(metrics["shm_alloc_count"], 0)
        # All shm should be released by the time runtime.close() returns.
        leftover = [n for n in os.listdir("/dev/shm") if n.startswith("mas_state_")]
        self.assertEqual(leftover, [])


if __name__ == "__main__":
    unittest.main()
