"""Phase 2 configs: L3-switch cores, access switches, branch, dual ISP."""
import sys
from g import con
from routers import PROJECT, wait_boot

BASE = ["line con 0", "logging synchronous", "exec-timeout 0 0", "exit", "no ip domain-lookup"]
VLANS = {10: "USERS-A", 20: "USERS-B", 30: "SERVERS", 40: "MGMT"}


def vlan_db():
    return ["vlan database", "vtp transparent"] + [f"vlan {v} name {n}" for v, n in VLANS.items()] + ["exit"]


def trunk(ifname, extra=()):
    return [f"interface {ifname}", "switchport trunk encapsulation dot1q", "switchport mode trunk", *extra, "exit"]


def access(ifname, vlan):
    return [f"interface {ifname}", "switchport mode access", f"switchport access vlan {vlan}",
            "spanning-tree portfast", "exit"]


def core(n):
    me = 1 + n  # CORE1 .2, CORE2 .3
    primary = (10, 40) if n == 1 else (20, 30)
    c = ["enable"] + vlan_db() + ["configure terminal", *BASE,
         "no interface FastEthernet0/0.10", "no interface FastEthernet0/0.20"]
    for v in (1, *VLANS):
        c.append(f"spanning-tree vlan {v} priority {4096 if v in primary or (v == 1 and n == 1) else 8192}")
    for i in ("FastEthernet1/0", "FastEthernet1/1"):
        c += trunk(i, ["channel-group 1 mode on"])
    c += trunk("FastEthernet1/3") + trunk("FastEthernet1/4")
    for v in VLANS:
        c += [f"interface Vlan{v}", f"ip address 10.0.{v}.{me} 255.255.255.0",
              f"standby {v} ip 10.0.{v}.254", f"standby {v} priority {110 if v in primary else 90}",
              f"standby {v} preempt", "no shutdown", "exit"]
    c += ["ip access-list extended SRV-PROTECT",
          "permit tcp 10.0.10.0 0.0.0.255 host 10.0.30.10 eq 3306",
          "permit icmp 10.0.10.0 0.0.0.255 10.0.30.0 0.0.0.255",
          "permit ip 10.0.40.0 0.0.0.255 10.0.30.0 0.0.0.255",
          "deny ip 10.0.20.0 0.0.0.255 10.0.30.0 0.0.0.255 log",
          "deny ip 10.0.10.0 0.0.0.255 10.0.30.0 0.0.0.255",
          "deny tcp any host 10.0.30.10 eq 3306",
          "permit ip any any", "exit",
          "interface Vlan30", "ip access-group SRV-PROTECT out", "exit",
          "interface FastEthernet0/1", f"ip address 10.1.255.{1 if n == 1 else 5} 255.255.255.252",
          "no shutdown", "exit",
          "router ospf 1", f"router-id {n}.{n}.{n}.{n}", "passive-interface default",
          "no passive-interface FastEthernet0/1", "no passive-interface FastEthernet0/0.99",
          "network 10.0.0.0 0.0.255.255 area 0", "network 10.1.255.0 0.0.0.7 area 0",
          "default-information originate", "exit", "end", "write memory"]
    return c


def asw(n, ports):
    c = ["enable"] + vlan_db() + ["configure terminal", f"hostname ASW{n}", *BASE, "no ip routing"]
    c += trunk("FastEthernet1/0") + trunk("FastEthernet1/1")
    for ifname, vlan in ports:
        c += access(ifname, vlan)
    return c + ["interface Vlan40", f"ip address 10.0.40.{20 + n} 255.255.255.0", "no shutdown", "exit",
                "ip default-gateway 10.0.40.254", "end", "write memory"]


CONFIGS = {
    "CORE1": core(1),
    "CORE2": core(2),
    "ASW1": asw(1, [("FastEthernet1/5", 10), ("FastEthernet1/6", 20)]),
    "ASW2": asw(2, [("FastEthernet1/5", 10), ("FastEthernet1/6", 30), ("FastEthernet1/7", 40)]),
    "BRANCH": ["enable", "configure terminal", "hostname BRANCH", *BASE,
               "interface FastEthernet0/0", "ip address 10.1.255.2 255.255.255.252", "no shutdown", "exit",
               "interface FastEthernet0/1", "ip address 10.1.255.6 255.255.255.252", "no shutdown", "exit",
               "interface Vlan1", "ip address 10.1.10.1 255.255.255.0", "no shutdown", "exit",
               "interface FastEthernet1/0", "spanning-tree portfast", "exit",
               "router ospf 1", "router-id 3.3.3.3", "passive-interface Vlan1",
               "network 10.1.0.0 0.0.255.255 area 0", "exit", "end", "write memory"],
    "ISP1": ["enable", "configure terminal", "hostname ISP1",
             "interface FastEthernet0/1", "ip address 203.0.113.1 255.255.255.252", "no shutdown", "exit",
             "interface Vlan1", "ip address 198.51.100.1 255.255.255.0", "no shutdown", "exit",
             "ip route 211.0.2.0 255.255.255.252 203.0.113.2", "end", "write memory"],
    "ISP2": ["enable", "configure terminal", "hostname ISP2", *BASE,
             "interface FastEthernet0/0", "ip address 211.0.2.1 255.255.255.252", "no shutdown", "exit",
             "interface FastEthernet0/1", "ip address 203.0.113.2 255.255.255.252", "no shutdown", "exit",
             "ip route 0.0.0.0 0.0.0.0 203.0.113.1", "end", "write memory"],
}

if __name__ == "__main__":
    for node in sys.argv[1:] or CONFIGS:
        print(f"===== {node} boot:", wait_boot(node), flush=True)
        con(PROJECT, node, CONFIGS[node], t=30)
