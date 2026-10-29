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


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append_file(name, record):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, name), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
        rec = {"id": next(_ids), "time": now(), "tool": tool, "input": tool_input, "summary": summary,
               "reason": reason, "status": "pending", "result": None, "decided_at": None}
        approvals.append(rec)
    log_audit("agent", "approval_requested", f"#{rec['id']} {summary} (사유: {reason})")
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
        }
