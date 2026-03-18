#!/usr/bin/env bash
# gateway-restarts.sh — Analyze OpenClaw Gateway restart history
# Usage: ./gateway-restarts.sh [log-file-or-dir] [--json] [--utc] [--tz ZONE]
#
# Examples:
#   ./gateway-restarts.sh                          # auto-detect logs
#   ./gateway-restarts.sh /tmp/openclaw/            # scan directory
#   ./gateway-restarts.sh /tmp/openclaw/openclaw-2026-03-17.log
#   ./gateway-restarts.sh --json                   # JSON output
#   ./gateway-restarts.sh --tz Asia/Shanghai       # set timezone (default: Asia/Shanghai)
#   ./gateway-restarts.sh --utc                    # use UTC

set -uo pipefail

JSON_OUTPUT=false
LOG_INPUT=""
DISPLAY_TZ="Asia/Shanghai"

for arg in "$@"; do
  case "$arg" in
    --json) JSON_OUTPUT=true ;;
    --utc) DISPLAY_TZ="UTC" ;;
    --tz) :;;
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

# --- utils ---

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

# --- find logs ---

find_logs() {
  for dir in "/tmp/openclaw" "$HOME/.openclaw/logs" "/var/log/openclaw"; do
    if [[ -d "$dir" ]] && ls "$dir"/openclaw-*.log &>/dev/null; then
      echo "$dir"; return
    fi
  done
  return 1
}

if [[ -z "$LOG_INPUT" ]]; then
  LOG_DIR=$(find_logs) || { echo "❌ No log files found, please specify path" >&2; exit 1; }
  LOG_INPUT="$LOG_DIR"
fi

if [[ -d "$LOG_INPUT" ]]; then
  mapfile -t LOG_ARRAY < <(ls -1 "$LOG_INPUT"/openclaw-*.log 2>/dev/null | sort)
  [[ ${#LOG_ARRAY[@]} -eq 0 ]] && { echo "❌ No openclaw-*.log files in directory" >&2; exit 1; }
elif [[ -f "$LOG_INPUT" ]]; then
  LOG_ARRAY=("$LOG_INPUT")
else
  echo "❌ Path not found: $LOG_INPUT" >&2; exit 1
fi

TZ_LABEL="$DISPLAY_TZ"
[[ "$DISPLAY_TZ" == "Asia/Shanghai" ]] && TZ_LABEL="CST"

# --- extract events (restarts only, no hot reload) ---

extract_events() {
  for f in "${LOG_ARRAY[@]}"; do
    # SIGTERM shutdown
    grep -n '"received SIGTERM; shutting down"' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      [[ -n "$ts" ]] && echo "SHUTDOWN|${ts}|SIGTERM|$f"
    done

    # config change that requires restart (not hot reload)
    grep -n 'config change requires gateway restart' "$f" 2>/dev/null | while IFS=: read -r _ rest; do
      ts=$(echo "$rest" | grep -oP '"date":"\K[^"]+' | head -1)
      detail=$(echo "$rest" | grep -oP 'config change requires gateway restart \(\K[^)]+' | head -1)
      [[ -n "$ts" ]] && echo "TRIGGER|${ts}|${detail}|$f"
    done

    # heartbeat started = process startup marker
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
  echo "No restart records found"
  exit 0
fi

# --- output ---

echo "┌──────────────────────────────────────────────────────────────────────────────────┐"
echo "│  OpenClaw Gateway Restart History                                                │"
echo "│                                                                                  │"
echo "│  Logs: $LOG_INPUT"
echo "│  Timezone: $TZ_LABEL ($DISPLAY_TZ)"
echo "└──────────────────────────────────────────────────────────────────────────────────┘"
echo ""

restart_num=0
json_items=()
shutdown_ts=""
trigger_reason=""
restart_type=""

print_entry() {
  local num="$1" down_time="$2" up_time="$3" type="$4" reason="$5" downtime="${6:-}"

  [[ "$num" -gt 1 ]] && echo "  │"

  local downtime_str=""
  [[ -n "$downtime" ]] && downtime_str=" downtime $downtime"

  printf "  %-3d  %s\n" "$num" "$type$downtime_str"
  [[ "$down_time" != "-" ]] && echo "       Down: $down_time"
  [[ "$up_time" != "-" ]]   && echo "       Up:   $up_time"
  echo "       Reason: $reason"
}

while IFS='|' read -r etype ts reason _location; do
  case "$etype" in
    TRIGGER)
      trigger_reason="$reason"
      ;;
    SHUTDOWN|CRASH)
      shutdown_ts="$ts"
      if [[ "$etype" == "CRASH" ]]; then
        restart_type="CRASH"
        trigger_reason="$reason"
      else
        restart_type="SIGTERM"
        [[ -z "$trigger_reason" ]] && trigger_reason="manual/systemd"
      fi
      ;;
    STARTUP)
      restart_num=$((restart_num + 1))
      local_startup=$(to_display_tz "$ts")
      if [[ -z "$shutdown_ts" ]]; then
        # first boot, not a restart — skip
        restart_num=$((restart_num - 1))
      else
        local_shutdown=$(to_display_tz "$shutdown_ts")
        downtime=$(calc_downtime "$shutdown_ts" "$ts")

        if $JSON_OUTPUT; then
          json_items+=("$(printf '{"num":%d,"shutdown":"%s","shutdown_utc":"%s","startup":"%s","startup_utc":"%s","type":"%s","reason":"%s","downtime":"%s"}' \
            "$restart_num" "$local_shutdown" "$shutdown_ts" "$local_startup" "$ts" "$restart_type" "$trigger_reason" "$downtime")")
        else
          print_entry "$restart_num" "$local_shutdown" "$local_startup" "$restart_type" "$trigger_reason" "$downtime"
        fi
      fi
      shutdown_ts=""
      trigger_reason=""
      restart_type=""
      ;;
  esac
done <<< "$events"

# unclosed shutdown
if [[ -n "$shutdown_ts" ]]; then
  restart_num=$((restart_num + 1))
  local_shutdown=$(to_display_tz "$shutdown_ts")
  if $JSON_OUTPUT; then
    json_items+=("$(printf '{"num":%d,"shutdown":"%s","shutdown_utc":"%s","startup":null,"type":"%s","reason":"%s","downtime":null}' \
      "$restart_num" "$local_shutdown" "$shutdown_ts" "$restart_type" "$trigger_reason")")
  else
    print_entry "$restart_num" "$local_shutdown" "NOT RECOVERED" "$restart_type" "$trigger_reason"
  fi
fi

# JSON output
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
echo "  Total: $restart_num restarts"

# systemd info
if command -v systemctl &>/dev/null; then
  for svc in openclaw-gateway openclaw; do
    active_since=$(systemctl --user show "$svc" --property=ActiveEnterTimestamp 2>/dev/null | cut -d= -f2 || true)
    pid=$(systemctl --user show "$svc" --property=MainPID 2>/dev/null | cut -d= -f2 || true)
    if [[ -n "$active_since" ]] && [[ "$pid" != "0" ]] && [[ -n "$pid" ]]; then
      echo "  Current process: PID $pid, started at $active_since"
      break
    fi
  done
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
