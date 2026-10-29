"""HSRP-FW-LAB을 열고 에이전트 시연이 가능한 상태로 만든다 (이 PC에서 실행).

    python lab_up.py

GNS3 2.2 + VirtualBox 7 + dynamips 환경에서 랩을 다시 열 때마다 필요한 작업을 순서대로 한다.
1. VM 복제본 디스크 재등록 후 프로젝트 열기, 전체 노드 시작   (../gns3-agent/reopen_project.py)
2. 코어 VLAN 재생성: 프로젝트를 닫으면 dynamips NVRAM의 vlan.dat가 지워진다
3. 코어를 한 대씩 재부팅: VLAN 없이 HSRP가 먼저 Active가 되면 가상 MAC이 잘못된 포트에 고정되는 문제 해소
4. 스위치 MAC 테이블 정리
5. 에이전트 서비스 시작
AGENT-PC 인터넷 NIC(VirtualBox NAT)와 포트포워딩은 1단계 직후에 다시 연결하고(GNS3가 시작할 때마다 덮어씀),
기동하는 동안에는 에이전트 서비스를 멈춰 둔다(코어 재부팅 경보가 API 호출로 이어지지 않게).
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

PROJECT = "HSRP-FW-LAB"
GNS3 = "http://localhost:3080/v2"
VBOX = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
REOPEN = os.path.join(HERE, "..", "gns3-agent", "reopen_project.py")
VLANS = [(10, "USERS-A"), (20, "USERS-B"), (30, "SERVERS"), (40, "MGMT")]
PROMPT = re.compile(rb"[\w\-.()]+[#>]\s*$")


def api(method, path, body=None):
    req = urllib.request.Request(GNS3 + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def nodes():
    return {n["name"]: n for n in api("GET", f"/projects/{PID}/nodes")}


def console(node, lines, wait=20):
    """노드 콘솔(telnet)에 줄을 보내고 출력 전체를 돌려준다."""
    s = socket.create_connection(("localhost", nodes()[node]["console"]), timeout=1)
    out = b""

    def read(until):
        nonlocal out
        end = time.time() + until
        buf = b""
        while time.time() < end:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                chunk = b""
            buf += bytes(c for c in chunk if c < 0xF0)   # telnet 협상 바이트 대충 제거
            if PROMPT.search(buf.rstrip(b"\r\n ") + b" "):
                break
        out += buf

    try:
        s.sendall(b"\r\n")
        read(5)
        for line in lines:
            s.sendall(line.encode() + b"\r\n")
            read(wait)
    finally:
        s.close()
    return out.decode(errors="replace")


def wait_ios(node, timeout=400):
    end = time.time() + timeout
    while time.time() < end:
        try:
            out = console(node, [""], wait=8)
            if "[yes/no]" in out:
                console(node, ["no"], wait=30)
            if re.search(r"[\w-]+[#>]\s*$", out.strip()):
                return True
        except OSError:
            pass
        time.sleep(10)
    return False


def wait_pfsense(timeout=900):
    """pfSense 시리얼 콘솔에 메뉴나 셸 프롬프트가 뜰 때까지 기다린다 (부팅 완료)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(("localhost", nodes()["FW"]["console"]), timeout=1)
            s.sendall(b"\r")
            buf, stop = b"", time.time() + 8
            while time.time() < stop:
                try:
                    buf += s.recv(4096)
                except socket.timeout:
                    pass
            s.close()
            if b"Enter an option" in buf or b"root" in buf:
                return True
        except OSError:
            pass
        time.sleep(10)
    return False


def step(msg):
    print(f"\n== {msg}", flush=True)


def agentpc_uplink():
    """AGENT-PC nic2를 VirtualBox NAT로 되돌리고 대시보드/SSH 포트포워딩을 건다 (GNS3가 시작할 때마다 덮어씀)."""
    subprocess.run([VBOX, "controlvm", "AGENT-PC", "nic2", "nat"], check=True)
    subprocess.run([VBOX, "controlvm", "AGENT-PC", "setlinkstate2", "on"], check=True)
    info = subprocess.run([VBOX, "showvminfo", "AGENT-PC", "--machinereadable"], capture_output=True, text=True).stdout
    for name, hp, gp in (("dashboard", 8080, 8080), ("ssh", 2222, 22)):
        if f'"{name},' not in info:
            subprocess.run([VBOX, "controlvm", "AGENT-PC", "natpf2", f"{name},tcp,127.0.0.1,{hp},,{gp}"], check=True)


