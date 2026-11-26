#include "engine.hpp"

#include <random>

namespace chess {

namespace Bitboards {
U64 pawnAttacks[2][64];
U64 knightAttacks[64];
U64 kingAttacks[64];
U64 between[64][64];
U64 line[64][64];
} // namespace Bitboards

Magic rookMagics[64];
Magic bishopMagics[64];

namespace {
U64 rookTable[64][4096];
U64 bishopTable[64][1024];

U64 rookMask(int sq) {
    U64 mask = 0;
    int f = fileOf(sq), r = rankOf(sq);
    for (int ff = f + 1; ff <= 6; ++ff) mask |= bit(r * 8 + ff);
    for (int ff = f - 1; ff >= 1; --ff) mask |= bit(r * 8 + ff);
    for (int rr = r + 1; rr <= 6; ++rr) mask |= bit(rr * 8 + f);
    for (int rr = r - 1; rr >= 1; --rr) mask |= bit(rr * 8 + f);
    return mask;
}

U64 bishopMask(int sq) {
    U64 mask = 0;
    int f = fileOf(sq), r = rankOf(sq);
    for (int ff = f + 1, rr = r + 1; ff <= 6 && rr <= 6; ++ff, ++rr)
        mask |= bit(rr * 8 + ff);
    for (int ff = f - 1, rr = r + 1; ff >= 1 && rr <= 6; --ff, ++rr)
        mask |= bit(rr * 8 + ff);
    for (int ff = f + 1, rr = r - 1; ff <= 6 && rr >= 1; ++ff, --rr)
        mask |= bit(rr * 8 + ff);
    for (int ff = f - 1, rr = r - 1; ff >= 1 && rr >= 1; --ff, --rr)
        mask |= bit(rr * 8 + ff);
    return mask;
}

U64 sliderAttacks(int sq, U64 occ, bool rook) {
    U64 attacks = 0;
    const int dr[8] = {1, -1, 0, 0, 1, 1, -1, -1};
    const int df[8] = {0, 0, 1, -1, 1, -1, 1, -1};
    int start = rook ? 0 : 4;
    int end = rook ? 4 : 8;
    for (int d = start; d < end; ++d) {
        int f = fileOf(sq) + df[d];
        int r = rankOf(sq) + dr[d];
        while (f >= 0 && f < 8 && r >= 0 && r < 8) {
            U64 b = bit(r * 8 + f);
            attacks |= b;
            if (occ & b) break;
            f += df[d];
            r += dr[d];
        }
    }
    return attacks;
}

int sign(int x) { return (x > 0) - (x < 0); }

void initMagics() {
#if !CHESSBOT_PEXT
    std::mt19937_64 rng(0x9E3779B97F4A7C15ULL);
#endif

    for (int sq = 0; sq < 64; ++sq) {
        for (int isRook = 1; isRook >= 0; --isRook) {
            Magic& m = isRook ? rookMagics[sq] : bishopMagics[sq];
            U64 mask = isRook ? rookMask(sq) : bishopMask(sq);
            int bits = popcount(mask);
            int size = 1 << bits;
            m.mask = mask;
            m.shift = 64 - bits;
            m.attacks = isRook ? &rookTable[sq][0] : &bishopTable[sq][0];

            std::vector<U64> occs;
            std::vector<U64> refs;
            occs.reserve(size);
            refs.reserve(size);

            U64 subset = 0;
            do {
                occs.push_back(subset);
                refs.push_back(sliderAttacks(sq, subset, isRook));
                subset = (subset - mask) & mask;
            } while (subset);

            std::vector<U64> table(size, 0);
#if CHESSBOT_PEXT
            // PEXT indexes the masked occupancy directly: a bijection, so no
            // magic search and no collisions. The table size is 1 << bits.
            for (int i = 0; i < size; ++i)
                table[size_t(_pext_u64(occs[size_t(i)], mask))] = refs[size_t(i)];
            m.magic = 0;
            std::copy(table.begin(), table.end(), m.attacks);
#else
            for (;;) {
                U64 magic = rng() & rng() & rng();
                std::fill(table.begin(), table.end(), 0);
                bool ok = true;
                for (int i = 0; i < size; ++i) {
                    unsigned idx = unsigned((occs[size_t(i)] * magic) >> m.shift);
                    if (table[idx] == 0)
                        table[idx] = refs[size_t(i)];
                    else if (table[idx] != refs[size_t(i)]) {
                        ok = false;
                        break;
                    }
                }
                if (ok) {
                    m.magic = magic;
                    std::copy(table.begin(), table.end(), m.attacks);
                    break;
                }
            }
#endif
        }
    }
}

void initLeapers() {
    const int knightOffsets[8][2] = {
        {1, 2}, {2, 1}, {2, -1}, {1, -2},
        {-1, -2}, {-2, -1}, {-2, 1}, {-1, 2}
    };
    for (int sq = 0; sq < 64; ++sq) {
        int f = fileOf(sq), r = rankOf(sq);
        U64 n = 0;
        for (auto& o : knightOffsets) {
            int nf = f + o[0], nr = r + o[1];
            if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) n |= bit(nr * 8 + nf);
        }
        Bitboards::knightAttacks[sq] = n;

        U64 k = 0;
        for (int df = -1; df <= 1; ++df)
            for (int dr = -1; dr <= 1; ++dr) {
                if (!df && !dr) continue;
                int nf = f + df, nr = r + dr;
                if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8)
                    k |= bit(nr * 8 + nf);
            }
        Bitboards::kingAttacks[sq] = k;

