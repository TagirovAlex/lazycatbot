"""Minimal UCI engine wrapper for testing ChessBot.

Reads/writes stdout/stdin asynchronously so that `search` can be interrupted
by `stop` and so that `wait_bestmove` can enforce wall-clock timeouts.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque


class Engine:
    def __init__(self, path: str, name: str = "engine", startup_timeout: float = 10.0):
        self.path = path
        self.name = name
        self.proc = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.lines: list[str] = []
        self._cond = threading.Condition()
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- low level -----------------------------------------------------
    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\r\n")
            with self._cond:
                self.lines.append(line)
                self._cond.notify_all()
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def send(self, cmd: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def mark(self) -> int:
        with self._cond:
            return len(self.lines)

    def wait_for(self, token: str, timeout: float = 10.0, start: int | None = None) -> bool:
        """Wait until a line equal to (or starting with) token appears after `start`."""
        deadline = time.time() + timeout
        idx = self.mark() if start is None else start
        with self._cond:
            while True:
                while idx < len(self.lines):
                    line = self.lines[idx]
                    if line == token or line.startswith(token):
                        return True
                    idx += 1
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                if self._closed:
                    return False
                self._cond.wait(min(remaining, 0.05))

    def wait_bestmove(self, timeout: float = 20.0, start: int | None = None):
        """Return (bestmove, elapsed, all new lines) or (None, elapsed, lines) on timeout."""
        t0 = time.time()
        idx = self.mark() if start is None else start
        deadline = t0 + timeout
        with self._cond:
            while True:
                while idx < len(self.lines):
                    line = self.lines[idx]
                    if line.startswith("bestmove"):
                        return line, time.time() - t0, self.lines[start if start is not None else 0:]
                    idx += 1
                remaining = deadline - time.time()
                if remaining <= 0 or self._closed:
                    return None, time.time() - t0, self.lines[start if start is not None else 0:][-200:]
                self._cond.wait(min(remaining, 0.05))

    def collect(self, start: int) -> list[str]:
        with self._cond:
            return list(self.lines[start:])

    def wait_ready(self, timeout: float = 10.0) -> bool:
        self.send("isready")
        return self.wait_for("readyok", timeout=timeout)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.send("quit")
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def alive(self) -> bool:
        return self.proc.poll() is None

    # -- UCI helpers ---------------------------------------------------
    def uci(self, timeout: float = 10.0) -> list[str]:
        start = self.mark()
        self.send("uci")
        if not self.wait_for("uciok", timeout=timeout, start=start):
            raise RuntimeError(f"{self.name}: no uciok")
        return self.collect(start)

    def newgame(self) -> None:
        self.send("ucinewgame")
        if not self.wait_ready():
            raise RuntimeError(f"{self.name}: no readyok after ucinewgame")

    def set_position(self, fen: str | None = None, moves: list[str] | None = None) -> None:
        if fen is None:
            cmd = "position startpos"
        else:
            cmd = "position fen " + fen
        if moves:
            cmd += " moves " + " ".join(moves)
        self.send(cmd)

    def search(self, go: str, timeout: float = 30.0):
        start = self.mark()
        self.send("go " + go)
        return self.wait_bestmove(timeout=timeout, start=start)
