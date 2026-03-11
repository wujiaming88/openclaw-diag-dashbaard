#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw 诊断面板 v1.1
单文件 Python Web 服务，内嵌前端 HTML/CSS/JS
用于可视化 OpenClaw 的性能诊断数据

零外部依赖，只用 Python 标准库
兼容 Python 3.6+
"""

from __future__ import print_function

import sys

# 版本检查
if sys.version_info < (3, 6):
    print("需要 Python 3.6 或更高版本")
    sys.exit(1)

import argparse
import glob
import json
import math
import os
import platform
import re
import signal
import socket
import subprocess
import threading
import traceback
from collections import OrderedDict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ============================================================
# 全局常量
# ============================================================
VERSION = "1.1.0"
MAX_LOG_LINES = 50000  # 大文件只解析最后 N 行

# 模型定价表 (input_per_1m, output_per_1m, cache_read_per_1m, cache_write_per_1m) USD
MODEL_PRICING = {
    "claude-opus-4": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4.5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4": (3.0, 15.0, 0.3, 3.75),
    "claude-sonnet-3.5": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku": (0.25, 1.25, 0.025, 0.3),
    "gpt-4o": (2.5, 10.0, 1.25, 1.25),
    "gpt-4o-mini": (0.15, 0.6, 0.075, 0.075),
    "gemini-3-flash": (0.1, 0.4, 0.025, 0.05),
    "kimi-k2": (0.6, 2.0, 0.3, 0.3),
}

# ============================================================
# 路径自动检测
# ============================================================

def detect_log_dir(cli_arg):
    """按优先级检测日志目录"""
    candidates = []
    if cli_arg:
        candidates.append(cli_arg)
    env_val = os.environ.get("OPENCLAW_LOG_DIR", "")
    if env_val:
        candidates.append(env_val)
    # 平台默认
    candidates.append("/tmp/openclaw")
    candidates.append(os.path.expanduser("~/Library/Logs/openclaw"))
    # Windows: %TEMP%/openclaw
    temp = os.environ.get("TEMP", "")
    if temp:
        candidates.append(os.path.join(temp, "openclaw"))
    for c in candidates:
        c = os.path.expanduser(c)
        if os.path.isdir(c):
            return c
    # 返回第一个候选（可能不存在，后续容错处理）
    if cli_arg:
        return os.path.expanduser(cli_arg)
    return "/tmp/openclaw"


def detect_sessions_dirs(cli_arg):
    """按优先级检测会话目录列表"""
    dirs = []
    if cli_arg:
        expanded = os.path.expanduser(cli_arg)
        if os.path.isdir(expanded):
            dirs.append(expanded)
        return dirs
    env_val = os.environ.get("OPENCLAW_SESSIONS_DIR", "")
    if env_val:
        expanded = os.path.expanduser(env_val)
        if os.path.isdir(expanded):
            dirs.append(expanded)
        return dirs
    # 标准路径
    pattern = os.path.expanduser("~/.openclaw/agents/*/sessions")
    for d in glob.glob(pattern):
        if os.path.isdir(d):
            dirs.append(d)
    # 自定义 state dir
    state_dir = os.environ.get("OPENCLAW_STATE_DIR", "")
    if state_dir:
        pattern2 = os.path.join(state_dir, "agents/*/sessions")
        for d in glob.glob(pattern2):
            if os.path.isdir(d):
                dirs.append(d)
    return dirs


# ============================================================
# 日志解析
# ============================================================

def check_openclaw_config():
    """检测 OpenClaw 配置是否开启了诊断和 debug 日志级别
    返回: (config_ok, warnings_list, config_path, config_data)
    """
    warnings = []
    config_ok = False

    # 查找配置文件
    config_paths = []
    state_dir = os.environ.get("OPENCLAW_STATE_DIR", "")
    config_env = os.environ.get("OPENCLAW_CONFIG_PATH", "")

    if config_env:
        config_paths.append(config_env)
    if state_dir:
        config_paths.append(os.path.join(state_dir, "openclaw.json"))
    config_paths.append(os.path.expanduser("~/.openclaw/openclaw.json"))

    config_path = None
    config_data = None
    for cp in config_paths:
        cp = os.path.expanduser(cp)
        if os.path.isfile(cp):
            config_path = cp
            break

    if not config_path:
        warnings.append("[警告] 未找到 OpenClaw 配置文件, 无法校验诊断是否开启")
        warnings.append("       搜索路径: %s" % ", ".join(config_paths))
        return False, warnings, None, None

    # 读取并解析配置
    try:
        with open(config_path, "r", encoding="utf-8", errors="replace") as f:
            config_data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        warnings.append("[警告] 配置文件读取失败 (%s): %s" % (config_path, e))
        return False, warnings, config_path, None

    # 检查 diagnostics.enabled
    diag = config_data.get("diagnostics", {})
    if not isinstance(diag, dict):
        diag = {}
    diag_enabled = diag.get("enabled", False)

    # 检查 logging.level
    logging_cfg = config_data.get("logging", {})
    if not isinstance(logging_cfg, dict):
        logging_cfg = {}
    log_level = logging_cfg.get("level", "info")

    # 判断结果
    issues = []
    if not diag_enabled:
        issues.append('diagnostics.enabled 未设置为 true')
    if log_level not in ("debug", "trace"):
        issues.append('logging.level 为 "%s" (需要 "debug" 或 "trace")' % log_level)

    if issues:
        warnings.append("[警告] OpenClaw 配置不完整, Dashboard 可能无法获取数据!")
        warnings.append("       配置文件: %s" % config_path)
        for issue in issues:
            warnings.append("       - %s" % issue)
        warnings.append("")
        warnings.append("       请在配置文件中添加以下内容并重启 Gateway:")
        warnings.append('       {')
        warnings.append('         "diagnostics": { "enabled": true },')
        warnings.append('         "logging": { "level": "debug" }')
        warnings.append('       }')
        warnings.append("")
        warnings.append("       然后执行: openclaw gateway restart")
        config_ok = False
    else:
        config_ok = True

    return config_ok, warnings, config_path, config_data


def safe_read_lines(filepath, max_lines=MAX_LOG_LINES):
    """安全读取文件行，大文件只取最后 max_lines 行"""
    try:
        fsize = os.path.getsize(filepath)
    except OSError:
        return []
    # 大文件 > 100MB，只读尾部
    if fsize > 100 * 1024 * 1024:
        lines = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                # 跳到尾部附近
                seek_pos = max(0, fsize - 80 * max_lines)  # 估计每行 ~80 字节
                f.seek(seek_pos)
                if seek_pos > 0:
                    f.readline()  # 丢弃可能不完整的第一行
                lines = f.readlines()
                if len(lines) > max_lines:
                    lines = lines[-max_lines:]
        except Exception:
            return []
        return lines
    else:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        except Exception:
            return []


def parse_time(ts):
    """解析时间戳字符串，返回 datetime 对象（UTC）"""
    if not ts:
        return None
    # 去掉尾部时区偏移便于解析
    ts = ts.strip()
    # 格式: 2026-03-11T11:34:43.722+00:00 或 2026-03-11T11:34:43.721Z
    ts = ts.replace("Z", "+00:00")
    # 去掉最后的 +00:00 做 naive datetime
    if "+" in ts[10:]:
        ts = ts[:ts.rindex("+")]
    elif ts.endswith("-00:00"):
        ts = ts[:-6]
    # 解析
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def ms_between(dt1, dt2):
    """两个 datetime 之间的毫秒差"""
    if dt1 is None or dt2 is None:
        return 0
    delta = dt2 - dt1
    return int(delta.total_seconds() * 1000)


def extract_kv(text, key):
    """从文本中提取 key=value"""
    pattern = key + r"=(\S+)"
    m = re.search(pattern, text)
    if m:
        return m.group(1)
    return ""


def parse_log_events(filepath):
    """解析日志文件，提取所有 embedded run 事件，按 runId 分组"""
    runs = OrderedDict()  # runId -> dict
    lines = safe_read_lines(filepath)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        msg = obj.get("1", "")
        if not isinstance(msg, str):
            continue
        ts_str = obj.get("time", "") or obj.get("_meta", {}).get("date", "")
        ts = parse_time(ts_str)
        if "embedded run start:" in msg:
            run_id = extract_kv(msg, "runId")
            if not run_id:
                continue
            runs[run_id] = {
                "run_id": run_id,
                "session_id": extract_kv(msg, "sessionId"),
                "model": extract_kv(msg, "model"),
                "channel": extract_kv(msg, "messageChannel"),
                "provider": extract_kv(msg, "provider"),
                "start": ts,
                "start_str": ts_str,
                "agent_start": None,
                "agent_end": None,
                "end": None,
                "duration_ms": 0,
                "tools": [],  # [{tool, toolCallId, start, end}]
                "is_error": False,
                "aborted": False,
                "prompt_info": {},
            }
        elif "embedded run agent start:" in msg:
            run_id = extract_kv(msg, "runId")
            if run_id in runs:
                runs[run_id]["agent_start"] = ts
        elif "embedded run tool start:" in msg:
            run_id = extract_kv(msg, "runId")
            if run_id in runs:
                tool_name = extract_kv(msg, "tool")
                tool_call_id = extract_kv(msg, "toolCallId")
                runs[run_id]["tools"].append({
                    "tool": tool_name,
                    "toolCallId": tool_call_id,
                    "start": ts,
                    "end": None,
                })
        elif "embedded run tool end:" in msg:
            run_id = extract_kv(msg, "runId")
            tool_call_id = extract_kv(msg, "toolCallId")
            if run_id in runs:
                for t in runs[run_id]["tools"]:
                    if t["toolCallId"] == tool_call_id and t["end"] is None:
                        t["end"] = ts
                        break
        elif "embedded run agent end:" in msg:
            run_id = extract_kv(msg, "runId")
            if run_id in runs:
                runs[run_id]["agent_end"] = ts
                err = extract_kv(msg, "isError")
                if err == "true":
                    runs[run_id]["is_error"] = True
        elif "embedded run done:" in msg:
            run_id = extract_kv(msg, "runId")
            if run_id in runs:
                runs[run_id]["end"] = ts
                dur = extract_kv(msg, "durationMs")
                try:
                    runs[run_id]["duration_ms"] = int(dur)
                except (ValueError, TypeError):
                    pass
                aborted = extract_kv(msg, "aborted")
                if aborted == "true":
                    runs[run_id]["aborted"] = True
        elif "[context-diag] pre-prompt:" in msg:
            run_id = extract_kv(msg, "runId")
            if run_id in runs:
                runs[run_id]["prompt_info"] = {
                    "messages": extract_kv(msg, "messages"),
                    "historyTextChars": extract_kv(msg, "historyTextChars"),
                    "systemPromptChars": extract_kv(msg, "systemPromptChars"),
                    "sessionKey": extract_kv(msg, "sessionKey"),
                }
    return runs


# ============================================================
# 会话文件解析（工具参数 + Token 用量）
# ============================================================

def parse_session_files(sessions_dirs):
    """解析会话文件，建立 toolCallId -> {arguments, usage} 映射
    同时收集纯文本回复（无 toolCall）的 usage，按 timestamp 索引
    返回: (tool_data, text_reply_usage)
    """
    tool_data = {}  # toolCallId -> {tool, arguments_raw, arguments_summary, usage}
    text_reply_usage = []  # [(timestamp_str, usage_dict)]
    if not sessions_dirs:
        return tool_data, text_reply_usage
    files = []
    for d in sessions_dirs:
        pattern = os.path.join(d, "*.jsonl")
        files.extend(glob.glob(pattern))
    for fpath in files:
        lines = safe_read_lines(fpath, max_lines=20000)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") != "message":
                continue
            msg = obj.get("message", {})
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            model = msg.get("model", "")
            if model == "delivery-mirror":
                continue
            usage = msg.get("usage", {})
            timestamp = obj.get("timestamp", "")
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            has_tool_call = False
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "toolCall":
                    continue
                has_tool_call = True
                tc_id = item.get("id", "")
                tc_name = item.get("name", "")
                tc_args_raw = item.get("arguments", "{}")
                if isinstance(tc_args_raw, dict):
                    pass
                elif not isinstance(tc_args_raw, str):
                    tc_args_raw = str(tc_args_raw)
                summary = summarize_tool_args(tc_name, tc_args_raw)
                tool_data[tc_id] = {
                    "tool": tc_name,
                    "arguments_raw": tc_args_raw,
                    "arguments_summary": summary,
                    "usage": usage,
                    "timestamp": timestamp,
                }
            # 没有 toolCall 的 assistant 消息 = 纯文本回复
            if not has_tool_call and usage and timestamp:
                text_reply_usage.append((timestamp, usage))
    return tool_data, text_reply_usage


def summarize_tool_args(tool_name, args_raw):
    """提取工具参数的可读摘要（详细版）"""
    if isinstance(args_raw, dict):
        args = args_raw
    elif isinstance(args_raw, str):
        try:
            args = json.loads(args_raw)
        except (json.JSONDecodeError, ValueError):
            return args_raw[:200] if args_raw else ""
    else:
        return str(args_raw)[:200] if args_raw else ""
    if not isinstance(args, dict):
        return str(args)[:200]
    if tool_name == "exec":
        cmd = args.get("command", "")
        cmd_preview = cmd[:200]
        wd = args.get("workdir", "")
        parts = [cmd_preview]
        if wd:
            parts.append("[cwd: %s]" % wd)
        bg = args.get("background")
        if bg:
            parts.append("[background]")
        timeout = args.get("timeout")
        if timeout:
            parts.append("[timeout: %s]" % timeout)
        return "\n".join(parts)
    elif tool_name in ("read", "write"):
        p = args.get("path", "") or args.get("file_path", "")
        parts = [p]
        offset = args.get("offset")
        limit = args.get("limit")
        if offset:
            parts.append("offset=%s" % offset)
        if limit:
            parts.append("limit=%s" % limit)
        if tool_name == "write":
            content = args.get("content", "")
            if content:
                parts.append("(%d chars)" % len(content))
        return "  ".join(parts)
    elif tool_name == "edit":
        p = args.get("path", "") or args.get("file_path", "")
        old = args.get("old_string", "") or args.get("oldText", "")
        new = args.get("new_string", "") or args.get("newText", "")
        old_preview = old[:100] if old else ""
        new_preview = new[:100] if new else ""
        lines = [p]
        if old_preview:
            lines.append("old: %s" % old_preview)
        if new_preview:
            lines.append("new: %s" % new_preview)
        return "\n".join(lines)
    elif tool_name == "web_search":
        q = args.get("query", "")
        count = args.get("count", "")
        parts = [q]
        if count:
            parts.append("count=%s" % count)
        return "  ".join(parts)
    elif tool_name == "web_fetch":
        url = args.get("url", "")
        mode = args.get("extractMode", "")
        parts = [url]
        if mode:
            parts.append("mode=%s" % mode)
        return "  ".join(parts)
    elif tool_name == "sessions_spawn":
        agent = args.get("agentId", "")
        task = args.get("task", "")[:100]
        label = args.get("label", "")
        model = args.get("model", "")
        parts = ["agent=%s" % agent]
        if label:
            parts.append("label=%s" % label)
        if model:
            parts.append("model=%s" % model)
        if task:
            parts.append("\ntask: %s" % task)
        return "  ".join(parts)
    elif tool_name == "message":
        action = args.get("action", "")
        target = args.get("target", "")
        msg = args.get("message", "")[:100]
        parts = ["action=%s" % action]
        if target:
            parts.append("target=%s" % target)
        if msg:
            parts.append("\n%s" % msg)
        return "  ".join(parts)
    elif tool_name == "browser":
        action = args.get("action", "")
        url = args.get("url", "")
        ref = args.get("ref", "")
        kind = args.get("kind", "")
        text = args.get("text", "")
        parts = ["action=%s" % action]
        if url:
            parts.append("url=%s" % url[:120])
        if ref:
            parts.append("ref=%s" % ref)
        if kind:
            parts.append("kind=%s" % kind)
        if text:
            parts.append("text=%s" % text[:80])
        return "  ".join(parts)
    elif tool_name == "memory_search":
        return args.get("query", "")
    elif tool_name == "memory_get":
        p = args.get("path", "")
        frm = args.get("from", "")
        ln = args.get("lines", "")
        parts = [p]
        if frm:
            parts.append("from=%s" % frm)
        if ln:
            parts.append("lines=%s" % ln)
        return "  ".join(parts)
    else:
        # 通用: 前几个 key=value
        parts = []
        for k, v in list(args.items())[:5]:
            vs = str(v)[:80]
            parts.append("%s=%s" % (k, vs))
        return "  ".join(parts)


# ============================================================
# 计算推理分段
# ============================================================

def compute_infer_segments(run):
    """计算推理分段 [{start, end, duration_ms, label}]"""
    segments = []
    agent_start = run.get("agent_start")
    agent_end = run.get("agent_end")
    tools = run.get("tools", [])
    if not agent_start:
        return segments
    # 按 start 时间排序工具
    sorted_tools = sorted([t for t in tools if t.get("start")], key=lambda t: t["start"])
    if not sorted_tools:
        end = agent_end or run.get("end") or agent_start
        segments.append({
            "label": "推理 #1 (生成回复)",
            "start": agent_start,
            "end": end,
            "duration_ms": ms_between(agent_start, end),
        })
        return segments
    first_tool = sorted_tools[0]
    seg_end = first_tool["start"]
    segments.append({
        "label": "推理 #1",
        "start": agent_start,
        "end": seg_end,
        "duration_ms": ms_between(agent_start, seg_end),
    })
    for i in range(len(sorted_tools) - 1):
        t_end = sorted_tools[i].get("end")
        t_next_start = sorted_tools[i + 1].get("start")
        if t_end and t_next_start:
            segments.append({
                "label": "推理 #%d" % (i + 2),
                "start": t_end,
                "end": t_next_start,
                "duration_ms": ms_between(t_end, t_next_start),
            })
    last_tool = sorted_tools[-1]
    last_end = last_tool.get("end")
    final_end = agent_end or run.get("end")
    if last_end and final_end:
        segments.append({
            "label": "推理 #%d (生成回复)" % (len(sorted_tools) + 1),
            "start": last_end,
            "end": final_end,
            "duration_ms": ms_between(last_end, final_end),
        })
    return segments


# ============================================================
# 模型定价匹配
# ============================================================

def match_model_pricing(model_name):
    """根据模型名匹配定价，返回 (input, output, cache_read, cache_write) per 1M tokens 或 None"""
    if not model_name:
        return None
    lower = model_name.lower()
    for keyword, pricing in MODEL_PRICING.items():
        if keyword in lower:
            return pricing
    return None


def estimate_cost(inp, out, cache_read, cache_write, pricing):
    """根据 token 数和定价计算费用"""
    if not pricing:
        return None
    return {
        "input_cost": round(inp / 1_000_000 * pricing[0], 4),
        "output_cost": round(out / 1_000_000 * pricing[1], 4),
        "cache_read_cost": round(cache_read / 1_000_000 * pricing[2], 4),
        "cache_write_cost": round(cache_write / 1_000_000 * pricing[3], 4),
        "total_cost": round(
            inp / 1_000_000 * pricing[0] +
            out / 1_000_000 * pricing[1] +
            cache_read / 1_000_000 * pricing[2] +
            cache_write / 1_000_000 * pricing[3], 4),
        "currency": "USD",
    }


# ============================================================
# 系统信息
# ============================================================

def get_system_info(data_store, config_path, config_data):
    """收集系统信息"""
    info = {}

    # OpenClaw version
    oc_version = ""
    try:
        result = subprocess.run(
            ["openclaw", "--version"],
            capture_output=True, text=True, timeout=5,
            stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            oc_version = result.stdout.strip()
    except Exception:
        pass
    if not oc_version:
        # try package.json
        for pj_path in [
            "/usr/lib/node_modules/openclaw/package.json",
            os.path.expanduser("~/.npm-global/lib/node_modules/openclaw/package.json"),
        ]:
            if os.path.isfile(pj_path):
                try:
                    with open(pj_path, "r") as f:
                        pj = json.load(f)
                    oc_version = pj.get("version", "")
                    break
                except Exception:
                    pass
    info["openclaw_version"] = oc_version
    info["openclaw_config_path"] = config_path or ""

    # Config info
    diag_enabled = False
    log_level = "info"
    default_model = ""
    agents_list = []
    channels_list = []
    if config_data and isinstance(config_data, dict):
        diag = config_data.get("diagnostics", {})
        if isinstance(diag, dict):
            diag_enabled = diag.get("enabled", False)
        logging_cfg = config_data.get("logging", {})
        if isinstance(logging_cfg, dict):
            log_level = logging_cfg.get("level", "info")
        default_model = config_data.get("defaultModel", "")
        agents_cfg = config_data.get("agents", {})
        if isinstance(agents_cfg, dict):
            al = agents_cfg.get("list", [])
            if isinstance(al, list):
                for a in al:
                    if isinstance(a, dict):
                        agents_list.append(a.get("id", a.get("name", "")))
                    elif isinstance(a, str):
                        agents_list.append(a)
        channels_cfg = config_data.get("channels", {})
        if isinstance(channels_cfg, dict):
            for key in channels_cfg:
                channels_list.append(key)
        elif isinstance(channels_cfg, list):
            channels_list = channels_cfg

    info["diagnostics_enabled"] = diag_enabled
    info["logging_level"] = log_level
    info["default_model"] = default_model
    info["agents"] = agents_list
    info["channels"] = channels_list

    # System info
    info["python_version"] = platform.python_version()
    info["platform"] = platform.platform()
    info["hostname"] = socket.gethostname()
    info["cpu_count"] = os.cpu_count() or 0

    # Memory
    mem_total = 0
    mem_used = 0
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = f.read()
        m_total = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
        m_avail = re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo)
        if m_total:
            mem_total = int(m_total.group(1)) // 1024
        if m_total and m_avail:
            mem_used = mem_total - int(m_avail.group(1)) // 1024
    except Exception:
        pass
    info["memory_total_mb"] = mem_total
    info["memory_used_mb"] = mem_used

    # Log/session stats
    info["log_dir"] = data_store.log_dir
    log_files = glob.glob(os.path.join(data_store.log_dir, "openclaw-*.log"))
    info["log_file_count"] = len(log_files)

    sessions_dir_count = len(data_store.sessions_dirs)
    session_file_count = 0
    for d in data_store.sessions_dirs:
        session_file_count += len(glob.glob(os.path.join(d, "*.jsonl")))
    info["sessions_dir_count"] = sessions_dir_count
    info["session_file_count"] = session_file_count

    return info


# ============================================================
# 构建 API 数据
# ============================================================

class DataStore(object):
    """缓存和提供诊断数据"""

    def __init__(self, log_dir, sessions_dirs):
        self.log_dir = log_dir
        self.sessions_dirs = sessions_dirs
        self._cache = {}  # date -> runs dict
        self._tool_data = None
        self._text_reply_usage = None
        self._tool_data_loaded = False
        self._lock = threading.Lock()

    def _get_tool_data(self):
        if not self._tool_data_loaded:
            with self._lock:
                if not self._tool_data_loaded:
                    self._tool_data, self._text_reply_usage = parse_session_files(self.sessions_dirs)
                    self._tool_data_loaded = True
        return self._tool_data or {}

    def _get_text_reply_usage(self):
        self._get_tool_data()
        return self._text_reply_usage or []

    def get_dates(self):
        pattern = os.path.join(self.log_dir, "openclaw-*.log")
        files = glob.glob(pattern)
        dates = []
        for f in files:
            basename = os.path.basename(f)
            m = re.match(r"openclaw-(\d{4}-\d{2}-\d{2})\.log", basename)
            if m:
                dates.append(m.group(1))
        dates.sort(reverse=True)
        return dates

    def _load_runs(self, date):
        filepath = os.path.join(self.log_dir, "openclaw-%s.log" % date)
        if not os.path.isfile(filepath):
            return OrderedDict()
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            mtime = 0
        cache_key = date
        cached = self._cache.get(cache_key)
        if cached and cached[0] == mtime:
            return cached[1]
        runs = parse_log_events(filepath)
        self._cache[cache_key] = (mtime, runs)
        return runs

    def get_summary(self, date):
        runs = self._load_runs(date)
        if not runs:
            return {
                "date": date,
                "total_runs": 0,
                "avg_duration_ms": 0,
                "total_infer_ms": 0,
                "total_tool_ms": 0,
                "infer_ratio": 0,
                "total_tokens_output": 0,
                "error_count": 0,
                "models": [],
                "channels": [],
            }
        durations = []
        total_infer = 0
        total_tool = 0
        total_tokens = 0
        error_count = 0
        models = set()
        channels = set()
        tool_data = self._get_tool_data()
        for run in runs.values():
            dur = run.get("duration_ms", 0)
            if dur > 0:
                durations.append(dur)
            if run.get("is_error"):
                error_count += 1
            if run.get("model"):
                models.add(run["model"])
            if run.get("channel"):
                channels.add(run["channel"])
            segs = compute_infer_segments(run)
            for s in segs:
                total_infer += s["duration_ms"]
            for t in run.get("tools", []):
                if t.get("start") and t.get("end"):
                    total_tool += ms_between(t["start"], t["end"])
                tc_id = t.get("toolCallId", "")
                td = tool_data.get(tc_id, {})
                usage = td.get("usage", {})
                total_tokens += usage.get("output", 0)
        avg_dur = int(sum(durations) / len(durations)) if durations else 0
        total_time = total_infer + total_tool
        infer_ratio = round(total_infer / total_time * 100, 1) if total_time > 0 else 0
        return {
            "date": date,
            "total_runs": len(runs),
            "avg_duration_ms": avg_dur,
            "total_infer_ms": total_infer,
            "total_tool_ms": total_tool,
            "infer_ratio": infer_ratio,
            "total_tokens_output": total_tokens,
            "error_count": error_count,
            "models": sorted(models),
            "channels": sorted(channels),
        }

    def get_runs_list(self, date, page=1, per_page=20):
        """返回某天的 run 列表（分页）"""
        runs = self._load_runs(date)
        tool_data = self._get_tool_data()
        all_results = []
        for run in runs.values():
            infer_segs = compute_infer_segments(run)
            infer_ms = sum(s["duration_ms"] for s in infer_segs)
            tool_ms = 0
            tool_count = len(run.get("tools", []))
            token_output = 0
            seen_usage_ids = set()
            for t in run.get("tools", []):
                if t.get("start") and t.get("end"):
                    tool_ms += ms_between(t["start"], t["end"])
                tc_id = t.get("toolCallId", "")
                td = tool_data.get(tc_id, {})
                usage = td.get("usage", {})
                uid = id(usage)
                if uid not in seen_usage_ids and usage:
                    seen_usage_ids.add(uid)
                    token_output += usage.get("output", 0)
            status = "ok"
            if run.get("is_error"):
                status = "error"
            elif run.get("aborted"):
                status = "aborted"
            elif not run.get("end"):
                status = "running"
            start_str = ""
            if run.get("start"):
                start_str = run["start"].strftime("%H:%M:%S")
            end_str = ""
            if run.get("end"):
                end_str = run["end"].strftime("%H:%M:%S")
            all_results.append({
                "run_id": run["run_id"],
                "start": start_str,
                "end": end_str,
                "model": run.get("model", ""),
                "channel": run.get("channel", ""),
                "duration_ms": run.get("duration_ms", 0),
                "infer_ms": infer_ms,
                "tool_ms": tool_ms,
                "tool_count": tool_count,
                "token_output": token_output,
                "status": status,
            })
        total = len(all_results)
        total_pages = max(1, math.ceil(total / per_page)) if per_page > 0 else 1
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        return {
            "runs": all_results[start_idx:end_idx],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    def get_run_detail(self, date, run_id):
        runs = self._load_runs(date)
        run = runs.get(run_id)
        if not run:
            return None
        tool_data = self._get_tool_data()
        text_reply_usage = self._get_text_reply_usage()

        infer_segs = compute_infer_segments(run)
        run_start_str = run["start"].strftime("%Y-%m-%dT%H:%M:%S") if run.get("start") else ""
        run_end_str = run["end"].strftime("%Y-%m-%dT%H:%M:%S") if run.get("end") else ""

        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        seen_usage_ids = set()

        infer_list = []
        for i, s in enumerate(infer_segs):
            output_tokens = 0
            usage_rec = {}

            if i < len(run.get("tools", [])):
                tc_id = run["tools"][i].get("toolCallId", "")
                td = tool_data.get(tc_id, {})
                usage_rec = td.get("usage", {})
                output_tokens = usage_rec.get("output", 0)
            elif run.get("end"):
                for ts_str, u in text_reply_usage:
                    if run_start_str and run_end_str:
                        if ts_str[:19] >= run_start_str and ts_str[:19] <= run_end_str:
                            if id(u) not in seen_usage_ids:
                                usage_rec = u
                                output_tokens = u.get("output", 0)
                                break
            elif not run.get("tools") and run.get("end"):
                for ts_str, u in text_reply_usage:
                    if run_start_str and run_end_str:
                        if ts_str[:19] >= run_start_str and ts_str[:19] <= run_end_str:
                            if id(u) not in seen_usage_ids:
                                usage_rec = u
                                output_tokens = u.get("output", 0)
                                break

            if usage_rec and id(usage_rec) not in seen_usage_ids:
                seen_usage_ids.add(id(usage_rec))
                total_input += usage_rec.get("input", 0)
                total_output += usage_rec.get("output", 0)
                total_cache_read += usage_rec.get("cacheRead", 0)
                total_cache_write += usage_rec.get("cacheWrite", 0)

            dur_s = s["duration_ms"] / 1000.0 if s["duration_ms"] > 0 else 0.001
            tok_per_s = round(output_tokens / dur_s, 1) if output_tokens > 0 else 0
            infer_list.append({
                "label": s["label"],
                "duration_ms": s["duration_ms"],
                "output_tokens": output_tokens,
                "tok_per_s": tok_per_s,
            })

        tools_list = []
        total_tool_ms = 0
        for t in run.get("tools", []):
            dur = 0
            if t.get("start") and t.get("end"):
                dur = ms_between(t["start"], t["end"])
            total_tool_ms += dur
            tc_id = t.get("toolCallId", "")
            td = tool_data.get(tc_id, {})
            usage = td.get("usage", {})
            start_str = ""
            if t.get("start"):
                start_str = t["start"].strftime("%H:%M:%S.%f")[:-3]
            tools_list.append({
                "tool": t.get("tool", ""),
                "toolCallId": tc_id,
                "duration_ms": dur,
                "start": start_str,
                "arguments_summary": td.get("arguments_summary", ""),
                "usage": {
                    "input": usage.get("input", 0),
                    "output": usage.get("output", 0),
                    "cacheRead": usage.get("cacheRead", 0),
                    "cacheWrite": usage.get("cacheWrite", 0),
                    "totalTokens": usage.get("totalTokens", 0),
                },
            })

        total_infer_ms = sum(s["duration_ms"] for s in infer_segs)
        run_start = run.get("start")
        run_end = run.get("end") or run.get("agent_end")
        total_dur = run.get("duration_ms", 0)
        if total_dur == 0 and run_start and run_end:
            total_dur = ms_between(run_start, run_end)
        gantt = []
        if total_dur > 0:
            for s in infer_segs:
                offset = ms_between(run_start, s["start"]) if run_start and s.get("start") else 0
                gantt.append({
                    "type": "infer",
                    "label": s["label"],
                    "offset_pct": round(offset / total_dur * 100, 2),
                    "width_pct": round(s["duration_ms"] / total_dur * 100, 2),
                    "duration_ms": s["duration_ms"],
                })
            for t in run.get("tools", []):
                dur = 0
                if t.get("start") and t.get("end"):
                    dur = ms_between(t["start"], t["end"])
                offset = ms_between(run_start, t["start"]) if run_start and t.get("start") else 0
                gantt.append({
                    "type": "tool",
                    "label": t.get("tool", ""),
                    "offset_pct": round(offset / total_dur * 100, 2),
                    "width_pct": round(dur / total_dur * 100, 2),
                    "duration_ms": dur,
                })

        dur_s = total_dur / 1000.0 if total_dur > 0 else 1
        overall_tok_s = round(total_output / dur_s, 1) if total_output > 0 else 0
        return {
            "run_id": run["run_id"],
            "session_id": run.get("session_id", ""),
            "model": run.get("model", ""),
            "provider": run.get("provider", ""),
            "channel": run.get("channel", ""),
            "start": run["start"].strftime("%H:%M:%S.%f")[:-3] if run.get("start") else "",
            "end": run["end"].strftime("%H:%M:%S.%f")[:-3] if run.get("end") else "",
            "duration_ms": total_dur,
            "infer_ms": total_infer_ms,
            "tool_ms": total_tool_ms,
            "tool_count": len(tools_list),
            "total_tokens_output": total_output,
            "overall_tok_per_s": overall_tok_s,
            "token_summary": {
                "input": total_input,
                "output": total_output,
                "cacheRead": total_cache_read,
                "cacheWrite": total_cache_write,
            },
            "status": "error" if run.get("is_error") else ("aborted" if run.get("aborted") else "ok"),
            "prompt_info": run.get("prompt_info", {}),
            "infer_segments": infer_list,
            "tools": tools_list,
            "gantt": gantt,
        }

    def get_token_analysis(self, date):
        """返回 Token 消耗分析数据"""
        runs = self._load_runs(date)
        tool_data = self._get_tool_data()
        text_reply_usage = self._get_text_reply_usage()

        if not runs:
            return {
                "date": date, "total_runs": 0,
                "token_totals": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                "cost_estimate": None, "by_model": [], "by_hour": [],
                "top_expensive_runs": [], "efficiency": {},
            }

        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_infer_ms = 0
        total_tool_ms = 0
        total_dur_ms = 0

        by_model = {}  # model -> {runs, input, output, cache_read, cache_write, infer_ms}
        by_hour = {}   # "HH:00" -> {runs, output}
        run_costs = []  # for top expensive

        for run in runs.values():
            model = run.get("model", "unknown")
            run_start = run.get("start")
            run_end = run.get("end")
            run_start_str = run_start.strftime("%Y-%m-%dT%H:%M:%S") if run_start else ""
            run_end_str = run_end.strftime("%Y-%m-%dT%H:%M:%S") if run_end else ""

            # Collect token usage for this run
            r_input = 0
            r_output = 0
            r_cache_read = 0
            r_cache_write = 0
            seen_ids = set()
            infer_segs = compute_infer_segments(run)
            r_infer_ms = sum(s["duration_ms"] for s in infer_segs)
            r_tool_ms = 0

            for i, s in enumerate(infer_segs):
                usage_rec = {}
                if i < len(run.get("tools", [])):
                    tc_id = run["tools"][i].get("toolCallId", "")
                    td = tool_data.get(tc_id, {})
                    usage_rec = td.get("usage", {})
                elif run_end:
                    for ts_str, u in text_reply_usage:
                        if run_start_str and run_end_str:
                            if ts_str[:19] >= run_start_str and ts_str[:19] <= run_end_str:
                                if id(u) not in seen_ids:
                                    usage_rec = u
                                    break
                if usage_rec and id(usage_rec) not in seen_ids:
                    seen_ids.add(id(usage_rec))
                    r_input += usage_rec.get("input", 0)
                    r_output += usage_rec.get("output", 0)
                    r_cache_read += usage_rec.get("cacheRead", 0)
                    r_cache_write += usage_rec.get("cacheWrite", 0)

            for t in run.get("tools", []):
                if t.get("start") and t.get("end"):
                    r_tool_ms += ms_between(t["start"], t["end"])

            total_input += r_input
            total_output += r_output
            total_cache_read += r_cache_read
            total_cache_write += r_cache_write
            total_infer_ms += r_infer_ms
            total_tool_ms += r_tool_ms
            dur = run.get("duration_ms", 0)
            total_dur_ms += dur

            # by_model
            if model not in by_model:
                by_model[model] = {"runs": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "infer_ms": 0}
            bm = by_model[model]
            bm["runs"] += 1
            bm["input"] += r_input
            bm["output"] += r_output
            bm["cache_read"] += r_cache_read
            bm["cache_write"] += r_cache_write
            bm["infer_ms"] += r_infer_ms

            # by_hour
            if run_start:
                hour_key = run_start.strftime("%H:00")
                if hour_key not in by_hour:
                    by_hour[hour_key] = {"runs": 0, "output": 0}
                by_hour[hour_key]["runs"] += 1
                by_hour[hour_key]["output"] += r_output

            # Estimate cost per run
            pricing = match_model_pricing(model)
            r_cost = estimate_cost(r_input, r_output, r_cache_read, r_cache_write, pricing)
            start_str = run_start.strftime("%H:%M:%S") if run_start else ""
            run_costs.append({
                "run_id": run["run_id"],
                "start": start_str,
                "output": r_output,
                "duration_ms": dur,
                "tool_count": len(run.get("tools", [])),
                "estimated_cost": r_cost["total_cost"] if r_cost else 0,
            })

        # Overall cost estimate (use first model's pricing or best guess)
        primary_model = ""
        if by_model:
            primary_model = max(by_model.keys(), key=lambda m: by_model[m]["runs"])
        pricing = match_model_pricing(primary_model)
        cost_est = estimate_cost(total_input, total_output, total_cache_read, total_cache_write, pricing)
        if cost_est:
            cost_est["note"] = "基于 %s 定价估算, 实际费用以账单为准" % primary_model
        else:
            cost_est = {
                "input_cost": 0, "output_cost": 0, "cache_read_cost": 0, "cache_write_cost": 0,
                "total_cost": 0, "currency": "USD", "note": "未知模型定价",
            }

        # by_model list
        by_model_list = []
        for m, bm in by_model.items():
            infer_s = bm["infer_ms"] / 1000.0 if bm["infer_ms"] > 0 else 1
            avg_out = round(bm["output"] / bm["runs"]) if bm["runs"] > 0 else 0
            avg_tok_s = round(bm["output"] / infer_s, 1) if bm["output"] > 0 else 0
            mp = match_model_pricing(m)
            mc = estimate_cost(bm["input"], bm["output"], bm["cache_read"], bm["cache_write"], mp)
            by_model_list.append({
                "model": m,
                "runs": bm["runs"],
                "input": bm["input"],
                "output": bm["output"],
                "cache_read": bm["cache_read"],
                "cache_write": bm["cache_write"],
                "avg_output_per_run": avg_out,
                "avg_tok_per_s": avg_tok_s,
                "estimated_cost": mc["total_cost"] if mc else 0,
            })

        # by_hour list sorted
        by_hour_list = []
        for h in sorted(by_hour.keys()):
            by_hour_list.append({"hour": h, "runs": by_hour[h]["runs"], "output": by_hour[h]["output"]})

        # top 5 expensive runs
        run_costs.sort(key=lambda x: x["output"], reverse=True)
        top5 = run_costs[:5]

        # efficiency
        total_tokens_all = total_cache_read + total_cache_write + total_input
        cache_hit = round(total_cache_read / total_tokens_all * 100, 1) if total_tokens_all > 0 else 0
        n_runs = len(runs)
        avg_out_per_run = round(total_output / n_runs) if n_runs > 0 else 0
        avg_infer_s = round(total_infer_ms / n_runs / 1000.0, 1) if n_runs > 0 else 0
        infer_s_total = total_infer_ms / 1000.0 if total_infer_ms > 0 else 1
        avg_tok_s = round(total_output / infer_s_total, 1) if total_output > 0 else 0
        total_time = total_infer_ms + total_tool_ms
        tool_overhead = round(total_tool_ms / total_time * 100, 1) if total_time > 0 else 0

        return {
            "date": date,
            "total_runs": n_runs,
            "token_totals": {
                "input": total_input,
                "output": total_output,
                "cache_read": total_cache_read,
                "cache_write": total_cache_write,
            },
            "cost_estimate": cost_est,
            "by_model": by_model_list,
            "by_hour": by_hour_list,
            "top_expensive_runs": top5,
            "efficiency": {
                "cache_hit_ratio": cache_hit,
                "avg_output_per_run": avg_out_per_run,
                "avg_inference_time_s": avg_infer_s,
                "avg_tok_per_s": avg_tok_s,
                "tool_overhead_ratio": tool_overhead,
            },
        }


# ============================================================
# 前端 HTML/CSS/JS
# ============================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 诊断面板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#1a1a2e;--bg2:#16213e;--bg3:#0f3460;
  --text:#e0e0e0;--text2:#a0a0b0;--accent:#4361ee;
  --green:#2ec4b6;--red:#e63946;--yellow:#f4a261;--orange:#e76f51;
  --card:#16213e;--border:#2a2a4a;--hover:#1e2a4a;
}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.5;min-height:100vh}
a{color:var(--accent);text-decoration:none}
.container{max-width:1400px;margin:0 auto;padding:16px}

/* 系统信息栏 */
.sysinfo-bar{background:var(--bg2);border:1px solid var(--border);border-radius:8px;margin-bottom:16px;overflow:hidden}
.sysinfo-header{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;cursor:pointer;font-size:13px;color:var(--text2);user-select:none}
.sysinfo-header:hover{background:var(--hover)}
.sysinfo-header .sysinfo-summary{display:flex;gap:16px;flex-wrap:wrap;align-items:center}
.sysinfo-header .sysinfo-summary span{color:var(--text)}
.sysinfo-header .sysinfo-summary .sep{color:var(--border)}
.sysinfo-header .toggle-icon{transition:transform .2s;font-size:11px}
.sysinfo-header.open .toggle-icon{transform:rotate(180deg)}
.sysinfo-detail{display:none;padding:12px 16px;border-top:1px solid var(--border);font-size:12px;color:var(--text2)}
.sysinfo-detail.open{display:block}
.sysinfo-detail .sysinfo-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 24px}
.sysinfo-detail .si-item{display:flex;gap:8px}
.sysinfo-detail .si-label{color:var(--text2);min-width:100px}
.sysinfo-detail .si-value{color:var(--text)}

/* 顶部栏 */
.header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:20px}
.header h1{font-size:1.5em;color:#fff;white-space:nowrap}
.header h1 span{color:var(--accent)}
.controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.controls select,.controls button{padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:14px;cursor:pointer}
.controls select:hover,.controls button:hover{border-color:var(--accent)}
.controls label{font-size:13px;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:4px}
.controls input[type=checkbox]{accent-color:var(--accent)}

/* 摘要卡片 */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px;text-align:center}
.card .value{font-size:1.8em;font-weight:700;color:#fff;margin:4px 0}
.card .label{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px}
.card.error .value{color:var(--red)}

/* Token 分析板块 */
.token-analysis{background:var(--bg2);border:1px solid var(--border);border-radius:10px;margin-bottom:24px;overflow:hidden}
.ta-header{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;cursor:pointer;user-select:none}
.ta-header:hover{background:var(--hover)}
.ta-header h2{font-size:1.1em;color:var(--accent);font-weight:600}
.ta-header .toggle-icon{color:var(--text2);transition:transform .2s;font-size:11px}
.ta-header.open .toggle-icon{transform:rotate(180deg)}
.ta-body{display:none;padding:0 18px 18px}
.ta-body.open{display:block}
.ta-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.ta-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center}
.ta-card .tc-val{font-size:1.5em;font-weight:700;color:#fff}
.ta-card .tc-lbl{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.ta-card.cost .tc-val{color:var(--yellow)}
.ta-section{margin-bottom:16px}
.ta-section h3{font-size:13px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;font-weight:600}

/* 柱状图 */
.bar-chart{display:flex;align-items:flex-end;gap:6px;height:120px;padding:0 4px}
.bar-col{display:flex;flex-direction:column;align-items:center;flex:1;min-width:30px}
.bar-col .bar{background:var(--accent);border-radius:3px 3px 0 0;min-height:2px;width:100%;max-width:40px;transition:height .3s}
.bar-col .bar-label{font-size:10px;color:var(--text2);margin-top:4px;white-space:nowrap}
.bar-col .bar-val{font-size:10px;color:var(--text);margin-bottom:2px}

/* 效率指标 */
.efficiency-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.eff-item{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:10px;text-align:center}
.eff-item .ev{font-size:1.3em;font-weight:700;color:#fff}
.eff-item .el{font-size:11px;color:var(--text2)}

/* 优化建议 */
.suggestions{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 16px}
.suggestions .sug-item{padding:6px 0;font-size:13px;color:var(--text);border-bottom:1px solid rgba(255,255,255,.05)}
.suggestions .sug-item:last-child{border-bottom:none}
.suggestions .sug-icon{margin-right:6px}

/* 表格 */
.table-wrap{overflow-x:auto;margin-bottom:20px}
.runs-scroll{max-height:600px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{background:var(--bg3);color:var(--text);padding:10px 12px;text-align:left;position:sticky;top:0;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px;z-index:2}
td{padding:10px 12px;border-bottom:1px solid var(--border)}
tr:nth-child(even) td{background:rgba(255,255,255,.02)}
tr.clickable{cursor:pointer;transition:background .15s}
tr.clickable:hover td{background:var(--hover)}
.status-ok{color:var(--green)}
.status-error{color:var(--red)}
.status-aborted{color:var(--yellow)}
.status-running{color:var(--accent);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.slow{color:var(--red);font-weight:600}
.medium{color:var(--yellow)}
.mono{font-family:"SF Mono",Monaco,Consolas,monospace;font-size:13px}

/* 分页 */
.pagination{display:flex;align-items:center;justify-content:center;gap:12px;padding:12px 0;font-size:13px;color:var(--text2)}
.pagination button{padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:13px;cursor:pointer}
.pagination button:hover:not(:disabled){border-color:var(--accent);color:#fff}
.pagination button:disabled{opacity:.4;cursor:not-allowed}
.pagination select{padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:13px}
.pagination .page-info{color:var(--text)}

/* Run 详情展开 */
.run-detail{display:none;background:var(--bg);border:1px solid var(--border);border-radius:8px;margin:4px 0 12px;padding:20px}
.run-detail.open{display:block}
.run-detail h3{color:var(--accent);margin-bottom:12px;font-size:1em;font-weight:600}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:800px){.detail-grid{grid-template-columns:1fr}}
.detail-section{background:var(--card);border-radius:8px;padding:14px;border:1px solid var(--border)}
.detail-section h4{color:var(--text2);font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}

/* 甘特图 */
.gantt{position:relative;background:var(--bg2);border-radius:6px;height:48px;margin-bottom:16px;overflow:hidden}
.gantt-bar{position:absolute;top:8px;height:32px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#fff;min-width:2px;overflow:hidden;white-space:nowrap;transition:opacity .15s}
.gantt-bar.infer{background:var(--accent)}
.gantt-bar.tool{background:var(--green)}
.gantt-bar:hover{opacity:.85;z-index:2}
.gantt-tooltip{display:none;position:absolute;bottom:52px;left:50%;transform:translateX(-50%);background:#000;color:#fff;padding:4px 8px;border-radius:4px;font-size:11px;white-space:nowrap;z-index:10;pointer-events:none}
.gantt-bar:hover .gantt-tooltip{display:block}
.gantt-legend{display:flex;gap:16px;margin-bottom:8px;font-size:12px;color:var(--text2)}
.gantt-legend span{display:flex;align-items:center;gap:4px}
.gantt-legend .dot{width:10px;height:10px;border-radius:2px;display:inline-block}
.gantt-legend .dot.infer{background:var(--accent)}
.gantt-legend .dot.tool{background:var(--green)}

/* 小表格 */
.detail-table{width:100%;border-collapse:collapse;font-size:13px}
.detail-table th{background:rgba(255,255,255,.05);padding:6px 8px;text-align:left;font-size:11px}
.detail-table td{padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.05)}
.detail-table td.tool-args{white-space:pre-wrap;word-break:break-all;font-family:"SF Mono",Monaco,Consolas,monospace;font-size:11px;max-width:500px}

/* 汇总条 */
.summary-bar{display:flex;gap:20px;flex-wrap:wrap;padding:12px 16px;background:var(--card);border-radius:8px;border:1px solid var(--border);margin-top:12px;font-size:13px}
.summary-bar .item{display:flex;flex-direction:column;align-items:center}
.summary-bar .item .val{font-size:1.2em;font-weight:700;color:#fff}
.summary-bar .item .lbl{font-size:11px;color:var(--text2)}

/* 空状态 */
.empty{text-align:center;padding:60px 20px;color:var(--text2)}
.empty .icon{font-size:3em;margin-bottom:12px}

/* 加载动画 */
.loading{text-align:center;padding:40px;color:var(--text2)}
.spinner{display:inline-block;width:24px;height:24px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;margin-right:8px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}

/* 占比条 */
.ratio-bar{display:flex;height:6px;border-radius:3px;overflow:hidden;background:var(--border);margin-top:4px}
.ratio-bar .fill-infer{background:var(--accent)}
.ratio-bar .fill-tool{background:var(--green)}
</style>
</head>
<body>
<div class="container">
  <div id="sysInfoBar" class="sysinfo-bar" style="display:none"></div>
  <div class="header">
    <h1>🔍 <span>OpenClaw</span> 诊断面板</h1>
    <div class="controls">
      <select id="dateSelect"></select>
      <button onclick="refresh()">🔄 刷新</button>
      <label>自动刷新:
        <select id="autoRefreshSelect">
          <option value="0">关闭</option>
          <option value="5000">5s</option>
          <option value="10000">10s</option>
          <option value="30000" selected>30s</option>
          <option value="60000">1min</option>
          <option value="300000">5min</option>
        </select>
      </label>
    </div>
  </div>
  <div id="summaryCards" class="cards"></div>
  <div id="tokenAnalysis"></div>
  <div id="content"></div>
</div>

<script>
(function(){
var BASE = '';
var currentDate = '';
var autoTimer = null;
var autoInterval = 30000;
var openRuns = {};
var currentPage = 1;
var perPage = 20;

function $(sel){return document.querySelector(sel)}
function $$(sel){return document.querySelectorAll(sel)}
function fmtMs(ms){
  if(ms===0||ms===undefined||ms===null) return '0ms';
  if(ms<1000) return ms+'ms';
  if(ms<60000) return (ms/1000).toFixed(1)+'s';
  return (ms/60000).toFixed(1)+'m';
}
function fmtTok(n){
  if(!n) return '0';
  if(n<1000) return n.toString();
  if(n<1000000) return (n/1000).toFixed(1)+'k';
  return (n/1000000).toFixed(2)+'M';
}
function fmtCost(n){
  if(!n && n!==0) return '-';
  if(n<0.01) return '$'+n.toFixed(4);
  return '$'+n.toFixed(2);
}
function speedClass(ms){
  if(ms>5000) return 'slow';
  if(ms>1000) return 'medium';
  return '';
}
function statusClass(s){return 'status-'+s}
function statusIcon(s){
  var m={'ok':'✅','error':'❌','aborted':'⚠️','running':'🔄'};
  return m[s]||s;
}
function escHtml(s){
  if(!s) return '';
  var d=document.createElement('div');d.textContent=s;return d.innerHTML;
}

function api(path,cb){
  var x=new XMLHttpRequest();
  x.open('GET',BASE+path);
  x.onload=function(){
    if(x.status===200){try{cb(JSON.parse(x.responseText))}catch(e){cb(null)}}
    else{cb(null)}
  };
  x.onerror=function(){cb(null)};
  x.send();
}

// ---- System Info ----
function loadSystemInfo(){
  api('/api/system_info',function(info){
    if(!info) return;
    var bar=$('#sysInfoBar');
    bar.style.display='block';
    var ver=info.openclaw_version||'?';
    var model=info.default_model||'?';
    // shorten model name for display
    var modelShort=model.split('.').pop().replace(/-v\d+$/,'');
    var channels=(info.channels||[]).join(', ')||'-';
    var host=info.hostname||'?';

    var html='<div class="sysinfo-header" onclick="toggleSysInfo(this)">';
    html+='<div class="sysinfo-summary">';
    html+='<span>🟢 OpenClaw <strong>'+escHtml(ver)+'</strong></span>';
    html+='<span class="sep">|</span>';
    html+='<span>Model: <strong>'+escHtml(modelShort)+'</strong></span>';
    html+='<span class="sep">|</span>';
    html+='<span>Channels: <strong>'+escHtml(channels)+'</strong></span>';
    html+='<span class="sep">|</span>';
    html+='<span>Host: <strong>'+escHtml(host)+'</strong></span>';
    html+='</div>';
    html+='<span class="toggle-icon">▼</span>';
    html+='</div>';
    html+='<div class="sysinfo-detail"><div class="sysinfo-grid">';
    var items=[
      ['版本',info.openclaw_version],
      ['配置文件',info.openclaw_config_path],
      ['诊断',info.diagnostics_enabled?'已开启':'未开启'],
      ['日志级别',info.logging_level],
      ['默认模型',info.default_model],
      ['Agents',(info.agents||[]).join(', ')],
      ['Channels',(info.channels||[]).join(', ')],
      ['Python',info.python_version],
      ['平台',info.platform],
      ['主机名',info.hostname],
      ['CPU',info.cpu_count+' 核'],
      ['内存',info.memory_used_mb+'MB / '+info.memory_total_mb+'MB'],
      ['日志目录',info.log_dir],
      ['日志文件数',info.log_file_count],
      ['会话目录数',info.sessions_dir_count],
      ['会话文件数',info.session_file_count],
    ];
    items.forEach(function(it){
      html+='<div class="si-item"><span class="si-label">'+escHtml(it[0])+'</span><span class="si-value">'+escHtml(String(it[1]||'-'))+'</span></div>';
    });
    html+='</div></div>';
    bar.innerHTML=html;
  });
}
window.toggleSysInfo=function(el){
  el.classList.toggle('open');
  var detail=el.nextElementSibling;
  if(detail) detail.classList.toggle('open');
};

// ---- Dates ----
function loadDates(){
  api('/api/dates',function(dates){
    var sel=$('#dateSelect');
    sel.innerHTML='';
    if(!dates||dates.length===0){
      sel.innerHTML='<option>无数据</option>';
      showEmpty();
      return;
    }
    dates.forEach(function(d){
      var o=document.createElement('option');
      o.value=d;o.textContent=d;
      sel.appendChild(o);
    });
    currentDate=dates[0];
    sel.value=currentDate;
    loadData();
  });
}

function showEmpty(){
  $('#summaryCards').innerHTML='';
  $('#tokenAnalysis').innerHTML='';
  $('#content').innerHTML='<div class="empty"><div class="icon">📭</div><p>暂无诊断数据</p><p style="margin-top:8px;font-size:13px">等待 OpenClaw 生成日志后自动显示</p></div>';
}

function showLoading(){
  $('#content').innerHTML='<div class="loading"><span class="spinner"></span>加载中...</div>';
}

function loadData(){
  showLoading();
  var d=currentDate;
  api('/api/summary?date='+d,function(summary){
    renderSummary(summary);
  });
  api('/api/token_analysis?date='+d,function(ta){
    renderTokenAnalysis(ta);
  });
  api('/api/runs?date='+d+'&page='+currentPage+'&per_page='+perPage,function(data){
    renderRunsList(data);
  });
}

function renderSummary(s){
  if(!s||s.total_runs===0){
    $('#summaryCards').innerHTML='';
    return;
  }
  var html='';
  html+='<div class="card"><div class="label">Run 总数</div><div class="value">'+s.total_runs+'</div></div>';
  html+='<div class="card"><div class="label">平均耗时</div><div class="value">'+fmtMs(s.avg_duration_ms)+'</div></div>';
  html+='<div class="card"><div class="label">推理占比</div><div class="value">'+s.infer_ratio+'%</div><div class="ratio-bar"><div class="fill-infer" style="width:'+s.infer_ratio+'%"></div><div class="fill-tool" style="width:'+(100-s.infer_ratio)+'%"></div></div></div>';
  html+='<div class="card"><div class="label">总输出 Token</div><div class="value">'+fmtTok(s.total_tokens_output)+'</div></div>';
  var errCls=s.error_count>0?' error':'';
  html+='<div class="card'+errCls+'"><div class="label">错误数</div><div class="value">'+s.error_count+'</div></div>';
  $('#summaryCards').innerHTML=html;
}

// ---- Token Analysis ----
function renderTokenAnalysis(ta){
  var el=$('#tokenAnalysis');
  if(!ta||ta.total_runs===0){el.innerHTML='';return;}
  var tt=ta.token_totals||{};
  var ce=ta.cost_estimate||{};
  var eff=ta.efficiency||{};
  var html='<div class="token-analysis">';
  html+='<div class="ta-header open" onclick="toggleTA(this)"><h2>💰 Token 消耗与成本分析</h2><span class="toggle-icon">▼</span></div>';
  html+='<div class="ta-body open">';

  // Cards
  html+='<div class="ta-cards">';
  html+='<div class="ta-card"><div class="tc-val">'+fmtTok(tt.input)+'</div><div class="tc-lbl">总输入</div></div>';
  html+='<div class="ta-card"><div class="tc-val">'+fmtTok(tt.output)+'</div><div class="tc-lbl">总输出</div></div>';
  html+='<div class="ta-card"><div class="tc-val">'+eff.cache_hit_ratio+'%</div><div class="tc-lbl">缓存命中率</div></div>';
  html+='<div class="ta-card cost"><div class="tc-val">'+fmtCost(ce.total_cost)+'</div><div class="tc-lbl">预估费用</div></div>';
  html+='</div>';

  // By hour chart
  if(ta.by_hour && ta.by_hour.length>0){
    html+='<div class="ta-section"><h3>📊 按小时分布</h3>';
    var maxOut=0;
    ta.by_hour.forEach(function(h){if(h.output>maxOut)maxOut=h.output;});
    if(maxOut===0)maxOut=1;
    html+='<div class="bar-chart">';
    ta.by_hour.forEach(function(h){
      var pct=Math.max(2,Math.round(h.output/maxOut*100));
      html+='<div class="bar-col">';
      html+='<div class="bar-val">'+h.runs+'</div>';
      html+='<div class="bar" style="height:'+pct+'%"></div>';
      html+='<div class="bar-label">'+h.hour+'</div>';
      html+='</div>';
    });
    html+='</div></div>';
  }

  // By model table
  if(ta.by_model && ta.by_model.length>0){
    html+='<div class="ta-section"><h3>🤖 按模型分布</h3>';
    html+='<table class="detail-table"><thead><tr><th>模型</th><th>Run 数</th><th>输出 Token</th><th>平均速率</th><th>预估费用</th></tr></thead><tbody>';
    ta.by_model.forEach(function(m){
      html+='<tr><td>'+escHtml(m.model)+'</td><td>'+m.runs+'</td><td>'+fmtTok(m.output)+'</td><td>'+m.avg_tok_per_s+' tok/s</td><td>'+fmtCost(m.estimated_cost)+'</td></tr>';
    });
    html+='</tbody></table></div>';
  }

  // Top 5 expensive
  if(ta.top_expensive_runs && ta.top_expensive_runs.length>0){
    html+='<div class="ta-section"><h3>🔥 Top 5 高消耗 Run</h3>';
    html+='<table class="detail-table"><thead><tr><th>Run ID</th><th>时间</th><th>输出 Token</th><th>耗时</th><th>工具数</th><th>预估费用</th></tr></thead><tbody>';
    ta.top_expensive_runs.forEach(function(r){
      var sid=r.run_id.substring(0,8);
      html+='<tr class="clickable" onclick="scrollToRun(\''+escHtml(r.run_id)+'\')">';
      html+='<td class="mono" title="'+escHtml(r.run_id)+'">'+escHtml(sid)+'</td>';
      html+='<td class="mono">'+escHtml(r.start)+'</td>';
      html+='<td>'+fmtTok(r.output)+'</td>';
      html+='<td>'+fmtMs(r.duration_ms)+'</td>';
      html+='<td>'+r.tool_count+'</td>';
      html+='<td>'+fmtCost(r.estimated_cost)+'</td>';
      html+='</tr>';
    });
    html+='</tbody></table></div>';
  }

  // Efficiency
  html+='<div class="ta-section"><h3>⚡ 效率指标</h3>';
  html+='<div class="efficiency-grid">';
  html+='<div class="eff-item"><div class="ev">'+eff.cache_hit_ratio+'%</div><div class="el">缓存命中率</div></div>';
  html+='<div class="eff-item"><div class="ev">'+eff.avg_output_per_run+'</div><div class="el">平均每 Run 输出</div></div>';
  html+='<div class="eff-item"><div class="ev">'+eff.avg_tok_per_s+' tok/s</div><div class="el">平均推理速率</div></div>';
  html+='<div class="eff-item"><div class="ev">'+eff.tool_overhead_ratio+'%</div><div class="el">工具开销占比</div></div>';
  html+='</div></div>';

  // Suggestions
  var sugs=[];
  if(eff.cache_hit_ratio<50) sugs.push({icon:'💡',text:'缓存命中率较低 ('+eff.cache_hit_ratio+'%), 考虑减少 /reset 频率以保持缓存'});
  if(eff.avg_tok_per_s>0 && eff.avg_tok_per_s<20) sugs.push({icon:'🚀',text:'输出速率较慢 ('+eff.avg_tok_per_s+' tok/s), 考虑切换更快的模型（如 Sonnet）'});
  if(eff.tool_overhead_ratio>20) sugs.push({icon:'🔧',text:'工具执行开销较大 ('+eff.tool_overhead_ratio+'%), 检查是否有慢工具可优化'});
  if(ce.total_cost>5) sugs.push({icon:'💸',text:'今日预估费用较高 ('+fmtCost(ce.total_cost)+'), 考虑对非关键对话使用更经济的模型'});
  if(sugs.length>0){
    html+='<div class="ta-section"><h3>💡 优化建议</h3><div class="suggestions">';
    sugs.forEach(function(s){
      html+='<div class="sug-item"><span class="sug-icon">'+s.icon+'</span>'+escHtml(s.text)+'</div>';
    });
    html+='</div></div>';
  }

  html+='</div></div>';
  el.innerHTML=html;
}
window.toggleTA=function(el){
  el.classList.toggle('open');
  var body=el.nextElementSibling;
  if(body) body.classList.toggle('open');
};
window.scrollToRun=function(rid){
  var row=document.querySelector('tr[data-runid="'+rid+'"]');
  if(row){
    row.scrollIntoView({behavior:'smooth',block:'center'});
    row.style.background='var(--bg3)';
    setTimeout(function(){row.style.background='';},2000);
  }
};

// ---- Runs List ----
function renderRunsList(data){
  if(!data){$('#content').innerHTML='<div class="empty"><div class="icon">📭</div><p>加载失败</p></div>';return;}
  var runs=data.runs||[];
  var total=data.total||0;
  var page=data.page||1;
  var pp=data.per_page||20;
  var totalPages=data.total_pages||1;
  currentPage=page;

  if(total===0){
    $('#content').innerHTML='<div class="empty"><div class="icon">📭</div><p>该日期暂无 Run 数据</p></div>';
    return;
  }
  var colSpan=12;
  var html='<div class="table-wrap"><div class="runs-scroll" id="runsScroll"><table><thead><tr>';
  html+='<th>开始</th><th>结束</th><th>Run ID</th><th>模型</th><th>通道</th><th>端到端</th><th>推理</th><th>工具</th><th>工具数</th><th>输出Token</th><th>状态</th>';
  html+='</tr></thead><tbody>';
  runs.forEach(function(r){
    var durCls=speedClass(r.duration_ms);
    var short_id=r.run_id.substring(0,8);
    html+='<tr class="clickable" data-runid="'+escHtml(r.run_id)+'" onclick="toggleRun(this)">';
    html+='<td class="mono">'+escHtml(r.start)+'</td>';
    html+='<td class="mono">'+escHtml(r.end||'-')+'</td>';
    html+='<td class="mono" title="'+escHtml(r.run_id)+'">'+escHtml(short_id)+'</td>';
    html+='<td>'+escHtml(shortModel(r.model))+'</td>';
    html+='<td>'+escHtml(r.channel)+'</td>';
    html+='<td class="'+durCls+'">'+fmtMs(r.duration_ms)+'</td>';
    html+='<td>'+fmtMs(r.infer_ms)+'</td>';
    html+='<td>'+fmtMs(r.tool_ms)+'</td>';
    html+='<td>'+r.tool_count+'</td>';
    html+='<td>'+fmtTok(r.token_output)+'</td>';
    html+='<td class="'+statusClass(r.status)+'">'+statusIcon(r.status)+'</td>';
    html+='</tr>';
    html+='<tr class="detail-row"><td colspan="'+colSpan+'"><div class="run-detail" id="detail-'+escHtml(r.run_id)+'"></div></td></tr>';
  });
  html+='</tbody></table></div>';

  // Pagination
  html+='<div class="pagination">';
  html+='<button onclick="goPage('+(page-1)+')"'+(page<=1?' disabled':'')+'>◀ 上一页</button>';
  html+='<span class="page-info">第 '+page+' / '+totalPages+' 页 (共 '+total+' 条)</span>';
  html+='<button onclick="goPage('+(page+1)+')"'+(page>=totalPages?' disabled':'')+'>下一页 ▶</button>';
  html+='<select onchange="changePerPage(this.value)">';
  [20,50,100].forEach(function(n){
    html+='<option value="'+n+'"'+(n===pp?' selected':'')+'>'+n+' 条/页</option>';
  });
  html+='</select>';
  html+='</div></div>';

  $('#content').innerHTML=html;
  // Restore open details
  Object.keys(openRuns).forEach(function(rid){
    var el=document.getElementById('detail-'+rid);
    if(el){
      el.classList.add('open');
      loadRunDetail(rid,el);
    }
  });
}

window.goPage=function(p){
  currentPage=p;
  var sc=$('#runsScroll');
  if(sc) sc.scrollTop=0;
  api('/api/runs?date='+currentDate+'&page='+currentPage+'&per_page='+perPage,function(data){
    renderRunsList(data);
  });
};
window.changePerPage=function(v){
  perPage=parseInt(v)||20;
  currentPage=1;
  api('/api/runs?date='+currentDate+'&page=1&per_page='+perPage,function(data){
    renderRunsList(data);
  });
};

function shortModel(m){
  if(!m) return '';
  var parts=m.split('.');
  var last=parts[parts.length-1];
  return last.replace(/-v\d+$/,'');
}

window.toggleRun=function(tr){
  var rid=tr.getAttribute('data-runid');
  var el=document.getElementById('detail-'+rid);
  if(!el) return;
  if(el.classList.contains('open')){
    el.classList.remove('open');
    delete openRuns[rid];
  }else{
    el.classList.add('open');
    openRuns[rid]=true;
    loadRunDetail(rid,el);
  }
};

function loadRunDetail(rid,el){
  el.innerHTML='<div class="loading"><span class="spinner"></span>加载详情...</div>';
  api('/api/run/'+rid+'?date='+currentDate,function(d){
    if(!d){el.innerHTML='<p>加载失败</p>';return;}
    renderRunDetail(d,el);
  });
}

function renderRunDetail(d,el){
  var html='';

  // Timing info
  html+='<div style="margin-bottom:12px;font-size:13px;color:var(--text2)">';
  html+='开始: <strong style="color:var(--text)">'+escHtml(d.start)+'</strong>';
  html+=' &nbsp;结束: <strong style="color:var(--text)">'+escHtml(d.end||'-')+'</strong>';
  html+=' &nbsp;输出速率: <strong style="color:var(--text)">'+(d.overall_tok_per_s||0)+' tok/s</strong>';
  html+='</div>';

  // Gantt
  html+='<div class="gantt-legend"><span><span class="dot infer"></span>推理</span><span><span class="dot tool"></span>工具</span><span style="margin-left:auto;font-size:11px;color:var(--text2)">总耗时: '+fmtMs(d.duration_ms)+'</span></div>';
  html+='<div class="gantt">';
  if(d.gantt){
    d.gantt.forEach(function(g){
      var cls=g.type==='infer'?'infer':'tool';
      var w=Math.max(g.width_pct,0.5);
      html+='<div class="gantt-bar '+cls+'" style="left:'+g.offset_pct+'%;width:'+w+'%">';
      if(w>5) html+='<span style="padding:0 4px;overflow:hidden;text-overflow:ellipsis">'+escHtml(g.label)+'</span>';
      html+='<div class="gantt-tooltip">'+escHtml(g.label)+' — '+fmtMs(g.duration_ms)+'</div>';
      html+='</div>';
    });
  }
  html+='</div>';

  html+='<div class="detail-grid">';

  // Infer segments
  html+='<div class="detail-section"><h4>推理分段</h4>';
  if(d.infer_segments && d.infer_segments.length>0){
    html+='<table class="detail-table"><thead><tr><th>阶段</th><th>耗时</th><th>输出 Token</th><th>速率</th></tr></thead><tbody>';
    d.infer_segments.forEach(function(s){
      var dc=speedClass(s.duration_ms);
      html+='<tr><td>'+escHtml(s.label)+'</td><td class="'+dc+'">'+fmtMs(s.duration_ms)+'</td><td>'+s.output_tokens+'</td><td>'+(s.tok_per_s>0?s.tok_per_s+' tok/s':'-')+'</td></tr>';
    });
    html+='</tbody></table>';
  }else{
    html+='<p style="color:var(--text2)">无推理数据</p>';
  }
  html+='</div>';

  // Tools
  html+='<div class="detail-section"><h4>工具调用 ('+d.tool_count+')</h4>';
  if(d.tools && d.tools.length>0){
    html+='<table class="detail-table"><thead><tr><th>工具</th><th>参数</th><th>耗时</th></tr></thead><tbody>';
    d.tools.forEach(function(t){
      var dc=speedClass(t.duration_ms);
      var argText=t.arguments_summary||'';
      html+='<tr><td><strong>'+escHtml(t.tool)+'</strong></td><td class="tool-args" title="'+escHtml(argText)+'">'+escHtml(argText)+'</td><td class="'+dc+'">'+fmtMs(t.duration_ms)+'</td></tr>';
    });
    html+='</tbody></table>';
  }else{
    html+='<p style="color:var(--text2)">无工具调用</p>';
  }
  html+='</div>';

  html+='</div>';

  // Summary bar
  html+='<div class="summary-bar">';
  html+='<div class="item"><div class="val">'+fmtMs(d.duration_ms)+'</div><div class="lbl">端到端</div></div>';
  html+='<div class="item"><div class="val">'+fmtMs(d.infer_ms)+'</div><div class="lbl">推理总耗时</div></div>';
  html+='<div class="item"><div class="val">'+fmtMs(d.tool_ms)+'</div><div class="lbl">工具总耗时</div></div>';
  html+='<div class="item"><div class="val">'+fmtTok(d.total_tokens_output)+'</div><div class="lbl">输出 Token</div></div>';
  html+='<div class="item"><div class="val">'+(d.overall_tok_per_s||0)+' tok/s</div><div class="lbl">输出速率</div></div>';
  html+='<div class="item"><div class="val">'+escHtml(d.model)+'</div><div class="lbl">模型</div></div>';
  html+='<div class="item"><div class="val">'+escHtml(d.channel)+'</div><div class="lbl">通道</div></div>';
  html+='</div>';

  // Token summary
  if(d.token_summary){
    var ts=d.token_summary;
    html+='<div style="margin-top:8px;font-size:12px;color:var(--text2)">';
    html+='Token: input='+fmtTok(ts.input)+' output='+fmtTok(ts.output)+' cacheRead='+fmtTok(ts.cacheRead)+' cacheWrite='+fmtTok(ts.cacheWrite);
    html+='</div>';
  }

  // Prompt info
  if(d.prompt_info && d.prompt_info.messages){
    html+='<div style="margin-top:4px;font-size:12px;color:var(--text2)">';
    html+='Prompt: messages='+escHtml(d.prompt_info.messages);
    if(d.prompt_info.historyTextChars) html+=' historyChars='+escHtml(d.prompt_info.historyTextChars);
    if(d.prompt_info.systemPromptChars) html+=' sysPromptChars='+escHtml(d.prompt_info.systemPromptChars);
    html+='</div>';
  }

  el.innerHTML=html;
}

// Date change
$('#dateSelect').addEventListener('change',function(){
  currentDate=this.value;
  currentPage=1;
  openRuns={};
  loadData();
});

// Auto refresh
function setupAutoRefresh(ms){
  if(autoTimer){clearInterval(autoTimer);autoTimer=null;}
  autoInterval=ms;
  if(ms>0){
    autoTimer=setInterval(function(){loadData()},ms);
  }
}
$('#autoRefreshSelect').addEventListener('change',function(){
  setupAutoRefresh(parseInt(this.value)||0);
});

window.refresh=function(){loadData()};

// Start
loadSystemInfo();
loadDates();
// Start default auto-refresh
setupAutoRefresh(autoInterval);
})();
</script>
</body>
</html>"""


