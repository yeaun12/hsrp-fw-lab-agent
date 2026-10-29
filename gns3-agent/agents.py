"""기획 에이전트와 구축 에이전트 정의."""
import json

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query

import config
import gns3_tools

# ---------------------------------------------------------------- 설계도 스키마

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project_name", "summary", "vm_templates", "nodes", "links", "configs", "verify"],
    "properties": {
        "project_name": {"type": "string", "description": "영문, 숫자, 하이픈만 사용"},
        "summary": {"type": "string", "description": "설계 요약 (사람이 검토할 설명)"},
        "vm_templates": {
            "type": "array",
            "description": "PC의 VM을 노드로 쓰려면 먼저 템플릿으로 등록할 목록. 안 쓰면 빈 배열",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["template_name", "vm_type", "vm_name", "adapters"],
                "properties": {
                    "template_name": {"type": "string", "description": "nodes[].template에서 이 이름으로 참조"},
                    "vm_type": {"type": "string", "enum": ["virtualbox", "vmware"]},
                    "vm_name": {"type": "string", "description": "사용 가능한 VM 목록의 이름 그대로"},
                    "adapters": {"type": "integer", "description": "이 VM이 쓸 링크 수 이상"},
                },
            },
        },
        "nodes": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "template", "x", "y"],
                "properties": {
                    "name": {"type": "string"},
                    "template": {"type": "string", "description": "GNS3 템플릿 이름 그대로"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["node_a", "interface_a", "node_b", "interface_b", "subnet"],
                "properties": {
                    "node_a": {"type": "string"}, "interface_a": {"type": "string"},
                    "node_b": {"type": "string"}, "interface_b": {"type": "string"},
                    "subnet": {"type": "string", "description": "예: 10.0.12.0/24, 스위치 링크면 빈 문자열"},
                },
            },
        },
        "configs": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["node", "commands"],
                "properties": {
                    "node": {"type": "string"},
                    "commands": {"type": "array", "items": {"type": "string"},
                                 "description": "enable / configure terminal 부터 end, write memory 까지 순서대로"},
                },
            },
        },
        "verify": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["node", "command", "expect"],
                "properties": {
                    "node": {"type": "string"},
                    "command": {"type": "string"},
                    "expect": {"type": "string", "description": "성공이면 출력에 무엇이 보여야 하는지"},
                },
            },
        },
    },
}

# ---------------------------------------------------------------- 기획 에이전트

PLANNER_PROMPT = """너는 네트워크 설계 전문가다. 사용자의 요구사항을 GNS3 랩 설계도로 바꾼다.

설계 원칙:
- 장비 템플릿은 "사용 가능한 자원"에 있는 템플릿 이름만 쓴다.
- 서버, 클라이언트, PC 역할이 필요하면 "사용 가능한 자원"의 VirtualBox/VMware VM을 쓸 수 있다.
  이때 vm_templates에 등록 정보를 넣고, nodes[].template에는 그 template_name을 쓴다.
  VM 하나로는 노드 하나만 만든다 (같은 VM을 여러 노드로 쓰지 않는다).
  VM 인터페이스 이름은 Ethernet0, Ethernet1 ... 이다.
- VM 내부 설정은 VirtualBox Guest Additions로 OS 셸 명령을 실행해서 한다.
  configs의 commands에는 한 줄에 하나씩 셸 명령을 쓴다 (리눅스: sh 명령, Windows: cmd 명령).
  로그인은 도구가 알아서 하므로 로그인 명령이나 비밀번호는 절대 넣지 않는다.
  게스트 안의 인터페이스 이름은 미리 알 수 없으니 "<GNS3 포트 Ethernet0에 해당하는 인터페이스>"처럼
  자리표시로 쓰고, 구축 에이전트가 실제 이름을 확인해 바꾼다.
  리눅스에서 관리자 권한이 필요한 명령은 "sudo "로 시작한다 (비밀번호는 도구가 넣는다).
  리눅스 예: sudo ip addr add 192.168.10.10/24 dev <Ethernet0 인터페이스>, sudo ip link set <...> up, sudo ip route add default via 192.168.10.1
  Windows 예: netsh interface ip set address name="<Ethernet0 인터페이스>" static 192.168.10.10 255.255.255.0 192.168.10.1
- VM의 verify에는 게이트웨이나 다른 VM으로의 ping을 넣는다 (리눅스 ping -c 3, Windows ping -n 3).
- 인터페이스 이름은 템플릿의 GNS3 포트 이름 규칙을 따른다 (Cisco IOSv: Gi0/0, Gi0/1 ...).
- 링크마다 겹치지 않는 서브넷을 배정한다. 점대점 링크는 /24 또는 /30, 루프백은 /32를 쓴다.
- 장비 설정은 콘솔에 그대로 입력할 명령 순서로 쓴다: enable → configure terminal → ... → end → write memory.
- 각 장비의 관리용 hostname을 노드 이름과 같게 설정한다.
- verify에는 요구사항이 충족됐는지 확인할 show/ping 명령과 기대 결과를 넣는다.
- 노드 좌표는 토폴로지 모양이 보이도록 200px 간격으로 배치한다.
- summary에는 토폴로지, IP 계획, 라우팅 설계를 사람이 읽기 쉽게 한국어로 정리한다."""


