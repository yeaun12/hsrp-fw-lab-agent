# Phase 6 — SQL 기반 운영 분석 + KPI 데이터 대시보드

작성일: 2026-10-30 · 대상: 로드맵 Phase 6(P1) / 요청문 §6.3 SQL 운영 분석

## 1. 범위
Phase 5 저장 계층(`ops_dw.sqlite`) 위에서 **SQL 분석용 View**와 운영 KPI를 만들고, 대시보드에
데이터 분석 패널을 붙인다. "쌓는 데서 끝내지 않고 운영 개선에 쓸 수 있는 분석 구조"를 목표로 한다.

## 2. 분석용 View (§6.3 "분석용 View")
`analytics.py`가 생성하는 View:
- `v_incidents_daily` — 기간별(일자별) 장애 발생 횟수
- `v_incident_device` — 장비별 장애 빈도(사건이 건드린 장비 기준, tool_calls distinct)
- `v_events_type`, `v_events_type_daily` — 이벤트 유형별 발생/추이
- `v_security_hour` — 시간대별(0~23시) 보안 이벤트
- `v_approvals` — 승인 수명주기(요청/승인/거부/실행/실패) 집계
- `v_incident_status` — 사건 상태 분포

## 3. KPI (§6.3 분석 항목 → 구현)
`analytics.kpis(conn)`가 한 번에 돌려주는 지표:
| 요청 항목 | 구현 |
|---|---|
| 기간별 장애 발생 횟수 | `v_incidents_daily` |
| 장비별 장애 빈도 | `v_incident_device` |
| 이벤트 유형별 발생 추이 | `v_events_type` (+ daily) |
| 평균 조사·해결 시간 | `AVG(incidents.elapsed_sec)` (+ 최대) |
| 관리자 승인·거부 비율 | `v_approvals` → approve_ratio |
| 반복되는 장애 패턴 | `recurring_devices`(2건 이상 장비) |
| 에이전트 실행 성공률 | resolved / total incidents |
| 시간대별 보안 이벤트 추이 | `v_security_hour` |
| API 호출량·비용 | `model_usage` 최신 스냅샷 |

평균 "장애 탐지 시간"은 감시 폴링 주기(10~20초, `POLL_*`)가 상한을 정하는 상수에 가까워 DB 지표로
두지 않고, 대신 정확히 측정 가능한 조사·해결 시간(elapsed_sec)을 제공한다(정직한 선택).

## 4. 대시보드 + API
- `server.py` `/api/analytics`: 호출 시 최신 로그를 **멱등 적재(Phase 5 파이프라인)** 후 KPI를 JSON으로 반환.
- `static/index.html` "운영 분석 (KPI)" 패널: "분석 새로고침" 버튼 → KPI 카드(사건·성공률·평균 해결·도구
  오류율·API 비용) + 가로 막대(사건 상태, 장비별, 일자별, 이벤트 유형, 시간대별 보안). 외부 차트 라이브러리
  없이 경량 렌더링.

## 5. 파일
- `agent/analytics.py`: View DDL + `kpis()` + CLI(`python analytics.py [--json]`).
- `agent/server.py`: `/api/analytics` 엔드포인트.
- `agent/static/index.html`: 운영 분석 패널 + `loadAnalytics()`.

## 6. 검증 결과 (실데이터, AGENT-PC, 2026-10-30)
- `analytics.py` CLI + `/api/analytics` 모두 실제 `ops_dw.sqlite`로 동작.
- 실측 KPI: 사건 4건(전부 needs_confirm — 장비 다운 조사라 미해결로 집계, 정직), 실행 성공률 0.0,
  도구 오류율 0.345, 평균 조사·해결 10.5s(최대 16s), 장비별 CORE1 2·CORE2 1·BRANCH 1·ASW2 1,
  일자별 2026-10-30:4, 시간대별 보안 9·10·11·17시, API gemini 호출 113·입력 575,450·출력 13,265·$0.
- `/api/analytics` 호출 시 적재 멱등(read 1535, error 0).
- **라이브 대시보드(브라우저)**: "분석 새로고침" → KPI 카드 + 막대(사건상태/장비별/일자별/이벤트유형/시간대별
  보안)가 실데이터로 정상 렌더링 확인.

## 7. 비고
- 성공률 0.0은 버그가 아니라 데이터 반영 결과다. 검증용 사건이 모두 장비 다운 상황이라 '해결'이 아닌
  '확인요청'으로 정직하게 귀결됐기 때문(Phase 4 안전망). 정상 해결 사건이 쌓이면 자연히 올라간다.
- 이벤트 상관분석(여러 경보를 한 사건으로)·통계 이상탐지는 Phase 7~8에서 이 분석 구조 위에 올린다.
