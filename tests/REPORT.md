# ChessBot QA Report

- **Date:** 2026-11-24
- **Workspace:** `<project>`
- **Engine under test:** `tests/chessbot_test.exe`, built from `src/uci.cpp`, `src/bitboards.cpp`, `src/position.cpp`, `src/eval.cpp`, `src/search.cpp`, `src/nnue_weights.cpp`
- **Toolchain:** g++ 16.1.0 (MinGW-w64 UCRT), python-chess 1.10.0, Stockfish 19 (`tools/sf19/stockfish/stockfish-windows-x86-64-universal.exe`)
- **Scope:** tests only under `tests/`. No changes were made to `src/`, `tools/`, or data files.
- **Full-suite runtime:** 96.2 s (`tests/run_all.py`), plus a few seconds for one-off reference probes.

> **Post-fix status (2026-11-24):** after the startpos/FEN/input guards and the
> later hardening fixes, the full suite is **358/358**; see
> `tests/logs/run_final.log`. The 351/358 results below are historical and
> document the bugs that were fixed. Perft counts are unchanged.
>
> **2026-11-25:** the merged Lazy SMP + ProbCut + PGO build also passes
> **358/358** (`tests/logs/run_pgo_probcut.log`).
>
> **2026-11-25 (Syzygy):** WDL/DTZ support via Fathom is enabled; the PGO build
> passes **358/358** (`tests/logs/run_syzygy_final2.log`). The 8-thread stress
> test (`tests/run_stress.py`, 120 cycles, root+node TB probe) reports
> 0 errors, 0 timeouts, 0 illegal moves (`tests/logs/stress.json`).

## Verdict

**351 / 358 checks passed (98.0%).** All core functionality passed: perft/move generation, normal UCI operation, search-move legality, time management, and 10-game self-play. The 7 failures are all malformed-input / default-state robustness issues, not move-generation or search-legality failures.

| Suite | Passed | Failed | Total | Result | Log |
|---|---:|---:|---:|---|---|
| Perft | 54 | 0 | 54 | **PASS** | `tests/logs/perft.json` |
| UCI protocol / smoke | 103 | 2 | 105 | **FAIL** (2 robustness checks) | `tests/logs/protocol.json` |
| Time management | 59 | 0 | 59 | **PASS** | `tests/logs/timing.json` |
| Self-play | 36 | 0 | 36 | **PASS** | `tests/logs/selfplay.json` |
| Robustness | 99 | 5 | 104 | **FAIL** (5 malformed/default-state checks) | `tests/logs/robustness.json` |
| **TOTAL** | **351** | **7** | **358** | | `tests/logs/summary.json` |

All bestmoves returned during the suite were checked with python-chess. No illegal move, crash, timeout, or inconsistent FEN was observed in normal operation.

---

## 1. Perft validation

Command used in the engine (stdin): `position fen <FEN>`, then `perft <depth>`.

| Position | d1 | d2 | d3 | d4 | d5 | Result |
|---|---:|---:|---:|---:|---:|---|
| Startpos | 20 | 400 | 8,902 | 197,281 | 4,865,609 | PASS |
| Kiwipete | 48 | 2,039 | 97,862 | 4,085,603 | — | PASS |
| Position 3 `8/2p5/...` | 14 | 191 | 2,812 | 43,238 | 674,624 | PASS |
| Position 4 `r3k2r/Pppp...` | 6 | 264 | 9,467 | 422,333 | — | PASS |
| Position 5 `rnbq1k1r/...` | 44 | 1,486 | 62,379 | 2,103,487 | — | PASS |
| Task FEN `r4rk1/1pp1qppp/.../R1B1K2R w KQ -` | 44 | 1,763 | 73,911 | 2,846,198 | — | PASS |

All engine counts exactly match the expected counts. Cross-checks:

