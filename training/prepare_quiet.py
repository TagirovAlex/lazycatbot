#!/usr/bin/env python3
"""Convert Zurichess quiet-labeled.epd into FEN|win_prob lines."""
from __future__ import annotations

import argparse
import os
import re
import sys

LINE_RE = re.compile(r'^(.*)\s+c9\s+"([^"]+)"\s*;?\s*$')
RESULTS = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/quiet-labeled.epd")
    ap.add_argument("--out", default="training/work/quiet_positions.txt")
    ap.add_argument("--positions", type=int, default=0)
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"missing {args.input}", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    written = 0
    skipped = 0
    seen = set()
    with open(args.input, "r", encoding="utf-8", errors="replace") as inp, \
         open(args.out, "w", encoding="utf-8") as out:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            m = LINE_RE.match(line)
            if not m:
                skipped += 1
                continue
            fen, result = m.group(1).strip(), m.group(2)
            p = RESULTS.get(result)
            if p is None:
                skipped += 1
                continue
            if fen in seen:
                skipped += 1
                continue
            seen.add(fen)
            out.write(f"{fen}|{p:.1f}\n")
            written += 1
            if args.positions > 0 and written >= args.positions:
                break
    print(f"wrote {written} positions, skipped {skipped} -> {args.out}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
