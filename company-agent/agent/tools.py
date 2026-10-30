"""에이전트 도구. 조회 도구는 바로 실행하고, 변경 도구는 승인 대기열에 등록만 한다."""
import datetime
import ipaddress
import json
import re

import audit
import config
import state
import web_detect
from devices import DeviceError, ios_run, pfsense_run

# ------------------------------------------------------------------ 허용 목록

READ_PREFIXES = ("show ", "ping ", "traceroute ")
READ_DENY = re.compile(r"\b(tech-support|debug|reload|clear|write|copy|erase|delete|configure)\b", re.I)
# 에이전트가 관리 접근 자체를 바꾸지 못하게 막는 설정 키워드
CONFIG_DENY = re.compile(r"^\s*(no\s+)?(username|enable\s|line\s|access-class|ip access-list standard MGMT-ONLY|"
                         r"aaa|crypto|boot|config-register|vtp file)", re.I)
PROTECTED_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/15", "211.0.0.0/30", "211.0.1.0/28", "211.0.2.0/30")]
BLOCK_TABLE = "EasyRuleBlockHostsWAN"


def _ios_device(name):
    if name not in config.DEVICES or config.DEVICES[name]["type"] != "ios":
        raise ValueError(f"알 수 없는 IOS 장비입니다: {name}. 사용 가능: "
                         f"{[d for d, v in config.DEVICES.items() if v['type'] == 'ios']}")
    return name


def _external_ip(ip):
    addr = ipaddress.ip_address(ip)
    if addr.version != 4:
        raise ValueError("IPv4 주소만 지원합니다.")
    if any(addr in n for n in PROTECTED_NETS):
        raise ValueError(f"{ip}는 회사 내부/자사 대역이라 차단할 수 없습니다.")
    return str(addr)


# ------------------------------------------------------------------ 방화벽 로그

LOG_LINE = re.compile(r"^(\w{3}\s+\d+\s+\d\d:\d\d:\d\d)\s+\S+\s+filterlog\[\d+\]:\s*(\d+,.*)$")


def parse_filter_log(text, now_epoch, year, tz, window_sec):
    """pfSense filterlog(BSD syslog, FW 현지 시각)에서 window 안의 차단 기록을 출발지별로 묶는다."""
    by_src = {}
    for line in text.splitlines():
        m = LOG_LINE.match(line.strip())
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(f"{year} {m.group(1)} {tz}", "%Y %b %d %H:%M:%S %z").timestamp()
        except ValueError:
            continue
        if ts > now_epoch + 86400:          # 연말에 작년 기록이 올해로 읽힌 경우
            continue
        if now_epoch - ts > window_sec:
            continue
        f = m.group(2).split(",")
        if len(f) < 20 or f[6] != "block" or f[8] != "4":
            continue
        iface, proto, src, dst = f[4], f[16], f[18], f[19]
        dport = f[21] if proto in ("tcp", "udp") and len(f) > 21 else proto
        e = by_src.setdefault(src, {"src": src, "count": 0, "ports": set(), "dsts": set(), "ifaces": set(),
                                    "first": ts, "last": ts})
        e["count"] += 1
        e["ports"].add(f"{proto}/{dport}" if proto in ("tcp", "udp") else proto)
        e["dsts"].add(dst)
        e["ifaces"].add(iface)
        e["first"], e["last"] = min(e["first"], ts), max(e["last"], ts)
    return by_src


def firewall_blocked_summary(window_sec):
    _, out = pfsense_run("date '+%s %Y %z'; tail -n 4000 /var/log/filter.log")
    first, _, rest = out.partition("\n")
    now_epoch, year, tz = first.split()
    by_src = parse_filter_log(rest, int(now_epoch), year, tz, window_sec)
    rows = sorted(by_src.values(), key=lambda e: -e["count"])
    return [{"src": e["src"], "blocked_packets": e["count"], "distinct_targets": len(e["ports"]),
             "targets": sorted(e["ports"])[:25], "dst_ips": sorted(e["dsts"]), "interfaces": sorted(e["ifaces"]),
             "seconds_span": int(e["last"] - e["first"])} for e in rows]


