"""Phase 4: ADMIN-PC (Windows 10, GUI) on the management VLAN via ASW1 Fa1/9, plus mgmt DHCP on both cores."""
import contextlib, io, subprocess
from g import api, con

PROJECT = "HSRP-FW-LAB"
VB = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
TPL = {"name": "LAB-ADMIN(Windows10)", "vmname": "Windows10_22H2", "adapters": 1, "ram": 1536, "category": "guest"}


def ios(node, cmds):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        con(PROJECT, node, ["", *cmds], t=20)
    out = buf.getvalue()
    print(node, "saved" if "[OK]" in out[out.rfind("write memory"):] else "CHECK", "| errors:",
          [l for l in out[out.rfind("configure terminal"):].splitlines() if "% " in l])


pid = next(p["project_id"] for p in api("GET", "/projects") if p["name"] == PROJECT)
nodes = {n["name"]: n for n in api("GET", f"/projects/{pid}/nodes")}

# 관리망 DHCP: CORE1은 .100-.199, CORE2는 .200-.250 (한 대가 죽어도 다른 쪽이 할당)
ios("CORE1", ["configure terminal", "ip dhcp excluded-address 10.0.40.1 10.0.40.99",
              "ip dhcp excluded-address 10.0.40.200 10.0.40.255", "ip dhcp pool MGMT",
              "network 10.0.40.0 255.255.255.0", "default-router 10.0.40.254", "exit", "end", "write memory"])
ios("CORE2", ["configure terminal", "ip dhcp excluded-address 10.0.40.1 10.0.40.199",
              "ip dhcp excluded-address 10.0.40.251 10.0.40.255", "ip dhcp pool MGMT",
              "network 10.0.40.0 255.255.255.0", "default-router 10.0.40.254", "exit", "end", "write memory"])
ios("ASW1", ["configure terminal", "interface FastEthernet1/9", "switchport mode access", "switchport access vlan 40",
             "spanning-tree portfast", "exit", "end", "write memory"])

templates = {t["name"]: t for t in api("GET", "/templates")}
if TPL["name"] not in templates:
    templates[TPL["name"]] = api("POST", "/templates", {
        "template_type": "virtualbox", "compute_id": "local", "linked_clone": True, "console_type": "none",
        "adapter_type": "Intel PRO/1000 MT Desktop (82540EM)", **TPL})
if "ADMIN-PC" not in nodes:
    n = api("POST", f"/projects/{pid}/templates/{templates[TPL['name']]['template_id']}",
            {"x": -480, "y": 60, "compute_id": "local", "name": "ADMIN-PC"})
    subprocess.run([VB, "modifyvm", "ADMIN-PC", "--nic1", "none"], check=True)
    asw = nodes["ASW1"]
    p = next(p for p in asw["ports"] if p["name"] == "FastEthernet1/9")
    api("POST", f"/projects/{pid}/links", {"nodes": [
        {"node_id": n["node_id"], "adapter_number": 0, "port_number": 0},
        {"node_id": asw["node_id"], "adapter_number": p["adapter_number"], "port_number": p["port_number"]}]})
    api("POST", f"/projects/{pid}/nodes/{n['node_id']}/start")
    print("ADMIN-PC created, linked to ASW1 Fa1/9 (VLAN40), started")