# ============================================================
# HTTP 请求处理
# ============================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    data_store = None
    access_token = None
    config_path = None
    config_data = None

    def log_message(self, format, *args):
        pass

    def _check_token(self, params):
        if not self.access_token:
            return True
        tokens = params.get("token", [])
        if tokens and tokens[0] == self.access_token:
            return True
        return False

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if not self._check_token(params):
            self._send_json({"error": "Unauthorized"}, 403)
            return

        try:
            if path == "/":
                self._send_html(HTML_PAGE)
            elif path == "/api/dates":
                dates = self.data_store.get_dates()
                self._send_json(dates)
            elif path == "/api/system_info":
                info = get_system_info(self.data_store, self.config_path, self.config_data)
                self._send_json(info)
            elif path == "/api/summary":
                date = params.get("date", [""])[0]
                if not date:
                    dates = self.data_store.get_dates()
                    date = dates[0] if dates else ""
                if not date:
                    self._send_json({"total_runs": 0})
                    return
                summary = self.data_store.get_summary(date)
                self._send_json(summary)
            elif path == "/api/runs":
                date = params.get("date", [""])[0]
                if not date:
                    self._send_json({"runs": [], "total": 0, "page": 1, "per_page": 20, "total_pages": 1})
                    return
                page = 1
                per_page = 20
                try:
                    page = int(params.get("page", ["1"])[0])
                except (ValueError, IndexError):
                    pass
                try:
                    per_page = int(params.get("per_page", ["20"])[0])
                except (ValueError, IndexError):
                    pass
                per_page = max(1, min(per_page, 500))
                result = self.data_store.get_runs_list(date, page, per_page)
                self._send_json(result)
            elif path == "/api/token_analysis":
                date = params.get("date", [""])[0]
                if not date:
                    self._send_json({"total_runs": 0})
                    return
                analysis = self.data_store.get_token_analysis(date)
                self._send_json(analysis)
            elif path.startswith("/api/run/"):
                run_id = path[len("/api/run/"):]
                date = params.get("date", [""])[0]
                if not date or not run_id:
                    self._send_json({"error": "missing date or run_id"}, 400)
                    return
                detail = self.data_store.get_run_detail(date, run_id)
                if detail is None:
                    self._send_json({"error": "run not found"}, 404)
                else:
                    self._send_json(detail)
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)


