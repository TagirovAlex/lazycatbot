#!/usr/bin/env python3
"""Compare one or more puzzle_test JSON reports and print Markdown tables."""
from __future__ import annotations

import argparse
import json
import math
import os


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_pct(s: int, n: int) -> str:
    return f"{100.0 * s / n:.1f}%" if n else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    runs = []
    for path in args.reports:
        data = load(path)
        data["_path"] = path
        runs.append(data)

    lines: list[str] = []
    add = lines.append
    add("| Прогон | Задач | Решено | Первый ход | Ходы | Глубина |")
    add("|---|---:|---:|---:|---:|---:|")
    for r in runs:
        add(
            f"| `{r.get('tag') or os.path.basename(r['_path'])}` "
            f"| {r['puzzles']} "
            f"| {fmt_pct(r['solved'], r['puzzles'])} "
            f"| {fmt_pct(r['first_correct'], r['puzzles'])} "
            f"| {fmt_pct(r['moves_correct'], r['move_attempts'])} "
            f"| {r['mean_depth']:.1f} |"
        )

    add("")
    add("### По рейтингу задачи")
    add("")
    buckets = []
    for r in runs:
        for row in r["by_rating"]:
            if row["key"] not in buckets:
                buckets.append(row["key"])
    order = ["<1000", "1000-1399", "1400-1799", "1800-2199", "2200-2599", "2600+"]
    buckets.sort(key=lambda x: order.index(x) if x in order else 99)
    add("| Рейтинг | " + " | ".join(
        f"`{r.get('tag') or os.path.basename(r['_path'])}`" for r in runs) + " |")
    add("|---|" + "---:|" * len(runs))
    for b in buckets:
        cells = []
        for r in runs:
            row = next((x for x in r["by_rating"] if x["key"] == b), None)
            cells.append(fmt_pct(row["solved"], row["total"]) + f" ({row['total']})"
                         if row else "n/a")
        add(f"| {b} | " + " | ".join(cells) + " |")

    add("")
    add("### По длине решения")
    add("")
    lengths = sorted({row["key"] for r in runs for row in r["by_solution_len"]})
    add("| Ходов | " + " | ".join(
        f"`{r.get('tag') or os.path.basename(r['_path'])}`" for r in runs) + " |")
    add("|---|" + "---:|" * len(runs))
    for length in lengths:
        cells = []
        for r in runs:
            row = next((x for x in r["by_solution_len"] if x["key"] == length), None)
            cells.append(fmt_pct(row["solved"], row["total"]) + f" ({row['total']})"
                         if row else "n/a")
        add(f"| {length} | " + " | ".join(cells) + " |")

    add("")
    add("### Темы (n >= 20, первые 15 по количеству)")
    add("")
    theme_names = {}
    for r in runs:
        for row in r["by_theme"]:
            theme_names[row["key"]] = theme_names.get(row["key"], 0) + row["total"]
    top = sorted(theme_names, key=lambda x: -theme_names[x])[:15]
    add("| Тема | " + " | ".join(
        f"`{r.get('tag') or os.path.basename(r['_path'])}`" for r in runs) + " |")
    add("|---|" + "---:|" * len(runs))
    for theme in top:
        cells = []
        for r in runs:
            row = next((x for x in r["by_theme"] if x["key"] == theme), None)
            cells.append(fmt_pct(row["solved"], row["total"]) + f" ({row['total']})"
                         if row else "n/a")
        add(f"| {theme} | " + " | ".join(cells) + " |")

    if len(runs) == 2 and all("items" in r for r in runs):
        a = {x["id"]: x["solved"] for x in runs[0]["items"]}
        b = {x["id"]: x["solved"] for x in runs[1]["items"]}
        common = sorted(set(a) & set(b))
        only_a = sum(1 for i in common if a[i] and not b[i])
        only_b = sum(1 for i in common if b[i] and not a[i])
        discordant = only_a + only_b
        if discordant:
            chi2 = (abs(only_a - only_b) - 1) ** 2 / discordant
            p = math.erfc(math.sqrt(chi2 / 2.0))
        else:
            chi2 = 0.0
            p = 1.0
        add("")
        add(f"### Парное сравнение `{runs[0].get('tag')}` -> `{runs[1].get('tag')}`")
        add("")
        add(f"Общих задач: {len(common)}. "
            f"Решил только первый: {only_a}; только второй: {only_b}. "
            f"McNemar chi2={chi2:.2f}, p={p:.3f}.")
        add("")
        add("Положительное число `only_b` и малое p означают, что второй прогон "
            "лучше на тех же задачах.")

    text = "\n".join(lines) + "\n"
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"written {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
