#!/usr/bin/env bash
# gateway-restarts.sh — 分析 OpenClaw Gateway 重启历史
# 用法: ./gateway-restarts.sh [日志文件或目录] [--json]
#
# 示例:
#   ./gateway-restarts.sh                          # 自动查找今天的日志
#   ./gateway-restarts.sh /tmp/openclaw/            # 扫描目录下所有日志
#   ./gateway-restarts.sh /tmp/openclaw/openclaw-2026-03-17.log
#   ./gateway-restarts.sh --json                   # JSON 输出
#   ./gateway-restarts.sh --tz Asia/Shanghai       # 指定时区（默认 Asia/Shanghai）
#   ./gateway-restarts.sh --utc                    # 使用 UTC 时间

set -uo pipefail

JSON_OUTPUT=false
LOG_INPUT=""
DISPLAY_TZ="Asia/Shanghai"

for arg in "$@"; do
  case "$arg" in
    --json) JSON_OUTPUT=true ;;
    --utc) DISPLAY_TZ="UTC" ;;
    --tz) :;; # next arg handled below
    *) 
      if [[ "${prev_arg:-}" == "--tz" ]]; then
        DISPLAY_TZ="$arg"
      else
        LOG_INPUT="$arg"
      fi
      ;;
  esac
  prev_arg="$arg"
done

# ━━━ 工具函数 ━━━

# UTC -> 指定时区转换
to_display_tz() {
  local utc_ts="$1"
  [[ -z "$utc_ts" ]] && echo "-" && return
  if [[ "$DISPLAY_TZ" == "UTC" ]]; then
    echo "$utc_ts"; return
  fi
  if command -v python3 &>/dev/null; then
    python3 -c "
from datetime import datetime, timezone
import zoneinfo
ts='${utc_ts}'.replace('Z','')
if '.' in ts: ts=ts[:ts.index('.')]
dt=datetime.strptime(ts,'%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
tz=zoneinfo.ZoneInfo('${DISPLAY_TZ}')
print(dt.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S'))
" 2>/dev/null || echo "$utc_ts"
  else
    echo "$utc_ts"
  fi
}

# 计算停机秒数
calc_downtime() {
  local t1="$1" t2="$2"
  if command -v python3 &>/dev/null; then
    python3 -c "
from datetime import datetime
t1=datetime.strptime('${t1:0:19}','%Y-%m-%dT%H:%M:%S')
t2=datetime.strptime('${t2:0:19}','%Y-%m-%dT%H:%M:%S')
d=int((t2-t1).total_seconds())
print(f'{d}s' if d<60 else f'{d//60}m{d%60}s')
" 2>/dev/null || echo "?"
  else
    echo "?"
  fi
}

# ━━━ 查找日志 ━━━

find_logs() {
  for dir in "/tmp/openclaw" "$HOME/.openclaw/logs" "/var/log/openclaw"; do
    if [[ -d "$dir" ]] && ls "$dir"/openclaw-*.log &>/dev/null; then
      echo "$dir"; return
    fi
  done
  return 1
}

if [[ -z "$LOG_INPUT" ]]; then
  LOG_DIR=$(find_logs) || { echo "❌ 未找到日志文件，请手动指定路径" >&2; exit 1; }
  LOG_INPUT="$LOG_DIR"
fi

if [[ -d "$LOG_INPUT" ]]; then
  mapfile -t LOG_ARRAY < <(ls -1 "$LOG_INPUT"/openclaw-*.log 2>/dev/null | sort)
  [[ ${#LOG_ARRAY[@]} -eq 0 ]] && { echo "❌ 目录 $LOG_INPUT 中无 openclaw-*.log 文件" >&2; exit 1; }
elif [[ -f "$LOG_INPUT" ]]; then
  LOG_ARRAY=("$LOG_INPUT")
else
  echo "❌ 路径不存在: $LOG_INPUT" >&2; exit 1
fi

TZ_LABEL="$DISPLAY_TZ"
[[ "$DISPLAY_TZ" == "Asia/Shanghai" ]] && TZ_LABEL="北京时间"

# ━━━ 提取事件流 ━━━

extract_events() {
  for f in "${LOG_ARRAY[@]}"; do
    grep -n '"received SIGTERM; shutting down"' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      [[ -n "$ts" ]] && echo "SHUTDOWN|${ts}|SIGTERM|$f"
    done

    grep -n 'config change requires gateway restart' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      detail=$(echo "$rest" | grep -oP 'config change requires gateway restart \(\K[^)]+' | head -1)
      [[ -n "$ts" ]] && echo "TRIGGER|${ts}|${detail}|$f"
    done

    grep -n 'config change detected; evaluating reload' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      detail=$(echo "$rest" | grep -oP 'evaluating reload \(\K[^)]+' | head -1)
      [[ -n "$ts" ]] && echo "RELOAD|${ts}|${detail}|$f"
    done

    grep -n '"heartbeat: started"' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      [[ -n "$ts" ]] && echo "STARTUP|${ts}|heartbeat started|$f"
    done

    grep -n 'uncaughtException\|unhandledRejection\|ENOMEM\|SIGKILL\|out of memory' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      [[ -n "$ts" ]] && echo "CRASH|${ts}|crash/OOM|$f"
    done
  done | sort -t'|' -k2 | awk -F'|' '!seen[$1 substr($2,1,19)]++' 
}

events=$(extract_events)

if [[ -z "$events" ]]; then
  echo "ℹ️  未发现任何重启记录"
  exit 0
fi

# ━━━ 输出 ━━━

echo "┌──────────────────────────────────────────────────────────────────────────────────┐"
echo "│  🔍 OpenClaw Gateway 重启历史分析                                               │"
echo "│                                                                                  │"
echo "│  日志来源: $LOG_INPUT"
echo "│  显示时区: $TZ_LABEL ($DISPLAY_TZ)"
echo "└──────────────────────────────────────────────────────────────────────────────────┘"
echo ""

restart_num=0
json_items=()
shutdown_ts=""
trigger_reason=""
restart_type=""
restart_type_cn=""
count_initial=0
count_restart=0
count_reload=0

print_entry() {
  local num="$1" down_time="$2" up_time="$3" type_cn="$4" reason="$5" downtime="${6:-}"

  if [[ "$num" -gt 1 ]]; then
    echo "  │"
  fi

  local downtime_str=""
  [[ -n "$downtime" ]] && downtime_str=" ⏱ 停机 $downtime"

  printf "  %-3d  %s\n" "$num" "$type_cn$downtime_str"
  [[ "$down_time" != "-" ]] && echo "       ⏹ 关闭: $down_time"
  [[ "$up_time" != "-" ]]   && echo "       ▶ 启动: $up_time"
  echo "       📋 原因: $reason"
}

while IFS='|' read -r etype ts reason _location; do
  case "$etype" in
    TRIGGER)
      trigger_reason="$reason"
      ;;
    RELOAD)
      restart_num=$((restart_num + 1))
      count_reload=$((count_reload + 1))
      local_ts=$(to_display_tz "$ts")
      if $JSON_OUTPUT; then
        json_items+=("$(printf '{"num":%d,"shutdown":null,"startup":"%s","startup_utc":"%s","type":"HOT_RELOAD","reason":"%s","downtime":"0s"}' \
          "$restart_num" "$local_ts" "$ts" "$reason")")
      else
        print_entry "$restart_num" "-" "$local_ts" "🔄 热重载" "$reason"
      fi
      ;;
    SHUTDOWN|CRASH)
      shutdown_ts="$ts"
      if [[ "$etype" == "CRASH" ]]; then
        restart_type="CRASH"
        restart_type_cn="💥 崩溃"
        trigger_reason="$reason"
      else
        restart_type="SIGTERM"
        restart_type_cn="⏹ SIGTERM"
        [[ -z "$trigger_reason" ]] && trigger_reason="manual/systemd"
      fi
      ;;
    STARTUP)
      restart_num=$((restart_num + 1))
      local_startup=$(to_display_tz "$ts")
      if [[ -z "$shutdown_ts" ]]; then
        count_initial=$((count_initial + 1))
        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":null,"startup":"%s","startup_utc":"%s","type":"INITIAL","reason":"initial boot","downtime":null}' \
            "$restart_num" "$local_startup" "$ts")")
        else
          print_entry "$restart_num" "-" "$local_startup" "🟢 首次启动" "initial boot"
        fi
      else
        count_restart=$((count_restart + 1))
        local_shutdown=$(to_display_tz "$shutdown_ts")
        downtime=$(calc_downtime "$shutdown_ts" "$ts")

        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":"%s","shutdown_utc":"%s","startup":"%s","startup_utc":"%s","type":"%s","reason":"%s","downtime":"%s"}' \
            "$restart_num" "$local_shutdown" "$shutdown_ts" "$local_startup" "$ts" "$restart_type" "$trigger_reason" "$downtime")")
        else
          print_entry "$restart_num" "$local_shutdown" "$local_startup" "$restart_type_cn" "$trigger_reason" "$downtime"
        fi
      fi
      shutdown_ts=""
      trigger_reason=""
      restart_type=""
      restart_type_cn=""
      ;;
  esac
