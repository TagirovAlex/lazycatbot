# HalfKP-1024 training pipeline

Turnkey training for a HalfKP value network.

## Quick start (server with CUDA)

```bash
python -m pip install -r training/requirements.txt
# CUDA PyTorch, if not installed:
# pip install torch --index-url https://download.pytorch.org/whl/cu128

python training/auto_train.py --dataset lichess --positions 20000000 --gpu
```

The pipeline will:

1. check Python/PyTorch/CUDA/disk;
2. download `lichess_db_eval.jsonl.zst` (~17 GB, CC0) with resume;
3. stream it, pick the highest-depth Stockfish eval per position, convert
   White-relative cp to side-to-move win probability;
4. extract HalfKP features (64 king buckets × 12 pieces × 64 squares = 49,152);
5. train a `HalfKP -> 1024 -> 32 -> 32 -> 1` value net;
6. save checkpoints, `weights_best.pt`, `weights_best.npz`, and metrics;
7. optionally run a short validation.

Data is not committed. Everything is written under `training/work/`.

## Smoke test without downloading

Uses the local `data/quiet-labeled.epd`:

```bash
python training/auto_train.py --dataset quiet --smoke
```

This creates a tiny feature set and trains a small model on CPU to verify the
pipeline. It is not a strength test.

## Dataset options

- `lichess` — Lichess evaluation database, CC0, ~418M positions.
- `quiet` — `data/quiet-labeled.epd`, ~725k positions, MIT. Game result labels.

Lichess evals are from different Stockfish versions and depths. The downloader
selects the maximum-depth eval, drops mates and positions with `|cp| > 1500`,
and converts to win probability. Raw Stockfish `cp` must not be used directly:
its scale is compressed.

## Architecture

```
inputs: 49,152 = 64 king buckets × 12 relative pieces × 64 squares
FT:     1024 int16 (training: float32, sparse EmbeddingBag)
dense:  32 → 32 → 1
target: win probability
```

The C++ inference path is intentionally separate: export format is defined in
`weights_best.npz`; a halfKP accumulator still has to be added to the engine.
Do not enable such a model in a GUI/UCI build until the C++ forward pass and
speed/matches have been validated.

If `SparseAdam` is unavailable or errors on the server's CUDA build, add
`--dense-emb`; it is slower but 90 GB VRAM can handle it for a first run.

## Files

| File | Purpose |
|---|---|
| `auto_train.py` | Orchestrator: download, extract, train, export |
| `download_lichess_evals.py` | Streaming Lichess evals → `FEN|win_prob` |
| `prepare_quiet.py` | quiet-labeled EPD → `FEN|result` |
| `extract_halfkp.cpp` | FEN → memmapped HalfKP feature shards |
| `train_halfkp.py` | PyTorch training/export |
| `requirements.txt` | Python dependencies |

## Expected quality

This is a serious architecture, but not a guarantee of 3000 Elo. With 20–30M
Lichess positions and a value target, a well-trained net may reach roughly
2600–2850 only after C++ integration and search tuning. Data quality, label
scale, the C++ accumulator, and the search still matter.

## License

Lichess data is CC0. quiet-labeled is MIT. If you fine-tune or distill from
Stockfish weights/outputs, keep the project GPLv3-compatible and document it.
