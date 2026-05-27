from __future__ import annotations

import json
import os
import socket
import struct
import time
from typing import Any


HEADER = struct.Struct("!I")
MAX_FRAME_BYTES = 16 * 1024 * 1024


def encode_frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {len(body)} > {MAX_FRAME_BYTES}")
    return HEADER.pack(len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(
                f"socket closed while reading frame: needed {n} bytes, got {n - remaining}"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_framed(sock: socket.socket, payload: dict[str, Any]) -> int:
    frame = encode_frame(payload)
    sock.sendall(frame)
    return len(frame)


def recv_framed(sock: socket.socket) -> dict[str, Any]:
    header = _recv_exact(sock, HEADER.size)
    (size,) = HEADER.unpack(header)
    if size > MAX_FRAME_BYTES:
        raise ValueError(f"incoming frame too large: {size}")
    body = _recv_exact(sock, size)
    return json.loads(body.decode("utf-8"))


class SocketServer:
    """AF_UNIX stream server. Binds a single socket path, accepts one client.

    Used by an agent worker process: the worker binds first, then signals
    the coordinator (parent) which connects via SocketClient.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        if os.path.exists(path):
            os.unlink(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(1)
        self.conn: socket.socket | None = None

    def accept(self) -> None:
        self.conn, _ = self.sock.accept()

    def recv(self) -> dict[str, Any]:
        if self.conn is None:
            raise RuntimeError("accept() must be called before recv()")
        return recv_framed(self.conn)

    def send(self, payload: dict[str, Any]) -> int:
        if self.conn is None:
            raise RuntimeError("accept() must be called before send()")
        return send_framed(self.conn, payload)

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class SocketClient:
    """AF_UNIX stream client with bounded connect retry.

    Retries are needed because workers fork before they call bind+listen,
    so the parent's connect() may race the worker's listen().
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def connect(self, retries: int = 100, delay: float = 0.02) -> None:
        last_err: Exception | None = None
        for _ in range(retries):
            try:
                self.sock.connect(self.path)
                return
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                last_err = exc
                time.sleep(delay)
        raise ConnectionError(f"could not connect to {self.path}: {last_err}")

    def send(self, payload: dict[str, Any]) -> int:
        return send_framed(self.sock, payload)

    def recv(self) -> dict[str, Any]:
        return recv_framed(self.sock)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
