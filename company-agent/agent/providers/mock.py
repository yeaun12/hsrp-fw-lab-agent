"""Mock 제공자 — 키 없이 도구 호출 루프·승인·감사·추적을 검증하기 위한 가짜 모델.

실제 추론을 하지 않는다. 사용자 요청의 키워드와 지금까지 쌓인 도구 결과 수(단계)를 보고
'다음에 어떤 도구를 부를지 / 언제 끝낼지'를 정해진 흐름으로 결정한다. 도구 자체는 실제로 실행되므로
(루프·승인·감사·대시보드 추적)은 진짜로 동작한다. 최종 문장은 Mock이 만든 템플릿임을 밝힌다.

목적: Phase 1에서 제어 흐름을 키 없이 검증. 실제 AI 판단 품질 검증은 Gemini/Claude로 한다.
"""
import re

from .base import Provider, Response

TAG = "[Mock 모델] 실제 추론이 아니라 제어 흐름 검증용 응답입니다. 도구 호출과 결과는 실제입니다.\n\n"

# 키워드 -> 도구 호출 시나리오. 각 단계는 (tool_name, input) 또는 None(마지막, 요약 문장).
FLOWS = [
    (re.compile(r"보안 ?점검|취약|KISA|점검해"), [
        ("run_security_audit", {}),
        ("get_checklist_item", {"code": "N-08"}),
    ], "보안 점검을 실행했습니다. 규칙 판정 결과와 N-08(VTY 평문 프로토콜) 기준을 조회했습니다. "
       "취약 항목과 수동확인 항목은 결과표에서 확인하세요."),
    (re.compile(r"웹 ?공격|SQL|인젝션|XSS|웹 ?로그"), [
        ("web_attack_scan", {}),
        ("get_alerts", {}),
    ], "웹 서버 로그를 분석하고 최근 경보를 확인했습니다. 탐지된 출발지와 성공 추정은 웹 공격 패널에 있습니다. "
       "차단이 필요하면 request_firewall_block으로 승인 요청을 만들 수 있습니다."),
    (re.compile(r"영업|DB|데이터베이스|3306|접속.*안|접속이 안"), [
        ("list_devices", {}),
        ("ios_show", {"device": "CORE2", "commands": ["show access-lists SRV-PROTECT"]}),
    ], "영업팀은 VLAN20이고, 코어의 서버팜 ACL(SRV-PROTECT)에서 VLAN20→서버팜(VLAN30)은 전부 차단됩니다. "
       "이는 장애가 아니라 회사 정책에 따른 정상 차단입니다. 접근이 필요하면 ACL 변경을 승인 요청해야 합니다."),
    (re.compile(r"전체 ?상태|상태 ?점검|요약|현황"), [
        ("list_devices", {}),
        ("firewall_status", {}),
    ], "장비 목록과 방화벽 상태를 조회했습니다. 인프라 상태 패널에서 HSRP와 게이트웨이 상태를 확인하세요."),
    (re.compile(r"포트 ?스캔|스캔|차단한|차단 ?로그"), [
        ("firewall_blocked_traffic", {"minutes": 10}),
    ], "최근 방화벽 차단 트래픽을 출발지별로 요약했습니다. 스캔으로 의심되는 출발지는 결과를 보고 차단을 승인 요청하세요."),
]
DEFAULT_FLOW = ([("list_devices", {})],
                "요청을 처리하기 위해 장비 목록을 조회했습니다. (Mock은 정해진 흐름만 수행합니다.)")


def _latest_user_text(messages):
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], str):
            return m["content"]
    return ""


def _tool_results_so_far(messages):
    """현재 요청(마지막 사용자 텍스트) 이후 실행된 도구 결과 수 = 현재 단계."""
    last_user = max((i for i, m in enumerate(messages)
                     if m["role"] == "user" and isinstance(m["content"], str)), default=-1)
    n = 0
    for m in messages[last_user + 1:]:
        if m["role"] == "user" and isinstance(m["content"], list):
            n += sum(1 for b in m["content"] if b.get("type") == "tool_result")
    return n


class MockProvider(Provider):
    name = "mock"

    def generate(self, system, messages, tools):
        text = _latest_user_text(messages)
        steps, final = DEFAULT_FLOW[0], DEFAULT_FLOW[1]
        for rx, flow_steps, flow_final in FLOWS:
            if rx.search(text):
                steps, final = flow_steps, flow_final
                break
        done = _tool_results_so_far(messages)
        tool_names = {t["name"] for t in tools}
        # 아직 실행할 단계가 남았으면 그 도구를 호출, 없으면 요약 후 종료
        if done < len(steps):
            name, args = steps[done]
            if name in tool_names:
                return Response([{"type": "tool_use", "id": f"mock-{done}", "name": name, "input": args}],
                                "tool_use", usage={"input": 200, "output": 40})
        return Response([{"type": "text", "text": TAG + final}], "end_turn", usage={"input": 200, "output": 120})
