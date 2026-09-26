#!/usr/bin/env bash
set -uo pipefail

REPO="xiaofujie369/xboard-xray-docker-sync"
BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

SYNC_DIR="/opt/xray-sync"
XRAY_DIR="/opt/xray"
ENV_FILE="${SYNC_DIR}/.env"
XRAY_CONFIG="${XRAY_DIR}/config/config.json"
BACKUP_DIR="${XRAY_DIR}/config/backups"
CONTAINER="xray-core"

need_root() {
  if [ "$(id -u)" != "0" ]; then
    echo "请使用 root 运行: sudo xbr"
    exit 1
  fi
}

ok() { echo "[OK] $*"; }
warn() { echo "[WARN] $*"; }
err() { echo "[ERROR] $*" >&2; }

pause() {
  echo
  read -rp "按 Enter 返回菜单..." _
}

get_env_value() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-
}

mask_value() {
  local value="${1:-}"
  if [ -z "$value" ]; then
    echo ""
  elif [ "${#value}" -le 8 ]; then
    echo "****"
  else
    echo "${value:0:4}****${value: -4}"
  fi
}

ensure_runtime_dirs() {
  mkdir -p "$SYNC_DIR" "$XRAY_DIR/config" "$XRAY_DIR/logs" "$BACKUP_DIR"
  touch "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log"
  chown -R 65532:65532 "$XRAY_DIR/logs" 2>/dev/null || true
  chmod 750 "$XRAY_DIR/logs" 2>/dev/null || true
  chmod 640 "$XRAY_DIR/logs/access.log" "$XRAY_DIR/logs/error.log" 2>/dev/null || true
}

run_remote_script() {
  local script="$1"
  bash <(curl -fsSL "${RAW_BASE}/${script}")
}