def firewall_blocked_ips():
    # 아직 차단한 적이 없으면 테이블이 없어서 pfctl이 실패한다 -> 빈 목록
    code, out = pfsense_run(f"pfctl -t {BLOCK_TABLE} -T show 2>/dev/null")
    return [l.strip() for l in out.splitlines() if l.strip()] if code == 0 else []


# ------------------------------------------------------------------ 도구 정의

TOOLS = [
    {"name": "list_devices",
     "description": "관리 대상 장비 목록(이름, 종류, 관리 IP, 역할)과 최근 감시 상태를 돌려준다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ios_show",
     "description": "Cisco IOS 장비에서 조회 명령을 실행한다. 'show ...', 'ping ...', 'traceroute ...'만 허용된다. "
                    "여러 명령을 한 번에 넘기면 한 세션에서 차례로 실행한다.",
     "input_schema": {"type": "object", "properties": {
         "device": {"type": "string", "description": "CORE1, CORE2, ASW1, ASW2, BRANCH 중 하나"},
         "commands": {"type": "array", "items": {"type": "string"}, "description": "실행할 조회 명령 (최대 6개)"}},
         "required": ["device", "commands"]}},
    {"name": "firewall_status",
     "description": "pfSense 방화벽의 WAN 게이트웨이(ISP1/ISP2) 상태, 적용 중인 사용자 룰, 차단 목록에 등록된 IP를 조회한다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "firewall_blocked_traffic",
     "description": "pfSense가 최근 차단한 트래픽을 출발지 IP별로 요약한다 (패킷 수, 서로 다른 목적지 포트 수, 대상 IP). "
                    "포트 스캔이나 공격 출발지를 찾을 때 쓴다.",
     "input_schema": {"type": "object", "properties": {
         "minutes": {"type": "integer", "description": "몇 분 전까지 볼지 (1~60, 기본 10)"}}}},
    {"name": "get_alerts",
     "description": "자동 감시 기능이 최근에 올린 경보 목록을 돌려준다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "request_firewall_block",
     "description": "[변경 - 관리자 승인 필요] 외부 IP를 pfSense WAN에서 차단하도록 승인을 요청한다. "
                    "즉시 실행되지 않고 관리자가 대시보드에서 승인해야 적용된다. 내부·자사 대역은 차단할 수 없다.",
     "input_schema": {"type": "object", "properties": {
         "ip": {"type": "string"}, "reason": {"type": "string", "description": "근거(관측 내용)를 포함한 차단 사유"}},
         "required": ["ip", "reason"]}},
    {"name": "request_firewall_unblock",
     "description": "[변경 - 관리자 승인 필요] 차단 목록에서 IP를 해제하도록 승인을 요청한다.",
     "input_schema": {"type": "object", "properties": {
         "ip": {"type": "string"}, "reason": {"type": "string"}}, "required": ["ip", "reason"]}},
    {"name": "request_ios_config",
     "description": "[변경 - 관리자 승인 필요] IOS 장비에 설정 명령을 적용하도록 승인을 요청한다. "
                    "승인되면 configure terminal 모드에서 차례로 입력하고 write memory로 저장한다. "
                    "계정, VTY, 관리 ACL(MGMT-ONLY), 부팅 관련 설정은 바꿀 수 없다.",
     "input_schema": {"type": "object", "properties": {
         "device": {"type": "string"},
         "commands": {"type": "array", "items": {"type": "string"}, "description": "config 모드 명령 (configure terminal/end 제외)"},
         "reason": {"type": "string"}},
         "required": ["device", "commands", "reason"]}},
]

