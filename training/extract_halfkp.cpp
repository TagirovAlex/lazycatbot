// HalfKP feature extractor for the training pipeline.
//
// Input:  text lines "FEN|target", where target is a side-to-move win
//         probability in [0,1] (or raw centipawns with --cp).
// Output: memmapped shards:
//   features.u16  [N,32]  uint16 feature indices
//   counts.u8     [N]     active feature count
//   groups.u32    [N]     FNV-1a group hash (for leak-resistant splits)
//   targets.f32   [N]     win probability
//   meta.txt
//
// Feature index:
//   bucket = oriented own-king square (0..63)
//   index  = (bucket * 12 + relativeColor * 6 + pieceType) * 64 + orientedSq
// Inputs: 64 * 12 * 64 = 49,152. Own king is excluded from the piece list.
#include "engine.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace chess;

namespace {

constexpr int kInputs = 64 * 12 * 64;

uint32_t fnv1a(const std::string& s) {
    uint32_t h = 2166136261u;
    for (unsigned char c : s) {
        h ^= c;
        h *= 16777619u;
    }
    return h;
}

float winProbFromCp(float cp) {
    return 1.0f / (1.0f + std::exp(-cp / 400.0f));
}

} // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage: extract_halfkp <input.txt> <outdir> [--cp] [--max N]\n";
        return 1;
    }

    std::string inputPath = argv[1];
    std::string outDir = argv[2];
    bool cpInput = false;
    long long maxPositions = 0;
    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--cp") cpInput = true;
        else if (a == "--max" && i + 1 < argc) maxPositions = std::stoll(argv[++i]);
    }

    // The caller creates outDir. Avoid std::filesystem here: on MinGW a
    // non-ASCII workspace path can make the narrow-to-wide conversion throw.
    std::ifstream in(inputPath);
    if (!in) {
        std::cerr << "cannot open " << inputPath << "\n";
        return 1;
    }

    std::ofstream featOut(outDir + "/features.u16", std::ios::binary);
    std::ofstream countOut(outDir + "/counts.u8", std::ios::binary);
    std::ofstream groupOut(outDir + "/groups.u32", std::ios::binary);
    std::ofstream targetOut(outDir + "/targets.f32", std::ios::binary);
    if (!featOut || !countOut || !groupOut || !targetOut) {
        std::cerr << "cannot create output shards in " << outDir << "\n";
        return 1;
    }

    initAll();

    std::string line;
    long long lines = 0, written = 0, skipped = 0;
    std::array<uint16_t, 32> features{};

    while (std::getline(in, line)) {
        ++lines;
        if (maxPositions > 0 && written >= maxPositions) break;
        if (line.empty()) continue;

        const size_t sep = line.rfind('|');
        if (sep == std::string::npos) { ++skipped; continue; }
        const std::string fen = line.substr(0, sep);
        float target = 0.0f;
        try {
            target = std::stof(line.substr(sep + 1));
        } catch (...) {
            ++skipped;
            continue;
        }
        if (cpInput) target = winProbFromCp(target);
        target = std::clamp(target, 0.0f, 1.0f);

        Position pos;
        if (!pos.setFen(fen)) { ++skipped; continue; }
        const Color us = pos.side;
        const Color them = ~us;
        const int ownKing = pos.kingSq[us];
        if (ownKing < 0) { ++skipped; continue; }

        const bool black = (us == BLACK);
        const int bucket = black ? (ownKing ^ 56) : ownKing;
        int n = 0;

        for (int p = 0; p < 12; ++p) {
            const Color c = pieceColor(Piece(p));
            const PieceType pt = pieceType(Piece(p));
            if (c == us && pt == KING) continue; // represented by the bucket
            U64 bb = pos.pieces(c, pt);
            while (bb) {
                const int sq = popLsb(bb);
                const int osq = black ? (sq ^ 56) : sq;
                const int rel = (c == us) ? 0 : 1;
                const int idx = (bucket * 12 + rel * 6 + int(pt)) * 64 + osq;
                if (idx >= 0 && idx < kInputs && n < 32)
                    features[size_t(n++)] = uint16_t(idx);
            }
        }
        if (n == 0) { ++skipped; continue; }

        std::array<uint16_t, 32> padded{};
        std::copy(features.begin(), features.begin() + n, padded.begin());
        featOut.write(reinterpret_cast<const char*>(padded.data()),
                      std::streamsize(padded.size() * sizeof(uint16_t)));
        const uint8_t cnt = uint8_t(n);
        countOut.write(reinterpret_cast<const char*>(&cnt), 1);
        const uint32_t group = fnv1a(fen);
        groupOut.write(reinterpret_cast<const char*>(&group), sizeof(group));
        targetOut.write(reinterpret_cast<const char*>(&target), sizeof(target));
        ++written;

        if (written % 100000 == 0)
            std::cerr << "extracted " << written << " positions\n";
    }

    featOut.close();
    countOut.close();
    groupOut.close();
    targetOut.close();

    std::ofstream meta(outDir + "/meta.txt");
    meta << "format=halfkp-v1\n";
    meta << "inputs=" << kInputs << "\n";
    meta << "max_active=32\n";
    meta << "count=" << written << "\n";
    meta << "lines=" << lines << "\n";
    meta << "skipped=" << skipped << "\n";
    meta << "target=win_probability\n";
    meta.close();

    std::cout << "extracted " << written << " positions, skipped " << skipped
              << " -> " << outDir << "\n";
    return written > 0 ? 0 : 1;
}
