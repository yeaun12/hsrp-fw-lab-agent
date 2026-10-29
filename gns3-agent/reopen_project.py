"""VirtualBox VM이 들어 있는 GNS3 프로젝트를 다시 엽니다.

GNS3 2.2 + VirtualBox 7 조합에서는 프로젝트를 닫으면 linked clone의 디스크 등록이 풀려서,
다시 열 때 "A differencing image of snapshot ... could not be found" 오류가 납니다.
이 스크립트는 남아 있는 복제본 등록을 정리하고 디스크를 다시 등록한 뒤 프로젝트를 엽니다.
VM 디스크와 설정은 지우지 않습니다.

    python reopen_project.py "프로젝트 이름" [--start]

외부 패키지 없이 표준 라이브러리만 씁니다. (GNS3 2.x API 기준)
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import config

BASE_URL = f"http://{config.GNS3_HOST}:{config.GNS3_PORT}/{config.GNS3_API_VERSION}"


def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:800].decode(errors='replace')}")


def vbox(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([config.VBOXMANAGE, *args], capture_output=True, text=True, errors="replace")


def vm_state(vm):
    m = re.search(r'VMState="(\w+)"', vbox("showvminfo", vm, "--machinereadable").stdout)
    return m.group(1) if m else "unknown"


def ensure_off(vm):
    """프로젝트를 닫은 뒤에도 꺼지는 중이거나 실행 중인 복제본을 끈다.
    pfSense처럼 'stopping'에서 멈추는 VM이 있어서: 기다림 -> 강제 전원 끔 -> 그 VM 프로세스만 종료 순으로 한다.
    True면 꺼진 상태(poweroff/aborted 등)."""
    for _ in range(12):                                   # 정상 종료 최대 60초 대기
        if vm_state(vm) not in ("running", "stopping", "starting", "paused"):
            return True
        time.sleep(5)
    print(f"{vm}: 60초 동안 꺼지지 않아 강제로 전원을 끕니다.")
    vbox("controlvm", vm, "poweroff")
    time.sleep(10)
    if vm_state(vm) not in ("running", "stopping", "starting", "paused"):
        return True
    if os.name == "nt":                                   # 그래도 멈춰 있으면 이 복제본의 VM 프로세스만 종료
        print(f"{vm}: 응답이 없어 VM 프로세스를 종료합니다.")
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='VirtualBoxVM.exe'\" | "
                        f"Where-Object {{ $_.CommandLine -match '--comment {re.escape(vm)}( |$)' }} | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
        time.sleep(5)
    return vm_state(vm) not in ("running", "stopping", "starting", "paused")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    project = next((p for p in api("GET", "/projects") if p["name"] == args[0]), None)
    if project is None:
        raise SystemExit(f"'{args[0]}' 프로젝트가 없습니다.")
    pid = project["project_id"]
    vbox_root = os.path.join(project["path"], "project-files", "virtualbox")

    # 반쯤 실패한 열기가 일부 복제본을 등록된 채로 남기면 GNS3가 다시 등록하지 못한다.
    if project["status"] == "opened":
        api("POST", f"/projects/{pid}/close")
    registered = vbox("list", "vms").stdout
    for vbox_file in glob.glob(os.path.join(vbox_root, "*", "*", "*.vbox")):
        vm = os.path.splitext(os.path.basename(vbox_file))[0]
        if f'"{vm}"' not in registered:
            continue
        if not ensure_off(vm):
            raise SystemExit(f"VM '{vm}'을 끄지 못했습니다. VirtualBox에서 직접 끈 뒤 다시 실행하세요.")
        r = vbox("unregistervm", vm)  # --delete 없음: 파일은 그대로 둔다
        print(f"남아 있던 등록 해제: {vm}" if r.returncode == 0 else f"등록 해제 실패: {vm}\n{r.stdout}")

    # 디스크 파일을 열면 VirtualBox가 다시 등록한다. 부모 디스크가 먼저 열려야 자식이 열린다.
    vdis = glob.glob(os.path.join(vbox_root, "*", "*", "Snapshots", "*.vdi"))
    pending = vdis
    while pending:
        failed = [v for v in pending if vbox("showmediuminfo", "disk", v).returncode != 0]
        if len(failed) == len(pending):
            break
        pending = failed
    print(f"디스크 재등록: {len(vdis) - len(pending)}/{len(vdis)}")
    for v in pending:
        print(f"  실패: {v}")

    print("프로젝트 열기:", api("POST", f"/projects/{pid}/open")["status"])

    # 강제로 꺼진 VM은 'aborted' 상태라 GNS3가 시작을 거부한다. 잠깐 켰다 꺼서 poweroff로 만든다.
    for node in api("GET", f"/projects/{pid}/nodes"):
        if node["node_type"] != "virtualbox" or not node["properties"].get("linked_clone"):
            continue  # 복제본이 아닌 VM은 PC의 원본 VM이므로 건드리지 않는다
        vm = node["properties"].get("vmname") or node["name"]
        if 'VMState="aborted"' in vbox("showvminfo", vm, "--machinereadable").stdout:
            vbox("startvm", vm, "--type", "headless")
            vbox("controlvm", vm, "poweroff")
            print(f"aborted 상태 정리: {vm}")

    if "--start" in sys.argv:
        api("POST", f"/projects/{pid}/nodes/start")
        print({n["name"]: n["status"] for n in api("GET", f"/projects/{pid}/nodes")})


if __name__ == "__main__":
    main()
