"""Give the agent management access: IOS VTY (telnet, local user, mgmt-VLAN-only ACL).

The password is generated once and kept in secrets.env next to this script (never printed).
"""
import contextlib, io, os, re, secrets, string
from g import con

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets.env")
DEVICES = ["CORE1", "CORE2", "ASW1", "ASW2", "BRANCH"]

if not os.path.exists(SECRETS):
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(18))
    with open(SECRETS, "w") as f:
        f.write(f"NETOPS_USER=netops\nNETOPS_PASSWORD={pw}\n")
pw = dict(l.strip().split("=", 1) for l in open(SECRETS) if "=" in l)["NETOPS_PASSWORD"]

for dev in DEVICES:
    cmds = ["", "configure terminal",
            f"username netops privilege 15 secret {pw}",
            "ip access-list standard MGMT-ONLY", "permit 10.0.40.0 0.0.0.255", "deny any log", "exit",
            "line vty 0 4", "access-class MGMT-ONLY in", "login local", "transport input telnet",
            "exec-timeout 30 0", "exit", "end", "write memory"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        con("HSRP-FW-LAB", dev, cmds, t=20)
    out = buf.getvalue()
    ok = "[OK]" in out[out.rfind("write memory"):]
    err = re.findall(r"% (Invalid|Incomplete|Ambiguous)[^\n]*", out[out.rfind("configure terminal"):])
    print(f"{dev}: {'saved' if ok else 'NOT SAVED'} {err or ''}")
