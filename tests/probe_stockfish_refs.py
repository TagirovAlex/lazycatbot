import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil

CASES = [
    ("startpos", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 5),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 4),
    ("pos3", "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 5),
    ("pos4", "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", 4),
    ("pos5", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 4),
    ("task-pos6", "r4rk1/1pp1qppp/p1np1n2/2b1p3/2B1P3/P1NP1N2/1PP1QPPP/R1B1K2R w KQ - 0 1", 4),
    ("cpw-pos6", "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 4),
]
EXPECTED = {
    "startpos": {1: 20, 2: 400, 3: 8902, 4: 197281, 5: 4865609},
    "kiwipete": {1: 48, 2: 2039, 3: 97862, 4: 4085603},
    "pos3": {1: 14, 2: 191, 3: 2812, 4: 43238, 5: 674624},
    "pos4": {1: 6, 2: 264, 3: 9467, 4: 422333},
    "pos5": {1: 44, 2: 1486, 3: 62379, 4: 2103487},
    "task-pos6": {1: 44, 2: 1763, 3: 73911, 4: 2846198},
    "cpw-pos6": {1: 46, 2: 2079, 3: 89890, 4: 3894594},
}

p = subprocess.Popen([testutil.STOCKFISH], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1)
p.stdin.write("uci\n"); p.stdin.flush()
while True:
    if p.stdout.readline().strip() == "uciok":
        break
for name, fen, d in CASES:
    p.stdin.write("position fen " + fen + "\n"); p.stdin.flush()
    t = time.time()
    p.stdin.write(f"go perft {d}\n"); p.stdin.flush()
    while True:
        line = p.stdout.readline().strip()
        if line.startswith("Nodes searched:"):
            got = int(line.split(":")[1].strip())
            break
    exp = EXPECTED[name][d]
    print(f"{name:10s} depth {d}: stockfish={got} expected={exp} "
          f"{'OK' if got == exp else 'MISMATCH'} ({time.time()-t:.2f}s)", flush=True)
p.stdin.write("quit\n"); p.stdin.flush()
p.wait(timeout=5)