- python-chess independent perft at depths 1–3 for every position.
- Stockfish 19 `go perft` at every deepest depth above; log `tests/logs/stockfish_refs.log`.
- Together these confirm every count; e.g. python-chess gives Kiwipete d1–d3 (48 / 2,039 / 97,862) and Stockfish gives Kiwipete d4 = 4,085,603; Stockfish gives task-FEN d4 = 2,846,198.

### Note on the provided Position 6 FEN

The FEN supplied in the task:

`r4rk1/1pp1qppp/p1np1n2/2b1p3/2B1P3/P1NP1N2/1PP1QPPP/R1B1K2R w KQ - 0 1`

is **not** the Chess Programming Wiki "Position 6" FEN. CPW Position 6 is:

`r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10`

For the task FEN, python-chess gives **44 / 1,763 / 73,911** at d1–d3 and Stockfish 19 gives **2,846,198** at d4; the familiar 46 / 2,079 / 89,890 / 3,894,594 values do not apply. The engine matches the task FEN exactly. As an extra check, the engine also matches the CPW Position 6 values at d1–d4 (46 / 2,079 / 89,890 / 3,894,594); see `tests/logs/cpw_position6.log` and `tests/logs/cpw_position6_refs.log`.

### Perft edge cases

| Case | Depth | Nodes | Result |
|---|---:|---:|---|
| Castling rights `-` | 3 | 11,522 | PASS |
| Castling rights `KQkq` | 3 | 13,744 | PASS |
| EP pinned pawn | 3 | 492 | PASS |
| EP discovered check | 3 | 857 | PASS |
| Capture promotion | 3 | 857 | PASS |
| Promotion push | 3 | 342 | PASS |
| Halfmove clock 100 | 2 | 25 | PASS |
| Stalemate | 1 | 0 | PASS |
| Checkmate | 1 | 0 | PASS |
| `perft 0` | 0 | 1 | PASS |

All edge-case counts match python-chess.

---

## 2. UCI protocol / smoke tests

All of the following returned correct responses and legal moves:

- `uci` / `uciok`; options `Hash`, `Move Overhead`, `UseNNUE`, `NNUEWeight`, `Clear Hash` advertised with correct bounds.
- `isready` / `readyok`, including during an active search (immediate response).
- `ucinewgame`, including during an active search (search aborted, position reset to startpos).
- `position startpos` and `position startpos moves ...`, including castling, promotions, and 200+ move sequences.
- `position fen ...` and `position fen ... moves ...`; engine FEN after every tested sequence exactly matched python-chess (`en_passant='fen'` comparison).
- `go depth 0/1/5`, `go movetime 200/1000`, `go nodes 1/20000`, `go wtime/btime` with and without `movestogo`/increments, `go mate 1`.
- `stop` during `go infinite`; `quit` during search (exit code 0 in 0.004 s).
- `setoption` for `Hash` (1, 16, 32, 64, 256, 1024, 4096 MB), `Move Overhead` (0, 30, 5000), `NNUEWeight` (0, 40, 100, 999→clamped 200), `UseNNUE` true/false, `Clear Hash`.
- `d` (board/FEN), `eval` (classical/neural/total/options), `perft`, `bench` (depths 2 and 5).
- Bestmove format regex `^[a-h][1-8][a-h][1-8][qrbn]?$` (or `0000` only when no legal move), exactly one `bestmove` per search; every bestmove legal per python-chess.
- Terminal positions: checkmate and stalemate both return `bestmove 0000` with `info depth 0 score mate 0` / `score cp 0` immediately.

### Protocol failures

1. **Startup default position is an empty board, not startpos.** `d` after startup returns `8/8/8/8/8/8/8/8 w - - 0 1`. A subsequent `go` then crashes/hangs (see Bug 1).
2. **`d` output is not flushed.** A piped GUI sees nothing after `d`; the board/FEN arrives only when a later command using `std::endl` (e.g. `isready`) flushes stdout (see Bug 3).

---

## 3. Time management

