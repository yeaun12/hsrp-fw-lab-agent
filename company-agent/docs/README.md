# 사내 IT 운영 AI 에이전트 — 포트폴리오 개요

GNS3로 구축한 가상의 중소기업 네트워크 위에서, **AI 에이전트가 네트워크·보안을 조사·판단·조치**하고
그 운영 데이터를 **수집·분석·평가**하는 엔드투엔드 플랫폼. "AI를 썼다"가 아니라 "**AI가 무엇을, 얼마나
안전하게, 어떤 근거로 개선했는가**"를 실측으로 보여주는 데 초점을 뒀다.

> 모든 수치는 실제 실행으로 측정했다. 확인되지 않은 결과를 성공으로 표시하지 않았고, 수동 운영 시간 등
> 측정하지 않은 값은 날조하지 않았다. 합성/시연 데이터는 그렇게 표시했다(예: 웹 공격은 랩의 ATTACKER가 생성).

## 아키텍처
- **인프라(GNS3)**: HSRP 이중 코어(CORE1/2), 액세스 스위치, OSPF 지사(BRANCH), pfSense 방화벽(ISP 이중화·DMZ),
  서버팜(SRV/WEB), 공격자(Kali), 관리/에이전트 PC. 복구는 `lab_up.py`.
- **에이전트(AGENT-PC)**: 파이썬 + SQLite 경량 구성(저RAM). 웹 대시보드 + 자동 감시 + 도구 기반 LLM 루프.
- **LLM 교체형**: Mock(무료·키 불필요) / Google Gemini(무료 티어) / Anthropic Claude(유료)를 어댑터로 교체.

## 단계별 구현과 검증 (전부 실측)
| Phase | 내용 | 핵심 검증 결과 | 문서 |
|---|---|---|---|
| 0 | 인벤토리·백업 | 기존 환경 보존 | [phase0](phase0-inventory.md) |
| 1 | LLM 제공자 어댑터(교체형) | Mock 루프 검증 | [phase1](phase1-llm-adapter.md) |
| 3 | 무료 Gemini로 실제 AI 판단 | 3시나리오 $0, Gemini 3.x thought-signature 이슈 해결 | [phase3](phase3-ai-judgment-verification.md) |
| 2 | 조사 기록(판단 근거·도구 실행 시각화) | 관측 가능 근거만 표시, Mock·Gemini 검증 | [phase2](phase2-investigation-trace.md) |
| 4 | 사건 관리 + 변경 작업 관리 | **실제 IOS에 변경·전후비교·실패 롤백 라이브 검증**, 정직한 결론(미해결→확인요청) | [phase4](phase4-incident-change-mgmt.md) |
| 5 | 데이터 파이프라인(수집·정규화·검증·저장) | 실데이터 1,519건 적재·품질 OK·완전 멱등 | [phase5](phase5-data-pipeline.md) |
| 6 | SQL 운영 분석 + KPI 대시보드 | 실데이터 KPI·라이브 대시보드 렌더 | [phase6](phase6-sql-analytics.md) |
| 7 | 에이전트 평가 체계 + 자동화 효과 | 22케이스 실측: 위험 차단 100%·승인없는 변경 0·허위 진단 0·진단 71% | [phase7](phase7-evaluation.md) |
| 8 | 통계 이상 탐지 + 이벤트 상관분석 | ML 보류(데이터 부족, 정직), 통계로 급증 탐지·경보 상관 그룹화 | [phase8](phase8-anomaly-correlation.md) |
| 9 | 대시보드 개선 + 통합 회귀 테스트 + 본 문서 | 회귀 테스트 3/3(모듈·파이프라인·라이브 API) 통과 | (이 문서) |

## 관리자 대시보드 (§9의 6개 화면 대응)
기존 구조를 유지하며 정보 구조·사용성을 개선. 헤더에 **운영 현황 한눈 요약**(장비 n/6·미해결·승인대기·긴급경보).
1. 운영 현황 → 헤더 요약 + 인프라 상태 + 감시 경보 + 승인 대기
2. AI 에이전트 → 대화 + 조사 기록(도구 호출·근거·결론·승인)
3. 사건 관리 → 조사 기록(사건=case: 상태·관련 장비·시점·근거·타임라인)
4. 데이터 분석 → 운영 분석(KPI) 패널 (`/api/analytics`)
5. 변경 관리 → 승인 대기(명령 일치·백업·전후 비교·롤백 결과)
6. 에이전트 성능 → `evaluate.py` 평가 리포트(진단 정확도·차단율·토큰·시간)

## 핵심 설계 결정 (정직한 엔지니어링)
- **교체형 LLM**: 모델별 요구사항(예: Gemini 3.x thought-signature)을 어댑터가 흡수 → 설계가 실모델에서 검증됨.
- **도구 실행 계층에서 권한 강제**: 프롬프트가 아니라 코드로 위험 명령·승인 없는 변경을 차단(평가에서 0건 확인).
- **정직한 결론**: 해결 못 한 사건을 '해결'로 표시하지 않고 '확인요청/미해결'로(안전망). 평가 허위 진단율 0.
- **안전한 변경 관리**: 백업 → 적용(미저장) → 전후 비교 → 실패 시 롤백 → 성공 시에만 저장(실IOS 검증).
- **데이터 우선, ML은 필요할 때만**: 데이터 양·품질을 먼저 평가해 ML을 무리하게 적용하지 않음.
- **비용 최소화**: 무료 모델 우선, 포트폴리오 데모/반복 테스트 비용 $0.

## 한계 (솔직히)
- lite 모델은 중첩 구조화 출력(원인 후보·resolution)을 불안정하게 채움 → 안전망/폴백으로 보완.
- §8 상관분석의 시간 창은 플러드 구간을 과다 병합. ML은 데이터가 쌓이면 활성화 예정.
- §8 수동 운영 기준시간은 사람이 직접 측정해야 함(프로토콜 제공, 날조 금지).

## 실행 방법 (요약)
- 랩 복구: 호스트에서 `python company-agent/lab_up.py` (GNS3 필요).
- 에이전트: AGENT-PC에서 `lab-agent` systemd 서비스. 대시보드 `http://127.0.0.1:8080`.
- 분석/평가 CLI(AGENT-PC): `python warehouse.py --quality`, `python analytics.py`, `python anomaly.py`,
  `python correlate.py`, `python evaluate.py --subset safety`, `python regression_test.py`.
- 테스트: `warehouse_test.py`, `phase8_test.py`, `regression_test.py`.
