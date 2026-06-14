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

bash <(curl -fsSL https://raw.githubusercontent.com/xiaofujie369/xboard-xray-docker-sync/main/install.sh)

After installation, use the management menu:

xbr

## Manual Install

git clone https://github.com/xiaofujie369/xboard-xray-docker-sync.git
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
/opt/xray/logs/access.log
/opt/xray-sync
/opt/xray-sync/.env
/opt/xray-sync/report_state.json
/opt/xray-sync/xboard_sync.py
/opt/xray-sync/xboard_report.py
/opt/xray-sync/healthcheck.sh

## Services

systemctl status xboard-sync --no-pager
systemctl status xboard-report --no-pager

## Management Menu

Run as root:

xbr

The longer `xray-sync` command is still installed as a compatibility alias.

Menu features:

- Edit panel config
- Install, update, uninstall
- Start, stop, restart services
- View status and logs
- Sync panel config now
- Inspect generated node config
- Check Xray config JSON and port conflicts
- Check TLS certificate files and openssl output
- Open generated node ports in ufw
- Backup and restore config.json

## Health Check

/opt/xray-sync/healthcheck.sh

## Traffic and Online Reporting

xboard-report reads Xray Stats API and reports to XBoard through `/api/v2/server/report`.
Each report includes node status, so the panel can keep the node online even when no user traffic is generated.

It also reads /opt/xray/logs/access.log incrementally to report real user IPs and online counts.
Recently active users are kept for `REPORT_ONLINE_TTL` seconds, default `180`, so online counts do not drop just because no new access log line appeared in the current report window.

If online users or traffic are not visible, run:

/opt/xray-sync/healthcheck.sh
journalctl -u xboard-report -n 100 --no-pager

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

## Custom Outbounds and Routes

This project supports XBoard per-node custom outbounds and custom routes.

You can configure different outbound rules for each node in XBoard.

XBoard route groups selected on a node are also synced. `block`, `direct`, and `proxy` actions are compiled into Xray routing rules bound to that node inbound; `dns` actions are compiled into Xray DNS server rules. Dangerous global matchers such as `*`, `0.0.0.0/0`, and `::/0` are ignored in panel route groups by default, and wildcard default DNS routes are ignored unless `XRAY_ENABLE_PANEL_DEFAULT_DNS=true` is set.

Custom outbounds are definitions only; they do not affect traffic until a custom route or panel proxy route references them. Per-node custom outbound tags are automatically scoped, so two nodes can both define `ss-us` without sharing the same outbound. Per-node custom routes are forced to the current node inbound; routes targeting another node inbound are ignored for stability.
If a route references an outbound that is not defined on that node, that route is ignored instead of being written into Xray config.

Example:

- Node 249 uses VLESS Reality inbound on port 443
- Node 249 custom outbound uses another upstream VLESS/TLS/Vision node
- Only traffic from inbound tag `vless-443` will be routed to this outbound

### Custom Route Example

```json
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-tls"
  }
]
[
  {
    "tag": "relay-vless-tls",
    "protocol": "vless",
    "settings": {
      "vnext": [
        {
          "address": "example.com",
          "port": 443,
          "users": [
            {
              "id": "YOUR-UPSTREAM-VLESS-UUID",
              "encryption": "none",
              "flow": "xtls-rprx-vision"
            }
          ]
        }
      ]
    },
    "streamSettings": {
      "network": "tcp",
      "security": "tls",
      "tlsSettings": {
        "serverName": "example.com",
        "allowInsecure": false,
        "fingerprint": "edge"
      }
    }
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-tls"
  }
]
[
  {
    "tag": "relay-vless-reality",
    "protocol": "vless",
    "settings": {
      "vnext": [
        {
          "address": "example.com",
          "port": 443,
          "users": [
            {
              "id": "YOUR-UPSTREAM-VLESS-UUID",
              "encryption": "none",
              "flow": "xtls-rprx-vision"
            }
          ]
        }
      ]
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "serverName": "www.microsoft.com",
        "fingerprint": "edge",
        "publicKey": "YOUR-REALITY-PUBLIC-KEY",
        "shortId": "YOUR-REALITY-SHORT-ID",
        "spiderX": "/"
      }
    }
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-reality"
  }
]
[
  {
    "tag": "relay-trojan-tls",
    "protocol": "trojan",
    "settings": {
      "servers": [
        {
          "address": "example.com",
          "port": 443,
          "password": "YOUR-TROJAN-PASSWORD"
        }
      ]
    },
    "streamSettings": {
      "network": "tcp",
      "security": "tls",
      "tlsSettings": {
        "serverName": "example.com",
        "allowInsecure": false,
        "fingerprint": "edge"
      }
    }
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-trojan-tls"
  }
]
[
  {
    "tag": "relay-shadowsocks",
    "protocol": "shadowsocks",
    "settings": {
      "servers": [
        {
          "address": "example.com",
          "port": 8388,
          "method": "chacha20-ietf-poly1305",
          "password": "YOUR-SHADOWSOCKS-PASSWORD"
        }
      ]
    }
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-shadowsocks"
  }
]
[
  {
    "tag": "relay-socks5",
    "protocol": "socks",
    "settings": {
      "servers": [
        {
          "address": "example.com",
          "port": 1080,
          "users": [
            {
              "user": "YOUR-SOCKS-USER",
              "pass": "YOUR-SOCKS-PASSWORD"
            }
          ]
        }
      ]
    }
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-socks5"
  }
]
[
  {
    "tag": "relay-http",
    "protocol": "http",
    "settings": {
      "servers": [
        {
          "address": "example.com",
          "port": 8080,
          "users": [
            {
              "user": "YOUR-HTTP-USER",
              "pass": "YOUR-HTTP-PASSWORD"
            }
          ]
        }
      ]
    }
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-http"
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "direct"
  }
]
[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "block"
  }
]

---

## 3. 提交并推送

```bash
git status

git add sync/xboard_sync.py README.md

git commit -m "Support XBoard per-node custom outbounds and add outbound examples"

git push

## Custom Outbounds

This project supports XBoard per-node custom outbounds and custom routes.

See:

docs/custom-outbounds.md

Supported common custom outbound examples:

- VLESS + TLS + Vision
- VLESS + TLS
- VLESS + Reality
- Trojan + TLS
- Shadowsocks
- SOCKS5
- HTTP Proxy
- Direct route
- Block route
