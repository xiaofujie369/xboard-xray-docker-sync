# XBoard Per-Node Custom Outbounds and Routes

This project supports XBoard per-node custom outbounds and custom routes.

Custom outbounds are server-side only. They are used by the VPS Xray-core process and should not appear in client subscription links.

## How It Works

Example:

- Inbound node: vless-443
- Custom outbound: relay-vless-tls
- Route: vless-443 -> relay-vless-tls

The client connects to the current VPS inbound node first. Then Xray routes the traffic to the configured upstream outbound node.

## Custom Route Example

Use this in XBoard node custom routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-tls"
  }
]

Common generated inbound tags:

- vless-443
- vless-31059
- shadowsocks-45123
- trojan-443
- vmess-443

If custom routes do not include inboundTag, this script will automatically bind the route to the current node inbound tag.

---

## 1. VLESS + TLS + Vision Outbound

Use this when your upstream outbound node is VLESS + TLS + xtls-rprx-vision.

Custom Outbounds:

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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-tls"
  }
]

Important:

If the upstream node is VLESS + TLS + Vision, remember to add:

"flow": "xtls-rprx-vision"

---

## 2. VLESS + TLS Outbound

Use this when your upstream outbound node is normal VLESS + TLS without Vision flow.

Custom Outbounds:

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
              "encryption": "none"
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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-tls"
  }
]

---

## 3. VLESS + Reality Outbound

Use this when your upstream outbound node is VLESS + Reality.

Custom Outbounds:

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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-vless-reality"
  }
]

Important:

Use the upstream Reality public key, not the private key.

---

## 4. Trojan + TLS Outbound

Custom Outbounds:

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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-trojan-tls"
  }
]

---

## 5. Shadowsocks Outbound

Recommended methods:

- chacha20-ietf-poly1305
- aes-128-gcm
- aes-256-gcm

Custom Outbounds:

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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-shadowsocks"
  }
]

---

## 6. SOCKS5 Outbound

Custom Outbounds:

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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-socks5"
  }
]

Without username and password:

[
  {
    "tag": "relay-socks5",
    "protocol": "socks",
    "settings": {
      "servers": [
        {
          "address": "example.com",
          "port": 1080
        }
      ]
    }
  }
]

---

## 7. HTTP Proxy Outbound

Custom Outbounds:

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

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "relay-http"
  }
]

Without username and password:

[
  {
    "tag": "relay-http",
    "protocol": "http",
    "settings": {
      "servers": [
        {
          "address": "example.com",
          "port": 8080
        }
      ]
    }
  }
]

---

## 8. Direct Outbound Route

Use this if you want one node to use direct outbound.

Custom Routes:

[
  {
    "type": "field",
    "inboundTag": [
      "vless-443"
    ],
    "outboundTag": "direct"
  }
]

---

## 9. Block Outbound Route

Use this if you want one node to block traffic.

Custom Routes:

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

## Notes

- Custom outbounds are server-side only.
- Client apps only need the inbound node configuration.
- Do not put upstream UUID/password/private keys into public repositories.
- If using VLESS + TLS + Vision, add flow: xtls-rprx-vision.
- If using Reality outbound, use the upstream public key, not private key.
- Do not parse XBoard normal routes as Xray routing rules. XBoard routes may include DNS/domain policy rules.
