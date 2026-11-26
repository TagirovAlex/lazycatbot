"""Minimal repros for the issues found; writes a clean log for REPORT.md."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402
from testutil import parse_bestmove  # noqa: E402
from uci_engine import Engine  # noqa: E402

OUT = []


def log(s: str) -> None:
    print(s, flush=True)
    OUT.append(s)


def kill(e: Engine) -> None:
    try:
        e.proc.kill()
        e.proc.wait(timeout=5)
    except Exception:
        pass


def main() -> int:
    log("ChessBot minimal bug repros")
    log("engine: tests/chessbot_test.exe (built from src with g++ 16.1 -O2)")
    log("")

    # 1. default position
    e = Engine(testutil.ENGINE, "repro-default")
    e.wait_ready(20)
    start = e.mark()
    e.send("d")
    e.send("isready")
    e.wait_for("readyok", 5, start)
    fens = [ln[5:].strip() for ln in e.collect(start) if ln.startswith("FEN: ")]
    log("[1] startup position, after `d` + `isready`:")
    log("    FEN = " + (fens[-1] if fens else "<none>"))
    log("    expected startpos: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    log("")

    # 2. go without position
    start = e.mark()
    e.send("go depth 1")
    bm, elapsed, lines = e.wait_bestmove(timeout=2, start=start)
    log("[2] `go depth 1` with no `position` command:")
    log(f"    bestmove = {parse_bestmove(bm or '')!r}, waited {elapsed:.2f}s, "
        f"process_alive = {e.alive()}")
    log("    -> no bestmove, engine unresponsive (access violation in Position::inCheck,"
        " kingSq = -1)")
    kill(e)
    log("")

    # 3. invalid FEN then go
    e = Engine(testutil.ENGINE, "repro-fen")
    e.wait_ready(20)
    e.send("position fen banana")
    log("[3] `position fen banana` then `go depth 1`:")
    log(f"    isready after invalid FEN: {e.wait_ready(3)}")
    start = e.mark()
    e.send("go depth 1")
    bm, elapsed, lines = e.wait_bestmove(timeout=2, start=start)
    log(f"    bestmove = {parse_bestmove(bm or '')!r}, waited {elapsed:.2f}s, "
        f"process_alive = {e.alive()}")
    log("    -> same empty-board crash path")
    kill(e)
    log("")

    # 4. invalid numeric setoption
    e = Engine(testutil.ENGINE, "repro-option")
    e.wait_ready(20)
    start = e.mark()
    e.send("setoption name Hash value abc")
    e.send("isready")
    ok = e.wait_for("readyok", 2, start)
    log("[4] `setoption name Hash value abc`:")
    log(f"    readyok = {ok}; output = {e.collect(start)!r}")
    log("    -> uncaught std::invalid_argument from std::stoi, process terminates")
    kill(e)
    log("")

    # 5. invalid FEN halfmove field
    e = Engine(testutil.ENGINE, "repro-clock")
    e.wait_ready(20)
    start = e.mark()
    e.send("position fen 4k3/8/8/8/8/8/8/4K3 w - - x 1")
    e.send("isready")
    ok = e.wait_for("readyok", 2, start)
    log("[5] `position fen ... w - - x 1`:")
    log(f"    readyok = {ok}; output = {e.collect(start)!r}")
    log("    -> uncaught std::invalid_argument from std::stoi in Position::setFen")
    kill(e)
    log("")

    # 6. d not flushed
    e = Engine(testutil.ENGINE, "repro-d")
    e.wait_ready(20)
    start = e.mark()
    e.send("d")
    time.sleep(0.5)
    before = [ln for ln in e.collect(start) if ln.startswith("FEN: ")]
    e.send("isready")
    e.wait_for("readyok", 5, start)
    after = [ln for ln in e.collect(start) if ln.startswith("FEN: ")]
    log("[6] `d` output buffering:")
    log(f"    FEN lines 0.5s after `d` (no flush) = {before!r}")
    log(f"    FEN lines after following `isready`       = {after!r}")
    log("    -> d uses '\\n' and stdout is fully buffered (cin.tie(nullptr));"
        " output only appears when a later endl command flushes it")
    e.close()
    log("")

    path = os.path.join(testutil.LOGS, "repro_bugs.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("saved", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
