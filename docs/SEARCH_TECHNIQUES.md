# Search and optimization techniques research

Sources: [Chess Programming Wiki: Search](https://www.chessprogramming.org/Search),
[Selectivity](https://www.chessprogramming.org/Selectivity),
[Pruning](https://www.chessprogramming.org/Pruning),
[Extensions](https://www.chessprogramming.org/Extensions),
[Move Ordering](https://www.chessprogramming.org/Move_Ordering),
[Stockfish search.cpp](https://github.com/official-stockfish/Stockfish/blob/master/src/search.cpp).
CPW content is CC BY-SA 3.0.

## Implemented in ChessBot

| Technique | Status | Notes |
|---|---|---|
| Alpha-beta / negamax | yes | fail-soft |
| Iterative deepening | yes | soft/hard time |
| Principal Variation Search (PVS) | yes | null-window + re-search |
| Aspiration windows | yes | root re-order after fail |
| Transposition table | yes | 16-byte slots, rule50-aware, aging |
| Quiescence search | yes | captures/promotions, checks as evasions |
| Null-move pruning | yes | adaptive R |
| Late Move Reductions (LMR) | yes | log table |
| Late Move Pruning (LMP) | yes | |
| Futility pruning | yes | shallow quiet moves |
| Reverse futility (static null) | yes | |
| Razoring | yes | |
| SEE pruning | yes | captures |
| Killer moves | yes | per ply |
| Countermove heuristic | yes | previous move |
| History heuristic | yes | butterfly + gravity |
| Capture history | yes | |
| Continuation history | yes | previous piece/to |
| Check extension | yes | |
| Internal iterative reduction | yes | shallow TT-move bootstrap |
| Mate-distance pruning | yes | |
| Lazy SMP | yes | `Threads` 1..16, main + helpers, lockless shared TT |
| ProbCut | yes | non-PV, depth >= 5, SEE >= 100, beta+180; −10% nodes, Elo unproven |
| PEXT/BMI2 slider lookup | yes | compile guard `__BMI2__`, perft 54/54, NPS neutral |
| PGO build | yes | `build_pgo.ps1`, +5–6% NPS on this machine |
| MultiPV | analysis build | `experiments/multipv_build`, not in MT binary |
| Pondering | analysis build | `experiments/multipv_build`, not in MT binary |
| Polyglot book | prototype | optional, no measured gain |
| HalfKP NNUE | training pipeline | not yet wired into C++ |

## Missing high-value techniques

| Technique | Why | Risk | Source |
|---|---|---|---|
| **Singular extensions** | Extend a TT move that is much better than all alternatives | High: TT exclusion, search explosion | [CPW](https://www.chessprogramming.org/Singular_Extensions) |
| **Correction history** | Correct static eval from search history / pawn structure / material | Medium: needs careful normalization | [CPW](https://www.chessprogramming.org/Correction_History) |
| **Enhanced transposition cutoff** | Try TT move before generating/ordering all moves | Low | [CPW](https://www.chessprogramming.org/Enhanced_Transposition_Cutoff) |
| **Internal iterative deepening** | Full IID instead of only reducing depth | Low/medium | [CPW](https://www.chessprogramming.org/Internal_Iterative_Deepening) |
| **Null-move verification** | Avoid zugzwang explosions | Medium | [CPW](https://www.chessprogramming.org/Null_Move_Verification) |
| **Recapture / one-reply / mate-threat extensions** | Tactical safety at low cost | Medium | [CPW Extensions](https://www.chessprogramming.org/Extensions) |
| **Multi-Cut** | Forward prune when several reduced moves fail high | High | [CPW](https://www.chessprogramming.org/Multi-Cut) |
| **Dynamic time management** | Stability, best-move changes, node velocity | Low | [CPW](https://www.chessprogramming.org/Time_Management) |
| **Syzygy tablebases** | Perfect endgames | Integration cost; needs files | [CPW](https://www.chessprogramming.org/Syzygy_Bases) |
| **Policy network for move ordering** | Learn quiet tactical moves from data | Research | NNUE/policy |

## Not recommended for this engine

- **MCTS/PUCT/UCT** — much weaker than alpha-beta + NNUE at equal time for chess.
- **Proof-number search / conspiracy search / SSS*** — memory-heavy, not competitive for general chess.
- **Best-first B\*** — niche, huge memory.
- **MTD(f)** — can work, but PVS + iterative deepening is simpler and already competitive.

## Optimizations worth doing

| Optimization | Expected effect | Risk |
|---|---|---|
| Merge/measure Lazy SMP | +30..100 Elo depending on cores | Done: 4t NPS ~6.5M |
| PEXT/BMI2 magic lookup where available | 2–5% NPS | Done: neutral locally |
| PGO build | 3–10% NPS | Done: +5–6% locally |
| TT prefetch before probing | 1–3% NPS | Low |
| Packed/staged move generation | 3–10% NPS | Medium |
| Incremental HalfKP accumulator + SIMD | required for NNUE inference | High coding effort |
| Pawn hash / eval cache | 2–8% NPS | Low/medium |
| Huge pages for TT | 1–3% at large Hash | Low |
| Lazy SMP with NUMA awareness | scaling only | High |
| Opening book | 0..15 Elo at fast TC | Already tested: no gain |
| Syzygy | endgame Elo | Files/licensing |

## Recommended order

1. MultiPV/Ponder остаются отдельной analysis-сборкой; MT-сборка — основная для игры.
2. **ProbCut** добавлен; сила не доказана. Следующий шаг — **enhanced transposition cutoff**.
3. Add **singular extensions** with TT exclusion; only keep if SPRT/paired matches show a gain.
4. Add **correction history** once a real NNUE evaluation exists.
5. Dynamic time management; PGO/BMI2 уже сделаны.
6. Syzygy or Lichess online EGTB for bot games.

No algorithm should be enabled by default without a measured positive result. Small samples (<30 games) are noise.
