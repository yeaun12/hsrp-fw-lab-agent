"""GNS3 랩 자동 구축: 기획 에이전트 → 사람 검토 → 구축 에이전트.

사용법:
  python main.py                       # 요구사항을 입력받아 기획부터 시작
  python main.py "R1-R2-R3 OSPF 랩"     # 요구사항을 인자로 전달
  python main.py --plan plans/x.json   # 저장된 설계도로 바로 구축
"""
import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import agents
import gns3_tools

PLANS_DIR = Path(__file__).parent / "plans"


def show_plan(plan: dict) -> None:
    print("\n" + "=" * 60)
    print(f"📐 설계도: {plan['project_name']}")
    print("=" * 60)
    print(plan["summary"])
    if plan.get("vm_templates"):
        print("\n[불러올 VM]")
        for v in plan["vm_templates"]:
            print(f"  - {v['vm_name']} ({v['vm_type']}) → 템플릿 '{v['template_name']}'")
    print("\n[노드]")
    for n in plan["nodes"]:
        print(f"  - {n['name']:<8} {n['template']}")
    print("\n[링크]")
    for l in plan["links"]:
        print(f"  - {l['node_a']} {l['interface_a']} <-> {l['node_b']} {l['interface_b']}  {l['subnet']}")
    print("\n[설정 명령 수]")
    for c in plan["configs"]:
        print(f"  - {c['node']}: {len(c['commands'])}줄")
    print("\n[검증]")
    for v in plan["verify"]:
        print(f"  - {v['node']}: {v['command']}  → {v['expect']}")
    print("=" * 60)


def save_plan(plan: dict) -> Path:
    PLANS_DIR.mkdir(exist_ok=True)
    path = PLANS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{plan['project_name']}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def plan_with_review(request: str) -> dict | None:
    print("\n🔎 GNS3 템플릿과 PC의 VM 목록을 확인합니다...")
    resources = await gns3_tools.get_resources()
    print(f"  템플릿 {len(resources['templates'])}개, VirtualBox VM {len(resources['virtualbox_vms'])}개, "
          f"VMware VM {len(resources['vmware_vms'])}개")
    print("\n🧠 기획 에이전트가 설계 중입니다...")
    plan = await agents.make_plan(request, resources)
    while True:
        show_plan(plan)
        path = save_plan(plan)
        print(f"💾 저장됨: {path}  (전체 설정 명령은 이 파일에서 확인할 수 있습니다)")
        answer = input("\n이 설계로 구축할까요? [y=구축 / n=취소 / 그 외=수정 요청 내용 입력] > ").strip()
        if answer.lower() == "y":
            return plan
        if answer.lower() in ("n", ""):
            return None
        print("\n🧠 검토 의견을 반영해 다시 설계 중입니다...")
        plan = await agents.make_plan(request, resources, feedback=answer, previous=plan)


async def main() -> None:
    parser = argparse.ArgumentParser(description="GNS3 랩 자동 구축 에이전트")
    parser.add_argument("request", nargs="?", help="만들고 싶은 랩 설명")
    parser.add_argument("--plan", type=Path, help="저장된 설계도(JSON)로 바로 구축")
    args = parser.parse_args()

    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        show_plan(plan)
        if input("\n이 설계로 구축할까요? [y/n] > ").strip().lower() != "y":
            return
    else:
        request = args.request or input("만들고 싶은 랩을 설명하세요 > ")
        plan = await plan_with_review(request)
        if plan is None:
            print("취소했습니다.")
            return

    print("\n🔨 구축 에이전트가 작업을 시작합니다...\n")
    report = await agents.build(plan)
    print("\n" + report)


if __name__ == "__main__":
    asyncio.run(main())
