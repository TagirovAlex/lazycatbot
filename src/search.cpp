#include "engine.hpp"

#include <cmath>

namespace chess {

// ---------------------------------------------------------------------------
// Transposition table
// ---------------------------------------------------------------------------
void TranspositionTable::resize(size_t megabytes) {
    size_t bytes = std::max<size_t>(1, megabytes) * 1024 * 1024;
    size_t count = bytes / sizeof(Slot);
    if (count < 1024) count = 1024;
    size_t pow2 = 1;
    while (pow2 * 2 <= count) pow2 *= 2;

    table_ = std::make_unique<Slot[]>(pow2);
    count_ = pow2;
    mask_ = pow2 - 1;
    generation_.store(1, std::memory_order_relaxed);
}

void TranspositionTable::clear() {
    // Only called while no search is running.
    for (size_t i = 0; i < count_; ++i) {
        table_[i].data.store(0, std::memory_order_relaxed);
        table_[i].tag.store(0, std::memory_order_relaxed);
    }
    generation_.store(1, std::memory_order_relaxed);
}

namespace {

inline int scoreToTT(int score, int ply) {
    if (score >= MATE_IN_MAX) return score + ply;
    if (score <= -MATE_IN_MAX) return score - ply;
    return score;
}

inline int scoreFromTT(int score, int ply) {
    if (score >= MATE_IN_MAX) return score - ply;
    if (score <= -MATE_IN_MAX) return score + ply;
    return score;
}

// Tablebase scores live below the mate range but far above normal evals.
constexpr int TB_WIN_SCORE = 20000;

int tbScore(Syzygy::Wdl wdl, int ply, bool rule50) {
    switch (wdl) {
        case Syzygy::Wdl::Win:
            return TB_WIN_SCORE - ply;
        case Syzygy::Wdl::Loss:
            return -TB_WIN_SCORE + ply;
        case Syzygy::Wdl::CursedWin:
            return rule50 ? 0 : TB_WIN_SCORE - ply;
        case Syzygy::Wdl::BlessedLoss:
            return rule50 ? 0 : -TB_WIN_SCORE + ply;
        case Syzygy::Wdl::Draw:
            return 0;
        default:
            return 0;
    }
}

int lmrTable[64][64];
std::once_flag lmrOnce;

void initLmr() {
    std::call_once(lmrOnce, [] {
        for (int d = 1; d < 64; ++d)
            for (int m = 1; m < 64; ++m) {
                double r =
                    0.75 + std::log(double(d)) * std::log(double(m)) / 2.25;
                lmrTable[d][m] = int(r);
            }
    });
}

} // namespace

int64_t Search::elapsedMs() const {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now() - startTime_)
        .count();
}

uint64_t Search::reportedNodes() const {
    SharedSearchState* s = shared_.load(std::memory_order_relaxed);
    if (!s) return nodeCount_;
    return s->nodes.load(std::memory_order_relaxed) + (nodeCount_ & 1023ULL);
}

// Called once per node.  Every 1024 nodes the local count is published to the
// shared counter (so `go nodes` and NPS see all threads) and the stop/time
// conditions are checked.
bool Search::nodeCheck() {
    ++nodeCount_;
    if ((nodeCount_ & 1023ULL) != 0) return false;
    if (SharedSearchState* s = shared_.load(std::memory_order_relaxed))
        s->nodes.fetch_add(1024, std::memory_order_relaxed);
    return timeUp();
}

// Publish the final partial block once, when this search finishes.
void Search::flushNodes() {
    if (SharedSearchState* s = shared_.load(std::memory_order_relaxed)) {
        const uint64_t rem = nodeCount_ & 1023ULL;
        if (rem) s->nodes.fetch_add(rem, std::memory_order_relaxed);
    }
}

