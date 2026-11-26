#!/usr/bin/env python3
"""Hardened UCI arena for ChessBot-style engines.

This is a copy/rewrite of ``tools/arena.py`` for experiments.  It keeps the
same basic command line but fixes the protocol and methodology problems of
the original:

* a timed-out / crashed / poisoned engine process is killed, all of its
  pending output is discarded, and a fresh process is started before the
  next game (so a late ``bestmove`` can never leak into another game);
* ``bestmove 0000`` is only accepted in a terminal position, otherwise it is
  an engine error and a loss for the engine that produced it;
* ``ucinewgame`` + ``isready``/``readyok`` (and ``Clear Hash`` when the engine
  advertises it) are sent between games;
* every search sends the complete move history as
  ``position startpos moves ...`` (or ``position fen ... moves ...`` for a
  custom root position), preserving repetition/50-move context;
* openings are played in mirrored colour pairs: the same opening is played
  once with A as White and once with B as White;
* optional PGN logging (``--pgn``), clock-aware time controls
  (``--wtime/--inc/--movestogo``) and a W/D/L + Elo summary with a
  sample-size caveat.

Requires python-chess 1.10+ (``pip install chess``).

Examples
--------
Fixed movetime::

    python experiments/arena_harness/arena.py --pairs 5 --movetime 50 \
        --engine-a .\\chessbot_fixed.exe --engine-b .\\chessbot_current.exe

Clock mode (milliseconds)::

    python experiments/arena_harness/arena.py --games 10 --wtime 2000 --inc 20 \
        --engine-a .\\chessbot_fixed.exe --engine-b .\\chessbot_current.exe

Stockfish limited to 1800::

    python experiments/arena_harness/arena.py --pairs 2 --movetime 100 \
        --engine-a .\\chessbot_fixed.exe \
        --engine-b .\\tools\\sf19\\stockfish\\stockfish-windows-x86-64-universal.exe \
        --option-b UCI_LimitStrength=true --option-b UCI_Elo=1800

Exit codes: 0 = normal, 2 = fatal engine/startup problem, 3 = ``--strict``
and at least one engine error/timeout/illegal move occurred.
"""
from __future__ import annotations

import argparse
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import chess
import chess.pgn

STARTING_FEN = chess.STARTING_FEN

# Openings are UCI move sequences from the initial position.  Each opening is
# played twice (mirrored colours), so the opening/colour bias cancels inside a
# pair.  The list is intentionally modest: the arena is a testing tool, not a
# tournament database.
DEFAULT_OPENINGS: list[str] = [
    "",
    "e2e4",
    "d2d4",
    "g1f3",
    "c2c4",
    "e2e4 e7e5",
    "d2d4 d7d5",
    "e2e4 c7c5",
    "e2e4 e7e6",
    "e2e4 c7c6",
    "d2d4 g8f6",
    "d2d4 e7e6",
    "g1f3 g8f6",
    "c2c4 e7e5",
    "c2c4 c7c5",
    "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6",
    "e2e4 e7e5 g1f3 b8c6 f1c4 f8c5",
    "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4",
    "e2e4 c7c5 g1f3 b8c6 d2d4 c5d4",
    "e2e4 e7e6 d2d4 d7d5 b1c3 g8f6",
    "e2e4 c7c6 d2d4 d7d5 e4e5 c8f5",
    "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6",
    "d2d4 d7d5 c2c4 c7c6 g1f3 g8f6",
    "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4",
    "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7",
    "c2c4 e7e5 b1c3 g8f6 g2g3 d7d5",
    "c2c4 c7c5 g1f3 g8f6 d2d4 c5d4",
    "e2e4 e7e5 g1f3 g8f6 f3e5 d7d6",
    "e2e4 d7d5 e4d5 d8d5 b1c3 d5a5",
    "d2d4 e7e6 e2e4 d7d5 b1c3 g8f6",
    "e2e4 g7g6 d2d4 f8g7 b1c3 d7d6",
    "d2d4 c7c5 d4d5 e7e5 e2e4 d7d6",
    "e2e4 c7c5 c2c3 g8f6 e4e5 f6d5",
    "e2e4 e7e5 b1c3 g8f6 f1c4 f8b4",
    "d2d4 d7d5 g1f3 g8f6 c1f4 c7c5",
]


# ---------------------------------------------------------------------------
# Errors / logging


class EngineError(RuntimeError):
    """A protocol-level engine problem.

    ``kind`` is one of: timeout, crash, protocol, illegal, nullmove, stale,
    clock, startup.
    """

    def __init__(self, message: str, kind: str = "protocol"):
        super().__init__(message)
        self.kind = kind


class Logger:
    """Tiny line logger used for the optional ``--log`` file."""

    def __init__(self, path: Optional[str] = None):
        self.fp = None
        if path:
            self.fp = open(path, "w", encoding="utf-8", newline="\n")
        self.trace = False

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        if self.fp is not None:
            self.fp.write(line + "\n")
            self.fp.flush()
        if self.trace:
            print(line, file=sys.stderr, flush=True)

    def close(self) -> None:
        if self.fp is not None:
            self.fp.close()
            self.fp = None


# ---------------------------------------------------------------------------
# UCI engine wrapper


