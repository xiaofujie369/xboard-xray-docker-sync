#!/usr/bin/env python3
import sys
import json
import time
import hashlib
import subprocess
from pathlib import Path

import requests

ENV_PATH = "/opt/xray-sync/.env"


def load_env():
    env = {}
    p = Path(ENV_PATH)
    if not p.exists():
        raise RuntimeError(f"配置文件不存在: {ENV_PATH}")

    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_nodes(env):
    """
    推荐格式：
    NODES=3047:vless,8881:shadowsocks,8882:trojan,8883:vmess

    兼容旧格式：
    NODE_ID=3047
    NODE_TYPE=vless
    """
    nodes = []

    if env.get("NODES"):
        for item in env["NODES"].split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise RuntimeError(f"NODES 格式错误: {item}，正确格式是 节点ID:协议")
            node_id, node_type = item.split(":", 1)
            nodes.append((node_id.strip(), normalize_node_type(node_type.strip())))
    else:
        nodes.append((env["NODE_ID"], normalize_node_type(env.get("NODE_TYPE", "vless"))))

    return nodes


def normalize_node_type(t):
    t = str(t).strip().lower()
    aliases = {
        "ss": "shadowsocks",
        "shadow": "shadowsocks",
        "shadowsocks2022": "shadowsocks",
        "v2ray": "vmess",
    }
    return aliases.get(t, t)


def stats_email(user_id, node_id=None):
    user_id = str(user_id)
    if node_id not in [None, ""]:
        return f"{node_id}:{user_id}"
    return user_id


def get_json(url):
    r = requests.get(url, timeout=25)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"接口返回不是 JSON: HTTP {r.status_code}, {r.text[:500]}")

    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {json.dumps(data, ensure_ascii=False)[:800]}")

    return data


def pick(d, *keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] not in [None, ""]:
            return d[k]
    return default


def parse_json_maybe(v):
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return v
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except Exception:
                return v
    return v


def unwrap_config(resp):
    if isinstance(resp, dict) and isinstance(resp.get("data"), dict):
        return resp["data"]
    return resp


def get_users_list(user_resp):
    if isinstance(user_resp, dict):
        if isinstance(user_resp.get("users"), list):
            return user_resp["users"]
        if isinstance(user_resp.get("data"), list):
            return user_resp["data"]
        if isinstance(user_resp.get("data"), dict):
            d = user_resp["data"]
            if isinstance(d.get("users"), list):
                return d["users"]

    if isinstance(user_resp, list):
        return user_resp

    return []


def get_server_port(server, default=443):
    return int(pick(server, "server_port", "port", "listen_port", default=default))


def get_listen_ip(server):
    return str(pick(server, "listen_ip", "listen", default="0.0.0.0"))


def get_network(server):
    return str(pick(server, "network", default="tcp") or "tcp").lower()


def get_network_settings(server):
    ns = pick(server, "networkSettings", "network_settings", default={})
    ns = parse_json_maybe(ns)
    return ns if isinstance(ns, dict) else {}


def get_tls_settings(server):
    ts = pick(server, "tls_settings", "tlsSettings", default={})
    ts = parse_json_maybe(ts)
    return ts if isinstance(ts, dict) else {}


def normalize_short_ids(v):
    v = parse_json_maybe(v)

    if v is None or v == "":
        return [""]

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        if "," in v:
            return [x.strip() for x in v.split(",") if x.strip()]
        return [v]

    return [str(v)]