edit_panel_config() {
  ensure_runtime_dirs

  local old_panel old_token old_nodes old_interval old_backups old_pretest old_conn_idle
  local old_udp_mode old_udp_soft old_udp_hard old_udp_window old_udp_block
  local panel token nodes interval backups pretest
  old_panel="$(get_env_value PANEL_URL)"
  old_token="$(get_env_value PANEL_TOKEN)"
  old_nodes="$(get_env_value NODES)"
  old_interval="$(get_env_value SYNC_INTERVAL)"
  old_backups="$(get_env_value XRAY_CONFIG_BACKUPS)"
  old_pretest="$(get_env_value XRAY_PRESTART_TEST)"
  old_conn_idle="$(get_env_value XRAY_CONN_IDLE_SECONDS)"
  old_udp_mode="$(get_env_value UDP_GUARD_MODE)"
  old_udp_soft="$(get_env_value UDP_GUARD_SOFT_LIMIT)"
  old_udp_hard="$(get_env_value UDP_GUARD_HARD_LIMIT)"
  old_udp_window="$(get_env_value UDP_GUARD_WINDOW_SECONDS)"
  old_udp_block="$(get_env_value UDP_GUARD_BLOCK_SECONDS)"

  echo "当前配置:"
  echo "PANEL_URL=${old_panel:-未设置}"
  echo "PANEL_TOKEN=$(mask_value "$old_token")"
  echo "NODES=${old_nodes:-未设置}"
  echo "SYNC_INTERVAL=${old_interval:-60}"
  echo "XRAY_CONFIG_BACKUPS=${old_backups:-3}"
  echo "XRAY_PRESTART_TEST=${old_pretest:-true}"
  echo

  read -rp "请输入 XBoard 面板地址 [${old_panel:-https://bs.example.com}]: " panel
  read -rsp "请输入 XBoard TOKEN，留空则保留旧值: " token
  echo
  read -rp "请输入节点列表 [${old_nodes:-371:vless}]: " nodes
  read -rp "同步间隔秒数 [${old_interval:-60}]: " interval
  read -rp "保留 config 备份份数 [${old_backups:-3}]: " backups
  read -rp "写入前执行 Xray 配置预检测 true/false [${old_pretest:-true}]: " pretest

  panel="${panel:-$old_panel}"
  token="${token:-$old_token}"
  nodes="${nodes:-$old_nodes}"
  interval="${interval:-${old_interval:-60}}"
  backups="${backups:-${old_backups:-3}}"
  pretest="${pretest:-${old_pretest:-true}}"

  if [ -z "$panel" ] || [ -z "$token" ] || [ -z "$nodes" ]; then
    err "PANEL_URL / PANEL_TOKEN / NODES 不能为空"
    return 1
  fi

  cat > "$ENV_FILE" <<EOFENV
PANEL_URL=$panel
PANEL_TOKEN=$token

XRAY_CONFIG=/opt/xray/config/config.json
XRAY_CONTAINER=xray-core
XRAY_CONTAINER_CONFIG_DIR=/etc/xray
XRAY_LOG_DIR=/opt/xray/logs
XRAY_CONFIG_BACKUPS=$backups
XRAY_PRESTART_TEST=$pretest
XRAY_CONN_IDLE_SECONDS=${old_conn_idle:-120}
XRAY_ENABLE_PANEL_ROUTES=true
XRAY_ENABLE_PANEL_DNS_ROUTES=true
XRAY_ENABLE_PANEL_DEFAULT_DNS=false

SYNC_INTERVAL=$interval
REPORT_USE_V2_REPORT=true
REPORT_V2_FALLBACK=true
REPORT_KERNEL_STATUS=true
REPORT_ONLINE_TTL=180

UDP_GUARD_MODE=${old_udp_mode:-observe}
UDP_GUARD_SOFT_LIMIT=${old_udp_soft:-256}
UDP_GUARD_HARD_LIMIT=${old_udp_hard:-512}
UDP_GUARD_WINDOW_SECONDS=${old_udp_window:-120}
UDP_GUARD_BLOCK_SECONDS=${old_udp_block:-600}
UDP_GUARD_POLL_SECONDS=1
UDP_GUARD_ALERT_COOLDOWN=60
UDP_GUARD_SYNC_MIN_INTERVAL=30
UDP_GUARD_STATE=/opt/xray-sync/udp_guard_state.json
UDP_GUARD_ACCESS_LOG=/opt/xray/logs/access.log
UDP_GUARD_READ_EXISTING=false
NODES=$nodes
EOFENV
  chmod 600 "$ENV_FILE"
  ok "面板配置已写入 $ENV_FILE"
}

install_stack() {
  run_remote_script "install.sh"
}

update_stack() {
  run_remote_script "update.sh"
}

uninstall_stack() {
  run_remote_script "uninstall.sh"
}

start_services() {
  systemctl start docker 2>/dev/null || true
  cd "$XRAY_DIR" 2>/dev/null && docker compose up -d || docker start "$CONTAINER" 2>/dev/null || true
  systemctl start xboard-sync 2>/dev/null || true
  systemctl start xboard-report 2>/dev/null || true
  systemctl start xboard-udp-guard 2>/dev/null || true
  ok "启动命令已执行"
}

stop_services() {
  systemctl stop xboard-sync 2>/dev/null || true
  systemctl stop xboard-report 2>/dev/null || true
  systemctl stop xboard-udp-guard 2>/dev/null || true
  docker stop "$CONTAINER" 2>/dev/null || true
  ok "停止命令已执行"
}

restart_services() {
  systemctl restart xboard-sync 2>/dev/null || true
  systemctl restart xboard-report 2>/dev/null || true
  systemctl restart xboard-udp-guard 2>/dev/null || true
  docker restart "$CONTAINER" 2>/dev/null || true
  ok "重启命令已执行"
}

