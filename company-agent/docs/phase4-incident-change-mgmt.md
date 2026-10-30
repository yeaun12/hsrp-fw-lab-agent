# Phase 4 — 사건 관리 + 변경 작업 관리

작성일: 2026-10-30 · 대상: 로드맵 Phase 4(P1) / 요청문 §5.1 사건 관리, §5.4 변경 작업 관리, §10 변경 전 백업·롤백

## 1. 범위
Phase 2의 조사 기록(case)을 **사건(incident)**으로 정식화하고, 승인 기능을 실제 기업의
**변경 관리 프로세스**(백업·명령 일치·전후 비교·롤백)에 가깝게 끌어올린다.

## 2. 사건 관리 (§5.1)
- 사건 상태: `investigating(조사중) → awaiting_approval(승인대기) → executing(조치중) → verifying(검증중) →
  resolved(해결) / unresolved(미해결) / needs_confirm(확인요청)`. 접수는 사건 생성 시점.
- **정직한 결론(§5.1 "해결 못 한 걸 해결로 표시 금지")**: 에이전트는 report_investigation의 `resolution`
  (해결/미해결/확인요청/정보제공)으로 결론을 선언한다. **자동으로 '해결'로 표시하지 않는** 안전망:
  - 승인 대기가 남아 있으면 → 승인대기
  - resolution을 명시했으면 → 그대로 반영
  - **조사 도구를 썼는데 '해결'을 명시하지 않았으면 → 확인요청**(needs_confirm) (억지 해결 금지)
  - 도구를 전혀 안 쓴 단순 질의응답만 → 해결
- server가 승인 실행을 시작하면 사건을 '조치중'으로, 실행 결과는 사건에 연결(Phase 2에서 구축).

## 3. 변경 작업 관리 (§5.4, §10)
`request_ios_config` 승인 → 실행 경로를 `tools._apply_ios_change`로 재작성:
1. **실행 계층 권한 강제**: 승인된 명령이라도 적용 직전 `CONFIG_DENY` 정책을 **도구 계층에서 다시 검증**
   (프롬프트가 아니라 코드에서 강제). 위반 시 실행 거부. `change.cmd_verified`로 기록.
2. **설정 백업 + 변경 전 상태**: `show running-config` 캡처.
3. **적용(미저장)**: configure terminal → 명령 → end. 실패 시 롤백을 위해 **아직 write memory 안 함**.
4. **변경 후 상태 + 전후 비교**: `_config_diff`로 추가/삭제 줄 요약.
5. **실패 시 안전 롤백**: 오류(`% `) 발견 시 `_inverse_commands` 역명령 적용(저장 안 했으므로 startup-config 보존),
   롤백 후 백업 대비 차이 확인. `change.rolled_back` 기록.
6. **성공 시에만 저장**: write memory. 대시보드 '승인 대기'에 명령 일치·정책 검증·백업 줄 수·전후 차이 표시.

## 4. 변경 파일
- `agent/tools.py`: `_config_diff`, `_inverse_commands`, `_apply_ios_change`; report_investigation에 `resolution` 추가.
- `agent/brain.py`: `_finalize_case`의 정직한 상태 매핑(안전망).
- `agent/state.py`: case에 `resolution`/`reported` 필드.
- `agent/server.py`: 승인 실행 시작 시 사건 '조치중' 전환.
- `agent/static/index.html`: 사건 상태 라벨(조치중/미해결/확인요청), 승인 카드의 변경관리 블록.

## 5. 검증 결과 (2026-10-30)
- **사건 정직한 결론(Gemini 실호출)**: "영업팀 인터넷 느림" 사건 접수 → 장비 전면 불통(No route to host)
  상황에서 에이전트가 5~9단계 조사 후, **status=needs_confirm(확인요청)** 으로 귀결(억지 '해결' 아님). ✓
  - 참고: gemini-3.5-flash-lite는 `resolution` enum을 안정적으로 채우지 않는다(Phase 2와 동일한 구조화 출력 한계).
    그래서 "조사했으나 해결 미선언 → 확인요청" 안전망이 실제로 작동함을 확인. 더 강한 모델은 resolution을 직접 채운다.
- **변경 관리 순수 로직(오프라인 단위 테스트)**: `_config_diff`(추가 줄만 정확히 추출), `_inverse_commands`(역순·토글),
  실행 계층 정책 검증(`username ...` 차단, 정상 명령 통과) 검증. ✓
- **대시보드**: 확인요청/해결 배지, 승인 카드의 "변경관리: 명령 일치·정책 검증 ✓ · 백업 142줄" + 전후 차이 렌더 확인. ✓

## 6. 한계 (정직한 기록 — 라이브 검증 대기)
- **IOS 설정 변경의 실제 적용·전후 비교·롤백은 라이브 검증하지 못했다.** 검증 시점에 GNS3 랩 장비가
  미기동(No route to host)이라 `_apply_ios_change`의 장치 I/O 경로는 **구현완료 / 라이브 검증 대기** 상태다.
  랩 복구(lab_up.py) 후 실제 변경·실패 주입·롤백을 재확인해야 한다.
- **역명령 롤백은 추가형 leaf 변경에 적합**하다. `interface Vlan30` 같은 서브모드 컨텍스트 줄의 단순 역명령
  (`no interface Vlan30`)은 위험할 수 있어, 운영 환경에서는 `configure replace`(백업 파일 기반 전체 복원)가 정석이다.
  본 구현은 랩 수준의 안전 롤백이며 이 점을 설계 결정으로 남긴다.
