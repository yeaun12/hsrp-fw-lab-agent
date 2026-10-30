"""warehouse.py ETL·데이터 품질 테스트 (§6.2 검증 기준). 합성 데이터로 장비 없이 돌아간다.

    python warehouse_test.py
"""
import json
import os
import tempfile

import warehouse as W


def _write(d, name, lines):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_fixtures(d):
    # audit.jsonl: 정상 2건 + 민감정보(마스킹 대상) + 깨진 JSON(격리 대상) + 필드 누락(격리 대상)
    _write(d, "audit.jsonl", [
        json.dumps({"time": "2026-10-30 10:00:00", "actor": "agent", "action": "read", "detail": "CORE2: show run"}, ensure_ascii=False),
        json.dumps({"time": "2026-10-30 10:01:00", "actor": "monitor", "action": "alert", "detail": "[high] 웹 공격 탐지: 198.51.100.66"}, ensure_ascii=False),
        json.dumps({"time": "2026-10-30 10:02:00", "actor": "관리자", "action": "llm_set", "detail": "키 AQ.abcdef1234567890 등록"}, ensure_ascii=False),
        "{ this is broken json",
        json.dumps({"actor": "x", "action": "read"}, ensure_ascii=False),  # time 누락 -> 격리
    ])
    _write(d, "chat.jsonl", [
        json.dumps({"time": "2026-10-30 10:00:00", "kind": "admin", "text": "DB 접속이 안 돼요 #42"}, ensure_ascii=False),
    ])
    # cases.json: 사건 1건(도구 2개, 그중 1개는 오류). approval_ids로 상관.
    with open(os.path.join(d, "cases.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": 42, "started": "2026-10-30 10:00:05", "source": "admin", "trigger": "DB 접속 불가",
                    "model": "gemini-3.5-flash-lite", "provider": "gemini", "status": "needs_confirm",
                    "resolution": "", "steps": 3, "elapsed_sec": 12, "tokens": {"input": 1000, "output": 50},
                    "basis": "CORE2 ACL 확인", "proposed_action": "", "approval_ids": [],
                    "tools": [{"time": "2026-10-30 10:00:06", "name": "ios_show", "device": "CORE2", "error": False, "summary": "ok"},
                              {"time": "2026-10-30 10:00:08", "name": "firewall_status", "device": "", "error": True, "summary": "오류"}]}], f, ensure_ascii=False)
    with open(os.path.join(d, "usage.json"), "w", encoding="utf-8") as f:
        json.dump({"provider": "gemini", "model": "gemini-3.5-flash-lite", "calls": 3, "input": 1000, "output": 50, "usd": 0.0}, f)


def main():
    d = tempfile.mkdtemp(prefix="dwtest_")
    db = os.path.join(d, "t.sqlite")
    build_fixtures(d)

    # 1) 1차 적재
    conn, s1 = W.ingest(d, db)
    print("run1:", s1)
    assert s1["error"] == 2, f"격리 2건이어야 함(깨진 JSON + time 누락), 실제 {s1['error']}"
    assert s1["loaded"] >= 5, f"정상 적재 건수 부족: {s1['loaded']}"
    conn.close()

    # 2) 재적재 멱등성(재처리 가능): 같은 데이터 다시 → 신규 0
    conn, s2 = W.ingest(d, db)
    print("run2(재적재):", s2)
    assert s2["loaded"] == 0, f"재적재 시 신규는 0이어야 함(중복 방지), 실제 {s2['loaded']}"
    assert s2["dup"] >= 5, "재적재는 중복으로 집계되어야 함"

    # 3) 데이터 품질/정합성
    r = W.quality_report(conn)
    print("quality:", r)
    assert r["ok"], f"품질 점검 실패: {r}"
    assert r["events_null_time"] == 0 and r["non_iso_time"] == 0, "시간/타임존 통일 실패"
    assert r["orphan_tool_calls"] == 0, "고아 도구호출(정합성) 발생"
    assert r["dup_event_keys"] == 0, "이벤트 중복"
    assert r["unmasked_secrets"] == 0, "마스킹 누락"

    # 4) 마스킹 실제 확인 (AQ. 키가 ***MASKED***로 저장됐는지)
    row = conn.execute("SELECT detail FROM events WHERE event_type='llm_set'").fetchone()
    assert row and "AQ.abcdef" not in row["detail"] and "MASKED" in row["detail"], f"마스킹 안 됨: {row and row['detail']}"

    # 5) 수집 실패 기록(격리) 확인
    errs = conn.execute("SELECT reason FROM ingest_errors").fetchall()
    assert len(errs) == 2, f"ingest_errors 2건이어야 함, 실제 {len(errs)}"

    # 6) 상관/구조화 확인: 사건 1건 + 도구 2건(오류 1) + 정합성
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tool_calls WHERE incident_id='42'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM tool_calls WHERE is_error=1").fetchone()[0] == 1

    # 7) 출처 추적(lineage): 모든 정제 행이 raw_id를 가진다
    assert conn.execute("SELECT COUNT(*) FROM events WHERE raw_id IS NULL").fetchone()[0] == 0

    # 8) 보존 정책: 미래 기준 0일 보존 시 원본 정리 동작
    before = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
    deleted = W.retention(conn, -1)   # cutoff를 미래로 밀어 전부 대상
    assert deleted == before and deleted > 0, f"보존 정리 실패: {deleted}/{before}"
    conn.close()

    print("\n[OK] 모든 ETL/데이터 품질 테스트 통과")


if __name__ == "__main__":
    main()
