"""UCI protocol and smoke tests."""
from __future__ import annotations

import os
import re
import sys
import time

import chess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402
from testutil import Results, parse_bestmove, parse_info_last  # noqa: E402
from uci_engine import Engine  # noqa: E402

BM_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def legal(board: chess.Board, bm: str) -> bool:
    if bm == "0000":
        return board.legal_moves.count() == 0
    try:
        return chess.Move.from_uci(bm) in board.legal_moves
    except ValueError:
        return False


def fen_after(eng: Engine) -> str:
    """Read FEN via `d`. `d` output is not flushed by the engine, so follow it
    with isready (whose readyok uses endl and flushes the pending buffer)."""
    start = eng.mark()
    eng.send("d")
    eng.send("isready")
    eng.wait_for("readyok", timeout=5, start=start)
    for line in eng.collect(start):
        if line.startswith("FEN: "):
            return line[5:].strip()
    return ""


def do_search(res: Results, eng: Engine, board: chess.Board, go: str,
              timeout: float = 10.0, label: str = "") -> tuple[str | None, float, list[str]]:
    start = eng.mark()
    t0 = time.time()
    eng.send("go " + go)
    bmline, elapsed, lines = eng.wait_bestmove(timeout=timeout, start=start)
    bm = parse_bestmove(bmline or "")
    ok = bm is not None
    res.add(f"{label}: bestmove returned", ok,
            f"bestmove={bm!r} in {elapsed:.3f}s" if ok else f"timeout after {elapsed:.3f}s")
    if ok:
        res.add(f"{label}: bestmove format", bool(BM_RE.match(bm)), bm)
        res.add(f"{label}: bestmove legal", legal(board, bm), f"{bm} on {board.fen()}")
        n_bm = sum(1 for ln in lines if ln.startswith("bestmove"))
        res.check(f"{label}: single bestmove line", n_bm, 1)
        info = [ln for ln in lines if ln.startswith("info depth")]
        res.add(f"{label}: info lines", len(info) >= 1, f"{len(info)} info lines")
        last = parse_info_last(lines)
        if last:
            res.add(f"{label}: info has nodes/nps/time", all(k in last for k in ("nodes", "nps", "time")),
                    str({k: last.get(k) for k in ("depth", "nodes", "nps", "time", "score")}))
    return bm, elapsed, lines


