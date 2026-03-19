#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw 诊断面板 v2.0
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
import gzip
import hashlib
import json
import math
import os
import platform
import re
import signal
import socket
import subprocess
import threading
import time
import traceback
from collections import OrderedDict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# ============================================================
# 全局常量
# ============================================================
VERSION = "3.0.0"
ADVANCED_MODE = False  # 全局模式标志，由 main() 设置
MAX_LOG_LINES = 50000  # 大文件只解析最后 N 行

# ============================================================
# 探测命令定义
# ============================================================

PROBES = {
    "health": {
        "cmd": ["openclaw", "health", "--json"],
        "timeout": 30,
        "format": "json",
        "label": "健康检查",
        "description": "频道状态、Agent列表、Session统计",
        "icon": "🏥",
    },
    "gateway_status": {
        "cmd": ["openclaw", "gateway", "status", "--json"],
        "timeout": 30,
        "format": "json",
        "label": "Gateway 状态",
        "description": "服务状态、PID、端口、RPC探测",
        "icon": "🌐",
    },
    "config_validate": {
        "cmd": ["openclaw", "config", "validate"],
        "timeout": 15,
        "format": "text",
        "label": "配置校验",
        "description": "校验配置文件语法和结构",
        "icon": "✅",
    },
    "doctor": {
        "cmd": ["openclaw", "doctor", "--non-interactive"],
        "timeout": 30,
        "format": "text",
        "label": "全面诊断",
        "description": "配置审计、安全检查、技能状态、会话锁",
        "icon": "🔬",
    },
    "update_status": {
        "cmd": ["openclaw", "update", "status"],
        "timeout": 30,
        "format": "text",
        "label": "版本状态",
        "description": "当前版本、更新通道、可用更新",
        "icon": "📦",
    },
    "models_status": {
        "cmd": ["openclaw", "models", "status"],
        "timeout": 30,
        "format": "text",
        "label": "模型状态",
        "description": "已配置模型、默认模型、fallback 列表",
        "icon": "🤖",
    },
}


def strip_ansi(text):
    """去除 ANSI 转义码"""
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)