TOOLS += [
    {"name": "run_security_audit",
     "description": "KISA 주요정보통신기반시설 취약점 분석·평가 가이드(2026)의 네트워크 장비 항목 N-01~N-38로 사내 IOS 장비 "
                    "(CORE1, CORE2, ASW1, ASW2, BRANCH)를 점검한다. 설정·버전·인터페이스·CDP·SNMP를 수집해 규칙으로 1차 판정하고 "
                    "점검 ID를 돌려준다. 판정은 양호/취약/수동확인이며, 수동확인은 맥락 판단이 필요한 항목이다. 조회만 한다.",
     "input_schema": {"type": "object", "properties": {
         "devices": {"type": "array", "items": {"type": "string"}, "description": "생략하면 사내 IOS 장비 5대 전체"}}}},
    {"name": "get_checklist_item",
     "description": "KISA 점검 항목 하나의 점검 내용, 양호/취약 판단 기준, 조치 방법, Cisco IOS 점검·조치 예시를 돌려준다. "
                    "수동확인 항목을 판정하거나 조치 명령을 만들 때 쓴다.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "예: N-07"}},
                      "required": ["code"]}},
    {"name": "web_attack_scan",
     "description": "DMZ 웹 서버(211.0.1.10)의 접속 로그를 가져와 SQL 인젝션·XSS 공격 시도를 출발지 IP별로 요약한다. "
                    "각 시도의 시그니처와, 앱이 남긴 결과로 추정한 성공 가능성(success_hints)을 함께 돌려준다. 조회만 한다.",
     "input_schema": {"type": "object", "properties": {
         "lines": {"type": "integer", "description": "로그 끝에서 몇 줄을 볼지 (기본 400)"}}}},
    {"name": "save_audit_report",
     "description": "점검 결과에 최종 판정과 보고서를 저장한다(대시보드 '보안 점검'에 표시). overrides로 수동확인 항목을 "
                    "판정하거나, 규칙 판정이 맥락상 틀린 경우 근거와 함께 정정한다. report는 관리자용 마크다운 보고서.",
     "input_schema": {"type": "object", "properties": {
         "audit_id": {"type": "string"},
         "overrides": {"type": "array", "items": {"type": "object", "properties": {
             "code": {"type": "string"}, "device": {"type": "string", "description": "장비 이름 또는 ALL"},
             "verdict": {"type": "string", "enum": ["양호", "취약", "해당없음"]},
             "reason": {"type": "string"}}, "required": ["code", "device", "verdict", "reason"]}},
         "report": {"type": "string"}},
         "required": ["audit_id", "report"]}},
]

TOOLS += [
    {"name": "report_investigation",
     "description": "조사의 결론을 구조화해 기록한다(대시보드 '조사 기록' 패널에 표시). cause_candidates에 원인 후보를 하나 이상 "
                    "각각 확인 상태와 함께 담고, basis(최종 근거)와 proposed_action(조치 제안)을 모두 채운다. 모델의 비공개 추론이 "
                    "아니라 도구로 확인한 관측 사실만 근거로 적는다. 장비를 바꾸지 않는 기록 전용 도구다. 조사를 마무리할 때 한 번 호출한다.",
     "input_schema": {"type": "object", "properties": {
         "cause_candidates": {"type": "array", "minItems": 1, "description": "원인 후보 목록 (하나 이상)",
             "items": {"type": "object", "properties": {
                 "cause": {"type": "string", "description": "원인 후보"},
                 "status": {"type": "string", "enum": ["확인됨", "배제됨", "의심"], "description": "도구로 확인한 상태"}},
                 "required": ["cause", "status"]}},
         "basis": {"type": "string", "description": "최종 판단 근거 (어느 장비에서 무엇을 봤는지)"},
         "proposed_action": {"type": "string", "description": "권고 조치 (없으면 '조치 불필요')"},
         "resolution": {"type": "string", "enum": ["해결", "미해결", "확인요청", "정보제공"],
                        "description": "조사 결론 상태. 해결하지 못했으면 반드시 '미해결', 담당자 확인이 필요하면 '확인요청'으로 정직하게 적는다. "
                                       "억지로 '해결'로 표시하지 않는다. 단순 정보 제공이면 '정보제공'."}},
         "required": ["cause_candidates", "basis", "proposed_action", "resolution"]}},
]

CHANGE_TOOLS = {"request_firewall_block", "request_firewall_unblock", "request_ios_config"}


def _audit_brief(a):
    """에이전트에게 돌려줄 점검 결과 요약: 양호가 아닌 항목만 장비별 근거와 함께."""
    rows = []
    for it in a["items"]:
        bad = {d: r for d, r in it["results"].items() if r["verdict"] != "양호"}
        if bad:
            rows.append({"code": it["code"], "importance": it["importance"], "name": it["name"],
                         "results": {d: f"{r['verdict']}: {r['evidence'][:220]}" for d, r in bad.items()}})
    good = [it["code"] for it in a["items"] if all(r["verdict"] == "양호" for r in it["results"].values())]
    return {"audit_id": a["id"], "devices": a["devices"], "errors": a["errors"], "summary": a["summary"],
            "all_good_items": good, "needs_attention": rows}


