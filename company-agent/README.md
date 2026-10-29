# 랩컴퍼니 IT 운영 에이전트

GNS3 랩(`HSRP-FW-LAB`)을 작은 회사 인프라로 보고, 관리자 PC(AGENT-PC)에 설치한 Claude 기반 운영 에이전트입니다.
에이전트는 네트워크·보안 장비를 직접 조회하고, 변경이 필요하면 **관리자 승인을 받은 뒤에만** 적용합니다.
자동 감시 기능이 포트 스캔, HSRP 전환, 장비 장애, ISP 회선 장애를 탐지하면 에이전트가 원인을 분석합니다.

```
              인터넷 (Claude API)
                    │ VirtualBox NAT  ── 이 PC 브라우저: http://127.0.0.1:8080
            ┌───────┴────────┐
            │ AGENT-PC       │ Rocky Linux · 관리망 VLAN40 · 10.0.40.50
            │ 에이전트 + 대시보드│
            └───────┬────────┘
                    │ telnet(관리 계정, MGMT-ONLY ACL) / SSH 키
   ┌────────┬───────┴─┬─────────┬──────────┐
 CORE1/2  ASW1/2   BRANCH   pfSense FW   (조회·승인된 변경)
```

## 관리자 PC (ADMIN-PC)

시연은 랩 안의 **ADMIN-PC**(Windows 10, 관리망 VLAN40, IP는 코어 DHCP로 자동 할당)에서 합니다.
GNS3에서 ADMIN-PC 콘솔을 열고 로그인하면, 대시보드(`http://10.0.40.50:8080`)가 Edge 앱 창으로 자동으로 열립니다.
바탕화면의 **IT 운영 에이전트** 바로가기로 다시 열 수도 있습니다.
"관리자가 사내 PC에서 관리 서버(AGENT-PC)의 운영 도구에 접속한다"는 구조입니다.
이 PC(호스트) 브라우저의 http://127.0.0.1:8080 도 같은 화면입니다(준비·점검용).

관리망 DHCP는 CORE1(.100~.199)과 CORE2(.200~.250)가 범위를 나눠 맡아서 코어 한 대가 죽어도 할당됩니다.

## 빠른 시작

1. **랩 켜기** (이 PC에서). GNS3가 켜져 있어야 합니다.
   ```bash
   python lab_up.py
   ```
   프로젝트를 열고, 코어 VLAN 복구·코어 재부팅·AGENT-PC 인터넷 연결까지 처리합니다. 약 10~15분 걸립니다.
