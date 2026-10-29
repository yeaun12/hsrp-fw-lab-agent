IF=$(ls /sys/class/net | grep -v lo | head -1)
for c in $(nmcli -g UUID con show); do nmcli con mod $c connection.autoconnect no; done
nmcli con add type ethernet ifname $IF con-name srv ipv4.method manual ipv4.addresses 10.0.30.10/24 ipv4.gateway 10.0.30.254 ipv6.method disabled connection.autoconnect yes
nmcli con up srv
systemctl enable --now mariadb
firewall-cmd --permanent --add-service=mysql
firewall-cmd --reload
echo SRV-SETUP-DONE; ip -4 addr show $IF | grep inet; systemctl is-active mariadb; ss -ltn | grep 3306
