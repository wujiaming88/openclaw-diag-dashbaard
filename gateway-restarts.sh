#!/usr/bin/env bash
# gateway-restarts.sh — 分析 OpenClaw Gateway 重启历史
# 用法: ./gateway-restarts.sh [日志文件或目录] [--json]
#
# 示例:
#   ./gateway-restarts.sh                          # 自动查找今天的日志
#   ./gateway-restarts.sh /tmp/openclaw/            # 扫描目录下所有日志
#   ./gateway-restarts.sh /tmp/openclaw/openclaw-2026-03-17.log
#   ./gateway-restarts.sh --json                   # JSON 输出

set -uo pipefail

JSON_OUTPUT=false
LOG_INPUT=""

for arg in "$@"; do
  case "$arg" in
    --json) JSON_OUTPUT=true ;;
    *) LOG_INPUT="$arg" ;;
  esac
done

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

echo "🔍 分析 OpenClaw Gateway 重启历史..."
echo "   日志来源: $LOG_INPUT"
echo "   文件数量: ${#LOG_ARRAY[@]}"
echo ""

# --- 提取事件流（去重：同一秒同类型只保留一条） ---
extract_events() {
  for f in "${LOG_ARRAY[@]}"; do
    # SIGTERM shutdown（只取第一条 "received SIGTERM; shutting down"）
    grep -n '"received SIGTERM; shutting down"' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      [[ -n "$ts" ]] && echo "SHUTDOWN|${ts}|SIGTERM|$f"
    done

    # config change trigger
    grep -n 'config change requires gateway restart' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      detail=$(echo "$rest" | grep -oP 'config change requires gateway restart \(\K[^)]+' | head -1)
      [[ -n "$ts" ]] && echo "TRIGGER|${ts}|config change: ${detail}|$f"
    done

    # config reload (no restart needed)
    grep -n 'config change detected; evaluating reload' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      detail=$(echo "$rest" | grep -oP 'evaluating reload \(\K[^)]+' | head -1)
      [[ -n "$ts" ]] && echo "RELOAD|${ts}|hot reload: ${detail}|$f"
    done

    # heartbeat started = startup marker
    grep -n '"heartbeat: started"' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      [[ -n "$ts" ]] && echo "STARTUP|${ts}|heartbeat started|$f"
    done

    # crash signals
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

# --- 组装重启周期 ---
restart_num=0
json_items=()

# 表头
if ! $JSON_OUTPUT; then
  printf "%-4s  %-26s  %-26s  %-14s  %s\n" "#" "⏹ 关闭时间 (UTC)" "▶ 启动时间 (UTC)" "类型" "触发原因"
  printf "%-4s  %-26s  %-26s  %-14s  %s\n" "----" "--------------------------" "--------------------------" "--------------" "------------------------------------"
fi

shutdown_ts=""
trigger_reason=""
restart_type=""

while IFS='|' read -r etype ts reason _location; do
  case "$etype" in
    TRIGGER)
      trigger_reason="$reason"
      ;;
    RELOAD)
      # hot reload 不需要重启，单独记录
      restart_num=$((restart_num + 1))
      if $JSON_OUTPUT; then
        json_items+=("$(printf '{"num":%d,"shutdown":null,"startup":"%s","type":"HOT_RELOAD","reason":"%s","downtime":"0s"}' \
          "$restart_num" "$ts" "$reason")")
      else
        printf "%-4d  %-26s  %-26s  %-14s  %s\n" \
          "$restart_num" "-" "$ts" "🔄 HOT_RELOAD" "$reason"
      fi
      ;;
    SHUTDOWN|CRASH)
      shutdown_ts="$ts"
      if [[ "$etype" == "CRASH" ]]; then
        restart_type="💥 CRASH"
        trigger_reason="$reason"
      else
        restart_type="⏹ SIGTERM"
        [[ -z "$trigger_reason" ]] && trigger_reason="manual/systemd"
      fi
      ;;
    STARTUP)
      restart_num=$((restart_num + 1))
      if [[ -z "$shutdown_ts" ]]; then
        # 没有对应的 shutdown = 初始启动或日志不完整
        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":null,"startup":"%s","type":"INITIAL","reason":"initial boot or log gap","downtime":null}' \
            "$restart_num" "$ts")")
        else
          printf "%-4d  %-26s  %-26s  %-14s  %s\n" \
            "$restart_num" "-" "$ts" "🟢 INITIAL" "initial boot"
        fi
      else
        # 计算停机时间
        if command -v python3 &>/dev/null; then
          downtime=$(python3 -c "
from datetime import datetime
fmt='%Y-%m-%dT%H:%M:%S'
t1=datetime.strptime('${shutdown_ts:0:19}', fmt)
t2=datetime.strptime('${ts:0:19}', fmt)
d=int((t2-t1).total_seconds())
print(f'{d}s' if d<60 else f'{d//60}m{d%60}s')
" 2>/dev/null || echo "?")
        else
          downtime="?"
        fi

        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":"%s","startup":"%s","type":"%s","reason":"%s","downtime":"%s"}' \
            "$restart_num" "$shutdown_ts" "$ts" "$restart_type" "$trigger_reason" "$downtime")")
        else
          printf "%-4d  %-26s  %-26s  %-14s  %s (停机 %s)\n" \
            "$restart_num" "$shutdown_ts" "$ts" "$restart_type" "$trigger_reason" "$downtime"
        fi
      fi
      # 重置状态
      shutdown_ts=""
      trigger_reason=""
      restart_type=""
      ;;
  esac
done <<< "$events"

# 处理最后一个未闭合的 shutdown
if [[ -n "$shutdown_ts" ]]; then
  restart_num=$((restart_num + 1))
  if $JSON_OUTPUT; then
    json_items+=("$(printf '{"num":%d,"shutdown":"%s","startup":null,"type":"%s","reason":"%s (⚠️ 未恢复)","downtime":null}' \
      "$restart_num" "$shutdown_ts" "$restart_type" "$trigger_reason")")
  else
    printf "%-4d  %-26s  %-26s  %-14s  %s\n" \
      "$restart_num" "$shutdown_ts" "⚠️  未恢复!" "$restart_type" "$trigger_reason"
  fi
fi

# --- 输出 ---
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
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 总计: $restart_num 次启动/重启/热重载"

# systemd 补充信息
if command -v systemctl &>/dev/null; then
  for svc in openclaw-gateway openclaw; do
    active_since=$(systemctl --user show "$svc" --property=ActiveEnterTimestamp 2>/dev/null | cut -d= -f2 || true)
    pid=$(systemctl --user show "$svc" --property=MainPID 2>/dev/null | cut -d= -f2 || true)
    if [[ -n "$active_since" ]] && [[ "$pid" != "0" ]] && [[ -n "$pid" ]]; then
      echo "📋 当前进程: PID $pid, 启动于 $active_since"
      break
    fi
  done
fi
