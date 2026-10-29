# GNS3 랩 자동 구축 에이전트

말로 설명한 네트워크 랩을 **기획 에이전트**가 설계하고, 사람이 확인한 뒤 **구축 에이전트**가 GNS3에 실제로 만듭니다.

```
요구사항 → [기획 에이전트] → 설계도(JSON) → [사람 검토/수정 요청] → [구축 에이전트] → 결과 보고
```

| 파일 | 역할 |
|---|---|
| `config.py` | GNS3 주소, 버전, 모델, 비용 한도 설정 |
| `gns3_tools.py` | GNS3 REST API와 장비 콘솔(telnet)을 감싼 도구들 |
| `agents.py` | 기획/구축 에이전트의 프롬프트, 설계도 스키마, 실행 함수 |
| `main.py` | 전체 흐름 실행 (기획 → 검토 → 구축) |
| `plans/` | 기획 결과 설계도가 저장되는 폴더 |

## 준비

1. GNS3 서버가 켜져 있고, 쓸 장비 이미지(예: Cisco IOSv)가 **템플릿으로 등록**되어 있어야 합니다.
2. Python 3.10 이상에서 패키지를 설치합니다.
   ```bash
   pip install -r requirements.txt
   ```
3. Claude API 키를 설정합니다.
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```
4. `config.py`에서 GNS3 주소와 버전을 확인합니다 (기본값: `localhost:3080`, `v2`).

## 실행

```bash
python main.py "Cisco IOSv 라우터 3대를 삼각형으로 연결하고 OSPF area 0 구성. 각 라우터에 루프백 하나씩"
```

1. 기획 에이전트가 설계도를 만들어 요약을 보여 주고, `plans/`에 저장합니다.
2. `y`를 누르면 구축을 시작하고, `n`을 누르면 취소합니다. 고칠 점을 입력하면(예: "링크를 /30으로 바꿔줘") 다시 설계합니다.
3. 구축 에이전트가 진행 상황(🔧 도구 호출)을 출력하고, 마지막에 결과 보고서를 보여 줍니다.

저장된 설계도로 다시 구축할 때는 다음과 같이 실행합니다.

```bash
python main.py --plan plans/20261029-120000-ospf-triangle.json
```

## 구축 에이전트가 쓰는 도구

| 도구 | 하는 일 |
|---|---|
| `list_host_vms` | PC에 설치된 VirtualBox / VMware VM 조회 |
| `register_vm_template` | VM을 GNS3 템플릿으로 등록 (linked clone) |
| `list_templates` | 등록된 장비 템플릿 조회 |
| `create_project` | 프로젝트 생성 (같은 이름이 있으면 열기) |
| `add_node` | 노드 추가, 포트 목록 반환 |
| `add_link` | 노드 이름 + 인터페이스 이름으로 연결 |
| `start_all_nodes` | 모든 노드 시작 |
| `project_status` | 노드 상태, 링크 조회 |
| `wait_for_boot` | 콘솔에 프롬프트가 뜰 때까지 대기 (초기 설정 대화상자 자동 건너뜀) |
| `console_send` | 콘솔에 명령 입력, 출력 반환 (`% Invalid` 오류가 나면 멈춤) |
| `wait_for_guest` | VirtualBox VM의 OS가 부팅되고 Guest Additions가 응답할 때까지 대기 |
| `vm_exec` | VirtualBox VM 안에서 셸 명령 실행 (리눅스: sh, Windows: cmd) |
| `wait_seconds` | 라우팅 수렴 대기 |

구축 에이전트는 이 도구만 쓸 수 있습니다. 셸, 파일 같은 내장 도구는 꺼져 있어서 PC의 다른 것을 건드리지 않습니다.

## PC의 VirtualBox VM 불러오기

실행하면 먼저 GNS3 템플릿과 PC에 설치된 VM 목록을 자동으로 조회해서 기획 에이전트에게 알려 줍니다. 요구사항에 VM을 언급하면 설계에 포함되고, 구축 에이전트가 다음 작업을 진행합니다.

1. VM을 GNS3 템플릿으로 등록합니다. linked clone이라서 **원본 VM은 바뀌지 않습니다**.
2. 노드로 추가하고 라우터와 연결한 뒤 켭니다.
3. **Guest Additions**로 VM 안에서 명령을 실행해 IP, 게이트웨이 같은 설정을 넣습니다. 리눅스와 Windows 모두 됩니다.
4. VM에서 ping으로 통신을 검증합니다.

```bash
python main.py "R1-R2 OSPF 랩을 만들고, R1 뒤에 ubuntu-server VM, R2 뒤에 win10 VM을 붙여서 서로 ping 되게 해줘"
```

### VirtualBox 준비 사항

1. **각 VM에 Guest Additions를 설치합니다.** VirtualBox 메뉴의 장치 → 게스트 확장 CD 이미지 삽입으로 설치할 수 있습니다.
2. **모든 VM에 같은 로그인 계정 `user1` / `1234`를 만들어 둡니다.** 이 값은 `config.py`의 `VM_USERNAME`, `VM_PASSWORD`에 설정되어 있습니다.
   - 리눅스: `user1`이 sudo를 쓸 수 있어야 합니다. sudo 비밀번호는 도구가 자동으로 입력합니다.
   - Windows: `user1`이 Administrators 그룹에 속해 있어야 IP를 바꿀 수 있습니다. UAC 때문에 막히면 보고서에 표시됩니다.
   - 특정 VM만 다른 계정을 쓰려면 `VM_CREDENTIALS`에 추가하면 됩니다.
3. **실행 전에 VM을 꺼 두세요.** 켜져 있으면 GNS3가 복제본을 만들고 연결할 수 없습니다.
4. **GNS3 설정을 확인합니다.** GNS3 Preferences → VirtualBox에 VBoxManage 경로가 잡혀 있어야 VM 목록이 보입니다.
5. **`main.py`는 VirtualBox가 설치된 그 PC에서 실행합니다.** `VBoxManage`를 직접 호출하기 때문입니다. 경로는 자동으로 찾고, 못 찾으면 환경변수 `VBOXMANAGE`로 지정하세요.
6. **Windows 호스트:** Hyper-V(WSL2, Docker Desktop 등)가 켜져 있으면 VirtualBox가 느려지거나 오동작할 수 있습니다.

### VM이 든 프로젝트를 다시 열 때 (GNS3 2.2 + VirtualBox 7)

이 조합에서는 프로젝트를 닫으면 VM 복제본의 디스크 등록이 풀려서, 다시 열 때 `A differencing image of snapshot ... could not be found` 오류가 납니다. GNS3에서 바로 열지 말고 다음 스크립트로 여세요. VM 디스크와 설정은 지우지 않습니다.

```bash
python reopen_project.py "프로젝트 이름" --start
```

- 남아 있는 복제본 등록을 정리하고, 디스크를 다시 등록한 뒤 프로젝트를 엽니다.
- 강제로 꺼져 `aborted` 상태가 된 복제본은 잠깐 켰다 꺼서 GNS3가 시작할 수 있게 만듭니다.
- `--start`를 붙이면 모든 노드를 켭니다.
- VM 노드에 링크를 연결한 상태에서 **어댑터 수를 바꾸지 마세요.** 기존 링크가 실제로는 끊긴 채 남습니다. 바꿨다면 프로젝트를 닫고 이 스크립트로 다시 여세요.

## 알아 둘 점

- **GNS3 3.x:** `config.py`에서 `GNS3_API_VERSION = "v3"`와 로그인 정보를 넣으세요. 3.x 경로는 아직 실제 서버에서 테스트하지 않았습니다.
- **콘솔 프롬프트:** 콘솔 도구는 Cisco IOS 스타일 프롬프트(`R1#`, `R1(config)#`)를 기준으로 만들었습니다. 다른 장비(VyOS, FRR 등)를 쓰면 `gns3_tools.py`의 `PROMPT_RE`를 조정해야 할 수 있습니다.
- **비용 한도:** 구축 1회당 기본 한도는 $10, 최대 200턴입니다 (`config.py`). 구축 에이전트를 `claude-sonnet-5-5`로 바꾸면 비용이 줄어듭니다.
- 이미 만든 프로젝트 이름으로 다시 실행하면 기존 프로젝트를 열고, 이미 있는 노드는 다시 만들지 않습니다.
