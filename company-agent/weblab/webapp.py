"""랩 전용 테스트 웹 앱 (일부러 취약하게 만든 사내 포털).

격리된 GNS3 랩의 WEB 서버(DMZ)에서만 돌리는 교육용이다. SQL 인젝션과 반사형 XSS가 성립하도록 두어,
에이전트의 탐지·분석 기능이 실제로 동작하는지 검증하는 데 쓴다. 외부 패키지 없이 표준 라이브러리만 사용한다.

요청마다 /var/log/labapp/access.log 에 JSON 한 줄을 남긴다:
 {ts, ip, method, path, query, body, status, bytes, ua, note}
note 는 앱이 스스로 남기는 힌트(로그인 성공/실패, 검색 행 수)로, 탐지가 "성공 여부"를 판단할 근거가 된다.
"""
import html
import json
import os
import sqlite3
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = "/opt/labapp/portal.db"
LOG = "/var/log/labapp/access.log"
PORT = 80


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def log(ip, method, path, query, body, status, size, ua, note=""):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ip": ip, "method": method, "path": path,
           "query": query, "body": body, "status": status, "bytes": size, "ua": ua[:200], "note": note}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8><title>랩컴퍼니 사내 포털</title>
<style>body{{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 16px}}
input{{padding:6px}}.box{{border:1px solid #ccc;border-radius:8px;padding:16px;margin:16px 0}}</style></head>
<body><h2>랩컴퍼니 사내 포털</h2>
<div class=box><h3>로그인</h3><form method=post action=/login>
사번 <input name=user> 비밀번호 <input name=pw type=password> <button>로그인</button></form></div>
<div class=box><h3>제품 검색</h3><form method=get action=/search>
검색어 <input name=q value="{q}"> <button>검색</button></form>{result}</div>
<p style=color:#888>※ 교육용 테스트 포털 (격리 랩 전용)</p></body></html>"""


class H(BaseHTTPRequestHandler):
    server_version = "LabPortal/1.0"

    def _send(self, status, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return len(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(u.query)
        ua = self.headers.get("User-Agent", "")
        if u.path == "/search":
            q = qs.get("q", [""])[0]
            note, rows_html = "", ""
            try:
                # 취약: 사용자 입력을 쿼리에 직접 끼워 넣음 (SQL 인젝션 성립)
                sql = "SELECT name, price FROM products WHERE name LIKE '%" + q + "%'"
                rows = db().execute(sql).fetchall()
                note = f"rows={len(rows)}"
                rows_html = "<ul>" + "".join(f"<li>{html.escape(r['name'])} - {r['price']}원</li>" for r in rows) + "</ul>"
            except Exception as e:
                note = f"sqlerror={type(e).__name__}"
                rows_html = f"<p style=color:#c00>검색 오류: {e}</p>"   # 취약: 오류 메시지 노출
            # 취약: 검색어를 그대로 페이지에 반사 (반사형 XSS 성립)
            size = self._send(200, PAGE.format(q=q, result=rows_html))
            log(self.client_address[0], "GET", u.path, u.query, "", 200, size, ua, note)
        elif u.path in ("/", "/index.html"):
            size = self._send(200, PAGE.format(q="", result=""))
            log(self.client_address[0], "GET", u.path, u.query, "", 200, size, ua)
        else:
            size = self._send(404, "<h3>404 Not Found</h3>")
            log(self.client_address[0], "GET", u.path, u.query, "", 404, size, ua)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode(errors="replace")
        form = urllib.parse.parse_qs(raw)
        ua = self.headers.get("User-Agent", "")
        if self.path == "/login":
            user = form.get("user", [""])[0]
            pw = form.get("pw", [""])[0]
            masked = "user=" + user + "&pw=***"      # 비밀번호는 로그에 남기지 않음
            try:
                sql = "SELECT role FROM users WHERE user='" + user + "' AND pass='" + pw + "'"
                row = db().execute(sql).fetchone()
            except Exception as e:
                size = self._send(200, f"<h3>로그인 오류</h3><p>{e}</p>")
                log(self.client_address[0], "POST", "/login", "", masked, 200, size, ua, f"sqlerror={type(e).__name__}")
                return
            if row:
                body = f"<h3>환영합니다</h3><p>권한: {html.escape(row['role'])}</p>"
                note = f"login_success role={row['role']}"
            else:
                body = "<h3>로그인 실패</h3>"
                note = "login_fail"
            size = self._send(200, body)
            log(self.client_address[0], "POST", "/login", "", masked, 200, size, ua, note)
        else:
            size = self._send(404, "<h3>404</h3>")
            log(self.client_address[0], "POST", self.path, "", "", 404, size, ua)


def seed():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = db()
    c.executescript("""
        DROP TABLE IF EXISTS users; DROP TABLE IF EXISTS products;
        CREATE TABLE users(user TEXT, pass TEXT, role TEXT);
        INSERT INTO users VALUES('admin','S3cretAdmin!','administrator'),('hong','pw1234','staff'),('kim','sales99','staff');
        CREATE TABLE products(name TEXT, price INTEGER);
        INSERT INTO products VALUES('노트북 거치대',51250),('USB 허브',18900),('기계식 키보드',89000),
            ('27인치 모니터',245000),('웹캠',62000);""")
    c.commit()
    c.close()


if __name__ == "__main__":
    seed()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
