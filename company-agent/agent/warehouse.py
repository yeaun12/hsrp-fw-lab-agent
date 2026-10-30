"""운영 데이터 파이프라인 (Phase 5). 에이전트가 남긴 로그/기록을 수집·정규화·검증·저장한다.

경량 구성(표준 라이브러리 + SQLite)으로, 저장 계층을 분리해 나중에 PostgreSQL로 옮길 수 있게 했다.
흐름: 원본 로그(JSONL/JSON) → 수집 → 파싱·정규화 → 검증 → 저장(원본 landing + 정제 테이블).
상관분석·이상탐지·분석(Phase 6~8)은 이 저장 계층 위에서 따로 수행한다.

    python warehouse.py                 # data/ 를 ops_dw.sqlite 로 적재
    python warehouse.py --quality       # 적재 후 데이터 품질 리포트
    python warehouse.py --retention 90  # 90일 넘은 원본(raw) 정리

검증 기준(§6.2): 중복 적재 방지(content_hash), 누락·오류 격리(ingest_errors), 시간·타임존 통일(ISO8601+TZ),
정합성(외래키 점검), 재처리·과거 재적재(멱등 재실행), 수집 실패 기록(ingest_runs/errors),
출처 추적(raw_id·source·line), 민감정보 마스킹, 보존 기간 삭제.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import time

try:
    import config
    DEFAULT_DATA = config.DATA_DIR
except Exception:                                   # 단독 실행/테스트 시 config 없이도 동작
    DEFAULT_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

TZ = os.environ.get("OPS_TZ", "+09:00")             # 에이전트 타임스탬프는 로컬 naive -> 이 오프셋으로 통일
DEVICES = ("CORE1", "CORE2", "ASW1", "ASW2", "BRANCH", "FW", "SRV", "WEB")
_SECRET = re.compile(r"\b(sk-ant-[\w-]{6,}|AIza[\w-]{10,}|AQ\.[\w.-]{10,})\b")

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events(
  raw_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, line_no INTEGER,
  content_hash TEXT UNIQUE, raw_json TEXT, ingested_at TEXT, run_id INTEGER);
CREATE TABLE IF NOT EXISTS events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE,
  occurred_at TEXT, ingested_at TEXT, category TEXT, event_type TEXT, actor TEXT,
  device_id TEXT, severity TEXT, correlation_id TEXT, detail TEXT, raw_id INTEGER);
CREATE TABLE IF NOT EXISTS incidents(
  incident_id TEXT PRIMARY KEY, occurred_at TEXT, ingested_at TEXT, source TEXT,
  trigger TEXT, model TEXT, provider TEXT, status TEXT, resolution TEXT,
  steps INTEGER, elapsed_sec INTEGER, tokens_input INTEGER, tokens_output INTEGER,
  basis TEXT, proposed_action TEXT, approval_ids TEXT, raw_id INTEGER);
CREATE TABLE IF NOT EXISTS tool_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_key TEXT UNIQUE, incident_id TEXT,
  seq INTEGER, occurred_at TEXT, tool TEXT, device_id TEXT, is_error INTEGER,
  summary TEXT, raw_id INTEGER);
CREATE TABLE IF NOT EXISTS model_usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, usage_key TEXT UNIQUE, captured_at TEXT,
  provider TEXT, model TEXT, calls INTEGER, input_tokens INTEGER,
  output_tokens INTEGER, usd REAL, raw_id INTEGER);
CREATE TABLE IF NOT EXISTS ingest_runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  files TEXT, rows_read INTEGER, rows_loaded INTEGER, rows_dup INTEGER, rows_error INTEGER);
CREATE TABLE IF NOT EXISTS ingest_errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, source TEXT, line_no INTEGER,
  reason TEXT, raw TEXT, ingested_at TEXT);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS ix_events_corr ON events(correlation_id);
CREATE INDEX IF NOT EXISTS ix_tools_incident ON tool_calls(incident_id);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S") + TZ


def _iso(naive):
    """에이전트의 'YYYY-MM-DD HH:MM:SS'(로컬 naive)를 ISO8601+TZ로 통일. 이미 ISO면 그대로."""
    s = (naive or "").strip()
    if not s:
        return None
    if "T" in s and (s.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", s)):
        return s
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.strptime(s, "%Y-%m-%d %H:%M:%S")) + TZ
    except ValueError:
        return None


def _mask(text):
    """API 키 등 민감 토큰 마스킹."""
    return _SECRET.sub("***MASKED***", text) if isinstance(text, str) else text


def _hash(*parts):
    return hashlib.sha256("||".join(str(p) for p in parts).encode()).hexdigest()


def _device_in(text):
    for d in DEVICES:
        if d in (text or ""):
            return d
    return None


def _corr(text):
    m = re.search(r"#(\d+)", text or "")
    return m.group(1) if m else None


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _land(conn, run_id, source, line_no, obj):
    """원본을 raw_events에 적재(중복 방지). (raw_id, is_new)를 돌려준다. 이미 있으면 기존 raw_id."""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    h = _hash(source, raw)
    cur = conn.execute("INSERT OR IGNORE INTO raw_events(source,line_no,content_hash,raw_json,ingested_at,run_id)"
                       " VALUES(?,?,?,?,?,?)", (source, line_no, h, _mask(raw), _now(), run_id))
    if cur.lastrowid and cur.rowcount:
        return cur.lastrowid, True
    row = conn.execute("SELECT raw_id FROM raw_events WHERE content_hash=?", (h,)).fetchone()
    return (row["raw_id"] if row else None), False


def _ingest_jsonl(conn, run_id, path, source, handler, stats):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            stats["read"] += 1
            # 줄 단위로 먼저 landing(중복 방지). 이미 본 줄이면 성공/오류 모두 재처리하지 않는다(멱등).
            cur = conn.execute("INSERT OR IGNORE INTO raw_events(source,line_no,content_hash,raw_json,ingested_at,"
                               "run_id) VALUES(?,?,?,?,?,?)", (source, i, _hash(source, line), _mask(line), _now(), run_id))
            if not cur.rowcount:
                stats["dup"] += 1
                continue
            raw_id = cur.lastrowid
            try:
                obj = json.loads(line)
            except ValueError as e:
                stats["error"] += 1
                conn.execute("INSERT INTO ingest_errors(run_id,source,line_no,reason,raw,ingested_at)"
                             " VALUES(?,?,?,?,?,?)", (run_id, source, i, f"JSON 파싱 실패: {e}", line[:500], _now()))
                continue
            try:
                handler(conn, obj, raw_id)
            except (KeyError, ValueError, TypeError) as e:
                stats["error"] += 1
                conn.execute("INSERT INTO ingest_errors(run_id,source,line_no,reason,raw,ingested_at)"
                             " VALUES(?,?,?,?,?,?)", (run_id, source, i, f"정규화 실패: {type(e).__name__}: {e}",
                                                      json.dumps(obj, ensure_ascii=False)[:500], _now()))
                continue
            stats["loaded"] += 1


# ------------------------------------------------------------------ 소스별 정규화 핸들러

_SEV_PREFIX = re.compile(r"\[(high|medium|info|low)\]", re.I)


def _h_audit(conn, o, raw_id):
    """audit.jsonl -> events (감사·경보·승인 수명주기 등 운영 이벤트)."""
    occurred = _iso(o.get("time"))
    if not occurred or not o.get("action"):
        raise ValueError("time/action 누락")
    detail = _mask(o.get("detail", ""))
    sev = (_SEV_PREFIX.search(detail).group(1).lower() if _SEV_PREFIX.search(detail) else "info")
    cat = {"alert": "security" if "공격" in detail else "network", "security_audit": "security",
           "read": "device_status"}.get(o["action"], "ops")
    key = _hash("audit", o.get("time"), o.get("actor"), o["action"], detail)
    cur = conn.execute("INSERT OR IGNORE INTO events(event_key,occurred_at,ingested_at,category,event_type,"
                       "actor,device_id,severity,correlation_id,detail,raw_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (key, occurred, _now(), cat, o["action"], o.get("actor"), _device_in(detail),
                        sev, _corr(detail), detail, raw_id))
    return bool(cur.rowcount)


def _h_chat(conn, o, raw_id):
    """chat.jsonl -> events (대화 기록 연결; 도구 세부는 incidents/tool_calls에 구조화되어 있음)."""
    occurred = _iso(o.get("time"))
    if not occurred or not o.get("kind"):
        raise ValueError("time/kind 누락")
    text = _mask(o.get("text", ""))[:500]
    key = _hash("chat", o.get("time"), o.get("kind"), text)
    cur = conn.execute("INSERT OR IGNORE INTO events(event_key,occurred_at,ingested_at,category,event_type,"
                       "actor,device_id,severity,correlation_id,detail,raw_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (key, occurred, _now(), "conversation", o["kind"], o.get("kind"),
                        o.get("tool") and _device_in(text), "info", _corr(text), text, raw_id))
    return bool(cur.rowcount)


def _h_case(conn, o, raw_id):
    """cases.json 항목 -> incidents + tool_calls (장애 사건 + AI 실행·도구 호출 기록)."""
    iid = str(o.get("id"))
    if not o.get("id"):
        raise ValueError("case id 누락")
    tok = o.get("tokens") or {}
    conn.execute("INSERT OR REPLACE INTO incidents(incident_id,occurred_at,ingested_at,source,trigger,model,"
                 "provider,status,resolution,steps,elapsed_sec,tokens_input,tokens_output,basis,proposed_action,"
                 "approval_ids,raw_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (iid, _iso(o.get("started")), _now(), o.get("source"), _mask(o.get("trigger", "")),
                  o.get("model"), o.get("provider"), o.get("status"), o.get("resolution"), o.get("steps"),
                  o.get("elapsed_sec"), tok.get("input"), tok.get("output"), _mask(o.get("basis", "")),
                  _mask(o.get("proposed_action", "")), json.dumps(o.get("approval_ids") or []), raw_id))
    for seq, t in enumerate(o.get("tools") or []):
        key = _hash("tool", iid, seq, t.get("time"), t.get("name"))
        conn.execute("INSERT OR IGNORE INTO tool_calls(call_key,incident_id,seq,occurred_at,tool,device_id,"
                     "is_error,summary,raw_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (key, iid, seq, _iso(t.get("time")), t.get("name"), t.get("device") or None,
                      1 if t.get("error") else 0, _mask(t.get("summary", ""))[:300], raw_id))
    return True


def _ingest_cases(conn, run_id, path, stats):
    if not os.path.exists(path):
        return
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        stats["error"] += 1
        conn.execute("INSERT INTO ingest_errors(run_id,source,line_no,reason,raw,ingested_at) VALUES(?,?,?,?,?,?)",
                     (run_id, "cases.json", 0, f"파일 로드 실패: {e}", "", _now()))
        return
    for o in data:
        stats["read"] += 1
        raw_id, is_new = _land(conn, run_id, "cases.json", o.get("id", 0), o)
        if not is_new:                      # 내용이 바뀌지 않은 사건은 재처리하지 않는다(멱등)
            stats["dup"] += 1
            continue
        try:
            _h_case(conn, o, raw_id)        # 바뀐 사건만 업서트(상태·토큰 등 갱신)
            stats["loaded"] += 1
        except (KeyError, ValueError, TypeError) as e:
            stats["error"] += 1
            conn.execute("INSERT INTO ingest_errors(run_id,source,line_no,reason,raw,ingested_at) VALUES(?,?,?,?,?,?)",
                         (run_id, "cases.json", o.get("id", 0), f"정규화 실패: {e}",
                          json.dumps(o, ensure_ascii=False)[:500], _now()))


def _ingest_usage(conn, run_id, path, stats):
    if not os.path.exists(path):
        return
    try:
        u = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return
    stats["read"] += 1
    raw_id, _ = _land(conn, run_id, "usage.json", 0, u)
    key = _hash("usage", u.get("provider"), u.get("calls"), u.get("input"), u.get("output"), u.get("usd"))
    cur = conn.execute("INSERT OR IGNORE INTO model_usage(usage_key,captured_at,provider,model,calls,"
                       "input_tokens,output_tokens,usd,raw_id) VALUES(?,?,?,?,?,?,?,?,?)",
                       (key, _now(), u.get("provider"), u.get("model"), u.get("calls"),
                        u.get("input"), u.get("output"), u.get("usd"), raw_id))
    stats["loaded" if cur.rowcount else "dup"] += 1


# ------------------------------------------------------------------ 실행

def ingest(data_dir, db_path):
    conn = connect(db_path)
    run = conn.execute("INSERT INTO ingest_runs(started_at,files,rows_read,rows_loaded,rows_dup,rows_error)"
                       " VALUES(?,?,0,0,0,0)", (_now(), "")).lastrowid
    stats = {"read": 0, "loaded": 0, "dup": 0, "error": 0}
    files = []
    for name, source, handler in (("audit.jsonl", "audit.jsonl", _h_audit), ("chat.jsonl", "chat.jsonl", _h_chat)):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            files.append(name)
        _ingest_jsonl(conn, run, p, source, handler, stats)
    if os.path.exists(os.path.join(data_dir, "cases.json")):
        files.append("cases.json")
    _ingest_cases(conn, run, os.path.join(data_dir, "cases.json"), stats)
    if os.path.exists(os.path.join(data_dir, "usage.json")):
        files.append("usage.json")
    _ingest_usage(conn, run, os.path.join(data_dir, "usage.json"), stats)
    conn.execute("UPDATE ingest_runs SET finished_at=?,files=?,rows_read=?,rows_loaded=?,rows_dup=?,rows_error=?"
                 " WHERE run_id=?", (_now(), ",".join(files), stats["read"], stats["loaded"], stats["dup"],
                                     stats["error"], run))
    conn.commit()
    stats["run_id"] = run
    return conn, stats


def quality_report(conn):
    """데이터 품질·정합성 점검 결과를 dict로 돌려준다(검증 기준 §6.2)."""
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    checks = {
        "events": q("SELECT COUNT(*) FROM events"),
        "incidents": q("SELECT COUNT(*) FROM incidents"),
        "tool_calls": q("SELECT COUNT(*) FROM tool_calls"),
        "raw_events": q("SELECT COUNT(*) FROM raw_events"),
        "ingest_errors": q("SELECT COUNT(*) FROM ingest_errors"),
        # 정합성/품질
        "events_null_time": q("SELECT COUNT(*) FROM events WHERE occurred_at IS NULL"),
        "orphan_tool_calls": q("SELECT COUNT(*) FROM tool_calls t "
                               "WHERE NOT EXISTS(SELECT 1 FROM incidents i WHERE i.incident_id=t.incident_id)"),
        "dup_event_keys": q("SELECT COUNT(*)-COUNT(DISTINCT event_key) FROM events"),
        "unmasked_secrets": q("SELECT COUNT(*) FROM events WHERE detail LIKE 'sk-ant-%' "
                              "OR detail LIKE 'AIza%' OR detail LIKE 'AQ.%'"),
        "non_iso_time": q("SELECT COUNT(*) FROM events WHERE occurred_at NOT LIKE '____-__-__T__:__:__%'"),
    }
    checks["ok"] = all(checks[k] == 0 for k in ("events_null_time", "orphan_tool_calls", "dup_event_keys",
                                                "unmasked_secrets", "non_iso_time"))
    return checks


def retention(conn, days):
    """보존 기간이 지난 원본(raw_events)을 삭제한다. 정제 테이블은 보존한다."""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400)) + TZ
    n = conn.execute("DELETE FROM raw_events WHERE ingested_at < ?", (cutoff,)).rowcount
    conn.commit()
    return n


def main():
    ap = argparse.ArgumentParser(description="운영 데이터 파이프라인 (Phase 5)")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--db", default=os.path.join(DEFAULT_DATA, "ops_dw.sqlite"))
    ap.add_argument("--quality", action="store_true")
    ap.add_argument("--retention", type=int, metavar="DAYS")
    a = ap.parse_args()
    conn, stats = ingest(a.data, a.db)
    print(f"적재 완료 run#{stats['run_id']}: 읽음 {stats['read']} · 신규 {stats['loaded']} · "
          f"중복 {stats['dup']} · 오류 {stats['error']}  -> {a.db}")
    if a.retention:
        print(f"보존 정리: {retention(conn, a.retention)}개 원본 삭제(>{a.retention}일)")
    if a.quality:
        r = quality_report(conn)
        print("데이터 품질:", "OK" if r["ok"] else "문제 있음")
        for k, v in r.items():
            if k != "ok":
                print(f"  {k}: {v}")
    conn.close()


if __name__ == "__main__":
    main()