| Test | Runs | Observed | Limit | Result |
|---|---:|---|---:|---|
| `go movetime 200` | 10 | min 0.090 s, median 0.124 s, max 0.171 s | ≤ 2.2 s | PASS |
| `go movetime 1000` | 5 | min 0.529 s, median 0.763 s, max 0.970 s | ≤ 3.0 s | PASS |
| `go movetime 3000` | 1 | 2.970 s | ≤ 5.0 s | PASS |
| `go wtime 1000 btime 1000` | 1 | 0.004 s | ≤ 1.5 s | PASS |
| `go wtime 100 btime 100` | 1 | 0.002 s | ≤ 1.0 s | PASS |
| `go wtime 5000 btime 5000 movestogo 20` | 1 | 0.231 s | ≤ 2.0 s | PASS |
| `go wtime 10000 btime 10000 movestogo 40` | 1 | 0.273 s | ≤ 2.0 s | PASS |
| `go wtime 5000 btime 5000 winc 100 binc 100` | 1 | 0.729 s | ≤ 2.0 s | PASS |
| `Move Overhead 0`, `go movetime 100` | 5 | min 0.053 s, max 0.087 s | ≤ 1.0 s | PASS |
| `go infinite` + `stop` after 350 ms | 5 | stop→bestmove max 0.0001 s | ≤ 1.0 s | PASS |
| `go infinite` 1.5 s + `stop` | 1 | stop→bestmove 0.0001 s | ≤ 1.0 s | PASS |

No run grossly exceeded its budget. All bestmoves were legal. Clock-based management is very conservative at low time (1 s clock uses only ~4 ms), which avoids flagging but underuses the clock.

---

## 4. Self-play stress

- 10 games, 40 ms/move (`Move Overhead 0`), alternating colors between two engine processes.
- 1,506 plies total; every move validated with python-chess (`chess.Move.from_uci` and `in board.legal_moves`).
- No illegal move, crash, timeout, or malformed bestmove. Search time averaged 32.9 ms per move, maximum 41.8 ms; no move came close to the 5 s harness timeout.
- Games 1–9 from startpos; game 10 from the midgame FEN `r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1` (Kiwipete).
- Results: 2 white wins, 4 black wins, 4 draws; terminations: 6 checkmate, 2 threefold, 1 50-move, 1 insufficient material.
- PGNs: `tests/pgn/selfplay_game01.pgn` … `selfplay_game10.pgn` (game 10 has `[FEN]`/`[SetUp]`). Each PGN was parsed back by python-chess and its move count/result verified.

| Game | Start | Plies | Result | Termination |
|---:|---|---:|---|---|
| 1 | startpos | 188 | 1/2–1/2 | threefold |
| 2 | startpos | 214 | 0–1 | checkmate |
| 3 | startpos | 128 | 0–1 | checkmate |
| 4 | startpos | 122 | 0–1 | checkmate |
| 5 | startpos | 203 | 1–0 | checkmate |
| 6 | startpos | 129 | 1/2–1/2 | insufficient material |
| 7 | startpos | 113 | 1–0 | checkmate |
| 8 | startpos | 225 | 1/2–1/2 | 50-move |
| 9 | startpos | 74 | 1/2–1/2 | threefold |
| 10 | midgame FEN | 110 | 0–1 | checkmate |

---

## 5. Robustness

Passed:

- Checkmate, stalemate, and back-rank mate: `bestmove 0000`, correct `info depth 0` line, no hang.
- Promotion push and capture-promotion searches return correctly encoded 5-character moves (`a7a8q`, `a7b8q`).
- En-passant, castling, no-castling, halfmove-100, insufficient-material, and in-check positions: bestmove legal.
- Mate-in-1 search returns `e1e8` with `score mate 1`; `go mate 1` also returns `e1e8`.
- Hash resizing 1 / 16 / 256 / 1024 MB with searches after each; one-off 4096 MB test ready in 1.06 s and searched correctly (`tests/logs/hash4096.log`).
- `UseNNUE false` → `neural 0`, `total == classical`; `UseNNUE true` and `NNUEWeight 0/40/100` behave consistently.
- `Hash 0`, `Hash -5`, `NNUEWeight 999`, `Move Overhead 5000`, unknown option names: accepted/ignored without breaking subsequent searches.
- Malformed `go depth abc`: search starts (unbounded depth) but `stop` recovers promptly with a legal bestmove.
- Shipped binaries `chessbot_test.exe`, `chessbot_res.exe`, `chessbot_sfnet.exe`, `chessbot_nnue.exe`: all passed uci/perft-3/search/bench. `chessbot_sfnet.exe` uses a different NNUE configuration (`direct 1`, weight 50, startpos eval 27/18, bench-2 nodes ~6.3k vs ~2.9k for the others); that appears to be intentional build variation, not a failure.

