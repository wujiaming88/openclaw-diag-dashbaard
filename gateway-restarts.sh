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
      # handle --tz VALUE
      if [[ "${prev_arg:-}" == "--tz" ]]; then
        DISPLAY_TZ="$arg"
      else
        LOG_INPUT="$arg"
      fi
      ;;
  esac
  prev_arg="$arg"
done

# UTC -> 指定时区转换函数
to_display_tz() {
  local utc_ts="$1"
  [[ -z "$utc_ts" ]] && echo "-" && return
  if [[ "$DISPLAY_TZ" == "UTC" ]]; then
    echo "$utc_ts"
    return
  fi
  if command -v python3 &>/dev/null; then
    python3 -c "
from datetime import datetime, timezone, timedelta
import zoneinfo
ts='${utc_ts}'.replace('Z','')
if '.' in ts: ts=ts[:ts.index('.')]
dt=datetime.strptime(ts,'%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
tz=zoneinfo.ZoneInfo('${DISPLAY_TZ}')
local=dt.astimezone(tz)
print(local.strftime('%Y-%m-%d %H:%M:%S'))
" 2>/dev/null || echo "$utc_ts"
  else
    echo "$utc_ts"
  fi
}

# 配置路径/原因 → 中文说明
translate_reason() {
  local reason="$1"
  local cn=""

  # --- 事件类型翻译 ---
  case "$reason" in
    "manual/systemd")          echo "手动重启或 systemd 触发"; return ;;
    "initial boot")            echo "首次启动"; return ;;
    "initial boot or log gap") echo "首次启动或日志缺失"; return ;;
    "crash/OOM")               echo "进程崩溃或内存溢出"; return ;;
  esac

  # --- 配置路径关键字翻译 ---
  # 逐个匹配，拼接中文说明
  local keys_cn=""

  # 提取括号/冒号后的配置路径部分
  local paths=""
  if [[ "$reason" == *": "* ]]; then
    paths="${reason#*: }"
  else
    paths="$reason"
  fi

  # 按逗号分割每个 key path
  IFS=',' read -ra path_arr <<< "$paths"
  for p in "${path_arr[@]}"; do
    p=$(echo "$p" | xargs)  # trim
    local desc=""
    if   [[ "$p" == "meta.lastTouchedVersion" ]];       then desc="版本号更新"
    elif [[ "$p" == "meta.lastTouchedAt" ]];             then desc="最后修改时间"
    elif [[ "$p" == *".groupPolicy" ]];                  then desc="群组策略 (${p})"
    elif [[ "$p" == *".groupAllowFrom" ]];               then desc="群组白名单 (${p})"
    elif [[ "$p" == *".allowFrom" ]];                    then desc="允许来源 (${p})"
    elif [[ "$p" == *".groups" ]];                       then desc="群组列表 (${p})"
    elif [[ "$p" == "channels.telegram.accounts."* ]];   then desc="Telegram 账号配置: ${p##channels.telegram.accounts.}"
    elif [[ "$p" == "channels.telegram."* ]];             then desc="Telegram 配置: ${p##channels.telegram.}"
    elif [[ "$p" == "channels.feishu."* ]];               then desc="飞书配置: ${p##channels.feishu.}"
    elif [[ "$p" == "channels.discord."* ]];              then desc="Discord 配置: ${p##channels.discord.}"
    elif [[ "$p" == "channels.slack."* ]];                then desc="Slack 配置: ${p##channels.slack.}"
    elif [[ "$p" == "channels.whatsapp."* ]];             then desc="WhatsApp 配置: ${p##channels.whatsapp.}"
    elif [[ "$p" == "channels."* ]];                      then desc="渠道配置: ${p##channels.}"
    elif [[ "$p" == "plugins.entries."* ]];                then desc="插件配置: ${p##plugins.entries.}"
    elif [[ "$p" == "plugins."* ]];                       then desc="插件配置: ${p##plugins.}"
    elif [[ "$p" == "agents.list" ]];                     then desc="Agent 列表"
    elif [[ "$p" == "agents.defaults."* ]];               then desc="Agent 默认配置: ${p##agents.defaults.}"
    elif [[ "$p" == "agents."* ]];                        then desc="Agent 配置: ${p##agents.}"
    elif [[ "$p" == "models.providers."* ]];               then desc="模型提供商: ${p##models.providers.}"
    elif [[ "$p" == "models."* ]];                         then desc="模型配置: ${p##models.}"
    elif [[ "$p" == "gateway."* ]];                        then desc="网关配置: ${p##gateway.}"
    elif [[ "$p" == "session."* ]];                        then desc="会话配置: ${p##session.}"
    elif [[ "$p" == "auth."* ]];                           then desc="认证配置: ${p##auth.}"
    elif [[ "$p" == "logging."* ]];                        then desc="日志配置: ${p##logging.}"
    else                                                        desc="$p"
    fi
    [[ -n "$keys_cn" ]] && keys_cn="$keys_cn, "
    keys_cn="$keys_cn$desc"
  done

  # 拼接前缀
  if [[ "$reason" == "config change:"* ]]; then
    echo "配置变更触发重启: $keys_cn"
  elif [[ "$reason" == "hot reload:"* ]]; then
    echo "热重载: $keys_cn"
  else
    echo "$keys_cn"
  fi
}

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

