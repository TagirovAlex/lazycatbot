# Build the ChessBot test binary (same sources as the shipped engine).
# Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File tests/build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    g++ -std=c++20 -O2 -march=native -pthread "-Wl,--stack,33554432" `
        -o tests/chessbot_test.exe `
        src/uci.cpp src/bitboards.cpp src/position.cpp src/eval.cpp `
        src/search.cpp src/nnue_weights.cpp
    if ($LASTEXITCODE -ne 0) { throw "build failed with exit code $LASTEXITCODE" }
    Write-Output "built tests/chessbot_test.exe"
}
finally {
    Pop-Location
}
