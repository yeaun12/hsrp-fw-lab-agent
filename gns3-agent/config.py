"""환경에 맞게 이 파일만 고치면 됩니다. 환경변수로도 덮어쓸 수 있습니다."""
import os

# GNS3 서버
GNS3_HOST = os.getenv("GNS3_HOST", "localhost")
GNS3_PORT = int(os.getenv("GNS3_PORT", "3080"))
GNS3_API_VERSION = os.getenv("GNS3_API_VERSION", "v2")  # GNS3 2.x: "v2", 3.x: "v3"

# GNS3 3.x 전용 로그인 정보 (2.x는 비워 두면 됨)
GNS3_USER = os.getenv("GNS3_USER", "")
GNS3_PASSWORD = os.getenv("GNS3_PASSWORD", "")

# VirtualBox: VBoxManage 실행 파일 경로 (PATH에 없으면 OS별 기본 설치 경로를 씀)
def _find_vboxmanage() -> str:
    import shutil
    for cand in (os.getenv("VBOXMANAGE"), shutil.which("VBoxManage"), shutil.which("vboxmanage"),
                 r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
                 "/Applications/VirtualBox.app/Contents/MacOS/VBoxManage", "/usr/bin/VBoxManage"):
        if cand and os.path.exists(cand):
            return cand
    return "VBoxManage"


VBOXMANAGE = _find_vboxmanage()

# VM 로그인 계정 (VM 안의 OS에 로그인하는 계정. 회원가입 계정이 아님)
# 모든 VM이 같은 계정을 씁니다. 리눅스 VM에서 sudo 비밀번호도 이 비밀번호로 자동 입력합니다.
# Windows VM에서는 이 계정이 관리자 그룹에 속해 있어야 IP를 바꿀 수 있습니다.
VM_USERNAME = os.getenv("VM_USERNAME", "user1")
VM_PASSWORD = os.getenv("VM_PASSWORD", "1234")

# 특정 VM만 다른 계정을 써야 하면 여기에 추가: {"VirtualBox 원본 VM 이름": ("사용자", "비밀번호")}
VM_CREDENTIALS: dict[str, tuple[str, str]] = {}

# 콘솔(telnet) 대기 시간
BOOT_TIMEOUT_SEC = int(os.getenv("BOOT_TIMEOUT_SEC", "300"))   # 장비 부팅 대기 최대 시간
COMMAND_TIMEOUT_SEC = int(os.getenv("COMMAND_TIMEOUT_SEC", "15"))  # 명령 하나당 응답 대기

# 에이전트 모델 (구축 에이전트를 "claude-sonnet-5-5"로 바꾸면 비용이 줄어듭니다)
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "claude-opus-5-5")
BUILDER_MODEL = os.getenv("BUILDER_MODEL", "claude-opus-5-5")

# 안전장치: 구축 에이전트 1회 실행의 최대 턴 수와 비용 한도(USD)
BUILDER_MAX_TURNS = int(os.getenv("BUILDER_MAX_TURNS", "200"))
BUILDER_MAX_BUDGET_USD = float(os.getenv("BUILDER_MAX_BUDGET_USD", "10"))