echo "🔍 分析 OpenClaw Gateway 重启历史..."
echo "   日志来源: $LOG_INPUT"
echo "   文件数量: ${#LOG_ARRAY[@]}"
echo "   显示时区: $TZ_LABEL ($DISPLAY_TZ)"
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
  printf "%-4s  %-22s  %-22s  %-14s  %s\n" "#" "⏹ 关闭时间 ($TZ_LABEL)" "▶ 启动时间 ($TZ_LABEL)" "类型" "触发原因"
  printf "%-4s  %-22s  %-22s  %-14s  %s\n" "----" "----------------------" "----------------------" "--------------" "------------------------------------"
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
      local_ts=$(to_display_tz "$ts")
      reason_cn=$(translate_reason "$reason")
      if $JSON_OUTPUT; then
        json_items+=("$(printf '{"num":%d,"shutdown":null,"startup":"%s","startup_utc":"%s","type":"HOT_RELOAD","reason":"%s","reason_cn":"%s","downtime":"0s"}' \
          "$restart_num" "$local_ts" "$ts" "$reason" "$reason_cn")")
      else
        printf "%-4d  %-22s  %-22s  %-14s  %s\n" \
          "$restart_num" "-" "$local_ts" "🔄 热重载" "$reason_cn"
      fi
      ;;
    SHUTDOWN|CRASH)
      shutdown_ts="$ts"
      if [[ "$etype" == "CRASH" ]]; then
        restart_type="💥 崩溃"
        trigger_reason="$reason"
      else
        restart_type="⏹ 终止信号"
        [[ -z "$trigger_reason" ]] && trigger_reason="manual/systemd"
      fi
      ;;
    STARTUP)
      restart_num=$((restart_num + 1))
      local_startup=$(to_display_tz "$ts")
      if [[ -z "$shutdown_ts" ]]; then
        # 没有对应的 shutdown = 初始启动或日志不完整
        reason_cn=$(translate_reason "initial boot")
        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":null,"startup":"%s","startup_utc":"%s","type":"INITIAL","reason":"initial boot","reason_cn":"%s","downtime":null}' \
            "$restart_num" "$local_startup" "$ts" "$reason_cn")")
        else
          printf "%-4d  %-22s  %-22s  %-14s  %s\n" \
            "$restart_num" "-" "$local_startup" "🟢 首次启动" "$reason_cn"
        fi
      else
        local_shutdown=$(to_display_tz "$shutdown_ts")
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

        reason_cn=$(translate_reason "$trigger_reason")
        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":"%s","shutdown_utc":"%s","startup":"%s","startup_utc":"%s","type":"%s","reason":"%s","reason_cn":"%s","downtime":"%s"}' \
            "$restart_num" "$local_shutdown" "$shutdown_ts" "$local_startup" "$ts" "$restart_type" "$trigger_reason" "$reason_cn" "$downtime")")
        else
          printf "%-4d  %-22s  %-22s  %-14s  %s (停机 %s)\n" \
            "$restart_num" "$local_shutdown" "$local_startup" "$restart_type" "$reason_cn" "$downtime"
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
    local_shutdown=$(to_display_tz "$shutdown_ts")
    reason_cn=$(translate_reason "$trigger_reason")
    json_items+=("$(printf '{"num":%d,"shutdown":"%s","shutdown_utc":"%s","startup":null,"type":"%s","reason":"%s","reason_cn":"%s (⚠️ 未恢复)","downtime":null}' \
      "$restart_num" "$local_shutdown" "$shutdown_ts" "$restart_type" "$trigger_reason" "$reason_cn")")
  else
    local_shutdown=$(to_display_tz "$shutdown_ts")
    reason_cn=$(translate_reason "$trigger_reason")
    printf "%-4d  %-22s  %-22s  %-14s  %s\n" \
      "$restart_num" "$local_shutdown" "⚠️  未恢复!" "$restart_type" "$reason_cn"
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