def build_stream_settings(server):
    """
    统一处理 Xray streamSettings:
    - tcp
    - ws
    - grpc
    - httpupgrade
    - reality
    - tls
    """
    network = get_network(server)
    ns = get_network_settings(server)
    ts = get_tls_settings(server)

    tls_mode = pick(server, "tls", default=0)
    try:
        tls_mode = int(tls_mode)
    except Exception:
        tls_mode = 0

    stream = {
        "network": network
    }

    # -------- transport settings --------
    if network == "ws":
        path = pick(ns, "path", default="/")
        host = pick(ns, "host", "Host", default=None)

        ws_settings = {
            "path": str(path)
        }

        if host:
            ws_settings["headers"] = {
                "Host": str(host)
            }

        stream["wsSettings"] = ws_settings

    elif network == "grpc":
        service_name = pick(ns, "serviceName", "service_name", default="")
        stream["grpcSettings"] = {
            "serviceName": str(service_name)
        }

    elif network == "httpupgrade":
        path = pick(ns, "path", default="/")
        host = pick(ns, "host", "Host", default=None)

        httpupgrade_settings = {
            "path": str(path)
        }

        if host:
            httpupgrade_settings["host"] = str(host)

        stream["httpupgradeSettings"] = httpupgrade_settings

    elif network == "tcp":
        # tcp 默认无需额外 transport 参数
        pass

    else:
        # Xray 可能支持更多 network，但这里不主动生成未知结构
        stream["network"] = network

    # -------- security settings --------
    # XBoard 常见：
    # tls = 0 无
    # tls = 1 TLS
    # tls = 2 Reality
    #
    # Reality 字段：
    # tls_settings.server_name
    # tls_settings.server_port
    # tls_settings.private_key
    # tls_settings.short_id

    private_key = pick(ts, "private_key", "privateKey")
    short_id = pick(ts, "short_id", "shortId", "short_ids", "shortIds")
    server_name = pick(ts, "server_name", "serverName", "sni")
    reality_port = pick(ts, "server_port", "serverPort", default="443")

    private_key_is_pem = isinstance(private_key, str) and "BEGIN" in private_key
    if tls_mode == 2 or (private_key and not private_key_is_pem and tls_mode != 1):
        if not server_name:
            raise RuntimeError("Reality 缺少 tls_settings.server_name")
        if not private_key:
            raise RuntimeError("Reality 缺少 tls_settings.private_key")
        if short_id is None:
            raise RuntimeError("Reality 缺少 tls_settings.short_id")

        stream["security"] = "reality"
        stream["realitySettings"] = {
            "show": False,
            "dest": f"{server_name}:{reality_port}",
            "xver": 0,
            "serverNames": [
                str(server_name)
            ],
            "privateKey": str(private_key),
            "shortIds": normalize_short_ids(short_id)
        }

    elif tls_mode == 1:
        import os
        import glob
        import shutil

        cert_file = pick(ts, "cert_file", "certFile", "certificateFile")
        key_file = pick(ts, "key_file", "keyFile")

        cert_content = pick(
            ts,
            "cert",
            "crt",
            "certificate",
            "certificate_content",
            "certificateContent",
            "cert_content",
            "certContent",
            "public_key",
            "publicKey",
            "public"
        )

        key_content = pick(
            ts,
            "key",
            "private",
            "private_key",
            "privateKey",
            "private_key_content",
            "privateKeyContent",
            "key_content",
            "keyContent"
        )

        stream["security"] = "tls"
        tls_settings = {
            "serverName": str(server_name or "")
        }

        host_cert_dir = "/opt/xray/config/certs"
        container_cert_dir = "/etc/xray/certs"
        os.makedirs(host_cert_dir, exist_ok=True)

        node_id = pick(server, "id", "node_id", "nodeId", "NodeID", "server_id", "serverId")
        safe_name = str(server_name or f"node-{node_id or 'unknown'}")
        safe_name = safe_name.replace("*", "wildcard").replace("/", "_").replace(":", "_")

        def install_cert_pair(src_cert, src_key, name):
            dst_cert = os.path.join(host_cert_dir, f"{name}.crt")
            dst_key = os.path.join(host_cert_dir, f"{name}.key")

            shutil.copyfile(src_cert, dst_cert)
            shutil.copyfile(src_key, dst_key)

            os.chmod(dst_cert, 0o644)
            os.chmod(dst_key, 0o644)

            return {
                "certificateFile": f"{container_cert_dir}/{name}.crt",
                "keyFile": f"{container_cert_dir}/{name}.key"
            }

        if cert_file and key_file:
            cert_file_s = str(cert_file)
            key_file_s = str(key_file)

            if os.path.exists(cert_file_s) and os.path.exists(key_file_s):
                tls_settings["certificates"] = [
                    install_cert_pair(cert_file_s, key_file_s, safe_name)
                ]
            else:
                tls_settings["certificates"] = [
                    {
                        "certificateFile": cert_file_s,
                        "keyFile": key_file_s
                    }
                ]

        elif cert_content and key_content and "BEGIN" in str(cert_content) and "BEGIN" in str(key_content):
            dst_cert = os.path.join(host_cert_dir, f"{safe_name}.crt")
            dst_key = os.path.join(host_cert_dir, f"{safe_name}.key")

            with open(dst_cert, "w") as f:
                f.write(str(cert_content).strip() + "\n")

            with open(dst_key, "w") as f:
                f.write(str(key_content).strip() + "\n")

            os.chmod(dst_cert, 0o644)
            os.chmod(dst_key, 0o644)

            tls_settings["certificates"] = [
                {
                    "certificateFile": f"{container_cert_dir}/{safe_name}.crt",
                    "keyFile": f"{container_cert_dir}/{safe_name}.key"
                }
            ]

        else:
            candidates = []

            if node_id:
                candidates += glob.glob(f"/etc/xboard-node/instances/*/node-{node_id}/certs/cert.pem")

            candidates += glob.glob("/etc/xboard-node/instances/*/node-*/certs/cert.pem")

            chosen_cert = None
            chosen_key = None

            for c in candidates:
                k = os.path.join(os.path.dirname(c), "key.pem")
                if os.path.exists(c) and os.path.exists(k):
                    chosen_cert = c
                    chosen_key = k
                    break

            if chosen_cert and chosen_key:
                tls_settings["certificates"] = [
                    install_cert_pair(chosen_cert, chosen_key, safe_name)
                ]

        stream["tlsSettings"] = tls_settings

    else:
        stream["security"] = "none"

    return stream



