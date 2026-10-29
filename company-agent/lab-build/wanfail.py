"""Shut ISP1's FW-facing port while PC1 pings 8.8.8.8; report loss and which WAN carried traffic.

usage: py -3 wanfail.py down|up [ping_count]
"""
import contextlib, io, re, sys, threading, time
from g import con

P = "HSRP-FW-LAB"
action = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
cmd = "shutdown" if action == "down" else "no shutdown"


def act():
    time.sleep(5)
    with contextlib.redirect_stdout(io.StringIO()):
        con(P, "ISP1", ["", "configure terminal", "interface FastEthernet0/0", cmd, "end"], t=15)
    print(f"[{time.strftime('%X')}] ISP1 Fa0/0 {cmd}", flush=True)


threading.Thread(target=act).start()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    con(P, "PC1", ["", f"ping 8.8.8.8 -c {count}"], t=count * 3)
seqs = re.findall(r"icmp_seq=(\d+) (ttl|timeout)", buf.getvalue()[buf.getvalue().rfind(f"-c {count}"):])
lost = [int(s) for s, k in seqs if k == "timeout"]
print(f"PC1 -> 8.8.8.8: sent {len(seqs)}, ok {len(seqs) - len(lost)}, lost {len(lost)} (seq {lost})")