show_status() {
  echo "===== 服务状态 ====="
  systemctl is-active --quiet xboard-sync && ok "xboard-sync 运行中" || warn "xboard-sync 未运行"
  systemctl is-active --quiet xboard-report && ok "xboard-report 运行中" || warn "xboard-report 未运行"
  systemctl is-active --quiet xboard-udp-guard && ok "xboard-udp-guard 运行中" || warn "xboard-udp-guard 未运行"
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" && ok "xray-core 运行中" || warn "xray-core 未运行"
  echo
  docker ps -a --filter "name=${CONTAINER}" 2>/dev/null || true
  echo
  systemctl status xboard-sync --no-pager 2>/dev/null | sed -n '1,12p' || true
  echo
  if [ -f "$SYNC_DIR/udp_guard.py" ]; then
    python3 "$SYNC_DIR/udp_guard.py" status || true
  fi
}

show_logs() {
  echo "1. xboard-sync 日志"
  echo "2. xboard-report 日志"
  echo "3. UDP Guard 日志"
  echo "4. xray-core Docker 日志"
  echo "5. Xray error.log"
  read -rp "选择 [1-5]: " choice
  case "$choice" in
    1) journalctl -u xboard-sync -n 120 --no-pager ;;
    2) journalctl -u xboard-report -n 120 --no-pager ;;
    3) journalctl -u xboard-udp-guard -n 120 --no-pager ;;
    4) docker logs "$CONTAINER" --tail=120 ;;
    5) tail -n 120 "$XRAY_DIR/logs/error.log" 2>/dev/null || true ;;
    *) warn "无效选择" ;;
  esac
}

sync_now() {
  if [ ! -f "$SYNC_DIR/xboard_sync.py" ]; then
    err "未找到 $SYNC_DIR/xboard_sync.py，请先安装"
    return 1
  fi
  python3 "$SYNC_DIR/xboard_sync.py" once
}