def main() -> int:
    res = Results("protocol")
    eng = Engine(testutil.ENGINE, "chessbot_test")

    try:
        # --- handshake -------------------------------------------------
        lines = eng.uci(timeout=10)
        res.check("uci: uciok count", sum(1 for ln in lines if ln == "uciok"), 1)
        res.add("uci: id name", any(ln.startswith("id name ") for ln in lines),
                next((ln for ln in lines if ln.startswith("id name")), "missing"))
        res.add("uci: id author", any(ln.startswith("id author ") for ln in lines), "")
        opts = [ln for ln in lines if ln.startswith("option name ")]
        for name in ("Hash", "Move Overhead", "UseNNUE", "NNUEWeight", "Clear Hash"):
            res.add(f"uci: option {name!r} advertised",
                    any(ln.startswith(f"option name {name} ") or ln == f"option name {name} type button"
                        for ln in opts),
                    next((ln for ln in opts if ln.startswith(f"option name {name} ")), "missing"))
        res.add("uci: uciok last", lines[-1] == "uciok", lines[-1] if lines else "")

        res.add("isready -> readyok", eng.wait_ready(5), "")

        # Startup position (no `position` command sent yet).
        res.check("startup default position is startpos", fen_after(eng),
                  chess.Board().fen(en_passant="fen"))

        # `d` writes with '\n' rather than endl; check it reaches a piped GUI.
        start = eng.mark()
        eng.send("d")
        time.sleep(0.5)
        unflushed = [ln for ln in eng.collect(start) if ln.startswith("FEN: ")]
        res.add("d command output flushed promptly", bool(unflushed),
                "" if unflushed else "no output after 0.5s (buffered until next endl)")
        eng.send("isready")
        eng.wait_for("readyok", 5, start)

        # ucinewgame resets position
        start = eng.mark()
        eng.send("ucinewgame")
        eng.wait_ready(5)
        fen = fen_after(eng)
        res.check("ucinewgame resets to startpos", fen,
                  chess.Board().fen(en_passant="fen"))

        # --- position parsing ------------------------------------------
        moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4",
                 "g8f6", "e1g1"]
        b = chess.Board()
        for m in moves:
            b.push_uci(m)
        eng.set_position(moves=moves)
        res.check("position startpos moves (castling) FEN", fen_after(eng),
                  b.fen(en_passant="fen"))

        fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
        b2 = chess.Board(fen)
        for m in ("e1g1", "e8c8"):
            b2.push_uci(m)
        eng.set_position(fen=fen, moves=["e1g1", "e8c8"])
        res.check("position fen + moves FEN", fen_after(eng), b2.fen(en_passant="fen"))

        fen = "8/P6k/8/8/8/8/8/K7 w - - 0 1"
        b3 = chess.Board(fen)
        b3.push_uci("a7a8q")
        eng.set_position(fen=fen, moves=["a7a8q"])
        res.check("position promotion move FEN", fen_after(eng), b3.fen(en_passant="fen"))

        fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
        b4 = chess.Board(fen)
        b4.push_uci("e5d6")
        eng.set_position(fen=fen, moves=["e5d6"])
        res.check("position en-passant move FEN", fen_after(eng), b4.fen(en_passant="fen"))

        # illegal move in list stops parsing, engine remains usable
        eng.set_position(moves=["e2e4", "e7e5", "e2e4"])
        b_ill = chess.Board()
        b_ill.push_uci("e2e4")
        b_ill.push_uci("e7e5")
        res.check("position illegal move ignored (stays at last legal)", fen_after(eng),
                  b_ill.fen(en_passant="legal"))

        # --- d / eval / perft / bench ----------------------------------
        eng.set_position()
        fen = fen_after(eng)
        res.check("d command startpos FEN", fen, chess.Board().fen(en_passant="fen"))
        start = eng.mark()
        eng.send("eval")
        got = eng.wait_for("info string", timeout=5, start=start)
        ev = eng.collect(start)
        res.add("eval command output", got and any("classical(stm)" in ln for ln in ev),
                next((ln for ln in ev if "classical" in ln), "missing"))
        ev_parsed = {}
        for ln in ev:
            if "classical(stm)" in ln:
                toks = ln.split()
                for key in ("classical(stm)", "neural", "total", "weight"):
                    try:
                        ev_parsed[key] = int(toks[toks.index(key) + 1])
                    except Exception:
                        pass
                ev_parsed["available"] = "nnue_available 1" in ln
                ev_parsed["enabled"] = "enabled 1" in ln
        res.add("eval command numeric fields", len(ev_parsed) >= 6, str(ev_parsed))

        start = eng.mark()
        eng.send("perft 3")
        res.add("perft command responds", eng.wait_for("perft ", timeout=30, start=start),
                eng.collect(start)[-1] if eng.collect(start) else "")
        res.check("perft 3 startpos count", int(eng.collect(start)[-1].split()[1]), 8902)

        start = eng.mark()
        t0 = time.time()
        eng.send("bench 5")
        got = eng.wait_for("bench nodes", timeout=120, start=start)
        blines = [ln for ln in eng.collect(start) if ln.startswith("bench nodes")]
        res.add("bench 5 completes", got, f"{time.time()-t0:.2f}s {blines[-1] if blines else ''}")
        if blines:
            toks = blines[-1].split()
            res.add("bench 5 nodes > 0", int(toks[2]) > 0, blines[-1])
        res.add("engine responsive after bench", eng.wait_ready(10), "")

        # --- setoption -------------------------------------------------
        for name, value in (("Hash", "1"), ("Hash", "32"), ("Hash", "256"),
                            ("Move Overhead", "0"), ("Move Overhead", "30")):
            start = eng.mark()
            t0 = time.time()
            eng.send(f"setoption name {name} value {value}")
            ok = eng.wait_ready(30)
            res.add(f"setoption {name}={value}", ok, f"ready in {time.time()-t0:.3f}s")
        eng.send("setoption name Clear Hash")
        res.add("setoption Clear Hash button", eng.wait_ready(10), "")

        # NNUE option behaviour via eval
        def eval_now() -> dict:
            start = eng.mark()
            eng.send("eval")
            eng.wait_for("info string", timeout=5, start=start)
            d = {}
            for ln in eng.collect(start):
                if "classical(stm)" in ln:
                    toks = ln.split()
                    for key in ("classical(stm)", "neural", "total", "weight"):
                        try:
                            d[key] = int(toks[toks.index(key) + 1])
                        except Exception:
                            pass
                    d["enabled"] = "enabled 1" in ln
                    d["available"] = "nnue_available 1" in ln
                    d["direct"] = "direct 1" in ln
            return d

        eng.set_position()
        e0 = eval_now()
        res.add("NNUE available at startpos", e0.get("available") is True, str(e0))
        eng.send("setoption name UseNNUE value false")
        e1 = eval_now()
        res.add("UseNNUE false -> neural 0", e1.get("neural") == 0 and not e1.get("enabled"), str(e1))
        res.check("UseNNUE false -> total == classical", e1.get("total"), e1.get("classical(stm)"))
        eng.send("setoption name UseNNUE value true")
        eng.send("setoption name NNUEWeight value 0")
        e2 = eval_now()
        res.check("NNUEWeight 0 -> total == classical", e2.get("total"), e2.get("classical(stm)"))
        eng.send("setoption name NNUEWeight value 100")
        e3 = eval_now()
        res.add("NNUEWeight 100 -> neural applied",
                e3.get("neural", 0) != 0 and e3.get("total") != e3.get("classical(stm)"), str(e3))
        eng.send("setoption name NNUEWeight value 40")
        e4 = eval_now()
        res.check("NNUEWeight restored to 40", e4.get("weight"), 40)

        # --- searches --------------------------------------------------
        eng.newgame()
        b = chess.Board()
        do_search(res, eng, b, "depth 5", 15, "go depth 5")
        do_search(res, eng, b, "depth 1", 10, "go depth 1")
        do_search(res, eng, b, "depth 0", 10, "go depth 0 (clamped)")
        do_search(res, eng, b, "movetime 200", 3, "go movetime 200")
        do_search(res, eng, b, "movetime 1000", 5, "go movetime 1000")
        do_search(res, eng, b, "nodes 20000", 10, "go nodes 20000")
        do_search(res, eng, b, "nodes 1", 10, "go nodes 1")
        do_search(res, eng, b, "wtime 5000 btime 5000", 10, "go wtime/btime 5000")
        do_search(res, eng, b, "wtime 1000 btime 1000 movestogo 1", 10,
                  "go wtime/btime 1000 movestogo 1")
        do_search(res, eng, b, "mate 1", 20, "go mate 1")

        # --- stop / quit during search ---------------------------------
        start = eng.mark()
        eng.send("go infinite")
        time.sleep(0.3)
        t0 = time.time()
        eng.send("stop")
        bmline, elapsed, lines = eng.wait_bestmove(timeout=3, start=start)
        bm = parse_bestmove(bmline or "")
        res.add("go infinite + stop returns <= 2s", elapsed <= 2.0, f"{elapsed:.3f}s")
        res.add("go infinite + stop bestmove legal", bm is not None and legal(b, bm), f"{bm!r}")

        # stop with no active search must be harmless
        eng.send("stop")
        res.add("stop with no search is harmless", eng.wait_ready(5), "")

        # isready during search must answer promptly
        eng.send("go infinite")
        time.sleep(0.2)
        start = eng.mark()
        t0 = time.time()
        eng.send("isready")
        got = eng.wait_for("readyok", timeout=2, start=start)
        res.add("isready during search answered", got, f"{time.time()-t0:.3f}s")
        eng.send("stop")
        eng.wait_bestmove(timeout=3, start=start)

        # ucinewgame during search stops it and resets
        eng.send("go infinite")
        time.sleep(0.2)
        start = eng.mark()
        t0 = time.time()
        eng.send("ucinewgame")
        eng.send("isready")
        got = eng.wait_for("readyok", timeout=5, start=start)
        res.add("ucinewgame during search returns", got, f"{time.time()-t0:.3f}s")
        res.check("ucinewgame during search resets FEN", fen_after(eng),
                  chess.Board().fen(en_passant="fen"))

        # --- quit during search (separate process) ---------------------
        eng2 = Engine(testutil.ENGINE, "quit-test")
        eng2.wait_ready(10)
        eng2.set_position()
        eng2.send("go infinite")
        time.sleep(0.2)
        t0 = time.time()
        eng2.send("quit")
        try:
            eng2.proc.wait(timeout=5)
            exited = True
        except Exception:
            exited = False
        res.add("quit during search exits <= 5s", exited and time.time() - t0 <= 5,
                f"{time.time()-t0:.3f}s exit={eng2.proc.poll()}")
        if eng2.alive():
            eng2.proc.kill()

    finally:
        eng.close()

    res.write()
    return 0 if res.summary()["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
