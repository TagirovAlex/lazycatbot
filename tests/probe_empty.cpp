#include "../src/engine.hpp"
#include <cstdio>
#include <iostream>

int main() {
    using namespace chess;
    initAll();
    Position pos; // default empty position, no kings
    std::cerr << "empty: kings " << pos.kingSq[0] << "," << pos.kingSq[1]
              << " occ=0x" << std::hex << pos.occ << std::dec << std::endl;
    MoveList list;
    pos.generateMoves(list);
    std::cerr << "generateMoves count=" << list.count << std::endl;
    std::cerr << "calling inCheck..." << std::endl;
    bool chk = pos.inCheck();
    std::cerr << "inCheck=" << chk << std::endl;

    TranspositionTable tt;
    Search search(pos, tt);
    SearchLimits limits;
    limits.depth = 1;
    std::cerr << "calling search.run(depth 1)..." << std::endl;
    search.run(limits);
    std::cerr << "search.run returned best=" << moveToUci(search.lastBest()) << std::endl;
    return 0;
}
