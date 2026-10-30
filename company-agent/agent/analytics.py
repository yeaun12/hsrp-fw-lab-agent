"""SQL 기반 운영 분석 (Phase 6). Phase 5 저장 계층(ops_dw.sqlite) 위에서 KPI와 추이를 계산한다.

분석용 View를 만들고(§6.3 "분석용 View"), 운영 개선에 쓸 KPI를 한 번에 돌려준다.
- 기간별 장애 발생, 장비별 장애 빈도, 이벤트 유형별 추이, 평균 조사·해결 시간,
  관리자 승인·거부 비율, 반복 장애 패턴, 에이전트 실행 성공률, 시간대별 보안 이벤트, API 호출량·비용.

    python analytics.py            # ops_dw.sqlite에 View 생성 + KPI 리포트 출력
"""
import argparse
import json
import os
import sqlite3

try:
    import config
    DEFAULT_DB = os.path.join(config.DATA_DIR, "ops_dw.sqlite")
except Exception:
    DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ops_dw.sqlite")

VIEWS = """
CREATE VIEW IF NOT EXISTS v_incidents_daily AS
  SELECT substr(occurred_at,1,10) AS day, COUNT(*) AS n FROM incidents
  WHERE occurred_at IS NOT NULL GROUP BY day ORDER BY day;
CREATE VIEW IF NOT EXISTS v_incident_device AS
  SELECT device_id AS device, COUNT(DISTINCT incident_id) AS n FROM tool_calls
  WHERE device_id IS NOT NULL AND device_id<>'' GROUP BY device_id ORDER BY n DESC;
CREATE VIEW IF NOT EXISTS v_events_type AS
  SELECT category, event_type, COUNT(*) AS n FROM events GROUP BY category, event_type ORDER BY n DESC;
CREATE VIEW IF NOT EXISTS v_events_type_daily AS
  SELECT substr(occurred_at,1,10) AS day, event_type, COUNT(*) AS n FROM events
  WHERE occurred_at IS NOT NULL GROUP BY day, event_type;
CREATE VIEW IF NOT EXISTS v_security_hour AS
  SELECT CAST(substr(occurred_at,12,2) AS INTEGER) AS hour, COUNT(*) AS n FROM events
  WHERE category='security' AND occurred_at IS NOT NULL GROUP BY hour;
CREATE VIEW IF NOT EXISTS v_approvals AS
  SELECT event_type, COUNT(*) AS n FROM events
  WHERE event_type IN ('approval_requested','approved','rejected','executed','failed') GROUP BY event_type;
CREATE VIEW IF NOT EXISTS v_incident_status AS
  SELECT status, COUNT(*) AS n FROM incidents GROUP BY status ORDER BY n DESC;
"""


def ensure_views(conn):
    conn.executescript(VIEWS)
    conn.commit()


def _rows(conn, sql, *a):
    return [dict(r) for r in conn.execute(sql, a).fetchall()]


def _one(conn, sql, *a):
    r = conn.execute(sql, a).fetchone()
    return r[0] if r else None


def kpis(conn):
    """§6.3 분석을 한 번에 계산해 JSON 직렬화 가능한 dict로 돌려준다."""
    ensure_views(conn)
    total_inc = _one(conn, "SELECT COUNT(*) FROM incidents") or 0
    resolved = _one(conn, "SELECT COUNT(*) FROM incidents WHERE status='resolved'") or 0
    tool_total = _one(conn, "SELECT COUNT(*) FROM tool_calls") or 0
    tool_err = _one(conn, "SELECT COUNT(*) FROM tool_calls WHERE is_error=1") or 0
    appr = {r["event_type"]: r["n"] for r in _rows(conn, "SELECT * FROM v_approvals")}
    decided = appr.get("approved", 0) + appr.get("rejected", 0)
    usage = _rows(conn, "SELECT provider,model,calls,input_tokens,output_tokens,usd FROM model_usage "
                        "ORDER BY id DESC LIMIT 1")
    return {
        "incidents_total": total_inc,
        "incidents_by_status": _rows(conn, "SELECT status, n FROM v_incident_status"),
        "incidents_daily": _rows(conn, "SELECT day, n FROM v_incidents_daily"),
        "incidents_by_device": _rows(conn, "SELECT device, n FROM v_incident_device"),
        "events_by_type": _rows(conn, "SELECT category, event_type, n FROM v_events_type LIMIT 12"),
        "security_by_hour": _rows(conn, "SELECT hour, n FROM v_security_hour"),
        # 평균 조사·해결 시간 (초). 탐지 시간은 감시 폴링 주기(10~20초)로 상한이 정해져 DB 지표로는 두지 않음.
        "avg_resolution_sec": round(_one(conn, "SELECT AVG(elapsed_sec) FROM incidents WHERE elapsed_sec IS NOT NULL") or 0, 1),
        "max_resolution_sec": _one(conn, "SELECT MAX(elapsed_sec) FROM incidents") or 0,
        "agent_success_rate": round(resolved / total_inc, 3) if total_inc else None,
        "tool_error_rate": round(tool_err / tool_total, 3) if tool_total else None,
        "approvals": {"requested": appr.get("approval_requested", 0), "approved": appr.get("approved", 0),
                      "rejected": appr.get("rejected", 0),
                      "approve_ratio": round(appr.get("approved", 0) / decided, 3) if decided else None},
        # 반복 장애 패턴: 2건 이상 발생한 장비
        "recurring_devices": _rows(conn, "SELECT device, n FROM v_incident_device WHERE n>=2"),
        "usage": usage[0] if usage else None,
    }


def report(conn):
    k = kpis(conn)
    print("=== 운영 분석 KPI (Phase 6) ===")
    print(f"사건 총 {k['incidents_total']}건 | 상태별: " +
          ", ".join(f"{r['status']} {r['n']}" for r in k["incidents_by_status"]))
    print(f"에이전트 실행 성공률: {k['agent_success_rate']} | 도구 오류율: {k['tool_error_rate']}")
    print(f"평균 조사·해결 시간: {k['avg_resolution_sec']}s (최대 {k['max_resolution_sec']}s)")
    a = k["approvals"]
    print(f"승인 요청 {a['requested']} · 승인 {a['approved']} · 거부 {a['rejected']} · 승인비율 {a['approve_ratio']}")
    print("장비별 장애: " + (", ".join(f"{r['device']} {r['n']}" for r in k["incidents_by_device"]) or "(없음)"))
    print("이벤트 유형 Top: " + ", ".join(f"{r['event_type']}({r['category']}) {r['n']}" for r in k["events_by_type"][:6]))
    print("일자별 사건: " + ", ".join(f"{r['day']}:{r['n']}" for r in k["incidents_daily"]))
    print("시간대별 보안 이벤트: " + (", ".join(f"{r['hour']}시:{r['n']}" for r in k["security_by_hour"]) or "(없음)"))
    if k["usage"]:
        u = k["usage"]
        print(f"API: {u['provider']}/{u['model']} 호출 {u['calls']} · 입력 {u['input_tokens']} · 출력 {u['output_tokens']} · ${u['usd']}")


def main():
    ap = argparse.ArgumentParser(description="SQL 운영 분석 (Phase 6)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    if a.json:
        print(json.dumps(kpis(conn), ensure_ascii=False, indent=2))
    else:
        report(conn)
    conn.close()


if __name__ == "__main__":
    main()
