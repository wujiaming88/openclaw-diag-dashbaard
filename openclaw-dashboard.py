#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw 诊断面板 v1.0
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
import os
import platform
import re
import signal
import socket
import threading
import traceback
from collections import OrderedDict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ============================================================
# 全局常量
# ============================================================
VERSION = "1.0.0"
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
    """解析会话文件，建立 toolCallId -> {arguments, usage} 映射"""
    tool_data = {}  # toolCallId -> {tool, arguments_raw, arguments_summary, usage}
    if not sessions_dirs:
        return tool_data
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
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "toolCall":
                    continue
                tc_id = item.get("id", "")
                tc_name = item.get("name", "")
                tc_args_raw = item.get("arguments", "{}")
                if isinstance(tc_args_raw, dict):
                    # arguments 已经是 dict，无需二次解析
                    pass
                elif not isinstance(tc_args_raw, str):
                    tc_args_raw = str(tc_args_raw)
                summary = summarize_tool_args(tc_name, tc_args_raw)
                tool_data[tc_id] = {
                    "tool": tc_name,
                    "arguments_raw": tc_args_raw,
                    "arguments_summary": summary,
                    "usage": usage,
                }
    return tool_data


def summarize_tool_args(tool_name, args_raw):
    """提取工具参数的可读摘要"""
    if isinstance(args_raw, dict):
        args = args_raw
    elif isinstance(args_raw, str):
        try:
            args = json.loads(args_raw)
        except (json.JSONDecodeError, ValueError):
            return args_raw[:80] if args_raw else ""
    else:
        return str(args_raw)[:80] if args_raw else ""
    if not isinstance(args, dict):
        return str(args)[:80]
    if tool_name == "exec":
        cmd = args.get("command", "")
        first_line = cmd.split("\n")[0][:90]
        wd = args.get("workdir", "")
        if wd:
            return "%s  [cwd: %s]" % (first_line, wd)
        return first_line
    elif tool_name in ("read", "write"):
        p = args.get("path", "") or args.get("file_path", "")
        return p
    elif tool_name == "edit":
        p = args.get("path", "") or args.get("file_path", "")
        old = args.get("old_string", "") or args.get("oldText", "")
        preview = old[:60].replace("\n", "\\n") if old else ""
        return "%s  old: %s" % (p, preview)
    elif tool_name == "web_search":
        return args.get("query", "")
    elif tool_name == "web_fetch":
        return args.get("url", "")[:90]
    elif tool_name == "sessions_spawn":
        agent = args.get("agentId", "")
        task = args.get("task", "")[:60]
        return "agent=%s task=%s" % (agent, task)
    elif tool_name == "message":
        action = args.get("action", "")
        target = args.get("target", "")
        return "action=%s target=%s" % (action, target)
    elif tool_name == "browser":
        action = args.get("action", "")
        url = args.get("url", "")
        return "action=%s url=%s" % (action, url[:60])
    else:
        # 通用: 前几个 key=value
        parts = []
        for k, v in list(args.items())[:3]:
            vs = str(v)[:50]
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
        # 无工具调用
        end = agent_end or run.get("end") or agent_start
        segments.append({
            "label": "推理 #1 (生成回复)",
            "start": agent_start,
            "end": end,
            "duration_ms": ms_between(agent_start, end),
        })
        return segments
    # 推理 #1: agent_start -> 第一个 tool_start
    first_tool = sorted_tools[0]
    seg_end = first_tool["start"]
    segments.append({
        "label": "推理 #1",
        "start": agent_start,
        "end": seg_end,
        "duration_ms": ms_between(agent_start, seg_end),
    })
    # 推理 #2..N-1: tool_end[i] -> tool_start[i+1]
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
    # 推理 #N: 最后一个 tool_end -> agent_end
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
# 构建 API 数据
# ============================================================

