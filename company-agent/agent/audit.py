"""KISA 네트워크 장비 점검(N-01~N-38) - 증적 수집과 규칙 기반 1차 판정.

규칙은 설정 문구로 판정이 분명한 항목만 판정하고(IOS 12.4 기본값 반영), 맥락이 필요한 항목은
'수동확인'으로 남겨 에이전트(AI)가 판정한다. 결과는 data/audits/<id>.json에 저장한다.
"""
import json
import os
import re
import time

import config
from devices import DeviceError, ios_run

AUDIT_DEVICES = ["CORE1", "CORE2", "ASW1", "ASW2", "BRANCH"]
EVIDENCE_COMMANDS = ["show running-config", "show version", "show ip interface brief", "show cdp", "show snmp"]
GOOD, VULN, MANUAL = "양호", "취약", "수동확인"
AUDIT_DIR = os.path.join(config.DATA_DIR, "audits")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kisa_network_checklist.json"), encoding="utf-8") as f:
    CHECKLIST = json.load(f)
ITEMS = {it["code"]: it for it in CHECKLIST["items"]}


# ------------------------------------------------------------------ 설정 파싱

def _blocks(cfg, prefix):
    """'interface X' / 'line vty 0 4' 같은 블록을 {머리줄: [하위 줄]}로 돌려준다."""
    out, cur = {}, None
    for line in cfg.splitlines():
        if line.startswith(prefix):
            cur = line.strip()
            out[cur] = []
        elif cur and line.startswith(" "):
            out[cur].append(line.strip())
        elif not line.startswith(" "):
            cur = None
    return out


def _has(cfg, pattern):
    return re.search(pattern, cfg, re.M) is not None


def _lines(cfg, pattern):
    return [l.strip() for l in cfg.splitlines() if re.search(pattern, l)]


def _l3_interfaces(cfg):
    """IP 주소가 있는(라우팅에 참여하는) 인터페이스와 하위 설정."""
    return {k: v for k, v in _blocks(cfg, "interface ").items()
            if any(x.startswith("ip address ") for x in v) and not any(x == "shutdown" for x in v)}


def _exec_timeout_minutes(sub):
    m = next((re.match(r"exec-timeout (\d+)(?: (\d+))?", x) for x in sub if x.startswith("exec-timeout")), None)
    if not m:
        return 10.0                       # IOS 기본값 10분
    mins, secs = int(m.group(1)), int(m.group(2) or 0)
    return float("inf") if mins == 0 and secs == 0 else mins + secs / 60


# ------------------------------------------------------------------ 규칙 (code -> fn(ev) -> (판정, 근거))

def _r(verdict, evidence):
    return verdict, evidence


def n01(ev):
    cfg = ev["cfg"]
    issues = []
    if not _has(cfg, r"^enable (secret|password) "):
        issues.append("enable 비밀번호 없음")
    for name, sub in _blocks(cfg, "line ").items():
        if name.startswith("line vty") or name.startswith("line con") or name.startswith("line aux"):
            auth = any(x.startswith("login") and x != "no login" for x in sub) or any(x.startswith("password") for x in sub)
            if not auth and not (name.startswith("line aux") and "no exec" in sub):
                issues.append(f"{name}: 로그인 인증 없음")
    return _r(VULN, "; ".join(issues)) if issues else _r(GOOD, "enable 비밀번호와 각 라인 로그인 인증 설정됨")


def n02(ev):
    m = _lines(ev["cfg"], r"^security passwords min-length")
    return _r(GOOD, m[0]) if m else _r(VULN, "security passwords min-length 미설정 (비밀번호 최소 길이 정책 없음)")


def n03(ev):
    cfg = ev["cfg"]
    issues = []
    if not _has(cfg, r"^service password-encryption"):
        issues.append("service password-encryption 미설정")
    if _has(cfg, r"^enable password "):
        issues.append("enable password(평문/약한 암호화) 사용")
    if _has(cfg, r"^username \S+ .*password "):
        issues.append("username ... password 사용 (secret 권장)")
    return _r(VULN, "; ".join(issues)) if issues else _r(GOOD, "비밀번호 암호화 설정됨")


def n04(ev):
    m = _lines(ev["cfg"], r"^login block-for")
    if not m:
        return _r(VULN, "login block-for 미설정 (로그인 실패 임계값 없음)")
    a = re.search(r"attempts (\d+)", m[0])
    return _r(GOOD if a and int(a.group(1)) <= 5 else VULN, m[0])


def n05(ev):
    return _r(MANUAL, "계정 권한: " + ("; ".join(_lines(ev["cfg"], r"^(username|privilege) ")) or "로컬 계정 없음"))