Failed (see Bug reports):

- `go` without a valid position (fresh process or after invalid FEN) hangs/crashes.
- Invalid numeric `setoption`/FEN values cause an uncaught `std::invalid_argument`.
- `d` output is buffered.
- Startup default position is empty.

---

## 6. Bugs

### Bug 1 — `go` without a valid position crashes in `Position::inCheck()` (Medium)

With no preceding valid `position`, or after an invalid FEN that leaves the board empty, `kingSq[side] == -1`. `Position::inCheck()` calls `isSquareAttacked(-1, ...)`, which indexes attack/magic tables out of bounds.

Minimal C++ repro (in `tests/`):

```
tests\probe_empty.exe
```

Observed:

```
empty: kings -1,-1 occ=0x0
generateMoves count=0
calling inCheck...
RETURNCODE=3221225477       # 0xC0000005 ACCESS_VIOLATION
```

Minimal UCI repro:

```
tests\chessbot_test.exe
go depth 1
```

Observed: no `bestmove` after 2 s; process remains alive but unresponsive (Windows error reporting), then has to be killed. Same after:

```
position fen banana
go depth 1
```

Evidence: `tests/logs/repro_bugs.log`, `tests/logs/probe_empty_run.log`.

Related source locations (report only; not fixed):

- `src/uci.cpp`: `UciEngine` constructor does not call `pos_.setStartpos()`; `setFen` failure leaves the cleared (kingless) position.
- `src/engine.hpp`: `inCheck()` calls `isSquareAttacked(kingSq[side], ~side)` with no guard for `kingSq == -1`.
- `src/position.cpp`: `isSquareAttacked()` indexes `pawnAttacks[..][sq]`, `knightAttacks[sq]`, `bishopMagics[sq]`, `rookMagics[sq]` without validating `sq`.

Workaround: always send `ucinewgame` or a valid `position` before `go`.

### Bug 2 — Uncaught `std::invalid_argument` on non-numeric option/FEN fields (Low)

Repro:

```
tests\chessbot_test.exe
setoption name Hash value abc
isready
```

Observed output instead of `readyok`:

```
terminate called after throwing an instance of 'std::invalid_argument'
  what():  stoi
```

The same happens for a malformed FEN clock field:

```
position fen 4k3/8/8/8/8/8/8/4K3 w - - x 1
isready
```

`std::stoi` is used in `UciEngine::setOption` and `Position::setFen` without exception handling. Evidence: `tests/logs/repro_bugs.log`, `tests/logs/robustness.log`.

### Bug 3 — `d` output is not flushed (Low)

Repro (piped stdin/stdout):

```
tests\chessbot_test.exe
d
(wait 0.5 s)          -> no output
isready               -> board, "FEN: ...", and "readyok" appear together
```

`d` prints `pos_.ascii()` using `'\n'`, while the program disables cin/cout tie and stdout is fully buffered when piped. `d` is a debug command, but GUIs/tools see no output until a later `std::endl` command. Evidence: `tests/logs/repro_bugs.log`.

### Bug 4 — Startup default position is empty (Low)

A fresh engine, before any `position`/`ucinewgame`, reports:

```
8/8/8/8/8/8/8/8 w - - 0 1
```

