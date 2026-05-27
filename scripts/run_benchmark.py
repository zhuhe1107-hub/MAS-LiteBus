from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mas_litebus.eval.benchmark import run_benchmark
from mas_litebus.eval.report import build_summary, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MAS-LiteBus benchmark.")
    parser.add_argument(
        "--mode",
        choices=[
            "text",
            "text_v2",
            "text_with_memory",
            "protocol_no_memory",
            "protocol",
            "protocol_ipc",
            "both",
            "ablation",
            "all",
        ],
        default="all",
        help=(
            "one specific mode, or 'both' (text+protocol), or 'all'/'ablation' for the full "
            "6-mode comparison covering text/protocol × memory/no-memory + IPC variant"
        ),
    )
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="repeat each mode N times and aggregate mean / std (useful for latency variance)",
    )
    parser.add_argument("--tasks", default=str(ROOT / "tasks" / "continuous_tasks.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "outputs"))
    args = parser.parse_args()

    results = run_benchmark(args.mode, args.tasks, args.output_dir, args.rounds, repeat=args.repeat)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if len(results) == 1:
        only = next(iter(results))
        (output / f"benchmark_{only}.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(results[only]["metrics"], ensure_ascii=False, indent=2))
    else:
        summary = write_report(results, args.output_dir)
        print(json.dumps(build_summary(results), ensure_ascii=False, indent=2))
        print(f"Report written to: {output / 'benchmark_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

