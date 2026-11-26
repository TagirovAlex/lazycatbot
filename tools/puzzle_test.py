#!/usr/bin/env python3
"""Run Lichess tactical puzzles against a UCI engine.

The official Lichess puzzle database stores the position *before* the
opponent's setup move. The first move in `Moves` is played by the opponent;
solver moves are indices 1, 3, 5, ... . A puzzle is solved when the engine
plays every solver move correctly. For mate-in-one puzzles any move that
checkmates is accepted.

Examples:
  python tools/puzzle_test.py --puzzles 100 --movetime 100 --threads 1
  python tools/puzzle_test.py --puzzles 300 --movetime 1000 --threads 4 \
      --sample-in results/puzzles_sample_300_1234.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import sys
import time
import zlib
from collections import Counter, defaultdict

import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from uci_engine import Engine  # noqa: E402

RATING_BUCKETS = [
    (0, 999, "<1000"),
    (1000, 1399, "1000-1399"),
    (1400, 1799, "1400-1799"),
    (1800, 2199, "1800-2199"),
    (2200, 2599, "2200-2599"),
    (2600, 9999, "2600+"),
]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{100.0 * k / n:5.1f}% (95% CI {100*lo:.1f}-{100*hi:.1f})"


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def row_to_puzzle(row: list[str]) -> dict | None:
    if len(row) < 8:
        return None
    pid, fen, moves, rating, rd, pop, plays = row[:7]
    if not pid or not fen or not moves:
        return None
    return {
        "id": pid,
        "fen": fen,
        "moves": moves.strip(),
        "rating": parse_int(rating, 0),
        "rd": parse_int(rd, 0),
        "popularity": parse_int(pop, 0),
        "plays": parse_int(plays, 0),
        "themes": row[7].strip().split() if len(row) > 7 and row[7] else [],
        "url": f"https://lichess.org/training/{pid}",
        "opening": row[9] if len(row) > 9 else "",
    }


def read_db(path: str, limit: int = 0):
    import zstandard as zstd

    with open(path, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        with dctx.stream_reader(fh) as reader:
            text = io.TextIOWrapper(reader, encoding="utf-8", newline="")
            reader_csv = csv.reader(text)
            first = True
            for row in reader_csv:
                if first:
                    first = False
                    if row and row[0].strip().lower() in ("puzzleid", "puzzle_id"):
                        continue
                if limit and reader_csv.line_num > limit:
                    break
                yield row


def sample_puzzles(args) -> list[dict]:
    rng = random.Random(args.seed)
    reservoir: list[dict] = []
    seen = 0
    wanted_themes = set(args.themes.split(",")) if args.themes else set()

    for row in read_db(args.db, args.scan_limit):
        p = row_to_puzzle(row)
        if p is None:
            continue
        if not (args.min_rating <= p["rating"] <= args.max_rating):
            continue
        if p["popularity"] < args.min_popularity:
            continue
        if p["plays"] < args.min_plays:
            continue
        if wanted_themes and not wanted_themes.issubset(set(p["themes"])):
            continue
        # Reservoir sampling over the complete filtered stream.
        seen += 1
        if len(reservoir) < args.puzzles:
            reservoir.append(p)
        else:
            j = rng.randrange(seen)
            if j < args.puzzles:
                reservoir[j] = p

    rng.shuffle(reservoir)
    return reservoir


def load_sample(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        puzzles = json.load(f)
    if not isinstance(puzzles, list):
        raise ValueError("sample file must contain a JSON list")
    return puzzles


def parse_info(lines: list[str]) -> dict:
    depth = 0
    seldepth = 0
    nodes = 0
    score = ""
    for line in lines:
        if not line.startswith("info "):
            continue
        m = re.search(r"\bdepth (\d+)", line)
        if m:
            depth = int(m.group(1))
        m = re.search(r"\bseldepth (\d+)", line)
        if m:
            seldepth = int(m.group(1))
        m = re.search(r"\bnodes (\d+)", line)
        if m:
            nodes = int(m.group(1))
        m = re.search(r"\bscore (cp|mate) (-?\d+)", line)
        if m:
            score = f"{m.group(1)} {m.group(2)}"
    return {"depth": depth, "seldepth": seldepth, "nodes": nodes, "score": score}


def run_puzzle(engine: Engine, puzzle: dict, movetime: int,
               timeout_extra: float) -> dict:
    result = {
        "id": puzzle["id"],
        "rating": puzzle["rating"],
        "themes": puzzle["themes"],
        "popularity": puzzle["popularity"],
        "plays": puzzle["plays"],
        "url": puzzle["url"],
        "fen": puzzle["fen"],
        "moves": puzzle["moves"],
        "solved": False,
        "first_correct": False,
        "moves_attempted": 0,
        "moves_correct": 0,
        "failed_at": 0,
        "expected": "",
        "played": "",
        "error": "",
        "time_ms": 0,
        "depth": 0,
        "seldepth": 0,
        "nodes": 0,
        "score": "",
    }

    try:
        board = chess.Board(puzzle["fen"])
        uci_moves = puzzle["moves"].split()
        if len(uci_moves) < 2:
            result["error"] = "no-solution"
            return result
        setup = chess.Move.from_uci(uci_moves[0])
        if setup not in board.legal_moves:
            result["error"] = "illegal-setup"
            return result
        board.push(setup)
        if board.is_game_over():
            result["error"] = "game-over-after-setup"
            return result
    except ValueError as exc:
        result["error"] = f"bad-puzzle:{exc}"
        return result

    solution = uci_moves[1::2]
    replies = uci_moves[2::2]
    result["solution_len"] = len(solution)
    total_time = 0.0

    for idx, expected in enumerate(solution):
        try:
            expected_move = chess.Move.from_uci(expected)
        except ValueError:
            result["error"] = f"bad-expected:{expected}"
            break
        if expected_move not in board.legal_moves:
            result["error"] = f"illegal-expected:{expected}"
            break

        engine.set_position(fen=board.fen())
        best_line, elapsed, lines = engine.search(
            f"movetime {movetime}",
            timeout=movetime / 1000.0 + timeout_extra,
        )
        total_time += elapsed
        info = parse_info(lines)
        result["depth"] = info["depth"]
        result["seldepth"] = info["seldepth"]
        result["nodes"] = info["nodes"]
        result["score"] = info["score"]

        result["moves_attempted"] += 1
        result["failed_at"] = idx + 1
        result["expected"] = expected

        if best_line is None:
            result["error"] = "timeout"
            break
        parts = best_line.split()
        if len(parts) < 2:
            result["error"] = "no-bestmove"
            break
        played = parts[1]
        result["played"] = played
        if played == "0000":
            result["error"] = "null-bestmove"
            break

        try:
            move = chess.Move.from_uci(played)
        except ValueError:
            result["error"] = f"bad-uci:{played}"
            break
        if move not in board.legal_moves:
            result["error"] = f"illegal-move:{played}"
            break

        correct = played == expected
        if not correct:
            # Lichess accepts any mate-in-one as a solution.
            board.push(move)
            if board.is_checkmate() and idx + 1 == len(solution):
                correct = True
            board.pop()
        if not correct:
            break

        result["moves_correct"] += 1
        if idx == 0:
            result["first_correct"] = True
        result["failed_at"] = 0

        board.push(move)
        if idx < len(replies):
            reply = chess.Move.from_uci(replies[idx])
            if reply not in board.legal_moves:
                result["error"] = "illegal-reply"
                break
            board.push(reply)

    result["solved"] = (
        not result["error"]
        and result["moves_correct"] == result["solution_len"]
    )
    result["time_ms"] = int(total_time * 1000)
    return result


def bucket_for(rating: int) -> str:
    for lo, hi, name in RATING_BUCKETS:
        if lo <= rating <= hi:
            return name
    return "?"


def summarise(results: list[dict], args) -> dict:
    n = len(results)
    solved = sum(1 for r in results if r["solved"])
    first = sum(1 for r in results if r["first_correct"])
    attempts = sum(r["moves_attempted"] for r in results)
    correct_moves = sum(r["moves_correct"] for r in results)
    errors = [r for r in results if r["error"]]
    times = [r["time_ms"] for r in results if r["time_ms"] > 0]
    depths = [r["depth"] for r in results if r["depth"] > 0]

    by_rating = defaultdict(lambda: [0, 0, 0])
    for r in results:
        b = bucket_for(r["rating"])
        by_rating[b][1] += 1
        by_rating[b][0] += int(r["solved"])
        by_rating[b][2] += int(r["first_correct"])

    by_theme = defaultdict(lambda: [0, 0])
    for r in results:
        for t in r["themes"]:
            by_theme[t][1] += 1
            by_theme[t][0] += int(r["solved"])

    by_len = defaultdict(lambda: [0, 0])
    for r in results:
        by_len[r.get("solution_len", 0)][1] += 1
        by_len[r.get("solution_len", 0)][0] += int(r["solved"])

    def table(mapping, min_n=1):
        out = []
        for key, values in mapping.items():
            s, total = values[0], values[1]
            if total < min_n:
                continue
            lo, hi = wilson(s, total)
            out.append({
                "key": key,
                "solved": s,
                "total": total,
                "rate": s / total,
                "ci_low": lo,
                "ci_high": hi,
            })
        return out

    return {
        "engine": os.path.abspath(args.engine),
        "db": os.path.abspath(args.db),
        "movetime_ms": args.movetime,
        "threads": args.threads,
        "hash_mb": args.hash,
        "puzzles": n,
        "solved": solved,
        "solved_rate": solved / n if n else 0.0,
        "solved_ci": wilson(solved, n),
        "first_correct": first,
        "first_rate": first / n if n else 0.0,
        "first_ci": wilson(first, n),
        "move_attempts": attempts,
        "moves_correct": correct_moves,
        "move_rate": correct_moves / attempts if attempts else 0.0,
        "errors": len(errors),
        "mean_time_ms": sum(times) / len(times) if times else 0.0,
        "mean_depth": sum(depths) / len(depths) if depths else 0.0,
        "by_rating": table(by_rating),
        "by_theme": table(by_theme, min_n=10),
        "by_solution_len": table(by_len),
        "items": [
            {
                "id": r["id"],
                "solved": bool(r["solved"]),
                "first": bool(r["first_correct"]),
                "time_ms": r["time_ms"],
                "depth": r["depth"],
                "nodes": r["nodes"],
                "failed_at": r["failed_at"],
                "error": r["error"],
            }
            for r in results
        ],
        "failures": [r for r in results if not r["solved"]],
    }


def print_summary(s: dict) -> None:
    print()
    print("=== Lichess puzzle test ===")
    print(f"engine:    {s['engine']}")
    print(f"settings:  {s['threads']} thread(s), Hash {s['hash_mb']} MB, "
          f"{s['movetime_ms']} ms/move")
    print(f"puzzles:   {s['puzzles']}")
    print(f"solved:    {s['solved']}/{s['puzzles']}  {pct(s['solved'], s['puzzles'])}")
    print(f"first move:{s['first_correct']}/{s['puzzles']}  {pct(s['first_correct'], s['puzzles'])}")
    print(f"move-level:{s['moves_correct']}/{s['move_attempts']}  {pct(s['moves_correct'], s['move_attempts'])}")
    print(f"errors:    {s['errors']}")
    print(f"mean:      {s['mean_time_ms']:.0f} ms, depth {s['mean_depth']:.1f}")
    print()
    print("By puzzle rating:")
    for row in s["by_rating"]:
        print(f"  {row['key']:>10}  {row['solved']:4}/{row['total']:<4}  {pct(row['solved'], row['total'])}")
    print()
    print("By solution length:")
    for row in sorted(s["by_solution_len"], key=lambda x: x["key"]):
        print(f"  {row['key']:>2} moves  {row['solved']:4}/{row['total']:<4}  {pct(row['solved'], row['total'])}")
    print()
    print("Themes with >=10 puzzles (best/worst):")
    themes = sorted(s["by_theme"], key=lambda x: (-x["rate"], -x["total"]))
    for row in themes[:12]:
        print(f"  {row['key']:<22} {row['solved']:4}/{row['total']:<4}  {pct(row['solved'], row['total'])}")
    if len(themes) > 20:
        print("  ...")
        for row in themes[-8:]:
            print(f"  {row['key']:<22} {row['solved']:4}/{row['total']:<4}  {pct(row['solved'], row['total'])}")
    print()


def write_failures_csv(path: str, failures: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "puzzle_id", "rating", "themes", "url", "expected", "played",
            "failed_at", "error", "fen", "moves",
        ])
        for r in failures:
            writer.writerow([
                r["id"], r["rating"], " ".join(r["themes"]), r["url"],
                r["expected"], r["played"], r["failed_at"], r["error"],
                r["fen"], r["moves"],
            ])


def main() -> int:
    ap = argparse.ArgumentParser(description="Test a UCI engine on Lichess puzzles")
    ap.add_argument("--engine", default=os.path.join(ROOT, "chessbot.exe"))
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "puzzles",
                                               "lichess_db_puzzle.csv.zst"))
    ap.add_argument("--puzzles", type=int, default=100)
    ap.add_argument("--movetime", type=int, default=100)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--hash", type=int, default=128)
    ap.add_argument("--timeout-extra", type=float, default=3.0)
    ap.add_argument("--min-rating", type=int, default=600)
    ap.add_argument("--max-rating", type=int, default=3000)
    ap.add_argument("--min-popularity", type=int, default=50)
    ap.add_argument("--min-plays", type=int, default=100)
    ap.add_argument("--themes", default="")
    ap.add_argument("--set-option", action="append", default=[],
                    metavar="NAME=VALUE",
                    help="extra UCI option for the engine (repeatable)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--scan-limit", type=int, default=0,
                    help="read only the first N CSV lines (0 = whole DB)")
    ap.add_argument("--sample-in", default="")
    ap.add_argument("--sample-out", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--failures", default="")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    tag = args.tag or f"t{args.threads}_m{args.movetime}_n{args.puzzles}_s{args.seed}"
    args.out = args.out or os.path.join(ROOT, "results", f"puzzles_{tag}.json")
    args.failures = args.failures or os.path.join(ROOT, "results", f"puzzles_{tag}_failures.csv")
    args.sample_out = args.sample_out or os.path.join(
        ROOT, "results", f"puzzles_sample_{args.puzzles}_{args.seed}.json")

    if args.sample_in and os.path.exists(args.sample_in):
        puzzles = load_sample(args.sample_in)
        print(f"loaded {len(puzzles)} puzzles from {args.sample_in}")
    else:
        if not os.path.exists(args.db):
            print(f"missing puzzle DB: {args.db}", file=sys.stderr)
            print("download: https://database.lichess.org/lichess_db_puzzle.csv.zst",
                  file=sys.stderr)
            return 1
        t0 = time.time()
        puzzles = sample_puzzles(args)
        print(f"sampled {len(puzzles)} puzzles in {time.time()-t0:.1f}s")
        if args.sample_out:
            os.makedirs(os.path.dirname(args.sample_out) or ".", exist_ok=True)
            with open(args.sample_out, "w", encoding="utf-8") as f:
                json.dump(puzzles, f, ensure_ascii=False, indent=1)
            print(f"sample -> {args.sample_out}")

    if not puzzles:
        print("no puzzles selected", file=sys.stderr)
        return 1

    engine = Engine(args.engine)
    try:
        uci_lines = engine.uci()
        is_chessbot = any(
            line.startswith("id name") and "ChessBot" in line
            for line in uci_lines
        )
        engine.send(f"setoption name Threads value {args.threads}")
        engine.send(f"setoption name Hash value {args.hash}")
        if is_chessbot:
            engine.send("setoption name UseNNUE value false")
        for opt in args.set_option:
            if "=" not in opt:
                raise ValueError(f"bad --set-option (need NAME=VALUE): {opt}")
            name, value = opt.split("=", 1)
            engine.send(f"setoption name {name} value {value}")
        if not engine.wait_ready():
            raise RuntimeError("engine did not answer isready")
        engine.newgame()

        results = []
        t0 = time.time()
        for i, puzzle in enumerate(puzzles, 1):
            r = run_puzzle(engine, puzzle, args.movetime, args.timeout_extra)
            results.append(r)
            if i % 25 == 0 or i == len(puzzles):
                solved = sum(1 for x in results if x["solved"])
                print(f"  {i}/{len(puzzles)}  solved {solved} "
                      f"({100.0*solved/i:.1f}%)  {time.time()-t0:.0f}s",
                      flush=True)
    finally:
        engine.close()

    summary = summarise(results, args)
    summary["elapsed_s"] = time.time() - t0
    summary["tag"] = tag
    summary["sample"] = args.sample_in or args.sample_out
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    write_failures_csv(args.failures, summary["failures"])
    print_summary(summary)
    print(f"json:     {args.out}")
    print(f"failures: {args.failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
