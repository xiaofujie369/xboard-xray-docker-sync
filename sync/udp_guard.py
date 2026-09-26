#!/usr/bin/env python3
"""Per-user UDP abuse guard for the XBoard/Xray sync stack.

Xray's access log records accepted UDP sessions with the client email.  This
project scopes that email as ``node_id:user_id``, so a short sliding window is
useful as a conservative approximation of active UDP mappings.  The guard is
observation-only by default.  In block mode it writes temporary blocks to a
state file consumed by xboard_sync.py.
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path


ENV_PATH = "/opt/xray-sync/.env"
DEFAULT_STATE_PATH = "/opt/xray-sync/udp_guard_state.json"
DEFAULT_ACCESS_LOG = "/opt/xray/logs/access.log"
USER_KEY_RE = re.compile(r"^[0-9]+(?::[0-9]+)?$")
UDP_ACCEPT_RE = re.compile(r"\baccepted\s+udp:", re.IGNORECASE)
USER_PATTERNS = (
    re.compile(r"email:\s*([^\s\]]+)", re.IGNORECASE),
    re.compile(r"\[([0-9]+(?::[0-9]+)?)\]"),
)


def load_env(path=ENV_PATH):
    env = {}
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"配置文件不存在: {path}")

    for line in p.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def parse_bool(value, default=False):
    if value in [None, ""]:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_number(env, key, default, minimum, number_type=int):
    raw = env.get(key, default)
    try:
        value = number_type(raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"{key} 必须是数字，当前值: {raw}")
    if value < minimum:
        raise RuntimeError(f"{key} 不能小于 {minimum}，当前值: {value}")
    return value


def build_config(env):
    mode = str(env.get("UDP_GUARD_MODE", "observe")).strip().lower()
    if not parse_bool(env.get("UDP_GUARD_ENABLED", "true"), default=True):
        mode = "disabled"
    if mode not in {"disabled", "observe", "block"}:
        raise RuntimeError("UDP_GUARD_MODE 必须是 disabled、observe 或 block")

    soft_limit = parse_number(env, "UDP_GUARD_SOFT_LIMIT", 256, 1)
    hard_limit = parse_number(env, "UDP_GUARD_HARD_LIMIT", 512, 2)
    if hard_limit <= soft_limit:
        raise RuntimeError("UDP_GUARD_HARD_LIMIT 必须大于 UDP_GUARD_SOFT_LIMIT")

    return {
        "mode": mode,
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "window_seconds": parse_number(env, "UDP_GUARD_WINDOW_SECONDS", 120, 10),
        "block_seconds": parse_number(env, "UDP_GUARD_BLOCK_SECONDS", 600, 60),
        "poll_seconds": parse_number(env, "UDP_GUARD_POLL_SECONDS", 1.0, 0.2, float),
        "alert_cooldown": parse_number(env, "UDP_GUARD_ALERT_COOLDOWN", 60, 1),
        "sync_min_interval": parse_number(env, "UDP_GUARD_SYNC_MIN_INTERVAL", 30, 5),
        "state_path": env.get("UDP_GUARD_STATE", DEFAULT_STATE_PATH),
        "lock_path": env.get("UDP_GUARD_LOCK", "/run/lock/xboard-udp-guard.lock"),
        "access_log": env.get("UDP_GUARD_ACCESS_LOG", DEFAULT_ACCESS_LOG),
        "read_existing": parse_bool(env.get("UDP_GUARD_READ_EXISTING", "false")),
        "sync_script": env.get(
            "UDP_GUARD_SYNC_SCRIPT",
            str(Path(__file__).resolve().with_name("xboard_sync.py")),
        ),
    }


def empty_state():
    return {
        "version": 1,
        "mode": "observe",
        "log": {},
        "events": {},
        "blocked_users": {},
        "alerts": {},
        "updated_at": 0,
    }


def load_state(path):
    p = Path(path)
    if not p.exists():
        return empty_state()
    try:
        value = json.loads(p.read_text(errors="ignore"))
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(value, dict):
        return empty_state()

    state = empty_state()
    state.update(value)
    for key in ("log", "events", "blocked_users", "alerts"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def save_state(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        os.replace(str(temp), str(p))
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def acquire_state_lock(path):
    try:
        import fcntl
    except ImportError:
        return None

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def valid_user_key(value):
    return bool(USER_KEY_RE.fullmatch(str(value or "")))


def extract_udp_user(line):
    if not UDP_ACCEPT_RE.search(line):
        return None
    for pattern in USER_PATTERNS:
        # The real Xray identity is at the end of the access line.  Taking the
        # last match prevents a crafted destination such as ``email:3047``
        # from being mistaken for the authenticated user.
        matches = list(pattern.finditer(line))
        for match in reversed(matches):
            if valid_user_key(match.group(1)):
                return match.group(1)
    return None


def risk_score(count, soft_limit=256, hard_limit=512):
    """Return a stable 0-100 risk score for an event count."""
    count = max(0, int(count))
    normal_limit = max(1, soft_limit // 2)

    if count <= normal_limit:
        return round(25 * count / normal_limit)
    if count <= soft_limit:
        return round(25 + 25 * (count - normal_limit) / (soft_limit - normal_limit))
    if count <= hard_limit:
        return round(50 + 25 * (count - soft_limit) / (hard_limit - soft_limit))
    if count <= hard_limit * 2:
        return round(75 + 15 * (count - hard_limit) / hard_limit)
    return min(100, round(90 + 10 * (count - hard_limit * 2) / (hard_limit * 2)))


def _stat_identity(stat_result):
    return {
        "device": int(getattr(stat_result, "st_dev", 0)),
        "inode": int(getattr(stat_result, "st_ino", 0)),
    }


def read_new_log_lines(state, config):
    """Read complete newly appended log lines and safely track byte offsets."""
    path = Path(config["access_log"])
    if not path.exists():
        return [], False

    stat_result = path.stat()
    identity = _stat_identity(stat_result)
    log_state = state.get("log") or {}
    is_first_open = not log_state
    rotated = (
        log_state.get("path") != str(path)
        or int(log_state.get("device", -1)) != identity["device"]
        or int(log_state.get("inode", -1)) != identity["inode"]
    )

    if is_first_open:
        offset = 0 if config["read_existing"] else stat_result.st_size
    elif rotated or int(log_state.get("offset", 0)) > stat_result.st_size:
        offset = 0
    else:
        offset = max(0, int(log_state.get("offset", 0)))

    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read()

    newline = raw.rfind(b"\n")
    if newline < 0:
        consumed = b""
    else:
        consumed = raw[: newline + 1]

    new_offset = offset + len(consumed)
    new_log_state = {
        "path": str(path),
        "offset": new_offset,
        "size": stat_result.st_size,
        **identity,
    }
    changed = new_log_state != log_state
    state["log"] = new_log_state

    if not consumed:
        return [], changed
    return consumed.decode("utf-8", errors="ignore").splitlines(), changed


def _event_deques(state, cutoff, max_events):
    events = {}
    raw_events = state.get("events") or {}
    for user, timestamps in raw_events.items():
        if not valid_user_key(user) or not isinstance(timestamps, list):
            continue
        cleaned = []
        for timestamp in timestamps[-max_events:]:
            try:
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                continue
            if timestamp >= cutoff:
                cleaned.append(timestamp)
        if cleaned:
            events[user] = deque(cleaned, maxlen=max_events)
    return events


def process_udp_users(state, users, config, now=None):
    """Update the sliding window and return (routing_changed, state_changed)."""
    now = float(time.time() if now is None else now)
    cutoff = now - config["window_seconds"]
    max_events = max(2049, config["hard_limit"] * 4 + 1)
    events = _event_deques(state, cutoff, max_events)
    before_events = copy.deepcopy(state.get("events") or {})
    before_mode = state.get("mode")
    before_blocked = copy.deepcopy(state.get("blocked_users") or {})
    before_alerts = copy.deepcopy(state.get("alerts") or {})
    blocked = state.get("blocked_users") or {}
    alerts = state.get("alerts") or {}
    routing_changed = False

    if config["mode"] != "block" and blocked:
        blocked = {}
        routing_changed = True

    grouped = defaultdict(int)
    for user in users:
        if valid_user_key(user):
            grouped[user] += 1

    for user, amount in grouped.items():
        queue = events.setdefault(user, deque(maxlen=max_events))
        for _ in range(amount):
            queue.append(now)

    # Expire temporary blocks only after old events have fallen out of the
    # window.  Continued abuse renews the block without restarting Xray again.
    for user in list(blocked):
        entry = blocked.get(user)
        if not isinstance(entry, dict) or not valid_user_key(user):
            del blocked[user]
            routing_changed = True
            continue
        try:
            expires_at = float(entry.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0
        count = len(events.get(user, ()))
        if expires_at <= now:
            if config["mode"] == "block" and count >= config["hard_limit"]:
                entry["expires_at"] = int(now + config["block_seconds"])
                entry["event_count"] = count
                entry["risk"] = risk_score(count, config["soft_limit"], config["hard_limit"])
            else:
                del blocked[user]
                routing_changed = True
                print(f"[udp-guard] UNBLOCK user={user} reason=timeout", flush=True)

    for user in grouped:
        count = len(events[user])
        score = risk_score(count, config["soft_limit"], config["hard_limit"])
        alert = alerts.setdefault(user, {})

        if count >= config["soft_limit"]:
            level = "hard" if count >= config["hard_limit"] else "soft"
            last_alert = float(alert.get(level, 0) or 0)
            if now - last_alert >= config["alert_cooldown"]:
                print(
                    f"[udp-guard] ALERT user={user} udp_events={count} "
                    f"window={config['window_seconds']}s risk={score}/100 "
                    f"level={level} mode={config['mode']}",
                    flush=True,
                )
                alert[level] = int(now)

        if config["mode"] == "block" and count >= config["hard_limit"]:
            entry = blocked.get(user)
            if not isinstance(entry, dict):
                blocked[user] = {
                    "blocked_at": int(now),
                    "expires_at": int(now + config["block_seconds"]),
                    "event_count": count,
                    "risk": score,
                    "reason": "udp_window_hard_limit",
                }
                routing_changed = True
                print(
                    f"[udp-guard] BLOCK user={user} udp_events={count} "
                    f"risk={score}/100 duration={config['block_seconds']}s",
                    flush=True,
                )
            elif float(entry.get("expires_at", 0) or 0) - now < config["block_seconds"] / 2:
                entry["expires_at"] = int(now + config["block_seconds"])
                entry["event_count"] = count
                entry["risk"] = score

    serialized_events = {
        user: [int(timestamp) for timestamp in queue]
        for user, queue in events.items()
        if queue
    }
    # Remove old alert cooldown records when a user no longer has events.
    alerts = {user: value for user, value in alerts.items() if user in serialized_events}

    state["mode"] = config["mode"]
    state["events"] = serialized_events
    state["blocked_users"] = blocked
    state["alerts"] = alerts
    state["updated_at"] = int(now)

    state_changed = (
        before_mode != config["mode"]
        or before_events != serialized_events
        or before_blocked != blocked
        or before_alerts != alerts
        or bool(grouped)
        or routing_changed
    )
    return routing_changed, state_changed


def read_conntrack_pressure():
    base = Path("/proc/sys/net/netfilter")
    try:
        count = int((base / "nf_conntrack_count").read_text().strip())
        maximum = int((base / "nf_conntrack_max").read_text().strip())
    except (OSError, ValueError):
        return None
    percent = round((100 * count / maximum), 1) if maximum else 0.0
    return {"count": count, "max": maximum, "percent": percent}


def trigger_sync(config):
    script = Path(config["sync_script"])
    if not script.is_file():
        print(f"[udp-guard] ERROR sync script not found: {script}", file=sys.stderr, flush=True)
        return False
    try:
        result = subprocess.run(
            [sys.executable, str(script), "once"],
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[udp-guard] ERROR failed to apply routing update: {exc}", file=sys.stderr, flush=True)
        return False
    if result.returncode != 0:
        print(
            f"[udp-guard] ERROR xboard_sync exited with {result.returncode}; will retry",
            file=sys.stderr,
            flush=True,
        )
        return False
    return True


def has_active_blocks(state, now=None):
    now = time.time() if now is None else float(now)
    for user, item in (state.get("blocked_users") or {}).items():
        if not valid_user_key(user) or not isinstance(item, dict):
            continue
        try:
            if float(item.get("expires_at", 0) or 0) > now:
                return True
        except (TypeError, ValueError):
            continue
    return False


def guard_step(state, config, now=None):
    lines, log_changed = read_new_log_lines(state, config)
    users = []
    for line in lines:
        user = extract_udp_user(line)
        if user:
            users.append(user)
    routing_changed, state_changed = process_udp_users(state, users, config, now=now)
    return routing_changed, state_changed or log_changed, len(lines), len(users)


def run_guard(config, once=False):
    state = load_state(config["state_path"])
    now = time.time()
    pending_sync = config["mode"] == "block" and has_active_blocks(state, now=now)
    last_sync = 0.0

    print(
        f"[udp-guard] started mode={config['mode']} soft={config['soft_limit']} "
        f"hard={config['hard_limit']} window={config['window_seconds']}s "
        f"block={config['block_seconds']}s",
        flush=True,
    )

    while True:
        now = time.time()
        lock = None
        try:
            lock = acquire_state_lock(config["lock_path"])
            # Reload each poll so manual block/unblock commands cannot be
            # overwritten by a daemon process holding an older state copy.
            state = load_state(config["state_path"])
            routing_changed, state_changed, _lines, _udp_events = guard_step(state, config, now=now)
            if state_changed:
                save_state(config["state_path"], state)
            pending_sync = pending_sync or routing_changed
        except Exception as exc:
            print(f"[udp-guard] ERROR: {exc}", file=sys.stderr, flush=True)
        finally:
            if lock is not None:
                lock.close()

        if pending_sync and now - last_sync >= config["sync_min_interval"]:
            if trigger_sync(config):
                pending_sync = False
            last_sync = time.time()

        if once:
            return 0 if not pending_sync else 1
        time.sleep(config["poll_seconds"])


def print_status(config, state):
    now = time.time()
    cutoff = now - config["window_seconds"]
    events = _event_deques(state, cutoff, max(2049, config["hard_limit"] * 4 + 1))
    blocked = state.get("blocked_users") or {}

    print(
        f"UDP Guard: mode={config['mode']} window={config['window_seconds']}s "
        f"soft={config['soft_limit']} hard={config['hard_limit']} "
        f"block={config['block_seconds']}s"
    )
    pressure = read_conntrack_pressure()
    if pressure:
        level = "critical" if pressure["percent"] >= 85 else "warning" if pressure["percent"] >= 70 else "normal"
        print(
            f"Conntrack: {pressure['count']}/{pressure['max']} "
            f"({pressure['percent']}%, {level})"
        )
    else:
        print("Conntrack: unavailable")

    rows = []
    for user, queue in events.items():
        count = len(queue)
        rows.append((count, user, risk_score(count, config["soft_limit"], config["hard_limit"])))
    rows.sort(reverse=True)

    print("Top users:")
    if not rows:
        print("  no UDP events in the active window")
    for count, user, score in rows[:20]:
        entry = blocked.get(user) if isinstance(blocked.get(user), dict) else {}
        expires = max(0, int(float(entry.get("expires_at", 0) or 0) - now))
        suffix = f" blocked_for={expires}s" if expires else ""
        print(f"  user={user} udp_events={count} risk={score}/100{suffix}")


def change_manual_block(config, user, block, seconds=None):
    if not valid_user_key(user):
        raise RuntimeError("用户标识必须是 user_id 或 node_id:user_id，且只能包含数字和冒号")
    lock = acquire_state_lock(config["lock_path"])
    try:
        state = load_state(config["state_path"])
        blocked = state.get("blocked_users") or {}
        now = int(time.time())

        if block:
            duration = max(60, int(seconds or config["block_seconds"]))
            blocked[user] = {
                "blocked_at": now,
                "expires_at": now + duration,
                "event_count": len((state.get("events") or {}).get(user, [])),
                "risk": 100,
                "reason": "manual",
            }
            print(f"[udp-guard] manual block user={user} duration={duration}s")
        else:
            blocked.pop(user, None)
            (state.get("events") or {}).pop(user, None)
            print(f"[udp-guard] manual unblock user={user}")

        state["mode"] = config["mode"]
        state["blocked_users"] = blocked
        state["updated_at"] = now
        save_state(config["state_path"], state)
    finally:
        if lock is not None:
            lock.close()
    if config["mode"] != "block":
        print("[udp-guard] mode is not block; routing will remain unchanged")
        return 0
    return 0 if trigger_sync(config) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Per-user Xray UDP abuse guard")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="run the guard loop")
    subparsers.add_parser("once", help="process newly appended log entries once")
    subparsers.add_parser("status", help="show current UDP risk and blocks")

    block_parser = subparsers.add_parser("block", help="temporarily block one user's UDP")
    block_parser.add_argument("user", help="user_id or node_id:user_id")
    block_parser.add_argument("--seconds", type=int, default=None)

    unblock_parser = subparsers.add_parser("unblock", help="remove one user's UDP block")
    unblock_parser.add_argument("user", help="user_id or node_id:user_id")

    args = parser.parse_args(argv)
    env = load_env()
    config = build_config(env)
    command = args.command or "run"

    if command == "status":
        print_status(config, load_state(config["state_path"]))
        return 0
    if command == "block":
        return change_manual_block(config, args.user, True, seconds=args.seconds)
    if command == "unblock":
        return change_manual_block(config, args.user, False)
    return run_guard(config, once=command == "once")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[udp-guard] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
