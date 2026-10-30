# Phase 5 — 운영 데이터 파이프라인 (수집·정규화·검증·저장)

작성일: 2026-10-30 · 대상: 로드맵 Phase 5(P1) / 요청문 §6.1 통합 파이프라인, §6.2 스키마·검증 기준

## 1. 범위와 기술 선택
에이전트가 남긴 로그/기록을 **수집 → 파싱·정규화 → 검증 → 저장**하는 파이프라인. 상관분석·이상탐지·
SQL 분석(Phase 6~8)은 이 저장 계층 위에서 따로 한다. 요청대로 Kafka/Spark 없이 **Python + SQLite 경량 구성**,
저장 계층을 모듈(`warehouse.py`)로 분리해 나중에 PostgreSQL로 옮길 수 있게 했다. 기존 `audit.jsonl`·대화·
`cases.json`은 보존하고 그대로 소스로 연결한다.

## 2. 소스 → 정제 스키마 (§6.2)
원본 landing과 정제 테이블을 분리한다(원본/정제 분리, 출처 추적).
- `raw_events` — 모든 원본 레코드의 landing. `content_hash` UNIQUE로 **중복 적재 방지**, `source`·`line_no`로 출처 추적.
- `events` — 네트워크·보안 이벤트·장비 상태·감사·대화를 통합(category/event_type/severity/correlation_id). 소스: audit.jsonl, chat.jsonl.
- `incidents` — 장애 사건 + AI 실행 기록(상태·결론·모델·토큰·근거). 소스: cases.json.
- `tool_calls` — 도구 호출 기록(사건별 순서·장비·오류). 소스: cases.json tools[].
- `model_usage` — AI 모델·비용 기록. 소스: usage.json.
- `ingest_runs` / `ingest_errors` — 적재 실행 통계와 **수집 실패 격리**(누락·오류 데이터).

공통 키: event_id, occurred_at(발생), ingested_at(수집), device_id, event_type, severity, correlation_id,
그리고 모든 정제 행의 `raw_id`(원본 역추적).

## 3. 검증 기준 구현 (§6.2)
| 기준 | 구현 |
|---|---|
| 중복 적재 방지 | 원본 줄/레코드 `content_hash` UNIQUE + INSERT OR IGNORE |
| 누락·오류 처리 | 파싱/정규화 실패 → `ingest_errors`로 격리(사유·원본 보존) |
| 시간·타임존 통일 | naive 로컬시각 → ISO8601 + 고정 오프셋(`OPS_TZ`, 기본 +09:00) |
| 데이터 정합성 | 품질 리포트: null 시간, 고아 tool_calls, 중복 event_key 점검 |
| 재처리·과거 재적재 | 멱등 재실행(줄/사건 해시 기준). 바뀐 사건만 업서트 |
| 수집 실패 기록 | `ingest_runs`(읽음/신규/중복/오류), `ingest_errors` |
| 출처 추적(lineage) | 정제 행마다 `raw_id` → `raw_events`(source·line) |
| 민감정보 마스킹 | API 키 패턴(sk-ant-/AIza/AQ.) → `***MASKED***` (원본·정제 모두) |
| 보존 기간·삭제 | `retention(days)` — 기간 지난 원본 정리(정제는 보존) |

## 4. 파일
- `agent/warehouse.py`: 스키마 DDL + ETL(소스별 정규화 핸들러) + 품질 리포트 + 보존. CLI:
  `python warehouse.py [--quality] [--retention DAYS]`.
- `agent/warehouse_test.py`: ETL·데이터 품질 테스트(합성 데이터, 장비 불필요).

## 5. 검증 결과 (2026-10-30)
- **단위 테스트(합성 데이터)**: 로컬 + AGENT-PC 양쪽 통과. 1차 적재(정상 6·격리 2) → 2차 재적재(신규 0·중복 8·오류 0,
  **멱등**) → 품질 OK(null시간/고아/중복키/미마스킹/비ISO 모두 0) → 마스킹 실제 확인 → 격리 2건 → 정합성·lineage·보존 확인.
- **실데이터(AGENT-PC `/opt/lab-agent/data`)**: 읽음 1519, 오류 0, **품질 OK**.
  events 1499 · incidents 4 · tool_calls 58 · raw_events 1517. 정합성 지표 전부 0(null시간/고아/중복키/미마스킹/비ISO).
  2차 적재 시 **신규 0 · 중복 1519 → 완전 멱등**.

## 6. 다음 단계 연결
- Phase 6: 이 저장 계층(`events`/`incidents`/`tool_calls`/`model_usage`) 위에서 SQL 운영 분석·KPI·데이터 대시보드.
- Phase 7~8: `correlation_id`를 활용한 이벤트 상관분석, 통계·ML 이상 탐지.
- 비고: `ops_dw.sqlite`는 서버 데이터 디렉터리에 생성(저장소에는 코드만). 상관관계 심화는 Phase 7에서 보강.
