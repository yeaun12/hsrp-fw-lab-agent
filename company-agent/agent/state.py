"""대시보드, 에이전트, 감시 기능이 함께 쓰는 상태. 감사 로그와 대화는 파일에도 남긴다."""
import itertools
import json
import os
import threading
import time

import config

lock = threading.RLock()
_ids = itertools.count(1)

alerts = []        # {"id","time","kind","severity","title","detail","key"}
approvals = []     # {"id","time","tool","input","summary","reason","status","result","decided_at"}
audit = []         # {"time","actor","action","detail"}
chat = []          # 화면 표시용 대화 이벤트 {"time","kind","text",...}
status = {"devices": {}, "hsrp": {}, "gateways": {}, "blocked_ips": [], "updated": None}
agent_busy = {"value": False, "since": None}

# 조사 건(case): 관리자 요청/경보 하나를 조사→판단→승인→실행→재검증까지 묶은 기록.
# Phase 2 — 관리자가 AI 판단 근거를 검증할 수 있게 관측 가능한 사실을 구조화해 남긴다.
cases = []                      # 최신이 뒤. {"id","started","source","trigger","model",...}
active_case = {"id": None}      # 진행 중 조사 id. 에이전트 워커가 단일 스레드라 안전하다.


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append_file(name, record):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, name), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_json(name, obj):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = os.path.join(config.DATA_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(config.DATA_DIR, name))


def get_case(cid):
    return next((c for c in cases if c["id"] == cid), None)


def current_case():
    return get_case(active_case["id"])


def open_case(source, trigger):
    with lock:
        rec = {"id": next(_ids), "started": now(), "ts": time.time(), "source": source,
               "trigger": trigger, "model": "", "provider": "", "status": "investigating",
               "steps": 0, "tools": [], "devices": [], "cause_candidates": [], "basis": "",
               "proposed_action": "", "resolution": "", "reported": False, "approval_ids": [], "execution_result": "",
               "reverification": "", "elapsed_sec": 0,
               "tokens": {"calls": 0, "input": 0, "output": 0}, "updated": now()}
        cases.append(rec)
        del cases[:-60]
        active_case["id"] = rec["id"]
    save_cases()
    return rec


def save_cases():
    try:
        with lock:
            data = [{k: v for k, v in c.items() if k != "ts"} for c in cases]
        _write_json("cases.json", data)
    except OSError:
        pass


def load_cases():
    try:
        with open(os.path.join(config.DATA_DIR, "cases.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    with lock:
        cases.clear()
        for c in data:
            c.setdefault("ts", time.time())
            cases.append(c)
    _resume_ids()


def save_approvals():
    """승인 요청은 재시작해도 남아야 한다(대기 중인 요청이 사라지면 관리자가 판단할 기회를 잃는다)."""
    try:
        with lock:
            data = list(approvals[-100:])
        _write_json("approvals.json", data)
    except OSError:
        pass


def load_approvals():
    try:
        with open(os.path.join(config.DATA_DIR, "approvals.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    with lock:
        approvals.clear()
        for a in data:
            if a.get("status") == "running":     # 실행 도중 재시작: 결과를 모르므로 성공으로 두지 않는다
                a["status"] = "failed"
                a["result"] = ((a.get("result") or "") + "\n에이전트가 실행 도중 재시작되어 결과를 확인하지 못했습니다. "
                               "장비에 적용됐는지 직접 확인하세요.").strip()
            approvals.append(a)
    _resume_ids()


def _resume_ids():
    """번호는 저장된 조사·승인 중 가장 큰 번호 다음부터 이어 간다(재시작 후 번호가 겹치지 않게)."""
    global _ids
    with lock:
        top = max([c.get("id", 0) for c in cases] + [a.get("id", 0) for a in approvals] + [0])
        _ids = itertools.count(top + 1)


def log_audit(actor, action, detail):
    rec = {"time": now(), "actor": actor, "action": action, "detail": detail}
    with lock:
        audit.append(rec)
        del audit[:-300]
    _append_file("audit.jsonl", rec)


def add_chat(kind, text, **extra):
    rec = {"time": now(), "kind": kind, "text": text, **extra}
    with lock:
        chat.append(rec)
        del chat[:-400]
    _append_file("chat.jsonl", rec)


def add_alert(kind, severity, title, detail, key):
    """같은 key의 경보가 10분 안에 이미 있으면 무시하고 None을 돌려준다."""
    with lock:
        for a in reversed(alerts):
            if a["key"] == key and time.time() - a["ts"] < 600:
                return None
        rec = {"id": next(_ids), "time": now(), "ts": time.time(), "kind": kind,
               "severity": severity, "title": title, "detail": detail, "key": key}
        alerts.append(rec)
        del alerts[:-100]
    log_audit("monitor", "alert", f"[{severity}] {title}")
    return rec


def add_approval(tool, tool_input, summary, reason):
    with lock:
        cid = active_case["id"]
        rec = {"id": next(_ids), "time": now(), "tool": tool, "input": tool_input, "summary": summary,
               "reason": reason, "status": "pending", "result": None, "decided_at": None, "case_id": cid}
        approvals.append(rec)
        c = get_case(cid)
        if c:                                   # 이 승인 요청을 낸 조사 건과 연결
            c["approval_ids"].append(rec["id"])
            c["status"] = "awaiting_approval"
            c["updated"] = now()
    log_audit("agent", "approval_requested", f"#{rec['id']} {summary} (사유: {reason})")
    save_cases()
    save_approvals()
    return rec


def snapshot():
    with lock:
        return {
            "alerts": [{k: v for k, v in a.items() if k != "ts"} for a in alerts[-30:]][::-1],
            "approvals": list(approvals[-30:])[::-1],
            "audit": audit[-60:][::-1],
            "chat": chat[-200:],
            "status": status,
            "agent_busy": agent_busy["value"],
            "cases": [{k: v for k, v in c.items() if k != "ts"} for c in cases[-12:]][::-1],
        }