# ------------------------------------------------------------------ 실행

def run_tool(name, args):
    """도구를 실행하고 (결과 문자열, 오류 여부)를 돌려준다."""
    try:
        if name == "list_devices":
            with state.lock:
                st = json.loads(json.dumps(state.status, default=str))
            return json.dumps({"devices": config.DEVICES, "last_poll": st}, ensure_ascii=False), False

        if name == "ios_show":
            dev = _ios_device(args.get("device", ""))
            cmds = [c.strip() for c in args.get("commands", []) if c.strip()][:6]
            if not cmds:
                raise ValueError("commands가 비어 있습니다.")
            for c in cmds:
                if not c.lower().startswith(READ_PREFIXES) or READ_DENY.search(c):
                    raise ValueError(f"허용되지 않는 명령입니다 (조회 전용): {c}")
            out = ios_run(dev, cmds)
            state.log_audit("agent", "read", f"{dev}: {', '.join(cmds)}")
            return "\n\n".join(f"{dev}# {c}\n{o}" for c, o in out)[-12000:], False

        if name == "firewall_status":
            _, gw = pfsense_run("pfSsh.php playback gatewaystatus")
            _, rules = pfsense_run("pfctl -sr | grep USER_RULE | sed -E 's/ (ridentifier|label \"id).*//'")
            blocked = firewall_blocked_ips()
            state.log_audit("agent", "read", "FW: gateway status, rules, block list")
            return (f"[게이트웨이]\n{gw}\n\n[사용자 룰]\n{rules}\n\n[차단 목록 {BLOCK_TABLE}]\n"
                    f"{chr(10).join(blocked) or '(없음)'}"), False

        if name == "firewall_blocked_traffic":
            minutes = max(1, min(int(args.get("minutes") or 10), 60))
            rows = firewall_blocked_summary(minutes * 60)
            state.log_audit("agent", "read", f"FW: blocked traffic last {minutes}m")
            return json.dumps({"window_minutes": minutes, "sources": rows[:15]}, ensure_ascii=False), False

        if name == "get_alerts":
            with state.lock:
                return json.dumps([{k: v for k, v in a.items() if k != "ts"} for a in state.alerts[-15:]],
                                  ensure_ascii=False), False

        if name == "run_security_audit":
            devs = [d for d in (args.get("devices") or audit.AUDIT_DEVICES)]
            for d in devs:
                if d not in audit.AUDIT_DEVICES:
                    raise ValueError(f"점검 대상이 아닌 장비: {d}. 대상: {audit.AUDIT_DEVICES}")
            a = audit.run_audit(devs, actor="agent")
            state.log_audit("agent", "security_audit", f"점검 {a['id']}: {a['summary']}")
            return json.dumps(_audit_brief(a), ensure_ascii=False), False

        if name == "web_attack_scan":
            rows = web_detect.scan(lines=int(args.get("lines") or 400))
            state.log_audit("agent", "read", "WEB 공격 로그 분석")
            return json.dumps({"web_server": web_detect.WEB_HOST, "sources": rows}, ensure_ascii=False), False

        if name == "get_checklist_item":
            it = audit.ITEMS.get((args.get("code") or "").upper())
            if not it:
                raise ValueError("N-01 ~ N-38 중 하나를 지정하세요.")
            return json.dumps(it, ensure_ascii=False), False

        if name == "save_audit_report":
            a = audit.load(args.get("audit_id", ""))
            ov = args.get("overrides") or []
            for o in ov:
                it = next((x for x in a["items"] if x["code"] == o.get("code")), None)
                if not it:
                    continue
                targets = list(it["results"]) if o.get("device") == "ALL" else [o.get("device")]
                for d in targets:
                    if d in it["results"]:
                        it["results"][d].update(final=o["verdict"], reason=o.get("reason", ""))
            a["report"] = args.get("report", "")
            a["final_summary"] = audit.summarize([{**it, "results": {d: {"verdict": r.get("final", r["verdict"])}
                                                                     for d, r in it["results"].items()}}
                                                  for it in a["items"]])
            audit.save(a)
            state.log_audit("agent", "audit_report", f"점검 {a['id']} 보고서 저장 (정정 {len(ov)}건)")
            return f"점검 {a['id']} 보고서를 저장했습니다. 최종 집계: {a['final_summary']}", False

        if name in ("request_firewall_block", "request_firewall_unblock"):
            ip = _external_ip(args.get("ip", ""))
            verb = "차단" if name == "request_firewall_block" else "차단 해제"
            rec = state.add_approval(name, {"ip": ip}, f"FW WAN에서 {ip} {verb}", args.get("reason", ""))
            return (f"승인 요청 #{rec['id']}을 등록했습니다 ({ip} {verb}). 관리자가 승인하기 전에는 실행되지 않습니다. "
                    "승인되면 결과가 대화에 전달됩니다."), False

        if name == "request_ios_config":
            dev = _ios_device(args.get("device", ""))
            cmds = [c.strip() for c in args.get("commands", []) if c.strip()]
            if not cmds or len(cmds) > 30:
                raise ValueError("commands는 1~30줄이어야 합니다.")
            for c in cmds:
                if CONFIG_DENY.match(c) or c.lower() in ("end", "configure terminal", "conf t"):
                    raise ValueError(f"에이전트가 바꿀 수 없는 설정입니다: {c}")
            rec = state.add_approval(name, {"device": dev, "commands": cmds},
                                     f"{dev} 설정 변경 {len(cmds)}줄: " + " / ".join(cmds[:4]) + (" ..." if len(cmds) > 4 else ""),
                                     args.get("reason", ""))
            return (f"승인 요청 #{rec['id']}을 등록했습니다 ({dev} 설정 {len(cmds)}줄). "
                    "관리자가 승인하기 전에는 실행되지 않습니다."), False

        if name == "report_investigation":
            c = state.current_case()
            cands = [{"cause": str(x.get("cause", "")), "status": x.get("status", "의심")}
                     for x in (args.get("cause_candidates") or []) if x.get("cause")]
            with state.lock:
                if c:
                    c["reported"] = True
                    c["cause_candidates"] = cands
                    c["basis"] = str(args.get("basis", "")) or c["basis"]
                    c["proposed_action"] = str(args.get("proposed_action", ""))
                    if args.get("resolution"):
                        c["resolution"] = args["resolution"]
                    c["updated"] = state.now()
            state.save_cases()
            return "조사 기록을 저장했습니다. (대시보드 '조사 기록'에 표시됩니다)", False

        return f"알 수 없는 도구: {name}", True
    except (ValueError, DeviceError) as e:
        return f"오류: {e}", True
    except OSError:
        return "오류: 해당 점검 결과를 찾을 수 없습니다. run_security_audit가 돌려준 audit_id를 쓰세요.", True


