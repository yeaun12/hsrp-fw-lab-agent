"""통합 회귀 테스트 (Phase 9). 전체 스택이 한 번에 깨지지 않는지 확인한다.

1) 핵심 모듈 임포트 스모크 테스트
2) 데이터 파이프라인 end-to-end: 합성 로그 → 적재(Phase5) → KPI(Phase6) → 이상탐지·상관분석(Phase8)
3) (에이전트가 떠 있으면) 라이브 API 스모크: /api/state, /api/analytics

    python regression_test.py
장비·네트워크 없이 돌아간다(2번은 합성 데이터, 3번은 있으면만).
"""
import json
import os
import sqlite3
import tempfile
import urllib.request

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"[OK] {name}")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        FAILS.append(name)


# --- 1) 임포트 스모크 ---
def t_imports():
    import importlib
    for m in ("config", "state", "tools", "providers", "warehouse", "analytics",
              "anomaly", "correlate", "eval_cases", "evaluate", "web_detect", "audit"):
        importlib.import_module(m)


# --- 2) 데이터 파이프라인 end-to-end (합성) ---
def _fixtures(d):
    with open(os.path.join(d, "audit.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in [
            {"time": "2026-10-30 10:00:00", "actor": "agent", "action": "read", "detail": "CORE2: show run"},
            {"time": "2026-10-30 10:01:00", "actor": "monitor", "action": "alert", "detail": "[high] 웹 공격 탐지: 203.0.113.9"},
            {"time": "2026-10-30 10:01:30", "actor": "monitor", "action": "alert", "detail": "[high] CORE1 관리 접속 불가"},
        ]))
    with open(os.path.join(d, "cases.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": 1, "started": "2026-10-30 10:00:05", "source": "admin", "trigger": "DB 접속",
                    "model": "mock-1", "provider": "mock", "status": "needs_confirm", "resolution": "",
                    "steps": 2, "elapsed_sec": 8, "tokens": {"input": 500, "output": 30},
                    "basis": "CORE2 ACL 확인", "proposed_action": "", "approval_ids": [],
                    "tools": [{"time": "2026-10-30 10:00:06", "name": "ios_show", "device": "CORE2", "error": False, "summary": "ok"}]}], f, ensure_ascii=False)
    with open(os.path.join(d, "usage.json"), "w", encoding="utf-8") as f:
        json.dump({"provider": "mock", "model": "mock-1", "calls": 2, "input": 500, "output": 30, "usd": 0.0}, f)


def t_pipeline():
    import warehouse, analytics, anomaly, correlate
    d = tempfile.mkdtemp(prefix="reg_")
    db = os.path.join(d, "dw.sqlite")
    _fixtures(d)
    conn, stats = warehouse.ingest(d, db)
    assert stats["error"] == 0 and stats["loaded"] > 0, f"적재 실패: {stats}"
    q = warehouse.quality_report(conn)
    assert q["ok"], f"품질 실패: {q}"
    conn.close()
    conn2 = sqlite3.connect(db); conn2.row_factory = sqlite3.Row
    k = analytics.kpis(conn2)
    assert k["incidents_total"] == 1, f"KPI 사건 수 오류: {k['incidents_total']}"
    r = anomaly.ml_readiness(conn2)
    assert "recommend_ml" in r, "이상탐지 데이터 평가 실패"
    anomaly.detect(conn2)                      # 예외 없이 동작
    groups = correlate.correlate(conn2)        # 예외 없이 동작
    assert isinstance(groups, list), "상관분석 결과 형식 오류"
    conn2.close()


# --- 3) 라이브 API 스모크 (있으면만) ---
def t_live_api():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/state", timeout=5) as r:
            st = json.loads(r.read())
    except Exception:
        print("    (에이전트 미기동 → 라이브 API 스모크 건너뜀)")
        return
    assert "llm" in st and "cases" in st, "api/state 응답 형식 오류"
    with urllib.request.urlopen("http://127.0.0.1:8080/api/analytics", timeout=30) as r:
        k = json.loads(r.read())
    assert "incidents_total" in k, "api/analytics 응답 형식 오류"


if __name__ == "__main__":
    check("1. 핵심 모듈 임포트", t_imports)
    check("2. 데이터 파이프라인 end-to-end(적재→KPI→이상탐지→상관분석)", t_pipeline)
    check("3. 라이브 API 스모크", t_live_api)
    print("\n" + ("[OK] 통합 회귀 테스트 전부 통과" if not FAILS else f"[FAIL] 실패 {len(FAILS)}건: {FAILS}"))
    raise SystemExit(1 if FAILS else 0)
