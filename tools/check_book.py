#!/usr/bin/env python3
"""Sanity-check the anti-Fried-Liver book.

Position after 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Ng5 d5 5.exd5:

    r1bqkb1r/ppp2ppp/2n2n2/3Pp1N1/2B5/8/PPPP1PPP/RNBQK2R b KQkq - 0 5

The tiny book must offer the safe Polerio/Ulvestad moves (Na5/b5) and must
not offer the Fried Liver blunder Nxd5.  It contains no startpos entries, so
the engine keeps its own opening choices everywhere else.
"""
from __future__ import annotations

import os
import sys

import chess
import chess.polyglot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK = os.path.join(ROOT, "data", "books", "anti_fried_liver.bin")
FEN = "r1bqkb1r/ppp2ppp/2n2n2/3Pp1N1/2B5/8/PPPP1PPP/RNBQK2R b KQkq - 0 5"
SAFE = {"c6a5": "Na5", "b7b5": "b5"}
BAD = "c6d5"  # 5...Nxd5?, the Fried Liver blunder


def main() -> int:
    if not os.path.isfile(BOOK):
        print(f"book not found: {BOOK}")
        return 1

    board = chess.Board(FEN)
    with chess.polyglot.open_reader(BOOK) as reader:
        entries = {entry.move.uci(): entry.weight for entry in reader.find_all(board)}

    print(f"position: {FEN}")
    print(f"book: {BOOK}")
    print("entries:", entries or "none")

    missing = [uci for uci in SAFE if uci not in entries]
    if missing:
        print(f"FAIL: missing safe book moves: {missing}")
        return 1
    if BAD in entries:
        print(f"FAIL: book contains the Fried Liver blunder {BAD} (5...Nxd5?)")
        return 1

    print("PASS: Na5/b5 are in the book; Nxd5 is absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
