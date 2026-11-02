"""사내 IT 운영 에이전트 - 웹 대시보드 서버 (진입점).

    /opt/lab-agent/venv/bin/python server.py
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            snap = state.snapshot()
            info = providers.provider_info()
            snap["llm"] = info
            snap["api_key_set"] = info["ready"]      # 하위 호환
            snap["model"] = info["model"]
            with state.lock:
                snap["usage"] = dict(brain.usage)
            snap["audits"] = audit.list_audits()[:3]
            snap["audit_running"] = _audit_running.is_set()
            snap["inventory"] = {n: {"host": d["host"], "role": d["role"], "type": d["type"]}
                                 for n, d in config.DEVICES.items()}
            snap["scan_min_ports"] = config.SCAN_MIN_PORTS
            snap["scan_window_sec"] = config.SCAN_WINDOW_SEC
            snap["now"] = state.now()           # 경과 시간은 서버 시각 기준으로 계산(브라우저 시계와 어긋날 수 있음)
            self._json(snap)
        elif self.path == "/api/analytics":
            try:
                import warehouse
                import analytics
                db = os.path.join(config.DATA_DIR, "ops_dw.sqlite")
                conn, stats = warehouse.ingest(config.DATA_DIR, db)   # 최신 로그를 멱등 적재
                k = analytics.kpis(conn)
                conn.close()
                k["ingest"] = stats
                self._json(k)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif self.path == "/api/overview":
            # 개요 화면용 추세·처리 단계. 경보는 감시가 올린 것만 센다
            # (같은 경보가 에이전트 대화로 전달되며 conversation 범주에 한 번 더 적재되므로 제외).
            try:
                import warehouse
                db = os.path.join(config.DATA_DIR, "ops_dw.sqlite")
                conn, _ = warehouse.ingest(config.DATA_DIR, db)
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
                self._json(out)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif self.path == "/api/insights":
            # Phase 8 통계 이상 탐지 + 이벤트 상관분석을 대시보드에 노출한다(최신 로그를 멱등 적재 후 재계산).
            try:
                import warehouse
                import anomaly
                import correlate
                db = os.path.join(config.DATA_DIR, "ops_dw.sqlite")
                conn, _ = warehouse.ingest(config.DATA_DIR, db)
                groups = correlate.correlate(conn)
                basis = {r["group_id"]: r["verified_basis"] for r in
                         conn.execute("SELECT group_id, verified_basis FROM correlation_groups")}
                out = {"readiness": anomaly.ml_readiness(conn), "profile": anomaly.data_profile(conn),
                       "z_threshold": anomaly.Z_THRESHOLD, "anomalies": anomaly.detect(conn),
                       "window_sec": correlate.WINDOW_SEC,
                       "groups": [{**g, "verified_basis": basis.get(g["group_id"])} for g in groups][::-1][:20]}
                conn.close()
                self._json(out)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif self.path == "/api/webattacks":
            try:
                self._json({"sources": web_detect.scan(lines=400)})
            except Exception as e:
                self._json({"error": str(e)}, 502)
        elif self.path.startswith("/api/audit?id="):
            try:
                self._json(audit.load(self.path.split("=", 1)[1]))
            except (OSError, ValueError):
                self._json({"error": "점검 결과를 찾을 수 없습니다."}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        data = self._body()
        if self.path == "/api/chat":
            text = (data.get("text") or "").strip()
            if not text:
                return self._json({"error": "빈 메시지"}, 400)
            brain.submit(text, "admin")
            return self._json({"ok": True})
        if self.path == "/api/audit/run":
            if _audit_running.is_set():
                return self._json({"error": "점검이 이미 진행 중입니다."}, 409)
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
            return self._json({"ok": True})
        if self.path == "/api/reset":
            ok = brain.reset_conversation()
            return self._json({"ok": ok} if ok else {"error": "에이전트가 작업 중입니다. 끝난 뒤 다시 시도하세요."},
                              200 if ok else 409)
        if self.path in ("/api/approve", "/api/reject"):
            rec = _decide(int(data.get("id", 0)), self.path == "/api/approve")
            return self._json({"ok": rec is not None})
        if self.path == "/api/apikey":
            prov = (data.get("provider") or "").strip()
            key = (data.get("key") or "").strip()
            if prov not in config.PROVIDERS:
                return self._json({"error": "알 수 없는 제공자입니다."}, 400)
            cfg = config.PROVIDERS[prov]
            if cfg["key_secret"]:                    # 키가 필요한 제공자
                prefixes = cfg.get("key_prefix") or ()
                if isinstance(prefixes, str):
                    prefixes = (prefixes,)
                if key and prefixes and not any(key.startswith(p) for p in prefixes):
                    return self._json({"error": f"{cfg['label']} 키 형식이 올바르지 않습니다 (예: {', '.join(prefixes)})."}, 400)
                if key:
                    config.write_secret(cfg["key_secret"], key)
                elif not config.provider_key(prov):
                    return self._json({"error": f"{cfg['label']} API 키를 입력하세요."}, 400)
            config.write_secret("llm_provider", prov)
            state.log_audit("관리자", "llm_set", f"모델 제공자 {prov} 선택" + (" + 키 등록" if key else ""))
            return self._json({"ok": True, "llm": providers.provider_info()})
        self._json({"error": "not found"}, 404)


def main():
    brain.start()
    monitor.start()
    state.log_audit("system", "start", f"에이전트 시작 (제공자 {config.active_provider()})")
    server = ThreadingHTTPServer((config.LISTEN_HOST, config.LISTEN_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
