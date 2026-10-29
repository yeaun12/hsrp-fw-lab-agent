"""웹 공격 탐지 모듈 (보안 분석 역할).

DMZ 웹 서버의 접속 로그를 SSH로 가져와, SQL 인젝션·XSS 시그니처를 규칙으로 1차 탐지한다.
앱이 로그에 남긴 결과(note: login_success, rows 수, sqlerror)를 함께 모아, AI가 공격의 성공 여부를
판단할 근거로 삼는다. 네트워크 점검(audit.py)과 마찬가지로 '규칙 탐지 + AI 판단' 구조다.

메인 에이전트와 같은 프로세스에서 돌지만 도구·지침이 분리돼 있어, 나중에 별도 분석 에이전트로 떼기 쉽다.
"""
import json
import re
import subprocess
import urllib.parse

import config

WEB_HOST = "211.0.1.10"
WEB_LOG = "/var/log/labapp/access.log"
INTERNAL = re.compile(r"^(10\.|211\.0\.1\.)")   # 내부·DMZ 자체 트래픽은 공격자 후보에서 제외

# 공격 시그니처 (디코딩한 문자열에 대해 매칭)
SIGNATURES = [
    ("SQLi", "union-select", re.compile(r"union\s+select", re.I)),
    ("SQLi", "or-tautology", re.compile(r"('|\b)or\b.{0,6}?(\d+\s*=\s*\d+|'[^']*'\s*=\s*'[^']*')", re.I)),
    ("SQLi", "comment", re.compile(r"(--|#|/\*)")),
    ("SQLi", "stacked-query", re.compile(r";\s*(drop|delete|update|insert|alter)\b", re.I)),
    ("SQLi", "sleep-benchmark", re.compile(r"\b(sleep|benchmark|pg_sleep|waitfor\s+delay)\s*\(", re.I)),
    ("XSS", "script-tag", re.compile(r"<\s*script", re.I)),
    ("XSS", "event-handler", re.compile(r"\bon(error|load|mouseover|focus)\s*=", re.I)),
    ("XSS", "js-uri", re.compile(r"javascript:", re.I)),
    ("XSS", "img-svg", re.compile(r"<\s*(img|svg|iframe)\b", re.I)),
]


def _decode(s):
    """URL 인코딩을 두 번까지 풀어 우회를 줄인다."""
    out = s or ""
    for _ in range(2):
        d = urllib.parse.unquote_plus(out)
        if d == out:
            break
        out = d
    return out


def match_signatures(text):
    d = _decode(text)
    return [{"category": cat, "name": name} for cat, name, rx in SIGNATURES if rx.search(d)]


def _ssh_tail(lines):
    cmd = ["ssh", "-i", config.SSH_KEY, "-o", f"UserKnownHostsFile={config.SSH_KNOWN_HOSTS}",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"root@{WEB_HOST}", f"tail -n {int(lines)} {WEB_LOG}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"WEB 로그 수집 실패: {r.stderr.strip()[-200:]}")
    return r.stdout


def _success_hint(rec, sigs):
    """앱이 남긴 결과로 공격 성공 가능성을 추정한다."""
    note = rec.get("note", "")
    if "login_success" in note and rec["path"] == "/login" and any("--" in _decode(rec.get("body", "")) or "or" in _decode(rec.get("body", "")).lower() for _ in [0]):
        role = note.split("role=")[-1] if "role=" in note else ""
        return f"로그인 우회 성공 가능 (권한 {role})"
    m = re.search(r"rows=(\d+)", note)
    if m and any(s["category"] == "SQLi" for s in sigs) and int(m.group(1)) >= 5:
        return f"정상보다 많은 행 반환(rows={m.group(1)}) - 인젝션 성공 가능"
    if "sqlerror" in note:
        return "SQL 오류 유발 - 인젝션 지점 존재(공격 자체는 실패)"
    if any(s["category"] == "XSS" for s in sigs):
        return "검색어가 응답에 반사됨 - XSS 성립 가능"
    return ""


def scan(lines=400, window_only_attacks=True):
    """로그를 가져와 출발지별 공격 시도를 요약한다."""
    raw = _ssh_tail(lines)
    by_src = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if INTERNAL.match(rec.get("ip", "")):
            continue
        sigs = match_signatures(rec.get("query", "")) + match_signatures(rec.get("body", ""))
        # 중복 시그니처 제거
        sigs = list({(s["category"], s["name"]): s for s in sigs}.values())
        if window_only_attacks and not sigs:
            continue
        src = rec["ip"]
        e = by_src.setdefault(src, {"src": src, "attempts": 0, "categories": set(), "sig_names": set(),
                                    "success_hints": [], "samples": []})
        e["attempts"] += 1
        for s in sigs:
            e["categories"].add(s["category"])
            e["sig_names"].add(s["name"])
        hint = _success_hint(rec, sigs)
        if hint and hint not in e["success_hints"]:
            e["success_hints"].append(hint)
        if len(e["samples"]) < 8:
            e["samples"].append({"method": rec["method"], "path": rec["path"],
                                 "input": _decode(rec.get("query") or rec.get("body") or "")[:120],
                                 "status": rec["status"], "note": rec.get("note", "")})
    rows = []
    for e in sorted(by_src.values(), key=lambda x: -x["attempts"]):
        rows.append({"src": e["src"], "attempts": e["attempts"], "categories": sorted(e["categories"]),
                     "signatures": sorted(e["sig_names"]), "success_hints": e["success_hints"], "samples": e["samples"]})
    return rows