def ensure_list(v):
    v = parse_json_maybe(v)

    if v is None or v == "":
        return []

    if isinstance(v, list):
        return v

    if isinstance(v, dict):
        return [v]

    return []


def extract_custom_outbounds(server):
    """
    Read per-node custom outbounds from XBoard.
    Supported field names:
    - custom_outbounds
    - customOutbounds
    - outbounds
    """
    if not isinstance(server, dict):
        return []

    for key in ["custom_outbounds", "customOutbounds", "outbounds"]:
        val = server.get(key)
        items = ensure_list(val)
        if not items:
            continue

        valid = []
        for item in items:
            if isinstance(item, dict) and item.get("tag") and item.get("protocol"):
                valid.append(item)

        if valid:
            return valid

    return []


def extract_custom_routes(server, inbound_tag=None):
    """
    Read per-node custom routes from XBoard.
    Supported field names:
    - custom_routes
    - customRoutes

    Important:
    Do not parse server.routes here. XBoard routes may include DNS/domain rules,
    not pure Xray routing rules.
    """
    if not isinstance(server, dict):
        return []

    routes = []

    for key in ["custom_routes", "customRoutes"]:
        val = server.get(key)
        items = ensure_list(val)
        if not items:
            continue

        for item in items:
            if isinstance(item, dict) and item.get("type") == "field" and item.get("outboundTag"):
                rr = dict(item)

                # Per-node isolation:
                # If XBoard route does not include inboundTag, automatically bind it to this node inbound.
                if inbound_tag and "inboundTag" not in rr:
                    rr["inboundTag"] = [inbound_tag]

                routes.append(rr)

    return routes


def dedupe_outbounds(outbounds):
    seen = set()
    result = []

    for ob in outbounds:
        if not isinstance(ob, dict):
            continue

        tag = ob.get("tag")
        if not tag:
            continue

        if tag in seen:
            continue

        seen.add(tag)
        result.append(ob)

    return result



