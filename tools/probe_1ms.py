#!/usr/bin/env python3
"""Probe a real engine with a 1 ms movetime on complex positions.

This is a smoke test for the "1 ms on a complex position" timeout scenario:
a well-behaved engine should still return a legal ``bestmove`` long before
the arena's hard timeout.  The forced-hang timeout path is covered by
``test_harness.py`` with ``fake_engine.py``.

Example::

    python experiments/arena_harness/probe_1ms.py --engine .\\chessbot_fixed.exe
"""
from __future__ import annotations

import argparse
import os
import sys

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import arena  # noqa: E402

POSITIONS = {
    "kiwipete": "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "middlegame": "r4rk1/1pp1qppp/p1np1n2/2b1p3/2B1P3/P1NP1N2/1PP1QPPP/R1B1K2R w KQ - 1 10",
    "tactical": "r1b1k2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 6 6",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--movetime", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--option", action="append", default=[], metavar="NAME=VALUE")
    args = ap.parse_args(argv)

    options = arena.parse_options(args.option)
    engine = arena.UciEngine(
        args.engine,
        options,
        name="probe",
        startup_timeout=30.0,
        log=lambda m: print(m, file=sys.stderr),
    )
    ok = True
    try:
        engine.new_game()
        for name, fen in POSITIONS.items():
            board = chess.Board(fen)
            command = arena.build_position_command(board)
            try:
                token, elapsed, line = engine.search_move(
                    command,
                    f"go movetime {args.movetime}",
                    hard_timeout=args.timeout,
                )
            except arena.EngineError as exc:
                print(f"{name}: ERROR [{exc.kind}] {exc}")
                ok = False
                engine.new_game()
                continue
            try:
                move, _, _ = arena.coerce_move(board, token)
            except arena.EngineError as exc:
                print(f"{name}: BAD MOVE {token!r} [{exc.kind}] {exc}")
                ok = False
                continue
            print(
                f"{name}: {line!r} legal={move is not None} "
                f"elapsed={elapsed * 1000.0:.1f} ms "
                f"timeout={args.timeout * 1000.0:.0f} ms"
            )
    finally:
        engine.quit()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