def _config_diff(before, after):
    """IOS running-config 두 스냅샷의 줄 차이를 요약한다(순서 무시, 주석/빈 줄 제외)."""
    skip = ("!", "Current configuration", "Building configuration", "Load for", "Time source", "ntp clock-period")
    norm = lambda t: [l.rstrip() for l in t.splitlines() if l.strip() and not l.strip().startswith(skip)]
    b, a = norm(before), norm(after)
    bset, aset = set(b), set(a)
    lines = [f"+ {l.strip()}" for l in a if l not in bset] + [f"- {l.strip()}" for l in b if l not in aset]
    return "\n".join(lines[:40])


def _inverse_commands(cmds):
    """추가형 설정 변경의 역명령(롤백용). 'no X' -> 'X', 그 외 -> 'no X'. 역순으로 적용한다.
    일반적인 추가 변경을 원복한다. 완전한 복원은 configure replace가 필요하다(운영 환경 과제로 문서화)."""
    inv = []
    for c in reversed(cmds):
        s = c.strip()
        inv.append(s[3:].strip() if s.lower().startswith("no ") else "no " + s)
    return inv


def _apply_ios_change(rec):
    """IOS 설정 변경을 변경관리 절차대로 적용한다: 실행계층 재검증 → 백업·변경전 → 적용(미저장)
    → 변경후·전후비교 → 실패 시 안전 롤백 / 성공 시 저장. 상세를 rec['change']에 남기고 결과 문자열을 돌려준다."""
    dev = _ios_device(rec["input"]["device"])
    cmds = rec["input"]["commands"]
    change = {"cmd_verified": False, "diff": "", "rolled_back": False, "backup_lines": 0}
    rec["change"] = change
    # 실행 계층 권한 강제: 승인된 명령이라도 적용 직전에 정책을 다시 검증한다(프롬프트가 아니라 도구 계층).
    bad = [c for c in cmds if CONFIG_DENY.match(c) or c.strip().lower() in ("end", "configure terminal", "conf t")]
    if bad:
        return f"실행 거부(도구 계층 검증 실패): 승인 범위를 벗어난 명령 {bad}"
    change["cmd_verified"] = True
    # 1) 설정 백업 + 변경 전 상태
    (_, before), = ios_run(dev, ["show running-config"], timeout=30)
    change["backup_lines"] = len(before.splitlines())
    # 2) 적용 (실패 시 롤백을 위해 아직 저장하지 않는다)
    out = ios_run(dev, ["configure terminal", *cmds, "end"], timeout=30)
    errors = [f"{c} -> {o.strip()}" for c, o in out if "% " in o]
    # 3) 변경 후 상태 + 전후 비교
    (_, after), = ios_run(dev, ["show running-config"], timeout=30)
    change["diff"] = _config_diff(before, after)
    if errors:
        # 4) 안전한 롤백: 역명령 적용(저장하지 않았으므로 startup-config는 그대로)
        try:
            ios_run(dev, ["configure terminal", *_inverse_commands(cmds), "end"], timeout=30)
            (_, restored), = ios_run(dev, ["show running-config"], timeout=30)
            change["rolled_back"] = True
            change["rollback_diff"] = _config_diff(before, restored)
        except DeviceError as e:
            change["rollback_diff"] = f"롤백 중 오류: {e}"
        return ("변경 실패 → 롤백함(저장하지 않음):\n" + "\n".join(errors) +
                f"\n[롤백 후 백업 대비 차이]\n{change.get('rollback_diff') or '차이 없음(원상복구 확인)'}")
    # 5) 성공 시에만 저장
    ios_run(dev, ["write memory"], timeout=30)
    return (f"적용 및 저장됨 · 승인 명령 {len(cmds)}줄 실행(도구 계층 검증 통과, 백업 {change['backup_lines']}줄)\n"
            f"[변경 전후 차이]\n{change['diff'] or '차이 없음'}")


