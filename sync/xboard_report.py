#!/usr/bin/env python3
import json
import re
import subprocess
import sys
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


def normalize_node_type(t):
    t = str(t).strip().lower()
    aliases = {
        "ss": "shadowsocks",
        "shadow": "shadowsocks",
        "shadowsocks2022": "shadowsocks",
        "v2ray": "vmess",
    }
    return aliases.get(t, t)


def get_nodes(env):
    nodes = []

    if env.get("NODES"):
        for item in env["NODES"].split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise RuntimeError(f"NODES format error: {item}; expected node_id:protocol")
            node_id, node_type = item.split(":", 1)
            node_id = node_id.strip()
            node_type = normalize_node_type(node_type.strip())
            if not node_id or not node_type:
                raise RuntimeError(f"NODES format error: {item}; node_id and protocol are required")
            nodes.append((node_id, node_type))
    else:
        nodes.append((env["NODE_ID"], normalize_node_type(env.get("NODE_TYPE", "vless"))))

    if not nodes:
        raise RuntimeError("NODES cannot be empty; expected node_id:protocol")

    return nodes


def run_statsquery():
    cmd = [
        "docker",
        "exec",
        "xray-core",
        "xray",
        "api",
        "statsquery",
        "--server=127.0.0.1:10085",
        "-pattern",
        "user>>>",
        "-reset"
    ]

    p = subprocess.run(cmd, text=True, capture_output=True)

    if p.returncode != 0:
        raise RuntimeError(f"statsquery failed: {p.stderr.strip() or p.stdout.strip()}")

    out = p.stdout.strip()
    if not out:
        return {}

    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError(f"statsquery 返回不是 JSON: {out[:500]}")


def parse_traffic(stats_json):
    """
    Xray 返回：
    {
      "stat": [
        {"name": "user>>>1485>>>traffic>>>uplink", "value": "123"},
        {"name": "user>>>1485>>>traffic>>>downlink", "value": "456"}
      ]
    }

    XBoard 需要：
    {
      "1485": [123, 456]
    }
    """
    stat_list = stats_json.get("stat") or stats_json.get("stats") or []

    traffic = {}

    for item in stat_list:
        name = item.get("name", "")
        value = int(item.get("value", 0) or 0)

        m = re.match(r"^user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
        if not m:
            continue

        user_id_raw = m.group(1)
        direction = m.group(2)

        # 我们在 xray_sync.py 里把 email 设置成 XBoard 用户 id，例如 "1485"
        try:
            uid = int(user_id_raw)
        except Exception:
            continue

        if uid not in traffic:
            traffic[uid] = [0, 0]

        if direction == "uplink":
            traffic[uid][0] += value
        elif direction == "downlink":
            traffic[uid][1] += value

    # 删除 0 流量
    traffic = {
        uid: arr for uid, arr in traffic.items()
        if arr[0] > 0 or arr[1] > 0
    }

    return traffic


def add_traffic(traffic, uid, direction, value):
    if uid not in traffic:
        traffic[uid] = [0, 0]

    if direction == "uplink":
        traffic[uid][0] += value
    elif direction == "downlink":
        traffic[uid][1] += value


def strip_zero_traffic(traffic):
    return {
        uid: arr for uid, arr in traffic.items()
        if arr[0] > 0 or arr[1] > 0
    }


def parse_traffic_by_node(stats_json):
    stat_list = stats_json.get("stat") or stats_json.get("stats") or []

    scoped = {}
    legacy = {}

    for item in stat_list:
        name = item.get("name", "")
        value = int(item.get("value", 0) or 0)

        m = re.match(r"^user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
        if not m:
            continue

        user_key = m.group(1)
        direction = m.group(2)

        if ":" in user_key:
            node_id, user_id_raw = user_key.split(":", 1)
            target = scoped.setdefault(node_id, {})
        else:
            user_id_raw = user_key
            target = legacy

        try:
            uid = int(user_id_raw)
        except Exception:
            continue

        add_traffic(target, uid, direction, value)

    scoped = {
        node_id: strip_zero_traffic(traffic)
        for node_id, traffic in scoped.items()
    }
    scoped = {node_id: traffic for node_id, traffic in scoped.items() if traffic}

    return scoped, strip_zero_traffic(legacy)


def post_traffic(env, node_id, node_type, traffic):
    if not traffic:
        print("[report] 没有新增用户流量，不上报")
        return

    panel = env["PANEL_URL"].rstrip("/")
    token = env["PANEL_TOKEN"]

    url = f"{panel}/api/v1/server/UniProxy/push?node_id={node_id}&node_type={node_type}&token={token}"

    r = requests.post(url, json=traffic, timeout=25)

    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]

    if r.status_code >= 400:
        raise RuntimeError(f"push failed HTTP {r.status_code}: {resp}")

    print(f"[report] 已上报 {len(traffic)} 个用户流量: {resp}")


def main():
    env = load_env()
    nodes = get_nodes(env)
    stats = run_statsquery()
    scoped_traffic, legacy_traffic = parse_traffic_by_node(stats)

    if legacy_traffic:
        if len(nodes) == 1:
            node_id = nodes[0][0]
            traffic = scoped_traffic.setdefault(node_id, {})
            for uid, values in legacy_traffic.items():
                if uid not in traffic:
                    traffic[uid] = [0, 0]
                traffic[uid][0] += values[0]
                traffic[uid][1] += values[1]
        else:
            print("[report] skipped legacy unscoped traffic because multiple NODES are configured")

    for node_id, node_type in nodes:
        post_traffic(env, node_id, node_type, scoped_traffic.get(node_id, {}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[report] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
