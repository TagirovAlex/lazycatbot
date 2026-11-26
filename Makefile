CXX ?= g++
CXXFLAGS ?= -std=c++20 -O3 -march=native -flto -DNDEBUG -Wall -Wextra -pthread
LDFLAGS ?= -flto -static-libgcc -static-libstdc++ -Wl,--stack,33554432

ENGINE_SRC = src/bitboards.cpp src/position.cpp src/eval.cpp src/search.cpp src/syzygy.cpp src/nnue_weights.cpp third_party/fathom/tbprobe.c

all: chessbot.exe trainer.exe

chessbot.exe: src/uci.cpp src/engine.hpp $(ENGINE_SRC)
	$(CXX) $(CXXFLAGS) -Ithird_party/fathom -o $@ src/uci.cpp $(ENGINE_SRC) $(LDFLAGS)

trainer.exe: src/trainer.cpp src/engine.hpp $(ENGINE_SRC)
	$(CXX) $(CXXFLAGS) -Ithird_party/fathom -o $@ src/trainer.cpp $(ENGINE_SRC) $(LDFLAGS)

clean:
	del /Q chessbot.exe trainer.exe 2>nul || true

.PHONY: all clean
