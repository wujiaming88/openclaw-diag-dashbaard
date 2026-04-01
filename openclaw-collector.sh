#!/bin/bash
# ============================================================
# OpenClaw Diagnostics Collector v1.0
# 采集本机 OpenClaw 诊断数据并上报到 Dashboard Server
#
# 用法:
#   bash openclaw-collector.sh           # 首次交互配置 + 前台运行
#   bash openclaw-collector.sh --daemon  # 后台运行
#   bash openclaw-collector.sh --once    # 采集一次后退出
#   bash openclaw-collector.sh --config  # 重新配置
#   bash openclaw-collector.sh --status  # 查看后台进程状态
#   bash openclaw-collector.sh --stop    # 停止后台进程
#
# 依赖: bash + python3 (标准库)
# ============================================================

set -uo pipefail

CONFIG_FILE="$HOME/.openclaw-collector.json"
PID_FILE="$HOME/.openclaw-collector.pid"
LOG_FILE="$HOME/.openclaw-collector.log"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ============================================================
# 配置管理
# ============================================================

load_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        return 1
    fi
    DASHBOARD_URL=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d.get('dashboard_url',''))" 2>/dev/null)
    API_KEY=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d.get('api_key',''))" 2>/dev/null)
    NODE_ID=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d.get('node_id',''))" 2>/dev/null)
    NODE_NAME=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d.get('node_name',''))" 2>/dev/null)
    INTERVAL=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(d.get('interval_seconds',300))" 2>/dev/null)
    if [ -z "$DASHBOARD_URL" ] || [ -z "$API_KEY" ]; then
        return 1
    fi
    return 0
}

save_config() {
    python3 -c "
import json
config = {
    'dashboard_url': '$DASHBOARD_URL',
    'api_key': '$API_KEY',
    'node_id': '$NODE_ID',
    'node_name': '$NODE_NAME',
    'interval_seconds': $INTERVAL
}
with open('$CONFIG_FILE', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
}

interactive_config() {
    echo ""
    echo -e "${BOLD}=== OpenClaw Diagnostics Collector ===${NC}"
    echo ""

    # 探测可采集数据
    echo "检测可采集数据..."
    detect_data
    echo ""

    # 数据授权声明
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}📋 数据上报授权声明${NC}"
    echo ""
    echo "  本工具将采集以上列出的 OpenClaw 诊断数据，并通过 HTTP"
    echo "  上报到您指定的 Dashboard Server。"
    echo ""
    echo "  上报数据包括："
    echo "    • 模型调用统计（调用次数、推理耗时、Token 用量）"
    echo "    • 工具执行统计（工具名、耗时、成功率）"
    echo "    • Gateway 重启历史"
    echo "    • Session 摘要信息"
    echo "    • Thinking 深度统计"
    echo "    • 系统事件和模型切换记录"
    echo ""
    echo "  数据${BOLD}不包含${NC}对话内容、个人信息或敏感凭证。"
    echo "  数据仅发送到您配置的 Dashboard 地址。"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    read -rp "是否同意上报以上数据？(y/N): " consent
    if [[ ! "$consent" =~ ^[Yy]$ ]]; then
        echo -e "${RED}已取消。未经授权不会上报任何数据。${NC}"
        exit 0
    fi
    echo ""
    echo -e "${GREEN}✅ 已获得授权，继续配置...${NC}"
    echo ""

    # 读取配置
    local default_host
    default_host=$(hostname)

    read -rp "请输入 Dashboard 上报地址: " DASHBOARD_URL
    if [ -z "$DASHBOARD_URL" ]; then
        echo -e "${RED}错误: 上报地址不能为空${NC}"
        exit 1
    fi

    read -rp "请输入 API Key: " API_KEY
    if [ -z "$API_KEY" ]; then
        echo -e "${RED}错误: API Key 不能为空${NC}"
        exit 1
    fi

    read -rp "请输入节点ID (默认: $default_host): " NODE_ID
    NODE_ID=${NODE_ID:-$default_host}

    read -rp "请输入节点名称 (默认: $default_host): " NODE_NAME
    NODE_NAME=${NODE_NAME:-$default_host}

    read -rp "请输入上报间隔(秒, 默认300): " INTERVAL
    INTERVAL=${INTERVAL:-300}

    save_config
    echo ""
    echo -e "${GREEN}配置已保存到 $CONFIG_FILE${NC}"
}