bool Search::timeUp() {
    if (stopped()) return true;

    if (SharedSearchState* s = shared_.load(std::memory_order_relaxed)) {
        const uint64_t limit = s->nodeLimit.load(std::memory_order_relaxed);
        if (limit && s->nodes.load(std::memory_order_relaxed) >= limit) {
            s->stop.store(true, std::memory_order_relaxed);
            return true;
        }
    } else if (nodeLimit_ && nodeCount_ >= nodeLimit_) {
        stopFlag.store(true, std::memory_order_relaxed);
        return true;
    }

    // Only the main worker enforces its own hard clock.  A helper may reach
    // the deadline slightly earlier (thread startup/scheduling skew); if it
    // called stop() the shared flag would cut the main search short.  Helpers
    // keep searching until the main worker or controller stops the group via
    // stopped()/the shared node limit.  A standalone Search is a main thread,
    // so its timing behavior is unchanged.
    if (mainThread_ && timeLimited_ && elapsedMs() >= hardLimitMs_) {
        stop();
        return true;
    }
    return false;
}

void Search::updateHistory(int& entry, int bonus) {
    const int absBonus = bonus < 0 ? -bonus : bonus;
    entry += bonus - entry * absBonus / 16384;
}

void Search::clearHistory() {
    std::memset(history_, 0, sizeof(history_));
    std::memset(captureHistory_, 0, sizeof(captureHistory_));
    std::memset(continuationHistory_, 0, sizeof(continuationHistory_));
    for (int p = 0; p < MAX_PLY; ++p) {
        killers_[p][0] = Move();
        killers_[p][1] = Move();
    }
    for (int c = 0; c < COLOR_NB; ++c)
        for (int f = 0; f < 64; ++f)
            for (int t = 0; t < 64; ++t)
                counterMoves_[c][f][t] = Move();
    for (auto& m : moveStack_) m = Move();
}

void Search::clearState() {
    clearHistory();
    std::memset(pvLen_, 0, sizeof(pvLen_));
    std::memset(evalStack_, 0, sizeof(evalStack_));
    rootMoveCount_ = 0;
    rootBest_ = Move();
    rootFallback_ = Move();
    lastBest_ = Move();
    lastScore_ = 0;
    selDepth_ = 0;
}

void Search::updatePv(int ply, Move m) {
    pvTable_[ply][ply] = m;
    int childLen = pvLen_[ply + 1];
    for (int i = ply + 1; i < childLen; ++i)
        pvTable_[ply][i] = pvTable_[ply + 1][i];
    pvLen_[ply] = std::max(ply + 1, childLen);
}

int Search::moveOrderScore(Move m, Move ttMove, int ply,
                           bool useCounter) const {
    if (!ttMove.isNone() && m == ttMove) return 1000000;

    if (m.isCapture() || m.type() == Move::PROMOTION) {
        Piece victim = NO_PIECE;
        if (m.type() == Move::EN_PASSANT)
            victim = makePiece(~pos_.side, PAWN);
        else
            victim = pos_.board[m.to()];
        int victimValue = (victim == NO_PIECE) ? 0 : PieceValue[pieceType(victim)];
        PieceType attackerType = pieceType(pos_.board[m.from()]);
        int attackerValue = PieceValue[attackerType];
        int s = 100000 + 16 * victimValue - attackerValue;
        if (m.type() == Move::PROMOTION)
            s += PieceValue[PieceType(m.promo())];
        if (victim != NO_PIECE) {
            int capHist = captureHistory_[pos_.side][attackerType][m.to()]
                                          [pieceType(victim)];
            s += std::min(capHist, 8000);
        }
        return s;
    }
    if (m == killers_[ply][0]) return 90000;
    if (m == killers_[ply][1]) return 89000;
    if (useCounter && ply > 0) {
        Move prev = moveStack_[ply];
        if (!prev.isNone() &&
            counterMoves_[pos_.side][prev.from()][prev.to()] == m)
            return 80000;
    }
    int score = history_[pos_.side][m.from()][m.to()];
    if (useCounter && ply > 0) {
        Move prev = moveStack_[ply];
        Piece prevPiece = prev.isNone() ? NO_PIECE : pos_.board[prev.to()];
        Piece curPiece = pos_.board[m.from()];
        if (isPiece(prevPiece) && isPiece(curPiece)) {
            score += continuationHistory_[int(curPiece)][m.to()]
                                          [int(prevPiece)][prev.to()];
        }
    }
    return score;
}

void Search::scoreMoves(Move* moves, int count, int* scores, Move ttMove,
                        int ply, bool useCounter) const {
    for (int i = 0; i < count; ++i)
        scores[i] = moveOrderScore(moves[i], ttMove, ply, useCounter);
}

namespace {
struct ScoredMove {
    Move move;
    int score;
};
}

