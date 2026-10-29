"""Run a cmd.exe command inside a Windows VM via Guest Additions with config.py's lab account.

usage: py -3 winexec.py VM "command" [timeout_sec]
"""
import os, subprocess, sys, tempfile

sys.path.insert(0, r"C:\Users\User\Desktop\claude-test\gns3-agent")
import config

vm, cmd = sys.argv[1], sys.argv[2]
timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 60
fd, pwfile = tempfile.mkstemp()
os.write(fd, config.VM_PASSWORD.encode())
os.close(fd)
try:
    r = subprocess.run([config.VBOXMANAGE, "guestcontrol", vm, "run", "--exe", r"C:\Windows\System32\cmd.exe",
                        "--username", config.VM_USERNAME, "--passwordfile", pwfile, "--wait-stdout", "--wait-stderr",
                        "--timeout", str(timeout * 1000), "--", "/c", cmd],
                       capture_output=True, timeout=timeout + 30)
    out = (r.stdout + r.stderr).decode("cp949", errors="replace")
    print(f"[exit {r.returncode}]\n{out.strip()[-3000:]}")
finally:
    os.unlink(pwfile)
