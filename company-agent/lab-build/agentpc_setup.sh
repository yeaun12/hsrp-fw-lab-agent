hostnamectl set-hostname agent-pc
for c in $(nmcli -g UUID con show); do nmcli con delete $c; done
nmcli con add type ethernet ifname enp0s3 con-name lab ipv4.method manual ipv4.addresses 10.0.40.50/24 ipv4.never-default yes ipv4.routes "10.0.0.0/15 10.0.40.254, 211.0.1.0/28 10.0.40.254" ipv6.method disabled connection.autoconnect yes
nmcli con add type ethernet ifname enp0s8 con-name internet ipv4.method auto ipv6.method disabled connection.autoconnect yes
nmcli con up lab; nmcli con up internet
mkdir -p /root/.ssh; chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGBK0obycyd2EaKE2w4GCyecll3jjuv/0wB1nHyluKz1 claude-lab-setup' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
systemctl enable --now sshd
echo AGENTPC-DONE; ip -4 -br addr; for h in 10.0.40.254 10.0.99.1 10.1.10.1 10.0.30.10 211.0.1.10; do ping -c2 -W1 $h >/dev/null && echo "$h ok" || echo "$h FAIL"; done
