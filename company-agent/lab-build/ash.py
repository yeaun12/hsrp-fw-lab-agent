"""Run a shell script (or -c command) on AGENT-PC over the NAT port forward, or copy files to it.

usage:
  py -3 ash.py script.sh
  py -3 ash.py -c "command"
  py -3 ash.py put LOCAL REMOTE      (file or directory, via scp -r)
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SSH_OPTS = ["-i", os.path.join(HERE, "agentpc_key"), "-o", f"UserKnownHostsFile={os.path.join(HERE, 'agentpc_known_hosts')}",
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]

if sys.argv[1] == "put":
    sys.exit(subprocess.run(["scp", "-q", "-r", "-P", "2222", *SSH_OPTS, sys.argv[2], f"root@127.0.0.1:{sys.argv[3]}"]).returncode)
script = sys.argv[2] if sys.argv[1] == "-c" else open(sys.argv[1], encoding="utf-8").read()
r = subprocess.run(["ssh", "-p", "2222", *SSH_OPTS, "root@127.0.0.1", "bash -s"],
                   input=script.replace("\r\n", "\n").encode(), capture_output=True)
sys.stdout.write(r.stdout.decode(errors="replace"))
sys.stderr.write(r.stderr.decode(errors="replace"))
sys.exit(r.returncode)
