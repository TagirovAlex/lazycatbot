"""Cross-check move generation against python-chess and Stockfish."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import chess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402
from testutil import Results, STOCKFISH  # noqa: E402
from uci_engine import Engine  # noqa: E402

CASES = [
    ("startpos",
     "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
     {1: 20, 2: 400, 3: 8902, 4: 197281, 5: 4865609}),
    ("kiwipete",
     "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
     {1: 48, 2: 2039, 3: 97862, 4: 4085603}),
    ("pos3",
     "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
     {1: 14, 2: 191, 3: 2812, 4: 43238, 5: 674624}),
    ("pos4",
     "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
     {1: 6, 2: 264, 3: 9467, 4: 422333}),
    ("pos5",
     "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
     {1: 44, 2: 1486, 3: 62379, 4: 2103487}),
    # FEN provided by the task; reference values intentionally not assumed.
    ("pos6-task",
     "r4rk1/1pp1qppp/p1np1n2/2b1p3/2B1P3/P1NP1N2/1PP1QPPP/R1B1K2R w KQ - 0 1",
     {}),
]


def py_perft(board: chess.Board, depth: int) -> int:
    if depth == 0:
        return 1
    n = 0
    for m in board.legal_moves:
        board.push(m)
        n += py_perft(board, depth - 1)
        board.pop()
    return n


class Stockfish:
    def __init__(self, path: str):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1,
                                  encoding="utf-8", errors="replace")
        self.send("uci")
        while True:
            line = self.p.stdout.readline().strip()
            if line == "uciok":
                break

    def send(self, s: str) -> None:
        self.p.stdin.write(s + "\n")
        self.p.stdin.flush()

    def perft(self, fen: str, depth: int) -> int:
        self.send("position fen " + fen)
        self.send(f"go perft {depth}")
        while True:
            line = self.p.stdout.readline().strip()
            if line.startswith("Nodes searched:"):
                return int(line.split(":")[1].strip())

    def close(self) -> None:
        try:
            self.send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main() -> int:
    res = Results("perft")
    eng = Engine(testutil.ENGINE, "chessbot_test")
    eng.uci()
    sf = Stockfish(STOCKFISH)

    try:
        for name, fen, expected in CASES:
            board = chess.Board(fen)
            res.add(f"{name}: fen valid", board.is_valid(),
                    "" if board.is_valid() else "python-chess rejects FEN")
            # python-chess shallow cross-check (independent generator).
            for d in (1, 2, 3):
                if expected.get(d, 1 << 60) <= 300_000:
                    pv = py_perft(board, d)
                    res.check(f"{name}: python-chess perft {d}", pv,
                              expected.get(d, pv), f"FEN {fen}")
            depths = sorted(expected)
            if not depths:  # pos6: reference from Stockfish + python-chess
                depths = [1, 2, 3, 4]
                for d in depths:
                    ref = sf.perft(fen, d)
                    pv = py_perft(board, d) if d <= 3 else None
                    res.add(f"{name}: reference perft {d}", True,
                            f"stockfish={ref}" + (f" python-chess={pv}" if pv is not None else ""))
                    expected[d] = ref
                    if pv is not None:
                        res.check(f"{name}: python-chess vs stockfish perft {d}", pv, ref)

            eng.set_position(fen=fen)
            for d in sorted(expected):
                start = eng.mark()
                t0 = time.time()
                eng.send(f"perft {d}")
                ok = eng.wait_for("perft ", timeout=180, start=start)
                got = None
                line = ""
                if ok:
                    line = eng.collect(start)[-1]
                    try:
                        got = int(line.split()[1])
                    except Exception:
                        got = None
                res.check(f"{name}: engine perft {d}", got, expected[d],
                          ("" if got == expected[d] else f"engine output {line!r}") +
                          f" ({time.time()-t0:.2f}s)")
    finally:
        sf.close()
        eng.close()
    res.write()
    return 0 if res.summary()["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