show_node_config() {
  if [ ! -f "$XRAY_CONFIG" ]; then
    err "未找到 $XRAY_CONFIG"
    return 1
  fi
  jq '.inbounds[]? | select(.tag != "api") | {
    tag,
    listen,
    port,
    protocol,
    decryption: .settings.decryption,
    flow: (.settings.clients[0].flow // null),
    security: (.streamSettings.security // "none"),
    serverName: (
      .streamSettings.tlsSettings.serverName //
      .streamSettings.realitySettings.serverNames[0] //
      (.streamSettings.realitySettings.dest // "" | split(":")[0]) //
      null
    ),
    realityDest: .streamSettings.realitySettings.dest,
    fingerprint: (
      .streamSettings.tlsSettings.fingerprint //
      .streamSettings.realitySettings.fingerprint
    ),
    ech: (.streamSettings.tlsSettings.echServerKeys != null),
    certificates: .streamSettings.tlsSettings.certificates,
    clients: (.settings.clients | length)
  }' "$XRAY_CONFIG"
}

check_xray_config() {
  if [ ! -f "$XRAY_CONFIG" ]; then
    err "未找到 $XRAY_CONFIG"
    return 1
  fi

  python3 -m json.tool "$XRAY_CONFIG" >/dev/null && ok "JSON 格式正常" || return 1

  local duplicate_ports
  duplicate_ports="$(jq -r '[.inbounds[]?.port?] | group_by(.)[] | select(length > 1) | .[0]' "$XRAY_CONFIG")"
  if [ -n "$duplicate_ports" ]; then
    warn "发现重复端口: $duplicate_ports"
  else
    ok "未发现 inbound 端口冲突"
  fi

  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    if docker exec "$CONTAINER" xray run -test -config /etc/xray/config.json >/tmp/xray-config-test.log 2>&1; then
      ok "Xray 配置测试通过"
    else
      warn "Xray 内置测试未通过或当前版本不支持 run -test，输出如下:"
      cat /tmp/xray-config-test.log
    fi
  else
    warn "xray-core 容器未运行，跳过 Xray 内置测试"
  fi
}

first_tls_server_name() {
  jq -r '[.inbounds[]? | select(.streamSettings.security == "tls") | .streamSettings.tlsSettings.serverName // empty][0] // empty' "$XRAY_CONFIG" 2>/dev/null
}

first_node_port() {
  jq -r '[.inbounds[]? | select(.tag != "api") | .port // empty][0] // empty' "$XRAY_CONFIG" 2>/dev/null
}

check_tls_cert() {
  if [ ! -f "$XRAY_CONFIG" ]; then
    err "未找到 $XRAY_CONFIG"
    return 1
  fi

  echo "===== TLS 配置 ====="
  jq '.inbounds[]? | select(.streamSettings.security == "tls") | {
    tag,
    port,
    serverName: .streamSettings.tlsSettings.serverName,
    fingerprint: .streamSettings.tlsSettings.fingerprint,
    ech: (.streamSettings.tlsSettings.echServerKeys != null),
    certificates: .streamSettings.tlsSettings.certificates
  }' "$XRAY_CONFIG"

  echo
  echo "===== 宿主机证书目录 ====="
  ls -lah "$XRAY_DIR/config/certs" 2>/dev/null || warn "未找到 $XRAY_DIR/config/certs"

  echo
  jq -r '.inbounds[]? | select(.streamSettings.security == "tls") | .tag as $tag |
    (.streamSettings.tlsSettings.certificates // [])[]? |
    [$tag, .certificateFile, .keyFile] | @tsv' "$XRAY_CONFIG" |
  while IFS=$'\t' read -r tag cert key; do
    local host_cert host_key
    host_cert="${cert/#\/etc\/xray/$XRAY_DIR/config}"
    host_key="${key/#\/etc\/xray/$XRAY_DIR/config}"
    [ -r "$host_cert" ] && ok "$tag 证书可读: $host_cert" || warn "$tag 证书不可读: $host_cert"
    [ -r "$host_key" ] && ok "$tag 私钥可读: $host_key" || warn "$tag 私钥不可读: $host_key"
  done

  local server_name port
  server_name="$(first_tls_server_name)"
  port="$(first_node_port)"
  if [ -n "$server_name" ] && [ -n "$port" ]; then
    echo
    echo "===== openssl 测试 ====="
    timeout 12 openssl s_client -connect "127.0.0.1:${port}" -servername "$server_name" -showcerts </dev/null 2>/dev/null |
      openssl x509 -noout -subject -issuer -dates || warn "openssl 未读到证书，请检查端口和 SNI"
  fi

  echo
  docker logs "$CONTAINER" --tail=120 2>/dev/null | grep -i "no certificates configured" && warn "Docker 日志发现 no certificates configured" || ok "最近 Docker 日志未发现 no certificates configured"
}

test_node_port() {
  local port server_name
  port="$(first_node_port)"
  server_name="$(first_tls_server_name)"
  read -rp "测试端口 [${port:-8443}]: " input_port
  port="${input_port:-${port:-8443}}"
  read -rp "TLS SNI [${server_name:-留空跳过证书测试}]: " input_sni
  server_name="${input_sni:-$server_name}"

  ss -lntp | grep -E ":${port}[[:space:]]" && ok "端口 ${port} 正在监听" || warn "端口 ${port} 未监听"

  if [ -n "$server_name" ]; then
    timeout 12 openssl s_client -connect "127.0.0.1:${port}" -servername "$server_name" -showcerts </dev/null 2>/dev/null |
      openssl x509 -noout -subject -issuer -dates || warn "TLS 证书测试失败"
  fi
}

allow_node_ports() {
  if ! command -v ufw >/dev/null 2>&1; then
    err "未安装 ufw"
    return 1
  fi
  if [ ! -f "$XRAY_CONFIG" ]; then
    err "未找到 $XRAY_CONFIG，无法自动读取节点端口"
    return 1
  fi

  jq -r '.inbounds[]? | select(.tag != "api") | [.protocol, .port] | @tsv' "$XRAY_CONFIG" |
  while IFS=$'\t' read -r protocol port; do
    [ -n "$port" ] || continue
    ufw allow "${port}/tcp"
    if [ "$protocol" = "shadowsocks" ]; then
      ufw allow "${port}/udp"
    fi
  done
  ufw reload || true
  ufw status
}

show_config() {
  if [ ! -f "$XRAY_CONFIG" ]; then
    err "未找到 $XRAY_CONFIG"
    return 1
  fi
  jq . "$XRAY_CONFIG" || cat "$XRAY_CONFIG"
}

backup_current_config() {
  ensure_runtime_dirs
  local stamp backup
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup="$BACKUP_DIR/config.json.manual.${stamp}"
  if [ -f "$XRAY_CONFIG" ]; then
    cp -a "$XRAY_CONFIG" "$backup"
    ok "已备份配置: $backup"
  else
    warn "未找到 $XRAY_CONFIG"
  fi
  if [ -d "$XRAY_DIR/config/certs" ]; then
    cp -a "$XRAY_DIR/config/certs" "$BACKUP_DIR/certs.manual.${stamp}"
    ok "已备份证书目录"
  fi
}

restore_latest_config() {
  local latest
  latest="$(ls -1t "$BACKUP_DIR"/config.json.* 2>/dev/null | head -n 1 || true)"
  if [ -z "$latest" ]; then
    err "没有找到可恢复的 config 备份"
    return 1
  fi
  echo "将恢复: $latest"
  read -rp "确认恢复并重启 xray-core? [y/N]: " confirm
  case "$confirm" in
    y|Y)
      cp -a "$latest" "$XRAY_CONFIG"
      docker restart "$CONTAINER" 2>/dev/null || true
      ok "已恢复并重启"
      ;;
    *) warn "已取消" ;;
  esac
}

