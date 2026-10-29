"""Run show/ping commands and print only this session's output (GNS3 replays console history).

usage: py -3 verify.py NODE "cmd" ["cmd" ...]
"""
import contextlib, io, sys, uuid
from g import con

node, cmds = sys.argv[1], sys.argv[2:]
marker = "mark-" + uuid.uuid4().hex[:6]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    # an unknown word echoes back as an error line we can cut on; VPCS and IOS both just complain
    con("HSRP-FW-LAB", node, ["", marker, *cmds], t=60)
out = buf.getvalue()
out = out[out.rfind(marker):]
out = out[out.find("\n") + 1:]
lines = [l for l in out.splitlines() if l.strip() and "Translating" not in l and "Unknown command" not in l
         and "% Invalid" not in l and "ERROR after" not in l and "^" != l.strip()]
print(f"##### {node}")
print("\n".join(lines))