int Search::quiescence(int alpha, int beta, int ply) {
    if (ply >= MAX_PLY - 1) return evaluate(pos_);
    pvLen_[ply] = ply;
    if (nodeCheck()) return 0;
    if (ply > selDepth_) selDepth_ = ply;

    if (ply > 0 && pos_.isDraw()) return 0;

    const bool inCheck = pos_.inCheck();
    int bestScore;
    int standPat = 0;

    if (!inCheck) {
        standPat = evaluate(pos_);
        if (standPat >= beta) return standPat;
        if (standPat > alpha) alpha = standPat;
        bestScore = standPat;
    } else {
        bestScore = -INF;
    }

    MoveList list;
    if (inCheck)
        pos_.generateMoves(list);
    else
        pos_.generateCaptures(list);

    int scores[MAX_MOVES];
    Move ttNone;
    scoreMoves(list.moves, list.count, scores, ttNone, ply);

    ScoredMove sm[MAX_MOVES];
    for (int i = 0; i < list.count; ++i) sm[i] = {list.moves[i], scores[i]};
    std::sort(sm, sm + list.count,
              [](const ScoredMove& a, const ScoredMove& b) {
                  return a.score > b.score;
              });

    int legal = 0;
    for (int i = 0; i < list.count; ++i) {
        Move m = sm[i].move;

        if (!inCheck) {
            int victim = 0;
            if (m.type() == Move::EN_PASSANT)
                victim = PieceValue[PAWN];
            else if (pos_.board[m.to()] != NO_PIECE)
                victim = PieceValue[pieceType(pos_.board[m.to()])];
            if (m.type() == Move::PROMOTION)
                victim += PieceValue[PieceType(m.promo())] - PieceValue[PAWN];

            if (legal > 0 && m.isCapture() && m.type() != Move::EN_PASSANT) {
                if (pos_.see(m) < 0) continue;
            }
            if (m.type() != Move::PROMOTION && standPat + victim + 180 < alpha)
                continue;
        }

        if (!pos_.makeMove(m)) continue;
        ++legal;
        int score = -quiescence(-beta, -alpha, ply + 1);
        pos_.unmakeMove(m);
        if (stopped()) return 0;

        if (score > bestScore) bestScore = score;
        if (score >= beta) return score;
        if (score > alpha) alpha = score;
    }

    if (inCheck && legal == 0) return -MATE + ply;
    return bestScore;
}