class UciEngine:
    """A UCI engine process with strict lifecycle handling.

    Search protocol::

        engine.new_game()
        engine.search_move(position_cmd, go_cmd, hard_timeout)

    On timeout the process is poisoned: ``abort()`` sends ``stop``, kills the
    process and throws away its queue.  ``new_game()`` calls ``ensure_ready()``
    which starts a fresh process before the next game.  The old queue is
    unreachable, so a stale ``bestmove`` cannot be consumed.
    """

    def __init__(
        self,
        path: str,
        options: Optional[dict[str, str]] = None,
        name: str = "engine",
        extra_args: Optional[list[str]] = None,
        startup_timeout: float = 30.0,
        log: Optional[Callable[[str], None]] = None,
        trace: bool = False,
        cwd: Optional[str] = None,
    ):
        self.path = os.fspath(path)
        self.extra_args = list(extra_args or [])
        self.options = dict(options or {})
        self.name = name
        self.startup_timeout = float(startup_timeout)
        self.cwd = cwd
        self.trace = trace
        self._log_fn = log or (lambda _msg: None)

        self.proc: Optional[subprocess.Popen] = None
        self.q: Optional[queue.Queue] = None
        self.reader: Optional[threading.Thread] = None
        self.id_name: Optional[str] = None
        self.option_types: dict[str, str] = {}
        self.last_info: str = ""
        self.last_bestmove: str = ""

        self.broken = False
        self.broken_reason = ""
        self.restarts = 0
        self.restart_reasons: list[str] = []
        self.aborts: list[dict[str, str]] = []
        try:
            self._spawn()
            self._handshake()
        except Exception:
            self.broken = True
            self.broken_reason = "startup failed"
            self._stop_process(graceful=False)
            raise

    # -- construction / process lifecycle -------------------------------

    def _command(self) -> list[str]:
        if self.path.lower().endswith(".py"):
            cmd = [sys.executable, self.path]
        else:
            cmd = [self.path]
        cmd.extend(self.extra_args)
        return cmd

    def _spawn(self) -> None:
        assert self.proc is None
        self.q = queue.Queue()
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.cwd,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.proc = None
            self.q = None
            raise EngineError(
                f"{self.name}: cannot start {self.path!r}: {exc}", kind="startup"
            ) from exc

        q = self.q
        stdout = self.proc.stdout
        assert stdout is not None

        def reader() -> None:
            try:
                for line in stdout:
                    q.put(line.rstrip("\r\n"))
            except Exception:
                # The pipe is closed while aborting; this is expected.
                pass
            finally:
                q.put(None)

        self.reader = threading.Thread(
            target=reader, name=f"{self.name}-reader", daemon=True
        )
        self.reader.start()

    def _stop_process(self, graceful: bool) -> None:
        proc = self.proc
        if proc is None:
            return
        if graceful and proc.poll() is None:
            try:
                self._write_raw("quit")
                proc.wait(timeout=3.0)
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        if self.reader is not None:
            try:
                self.reader.join(timeout=1.0)
            except Exception:
                pass
        self.reader = None
        self.proc = None
        self.q = None

    def _kill(self) -> None:
        """Compatibility alias for an ungraceful stop."""
        self._stop_process(graceful=False)

    def _write_raw(self, text: str) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise EngineError(f"{self.name}: process is not running", kind="crash")
        if self.proc.poll() is not None:
            raise EngineError(
                f"{self.name}: process exited with {self.proc.returncode}",
                kind="crash",
            )
        if self.trace:
            self._log_fn(f"{self.name} >> {text}")
        try:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise EngineError(
                f"{self.name}: write failed ({exc})", kind="crash"
            ) from exc

    def send(self, text: str) -> None:
        self._write_raw(text)

    # -- reading --------------------------------------------------------

    def _read_line(self, timeout: float) -> str:
        q = self.q
        if q is None:
            raise EngineError(f"{self.name}: no output queue", kind="crash")
        try:
            line = q.get(timeout=max(0.0, timeout))
        except queue.Empty as exc:
            raise EngineError(
                f"{self.name}: timeout waiting for output", kind="timeout"
            ) from exc
        if line is None:
            raise EngineError(
                f"{self.name}: process ended while waiting for output",
                kind="crash",
            )
        if self.trace:
            self._log_fn(f"{self.name} << {line}")
        if line.startswith("info "):
            self.last_info = line
        return line

    def wait_for(
        self,
        prefix: str,
        timeout: float,
        collect: Optional[list[str]] = None,
    ) -> str:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise EngineError(
                    f"{self.name}: timeout waiting for {prefix!r}", kind="timeout"
                )
            line = self._read_line(left)
            if collect is not None:
                collect.append(line)
            if line == prefix or line.startswith(prefix):
                return line

    def _drain_idle(self) -> list[str]:
        """Discard idle output; a queued bestmove means the engine is dirty."""
        lines: list[str] = []
        q = self.q
        if q is None:
            raise EngineError(f"{self.name}: process is not running", kind="crash")
        while True:
            try:
                line = q.get_nowait()
            except queue.Empty:
                break
            if line is None:
                raise EngineError(
                    f"{self.name}: process ended while draining", kind="crash"
                )
            if self.trace:
                self._log_fn(f"{self.name} (idle) << {line}")
            if line.startswith("info "):
                self.last_info = line
            if line.startswith("bestmove"):
                self.abort(
                    f"stale bestmove while engine should be idle: {line!r}",
                    kind="stale",
                )
                raise EngineError(
                    f"{self.name}: stale bestmove while idle: {line!r}",
                    kind="stale",
                )
            lines.append(line)
        return lines

    # -- handshake / restart -------------------------------------------

    def _handshake(self) -> None:
        lines: list[str] = []
        self.send("uci")
        self.wait_for("uciok", self.startup_timeout, collect=lines)
        for line in lines:
            if line.startswith("id name "):
                self.id_name = line[len("id name ") :].strip()
            m = re.match(r"option name (.+?) type (\w+)", line)
            if m:
                self.option_types[m.group(1)] = m.group(2).lower()
        for key, value in self.options.items():
            if self._has_button(key):
                self.send(f"setoption name {key}")
            else:
                self.send(f"setoption name {key} value {value}")
        self.send("isready")
        self.wait_for("readyok", self.startup_timeout)
        self._log_fn(
            f"{self.name}: started {self.display_name} "
            f"(rc={self.proc.returncode if self.proc else '?'})"
        )

    def _has_button(self, option_name: str) -> bool:
        for name, typ in self.option_types.items():
            if name.lower() == option_name.lower():
                return typ == "button"
        return False

    @property
    def display_name(self) -> str:
        if self.id_name:
            return f"{self.name}:{self.id_name}"
        return f"{self.name}:{os.path.basename(self.path)}"

    def ensure_ready(self) -> None:
        if self.broken or self.proc is None or self.proc.poll() is not None:
            reason = self.broken_reason or "process not running"
            self.restart(reason)

    def restart(self, reason: str) -> None:
        self._stop_process(graceful=False)
        self.restarts += 1
        self.restart_reasons.append(reason)
        self.broken = False
        self.broken_reason = ""
        self._log_fn(f"{self.name}: restart #{self.restarts} ({reason})")
        try:
            self._spawn()
            self._handshake()
        except Exception:
            self.broken = True
            self.broken_reason = "startup failed"
            self._stop_process(graceful=False)
            raise

    def abort(self, detail: str, kind: str = "abort") -> None:
        """Poison the current process and discard all of its output."""
        if self.broken and self.proc is None:
            return
        self.broken = True
        self.broken_reason = f"{kind}: {detail}"
        self.aborts.append({"kind": kind, "detail": detail})
        self._log_fn(f"{self.name}: ABORT [{kind}] {detail}")
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                self._write_raw("stop")
            except Exception:
                pass
            # Give a well-behaved engine a brief chance to stop its worker,
            # then kill it.  We never read its remaining output.
            try:
                proc.wait(timeout=0.25)
            except Exception:
                pass
        self._stop_process(graceful=False)

    def new_game(self) -> None:
        """Send ucinewgame/isready, restarting a poisoned process first."""
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                self.ensure_ready()
                self.send("ucinewgame")
                if self._has_button("Clear Hash"):
                    self.send("setoption name Clear Hash")
                self.send("isready")
                self.wait_for("readyok", self.startup_timeout)
                self._drain_idle()
                self.last_info = ""
                self.last_bestmove = ""
                return
            except Exception as exc:
                last_exc = exc
                self.abort(f"new_game failed: {exc}", kind="startup")
                # The next loop iteration restarts the process and retries.
        raise EngineError(
            f"{self.name}: cannot start a new game after restart: {last_exc}",
            kind="startup",
        )

    # -- search ---------------------------------------------------------

    def search_move(
        self,
        position_command: str,
        go_command: str,
        hard_timeout: float,
    ) -> tuple[str, float, str]:
        """Run one search and return ``(bestmove_token, elapsed_seconds, line)``.

        Raises :class:`EngineError` with kind ``timeout`` after poisoning the
        process, or kind ``crash``/``stale`` when the process is unusable.
        """
        if self.broken or self.proc is None or self.proc.poll() is not None:
            raise EngineError(
                f"{self.name}: engine is not running ({self.broken_reason})",
                kind="crash",
            )
        # Make sure nothing (especially a late bestmove) is waiting.
        self._drain_idle()

        self.send(position_command)
        self.send(go_command)
        t0 = time.perf_counter()
        try:
            line = self.wait_for("bestmove", hard_timeout)
        except EngineError as exc:
            elapsed = time.perf_counter() - t0
            if not self.broken:
                self.abort(
                    f"{exc.kind} during search after {elapsed:.3f}s: {exc}",
                    kind=exc.kind,
                )
            if exc.kind == "timeout":
                raise EngineError(
                    f"{self.name}: search timeout after {elapsed:.3f}s "
                    f"(limit {hard_timeout:.3f}s)",
                    kind="timeout",
                ) from exc
            raise
        elapsed = time.perf_counter() - t0

        parts = line.split()
        if len(parts) < 2:
            self.abort(f"malformed bestmove line: {line!r}", kind="protocol")
            raise EngineError(
                f"{self.name}: malformed bestmove line: {line!r}",
                kind="protocol",
            )
        token = parts[1]
        self.last_bestmove = line
        self._check_after_search()
        return token, elapsed, line

    def _check_after_search(self) -> None:
        """Detect an immediate duplicate/stale bestmove without blocking."""
        q = self.q
        if q is None:
            return
        while True:
            try:
                line = q.get_nowait()
            except queue.Empty:
                return
            if line is None:
                # The move was received; a process that dies immediately
                # afterwards will be restarted before the next game.
                return
            if self.trace:
                self._log_fn(f"{self.name} (post) << {line}")
            if line.startswith("bestmove"):
                self.abort(
                    f"duplicate bestmove after search completed: {line!r}",
                    kind="stale",
                )
                raise EngineError(
                    f"{self.name}: duplicate bestmove after search: {line!r}",
                    kind="stale",
                )
            if line.startswith("info "):
                self.last_info = line

    def quit(self) -> None:
        try:
            self._stop_process(graceful=True)
        finally:
            self.broken = True
            self.broken_reason = "quit"


