# Phase 0 — 기존 프로젝트 점검 (2026-10-30)

고도화 로드맵 시작 전, 현재 코드·환경·검증 상태를 정리한다. 이후 모든 Phase는 이 기준에서 출발한다.
백업: 태그 `pre-portfolio-upgrade`(= `e063716`), 작업 브랜치 `feature/portfolio-upgrade`.

## git 상태
- `main`(06aedd2, 태그 agent-v1) ← `feature/kisa-network-audit`(de48f40) ← `feature/web-attack-detection`(e063716)
- 두 기능 브랜치는 **main에 병합 안 됨**. 작업 브랜치는 모든 기능이 있는 web-attack-detection 끝에서 분기.
- 미커밋 변경 0건.

## 1. 실제 구현된 기능 (동작 확인됨)
| 영역 | 내용 | 검증 |
|---|---|---|
| 네트워크 조회 | IOS 5대 telnet 접속, show/ping/traceroute (`devices.py`, `tools.ios_show`) | 실장비 확인 |
| 방화벽 | pfSense SSH, 게이트웨이·룰·차단목록 조회, 차단 로그 요약 (`tools.firewall_*`) | 실장비 확인 |
| 자동 감시 | 포트 스캔·HSRP 전환·장비 장애·ISP 장애·웹 공격 (`monitor.py`) | 실장비 확인 |
| 승인 기반 변경 | 방화벽 차단/해제, IOS 설정 변경 — 승인 후 실행·재검증 (`server.py`, `tools.execute_approved`) | 차단/해제 실장비 확인 |
| 보안 점검 | KISA 네트워크 장비 N-01~38 규칙 점검 (`audit.py`) | 실장비 규칙 판정 확인 |
| 웹 공격 탐지 | DMZ 웹 로그 SQLi·XSS 규칙 탐지, 성공 추정 (`web_detect.py`) | 실제 공격 트래픽으로 확인 |
| 대시보드 | 대화·승인·상태·경보·감사·보안점검·웹공격 (`static/index.html`) | 브라우저 확인 |
| 감사 로그 | 조회·요청·승인·실행을 `audit.jsonl`에 기록 (`state.py`) | 파일 확인 |
| 안전장치 | 조회/변경 도구 허용목록, 내부IP 차단 금지, 관리계정·VTY 변경 금지 | 12종 시험 통과 |
| 비용 | 대화 기록 자동 캐시, 사용량·추정비용 표시, 새 대화 | 계산식 확인(실호출 미검증) |

## 2. 아직 구현되지 않은 기능 (로드맵 대상)
- 모델 제공자 추상화(어댑터), Mock 모델, 무료 모델(Gemini/Groq) — **Phase 1**
- 에이전트 실행 과정 시각화(도구 호출 타임라인, 원인 후보·근거) — Phase 2
- 사건(incident) 관리, 변경 전후 비교·롤백 — Phase 4
- 데이터 파이프라인(수집→정규화→SQLite 저장→View), SQL 운영 분석 — Phase 5~6
- 에이전트 평가 세트(20~30 케이스), 수동/규칙/AI 자동화 효과 비교 — Phase 7
- 통계 기준선 이상 탐지(ML은 데이터 부족으로 보류) — Phase 8
- 대시보드 운영 콘솔화, 보안(대시보드 인증 등) — Phase 9, 10

## 3. 구현했지만 검증되지 않은 기능
- **AI의 자율 판단 전체** — tool-use 루프(`brain.py`)는 구현돼 있으나 LLM API 미연결로 실제 조사·판단·보고·사건연결을 돌려본 적 없음. **가장 큰 미검증 항목.**
- 서버 측 refusal fallback, 대화 캐시 적중률, 토큰·비용 실측 — 실호출 전까지 미검증.