set_env_value() {
  local key="$1" value="$2"
  if grep -q -E "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
}

configure_udp_guard() {
  local mode soft hard window block confirm old_mode old_soft old_hard old_window old_block
  old_mode="$(get_env_value UDP_GUARD_MODE)"; old_mode="${old_mode:-observe}"
  old_soft="$(get_env_value UDP_GUARD_SOFT_LIMIT)"; old_soft="${old_soft:-256}"
  old_hard="$(get_env_value UDP_GUARD_HARD_LIMIT)"; old_hard="${old_hard:-512}"
  old_window="$(get_env_value UDP_GUARD_WINDOW_SECONDS)"; old_window="${old_window:-120}"
  old_block="$(get_env_value UDP_GUARD_BLOCK_SECONDS)"; old_block="${old_block:-600}"

  read -rp "模式 disabled/observe/block [$old_mode]: " mode
  read -rp "软阈值 [$old_soft]: " soft
  read -rp "硬阈值 [$old_hard]: " hard
  read -rp "统计窗口秒数 [$old_window]: " window
  read -rp "临时阻断秒数 [$old_block]: " block

  mode="${mode:-$old_mode}"
  soft="${soft:-$old_soft}"
  hard="${hard:-$old_hard}"
  window="${window:-$old_window}"
  block="${block:-$old_block}"

  case "$mode" in
    disabled|observe|block) ;;
    *) err "模式必须是 disabled、observe 或 block"; return 1 ;;
  esac
  for value in "$soft" "$hard" "$window" "$block"; do
    [[ "$value" =~ ^[0-9]+$ ]] || { err "阈值和秒数必须是正整数"; return 1; }
  done
  if [ "$soft" -lt 1 ] || [ "$hard" -le "$soft" ] || [ "$window" -lt 10 ] || [ "$block" -lt 60 ]; then
    err "要求: soft >= 1、hard > soft、window >= 10、block >= 60"
    return 1
  fi

  if [ "$mode" = "block" ]; then
    echo "block 模式会在单用户超过硬阈值时自动临时阻断其 UDP。"
    read -rp "确认启用自动阻断？[y/N]: " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { warn "已取消"; return 0; }
  fi

  set_env_value UDP_GUARD_MODE "$mode"
  set_env_value UDP_GUARD_SOFT_LIMIT "$soft"
  set_env_value UDP_GUARD_HARD_LIMIT "$hard"
  set_env_value UDP_GUARD_WINDOW_SECONDS "$window"
  set_env_value UDP_GUARD_BLOCK_SECONDS "$block"

  systemctl restart xboard-udp-guard 2>/dev/null || warn "UDP Guard 服务重启失败，请检查 systemd 日志"
  if ! python3 "$SYNC_DIR/xboard_sync.py" once; then
    err "Xray 配置同步失败，已保留新的 Guard 参数，请检查日志"
    return 1
  fi
  ok "UDP Guard 已更新: mode=$mode soft=$soft hard=$hard window=${window}s block=${block}s"
}