# ---------------------------------------------------------------------------
# Board / move helpers


def game_result(board: chess.Board, max_plies: int) -> tuple[Optional[str], Optional[str]]:
    """Return ``(result, reason)`` or ``(None, None)`` if the game continues.

    The result is from White's point of view (``1-0`` / ``0-1`` /
    ``1/2-1/2``), exactly as used in PGN.
    """
    if board.is_checkmate():
        return ("1-0" if board.turn == chess.BLACK else "0-1", "checkmate")
    if board.is_stalemate():
        return "1/2-1/2", "stalemate"
    if board.is_insufficient_material():
        return "1/2-1/2", "insufficient material"
    if board.is_seventyfive_moves():
        return "1/2-1/2", "75-move rule"
    if board.is_fivefold_repetition():
        return "1/2-1/2", "fivefold repetition"
    if board.is_fifty_moves():
        return "1/2-1/2", "50-move rule"
    if board.is_repetition(3):
        return "1/2-1/2", "threefold repetition"
    if len(board.move_stack) >= max_plies:
        return "1/2-1/2", f"adjudication: max plies ({max_plies})"
    return None, None


def build_position_command(board: chess.Board) -> str:
    """Build a full-history UCI position command.

    ``position startpos moves ...`` is used for standard games.  For a custom
    root, ``position fen <root fen> moves ...`` is used.  The reconstruction is
    verified against ``board.fen()`` so a mistaken/missing history can never be
    sent silently.
    """
    root = board.root()
    moves = [move.uci() for move in board.move_stack]
    if root.fen() == STARTING_FEN:
        command = "position startpos"
    else:
        command = "position fen " + root.fen()
    if moves:
        command += " moves " + " ".join(moves)

    probe = chess.Board(root.fen())
    for uci in moves:
        try:
            probe.push_uci(uci)
        except ValueError as exc:
            raise EngineError(
                f"cannot rebuild history at move {uci!r}: {exc}", kind="protocol"
            ) from exc
    if probe.fen() != board.fen():
        raise EngineError(
            "position history verification failed: "
            f"rebuilt {probe.fen()!r} != board {board.fen()!r}",
            kind="protocol",
        )
    return command


