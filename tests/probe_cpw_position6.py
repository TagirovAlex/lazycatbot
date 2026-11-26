import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil
from uci_engine import Engine
import chess

fen = "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"
exp = {1: 46, 2: 2079, 3: 89890, 4: 3894594}
b = chess.Board(fen)
print("valid", b.is_valid(), "legal", b.legal_moves.count())
e = Engine(testutil.ENGINE, "cpw6")
e.uci()
e.set_position(fen=fen)
for d in sorted(exp):
    st = e.mark(); e.send(f"perft {d}")
    ok = e.wait_for("perft ", 60, st)
    got = int(e.collect(st)[-1].split()[1]) if ok else None
    print("depth", d, "got", got, "expected", exp[d], "OK" if got == exp[d] else "FAIL")
e.close()
