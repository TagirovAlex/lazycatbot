# Build ChessBot with MinGW g++ (default on this machine).
$ErrorActionPreference = "Stop"

$sources = @(
    "src/bitboards.cpp",
    "src/position.cpp",
    "src/eval.cpp",
    "src/search.cpp",
    "src/syzygy.cpp",
    "src/nnue_weights.cpp",
    "third_party/fathom/tbprobe.c"
)

$flags = @("-std=c++20", "-O3", "-march=native", "-flto", "-DNDEBUG",
           "-Wall", "-Wextra", "-pthread", "-Ithird_party/fathom")
$linkFlags = @("-flto", "-static-libgcc", "-static-libstdc++", "-Wl,--stack,33554432")

Write-Host "Building chessbot.exe ..."
& g++ @flags -o chessbot.exe src/uci.cpp @sources @linkFlags
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building trainer.exe ..."
& g++ @flags -o trainer.exe src/trainer.cpp @sources @linkFlags
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Done. Run .\chessbot.exe for UCI mode."
