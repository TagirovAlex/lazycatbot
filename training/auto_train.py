#!/usr/bin/env python3
"""One-command HalfKP training orchestrator.

It downloads the selected dataset (unless present), extracts HalfKP features,
trains the value network, and exports weights. Nothing is integrated into the
engine automatically; a model that does not pass matches must stay disabled.

Examples:
  python training/auto_train.py --dataset quiet --smoke
  python training/auto_train.py --dataset lichess --positions 20000000 --device cuda
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "training"
SRC = ROOT / "src"
DEFAULT_WORK = TRAIN / "work"


def run(cmd, **kwargs):
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def ensure_zstd():
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        print("zstandard missing; installing (small package)...", flush=True)
        try:
            run([sys.executable, "-m", "pip", "install", "zstandard"])
            return True
        except subprocess.CalledProcessError:
            print("could not install zstandard; run: pip install zstandard", file=sys.stderr)
            return False


def build_extractor(work: Path) -> Path:
    gxx = shutil.which("g++") or shutil.which("clang++")
    if not gxx:
        raise RuntimeError("g++/clang++ not found")
    exe = work / ("extract_halfkp.exe" if os.name == "nt" else "extract_halfkp")
    sources = [
        str(TRAIN / "extract_halfkp.cpp"),
        str(SRC / "bitboards.cpp"),
        str(SRC / "position.cpp"),
        str(SRC / "eval.cpp"),
        str(SRC / "nnue_weights.cpp"),
    ]
    cmd = [
        gxx, "-std=c++20", "-O3", "-march=native", "-pthread",
        "-I", str(SRC), "-o", str(exe),
    ] + sources
    if os.name == "nt":
        cmd += ["-Wl,--stack,16777216"]
    run(cmd)
    return exe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["lichess", "quiet"], default="lichess")
    ap.add_argument("--positions", type=int, default=20_000_000)
    ap.add_argument("--hidden", type=int, default=1024)
    ap.add_argument("--batch", type=int, default=16384)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    ap.add_argument("--workdir", default=str(DEFAULT_WORK))
    ap.add_argument("--input", default="", help="local FEN|target or .epd")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--skip-train", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    try:
        import torch
    except ImportError:
        print("PyTorch missing. Install it first.", file=sys.stderr)
        return 1

    work = Path(args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    train_dir = work / "halfkp"
    model_dir = work / "model"
    positions = work / ("quiet_positions.txt" if args.dataset == "quiet" else "lichess_positions.txt")

    if args.smoke:
        args.positions = 2000
        args.hidden = 128
        args.batch = 512
        args.epochs = 1
        args.dataset = "quiet"
        positions = work / "smoke_positions.txt"
        train_dir = work / "smoke_halfkp"
        model_dir = work / "smoke_model"
        print("SMOKE MODE: tiny CPU run, not a strength test")

    if not positions.exists():
        if args.dataset == "quiet":
            inp = args.input or str(ROOT / "data" / "quiet-labeled.epd")
            run([
                sys.executable, str(TRAIN / "prepare_quiet.py"),
                "--input", inp, "--out", str(positions),
                "--positions", str(args.positions),
            ])
        else:
            if not ensure_zstd():
                return 1
            cmd = [
                sys.executable, str(TRAIN / "download_lichess_evals.py"),
                "--positions", str(args.positions), "--out", str(positions),
                "--cache", str(work / "lichess_db_eval.jsonl.zst"),
            ]
            if args.input:
                cmd += ["--input", args.input]
            if args.skip_download:
                cmd += ["--input", str(work / "lichess_db_eval.jsonl.zst")]
            run(cmd)

    exe = work / ("extract_halfkp.exe" if os.name == "nt" else "extract_halfkp")
    if not exe.exists():
        exe = build_extractor(work)

    if not args.skip_extract:
        train_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(exe), str(positions), str(train_dir), "--max", str(args.positions)]
        run(cmd)

    if not args.skip_train:
        cmd = [
            sys.executable, str(TRAIN / "train_halfkp.py"),
            "--data", str(train_dir), "--out", str(model_dir),
            "--hidden", str(args.hidden), "--batch", str(args.batch),
            "--epochs", str(args.epochs), "--lr", str(args.lr),
            "--device", args.device,
        ]
        run(cmd)

    print(f"\nartifacts: {model_dir}")
    print("NOTE: this model is not wired into the engine yet. Validate NPS and matches before enabling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
