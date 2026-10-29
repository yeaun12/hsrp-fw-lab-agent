"""HSRP-FW-LAB phase 2: access layer, server/mgmt VLANs, branch, dual ISP, attacker."""
import subprocess
from g import api

PROJECT = "HSRP-FW-LAB"
VB = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"

VM_TEMPLATES = [
    {"name": "LAB-SRV(MariaDB-Rocky9)", "vmname": "MariaDB(RockyLinux9)", "adapters": 1, "ram": 1024, "category": "guest"},
    {"name": "LAB-KALI(kali_2026.2)", "vmname": "kali_2026.2", "adapters": 1, "ram": 1024, "category": "guest"},
]

NEW_NODES = [  # name, template
    ("ISP2", "c2691"), ("ASW1", "Cisco Access Switch- L3"), ("ASW2", "Cisco Access Switch- L3"),
    ("BRANCH", "c2691"), ("SRV", "LAB-SRV(MariaDB-Rocky9)"), ("ATTACKER", "LAB-KALI(kali_2026.2)"),
    ("PC4", "VPCS"), ("BR-PC", "VPCS"),
]

LAYOUT = {
    "ATTACKER": (-350, -470), "ISP1": (-150, -470), "ISP2": (150, -470),
    "FW": (0, -340), "WEB": (260, -340), "SW1": (0, -220),
    "CORE1": (-200, -100), "CORE2": (200, -100), "BRANCH": (480, -220), "BR-PC": (640, -220),
    "ASW1": (-200, 60), "ASW2": (200, 60),
    "PC1": (-330, 220), "PC3": (-130, 220), "PC2": (80, 220), "SRV": (240, 220), "PC4": (400, 220),
}

LINKS = [
    ("ISP1", "FastEthernet0/1", "ISP2", "FastEthernet0/1"),
    ("ISP1", "FastEthernet1/0", "ATTACKER", "Ethernet0"),
    ("ISP2", "FastEthernet0/0", "FW", "Ethernet3"),
    ("CORE1", "FastEthernet1/0", "CORE2", "FastEthernet1/0"),
    ("CORE1", "FastEthernet1/1", "CORE2", "FastEthernet1/1"),
    ("CORE1", "FastEthernet1/3", "ASW1", "FastEthernet1/0"),
    ("CORE2", "FastEthernet1/3", "ASW1", "FastEthernet1/1"),
    ("CORE1", "FastEthernet1/4", "ASW2", "FastEthernet1/0"),
    ("CORE2", "FastEthernet1/4", "ASW2", "FastEthernet1/1"),
    ("CORE1", "FastEthernet0/1", "BRANCH", "FastEthernet0/0"),
    ("CORE2", "FastEthernet0/1", "BRANCH", "FastEthernet0/1"),
    ("BRANCH", "FastEthernet1/0", "BR-PC", "Ethernet0"),
    ("ASW1", "FastEthernet1/5", "PC1", "Ethernet0"),
    ("ASW1", "FastEthernet1/6", "PC3", "Ethernet0"),
    ("ASW2", "FastEthernet1/5", "PC2", "Ethernet0"),
    ("ASW2", "FastEthernet1/6", "SRV", "Ethernet0"),
    ("ASW2", "FastEthernet1/7", "PC4", "Ethernet0"),
]


def port(node, name):
    p = next(p for p in node["ports"] if p["name"] == name)
    return {"node_id": node["node_id"], "adapter_number": p["adapter_number"], "port_number": p["port_number"]}


pid = next(p["project_id"] for p in api("GET", "/projects") if p["name"] == PROJECT)
nodes = {n["name"]: n for n in api("GET", f"/projects/{pid}/nodes")}

if "ISP" in nodes:
    isp = nodes.pop("ISP")["node_id"]
    api("POST", f"/projects/{pid}/nodes/{isp}/stop")  # dynamips nodes can only be renamed while off
    nodes["ISP1"] = api("PUT", f"/projects/{pid}/nodes/{isp}", {"name": "ISP1"})

templates = {t["name"]: t for t in api("GET", "/templates")}
for vt in VM_TEMPLATES:
    if vt["name"] not in templates:
        body = {"template_type": "virtualbox", "compute_id": "local", "linked_clone": True,
                "console_type": "telnet", "adapter_type": "Intel PRO/1000 MT Desktop (82540EM)", **vt}
        templates[vt["name"]] = api("POST", "/templates", body)
        print("template registered:", vt["name"])

for name, tpl in NEW_NODES:
    if name in nodes:
        continue
    x, y = LAYOUT[name]
    n = api("POST", f"/projects/{pid}/templates/{templates[tpl]['template_id']}",
            {"x": x, "y": y, "compute_id": "local", "name": name})
    if n["name"] != name:
        n = api("PUT", f"/projects/{pid}/nodes/{n['node_id']}", {"name": name})
    nodes[name] = n
    if n["node_type"] == "virtualbox":  # clone inherits NAT/bridged NIC from the base VM
        subprocess.run([VB, "modifyvm", name, "--nic1", "none", "--nic2", "none", "--nic3", "none"], check=True)
    print("node", name)

for name, (x, y) in LAYOUT.items():
    nodes[name] = api("PUT", f"/projects/{pid}/nodes/{nodes[name]['node_id']}", {"x": x, "y": y})

# PCs move from SW1 to the access switches; SW1 stays as the FW/core transit switch
by_id = {n["node_id"]: name for name, n in nodes.items()}
for l in api("GET", f"/projects/{pid}/links"):
    names = {by_id.get(e["node_id"]) for e in l["nodes"]}
    if "SW1" in names and names & {"PC1", "PC2", "PC3"}:
        api("DELETE", f"/projects/{pid}/links/{l['link_id']}")
        print("unlinked", names)

used = {(e["node_id"], e["adapter_number"], e["port_number"])
        for l in api("GET", f"/projects/{pid}/links") for e in l["nodes"]}
for a, ia, b, ib in LINKS:
    ends = [port(nodes[a], ia), port(nodes[b], ib)]
    if any((e["node_id"], e["adapter_number"], e["port_number"]) in used for e in ends):
        continue
    api("POST", f"/projects/{pid}/links", {"nodes": ends})
    print("link", a, ia, "<->", b, ib)
print("nodes", len(nodes), "links", len(api("GET", f"/projects/{pid}/links")))