udp_guard_menu() {
  local choice user seconds
  if [ ! -f "$SYNC_DIR/udp_guard.py" ]; then
    err "未找到 $SYNC_DIR/udp_guard.py，请先更新或安装"
    return 1
  fi
  cat <<'MENU'
1. 查看 UDP 风险状态
2. 修改 UDP Guard 阈值/模式
3. 手动临时阻断用户 UDP
4. 手动解除用户 UDP 阻断
MENU
  read -rp "选择 [1-4]: " choice
  case "$choice" in
    1) python3 "$SYNC_DIR/udp_guard.py" status ;;
    2) configure_udp_guard ;;
    3)
      read -rp "用户标识 node_id:user_id: " user
      read -rp "阻断秒数 [600]: " seconds
      python3 "$SYNC_DIR/udp_guard.py" block "$user" --seconds "${seconds:-600}"
      ;;
    4)
      read -rp "用户标识 node_id:user_id: " user
      python3 "$SYNC_DIR/udp_guard.py" unblock "$user"
      ;;
    *) warn "无效选择" ;;
  esac
}

print_header() {
  clear 2>/dev/null || true
  echo "========================================"
  echo " XBoard Xray Docker Sync 管理菜单"
  echo "========================================"
  echo
  echo "面板: $(get_env_value PANEL_URL)"
  echo "节点: $(get_env_value NODES)"
  echo -n "xboard-sync: "
  systemctl is-active xboard-sync 2>/dev/null || true
  echo -n "xray-core: "
  docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "not-found"
  echo "UDP Guard: $(get_env_value UDP_GUARD_MODE)"
  echo
}

main_menu() {
  need_root
  while true; do
    print_header
    cat <<'MENU'
0. 修改面板配置
1. 安装/重装
2. 更新脚本
3. 卸载

4. 启动服务
5. 停止服务
6. 重启服务
7. 查看状态
8. 查看日志

9. 立即同步面板配置
10. 查看当前节点配置
11. 检查 Xray 配置
12. 检查 TLS 证书
13. 测试节点端口
14. 放行节点端口
15. 查看完整 config.json
16. 备份当前配置
17. 恢复上一次配置
18. UDP Guard 风险与限制

q. 退出
MENU
    echo
    read -rp "请输入选择 [0-18/q]: " choice
    case "$choice" in
      0) edit_panel_config; pause ;;
      1) install_stack; pause ;;
      2) update_stack; pause ;;
      3) uninstall_stack; pause ;;
      4) start_services; pause ;;
      5) stop_services; pause ;;
      6) restart_services; pause ;;
      7) show_status; pause ;;
      8) show_logs; pause ;;
      9) sync_now; pause ;;
      10) show_node_config; pause ;;
      11) check_xray_config; pause ;;
      12) check_tls_cert; pause ;;
      13) test_node_port; pause ;;
      14) allow_node_ports; pause ;;
      15) show_config; pause ;;
      16) backup_current_config; pause ;;
      17) restore_latest_config; pause ;;
      18) udp_guard_menu; pause ;;
      q|Q) exit 0 ;;
      *) warn "无效选择"; pause ;;
    esac
  done
}

main_menu "$@"
