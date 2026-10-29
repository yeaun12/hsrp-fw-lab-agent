"""Phase 3: AGENT-PC (Rocky Linux) on the management VLAN (ASW2 Fa1/8, VLAN40).

nic1 = GNS3 link (VLAN40). nic2 = VirtualBox NAT (internet for the Claude API, plus host port forwards
127.0.0.1:8080 -> dashboard and 127.0.0.1:2222 -> ssh). GNS3 rewrites nic2 on every start, so
agentpc_uplink() must run after each start.
"""
import subprocess, time
from g import api

PROJECT = "HSRP-FW-LAB"
VB = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
TEMPLATE = "LAB-AGENT(RockyLinux9)"


def agentpc_uplink():
    subprocess.run([VB, "controlvm", "AGENT-PC", "nic2", "nat"], check=True)
    subprocess.run([VB, "controlvm", "AGENT-PC", "setlinkstate2", "on"], check=True)
    rules = subprocess.run([VB, "showvminfo", "AGENT-PC", "--machinereadable"], capture_output=True, text=True).stdout
    for name, hp, gp in (("dashboard", 8080, 8080), ("ssh", 2222, 22)):
        if f'"{name},' not in rules:
            subprocess.run([VB, "controlvm", "AGENT-PC", "natpf2", f"{name},tcp,127.0.0.1,{hp},,{gp}"], check=True)


if __name__ == "__main__":
    pid = next(p["project_id"] for p in api("GET", "/projects") if p["name"] == PROJECT)
    nodes = {n["name"]: n for n in api("GET", f"/projects/{pid}/nodes")}
    if "AGENT-PC" in nodes:
        api("POST", f"/projects/{pid}/nodes/{nodes['AGENT-PC']['node_id']}/stop"); time.sleep(8)
        api("DELETE", f"/projects/{pid}/nodes/{nodes['AGENT-PC']['node_id']}")
    tpl = next(t for t in api("GET", "/templates") if t["name"] == TEMPLATE)
    api("PUT", f"/templates/{tpl['template_id']}", {"adapters": 2, "use_any_adapter": True})
    ag = api("POST", f"/projects/{pid}/templates/{tpl['template_id']}",
             {"x": 560, "y": 220, "compute_id": "local", "name": "AGENT-PC"})
    subprocess.run([VB, "modifyvm", "AGENT-PC", "--nic1", "none", "--nic2", "nat"], check=True)
    asw = nodes["ASW2"]
    p = next(p for p in asw["ports"] if p["name"] == "FastEthernet1/8")
    api("POST", f"/projects/{pid}/links", {"nodes": [
        {"node_id": ag["node_id"], "adapter_number": 0, "port_number": 0},
        {"node_id": asw["node_id"], "adapter_number": p["adapter_number"], "port_number": p["port_number"]}]})
    api("POST", f"/projects/{pid}/nodes/{ag['node_id']}/start")
    agentpc_uplink()
    print("AGENT-PC recreated (2 adapters), linked to ASW2 Fa1/8, started, NAT uplink set")
