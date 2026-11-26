#!/usr/bin/env python3
"""Train a HalfKP value network from memmapped feature shards.

Architecture: 49,152 -> FT(hidden) -> 32 -> 32 -> 1 (win probability logit).
The embedding is sparse, so a 1024-wide feature transformer is trained with
SparseAdam while the small dense head uses AdamW.

Input files (produced by extract_halfkp):
  features.u16  [N,32]
  counts.u8     [N]
  groups.u32    [N]
  targets.f32   [N]

Usage:
  python training/train_halfkp.py --data training/work/halfkp --hidden 1024 \
      --batch 16384 --epochs 8 --out training/work/model
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

INPUTS = 64 * 12 * 64


class ShardDataset(Dataset):
    def __init__(self, root: str, indices: np.ndarray):
        self.root = root
        self.indices = indices
        self.feat = np.memmap(
            os.path.join(root, "features.u16"), dtype=np.uint16, mode="r"
        )
        self.counts = np.memmap(
            os.path.join(root, "counts.u8"), dtype=np.uint8, mode="r"
        )
        self.groups = np.memmap(
            os.path.join(root, "groups.u32"), dtype=np.uint32, mode="r"
        )
        self.targets = np.memmap(
            os.path.join(root, "targets.f32"), dtype=np.float32, mode="r"
        )
        n = len(self.counts)
        if self.feat.size != n * 32:
            raise ValueError(
                f"features.u16 size {self.feat.size} != {n} * 32; corrupt shard?"
            )
        self.feat = self.feat.reshape(n, 32)
        if len(self.groups) != n or len(self.targets) != n:
            raise ValueError("shard lengths disagree")

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, i: int):
        row = int(self.indices[i])
        n = int(self.counts[row])
        feats = torch.from_numpy(
            np.asarray(self.feat[row, :n], dtype=np.int64)
        )
        return feats, float(self.targets[row])


def collate(batch):
    feats = []
    offsets = []
    targets = []
    off = 0
    for f, t in batch:
        offsets.append(off)
        feats.append(f)
        off += int(f.numel())
        targets.append(t)
    return torch.cat(feats), torch.tensor(offsets, dtype=torch.long), torch.tensor(
        targets, dtype=torch.float32
    )


class HalfKPNet(nn.Module):
    def __init__(self, hidden: int, dropout: float = 0.1):
        super().__init__()
        self.emb = nn.EmbeddingBag(INPUTS, hidden, mode="sum", sparse=True)
        self.bias = nn.Parameter(torch.zeros(hidden))
        self.fc1 = nn.Linear(hidden, 32)
        self.fc2 = nn.Linear(32, 32)
        self.out = nn.Linear(32, 1)
        self.drop = nn.Dropout(dropout)
        nn.init.normal_(self.emb.weight, mean=0.0, std=0.01)

    def forward(self, feats, offsets):
        h = self.emb(feats, offsets) + self.bias
        h = torch.relu(h)
        h = self.drop(h)
        h = torch.relu(self.fc1(h))
        h = torch.relu(self.fc2(h))
        return self.out(h).squeeze(1)


def make_indices(root: str, max_samples: int, seed: int, val_fraction: float):
    counts = np.memmap(os.path.join(root, "counts.u8"), dtype=np.uint8, mode="r")
    groups = np.memmap(os.path.join(root, "groups.u32"), dtype=np.uint32, mode="r")
    total = len(counts)
    if max_samples > 0:
        total = min(total, max_samples)
    if total < 2:
        raise ValueError("not enough samples")
    # Group split: no contiguous or near-identical FENs across train/val.
    mask = (np.asarray(groups[:total]) % 10) == 0
    val = np.flatnonzero(mask)
    train = np.flatnonzero(~mask)
    if len(val) < 100 or len(train) < 100:
        rng = np.random.default_rng(seed)
        perm = rng.permutation(total)
        cut = max(1, int(total * val_fraction))
        val = perm[:cut]
        train = perm[cut:]
    return train.astype(np.int64), val.astype(np.int64)


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    loss_sum = 0.0
    brier = 0.0
    correct = 0
    decisive = 0
    n = 0
    criterion = nn.BCEWithLogitsLoss(reduction="sum")
    for feats, offsets, target in loader:
        feats = feats.to(device, non_blocking=True)
        offsets = offsets.to(device, non_blocking=True)
        target = target.to(device, non_blocking=True)
        logits = model(feats, offsets)
        loss_sum += float(criterion(logits, target).item())
        p = torch.sigmoid(logits)
        brier += float(((p - target) ** 2).sum().item())
        mask = target != 0.5
        if mask.any():
            correct += int(((p[mask] > 0.5) == (target[mask] > 0.5)).sum().item())
            decisive += int(mask.sum().item())
        n += int(target.numel())
    return {
        "loss": loss_sum / max(n, 1),
        "brier": brier / max(n, 1),
        "acc_decisive": correct / max(decisive, 1),
        "n": n,
    }


def export(model: HalfKPNet, out_dir: str, hidden: int, card: dict):
    os.makedirs(out_dir, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(out_dir, "weights_best.pt"))
    emb = model.emb.weight.detach().cpu().numpy()
    scale = float(np.max(np.abs(emb))) / 32767.0
    if not np.isfinite(scale) or scale <= 0:
        scale = 1.0
    q = np.clip(np.rint(emb / scale), -32767, 32767).astype(np.int16)
    np.savez_compressed(
        os.path.join(out_dir, "weights_best.npz"),
        ft=q,
        ft_scale=np.float32(scale),
        ft_bias=model.bias.detach().cpu().numpy().astype(np.float32),
        fc1_w=model.fc1.weight.detach().cpu().numpy().astype(np.float32),
        fc1_b=model.fc1.bias.detach().cpu().numpy().astype(np.float32),
        fc2_w=model.fc2.weight.detach().cpu().numpy().astype(np.float32),
        fc2_b=model.fc2.bias.detach().cpu().numpy().astype(np.float32),
        out_w=model.out.weight.detach().cpu().numpy().astype(np.float32),
        out_b=model.out.bias.detach().cpu().numpy().astype(np.float32),
        inputs=np.int32(INPUTS),
        hidden=np.int32(hidden),
    )
    card = dict(card)
    card.update({"inputs": INPUTS, "hidden": hidden, "target": "win_probability"})
    with open(os.path.join(out_dir, "model_card.json"), "w", encoding="utf-8") as f:
        json.dump(card, f, indent=2)
    print(f"exported {out_dir}/weights_best.pt and .npz")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="training/work/halfkp")
    ap.add_argument("--out", default="training/work/model")
    ap.add_argument("--hidden", type=int, default=1024)
    ap.add_argument("--batch", type=int, default=16384)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--max-samples", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--resume", default="")
    ap.add_argument("--log-every", type=int, default=200)
    ap.add_argument("--dense-emb", action="store_true",
                    help="use dense gradients/Adam instead of sparse SparseAdam")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    if args.device == "cuda" and not torch.cuda.is_available():
        print("CUDA requested but torch.cuda.is_available() is false", file=sys.stderr)
        return 1
    device = torch.device(
        "cuda" if (args.device == "auto" and torch.cuda.is_available()) else
        (args.device if args.device != "auto" else "cpu")
    )
    print("device:", device, "torch:", torch.__version__)
    if device.type == "cuda":
        print("gpu:", torch.cuda.get_device_name(0))

    train_idx, val_idx = make_indices(
        args.data, args.max_samples, args.seed, val_fraction=0.1
    )
    print(f"train={len(train_idx)} val={len(val_idx)}")
    train_ds = ShardDataset(args.data, train_idx)
    val_ds = ShardDataset(args.data, val_idx)
    pin = device.type == "cuda"
    train_loader = DataLoader(
        train_ds, batch_size=args.batch, shuffle=True,
        num_workers=args.workers, collate_fn=collate, pin_memory=pin,
        drop_last=False,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch, shuffle=False,
        num_workers=args.workers, collate_fn=collate, pin_memory=pin,
        drop_last=False,
    )

    model = HalfKPNet(args.hidden).to(device)
    emb_params = list(model.emb.parameters())
    dense_params = [
        p for n, p in model.named_parameters() if not n.startswith("emb.")
    ]
    if args.dense_emb:
        model.emb = nn.EmbeddingBag(INPUTS, args.hidden, mode="sum", sparse=False).to(device)
        nn.init.normal_(model.emb.weight, mean=0.0, std=0.01)
        opt_all = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
        opt_sparse = None
    else:
        opt_sparse = torch.optim.SparseAdam(emb_params, lr=args.lr)
        opt_all = torch.optim.AdamW(dense_params, lr=args.lr, weight_decay=1e-4)

    start_epoch = 0
    best_val = float("inf")
    if args.resume and os.path.exists(args.resume):
        ckpt = torch.load(args.resume, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model"])
        if opt_sparse and "opt_sparse" in ckpt:
            opt_sparse.load_state_dict(ckpt["opt_sparse"])
        opt_all.load_state_dict(ckpt["opt_all"])
        start_epoch = int(ckpt.get("epoch", 0)) + 1
        best_val = float(ckpt.get("best_val", best_val))
        print(f"resumed from {args.resume} at epoch {start_epoch}")

    criterion = nn.BCEWithLogitsLoss()
    t0 = time.time()
    for epoch in range(start_epoch, args.epochs):
        model.train()
        running = 0.0
        seen = 0
        for step, (feats, offsets, target) in enumerate(train_loader):
            feats = feats.to(device, non_blocking=True)
            offsets = offsets.to(device, non_blocking=True)
            target = target.to(device, non_blocking=True)
            if opt_sparse:
                opt_sparse.zero_grad(set_to_none=True)
            opt_all.zero_grad(set_to_none=True)
            logits = model(feats, offsets)
            loss = criterion(logits, target)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(dense_params, 1.0)
            if opt_sparse:
                opt_sparse.step()
            opt_all.step()
            running += float(loss.item()) * int(target.numel())
            seen += int(target.numel())
            if args.log_every and step % args.log_every == 0:
                print(
                    f"epoch {epoch} step {step} loss {running / max(seen,1):.5f} "
                    f"elapsed {time.time()-t0:.0f}s",
                    flush=True,
                )
        val = evaluate(model, val_loader, device)
        print(f"epoch {epoch} val {val}", flush=True)
        ckpt = {
            "model": model.state_dict(),
            "opt_all": opt_all.state_dict(),
            "epoch": epoch,
            "best_val": min(best_val, val["loss"]),
        }
        if opt_sparse:
            ckpt["opt_sparse"] = opt_sparse.state_dict()
        os.makedirs(args.out, exist_ok=True)
        torch.save(ckpt, os.path.join(args.out, "checkpoint.pt"))
        if val["loss"] < best_val:
            best_val = val["loss"]
            export(
                model, args.out, args.hidden,
                {
                    "train_loss": running / max(seen, 1),
                    "val": val,
                    "epoch": epoch,
                    "device": str(device),
                    "train_samples": len(train_idx),
                    "val_samples": len(val_idx),
                },
            )

    print(f"done in {time.time()-t0:.0f}s; best val {best_val:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