# ============================================================
# 主程序
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw 诊断面板 v%s — 零依赖 Web Dashboard" % VERSION
    )
    parser.add_argument("--port", type=int, default=9090, help="监听端口 (默认 9090)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)")
    parser.add_argument("--log-dir", type=str, default="", help="日志目录")
    parser.add_argument("--sessions-dir", type=str, default="", help="会话文件目录")
    parser.add_argument("--token", type=str, default="", help="访问令牌 (可选)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    # 路径检测
    log_dir = detect_log_dir(args.log_dir)
    sessions_dirs = detect_sessions_dirs(args.sessions_dir)

    # 检测 OpenClaw 配置
    config_ok, config_warnings, config_path, config_data = check_openclaw_config()

    # 打印启动信息
    print("=" * 50)
    print("OpenClaw 诊断面板 v%s" % VERSION)
    print("=" * 50)
    print("Python: %s" % platform.python_version())
    print("平台: %s" % platform.platform())

    if config_ok:
        print("OpenClaw 配置: 诊断已开启")
    for w in config_warnings:
        print(w)

    if os.path.isdir(log_dir):
        log_files = glob.glob(os.path.join(log_dir, "openclaw-*.log"))
        print("日志目录: %s (找到 %d 个日志文件)" % (log_dir, len(log_files)))
    else:
        print("[警告] 日志目录 %s 不存在, 等待日志生成..." % log_dir)

    if sessions_dirs:
        total_sessions = 0
        for d in sessions_dirs:
            total_sessions += len(glob.glob(os.path.join(d, "*.jsonl")))
        print("会话目录: %s (%d 个会话文件)" % (", ".join(sessions_dirs), total_sessions))
    else:
        print("[警告] 会话目录未找到, Token 数据将不可用")

    # 初始化数据存储
    store = DataStore(log_dir, sessions_dirs)
    DashboardHandler.data_store = store
    DashboardHandler.access_token = args.token if args.token else None
    DashboardHandler.config_path = config_path
    DashboardHandler.config_data = config_data

    # 信号处理
    def signal_handler(sig, frame):
        print("\n正在退出...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    try:
        signal.signal(signal.SIGTERM, signal_handler)
    except (OSError, AttributeError):
        pass

    # 绑定服务器
    host = args.host
    port = args.port
    server = None

    if host == "::":
        try:
            class IPv6Server(HTTPServer):
                address_family = socket.AF_INET6
            server = IPv6Server((host, port), DashboardHandler)
        except (OSError, socket.error) as e:
            print("[警告] IPv6 绑定失败 (%s), 回退到 IPv4..." % e)
            host = "0.0.0.0"

    if server is None:
        try:
            server = HTTPServer((host, port), DashboardHandler)
        except (OSError, socket.error) as e:
            print("[错误] 端口 %d 绑定失败: %s" % (port, e))
            print("请尝试其他端口: python3 %s --port %d" % (sys.argv[0], port + 1))
            sys.exit(1)

    if host in ("0.0.0.0", "::"):
        display_host = "127.0.0.1"
    else:
        display_host = host
    url = "http://%s:%d" % (display_host, port)
    if args.token:
        url += "?token=%s" % args.token

    print("监听: %s" % url)
    print("按 Ctrl+C 退出")
    print("=" * 50)

    if not args.no_browser:
        try:
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