int Search::negamax(int depth, int alpha, int beta, int ply, bool cutNode,
                    bool nullAllowed) {
    if (ply >= MAX_PLY - 1) return evaluate(pos_);
    pvLen_[ply] = ply;

    if (nodeCheck()) return 0;

    const bool rootNode = (ply == 0);
    const bool inCheck = pos_.inCheck();

    if (ply > 0) {
        if (pos_.isDraw()) return 0;

        alpha = std::max(alpha, -MATE + ply);
        beta = std::min(beta, MATE - ply - 1);
        if (alpha >= beta) return alpha;
    }

    if (depth <= 0) return quiescence(alpha, beta, ply);
    if (inCheck) ++depth;

    const bool pvNode = (beta - alpha > 1);

    // Syzygy WDL probe.  Fathom only accepts a zero 50-move counter for WDL;
    // probeWDL enforces that and returns Fail when probing is not applicable.
    // The cheap guard mirrors probeWDL's preconditions so the hot path does
    // not take the shared probe lock for positions no tablebase can contain.
    if (!rootNode && tbEnabled_ && depth >= tbProbeDepth_ &&
        std::popcount(pos_.occ) <= tbMaxPieces_ && pos_.castling == 0 &&
        (!tbRule50_ || pos_.halfMove == 0)) {
        const Syzygy::Wdl wdl = Syzygy::probeWDL(pos_);
        if (wdl != Syzygy::Wdl::Fail) return tbScore(wdl, ply, tbRule50_);
    }

    // The TT key includes the halfmove clock (see transpositionKey()). In
    // addition, close to the 50-move boundary the value may depend on history
    // (repetition claims) that the key does not encode, so keep the TT move
    // for ordering but disable cutoffs/stores there.
    const U64 ttKey = pos_.transpositionKey();
    const bool ttContextual = pos_.halfMove >= 90;
    TTEntry tte;
    bool ttHit = tt_.probe(ttKey, tte);
    Move ttMove = ttHit ? Move(tte.move) : Move();
    int ttScore = ttHit ? scoreFromTT(tte.score, ply) : 0;

    if (ply > 0 && ttHit && tte.depth >= depth && !pvNode && !ttContextual) {
        if (tte.bound == BOUND_EXACT) return ttScore;
        if (tte.bound == BOUND_LOWER && ttScore >= beta) return ttScore;
        if (tte.bound == BOUND_UPPER && ttScore <= alpha) return ttScore;
    }

    int staticEval = 0;
    if (!inCheck) {
        if (ttHit) {
            staticEval = tte.eval;
        } else {
            staticEval = evaluate(pos_);
        }
    }
    evalStack_[ply] = staticEval;
    bool improving = !inCheck && ply >= 2 && staticEval > evalStack_[ply - 2];

    // Reverse futility pruning
    if (!rootNode && !inCheck && !pvNode && depth <= 6 &&
        staticEval - 85 * depth >= beta && std::abs(beta) < MATE_IN_MAX &&
        pos_.hasLegalMove())
        return staticEval;

    // Razoring
    if (!rootNode && !inCheck && !pvNode && depth <= 2 &&
        staticEval + 300 + 180 * depth <= alpha && pos_.hasLegalMove())
        return quiescence(alpha, beta, ply);

    // ProbCut: verify a likely capture cutoff at reduced depth with a raised
    // beta. Only good captures are tried; a fail-high is accepted as a lower
    // bound instead of searching the whole node at full depth.
    if (!rootNode && !inCheck && !pvNode && depth >= 5 &&
        std::abs(beta) < MATE_IN_MAX && staticEval >= beta - 150) {
        const int probBeta = beta + 180;
        MoveList caps;
        pos_.generateCaptures(caps);
        int pcScores[MAX_MOVES];
        scoreMoves(caps.moves, caps.count, pcScores, ttMove, ply);
        ScoredMove pc[MAX_MOVES];
        for (int i = 0; i < caps.count; ++i)
            pc[i] = {caps.moves[i], pcScores[i]};
        std::sort(pc, pc + caps.count,
                  [](const ScoredMove& a, const ScoredMove& b) {
                      return a.score > b.score;
                  });
        for (int i = 0; i < caps.count; ++i) {
            const Move m = pc[i].move;
            if (m == ttMove || pos_.see(m) < 100) continue;
            if (!pos_.makeMove(m)) continue;
            moveStack_[ply + 1] = m;
            const int score = -negamax(depth - 4, -probBeta, -probBeta + 1,
                                       ply + 1, true, true);
            pos_.unmakeMove(m);
            if (stopped()) return 0;
            if (score >= probBeta) {
                if (!ttContextual)
                    tt_.store(ttKey, depth, scoreToTT(score, ply), staticEval,
                              BOUND_LOWER, m);
                return score;
            }
        }
    }

    // Null-move pruning
    if (nullAllowed && !rootNode && !inCheck && !pvNode && depth >= 3 &&
        staticEval >= beta && std::abs(beta) < MATE_IN_MAX &&
        pos_.histPly < MAX_HIST - 1 &&
        pos_.hasNonPawnMaterial(pos_.side) && pos_.makeNullMove()) {
        int R = 2 + depth / 4 + std::min(3, (staticEval - beta) / 200);
        // A null move has no predecessor move for countermove ordering.
        moveStack_[ply + 1] = Move();
        int nullScore =
            -negamax(depth - 1 - R, -beta, -beta + 1, ply + 1, !cutNode, false);
        pos_.unmakeNullMove();
        if (stopped()) return 0;
        if (nullScore >= beta && pos_.hasLegalMove())
            return nullScore >= MATE_IN_MAX ? beta : nullScore;
    }

    // Internal iterative reduction
    if (depth >= 5 && ttMove.isNone()) --depth;

    MoveList list;
    pos_.generateMoves(list);

    int scores[MAX_MOVES];
    scoreMoves(list.moves, list.count, scores, ttMove, ply);

    ScoredMove sm[MAX_MOVES];
    for (int i = 0; i < list.count; ++i) sm[i] = {list.moves[i], scores[i]};
    std::sort(sm, sm + list.count,
              [](const ScoredMove& a, const ScoredMove& b) {
                  return a.score > b.score;
              });

    const int alphaOrig = alpha;
    int bestScore = -INF;
    Move bestMove;
    int legal = 0;

    for (int i = 0; i < list.count; ++i) {
        Move m = sm[i].move;
        const bool isQuiet = !m.isCapture() && m.type() != Move::PROMOTION;
        const int from = m.from();
        const int to = m.to();
        const PieceType movedType = pieceType(pos_.board[from]);
        const PieceType capturedType =
            (m.type() == Move::EN_PASSANT)
                ? PAWN
                : (pos_.board[to] == NO_PIECE ? PIECE_TYPE_NB
                                              : pieceType(pos_.board[to]));

        // Conservative pre-move pruning. Never prune before at least one
        // move has been searched, otherwise a node could return -INF.
        if (!rootNode && !inCheck && legal > 0 &&
            std::abs(alpha) < MATE_IN_MAX) {
            if (isQuiet && depth <= 4 &&
                staticEval + 90 + 90 * depth <= alpha)
                continue;
            if (isQuiet && depth <= 5 && legal >= 5 + depth * depth)
                continue;
            if (!isQuiet && depth <= 6 && pos_.see(m) < -110 * depth)
                continue;
        }

        if (!pos_.makeMove(m)) continue;
        ++legal;
        moveStack_[ply + 1] = m;

        const int newDepth = depth - 1;
        int reduction = 0;
        if (!rootNode && depth >= 3 && legal > 3 && isQuiet && !inCheck &&
            !pvNode) {
            reduction = lmrTable[std::min(depth, 63)][std::min(legal, 63)];
            if (cutNode) ++reduction;
            if (!improving) ++reduction;
            if (m == killers_[ply][0] || m == killers_[ply][1])
                reduction = std::max(0, reduction - 1);
            reduction = std::max(0, std::min(reduction, newDepth - 1));
        }

        int score;
        if (legal == 1) {
            score = -negamax(newDepth, -beta, -alpha, ply + 1, false, true);
        } else {
            score = -negamax(newDepth - reduction, -alpha - 1, -alpha,
                             ply + 1, true, true);
            if (!stopped() && score > alpha &&
                reduction > 0)
                score = -negamax(newDepth, -alpha - 1, -alpha, ply + 1,
                                  !cutNode, true);
            if (!stopped() && score > alpha &&
                score < beta)
                score = -negamax(newDepth, -beta, -alpha, ply + 1, false, true);
        }

        pos_.unmakeMove(m);
        if (stopped()) return 0;

        if (score > bestScore) {
            bestScore = score;
            bestMove = m;
        }
        if (score > alpha) {
            alpha = score;
            updatePv(ply, m);
            if (alpha >= beta) {
                if (isQuiet) {
                    if (killers_[ply][0] != m) {
                        killers_[ply][1] = killers_[ply][0];
                        killers_[ply][0] = m;
                    }
                    const int bonus = std::min(depth * depth, 1024) + 1;
                    updateHistory(history_[pos_.side][from][to], bonus);
                    if (ply > 0) {
                        Move prev = moveStack_[ply];
                        if (!prev.isNone()) {
                            counterMoves_[pos_.side][prev.from()][prev.to()] = m;
                            Piece prevPiece = pos_.board[prev.to()];
                            Piece curPiece = pos_.board[from];
                            if (isPiece(prevPiece) && isPiece(curPiece)) {
                                updateHistory(
                                    continuationHistory_[int(curPiece)][to]
                                                        [int(prevPiece)]
                                                        [prev.to()],
                                    bonus);
                            }
                        }
                    }
                } else if (capturedType != PIECE_TYPE_NB) {
                    int& ch = captureHistory_[pos_.side][movedType][to]
                                              [capturedType];
                    ch = std::min(ch + depth * depth, 1 << 19);
                }
                break;
            }
        }
    }

    if (legal == 0) {
        int value = inCheck ? -MATE + ply : 0;
        if (!stopped() && !ttContextual)
            tt_.store(ttKey, depth, scoreToTT(value, ply), staticEval,
                      BOUND_EXACT, Move());
        return value;
    }

    if (!stopped() && !ttContextual) {
        Bound bound = bestScore >= beta
                          ? BOUND_LOWER
                          : (bestScore > alphaOrig ? BOUND_EXACT : BOUND_UPPER);
        tt_.store(ttKey, depth, scoreToTT(bestScore, ply), staticEval,
                  bound, bestMove);
    }

    return bestScore;
}