def planner_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.PLANNER_MODEL,
        system_prompt=PLANNER_PROMPT,
        tools=[],              # 기획 에이전트는 도구 없이 설계만 한다
        setting_sources=[],    # 로컬 Claude Code 설정의 영향을 받지 않게 격리
        output_format={"type": "json_schema", "schema": PLAN_SCHEMA},
        max_turns=5,
    )


async def make_plan(request: str, resources: dict, feedback: str | None = None,
                    previous: dict | None = None) -> dict:
    prompt = (f"요구사항:\n{request}\n\n"
              f"사용 가능한 자원:\n{json.dumps(resources, ensure_ascii=False, indent=2)}")
    if previous and feedback:
        prompt += (f"\n\n이전 설계도:\n{json.dumps(previous, ensure_ascii=False, indent=2)}"
                   f"\n\n검토 의견 (반영해서 설계도를 다시 작성하라):\n{feedback}")
    result = None
    async for msg in query(prompt=prompt, options=planner_options()):
        if isinstance(msg, ResultMessage):
            result = msg
    if result is None or result.is_error:
        raise RuntimeError(f"기획 실패: {getattr(result, 'errors', None) or getattr(result, 'result', None)}")
    if result.structured_output:
        return result.structured_output
    return json.loads(result.result)

# ---------------------------------------------------------------- 구축 에이전트

BUILDER_PROMPT = """너는 네트워크 구축 엔지니어다. 주어진 설계도를 GNS3 도구로 정확히 구현한다.

작업 순서:
1. vm_templates가 있으면 register_vm_template로 먼저 등록한다.
   그다음 list_templates로 설계도의 템플릿이 모두 있는지 확인한다. 없으면 구축하지 말고 즉시 보고한다.
2. create_project → add_node(모든 노드) → add_link(모든 링크) 순서로 토폴로지를 만든다.
   add_node 결과의 포트 이름이 설계도와 표기만 다르면(예: Gi0/0 vs GigabitEthernet0/0) 실제 포트 이름을 쓴다.
3. start_all_nodes로 장비를 켜고 부팅을 기다린다.
   네트워크 장비는 wait_for_boot, VirtualBox VM 노드는 wait_for_guest를 쓴다.
4. 설계도의 configs를 노드별로 입력한다.
   - 네트워크 장비: console_send. 명령이 많으면 나눠서 보내도 된다.
   - VirtualBox VM: vm_exec. 먼저 인터페이스 목록을 조회해서
     (리눅스: ip -br link, Windows: netsh interface show interface)
     GNS3 포트 Ethernet0, Ethernet1 ... 이 게스트의 첫째, 둘째 ... 어댑터라는 점을 이용해 자리표시를 실제 이름으로 바꾼 뒤 실행한다.
     wait_for_guest나 vm_exec가 계정 문제로 실패하면 그 노드는 건너뛰고 보고서에 적는다.
5. wait_seconds로 60초 정도 라우팅 수렴을 기다린 뒤 verify 명령을 실행해 expect와 비교한다
   (네트워크 장비는 console_send, VM은 vm_exec).
   실패하면 한 번 더 기다렸다가 다시 확인한다.

규칙:
- 설계도에 없는 장비, 링크, 설정을 임의로 추가하지 않는다.
- 설정 명령에서 오류가 나면 원인을 파악해 같은 의도의 올바른 명령으로 한 번 고쳐 보고, 그래도 안 되면 보고서에 적는다.
- 마지막 답변은 아래 형식의 한국어 보고서로 끝낸다:
  ## 구축 결과
  - 토폴로지: (완료/실패)
  - 설정: 노드별 (완료/실패와 이유)
  - 검증: verify 항목별 (통과/실패와 실제 출력 요약)
  - 설계도와 달라진 점: (없으면 "없음")"""


def builder_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.BUILDER_MODEL,
        system_prompt=BUILDER_PROMPT,
        tools=[],                          # 셸, 파일 등 내장 도구는 끈다
        mcp_servers={"gns3": gns3_tools.server},
        allowed_tools=gns3_tools.TOOL_NAMES,
        setting_sources=[],
        max_turns=config.BUILDER_MAX_TURNS,
        max_budget_usd=config.BUILDER_MAX_BUDGET_USD,
    )


async def build(plan: dict, verbose: bool = True) -> str:
    prompt = "다음 설계도대로 GNS3에 구축하고 검증해줘:\n" + json.dumps(plan, ensure_ascii=False, indent=2)
    result = None
    async for msg in query(prompt=prompt, options=builder_options()):
        if verbose and isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    args = json.dumps(block.input, ensure_ascii=False)
                    print(f"  🔧 {block.name.removeprefix('mcp__gns3__')} {args[:150]}")
                elif isinstance(block, TextBlock) and block.text.strip():
                    print(f"  💬 {block.text.strip()[:300]}")
        if isinstance(msg, ResultMessage):
            result = msg
    if result is None:
        raise RuntimeError("구축 에이전트가 결과 없이 끝났습니다.")
    cost = f"(비용 ${result.total_cost_usd:.2f}, {result.num_turns}턴)" if result.total_cost_usd else ""
    return f"{result.result or result.errors}\n\n{cost}"
