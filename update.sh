#!/usr/bin/env bash
set -e

REPO="xiaofujie369/xboard-xray-docker-sync"
BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

SYNC_DIR="/opt/xray-sync"
XRAY_DIR="/opt/xray"

if [ "$(id -u)" != "0" ]; then
  echo "Please run as root."
  exit 1
fi

UPDATE_DONE=0
restore_services_on_exit() {
  if [ "$UPDATE_DONE" != "1" ]; then
    echo "[WARN] 更新中断，尝试恢复 xboard 服务..."
    systemctl start xboard-sync 2>/dev/null || true
    systemctl start xboard-report 2>/dev/null || true
    systemctl start xboard-udp-guard 2>/dev/null || true
  fi
}
trap restore_services_on_exit EXIT

echo "[1/6] 停止服务..."
systemctl stop xboard-sync 2>/dev/null || true
systemctl stop xboard-report 2>/dev/null || true
systemctl stop xboard-udp-guard 2>/dev/null || true

echo "[2/6] 备份旧脚本..."
mkdir -p "$SYNC_DIR/backup"
cp "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/backup/xboard_sync.py.$(date +%F-%H%M%S)" 2>/dev/null || true
cp "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/backup/xboard_report.py.$(date +%F-%H%M%S)" 2>/dev/null || true
cp "$SYNC_DIR/udp_guard.py" "$SYNC_DIR/backup/udp_guard.py.$(date +%F-%H%M%S)" 2>/dev/null || true
cp "$SYNC_DIR/manage.sh" "$SYNC_DIR/backup/manage.sh.$(date +%F-%H%M%S)" 2>/dev/null || true

echo "[3/6] 下载新脚本..."
curl -fsSL "${RAW_BASE}/sync/xboard_sync.py" -o "$SYNC_DIR/xboard_sync.py"
curl -fsSL "${RAW_BASE}/sync/xboard_report.py" -o "$SYNC_DIR/xboard_report.py"
curl -fsSL "${RAW_BASE}/sync/udp_guard.py" -o "$SYNC_DIR/udp_guard.py"
curl -fsSL "${RAW_BASE}/sync/healthcheck.sh" -o "$SYNC_DIR/healthcheck.sh"
curl -fsSL "${RAW_BASE}/sync/manage.sh" -o "$SYNC_DIR/manage.sh"

cp "$SYNC_DIR/manage.sh" /usr/local/bin/xray-sync
cp "$SYNC_DIR/manage.sh" /usr/local/bin/xbr
chmod +x "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/udp_guard.py" "$SYNC_DIR/healthcheck.sh" "$SYNC_DIR/manage.sh" /usr/local/bin/xray-sync /usr/local/bin/xbr

mkdir -p "$XRAY_DIR/logs"
touch "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"
chown -R 65532:65532 "$XRAY_DIR/logs"
chmod 750 "$XRAY_DIR/logs"
chmod 640 "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"

echo "[4/6] 更新 systemd 服务..."
curl -fsSL "${RAW_BASE}/systemd/xboard-sync.service" -o /etc/systemd/system/xboard-sync.service
curl -fsSL "${RAW_BASE}/systemd/xboard-report.service" -o /etc/systemd/system/xboard-report.service
curl -fsSL "${RAW_BASE}/systemd/xboard-udp-guard.service" -o /etc/systemd/system/xboard-udp-guard.service
systemctl daemon-reload
systemctl enable xboard-sync xboard-report xboard-udp-guard

echo "[5/6] 重新同步配置..."
cd "$SYNC_DIR"
python3 "$SYNC_DIR/xboard_sync.py" once

echo "[6/6] 重启服务..."
systemctl restart xboard-sync
systemctl restart xboard-report
systemctl restart xboard-udp-guard

UPDATE_DONE=1
trap - EXIT

echo "更新完成。"
echo "管理菜单: xbr"