2. **대시보드 열기**: ADMIN-PC에 로그인하면 자동으로 열립니다 (호스트에서는 http://127.0.0.1:8080)
3. **Claude API 키 등록**: 오른쪽 위 **API 키 설정** 버튼. 키는 [console.anthropic.com](https://console.anthropic.com) → API Keys에서 발급합니다.
   키는 AGENT-PC의 `/opt/lab-agent/secrets/`에만 저장되고 화면·로그에 다시 나오지 않습니다.

## 시연 시나리오

| # | 시나리오 | 하는 방법 | 기대 결과 |
|---|---|---|---|
| 1 | 헬프데스크 진단 | 대시보드의 **헬프데스크: DB 접속 불가** 버튼 | 에이전트가 경로·HSRP·서버팜 ACL을 조회하고 "VLAN20→서버팜은 정책상 차단"이라고 근거와 함께 설명 |
| 2 | 공격 탐지·차단 | kali(ATTACKER)에서 `sudo nmap -Pn --top-ports 40 211.0.1.10 211.0.0.2` | 감시 경보 → 에이전트가 방화벽 로그를 분석하고 차단 승인 요청 → **승인** 클릭 → pfSense에 차단 적용 → 에이전트가 검증 보고. kali에서 `curl http://211.0.1.10`이 막힘 |
| 3 | 장애 감지·보고 | GNS3에서 CORE1 정지 | HSRP 전환 경보(묶음 1건) + 약 45초 뒤 CORE1 장애 경보 → 에이전트가 영향 범위와 현재 우회 경로 보고. CORE1을 켜면 복구·원위치 경보 |

시나리오 2 시연 후에는 대화로 "198.51.100.66 차단 해제해 줘"라고 요청하면 해제 승인 요청을 만듭니다.

## 에이전트가 할 수 있는 일

| 도구 | 종류 | 내용 |
|---|---|---|
| `list_devices`, `get_alerts` | 조회 | 장비 목록·감시 상태, 최근 경보 |
| `ios_show` | 조회 | `show` / `ping` / `traceroute`만 허용. `configure`, `reload`, `debug`, `clear` 등은 거부 |
| `firewall_status`, `firewall_blocked_traffic` | 조회 | pfSense 게이트웨이·룰·차단 목록, 차단 로그를 출발지별 요약 |
| `request_firewall_block` / `unblock` | **변경(승인)** | pfSense WAN 차단 목록(easyrule). 사내·자사 대역(10.0.0.0/15, 211.0.x)은 차단 불가 |
| `request_ios_config` | **변경(승인)** | IOS 설정 + `write memory`. 계정, VTY, 관리 ACL, 부팅 설정은 변경 불가 |

변경 도구는 실행하지 않고 **승인 대기열에만 등록**합니다. 관리자가 대시보드에서 승인해야 실행되고, 실행 결과는 에이전트에게 다시 전달되어 검증합니다. 모든 조회·요청·승인·실행은 감사 로그(`/opt/lab-agent/data/audit.jsonl`)에 남습니다.

## 구성 파일

| 경로 | 내용 |
|---|---|
| `agent/server.py` | 진입점. 대시보드와 API(`/api/state`, `/api/chat`, `/api/approve`, `/api/reject`, `/api/apikey`) |
| `agent/brain.py` | Claude 호출 루프(`claude-opus-5-5`, 서버 측 fallback). 관리자 대화·감시 경보·승인 결과가 한 대화로 이어짐 |
| `agent/tools.py` | 도구 정의, 허용 목록, 승인 실행 |
| `agent/monitor.py` | 자동 감시 (방화벽 10초, HSRP·게이트웨이 20초, 액세스 장비 60초). 장애는 45초 유예, 경보는 15초 묶음 |
| `agent/devices.py` | IOS telnet, pfSense SSH 접속 |
| `agent/static/index.html` | 대시보드 화면 |
| `agent/smoke_test.py` | API 키 없이 도구·안전장치 점검 (AGENT-PC에서 실행) |
| `deploy.py`, `deploy/lab-agent.service` | AGENT-PC로 배포, systemd 서비스 |
| `lab_up.py` | 랩 기동 |
| `keys/`, `secrets.env` | 설치용 SSH 키와 장비 관리 계정. **git에 올리지 마세요**(`.gitignore` 처리됨) |

코드를 고친 뒤에는 `python deploy.py`로 다시 배포합니다(서비스 자동 재시작).

## 알아 둘 점

- **랩 전용 보안 수준입니다.** 장비 관리는 telnet이고, 대시보드에는 로그인이 없습니다(이 PC의 127.0.0.1과 사내 관리망에서만 접근 가능). 실제 환경이라면 SSH, 대시보드 인증, 비밀 관리 도구를 써야 합니다.
- **비용:** 시나리오 1회에 대략 $0.3~0.5(Claude Opus 5.5 기준)입니다. 상단의 `API 사용 ≈ $…` 칩이 응답 usage로 계산한 추정 누적 비용과 캐시 적중률을 보여 줍니다(정확한 금액은 Claude 콘솔). 대화 기록은 자동 캐시되어 이전 기록은 캐시 단가로 읽힙니다.
  시나리오가 끝날 때마다 **새 대화** 버튼으로 대화를 비우면 호출마다 보내는 기록이 짧아져 비용이 줄어듭니다(이전 대화는 `/opt/lab-agent/data/messages-*.json`에 보관).
  감시 경보마다 에이전트가 분석을 돌리므로, 시연하지 않을 때는 랩을 꺼 두세요. 콘솔(Settings → Limits)에서 월 사용 한도를 걸어 두면 안전합니다.
- **GNS3 2.2 + VirtualBox 7 + dynamips의 제약** 때문에 `lab_up.py`가 필요합니다. 프로젝트를 다시 열 때 VM 복제본 디스크 등록이 풀리고, 코어 VLAN이 사라지고, AGENT-PC의 NAT NIC가 덮어써집니다. GNS3에서 직접 열지 말고 `lab_up.py`를 쓰세요.
- **VM 노드에 링크를 연결한 상태에서 어댑터 수를 바꾸지 마세요.** 링크가 실제로는 끊긴 채 남습니다.