int Search::searchRoot(int depth, int alpha, int beta) {
    pvLen_[0] = 0;
    const bool rootInCheck = pos_.inCheck();
    if (rootInCheck) ++depth;
    evalStack_[0] = rootInCheck ? 0 : evaluate(pos_);
    const int rootEval = evalStack_[0];

    const U64 ttKey = pos_.transpositionKey();
    TTEntry tte;
    Move ttMove;
    if (tt_.probe(ttKey, tte)) {
        ttMove = Move(tte.move);
        if (!ttMove.isNone()) {
            for (int i = 0; i < rootMoveCount_; ++i) {
                if (rootMoves_[i].move == ttMove) {
                    std::swap(rootMoves_[0], rootMoves_[i]);
                    break;
                }
            }
        }
    }

    int bestScore = -INF;
    Move bestMove;
    const int alphaOrig = alpha;
    int legal = 0;

    for (int i = 0; i < rootMoveCount_; ++i) {
        Move m = rootMoves_[i].move;
        if (!pos_.makeMove(m)) continue;
        moveStack_[1] = m;

        int score;
        if (legal == 0) {
            score = -negamax(depth - 1, -beta, -alpha, 1, false, true);
        } else {
            score = -negamax(depth - 1, -alpha - 1, -alpha, 1, true, true);
            if (!stopped() && score > alpha)
                score = -negamax(depth - 1, -beta, -alpha, 1, false, true);
        }

        pos_.unmakeMove(m);
        if (stopped()) return bestScore;

        ++legal;
        rootMoves_[i].score = score;
        if (score > bestScore) {
            bestScore = score;
            bestMove = m;
        }
        if (score > alpha) {
            alpha = score;
            updatePv(0, m);
        }
        if (alpha >= beta) break;
    }

    if (legal == 0) return -MATE;
    rootBest_ = bestMove;
    if (pvLen_[0] == 0 && !bestMove.isNone()) {
        pvTable_[0][0] = bestMove;
        pvLen_[0] = 1;
    }

    if (!stopped()) {
        Bound bound = bestScore >= beta
                          ? BOUND_LOWER
                          : (bestScore > alphaOrig ? BOUND_EXACT : BOUND_UPPER);
        tt_.store(ttKey, depth, scoreToTT(bestScore, 0), rootEval, bound,
                  bestMove);
    }
    return bestScore;
}

