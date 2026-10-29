"""API 키 없이 도구 계층을 점검한다 (에이전트 PC에서 실행).

    /opt/lab-agent/venv/bin/python smoke_test.py            조회 도구와 안전장치
    /opt/lab-agent/venv/bin/python smoke_test.py block IP   승인된 차단을 직접 실행 (해제는 unblock IP)
"""
import sys

import tools


def check(title, name, args, expect_error):
    out, is_error = tools.run_tool(name, args)
    mark = "OK " if is_error == expect_error else "BAD"
    print(f"[{mark}] {title}: {'오류' if is_error else '성공'}\n      {out[:300].replace(chr(10), chr(10) + '      ')}")
    return is_error == expect_error


if len(sys.argv) == 3 and sys.argv[1] in ("block", "unblock"):
    rec = {"tool": f"request_firewall_{sys.argv[1]}", "input": {"ip": sys.argv[2]}}
    print(tools.execute_approved(rec))
    sys.exit()

results = [
    check("코어 ACL 조회", "ios_show", {"device": "CORE2", "commands": ["show access-lists SRV-PROTECT"]}, False),
    check("지사 OSPF 조회", "ios_show", {"device": "BRANCH", "commands": ["show ip ospf neighbor"]}, False),
    check("방화벽 상태", "firewall_status", {}, False),
    check("차단 트래픽 요약", "firewall_blocked_traffic", {"minutes": 60}, False),
    check("[안전] 조회 도구로 설정 변경 시도", "ios_show", {"device": "CORE1", "commands": ["configure terminal"]}, True),
    check("[안전] 조회 도구로 reload 시도", "ios_show", {"device": "CORE1", "commands": ["show run | reload"]}, True),
    check("[안전] 없는 장비", "ios_show", {"device": "ISP1", "commands": ["show ip route"]}, True),
    check("[안전] 내부 IP 차단 요청", "request_firewall_block", {"ip": "10.0.10.11", "reason": "test"}, True),
    check("[안전] 자사 DMZ IP 차단 요청", "request_firewall_block", {"ip": "211.0.1.10", "reason": "test"}, True),
    check("[안전] 관리 계정 변경 요청", "request_ios_config",
          {"device": "CORE1", "commands": ["username hacker privilege 15 secret x"], "reason": "test"}, True),
    check("[안전] VTY 변경 요청", "request_ios_config", {"device": "ASW1", "commands": ["line vty 0 4"], "reason": "test"}, True),
    check("외부 IP 차단 요청 (승인 대기열 등록만)", "request_firewall_block", {"ip": "198.51.100.99", "reason": "test"}, False),
]
print(f"\n{sum(results)}/{len(results)} 통과")
