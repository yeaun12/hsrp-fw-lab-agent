"""pfSense serial console session (menu/tcsh prompts don't match the IOS prompt regex).

usage:
  py -3 fwser.py wait                    wait for the console menu
  py -3 fwser.py sh "cmd" ["cmd" ...]   run shell commands (enters option 8 if at the menu)
  py -3 fwser.py put LOCAL DEST         write a local file on the guest via heredoc
"""
import re, socket, sys, time
from g import node_port, strip_telnet

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MENU = re.compile(r"Enter an option:\s*$")
SHELL = re.compile(r"root:\s*$")


def session():
    return socket.create_connection(("localhost", node_port("HSRP-FW-LAB", "FW")), timeout=0.5)


def read(s, timeout, pattern=None):
    buf, end = "", time.time() + timeout
    while time.time() < end:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            chunk = b""
        if chunk:
            buf += strip_telnet(chunk).decode(errors="replace")
            if pattern and pattern.search(ANSI.sub("", buf)):
                return ANSI.sub("", buf), True
    return ANSI.sub("", buf), False


def to_shell(s):
    s.sendall(b"\r")
    out, _ = read(s, 5, re.compile(r"(Enter an option:|root:)\s*$"))
    if MENU.search(out):
        s.sendall(b"8\r")
        read(s, 8)  # resizewin probes the terminal and swallows input for a few seconds
    s.sendall(b"\r")
    read(s, 5, SHELL)


def sh(cmds, t=60):
    s = session()
    to_shell(s)
    out = []
    for c in cmds:
        s.sendall(c.encode() + b"\r")
        o, ok = read(s, t, SHELL)
        out.append(o if ok else o + "\n[no prompt]")
    s.close()
    print("".join(out)[-8000:])


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "wait":
        end = time.time() + 600
        while time.time() < end:
            s = session(); s.sendall(b"\r")
            o, ok = read(s, 8, re.compile(r"(Enter an option:|root:)\s*$")); s.close()
            if ok:
                print("ready"); break
            time.sleep(10)
        else:
            print("timeout")
    elif cmd == "sh":
        sh(sys.argv[2:])
    elif cmd == "put":  # base64 in chunks: no shell quoting or heredoc surprises on tcsh
        import base64
        b64 = base64.b64encode(open(sys.argv[2], "rb").read()).decode()
        dest = sys.argv[3]
        cmds = [f"rm -f {dest}.b64"] + [f"echo {b64[i:i + 200]} >> {dest}.b64" for i in range(0, len(b64), 200)]
        sh(cmds + [f"b64decode -r < {dest}.b64 > {dest}", f"md5 -q {dest}"])
    elif cmd == "shf":  # commands from a local file, one per line
        sh([l for l in open(sys.argv[2], encoding="utf-8").read().splitlines() if l.strip()])
