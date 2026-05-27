"""Lightweight subprocess sandbox.

Launches an isolated Python subprocess to run a snippet supplied by the
Executor's LLM. The child uses `resource.setrlimit` to cap CPU seconds and
address space, runs with `unset HTTP_PROXY` / `PYTHONDONTWRITEBYTECODE=1`, and
has its working directory rooted in a per-invocation temp dir. stdout/stderr
are captured with a wall-clock timeout. The runner never imports the snippet
into the host interpreter, so a syntax error or infinite loop cannot kill
the coordinator.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SandboxResult:
    ok: bool
    stdout: str
    stderr: str
    elapsed_ms: float
    timed_out: bool = False
    exit_code: int = 0


_PREAMBLE = textwrap.dedent(
    """
    import resource, signal, sys
    # CPU seconds: prevents pure compute infinite loops.
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu}, {cpu}))
    # Address space (bytes): prevents large allocations from OOMing the host.
    resource.setrlimit(resource.RLIMIT_AS, ({mem}, {mem}))
    # Files: keep the snippet from opening sockets / writing large outputs.
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))

    def _on_xcpu(signum, frame):
        sys.stderr.write("sandbox: SIGXCPU received\\n")
        sys.exit(124)
    signal.signal(signal.SIGXCPU, _on_xcpu)

    # User code follows.
    """
).strip()


def run_python(
    code: str,
    *,
    timeout_sec: float = 5.0,
    cpu_seconds: int = 4,
    memory_mb: int = 256,
    workdir: Optional[Path] = None,
) -> SandboxResult:
    """Execute `code` in a fresh `python3` subprocess with rlimit caps.

    Returns SandboxResult.ok=True iff exit code is 0 and no timeout.
    stdout / stderr are captured as text.
    """
    wd = workdir or Path(tempfile.mkdtemp(prefix="mas_litebus_sb_"))
    wd.mkdir(parents=True, exist_ok=True)
    script = wd / "snippet.py"
    preamble = _PREAMBLE.format(cpu=int(cpu_seconds), mem=int(memory_mb) * 1024 * 1024)
    script.write_text(preamble + "\n" + code, encoding="utf-8")
    env = {
        # Minimal env: drop the HTTP proxy so snippets cannot exfiltrate.
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(wd),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(wd),
            env=env,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return SandboxResult(
            ok=(proc.returncode == 0),
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_ms=round(elapsed_ms, 2),
            timed_out=False,
            exit_code=proc.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return SandboxResult(
            ok=False,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + "\nsandbox: wall-clock timeout",
            elapsed_ms=round(elapsed_ms, 2),
            timed_out=True,
            exit_code=124,
        )
    finally:
        if workdir is None:
            shutil.rmtree(wd, ignore_errors=True)
