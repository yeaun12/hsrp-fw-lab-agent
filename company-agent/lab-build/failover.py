"""Start/stop CORE1 while PC1 pings 8.8.8.8; report loss and HSRP state.

usage: py -3 failover.py stop|start [ping_count]
"""
import contextlib, io, re, sys, threading, time
from g import api, con

P = "HSRP-FW-LAB"
action = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 40
pid = next(p["project_id"] for p in api("GET", "/projects") if p["name"] == P)
core1 = next(n for n in api("GET", f"/projects/{pid}/nodes") if n["name"] == "CORE1")


def act():
    time.sleep(5)
    api("POST", f"/projects/{pid}/nodes/{core1['node_id']}/{action}")
    print(f"[{time.strftime('%X')}] CORE1 {action}", flush=True)


threading.Thread(target=act).start()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    con(P, "PC1", ["", f"ping 8.8.8.8 -c {count}"], t=count * 3)
seqs = re.findall(r"icmp_seq=(\d+) (ttl|timeout)", buf.getvalue())
lost = [int(s) for s, k in seqs if k == "timeout"]
print(f"PC1 -> 8.8.8.8: sent {len(seqs)}, ok {len(seqs) - len(lost)}, lost {len(lost)} (seq {lost})")
if action == "start":
    time.sleep(45)  # CORE1 boot + HSRP preempt
for n in ("CORE1", "CORE2") if action == "start" else ("CORE2",):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        con(P, n, ["terminal length 0", "show standby brief"], t=15)
    print(n, "\n".join(l for l in out.getvalue().splitlines() if re.match(r"Fa0/0\.\d+", l)))
