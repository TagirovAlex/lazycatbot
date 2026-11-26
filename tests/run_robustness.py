"""Robustness tests: edge positions, options, malformed input, all binaries."""
from __future__ import annotations

import os
import re
import sys
import time

import chess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402
from testutil import Results, parse_bestmove  # noqa: E402
from uci_engine import Engine  # noqa: E402

BM_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def py_perft(board: chess.Board, depth: int) -> int:
    if depth == 0:
        return 1
    n = 0
    for m in board.legal_moves:
        board.push(m)
        n += py_perft(board, depth - 1)
        board.pop()
    return n


def legal(board: chess.Board, bm: str) -> bool:
    if bm == "0000":
        return board.legal_moves.count() == 0
    try:
        return chess.Move.from_uci(bm) in board.legal_moves
    except ValueError:
        return False


def search_fen(res: Results, eng: Engine, fen: str, go: str, label: str,
               timeout: float = 10.0, max_elapsed: float | None = None) -> str | None:
    board = chess.Board(fen)
    eng.set_position(fen=fen)
    start = eng.mark()
    eng.send("go " + go)
    bmline, elapsed, lines = eng.wait_bestmove(timeout=timeout, start=start)
    bm = parse_bestmove(bmline or "")
    res.add(f"{label}: returns bestmove", bm is not None,
            f"{bm!r} in {elapsed:.3f}s" if bm is not None else f"timeout {elapsed:.3f}s")
    if bm is None:
        return None
    res.add(f"{label}: format", bool(BM_RE.match(bm)) or bm == "0000", bm)
    res.add(f"{label}: legal", legal(board, bm), f"{bm} on {fen}")
    if max_elapsed is not None:
        res.add(f"{label}: within {max_elapsed:.1f}s", elapsed <= max_elapsed, f"{elapsed:.3f}s")
    return bm