class DataStore(object):
    """缓存和提供诊断数据"""

    def __init__(self, log_dir, sessions_dirs):
        self.log_dir = log_dir
        self.sessions_dirs = sessions_dirs
        self._cache = {}  # date -> runs dict
        self._tool_data = None
        self._tool_data_loaded = False
        self._lock = threading.Lock()

    def _get_tool_data(self):
        if not self._tool_data_loaded:
            with self._lock:
                if not self._tool_data_loaded:
                    self._tool_data = parse_session_files(self.sessions_dirs)
                    self._tool_data_loaded = True
        return self._tool_data or {}

    def get_dates(self):
        """返回可用的日期列表（降序）"""
        pattern = os.path.join(self.log_dir, "openclaw-*.log")
        files = glob.glob(pattern)
        dates = []
        for f in files:
            basename = os.path.basename(f)
            # openclaw-YYYY-MM-DD.log
            m = re.match(r"openclaw-(\d{4}-\d{2}-\d{2})\.log", basename)
            if m:
                dates.append(m.group(1))
        dates.sort(reverse=True)
        return dates

    def _load_runs(self, date):
        """加载某天的 run 数据（带缓存）"""
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
        """返回某天的摘要数据"""
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
            # 推理时间
            segs = compute_infer_segments(run)
            for s in segs:
                total_infer += s["duration_ms"]
            # 工具时间
            for t in run.get("tools", []):
                if t.get("start") and t.get("end"):
                    total_tool += ms_between(t["start"], t["end"])
                # token
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

    def get_runs_list(self, date):
        """返回某天的 run 列表"""
        runs = self._load_runs(date)
        tool_data = self._get_tool_data()
        result = []
        for run in runs.values():
            infer_segs = compute_infer_segments(run)
            infer_ms = sum(s["duration_ms"] for s in infer_segs)
            tool_ms = 0
            tool_count = len(run.get("tools", []))
            token_output = 0
            # 收集已关联的 usage id，避免重复计
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
            result.append({
                "run_id": run["run_id"],
                "start": start_str,
                "model": run.get("model", ""),
                "channel": run.get("channel", ""),
                "duration_ms": run.get("duration_ms", 0),
                "infer_ms": infer_ms,
                "tool_ms": tool_ms,
                "tool_count": tool_count,
                "token_output": token_output,
                "status": status,
            })
        return result

    def get_run_detail(self, date, run_id):
        """返回单个 run 的完整详情"""
        runs = self._load_runs(date)
        run = runs.get(run_id)
        if not run:
            return None
        tool_data = self._get_tool_data()
        # 推理分段
        infer_segs = compute_infer_segments(run)
        infer_list = []
        for i, s in enumerate(infer_segs):
            # 查找这段推理对应的 token（取下一个工具调用的 usage 或最后一段的 usage）
            output_tokens = 0
            if i < len(run.get("tools", [])):
                tc_id = run["tools"][i].get("toolCallId", "")
                td = tool_data.get(tc_id, {})
                usage = td.get("usage", {})
                output_tokens = usage.get("output", 0)
            elif run.get("tools"):
                # 最后一段推理，取最后一个工具之后的 — 可能在 assistant 纯文本回复中
                # 暂时用0
                pass
            dur_s = s["duration_ms"] / 1000.0 if s["duration_ms"] > 0 else 0.001
            tok_per_s = round(output_tokens / dur_s, 1) if output_tokens > 0 else 0
            infer_list.append({
                "label": s["label"],
                "duration_ms": s["duration_ms"],
                "output_tokens": output_tokens,
                "tok_per_s": tok_per_s,
            })
        # 工具列表
        tools_list = []
        total_tool_ms = 0
        total_tokens = 0
        seen_usage_ids = set()
        for t in run.get("tools", []):
            dur = 0
            if t.get("start") and t.get("end"):
                dur = ms_between(t["start"], t["end"])
            total_tool_ms += dur
            tc_id = t.get("toolCallId", "")
            td = tool_data.get(tc_id, {})
            usage = td.get("usage", {})
            uid = id(usage)
            if uid not in seen_usage_ids and usage:
                seen_usage_ids.add(uid)
                total_tokens += usage.get("output", 0)
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
        # 甘特图数据
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
        # 速率
        total_output_tok = total_tokens
        dur_s = total_dur / 1000.0 if total_dur > 0 else 1
        overall_tok_s = round(total_output_tok / dur_s, 1) if total_output_tok > 0 else 0
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
            "total_tokens_output": total_output_tok,
            "overall_tok_per_s": overall_tok_s,
            "status": "error" if run.get("is_error") else ("aborted" if run.get("aborted") else "ok"),
            "prompt_info": run.get("prompt_info", {}),
            "infer_segments": infer_list,
            "tools": tools_list,
            "gantt": gantt,
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

