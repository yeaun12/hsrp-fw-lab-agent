"""GNS3 REST + telnet console helper (stdlib only), mirrors gns3_tools.py.

usage:
  py -3 g.py api METHOD PATH [JSON]
  py -3 g.py con PROJECT NODE [--t SEC] [--raw] < commands.txt   (one command per line)
"""
import json, re, socket, sys, time, urllib.request

BASE = "http://localhost:3080/v2"
PROMPT_RE = re.compile(rb"([\w.\-()/@:~]+[>#$%]|(?i:login|username|password):)\s*$")


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:800].decode(errors='replace')}")


def strip_telnet(data):
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == 255 and i + 1 < len(data):
            c = data[i + 1]
            if c == 250:
                end = data.find(b"\xff\xf0", i)
                i = len(data) if end < 0 else end + 2
            elif c == 255:
                out.append(255); i += 2
            else:
                i += 3
        else:
            out.append(data[i]); i += 1
    return bytes(out)


def read_until(s, timeout, pattern=PROMPT_RE):
    buf, end = b"", time.time() + timeout
    while time.time() < end:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            chunk = b""
        if chunk:
            buf += strip_telnet(chunk)
            if b"--More--" in buf[-40:] or b"-- More --" in buf[-40:]:
                s.sendall(b" "); continue
            if pattern.search(buf.rstrip(b"\r\n ") + b" "):
                return buf.decode(errors="replace"), True
    return buf.decode(errors="replace"), False


def node_port(project, node):
    pid = next(p["project_id"] for p in api("GET", "/projects") if p["name"] == project)
    n = next(n for n in api("GET", f"/projects/{pid}/nodes") if n["name"] == node)
    return n["console"]


def con(project, node, cmds, t=15, raw=False):
    s = socket.create_connection(("localhost", node_port(project, node)), timeout=1)
    out = []
    try:
        s.sendall(b"\r\n" if not raw else b"")
        out.append(read_until(s, 5)[0])
        for c in cmds:
            if c.startswith("#sleep "):
                time.sleep(float(c.split()[1])); out.append(read_until(s, 1)[0]); continue
            s.sendall(c.encode() + b"\r\n" if not raw else c.encode().decode("unicode_escape").encode())
            o, ok = read_until(s, t)
            out.append(o if ok else o + "\n[no prompt]")
            if not raw and re.search(r"% (Invalid|Incomplete|Ambiguous)", o):
                out.append(f"[ERROR after '{c}' - stopped]"); break
    finally:
        s.close()
    print("".join(out)[-8000:])


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "api":
        r = api(a[1], a[2], json.loads(a[3]) if len(a) > 3 else None)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:20000])
    elif a[0] == "con":
        t = float(a[a.index("--t") + 1]) if "--t" in a else 15
        cmds = [l.rstrip("\r\n") for l in sys.stdin.read().splitlines() if l.strip()]
        con(a[1], a[2], cmds, t, "--raw" in a)