def execute_approved(rec):
    """관리자가 승인한 변경을 실제로 적용하고 결과 문자열을 돌려준다."""
    tool, inp = rec["tool"], rec["input"]
    # easyrule은 필터를 비동기로 다시 불러오므로, 차단 테이블에 반영될 때까지 기다린 뒤 확인한다.
    if tool == "request_firewall_block":
        ip = _external_ip(inp["ip"])
        _, out = pfsense_run(
            f"sh -c 'easyrule block wan {ip}; for i in $(seq 30); do "
            f"pfctl -t {BLOCK_TABLE} -T test {ip} >/dev/null 2>&1 && break; sleep 1; done; "
            f"pfctl -k {ip} >/dev/null 2>&1; pfctl -t {BLOCK_TABLE} -T show 2>/dev/null'", timeout=60)
        ok = ip in out.split()
        return ("적용됨: " if ok else "확인 필요: ") + f"{ip} 차단 목록 {'등록 확인, 기존 연결 종료' if ok else '등록 실패'}\n{out[-800:]}"
    if tool == "request_firewall_unblock":
        ip = _external_ip(inp["ip"])
        _, out = pfsense_run(
            f"sh -c 'easyrule unblock wan {ip}; for i in $(seq 30); do "
            f"pfctl -t {BLOCK_TABLE} -T test {ip} >/dev/null 2>&1 || break; sleep 1; done; "
            f"pfctl -t {BLOCK_TABLE} -T show 2>/dev/null'", timeout=60)
        ok = ip not in out.split()
        return ("적용됨: " if ok else "확인 필요: ") + f"{ip} 차단 {'해제 확인' if ok else '해제 실패'}\n{out[-800:]}"
    if tool == "request_ios_config":
        return _apply_ios_change(rec)
    raise ValueError(f"승인 실행을 지원하지 않는 도구: {tool}")