def n06(ev):
    vty = {k: v for k, v in _blocks(ev["cfg"], "line vty").items()}
    if not vty:
        return _r(MANUAL, "VTY 설정을 찾지 못함")
    missing = [k for k, v in vty.items() if not any(x.startswith("access-class") for x in v)]
    acl = [x for v in vty.values() for x in v if x.startswith("access-class")]
    return _r(VULN, f"access-class 없음: {', '.join(missing)}") if missing else _r(GOOD, ", ".join(sorted(set(acl))))


def n07(ev):
    bad = []
    for name, sub in _blocks(ev["cfg"], "line ").items():
        t = _exec_timeout_minutes(sub)
        if t > 10:
            bad.append(f"{name}: {'무제한(exec-timeout 0 0)' if t == float('inf') else f'{t:g}분'}")
    return _r(VULN, "; ".join(bad)) if bad else _r(GOOD, "모든 라인 10분 이하 (미설정 라인은 기본 10분)")


def n08(ev):
    bad = []
    for name, sub in _blocks(ev["cfg"], "line vty").items():
        ti = next((x for x in sub if x.startswith("transport input")), "transport input (기본값: 모두 허용)")
        if "ssh" not in ti or "telnet" in ti or "all" in ti or "기본값" in ti:
            bad.append(f"{name}: {ti}")
    return _r(VULN, "; ".join(bad)) if bad else _r(GOOD, "VTY는 SSH만 허용")


def n09(ev):
    aux = _blocks(ev["cfg"], "line aux")
    if not aux:
        return _r(GOOD, "AUX 포트 없음")
    bad = [k for k, v in aux.items() if "no exec" not in v and "transport input none" not in v]
    return _r(VULN, f"{', '.join(bad)}: no exec/transport input none 미설정") if bad else _r(GOOD, "AUX 포트 사용 제한됨")


WARNING_WORDS = re.compile(r"unauthori[sz]ed|authorized|prohibited|warning|경고|무단|허가|권한", re.I)


def n10(ev):
    banners = {}
    for m in re.finditer(r"^banner (motd|login|exec) (\^C|\S)(.*?)\2", ev["cfg"], re.M | re.S):
        banners[m.group(1)] = re.sub(r"\s+", " ", m.group(3)).strip()
    if not banners:
        return _r(VULN, "로그온 경고 배너(banner motd/login) 없음")
    pre_login = {k: v for k, v in banners.items() if k in ("motd", "login")}
    if pre_login and all(WARNING_WORDS.search(v) for v in pre_login.values()):
        return _r(GOOD, "; ".join(f"banner {k}: {v[:60]}" for k, v in pre_login.items()))
    # 배너는 있으나 로그인 전 경고가 아니거나 경고 문구가 아님 -> 내용 판단은 AI에게
    return _r(MANUAL, "; ".join(f"banner {k}: {v[:90]}" for k, v in banners.items())
              + " (로그인 전 경고 문구인지 확인 필요)")


def n11(ev):
    m = _lines(ev["cfg"], r"^logging (host )?\d+\.\d+\.\d+\.\d+")
    return _r(GOOD, ", ".join(m)) if m else _r(VULN, "원격 로그 서버(logging host) 미설정")


def n12(ev):
    v = re.search(r"Cisco IOS Software.*?Version ([^\s,]+)", ev["version"], re.S)
    return _r(MANUAL, f"IOS 버전 {v.group(1) if v else '확인 불가'} - 벤더 보안 권고·지원 상태 확인 필요")


def n13(ev):
    m = _lines(ev["cfg"], r"^logging buffered")
    if not m:
        return _r(VULN, "logging buffered 미설정 (기본 버퍼 크기 사용)")
    size = re.search(r"logging buffered (\d+)", m[0])
    return _r(GOOD if size and int(size.group(1)) >= 16000 else MANUAL, m[0])


def n14(ev):
    return _r(MANUAL, "로깅 설정: " + ("; ".join(_lines(ev["cfg"], r"^(logging|no logging) ")) or "logging 관련 설정 없음"))


def n15(ev):
    m = _lines(ev["cfg"], r"^ntp server")
    return _r(GOOD, ", ".join(m)) if m else _r(VULN, "NTP 서버 미설정")


def n16(ev):
    m = _lines(ev["cfg"], r"^service timestamps log datetime")
    return _r(GOOD, m[0]) if m else _r(VULN, "service timestamps log datetime 미설정")


def _snmp_on(ev):
    return _has(ev["cfg"], r"^snmp-server community") or ("not enabled" not in ev["snmp"] and "Invalid" not in ev["snmp"]
                                                          and ev["snmp"].strip() != "")


def n17(ev):
    if not _snmp_on(ev):
        return _r(GOOD, "SNMP 비활성 (show snmp: agent not enabled)")
    return _r(MANUAL, "SNMP 사용 중: " + "; ".join(_lines(ev["cfg"], r"^snmp-server")))


