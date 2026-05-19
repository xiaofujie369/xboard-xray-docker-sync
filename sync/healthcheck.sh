#!/usr/bin/env bash

echo "===== Docker Container ====="
docker ps -a | grep xray-core || true

echo
echo "===== Ports ====="
ss -lntup | grep -E '10085|31059|45123|xray' || true

echo
echo "===== Xray Config Inbounds ====="
cat /opt/xray/config/config.json | jq '.inbounds[] | {
  tag,
  listen,
  port,
  protocol,
  method: .settings.method,
  has_server_password: (.settings.password != null),
  security: .streamSettings.security,
  clients_count: (
    if .settings.clients then (.settings.clients | length)
    else null
    end
  )
}' || true

echo
echo "===== Xray Stats API ====="
docker exec xray-core xray api statsquery --server=127.0.0.1:10085 -pattern "user>>>" || true

echo
echo "===== xboard-sync Service ====="
systemctl status xboard-sync --no-pager || true

echo
echo "===== xboard-report Service ====="
systemctl status xboard-report --no-pager || true

echo
echo "===== xboard-report Logs ====="
journalctl -u xboard-report -n 30 --no-pager || true

echo
echo "===== Xray Access Log ====="
ls -lh /opt/xray/logs/access.log 2>/dev/null || true
tail -n 10 /opt/xray/logs/access.log 2>/dev/null || true