rather than startpos. This is the root state that triggers Bug 1. A GUI normally sends `position`, so it does not affect normal operation, but `ucinewgame`/constructor initialization should set startpos defensively.

---

## 7. Observations / non-failures

- `go nodes N` is enforced by checking every 1,024 nodes; `go nodes 1` completed depth 5 with 790 reported nodes before stopping. UCI permits approximate node limits, but very small limits are not exact.
- Low-clock time management is extremely conservative: `wtime 1000` used ~4 ms, `wtime 100` ~2 ms. This protects the clock but leaves time unused.
- `Move Overhead` is subtracted from `movetime`; default 30 means `go movetime 20` has a ~1 ms budget. Self-play therefore set `Move Overhead 0`.
- `position` silently ignores an illegal move and stops parsing the rest of the move list; no error message. This is acceptable for well-formed GUI input but worth knowing.
- `ucinewgame`/`position`/`setoption Hash` during a search stop it and the aborted search may still emit a `bestmove`. This is not a crash, but a GUI should discard output after aborting.
- `chessbot_sfnet.exe` differs from the other binaries (direct NNUE, weight 50, different eval/bench); the other three produce identical startpos eval/bench numbers.
- One-off tests: Hash 4096 MB allocation ready in 1.06 s; CPW Position 6 perft matches 46/2,079/89,890/3,894,594. Logs: `tests/logs/hash4096.log`, `tests/logs/cpw_position6.log`, `tests/logs/cpw_position6_refs.log`.
- Not exercised: extreme malformed FENs with impossible piece counts (e.g. >32 pieces/pawns on back ranks), repeated 4096 MB Hash resizes, and very long `go depth 100+` searches.

---

## 8. Commands and artifacts

### Build

```
cd /d "<project>"
g++ -std=c++20 -O2 -march=native -pthread "-Wl,--stack,33554432" ^
    -o tests/chessbot_test.exe ^
    src/uci.cpp src/bitboards.cpp src/position.cpp src/eval.cpp ^
    src/search.cpp src/nnue_weights.cpp
```

PowerShell equivalent: `tests/build.ps1`.

### Run suites

```
python -u tests/run_perft.py
python -u tests/run_protocol.py
python -u tests/run_timing.py
python -u tests/run_selfplay.py
python -u tests/run_robustness.py
python -u tests/run_all.py
```

### Reference / repro probes

```
python -u tests/repro_bugs.py
python -u tests/probe_stockfish_refs.py
python -u tests/probe_cpw_position6.py
python -u tests/probe_cpw_position6_refs.py
python -u tests/probe_hash4096.py
tests\probe_empty.exe
```

### Logs

- `tests/logs/summary.json` — aggregate pass/fail
- `tests/logs/run_all.log` — full console output of all suites
- `tests/logs/perft.json` / `tests/logs/perft.log`
- `tests/logs/protocol.json` / `tests/logs/protocol.log`
- `tests/logs/timing.json` / `tests/logs/timing.log`
- `tests/logs/selfplay.json` / `tests/logs/selfplay.log`
- `tests/logs/robustness.json` / `tests/logs/robustness.log`
- `tests/logs/repro_bugs.log` — minimal bug reproductions
- `tests/logs/stockfish_refs.log` — Stockfish 19 perft cross-checks
- `tests/logs/cpw_position6.log`, `tests/logs/cpw_position6_refs.log`
- `tests/logs/hash4096.log`, `tests/logs/probe_empty_run.log`

### Test code

- `tests/uci_engine.py` — asynchronous UCI process wrapper
- `tests/testutil.py` — paths, result collection, info parsing
- `tests/run_perft.py`, `run_protocol.py`, `run_timing.py`, `run_selfplay.py`, `run_robustness.py`
- `tests/run_all.py`, `tests/build.ps1`, `tests/repro_bugs.py`
- `tests/probe_*.py`, `tests/probe_empty.cpp` — one-off probes and crash isolator
- `tests/pgn/selfplay_game01.pgn` … `selfplay_game10.pgn`
