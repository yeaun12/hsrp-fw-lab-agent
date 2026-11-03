"""사내 IT 운영 에이전트 - 웹 대시보드 서버 (진입점).

    /opt/lab-agent/venv/bin/python server.py

Flask 앱을 waitress(순수 파이썬 WSGI 서버)로 띄운다. 감시·에이전트 스레드가 프로세스 안에 있으므로
워커는 반드시 한 프로세스(스레드 여러 개)로 둔다 — 프로세스를 늘리면 감시와 에이전트가 중복 실행된다.
"""
import json
import os
import re
import subprocess
import threading
import time

from flask import Flask, Response, request, send_from_directory

import audit
import brain
import config
import providers
import web_detect
import monitor
import state
import tools

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_audit_running = threading.Event()


def _decide(approval_id, approve, actor="관리자"):
    with state.lock:
        rec = next((a for a in state.approvals if a["id"] == approval_id), None)
        if rec is None or rec["status"] != "pending":
            return None
        rec["status"] = "running" if approve else "rejected"
        rec["decided_at"] = state.now()
    state.save_approvals()
    if not approve:
        state.log_audit(actor, "rejected", f"#{rec['id']} {rec['summary']}")
        brain.submit(f"[관리자 거부] 승인 요청 #{rec['id']}({rec['summary']})을 관리자가 거부했습니다. 실행하지 않았습니다.",
                     "approval")
        return rec

    def run():
        state.log_audit(actor, "approved", f"#{rec['id']} {rec['summary']}")
        with state.lock:                             # 승인 실행 시작 → 사건 상태 '조치중'
            c = state.get_case(rec.get("case_id"))
            if c:
                c["status"] = "executing"
                c["updated"] = state.now()
        try:
            result = tools.execute_approved(rec)
            status = "done"
        except Exception as e:
            result, status = f"실행 실패: {type(e).__name__}: {e}", "failed"
        with state.lock:
            rec.update(status=status, result=result)
            c = state.get_case(rec.get("case_id"))
            if c:                                    # 실행 결과를 조사 건에 연결하고 재검증 단계로
                c["execution_result"] = result
                c["status"] = "verifying"
                c["updated"] = state.now()
        state.save_cases()
        state.save_approvals()
        state.log_audit("agent", "executed" if status == "done" else "failed", f"#{rec['id']} {result.splitlines()[0]}")
        brain.submit(f"[관리자 승인] 승인 요청 #{rec['id']}({rec['summary']})을 실행했습니다.\n실행 결과:\n{result}\n"
                     "의도대로 적용됐는지 조회로 검증하고 보고하세요.", "approval")

    threading.Thread(target=run, daemon=True).start()
    return rec


app = Flask(__name__, static_folder=None)


def _json(obj, code=200):
    # 날짜 등 직렬화 안 되는 값은 문자열로, 한글은 그대로 내보낸다(기존 응답 형식 유지).
    return Response(json.dumps(obj, ensure_ascii=False, default=str), code,
                    content_type="application/json; charset=utf-8")


def _body():
    return request.get_json(force=True, silent=True) or {}


def _warehouse():
    import warehouse
    conn, stats = warehouse.ingest(config.DATA_DIR, os.path.join(config.DATA_DIR, "ops_dw.sqlite"))  # 최신 로그를 멱등 적재
    return conn, stats


@app.errorhandler(404)
@app.errorhandler(405)
def _not_found(e):
    return _json({"error": "not found"}, 404)


@app.get("/")
@app.get("/index.html")
def index():
    return send_from_directory(STATIC, "index.html", max_age=0)


@app.get("/api/state")
def api_state():
    snap = state.snapshot()
    info = providers.provider_info()
    snap["llm"] = info
    snap["api_key_set"] = info["ready"]          # 하위 호환
    snap["model"] = info["model"]
    with state.lock:
        snap["usage"] = dict(brain.usage)
    snap["audits"] = audit.list_audits()[:3]
    snap["audit_running"] = _audit_running.is_set()
    snap["inventory"] = {n: {"host": d["host"], "role": d["role"], "type": d["type"]}
                         for n, d in config.DEVICES.items()}
    snap["scan_min_ports"] = config.SCAN_MIN_PORTS
    snap["scan_window_sec"] = config.SCAN_WINDOW_SEC
    snap["now"] = state.now()               # 경과 시간은 서버 시각 기준으로 계산(브라우저 시계와 어긋날 수 있음)
    return _json(snap)