def n18(ev):
    if not _snmp_on(ev):
        return _r(GOOD, "SNMP 비활성")
    weak = [l for l in _lines(ev["cfg"], r"^snmp-server community") if re.match(r"snmp-server community (public|private)\b", l)]
    return _r(VULN, f"기본 community 사용: {weak}") if weak else _r(MANUAL, "community 복잡성 확인 필요")


def n19(ev):
    if not _snmp_on(ev):
        return _r(GOOD, "SNMP 비활성")
    no_acl = [l for l in _lines(ev["cfg"], r"^snmp-server community") if len(l.split()) < 5]
    return _r(VULN, f"ACL 없는 community: {len(no_acl)}개") if no_acl else _r(GOOD, "community에 ACL 지정")


def n20(ev):
    if not _snmp_on(ev):
        return _r(GOOD, "SNMP 비활성")
    rw = _lines(ev["cfg"], r"^snmp-server community \S+ RW")
    return _r(VULN, f"RW community {len(rw)}개") if rw else _r(GOOD, "RO community만 사용")


def n21(ev):
    m = _lines(ev["cfg"], r"^tftp-server")
    return _r(VULN, ", ".join(m)) if m else _r(GOOD, "tftp-server 미설정")


def n22(ev):
    acl_in = [f"{k}: {x}" for k, v in _l3_interfaces(ev["cfg"]).items() for x in v if re.match(r"ip (access-group \S+ in|verify unicast)", x)]
    return _r(MANUAL, "인터페이스 유입 필터: " + ("; ".join(acl_in) or "없음") + " (경계 장비 여부 판단 필요)")


def n23(ev):
    return _r(MANUAL, "DDoS 방어는 경계 방화벽(pfSense)·ISP 구간 판단 필요")


def n24(ev):
    cands = []
    for line in ev["ipbrief"].splitlines():
        f = line.split()
        if len(f) >= 6 and re.match(r"^(Fast|Gigabit)?Ethernet|^FastEthernet", f[0]):
            status = " ".join(f[4:-1])
            if status != "administratively down" and f[-1] == "down":
                cands.append(f[0])
    if not cands:
        return _r(GOOD, "링크가 없는데 활성화된 인터페이스 없음")
    return _r(VULN, f"링크 없음(down)인데 shutdown 안 된 포트 {len(cands)}개: {', '.join(cands[:12])}{' ...' if len(cands) > 12 else ''}")


def n25(ev):
    cfg = ev["cfg"]
    missing = [s for s in ("service tcp-keepalives-in", "service tcp-keepalives-out") if not _has(cfg, rf"^{s}")]
    return _r(VULN, ", ".join(missing) + " 미설정") if missing else _r(GOOD, "TCP keepalive 설정됨")


def n26(ev):
    m = _lines(ev["cfg"], r"^(ip finger|service finger)")
    return _r(VULN, ", ".join(m)) if m else _r(GOOD, "finger 미사용 (12.4 기본 비활성)")


def n27(ev):
    m = _lines(ev["cfg"], r"^ip http (secure-)?server")
    return _r(VULN, ", ".join(m)) if m else _r(GOOD, "ip http server 미설정")


def n28(ev):
    m = _lines(ev["cfg"], r"^service (tcp|udp)-small-servers")
    return _r(VULN, ", ".join(m)) if m else _r(GOOD, "small servers 미사용 (기본 비활성)")


def n29(ev):
    return _r(GOOD, "no ip bootp server") if _has(ev["cfg"], r"^no ip bootp server") else _r(VULN, "ip bootp server 기본 활성 (no ip bootp server 미설정)")


def n30(ev):
    if _has(ev["cfg"], r"^no cdp run") or "not enabled" in ev["cdp"]:
        return _r(GOOD, "CDP 비활성")
    return _r(VULN, "CDP 활성 (기본값, no cdp run 미설정)")


def n31(ev):
    m = [f"{k}" for k, v in _blocks(ev["cfg"], "interface ").items() if "ip directed-broadcast" in v]
    return _r(VULN, "directed-broadcast 허용: " + ", ".join(m)) if m else _r(GOOD, "directed-broadcast 미허용 (기본 비활성)")


def n32(ev):
    return _r(GOOD, "no ip source-route") if _has(ev["cfg"], r"^no ip source-route") else _r(VULN, "ip source-route 기본 활성 (no ip source-route 미설정)")


def n33(ev):
    l3 = _l3_interfaces(ev["cfg"])
    bad = [k.replace("interface ", "") for k, v in l3.items() if "no ip proxy-arp" not in v]
    return _r(VULN, f"proxy-arp 활성 L3 인터페이스 {len(bad)}개: {', '.join(bad)}") if bad else _r(GOOD, "모든 L3 인터페이스 no ip proxy-arp")


