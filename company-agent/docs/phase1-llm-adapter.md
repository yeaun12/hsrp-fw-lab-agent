# Phase 1 — LLM 제공자 어댑터 + Mock (2026-10-30)

목표: 특정 LLM에 종속되지 않도록 제공자 어댑터 계층을 만들고, **키 없이도** 도구 호출 루프·승인·감사·추적을 검증할 수 있게 한다. 무료 모델(Gemini) 우선, 유료(Claude)는 선택.

## 변경 사항
- `agent/providers/` 신설
  - `base.py` — 표준 대화/응답 형식, `Provider`·`Response` 인터페이스
  - `mock.py` — 키 불필요 가짜 모델(키워드→정해진 도구 흐름). 실제 추론 아님을 응답에 명시
  - `gemini.py` — Google Gemini REST를 **표준 라이브러리 urllib**로 호출(추가 의존성 없음), 재시도·오류 처리
  - `anthropic_provider.py` — 기존 Claude 호출을 어댑터로 이전(동작 유지)
  - `__init__.py` — 활성 제공자 팩토리, 대시보드용 정보
- `brain.py` — Anthropic 직접 결합 제거, 제공자 무관 루프로 리팩터링. **승인·감사·도구 실행은 그대로**. 예산 상한(`RUN_BUDGET_USD`)과 직전 동일 호출 반복 방지 추가
- `config.py` — 제공자/모델/키/예산 설정, `active_provider()`·`provider_key()`
- `server.py`·`index.html` — 모델 설정 대화상자(제공자 선택 + 키), 상태에 활성 제공자·모델 표시
- 기본 제공자 = **mock** (키 없이 바로 동작)

## 검증 결과
| 항목 | 방법 | 결과 |
|---|---|---|
| Mock 전체 루프 | 대시보드 /api/chat에 헬프데스크 질문 | admin→`list_devices`→`ios_show CORE2`(실제 telnet)→ACL 근거로 응답. 비용 $0, 키 불필요 — **통과** |
| Mock 자율 흐름 | 감시 경보(웹 공격) 자동 분석 | `web_attack_scan`→`get_alerts`→응답 자동 수행 — **통과** |
| 제공자 전환/키 처리 | anthropic·gemini 키 없이 조회 | 각각 `ready=False`로 안전하게 차단, 안내 메시지 — **통과** |
| Gemini 변환 | canonical→contents/tools 단위 | 역할 user/model, functionCall·functionResponse, 도구 12개 변환 — **통과** |
| 대시보드 | 제공자 드롭다운·키 입력·비용 | Mock/Gemini/Claude 선택, Gemini 키 입력 표시, "$0 · Mock" — **통과** |

## 미검증 (관리자 작업 필요)
- **Gemini 실제 호출**: 어댑터·변환은 검증했으나 실제 HTTP 응답은 무료 키가 있어야 확인 가능.
  - 발급: aistudio.google.com/apikey (무료) → 대시보드 "모델 설정"에서 Gemini 선택 + 키 등록.
  - 그 뒤 헬프데스크/웹공격/보안점검 시나리오로 **실제 AI 판단 품질**을 검증(Phase 3).
- Anthropic 실제 호출: 유료 키 등록 시 동일하게 검증 가능(기존 코드 유지).

## 발견·수정한 문제
- Mock 단계 카운터가 대화 전체의 도구 결과를 세어, 앞선 흐름이 있으면 다음 요청이 도구를 건너뜀 → 현재 요청(마지막 사용자 텍스트) 이후만 세도록 수정.
- `config.MODEL` 제거로 server.py가 깨지는 지점을 제공자 정보로 교체.

## 다음
- Phase 2: 에이전트 실행 과정 시각화(도구 타임라인, 원인 후보·근거, 소요·사용량) — 지금도 chat에 도구 호출/결과가 남지만, 전용 "조사 과정" 보기로 정리.
- Phase 3: 무료 모델(Gemini) 연결 후 기존 시나리오로 실제 AI 판단 검증.
