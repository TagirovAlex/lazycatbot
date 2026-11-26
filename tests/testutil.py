"""Shared helpers for ChessBot tests."""
from __future__ import annotations

import json
import os
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
LOGS = os.path.join(TESTS_DIR, "logs")
PGNS = os.path.join(TESTS_DIR, "pgn")
os.makedirs(LOGS, exist_ok=True)
os.makedirs(PGNS, exist_ok=True)

ENGINE = os.path.join(TESTS_DIR, "chessbot_test.exe")
ENGINE_ROOT = os.path.join(ROOT, "chessbot_res.exe")
ENGINE_SFNET = os.path.join(ROOT, "chessbot_sfnet.exe")
ENGINE_NNUE = os.path.join(ROOT, "chessbot_nnue.exe")
STOCKFISH = os.path.join(ROOT, "tools", "sf19", "stockfish",
                         "stockfish-windows-x86-64-universal.exe")


class Results:
    def __init__(self, suite: str):
        self.suite = suite
        self.rows: list[dict] = []
        self.t0 = time.time()

    def add(self, name: str, ok: bool, detail: str = "", **extra) -> bool:
        row = {"test": name, "status": "PASS" if ok else "FAIL",
               "detail": detail, "elapsed_ms": round((time.time() - self.t0) * 1000, 1)}
        row.update(extra)
        self.rows.append(row)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""),
              flush=True)
        return ok

    def check(self, name: str, actual, expected, detail: str = "") -> bool:
        ok = actual == expected
        return self.add(name, ok, detail or f"expected {expected!r}, got {actual!r}",
                        actual=repr(actual), expected=repr(expected))

    def summary(self) -> dict:
        passed = sum(1 for r in self.rows if r["status"] == "PASS")
        total = len(self.rows)
        return {"suite": self.suite, "passed": passed, "total": total,
                "failed": total - passed,
                "elapsed_s": round(time.time() - self.t0, 1),
                "rows": self.rows}

    def write(self, filename: str | None = None) -> str:
        path = os.path.join(LOGS, (filename or self.suite) + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, indent=2)
        print(f"\n{self.suite}: {self.summary()['passed']}/{self.summary()['total']} passed "
              f"in {self.summary()['elapsed_s']}s -> {path}", flush=True)
        return path


def parse_bestmove(line: str) -> str | None:
    if not line or not line.startswith("bestmove"):
        return None
    parts = line.split()
    return parts[1] if len(parts) > 1 else None


def parse_info_last(lines: list[str]) -> dict:
    """Parse the last UCI info line into a dict of key/value strings."""
    for line in reversed(lines):
        if line.startswith("info ") and " depth " in line:
            toks = line.split()
            d = {}
            i = 1
            while i < len(toks):
                k = toks[i]
                if k in ("pv", "string"):
                    d[k] = " ".join(toks[i + 1:])
                    break
                if k == "score" and i + 2 < len(toks):
                    d["score"] = toks[i + 1] + " " + toks[i + 2]
                    i += 3
                    continue
                if i + 1 < len(toks):
                    d[k] = toks[i + 1]
                    i += 2
                else:
                    break
            return d
    return {}
