"""사내 IT 운영 에이전트 - 웹 대시보드 서버 (진입점).

    /opt/lab-agent/venv/bin/python server.py
"""
import json
import os
import threading
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
    if not approve:
        state.log_audit(actor, "rejected", f"#{rec['id']} {rec['summary']}")
        brain.submit(f"[관리자 거부] 승인 요청 #{rec['id']}({rec['summary']})을 관리자가 거부했습니다. 실행하지 않았습니다.",
                     "approval")
        return rec

    def run():
        state.log_audit(actor, "approved", f"#{rec['id']} {rec['summary']}")
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
            self._json(snap)
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
