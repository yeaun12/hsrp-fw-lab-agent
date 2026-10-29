"""HSRP-FW-LAB: templates, project, nodes, links."""
from g import api

PROJECT = "HSRP-FW-LAB"

VM_TEMPLATES = [
    {"name": "LAB-FW(pfSense2.7.2)", "vmname": "pfSense2.7.2", "adapters": 3, "ram": 1024,
     "symbol": ":/symbols/firewall.svg", "category": "firewall"},
    {"name": "LAB-WEB(HTTPD-Rocky9)", "vmname": "HTTPD(RockyLinux9)", "adapters": 1, "ram": 1024,
     "category": "guest"},
]

NODES = [  # name, template, x, y
    ("ISP", "c2691", 0, -330),
    ("FW", "LAB-FW(pfSense2.7.2)", 0, -180),
    ("WEB", "LAB-WEB(HTTPD-Rocky9)", 250, -180),
    ("SW1", "Ethernet switch", 0, 30),
    ("CORE1", "c2691", -220, -60),
    ("CORE2", "c2691", 220, -60),
    ("PC1", "VPCS", -250, 200),
    ("PC2", "VPCS", -80, 200),
    ("PC3", "VPCS", 200, 200),
]

SW_PORTS = [  # port, type, vlan
    (0, "access", 99), (1, "dot1q", 1), (2, "dot1q", 1),
    (3, "access", 10), (4, "access", 10), (5, "access", 20),
]

LINKS = [
    ("ISP", "FastEthernet0/0", "FW", "Ethernet0"),
    ("FW", "Ethernet1", "SW1", "Ethernet0"),
    ("FW", "Ethernet2", "WEB", "Ethernet0"),
    ("CORE1", "FastEthernet0/0", "SW1", "Ethernet1"),
    ("CORE2", "FastEthernet0/0", "SW1", "Ethernet2"),
    ("PC1", "Ethernet0", "SW1", "Ethernet3"),
    ("PC2", "Ethernet0", "SW1", "Ethernet4"),
    ("PC3", "Ethernet0", "SW1", "Ethernet5"),
]


def port(node, name):
    p = next(p for p in node["ports"] if p["name"] == name)
    return {"node_id": node["node_id"], "adapter_number": p["adapter_number"], "port_number": p["port_number"]}


templates = {t["name"]: t for t in api("GET", "/templates")}
for vt in VM_TEMPLATES:
    if vt["name"] not in templates:
        body = {"template_type": "virtualbox", "compute_id": "local", "linked_clone": True,
                "console_type": "telnet", "adapter_type": "Intel PRO/1000 MT Desktop (82540EM)", **vt}
        templates[vt["name"]] = api("POST", "/templates", body)
        print("template registered:", vt["name"])

projects = {p["name"]: p for p in api("GET", "/projects")}
if PROJECT in projects:
    pid = projects[PROJECT]["project_id"]
    api("POST", f"/projects/{pid}/open")
else:
    pid = api("POST", "/projects", {"name": PROJECT})["project_id"]
    print("project created", pid)

nodes = {n["name"]: n for n in api("GET", f"/projects/{pid}/nodes")}
for name, tpl, x, y in NODES:
    if name in nodes:
        continue
    n = api("POST", f"/projects/{pid}/templates/{templates[tpl]['template_id']}",
            {"x": x, "y": y, "compute_id": "local", "name": name})
    if n["name"] != name:
        n = api("PUT", f"/projects/{pid}/nodes/{n['node_id']}", {"name": name})
    nodes[name] = n
    print("node", name, [p["name"] for p in n["ports"]][:4])

sw = nodes["SW1"]
mapping = [{"name": f"Ethernet{i}", "port_number": i, "type": t, "vlan": v, "ethertype": "0x8100"}
           for i, t, v in SW_PORTS]
nodes["SW1"] = api("PUT", f"/projects/{pid}/nodes/{sw['node_id']}",
                   {"properties": {"ports_mapping": mapping}})

used = {(e["node_id"], e["adapter_number"], e["port_number"])
        for l in api("GET", f"/projects/{pid}/links") for e in l["nodes"]}
for a, ia, b, ib in LINKS:
    ends = [port(nodes[a], ia), port(nodes[b], ib)]
    if any((e["node_id"], e["adapter_number"], e["port_number"]) in used for e in ends):
        continue
    api("POST", f"/projects/{pid}/links", {"nodes": ends})
    print("link", a, ia, "<->", b, ib)
print("done", pid)
