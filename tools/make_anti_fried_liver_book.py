#!/usr/bin/env python3
"""Build a tiny Polyglot book that only avoids the Fried Liver trap.

Position after 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Ng5 d5 5.exd5:

    r1bqkb1r/ppp2ppp/2n2n2/3Pp1N1/2B5/8/PPPP1PPP/RNBQK2R b KQkq - 0 5

Entries: 5...Na5 (weight 30) and 5...b5 (weight 2).  Every other opening
position is absent, so the engine plays its own moves everywhere else.
"""
from __future__ import annotations

import os
import struct

import chess
import chess.polyglot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "books", "anti_fried_liver.bin")
FEN = "r1bqkb1r/ppp2ppp/2n2n2/3Pp1N1/2B5/8/PPPP1PPP/RNBQK2R b KQkq - 0 5"

PROMO = {None: 0, chess.KNIGHT: 1, chess.BISHOP: 2, chess.ROOK: 3, chess.QUEEN: 4}


def raw_move(move: chess.Move) -> int:
    # Polyglot packs the destination square in the low six bits and the
    # origin square in bits 6-11.
    return (move.to_square | (move.from_square << 6) |
            (PROMO[move.promotion] << 12))


def main() -> int:
    board = chess.Board(FEN)
    key = chess.polyglot.zobrist_hash(board)
    entries = [(chess.Move.from_uci("c6a5"), 30), (chess.Move.from_uci("b7b5"), 2)]
    blob = b"".join(
        struct.pack(">QHHI", key, raw_move(move), weight, 0)
        for move, weight in entries
    )
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(blob)
    print(f"wrote {OUT} ({len(blob)} bytes, key {key:#x})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