done <<< "$events"

# 未闭合的 shutdown
if [[ -n "$shutdown_ts" ]]; then
  restart_num=$((restart_num + 1))
  local_shutdown=$(to_display_tz "$shutdown_ts")
  if $JSON_OUTPUT; then
    json_items+=("$(printf '{"num":%d,"shutdown":"%s","shutdown_utc":"%s","startup":null,"type":"%s","reason":"%s","downtime":null}' \
      "$restart_num" "$local_shutdown" "$shutdown_ts" "$restart_type" "$trigger_reason")")
  else
    print_entry "$restart_num" "$local_shutdown" "⚠️  未恢复!" "$restart_type_cn" "$trigger_reason"
  fi
fi

# JSON 输出
if $JSON_OUTPUT; then
  echo "["
  for i in "${!json_items[@]}"; do
    if [[ $i -lt $((${#json_items[@]} - 1)) ]]; then
      echo "  ${json_items[$i]},"
    else
      echo "  ${json_items[$i]}"
    fi
  done
  echo "]"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📊 总计: $restart_num 条记录 (首次启动 ${count_initial}, 重启 ${count_restart}, 热重载 ${count_reload})"

# systemd 补充信息
if command -v systemctl &>/dev/null; then
  for svc in openclaw-gateway openclaw; do
    active_since=$(systemctl --user show "$svc" --property=ActiveEnterTimestamp 2>/dev/null | cut -d= -f2 || true)
    pid=$(systemctl --user show "$svc" --property=MainPID 2>/dev/null | cut -d= -f2 || true)
    if [[ -n "$active_since" ]] && [[ "$pid" != "0" ]] && [[ -n "$pid" ]]; then
      echo "  📋 当前进程: PID $pid, 启动于 $active_since"
      break
    fi
  done
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