def agent_service(action, timeout=240):
    """AGENT-PC의 에이전트 서비스를 멈추거나 켠다. 기동 중 생기는 경보가 에이전트 분석(API 비용)으로 넘어가지 않게 한다."""
    key = os.path.join(HERE, "keys", "agentpc_key")
    known = os.path.join(HERE, "keys", "agentpc_known_hosts")
    end = time.time() + timeout
    while time.time() < end:
        r = subprocess.run(["ssh", "-p", "2222", "-i", key, "-o", f"UserKnownHostsFile={known}", "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=5", "root@127.0.0.1", f"systemctl {action} lab-agent"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
        time.sleep(10)
    return False


def main():
    global PID
    step("1. 프로젝트 열기 (VM 디스크 재등록 포함)")
    subprocess.run([sys.executable, REOPEN, PROJECT, "--start"], check=True)
    PID = next(p["project_id"] for p in api("GET", "/projects") if p["name"] == PROJECT)
    agentpc_uplink()
    print("AGENT-PC 인터넷 연결, 에이전트 서비스 일시 정지:", agent_service("stop"))

    step("2. 코어 VLAN 재생성")
    for core in ("CORE1", "CORE2"):
        print(core, "부팅 대기:", wait_ios(core))
        out = console(core, ["enable", "vlan database", "vtp transparent",
                             *[f"vlan {v} name {n}" for v, n in VLANS], "exit", "write memory"])
        print(core, "VLAN 적용:", "APPLY completed" in out)

    step("3. 코어 한 대씩 재부팅 (HSRP 가상 MAC 바로잡기)")
    for core in ("CORE1", "CORE2"):
        nid = nodes()[core]["node_id"]
        api("POST", f"/projects/{PID}/nodes/{nid}/stop")
        time.sleep(5)
        api("POST", f"/projects/{PID}/nodes/{nid}/start")
        print(core, "재부팅 후 부팅:", wait_ios(core))
        time.sleep(40)   # HSRP preempt, STP 수렴

    step("4. MAC 테이블 정리, 관리망 VM 포트 재시작")
    for sw in ("CORE1", "CORE2", "ASW1", "ASW2"):
        console(sw, ["enable", "clear mac-address-table dynamic"], wait=10)
        print(sw, "정리")
    # VM 링크가 붙은 NM-16ESW 포트가 프레임을 받지 않는 경우가 있어 한 번 내렸다 올린다 (ADMIN-PC, AGENT-PC)
    for sw, port in (("ASW1", "FastEthernet1/9"), ("ASW2", "FastEthernet1/8")):
        console(sw, ["enable", "configure terminal", f"interface {port}", "shutdown", "no shutdown", "end"], wait=10)
        print(sw, port, "재시작")

    step("5. 방화벽 부팅 대기 후 에이전트 서비스 시작")
    print("FW 부팅 완료:", wait_pfsense())   # FW가 늦게 뜨면 '접속 불가' 경보가 에이전트 분석으로 이어지므로 기다린다
    print("시작:", agent_service("start"))

    step("6. 확인")
    for core in ("CORE1", "CORE2"):
        out = console(core, ["terminal length 0", "show standby brief"], wait=10)
        out = out[out.rfind("show standby brief"):]   # 콘솔이 다시 보내 주는 이전 기록은 버린다
        print(core, " ".join(re.findall(r"Vl\d+\s+\d+\s+\d+\s+P?\s*(?:Active|Standby)", out)) or "HSRP 확인 필요")
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/state", timeout=5) as r:
                json.load(r)
            print("대시보드: http://127.0.0.1:8080  /  관리자 PC(ADMIN-PC)에 로그인하면 자동으로 열립니다.")
            break
        except OSError:
            time.sleep(10)
    else:
        print("대시보드 응답 없음: AGENT-PC 부팅 상태를 확인하세요.")


if __name__ == "__main__":
    main()
