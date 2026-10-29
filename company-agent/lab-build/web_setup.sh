IF=$(ls /sys/class/net | grep -v lo | head -1)
for c in $(nmcli -g UUID con show); do nmcli con mod $c connection.autoconnect no; done
nmcli con add type ethernet ifname $IF con-name dmz ipv4.method manual ipv4.addresses 211.0.1.10/28 ipv4.gateway 211.0.1.1 ipv6.method disabled connection.autoconnect yes
nmcli con up dmz
rpm -q httpd || echo NO-HTTPD
echo "HSRP-FW-LAB WEB OK" > /var/www/html/index.html
systemctl enable --now httpd
firewall-cmd --permanent --add-service=http --add-service=https
firewall-cmd --reload
echo WEB-SETUP-DONE; ip -4 addr show $IF | grep inet; systemctl is-active httpd