@app.get("/api/analytics")
def api_analytics():
    try:
        import analytics
        conn, stats = _warehouse()
        k = analytics.kpis(conn)
        conn.close()
        k["ingest"] = stats
        return _json(k)
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}"}, 500)


@app.get("/api/overview")
def api_overview():
    # 개요 화면용 추세·처리 단계. 경보는 감시가 올린 것만 센다
    # (같은 경보가 에이전트 대화로 전달되며 conversation 범주에 한 번 더 적재되므로 제외).
    try:
        conn, _ = _warehouse()
        today = time.strftime("%Y-%m-%d")
        alert = "event_type='alert' AND category<>'conversation'"
        rows = lambda sql, *a: [dict(r) for r in conn.execute(sql, a)]
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0
        appr = {r["event_type"]: r["n"] for r in rows("SELECT * FROM v_approvals")}
        out = {
            "today": today,
            "alerts_daily": rows(f"SELECT substr(occurred_at,1,10) day, COUNT(*) n FROM events WHERE {alert} "
                                 "GROUP BY day ORDER BY day"),
            "incidents_daily": rows("SELECT day, n FROM v_incidents_daily ORDER BY day"),
            "alerts_today_hourly": rows(f"SELECT CAST(substr(occurred_at,12,2) AS INTEGER) h, COUNT(*) n FROM events "
                                        f"WHERE {alert} AND occurred_at LIKE ? GROUP BY h", today + "%"),
            "funnel": {"alerts": one(f"SELECT COUNT(*) FROM events WHERE {alert}"),
                       "incidents": one("SELECT COUNT(*) FROM incidents"),
                       "tooled": one("SELECT COUNT(DISTINCT incident_id) FROM tool_calls"),
                       "proposed": appr.get("approval_requested", 0),
                       "approved": appr.get("approved", 0)},
            "by_device": [r for r in rows("SELECT device, n FROM v_incident_device ORDER BY n DESC")
                          if r["device"] in config.DEVICES][:6],
        }
        conn.close()
        return _json(out)
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}"}, 500)


@app.get("/api/insights")
def api_insights():
    # Phase 8 통계 이상 탐지 + 이벤트 상관분석을 대시보드에 노출한다(최신 로그를 멱등 적재 후 재계산).
    try:
        import anomaly
        import correlate
        conn, _ = _warehouse()
        groups = correlate.correlate(conn)
        basis = {r["group_id"]: r["verified_basis"] for r in
                 conn.execute("SELECT group_id, verified_basis FROM correlation_groups")}
        out = {"readiness": anomaly.ml_readiness(conn), "profile": anomaly.data_profile(conn),
               "z_threshold": anomaly.Z_THRESHOLD, "anomalies": anomaly.detect(conn),
               "window_sec": correlate.WINDOW_SEC,
               "groups": [{**g, "verified_basis": basis.get(g["group_id"])} for g in groups][::-1][:20]}
        conn.close()
        return _json(out)
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}"}, 500)


@app.get("/api/webattacks")
def api_webattacks():
    try:
        return _json({"sources": web_detect.scan(lines=400)})
    except Exception as e:
        return _json({"error": str(e)}, 502)


@app.get("/api/audit")
def api_audit():
    try:
        return _json(audit.load(request.args.get("id", "")))
    except (OSError, ValueError):
        return _json({"error": "점검 결과를 찾을 수 없습니다."}, 404)


@app.post("/api/chat")
def api_chat():
    text = (_body().get("text") or "").strip()
    if not text:
        return _json({"error": "빈 메시지"}, 400)
    brain.submit(text, "admin")
    return _json({"ok": True})


@app.post("/api/audit/run")
def api_audit_run():
    if _audit_running.is_set():
        return _json({"error": "점검이 이미 진행 중입니다."}, 409)
    _audit_running.set()

    def run():
        try:
            a = audit.run_audit(actor="관리자")
            state.log_audit("관리자", "security_audit", f"규칙 점검 {a['id']}: {a['summary']}")
        except Exception as e:
            state.log_audit("system", "error", f"보안 점검 실패: {e}")
        finally:
            _audit_running.clear()

    threading.Thread(target=run, daemon=True).start()
    return _json({"ok": True})


