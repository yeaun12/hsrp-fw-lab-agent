mkdir -p /root/.ssh && chmod 700 /root/.ssh
grep -q lab-agent@agent-pc /root/.ssh/authorized_keys 2>/dev/null || echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDS7zR4bF/cwqixDIqBRHgabbg6FUaTPL27RTJCRWeLR lab-agent@agent-pc' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
systemctl enable --now sshd
firewall-cmd -q --add-service=ssh
mkdir -p /opt/labapp /var/log/labapp
echo BOOTSTRAP-DONE; ip -4 -br addr show enp0s3; systemctl is-active sshd
