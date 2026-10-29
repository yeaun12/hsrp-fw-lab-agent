"""이 PC(호스트)에서 에이전트 PC로 코드를 배포하고 서비스를 재시작한다.

    python deploy.py [--secrets PATH]

에이전트 PC에는 VirtualBox NAT 포트포워딩(127.0.0.1:2222 -> 22)과 SSH 키로 접속한다.
--secrets 파일(NETOPS_USER=..., NETOPS_PASSWORD=...)을 주면 장비 관리 계정을 에이전트 PC에 저장한다.
비밀번호는 명령줄 인자가 아니라 표준 입력으로 넘긴다.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SSH_KEY = os.environ.get("AGENTPC_KEY", os.path.join(HERE, "keys", "agentpc_key"))
KNOWN = os.path.join(os.path.dirname(SSH_KEY), "agentpc_known_hosts")
SSH = ["-i", SSH_KEY, "-o", f"UserKnownHostsFile={KNOWN}", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes"]


def ssh(script, stdin=None):
    r = subprocess.run(["ssh", "-p", "2222", *SSH, "root@127.0.0.1", script], input=stdin, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"원격 명령 실패: {script[:60]}")


def main():
    subprocess.run(["scp", "-q", "-r", "-P", "2222", *SSH, os.path.join(HERE, "agent"), "root@127.0.0.1:/opt/lab-agent/app.new"],
                   check=True)
    subprocess.run(["scp", "-q", "-P", "2222", *SSH, os.path.join(HERE, "deploy", "lab-agent.service"),
                    "root@127.0.0.1:/etc/systemd/system/lab-agent.service"], check=True)
    if "--secrets" in sys.argv:
        vals = dict(l.strip().split("=", 1) for l in open(sys.argv[sys.argv.index("--secrets") + 1]) if "=" in l)
        ssh("umask 077; mkdir -p /opt/lab-agent/secrets; read -r u; read -r p; "
            "printf %s \"$u\" > /opt/lab-agent/secrets/netops_user; printf %s \"$p\" > /opt/lab-agent/secrets/netops_password",
            stdin=f"{vals['NETOPS_USER']}\n{vals['NETOPS_PASSWORD']}\n")
        print("장비 관리 계정 저장 완료")
    ssh("set -e; rm -rf /opt/lab-agent/app; mv /opt/lab-agent/app.new /opt/lab-agent/app; "
        "mkdir -p /opt/lab-agent/data; chmod 700 /opt/lab-agent/secrets 2>/dev/null || true; "
        "firewall-cmd -q --add-port=8080/tcp --permanent; firewall-cmd -q --reload; "
        "systemctl daemon-reload; systemctl enable -q lab-agent; systemctl restart lab-agent; sleep 2; "
        "systemctl is-active lab-agent")


if __name__ == "__main__":
    main()