## 4. 수정이 필요한 코드
- `brain.py` — Anthropic에 직접 결합(`anthropic.Anthropic`, `beta.messages.create`, `resp.to_dict()`, Anthropic 전용 파라미터). 제공자 추상화의 핵심 수정 지점.
- `tools.TOOLS` — Anthropic 스키마(`input_schema`) 고정. 제공자별 function-calling 스키마로 변환하는 어댑터 필요.
- `config.py` — `anthropic_api_key` 단일 키 전제. 제공자·모델 선택, 예산 한도 설정 추가 필요.
- 데이터 저장 — `audit.jsonl`/`chat.jsonl`/`audits/*.json` 분산. Phase 5에서 통합 스키마로 연결(기존 파일 보존).

## 5. 재사용 가능한 코드 (그대로 활용)
- `devices.py`(telnet/SSH), `audit.py`·`web_detect.py`(규칙 탐지), `monitor.py`(감시), `state.py`(감사·대화), 승인 흐름(`server.py`), 대시보드 틀(`index.html`), `lab_up.py`/`deploy.py`/`backup_configs.py`(운영 스크립트). 도구 함수(`tools.run_tool`)도 제공자 무관하게 재사용.

## 6. 기존 테스트 현황
- 도구·안전장치: `agent/smoke_test.py` (12종, 통과).
- 규칙 점검: 백업 설정 대상 오프라인 검증 + 실장비 점검(취약 93/상 30 등).
- 웹 탐지: 실제 공격 트래픽으로 end-to-end 확인.
- **자동화된 회귀 테스트 프레임워크는 없음** — 산발적 스크립트 위주. Phase 7/9에서 체계화 필요.
- 데이터 규모(솔직히 작음): `audit.jsonl` 127줄, `chat.jsonl` 61줄, 점검 2건.

## 7. 개선 시 위험
- `brain.py` 리팩터링이 승인 실행·감사 흐름을 건드릴 수 있음 → Mock으로 회귀 먼저.
- 무료 모델의 도구 호출 품질이 낮아 조사 정확도가 Claude보다 떨어질 수 있음(평가에서 솔직히 드러낼 부분).
- 호스트 RAM 여유 적음 → 무거운 구성(Kafka/Spark/대형 ML) 금지. SQLite·통계로 경량 유지.
- 데이터가 적어 ML·고급 분석은 과설계 위험 → 통계 기준선 우선.
- GNS3+VBox 간헐 문제(FW 멈춤 등)는 인프라 이슈로 분리 관리.

## 8. 추천 최종 아키텍처
```
[수집] 장비·방화벽·웹·에이전트 로그
   │  (기존 jsonl 보존 + 신규 정규화)
   ▼
[저장] SQLite (events / incidents / agent_runs / tool_calls / approvals / changes / costs)  ← 저장계층 분리(후에 PostgreSQL)
   │
   ├─[규칙] 감시·탐지·점검 (monitor, audit, web_detect)  ← 빠르고 저렴, 1차 판정
   │
   ├─[AI] 제공자 어댑터(Mock / Gemini / Groq / Claude) → tool-use 루프(brain)
   │        └ 수동확인·모호한 판단·사건 설명·조사 경로 결정
   │
   ├─[통제] 승인 → 실행 전 검증 → 실행 → 전후 비교 → 롤백 → 감사
   │
   └─[분석] SQL View·KPI, 자동화 효과(수동/규칙/AI 비교), 통계 이상탐지
   ▼
[대시보드] 운영현황 · AI조사 · 사건관리 · 데이터분석 · 변경관리 · 성능
```
원칙: 규칙으로 되는 건 규칙, 판단·설명·모호함만 AI. 모든 변경은 승인 후 도구 실행 계층에서 강제. 제공자 교체 가능. 실측과 시연 데이터 구분.

## 다음 단계
Phase 1: `providers/` 어댑터 계층 + **Mock 모델**(키 없이 tool-use 루프 검증) → **Gemini** 어댑터. `brain.py`를 제공자 무관 오케스트레이션으로 리팩터링하되 승인·감사·도구는 그대로 유지. 관리자 작업: Google AI Studio 무료 키 발급(그 시점에 안내).
