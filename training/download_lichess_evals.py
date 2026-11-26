#!/usr/bin/env python3
"""Download and stream the Lichess evaluation database into FEN|win_prob.

The Lichess evals file is large (~17 GB compressed, CC0). We do not decompress
it to disk: zstandard is streamed and only the first N useful positions are
written. The file is kept for resume/reuse.

Position selection:
  * highest-depth evaluation in the JSON record;
  * skip mates and |cp| > --max-cp;
  * FEN is White-relative in the data; convert cp to side-to-move;
  * target = sigmoid(cp / 400), a win probability in (0,1).

Usage:
  python training/download_lichess_evals.py --positions 20000000
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request

URL = "https://database.lichess.org/lichess_db_eval.jsonl.zst"


def download(url: str, path: str) -> None:
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        print(f"using existing {path} ({os.path.getsize(path)/1e9:.2f} GB)")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"downloading {url}\n  -> {path} (resume supported)")
    curl = shutil.which("curl")
    if curl:
        subprocess.run(
            [curl, "-L", "--retry", "3", "-C", "-", "-o", path, url],
            check=True,
        )
    else:
        print("curl not found; using urllib (no resume)", file=sys.stderr)
        tmp = path + ".part"
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", type=int, default=20_000_000)
    ap.add_argument("--min-depth", type=int, default=18)
    ap.add_argument("--max-cp", type=int, default=1500)
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--input", default="", help="existing .jsonl.zst")
    ap.add_argument("--out", default="training/work/lichess_positions.txt")
    ap.add_argument("--cache", default="training/work/lichess_db_eval.jsonl.zst")
    args = ap.parse_args()

    try:
        import zstandard as zstd
    except ImportError:
        print("Missing zstandard. Run: python -m pip install zstandard", file=sys.stderr)
        return 1

    src = args.input or args.cache
    if not args.input:
        download(URL, args.cache)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out = open(args.out, "w", encoding="utf-8")
    seen = set()
    written = 0
    read = 0
    t0 = time.time()
    dctx = zstd.ZstdDecompressor()

    try:
        with open(src, "rb") as fh:
            with dctx.stream_reader(fh) as reader:
                text = io.TextIOWrapper(reader, encoding="utf-8")
                for line in text:
                    read += 1
                    if args.positions > 0 and written >= args.positions:
                        break
                    try:
                        rec = json.loads(line)
                        fen = rec["fen"]
                        evals = rec.get("evals") or []
                        if not evals:
                            continue
                        best = max(evals, key=lambda e: int(e.get("depth", 0)))
                        if int(best.get("depth", 0)) < args.min_depth:
                            continue
                        pvs = best.get("pvs") or []
                        if not pvs:
                            continue
                        pv = pvs[0]
                        if pv.get("mate") is not None:
                            continue
                        cp = pv.get("cp")
                        if cp is None:
                            continue
                        cp = int(cp)
                        if abs(cp) > args.max_cp:
                            continue
                        if not args.no_dedup:
                            if fen in seen:
                                continue
                            seen.add(fen)
                        side = fen.split()[1] if len(fen.split()) > 1 else "w"
                        if side == "b":
                            cp = -cp
                        p = 1.0 / (1.0 + math.exp(-cp / 400.0))
                        out.write(f"{fen}|{p:.7f}\n")
                        written += 1
                    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if written and written % 100000 == 0:
                        dt = time.time() - t0
                        print(
                            f"{written}/{args.positions} positions, "
                            f"{read} lines, {dt:.0f}s",
                            flush=True,
                        )
    finally:
        out.close()

    print(f"done: {written} positions from {read} lines -> {args.out}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