detect_data() {
    # 用 Python 探测可用数据
    python3 << 'DETECT_EOF'
import glob, json, os, sys

sessions_dirs = glob.glob(os.path.expanduser("~/.openclaw/agents/*/sessions"))
log_dir = "/tmp/openclaw"

# Count session files
total_sessions = 0
total_messages = 0
total_model_calls = 0
total_tool_calls = 0
total_thinking = 0
total_events = 0
total_snapshots = 0

for d in sessions_dirs:
    for pattern in ["*.jsonl", "*.jsonl.reset.*", "*.jsonl.deleted.*"]:
        for fpath in glob.glob(os.path.join(d, pattern)):
            total_sessions += 1
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except:
                            continue
                        otype = obj.get("type", "")
                        if otype == "message":
                            msg = obj.get("message", {})
                            role = msg.get("role", "")
                            if role == "assistant" and msg.get("model", "") != "delivery-mirror":
                                total_model_calls += 1
                                content = msg.get("content", [])
                                if isinstance(content, list):
                                    for item in content:
                                        if isinstance(item, dict):
                                            if item.get("type") == "toolCall":
                                                total_tool_calls += 1
                                            if item.get("type") == "thinking":
                                                total_thinking += 1
                        elif otype == "custom_message":
                            total_events += 1
                        elif otype == "custom" and obj.get("customType") == "model-snapshot":
                            total_snapshots += 1
            except:
                pass

# Count restarts from logs
total_restarts = 0
if os.path.isdir(log_dir):
    for lf in glob.glob(os.path.join(log_dir, "openclaw-*.log")):
        try:
            with open(lf, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if '"heartbeat: started"' in line or 'heartbeat: started' in line:
                        total_restarts += 1
        except:
            pass

checks = [
    ("推理统计 (model_calls)", total_model_calls),
    ("工具统计 (tool_stats)", total_tool_calls),
    ("Gateway 重启历史 (restarts)", total_restarts),
    ("Session 浏览 (sessions)", total_sessions),
    ("Thinking 深度 (thinking_stats)", total_thinking),
    ("系统事件 (system_events)", total_events),
    ("模型切换 (model_switches)", total_snapshots),
]

for label, count in checks:
    mark = "\033[32m[✓]\033[0m" if count > 0 else "\033[90m[✗]\033[0m"
    suffix = "— %d 条记录" % count if count > 0 else "— 无数据"
    print("  %s %s %s" % (mark, label, suffix))
DETECT_EOF
}

# ============================================================
# 数据采集 (内嵌 Python)
# ============================================================

collect_and_report() {
    python3 << 'COLLECT_EOF'
import glob, json, os, sys, re
from datetime import datetime, timedelta, timezone
from collections import OrderedDict

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:
    from urllib2 import Request, urlopen, HTTPError, URLError

# 读取配置
config_path = os.path.expanduser("~/.openclaw-collector.json")
with open(config_path, 'r') as f:
    config = json.load(f)

dashboard_url = config["dashboard_url"].rstrip("/")
api_key = config["api_key"]
node_id = config["node_id"]
node_name = config["node_name"]

# ========== 数据采集逻辑 ==========

sessions_dirs = glob.glob(os.path.expanduser("~/.openclaw/agents/*/sessions"))
log_dir = "/tmp/openclaw"

def normalize_usage(usage):
    if not isinstance(usage, dict):
        return {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0}
    inp = usage.get("input", 0) or usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
    out = usage.get("output", 0) or usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
    cr = usage.get("cacheRead", 0) or usage.get("cache_read", 0) or 0
    cw = usage.get("cacheWrite", 0) or usage.get("cache_write", 0) or 0
    total = usage.get("totalTokens", 0) or usage.get("total_tokens", 0) or 0
    if not total and (inp or out):
        total = inp + out + cr + cw
    result = {"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw, "totalTokens": total}
    if "cost" in usage:
        result["cost"] = usage["cost"]
    return result

def parse_time(ts):
    if not ts:
        return None
    ts = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        pass
    return None

# Collect model_calls, tool_stats, sessions, thinking_stats, events, snapshots
model_calls = []
tool_data = {}
tool_results = {}
system_events = []
model_snapshots = []
all_messages = []

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

for d in sessions_dirs:
    for pattern in ["*.jsonl", "*.jsonl.reset.*", "*.jsonl.deleted.*"]:
        for fpath in glob.glob(os.path.join(d, pattern)):
            fname = os.path.basename(fpath)
            fname_base = fname
            for sfx in [".deleted.", ".reset."]:
                idx = fname_base.find(sfx)
                if idx >= 0:
                    fname_base = fname_base[:idx]
            fname_base = fname_base.replace(".jsonl", "")
            fname_session_id = fname_base.split("-topic-")[0]
            internal_session_id = ""
            prev_top_timestamp = ""
            last_user_text = ""

            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
            except:
                continue

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except:
                    continue

                obj_type = obj.get("type", "")

                if obj_type == "session":
                    internal_session_id = obj.get("id", "")
                    continue

                if obj_type == "custom" and obj.get("customType") == "model-snapshot":
                    snap_data = obj.get("data", {})
                    model_snapshots.append({
                        "timestamp": obj.get("timestamp", ""),
                        "provider": snap_data.get("provider", ""),
                        "modelApi": snap_data.get("modelApi", ""),
                        "modelId": snap_data.get("modelId", ""),
                        "session_id": internal_session_id,
                    })
                    continue

                if obj_type == "custom_message":
                    system_events.append({
                        "timestamp": obj.get("timestamp", ""),
                        "event_type": obj.get("customType", ""),
                        "content": (obj.get("content", "") or "")[:500],
                        "session_id": internal_session_id,
                    })
                    continue

                if obj_type != "message":
                    continue
                msg = obj.get("message", {})
                if not isinstance(msg, dict):
                    continue

                if msg.get("role") == "toolResult":
                    tc_id = msg.get("toolCallId", "")
                    if tc_id:
                        is_error = msg.get("isError", False)
                        result_text = ""
                        rc = msg.get("content", [])
                        if isinstance(rc, list):
                            for item in rc:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    result_text += item.get("text", "")
                        elif isinstance(rc, str):
                            result_text = rc
                        tr_details = msg.get("details", {}) or {}
                        tool_results[tc_id] = {
                            "isError": is_error,
                            "details": tr_details if isinstance(tr_details, dict) else {},
                            "toolName": msg.get("toolName", ""),
                        }
                    tr_ts = obj.get("timestamp", "")
                    if tr_ts:
                        prev_top_timestamp = tr_ts
                    continue

                if msg.get("role") == "user":
                    uc = msg.get("content", [])
                    ut = ""
                    if isinstance(uc, list):
                        for item in uc:
                            if isinstance(item, dict) and item.get("type") == "text":
                                ut += item.get("text", "")
                    elif isinstance(uc, str):
                        ut = uc
                    if ut:
                        last_user_text = ut
                    u_ts = obj.get("timestamp", "")
                    if u_ts:
                        prev_top_timestamp = u_ts
                    continue

                if msg.get("role") != "assistant":
                    continue
                model = msg.get("model", "")
                if model == "delivery-mirror":
                    dm_ts = obj.get("timestamp", "")
                    if dm_ts:
                        prev_top_timestamp = dm_ts
                    continue

                usage = normalize_usage(msg.get("usage", {}))
                timestamp = obj.get("timestamp", "")
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue

                tool_call_list = []
                has_thinking = False
                thinking_chars = 0
                text_chars = 0

                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type", "")
                    if itype == "toolCall":
                        tc_id = item.get("id", "")
                        tc_name = item.get("name", "")
                        tool_call_list.append({"name": tc_name, "id": tc_id})
                        tool_data[tc_id] = {"tool": tc_name, "timestamp": timestamp}
                    elif itype == "thinking":
                        has_thinking = True
                        thinking_chars += len(item.get("thinking", ""))
                    elif itype == "text":
                        text_chars += len(item.get("text", ""))

                # Calculate inference_ms
                inference_ms = 0
                if prev_top_timestamp and timestamp:
                    try:
                        cur_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        prev_dt = datetime.fromisoformat(prev_top_timestamp.replace("Z", "+00:00"))
                        delta = (cur_dt - prev_dt).total_seconds() * 1000
                        if delta > 0:
                            inference_ms = round(delta)
                    except:
                        pass

                output_tokens = usage.get("output", 0)
                tokens_per_sec = round(output_tokens / (inference_ms / 1000), 1) if inference_ms > 0 and output_tokens > 0 else 0

                cost_data = usage.get("cost", {}) if isinstance(usage, dict) else {}
                thinking_ratio = round(thinking_chars / max(thinking_chars + text_chars, 1), 3)

                call_record = {
                    "timestamp": timestamp,
                    "inference_ms": inference_ms,
                    "tokens_per_sec": tokens_per_sec,
                    "session_id": internal_session_id,
                    "model": model,
                    "provider": msg.get("provider", ""),
                    "api": msg.get("api", ""),
                    "stop_reason": msg.get("stopReason", ""),
                    "thinking_chars": thinking_chars,
                    "thinking_ratio": thinking_ratio,
                    "usage": {
                        "input": usage.get("input", 0),
                        "output": usage.get("output", 0),
                        "cacheRead": usage.get("cacheRead", 0),
                        "cacheWrite": usage.get("cacheWrite", 0),
                        "totalTokens": usage.get("totalTokens", 0),
                    },
                    "cost": {
                        "input": cost_data.get("input", 0) if isinstance(cost_data, dict) else 0,
                        "output": cost_data.get("output", 0) if isinstance(cost_data, dict) else 0,
                        "total": cost_data.get("total", 0) if isinstance(cost_data, dict) else 0,
                    },
                    "content_summary": {
                        "has_thinking": has_thinking,
                        "tool_calls": tool_call_list,
                    },
                }
                model_calls.append(call_record)
                if timestamp:
                    prev_top_timestamp = timestamp

# Sort model_calls by timestamp descending
model_calls.sort(key=lambda c: c.get("timestamp", ""), reverse=True)

# ========== Build summary ==========
session_total_inference_ms = 0
session_inference_count = 0
session_tps_values = []
total_model_cost = 0
session_total_input = 0
session_total_output = 0
session_total_cache_read = 0
session_total_cache_write = 0
session_model_call_count = 0
thinking_total_chars = 0
thinking_calls_count = 0
thinking_ratio_sum = 0

for mc in model_calls:
    session_model_call_count += 1
    inf_ms = mc.get("inference_ms", 0)
    if inf_ms > 0:
        session_total_inference_ms += inf_ms
        session_inference_count += 1
        tps = mc.get("tokens_per_sec", 0)
        if tps > 0:
            session_tps_values.append(tps)
    cost = mc.get("cost", {})
    if isinstance(cost, dict):
        total_model_cost += cost.get("total", 0)
    u = mc.get("usage", {})
    session_total_input += u.get("input", 0)
    session_total_output += u.get("output", 0)
    session_total_cache_read += u.get("cacheRead", 0)
    session_total_cache_write += u.get("cacheWrite", 0)
    tc = mc.get("thinking_chars", 0)
    if tc > 0:
        thinking_total_chars += tc
        thinking_calls_count += 1
        thinking_ratio_sum += mc.get("thinking_ratio", 0)

total_cache = session_total_cache_read + session_total_cache_write + session_total_input
cache_hit_ratio = round(session_total_cache_read / total_cache * 100, 1) if total_cache > 0 else 0

# Tool stats
tool_stats_map = {}
total_tool_calls = 0
total_tool_errors = 0
total_tool_duration_ms = 0
for tc_id, td in tool_data.items():
    tr = tool_results.get(tc_id, {})
    tool_name = tr.get("toolName", "") or td.get("tool", "") or "unknown"
    if tool_name not in tool_stats_map:
        tool_stats_map[tool_name] = {"count": 0, "total_ms": 0, "error_count": 0}
    tool_stats_map[tool_name]["count"] += 1
    total_tool_calls += 1
    details = tr.get("details", {})
    dur_ms = details.get("durationMs", 0) or details.get("tookMs", 0)
    if isinstance(dur_ms, (int, float)) and dur_ms > 0:
        tool_stats_map[tool_name]["total_ms"] += dur_ms
        total_tool_duration_ms += dur_ms
    is_err = tr.get("isError", False)
    exit_code = details.get("exitCode")
    if is_err or (exit_code is not None and exit_code != 0):
        tool_stats_map[tool_name]["error_count"] += 1
        total_tool_errors += 1

tool_stats_by_tool = []
for name, stats in sorted(tool_stats_map.items(), key=lambda x: x[1]["count"], reverse=True):
    avg_ms = round(stats["total_ms"] / stats["count"]) if stats["count"] > 0 else 0
    error_rate = round(stats["error_count"] / stats["count"], 3) if stats["count"] > 0 else 0
    tool_stats_by_tool.append({
        "name": name, "count": stats["count"], "avg_ms": avg_ms,
        "total_ms": round(stats["total_ms"]), "error_count": stats["error_count"],
        "error_rate": error_rate,
    })

# Sessions list
sessions_map = {}
for mc in model_calls:
    sid = mc.get("session_id", "")
    if not sid:
        continue
    if sid not in sessions_map:
        sessions_map[sid] = {"session_id": sid, "message_count": 0, "model": "", "total_tokens": 0}
    sessions_map[sid]["message_count"] += 1
    if not sessions_map[sid]["model"]:
        sessions_map[sid]["model"] = mc.get("model", "")
    u = mc.get("usage", {})
    sessions_map[sid]["total_tokens"] += u.get("totalTokens", 0) or (u.get("input", 0) + u.get("output", 0))

sessions_list = sorted(sessions_map.values(), key=lambda s: s.get("session_id", ""))

# Gateway restarts
restarts = []
if os.path.isdir(log_dir):
    log_files = sorted(glob.glob(os.path.join(log_dir, "openclaw-*.log")))
    events_list = []
    for lf in log_files:
        try:
            with open(lf, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except:
                        continue
                    msg_val = obj.get("1", "")
                    if not isinstance(msg_val, str):
                        continue
                    ts_str = obj.get("date", "") or obj.get("time", "")
                    if not ts_str:
                        continue
                    if "received SIGTERM; shutting down" in msg_val:
                        events_list.append(("SHUTDOWN", ts_str, "SIGTERM"))
                    elif "heartbeat: started" in msg_val:
                        events_list.append(("STARTUP", ts_str, "heartbeat"))
        except:
            pass
    events_list.sort(key=lambda e: e[1])
    shutdown_ts = None
    for etype, ts, detail in events_list:
        if etype == "SHUTDOWN":
            shutdown_ts = ts
        elif etype == "STARTUP" and shutdown_ts:
            restarts.append({"shutdown_utc": shutdown_ts, "startup_utc": ts, "type": "SIGTERM"})
            shutdown_ts = None

summary = {
    "date": today,
    "session_model_call_count": session_model_call_count,
    "session_total_inference_ms": session_total_inference_ms,
    "session_avg_inference_ms": round(session_total_inference_ms / session_inference_count) if session_inference_count > 0 else 0,
    "session_avg_tokens_per_sec": round(sum(session_tps_values) / len(session_tps_values), 1) if session_tps_values else 0,
    "session_inference_count": session_inference_count,
    "total_tokens_output": session_total_output,
    "total_tokens_input": session_total_input,
    "total_cache_read": session_total_cache_read,
    "total_cache_write": session_total_cache_write,
    "cache_hit_ratio": cache_hit_ratio,
    "total_model_cost": round(total_model_cost, 6),
    "restart_count": len(restarts),
    "tool_call_count": total_tool_calls,
    "tool_error_count": total_tool_errors,
    "tool_avg_duration_ms": round(total_tool_duration_ms / total_tool_calls) if total_tool_calls > 0 else 0,
    "thinking_total_chars": thinking_total_chars,
    "thinking_avg_chars": round(thinking_total_chars / thinking_calls_count) if thinking_calls_count > 0 else 0,
    "thinking_calls_count": thinking_calls_count,
    "thinking_avg_ratio": round(thinking_ratio_sum / thinking_calls_count, 3) if thinking_calls_count > 0 else 0,
}

# Build payload
payload = {
    "summary": summary,
    "model_calls": model_calls[:500],  # Limit to latest 500
    "tool_stats": {
        "total_calls": total_tool_calls,
        "total_errors": total_tool_errors,
        "total_duration_ms": round(total_tool_duration_ms),
        "avg_duration_ms": round(total_tool_duration_ms / total_tool_calls) if total_tool_calls > 0 else 0,
        "by_tool": tool_stats_by_tool,
    },
    "restarts": restarts,
    "sessions": sessions_list,
    "thinking_stats": {
        "total_chars": thinking_total_chars,
        "calls_count": thinking_calls_count,
        "avg_ratio": round(thinking_ratio_sum / thinking_calls_count, 3) if thinking_calls_count > 0 else 0,
    },
    "system_events": system_events[-100:],
    "model_switches": model_snapshots[-50:],
}

# Build report
report = {
    "node_id": node_id,
    "node_name": node_name,
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "data_type": "full_report",
    "payload": payload,
}

# Send report
report_json = json.dumps(report, ensure_ascii=False)
report_bytes = report_json.encode("utf-8")

url = dashboard_url + "/api/report"
req = Request(url, data=report_bytes, method="POST")
req.add_header("Content-Type", "application/json; charset=utf-8")
req.add_header("Authorization", "Bearer " + api_key)

try:
    resp = urlopen(req, timeout=30)
    body = resp.read().decode("utf-8")
    result = json.loads(body)
    if result.get("ok"):
        print("\033[32m✅ 上报成功！\033[0m (model_calls=%d, tools=%d, sessions=%d)" % (
            len(model_calls[:500]), total_tool_calls, len(sessions_list)), file=sys.stderr)
        sys.exit(0)
    else:
        print("\033[31m❌ 上报失败: %s\033[0m" % result.get("error", "unknown"), file=sys.stderr)
        sys.exit(1)
except HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print("\033[31m❌ HTTP 错误 %d: %s\033[0m" % (e.code, body[:200]), file=sys.stderr)
    sys.exit(1)
except URLError as e:
    print("\033[31m❌ 连接失败: %s\033[0m" % str(e.reason), file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print("\033[31m❌ 未知错误: %s\033[0m" % str(e), file=sys.stderr)
    sys.exit(1)
COLLECT_EOF
}

# ============================================================
# 运行模式
# ============================================================

run_foreground() {
    echo -e "${CYAN}开始采集上报...${NC}"
    while true; do
        collect_and_report || true
        echo -e "${CYAN}下次采集: ${INTERVAL}s 后${NC}" >&2
        sleep "$INTERVAL"
    done
}

run_daemon() {
    echo -e "${CYAN}启动后台采集...${NC}"
    nohup bash "$0" --_loop >> "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    disown "$pid" 2>/dev/null
    echo -e "${GREEN}后台运行中 (PID: $pid)${NC}"
    echo -e "日志: $LOG_FILE"
    echo -e "PID 文件: $PID_FILE"
}

run_once() {
    echo -e "${CYAN}执行单次采集上报...${NC}"
    collect_and_report
}

check_status() {
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${YELLOW}没有运行中的后台采集进程${NC}"
        return 1
    fi
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${GREEN}采集进程运行中 (PID: $pid)${NC}"
        if [ -f "$CONFIG_FILE" ]; then
            echo "  配置: $CONFIG_FILE"
            load_config && echo "  节点: $NODE_ID ($NODE_NAME)" && echo "  目标: $DASHBOARD_URL" && echo "  间隔: ${INTERVAL}s"
        fi
        return 0
    else
        echo -e "${YELLOW}采集进程已停止 (PID: $pid)${NC}"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${YELLOW}没有运行中的后台采集进程${NC}"
        return 0
    fi
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        echo -e "${GREEN}已停止采集进程 (PID: $pid)${NC}"
    else
        echo -e "${YELLOW}进程已停止${NC}"
    fi
    rm -f "$PID_FILE"
}

# ============================================================
# 主逻辑
# ============================================================

case "${1:-}" in
    --daemon)
        if ! load_config; then
            interactive_config
        fi
        load_config
        run_daemon
        ;;
    --once)
        if ! load_config; then
            interactive_config
        fi
        load_config
        run_once
        ;;
    --config)
        interactive_config
        ;;
    --status)
        check_status
        ;;
    --stop)
        stop_daemon
        ;;
    --_loop)
        # 内部循环模式 (daemon 使用)
        load_config
        while true; do
            collect_and_report || true
            sleep "$INTERVAL"
        done
        ;;
    --help|-h)
        echo "用法: bash openclaw-collector.sh [选项]"
        echo ""
        echo "选项:"
        echo "  (无参数)    首次运行交互配置，然后前台采集"
        echo "  --daemon    后台运行"
        echo "  --once      采集一次上报后退出"
        echo "  --config    重新配置"
        echo "  --status    查看后台进程状态"
        echo "  --stop      停止后台进程"
        echo "  --help      显示此帮助"
        ;;
    *)
        # 默认：首次运行交互配置，然后前台
        if ! load_config; then
            interactive_config
            load_config
        fi
        echo ""
        echo -e "${CYAN}开始首次采集上报...${NC}"
        collect_and_report
        echo ""
        echo -e "前台运行中，按 Ctrl+C 退出。"
        echo -e "使用 ${BOLD}--daemon${NC} 参数可后台运行。"
        echo ""
        run_foreground
        ;;
esac
