import os, sys, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil
import chess

fen = "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"


def py_perft(b, d):
    if d == 0:
        return 1
    n = 0
    for m in b.legal_moves:
        b.push(m); n += py_perft(b, d - 1); b.pop()
    return n


b = chess.Board(fen)
for d in (1, 2, 3):
    print("python-chess depth", d, py_perft(b, d), flush=True)

p = subprocess.Popen([testutil.STOCKFISH], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1)
p.stdin.write("uci\n"); p.stdin.flush()
while True:
    if p.stdout.readline().strip() == "uciok":
        break
p.stdin.write("position fen " + fen + "\n"); p.stdin.flush()
p.stdin.write("go perft 4\n"); p.stdin.flush()
while True:
    line = p.stdout.readline().strip()
    if line.startswith("Nodes searched:"):
        print("stockfish depth 4", line.split(":")[1].strip())
        break
p.stdin.write("quit\n"); p.stdin.flush()
p.wait(timeout=5)
