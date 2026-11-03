"""Phase 8 테스트: 통계 이상 탐지 + 이벤트 상관분석 (합성 데이터, 외부 의존성 없음).

    python phase8_test.py
"""
import sqlite3

import anomaly
import correlate


def test_robust_z():
    med, mad, z = anomaly.robust_z([2, 3, 2, 3, 2, 3, 20])
    assert z[-1] >= 3.5, f"명백한 이상치(20)를 탐지해야 함: z={z[-1]}"
    _, _, z2 = anomaly.robust_z([5, 5, 5, 5, 5])
    assert all(abs(x) < 1e-6 for x in z2), "변동 없는 계열은 이상 없음이어야 함"
    print("[OK] robust_z: 이상치 탐지 + 무변동 처리")


def _events_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE events(event_id INTEGER PRIMARY KEY, occurred_at TEXT, category TEXT, "
              "event_type TEXT, severity TEXT, device_id TEXT, detail TEXT)")
    c.execute("CREATE TABLE incidents(incident_id TEXT, occurred_at TEXT, status TEXT, resolution TEXT, basis TEXT)")
    # 캐스케이드 1: CORE1 장애 → HSRP/통신 실패 (짧은 시간에 몰림)
    rows = [
        (1, "2026-10-30T10:00:00+09:00", "device_status", "alert", "high", "CORE1", "CORE1 관리 접속 불가"),
        (2, "2026-10-30T10:00:30+09:00", "network", "alert", "medium", "CORE2", "HSRP 그룹 10 Active 변경: CORE1 → CORE2"),
        (3, "2026-10-30T10:01:10+09:00", "network", "alert", "medium", "", "일시적 통신 실패 (CORE1 관련)"),
        # 캐스케이드 2: 1시간 뒤 웹 공격 (별개 그룹)
        (4, "2026-10-30T11:00:00+09:00", "security", "alert", "high", "", "웹 공격 탐지: 203.0.113.9 (SQLi)"),
        (5, "2026-10-30T11:00:40+09:00", "security", "alert", "high", "WEB", "203.0.113.9 UNION SELECT 성공 의심 211.0.1.10"),
    ]
    c.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?)", rows)
    # CORE1 캐스케이드는 에이전트가 조사해 검증한 원인이 있다고 가정
    c.execute("INSERT INTO incidents VALUES('77','2026-10-30T10:02:00+09:00','resolved','해결','CORE1 장비 다운이 원인')")
    c.commit()
    return c


def test_correlate():
    c = _events_db()
    groups = correlate.correlate(c, window_sec=300)
    assert len(groups) == 2, f"두 개의 상관 그룹이어야 함(1시간 간격), 실제 {len(groups)}"
    # 재실행 멱등성
    again = correlate.correlate(c, window_sec=300)
    assert len(again) == 2, "재계산해도 그룹 수는 동일해야 함(멱등)"
    assert c.execute("SELECT COUNT(*) FROM correlation_groups").fetchone()[0] == 2, "그룹 중복 적재 없어야 함"
    # 역할 분류: CORE1 그룹은 incident 77로 검증, 웹공격 그룹은 미검증
    core = next(g for g in again if "CORE1" in g["entities"])
    assert core["verified_incident"] == "77", f"CORE1 캐스케이드는 검증된 원인(77)과 연결돼야 함: {core['verified_incident']}"
    web = next(g for g in again if "WEB" in g["entities"])
    assert web["verified_incident"] is None, "웹공격 그룹은 미검증(AI 설명 필요)이어야 함"
    amb = correlate.ambiguous_groups(c)
    assert len(amb) == 1, f"미검증 그룹 1개여야 함, 실제 {len(amb)}"
    # 역할이 저장됐는지
    roles = {r[0] for r in c.execute("SELECT DISTINCT role FROM event_correlations")}
    assert "correlated" in roles and ("verified" in roles or "suspected" in roles), f"역할 분류 누락: {roles}"
    print("[OK] correlate: 캐스케이드 군집 + 검증/미검증 분류 + 멱등성")


def test_anomaly_detect():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE events(event_id INTEGER, occurred_at TEXT, category TEXT, event_type TEXT, "
              "severity TEXT, device_id TEXT, detail TEXT)")
    c.execute("CREATE TABLE tool_calls(device_id TEXT, is_error INT, occurred_at TEXT)")
    # 10시간 평탄(시간당 3건) + 1시간 급증(30건)
    eid = 0
    for h in range(10):
        for _ in range(3):
            eid += 1
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                      (eid, f"2026-10-30T{h:02d}:10:00+09:00", "network", "alert", "info", "", "x"))
    for _ in range(30):
        eid += 1
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                  (eid, "2026-10-30T20:10:00+09:00", "network", "alert", "high", "", "spike"))
    c.commit()
    res = anomaly.detect(c)
    hourly = next(s for s in res if s["series"] == "시간대별 이벤트량")
    assert hourly["anomalies"], "급증 시간대를 이상으로 탐지해야 함"
    assert any("20" in a["key"] for a in hourly["anomalies"]), "20시 급증이 탐지돼야 함"
    print("[OK] anomaly.detect: 시간대별 급증 탐지")


if __name__ == "__main__":
    test_robust_z()
    test_correlate()
    test_anomaly_detect()
    print("\n[OK] Phase 8 테스트 전부 통과")