        U64 pw = 0, pb = 0;
        if (r < 7) {
            if (f > 0) pw |= bit(sq + 7);
            if (f < 7) pw |= bit(sq + 9);
        }
        if (r > 0) {
            if (f > 0) pb |= bit(sq - 9);
            if (f < 7) pb |= bit(sq - 7);
        }
        Bitboards::pawnAttacks[WHITE][sq] = pw;
        Bitboards::pawnAttacks[BLACK][sq] = pb;
    }
}

void initLines() {
    for (int a = 0; a < 64; ++a) {
        for (int b = 0; b < 64; ++b) {
            Bitboards::between[a][b] = 0;
            Bitboards::line[a][b] = 0;
            if (a == b) continue;
            int df = fileOf(b) - fileOf(a);
            int dr = rankOf(b) - rankOf(a);
            if (df != 0 && dr != 0 && std::abs(df) != std::abs(dr)) continue;

            int sf = sign(df), sr = sign(dr);
            int f = fileOf(a) + sf, r = rankOf(a) + sr;
            U64 btw = 0;
            while (f != fileOf(b) || r != rankOf(b)) {
                btw |= bit(r * 8 + f);
                f += sf;
                r += sr;
            }
            Bitboards::between[a][b] = btw;
            Bitboards::between[b][a] = btw;

            U64 ln = bit(a) | bit(b);
            for (int dir = -1; dir <= 1; dir += 2) {
                int lf = fileOf(a) + dir * sf;
                int lr = rankOf(a) + dir * sr;
                while (lf >= 0 && lf < 8 && lr >= 0 && lr < 8) {
                    ln |= bit(lr * 8 + lf);
                    lf += dir * sf;
                    lr += dir * sr;
                }
            }
            Bitboards::line[a][b] = ln;
            Bitboards::line[b][a] = ln;
        }
    }
}

} // namespace

namespace Bitboards {
void init() {
    initLeapers();
    initLines();
    initMagics();
}
} // namespace Bitboards

// ---------------------------------------------------------------------------
// Zobrist keys
// ---------------------------------------------------------------------------
namespace Zobrist {
U64 psq[12][64];
U64 castling[16];
U64 epFile[8];
U64 rule50[128];
U64 side;

void init() {
    std::mt19937_64 rng(0xD1B54A32D192ED03ULL);
    for (int p = 0; p < 12; ++p)
        for (int s = 0; s < 64; ++s) psq[p][s] = rng();
    for (int i = 0; i < 16; ++i) castling[i] = rng();
    for (int i = 0; i < 8; ++i) epFile[i] = rng();
    for (int i = 0; i < 128; ++i) rule50[i] = rng();
    side = rng();
}
} // namespace Zobrist

void initAll() {
    Bitboards::init();
    Zobrist::init();
    initEvaluation();
}

} // namespace chess
