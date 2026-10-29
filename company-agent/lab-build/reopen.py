"""Reopen a GNS3 project whose VirtualBox linked clones lost their disk registrations.

GNS3 2.2 + VirtualBox 7: after a project close, the clones' differencing disks can drop out of
the base VM's media registry, and `registervm` then fails with "A differencing image of snapshot
... could not be found". Opening each .vdi re-registers it (parents first), then the project opens.

usage: py -3 reopen.py [PROJECT_NAME]   (default HSRP-FW-LAB)
"""
import glob, os, subprocess, sys
from g import api

VB = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
name = sys.argv[1] if len(sys.argv) > 1 else "HSRP-FW-LAB"
proj = next(p for p in api("GET", "/projects") if p["name"] == name)
pid = proj["project_id"]
vbox_root = os.path.join(proj["path"], "project-files", "virtualbox")
vdis = glob.glob(os.path.join(vbox_root, "*", "*", "Snapshots", "*.vdi"))

# a half-finished earlier open can leave some clones registered; GNS3 then refuses to register them again
registered = subprocess.run([VB, "list", "vms"], capture_output=True, text=True, errors="replace").stdout
for vbox in glob.glob(os.path.join(vbox_root, "*", "*", "*.vbox")):
    vm = os.path.splitext(os.path.basename(vbox))[0]
    if f'"{vm}"' in registered:
        subprocess.run([VB, "unregistervm", vm], check=True)  # files stay; no --delete
        print("unregistered leftover", vm)

pending = list(vdis)
for _ in range(len(vdis) + 1):  # parents must be open before their children
    failed = []
    for v in pending:
        r = subprocess.run([VB, "showmediuminfo", "disk", v], capture_output=True, text=True, errors="replace")
        (failed.append(v) if r.returncode else None)
    if not failed or len(failed) == len(pending):
        pending = failed
        break
    pending = failed
print(f"disks re-registered: {len(vdis) - len(pending)}/{len(vdis)}", *(f"\n  FAILED {p}" for p in pending))

print("open:", api("POST", f"/projects/{pid}/open")["status"])
api("POST", f"/projects/{pid}/nodes/start")
print({n["name"]: n["status"] for n in api("GET", f"/projects/{pid}/nodes")})
