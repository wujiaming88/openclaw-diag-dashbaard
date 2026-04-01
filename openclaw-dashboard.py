#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw 诊断面板 v4.0
Python 后端 API 服务 + 静态文件服务
前端文件位于 static/ 目录

v4.0 新增: 采集端-Server 分离模式
  - POST /api/report 接收远程节点上报
  - GET /api/nodes 节点列表
  - GET /api/node/<node_id>/* 按节点查询
  - 节点超时检测 (30分钟)
  - --api-key 启动参数

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
import hmac
import json
import math
import os
import platform
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
import traceback
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# ============================================================
# 全局常量
# ============================================================
VERSION = "4.2.0"

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


class NodeStore(object):
    """管理多节点数据（内存存储，分节点锁）"""

    def __init__(self):
        self._nodes = {}  # node_id -> {node_name, last_report_at, payload, node_type}
        self._global_lock = threading.Lock()  # 保护 _nodes 字典结构和 _node_locks
        self._node_locks = {}  # node_id -> threading.Lock  —— 分节点锁

    def _get_node_lock(self, node_id):
        """获取或创建节点专属锁（需在 _global_lock 外调用）"""
        with self._global_lock:
            if node_id not in self._node_locks:
                self._node_locks[node_id] = threading.Lock()
            return self._node_locks[node_id]

    def upsert(self, node_id, node_name, payload, node_type="remote"):
        """新增或更新节点数据（分节点锁，不阻塞其他节点）"""
        node_lock = self._get_node_lock(node_id)
        with node_lock:
            with self._global_lock:
                self._nodes[node_id] = {
                    "node_name": node_name,
                    "last_report_at": datetime.now(timezone.utc).isoformat(),
                    "payload": payload,
                    "node_type": node_type,
                }

    def delete_node(self, node_id):
        """删除节点及其数据，返回 True 表示成功删除，False 表示不存在"""
        with self._global_lock:
            if node_id not in self._nodes:
                return False
            del self._nodes[node_id]
            self._node_locks.pop(node_id, None)
            return True

    def get_node(self, node_id):
        """获取节点数据，返回 None 如果不存在"""
        with self._global_lock:
            return self._nodes.get(node_id)

    def get_nodes_list(self):
        """返回所有远程节点列表（不含本机）"""
        now = datetime.now(timezone.utc)
        result = []
        with self._global_lock:
            for nid, ndata in self._nodes.items():
                node_type = ndata.get("node_type", "remote")
                last_ts = ndata.get("last_report_at", "")
                try:
                    last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    elapsed = (now - last_dt).total_seconds()
                    status = "online" if elapsed < 1800 else "offline"
                except (ValueError, TypeError):
                    status = "offline"
                result.append({
                    "node_id": nid,
                    "node_name": ndata.get("node_name", nid),
                    "last_report_at": ndata.get("last_report_at", ""),
                    "status": status,
                })
        return result

    def get_node_payload(self, node_id):
        """获取节点 payload"""
        with self._global_lock:
            ndata = self._nodes.get(node_id)
            if ndata:
                return ndata.get("payload", {})
            return None




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

    node_store = None
    access_token = None
    api_key = None  # 上报认证密钥（同时用于 Dashboard 登录）
    config_path = None
    config_data = None
    static_dir = None  # 静态文件目录
    _session_secret = None  # HMAC 签名密钥（启动时随机生成）

    # 不需要登录的路径白名单
    _PUBLIC_PATHS = frozenset(["/login", "/api/report"])

    def log_message(self, format, *args):
        pass

    @classmethod
    def _init_session_secret(cls):
        """启动时生成随机 session 签名密钥"""
        if cls._session_secret is None:
            cls._session_secret = secrets.token_hex(32)

    def _make_session_cookie(self):
        """生成 HMAC 签名的 session cookie"""
        ts = str(int(time.time()))
        msg = "openclaw-dash:" + ts
        sig = hmac.new(self._session_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return "%s.%s" % (ts, sig)

    def _verify_session_cookie(self, cookie_val):
        """验证 session cookie 签名，有效期 24h"""
        if not cookie_val or "." not in cookie_val:
            return False
        try:
            ts_str, sig = cookie_val.split(".", 1)
            ts = int(ts_str)
        except (ValueError, TypeError):
            return False
        # 过期检查 (24h)
        if abs(time.time() - ts) > 86400:
            return False
        expected = hmac.new(self._session_secret.encode(), ("openclaw-dash:" + ts_str).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

    def _check_dashboard_auth(self):
        """检查 Dashboard 访问认证，返回 True 表示已认证"""
        # 未配置 api_key 则不需要登录
        if not self.api_key:
            return True
        # 检查 session cookie
        cookie_header = self.headers.get("Cookie", "")
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
        except Exception:
            return False
        morsel = cookies.get("_oc_dash_session")
        if morsel and self._verify_session_cookie(morsel.value):
            return True
        return False

    def _send_login_page(self, error_msg=""):
        """返回登录页面 HTML"""
        error_html = ""
        if error_msg:
            error_html = '<div class="error">%s</div>' % error_msg
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ArkClaw 诊断面板 — 登录</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f172a; color: #e2e8f0; display: flex; align-items: center;
       justify-content: center; min-height: 100vh; }
.login-box { background: #1e293b; border-radius: 12px; padding: 40px;
             width: 380px; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
.login-box h1 { font-size: 22px; margin-bottom: 8px; color: #38bdf8; }
.login-box p { font-size: 13px; color: #94a3b8; margin-bottom: 24px; }
.login-box label { display: block; font-size: 13px; color: #94a3b8; margin-bottom: 6px; }
.login-box input[type=password] { width: 100%%; padding: 10px 14px; border: 1px solid #334155;
       border-radius: 8px; background: #0f172a; color: #e2e8f0; font-size: 15px;
       outline: none; transition: border 0.2s; }
.login-box input[type=password]:focus { border-color: #38bdf8; }
.login-box button { width: 100%%; padding: 10px; margin-top: 16px; border: none;
       border-radius: 8px; background: #2563eb; color: #fff; font-size: 15px;
       cursor: pointer; transition: background 0.2s; }
.login-box button:hover { background: #1d4ed8; }
.error { background: #7f1d1d; color: #fca5a5; padding: 8px 12px; border-radius: 6px;
         font-size: 13px; margin-bottom: 16px; }
.lock-icon { font-size: 36px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="login-box">
  <div class="lock-icon">🔒</div>
  <h1>ArkClaw 诊断面板</h1>
  <p>请输入 API Key 以访问诊断面板</p>
  %s
  <form method="POST" action="/login" autocomplete="off">
    <label for="key">API Key</label>
    <input type="password" id="key" name="key" placeholder="输入 API Key..." autofocus required>
    <button type="submit">登 录</button>
  </form>
</div>
</body>
</html>''' % error_html
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 防止缓存登录页
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

    def _check_token(self, params):
        if not self.access_token:
            return True
        tokens = params.get("token", [])
        if tokens and tokens[0] == self.access_token:
            return True
        return False

    def _handle_report(self):
        """处理采集端上报请求"""
        # 检查 API Key
        if not self.api_key:
            self._send_json({"error": "Report endpoint disabled (no API key configured)"}, 403)
            return

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json({"error": "Missing or invalid Authorization header"}, 401)
            return

        token = auth_header[7:].strip()
        if token != self.api_key:
            self._send_json({"error": "Invalid API key"}, 401)
            return

        # 读取请求体（支持 gzip 压缩）
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 20 * 1024 * 1024:  # 20MB 限制（压缩前可能更大）
                self._send_json({"error": "Request body too large"}, 413)
                return
            body = self.rfile.read(content_length)

            # gzip 解压
            content_encoding = self.headers.get("Content-Encoding", "").lower()
            if content_encoding == "gzip":
                import gzip as _gzip
                body = _gzip.decompress(body)

            data = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({"error": "Invalid JSON: %s" % str(e)}, 400)
            return
        except Exception as e:
            self._send_json({"error": "Read error: %s" % str(e)}, 400)
            return

        # 验证必填字段
        # 使用客户端 IP 作为唯一节点 key（忽略自报的 node_id）
        client_ip = self.client_address[0] if self.client_address else "unknown"
        node_id = client_ip
        node_name = data.get("node_name", client_ip)
        payload = data.get("payload", {})

        if not isinstance(payload, dict):
            self._send_json({"error": "payload must be an object"}, 400)
            return

        # 存储数据
        if self.node_store:
            self.node_store.upsert(node_id, node_name, payload, node_type="remote")

        self._send_json({
            "ok": True,
            "node_id": node_id,
            "message": "Report received",
            "timestamp": _utcnow_iso(),
        })

    @staticmethod
    def _mask_env_vars(env_dict):
        """对敏感环境变量值进行脱敏"""
        import re
        sensitive_keys = re.compile(r'(key|token|secret|password|auth|credential|bearer|private|absk)', re.I)
        sensitive_vals = re.compile(r'^(sk-|ghp_|ghu_|AKIA|eyJ|xox[bposa]-)', re.I)

        def mask(val):
            s = str(val)
            if len(s) <= 8:
                return s[:2] + "***"
            return s[:4] + "***" + s[-4:]

        masked = {}
        for k, v in env_dict.items():
            vs = str(v)
            if sensitive_keys.search(k) or sensitive_vals.match(vs):
                masked[k] = mask(vs)
            else:
                masked[k] = v
        return masked

    @staticmethod
    def _normalize_restarts(raw_restarts):
        """将 Collector 上报的 restarts 格式化为前端 renderRestarts 期望的格式"""
        normalized = []
        for i, r in enumerate(raw_restarts, 1):
            normalized.append({
                "num": i,
                "type": r.get("type", "UNKNOWN"),
                "shutdown_utc": r.get("timestamp", r.get("shutdown_utc")),
                "startup_utc": r.get("startup_utc"),
                "downtime_sec": r.get("downtime_sec", 0),
                "reason": r.get("details", r.get("reason", "")),
            })
        return {"restarts": normalized, "total": len(normalized), "current_pid": "", "current_since": ""}

    @staticmethod
    def _normalize_model_calls(raw_calls):
        """将 Collector 上报的 model_calls 格式化为前端期望的格式"""
        normalized = []
        for mc in raw_calls:
            # 前端读 mc.usage.input/output/cacheRead
            usage = mc.get("usage", {})
            if not usage:
                usage = {
                    "input": mc.get("input_tokens", 0),
                    "output": mc.get("output_tokens", 0),
                    "cacheRead": mc.get("cache_read", 0),
                    "cacheWrite": mc.get("cache_write", 0),
                    "totalTokens": mc.get("input_tokens", 0) + mc.get("output_tokens", 0) + mc.get("cache_read", 0),
                }
            normalized.append({
                **mc,
                "usage": usage,
            })
        return normalized

    @staticmethod
    def _normalize_kpi(raw, payload):
        """将 Collector 上报的 kpi 字段映射为前端 renderSummary 期望的字段名"""
        mc = payload.get("model_calls", [])
        raw_ts = payload.get("tool_stats", [])
        # Collector 发送的 tool_stats 可能是嵌套对象 {"by_tool": [...], "total_calls": N}
        # 或直接是数组 [{name, count, ...}]
        if isinstance(raw_ts, dict):
            ts = raw_ts.get("by_tool", [])
        elif isinstance(raw_ts, list):
            ts = raw_ts
        else:
            ts = []
        restarts = payload.get("restarts", [])
        # 计算工具汇总
        total_tool_calls = sum(t.get("count", 0) for t in ts) if ts else raw.get("total_tool_calls", 0)
        total_tool_ms = sum(t.get("total_ms", 0) for t in ts)
        tool_error_count = sum(int(t.get("count", 0) * (1 - t.get("success_rate", 1))) for t in ts)
        avg_tool_ms = (total_tool_ms / total_tool_calls) if total_tool_calls else 0
        # 推理统计
        infer_items = [m for m in mc if m.get("inference_ms", 0) > 0]
        total_infer_ms = sum(m.get("inference_ms", 0) for m in infer_items) or raw.get("total_inference_ms", 0)
        avg_infer_ms = raw.get("avg_inference_ms", 0) or ((total_infer_ms / len(infer_items)) if infer_items else 0)
        tps_items = [m.get("tokens_per_sec", 0) for m in mc if m.get("tokens_per_sec", 0) > 0]
        avg_tps = (sum(tps_items) / len(tps_items)) if tps_items else 0
        # Thinking 统计
        thinking_stats = payload.get("thinking_stats", {})
        thinking_calls = [m for m in mc if m.get("thinking_chars", 0) > 0]
        thinking_total_chars = sum(m.get("thinking_chars", 0) for m in mc)
        thinking_calls_count = len(thinking_calls) or thinking_stats.get("total_thinking", 0)
        # 计算 thinking 占输出 token 比例 (chars ≈ tokens * 4)
        total_output_tokens = sum(m.get("output_tokens", 0) for m in mc) or raw.get("total_output_tokens", 0)
        thinking_avg_ratio = (thinking_total_chars / (thinking_total_chars + total_output_tokens * 4)) if (thinking_total_chars + total_output_tokens * 4) > 0 else 0
        return {
            # 前端 renderSummary 使用的字段 — 第一行
            "session_model_call_count": raw.get("total_model_calls", len(mc)),
            "session_avg_inference_ms": round(avg_infer_ms, 1),
            "session_inference_count": len(infer_items) or raw.get("total_model_calls", 0),
            "session_avg_tokens_per_sec": round(avg_tps, 1),
            "session_total_inference_ms": total_infer_ms,
            "restart_count": len(restarts),
            "total_downtime_sec": 0,
            # 第二行 — Token 统计
            "total_tokens_output": raw.get("total_output_tokens", sum(m.get("output_tokens", 0) for m in mc)),
            "total_tokens_input": raw.get("total_input_tokens", 0),
            "total_cache_read": 0,
            "total_cache_write": 0,
            "cache_hit_ratio": round(raw.get("cache_hit_rate", 0) * 100, 1) if raw.get("cache_hit_rate", 0) <= 1 else raw.get("cache_hit_rate", 0),
            # 第三行 — 工具统计
            "tool_call_count": total_tool_calls,
            "tool_error_count": tool_error_count,
            "tool_avg_duration_ms": round(avg_tool_ms, 1),
            "avg_tool_ms": round(avg_tool_ms, 1),
            # 第三行 — Thinking 统计
            "thinking_total_chars": thinking_total_chars or (thinking_stats.get("avg_chars", 0) * thinking_stats.get("total_thinking", 0)),
            "thinking_calls_count": thinking_calls_count,
            "thinking_avg_ratio": round(thinking_avg_ratio, 4),
            # 兼容: 保留原始字段
            **raw,
        }

    def _handle_node_route(self, path, params):
        """处理 /api/node/<node_id>/<endpoint> 路由"""
        # 解析 node_id 和 endpoint
        # path = /api/node/<node_id>/<endpoint>
        rest = path[len("/api/node/"):]
        if "/" not in rest:
            # /api/node/<node_id> — 返回节点信息
            node_id = rest
            endpoint = ""
        else:
            node_id, endpoint = rest.split("/", 1)

        if not node_id:
            self._send_json({"error": "Missing node_id"}, 400)
            return

        if not self.node_store:
            self._send_json({"error": "Node store not initialized"}, 500)
            return

        # 远程节点：从 payload 返回数据
        payload = self.node_store.get_node_payload(node_id)
        if payload is None:
            self._send_json({"error": "Node not found: %s" % node_id}, 404)
            return

        if not endpoint or endpoint == "dashboard":
            # 返回完整 dashboard 数据
            date = params.get("date", [""])[0]
            raw_kpi = payload.get("kpi", payload.get("summary", {}))
            summary = self._normalize_kpi(raw_kpi, payload)
            result = {
                "date": date,
                "summary": summary,
                "restarts": self._normalize_restarts(payload.get("restarts", [])),
                "model_calls": {
                    "date": date,
                    "model_calls": self._normalize_model_calls(payload.get("model_calls", [])),
                    "total": len(payload.get("model_calls", [])),
                    "page": 1,
                    "per_page": len(payload.get("model_calls", [])) or 50,
                    "total_pages": 1,
                },
                "probes_available": [],
                "events": payload.get("events_summary", {}),
                "runs": {
                    "runs": payload.get("runs", [])[:20],
                    "total": len(payload.get("runs", [])),
                    "page": 1,
                    "per_page": 20,
                    "total_pages": max(1, (len(payload.get("runs", [])) + 19) // 20),
                },
                "errors": {
                    "errors": payload.get("errors", []),
                    "total": len(payload.get("errors", [])),
                },
            }
            self._send_json(result)
        elif endpoint == "summary":
            # kpi 或 summary 字段均可，做字段映射
            raw_kpi = payload.get("kpi", payload.get("summary", {}))
            s = self._normalize_kpi(raw_kpi, payload)
            self._send_json(s)
        elif endpoint == "model_calls":
            mc = payload.get("model_calls", [])
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
            total = len(mc)
            start = (page - 1) * per_page
            end = start + per_page
            self._send_json({
                "model_calls": mc[start:end],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            })
        elif endpoint == "tool_stats":
            self._send_json(payload.get("tool_stats", {}))
        elif endpoint == "sessions":
            self._send_json(payload.get("sessions", []))
        elif endpoint == "conversation_tree":
            # 返回指定 session 的消息列表
            session_id = params.get("session_id", [""])[0]
            all_msgs = payload.get("session_messages", {})
            if session_id and session_id in all_msgs:
                self._send_json(all_msgs[session_id])
            else:
                # 尝试前缀匹配（前端可能只传了短 ID）
                for sid, msgs in all_msgs.items():
                    if sid.startswith(session_id):
                        self._send_json(msgs)
                        return
                self._send_json([])
        elif endpoint == "restarts":
            self._send_json(self._normalize_restarts(payload.get("restarts", [])))
        elif endpoint == "thinking_stats":
            self._send_json(payload.get("thinking_stats", {}))
        elif endpoint == "system_events":
            self._send_json(payload.get("system_events", []))
        elif endpoint == "model_switches":
            self._send_json(payload.get("model_switches", []))
        elif endpoint == "env_vars":
            raw_env = payload.get("env_vars", {})
            self._send_json(self._mask_env_vars(raw_env))
        elif endpoint == "openclaw_config":
            self._send_json(payload.get("openclaw_config", {}))
        elif endpoint == "bash_history":
            self._send_json(payload.get("bash_history", []))
        elif endpoint == "journalctl":
            self._send_json(payload.get("journalctl", []))
        elif endpoint == "dates":
            # 远程节点返回 payload 中的日期列表
            dates_list = payload.get("dates", [])
            if not dates_list:
                # fallback: 从 kpi/summary 提取
                s = payload.get("kpi", payload.get("summary", {}))
                d = s.get("date", "")
                dates_list = [d] if d else []
            self._send_json(dates_list)
        elif endpoint == "mode":
            has_debug = bool(payload.get("runs") or payload.get("events_timeline") or payload.get("errors"))
            self._send_json({"mode": "auto", "debug_log_available": has_debug, "session_files_available": True})
        elif endpoint == "runs":
            runs = payload.get("runs", [])
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
            total = len(runs)
            start = (page - 1) * per_page
            end = start + per_page
            self._send_json({
                "runs": runs[start:end],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            })
        elif endpoint == "events":
            self._send_json(payload.get("events_summary", {
                "summary": {}, "message_stats": {}, "session_stats": {}, "model_usage": {}
            }))
        elif endpoint.startswith("run/"):
            run_id = endpoint[4:]
            runs = payload.get("runs", [])
            found = None
            for r in runs:
                if r.get("run_id") == run_id:
                    found = r
                    break
            if found:
                # Build a basic run detail response
                self._send_json({
                    "run_id": found.get("run_id", ""),
                    "start": found.get("start", ""),
                    "end": found.get("end", ""),
                    "duration_ms": found.get("duration_ms", 0),
                    "model": found.get("model", ""),
                    "channel": found.get("channel", ""),
                    "tool_count": found.get("tool_count", 0),
                    "token_output": found.get("token_output", 0),
                    "status": found.get("status", "unknown"),
                    "overall_tok_per_s": 0,
                    "gantt": [],
                    "infer_segments": [],
                    "tools": [],
                })
            else:
                self._send_json({"error": "Run not found: %s" % run_id}, 404)
        else:
            self._send_json({"error": "Unknown endpoint: %s" % endpoint}, 404)

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
        """处理 POST 请求 — 探测命令 + 上报接收"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # === POST /api/report — 采集端上报 ===
        # 登录处理（公开路径）
        if path == "/login":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                # 解析 form 数据
                form_params = parse_qs(body)
                submitted_key = form_params.get("key", [""])[0].strip()
                if not self.api_key:
                    # 未配置 api_key，直接放行
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                if hmac.compare_digest(submitted_key, self.api_key):
                    cookie_val = self._make_session_cookie()
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie",
                        "_oc_dash_session=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400" % cookie_val)
                    self.end_headers()
                else:
                    self._send_login_page(error_msg="API Key 错误，请重试")
            except Exception:
                self._send_login_page(error_msg="请求异常，请重试")
            return

        # Collector 上报（Bearer token 认证，不走 session）
        if path == "/api/report":
            self._handle_report()
            return

        # Collector ping（返回客户端 IP，用于启动时显示节点 ID）
        if path == "/api/ping":
            auth_header = self.headers.get("Authorization", "")
            token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
            if not self.api_key or token != self.api_key:
                self._send_json({"error": "Unauthorized"}, 401)
                return
            client_ip = self.client_address[0] if self.client_address else "unknown"
            self._send_json({"node_id": client_ip})
            return

        # 以下路由需要 Dashboard 登录
        if not self._check_dashboard_auth():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        if not self._check_token(params):
            self._send_json({"error": "Unauthorized"}, 403)
            return

        try:
            self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._send_json({"error": str(e)}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def do_DELETE(self):
        """处理 DELETE 请求 — 节点数据删除"""
        parsed = urlparse(self.path)
        path = parsed.path

        # DELETE /api/node/<node_id>
        if not path.startswith("/api/node/"):
            self._send_json({"error": "Not found"}, 404)
            return

        node_id = path[len("/api/node/"):].strip("/")
        if not node_id:
            self._send_json({"error": "Missing node_id"}, 400)
            return

        # 认证：Bearer token 或 Dashboard session
        auth_header = self.headers.get("Authorization", "")
        authed = False
        if auth_header.startswith("Bearer ") and self.api_key:
            token = auth_header[7:].strip()
            authed = hmac.compare_digest(token, self.api_key)
        if not authed:
            authed = self._check_dashboard_auth()
        if not authed:
            self._send_json({"error": "Unauthorized"}, 401)
            return

        if self.node_store and self.node_store.delete_node(node_id):
            self._send_json({
                "ok": True,
                "node_id": node_id,
                "message": "Node data deleted",
                "timestamp": _utcnow_iso(),
            })
        else:
            self._send_json({"error": "Node not found: %s" % node_id}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Dashboard 访问认证（公开路径和 collector 上报不拦截）
        if path not in self._PUBLIC_PATHS and not path.startswith("/api/report"):
            if not self._check_dashboard_auth():
                # API 路径也接受 Bearer token 认证
                auth_header = self.headers.get("Authorization", "")
                if auth_header.startswith("Bearer ") and self.api_key:
                    token = auth_header[7:].strip()
                    if hmac.compare_digest(token, self.api_key):
                        pass  # Bearer 认证通过
                    else:
                        self._send_json({"error": "Invalid API key"}, 401)
                        return
                else:
                    self._send_login_page()
                    return

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
            elif path == "/api/nodes":
                # 返回所有节点列表
                if self.node_store:
                    nodes = self.node_store.get_nodes_list()
                    self._send_json(nodes)
                else:
                    self._send_json([])
            elif path.startswith("/api/node/"):
                # 节点专属路由: /api/node/<node_id>/<endpoint>
                self._handle_node_route(path, params)
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


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw 诊断面板 v%s — 零依赖 Web Dashboard" % VERSION
    )
    parser.add_argument("--port", type=int, default=9090, help="监听端口 (默认 9090)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址 (默认 0.0.0.0)")
    parser.add_argument("--token", type=str, default="", help="访问令牌 (可选)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--api-key", type=str, default="", help="上报认证密钥 (或环境变量 DIAG_API_KEY)")
    args = parser.parse_args()

    # 读取配置文件获取基本信息
    _, _, config_path, config_data = check_openclaw_config()

    # 打印启动信息
    print("")
    print("🦞 OpenClaw 诊断面板 v%s" % VERSION)
    print("━" * 35)
    print("模式: 纯远程 Server")
    print("数据源: 远程节点上报 (POST /api/report)")

    # 初始化节点存储（纯远程模式）
    node_store = NodeStore()

    # API Key
    api_key = args.api_key or os.environ.get("DIAG_API_KEY", "")

    DashboardHandler.node_store = node_store
    DashboardHandler.access_token = args.token if args.token else None
    DashboardHandler.api_key = api_key if api_key else None
    DashboardHandler._init_session_secret()  # 初始化 session 签名密钥
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
    if api_key:
        print("上报密钥: 已配置 (POST /api/report 已启用)")
    else:
        print("上报密钥: 未配置 (POST /api/report 已禁用)")
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
