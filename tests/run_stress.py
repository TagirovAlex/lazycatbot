#!/usr/bin/env python3
"""Stress-test a UCI engine with many threads and repeated searches.

Checks:
  * Threads=8 (and toggling 8 <-> 1) does not crash or deadlock;
  * repeated `go movetime`, `go nodes`, `go infinite` + `stop`;
  * `isready` is answered while a search is running;
  * `position`/`ucinewgame` between searches;
  * optional Syzygy root probing when `data/syzygy` exists;
  * every bestmove is legal.

Usage:
  python tests/run_stress.py --cycles 120 --threads 8 --movetime 40
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uci_engine import Engine  # noqa: E402


def random_board(rng: random.Random) -> chess.Board:
    board = chess.Board()
    for _ in range(rng.randint(8, 50)):
        moves = list(board.legal_moves)
        if not moves or board.is_game_over():
            break
        board.push(rng.choice(moves))
    return board


def parse_best(line: str | None) -> str | None:
    if not line:
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    return parts[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.path.join(ROOT, "chessbot.exe"))
    ap.add_argument("--cycles", type=int, default=120)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--hash", type=int, default=256)
    ap.add_argument("--movetime", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "logs", "stress.json"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    tb_dir = os.path.join(ROOT, "data", "syzygy")
    tb_available = os.path.isdir(tb_dir) and os.path.exists(
        os.path.join(tb_dir, "KQvK.rtbw")
    )
    # Fathom uses narrow fopen() on Windows; a relative ASCII path is safest
    # when the workspace itself contains non-ASCII characters.
    tb_setting = "data/syzygy"

    result = {
        "engine": os.path.abspath(args.engine),
        "threads": args.threads,
        "hash_mb": args.hash,
        "movetime_ms": args.movetime,
        "cycles": args.cycles,
        "errors": [],
        "timeouts": 0,
        "illegal": 0,
        "null_bestmove": 0,
        "max_latency_ms": 0,
        "tb_tests": 0,
        "tb_node_tests": 0,
        "elapsed_s": 0.0,
    }

    engine = Engine(args.engine)
    t0 = time.time()
    try:
        engine.uci()
        engine.send(f"setoption name Threads value {args.threads}")
        engine.send(f"setoption name Hash value {args.hash}")
        engine.send("setoption name UseNNUE value false")
        if not engine.wait_ready():
            raise RuntimeError("no readyok after options")

        for cycle in range(1, args.cycles + 1):
            if cycle % 30 == 0:
                # Recreate/tear down helpers and resize TT under load.
                n = args.threads if (cycle // 30) % 2 else 1
                engine.send(f"setoption name Threads value {n}")
                engine.send(f"setoption name Hash value {args.hash}")
                if not engine.wait_ready():
                    result["errors"].append(f"cycle {cycle}: no readyok after options")
                    break

            board = random_board(rng)
            if board.is_game_over():
                continue
            engine.set_position(fen=board.fen())
            mode = cycle % 5
            start = engine.mark()

            if mode == 0:
                # Infinite search, then stop from the UCI thread.
                engine.send("go infinite")
                time.sleep(0.02)
                ready_mark = engine.mark()
                engine.send("isready")
                if not engine.wait_for("readyok", timeout=2.0, start=ready_mark):
                    result["errors"].append(f"cycle {cycle}: no readyok during search")
                engine.send("stop")
                best_line, elapsed, _ = engine.wait_bestmove(timeout=3.0, start=start)
            elif mode == 1:
                # Node-limited search.
                engine.send("go nodes 4000")
                best_line, elapsed, _ = engine.wait_bestmove(timeout=3.0, start=start)
            else:
                best_line, elapsed, _ = engine.search(
                    f"movetime {args.movetime}",
                    timeout=args.movetime / 1000.0 + 3.0,
                )

            result["max_latency_ms"] = max(
                result["max_latency_ms"], int(elapsed * 1000)
            )
            if best_line is None:
                result["timeouts"] += 1
                result["errors"].append(f"cycle {cycle}: search timeout")
                continue
            uci = parse_best(best_line)
            if not uci:
                result["errors"].append(f"cycle {cycle}: malformed bestmove")
                continue
            if uci == "0000":
                result["null_bestmove"] += 1
                result["errors"].append(f"cycle {cycle}: null bestmove")
                continue
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                result["illegal"] += 1
                result["errors"].append(f"cycle {cycle}: bad uci {uci}")
                continue
            if move not in board.legal_moves:
                result["illegal"] += 1
                result["errors"].append(f"cycle {cycle}: illegal move {uci}")

            if cycle % 40 == 0 and tb_available:
                engine.send(f"setoption name SyzygyPath value {tb_setting}")
                if not engine.wait_ready():
                    result["errors"].append(f"cycle {cycle}: no readyok after TB path")
                engine.set_position(fen="7k/8/8/8/8/8/8/KQ6 w - - 0 1")
                tb_line, _, tb_lines = engine.search("movetime 50", timeout=5.0)
                if not tb_line or "bestmove" not in tb_line:
                    result["errors"].append(f"cycle {cycle}: TB search failed")
                elif not any(line.startswith("info depth 0") for line in tb_lines):
                    result["errors"].append(f"cycle {cycle}: TB root probe not used")
                else:
                    result["tb_tests"] += 1

                # Root probe is disabled by limit 3; the search must use the
                # thread-safe WDL probe on 3-piece children of a 4-piece root.
                engine.send("setoption name SyzygyProbeLimit value 3")
                engine.wait_ready()
                engine.set_position(fen="7k/8/8/8/8/8/6r1/KQ6 w - - 0 1")
                node_line, _, _ = engine.search("movetime 50", timeout=5.0)
                if not node_line or "bestmove" not in node_line:
                    result["errors"].append(f"cycle {cycle}: TB node search failed")
                else:
                    result["tb_node_tests"] = result.get("tb_node_tests", 0) + 1
                engine.send("setoption name SyzygyProbeLimit value 7")

                engine.send("setoption name SyzygyPath value <empty>")
                engine.wait_ready()

            if not engine.alive():
                result["errors"].append(f"cycle {cycle}: engine died")
                break
    finally:
        engine.close()

    result["elapsed_s"] = round(time.time() - t0, 2)
    ok = not result["errors"]
    result["ok"] = ok
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(
        f"stress: {args.cycles} cycles, {args.threads} threads, "
        f"errors={len(result['errors'])}, timeouts={result['timeouts']}, "
        f"illegal={result['illegal']}, null={result['null_bestmove']}, "
        f"tb={result['tb_tests']}/{result['tb_node_tests']}, "
        f"max_latency={result['max_latency_ms']} ms, "
        f"{result['elapsed_s']}s"
    )
    if result["errors"]:
        for err in result["errors"][:10]:
            print("  " + err)
    print(f"log: {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