void Search::printInfo(int depth, int score, int64_t elapsed) const {
    std::ostringstream os;
    os << "info depth " << depth << " seldepth " << selDepth_;

    if (score >= MATE_IN_MAX) {
        int plies = MATE - score;
        os << " score mate " << (plies + 1) / 2;
    } else if (score <= -MATE_IN_MAX) {
        int plies = MATE + score;
        os << " score mate -" << (plies + 1) / 2;
    } else {
        os << " score cp " << score;
    }

    const uint64_t shownNodes = reportedNodes();
    uint64_t nps =
        elapsed > 0 ? shownNodes * 1000 / uint64_t(elapsed) : shownNodes * 1000;
    os << " nodes " << shownNodes << " nps " << nps << " hashfull "
       << tt_.hashfull() << " time " << elapsed << " pv ";
    int len = pvLen_[0];
    if (len <= 0) {
        os << "0000";
    } else {
        for (int i = 0; i < len; ++i) {
            if (i) os << ' ';
            os << moveToUci(pvTable_[0][i]);
        }
    }
    Output::line(os.str());
}

void Search::run(const SearchLimits& limits) {
    initLmr();

    // Cache tablebase settings for this search so the hot path does not read
    // atomics at every node.  UCI changes these only while no search runs.
    tbEnabled_ = Syzygy::enabled();
    tbProbeDepth_ = Syzygy::probeDepth();
    tbMaxPieces_ = Syzygy::maxPieces();
    tbRule50_ = Syzygy::rule50();

    nodeCount_ = 0;
    if (SharedSearchState* s = shared_.load(std::memory_order_relaxed)) {
        nodeLimit_ = 0;  // enforced through the shared counter
        s->nodeLimit.store(limits.nodes, std::memory_order_relaxed);
    } else {
        nodeLimit_ = limits.nodes;
    }
    selDepth_ = 0;
    searchDepth_ = 0;
    rootBest_ = Move();
    rootFallback_ = Move();
    lastBest_ = Move();
    lastScore_ = 0;
    std::memset(pvLen_, 0, sizeof(pvLen_));
    std::memset(killers_, 0, sizeof(killers_));

    startTime_ = std::chrono::steady_clock::now();
    timeLimited_ = false;
    softLimitMs_ = 0;
    hardLimitMs_ = 0;

    const int64_t overhead = std::max(0, limits.moveOverhead);
    if (limits.movetime >= 0) {
        // For a fixed local movetime the full clock overhead is too
        // conservative (30 ms out of 100 ms). Keep only a small margin.
        int64_t reserve = std::min<int64_t>(
            overhead, std::max<int64_t>(1, limits.movetime / 10));
        int64_t budget = std::max<int64_t>(1, limits.movetime - reserve);
        softLimitMs_ = hardLimitMs_ = budget;
        timeLimited_ = true;
    } else if (!limits.infinite) {
        int64_t myTime = pos_.side == WHITE ? limits.wtime : limits.btime;
        int64_t myInc = pos_.side == WHITE ? limits.winc : limits.binc;
        bool anyClock = (limits.wtime >= 0 || limits.btime >= 0);
        if (anyClock) {
            if (myTime < 0) {
                myTime = 1000;
                myInc = 0;
            }
            myTime = std::min<int64_t>(myTime, 24LL * 3600 * 1000);
            myInc = std::clamp<int64_t>(myInc, 0, 1000000);
            int mtg = limits.movestogo > 0 ? limits.movestogo : 30;
            int64_t base = myTime / std::max(1, mtg) + myInc / 4 * 3;
            softLimitMs_ = std::clamp<int64_t>(
                base, 3, std::max<int64_t>(3, myTime / 2));
            hardLimitMs_ = std::min<int64_t>(
                std::max<int64_t>(3, myTime / 4 * 3),
                softLimitMs_ * 4 + 20);
            softLimitMs_ = std::max<int64_t>(1, softLimitMs_ - overhead);
            hardLimitMs_ = std::max<int64_t>(
                softLimitMs_, hardLimitMs_ - overhead);
            timeLimited_ = true;
        }
    }

    // Root move list (legal moves only).
    rootMoveCount_ = 0;
    MoveList all;
    pos_.generateMoves(all);
    for (int i = 0; i < all.count; ++i) {
        Move m = all.moves[i];
        if (pos_.makeMove(m)) {
            pos_.unmakeMove(m);
            if (rootMoveCount_ < MAX_MOVES) {
                rootMoves_[rootMoveCount_].move = m;
                rootMoves_[rootMoveCount_].score = -INF;
                rootMoves_[rootMoveCount_].prevScore = -INF;
                ++rootMoveCount_;
            }
        }
    }

    if (rootMoveCount_ == 0) {
        lastBest_ = Move();
        if (!silent_) {
            if (pos_.inCheck())
                Output::line("info depth 0 score mate 0");
            else
                Output::line("info depth 0 score cp 0");
            Output::line("bestmove 0000");
        }
        flushNodes();
        return;
    }

    // Root Syzygy probe: play a tablebase move immediately.  Only the main
    // worker probes; Fathom's root probe is not thread safe.  Helpers keep
    // their normal search and are stopped by the controller when this returns.
    if (mainThread_ && tbEnabled_) {
        const Syzygy::RootResult tb = Syzygy::probeRoot(pos_);
        if (tb.ok && !tb.checkmate && !tb.stalemate && tb.from >= 0) {
            int promo = 0;
            switch (tb.promo) {
                case 1: promo = QUEEN; break;
                case 2: promo = ROOK; break;
                case 3: promo = BISHOP; break;
                case 4: promo = KNIGHT; break;
                default: promo = 0; break;
            }
            Move tbMove;
            for (int i = 0; i < rootMoveCount_; ++i) {
                const Move m = rootMoves_[i].move;
                if (m.from() == tb.from && m.to() == tb.to &&
                    (promo == 0 || m.promo() == promo)) {
                    tbMove = m;
                    break;
                }
            }
            if (!tbMove.isNone()) {
                int score = 0;
                switch (tb.wdl) {
                    case Syzygy::Wdl::Win:
                        score = TB_WIN_SCORE;
                        break;
                    case Syzygy::Wdl::Loss:
                        score = -TB_WIN_SCORE;
                        break;
                    case Syzygy::Wdl::CursedWin:
                        score = tbRule50_ ? 0 : TB_WIN_SCORE;
                        break;
                    case Syzygy::Wdl::BlessedLoss:
                        score = tbRule50_ ? 0 : -TB_WIN_SCORE;
                        break;
                    default:
                        score = 0;
                        break;
                }
                lastBest_ = tbMove;
                rootBest_ = tbMove;
                lastScore_ = score;
                searchDepth_ = 0;
                if (!silent_) {
                    std::ostringstream os;
                    os << "info depth 0 seldepth 0 score cp " << score
                       << " nodes 0 nps 0 hashfull " << tt_.hashfull()
                       << " time 0 pv " << moveToUci(tbMove);
                    Output::line(os.str());
                    Output::line("bestmove " + moveToUci(tbMove));
                }
                flushNodes();
                return;
            }
        }
    }

    rootFallback_ = rootMoves_[0].move;
    Move completedBest = rootFallback_;
    int prevScore = 0;

    // A group controller bumps the generation once before launching the
    // workers; a standalone Search (trainer, bench) bumps it here.
    if (!shared_.load(std::memory_order_relaxed)) tt_.newSearch();

    // Helpers get a small deterministic depth offset and rotate the initial
    // root order so they do not all explore exactly the same tree.
    int depthBoost = mainThread_ ? 0 : (threadId_ % 3);
    if (!mainThread_ && rootMoveCount_ > 1) {
        int shift = threadId_ % rootMoveCount_;
        std::rotate(rootMoves_, rootMoves_ + shift,
                    rootMoves_ + rootMoveCount_);
    }

    int maxDepth = std::min(limits.depth + depthBoost, MAX_PLY - 2);
    if (maxDepth < 1) maxDepth = 1;

    for (int depth = 1; depth <= maxDepth; ++depth) {
        int alpha = -INF, beta = INF;
        int delta = 30;
        int score = 0;

        if (depth >= 4) {
            alpha = std::max(-INF, prevScore - 30);
            beta = std::min(INF, prevScore + 30);
        }

        for (;;) {
            score = searchRoot(depth, alpha, beta);
            if (stopped()) break;

            std::stable_sort(rootMoves_, rootMoves_ + rootMoveCount_,
                             [](const RootMove& a, const RootMove& b) {
                                 return a.score > b.score;
                             });

            if (score <= alpha && alpha > -INF) {
                alpha = std::max(-INF, alpha - delta);
                delta *= 2;
            } else if (score >= beta && beta < INF) {
                beta = std::min(INF, beta + delta);
                delta *= 2;
            } else {
                break;
            }
            if (delta > 4000) {
                alpha = -INF;
                beta = INF;
            }
        }

        if (stopped()) break;

        completedBest = rootBest_;
        lastScore_ = score;
        prevScore = score;
        searchDepth_ = depth;

        if (!silent_) {
            int64_t elapsed = elapsedMs();
            printInfo(depth, score, elapsed);
        }

        std::stable_sort(rootMoves_, rootMoves_ + rootMoveCount_,
                         [](const RootMove& a, const RootMove& b) {
                             return a.score > b.score;
                         });

        if (score >= MATE_IN_MAX || score <= -MATE_IN_MAX) break;

        // Only the main search uses the soft time limit.  Helpers keep
        // searching (sharing their TT results) until the main search finishes
        // and the controller raises the shared stop flag.
        int64_t elapsed = elapsedMs();
        if (mainThread_ && timeLimited_ && elapsed >= softLimitMs_) break;
        if (mainThread_ && timeLimited_ && depth >= 8 &&
            elapsed * 2 > hardLimitMs_)
            break;
    }

    if (completedBest.isNone()) completedBest = rootFallback_;
    lastBest_ = completedBest;
    flushNodes();

    if (!silent_) {
        Output::line("bestmove " + moveToUci(completedBest));
    }
}

} // namespace chess
