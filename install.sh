#!/usr/bin/env bash
set -e

REPO="xiaofujie369/xboard-xray-docker-sync"
BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

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
apt install -y ca-certificates curl gnupg lsb-release python3 python3-requests jq ufw git openssl iproute2

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
else
  echo "[3/9] docker compose 已安装"
fi

echo "[3.5/9] 配置低端口绑定权限..."
cat > /etc/sysctl.d/99-xray-low-port.conf <<'EOS'
net.ipv4.ip_unprivileged_port_start=0
EOS
sysctl --system >/dev/null || true

echo "[4/9] 创建目录..."
mkdir -p "$XRAY_DIR/config" "$XRAY_DIR/logs" "$SYNC_DIR"
touch "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"
chmod 777 "$XRAY_DIR/logs"
chmod 666 "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"

echo "[5/9] 写入 Xray docker-compose.yml..."
cat > "$XRAY_DIR/docker-compose.yml" <<'EOC'
services:
  xray:
    image: ghcr.io/xtls/xray-core:26.5.9
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

echo "[7/9] 下载同步脚本..."
curl -fsSL "${RAW_BASE}/sync/xboard_sync.py" -o "$SYNC_DIR/xboard_sync.py"
curl -fsSL "${RAW_BASE}/sync/xboard_report.py" -o "$SYNC_DIR/xboard_report.py"
curl -fsSL "${RAW_BASE}/sync/healthcheck.sh" -o "$SYNC_DIR/healthcheck.sh"
curl -fsSL "${RAW_BASE}/sync/manage.sh" -o "$SYNC_DIR/manage.sh"
cp "$SYNC_DIR/manage.sh" /usr/local/bin/xray-sync
cp "$SYNC_DIR/manage.sh" /usr/local/bin/xbr
chmod +x "$SYNC_DIR/xboard_sync.py" "$SYNC_DIR/xboard_report.py" "$SYNC_DIR/healthcheck.sh" "$SYNC_DIR/manage.sh" /usr/local/bin/xray-sync /usr/local/bin/xbr

cat > "$SYNC_DIR/.env" <<EOFENV
PANEL_URL=$PANEL_URL
PANEL_TOKEN=$PANEL_TOKEN

XRAY_CONFIG=/opt/xray/config/config.json
XRAY_CONTAINER=xray-core
XRAY_CONTAINER_CONFIG_DIR=/etc/xray
XRAY_LOG_DIR=/opt/xray/logs
XRAY_CONFIG_BACKUPS=3
XRAY_PRESTART_TEST=true

SYNC_INTERVAL=60
REPORT_EMPTY_TRAFFIC_HEARTBEAT=true
NODES=$NODES
EOFENV

chmod 600 "$SYNC_DIR/.env"

echo "[8/9] 写入 systemd 服务..."
curl -fsSL "${RAW_BASE}/systemd/xboard-sync.service" -o /etc/systemd/system/xboard-sync.service
curl -fsSL "${RAW_BASE}/systemd/xboard-report.service" -o /etc/systemd/system/xboard-report.service

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
echo "  xbr"
echo "  xray-sync"
echo
echo "请确认云安全组 / 防火墙已放行节点端口。"
