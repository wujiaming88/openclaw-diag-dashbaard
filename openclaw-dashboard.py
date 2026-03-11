#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw 诊断面板 v1.2
Python 后端 API 服务 + 静态文件服务
前端文件位于 static/ 目录

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
VERSION = "1.2.0"
MAX_LOG_LINES = 50000  # 大文件只解析最后 N 行

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
    """计算推理分段 [{start, end, duration_ms, label, tool_indices}]

    Batched tool calls (gap between consecutive tool_end → tool_start < 500ms)
    are merged into one inference segment.  ``tool_indices`` lists the indices
    into *sorted_tools* that belong to each segment so callers can associate
    the correct usage records.
    """
    BATCH_GAP_MS = 500
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
            "tool_indices": [],
        })
        return segments

    # --- Group tools into batches (gap < 500ms means same batch) ---
    batches = []  # each batch is a list of indices into sorted_tools
    current_batch = [0]
    for i in range(1, len(sorted_tools)):
        prev_end = sorted_tools[i - 1].get("end")
        cur_start = sorted_tools[i].get("start")
        if prev_end and cur_start and ms_between(prev_end, cur_start) < BATCH_GAP_MS:
            current_batch.append(i)
        else:
            batches.append(current_batch)
            current_batch = [i]
    batches.append(current_batch)

    # --- Build inference segments from batches ---
    seg_num = 1
    # Segment before first batch
    first_tool = sorted_tools[batches[0][0]]
    seg_end = first_tool["start"]
    segments.append({
        "label": "推理 #%d" % seg_num,
        "start": agent_start,
        "end": seg_end,
        "duration_ms": ms_between(agent_start, seg_end),
        "tool_indices": batches[0],
    })
    seg_num += 1

    # Segments between batches
    for b in range(len(batches) - 1):
        last_idx_in_batch = batches[b][-1]
        first_idx_next_batch = batches[b + 1][0]
        t_end = sorted_tools[last_idx_in_batch].get("end")
        t_next_start = sorted_tools[first_idx_next_batch].get("start")
        if t_end and t_next_start:
            segments.append({
                "label": "推理 #%d" % seg_num,
                "start": t_end,
                "end": t_next_start,
                "duration_ms": ms_between(t_end, t_next_start),
                "tool_indices": batches[b + 1],
            })
            seg_num += 1

    # Final segment after last batch
    last_batch = batches[-1]
    last_tool = sorted_tools[last_batch[-1]]
    last_end = last_tool.get("end")
    final_end = agent_end or run.get("end")
    if last_end and final_end:
        segments.append({
            "label": "推理 #%d (生成回复)" % seg_num,
            "start": last_end,
            "end": final_end,
            "duration_ms": ms_between(last_end, final_end),
            "tool_indices": [],
        })
    return segments



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
        # Sort by start time descending (newest first)
        all_results.sort(key=lambda r: r["start"], reverse=True)
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
            sorted_tools = sorted([t for t in run.get("tools", []) if t.get("start")], key=lambda t: t["start"])
            tool_indices = s.get("tool_indices", [])

            if tool_indices:
                # Collect usage from all tools in this batch, deduplicating
                for ti in tool_indices:
                    if ti < len(sorted_tools):
                        tc_id = sorted_tools[ti].get("toolCallId", "")
                        td = tool_data.get(tc_id, {})
                        u = td.get("usage", {})
                        if u and id(u) not in seen_usage_ids:
                            seen_usage_ids.add(id(u))
                            output_tokens += u.get("output", 0)
                            total_input += u.get("input", 0)
                            total_output += u.get("output", 0)
                            total_cache_read += u.get("cacheRead", 0)
                            total_cache_write += u.get("cacheWrite", 0)
                            usage_rec = u  # keep last for reference
            elif run.get("end"):
                # Final segment (生成回复) or no-tool run: find text reply usage
                for ts_str, u in text_reply_usage:
                    if run_start_str and run_end_str:
                        if ts_str[:19] >= run_start_str and ts_str[:19] <= run_end_str:
                            if id(u) not in seen_usage_ids:
                                usage_rec = u
                                output_tokens = u.get("output", 0)
                                seen_usage_ids.add(id(u))
                                total_input += u.get("input", 0)
                                total_output += u.get("output", 0)
                                total_cache_read += u.get("cacheRead", 0)
                                total_cache_write += u.get("cacheWrite", 0)
                                break

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


# ============================================================
# HTTP 请求处理
# ============================================================

# Content-Type 映射
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    data_store = None
    access_token = None
    config_path = None
    config_data = None
    static_dir = None  # 静态文件目录

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

    def _send_file(self, filepath, content_type=None):
        """发送静态文件"""
        if not os.path.isfile(filepath):
            self.send_error(404, "Not Found")
            return
        if content_type is None:
            ext = os.path.splitext(filepath)[1].lower()
            content_type = MIME_TYPES.get(ext, "application/octet-stream")
        try:
            with open(filepath, "rb") as f:
                body = f.read()
        except (IOError, OSError):
            self.send_error(500, "Read Error")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
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
            # 静态文件服务
            if path == "/":
                self._send_file(os.path.join(self.static_dir, "index.html"))
            elif path.startswith("/static/"):
                # 安全检查: 防止路径遍历
                rel = path[len("/static/"):]
                if ".." in rel or rel.startswith("/"):
                    self.send_error(403, "Forbidden")
                    return
                filepath = os.path.join(self.static_dir, rel)
                self._send_file(filepath)
            # API 路由
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
    # 静态文件目录: 脚本同级的 static/ 文件夹
    script_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(script_dir, "static")
    if not os.path.isdir(static_dir):
        print("[错误] 静态文件目录不存在: %s" % static_dir)
        sys.exit(1)
    DashboardHandler.static_dir = static_dir

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