def _utcnow_iso():
    """返回 UTC 当前时间的 ISO 格式字符串"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_json_block(text):
    """从可能混有非 JSON 行的文本中提取第一个完整 JSON 对象"""
    start = text.find('{')
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        if depth == 0:
            return text[start:i + 1]
    return text[start:]


def run_probe(probe_name):
    """执行探测命令，返回结果字典"""
    probe = PROBES.get(probe_name)
    if not probe:
        return {"ok": False, "error": "未知探测项: %s" % probe_name, "probe": probe_name}

    t0 = time.time()
    try:
        result = subprocess.run(
            probe["cmd"],
            capture_output=True,
            text=True,
            timeout=probe["timeout"],
        )
        duration_ms = int((time.time() - t0) * 1000)

        # 过滤 stderr 中的 [plugins] 行
        stdout = result.stdout
        stderr = "\n".join(
            line for line in result.stderr.splitlines()
            if not line.strip().startswith("[plugins]")
        ).strip()

        output = {}
        if probe["format"] == "json":
            json_str = extract_json_block(stdout)
            try:
                output["data"] = json.loads(json_str)
            except (json.JSONDecodeError, ValueError):
                output["raw"] = strip_ansi(stdout)
        else:
            output["raw"] = strip_ansi(stdout)

        return {
            "ok": result.returncode == 0,
            "probe": probe_name,
            "label": probe["label"],
            "description": probe["description"],
            "icon": probe.get("icon", ""),
            "format": probe["format"],
            "exit_code": result.returncode,
            "duration_ms": duration_ms,
            "output": output,
            "stderr": stderr if stderr else None,
            "timestamp": _utcnow_iso(),
        }
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "probe": probe_name,
            "label": probe["label"],
            "description": probe["description"],
            "icon": probe.get("icon", ""),
            "error": "命令超时 (%ds)" % probe["timeout"],
            "duration_ms": duration_ms,
            "timestamp": _utcnow_iso(),
        }
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "probe": probe_name,
            "label": probe["label"],
            "description": probe["description"],
            "icon": probe.get("icon", ""),
            "error": str(e),
            "duration_ms": duration_ms,
            "timestamp": _utcnow_iso(),
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


def parse_all_events(filepath):
    """解析所有诊断事件，返回分类事件字典"""
    events = {
        "webhooks": [],       # webhook received (telegram update) / channel events
        "messages": [],       # message queued/processed
        "queue": [],          # lane enqueue/dequeue/task done
        "sessions": [],       # session state/stuck
        "heartbeats": [],     # diagnostic heartbeat
        "errors": [],         # all error events
        "all_timeline": [],   # all events for timeline
    }
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
            msg = str(msg) if msg else ""
        ts_str = obj.get("time", "")
        ts = parse_time(ts_str)
        sub_str = obj.get("0", "")
        data = obj.get("2", {})

        # Parse subsystem
        subsystem = ""
        if isinstance(sub_str, str) and sub_str.startswith("{"):
            try:
                sub_obj = json.loads(sub_str)
                subsystem = sub_obj.get("subsystem", "")
            except (json.JSONDecodeError, ValueError):
                pass

        ts_display = ""
        if ts:
            ts_display = ts.strftime("%H:%M:%S.%f")[:-3]

        # === Capture ALL ERROR level logs ===
        log_level_name = obj.get("logLevelName", "")
        if log_level_name == "ERROR":
            source_file = ""
            path_field = obj.get("path", "")
            if isinstance(path_field, str) and path_field:
                source_file = path_field.split("/")[-1] if "/" in path_field else path_field
            detail_text = msg if msg else ""
            if isinstance(data, dict):
                # Include extra data fields for richer detail
                err_str = data.get("err", "")
                if err_str:
                    detail_text = detail_text + "\n" + str(err_str)
                msg_detail = data.get("message", "")
                if msg_detail and str(msg_detail) not in detail_text:
                    detail_text = detail_text + "\n" + str(msg_detail)
            # Truncate to 500 chars
            detail_text = detail_text[:500] if detail_text else ""
            # Determine error type
            err_type = "error.general"
            if "exec failed" in msg or "[tools] exec" in msg:
                err_type = "error.exec"
            elif "sendMessage failed" in msg or "telegram" in subsystem.lower():
                err_type = "error.telegram"
            elif "model" in msg.lower() or "provider" in msg.lower():
                err_type = "error.model"
            elif "reply failed" in msg:
                err_type = "error.reply"
            channel_name = ""
            if "telegram" in subsystem.lower() or "telegram" in msg.lower():
                channel_name = "telegram"
            error_evt = {
                "time": ts_display,
                "time_full": ts_str,
                "type": err_type,
                "severity": "error",
                "subsystem": subsystem,
                "detail": detail_text,
                "source_file": source_file,
                "channel": channel_name,
                "run_id": extract_kv(msg, "runId"),
                "session_id": extract_kv(msg, "sessionId"),
            }
            events["errors"].append(error_evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "error", "type": err_type,
                "detail": detail_text[:200],
            })
            # Don't skip — let specific handlers below also process if applicable

        # === Webhook / Telegram raw updates ===
        if subsystem == "gateway/channels/telegram/raw-update":
            evt = {
                "time": ts_display,
                "time_full": ts_str,
                "type": "webhook.received",
                "channel": "telegram",
                "detail": msg[:200] if msg else "",
            }
            events["webhooks"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "webhook", "type": "webhook.received",
                "detail": "Telegram webhook received",
            })
            continue

        # === Telegram channel events (sendMessage ok/failed) ===
        if subsystem == "gateway/channels/telegram":
            if "sendMessage failed" in msg or "reply failed" in msg:
                # Only add if not already captured by ERROR level handler above
                already_captured = False
                if log_level_name == "ERROR":
                    already_captured = True
                if not already_captured:
                    err_evt = {
                        "time": ts_display, "time_full": ts_str,
                        "type": "error.telegram", "severity": "error",
                        "channel": "telegram",
                        "subsystem": subsystem,
                        "detail": msg[:500],
                        "source_file": "",
                        "run_id": "",
                        "session_id": "",
                    }
                    events["errors"].append(err_evt)
                events["webhooks"].append({
                    "time": ts_display, "time_full": ts_str,
                    "type": "webhook.error", "channel": "telegram",
                    "detail": msg,
                })
                if not already_captured:
                    events["all_timeline"].append({
                        "time": ts_display, "time_full": ts_str,
                        "category": "error", "type": "webhook.error",
                        "detail": msg[:200],
                    })
            continue

        # === Diagnostic subsystem events ===
        if subsystem != "diagnostic":
            continue

        # --- message queued ---
        if msg.startswith("message queued:"):
            session_id = extract_kv(msg, "sessionId")
            session_key = extract_kv(msg, "sessionKey")
            queue_depth = extract_kv(msg, "queueDepth")
            session_state = extract_kv(msg, "sessionState")
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "message.queued",
                "session_id": session_id,
                "session_key": session_key,
                "queue_depth": int(queue_depth) if queue_depth.isdigit() else 0,
                "session_state": session_state,
            }
            events["messages"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "message", "type": "message.queued",
                "detail": "session=%s depth=%s" % (session_key, queue_depth),
            })

        # --- message processed ---
        elif msg.startswith("message processed:"):
            channel = extract_kv(msg, "channel")
            chat_id = extract_kv(msg, "chatId")
            message_id = extract_kv(msg, "messageId")
            session_key = extract_kv(msg, "sessionKey")
            outcome = extract_kv(msg, "outcome")
            duration_str = extract_kv(msg, "duration")
            duration_ms = 0
            if duration_str:
                m = re.match(r"(\d+)", duration_str)
                if m:
                    duration_ms = int(m.group(1))
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "message.processed",
                "channel": channel,
                "chat_id": chat_id,
                "message_id": message_id,
                "session_key": session_key,
                "outcome": outcome,
                "duration_ms": duration_ms,
            }
            events["messages"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "message", "type": "message.processed",
                "detail": "ch=%s outcome=%s dur=%dms" % (channel, outcome, duration_ms),
            })
            if outcome and outcome != "completed":
                events["errors"].append({
                    "time": ts_display, "time_full": ts_str,
                    "type": "message.error",
                    "severity": "warn",
                    "channel": channel,
                    "subsystem": "diagnostic",
                    "detail": "outcome=%s session=%s messageId=%s" % (outcome, session_key, message_id),
                    "source_file": "",
                    "run_id": "",
                    "session_id": "",
                })

        # --- session state ---
        elif msg.startswith("session state:"):
            session_id = extract_kv(msg, "sessionId")
            session_key = extract_kv(msg, "sessionKey")
            prev_state = extract_kv(msg, "prev")
            new_state = extract_kv(msg, "new")
            reason_m = re.search(r'reason="([^"]*)"', msg)
            reason = reason_m.group(1) if reason_m else extract_kv(msg, "reason")
            queue_depth = extract_kv(msg, "queueDepth")
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "session.state",
                "session_id": session_id,
                "session_key": session_key,
                "prev": prev_state,
                "new": new_state,
                "reason": reason,
                "queue_depth": int(queue_depth) if queue_depth.isdigit() else 0,
            }
            events["sessions"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "session", "type": "session.state",
                "detail": "%s→%s reason=%s" % (prev_state, new_state, reason),
            })

        # --- stuck session ---
        elif msg.startswith("stuck session:"):
            session_id = extract_kv(msg, "sessionId")
            session_key = extract_kv(msg, "sessionKey")
            state = extract_kv(msg, "state")
            age_str = extract_kv(msg, "age")
            age_s = 0
            if age_str:
                m = re.match(r"(\d+)", age_str)
                if m:
                    age_s = int(m.group(1))
            queue_depth = extract_kv(msg, "queueDepth")
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "session.stuck",
                "session_id": session_id,
                "session_key": session_key,
                "state": state,
                "age_s": age_s,
                "queue_depth": int(queue_depth) if queue_depth.isdigit() else 0,
            }
            events["sessions"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "session", "type": "session.stuck",
                "detail": "session=%s age=%ds state=%s" % (session_key, age_s, state),
            })

        # --- lane enqueue ---
        elif msg.startswith("lane enqueue:"):
            lane = extract_kv(msg, "lane")
            queue_size = extract_kv(msg, "queueSize")
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "queue.lane.enqueue",
                "lane": lane,
                "queue_size": int(queue_size) if queue_size.isdigit() else 0,
            }
            events["queue"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "queue", "type": "queue.enqueue",
                "detail": "lane=%s size=%s" % (lane, queue_size),
            })

        # --- lane dequeue ---
        elif msg.startswith("lane dequeue:"):
            lane = extract_kv(msg, "lane")
            wait_ms = extract_kv(msg, "waitMs")
            queue_size = extract_kv(msg, "queueSize")
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "queue.lane.dequeue",
                "lane": lane,
                "wait_ms": int(wait_ms) if wait_ms.isdigit() else 0,
                "queue_size": int(queue_size) if queue_size.isdigit() else 0,
            }
            events["queue"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "queue", "type": "queue.dequeue",
                "detail": "lane=%s waitMs=%s" % (lane, wait_ms),
            })

        # --- lane task done ---
        elif msg.startswith("lane task done:"):
            lane = extract_kv(msg, "lane")
            dur = extract_kv(msg, "durationMs")
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "queue.lane.done",
                "lane": lane,
                "duration_ms": int(dur) if dur.isdigit() else 0,
            }
            events["queue"].append(evt)
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "queue", "type": "queue.done",
                "detail": "lane=%s dur=%sms" % (lane, dur),
            })

        # --- heartbeat ---
        elif msg.startswith("heartbeat:"):
            # heartbeat: webhooks=0/0/0 active=1 waiting=0 queued=1
            wh = extract_kv(msg, "webhooks")
            active = extract_kv(msg, "active")
            waiting = extract_kv(msg, "waiting")
            queued = extract_kv(msg, "queued")
            wh_parts = wh.split("/") if wh else ["0", "0", "0"]
            evt = {
                "time": ts_display, "time_full": ts_str,
                "type": "diagnostic.heartbeat",
                "webhooks_received": int(wh_parts[0]) if len(wh_parts) > 0 and wh_parts[0].isdigit() else 0,
                "webhooks_processed": int(wh_parts[1]) if len(wh_parts) > 1 and wh_parts[1].isdigit() else 0,
                "webhooks_errors": int(wh_parts[2]) if len(wh_parts) > 2 and wh_parts[2].isdigit() else 0,
                "active": int(active) if active.isdigit() else 0,
                "waiting": int(waiting) if waiting.isdigit() else 0,
                "queued": int(queued) if queued.isdigit() else 0,
            }
            events["heartbeats"].append(evt)
            # Don't add every heartbeat to timeline (too noisy), only sample
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "heartbeat", "type": "heartbeat",
                "detail": "active=%s queued=%s webhooks=%s" % (active, queued, wh),
            })

        # --- run registered ---
        elif msg.startswith("run registered:"):
            session_id = extract_kv(msg, "sessionId")
            total_active = extract_kv(msg, "totalActive")
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "session", "type": "run.registered",
                "detail": "session=%s totalActive=%s" % (session_id, total_active),
            })

        # --- run cleared ---
        elif msg.startswith("run cleared:"):
            session_id = extract_kv(msg, "sessionId")
            total_active = extract_kv(msg, "totalActive")
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "session", "type": "run.cleared",
                "detail": "session=%s totalActive=%s" % (session_id, total_active),
            })

        # --- aborting / abort failed ---
        elif msg.startswith("aborting run") or msg.startswith("abort failed"):
            events["all_timeline"].append({
                "time": ts_display, "time_full": ts_str,
                "category": "error", "type": "run.abort",
                "detail": msg[:200],
            })
            events["errors"].append({
                "time": ts_display, "time_full": ts_str,
                "type": "run.abort",
                "severity": "error",
                "channel": "",
                "subsystem": "diagnostic",
                "detail": msg[:500],
                "source_file": "",
                "run_id": extract_kv(msg, "runId"),
                "session_id": "",
            })

    # Sort timeline by time_full
    events["all_timeline"].sort(key=lambda e: e.get("time_full", ""))
    return events


# ============================================================
# Gateway 重启历史解析
# ============================================================

def find_log_files(log_dir, date=None):
    """查找日志文件列表，可选按日期过滤"""
    if date:
        filepath = os.path.join(log_dir, "openclaw-%s.log" % date)
        if os.path.isfile(filepath):
            return [filepath]
        return []
    pattern = os.path.join(log_dir, "openclaw-*.log")
    files = sorted(glob.glob(pattern))
    return files


def parse_gateway_restarts(log_files):
    """
    从日志文件解析 Gateway 重启历史。
    从日志 JSON 提取 4 种事件（SHUTDOWN/TRIGGER/STARTUP/CRASH），
    按时间排序后配对成重启记录。

    返回: list of {
        "num": int,
        "shutdown_utc": str,     # ISO timestamp
        "startup_utc": str|None, # ISO timestamp, None = NOT RECOVERED
        "type": str,             # "SIGTERM" | "CRASH"
        "reason": str,           # 具体原因
        "downtime_sec": int|None # 停机秒数
    }
    """
    events = []  # [(type, timestamp_str, detail)]

    # Patterns
    re_shutdown = re.compile(r'received SIGTERM; shutting down')
    re_trigger = re.compile(r'config change requires gateway restart')
    re_trigger_detail = re.compile(r'config change requires gateway restart \(([^)]+)\)')
    re_startup = re.compile(r'heartbeat: started')
    re_crash = re.compile(r'uncaughtException|unhandledRejection|ENOMEM|SIGKILL|out of memory')

    for filepath in log_files:
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
                msg = str(msg) if msg else ""
            if not msg:
                continue

            ts_str = obj.get("date", "") or obj.get("time", "")
            if not ts_str:
                continue

            if re_shutdown.search(msg):
                events.append(("SHUTDOWN", ts_str, "SIGTERM"))
            elif re_trigger.search(msg):
                m = re_trigger_detail.search(msg)
                detail = m.group(1) if m else "config change"
                events.append(("TRIGGER", ts_str, detail))
            elif re_startup.search(msg):
                events.append(("STARTUP", ts_str, "heartbeat started"))
            elif re_crash.search(msg):
                events.append(("CRASH", ts_str, "crash/OOM"))

    # Sort by timestamp, deduplicate by (type, timestamp[:19])
    events.sort(key=lambda e: e[1])
    seen = set()
    unique_events = []
    for e in events:
        key = (e[0], e[1][:19])
        if key not in seen:
            seen.add(key)
            unique_events.append(e)

    # Pair events into restart records
    restarts = []
    restart_num = 0
    shutdown_ts = None
    trigger_reason = ""
    restart_type = ""

    for etype, ts, detail in unique_events:
        if etype == "TRIGGER":
            trigger_reason = detail
        elif etype in ("SHUTDOWN", "CRASH"):
            shutdown_ts = ts
            if etype == "CRASH":
                restart_type = "CRASH"
                trigger_reason = detail
            else:
                restart_type = "SIGTERM"
                if not trigger_reason:
                    trigger_reason = "manual/systemd"
        elif etype == "STARTUP":
            if shutdown_ts is None:
                # First boot, not a restart — skip
                continue
            restart_num += 1
            # Calculate downtime
            dt_shutdown = parse_time(shutdown_ts)
            dt_startup = parse_time(ts)
            downtime_sec = None
            if dt_shutdown and dt_startup:
                downtime_sec = int((dt_startup - dt_shutdown).total_seconds())

            restarts.append({
                "num": restart_num,
                "shutdown_utc": shutdown_ts,
                "startup_utc": ts,
                "type": restart_type or "SIGTERM",
                "reason": trigger_reason or "unknown",
                "downtime_sec": downtime_sec,
            })
            shutdown_ts = None
            trigger_reason = ""
            restart_type = ""

    # Unclosed shutdown (NOT RECOVERED)
    if shutdown_ts is not None:
        restart_num += 1
        restarts.append({
            "num": restart_num,
            "shutdown_utc": shutdown_ts,
            "startup_utc": None,
            "type": restart_type or "SIGTERM",
            "reason": trigger_reason or "unknown",
            "downtime_sec": None,
        })

    return restarts


def get_current_gateway_process():
    """获取当前 gateway 进程信息"""
    current_pid = ""
    current_since = ""
    try:
        for svc in ("openclaw-gateway", "openclaw"):
            result = subprocess.run(
                ["systemctl", "--user", "show", svc,
                 "--property=ActiveEnterTimestamp,MainPID"],
                capture_output=True, text=True, timeout=5,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0:
                props = {}
                for line in result.stdout.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
                pid = props.get("MainPID", "0")
                since = props.get("ActiveEnterTimestamp", "")
                if pid and pid != "0" and since:
                    current_pid = pid
                    current_since = since
                    break
    except Exception:
        pass
    return current_pid, current_since


# ============================================================
# 会话文件解析（工具参数 + Token 用量）
# ============================================================

def parse_session_files(sessions_dirs):
    """解析会话文件，建立 toolCallId -> {arguments, usage} 映射
    同时收集纯文本回复（无 toolCall）的 usage，按 timestamp 索引
    同时收集模型调用记录列表 (每次 assistant 消息 = 一次调用)
    同时收集 toolResult 映射 (toolCallId -> result)
    同时收集系统事件 (custom_message) 和模型快照 (model-snapshot)
    同时收集所有消息用于对话树
    返回: (tool_data, text_reply_usage, model_calls, tool_results, system_events, model_snapshots, all_messages)
    """
    tool_data = {}  # toolCallId -> {tool, arguments_raw, arguments_summary, arguments_full, usage}
    text_reply_usage = []  # [(timestamp_str, usage_dict)]
    model_calls = []  # 每次 assistant 消息的详细调用记录
    tool_results = {}  # toolCallId -> {text, text_preview, isError, details}
    system_events = []  # [{timestamp, event_type, content, details, session_id}]
    model_snapshots = []  # [{timestamp, provider, modelApi, modelId, session_id}]
    all_messages = []  # [{id, parentId, type, role, timestamp, preview, has_thinking, tool_count, session_id, ...}]
    if not sessions_dirs:
        return tool_data, text_reply_usage, model_calls, tool_results, system_events, model_snapshots, all_messages
    files = []
    for d in sessions_dirs:
        pattern = os.path.join(d, "*.jsonl")
        files.extend(glob.glob(pattern))
    for fpath in files:
        lines = safe_read_lines(fpath, max_lines=20000)
        # 从文件名提取 session_id (文件名格式: {session_id}.jsonl 或 {session_id}-topic-xxx.jsonl)
        fname = os.path.basename(fpath)
        fname_session_id = fname.split(".")[0].split("-topic-")[0] if fname else ""
        # 从文件内容第一行获取内部 session_id
        internal_session_id = ""
        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue
            try:
                first_obj = json.loads(line_s)
            except (json.JSONDecodeError, ValueError):
                continue
            if first_obj.get("type") == "session":
                internal_session_id = first_obj.get("id", "")
            break

        # 跟踪上一条 user 消息 (用于 prompt)
        last_user_text = ""
        # 跟踪前一条消息的顶层 timestamp（用于推理耗时计算）
        prev_top_timestamp = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            obj_type = obj.get("type", "")

            # === 功能 6: model-snapshot ===
            if obj_type == "custom" and obj.get("customType") == "model-snapshot":
                snap_data = obj.get("data", {})
                snap_ts = obj.get("timestamp", "")
                snap_record = {
                    "timestamp": snap_ts,
                    "provider": snap_data.get("provider", ""),
                    "modelApi": snap_data.get("modelApi", ""),
                    "modelId": snap_data.get("modelId", ""),
                    "session_id": internal_session_id,
                }
                model_snapshots.append(snap_record)
                # Add to all_messages for conversation tree
                all_messages.append({
                    "id": obj.get("id", ""),
                    "parentId": obj.get("parentId"),
                    "type": "model-snapshot",
                    "role": "system",
                    "timestamp": snap_ts,
                    "preview": "📸 模型切换: %s / %s" % (snap_data.get("provider", ""), snap_data.get("modelId", "")),
                    "has_thinking": False,
                    "tool_count": 0,
                    "session_id": internal_session_id,
                })
                continue

            # === 功能 5: custom_message 系统事件 ===
            if obj_type == "custom_message":
                cm_ts = obj.get("timestamp", "")
                cm_custom_type = obj.get("customType", "")
                cm_content = obj.get("content", "")
                cm_details = obj.get("details", {})
                system_events.append({
                    "timestamp": cm_ts,
                    "event_type": cm_custom_type,
                    "content": cm_content[:500] if cm_content else "",
                    "details": cm_details if isinstance(cm_details, dict) else {},
                    "session_id": internal_session_id,
                })
                # Add to all_messages for conversation tree
                all_messages.append({
                    "id": obj.get("id", ""),
                    "parentId": obj.get("parentId"),
                    "type": "custom_message",
                    "role": "system",
                    "timestamp": cm_ts,
                    "preview": "🔄 %s: %s" % (cm_custom_type, (cm_content[:80] if cm_content else "")),
                    "has_thinking": False,
                    "tool_count": 0,
                    "session_id": internal_session_id,
                    "event_type": cm_custom_type,
                })
                continue

            if obj_type != "message":
                continue
            msg = obj.get("message", {})
            if not isinstance(msg, dict):
                continue

            # 收集 toolResult 消息
            if msg.get("role") == "toolResult":
                tc_id = msg.get("toolCallId", "")
                if tc_id:
                    is_error = msg.get("isError", False)
                    result_text = ""
                    result_content = msg.get("content", [])
                    if isinstance(result_content, list):
                        for item in result_content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                result_text += item.get("text", "")
                    elif isinstance(result_content, str):
                        result_text = result_content
                    # 功能 1: 提取 details 字段
                    tr_details = msg.get("details", {})
                    if not isinstance(tr_details, dict):
                        tr_details = {}
                    tool_results[tc_id] = {
                        "text": result_text[:2000],
                        "text_preview": result_text[:200],
                        "isError": is_error,
                        "details": tr_details,
                        "toolName": msg.get("toolName", ""),
                    }
                # Add to all_messages for conversation tree
                all_messages.append({
                    "id": obj.get("id", ""),
                    "parentId": obj.get("parentId"),
                    "type": "message",
                    "role": "toolResult",
                    "timestamp": obj.get("timestamp", ""),
                    "preview": "🔧 %s: %s" % (msg.get("toolName", "tool"), result_text[:80] if tc_id else ""),
                    "has_thinking": False,
                    "tool_count": 0,
                    "session_id": internal_session_id,
                })
                # 记录 toolResult 的顶层 timestamp 用于推理耗时计算
                tr_ts = obj.get("timestamp", "")
                if tr_ts:
                    prev_top_timestamp = tr_ts
                continue

            # 跟踪 user 消息
            if msg.get("role") == "user":
                user_content = msg.get("content", [])
                user_text = ""
                if isinstance(user_content, list):
                    for item in user_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            user_text += item.get("text", "")
                        elif isinstance(item, str):
                            user_text += item
                elif isinstance(user_content, str):
                    user_text = user_content
                if user_text:
                    last_user_text = user_text
                # Add to all_messages for conversation tree
                all_messages.append({
                    "id": obj.get("id", ""),
                    "parentId": obj.get("parentId"),
                    "type": "message",
                    "role": "user",
                    "timestamp": obj.get("timestamp", ""),
                    "preview": user_text[:100] if user_text else "",
                    "has_thinking": False,
                    "tool_count": 0,
                    "session_id": internal_session_id,
                    "full_text": user_text[:3000] if user_text else "",
                })
                # 记录 user 消息的顶层 timestamp
                u_ts = obj.get("timestamp", "")
                if u_ts:
                    prev_top_timestamp = u_ts
                continue

            if msg.get("role") != "assistant":
                continue
            model = msg.get("model", "")
            if model == "delivery-mirror":
                # 记录 timestamp 但跳过 delivery-mirror
                dm_ts = obj.get("timestamp", "")
                if dm_ts:
                    prev_top_timestamp = dm_ts
                continue
            usage = msg.get("usage", {})
            timestamp = obj.get("timestamp", "")
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            has_tool_call = False
            tool_call_list = []
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
                    args_dict = tc_args_raw
                elif isinstance(tc_args_raw, str):
                    try:
                        args_dict = json.loads(tc_args_raw)
                    except (json.JSONDecodeError, ValueError):
                        args_dict = {}
                else:
                    tc_args_raw = str(tc_args_raw)
                    args_dict = {}
                summary = summarize_tool_args(tc_name, tc_args_raw)
                # 构建 arguments_full (限制大内容)
                args_full = {}
                if isinstance(args_dict, dict):
                    for ak, av in args_dict.items():
                        if ak == "content" and isinstance(av, str) and len(av) > 1000:
                            args_full[ak] = av[:1000] + "... (%d chars)" % len(av)
                        elif isinstance(av, str) and len(av) > 2000:
                            args_full[ak] = av[:2000] + "... (%d chars)" % len(av)
                        else:
                            args_full[ak] = av
                tool_data[tc_id] = {
                    "tool": tc_name,
                    "arguments_raw": tc_args_raw,
                    "arguments_summary": summary,
                    "arguments_full": args_full,
                    "usage": usage,
                    "timestamp": timestamp,
                }
                # 简化 args_summary 用于调用记录
                args_short = summary[:100] if summary else ""
                # 功能 1: 从 tool_results 获取 details (如果已收集)
                tc_details = {}
                if tc_id in tool_results:
                    tc_details = tool_results[tc_id].get("details", {})
                tool_call_list.append({
                    "name": tc_name,
                    "id": tc_id,
                    "args_summary": args_short,
                    "args_full": args_full,
                    "details": tc_details,
                })
            # 没有 toolCall 的 assistant 消息 = 纯文本回复
            if not has_tool_call and usage and timestamp:
                text_reply_usage.append((timestamp, usage))

            # 构建模型调用记录
            provider = msg.get("provider", "")
            api_name = msg.get("api", "")
            stop_reason = msg.get("stopReason", "")
            cost_data = usage.get("cost", {}) if isinstance(usage, dict) else {}

            # content_summary (包含完整内容)
            has_thinking = False
            thinking_preview = ""
            thinking_full = ""
            has_text = False
            text_preview = ""
            text_full = ""
            for item in content:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type", "")
                if itype == "thinking":
                    has_thinking = True
                    tk = item.get("thinking", "")
                    if tk and not thinking_preview:
                        thinking_preview = tk[:100]
                    if tk:
                        thinking_full += tk
                elif itype == "text":
                    has_text = True
                    tx = item.get("text", "")
                    if tx and not text_preview:
                        text_preview = tx[:200]
                    if tx:
                        text_full += tx
            # 限制完整内容大小
            thinking_chars_count = len(thinking_full)
            text_chars_count = len(text_full)
            if len(thinking_full) > 5000:
                thinking_full = thinking_full[:5000] + "... (%d chars)" % len(thinking_full)
            if len(text_full) > 3000:
                text_full = text_full[:3000] + "... (%d chars)" % len(text_full)

            # 构建 prompt (上一条 user 消息)
            prompt_data = {}
            if last_user_text:
                prompt_data = {
                    "role": "user",
                    "text": last_user_text[:3000],
                    "text_preview": last_user_text[:200],
                }

            # 计算推理耗时：assistant timestamp - 前一条消息 timestamp
            inference_ms = 0
            if prev_top_timestamp and timestamp:
                try:
                    cur_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    prev_dt = datetime.fromisoformat(prev_top_timestamp.replace("Z", "+00:00"))
                    delta = (cur_dt - prev_dt).total_seconds() * 1000
                    if delta > 0:
                        inference_ms = round(delta)
                except (ValueError, TypeError):
                    inference_ms = 0

            output_tokens = usage.get("output", 0) if isinstance(usage, dict) else 0
            tokens_per_sec = round(output_tokens / (inference_ms / 1000), 1) if inference_ms > 0 and output_tokens > 0 else 0

            call_record = {
                "timestamp": timestamp,
                "inference_ms": inference_ms,
                "tokens_per_sec": tokens_per_sec,
                "session_id": internal_session_id,
                "session_id_file": fname_session_id,
                "model": model,
                "provider": provider,
                "api": api_name,
                "stop_reason": stop_reason,
                "prompt": prompt_data,
                "thinking_chars": thinking_chars_count,
                "thinking_ratio": round(thinking_chars_count / max(thinking_chars_count + text_chars_count, 1), 3),
                "usage": {
                    "input": usage.get("input", 0) if isinstance(usage, dict) else 0,
                    "output": usage.get("output", 0) if isinstance(usage, dict) else 0,
                    "cacheRead": usage.get("cacheRead", 0) if isinstance(usage, dict) else 0,
                    "cacheWrite": usage.get("cacheWrite", 0) if isinstance(usage, dict) else 0,
                    "totalTokens": usage.get("totalTokens", 0) if isinstance(usage, dict) else 0,
                },
                "cost": {
                    "input": cost_data.get("input", 0) if isinstance(cost_data, dict) else 0,
                    "output": cost_data.get("output", 0) if isinstance(cost_data, dict) else 0,
                    "total": cost_data.get("total", 0) if isinstance(cost_data, dict) else 0,
                },
                "content_summary": {
                    "has_thinking": has_thinking,
                    "thinking_preview": thinking_preview,
                    "thinking_full": thinking_full,
                    "has_text": has_text,
                    "text_preview": text_preview,
                    "text_full": text_full,
                    "tool_calls": tool_call_list,
                },
            }
            model_calls.append(call_record)
            # Add to all_messages for conversation tree
            assistant_preview = text_preview[:100] if text_preview else (thinking_preview[:100] if thinking_preview else "")
            if not assistant_preview and tool_call_list:
                assistant_preview = "🔧 " + ", ".join(tc["name"] for tc in tool_call_list[:3])
            all_messages.append({
                "id": obj.get("id", ""),
                "parentId": obj.get("parentId"),
                "type": "message",
                "role": "assistant",
                "timestamp": timestamp,
                "preview": assistant_preview,
                "has_thinking": has_thinking,
                "tool_count": len(tool_call_list),
                "session_id": internal_session_id,
                "inference_ms": inference_ms,
                "tokens_per_sec": tokens_per_sec,
                "model": model,
                "full_text": (text_full[:3000] if text_full else ""),
            })
            # 更新 prev_top_timestamp 为当前 assistant 消息的时间
            if timestamp:
                prev_top_timestamp = timestamp

    # Sort model_calls by timestamp descending
    model_calls.sort(key=lambda c: c.get("timestamp", ""), reverse=True)

    # Second pass: backfill tool_call details from tool_results (since toolResult may come after assistant msg)
    for mc in model_calls:
        cs = mc.get("content_summary", {})
        for tc in cs.get("tool_calls", []):
            tc_id = tc.get("id", "")
            if tc_id and tc_id in tool_results and not tc.get("details"):
                tc["details"] = tool_results[tc_id].get("details", {})

    return tool_data, text_reply_usage, model_calls, tool_results, system_events, model_snapshots, all_messages


def summarize_tool_args(tool_name, args_raw):
    """提取工具参数的完整可读摘要"""
    if isinstance(args_raw, dict):
        args = args_raw
    elif isinstance(args_raw, str):
        try:
            args = json.loads(args_raw)
        except (json.JSONDecodeError, ValueError):
            return args_raw[:500] if args_raw else ""
    else:
        return str(args_raw)[:500] if args_raw else ""
    if not isinstance(args, dict):
        return str(args)[:500]

    if tool_name == "exec":
        cmd = args.get("command", "")
        lines = [cmd]  # 完整命令
        wd = args.get("workdir", "")
        if wd:
            lines.append("[cwd: %s]" % wd)
        timeout = args.get("timeout")
        if timeout:
            lines.append("[timeout: %ss]" % timeout)
        bg = args.get("background")
        if bg:
            lines.append("[background]")
        pty = args.get("pty")
        if pty:
            lines.append("[pty]")
        env = args.get("env")
        if env and isinstance(env, dict):
            lines.append("[env: %s]" % " ".join("%s=%s" % (k, v) for k, v in env.items()))
        return "\n".join(lines)

    elif tool_name == "read":
        p = args.get("path", "") or args.get("file_path", "")
        lines = [p]
        offset = args.get("offset")
        limit = args.get("limit")
        if offset:
            lines.append("offset=%s" % offset)
        if limit:
            lines.append("limit=%s" % limit)
        return "  ".join(lines)

    elif tool_name == "write":
        p = args.get("path", "") or args.get("file_path", "")
        content = args.get("content", "")
        lines = [p]
        lines.append("(%d chars)" % len(content))
        if content:
            # 显示前 5 行内容预览
            preview_lines = content.split("\n")[:5]
            preview = "\n".join(preview_lines)
            if len(content.split("\n")) > 5:
                preview += "\n... (%d lines total)" % len(content.split("\n"))
            lines.append("\n--- content preview ---\n%s" % preview)
        return "  ".join(lines) if len(lines) <= 2 else lines[0] + "  " + lines[1] + lines[2]

    elif tool_name == "edit":
        p = args.get("path", "") or args.get("file_path", "")
        old = args.get("old_string", "") or args.get("oldText", "")
        new = args.get("new_string", "") or args.get("newText", "")
        lines = [p]
        if old:
            old_lines = old.split("\n")
            old_preview = "\n".join(old_lines[:5])
            if len(old_lines) > 5:
                old_preview += "\n... (%d lines)" % len(old_lines)
            lines.append("--- old (%d chars) ---\n%s" % (len(old), old_preview))
        if new:
            new_lines = new.split("\n")
            new_preview = "\n".join(new_lines[:5])
            if len(new_lines) > 5:
                new_preview += "\n... (%d lines)" % len(new_lines)
            lines.append("--- new (%d chars) ---\n%s" % (len(new), new_preview))
        return "\n".join(lines)

    elif tool_name == "web_search":
        q = args.get("query", "")
        parts = ["query: %s" % q]
        for k in ("count", "country", "language", "freshness", "date_after", "date_before"):
            v = args.get(k)
            if v:
                parts.append("%s=%s" % (k, v))
        return "  ".join(parts)

    elif tool_name == "web_fetch":
        url = args.get("url", "")
        parts = [url]
        mode = args.get("extractMode", "")
        maxc = args.get("maxChars")
        if mode:
            parts.append("mode=%s" % mode)
        if maxc:
            parts.append("maxChars=%s" % maxc)
        return "  ".join(parts)

    elif tool_name == "sessions_spawn":
        lines = []
        agent = args.get("agentId", "")
        label = args.get("label", "")
        model = args.get("model", "")
        mode = args.get("mode", "")
        timeout = args.get("runTimeoutSeconds", "")
        task = args.get("task", "")
        if agent:
            lines.append("agent: %s" % agent)
        if label:
            lines.append("label: %s" % label)
        if model:
            lines.append("model: %s" % model)
        if mode:
            lines.append("mode: %s" % mode)
        if timeout:
            lines.append("timeout: %ss" % timeout)
        if task:
            # 显示 task 前 500 字符
            task_preview = task[:500]
            if len(task) > 500:
                task_preview += "\n... (%d chars total)" % len(task)
            lines.append("--- task ---\n%s" % task_preview)
        return "\n".join(lines)

    elif tool_name in ("sessions_send", "sessions_history"):
        lines = []
        for k in ("sessionKey", "label", "message", "agentId", "timeoutSeconds", "includeTools", "limit"):
            v = args.get(k)
            if v is not None:
                vs = str(v)
                if len(vs) > 200:
                    vs = vs[:200] + "..."
                lines.append("%s: %s" % (k, vs))
        return "\n".join(lines)

    elif tool_name == "subagents":
        lines = []
        for k in ("action", "target", "message"):
            v = args.get(k)
            if v is not None:
                vs = str(v)
                if k == "message" and len(vs) > 500:
                    vs = vs[:500] + "\n... (%d chars)" % len(str(args.get(k, "")))
                lines.append("%s: %s" % (k, vs))
        return "\n".join(lines)

    elif tool_name == "message":
        lines = []
        for k in ("action", "target", "channel", "message", "replyTo", "filePath", "media", "caption"):
            v = args.get(k)
            if v is not None:
                vs = str(v)
                if len(vs) > 200:
                    vs = vs[:200] + "..."
                lines.append("%s: %s" % (k, vs))
        return "\n".join(lines)

    elif tool_name == "browser":
        lines = []
        for k in ("action", "url", "ref", "kind", "text", "selector", "targetId", "profile", "key"):
            v = args.get(k)
            if v is not None:
                lines.append("%s: %s" % (k, str(v)[:200]))
        return "\n".join(lines)

    elif tool_name == "process":
        lines = []
        for k in ("action", "sessionId", "timeout", "data", "keys"):
            v = args.get(k)
            if v is not None:
                lines.append("%s: %s" % (k, str(v)[:200]))
        return "\n".join(lines)

    elif tool_name == "memory_search":
        q = args.get("query", "")
        parts = ["query: %s" % q]
        mr = args.get("maxResults")
        if mr:
            parts.append("maxResults=%s" % mr)
        return "  ".join(parts)

    elif tool_name == "memory_get":
        p = args.get("path", "")
        lines = [p]
        frm = args.get("from")
        ln = args.get("lines")
        if frm:
            lines.append("from=%s" % frm)
        if ln:
            lines.append("lines=%s" % ln)
        return "  ".join(lines)

    elif tool_name == "canvas":
        lines = []
        for k in ("action", "url", "javaScript", "width", "height"):
            v = args.get(k)
            if v is not None:
                lines.append("%s: %s" % (k, str(v)[:200]))
        return "\n".join(lines)

    elif tool_name == "nodes":
        lines = []
        for k in ("action", "node", "command", "facing", "duration"):
            v = args.get(k)
            if v is not None:
                lines.append("%s: %s" % (k, str(v)[:200]))
        return "\n".join(lines)

    elif tool_name == "tts":
        return args.get("text", "")[:200]

    elif tool_name == "session_status":
        parts = []
        for k in ("model", "sessionKey"):
            v = args.get(k)
            if v:
                parts.append("%s=%s" % (k, v))
        return "  ".join(parts) if parts else "(no args)"

    else:
        # 通用: 显示所有参数
        lines = []
        for k, v in args.items():
            vs = str(v)
            if len(vs) > 300:
                vs = vs[:300] + "... (%d chars)" % len(str(v))
            lines.append("%s: %s" % (k, vs))
        return "\n".join(lines) if lines else "(no args)"


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

    # 模型调用总数 (不触发懒加载，避免阻塞首次请求)
    model_calls = data_store._model_calls if data_store._tool_data_loaded else None
    info["model_calls_total"] = len(model_calls) if model_calls else -1

    return info


# ============================================================
# 构建 API 数据
# ============================================================

class DataStore(object):
    """缓存和提供诊断数据"""

    def __init__(self, log_dir, sessions_dirs):
        self.log_dir = log_dir
        self.sessions_dirs = sessions_dirs
        self._cache = {}  # date -> (mtime, runs dict)
        self._events_cache = {}  # date -> (mtime, events dict)
        self._tool_data = None
        self._text_reply_usage = None
        self._model_calls = None
        self._tool_results = None
        self._system_events = None
        self._model_snapshots = None
        self._all_messages = None
        self._tool_data_loaded = False
        self._lock = threading.Lock()

    def start_preload(self):
        """后台线程预加载 session 数据，不阻塞 HTTP 服务"""
        t = threading.Thread(target=self._get_tool_data, daemon=True)
        t.start()

    def _get_tool_data(self):
        if not self._tool_data_loaded:
            with self._lock:
                if not self._tool_data_loaded:
                    self._tool_data, self._text_reply_usage, self._model_calls, self._tool_results, self._system_events, self._model_snapshots, self._all_messages = parse_session_files(self.sessions_dirs)
                    self._tool_data_loaded = True
        return self._tool_data or {}

    def _get_tool_data_nonblocking(self):
        """非阻塞版本：如果数据没准备好返回空 dict"""
        if self._tool_data_loaded:
            return self._tool_data or {}
        return {}

    def _get_text_reply_usage_nonblocking(self):
        if self._tool_data_loaded:
            return self._text_reply_usage or []
        return []

    def _get_tool_results_nonblocking(self):
        if self._tool_data_loaded:
            return self._tool_results or {}
        return {}

    def _get_text_reply_usage(self):
        self._get_tool_data()
        return self._text_reply_usage or []

    def _get_tool_results(self):
        self._get_tool_data()
        return self._tool_results or {}

    def get_model_calls(self, date=None, page=1, per_page=50):
        """返回模型调用记录列表 (分页)，可按日期过滤"""
        self._get_tool_data()
        calls = self._model_calls or []
        if date:
            calls = [c for c in calls if c.get("timestamp", "").startswith(date)]
        total = len(calls)
        start = (page - 1) * per_page
        end = start + per_page
        page_calls = calls[start:end]
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {
            "date": date or "",
            "model_calls": page_calls,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    def get_dates(self):
        # Also include dates from session files (for standard mode without log files)
        pattern = os.path.join(self.log_dir, "openclaw-*.log")
        files = glob.glob(pattern)
        dates_set = set()
        for f in files:
            basename = os.path.basename(f)
            m = re.match(r"openclaw-(\d{4}-\d{2}-\d{2})\.log", basename)
            if m:
                dates_set.add(m.group(1))
        # Add dates from model_calls
        if self._tool_data_loaded and self._model_calls:
            for mc in self._model_calls:
                ts = mc.get("timestamp", "")[:10]
                if ts and len(ts) == 10:
                    dates_set.add(ts)
        dates = sorted(dates_set, reverse=True)
        return dates

    def get_tool_stats(self, date=None):
        """功能 1: 返回工具统计 — 按工具名分组的调用次数、平均 durationMs、成功/失败率"""
        self._get_tool_data()
        tr = self._tool_results or {}
        # Filter by date using model_calls timestamps to find relevant toolCallIds
        mc_tool_ids_by_date = set()
        all_mc = self._model_calls or []
        for mc in all_mc:
            mc_ts = mc.get("timestamp", "")[:10]
            if date and mc_ts != date:
                continue
            cs = mc.get("content_summary", {})
            for tc in cs.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id:
                    mc_tool_ids_by_date.add(tc_id)

        # Also collect from tool_data
        td = self._tool_data or {}
        for tc_id, info in td.items():
            tc_ts = info.get("timestamp", "")[:10]
            if date and tc_ts != date:
                continue
            mc_tool_ids_by_date.add(tc_id)

        # Build stats
        tool_stats = {}  # name -> {count, total_ms, error_count, exit_codes}
        total_duration_ms = 0
        total_calls = 0
        total_errors = 0
        exit_code_dist = {}

        for tc_id in mc_tool_ids_by_date:
            result = tr.get(tc_id, {})
            info = td.get(tc_id, {})
            tool_name = result.get("toolName", "") or info.get("tool", "") or "unknown"
            details = result.get("details", {})

            if tool_name not in tool_stats:
                tool_stats[tool_name] = {"count": 0, "total_ms": 0, "error_count": 0, "exit_codes": {}}

            tool_stats[tool_name]["count"] += 1
            total_calls += 1

            # Duration from details
            dur_ms = details.get("durationMs", 0) or details.get("tookMs", 0)
            if isinstance(dur_ms, (int, float)) and dur_ms > 0:
                tool_stats[tool_name]["total_ms"] += dur_ms
                total_duration_ms += dur_ms

            # Error detection
            is_err = result.get("isError", False)
            exit_code = details.get("exitCode")
            if is_err or (exit_code is not None and exit_code != 0):
                tool_stats[tool_name]["error_count"] += 1
                total_errors += 1

            # Exit code distribution for exec
            if tool_name == "exec" and exit_code is not None:
                ec_str = str(exit_code)
                exit_code_dist[ec_str] = exit_code_dist.get(ec_str, 0) + 1
                tool_stats[tool_name]["exit_codes"][ec_str] = tool_stats[tool_name]["exit_codes"].get(ec_str, 0) + 1

        # Build response
        by_tool = []
        for name, stats in sorted(tool_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            avg_ms = round(stats["total_ms"] / stats["count"]) if stats["count"] > 0 else 0
            error_rate = round(stats["error_count"] / stats["count"], 3) if stats["count"] > 0 else 0
            by_tool.append({
                "name": name,
                "count": stats["count"],
                "avg_ms": avg_ms,
                "total_ms": round(stats["total_ms"]),
                "error_count": stats["error_count"],
                "error_rate": error_rate,
                "exit_codes": stats.get("exit_codes", {}),
            })

        return {
            "date": date or "",
            "total_calls": total_calls,
            "total_errors": total_errors,
            "total_duration_ms": round(total_duration_ms),
            "avg_duration_ms": round(total_duration_ms / total_calls) if total_calls > 0 else 0,
            "exec_exit_code_dist": exit_code_dist,
            "by_tool": by_tool,
        }

    def get_sessions_list(self, date=None):
        """功能 4: 返回当日活跃 session 列表"""
        self._get_tool_data()
        all_mc = self._model_calls or []
        all_msgs = self._all_messages or []

        # Group by session_id
        sessions_map = {}  # session_id -> {agent, message_count, first_ts, last_ts, model, total_tokens}
        for m in all_msgs:
            sid = m.get("session_id", "")
            if not sid:
                continue
            ts = m.get("timestamp", "")
            if date and ts[:10] != date:
                continue
            if sid not in sessions_map:
                sessions_map[sid] = {
                    "session_id": sid,
                    "message_count": 0,
                    "first_ts": ts,
                    "last_ts": ts,
                    "model": "",
                    "total_tokens": 0,
                }
            sessions_map[sid]["message_count"] += 1
            if ts < sessions_map[sid]["first_ts"]:
                sessions_map[sid]["first_ts"] = ts
            if ts > sessions_map[sid]["last_ts"]:
                sessions_map[sid]["last_ts"] = ts

        # Enrich with model call data
        for mc in all_mc:
            sid = mc.get("session_id", "")
            if not sid or sid not in sessions_map:
                continue
            ts = mc.get("timestamp", "")[:10]
            if date and ts != date:
                continue
            if not sessions_map[sid]["model"]:
                sessions_map[sid]["model"] = mc.get("model", "")
            u = mc.get("usage", {})
            sessions_map[sid]["total_tokens"] += u.get("totalTokens", 0) or (u.get("input", 0) + u.get("output", 0))

        # Try to extract agent from session dir path
        # sessions_dirs pattern: ~/.openclaw/agents/{agent}/sessions
        agent_map = {}
        for d in self.sessions_dirs:
            parts = d.replace("\\", "/").split("/")
            for i, p in enumerate(parts):
                if p == "agents" and i + 1 < len(parts):
                    agent_name = parts[i + 1]
                    # find session files in this dir
                    for f in glob.glob(os.path.join(d, "*.jsonl")):
                        fname = os.path.basename(f).split(".")[0].split("-topic-")[0]
                        agent_map[fname] = agent_name

        sessions_list = []
        for sid, info in sessions_map.items():
            info["agent"] = agent_map.get(sid, "")
            sessions_list.append(info)

        sessions_list.sort(key=lambda s: s.get("last_ts", ""), reverse=True)
        return sessions_list

    def get_conversation_tree(self, session_id=None, date=None):
        """功能 4: 返回消息树 — 按 parentId 构建"""
        self._get_tool_data()
        all_msgs = self._all_messages or []

        filtered = []
        for m in all_msgs:
            if session_id and m.get("session_id", "") != session_id:
                continue
            if date and m.get("timestamp", "")[:10] != date:
                continue
            filtered.append(m)

        # Sort by timestamp
        filtered.sort(key=lambda m: m.get("timestamp", ""))
        return filtered

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

    def _load_events(self, date):
        filepath = os.path.join(self.log_dir, "openclaw-%s.log" % date)
        if not os.path.isfile(filepath):
            return {
                "webhooks": [], "messages": [], "queue": [],
                "sessions": [], "heartbeats": [], "errors": [],
                "all_timeline": [],
            }
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            mtime = 0
        cached = self._events_cache.get(date)
        if cached and cached[0] == mtime:
            return cached[1]
        events = parse_all_events(filepath)
        self._events_cache[date] = (mtime, events)
        return events

    def get_summary(self, date, advanced_mode=False):
        # 标准模式：只从 session 文件获取统计
        # 高级模式：额外从 debug 日志获取 Run 级统计
        
        # session 级推理统计（两种模式都有）
        self._get_tool_data()  # 确保加载
        session_total_inference_ms = 0
        session_inference_count = 0
        session_tps_values = []
        total_model_cost = 0
        all_mc = self._model_calls or []
        for mc in all_mc:
            mc_ts = mc.get("timestamp", "")[:10]
            if mc_ts != date:
                continue
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
        session_avg_inference_ms = round(session_total_inference_ms / session_inference_count) if session_inference_count > 0 else 0
        session_avg_tokens_per_sec = round(sum(session_tps_values) / len(session_tps_values), 1) if session_tps_values else 0

        # 从 model_calls 统计 token
        session_total_input = 0
        session_total_output = 0
        session_total_cache_read = 0
        session_total_cache_write = 0
        session_model_call_count = 0
        for mc in all_mc:
            mc_ts = mc.get("timestamp", "")[:10]
            if mc_ts != date:
                continue
            session_model_call_count += 1
            u = mc.get("usage", {})
            session_total_input += u.get("input", 0)
            session_total_output += u.get("output", 0)
            session_total_cache_read += u.get("cacheRead", 0)
            session_total_cache_write += u.get("cacheWrite", 0)

        session_total_cache = session_total_cache_read + session_total_cache_write + session_total_input
        session_cache_hit_ratio = round(session_total_cache_read / session_total_cache * 100, 1) if session_total_cache > 0 else 0

        # Gateway restart info (不依赖 debug 级别日志)
        restart_data = self.get_restarts(date)
        restart_count = restart_data["total"]
        last_restart = ""
        total_downtime_sec = 0
        if restart_data["restarts"]:
            last_r = restart_data["restarts"][-1]
            last_restart = last_r.get("shutdown_utc", "")
            for r in restart_data["restarts"]:
                if r.get("downtime_sec") is not None:
                    total_downtime_sec += r["downtime_sec"]

        # 标准模式基础结果
        result = {
            "date": date,
            "session_model_call_count": session_model_call_count,
            "session_total_inference_ms": session_total_inference_ms,
            "session_avg_inference_ms": session_avg_inference_ms,
            "session_avg_tokens_per_sec": session_avg_tokens_per_sec,
            "session_inference_count": session_inference_count,
            "total_tokens_output": session_total_output,
            "total_tokens_input": session_total_input,
            "total_cache_read": session_total_cache_read,
            "total_cache_write": session_total_cache_write,
            "cache_hit_ratio": session_cache_hit_ratio,
            "total_model_cost": round(total_model_cost, 6),
            "restart_count": restart_count,
            "last_restart": last_restart,
            "total_downtime_sec": total_downtime_sec,
        }

        # === 功能 1: 工具统计 ===
        tool_stats = self.get_tool_stats(date)
        result["tool_call_count"] = tool_stats["total_calls"]
        result["tool_error_count"] = tool_stats["total_errors"]
        result["tool_avg_duration_ms"] = tool_stats["avg_duration_ms"]
        # top 5 tools
        top_tools = []
        for t in tool_stats["by_tool"][:5]:
            top_tools.append({
                "name": t["name"],
                "count": t["count"],
                "avg_ms": t["avg_ms"],
                "error_count": t["error_count"],
            })
        result["top_tools"] = top_tools

        # === 功能 2: Thinking 深度统计 ===
        thinking_total_chars = 0
        thinking_calls_count = 0
        thinking_ratio_sum = 0
        for mc in all_mc:
            mc_ts = mc.get("timestamp", "")[:10]
            if mc_ts != date:
                continue
            tc = mc.get("thinking_chars", 0)
            if tc > 0:
                thinking_total_chars += tc
                thinking_calls_count += 1
                thinking_ratio_sum += mc.get("thinking_ratio", 0)
        result["thinking_total_chars"] = thinking_total_chars
        result["thinking_avg_chars"] = round(thinking_total_chars / thinking_calls_count) if thinking_calls_count > 0 else 0
        result["thinking_calls_count"] = thinking_calls_count
        result["thinking_avg_ratio"] = round(thinking_ratio_sum / thinking_calls_count, 3) if thinking_calls_count > 0 else 0

        # === 功能 5: 系统事件统计 ===
        sys_events = self._system_events or []
        yield_count = 0
        system_event_count = 0
        for ev in sys_events:
            ev_ts = ev.get("timestamp", "")[:10]
            if ev_ts != date:
                continue
            system_event_count += 1
            if ev.get("event_type") == "openclaw.sessions_yield":
                yield_count += 1
        result["yield_count"] = yield_count
        result["system_event_count"] = system_event_count

        # === 功能 6: 模型使用列表 ===
        snapshots = self._model_snapshots or []
        models_map = {}  # (model, provider) -> count
        for snap in snapshots:
            snap_ts = snap.get("timestamp", "")[:10]
            if snap_ts != date:
                continue
            key = (snap.get("modelId", ""), snap.get("provider", ""))
            models_map[key] = models_map.get(key, 0) + 1
        # Also count from model_calls
        for mc in all_mc:
            mc_ts = mc.get("timestamp", "")[:10]
            if mc_ts != date:
                continue
            key = (mc.get("model", ""), mc.get("provider", ""))
            if key[0]:
                models_map[key] = models_map.get(key, 0) + 1
        models_used = []
        for (model, provider), count in sorted(models_map.items(), key=lambda x: x[1], reverse=True):
            models_used.append({"model": model, "provider": provider, "call_count": count})
        result["models_used"] = models_used

        if not advanced_mode:
            # 标准模式不需要 Run 级别统计
            result["total_runs"] = 0
            result["avg_duration_ms"] = 0
            result["total_infer_ms"] = 0
            result["total_tool_ms"] = 0
            result["infer_ratio"] = 0
            result["avg_tok_per_s"] = 0
            result["error_count"] = 0
            result["models"] = []
            result["channels"] = []
            return result

        # === 高级模式：添加 Run 级别统计 ===
        runs = self._load_runs(date)
        if not runs:
            result["total_runs"] = 0
            result["avg_duration_ms"] = 0
            result["total_infer_ms"] = 0
            result["total_tool_ms"] = 0
            result["infer_ratio"] = 0
            result["avg_tok_per_s"] = 0
            result["error_count"] = 0
            result["models"] = []
            result["channels"] = []
            return result

        durations = []
        total_infer = 0
        total_tool = 0
        total_tokens = 0
        error_count = 0
        models = set()
        channels = set()
        tool_data = self._get_tool_data_nonblocking()
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
        # 也收集 text_reply_usage
        text_reply_usage = self._get_text_reply_usage_nonblocking()
        total_input = 0
        total_cache_read = 0
        total_cache_write = 0
        for run in runs.values():
            run_start_str = run["start"].strftime("%Y-%m-%dT%H:%M:%S") if run.get("start") else ""
            run_end_str = run["end"].strftime("%Y-%m-%dT%H:%M:%S") if run.get("end") else ""
            if run.get("start") and run.get("end"):
                for ts_str, u in text_reply_usage:
                    ts_cmp = ts_str[:19]
                    if ts_cmp >= run_start_str and ts_cmp <= run_end_str:
                        total_tokens += u.get("output", 0)
                        total_input += u.get("input", 0)
                        total_cache_read += u.get("cacheRead", 0)
                        total_cache_write += u.get("cacheWrite", 0)
                        break
            seen = set()
            for t in run.get("tools", []):
                tc_id = t.get("toolCallId", "")
                td = tool_data.get(tc_id, {})
                usage = td.get("usage", {})
                uid = id(usage)
                if uid not in seen and usage:
                    seen.add(uid)
                    total_input += usage.get("input", 0)
                    total_cache_read += usage.get("cacheRead", 0)
                    total_cache_write += usage.get("cacheWrite", 0)

        avg_dur = int(sum(durations) / len(durations)) if durations else 0
        total_time = total_infer + total_tool
        infer_ratio = round(total_infer / total_time * 100, 1) if total_time > 0 else 0
        avg_tok_per_s = round(total_tokens / (total_infer / 1000.0), 1) if total_infer > 0 else 0

        # 更新 result 添加 Run 级别统计
        result["total_runs"] = len(runs)
        result["avg_duration_ms"] = avg_dur
        result["total_infer_ms"] = total_infer
        result["total_tool_ms"] = total_tool
        result["infer_ratio"] = infer_ratio
        result["avg_tok_per_s"] = avg_tok_per_s
        result["error_count"] = error_count
        result["models"] = sorted(models)
        result["channels"] = sorted(channels)
        return result

    def get_events_summary(self, date):
        """返回所有事件的汇总统计"""
        events = self._load_events(date)
        runs = self._load_runs(date)
        tool_data = self._get_tool_data_nonblocking()

        # Count event types
        webhooks_received = sum(1 for w in events["webhooks"] if w.get("type") == "webhook.received")
        webhook_errors = sum(1 for w in events["webhooks"] if w.get("type") == "webhook.error")
        messages_queued = sum(1 for m in events["messages"] if m.get("type") == "message.queued")
        messages_processed = sum(1 for m in events["messages"] if m.get("type") == "message.processed")
        queue_enqueues = sum(1 for q in events["queue"] if q.get("type") == "queue.lane.enqueue")
        queue_dequeues = sum(1 for q in events["queue"] if q.get("type") == "queue.lane.dequeue")
        session_states = sum(1 for s in events["sessions"] if s.get("type") == "session.state")
        session_stuck = sum(1 for s in events["sessions"] if s.get("type") == "session.stuck")
        heartbeats = len(events["heartbeats"])
        error_count = len(events["errors"])

        # Message processing stats
        process_durations = []
        msg_outcomes = {}
        for m in events["messages"]:
            if m.get("type") == "message.processed":
                dur = m.get("duration_ms", 0)
                if dur > 0:
                    process_durations.append(dur)
                outcome = m.get("outcome", "unknown")
                msg_outcomes[outcome] = msg_outcomes.get(outcome, 0) + 1

        avg_process_ms = int(sum(process_durations) / len(process_durations)) if process_durations else 0

        # Queue wait stats
        queue_waits = []
        for q in events["queue"]:
            if q.get("type") == "queue.lane.dequeue":
                wms = q.get("wait_ms", 0)
                if wms > 0:
                    queue_waits.append(wms)
        avg_queue_wait_ms = int(sum(queue_waits) / len(queue_waits)) if queue_waits else 0

        # Webhook stats by channel
        wh_by_channel = {}
        for w in events["webhooks"]:
            ch = w.get("channel", "unknown")
            if ch not in wh_by_channel:
                wh_by_channel[ch] = {"received": 0, "errors": 0}
            if w.get("type") == "webhook.received":
                wh_by_channel[ch]["received"] += 1
            elif w.get("type") == "webhook.error":
                wh_by_channel[ch]["errors"] += 1

        # Session state transitions
        transitions = {}
        for s in events["sessions"]:
            if s.get("type") == "session.state":
                key = "%s→%s" % (s.get("prev", "?"), s.get("new", "?"))
                transitions[key] = transitions.get(key, 0) + 1

        # Model usage from runs + session tool data
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        model_usage_map = {}
        seen_usage = set()
        for run in runs.values():
            for t in run.get("tools", []):
                tc_id = t.get("toolCallId", "")
                td = tool_data.get(tc_id, {})
                usage = td.get("usage", {})
                uid = id(usage)
                if usage and uid not in seen_usage:
                    seen_usage.add(uid)
                    inp = usage.get("input", 0)
                    out = usage.get("output", 0)
                    cr = usage.get("cacheRead", 0)
                    cw = usage.get("cacheWrite", 0)
                    total_input += inp
                    total_output += out
                    total_cache_read += cr
                    total_cache_write += cw
                    model = run.get("model", "unknown")
                    if model not in model_usage_map:
                        model_usage_map[model] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
                    model_usage_map[model]["input"] += inp
                    model_usage_map[model]["output"] += out
                    model_usage_map[model]["cache_read"] += cr
                    model_usage_map[model]["cache_write"] += cw

        by_model = []
        for model, stats in model_usage_map.items():
            by_model.append({
                "model": model,
                "input": stats["input"],
                "output": stats["output"],
                "cache_read": stats["cache_read"],
                "cache_write": stats["cache_write"],
            })

        by_outcome = [{"outcome": k, "count": v} for k, v in msg_outcomes.items()]
        by_channel = [{"channel": k, "received": v["received"], "errors": v["errors"]}
                      for k, v in wh_by_channel.items()]
        state_transitions = [{"transition": k, "count": v} for k, v in transitions.items()]

        total_events = (webhooks_received + webhook_errors + messages_queued +
                       messages_processed + queue_enqueues + queue_dequeues +
                       session_states + session_stuck + heartbeats)

        return {
            "date": date,
            "summary": {
                "total_events": total_events,
                "runs": len(runs),
                "webhooks_received": webhooks_received,
                "webhook_errors": webhook_errors,
                "messages_queued": messages_queued,
                "messages_processed": messages_processed,
                "queue_enqueues": queue_enqueues,
                "queue_dequeues": queue_dequeues,
                "session_states": session_states,
                "session_stuck": session_stuck,
                "heartbeats": heartbeats,
                "errors": error_count,
            },
            "model_usage": {
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_cache_read": total_cache_read,
                "total_cache_write": total_cache_write,
                "by_model": by_model,
            },
            "webhook_stats": {
                "error_rate": round(webhook_errors / max(webhooks_received, 1), 3),
                "by_channel": by_channel,
            },
            "message_stats": {
                "avg_process_time_ms": avg_process_ms,
                "avg_queue_wait_ms": avg_queue_wait_ms,
                "by_outcome": by_outcome,
            },
            "session_stats": {
                "state_transitions": state_transitions,
                "stuck_count": session_stuck,
            },
        }

    def get_events_timeline(self, date, page=1, per_page=50, category_filter=None):
        """返回事件时间线（分页）"""
        events = self._load_events(date)
        timeline = events.get("all_timeline", [])

        # Filter by category
        if category_filter:
            filters = set(category_filter.split(","))
            timeline = [e for e in timeline if e.get("category", "") in filters]

        total = len(timeline)
        # Reverse for newest first
        timeline_rev = list(reversed(timeline))
        total_pages = max(1, math.ceil(total / per_page)) if per_page > 0 else 1
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page

        return {
            "events": timeline_rev[start_idx:end_idx],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    def get_events_webhooks(self, date):
        """返回 webhook 事件列表"""
        events = self._load_events(date)
        return {
            "date": date,
            "webhooks": events.get("webhooks", []),
            "total": len(events.get("webhooks", [])),
        }

    def get_events_messages(self, date):
        """返回消息事件列表"""
        events = self._load_events(date)
        return {
            "date": date,
            "messages": events.get("messages", []),
            "total": len(events.get("messages", [])),
        }

    def get_events_errors(self, date, severity=None, err_type=None):
        """返回错误事件列表，支持过滤"""
        events = self._load_events(date)
        errors = events.get("errors", [])
        # 按时间倒序
        errors = sorted(errors, key=lambda e: e.get("time_full", ""), reverse=True)
        if severity:
            errors = [e for e in errors if e.get("severity", "") == severity]
        if err_type:
            errors = [e for e in errors if e.get("type", "") == err_type]
        return {
            "date": date,
            "errors": errors,
            "total": len(errors),
        }

    def get_runs_list(self, date, page=1, per_page=20):
        """返回某天的 run 列表（分页）"""
        runs = self._load_runs(date)
        tool_data = self._get_tool_data_nonblocking()
        text_reply_usage = self._get_text_reply_usage_nonblocking()
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
            # 纯文本回复的 token（无 toolCall 或最后一段推理）
            if run.get("start") and run.get("end"):
                run_start_str = run["start"].strftime("%Y-%m-%dT%H:%M:%S")
                run_end_str = run["end"].strftime("%Y-%m-%dT%H:%M:%S")
                for ts_str, u in text_reply_usage:
                    if id(u) in seen_usage_ids:
                        continue
                    ts_cmp = ts_str[:19]
                    if ts_cmp >= run_start_str and ts_cmp <= run_end_str:
                        seen_usage_ids.add(id(u))
                        token_output += u.get("output", 0)
                        break  # 一个 run 最多匹配一个 text reply
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

    def get_restarts(self, date=None):
        """返回 Gateway 重启历史"""
        log_files = find_log_files(self.log_dir, date)
        restarts = parse_gateway_restarts(log_files)
        current_pid, current_since = get_current_gateway_process()
        return {
            "restarts": restarts,
            "total": len(restarts),
            "current_pid": current_pid,
            "current_since": current_since,
        }

    def get_run_detail(self, date, run_id):
        runs = self._load_runs(date)
        run = runs.get(run_id)
        if not run:
            return None
        tool_data = self._get_tool_data()
        text_reply_usage = self._get_text_reply_usage()
        tool_results = self._get_tool_results()

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
                "arguments_full": td.get("arguments_full", {}),
                "result": tool_results.get(tc_id, {}),
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

        # 匹配此 run 时间范围内的模型调用
        run_model_calls = []
        if run_start_str and run_end_str:
            all_model_calls = self._model_calls or []
            session_id = run.get("session_id", "")
            for mc in all_model_calls:
                mc_ts = mc.get("timestamp", "")
                if not mc_ts:
                    continue
                # 比较 ISO 时间 (截取到秒)
                mc_ts_short = mc_ts[:19]
                if mc_ts_short >= run_start_str and mc_ts_short <= run_end_str:
                    # session_id 匹配: 比对内部ID和文件名ID
                    if session_id:
                        mc_sid = mc.get("session_id", "")
                        mc_sid_file = mc.get("session_id_file", "")
                        if mc_sid and mc_sid_file:
                            if mc_sid != session_id and mc_sid_file != session_id:
                                continue
                    run_model_calls.append(mc)
            # 按时间正序
            run_model_calls.sort(key=lambda c: c.get("timestamp", ""))

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
            "model_calls": run_model_calls,
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


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器，避免单请求阻塞"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        """抑制 BrokenPipeError 的错误输出"""
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return  # 客户端断开，静默忽略
        super(ThreadingHTTPServer, self).handle_error(request, client_address)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    data_store = None
    access_token = None
    config_path = None
    config_data = None
    static_dir = None  # 静态文件目录
    advanced_mode = False  # 运行模式

    def log_message(self, format, *args):
        pass

    def _check_token(self, params):
        if not self.access_token:
            return True
        tokens = params.get("token", [])
        if tokens and tokens[0] == self.access_token:
            return True
        return False

    # 静态文件缓存: filepath -> (mtime, body_bytes, etag, gzip_bytes)
    _static_cache = {}

    def _accepts_gzip(self):
        """检查客户端是否支持 gzip"""
        ae = self.headers.get("Accept-Encoding", "")
        return "gzip" in ae

    def _gzip_body(self, body):
        """gzip 压缩 bytes，返回压缩后的 bytes"""
        return gzip.compress(body, compresslevel=6)

    def _send_body(self, body, content_type, status=200, cache_control="no-cache", etag=None):
        """统一发送响应，自动处理 gzip 和 ETag"""
        try:
            # ETag 304 检查
            if etag:
                inm = self.headers.get("If-None-Match", "")
                if inm == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", cache_control)
                    self.end_headers()
                    return

            # gzip 压缩 (>1KB 的文本响应)
            use_gzip = False
            gzip_body = None
            if self._accepts_gzip() and len(body) > 1024:
                ct_lower = content_type.lower()
                if any(t in ct_lower for t in ("text/", "json", "javascript", "xml", "svg")):
                    gzip_body = self._gzip_body(body)
                    # 只在压缩有效时使用 (至少省 10%)
                    if len(gzip_body) < len(body) * 0.9:
                        use_gzip = True

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Access-Control-Allow-Origin", "*")
            if etag:
                self.send_header("ETag", etag)
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(gzip_body)))
                self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                self.wfile.write(gzip_body)
            else:
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 客户端已断开，忽略

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_body(body, "application/json; charset=utf-8", status=status)

    def _send_file(self, filepath, content_type=None):
        """发送静态文件，支持缓存、ETag、gzip"""
        if not os.path.isfile(filepath):
            self.send_error(404, "Not Found")
            return
        if content_type is None:
            ext = os.path.splitext(filepath)[1].lower()
            content_type = MIME_TYPES.get(ext, "application/octet-stream")

        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            mtime = 0

        # 检查静态文件缓存
        cached = DashboardHandler._static_cache.get(filepath)
        if cached and cached[0] == mtime:
            body, etag = cached[1], cached[2]
        else:
            try:
                with open(filepath, "rb") as f:
                    body = f.read()
            except (IOError, OSError):
                self.send_error(500, "Read Error")
                return
            etag = '"%s"' % hashlib.md5(body).hexdigest()
            DashboardHandler._static_cache[filepath] = (mtime, body, etag)

        # 带版本号的静态资源用长缓存，否则 no-cache
        qs = urlparse(self.path).query
        if "v=" in qs:
            cache_ctl = "public, max-age=86400, immutable"
        else:
            cache_ctl = "no-cache"

        self._send_body(body, content_type, cache_control=cache_ctl, etag=etag)

    def do_HEAD(self):
        """支持 HEAD 请求（浏览器预检、缓存验证）"""
        self.do_GET()

    def do_OPTIONS(self):
        """支持 CORS 预检请求"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        """处理 POST 请求 — 探测命令"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if not self._check_token(params):
            self._send_json({"error": "Unauthorized"}, 403)
            return

        try:
            if path == "/api/probe/all":
                # 串行执行所有探测
                results = {}
                total_ms = 0
                passed = 0
                failed = 0
                for name in PROBES:
                    r = run_probe(name)
                    results[name] = r
                    total_ms += r.get("duration_ms", 0)
                    if r.get("ok"):
                        passed += 1
                    else:
                        failed += 1
                self._send_json({
                    "ok": failed == 0,
                    "probes": results,
                    "summary": {
                        "total": len(PROBES),
                        "passed": passed,
                        "failed": failed,
                        "total_duration_ms": total_ms,
                    },
                    "timestamp": _utcnow_iso(),
                })
            elif path.startswith("/api/probe/"):
                probe_name = path[len("/api/probe/"):]
                if probe_name not in PROBES:
                    self._send_json({"error": "未知探测项: %s" % probe_name, "available": list(PROBES.keys())}, 404)
                    return
                result = run_probe(probe_name)
                self._send_json(result)
            else:
                self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._send_json({"error": str(e)}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

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
            elif path == "/api/mode":
                # 返回当前运行模式信息
                log_dir = self.data_store.log_dir
                sessions_dirs = self.data_store.sessions_dirs
                debug_log_available = os.path.isdir(log_dir) and len(glob.glob(os.path.join(log_dir, "openclaw-*.log"))) > 0
                session_files_available = False
                for d in sessions_dirs:
                    if len(glob.glob(os.path.join(d, "*.jsonl"))) > 0:
                        session_files_available = True
                        break
                self._send_json({
                    "mode": "advanced" if self.advanced_mode else "standard",
                    "debug_log_available": debug_log_available,
                    "session_files_available": session_files_available,
                })
            elif path == "/api/system_info":
                info = get_system_info(self.data_store, self.config_path, self.config_data)
                self._send_json(info)
            elif path == "/api/probes":
                # 列出所有可用探测项
                probes_list = []
                for name, probe in PROBES.items():
                    probes_list.append({
                        "name": name,
                        "label": probe["label"],
                        "description": probe["description"],
                        "icon": probe.get("icon", ""),
                        "format": probe["format"],
                        "timeout": probe["timeout"],
                    })
                self._send_json({"probes": probes_list})
            elif path == "/api/dashboard":
                # 批量接口: 一次返回 summary + events + runs，减少前端请求数
                date = params.get("date", [""])[0]
                if not date:
                    dates = self.data_store.get_dates()
                    date = dates[0] if dates else ""
                if not date:
                    self._send_json({
                        "date": "",
                        "summary": {"total_runs": 0, "session_model_call_count": 0},
                        "model_calls": {"date": "", "model_calls": [], "total": 0, "page": 1, "per_page": 50, "total_pages": 1},
                    })
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
                result = {
                    "date": date,
                    "summary": self.data_store.get_summary(date, self.advanced_mode),
                    "restarts": self.data_store.get_restarts(date),
                    "probes_available": list(PROBES.keys()),
                }
                # 高级模式额外返回 events, runs, errors
                if self.advanced_mode:
                    result["events"] = self.data_store.get_events_summary(date)
                    result["runs"] = self.data_store.get_runs_list(date, page, per_page)
                    result["errors"] = self.data_store.get_events_errors(date)
                # 标准模式返回 model_calls
                mc_page = 1
                mc_per_page = 50
                try:
                    mc_page = int(params.get("mc_page", ["1"])[0])
                except (ValueError, IndexError):
                    pass
                try:
                    mc_per_page = int(params.get("mc_per_page", ["50"])[0])
                except (ValueError, IndexError):
                    pass
                mc_per_page = max(1, min(mc_per_page, 200))
                result["model_calls"] = self.data_store.get_model_calls(date, mc_page, mc_per_page)
                self._send_json(result)
            elif path == "/api/summary":
                date = params.get("date", [""])[0]
                if not date:
                    dates = self.data_store.get_dates()
                    date = dates[0] if dates else ""
                if not date:
                    self._send_json({"total_runs": 0})
                    return
                summary = self.data_store.get_summary(date, self.advanced_mode)
                self._send_json(summary)
            elif path == "/api/runs":
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
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
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
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
            # === New event API endpoints ===
            elif path == "/api/events":
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
                date = params.get("date", [""])[0]
                if not date:
                    dates = self.data_store.get_dates()
                    date = dates[0] if dates else ""
                if not date:
                    self._send_json({"date": "", "summary": {}})
                    return
                result = self.data_store.get_events_summary(date)
                self._send_json(result)
            elif path == "/api/events/timeline":
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
                date = params.get("date", [""])[0]
                if not date:
                    self._send_json({"events": [], "total": 0, "page": 1, "per_page": 50, "total_pages": 1})
                    return
                page = 1
                per_page = 50
                try:
                    page = int(params.get("page", ["1"])[0])
                except (ValueError, IndexError):
                    pass
                try:
                    per_page = int(params.get("per_page", ["50"])[0])
                except (ValueError, IndexError):
                    pass
                category = params.get("category", [""])[0]
                per_page = max(1, min(per_page, 500))
                result = self.data_store.get_events_timeline(date, page, per_page, category or None)
                self._send_json(result)
            elif path == "/api/events/webhooks":
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
                date = params.get("date", [""])[0]
                if not date:
                    self._send_json({"date": "", "webhooks": [], "total": 0})
                    return
                result = self.data_store.get_events_webhooks(date)
                self._send_json(result)
            elif path == "/api/events/messages":
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
                date = params.get("date", [""])[0]
                if not date:
                    self._send_json({"date": "", "messages": [], "total": 0})
                    return
                result = self.data_store.get_events_messages(date)
                self._send_json(result)
            elif path == "/api/events/errors":
                if not self.advanced_mode:
                    self._send_json({"available": False, "message": "需要高级诊断模式 (--advanced)"})
                    return
                date = params.get("date", [""])[0]
                if not date:
                    self._send_json({"date": "", "errors": [], "total": 0})
                    return
                severity = params.get("severity", [""])[0] or None
                err_type = params.get("type", [""])[0] or None
                result = self.data_store.get_events_errors(date, severity=severity, err_type=err_type)
                self._send_json(result)
            elif path == "/api/restarts":
                date = params.get("date", [""])[0] or None
                result = self.data_store.get_restarts(date)
                self._send_json(result)
            elif path == "/api/model_calls":
                date = params.get("date", [""])[0]
                if not date:
                    dates = self.data_store.get_dates()
                    date = dates[0] if dates else ""
                if not date:
                    self._send_json({"date": "", "model_calls": [], "total": 0, "page": 1, "per_page": 50, "total_pages": 1})
                    return
                page = 1
                per_page = 50
                try:
                    page = int(params.get("page", ["1"])[0])
                except (ValueError, IndexError):
                    pass
                try:
                    per_page = int(params.get("per_page", ["50"])[0])
                except (ValueError, IndexError):
                    pass
                per_page = max(1, min(per_page, 200))
                result = self.data_store.get_model_calls(date, page, per_page)
                self._send_json(result)
            elif path == "/api/tool_stats":
                date = params.get("date", [""])[0]
                if not date:
                    dates = self.data_store.get_dates()
                    date = dates[0] if dates else ""
                result = self.data_store.get_tool_stats(date)
                self._send_json(result)
            elif path == "/api/sessions":
                date = params.get("date", [""])[0]
                result = self.data_store.get_sessions_list(date)
                self._send_json(result)
            elif path == "/api/conversation_tree":
                session_id = params.get("session_id", [""])[0]
                date = params.get("date", [""])[0]
                result = self.data_store.get_conversation_tree(session_id, date)
                self._send_json(result)
            elif path == "/api/debug/sessions":
                # 诊断端点：检查 session 文件解析情况
                td = self.data_store._get_tool_data()
                tru = self.data_store._get_text_reply_usage()
                has_usage = sum(1 for v in td.values() if v.get("usage", {}).get("output", 0) > 0)
                sample_keys = list(td.keys())[:3]
                samples = []
                for k in sample_keys:
                    v = td[k]
                    samples.append({
                        "toolCallId": k,
                        "tool": v.get("tool", ""),
                        "usage": v.get("usage", {}),
                    })
                tru_has = sum(1 for _, u in tru if u.get("output", 0) > 0)
                self._send_json({
                    "sessions_dirs": self.data_store.sessions_dirs,
                    "tool_data_count": len(td),
                    "tool_data_with_output": has_usage,
                    "text_reply_count": len(tru),
                    "text_reply_with_output": tru_has,
                    "samples": samples,
                })
            else:
                self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 客户端已断开
        except Exception as e:
            traceback.print_exc()
            try:
                self._send_json({"error": str(e)}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


# ============================================================
# 主程序
# ============================================================

def run_cli_mode(args):
    """CLI 模式：不启动 web 服务器，直接在终端执行探测并输出结果"""
    json_output = getattr(args, 'json', False)

    # 确定要执行哪些探测
    probe_name = getattr(args, 'probe', None)
    if probe_name and probe_name != "all":
        probe_names = [probe_name]
    else:
        probe_names = list(PROBES.keys())

    if json_output:
        # JSON 输出模式
        results = {}
        total_ms = 0
        passed = 0
        failed = 0
        for name in probe_names:
            r = run_probe(name)
            results[name] = r
            total_ms += r.get("duration_ms", 0)
            if r.get("ok"):
                passed += 1
            else:
                failed += 1
        output = {
            "version": VERSION,
            "timestamp": _utcnow_iso(),
            "probes": results,
            "summary": {
                "total": len(probe_names),
                "passed": passed,
                "failed": failed,
                "total_duration_ms": total_ms,
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0 if failed == 0 else 1)
    else:
        # 人类可读输出
        print("")
        print("\033[1m🦞 OpenClaw 诊断工具 v%s\033[0m" % VERSION)
        print("━" * 40)
        print("")

        total_ms = 0
        passed = 0
        failed = 0

        for name in probe_names:
            probe = PROBES[name]
            sys.stdout.write("%s %s ... " % (probe.get("icon", ""), probe["label"]))
            sys.stdout.flush()
            r = run_probe(name)
            dur_s = r.get("duration_ms", 0) / 1000.0
            total_ms += r.get("duration_ms", 0)

            if r.get("ok"):
                passed += 1
                print("\033[32m✅ (%.1fs)\033[0m" % dur_s)
            else:
                failed += 1
                err = r.get("error", "")
                print("\033[31m❌ (%.1fs)\033[0m" % dur_s)
                if err:
                    print("  \033[31m错误: %s\033[0m" % err)

            # 显示输出内容
            output = r.get("output", {})
            if output.get("data"):
                data = output["data"]
                # 为 JSON 格式提取关键信息
                if name == "health":
                    _print_health_summary(data)
                elif name == "gateway_status":
                    _print_gateway_summary(data)
                else:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
            elif output.get("raw"):
                raw = output["raw"].strip()
                if raw:
                    for line in raw.split("\n"):
                        print("  %s" % line)

            if r.get("stderr"):
                print("  \033[33m[stderr] %s\033[0m" % r["stderr"][:200])

            print("")

        # 总结
        print("━" * 40)
        total_s = total_ms / 1000.0
        status_str = "\033[32m全部通过\033[0m" if failed == 0 else "\033[31m%d 项失败\033[0m" % failed
        print("总耗时: %.1fs | %d 项探测 | %s" % (total_s, len(probe_names), status_str))
        print("")
        sys.exit(0 if failed == 0 else 1)


def _print_health_summary(data):
    """为 health 探测提取关键信息的可读输出"""
    if not isinstance(data, dict):
        print("  %s" % str(data)[:300])
        return
    # Agents
    agents = data.get("agents", [])
    if isinstance(agents, list) and agents:
        agent_names = []
        for a in agents:
            if isinstance(a, dict):
                agent_names.append(a.get("name", a.get("agentId", "?")))
            elif isinstance(a, str):
                agent_names.append(a)
        print("  Agent: %d 个 (%s)" % (len(agent_names), ", ".join(agent_names[:10])))
        # 每个 agent 的 session 数
        for a in agents:
            if isinstance(a, dict):
                aid = a.get("agentId", a.get("id", a.get("name", "?")))
                a_sessions = a.get("sessions", {})
                if isinstance(a_sessions, dict):
                    count = a_sessions.get("count", "?")
                    print("    %s: %s sessions" % (aid, count))
    # 全局 sessions
    sessions = data.get("sessions", {})
    if isinstance(sessions, dict):
        total = sessions.get("count", "?")
        print("  Session 总数: %s" % total)


def _print_gateway_summary(data):
    """为 gateway_status 探测提取关键信息的可读输出"""
    if not isinstance(data, dict):
        print("  %s" % str(data)[:300])
        return
    # service.runtime
    service = data.get("service", {})
    runtime = service.get("runtime", {}) if isinstance(service, dict) else {}
    pid = runtime.get("pid", "?") if isinstance(runtime, dict) else "?"
    state = runtime.get("state", "?") if isinstance(runtime, dict) else "?"
    sub_state = runtime.get("subState", "") if isinstance(runtime, dict) else ""
    state_str = "%s/%s" % (state, sub_state) if sub_state else state
    # gateway
    gw = data.get("gateway", {})
    port = gw.get("port", "?") if isinstance(gw, dict) else "?"
    bind_mode = gw.get("bindMode", "") if isinstance(gw, dict) else ""
    # rpc
    rpc = data.get("rpc", {})
    rpc_ok = rpc.get("ok", False) if isinstance(rpc, dict) else False
    rpc_str = "✅ 正常" if rpc_ok else "❌ 异常"
    print("  PID: %s | 端口: %s (%s) | 状态: %s | RPC: %s" % (pid, port, bind_mode, state_str, rpc_str))
    # config audit
    config_audit = service.get("configAudit", {}) if isinstance(service, dict) else {}
    if isinstance(config_audit, dict):
        audit_ok = config_audit.get("ok", True)
        issues = config_audit.get("issues", [])
        if not audit_ok and issues:
            print("  ⚠️ 配置问题: %s" % "; ".join(str(i) for i in issues[:3]))


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw 诊断面板 v%s — 零依赖 Web Dashboard + CLI 探测工具" % VERSION
    )
    parser.add_argument("--port", type=int, default=9090, help="监听端口 (默认 9090)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址 (默认 0.0.0.0)")
    parser.add_argument("--log-dir", type=str, default="", help="日志目录")
    parser.add_argument("--sessions-dir", type=str, default="", help="会话文件目录")
    parser.add_argument("--token", type=str, default="", help="访问令牌 (可选)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--advanced", action="store_true", help="启用高级诊断模式 (需要 debug 日志)")
    parser.add_argument("--cli", action="store_true", help="CLI 模式（不启动 web 服务）")
    parser.add_argument("--probe", choices=list(PROBES.keys()) + ["all"], default=None,
                        help="CLI: 执行指定探测项 (默认 all)")
    parser.add_argument("--json", action="store_true", help="CLI: JSON 格式输出")
    args = parser.parse_args()

    # CLI 模式
    if args.cli:
        if args.probe is None:
            args.probe = "all"
        run_cli_mode(args)
        return

    # 设置全局模式标志
    global ADVANCED_MODE
    ADVANCED_MODE = args.advanced

    # 路径检测
    log_dir = detect_log_dir(args.log_dir)
    sessions_dirs = detect_sessions_dirs(args.sessions_dir)

    # 检测 OpenClaw 配置（高级模式才检查 debug 日志配置）
    config_ok = True
    config_warnings = []
    config_path = None
    config_data = None
    if ADVANCED_MODE:
        config_ok, config_warnings, config_path, config_data = check_openclaw_config()
    else:
        # 标准模式：只读取配置文件获取基本信息，不检查 debug 日志配置
        _, _, config_path, config_data = check_openclaw_config()
        config_ok = True  # 标准模式不需要 debug 配置

    # 打印启动信息
    mode_label = "高级诊断模式" if ADVANCED_MODE else "标准模式"
    print("")
    print("🦞 OpenClaw 诊断面板 v%s" % VERSION)
    print("━" * 35)
    print("模式: %s" % mode_label)

    if ADVANCED_MODE:
        print("数据源: debug 日志 + session 文件 + 系统信息")
        print("日志目录: %s" % log_dir)
        if sessions_dirs:
            print("Session 目录: %s" % ", ".join(sessions_dirs))
        if config_ok:
            print("[✓] diagnostics.enabled = true")
            print("[✓] logging.level = debug")
        for w in config_warnings:
            print(w)
    else:
        print("数据源: session 文件 + 系统信息")
        if sessions_dirs:
            total_sessions = 0
            for d in sessions_dirs:
                total_sessions += len(glob.glob(os.path.join(d, "*.jsonl")))
            print("Session 目录: %s" % ", ".join(sessions_dirs))
        print("日志目录: %s (仅用于重启检测)" % log_dir)

    if os.path.isdir(log_dir):
        log_files_count = len(glob.glob(os.path.join(log_dir, "openclaw-*.log")))
        if ADVANCED_MODE:
            print("日志文件: %d 个" % log_files_count)
    else:
        if ADVANCED_MODE:
            print("[警告] 日志目录 %s 不存在, 等待日志生成..." % log_dir)

    if sessions_dirs:
        total_sessions = 0
        for d in sessions_dirs:
            total_sessions += len(glob.glob(os.path.join(d, "*.jsonl")))
        if ADVANCED_MODE:
            print("会话文件: %d 个" % total_sessions)
    else:
        print("[警告] 会话目录未找到, Token 数据将不可用")

    # 初始化数据存储
    store = DataStore(log_dir, sessions_dirs)
    store.start_preload()  # 后台预加载 session 数据
    DashboardHandler.data_store = store
    DashboardHandler.access_token = args.token if args.token else None
    DashboardHandler.config_path = config_path
    DashboardHandler.config_data = config_data
    DashboardHandler.advanced_mode = ADVANCED_MODE
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
            class IPv6Server(ThreadingHTTPServer):
                address_family = socket.AF_INET6
            server = IPv6Server((host, port), DashboardHandler)
        except (OSError, socket.error) as e:
            print("[警告] IPv6 绑定失败 (%s), 回退到 IPv4..." % e)
            host = "0.0.0.0"

    if server is None:
        try:
            server = ThreadingHTTPServer((host, port), DashboardHandler)
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

    print("")
    print("Dashboard: %s" % url)
    print("按 Ctrl+C 退出")
    print("━" * 35)

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
