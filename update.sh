#!/usr/bin/env bash
set -e

SYNC_DIR="/opt/xray-sync"
XRAY_DIR="/opt/xray"

if [ "$(id -u)" != "0" ]; then
  echo "Please run as root."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/5] 停止服务..."
systemctl stop xboard-sync 2>/dev/null || true
systemctl stop xboard-report 2>/dev/null || true

echo "[2/5] 备份旧脚本..."
mkdir -p "$SYNC_DIR/backup"
cp "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/backup/xboard_sync.py.$(date +%F-%H%M%S)" 2>/dev/null || true
cp "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/backup/xboard_report.py.$(date +%F-%H%M%S)" 2>/dev/null || true

echo "[3/5] 更新脚本..."
cp "$SCRIPT_DIR/sync/xboard_sync.py" "$SYNC_DIR/xboard_sync.py"
cp "$SCRIPT_DIR/sync/xboard_report.py" "$SYNC_DIR/xboard_report.py"
cp "$SCRIPT_DIR/sync/healthcheck.sh" "$SYNC_DIR/healthcheck.sh"
chmod +x "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/healthcheck.sh"

echo "[4/5] 重新同步配置..."
cd "$SYNC_DIR"
python3 "$SYNC_DIR/xboard_sync.py" once

echo "[5/5] 重启服务..."
systemctl daemon-reload
systemctl restart xboard-sync
systemctl restart xboard-report

echo "更新完成。"