def coerce_move(
    board: chess.Board,
    best: str,
    max_plies: int = 300,
) -> tuple[Optional[chess.Move], Optional[str], Optional[str]]:
    """Validate a UCI bestmove token.

    Returns ``(move, None, None)`` for a legal move, or
    ``(None, result, reason)`` if ``best == "0000"`` and the position really is
    terminal.  A null move in a non-terminal position raises
    :class:`EngineError` with kind ``nullmove``.  Invalid/illegal moves raise
    with kind ``protocol``/``illegal``.
    """
    if best == "0000":
        if not any(board.legal_moves):
            result, reason = game_result(board, max_plies)
            if result is not None:
                return None, result, reason
            return None, "1/2-1/2", "terminal position"
        raise EngineError(
            f"bestmove 0000 in a position with legal moves: {board.fen()}",
            kind="nullmove",
        )
    try:
        move = chess.Move.from_uci(best)
    except ValueError as exc:
        raise EngineError(f"invalid UCI move {best!r}", kind="protocol") from exc
    if move not in board.legal_moves:
        raise EngineError(
            f"illegal move {best!r} in position {board.fen()}", kind="illegal"
        )
    return move, None, None


# ---------------------------------------------------------------------------
# Game record / play


@dataclass
class GameRecord:
    game_no: int
    pair_no: int
    pair_index: int
    a_white: bool
    opening: str
    result: str
    reason: str
    board: chess.Board
    a_score: float
    plies: int
    elapsed: float
    white_label: str
    black_label: str
    error_engine: Optional[str] = None
    error_kind: Optional[str] = None
    error_detail: str = ""
    final_clocks: Optional[dict[int, float]] = None
    san_moves: list[str] = field(default_factory=list)


