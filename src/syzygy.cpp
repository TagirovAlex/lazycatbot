// Syzygy tablebase probing through Fathom (MIT).
//
// The engine never bundles tablebase files.  A path is set with the UCI
// option `SyzygyPath`; when it is empty or contains no .rtbw files, probing
// stays disabled and the engine behaves exactly as before.

#include "engine.hpp"

#include "tbprobe.h"

#include <algorithm>
#include <atomic>
#include <bit>
#include <cstdint>
#include <shared_mutex>

namespace chess {
namespace Syzygy {

namespace {

std::atomic<bool> gInitialized{false};
std::atomic<bool> gEnabled{false};
std::atomic<int> gMaxPieces{0};
std::atomic<int> gProbeDepth{1};
std::atomic<bool> gRule50{true};
std::atomic<int> gProbeLimit{7};

// Fathom's WDL probe is thread-safe, but tb_probe_root is explicitly not.
// A shared mutex lets several search threads probe WDL concurrently while a
// root probe (main thread only) gets exclusive access.
std::shared_mutex gProbeMutex;

Wdl fromFathom(unsigned wdl) {
    switch (wdl) {
        case TB_LOSS: return Wdl::Loss;
        case TB_BLESSED_LOSS: return Wdl::BlessedLoss;
        case TB_DRAW: return Wdl::Draw;
        case TB_CURSED_WIN: return Wdl::CursedWin;
        case TB_WIN: return Wdl::Win;
        default: return Wdl::Fail;
    }
}

unsigned epSquare(const Position& pos) {
    return pos.ep >= 0 ? unsigned(pos.ep) : 0u;
}

} // namespace

bool init(const std::string& path) {
    if (gInitialized.exchange(false)) tb_free();
    gEnabled.store(false, std::memory_order_relaxed);
    gMaxPieces.store(0, std::memory_order_relaxed);

    if (path.empty()) return true;

    const bool ok = tb_init(path.c_str());
    gInitialized.store(true, std::memory_order_relaxed);
    gMaxPieces.store(int(TB_LARGEST), std::memory_order_relaxed);
    gEnabled.store(ok && TB_LARGEST > 0, std::memory_order_relaxed);
    return ok;
}

void shutdown() {
    if (gInitialized.exchange(false)) tb_free();
    gEnabled.store(false, std::memory_order_relaxed);
    gMaxPieces.store(0, std::memory_order_relaxed);
}

bool enabled() {
    return gEnabled.load(std::memory_order_relaxed) && maxPieces() > 0;
}

int tablebaseMaxPieces() {
    return gMaxPieces.load(std::memory_order_relaxed);
}

int maxPieces() {
    return std::min(gMaxPieces.load(std::memory_order_relaxed),
                    gProbeLimit.load(std::memory_order_relaxed));
}

void setProbeDepth(int depth) {
    gProbeDepth.store(std::clamp(depth, 1, 100), std::memory_order_relaxed);
}

int probeDepth() {
    return gProbeDepth.load(std::memory_order_relaxed);
}

void setRule50(bool useRule50) {
    gRule50.store(useRule50, std::memory_order_relaxed);
}

bool rule50() {
    return gRule50.load(std::memory_order_relaxed);
}

void setProbeLimit(int limit) {
    gProbeLimit.store(std::clamp(limit, 0, 7), std::memory_order_relaxed);
}

int probeLimit() {
    return gProbeLimit.load(std::memory_order_relaxed);
}

RootResult probeRoot(const Position& pos) {
    RootResult result;
    if (!enabled()) return result;
    if (std::popcount(pos.occ) > maxPieces()) return result;
    // Fathom rejects positions with castling rights.  Such positions cannot
    // occur in a tablebase anyway (they have too many pieces in practice),
    // but keep the guard explicit.
    if (pos.castling != 0) return result;

    const unsigned rule50Arg = rule50() ? unsigned(pos.halfMove) : 0u;
    std::unique_lock<std::shared_mutex> lock(gProbeMutex);
    const unsigned res = tb_probe_root(
        pos.byColor[WHITE], pos.byColor[BLACK],
        pos.byType[KING], pos.byType[QUEEN], pos.byType[ROOK],
        pos.byType[BISHOP], pos.byType[KNIGHT], pos.byType[PAWN],
        rule50Arg, unsigned(pos.castling), epSquare(pos),
        pos.side == WHITE, nullptr);

    if (res == TB_RESULT_FAILED) return result;

    result.ok = true;
    if (res == TB_RESULT_CHECKMATE) {
        result.checkmate = true;
        return result;
    }
    if (res == TB_RESULT_STALEMATE) {
        result.stalemate = true;
        return result;
    }

    result.from = int(TB_GET_FROM(res));
    result.to = int(TB_GET_TO(res));
    result.promo = int(TB_GET_PROMOTES(res));
    result.dtz = int(TB_GET_DTZ(res));
    result.wdl = fromFathom(TB_GET_WDL(res));
    return result;
}

Wdl probeWDL(const Position& pos) {
    if (!enabled()) return Wdl::Fail;
    if (std::popcount(pos.occ) > maxPieces()) return Wdl::Fail;
    if (pos.castling != 0) return Wdl::Fail;
    // tb_probe_wdl is only valid with the 50-move counter at zero.  When the
    // rule is disabled we intentionally probe with zero as well; the result
    // is then a pure WDL outcome without cursed/blessed distinctions.
    if (rule50() && pos.halfMove != 0) return Wdl::Fail;

    std::shared_lock<std::shared_mutex> lock(gProbeMutex);
    const unsigned res = tb_probe_wdl(
        pos.byColor[WHITE], pos.byColor[BLACK],
        pos.byType[KING], pos.byType[QUEEN], pos.byType[ROOK],
        pos.byType[BISHOP], pos.byType[KNIGHT], pos.byType[PAWN],
        0u, 0u, epSquare(pos), pos.side == WHITE);

    if (res == TB_RESULT_FAILED) return Wdl::Fail;
    return fromFathom(res);
}

} // namespace Syzygy
} // namespace chess
