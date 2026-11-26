// Auto-generated neural-network weights placeholder.
// Run `chessbot train` (or tools/train_nnue.cpp) to replace this file
// with trained weights. Until then the network is disabled and the engine
// uses the classical evaluation only.
#include "engine.hpp"

namespace chess {
namespace NNUE {

float W1[INPUTS][HIDDEN];
float B1[HIDDEN];
float W2[HIDDEN];
float B2 = 0.0f;
bool trained = false;
bool enabled = true;
bool direct = false;
int weight = 10;
int clip = 80;

void init() {
    if (trained) return;
    std::memset(W1, 0, sizeof(W1));
    std::memset(B1, 0, sizeof(B1));
    std::memset(W2, 0, sizeof(W2));
    B2 = 0.0f;
}

} // namespace NNUE
} // namespace chess
