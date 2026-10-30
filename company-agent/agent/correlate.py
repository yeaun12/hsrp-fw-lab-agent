"""이벤트 상관분석 (Phase 8 / §7.2). 여러 경보를 하나의 사건으로 묶는다.

예: CORE1 장애 → HSRP 전환, OSPF 경로 변경, 일시적 통신 실패가 짧은 시간에 몰리면 하나의 그룹으로 연결한다.
규칙 기반(시간 창 + 공유 엔티티)으로 묶고, 각 이벤트의 역할을 구분한다:
- 직접 관찰(observed): 로그로 직접 관측된 이벤트.
- 연관 확인(correlated): 같은 엔티티·시간 창에서 함께 발생한 이벤트.
- 원인 추정(suspected): 그룹에서 가장 이르고 심각도 높은 이벤트.
- 검증된 원인(verified): 에이전트 조사(incidents)로 실제 확인된 원인.
모호한 관계의 설명은 AI(에이전트 조사)에 맡긴다(ambiguous_groups).

    python correlate.py            # ops_dw.sqlite로 상관 그룹 생성 + 리포트
"""
import argparse
import os
import re
import sqlite3

try:
    import config
    DEFAULT_DB = os.path.join(config.DATA_DIR, "ops_dw.sqlite")
except Exception:
    DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ops_dw.sqlite")

WINDOW_SEC = 300                 # 이 시간 안에 몰린 유의 이벤트를 한 그룹으로
SEV = {"high": 3, "medium": 2, "low": 1, "info": 0}
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DEVICES = ("CORE1", "CORE2", "ASW1", "ASW2", "BRANCH", "FW", "SRV", "WEB")

SCHEMA = """
CREATE TABLE IF NOT EXISTS correlation_groups(
  group_id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, ended TEXT, n_events INTEGER,
  entities TEXT, suspected_event_id INTEGER, verified_incident TEXT, verified_basis TEXT);
CREATE TABLE IF NOT EXISTS event_correlations(
  group_id INTEGER, event_id INTEGER, role TEXT, entity TEXT, PRIMARY KEY(group_id, event_id));
"""


def _epoch(iso):
    # 'YYYY-MM-DDTHH:MM:SS+09:00' -> 대략 초(비교용). TZ는 전부 동일하다고 본다.
    import time
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def _entities(text, device_id):
    ents = set()
    if device_id:
        ents.add(device_id)
    for d in DEVICES:
        if d in (text or ""):
            ents.add(d)
    for ip in IPV4.findall(text or ""):
        ents.add(ip)
    return ents


def correlate(conn, window_sec=WINDOW_SEC):
    """유의 이벤트를 시간 창으로 군집화하고 역할을 분류해 저장한다. 그룹 목록을 돌려준다."""
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM correlation_groups")      # 재계산(멱등)
    conn.execute("DELETE FROM event_correlations")
    rows = conn.execute(
        "SELECT event_id, occurred_at, category, event_type, severity, device_id, detail FROM events "
        "WHERE (category IN('security','network','device_status') OR event_type='alert') "
        "AND occurred_at IS NOT NULL ORDER BY occurred_at").fetchall()
    incidents = conn.execute("SELECT incident_id, occurred_at, status, resolution, basis FROM incidents").fetchall()

    groups, cur, last_t = [], [], None
    for r in rows:
        t = _epoch(r["occurred_at"])
        if cur and t - last_t > window_sec:
            groups.append(cur); cur = []
        cur.append(r); last_t = t
    if cur:
        groups.append(cur)

    result = []
    for g in groups:
        if len(g) < 2:                      # 단독 이벤트는 상관 그룹으로 보지 않는다
            continue
        ents = set()
        for e in g:
            ents |= _entities(e["detail"], e["device_id"])
        # 원인 추정: 가장 이르고 심각도 높은 이벤트
        suspected = sorted(g, key=lambda e: (-SEV.get(e["severity"], 0), e["occurred_at"]))[0]
        # 검증된 원인: 그룹 엔티티를 언급하고 시간이 겹치는 에이전트 조사(incident)
        verified = None
        g_start, g_end = g[0]["occurred_at"], g[-1]["occurred_at"]
        for inc in incidents:
            if inc["basis"] and any(ent in inc["basis"] for ent in ents) and inc["occurred_at"]:
                if g_start[:13] <= inc["occurred_at"][:13] <= g_end[:16] or inc["occurred_at"][:10] == g_start[:10]:
                    verified = inc
                    break
        gid = conn.execute(
            "INSERT INTO correlation_groups(started,ended,n_events,entities,suspected_event_id,"
            "verified_incident,verified_basis) VALUES(?,?,?,?,?,?,?)",
            (g_start, g_end, len(g), ",".join(sorted(ents)), suspected["event_id"],
             verified["incident_id"] if verified else None,
             (verified["basis"][:300] if verified else None))).lastrowid
        for e in g:
            if e["event_id"] == suspected["event_id"]:
                role = "verified" if verified else "suspected"
            else:
                role = "correlated"
            conn.execute("INSERT OR IGNORE INTO event_correlations(group_id,event_id,role,entity) VALUES(?,?,?,?)",
                         (gid, e["event_id"], role, e["device_id"] or ""))
        result.append({"group_id": gid, "started": g_start, "ended": g_end, "n": len(g),
                       "entities": sorted(ents), "suspected": dict(suspected),
                       "verified_incident": verified["incident_id"] if verified else None})
    conn.commit()
    return result


def ambiguous_groups(conn):
    """검증된 원인이 아직 없는 상관 그룹 — AI(에이전트 조사)로 설명이 필요한 대상."""
    return [dict(r) for r in conn.execute(
        "SELECT group_id, started, n_events, entities FROM correlation_groups WHERE verified_incident IS NULL")]


def report(conn):
    groups = correlate(conn)
    print(f"=== 이벤트 상관분석: {len(groups)}개 그룹 (시간 창 {WINDOW_SEC}s) ===")
    for g in groups[:15]:
        sus = g["suspected"]
        print(f"[그룹 {g['group_id']}] {g['started'][11:19]}~{g['ended'][11:19]} · 이벤트 {g['n']} · 엔티티 {','.join(g['entities']) or '-'}")
        print(f"    원인 추정: [{sus['severity']}] {sus['event_type']} · {(sus['detail'] or '')[:70]}")
        print(f"    검증된 원인: {g['verified_incident'] or '(미검증 → AI 조사 필요)'}")
    amb = ambiguous_groups(conn)
    print(f"\n미검증(AI 설명 필요) 그룹: {len(amb)}개")


def main():
    ap = argparse.ArgumentParser(description="이벤트 상관분석 (Phase 8)")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    report(conn)
    conn.close()


if __name__ == "__main__":
    main()