def build_vless_clients(user_resp, flow, node_id=None):
    users = get_users_list(user_resp)
    clients = []

    for u in users:
        if not isinstance(u, dict):
            continue

        uuid = pick(u, "uuid", "id", "user_uuid")
        if not uuid:
            continue

        user_id = pick(u, "id", "email", "user_id", default=f"user-{len(clients)+1}")

        client = {
            "id": str(uuid),
            "email": stats_email(user_id, node_id)
        }

        if flow:
            client["flow"] = str(flow)

        clients.append(client)

    if not clients:
        raise RuntimeError("没有解析到 VLESS 用户。user 接口里没有 users[].uuid。")

    return clients


def build_vless_inbound(config_resp, user_resp, node_id=None):
    server = unwrap_config(config_resp)
    if not isinstance(server, dict):
        raise RuntimeError("config 接口格式不正确，不是 JSON 对象。")

    protocol = str(pick(server, "protocol", default="vless")).lower()
    if protocol != "vless":
        raise RuntimeError(f"当前节点不是 vless，面板返回 protocol={protocol}")

    port = get_server_port(server, 443)
    listen = get_listen_ip(server)
    flow = pick(server, "flow", default=None)

    clients = build_vless_clients(user_resp, flow, node_id=node_id)

    return {
        "tag": f"vless-{port}",
        "listen": listen,
        "port": port,
        "protocol": "vless",
        "settings": {
            "clients": clients,
            "decryption": str(pick(server, "decryption", default="none") or "none")
        },
        "streamSettings": build_stream_settings(server),
        "sniffing": {
            "enabled": True,
            "destOverride": [
                "http",
                "tls",
                "quic"
            ]
        }
    }


def build_vmess_clients(user_resp, node_id=None):
    users = get_users_list(user_resp)
    clients = []

    for u in users:
        if not isinstance(u, dict):
            continue

        uuid = pick(u, "uuid", "id", "user_uuid")
        if not uuid:
            continue

        user_id = pick(u, "id", "email", "user_id", default=f"user-{len(clients)+1}")

        clients.append({
            "id": str(uuid),
            "email": stats_email(user_id, node_id),
            "alterId": 0,
            "security": "auto"
        })

    if not clients:
        raise RuntimeError("没有解析到 VMess 用户。user 接口里没有 users[].uuid。")

    return clients


def build_vmess_inbound(config_resp, user_resp, node_id=None):
    server = unwrap_config(config_resp)
    if not isinstance(server, dict):
        raise RuntimeError("config 接口格式不正确，不是 JSON 对象。")

    protocol = str(pick(server, "protocol", default="vmess")).lower()
    if protocol not in ["vmess", "v2ray"]:
        raise RuntimeError(f"当前节点不是 vmess，面板返回 protocol={protocol}")

    port = get_server_port(server, 443)
    listen = get_listen_ip(server)

    return {
        "tag": f"vmess-{port}",
        "listen": listen,
        "port": port,
        "protocol": "vmess",
        "settings": {
            "clients": build_vmess_clients(user_resp, node_id=node_id)
        },
        "streamSettings": build_stream_settings(server),
        "sniffing": {
            "enabled": True,
            "destOverride": [
                "http",
                "tls",
                "quic"
            ]
        }
    }


def build_trojan_clients(user_resp, node_id=None):
    users = get_users_list(user_resp)
    clients = []

    for u in users:
        if not isinstance(u, dict):
            continue

        password = pick(u, "password", "passwd", "uuid", "id")
        if not password:
            continue

        user_id = pick(u, "id", "email", "user_id", default=f"user-{len(clients)+1}")

        clients.append({
            "password": str(password),
            "email": stats_email(user_id, node_id)
        })

    if not clients:
        raise RuntimeError("没有解析到 Trojan 用户。")

    return clients


