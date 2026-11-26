import os,sys,time
sys.path.insert(0,"tests")
from uci_engine import Engine
import testutil
e=Engine(testutil.ENGINE,"hash4096")
e.uci()
t=time.time(); e.send("setoption name Hash value 4096")
ok=e.wait_ready(60)
print("ready", ok, f"{time.time()-t:.2f}s")
st=e.mark(); e.send("position startpos"); e.send("go depth 4")
bm,el,ls=e.wait_bestmove(timeout=10,start=st)
print(bm, f"{el:.3f}s", [l for l in ls if l.startswith("info depth")][-1:])
e.close()
