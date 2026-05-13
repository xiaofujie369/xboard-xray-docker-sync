#!/usr/bin/env bash
set -e

PROJECT_NAME="xboard-xray-docker-sync"
XRAY_DIR="/opt/xray"
SYNC_DIR="/opt/xray-sync"

echo "========================================"
echo " XBoard + Official Xray-core Docker Sync"
echo "========================================"
echo

if [ "$(id -u)" != "0" ]; then
  echo "Please run as root."
  exit 1
fi

read -rp "请输入 XBoard 面板地址，例如 https://bs.example.com: " PANEL_URL
read -rsp "请输入 XBoard 通讯密钥 TOKEN: " PANEL_TOKEN
echo
read -rp "请输入节点列表，例如 3047:vless,8881:shadowsocks: " NODES

if [ -z "$PANEL_URL" ] || [ -z "$PANEL_TOKEN" ] || [ -z "$NODES" ]; then
  echo "PANEL_URL / PANEL_TOKEN / NODES 不能为空"
  exit 1
fi

echo
echo "[1/9] 安装依赖..."
apt update
apt install -y ca-certificates curl gnupg lsb-release python3 python3-requests jq ufw git

if ! command -v docker >/dev/null 2>&1; then
  echo "[2/9] 安装 Docker..."
  curl -fsSL https://get.docker.com | bash
else
  echo "[2/9] Docker 已安装"
fi

systemctl enable docker --now

if ! docker compose version >/dev/null 2>&1; then
  echo "[3/9] 安装 docker compose plugin..."
  apt install -y docker-compose-plugin || true
fi

echo "[4/9] 创建目录..."
mkdir -p "$XRAY_DIR/config" "$XRAY_DIR/logs" "$SYNC_DIR"

echo "[5/9] 写入 Xray docker-compose.yml..."
cat > "$XRAY_DIR/docker-compose.yml" <<'EOC'
services:
  xray:
    image: ghcr.io/xtls/xray-core:latest
    container_name: xray-core
    restart: always
    network_mode: host
    volumes:
      - ./config:/etc/xray
      - ./logs:/var/log/xray
    command: ["run", "-config", "/etc/xray/config.json"]
EOC

echo "[6/9] 写入初始 Xray config.json..."
cat > "$XRAY_DIR/config/config.json" <<'EOC'
{
  "log": {
    "loglevel": "warning"
  },
  "inbounds": [],
  "outbounds": [
    {
      "tag": "direct",
      "protocol": "freedom",
      "settings": {}
    }
  ]
}
EOC

echo "[7/9] 复制同步脚本..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/sync/xboard_sync.py" "$SYNC_DIR/xboard_sync.py"
cp "$SCRIPT_DIR/sync/xboard_report.py" "$SYNC_DIR/xboard_report.py"
cp "$SCRIPT_DIR/sync/healthcheck.sh" "$SYNC_DIR/healthcheck.sh"
chmod +x "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/healthcheck.sh"

cat > "$SYNC_DIR/.env" <<EOFENV
PANEL_URL=$PANEL_URL
PANEL_TOKEN=$PANEL_TOKEN

XRAY_CONFIG=/opt/xray/config/config.json
XRAY_CONTAINER=xray-core

SYNC_INTERVAL=60
NODES=$NODES
EOFENV

chmod 600 "$SYNC_DIR/.env"

echo "[8/9] 写入 systemd 服务..."
cp "$SCRIPT_DIR/systemd/xboard-sync.service" /etc/systemd/system/xboard-sync.service
cp "$SCRIPT_DIR/systemd/xboard-report.service" /etc/systemd/system/xboard-report.service

systemctl daemon-reload
systemctl enable xboard-sync xboard-report

echo "[9/9] 启动 Xray 并执行首次同步..."
cd "$XRAY_DIR"
docker compose pull
docker compose up -d

cd "$SYNC_DIR"
python3 "$SYNC_DIR/xboard_sync.py" once

systemctl restart xboard-sync
systemctl restart xboard-report

echo
echo "========================================"
echo "安装完成"
echo "========================================"
echo
echo "查看状态:"
echo "  docker ps -a | grep xray"
echo "  systemctl status xboard-sync --no-pager"
echo "  systemctl status xboard-report --no-pager"
echo "  /opt/xray-sync/healthcheck.sh"
echo
echo "请确认云安全组 / 防火墙已放行节点端口。"