def build_trojan_inbound(config_resp, user_resp, node_id=None):
    server = unwrap_config(config_resp)
    if not isinstance(server, dict):
        raise RuntimeError("config 接口格式不正确，不是 JSON 对象。")

    protocol = str(pick(server, "protocol", default="trojan")).lower()
    if protocol != "trojan":
        raise RuntimeError(f"当前节点不是 trojan，面板返回 protocol={protocol}")

    port = get_server_port(server, 443)
    listen = get_listen_ip(server)

    return {
        "tag": f"trojan-{port}",
        "listen": listen,
        "port": port,
        "protocol": "trojan",
        "settings": {
            "clients": build_trojan_clients(user_resp, node_id=node_id)
        },
        "streamSettings": build_stream_settings(server),
        "sniffing": {
            "enabled": True,
            "destOverride": [
                "http",
                "tls",
                "quic"
            ]
        }
    }


def build_ss_clients(user_resp, method, node_id=None):
    users = get_users_list(user_resp)
    clients = []

    for u in users:
        if not isinstance(u, dict):
            continue

        user_id = pick(u, "id", "email", "user_id", default=f"user-{len(clients)+1}")

        # XBoard SS 用户常见返回是 id + uuid。
        # 普通 Shadowsocks 可用 uuid/password；
        # Shadowsocks 2022 多用户时，用户 password 通常也使用 uuid。
        password = pick(u, "password", "passwd", "uuid", "id")
        if not password:
            continue

        client = {
            "email": stats_email(user_id, node_id),
            "password": str(password)
        }

        if not str(method).startswith("2022-"):
            client["method"] = str(method)

        clients.append(client)

    if not clients:
        raise RuntimeError("没有解析到 Shadowsocks 用户。需要确认 user 接口字段。")

    return clients


def build_shadowsocks_inbound(config_resp, user_resp, node_id=None):
    server = unwrap_config(config_resp)
    if not isinstance(server, dict):
        raise RuntimeError("config 接口格式不正确，不是 JSON 对象。")

    protocol = str(pick(server, "protocol", default="shadowsocks")).lower()
    if protocol not in ["shadowsocks", "ss"]:
        raise RuntimeError(f"当前节点不是 shadowsocks，面板返回 protocol={protocol}")

    listen = get_listen_ip(server)
    port = get_server_port(server, 8388)

    method = pick(
        server,
        "cipher",
        "method",
        "server_method",
        default="aes-128-gcm"
    )

    server_key = pick(server, "server_key", "password", "passwd")
    clients = build_ss_clients(user_resp, method, node_id=node_id)

    settings = {
        "method": str(method),
        "clients": clients,
        "network": "tcp,udp"
    }

    # Shadowsocks 2022 需要服务端主密码，也就是 XBoard 下发的 server_key。
    if str(method).startswith("2022-"):
        if not server_key:
            raise RuntimeError("Shadowsocks 2022 缺少 server_key，无法生成配置。")
        settings["password"] = str(server_key)

    return {
        "tag": f"shadowsocks-{port}",
        "listen": listen,
        "port": port,
        "protocol": "shadowsocks",
        "settings": settings,
        "sniffing": {
            "enabled": True,
            "destOverride": [
                "http",
                "tls",
                "quic"
            ]
        }
    }


def build_xray_config(inbounds, custom_outbounds=None, custom_routes=None):
    custom_outbounds = custom_outbounds or []
    custom_routes = custom_routes or []

    outbounds = dedupe_outbounds(
        custom_outbounds + [
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {}
            },
            {
                "tag": "block",
                "protocol": "blackhole",
                "settings": {}
            }
        ]
    )

    routing_rules = [
        {
            "type": "field",
            "inboundTag": [
                "api"
            ],
            "outboundTag": "api"
        },
        {
            "type": "field",
            "ip": [
                "geoip:private"
            ],
            "outboundTag": "block"
        },
        {
            "type": "field",
            "protocol": [
                "bittorrent"
            ],
            "outboundTag": "block"
        }
    ] + custom_routes

    return {
        "log": {
            "loglevel": "warning"
        },
        "api": {
            "tag": "api",
            "services": [
                "StatsService"
            ]
        },
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True
            }
        },
        "dns": {
            "servers": [
                "1.1.1.1",
                "8.8.8.8"
            ],
            "queryStrategy": "UseIPv4"
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 10085,
                "protocol": "dokodemo-door",
                "settings": {
                    "address": "127.0.0.1"
                }
            }
        ] + inbounds,
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "AsIs",
            "rules": routing_rules
        }
    }