def n34(ev):
    l3 = _l3_interfaces(ev["cfg"])
    bad = [k.replace("interface ", "") for k, v in l3.items() if "no ip unreachables" not in v or "no ip redirects" not in v]
    return _r(VULN, f"unreachables/redirects 허용 L3 인터페이스 {len(bad)}개: {', '.join(bad)}") if bad else _r(GOOD, "모든 L3 인터페이스 차단")


def n35(ev):
    m = _lines(ev["cfg"], r"^ip identd")
    return _r(VULN, ", ".join(m)) if m else _r(GOOD, "identd 미사용 (기본 비활성)")


def n36(ev):
    return _r(GOOD, "no ip domain lookup") if _has(ev["cfg"], r"^no ip domain[- ]lookup") else _r(VULN, "domain lookup 활성 (기본값)")


def n37(ev):
    return _r(GOOD, "no service pad") if _has(ev["cfg"], r"^no service pad") else _r(VULN, "PAD 서비스 기본 활성 (no service pad 미설정)")


def n38(ev):
    m = [k for k, v in _blocks(ev["cfg"], "interface ").items() if "ip mask-reply" in v]
    return _r(VULN, "mask-reply 허용: " + ", ".join(m)) if m else _r(GOOD, "mask-reply 미허용 (기본 비활성)")


RULES = {f"N-{i:02d}": fn for i, fn in enumerate(
    [n01, n02, n03, n04, n05, n06, n07, n08, n09, n10, n11, n12, n13, n14, n15, n16, n17, n18, n19, n20,
     n21, n22, n23, n24, n25, n26, n27, n28, n29, n30, n31, n32, n33, n34, n35, n36, n37, n38], start=1)}


# ------------------------------------------------------------------ 실행

SECRET_VALUE = re.compile(r"\b((?:secret|password|key)(?: \d)? )\S+")


def collect(device):
    out = dict(ios_run(device, EVIDENCE_COMMANDS, timeout=40))
    # 비밀번호 해시·평문은 판정에 필요 없으므로 AI·대시보드·파일로 넘기기 전에 가린다 (유형 번호는 N-03 판정에 남김)
    cfg = SECRET_VALUE.sub(r"\1<가림>", out["show running-config"])
    return {"cfg": cfg, "version": out["show version"],
            "ipbrief": out["show ip interface brief"], "cdp": out["show cdp"], "snmp": out["show snmp"]}


def evaluate(evidence_by_device):
    """{장비: 증적} -> 항목별 장비별 판정."""
    items = []
    for code, rule in RULES.items():
        meta = ITEMS[code]
        per = {}
        for dev, ev in evidence_by_device.items():
            try:
                v, e = rule(ev)
            except Exception as ex:  # 규칙 하나가 실패해도 점검 전체는 계속
                v, e = MANUAL, f"규칙 실행 오류: {ex}"
            per[dev] = {"verdict": v, "evidence": e}
        items.append({"code": code, "name": meta["name"], "importance": meta["importance"],
                      "category": meta["category"], "results": per})
    return items


def summarize(items):
    s = {"양호": 0, "취약": 0, "수동확인": 0, "해당없음": 0, "취약_상": 0}
    for it in items:
        for r in it["results"].values():
            s[r["verdict"]] = s.get(r["verdict"], 0) + 1
            if r["verdict"] == VULN and it["importance"] == "상":
                s["취약_상"] += 1
    return s


def run_audit(devices=None, actor="agent"):
    devices = devices or AUDIT_DEVICES
    evidence, errors = {}, {}
    for dev in devices:
        try:
            evidence[dev] = collect(dev)
        except DeviceError as e:
            errors[dev] = str(e)
    items = evaluate(evidence)
    audit = {"id": time.strftime("%Y%m%d-%H%M%S"), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "actor": actor,
             "standard": CHECKLIST["source"], "devices": list(evidence), "errors": errors,
             "summary": summarize(items), "items": items, "report": None}
    save(audit)
    return audit


def save(audit):
    os.makedirs(AUDIT_DIR, exist_ok=True)
    with open(os.path.join(AUDIT_DIR, f"{audit['id']}.json"), "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)


def load(audit_id):
    if not re.fullmatch(r"\d{8}-\d{6}", audit_id or ""):
        raise ValueError("잘못된 점검 ID")
    with open(os.path.join(AUDIT_DIR, f"{audit_id}.json"), encoding="utf-8") as f:
        return json.load(f)


def list_audits():
    if not os.path.isdir(AUDIT_DIR):
        return []
    out = []
    for name in sorted(os.listdir(AUDIT_DIR), reverse=True)[:20]:
        try:
            a = load(name[:-5])
            out.append({k: a[k] for k in ("id", "time", "actor", "devices", "summary")} | {"has_report": bool(a.get("report"))})
        except (OSError, ValueError):
            continue
    return out
