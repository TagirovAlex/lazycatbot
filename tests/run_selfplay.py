"""Self-play stress test: engine vs itself, every move validated by python-chess."""
from __future__ import annotations

import os
import sys
import time

import chess
import chess.pgn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402
from testutil import Results, PGNS, parse_bestmove  # noqa: E402
from uci_engine import Engine  # noqa: E402

MOVETIME_MS = 40
MAX_PLIES = 240
NUM_GAMES = 10
MIDGAME_FEN = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"


def game_result(board: chess.Board) -> str:
    if board.is_checkmate():
        return "0-1" if board.turn == chess.WHITE else "1-0"
    if board.is_game_over(claim_draw=True):
        return "1/2-1/2"
    return "*"


def termination(board: chess.Board) -> str:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient_material"
    if board.is_seventyfive_moves():
        return "75-move"
    if board.is_fivefold_repetition():
        return "fivefold"
    if board.is_fifty_moves() or board.can_claim_fifty_moves():
        return "50-move"
    if board.is_repetition(3) or board.can_claim_threefold_repetition():
        return "threefold"
    if board.is_game_over(claim_draw=True):
        return "claimable_draw"
    return "move_cap"


def main() -> int:
    res = Results("selfplay")
    eng_a = Engine(testutil.ENGINE, "selfplay-A")
    eng_b = Engine(testutil.ENGINE, "selfplay-B")
    for e in (eng_a, eng_b):
        e.uci()
        e.send("setoption name Move Overhead value 0")
        e.send("setoption name Hash value 32")
        e.wait_ready()

    games: list[dict] = []
    try:
        for g in range(1, NUM_GAMES + 1):
            white, black = (eng_a, eng_b) if g % 2 == 1 else (eng_b, eng_a)
            midgame = (g == NUM_GAMES)
            start_fen = MIDGAME_FEN if midgame else None
            board = chess.Board(start_fen) if midgame else chess.Board()
            moves: list[chess.Move] = []
            times: list[float] = []
            white.newgame()
            black.newgame()
            illegal = None
            timeout = None
            crashed = None
            t_game = time.time()

            for ply in range(MAX_PLIES):
                if board.is_game_over(claim_draw=True):
                    break
                eng = white if board.turn == chess.WHITE else black
                eng.set_position(fen=start_fen if midgame else None,
                                 moves=[m.uci() for m in moves])
                start = eng.mark()
                t0 = time.time()
                eng.send(f"go movetime {MOVETIME_MS}")
                bmline, elapsed, lines = eng.wait_bestmove(timeout=5.0, start=start)
                bm = parse_bestmove(bmline or "")
                times.append(elapsed)
                if not eng.alive():
                    crashed = ply
                    break
                if bmline is None:
                    timeout = ply
                    res.add(f"game {g} ply {ply}: no timeout", False,
                            f"no bestmove within 5s (last lines: {lines[-3:]})")
                    break
                try:
                    mv = chess.Move.from_uci(bm)
                except ValueError:
                    mv = None
                if mv is None or mv not in board.legal_moves:
                    illegal = (ply, bm, board.fen())
                    res.add(f"game {g} ply {ply}: legal move", False,
                            f"illegal/malformed bestmove {bm!r} in {board.fen()}")
                    break
                if elapsed > 3.0:
                    res.add(f"game {g} ply {ply}: move time sane", False,
                            f"{elapsed:.3f}s for movetime {MOVETIME_MS}ms")
                moves.append(mv)
                board.push(mv)

            over = board.is_game_over(claim_draw=True)
            if not over and len(moves) >= MAX_PLIES:
                term = "move_cap"
            else:
                term = termination(board)
            result = game_result(board) if over else "*"
            if result == "*" and term == "move_cap":
                result = "*"

            game = chess.pgn.Game()
            game.headers["Event"] = "ChessBot self-play test"
            game.headers["Site"] = "tests/"
            game.headers["Date"] = time.strftime("%Y.%m.%d")
            game.headers["Round"] = str(g)
            game.headers["White"] = "ChessBot-A" if white is eng_a else "ChessBot-B"
            game.headers["Black"] = "ChessBot-B" if white is eng_a else "ChessBot-A"
            game.headers["Result"] = result
            if midgame:
                game.setup(chess.Board(start_fen))
            node = game
            for mv in moves:
                node = node.add_variation(mv)
            pgn_path = os.path.join(PGNS, f"selfplay_game{g:02d}.pgn")
            with open(pgn_path, "w", encoding="utf-8") as f:
                f.write(str(game) + "\n")

            info = {
                "game": g, "white": game.headers["White"], "black": game.headers["Black"],
                "start": "midgame" if midgame else "startpos",
                "plies": len(moves), "result": result, "termination": term,
                "avg_ms": round(1000 * sum(times) / len(times), 1) if times else 0,
                "max_ms": round(1000 * max(times), 1) if times else 0,
                "wall_s": round(time.time() - t_game, 2),
                "illegal": illegal, "timeout": timeout, "crash": crashed,
                "pgn": os.path.relpath(pgn_path, testutil.ROOT),
            }
            games.append(info)
            ok = illegal is None and timeout is None and crashed is None
            res.add(f"game {g} ({info['start']}, {len(moves)} plies)", ok,
                    f"result {result} term {term} avg {info['avg_ms']}ms "
                    f"max {info['max_ms']}ms wall {info['wall_s']}s")
            # Validate the saved PGN parses back and has the same number of moves.
            try:
                with open(pgn_path, encoding="utf-8") as f:
                    parsed = chess.pgn.read_game(f)
                pgmoves = list(parsed.mainline_moves())
                res.check(f"game {g}: PGN round-trip move count", len(pgmoves), len(moves))
                res.check(f"game {g}: PGN result", parsed.headers["Result"], result)
            except Exception as exc:
                res.add(f"game {g}: PGN parses", False, repr(exc))

        # A couple of PGNs must exist and be non-empty.
        pgns = sorted(os.listdir(PGNS))
        res.add("PGN files saved", len(pgns) >= 2, f"{len(pgns)} files in {PGNS}")
        res.add("midgame PGN exists", os.path.exists(os.path.join(PGNS, f"selfplay_game{NUM_GAMES:02d}.pgn")),
                "last game starts from FEN")
        with open(os.path.join(PGNS, "selfplay_game10.pgn"), encoding="utf-8") as f:
            txt = f.read()
        res.add("midgame PGN has FEN header", "[FEN " in txt and "[SetUp " in txt,
                txt.splitlines()[0] if txt else "")

        # Aggregate
        total_plies = sum(g["plies"] for g in games)
        res.add("self-play games completed without illegal move/crash/timeout",
                all(g["illegal"] is None and g["timeout"] is None and g["crash"] is None
                    for g in games),
                f"{len(games)} games, {total_plies} plies")
        res.add("self-play colors alternated", len({g["white"] for g in games}) == 2,
                str(sorted({g["white"] for g in games})))
        res.add("self-play at least 10 games", len(games) >= 10, f"{len(games)} games")
        results: dict[str, int] = {}
        for g in games:
            results[g["result"]] = results.get(g["result"], 0) + 1
        print("self-play results:", results, flush=True)
        print("self-play aggregate:", {
            "plies": total_plies,
            "avg_ms": round(sum(g["avg_ms"] for g in games) / len(games), 1),
            "max_ms": max(g["max_ms"] for g in games),
        }, flush=True)
    finally:
        eng_a.close()
        eng_b.close()

    summary = res.summary()
    summary["games"] = games
    import json
    with open(os.path.join(testutil.LOGS, "selfplay.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nselfplay: {summary['passed']}/{summary['total']} passed in {summary['elapsed_s']}s", flush=True)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