def main() -> int:
    res = Results("robustness")
    eng = Engine(testutil.ENGINE, "chessbot_test")
    eng.uci()
    eng.newgame()

    # ------------------------------------------------------------------
    # Terminal positions: empty move list -> bestmove 0000
    # ------------------------------------------------------------------
    terminals = [
        ("checkmate", "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", "mate 0"),
        ("stalemate", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", "cp 0"),
        ("back-rank mate", "4R1k1/5ppp/8/8/8/8/8/6K1 b - - 0 1", "mate 0"),
    ]
    for label, fen, score_token in terminals:
        board = chess.Board(fen)
        res.check(f"{label}: python-chess has no legal moves", board.legal_moves.count(), 0)
        eng.set_position(fen=fen)
        start = eng.mark()
        eng.send("go depth 3")
        bmline, elapsed, lines = eng.wait_bestmove(timeout=5, start=start)
        res.check(f"{label}: bestmove 0000", (bmline or "").strip(), "bestmove 0000")
        info = [ln for ln in lines if ln.startswith("info depth 0")]
        res.add(f"{label}: info depth 0 {score_token}", bool(info) and score_token in info[0],
                info[0] if info else "missing")
        res.add(f"{label}: no hang", elapsed <= 2, f"{elapsed:.3f}s")

    # ------------------------------------------------------------------
    # Perft edge cases (castling rights, EP pins, promotions, draws)
    # ------------------------------------------------------------------
    perft_cases = [
        ("no castling rights", "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1", 3),
        ("full castling rights", "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", 3),
        ("ep pinned pawn", "8/8/8/2k5/2pP4/8/B7/4K3 b - d3 0 3", 3),
        ("ep discovered check", "8/8/8/8/k2Pp2Q/8/8/4K3 b - d3 0 1", 3),
        ("promotion capture", "1n6/P6k/8/8/8/8/8/K7 w - - 0 1", 3),
        ("promotion push", "8/P6k/8/8/8/8/8/K7 w - - 0 1", 3),
        ("halfmove 100", "4k3/8/8/8/8/8/8/4K3 w - - 100 1", 2),
        ("stalemate", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", 1),
        ("checkmate", "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", 1),
    ]
    for label, fen, depth in perft_cases:
        board = chess.Board(fen)
        ref = py_perft(board, depth)
        eng.set_position(fen=fen)
        start = eng.mark()
        eng.send(f"perft {depth}")
        ok = eng.wait_for("perft ", timeout=60, start=start)
        got = None
        if ok:
            try:
                got = int(eng.collect(start)[-1].split()[1])
            except Exception:
                pass
        res.check(f"perft edge {label} (d{depth})", got, ref)
    # perft 0
    eng.set_position()
    start = eng.mark()
    eng.send("perft 0")
    eng.wait_for("perft ", 10, start)
    res.check("perft 0 == 1", int(eng.collect(start)[-1].split()[1]), 1)

    # ------------------------------------------------------------------
    # Special-move searches
    # ------------------------------------------------------------------
    bm = search_fen(res, eng, "8/P6k/8/8/8/8/8/K7 w - - 0 1", "depth 4",
                    "promotion search")
    res.add("promotion search picks a promotion", bm is not None and len(bm) == 5
            and bm.startswith("a7a8") and bm[4] in "qrbn", str(bm))
    bm = search_fen(res, eng, "1n6/P6k/8/8/8/8/8/K7 w - - 0 1", "depth 4",
                    "capture promotion search")
    res.add("capture-promotion move encoded with promo char",
            bm is not None and len(bm) == 5 and bm[4] in "qrbn"
            and (bm.startswith("a7a8") or bm.startswith("a7b8")), str(bm))
    search_fen(res, eng, "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "depth 5",
               "en-passant search")
    search_fen(res, eng, "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "depth 5",
               "castling-rights search")
    search_fen(res, eng, "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1", "depth 5",
               "no-castling search")
    search_fen(res, eng, "4k3/8/8/8/8/8/8/4K3 w - - 100 1", "depth 4",
               "halfmove-100 search")
    search_fen(res, eng, "4k3/8/8/8/8/8/8/4KB2 w - - 0 1", "depth 4",
               "insufficient-material search")
    search_fen(res, eng, "4k3/8/8/8/8/8/4r3/4K3 w - - 0 1", "depth 5",
               "in-check search")
    search_fen(res, eng, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
               "depth 5", "black-to-move after e4")
    search_fen(res, eng, "4k3/8/8/8/8/8/8/4K3 w - -", "depth 4",
               "FEN with 4 fields (default clocks)")

    # Mate-in-1 search correctness
    mate_fen = "6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1"
    eng.set_position(fen=mate_fen)
    start = eng.mark()
    eng.send("go depth 5")
    bmline, elapsed, lines = eng.wait_bestmove(timeout=5, start=start)
    bm = parse_bestmove(bmline or "")
    res.check("mate-in-1: picks e1e8", bm, "e1e8")
    info = testutil.parse_info_last(lines)
    res.check("mate-in-1: score mate 1", info.get("score"), "mate 1")
    eng.set_position(fen=mate_fen)
    start = eng.mark()
    eng.send("go mate 1")
    bmline, elapsed, lines = eng.wait_bestmove(timeout=5, start=start)
    res.check("go mate 1: picks e1e8", parse_bestmove(bmline or ""), "e1e8")

    # FEN preservation of unusual (but legal) fields
    start = eng.mark()
    eng.send("position fen 4k3/8/8/8/8/8/8/4K3 w - -")
    eng.send("d")
    eng.send("isready")
    eng.wait_for("readyok", 5, start)
    fens = [ln[5:].strip() for ln in eng.collect(start) if ln.startswith("FEN: ")]
    res.check("4-field FEN defaults halfmove/fullmove", fens[-1] if fens else None,
              "4k3/8/8/8/8/8/8/4K3 w - - 0 1")

    # ------------------------------------------------------------------
    # Options / Hash resizing / NNUE toggles with live searches
    # ------------------------------------------------------------------
    eng.newgame()
    eng.set_position()
    for mb in (1, 16, 256, 1024):
        start = eng.mark()
        eng.send(f"setoption name Hash value {mb}")
        ok = eng.wait_ready(30)
        res.add(f"Hash resize {mb} MB", ok, "")
        # search without re-setting position (position is still startpos)
        start = eng.mark()
        eng.send("go depth 4")
        bmline, elapsed, lines = eng.wait_bestmove(timeout=10, start=start)
        bm = parse_bestmove(bmline or "")
        res.add(f"Hash {mb}: search legal", bm is not None and legal(chess.Board(), bm),
                f"{bm} in {elapsed:.3f}s")

    for op, val, label in (("Hash", "0", "Hash 0 clamps"),
                            ("Hash", "-5", "Hash -5 clamps"),
                            ("NNUEWeight", "999", "NNUEWeight 999 clamps"),
                            ("Move Overhead", "5000", "Move Overhead max")):
        start = eng.mark()
        eng.send(f"setoption name {op} value {val}")
        ok = eng.wait_ready(30)
        res.add(f"{label}: accepted", ok, "")
        start = eng.mark()
        eng.send("go movetime 100")
        bmline, elapsed, lines = eng.wait_bestmove(timeout=3, start=start)
        bm = parse_bestmove(bmline or "")
        res.add(f"{label}: search legal/quick", bm is not None and legal(chess.Board(), bm)
                and elapsed <= 2.0, f"{bm} in {elapsed:.3f}s")
    eng.send("setoption name Move Overhead value 30")
    eng.send("setoption name Hash value 64")
    eng.wait_ready(30)
    eng.send("setoption name NNUEWeight value 40")
    eng.send("setoption name UseNNUE value true")
    eng.send("setoption name Unknown Option value 1")
    res.add("unknown setoption ignored", eng.wait_ready(5), "")

    for use in ("false", "true"):
        eng.send(f"setoption name UseNNUE value {use}")
        start = eng.mark()
        eng.send("eval")
        eng.wait_for("info string", 5, start)
        ln = [l for l in eng.collect(start) if "classical(stm)" in l][-1]
        start = eng.mark()
        eng.send("go depth 5")
        bmline, elapsed, lines = eng.wait_bestmove(timeout=10, start=start)
        bm = parse_bestmove(bmline or "")
        res.add(f"UseNNUE {use}: search legal", bm is not None and legal(chess.Board(), bm),
                f"{bm}; {ln}")

    # ------------------------------------------------------------------
    # All shipped binaries: handshake, perft, eval, search, bench
    # ------------------------------------------------------------------
    for path in (testutil.ENGINE, testutil.ENGINE_ROOT, testutil.ENGINE_SFNET,
                 testutil.ENGINE_NNUE):
        name = os.path.basename(path)
        e = Engine(path, name)
        try:
            lines = e.uci(15)
            res.add(f"{name}: uciok", "uciok" in lines, "")
            e.newgame()
            e.set_position()
            start = e.mark()
            e.send("perft 3")
            ok = e.wait_for("perft ", 30, start)
            got = None
            if ok:
                try:
                    got = int(e.collect(start)[-1].split()[1])
                except Exception:
                    pass
            res.check(f"{name}: perft 3", got, 8902)
            start = e.mark()
            e.send("eval")
            e.wait_for("info string", 5, start)
            ev = next((ln for ln in e.collect(start) if "classical(stm)" in ln), "")
            res.add(f"{name}: eval output", "classical(stm)" in ev, ev)
            start = e.mark()
            e.send("go movetime 100")
            bmline, elapsed, slines = e.wait_bestmove(timeout=5, start=start)
            bm = parse_bestmove(bmline or "")
            res.add(f"{name}: search legal", bm is not None and legal(chess.Board(), bm),
                    f"{bm} in {elapsed:.3f}s")
            start = e.mark()
            e.send("bench 2")
            ok = e.wait_for("bench nodes", 60, start)
            bl = [ln for ln in e.collect(start) if ln.startswith("bench nodes")]
            res.add(f"{name}: bench", ok, bl[-1] if bl else "")
            if ev:
                print(f"  [{name}] {ev}", flush=True)
        except Exception as exc:
            res.add(f"{name}: binary smoke", False, repr(exc))
        finally:
            e.close()

    # ------------------------------------------------------------------
    # Malformed input / no-position crashes (each in a fresh process)
    # ------------------------------------------------------------------
    def fresh() -> Engine:
        e = Engine(testutil.ENGINE, "crash-probe")
        e.wait_ready(20)
        return e

    def kill(e: Engine) -> None:
        try:
            e.proc.kill()
            e.proc.wait(timeout=5)
        except Exception:
            pass

    # 1. go with no position at all
    e = fresh()
    start = e.mark()
    e.send("go depth 1")
    bmline, elapsed, lines = e.wait_bestmove(timeout=2, start=start)
    res.add("no position + go: bestmove returned", bmline is not None,
            f"bestmove={parse_bestmove(bmline or '')!r} after {elapsed:.2f}s; "
            f"alive={e.alive()} lines={lines[-2:]}")
    kill(e)

    # 2. invalid FEN then go
    e = fresh()
    e.send("position fen banana")
    ready = e.wait_ready(3)
    res.add("invalid FEN: engine still accepts isready", ready, "")
    start = e.mark()
    e.send("go depth 1")
    bmline, elapsed, lines = e.wait_bestmove(timeout=2, start=start)
    res.add("invalid FEN + go: bestmove returned", bmline is not None,
            f"bestmove={parse_bestmove(bmline or '')!r} after {elapsed:.2f}s; alive={e.alive()}")
    kill(e)

    # 3. FEN without kings then go
    e = fresh()
    e.send("position fen 8/8/8/8/8/8/8/8 w - - 0 1")
    e.wait_ready(3)
    start = e.mark()
    e.send("go depth 1")
    bmline, elapsed, lines = e.wait_bestmove(timeout=2, start=start)
    res.add("kingless FEN + go: bestmove returned", bmline is not None,
            f"bestmove={parse_bestmove(bmline or '')!r} after {elapsed:.2f}s; alive={e.alive()}")
    kill(e)

    # 4. invalid numeric setoption
    e = fresh()
    start = e.mark()
    e.send("setoption name Hash value abc")
    e.send("isready")
    ok = e.wait_for("readyok", 2, start)
    lines = e.collect(start)
    crashed = any("terminate called" in ln for ln in lines)
    res.add("invalid Hash value does not crash", ok and not crashed,
            f"readyok={ok} crash={crashed} lines={lines[-3:]}")
    kill(e)

    # 5. invalid numeric FEN clock
    e = fresh()
    start = e.mark()
    e.send("position fen 4k3/8/8/8/8/8/8/4K3 w - - x 1")
    e.send("isready")
    ok = e.wait_for("readyok", 2, start)
    lines = e.collect(start)
    crashed = any("terminate called" in ln for ln in lines)
    res.add("invalid FEN clock does not crash", ok and not crashed,
            f"readyok={ok} crash={crashed} lines={lines[-3:]}")
    kill(e)

    # 6. malformed go depth: must not hang; stop must recover
    e = fresh()
    e.send("position startpos")
    start = e.mark()
    e.send("go depth abc")
    time.sleep(0.3)
    e.send("stop")
    bmline, elapsed, lines = e.wait_bestmove(timeout=3, start=start)
    res.add("malformed 'go depth abc' recovers on stop", bmline is not None,
            f"bestmove={parse_bestmove(bmline or '')!r} after {elapsed:.2f}s")
    kill(e)

    eng.close()
    res.write()
    return 0 if res.summary()["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