@dataclass
class TimeControl:
    mode: str  # "movetime" or "clock"
    movetime: Optional[int] = None
    wtime: Optional[int] = None
    inc: int = 0
    movestogo: Optional[int] = None
    timeout_extra: float = 5.0

    def go_command(self, clocks: dict[int, float]) -> str:
        if self.mode == "movetime":
            assert self.movetime is not None
            return f"go movetime {int(self.movetime)}"
        assert clocks is not None
        parts = [
            "go",
            f"wtime {max(0, int(clocks[chess.WHITE]))}",
            f"btime {max(0, int(clocks[chess.BLACK]))}",
            f"winc {int(self.inc)}",
            f"binc {int(self.inc)}",
        ]
        if self.movestogo:
            parts.append(f"movestogo {int(self.movestogo)}")
        return " ".join(parts)

    def hard_timeout(self, clocks: Optional[dict[int, float]], turn: chess.Color) -> float:
        if self.mode == "movetime":
            assert self.movetime is not None
            return max(0.05, self.movetime / 1000.0 + self.timeout_extra)
        assert clocks is not None
        return max(0.05, clocks[turn] / 1000.0 + self.timeout_extra)


def play_game(
    game_no: int,
    pair_no: int,
    pair_index: int,
    white: UciEngine,
    black: UciEngine,
    white_key: str,
    black_key: str,
    a_white: bool,
    opening_moves: list[str],
    tc: TimeControl,
    max_plies: int,
    verbose: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> GameRecord:
    logger = log or (lambda _msg: None)
    board = chess.Board()
    for uci in opening_moves:
        try:
            board.push_uci(uci)
        except ValueError as exc:
            raise EngineError(f"illegal opening move {uci!r}: {exc}", kind="startup") from exc

    clocks: Optional[dict[int, float]] = None
    if tc.mode == "clock":
        assert tc.wtime is not None
        clocks = {chess.WHITE: float(tc.wtime), chess.BLACK: float(tc.wtime)}

    san_moves: list[str] = []
    error_engine: Optional[str] = None
    error_kind: Optional[str] = None
    error_detail = ""
    t_game = time.perf_counter()
    result: Optional[str] = None
    reason: Optional[str] = None

    while True:
        result, reason = game_result(board, max_plies)
        if result is not None:
            break

        color = board.turn
        engine = white if color == chess.WHITE else black
        key = white_key if color == chess.WHITE else black_key

        if tc.mode == "clock":
            assert clocks is not None
            if clocks[color] <= 0:
                error_engine = key
                error_kind = "clock"
                error_detail = "clock expired before the move"
                result = "0-1" if color == chess.WHITE else "1-0"
                reason = f"time forfeit ({key})"
                break
            go_cmd = tc.go_command(clocks)
            timeout = tc.hard_timeout(clocks, color)
        else:
            go_cmd = tc.go_command({})
            timeout = tc.hard_timeout(None, color)

        position_cmd = build_position_command(board)
        try:
            best, elapsed, raw = engine.search_move(position_cmd, go_cmd, timeout)
        except EngineError as exc:
            error_engine = key
            error_kind = exc.kind
            error_detail = str(exc)
            result = "0-1" if color == chess.WHITE else "1-0"
            reason = f"engine error ({key}, {exc.kind}): {exc}"
            logger(
                f"game {game_no}: {key} failed to move at ply "
                f"{len(board.move_stack)}: [{exc.kind}] {exc}"
            )
            break

        if tc.mode == "clock":
            assert clocks is not None
            clocks[color] -= elapsed * 1000.0
            if clocks[color] <= 0:
                error_engine = key
                error_kind = "clock"
                error_detail = (
                    f"move took {elapsed * 1000.0:.1f} ms; "
                    f"clock is {clocks[color]:.1f} ms"
                )
                result = "0-1" if color == chess.WHITE else "1-0"
                reason = f"time forfeit ({key})"
                break
        logger(
            f"game {game_no}: {key} ply {len(board.move_stack) + 1} "
            f"{go_cmd} -> {raw} ({elapsed * 1000.0:.1f} ms)"
        )

        try:
            move, terminal_result, terminal_reason = coerce_move(
                board, best, max_plies
            )
        except EngineError as exc:
            if not engine.broken:
                engine.abort(str(exc), kind=exc.kind)
            error_engine = key
            error_kind = exc.kind
            error_detail = str(exc)
            result = "0-1" if color == chess.WHITE else "1-0"
            reason = f"engine error ({key}, {exc.kind}): {exc}"
            logger(
                f"game {game_no}: {key} produced bad move {best!r} at ply "
                f"{len(board.move_stack)}: [{exc.kind}] {exc}"
            )
            break

        if terminal_result is not None:
            # Only reachable if a terminal position was somehow sent to the
            # engine; normal play checks game_result before searching.
            result = terminal_result
            reason = terminal_reason or "terminal position"
            break

        assert move is not None
        san = board.san(move)
        san_moves.append(san)
        board.push(move)
        if verbose:
            print(san, end=" ", flush=True)
        if tc.mode == "clock":
            assert clocks is not None
            clocks[color] += tc.inc

    elapsed = time.perf_counter() - t_game
    if clocks is not None:
        logger(
            f"game {game_no}: final clocks "
            f"W={clocks[chess.WHITE]:.1f}ms B={clocks[chess.BLACK]:.1f}ms"
        )
    if result is None:
        result = "1/2-1/2"
        reason = "unknown"
    if error_engine is None and verbose:
        print(f" [{result}]", flush=True)

    a_score = 0.5
    if result == "1-0":
        a_score = 1.0 if a_white else 0.0
    elif result == "0-1":
        a_score = 0.0 if a_white else 1.0

    return GameRecord(
        game_no=game_no,
        pair_no=pair_no,
        pair_index=pair_index,
        a_white=a_white,
        opening=" ".join(opening_moves),
        result=result,
        reason=reason or "",
        board=board,
        a_score=a_score,
        plies=len(board.move_stack),
        elapsed=elapsed,
        white_label=white.display_name,
        black_label=black.display_name,
        error_engine=error_engine,
        error_kind=error_kind,
        error_detail=error_detail,
        final_clocks=dict(clocks) if clocks is not None else None,
        san_moves=san_moves,
    )


# ---------------------------------------------------------------------------
# PGN / summary


def write_pgn(fp, record: GameRecord, event: str) -> None:
    game = chess.pgn.Game.from_board(record.board)
    game.headers["Event"] = event
    game.headers["Site"] = "arena_harness"
    game.headers["Date"] = time.strftime("%Y.%m.%d")
    game.headers["Round"] = str(record.game_no)
    game.headers["White"] = record.white_label
    game.headers["Black"] = record.black_label
    game.headers["Result"] = record.result
    game.headers["Termination"] = record.reason
    game.headers["Opening"] = record.opening or "startpos"
    game.headers["ArenaPair"] = str(record.pair_no)
    game.headers["ArenaColorA"] = "white" if record.a_white else "black"
    if record.error_engine:
        game.comment = (
            f"{record.error_engine} engine error [{record.error_kind}]: "
            f"{record.error_detail}"
        )
    print(game, file=fp)
    print(file=fp)
    try:
        fp.flush()
    except Exception:
        pass


def elo_from_score(p: float) -> Optional[float]:
    if p <= 0.0 or p >= 1.0:
        return None
    return -400.0 * math.log10(1.0 / p - 1.0)


_T975 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def t_critical_975(df: int) -> float:
    return _T975.get(df, 1.96)


def wilson_interval(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def fmt_elo(value: float) -> str:
    """Format an Elo value without a spurious '-0'."""
    if abs(value) < 0.5:
        value = 0.0
    return f"{value:+.0f}"


def fmt_ci(lo: float, hi: float) -> str:
    if lo <= 0.0:
        left = "-inf"
    elif lo >= 1.0:
        left = "+inf"
    else:
        left = fmt_elo(elo_from_score(lo))
    if hi >= 1.0:
        right = "+inf"
    elif hi <= 0.0:
        right = "-inf"
    else:
        right = fmt_elo(elo_from_score(hi))
    return f"{left} .. {right}"


def print_summary(records: list[GameRecord], a: UciEngine, b: UciEngine, args) -> None:
    n = len(records)
    if n == 0:
        print("No completed games.")
        return
    w = sum(1 for r in records if r.a_score == 1.0)
    d = sum(1 for r in records if r.a_score == 0.5)
    l = sum(1 for r in records if r.a_score == 0.0)
    score = sum(r.a_score for r in records)
    p = score / n
    elo = elo_from_score(p)

    print("\n=== summary ===")
    print(f"A: {a.display_name}")
    print(f"B: {b.display_name}")
    print(f"games: {n}  ({len({r.pair_index for r in records})} mirrored opening pair(s))")
    print(f"A W/D/L: +{w} ={d} -{l}   score {score:.2f}/{n} = {p:.3f}")

    if elo is None:
        print("approx Elo difference: unbounded (A scored 0% or 100%)")
    else:
        print(f"approx Elo difference (A-B): {fmt_elo(elo)}")

    wlo, whi = wilson_interval(score, n)
    print(
        f"95% Wilson score interval (naive, game-level): "
        f"{wlo:.3f} .. {whi:.3f}  -> Elo {fmt_ci(wlo, whi)}"
    )

    # Pair-level CI is the methodologically meaningful one for mirrored
    # openings.  It is only valid when every pair has two games.
    pair_groups: dict[int, list[float]] = {}
    for r in records:
        pair_groups.setdefault(r.pair_index, []).append(r.a_score)
    complete_pairs = [v for v in pair_groups.values() if len(v) == 2]
    if len(complete_pairs) >= 2 and len(complete_pairs) == len(pair_groups):
        pair_means = [sum(v) / 2.0 for v in complete_pairs]
        mean = sum(pair_means) / len(pair_means)
        var = sum((x - mean) ** 2 for x in pair_means) / (len(pair_means) - 1)
        se = math.sqrt(var / len(pair_means))
        tcrit = t_critical_975(len(pair_means) - 1)
        lo = max(0.0, mean - tcrit * se)
        hi = min(1.0, mean + tcrit * se)
        print(
            f"95% pair-level score CI ({len(pair_means)} pairs, t={tcrit:.2f}): "
            f"{lo:.3f} .. {hi:.3f}  -> Elo {fmt_ci(lo, hi)}"
        )
    else:
        print("95% pair-level CI: not available (need >=2 complete mirrored pairs)")

    errors = [r for r in records if r.error_engine]
    print(
        f"engine errors: {len(errors)} "
        f"(A={sum(1 for r in errors if r.error_engine == 'A')}, "
        f"B={sum(1 for r in errors if r.error_engine == 'B')})"
    )
    for rec in errors:
        print(
            f"  game {rec.game_no}: {rec.error_engine} [{rec.error_kind}] "
            f"{rec.error_detail}"
        )
    print(
        f"restarts: A={a.restarts} B={b.restarts}; "
        f"aborts: A={len(a.aborts)} B={len(b.aborts)}"
    )

    if n < 30:
        print(
            "\nSAMPLE-SIZE CAVEAT: fewer than 30 games were played.  The Elo "
            "difference and especially its confidence interval are indicative "
            "only; use at least a few hundred games (and many mirrored pairs) "
            "before drawing strength conclusions."
        )
    if args.strict and errors:
        print("STRICT: engine errors were observed; exiting with status 3.")
    print()


# ---------------------------------------------------------------------------
# CLI


def parse_options(pairs: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"bad option {item!r}; expected NAME=VALUE")
        out[key.strip()] = value.strip()
    return out


def validate_openings(openings: list[str]) -> list[str]:
    valid: list[str] = []
    for text in openings:
        board = chess.Board()
        ok = True
        for uci in text.split():
            try:
                board.push_uci(uci)
            except ValueError:
                ok = False
                break
        if ok:
            valid.append(text)
        else:
            print(f"warning: skipping illegal opening {text!r}", file=sys.stderr)
    if not valid:
        raise SystemExit("no valid openings")
    return valid


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Hardened UCI arena (mirrored openings, full history, "
        "timeout recovery, optional PGN)."
    )
    ap.add_argument("--engine-a", required=True, help="path to engine A")
    ap.add_argument("--engine-b", required=True, help="path to engine B")
    ap.add_argument(
        "--engine-a-arg", action="append", default=[],
        help="extra argv argument for engine A (repeatable; use "
        "--engine-a-arg=--flag for values starting with '-')",
    )
    ap.add_argument(
        "--engine-b-arg", action="append", default=[],
        help="extra argv argument for engine B (repeatable; use "
        "--engine-b-arg=--flag for values starting with '-')",
    )
    ap.add_argument(
        "--games", type=int, default=None,
        help="total number of games; rounded up to complete mirrored pairs",
    )
    ap.add_argument(
        "--pairs", type=int, default=None,
        help="number of mirrored opening pairs (2 games per pair); "
        "default 5 (10 games) if --games is omitted",
    )
    tc = ap.add_mutually_exclusive_group()
    tc.add_argument(
        "--movetime", type=int, default=None,
        help="fixed milliseconds per move (default 100 if --wtime is absent)",
    )
    tc.add_argument(
        "--wtime", type=int, default=None,
        help="initial clock in milliseconds per side; enables clock mode",
    )
    ap.add_argument("--inc", type=int, default=0, help="increment in ms (clock mode)")
    ap.add_argument(
        "--movestogo", type=int, default=None,
        help="moves to go for the next time control (clock mode)",
    )
    ap.add_argument(
        "--timeout-extra", type=float, default=5.0,
        help="hard-timeout grace beyond the expected search time, seconds",
    )
    ap.add_argument("--max-plies", type=int, default=300)
    ap.add_argument("--option-a", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--option-b", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--opening", action="append", default=[], metavar="UCI_MOVES",
                    help="override the built-in opening list (repeatable)")
    ap.add_argument("--opening-offset", type=int, default=0)
    ap.add_argument("--pgn", default=None, help="write PGN to this path ('-' for stdout)")
    ap.add_argument("--log", default=None, help="write arena event log to this path")
    ap.add_argument("--trace", action="store_true", help="log raw UCI traffic")
    ap.add_argument("--verbose", action="store_true", help="print SAN moves per game")
    ap.add_argument(
        "--strict", action="store_true",
        help="exit 3 if any engine error/timeout/illegal move occurred",
    )
    ap.add_argument("--startup-timeout", type=float, default=30.0)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    if args.games is not None and args.pairs is not None:
        ap.error("use either --games or --pairs, not both")
    if args.pairs is not None:
        if args.pairs < 1:
            ap.error("--pairs must be >= 1")
        total_games = args.pairs * 2
    elif args.games is not None:
        if args.games < 1:
            ap.error("--games must be >= 1")
        total_games = args.games
    else:
        total_games = 10
    if total_games % 2:
        print(
            "warning: odd number of games; the last game has no mirrored "
            "colour-swapped partner",
            file=sys.stderr,
        )

    if args.movetime is None and args.wtime is None:
        args.movetime = 100
    if args.movetime is not None:
        if args.movetime < 1:
            ap.error("--movetime must be >= 1 ms")
        tc = TimeControl(
            mode="movetime",
            movetime=args.movetime,
            timeout_extra=args.timeout_extra,
        )
    else:
        assert args.wtime is not None
        if args.wtime < 1:
            ap.error("--wtime must be >= 1 ms")
        if args.inc < 0:
            ap.error("--inc must be >= 0")
        if args.movestogo is not None and args.movestogo < 1:
            ap.error("--movestogo must be >= 1")
        tc = TimeControl(
            mode="clock",
            wtime=args.wtime,
            inc=args.inc,
            movestogo=args.movestogo,
            timeout_extra=args.timeout_extra,
        )
    if args.max_plies < 2:
        ap.error("--max-plies must be >= 2")
    if args.timeout_extra < 0:
        ap.error("--timeout-extra must be >= 0")

    openings = validate_openings(args.opening or DEFAULT_OPENINGS)
    logger = Logger(args.log)
    logger.trace = args.trace
    log = logger.log

    a: Optional[UciEngine] = None
    b: Optional[UciEngine] = None
    pgn_fp = None
    records: list[GameRecord] = []
    fatal: Optional[Exception] = None

    try:
        log(
            f"arena start: A={args.engine_a} B={args.engine_b} "
            f"games={total_games} tc={tc}"
        )
        a = UciEngine(
            args.engine_a,
            parse_options(args.option_a),
            "A",
            extra_args=args.engine_a_arg,
            startup_timeout=args.startup_timeout,
            log=log,
            trace=args.trace,
        )
        b = UciEngine(
            args.engine_b,
            parse_options(args.option_b),
            "B",
            extra_args=args.engine_b_arg,
            startup_timeout=args.startup_timeout,
            log=log,
            trace=args.trace,
        )
        print(f"A: {a.display_name}")
        print(f"B: {b.display_name}")
        if tc.mode == "movetime":
            print(f"time control: {tc.movetime} ms/move")
        else:
            print(
                f"time control: wtime={tc.wtime} ms inc={tc.inc} ms"
                + (f" movestogo={tc.movestogo}" if tc.movestogo else "")
            )
        print(
            f"games: {total_games} ({(total_games + 1) // 2} mirrored pairs max), "
            f"max plies: {args.max_plies}"
        )

        if args.pgn:
            if args.pgn == "-":
                pgn_fp = sys.stdout
            else:
                pgn_fp = open(args.pgn, "w", encoding="utf-8", newline="\n")

        pair_count = (total_games + 1) // 2
        for pair_index in range(pair_count):
            if len(records) >= total_games:
                break
            opening_text = openings[(pair_index + args.opening_offset) % len(openings)]
            opening_moves = opening_text.split()
            pair_scores: list[float] = []
            print(
                f"\npair {pair_index + 1}/{pair_count} opening: "
                f"{opening_text or 'startpos'}",
                flush=True,
            )
            for a_white in (True, False):
                if len(records) >= total_games:
                    break
                game_no = len(records) + 1
                if a is None or b is None:
                    raise RuntimeError("engines not initialized")
                # Between every game, including game 1.
                a.new_game()
                b.new_game()
                if a_white:
                    white, black = a, b
                    white_key, black_key = "A", "B"
                else:
                    white, black = b, a
                    white_key, black_key = "B", "A"
                record = play_game(
                    game_no=game_no,
                    pair_no=pair_index + 1,
                    pair_index=pair_index,
                    white=white,
                    black=black,
                    white_key=white_key,
                    black_key=black_key,
                    a_white=a_white,
                    opening_moves=opening_moves,
                    tc=tc,
                    max_plies=args.max_plies,
                    verbose=args.verbose,
                    log=log,
                )
                records.append(record)
                pair_scores.append(record.a_score)
                w = sum(1 for r in records if r.a_score == 1.0)
                d = sum(1 for r in records if r.a_score == 0.5)
                l = sum(1 for r in records if r.a_score == 0.0)
                print(
                    f"  game {record.game_no}/{total_games} "
                    f"{'A' if record.a_white else 'B'} as White: "
                    f"{record.result:7s} {record.plies:3d} plies "
                    f"{record.elapsed:6.1f}s  "
                    f"A=+{w} ={d} -{l}  ({record.reason})",
                    flush=True,
                )
                if args.verbose and record.san_moves:
                    print("    " + " ".join(record.san_moves), flush=True)
                log(
                    f"game {record.game_no}: A_white={record.a_white} "
                    f"opening={record.opening!r} result={record.result} "
                    f"reason={record.reason!r} plies={record.plies} "
                    f"elapsed={record.elapsed:.2f}s "
                    f"error={record.error_engine}:{record.error_kind}"
                )
                if pgn_fp is not None:
                    write_pgn(
                        pgn_fp,
                        record,
                        event=f"arena_harness {a.display_name} vs {b.display_name}",
                    )
            if pair_scores:
                print(
                    f"  pair score for A: {sum(pair_scores) / len(pair_scores):.2f} "
                    f"({len(pair_scores)} game(s))",
                    flush=True,
                )
    except EngineError as exc:
        fatal = exc
        print(f"\nfatal engine error: {exc}", file=sys.stderr)
        log(f"FATAL: {exc}")
    except Exception as exc:
        fatal = exc
        print(f"\nfatal error: {exc}", file=sys.stderr)
        log(f"FATAL: {type(exc).__name__}: {exc}")
    finally:
        if a is not None:
            a.quit()
        if b is not None:
            b.quit()
        if pgn_fp is not None and pgn_fp is not sys.stdout:
            try:
                pgn_fp.close()
            except Exception:
                pass
        if a is not None and b is not None:
            try:
                print_summary(records, a, b, args)
            except Exception as exc:
                print(f"summary error: {exc}", file=sys.stderr)
        log("arena end")
        logger.close()

    if fatal is not None:
        return 2
    if args.strict and any(r.error_engine for r in records):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
