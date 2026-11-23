#!/bin/sh
set -eu
# Only the unprivileged proxy may open connections to the approved upstream.
iptables-restore <<RULES
*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT DROP [0:0]
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A INPUT -i lo -d 127.0.0.1 -p tcp --dport 7890 -j ACCEPT
-A OUTPUT -o lo -d 127.0.0.1 -p tcp --dport 7890 -j ACCEPT
-A OUTPUT -o lo -s 127.0.0.1 -p tcp --sport 7890 -m conntrack --ctstate ESTABLISHED -j ACCEPT
-A OUTPUT -p tcp -d ${UPSTREAM_IP}/32 --dport ${UPSTREAM_PORT} -m owner --uid-owner 65532 -j ACCEPT
COMMIT
RULES
test "$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6)" = 1
# Workspace sees only the checked socket, mounted read-only. It has no external NIC.
rm -f /link/out.sock /link/status.sock
python3 -I /usr/local/bin/gateway-guard.py &
exec setpriv --reuid=65532 --regid=65532 --clear-groups --no-new-privs sing-box run -c /config/proxy.json
