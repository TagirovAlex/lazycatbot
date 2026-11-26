#!/usr/bin/env python3
"""Build a Polyglot opening book from the public Lichess masters explorer.

The explorer is queried by FEN, so no multi-gigabyte PGN download is needed.
Only positions reachable by the most popular master moves are included.

Outputs:
  data/books/lichess_masters.bin
  data/books/lichess_masters.manifest.json
  data/books/lichess_masters.cache.json   (resume cache)

Usage:
  python tools/make_lichess_book.py --depth 10 --max-moves 2
  python tools/make_lichess_book.py --depth 10 --max-positions 1500
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
from pathlib import Path

import chess
import requests

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "data" / "books" / "lichess_masters.bin"
MANIFEST = ROOT / "data" / "books" / "lichess_masters.manifest.json"
CACHE = ROOT / "data" / "books" / "lichess_masters.cache.json"
EXPLORER = "https://explorer.lichess.ovh/masters"
HEADERS = {"User-Agent": "ChessBot-book-generator/1.0"}

sys.path.insert(0, str(ROOT / "experiments" / "book" / "tools"))
from polyglot_ref import ENTRY_STRUCT, encode_move, zobrist_key  # noqa: E402


def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, path)


def query(fen: str, cache: dict, min_games: int) -> list[dict]:
    if fen in cache:
        rows = cache[fen]
    else:
        for attempt in range(4):
            try:
                r = requests.get(
                    EXPLORER,
                    params={"fen": fen, "moves": 16, "topGames": 0},
                    headers=HEADERS,
                    timeout=20,
                )
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                rows = r.json().get("moves", [])
                cache[fen] = rows
                break
            except Exception:
                if attempt == 3:
                    rows = []
                    cache[fen] = rows
                else:
                    time.sleep(2 * (attempt + 1))
        else:
            rows = cache.get(fen, [])

    out = []
    for row in rows:
        total = int(row.get("white", 0)) + int(row.get("draws", 0)) + int(row.get("black", 0))
        if total < min_games:
            continue
        uci = row.get("uci", "")
        if not uci:
            continue
        out.append({"uci": uci, "games": total, "san": row.get("san", "")})
    out.sort(key=lambda x: -x["games"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--max-moves", type=int, default=2)
    ap.add_argument("--root-moves", type=int, default=4)
    ap.add_argument("--min-games", type=int, default=20)
    ap.add_argument("--max-positions", type=int, default=0)
    ap.add_argument("--out", default=str(BOOK))
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()

    out_path = Path(args.out)
    manifest_path = Path(args.manifest)
    cache_path = Path(args.cache)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = load_cache(cache_path)

    entries: dict[tuple[int, int], int] = {}
    positions: list[dict] = []
    seen: set[int] = set()
    queue: list[tuple[chess.Board, int]] = [(chess.Board(), 0)]
    t0 = time.time()
    processed = 0

    while queue:
        board, depth = queue.pop(0)
        if args.max_positions and processed >= args.max_positions:
            break
        key = zobrist_key(board)
        if key in seen:
            continue
        seen.add(key)
        if board.is_game_over():
            continue

        rows = query(board.fen(), cache, args.min_games)
        if not rows:
            continue
        take = args.root_moves if depth == 0 else args.max_moves
        chosen = rows[:take]
        pos_moves = []
        for row in chosen:
            try:
                move = chess.Move.from_uci(row["uci"])
            except ValueError:
                continue
            if move not in board.legal_moves:
                continue
            # Scale games into a 16-bit Polyglot weight.  Keep relative order.
            weight = max(1, min(65535, row["games"] // 100))
            ekey = (key, encode_move(move))
            entries[ekey] = max(entries.get(ekey, 0), weight)
            pos_moves.append({
                "uci": row["uci"],
                "san": row["san"],
                "games": row["games"],
                "weight": weight,
            })
            if depth < args.depth:
                child = board.copy(stack=False)
                child.push(move)
                if not child.is_game_over():
                    queue.append((child, depth + 1))

        if pos_moves:
            positions.append({
                "fen": board.fen(),
                "key": key,
                "depth": depth,
                "moves": pos_moves,
            })
        processed += 1
        if processed % 50 == 0:
            print(
                f"positions {processed}, queue {len(queue)}, entries {len(entries)}, "
                f"elapsed {time.time()-t0:.0f}s",
                flush=True,
            )
            save_cache(cache_path, cache)
            if processed % 250 == 0:
                print("  cache saved", flush=True)

    if not entries:
        print("no book entries generated", file=sys.stderr)
        return 1

    save_cache(cache_path, cache)
    rows = [(key, move, weight) for (key, move), weight in entries.items()]
    rows.sort(key=lambda x: x[0])
    with open(out_path, "wb") as f:
        for key, move, weight in rows:
            f.write(ENTRY_STRUCT.pack(key, move, weight, 0))

    # Verify every manifest move against a fresh board and the written key.
    bad = 0
    for pos in positions:
        board = chess.Board(pos["fen"])
        if zobrist_key(board) != pos["key"]:
            bad += 1
            continue
        for m in pos["moves"]:
            try:
                move = chess.Move.from_uci(m["uci"])
            except ValueError:
                bad += 1
                continue
            if move not in board.legal_moves:
                bad += 1

    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    manifest = {
        "book": out_path.name,
        "format": "polyglot",
        "entry_size": ENTRY_STRUCT.size,
        "entries": len(rows),
        "positions": len(positions),
        "size_bytes": out_path.stat().st_size,
        "sha256": digest,
        "source": EXPLORER,
        "depth": args.depth,
        "max_moves": args.max_moves,
        "root_moves": args.root_moves,
        "min_games": args.min_games,
        "coverage": {
            "positions": len(positions),
            "edges": len(rows),
            "first_moves": next(
                (p["moves"] for p in positions if p["fen"] == chess.STARTING_FEN), []
            ),
        },
        "verification": {
            "bad_key_or_move": bad,
            "ok": bad == 0,
        },
        "positions_data": positions,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print(
        f"book: {out_path} entries={len(rows)} positions={len(positions)} "
        f"size={out_path.stat().st_size} bad={bad} sha256={digest}",
        flush=True,
    )
    print(f"manifest: {manifest_path}", flush=True)
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
