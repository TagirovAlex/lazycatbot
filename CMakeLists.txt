cmake_minimum_required(VERSION 3.16)
project(ChessBot VERSION 2.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)

set(ENGINE_SOURCES
    src/bitboards.cpp
    src/position.cpp
    src/eval.cpp
    src/search.cpp
    src/syzygy.cpp
    src/nnue_weights.cpp
    third_party/fathom/tbprobe.c
)

add_executable(chessbot src/uci.cpp ${ENGINE_SOURCES})
target_include_directories(chessbot PRIVATE third_party/fathom)
target_link_libraries(chessbot PRIVATE Threads::Threads)

add_executable(trainer src/trainer.cpp ${ENGINE_SOURCES})
target_include_directories(trainer PRIVATE third_party/fathom)
target_link_libraries(trainer PRIVATE Threads::Threads)

if(MSVC)
    target_compile_options(chessbot PRIVATE /O2 /arch:AVX2 /W3)
    target_compile_options(trainer PRIVATE /O2 /arch:AVX2 /W3)
    target_link_options(chessbot PRIVATE /STACK:33554432)
    target_link_options(trainer PRIVATE /STACK:33554432)
else()
    target_compile_options(chessbot PRIVATE -O3 -march=native -flto -Wall -Wextra)
    target_compile_options(trainer PRIVATE -O3 -march=native -flto -Wall -Wextra)
    target_link_options(chessbot PRIVATE -flto)
    target_link_options(trainer PRIVATE -flto)
    if(MINGW)
        # GNU ld option; only valid for the MinGW toolchain. Other linkers
        # (ld64, lld on ELF) reject it. The default thread stack is already
        # sufficient for MAX_PLY=128; this is extra headroom.
        target_link_options(chessbot PRIVATE
            -static-libgcc -static-libstdc++ -Wl,--stack,33554432)
        target_link_options(trainer PRIVATE
            -static-libgcc -static-libstdc++ -Wl,--stack,33554432)
    endif()
endif()
