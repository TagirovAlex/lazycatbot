#!/usr/bin/env python3
"""Generate Stockfish static-evaluation labels for NNUE distillation.

Reads FENs from an existing dataset and adds positions from random games.
For every position it asks Stockfish for its static `eval` and stores the
side-to-move score in centipawns as "<FEN>|<score>".
"""
import argparse
import queue
import random
import re
import subprocess
import threading
import time

import chess


FINAL_RE = re.compile(r"Final evaluation\s+([+-]?\d+(?:\.\d+)?)\s+\(white side\)")


class SfEval:
    def __init__(self, path):
        self.proc = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        self.q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self.send("uci")
        self.wait_for("uciok", 60)
        self.send("setoption name Threads value 1")
        self.send("setoption name Hash value 32")
        self.send("isready")
        self.wait_for("readyok", 60)

    def _reader(self):
        for line in self.proc.stdout:
            self.q.put(line.rstrip("\r\n"))
        self.q.put(None)

    def send(self, s):
        self.proc.stdin.write(s + "\n")
        self.proc.stdin.flush()

    def wait_for(self, prefix, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.q.get(timeout=deadline - time.time())
            if line is None:
                raise RuntimeError("stockfish ended")
            if line.startswith(prefix):
                return line
        raise TimeoutError(prefix)

    def evaluate(self, fen, timeout=10.0):
        self.send(f"position fen {fen}")
        self.send("eval")
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.q.get(timeout=deadline - time.time())
            if line is None:
                raise RuntimeError("stockfish ended")
            m = FINAL_RE.search(line)
            if m:
                return float(m.group(1))
        return None

    def close(self):
        try:
            self.send("quit")
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def read_fens(path):
    seen = set()
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            sep = line.rfind("|")
            fen = line[:sep] if sep >= 0 else line.strip()
            if not fen or fen in seen:
                continue
            try:
                b = chess.Board(fen)
            except ValueError:
                continue
            if not b.is_valid():
                continue
            seen.add(fen)
            out.append(fen)
    return out


def random_positions(count, seed):
    rng = random.Random(seed)
    out = []
    seen = set()
    while len(out) < count:
        b = chess.Board()
        plies = rng.randint(4, 90)
        for _ in range(plies):
            if b.is_game_over():
                break
            moves = list(b.legal_moves)
            if not moves:
                break
            b.push(rng.choice(moves))
            if b.is_check() or b.is_game_over():
                continue
            fen = b.fen()
            if fen not in seen and len(out) < count:
                seen.add(fen)
                out.append(fen)
        if len(out) >= count:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--stockfish", required=True)
    ap.add_argument("--random", type=int, default=0,
                    help="extra random positions to generate")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    fens = []
    seen = set()
    if args.input:
        fens = read_fens(args.input)
        print(f"loaded {len(fens)} positions from {args.input}")
        seen.update(fens)
    if args.random:
        extra = random_positions(args.random, args.seed)
        added = 0
        for fen in extra:
            if fen not in seen:
                seen.add(fen)
                fens.append(fen)
                added += 1
        print(f"added {added} random positions")
    if args.limit:
        fens = fens[:args.limit]
    print(f"labeling {len(fens)} positions")

    sf = SfEval(args.stockfish)
    written = 0
    skipped = 0
    t0 = time.time()
    try:
        with open(args.output, "w", encoding="utf-8") as out:
            for i, fen in enumerate(fens):
                try:
                    board = chess.Board(fen)
                except ValueError:
                    skipped += 1
                    continue
                if board.is_check() or board.is_game_over():
                    skipped += 1
                    continue
                val = sf.evaluate(fen)
                if val is None:
                    skipped += 1
                    continue
                cp = max(-2000, min(2000, int(round(val * 100))))
                if board.turn == chess.BLACK:
                    cp = -cp
                out.write(f"{fen}|{cp}\n")
                written += 1
                if (i + 1) % 2000 == 0:
                    print(f"{i + 1}/{len(fens)} written={written} "
                          f"skipped={skipped} {time.time() - t0:.1f}s",
                          flush=True)
                if written and written % 5000 == 0:
                    out.flush()
    finally:
        sf.close()
    print(f"done: {written} written, {skipped} skipped -> {args.output}")


if __name__ == "__main__":
    main()
