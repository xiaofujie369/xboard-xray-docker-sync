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

echo "[1/5] 停止服务..."
systemctl stop xboard-sync 2>/dev/null || true
systemctl stop xboard-report 2>/dev/null || true

echo "[2/5] 备份旧脚本..."
mkdir -p "$SYNC_DIR/backup"
cp "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/backup/xboard_sync.py.$(date +%F-%H%M%S)" 2>/dev/null || true
cp "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/backup/xboard_report.py.$(date +%F-%H%M%S)" 2>/dev/null || true

echo "[3/5] 下载新脚本..."
curl -fsSL "${RAW_BASE}/sync/xboard_sync.py" -o "$SYNC_DIR/xboard_sync.py"
curl -fsSL "${RAW_BASE}/sync/xboard_report.py" -o "$SYNC_DIR/xboard_report.py"
curl -fsSL "${RAW_BASE}/sync/healthcheck.sh" -o "$SYNC_DIR/healthcheck.sh"

chmod +x "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/healthcheck.sh"

mkdir -p "$XRAY_DIR/logs"
touch "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"
chmod 777 "$XRAY_DIR/logs"
chmod 666 "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"

echo "[4/5] 重新同步配置..."
cd "$SYNC_DIR"
python3 "$SYNC_DIR/xboard_sync.py" once

echo "[5/5] 重启服务..."
systemctl daemon-reload
systemctl restart xboard-sync
systemctl restart xboard-report

echo "更新完成。"
