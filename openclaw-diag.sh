#!/bin/bash
# OpenClaw 诊断日志解析工具 v2.5
# 用法:
#   ./openclaw-diag.sh              # 解析今天的日志
#   ./openclaw-diag.sh 2026-03-11   # 解析指定日期
#   ./openclaw-diag.sh -f           # 实时跟踪模式
#   ./openclaw-diag.sh -l 5         # 只看最近5个run
#   ./openclaw-diag.sh -s           # 只看摘要统计
#
# 功能:
#   - 解析 OpenClaw 诊断日志，展示 Run 时间线
#   - 从 session 文件提取工具调用参数和 Token 用量
#   - 计算推理分段耗时和 Token 速率 (inference_ms, tokens_per_sec)
#   - 实时跟踪模式 (-f) 流式输出
#   - 摘要模式 (-s) 快速统计
#
# 注: 完整探测功能（health/gateway/doctor/config/models）请使用:
#   python3 openclaw-dashboard.py --cli [--probe <name>] [--json]
#
# 数据源:
#   - 日志文件: /tmp/openclaw/openclaw-YYYY-MM-DD.log
#   - 会话文件: ~/.openclaw/agents/*/sessions/*.jsonl

set -euo pipefail

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
BOLD='\033[1m'
NC='\033[0m'