def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def fetch_node(panel, token, node_id, node_type):
    node_type = normalize_node_type(node_type)

    config_url = f"{panel}/api/v1/server/UniProxy/config?node_id={node_id}&node_type={node_type}&token={token}"
    user_url = f"{panel}/api/v1/server/UniProxy/user?node_id={node_id}&node_type={node_type}&token={token}"

    config_resp = get_json(config_url)
    user_resp = get_json(user_url)

    server = unwrap_config(config_resp)

    if node_type == "vless":
        inbound = build_vless_inbound(config_resp, user_resp, node_id=node_id)
    elif node_type == "vmess":
        inbound = build_vmess_inbound(config_resp, user_resp, node_id=node_id)
    elif node_type == "trojan":
        inbound = build_trojan_inbound(config_resp, user_resp, node_id=node_id)
    elif node_type == "shadowsocks":
        inbound = build_shadowsocks_inbound(config_resp, user_resp, node_id=node_id)
    else:
        raise RuntimeError(
            f"暂不支持协议: {node_type}。"
            f"官方 xray-core 不支持 AnyTLS/Hysteria2/TUIC，请用 sing-box。"
        )

    custom_outbounds = extract_custom_outbounds(server)
    custom_routes = extract_custom_routes(
        server,
        inbound_tag=inbound.get("tag")
    )

    if custom_outbounds:
        print(f"[sync] 节点 {node_id}:{node_type} 下发 custom outbounds: {len(custom_outbounds)}", flush=True)

    if custom_routes:
        print(f"[sync] 节点 {node_id}:{node_type} 下发 custom routes: {len(custom_routes)}", flush=True)

    print(f"[sync] 已生成节点 {node_id}:{node_type} -> {inbound['tag']}:{inbound['port']}", flush=True)

    return {
        "inbound": inbound,
        "custom_outbounds": custom_outbounds,
        "custom_routes": custom_routes
    }

def sync_once():
    env = load_env()

    panel = env["PANEL_URL"].rstrip("/")
    token = env["PANEL_TOKEN"]
    nodes = get_nodes(env)

    config_path = Path(env.get("XRAY_CONFIG", "/opt/xray/config/config.json"))
    container = env.get("XRAY_CONTAINER", "xray-core")

    inbounds = []
    custom_outbounds = []
    custom_routes = []

    for node_id, node_type in nodes:
        node_data = fetch_node(panel, token, node_id, node_type)
        inbound = node_data["inbound"]

        inbounds.append(inbound)
        custom_outbounds.extend(node_data.get("custom_outbounds", []))
        custom_routes.extend(node_data.get("custom_routes", []))

    # 防止端口重复
    seen_ports = {}
    for inbound in inbounds:
        p = inbound["port"]
        if p in seen_ports:
            raise RuntimeError(f"端口冲突：{seen_ports[p]} 和 {inbound['tag']} 都使用端口 {p}")
        seen_ports[p] = inbound["tag"]

    xray_config = build_xray_config(
        inbounds,
        custom_outbounds=custom_outbounds,
        custom_routes=custom_routes
    )

    new_text = json.dumps(xray_config, ensure_ascii=False, indent=2)
    old_text = config_path.read_text(errors="ignore") if config_path.exists() else ""

    if sha256_text(new_text) != sha256_text(old_text):
        tmp = config_path.with_suffix(".json.tmp")
        tmp.write_text(new_text)
        tmp.replace(config_path)

        print("[sync] 配置有变化，已写入 /opt/xray/config/config.json，正在重启 xray-core", flush=True)
        subprocess.run(["docker", "restart", container], check=True)
    else:
        print("[sync] 配置无变化，不重启", flush=True)


def loop():
    env = load_env()
    interval = int(env.get("SYNC_INTERVAL", "60"))

    while True:
        try:
            sync_once()
        except Exception as e:
            print(f"[sync] ERROR: {e}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        sync_once()
    else:
        loop()
