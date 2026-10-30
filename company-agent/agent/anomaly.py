"""통계 기반 네트워크 이상 탐지 (Phase 8 / §7.1).

규칙 기반 경보(monitor)를 보완해, '평소 상태'를 기준으로 벗어나는 지표를 통계로 탐지한다.
데이터 양·품질을 먼저 평가하고, 데이터가 적거나 품질이 부족하면 ML을 강행하지 않는다(로드맵 지침).
표준 라이브러리만 사용(numpy/sklearn 불필요). 로버스트 통계(중앙값 + MAD)로 소표본 왜곡에 강하게 한다.

    python anomaly.py            # ops_dw.sqlite로 데이터 평가 + 이상 탐지 리포트
"""
import argparse
import importlib.util
import os
import re
import sqlite3

try:
    import config
    DEFAULT_DB = os.path.join(config.DATA_DIR, "ops_dw.sqlite")
except Exception:
    DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ops_dw.sqlite")

Z_THRESHOLD = 3.5          # 로버스트 z 임계값
MIN_POINTS = 5             # 이보다 표본이 적은 계열은 기준선을 세우지 않는다(과적합·오탐 방지)
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def robust_z(xs):
    """중앙값과 MAD로 로버스트 z-score를 계산한다. (median, mad, [z...])."""
    if not xs:
        return 0, 0, []
    med = _median(xs)
    mad = _median([abs(x - med) for x in xs])
    scale = 1.4826 * mad if mad else 0
    if not scale:                         # 변동이 없으면 평균편차로 대체
        avg = sum(xs) / len(xs)
        scale = (sum(abs(x - avg) for x in xs) / len(xs)) or 1e-9
        med = avg
    return med, mad, [(x - med) / scale for x in xs]


def data_profile(conn):
    q = lambda s: conn.execute(s).fetchone()[0]
    return {
        "events": q("SELECT COUNT(*) FROM events"),
        "distinct_days": q("SELECT COUNT(DISTINCT substr(occurred_at,1,10)) FROM events"),
        "distinct_hours": q("SELECT COUNT(DISTINCT substr(occurred_at,1,13)) FROM events"),
        "security_events": q("SELECT COUNT(*) FROM events WHERE category='security'"),
        "incidents": q("SELECT COUNT(*) FROM incidents"),
    }


def ml_readiness(conn):
    """ML(Isolation Forest 등) 적용이 타당한지 정직하게 평가한다. 데이터·환경이 부족하면 통계 기준선을 쓴다."""
    p = data_profile(conn)
    have_sklearn = importlib.util.find_spec("sklearn") is not None
    have_numpy = importlib.util.find_spec("numpy") is not None
    reasons = []
    if p["distinct_hours"] < 72:
        reasons.append(f"시계열 길이 부족(관측 {p['distinct_hours']}시간 < 권장 72시간)")
    if p["events"] < 2000:
        reasons.append(f"표본 부족(events {p['events']} < 권장 2000)")
    if not (have_sklearn and have_numpy):
        reasons.append("sklearn/numpy 미설치(경량·저RAM 환경)")
    return {"recommend_ml": not reasons, "have_sklearn": have_sklearn, "have_numpy": have_numpy,
            "reasons": reasons, "method": "통계 기준선(중앙값+MAD)" if reasons else "통계 + Isolation Forest 비교"}


def _series(conn, sql):
    return [(r[0], r[1]) for r in conn.execute(sql).fetchall()]


def _flag(series, name, unit):
    """(키, 값) 계열에서 로버스트 z가 임계값을 넘는 지점을 이상으로 표시."""
    if len(series) < MIN_POINTS:
        return {"series": name, "skipped": f"표본 {len(series)}개 < {MIN_POINTS} (기준선 미수립)"}
    vals = [v for _, v in series]
    med, mad, zs = robust_z(vals)
    out = [{"key": k, "value": v, "z": round(z, 2)} for (k, v), z in zip(series, zs) if z >= Z_THRESHOLD]
    return {"series": name, "unit": unit, "baseline_median": med, "points": len(series),
            "anomalies": sorted(out, key=lambda a: -a["z"])}


def detect(conn):
    """여러 운영 지표에서 통계적 이상을 탐지한다(§7.1 지표 예시 기반)."""
    res = []
    # 시간대별 전체 이벤트량 / 보안 이벤트량 / 방화벽·네트워크 경보량
    res.append(_flag(_series(conn, "SELECT substr(occurred_at,1,13) h, COUNT(*) FROM events GROUP BY h ORDER BY h"),
                     "시간대별 이벤트량", "건/시간"))
    res.append(_flag(_series(conn, "SELECT substr(occurred_at,1,13) h, COUNT(*) FROM events "
                                   "WHERE category='security' GROUP BY h ORDER BY h"), "시간대별 보안 이벤트", "건/시간"))
    res.append(_flag(_series(conn, "SELECT substr(occurred_at,1,13) h, COUNT(*) FROM events "
                                   "WHERE event_type='alert' GROUP BY h ORDER BY h"), "시간대별 경보 발생량", "건/시간"))
    # 장비별 이벤트 수(연결/활동량 대리지표) → 특정 장비의 과다 활동
    res.append(_flag(_series(conn, "SELECT device_id, COUNT(*) FROM tool_calls WHERE device_id<>'' "
                                   "GROUP BY device_id"), "장비별 조사 활동량", "건"))
    # 시간대별 도구 오류율(연결 실패율 대리지표)
    rows = _series(conn, "SELECT substr(occurred_at,1,13) h, "
                         "CAST(SUM(is_error) AS FLOAT)/COUNT(*) FROM tool_calls GROUP BY h ORDER BY h")
    res.append(_flag(rows, "시간대별 도구 오류율(연결 실패율 대리)", "비율"))
    # 특정 IP의 비정상 활동: 경보 상세에서 IP 추출 후 빈도
    ip_counts = {}
    for (detail,) in conn.execute("SELECT detail FROM events WHERE category IN('security','network') AND detail IS NOT NULL"):
        for ip in set(IPV4.findall(detail or "")):
            if not ip.startswith(("10.", "211.")):          # 내부·자사 대역 제외
                ip_counts[ip] = ip_counts.get(ip, 0) + 1
    res.append(_flag(sorted(ip_counts.items()), "외부 IP 활동량", "건"))
    return res


def report(conn):
    r = ml_readiness(conn)
    print("=== 데이터 평가 / ML 적정성 ===")
    print("방법:", r["method"], "| ML 권장:", r["recommend_ml"])
    for x in r["reasons"]:
        print("  - 보류 사유:", x)
    print("\n=== 통계 이상 탐지 (로버스트 z >= %.1f) ===" % Z_THRESHOLD)
    for s in detect(conn):
        if s.get("skipped"):
            print(f"[{s['series']}] 건너뜀: {s['skipped']}")
            continue
        an = s["anomalies"]
        head = f"[{s['series']}] 기준선(중앙값) {s['baseline_median']} {s.get('unit','')} · 표본 {s['points']}"
        if not an:
            print(head + " · 이상 없음")
        else:
            print(head + f" · 이상 {len(an)}건")
            for a in an[:5]:
                print(f"    - {a['key']}: {a['value']} (z={a['z']})")


def main():
    ap = argparse.ArgumentParser(description="통계 기반 이상 탐지 (Phase 8)")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    report(conn)
    conn.close()


if __name__ == "__main__":
    main()
