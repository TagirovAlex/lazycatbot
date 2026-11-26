"""Time-management tests."""
from __future__ import annotations

import os
import statistics
import sys

import chess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402
from testutil import Results, parse_bestmove  # noqa: E402
from uci_engine import Engine  # noqa: E402


def main() -> int:
    res = Results("timing")
    eng = Engine(testutil.ENGINE, "chessbot_test")
    eng.uci()
    eng.newgame()
    eng.set_position()
    board = chess.Board()

    def timed(go: str, timeout: float, label: str) -> float | None:
        start = eng.mark()
        eng.send("go " + go)
        bmline, elapsed, lines = eng.wait_bestmove(timeout=timeout, start=start)
        bm = parse_bestmove(bmline or "")
        info = [ln for ln in lines if ln.startswith("info depth")]
        ok = bm is not None and bm != "0000" and chess.Move.from_uci(bm) in board.legal_moves
        res.add(f"{label}: returns legal bestmove", ok,
                f"{bm} in {elapsed:.3f}s ({len(info)} info lines)")
        if not ok:
            return None
        return elapsed

    # movetime budget, default Move Overhead 30
    for mt, reps, limit in ((200, 10, 2.2), (1000, 5, 3.0), (3000, 1, 5.0)):
        times = []
        for i in range(reps):
            t = timed(f"movetime {mt}", timeout=mt / 1000 + 8, label=f"movetime {mt} #{i+1}")
            if t is not None:
                times.append(t)
        if times:
            res.add(f"movetime {mt}: all within budget+2s", max(times) <= limit,
                    f"n={len(times)} min={min(times):.3f}s med={statistics.median(times):.3f}s "
                    f"max={max(times):.3f}s limit={limit:.1f}s")
            res.add(f"movetime {mt}: not grossly over budget", max(times) <= mt / 1000 * 1.5 + 0.5,
                    f"max={max(times):.3f}s budget={mt/1000:.3f}s")
            res.add(f"movetime {mt}: all runs completed", len(times) == reps,
                    f"{len(times)}/{reps}")

    # clocks
    for go, limit, label in (
        ("wtime 1000 btime 1000", 1.5, "clock 1s"),
        ("wtime 100 btime 100", 1.0, "clock 0.1s"),
        ("wtime 5000 btime 5000 movestogo 20", 2.0, "clock 5s/20"),
        ("wtime 10000 btime 10000 movestogo 40", 2.0, "clock 10s/40"),
        ("wtime 5000 btime 5000 winc 100 binc 100", 2.0, "clock 5s+0.1"),
    ):
        t = timed(go, timeout=limit + 5, label=label)
        if t is not None:
            res.add(f"{label}: within {limit:.1f}s", t <= limit,
                    f"{go} -> {t:.3f}s")

    # Move Overhead 0 should give near-exact movetime budgeting
    eng.send("setoption name Move Overhead value 0")
    eng.wait_ready()
    times = []
    for i in range(5):
        t = timed("movetime 100", timeout=5, label=f"overhead0 movetime100 #{i+1}")
        if t is not None:
            times.append(t)
    if times:
        res.add("Move Overhead 0, movetime 100: max <= 1s", max(times) <= 1.0,
                f"min={min(times):.3f}s max={max(times):.3f}s")
        res.add("Move Overhead 0, movetime 100: not >3x budget", max(times) <= 0.3,
                f"max={max(times):.3f}s")
    eng.send("setoption name Move Overhead value 30")
    eng.wait_ready()

    # infinite + stop latency
    lat = []
    for i in range(5):
        start = eng.mark()
        eng.send("go infinite")
        import time as _t
        _t.sleep(0.35)
        t0 = _t.time()
        eng.send("stop")
        bmline, elapsed, lines = eng.wait_bestmove(timeout=5, start=start)
        bm = parse_bestmove(bmline or "")
        lat.append(elapsed)
        res.add(f"infinite+stop #{i+1}: prompt bestmove", elapsed <= 1.0 and bm is not None,
                f"{elapsed:.4f}s {bm}")
        res.add(f"infinite+stop #{i+1}: bestmove legal", bm is not None and
                (bm == "0000" and board.legal_moves.count() == 0 or
                 bm != "0000" and chess.Move.from_uci(bm) in board.legal_moves), str(bm))
        res.add(f"infinite+stop #{i+1}: search produced info", any(
            ln.startswith("info depth") for ln in lines), f"{len(lines)} lines")
    res.add("stop latency max <= 1s", max(lat) <= 1.0, f"max={max(lat):.4f}s")

    # infinite running for ~1.5s then stop
    start = eng.mark()
    eng.send("go infinite")
    import time as _t
    _t.sleep(1.5)
    eng.send("stop")
    bmline, elapsed, lines = eng.wait_bestmove(timeout=5, start=start)
    res.add("infinite 1.5s + stop <= 1s", elapsed <= 1.0,
            f"{elapsed:.4f}s bestmove={parse_bestmove(bmline or '')}")

    eng.close()
    res.write()
    return 0 if res.summary()["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
