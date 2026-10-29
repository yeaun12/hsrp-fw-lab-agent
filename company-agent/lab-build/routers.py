"""Wait for IOS boot, skip setup dialog, push configs."""
import socket, sys, time
from g import con, node_port, read_until

PROJECT = "HSRP-FW-LAB"


def core(n, me, pri10, pri20, pri99):
    c = ["enable", "configure terminal", f"hostname CORE{n}", "no ip domain-lookup",
         "line con 0", "logging synchronous", "exec-timeout 0 0", "exit",
         "interface FastEthernet0/0", "no ip address", "no shutdown", "exit"]
    for vlan, pri in ((10, pri10), (20, pri20), (99, pri99)):
        c += [f"interface FastEthernet0/0.{vlan}", f"encapsulation dot1Q {vlan}",
              f"ip address 10.0.{vlan}.{me} 255.255.255.0",
              f"standby {vlan} ip 10.0.{vlan}.254", f"standby {vlan} priority {pri}",
              f"standby {vlan} preempt", "exit"]
    return c + ["ip route 0.0.0.0 0.0.0.0 10.0.99.1", "end", "write memory"]


CONFIGS = {
    "ISP": ["enable", "configure terminal", "hostname ISP", "no ip domain-lookup",
            "line con 0", "logging synchronous", "exec-timeout 0 0", "exit",
            "interface Loopback0", "ip address 8.8.8.8 255.255.255.255", "exit",
            "interface FastEthernet0/0", "ip address 211.0.0.1 255.255.255.252", "no shutdown", "exit",
            "ip route 211.0.1.0 255.255.255.240 211.0.0.2", "end", "write memory"],
    "CORE1": core(1, 2, 110, 90, 110),
    "CORE2": core(2, 3, 90, 110, 90),
}


def wait_boot(node, timeout=400):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(("localhost", node_port(PROJECT, node)), timeout=1)
            s.sendall(b"\r\n")
            out, ok = read_until(s, 10)
            if "[yes/no]" in out or "initial configuration dialog" in out:
                s.sendall(b"no\r\n"); time.sleep(8); s.sendall(b"\r\n\r\n")
                out, ok = read_until(s, 30)
            s.close()
            if ok and out.rstrip().endswith((">", "#")):
                return True
        except OSError:
            pass
        time.sleep(10)
    return False


if __name__ == "__main__":
    for node in sys.argv[1:] or CONFIGS:
        print(f"===== {node} boot:", wait_boot(node), flush=True)
        con(PROJECT, node, CONFIGS[node], t=20)
