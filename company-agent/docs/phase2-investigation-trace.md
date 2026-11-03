# Phase 2 — 자율 장애 조사 + 판단 근거·도구 실행 기록 (조사 기록)

작성일: 2026-10-30 · 대상: 로드맵 Phase 2(P0) / 요청문 4.3 "에이전트 실행 과정 시각화"

## 1. 목적
관리자가 **AI가 어떤 근거로 판단했는지 검증**할 수 있도록, 조사 한 건(관리자 요청 또는 감시 경보)을
조사→판단→승인→실행→재검증까지 묶어 **관측 가능한 사실**로 구조화해 대시보드에 표시한다.
핵심 제약(요청문): 모델의 비공개 내부 추론은 노출하지 않고, 실제 도구 사용 기록과 관측 가능한 근거만 표시.

- **구현완료**: 조사 건(case) 자료구조 + 자동 수집 + 대시보드 패널.
- **검증완료**: Mock·Gemini 실호출로 필드가 채워지고 대시보드가 렌더링됨(아래 4).

## 2. 설계 — 기계적 사실은 자동, 판단은 선언
요청문 4.3의 13개 표시 항목을 두 갈래로 나눠 구현했다.

- **자동 수집(모델이 못 꾸미는 관측 사실)**: 조사 시작 시간, 사용 모델, 호출한 도구, 조회한 장비,
  반환 핵심정보 요약, 조사 단계 수, 총 소요 시간, 토큰 사용량, 승인 요청/실행 결과/재검증 연결, 현재 상태.
  → `brain._run_turn`이 도구 루프를 돌며 `state.cases`에 적재. 승인은 `state.add_approval`에서 조사 건에 연결,
  실행 결과는 `server._decide`에서 연결.
- **에이전트 선언(관측 근거 기반 판단)**: 원인 후보와 각 후보의 확인 상태(확인됨/배제됨/의심), 최종 판단 근거,
  조치 제안. → 조사 종료 직전 에이전트가 `report_investigation` 도구로 명시 기록(장비를 바꾸지 않는 기록 전용).
  미호출 시 최종 답변 텍스트로 근거를 폴백한다.

조사 건 상태 전이: `investigating → awaiting_approval(승인 요청 시) → verifying(승인 실행 후) → resolved`.

## 3. 변경 파일
- `agent/state.py`: `cases`/`active_case`, `open_case/current_case/save_cases/load_cases`,
  승인↔조사건 연결, `snapshot`에 cases 포함(파일 `data/cases.json`에 영속).
- `agent/brain.py`: 큐가 `(text, source)` 전달. `_run_turn`이 조사 건을 열고 기계적 필드를 적재,
  `_finalize_case`가 상태 정리. 시스템 프롬프트에 report_investigation 지침 추가.
- `agent/tools.py`: `report_investigation` 도구(원인 후보+상태·근거·조치, required) 정의/핸들러.
- `agent/server.py`: 승인 실행 결과를 조사 건에 연결, verifying 전환.
- `agent/static/index.html`: "조사 기록 (AI 판단 근거)" 패널 + `renderCases`(13개 필드, 접이식 도구 기록,
  원인 후보 상태 배지, 상태별 테두리색).

## 4. 검증 결과 (실측, 2026-10-30)
- **Mock**: 헬프데스크 질의 → 조사 건 생성. model=mock-1, steps=3, elapsed=3s, tokens in=600/out=200,
  tools=[list_devices, ios_show(CORE2)], devices=[CORE2], 상태 resolved, 근거는 폴백으로 채워짐. 플러밍 정상.
- **Gemini(gemini-3.5-flash-lite)**: 새 대화로 헬프데스크 질의 → 6~7단계 실조사
  (list_devices→ios_show CORE2/CORE1→get_alerts→report_investigation), elapsed 14~21s, 비용 $0.
  report_investigation 호출됨, **basis(최종 판단 근거) 정상 기록**. 상태 resolved.
- **대시보드**: 패널이 조사 시작시간·모델·단계·소요·토큰·조회장비·도구기록·원인후보(확인됨/배제됨 배지)·
  근거·조치 제안을 모두 정상 렌더링(브라우저 확인).

## 5. 한계 (정직한 기록)
- **gemini-3.5-flash-lite는 `cause_candidates`(중첩 객체 배열)와 `proposed_action`을 required로 지정해도
  안정적으로 채우지 않고 내용을 `basis`에 몰아넣는다.** 소형 lite 모델의 구조화 출력 한계로 보인다.
  - 대응: basis는 확실히 기록되므로 판단 근거 자체는 검증 가능. required 스키마는 유지해 더 강한 모델
    (Claude 등)에서는 원인 후보·조치가 채워지도록 설계(모델 교체형). 대시보드는 빈 필드를 생략해 안전하게 렌더.
  - 후속: Phase 7(평가)에서 모델별로 구조화 필드 충실도를 비교 측정.
- 검증 당시 랩 IOS 장비 일부가 미기동(No route to host)이라 ios_show가 실패했으나, 조사 건은 그 실패까지
  기록하고 에이전트가 이를 관측해 추론했다(코드 문제 아님). 장비 복구 후 재확인 권장.
