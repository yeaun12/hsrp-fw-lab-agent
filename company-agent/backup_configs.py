"""랩 장비 설정을 백업한다 (이 PC에서 실행, 랩이 켜져 있어야 함).

    py -3 backup_configs.py

backups/<날짜-시각>/ 아래에 IOS 장비 7대의 running-config(GNS3 콘솔로 수집)와
pfSense config.xml(AGENT-PC를 거쳐 SSH로 수집)을 저장한다.
"""
import os
import re
import subprocess
import time

import lab_up

HERE = os.path.dirname(os.path.abspath(__file__))
IOS = ["ISP1", "ISP2", "CORE1", "CORE2", "ASW1", "ASW2", "BRANCH"]


def main():
    out_dir = os.path.join(HERE, "backups", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir)
    lab_up.PID = next(p["project_id"] for p in lab_up.api("GET", "/projects") if p["name"] == lab_up.PROJECT)
    for dev in IOS:
        # 첫 줄은 콘솔을 깨우는 빈 줄 (없으면 enable이 접속 직후 출력에 묻혀 수집이 비는 경우가 있음)
        raw = lab_up.console(dev, ["", "enable", "terminal length 0", "show running-config"], wait=40)
        cfg = raw[raw.rfind("show running-config"):]
        cfg = cfg[cfg.find("Building configuration"):] if "Building configuration" in cfg else cfg
        cfg = re.sub(r"\r", "", cfg).rsplit("\nend", 1)[0] + "\nend\n"
        with open(os.path.join(out_dir, f"{dev}.cfg"), "w", encoding="utf-8") as f:
            f.write(cfg)
        print(f"{dev}: {cfg.count(chr(10))}줄")
    key = os.path.join(HERE, "keys", "agentpc_key")
    known = os.path.join(HERE, "keys", "agentpc_known_hosts")
    r = subprocess.run(["ssh", "-p", "2222", "-i", key, "-o", f"UserKnownHostsFile={known}", "-o", "BatchMode=yes",
                        "root@127.0.0.1",
                        "ssh -i /opt/lab-agent/keys/id_ed25519 -o UserKnownHostsFile=/opt/lab-agent/keys/known_hosts "
                        "-o BatchMode=yes admin@10.0.99.1 cat /cf/conf/config.xml"], capture_output=True, text=True)
    if r.returncode == 0 and "<pfsense>" in r.stdout:
        with open(os.path.join(out_dir, "FW-pfsense-config.xml"), "w", encoding="utf-8") as f:
            f.write(r.stdout)
        print(f"FW: config.xml {len(r.stdout)} bytes")
    else:
        print("FW: 수집 실패", r.stderr.strip()[-200:])
    print("저장 위치:", out_dir)


if __name__ == "__main__":
    main()
