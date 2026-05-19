#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from pathlib import Path

import requests

ENV_PATH = "/opt/xray-sync/.env"
STATE_PATH = "/opt/xray-sync/report_state.json"
DEFAULT_ACCESS_LOG = "/opt/xray/logs/access.log"


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


def split_user_key(user_key):
    if ":" in user_key:
        node_id, user_id_raw = user_key.split(":", 1)
    else:
        node_id = None
        user_id_raw = user_key

    try:
        uid = int(user_id_raw)
    except Exception:
        return None, None

    return node_id, uid


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

        node_id, uid = split_user_key(user_key)
        if uid is None:
            continue

        if node_id:
            target = scoped.setdefault(node_id, {})
        else:
            target = legacy

        add_traffic(target, uid, direction, value)

    scoped = {
        node_id: strip_zero_traffic(traffic)
        for node_id, traffic in scoped.items()
    }
    scoped = {node_id: traffic for node_id, traffic in scoped.items() if traffic}

    return scoped, strip_zero_traffic(legacy)


def extract_user_key_from_access_line(line):
    patterns = [
        r"email:\s*([^\s\]]+)",
        r"\[([0-9]+(?::[0-9]+)?)\]",
    ]
    for pattern in patterns:
        m = re.search(pattern, line)
        if m:
            return m.group(1)
    return None


def extract_ip_from_access_line(line):
    ipv4 = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?![\d.])", line)
    if ipv4:
        return ipv4.group(1)

    ipv6 = re.search(r"\[([0-9a-fA-F:]+)\](?::\d+)?", line)
    if ipv6:
        return ipv6.group(1)

    return None


def parse_alive_from_access_lines(lines):
    scoped = {}
    legacy = {}

    for line in lines:
        user_key = extract_user_key_from_access_line(line)
        ip = extract_ip_from_access_line(line)
        if not user_key or not ip:
            continue

        node_id, uid = split_user_key(user_key)
        if uid is None:
            continue

        if node_id:
            target = scoped.setdefault(node_id, {})
        else:
            target = legacy

        target.setdefault(uid, set()).add(ip)

    def freeze(data):
        return {
            uid: sorted(ips)
            for uid, ips in data.items()
            if ips
        }

    scoped = {
        node_id: freeze(alive)
        for node_id, alive in scoped.items()
    }
    scoped = {node_id: alive for node_id, alive in scoped.items() if alive}

    return scoped, freeze(legacy)


def load_state(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_state(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def read_access_log_since(env):
    access_log = env.get("XRAY_ACCESS_LOG", DEFAULT_ACCESS_LOG)
    state_path = env.get("REPORT_STATE", STATE_PATH)
    log_path = Path(access_log)

    if not log_path.exists():
        return {}, {}

    state = load_state(state_path)
    log_state = state.get("access_log", {})

    size = log_path.stat().st_size
    offset = int(log_state.get("offset", 0) or 0)
    if offset < 0 or offset > size:
        offset = 0

    with log_path.open("r", errors="ignore") as f:
        f.seek(offset)
        lines = f.readlines()
        new_offset = f.tell()

    state["access_log"] = {
        "path": str(log_path),
        "offset": new_offset,
        "size": size,
    }
    save_state(state_path, state)

    return parse_alive_from_access_lines(lines)


def merge_legacy_map(scoped, legacy, nodes, label):
    if not legacy:
        return

    if len(nodes) != 1:
        print(f"[report] skipped legacy unscoped {label} because multiple NODES are configured")
        return

    node_id = nodes[0][0]
    target = scoped.setdefault(node_id, {})
    for uid, values in legacy.items():
        if uid not in target:
            target[uid] = values
        elif isinstance(values, list) and values and isinstance(values[0], int):
            target[uid][0] += values[0]
            target[uid][1] += values[1]
        else:
            target[uid] = sorted(set(target[uid]) | set(values))


def include_alive_users_in_traffic(scoped_traffic, scoped_alive):
    for node_id, alive in scoped_alive.items():
        traffic = scoped_traffic.setdefault(node_id, {})
        for uid in alive:
            traffic.setdefault(uid, [0, 0])


def post_traffic(env, node_id, node_type, traffic):
    if not traffic:
        print(f"[report] node {node_id}:{node_type} has no traffic to push")
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

    print(f"[report] pushed traffic for {len(traffic)} users on node {node_id}:{node_type}: {resp}")


def post_alive(env, node_id, node_type, alive):
    if not alive:
        print(f"[report] node {node_id}:{node_type} has no alive users to push")
        return

    panel = env["PANEL_URL"].rstrip("/")
    token = env["PANEL_TOKEN"]

    url = f"{panel}/api/v1/server/UniProxy/alive?node_id={node_id}&node_type={node_type}&token={token}"

    r = requests.post(url, json=alive, timeout=25)

    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]

    if r.status_code >= 400:
        raise RuntimeError(f"alive failed HTTP {r.status_code}: {resp}")

    print(f"[report] pushed alive devices for {len(alive)} users on node {node_id}:{node_type}: {resp}")


def main():
    env = load_env()
    nodes = get_nodes(env)
    stats = run_statsquery()
    scoped_traffic, legacy_traffic = parse_traffic_by_node(stats)
    scoped_alive, legacy_alive = read_access_log_since(env)

    merge_legacy_map(scoped_traffic, legacy_traffic, nodes, "traffic")
    merge_legacy_map(scoped_alive, legacy_alive, nodes, "alive users")
    include_alive_users_in_traffic(scoped_traffic, scoped_alive)

    for node_id, node_type in nodes:
        post_traffic(env, node_id, node_type, scoped_traffic.get(node_id, {}))
        post_alive(env, node_id, node_type, scoped_alive.get(node_id, {}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[report] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
