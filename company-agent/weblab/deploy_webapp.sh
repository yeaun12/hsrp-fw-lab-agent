# 에이전트에서 실행: /tmp/webapp.py 를 WEB으로 보내고 서비스로 띄운다
set -e
K="-i /opt/lab-agent/keys/id_ed25519 -o UserKnownHostsFile=/opt/lab-agent/keys/known_hosts -o BatchMode=yes"
scp $K /tmp/webapp.py root@211.0.1.10:/opt/labapp/webapp.py
ssh $K root@211.0.1.10 'bash -s' <<'EOS'
set -e
cat > /etc/systemd/system/labapp.service <<UNIT
[Unit]
Description=Lab test portal (deliberately vulnerable, isolated lab only)
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /opt/labapp/webapp.py
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl disable --now httpd 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now labapp
sleep 2
systemctl is-active labapp
curl -s -o /dev/null -w "self GET / -> HTTP %{http_code}\n" http://127.0.0.1/
EOS
echo DEPLOY-DONE
