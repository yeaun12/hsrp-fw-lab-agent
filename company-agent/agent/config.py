"""사내 IT 운영 에이전트 설정. 에이전트 PC(/opt/lab-agent)에서 실행됩니다."""
import os

BASE_DIR = os.environ.get("LAB_AGENT_HOME", "/opt/lab-agent")
DATA_DIR = os.path.join(BASE_DIR, "data")          # 대화, 감사 로그
SECRETS_DIR = os.path.join(BASE_DIR, "secrets")    # API 키, 장비 계정 (chmod 700)
SSH_KEY = os.path.join(BASE_DIR, "keys", "id_ed25519")
SSH_KNOWN_HOSTS = os.path.join(BASE_DIR, "keys", "known_hosts")

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_AGENT_STEPS = 20          # 한 번의 대화 턴에서 도구 호출 최대 횟수

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080

# 감시 주기(초)와 포트 스캔 탐지 기준
POLL_FIREWALL_SEC = 10
POLL_NETWORK_SEC = 20
SCAN_WINDOW_SEC = 120
SCAN_MIN_PORTS = 8            # 한 출발지가 창 안에서 막힌 서로 다른 목적지 포트 수

# 관리 대상 장비. host는 관리망(VLAN40)에서 접근하는 주소.
DEVICES = {
    "CORE1":  {"type": "ios", "host": "10.0.40.2",  "role": "본사 코어 L3 스위치 #1 (VLAN10/40/99 HSRP Active)"},
    "CORE2":  {"type": "ios", "host": "10.0.40.3",  "role": "본사 코어 L3 스위치 #2 (VLAN20/30 HSRP Active)"},
    "ASW1":   {"type": "ios", "host": "10.0.40.21", "role": "액세스 스위치 #1 (PC1 VLAN10, PC3 VLAN20)"},
    "ASW2":   {"type": "ios", "host": "10.0.40.22", "role": "액세스 스위치 #2 (PC2 VLAN10, SRV VLAN30, PC4·에이전트PC VLAN40)"},
    "BRANCH": {"type": "ios", "host": "10.1.10.1",  "role": "지사 라우터 (코어와 OSPF 이중 연결)"},
    "FW":     {"type": "pfsense", "host": "10.0.99.1", "role": "pfSense 방화벽 (ISP1/ISP2 이중화, DMZ)"},
}


def read_secret(name, default=None):
    path = os.path.join(SECRETS_DIR, name)
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def write_secret(name, value):
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(SECRETS_DIR, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value.strip())
