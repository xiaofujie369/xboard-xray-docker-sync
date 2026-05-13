# XBoard Xray Docker Sync

Official xray-core Docker deployment with XBoard panel sync and traffic report.

This project does not use Xboard-Node, V2bX, or XrayR. It uses official xray-core plus lightweight Python sync/report scripts.

## Features

- Official xray-core Docker
- XBoard node config sync
- XBoard user sync
- XBoard traffic report
- Multi-node support
- Multi-protocol support
- systemd auto start
- Restart on failure after 60 seconds
- Health check script

## Supported Protocols

Supported by official xray-core:

- VLESS
- VLESS Reality
- VMess
- Trojan
- Shadowsocks
- Shadowsocks TCP/UDP

Not supported by official xray-core:

- AnyTLS
- Hysteria2
- TUIC

Use sing-box for AnyTLS, Hysteria2, and TUIC.

## Tested

- VLESS Reality
- Shadowsocks chacha20-ietf-poly1305

## Important Notes

Do not commit real secrets:

- PANEL_TOKEN
- Reality privateKey
- Shadowsocks server_key
- User UUID list
- /opt/xray-sync/.env

For Shadowsocks 2022:

- 2022-blake3-aes-256-gcm requires valid base64 PSK for server and clients.
- If your XBoard only returns UUID as user password, use chacha20-ietf-poly1305 or aes-128-gcm instead.

## Quick Install

Replace YOUR_USERNAME with your GitHub username.

bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/xboard-xray-docker-sync/main/install.sh)

## Manual Install

git clone https://github.com/YOUR_USERNAME/xboard-xray-docker-sync.git
cd xboard-xray-docker-sync
bash install.sh

## Node List Format

NODES=node_id:protocol,node_id:protocol

Examples:

NODES=3047:vless
NODES=3047:vless,8881:shadowsocks
NODES=3047:vless,8881:shadowsocks,8882:trojan,8883:vmess

## Runtime Files

/opt/xray
/opt/xray/config/config.json
/opt/xray/docker-compose.yml
/opt/xray-sync
/opt/xray-sync/.env
/opt/xray-sync/xboard_sync.py
/opt/xray-sync/xboard_report.py
/opt/xray-sync/healthcheck.sh

## Services

systemctl status xboard-sync --no-pager
systemctl status xboard-report --no-pager

## Health Check

/opt/xray-sync/healthcheck.sh

## Update

cd xboard-xray-docker-sync
git pull
bash update.sh

## Uninstall

bash uninstall.sh

## Firewall

Open all node ports in your server firewall and cloud security group.

Example:

ufw allow 31059/tcp
ufw allow 45123/tcp
ufw allow 45123/udp

## License

MIT