# 默认参数
DATE=$(date +%F)
FOLLOW=false
LAST_N=0
SUMMARY_ONLY=false

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow)  FOLLOW=true; shift ;;
        -l|--last)    LAST_N=$2; shift 2 ;;
        -s|--summary) SUMMARY_ONLY=true; shift ;;
        -h|--help)
            echo "OpenClaw 诊断日志解析工具"
            echo ""
            echo "用法: $0 [选项] [日期]"
            echo ""
            echo "选项:"
            echo "  -f, --follow     实时跟踪模式"
            echo "  -l N, --last N   只显示最近 N 个 run"
            echo "  -s, --summary    只显示摘要统计"
            echo "  -h, --help       帮助"
            echo ""
            echo "示例:"
            echo "  $0                  # 解析今天的日志"
            echo "  $0 2026-03-11       # 解析指定日期"
            echo "  $0 -f               # 实时跟踪"
            echo "  $0 -l 3             # 最近3个run"
            echo "  $0 -s               # 摘要统计"
            exit 0
            ;;
        *)
            if [[ $1 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
                DATE=$1
            else
                echo "未知参数: $1 (用 -h 查看帮助)"
                exit 1
            fi
            shift
            ;;
    esac
done

LOG="/tmp/openclaw/openclaw-${DATE}.log"

# 自动查找会话文件目录
SESSIONS_DIR=""
for d in \
    "$HOME/.openclaw/agents/main/sessions" \
    "$HOME/.openclaw/agents/*/sessions" \
    "/root/.openclaw/agents/main/sessions" \
    "/root/.openclaw/agents/*/sessions"; do
    if [ -d "$d" ] 2>/dev/null; then
        SESSIONS_DIR="$d"
        break
    fi
done
# 如果通配符没展开，尝试 find
if [ -z "$SESSIONS_DIR" ] || [ ! -d "$SESSIONS_DIR" ]; then
    SESSIONS_DIR=$(find "${HOME}/.openclaw/agents" -type d -name "sessions" 2>/dev/null | head -1)
fi

if [ "$FOLLOW" = true ]; then
    echo -e "${BOLD}[实时跟踪] Ctrl+C 退出${NC}"
    echo -e "${GRAY}日志文件: $LOG${NC}"
    echo ""
    tail -f "$LOG" 2>/dev/null | python3 -c "
import json, sys
from datetime import datetime

prev_time = None
for line in sys.stdin:
    try:
        obj = json.loads(line.strip())
        t = obj.get('time', '')
        parts = [obj.get(str(i), '') for i in range(3) if isinstance(obj.get(str(i), ''), str)]
        msg = ' '.join(parts)
        level = obj.get('_meta', {}).get('logLevelName', '')

        label = None
        detail = ''

        if 'embedded run start:' in msg:
            label = '[RUN-START] 开始处理请求'
            if 'model=' in msg:
                detail = 'model=' + msg.split('model=')[1].split(' ')[0]
        elif 'run agent start' in msg:
            label = '[MODEL-SEND] 请求已发送给模型, 等待推理'
        elif 'run agent end' in msg:
            label = '[MODEL-DONE] 模型推理完成'
        elif 'tool start' in msg:
            tool = msg.split('tool=')[1].split(' ')[0] if 'tool=' in msg else '?'
            label = f'[TOOL-START] 开始执行工具: {tool}'
        elif 'tool end' in msg:
            tool = msg.split('tool=')[1].split(' ')[0] if 'tool=' in msg else '?'
            label = f'[TOOL-END]   工具执行完成: {tool}'
        elif 'sendMessage' in msg:
            label = '[MSG-SEND]  消息发送到通道'
            detail = msg.split('sendMessage')[1][:60].strip() if 'sendMessage' in msg else ''
        elif 'lane dequeue' in msg:
            label = '[DEQUEUE]   消息从队列取出'
            if 'waitMs=' in msg:
                detail = '排队等待 ' + msg.split('waitMs=')[1].split(' ')[0] + 'ms'
        elif 'pre-prompt' in msg:
            label = '[PROMPT]    构建提示词'
            if 'messages=' in msg:
                detail = '历史消息 ' + msg.split('messages=')[1].split(' ')[0] + ' 条'
        elif level == 'ERROR':
            label = '[ERROR]     错误'
            detail = msg[:80]
        elif level == 'WARN':
            label = '[WARN]      警告'
            detail = msg[:80]

        if not label:
            continue

        ts = t[11:23]
        try:
            curr = datetime.fromisoformat(t.replace('+00:00', ''))
            if prev_time:
                delta_ms = (curr - prev_time).total_seconds() * 1000
                if delta_ms >= 1000:
                    delta_str = f'+{delta_ms/1000:.1f}s'
                else:
                    delta_str = f'+{delta_ms:.0f}ms'
            else:
                delta_str = '---'
            prev_time = curr
        except:
            delta_str = '?'

        detail_str = f'  {detail}' if detail else ''
        print(f'{ts} {delta_str:>8} {label}{detail_str}', flush=True)
    except:
        pass
"
    exit 0
fi

# 非实时模式
if [ ! -f "$LOG" ]; then
    echo -e "${RED}[错误] 日志文件不存在: $LOG${NC}"
    echo ""
    echo "可用的日志文件:"
    ls -lt /tmp/openclaw/openclaw-*.log 2>/dev/null | head -5
    exit 1
fi

echo -e "${BOLD}[OpenClaw 诊断报告]${NC}"
echo -e "${GRAY}日志文件: $LOG${NC}"
echo -e "${GRAY}会话目录: ${SESSIONS_DIR:-未找到}${NC}"
echo -e "${GRAY}日期: $DATE${NC}"
echo ""

export DIAG_LOG="$LOG"
export DIAG_DATE="$DATE"
export DIAG_LAST_N="$LAST_N"
export DIAG_SUMMARY="$SUMMARY_ONLY"
export DIAG_SESSIONS_DIR="${SESSIONS_DIR:-}"

python3 << 'PYEOF'
import json, sys, os, glob
from datetime import datetime
from collections import defaultdict

LOG = os.environ.get("DIAG_LOG", "/tmp/openclaw/openclaw.log")
LAST_N = int(os.environ.get("DIAG_LAST_N", "0"))
SUMMARY_ONLY = os.environ.get("DIAG_SUMMARY", "false") == "true"
SESSIONS_DIR = os.environ.get("DIAG_SESSIONS_DIR", "")

# ============================================================
# 1. 从会话文件中提取工具调用参数
# ============================================================
# toolCallId -> {name, summary, workdir}
tool_params = {}

# 每次推理的 token 用量: toolCallId -> usage dict
# 一个 assistant 消息可能包含多个 toolCall, 它们共享同一个 usage
# 我们用第一个 toolCallId 作为 key, 也建立反向映射
inference_usage = {}  # toolCallId -> {input, output, cacheRead, cacheWrite, totalTokens, cost, all_tool_ids}
# 没有 toolCall 的推理(纯文本回复)按时间戳索引
text_reply_usage = []  # [(timestamp, usage)]

def extract_tool_summary(name, args):
    """从工具参数中提取可读摘要"""
    cmd = args.get("command", "")
    path = args.get("path", "") or args.get("file_path", "")
    workdir = args.get("workdir", "")
    query = args.get("query", "")
    url = args.get("url", "")
    action = args.get("action", "")
    task = args.get("task", "")
    text = args.get("text", "")
    message = args.get("message", "")
    old_str = args.get("old_string", "") or args.get("oldText", "")
    new_str = args.get("new_string", "") or args.get("newText", "")

    parts = []
    if name == "exec":
        # 取命令第一行
        first_line = cmd.split("\n")[0][:90] if cmd else ""
        parts.append(first_line)
        if workdir:
            parts.append(f"cwd={workdir}")
    elif name == "read":
        parts.append(path)
    elif name == "write":
        parts.append(path)
    elif name == "edit":
        parts.append(path)
        if old_str:
            preview = old_str[:40].replace("\n", " ")
            parts.append(f'替换: "{preview}..."')
    elif name == "web_search":
        parts.append(f'搜索: "{query}"')
    elif name == "web_fetch":
        parts.append(url)
    elif name == "browser":
        parts.append(action)
        if url:
            parts.append(url)
    elif name == "message":
        parts.append(action)
        if message:
            parts.append(message[:50])
    elif name == "sessions_spawn":
        agent = args.get("agentId", "?")
        parts.append(f"agent={agent}")
        if task:
            parts.append(task[:50])
    elif name == "memory_search":
        parts.append(f'查询: "{query}"')
    elif name == "memory_get":
        parts.append(path)
    elif name == "session_status":
        parts.append("查看状态")
    elif name == "process":
        parts.append(action)
        sid = args.get("sessionId", "")
        if sid:
            parts.append(f"session={sid}")
    elif name == "tts":
        parts.append(text[:50] if text else "")
    else:
        # 通用: 取前几个有值的参数
        for k, v in list(args.items())[:3]:
            if v and isinstance(v, str):
                parts.append(f"{k}={v[:40]}")

    return "  ".join(filter(None, parts))

if SESSIONS_DIR and os.path.isdir(SESSIONS_DIR):
    for sf in glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl")):
        try:
            with open(sf) as f:
                for line in f:
                    try:
                        obj = json.loads(line.strip())
                        if obj.get("type") != "message":
                            continue
                        msg = obj.get("message", {})
                        content = msg.get("content", [])
                        if not isinstance(content, list):
                            continue

                        role = msg.get("role", "")
                        usage = msg.get("usage", {})
                        model = msg.get("model", "")
                        timestamp = obj.get("timestamp", "")

                        # 收集 toolCall 参数
                        tool_ids_in_msg = []
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "toolCall":
                                tid = block.get("id", "")
                                name = block.get("name", "?")
                                args_raw = block.get("arguments", "{}")
                                try:
                                    args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw if isinstance(args_raw, dict) else {})
                                except:
                                    args = {}
                                workdir = args.get("workdir", "")
                                summary = extract_tool_summary(name, args)
                                tool_params[tid] = {
                                    "name": name,
                                    "summary": summary,
                                    "workdir": workdir,
                                }
                                tool_ids_in_msg.append(tid)

                        # 收集每次推理的 usage (仅 assistant, 非 delivery-mirror)
                        if role == "assistant" and usage and model != "delivery-mirror":
                            usage_record = {
                                "input": usage.get("input", 0),
                                "output": usage.get("output", 0),
                                "cacheRead": usage.get("cacheRead", 0),
                                "cacheWrite": usage.get("cacheWrite", 0),
                                "totalTokens": usage.get("totalTokens", 0),
                                "cost": usage.get("cost", {}),
                                "timestamp": timestamp,
                                "tool_ids": tool_ids_in_msg,
                            }
                            if tool_ids_in_msg:
                                # 用第一个 toolCallId 作为 key, 每个 id 都指向同一条
                                for tid in tool_ids_in_msg:
                                    inference_usage[tid] = usage_record
                            else:
                                # 纯文本回复, 按时间戳记录
                                text_reply_usage.append((timestamp, usage_record))

                    except:
                        pass
        except:
            pass

# ============================================================
# 2. 解析日志事件
# ============================================================
events = []
with open(LOG) as f:
    for line in f:
        try:
            obj = json.loads(line.strip())
            t = obj.get("time", "")
            parts = [obj.get(str(i), "") for i in range(3) if isinstance(obj.get(str(i), ""), str)]
            msg = " ".join(parts)
            level = obj.get("_meta", {}).get("logLevelName", "")
            events.append((t, level, msg))
        except:
            pass

# ============================================================
# 3. 提取 run 信息
# ============================================================
runs = {}
for t, level, msg in events:
    run_id = None
    if "runId=" in msg:
        run_id = msg.split("runId=")[1].split(" ")[0]

    if not run_id:
        continue

    if run_id not in runs:
        runs[run_id] = {
            "start": None, "end": None,
            "events": [], "tools": [],
            "model": "", "channel": "",
            "first_token": None,
            "prompt_messages": 0,
            "session_key": "",
        }

    r = runs[run_id]
    r["events"].append((t, msg))

    if "embedded run start:" in msg:
        r["start"] = t
        if "model=" in msg:
            r["model"] = msg.split("model=")[1].split(" ")[0]
        if "messageChannel=" in msg:
            r["channel"] = msg.split("messageChannel=")[1].split(" ")[0]
        elif "channel=" in msg.lower():
            r["channel"] = msg.lower().split("channel=")[1].split(" ")[0]
        if "sessionId=" in msg:
            pass  # session UUID, not key

    elif "run agent start" in msg:
        r["first_token"] = t

    elif "run agent end" in msg or "run end" in msg:
        r["end"] = t

    elif "tool start" in msg:
        tool_name = msg.split("tool=")[1].split(" ")[0] if "tool=" in msg else "?"
        tool_id = msg.split("toolCallId=")[1].split(" ")[0] if "toolCallId=" in msg else ""
        r["tools"].append({"name": tool_name, "start": t, "end": None, "id": tool_id})

    elif "tool end" in msg:
        tool_id = msg.split("toolCallId=")[1].split(" ")[0] if "toolCallId=" in msg else ""
        for tool in reversed(r["tools"]):
            if tool["id"] == tool_id or (not tool["end"] and tool["id"] == ""):
                tool["end"] = t
                break

    elif "pre-prompt" in msg and "messages=" in msg:
        try:
            r["prompt_messages"] = int(msg.split("messages=")[1].split(" ")[0])
        except:
            pass
        if "sessionKey=" in msg:
            r["session_key"] = msg.split("sessionKey=")[1].split(" ")[0]

# 收集 sendMessage 事件
sends = [(t, msg) for t, level, msg in events if "sendMessage" in msg]

# 收集错误
errors = [(t, msg) for t, level, msg in events if level == "ERROR"]

def parse_time(t):
    try:
        return datetime.fromisoformat(t.replace("+00:00", ""))
    except:
        return None

def fmt_duration(ms):
    if ms >= 60000:
        return f"{ms/60000:.1f}min"
    elif ms >= 1000:
        return f"{ms/1000:.1f}s"
    else:
        return f"{ms:.0f}ms"

def bar(ms, max_ms=30000, width=20):
    filled = min(int(ms / max_ms * width), width)
    return "#" * filled + "." * (width - filled)

# 按时间排序
sorted_runs = sorted(runs.items(), key=lambda x: x[1]["start"] or "")
if LAST_N > 0:
    sorted_runs = sorted_runs[-LAST_N:]

# ============================================================
# 4. 摘要统计
# ============================================================
print("=" * 68)
print(f"{'[摘要统计]':^64}")
print("=" * 68)

total_runs = len(runs)
total_tools = sum(len(r["tools"]) for r in runs.values())
total_errors = len(errors)
total_sends = len(sends)

def calc_inference_segments(r):
    """精确计算每段推理时间，合并批量工具调用 (gap < 500ms)。
    返回: (segments_list, total_inference_ms, total_tool_ms)
    segments_list: [(label, ms, tool_indices)]  tool_indices = list of int
    """
    BATCH_GAP_MS = 500
    segments = []  # [(label, ms, tool_indices)]
    total_tool_ms = 0

    if not r["first_token"]:
        return segments, 0, 0

    ft = parse_time(r["first_token"])
    if not ft:
        return segments, 0, 0

    tools = r["tools"]

    if not tools:
        if r["end"]:
            run_end = parse_time(r["end"])
            if run_end:
                total_ms = (run_end - ft).total_seconds() * 1000
                segments.append(("推理#1(生成回复)", total_ms, []))
        return segments, sum(ms for _, ms, _ in segments), 0

    # Parse all tool times
    parsed = []  # [(start_dt, end_dt, index)]
    for idx, tool in enumerate(tools):
        ts = parse_time(tool["start"])
        te = parse_time(tool["end"]) if tool["end"] else None
        if ts:
            parsed.append((ts, te, idx))

    if not parsed:
        return segments, 0, 0

    # Group into batches by gap < 500ms
    batches = []  # each batch = [(start_dt, end_dt, tool_idx), ...]
    current_batch = [parsed[0]]
    for i in range(1, len(parsed)):
        prev_end = current_batch[-1][1]  # end of previous tool
        cur_start = parsed[i][0]
        if prev_end and cur_start and (cur_start - prev_end).total_seconds() * 1000 < BATCH_GAP_MS:
            current_batch.append(parsed[i])
        else:
            batches.append(current_batch)
            current_batch = [parsed[i]]
    batches.append(current_batch)

    # Calculate tool_ms
    for batch in batches:
        for ts, te, idx in batch:
            if ts and te:
                total_tool_ms += (te - ts).total_seconds() * 1000

    # Build inference segments
    prev_end = ft  # starts at agent_start (first_token)

    for b_idx, batch in enumerate(batches):
        batch_start = batch[0][0]  # first tool start in batch
        tool_indices = [item[2] for item in batch]

        # Inference before this batch
        infer_ms = (batch_start - prev_end).total_seconds() * 1000
        if infer_ms > 0:
            segments.append((f"推理#{len(segments)+1}", infer_ms, tool_indices))

        # Update prev_end to end of last tool in batch
        last_end = batch[-1][1]
        if last_end:
            prev_end = last_end
        else:
            prev_end = batch[-1][0]

    # Final segment: last batch end -> run_end
    if r["end"] and prev_end:
        run_end = parse_time(r["end"])
        if run_end:
            last_ms = (run_end - prev_end).total_seconds() * 1000
            if last_ms > 0:
                segments.append((f"推理#{len(segments)+1}(生成回复)", last_ms, []))

    total_infer = sum(ms for _, ms, _ in segments)
    return segments, total_infer, total_tool_ms

run_durations = []
model_times = []
tool_times = []

for run_id, r in runs.items():
    if r["start"] and r["end"]:
        s = parse_time(r["start"])
        e = parse_time(r["end"])
        if s and e:
            run_durations.append((e - s).total_seconds() * 1000)

    # 精确计算推理总时间
    segments, total_infer, total_tool = calc_inference_segments(r)
    if total_infer > 0:
        model_times.append(total_infer)

    for tool in r["tools"]:
        if tool["start"] and tool["end"]:
            ts = parse_time(tool["start"])
            te = parse_time(tool["end"])
            if ts and te:
                tool_times.append((te - ts).total_seconds() * 1000)

print(f"  Run 总数:        {total_runs}")
print(f"  工具调用总数:    {total_tools}")
print(f"  消息发送总数:    {total_sends}")
print(f"  错误总数:        {total_errors}")
if tool_params:
    print(f"  工具参数已加载:  {len(tool_params)} 条 (来自会话文件)")
else:
    print(f"  工具参数:        未加载 (会话目录未找到或为空)")
print()

if run_durations:
    avg_run = sum(run_durations) / len(run_durations)
    max_run = max(run_durations)
    min_run = min(run_durations)
    print(f"  Run 耗时:    平均 {fmt_duration(avg_run)}  最短 {fmt_duration(min_run)}  最长 {fmt_duration(max_run)}")

if model_times:
    avg_model = sum(model_times) / len(model_times)
    max_model = max(model_times)
    print(f"  模型推理:    平均 {fmt_duration(avg_model)}  最长 {fmt_duration(max_model)}")

if tool_times:
    avg_tool = sum(tool_times) / len(tool_times)
    max_tool = max(tool_times)
    print(f"  工具执行:    平均 {fmt_duration(avg_tool)}  最长 {fmt_duration(max_tool)}")

# 工具使用统计
if total_tools > 0:
    tool_counts = defaultdict(int)
    tool_dur = defaultdict(list)
    for r in runs.values():
        for tool in r["tools"]:
            tool_counts[tool["name"]] += 1
            if tool["start"] and tool["end"]:
                ts = parse_time(tool["start"])
                te = parse_time(tool["end"])
                if ts and te:
                    tool_dur[tool["name"]].append((te - ts).total_seconds() * 1000)

    print()
    print("  工具使用排行:")
    for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        avg = sum(tool_dur.get(name, [0])) / max(len(tool_dur.get(name, [1])), 1)
        print(f"    {name:<20} {count:>3}次  平均耗时 {fmt_duration(avg)}")

if SUMMARY_ONLY:
    if errors:
        print()
        print("  最近错误:")
        for t, msg in errors[-5:]:
            print(f"    {t[11:19]} [ERROR] {msg[:80]}")
    sys.exit(0)

# ============================================================
# 5. 每个 Run 详情
# ============================================================
print()
print("=" * 68)
print(f"{'[Run 详情]':^64}")
print("=" * 68)

for i, (run_id, r) in enumerate(sorted_runs):
    if not r["start"]:
        continue

    start_time = parse_time(r["start"])
    end_time = parse_time(r["end"]) if r["end"] else None
    total_ms = (end_time - start_time).total_seconds() * 1000 if end_time and start_time else None

    print()
    if r["end"]:
        status = "[完成]"
    else:
        status = "[进行中]"
    total_str = fmt_duration(total_ms) if total_ms else "进行中"
    print(f"  Run #{i+1}  {status}  总耗时: {total_str}")
    print(f"  {'-' * 62}")
    print(f"  Run ID:     {run_id}")
    print(f"  模型:       {r['model']}")
    print(f"  渠道:       {r['channel']}")
    if r["session_key"]:
        print(f"  会话:       {r['session_key']}")
    if r["prompt_messages"]:
        print(f"  历史消息数: {r['prompt_messages']}")
    print(f"  开始时间:   {r['start'][11:23]}", end="")
    if r["end"]:
        print(f"  结束时间: {r['end'][11:23]}")
    else:
        print()

    # 时间线
    print()
    print(f"  {'时间':>12} {'间隔':>9}  {'步骤说明'}")
    print(f"  {'─'*12} {'─'*9}  {'─'*42}")

    timeline = []

    timeline.append((r["start"], "[RUN-START]  开始处理请求"))

    for t, msg in r["events"]:
        if "pre-prompt" in msg:
            detail = ""
            if "messages=" in msg:
                detail = f", 包含 {msg.split('messages=')[1].split(' ')[0]} 条历史消息"
            timeline.append((t, f"[PROMPT]     构建提示词{detail}"))
            break

    if r["first_token"]:
        timeline.append((r["first_token"], "[MODEL-SEND] 请求已发送给模型, 等待推理"))

    for tool in r["tools"]:
        tid = tool["id"]
        tname = tool["name"]

        # 从会话文件获取工具参数
        param_info = tool_params.get(tid, {})
        param_summary = param_info.get("summary", "")
        param_workdir = param_info.get("workdir", "")

        # 构建工具开始的描述
        start_label = f"[TOOL-START] 开始执行工具: {tname}"
        if param_summary:
            start_label += f"\n               {'':>9}  {'':>13}{param_summary}"
        if param_workdir:
            start_label += f"\n               {'':>9}  {'':>13}工作目录: {param_workdir}"

        timeline.append((tool["start"], start_label))

        if tool["end"]:
            ts = parse_time(tool["start"])
            te = parse_time(tool["end"])
            dur = fmt_duration((te - ts).total_seconds() * 1000) if ts and te else "?"
            timeline.append((tool["end"], f"[TOOL-END]   工具执行完成: {tname} (耗时 {dur})"))

    if r["end"]:
        timeline.append((r["end"], "[RUN-END]    处理完成, 准备返回结果"))

    timeline.sort(key=lambda x: x[0])

    prev = None
    for t, label in timeline:
        ts = t[11:23]
        curr = parse_time(t)
        if prev:
            delta_ms = (curr - prev).total_seconds() * 1000
            delta_str = fmt_duration(delta_ms)
            if delta_ms > 5000:
                marker = "  << 慢"
            elif delta_ms > 1000:
                marker = "  < 较慢"
            else:
                marker = ""
        else:
            delta_str = "---"
            marker = ""
        prev = curr

        # 处理多行标签（工具参数）
        lines = label.split("\n")
        print(f"  {ts:>12} {delta_str:>9}  {lines[0]}{marker}")
        for extra_line in lines[1:]:
            print(f"  {extra_line}")

    # ========== Run 汇总 ==========
    print()
    segments, total_infer, total_tool = calc_inference_segments(r)

    # 收集本 run 所有推理的 token 用量
    run_total_input = 0
    run_total_output = 0
    run_total_cache_read = 0
    run_total_cache_write = 0
    run_total_tokens = 0
    run_inference_count = 0
    seen_usage_ids = set()  # 避免同一条 usage 被多个 toolCall 重复计数

    # 从 calc_inference_segments 返回的 segments 构建 per_inference
    per_inference = []  # [(label, ms, merged_usage_or_None)]

    for label, ms, tool_indices in segments:
        merged_usage = None
        merged_output = 0
        merged_input = 0
        merged_cache_read = 0
        merged_cache_write = 0
        found_any = False

        for ti in tool_indices:
            if ti < len(r["tools"]):
                tool = r["tools"][ti]
                usage_rec = inference_usage.get(tool["id"])
                if usage_rec and id(usage_rec) not in seen_usage_ids:
                    seen_usage_ids.add(id(usage_rec))
                    merged_input += usage_rec["input"]
                    merged_output += usage_rec["output"]
                    merged_cache_read += usage_rec["cacheRead"]
                    merged_cache_write += usage_rec["cacheWrite"]
                    found_any = True

        if not tool_indices and "(生成回复)" in label:
            # Final segment or no-tool run: find text reply usage
            if r["end"] and r["start"]:
                for ts_str, u in text_reply_usage:
                    if ts_str[:19] >= r["start"][:19] and ts_str[:19] <= r["end"][:19]:
                        if id(u) not in seen_usage_ids:
                            merged_usage = u
                            seen_usage_ids.add(id(u))
                            merged_input += u["input"]
                            merged_output += u["output"]
                            merged_cache_read += u["cacheRead"]
                            merged_cache_write += u["cacheWrite"]
                            found_any = True
                            break

        if found_any:
            run_total_input += merged_input
            run_total_output += merged_output
            run_total_cache_read += merged_cache_read
            run_total_cache_write += merged_cache_write
            run_inference_count += 1
            combined = {
                "input": merged_input,
                "output": merged_output,
                "cacheRead": merged_cache_read,
                "cacheWrite": merged_cache_write,
            }
            per_inference.append((label, ms, combined))
        elif ms > 0:
            per_inference.append((label, ms, None))

    run_total_tokens = run_total_input + run_total_output + run_total_cache_read + run_total_cache_write

    # --- 汇总输出 ---
    print(f"  {'─'*62}")
    print(f"  [Run 汇总]")
    print()
    if total_ms:
        print(f"    端到端耗时:     {fmt_duration(total_ms)}")
    print(f"    模型推理总耗时: {fmt_duration(total_infer)}", end="")
    if total_ms and total_infer > 0:
        print(f" ({total_infer/total_ms*100:.0f}%)", end="")
    print()
    if total_tool > 0:
        print(f"    工具执行总耗时: {fmt_duration(total_tool)}", end="")
        if total_ms:
            print(f" ({total_tool/total_ms*100:.0f}%)", end="")
        print()
    if total_ms:
        other_ms = total_ms - total_infer - total_tool
        if other_ms > 200:
            print(f"    其他开销:       {fmt_duration(other_ms)} ({other_ms/total_ms*100:.0f}%)")
    print(f"    推理调用次数:   {run_inference_count}")
    print(f"    工具调用次数:   {len(r['tools'])}")
    print()

    # token 统计
    print(f"    Token 统计:")
    print(f"      输入 token:   {run_total_input:>8}")
    print(f"      输出 token:   {run_total_output:>8}")
    print(f"      缓存读取:     {run_total_cache_read:>8}")
    print(f"      缓存写入:     {run_total_cache_write:>8}")
    if run_total_output > 0 and total_infer > 0:
        tps = run_total_output / (total_infer / 1000)
        print(f"      输出速率:     {tps:>7.1f} tokens/s")

    # 耗时分布条形图
    if total_ms and total_ms > 0:
        print()
        print(f"    耗时分布:")
        print(f"      模型推理 {bar(total_infer, total_ms)}  {fmt_duration(total_infer):>8}")
        if total_tool > 0:
            print(f"      工具执行 {bar(total_tool, total_ms)}  {fmt_duration(total_tool):>8}")

    # 推理分段明细 (含 token)
    if per_inference:
        print()
        print(f"    推理分段明细:")
        print(f"      {'段':^24} {'耗时':>8} {'输出token':>10} {'速率':>12}")
        print(f"      {'─'*24} {'─'*8} {'─'*10} {'─'*12}")
        for label, ms, usage_rec in per_inference:
            dur_str = fmt_duration(ms) if ms > 0 else "-"
            if usage_rec:
                out_tok = usage_rec["output"]
                out_str = str(out_tok)
                if ms > 0 and out_tok > 0:
                    rate = out_tok / (ms / 1000)
                    rate_str = f"{rate:.1f} tok/s"
                else:
                    rate_str = "-"
            else:
                out_str = "(未知)"
                rate_str = "-"
            print(f"      {label:<24} {dur_str:>8} {out_str:>10} {rate_str:>12}")

# ============================================================
# 6. 错误列表
# ============================================================
if errors:
    print()
    print("=" * 68)
    print(f"{'[错误列表]':^64}")
    print("=" * 68)
    for t, msg in errors[-10:]:
        print(f"  {t[11:19]}  [ERROR] {msg[:100]}")

print()
print("=" * 68)
PYEOF
