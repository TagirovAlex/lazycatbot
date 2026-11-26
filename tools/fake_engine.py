#!/usr/bin/env python3
"""Tiny scriptable UCI engine used by the arena protocol tests.

It is deliberately *not* a chess engine: its job is to exercise the arena's
error handling (timeouts, stale bestmoves, null moves, illegal moves) in a
repeatable way.

Modes
-----
legal       first legal move (or 0000 in a terminal position)
zero        always ``bestmove 0000``
illegal     always ``bestmove e2e5``
stale-once  first process sleeps, then emits ``bestmove e2e4``; after a
            restart (marker file exists) it behaves like ``legal``
hang        sleeps for 30 s on every ``go`` (the arena must kill it)
crash       exits immediately on the first ``go``

Usage::

    python fake_engine.py stale-once --marker path/to/marker

The marker is created at process start, before the slow search, so an arena
that kills the hung process and restarts it still sees the marker.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import chess


def parse_position(tokens: list[str]) -> chess.Board:
    """Parse 'position startpos|fen ... [moves ...]' into a board."""
    board = chess.Board()
    if len(tokens) < 2:
        return board
    if tokens[1] == "startpos":
        board = chess.Board()
        idx = 2
    elif tokens[1] == "fen":
        try:
            idx = tokens.index("moves", 2)
        except ValueError:
            idx = len(tokens)
        fen = " ".join(tokens[2:idx])
        try:
            board = chess.Board(fen)
        except ValueError:
            board = chess.Board()
    else:
        return board
    if idx < len(tokens) and tokens[idx] == "moves":
        for uci in tokens[idx + 1 :]:
            try:
                board.push_uci(uci)
            except ValueError:
                break
    return board


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["legal", "zero", "illegal", "stale-once", "hang", "crash"])
    ap.add_argument("--marker", default=None, help="marker file for stale-once")
    args = ap.parse_args(argv)

    stale_pending = False
    if args.mode == "stale-once":
        if args.marker and not os.path.exists(args.marker):
            with open(args.marker, "w", encoding="utf-8") as fh:
                fh.write("used\n")
            stale_pending = True
        elif args.marker:
            stale_pending = False
        else:
            stale_pending = True

    board = chess.Board()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        cmd = line.split()
        head = cmd[0]
        if head == "uci":
            print("id name FakeEngine-" + args.mode, flush=True)
            print("id author arena_harness tests", flush=True)
            print("option name Hash type spin default 1 min 1 max 16", flush=True)
            print("option name Clear Hash type button", flush=True)
            print("uciok", flush=True)
        elif head == "isready":
            print("readyok", flush=True)
        elif head == "ucinewgame":
            board = chess.Board()
        elif head == "setoption":
            pass
        elif head == "position":
            board = parse_position(cmd)
        elif head == "go":
            if args.mode == "crash":
                os._exit(3)
            if args.mode == "hang":
                time.sleep(30.0)
                print("bestmove 0000", flush=True)
            elif args.mode == "stale-once" and stale_pending:
                stale_pending = False
                time.sleep(1.5)
                # Deliberately emit a move without regard to the current
                # position: this is the stale bestmove the arena must discard.
                print("bestmove e2e4", flush=True)
            elif args.mode == "zero":
                print("bestmove 0000", flush=True)
            elif args.mode == "illegal":
                print("bestmove e2e5", flush=True)
            else:
                move = next(iter(board.legal_moves), None)
                print("bestmove " + (move.uci() if move else "0000"), flush=True)
        elif head == "stop":
            # A single-threaded fake: 'stop' is ignored; the arena must kill
            # the process instead of waiting for a cooperative stop.
            pass
        elif head == "quit":
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
