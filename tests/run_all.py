"""Run every ChessBot test suite and aggregate the results."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGS = os.path.join(HERE, "logs")

SUITES = [
    ("perft", "run_perft.py"),
    ("protocol", "run_protocol.py"),
    ("timing", "run_timing.py"),
    ("selfplay", "run_selfplay.py"),
    ("robustness", "run_robustness.py"),
]


def main() -> int:
    os.makedirs(LOGS, exist_ok=True)
    t0 = time.time()
    aggregate = {"suites": [], "passed": 0, "total": 0, "failed": 0}
    for name, script in SUITES:
        print(f"\n===== {name} =====", flush=True)
        rc = subprocess.call([sys.executable, "-u", os.path.join(HERE, script)], cwd=ROOT)
        path = os.path.join(LOGS, name + ".json")
        row = {"suite": name, "exit": rc, "path": os.path.relpath(path, ROOT)}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            row.update({k: data.get(k) for k in ("passed", "total", "failed", "elapsed_s")})
            aggregate["passed"] += data.get("passed", 0)
            aggregate["total"] += data.get("total", 0)
            aggregate["failed"] += data.get("failed", 0)
        aggregate["suites"].append(row)
        print(f"===== {name}: exit {rc} =====", flush=True)
    aggregate["elapsed_s"] = round(time.time() - t0, 1)
    with open(os.path.join(LOGS, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(aggregate, f, indent=2)
    print("\n===== AGGREGATE =====")
    for row in aggregate["suites"]:
        print(f"{row['suite']:12s} {row.get('passed')}/{row.get('total')} "
              f"failed={row.get('failed')} exit={row['exit']} ({row.get('elapsed_s')}s)")
    print(f"TOTAL {aggregate['passed']}/{aggregate['total']} passed, "
          f"{aggregate['failed']} failed in {aggregate['elapsed_s']}s")
    print("summary -> tests/logs/summary.json")
    return 0 if aggregate["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
