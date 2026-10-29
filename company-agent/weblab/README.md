# 웹 공격 탐지 시연 (DMZ)

격리된 랩의 DMZ 웹 서버(WEB, 211.0.1.10)에 **일부러 취약하게 만든 테스트 포털**을 올리고, 외부 공격자(kali)가 SQL 인젝션·XSS를 시도하면 에이전트가 웹 로그를 분석해 탐지·판정하는 흐름입니다. 모두 격리 랩 안에서 우리 서버만 대상으로 하는 교육용입니다.

```
kali(198.51.100.66) ──80──> WEB(211.0.1.10) 테스트 포털  → /var/log/labapp/access.log
                                                              │ SSH(내부→DMZ, 상태 기반 허용)
                                              AGENT-PC ───────┘  web_detect.py 규칙 탐지 → 경보 → AI 판정
```

## 파일
| 파일 | 위치 | 내용 |
|---|---|---|
| `webapp.py` | WEB `/opt/labapp/` | 테스트 포털(로그인·검색). SQLi/XSS 성립, 요청마다 JSON 접속 로그 |
| `web_bootstrap.sh` | WEB 콘솔에서 1회 | sshd 켜기 + 에이전트 공개키 등록 + 디렉터리 생성 |
| `deploy_webapp.sh` | AGENT-PC에서 | webapp.py를 WEB에 올리고 systemd 서비스로 기동(httpd 중지, :80) |
| `gen_traffic.py` | kali `/tmp/` | 정상 요청 + 교과서 공격 시그니처를 섞어 보내 탐지를 검증 |
| `agent/web_detect.py` | AGENT-PC | 로그 수집(SSH) + SQLi·XSS 규칙 탐지 + 성공 가능성 추정 |

## 설치 (랩이 켜진 상태)
1. **WEB 준비** — GNS3에서 WEB 콘솔을 열어 root 로그인 후 `web_bootstrap.sh` 내용을 실행(에이전트 공개키는 파일에 포함).
2. **에이전트 경로** — AGENT-PC는 관리망만 보므로 DMZ 경로가 필요합니다. `lab-build/agentpc_setup.sh`에 `211.0.1.0/28` 경로가 들어 있어, 에이전트를 그 스크립트로 설정하면 자동 적용됩니다. 이미 만든 에이전트라면:
   ```
   nmcli con mod lab +ipv4.routes "211.0.1.0/28 10.0.40.254"; nmcli con up lab
   ```
3. **앱 배포** — AGENT-PC에서 `/tmp/webapp.py`에 webapp.py를 두고 `deploy_webapp.sh` 실행.

## 시연
- kali(hacker1)에서 `python3 /tmp/gen_traffic.py http://211.0.1.10` 실행 → 정상 요청 사이에 공격 시그니처가 섞여 들어갑니다.
- 대시보드 **웹 공격 탐지(DMZ)** 패널에 출발지·시도 건수·성공 추정이 뜨고, 자동 경보가 올라옵니다(규칙만, API 키 불필요).
- **AI 분석** 버튼(키 필요)을 누르면 에이전트가 성공 여부를 판정하고, 앞선 포트 스캔과 엮어 하나의 사건으로 보고하며, 차단 승인과 **웹 앱 수정 필요**를 분리해 안내합니다.

## 탐지가 구분하는 것 (첫 시연 기준)
| 공격 | 앱 반응 | 판정 |
|---|---|---|
| `' UNION SELECT user,pass FROM users --` | rows=8 (계정 유출) | 성공 |
| 로그인 `admin' --` | login_success role=administrator | 인증 우회 성공 |
| `' OR '1'='1` | rows=5 | 성공 |
| `<script>alert(1)</script>` | 응답에 반사 | XSS 성립 |
| `x'; DROP TABLE products --` | sqlerror (sqlite가 스택 쿼리 거부) | 시도 실패, 취약점은 존재 |
| 이중 인코딩 `%2527...` | 디코딩 안 됨, rows=0 | 미성공 |

## 주의
- **교육용·격리 랩 전용입니다.** webapp.py는 의도적으로 취약하며, 실제 서비스나 외부에 노출하면 안 됩니다.
- 방화벽 차단은 공격 출발지를 막을 뿐, 앱의 인젝션·XSS 취약점 자체는 막지 못합니다. 근본 조치는 WEB 앱 수정(파라미터 바인딩, 출력 이스케이프)입니다.
