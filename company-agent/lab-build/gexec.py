"""Run shell commands inside a VirtualBox guest via Guest Additions (same as gns3_tools._guest_run).

usage: py -3 gexec.py VM CMDFILE      (one command per line; 'sudo ' prefix gets the password on stdin)
"""
import os, subprocess, sys, tempfile

sys.path.insert(0, r"C:\Users\User\Desktop\claude-test\gns3-agent")
import config

vm, cmdfile = sys.argv[1], sys.argv[2]
user, pw = config.VM_CREDENTIALS.get(vm) or (config.VM_USERNAME, config.VM_PASSWORD)
user, pw = os.environ.get("GEXEC_USER", user), os.environ.get("GEXEC_PW", pw)  # per-VM override, never written to disk
fd, pwfile = tempfile.mkstemp()
with os.fdopen(fd, "w") as f:
    f.write(pw)
try:
    for cmd in [l for l in open(cmdfile, encoding="utf-8").read().splitlines() if l.strip()]:
        run = cmd
        if cmd.startswith("sudo "):
            q = pw.replace("'", "'\\''")
            run = f"printf '%s\\n' '{q}' | sudo -S -p '' " + cmd[5:]
        r = subprocess.run([config.VBOXMANAGE, "guestcontrol", vm, "run", "--exe", "/bin/sh",
                            "--username", user, "--passwordfile", pwfile, "--wait-stdout", "--wait-stderr",
                            "--timeout", "90000", "--", "-c", run],  # VBox 7.2 passes --exe as argv[0] itself
                           capture_output=True, text=True, errors="replace", timeout=150)
        print(f"$ {cmd}\n{(r.stdout + r.stderr).strip()}\n[exit {r.returncode}]")
        if r.returncode != 0:
            break
finally:
    os.unlink(pwfile)