@app.post("/api/reset")
def api_reset():
    ok = brain.reset_conversation()
    return _json({"ok": ok} if ok else {"error": "에이전트가 작업 중입니다. 끝난 뒤 다시 시도하세요."},
                 200 if ok else 409)


@app.post("/api/approve")
@app.post("/api/reject")
def api_decide():
    rec = _decide(int(_body().get("id", 0)), request.path == "/api/approve")
    return _json({"ok": rec is not None})


@app.post("/api/apikey")
def api_apikey():
    data = _body()
    prov = (data.get("provider") or "").strip()
    key = (data.get("key") or "").strip()
    if prov not in config.PROVIDERS:
        return _json({"error": "알 수 없는 제공자입니다."}, 400)
    cfg = config.PROVIDERS[prov]
    if cfg["key_secret"]:                        # 키가 필요한 제공자
        prefixes = cfg.get("key_prefix") or ()
        if isinstance(prefixes, str):
            prefixes = (prefixes,)
        if key and prefixes and not any(key.startswith(p) for p in prefixes):
            return _json({"error": f"{cfg['label']} 키 형식이 올바르지 않습니다 (예: {', '.join(prefixes)})."}, 400)
        if key:
            config.write_secret(cfg["key_secret"], key)
        elif not config.provider_key(prov):
            return _json({"error": f"{cfg['label']} API 키를 입력하세요."}, 400)
    config.write_secret("llm_provider", prov)
    state.log_audit("관리자", "llm_set", f"모델 제공자 {prov} 선택" + (" + 키 등록" if key else ""))
    return _json({"ok": True, "llm": providers.provider_info()})


# ---- 멀티 에이전트: 역할이 다른 에이전트들의 상태와 보고 ----
AGENTS = [
    {"id": "response", "name": "대응 에이전트", "role": "경보와 관리자 요청을 조사하고 조치를 제안", "perm": "조회 자유, 변경은 승인 후 실행",
     "runs": "경보·요청이 올 때 (서버 안)"},
    {"id": "monitor", "name": "감시", "role": "장비 응답, 방화벽 차단, 웹 로그를 규칙으로 감시 (AI 없음)", "perm": "조회만",
     "runs": f"{config.POLL_FIREWALL_SEC}~{config.POLL_NETWORK_SEC}초 주기 (서버 안)"},
    {"id": "drift", "name": "변경 감지 에이전트", "role": "장비 설정을 직전 기준과 비교하고 승인 기록과 대조, AI가 위험 해석",
     "perm": "설정 조회 명령 2개만 (변경 불가)", "runs": "15분 주기 (별도 프로세스, lab-drift.timer)"},
    {"id": "report", "name": "보고 에이전트", "role": "운영 데이터를 집계해 일일·주간 보고서 작성 (숫자는 코드, 문장은 AI)",
     "perm": "읽기 전용 (웨어하우스·상태 API), 장비 접속 없음", "runs": "매일 08:30, 월요일은 주간 포함 (별도 프로세스, lab-report.timer)"},
]
UNITS = {"drift": ("lab-drift", "변경 점검"), "report": ("lab-report", "보고서 작성")}   # 별도 프로세스로 도는 에이전트
REPORT_NAME = re.compile(r"^\d{4}-\d\d-\d\d-\d{4}-(daily|weekly)$")


def _systemctl(*args):
    try:
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _reports(limit=30):
    d = os.path.join(config.DATA_DIR, "reports")
    try:
        names = sorted((n[:-5] for n in os.listdir(d) if n.endswith(".json")), reverse=True)[:limit]
    except OSError:
        return []
    out = []
    for n in names:
        m = _load(os.path.join(d, n + ".json")) or {}
        out.append({k: m.get(k) for k in ("name", "kind", "created", "period", "author", "summary", "unverified_numbers")})
    return out


def _unit_state(aid):
    unit = UNITS[aid][0]
    if _systemctl("is-active", unit + ".service") == "activating":
        return "작업 중", None
    # 주기 타이머는 NextElapseUSecRealtime이 비어 있을 수 있어 list-timers의 NEXT 열을 읽는다.
    tok = _systemctl("list-timers", unit + ".timer", "--no-pager", "--no-legend").split()
    nxt = " ".join(tok[1:3]) if len(tok) > 3 and tok[0] != "-" else None
    return ("대기" if _systemctl("is-active", unit + ".timer") == "active" else "타이머 꺼짐"), nxt