/* 表格 */
.table-wrap{overflow-x:auto;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{background:var(--bg3);color:var(--text);padding:10px 12px;text-align:left;position:sticky;top:0;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
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
  <div class="header">
    <h1>🔍 <span>OpenClaw</span> 诊断面板</h1>
    <div class="controls">
      <select id="dateSelect"></select>
      <button onclick="refresh()">🔄 刷新</button>
      <label><input type="checkbox" id="autoRefresh"> 自动刷新 (30s)</label>
    </div>
  </div>
  <div id="summaryCards" class="cards"></div>
  <div id="content"></div>
</div>

<script>
(function(){
var BASE = '';
var currentDate = '';
var autoTimer = null;
var openRuns = {};  // run_id -> true

// 工具函数
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
  return (n/1000).toFixed(1)+'k';
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

// API 调用
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

// 加载日期列表
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
  $('#content').innerHTML='<div class="empty"><div class="icon">📭</div><p>暂无诊断数据</p><p style="margin-top:8px;font-size:13px">等待 OpenClaw 生成日志后自动显示</p></div>';
}

function showLoading(){
  $('#content').innerHTML='<div class="loading"><span class="spinner"></span>加载中...</div>';
}

// 加载摘要 + 列表
function loadData(){
  showLoading();
  var d=currentDate;
  api('/api/summary?date='+d,function(summary){
    renderSummary(summary);
  });
  api('/api/runs?date='+d,function(runs){
    renderRunsList(runs);
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

function renderRunsList(runs){
  if(!runs||runs.length===0){
    $('#content').innerHTML='<div class="empty"><div class="icon">📭</div><p>该日期暂无 Run 数据</p></div>';
    return;
  }
  var html='<div class="table-wrap"><table><thead><tr>';
  html+='<th>时间</th><th>Run ID</th><th>模型</th><th>通道</th><th>端到端</th><th>推理</th><th>工具</th><th>工具数</th><th>输出Token</th><th>状态</th>';
  html+='</tr></thead><tbody>';
  runs.forEach(function(r){
    var durCls=speedClass(r.duration_ms);
    var short_id=r.run_id.substring(0,8);
    html+='<tr class="clickable" data-runid="'+escHtml(r.run_id)+'" onclick="toggleRun(this)">';
    html+='<td class="mono">'+escHtml(r.start)+'</td>';
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
    html+='<tr class="detail-row"><td colspan="10"><div class="run-detail" id="detail-'+escHtml(r.run_id)+'"></div></td></tr>';
  });
  html+='</tbody></table></div>';
  $('#content').innerHTML=html;
  // 恢复展开状态
  Object.keys(openRuns).forEach(function(rid){
    var el=document.getElementById('detail-'+rid);
    if(el){
      el.classList.add('open');
      loadRunDetail(rid,el);
    }
  });
}

function shortModel(m){
  if(!m) return '';
  // us.anthropic.claude-opus-4-6-v1 -> claude-opus-4-6
  var parts=m.split('.');
  var last=parts[parts.length-1];
  return last.replace(/-v\d+$/,'');
}

// 展开/收起 Run 详情
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

  // 甘特图
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

  // 推理分段
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

  // 工具调用
  html+='<div class="detail-section"><h4>工具调用 ('+d.tool_count+')</h4>';
  if(d.tools && d.tools.length>0){
    html+='<table class="detail-table"><thead><tr><th>工具</th><th>参数</th><th>耗时</th></tr></thead><tbody>';
    d.tools.forEach(function(t){
      var dc=speedClass(t.duration_ms);
      var argText=t.arguments_summary||'';
      if(argText.length>100) argText=argText.substring(0,100)+'...';
      html+='<tr><td><strong>'+escHtml(t.tool)+'</strong></td><td class="mono" style="font-size:11px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+escHtml(t.arguments_summary)+'">'+escHtml(argText)+'</td><td class="'+dc+'">'+fmtMs(t.duration_ms)+'</td></tr>';
    });
    html+='</tbody></table>';
  }else{
    html+='<p style="color:var(--text2)">无工具调用</p>';
  }
  html+='</div>';

  html+='</div>';

  // 汇总条
  html+='<div class="summary-bar">';
  html+='<div class="item"><div class="val">'+fmtMs(d.duration_ms)+'</div><div class="lbl">端到端</div></div>';
  html+='<div class="item"><div class="val">'+fmtMs(d.infer_ms)+'</div><div class="lbl">推理总耗时</div></div>';
  html+='<div class="item"><div class="val">'+fmtMs(d.tool_ms)+'</div><div class="lbl">工具总耗时</div></div>';
  html+='<div class="item"><div class="val">'+fmtTok(d.total_tokens_output)+'</div><div class="lbl">输出 Token</div></div>';
  html+='<div class="item"><div class="val">'+(d.overall_tok_per_s||0)+' tok/s</div><div class="lbl">平均输出速率</div></div>';
  html+='<div class="item"><div class="val">'+escHtml(d.model)+'</div><div class="lbl">模型</div></div>';
  html+='<div class="item"><div class="val">'+escHtml(d.channel)+'</div><div class="lbl">通道</div></div>';
  html+='</div>';

  // Prompt 信息
  if(d.prompt_info && d.prompt_info.messages){
    html+='<div style="margin-top:12px;font-size:12px;color:var(--text2)">';
    html+='Prompt: messages='+escHtml(d.prompt_info.messages);
    if(d.prompt_info.historyTextChars) html+=' historyChars='+escHtml(d.prompt_info.historyTextChars);
    if(d.prompt_info.systemPromptChars) html+=' sysPromptChars='+escHtml(d.prompt_info.systemPromptChars);
    html+='</div>';
  }

  el.innerHTML=html;
}

// 日期切换
$('#dateSelect').addEventListener('change',function(){
  currentDate=this.value;
  openRuns={};
  loadData();
});

// 自动刷新
$('#autoRefresh').addEventListener('change',function(){
  if(this.checked){
    autoTimer=setInterval(function(){loadData()},30000);
  }else{
    if(autoTimer){clearInterval(autoTimer);autoTimer=null;}
  }
});

// 全局刷新
window.refresh=function(){loadData()};

// 启动
loadDates();
})();
</script>
</body>
</html>"""


# ============================================================
# HTTP 请求处理
# ============================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    # 类属性，由 main 设置
    data_store = None
    access_token = None

    def log_message(self, format, *args):
        """静默日志或简化输出"""
        pass

    def _check_token(self, params):
        """检查访问令牌"""
        if not self.access_token:
            return True
        tokens = params.get("token", [])
        if tokens and tokens[0] == self.access_token:
            return True
        return False

    def _send_json(self, data, status=200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        """发送 HTML 响应"""
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
                    self._send_json([])
                    return
                runs = self.data_store.get_runs_list(date)
                self._send_json(runs)
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

    # 打印启动信息
    print("=" * 50)
    print("OpenClaw 诊断面板 v%s" % VERSION)
    print("=" * 50)
    print("Python: %s" % platform.python_version())
    print("平台: %s" % platform.platform())

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

    # 信号处理
    def signal_handler(sig, frame):
        print("\n正在退出...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    try:
        signal.signal(signal.SIGTERM, signal_handler)
    except (OSError, AttributeError):
        pass  # Windows 上 SIGTERM 可能不存在

    # 绑定服务器（IPv6 回退 IPv4）
    host = args.host
    port = args.port
    server = None
    tried_ipv6 = False

    if host == "::":
        # 尝试 IPv6
        tried_ipv6 = True
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

    # 计算显示地址
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

    # 打开浏览器
    if not args.no_browser:
        try:
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    # 启动服务
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
