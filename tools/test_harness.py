#!/usr/bin/env python3
"""Protocol and methodology tests for the hardened arena.

Run from this directory::

    python -m unittest -v test_harness.py

The tests use ``fake_engine.py`` for deliberate timeout / stale-bestmove /
null-move scenarios.  They do not require a real chess engine.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest

import chess
import chess.pgn

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import arena  # noqa: E402


FOOLS_MATE = ["f2f3", "e7e5", "g2g4", "d8h4"]
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


def make_board(moves: list[str], fen: str | None = None) -> chess.Board:
    board = chess.Board(fen) if fen else chess.Board()
    for uci in moves:
        board.push_uci(uci)
    return board


class GameResultTests(unittest.TestCase):
    def test_checkmate(self):
        board = make_board(FOOLS_MATE)
        self.assertTrue(board.is_checkmate())
        self.assertEqual(arena.game_result(board, 300), ("0-1", "checkmate"))

    def test_stalemate(self):
        board = chess.Board(STALEMATE_FEN)
        self.assertTrue(board.is_stalemate())
        self.assertEqual(arena.game_result(board, 300), ("1/2-1/2", "stalemate"))

    def test_insufficient_material(self):
        board = chess.Board("8/8/8/8/8/8/k1K5/8 w - - 0 1")
        self.assertTrue(board.is_insufficient_material())
        self.assertEqual(
            arena.game_result(board, 300), ("1/2-1/2", "insufficient material")
        )

    def test_fifty_move_rule(self):
        board = chess.Board("k7/8/8/8/8/8/8/K6R w - - 100 1")
        self.assertTrue(board.is_fifty_moves())
        self.assertEqual(arena.game_result(board, 300), ("1/2-1/2", "50-move rule"))

    def test_threefold_repetition_from_history(self):
        board = chess.Board()
        for _ in range(2):
            for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
                board.push_uci(uci)
        self.assertTrue(board.is_repetition(3))
        self.assertEqual(
            arena.game_result(board, 300), ("1/2-1/2", "threefold repetition")
        )

    def test_max_plies(self):
        board = make_board(["e2e4", "e7e5"])
        result, reason = arena.game_result(board, 2)
        self.assertEqual(result, "1/2-1/2")
        self.assertIn("max plies", reason or "")

    def test_continuing_position(self):
        self.assertEqual(arena.game_result(chess.Board(), 300), (None, None))


class PositionHistoryTests(unittest.TestCase):
    def test_startpos_command_contains_every_move(self):
        moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"]
        board = make_board(moves)
        command = arena.build_position_command(board)
        self.assertTrue(command.startswith("position startpos moves "))
        sent = command.split(" moves ", 1)[1].split()
        self.assertEqual(sent, moves)
        probe = chess.Board()
        for uci in sent:
            probe.push_uci(uci)
        self.assertEqual(probe.fen(), board.fen())

    def test_repetition_survives_position_command(self):
        board = chess.Board()
        for _ in range(2):
            for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
                board.push_uci(uci)
        command = arena.build_position_command(board)
        sent = command.split(" moves ", 1)[1].split()
        probe = chess.Board()
        for uci in sent:
            probe.push_uci(uci)
        self.assertTrue(probe.is_repetition(3))
        self.assertEqual(probe.fen(), board.fen())

    def test_checkmate_survives_position_command(self):
        board = make_board(FOOLS_MATE)
        command = arena.build_position_command(board)
        sent = command.split(" moves ", 1)[1].split()
        probe = chess.Board()
        for uci in sent:
            probe.push_uci(uci)
        self.assertTrue(probe.is_checkmate())
        self.assertEqual(arena.game_result(probe, 300), ("0-1", "checkmate"))

    def test_stalemate_fen_command(self):
        board = chess.Board(STALEMATE_FEN)
        command = arena.build_position_command(board)
        self.assertTrue(command.startswith("position fen "))
        self.assertNotIn(" moves ", command)
        fen = command[len("position fen ") :]
        probe = chess.Board(fen)
        self.assertTrue(probe.is_stalemate())

    def test_custom_root_with_moves(self):
        root = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
        board = chess.Board(root)
        board.push_uci("e2e4")
        command = arena.build_position_command(board)
        self.assertTrue(command.startswith("position fen " + root + " moves e2e4"))
        probe = chess.Board(root)
        probe.push_uci("e2e4")
        self.assertEqual(probe.fen(), board.fen())


class BestmoveValidationTests(unittest.TestCase):
    def test_nullmove_in_nonterminal_is_error(self):
        with self.assertRaises(arena.EngineError) as ctx:
            arena.coerce_move(chess.Board(), "0000")
        self.assertEqual(ctx.exception.kind, "nullmove")

    def test_nullmove_in_checkmate_is_terminal(self):
        board = make_board(FOOLS_MATE)
        move, result, reason = arena.coerce_move(board, "0000")
        self.assertIsNone(move)
        self.assertEqual(result, "0-1")
        self.assertEqual(reason, "checkmate")

    def test_nullmove_in_stalemate_is_terminal(self):
        board = chess.Board(STALEMATE_FEN)
        move, result, reason = arena.coerce_move(board, "0000")
        self.assertIsNone(move)
        self.assertEqual(result, "1/2-1/2")
        self.assertEqual(reason, "stalemate")

    def test_illegal_move_is_error(self):
        with self.assertRaises(arena.EngineError) as ctx:
            arena.coerce_move(chess.Board(), "e2e5")
        self.assertEqual(ctx.exception.kind, "illegal")

    def test_malformed_move_is_error(self):
        with self.assertRaises(arena.EngineError) as ctx:
            arena.coerce_move(chess.Board(), "not-a-move")
        self.assertEqual(ctx.exception.kind, "protocol")

    def test_legal_move_accepted(self):
        move, result, reason = arena.coerce_move(chess.Board(), "e2e4")
        self.assertIsNotNone(move)
        self.assertEqual(move.uci(), "e2e4")
        self.assertIsNone(result)
        self.assertIsNone(reason)


class SummaryTests(unittest.TestCase):
    def test_ci_formatting_extremes(self):
        self.assertEqual(arena.fmt_ci(1.0, 1.0), "+inf .. +inf")
        self.assertEqual(arena.fmt_ci(0.0, 0.0), "-inf .. -inf")
        mid = arena.fmt_ci(0.25, 0.75)
        self.assertIn(" .. ", mid)
        self.assertNotIn("-0", mid)

    def test_elo_from_score(self):
        self.assertIsNone(arena.elo_from_score(0.0))
        self.assertIsNone(arena.elo_from_score(1.0))
        self.assertAlmostEqual(arena.elo_from_score(0.5) or 0.0, 0.0)


class PgnTests(unittest.TestCase):
    def test_pgn_has_result_termination_and_moves(self):
        board = make_board(FOOLS_MATE)
        record = arena.GameRecord(
            game_no=1,
            pair_no=1,
            pair_index=0,
            a_white=True,
            opening="",
            result="0-1",
            reason="checkmate",
            board=board,
            a_score=0.0,
            plies=len(board.move_stack),
            elapsed=0.1,
            white_label="A:Fake",
            black_label="B:Fake",
        )
        buf = io.StringIO()
        arena.write_pgn(buf, record, event="test")
        buf.seek(0)
        game = chess.pgn.read_game(buf)
        self.assertIsNotNone(game)
        self.assertEqual(game.headers["Result"], "0-1")
        self.assertEqual(game.headers["Termination"], "checkmate")
        self.assertEqual(game.headers["ArenaPair"], "1")
        moves = list(game.mainline_moves())
        self.assertEqual([m.uci() for m in moves], FOOLS_MATE)


class EngineProtocolTests(unittest.TestCase):
    def setUp(self):
        self.fake = os.path.join(HERE, "fake_engine.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_timeout_kills_and_restarts_without_stale_move(self):
        marker = os.path.join(self.tmp.name, "stale.marker")
        engine = arena.UciEngine(
            self.fake,
            extra_args=["stale-once", "--marker", marker],
            name="stale",
            startup_timeout=10.0,
        )
        self.addCleanup(engine.quit)
        engine.new_game()
        with self.assertRaises(arena.EngineError) as ctx:
            engine.search_move(
                arena.build_position_command(chess.Board()),
                "go movetime 20",
                hard_timeout=0.4,
            )
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertTrue(engine.broken)
        self.assertIsNone(engine.proc)
        self.assertEqual(engine.restarts, 0)
        self.assertTrue(
            any(a["kind"] == "timeout" for a in engine.aborts),
            engine.aborts,
        )

        # The next game must get a fresh process and a legal move.  If the
        # late 'bestmove e2e4' from the killed process leaked, the fake engine
        # would not be in legal mode yet and the test would fail.
        engine.new_game()
        self.assertEqual(engine.restarts, 1)
        token, elapsed, line = engine.search_move(
            arena.build_position_command(chess.Board()),
            "go movetime 20",
            hard_timeout=2.0,
        )
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertTrue(line.startswith("bestmove "))
        move = chess.Move.from_uci(token)
        self.assertIn(move, chess.Board().legal_moves)

    def test_new_game_sends_readyok_each_time(self):
        engine = arena.UciEngine(
            self.fake,
            extra_args=["legal"],
            name="legal",
            startup_timeout=10.0,
        )
        self.addCleanup(engine.quit)
        for _ in range(3):
            engine.new_game()
            self.assertFalse(engine.broken)
            self.assertIsNotNone(engine.proc)
        self.assertEqual(engine.restarts, 0)

    def test_duplicate_bestmove_is_detected(self):
        # A tiny inline engine is overkill; instead exercise the idle-drain
        # guard by injecting a bestmove into the queue.
        engine = arena.UciEngine(
            self.fake,
            extra_args=["legal"],
            name="legal",
            startup_timeout=10.0,
        )
        self.addCleanup(engine.quit)
        engine.new_game()
        assert engine.q is not None
        engine.q.put("bestmove e2e4")
        with self.assertRaises(arena.EngineError) as ctx:
            engine.search_move(
                arena.build_position_command(chess.Board()),
                "go movetime 20",
                hard_timeout=2.0,
            )
        self.assertEqual(ctx.exception.kind, "stale")
        self.assertTrue(engine.broken)
        self.assertIsNone(engine.proc)


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fake = os.path.join(HERE, "fake_engine.py")
        self.arena = os.path.join(HERE, "arena.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_arena(self, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
        cmd = [sys.executable, self.arena, *args]
        return subprocess.run(
            cmd,
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def read_pgn(self, path: str) -> list[chess.pgn.Game]:
        games: list[chess.pgn.Game] = []
        with open(path, encoding="utf-8") as fh:
            while True:
                game = chess.pgn.read_game(fh)
                if game is None:
                    break
                games.append(game)
        return games

    def test_mirrored_pairs_and_pgn(self):
        pgn = os.path.join(self.tmp.name, "mirror.pgn")
        proc = self.run_arena(
            "--engine-a", self.fake, "--engine-a-arg", "legal",
            "--engine-b", self.fake, "--engine-b-arg", "legal",
            "--pairs", "1",
            "--movetime", "1",
            "--max-plies", "20",
            "--timeout-extra", "2",
            "--pgn", pgn,
            "--strict",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        games = self.read_pgn(pgn)
        self.assertEqual(len(games), 2)
        colors = {g.headers["ArenaColorA"] for g in games}
        self.assertEqual(colors, {"white", "black"})
        openings = {g.headers["Opening"] for g in games}
        self.assertEqual(len(openings), 1)
        for game in games:
            self.assertEqual(game.headers["ArenaPair"], "1")
            self.assertIn(game.headers["Result"], ("1-0", "0-1", "1/2-1/2"))
            self.assertTrue(game.headers["Termination"])

    def test_nullmove_in_legal_position_is_loss(self):
        log = os.path.join(self.tmp.name, "zero.log")
        pgn = os.path.join(self.tmp.name, "zero.pgn")
        proc = self.run_arena(
            "--engine-a", self.fake, "--engine-a-arg", "zero",
            "--engine-b", self.fake, "--engine-b-arg", "legal",
            "--pairs", "1",
            "--movetime", "1",
            "--max-plies", "30",
            "--timeout-extra", "2",
            "--pgn", pgn,
            "--log", log,
            "--strict",
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + "\n" + proc.stderr)
        self.assertIn("nullmove", proc.stdout)
        self.assertIn("engine errors: 2", proc.stdout)
        games = self.read_pgn(pgn)
        self.assertEqual(len(games), 2)
        for game in games:
            self.assertIn("0000", game.headers["Termination"])
            self.assertIn("nullmove", game.headers["Termination"])
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("ABORT [nullmove]", text)

    def test_timeout_recovery_between_games(self):
        marker = os.path.join(self.tmp.name, "timeout.marker")
        log = os.path.join(self.tmp.name, "timeout.log")
        proc = self.run_arena(
            "--engine-a", self.fake,
            "--engine-a-arg", "stale-once", "--engine-a-arg=--marker",
            "--engine-a-arg", marker,
            "--engine-b", self.fake, "--engine-b-arg", "legal",
            "--games", "2",
            "--movetime", "20",
            "--timeout-extra", "0.3",
            "--max-plies", "20",
            "--log", log,
            "--strict",
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + "\n" + proc.stderr)
        # Game 1 should be a timeout loss for A, game 2 must still be played.
        self.assertIn("game 2/2", proc.stdout)
        self.assertIn("engine errors: 1", proc.stdout)
        self.assertIn("restarts: A=1 B=0", proc.stdout)
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("timeout", text)
        self.assertIn("restart #1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