@app.get("/api/agents")
def api_agents():
    with state.lock:
        last_case = state.cases[-1] if state.cases else None
        busy = state.agent_busy["value"]
        mon = state.status.get("updated")
    out = []
    for a in AGENTS:
        a = dict(a)
        if a["id"] == "response":
            a["state"] = "작업 중" if busy else "대기"
            a["last"] = last_case and {"time": last_case.get("started"), "text": f"조사 #{last_case['id']}",
                                       "status": last_case.get("status")}
        elif a["id"] == "monitor":
            a["state"] = "동작 중" if mon else "시작 중"
            a["last"] = mon and {"time": mon, "text": "장비·방화벽·웹 로그 확인"}
        else:
            a["state"], a["next"] = _unit_state(a["id"])
            if a["id"] == "drift":
                d = a["result"] = _load(os.path.join(config.DATA_DIR, "drift_last.json"))
                a["last"] = d and {"time": d.get("finished"), "text": d.get("summary")}
            else:
                r = (_reports(1) or [None])[0]
                a["last"] = r and {"time": r["created"], "text": r["summary"]}
        out.append(a)
    return _json({"agents": out})


@app.get("/api/reports")
def api_reports():
    return _json({"reports": _reports()})


@app.get("/api/reports/<name>")
def api_report(name):
    if not REPORT_NAME.match(name):                 # 경로 조작 방지: 보고서 이름 형식만 허용
        return _json({"error": "not found"}, 404)
    d = os.path.join(config.DATA_DIR, "reports")
    try:
        with open(os.path.join(d, name + ".md"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return _json({"error": "not found"}, 404)
    return _json({"name": name, "text": text, "meta": _load(os.path.join(d, name + ".json"))})


@app.post("/api/agents/report")
def api_agents_report():
    # 같은 PC의 에이전트 프로세스만 보고할 수 있다(대시보드 사용자나 외부에서 경보를 꾸며 넣지 못하게).
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return _json({"error": "forbidden"}, 403)
    r = _body()
    if r.get("agent") == "report":
        state.log_audit("report-agent", "report_written", f"{r.get('name')}: {r.get('summary', '')}")
        return _json({"ok": True})
    if r.get("agent") != "drift":
        return _json({"error": "알 수 없는 에이전트"}, 400)
    state.log_audit("drift-agent", "drift_check", r.get("summary", ""))
    bad = [f for f in r.get("findings") or [] if f.get("unapproved")]
    if bad:
        lines = []
        for f in bad:
            ai = f.get("ai") or {}
            lines.append(f"{f['device']}: 기준({f.get('since')}) 이후 승인 기록 없는 변경 {f['unapproved']}줄"
                         + (f" · AI 해석[{ai.get('risk')}]: {ai.get('meaning')}" if ai else ""))
            lines += [f"  {c['sign']} {c['line']}" for c in f["changes"] if not c.get("approval")][:8]
        monitor.raise_alert("config_drift", "high", "승인 없는 설정 변경: " + ", ".join(f["device"] for f in bad),
                            "변경 감지 에이전트 보고.\n" + "\n".join(lines), key=f"drift:{r.get('started')}")
    return _json({"ok": True})


@app.post("/api/agents/<aid>/run")
def api_agent_run(aid):
    if aid not in UNITS:
        return _json({"error": "알 수 없는 에이전트"}, 404)
    unit, what = UNITS[aid]
    if _systemctl("is-active", unit + ".service") == "activating":
        return _json({"error": f"{what}이 이미 진행 중입니다."}, 409)
    try:
        r = subprocess.run(["systemctl", "start", "--no-block", unit + ".service"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _json({"error": f"{what}을 시작하지 못했습니다: {e}"}, 500)
    if r.returncode:
        return _json({"error": f"{what}을 시작하지 못했습니다: {r.stderr.strip()[-200:]}"}, 500)
    state.log_audit("관리자", aid + "_run", f"{what} 즉시 실행")
    return _json({"ok": True})


def main():
    from waitress import serve
    brain.start()
    monitor.start()
    state.log_audit("system", "start", f"에이전트 시작 (제공자 {config.active_provider()})")
    serve(app, host=config.LISTEN_HOST, port=config.LISTEN_PORT, threads=8, ident=None)


if __name__ == "__main__":
    main()
