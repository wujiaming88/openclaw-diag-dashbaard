# OpenClaw 诊断面板

基于 Web 的 [OpenClaw](https://github.com/nicepkg/openclaw) AI 代理平台性能诊断工具。零外部依赖，开箱即用。

## 功能特性

### Web 面板 (`openclaw-dashboard.py`)

- **零依赖** — 仅使用 Python 标准库
- **前后端分离** — 后端 API + 静态文件 (`static/` 目录)
- **指标卡片** — 双行 KPI 卡片：Run 数、平均耗时、推理占比、Token 速率、错误数、Token 用量、缓存命中率
- **消息流水线** — 可视化管道：Message Queued → Queue Enqueue → Queue Dequeue → Run → Message Processed
- **模型调用详情** — 每个 Run 内嵌 LLM 调用明细：输入/输出 Token、缓存、费用、Thinking 预览、工具调用（从 session 文件匹配）
- **Run 列表** — 分页表格，含耗时、模型、通道、状态
- **Run 详情** — 可展开甘特图、推理分段、工具参数、Token 汇总
- **Gzip 压缩** — 自动压缩 >1KB 的响应（节省 72-76%）
- **ETag 缓存** — 静态文件 304 缓存验证
- **批量接口** — `/api/dashboard` 一次返回全部数据
- **骨架屏** — 首屏加载占位，提升感知性能
- **暗色主题** — GitHub Dark 风格 UI
- **自动刷新** — 5s 到 5min 可选
- **访问令牌** — 可选 `--token` 参数
- **跨平台** — Linux、macOS、Windows
- **Python 3.6+** — 兼容旧版本

### CLI 工具 (`openclaw-diag.sh`)

- **终端诊断** — 彩色终端输出，Run 时间线
- **实时跟踪** — `-f` 模式实时流式输出
- **摘要模式** — `-s` 快速统计概览
- **工具参数** — 从 session 文件提取工具调用参数
- **Token 统计** — 每次推理的 Token 用量明细
- **耗时分布** — 可视化条形图展示推理 vs 工具时间

## 前置条件：开启 OpenClaw 诊断

编辑 `~/.openclaw/openclaw.json`：

```json
{
  "diagnostics": {
    "enabled": true
  },
  "logging": {
    "level": "debug"
  }
}
```

然后重启：`openclaw gateway restart`

## 快速开始

### Web 面板

```bash
python3 openclaw-dashboard.py
# 打开 http://127.0.0.1:9090
```

### CLI 工具

```bash
./openclaw-diag.sh              # 今天的 Run
./openclaw-diag.sh 2026-03-11   # 指定日期
./openclaw-diag.sh -f           # 实时跟踪
./openclaw-diag.sh -l 5         # 最近 5 个 Run
./openclaw-diag.sh -s           # 仅摘要
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port PORT` | `9090` | 监听端口 |
| `--host HOST` | `0.0.0.0` | 绑定地址 |
| `--log-dir DIR` | 自动检测 | 日志目录 |
| `--sessions-dir DIR` | 自动检测 | 会话文件目录 |
| `--token TOKEN` | 无 | 访问令牌 |
| `--no-browser` | `false` | 不自动打开浏览器 |

### 路径自动检测

**日志目录：** `--log-dir` → `$OPENCLAW_LOG_DIR` → `/tmp/openclaw/` → `~/Library/Logs/openclaw/` → `%TEMP%/openclaw/`

**会话目录：** `--sessions-dir` → `$OPENCLAW_SESSIONS_DIR` → `~/.openclaw/agents/*/sessions/` → `$OPENCLAW_STATE_DIR/agents/*/sessions/`

## API 参考

| 端点 | 说明 |
|------|------|
| `GET /` | 面板页面 |
| `GET /api/dashboard?date=` | **批量接口**：summary + events + runs + errors |
| `GET /api/dates` | 可用日志日期 |
| `GET /api/system_info` | 系统信息 |
| `GET /api/summary?date=` | Run 摘要统计 |
| `GET /api/events?date=` | 事件摘要 + 流水线统计 |
| `GET /api/runs?date=&page=&per_page=` | 分页 Run 列表 |
| `GET /api/run/<id>?date=` | Run 详情（含 model_calls、甘特图、工具） |
| `GET /api/model_calls?date=` | 全部模型调用记录 |
| `GET /api/events/errors?date=&severity=&type=` | 错误列表（支持过滤） |

## 模型调用匹配

Session 文件中的模型调用通过以下方式匹配到 Run：
- **时间范围**：调用时间戳在 Run 的起止时间内
- **Session ID**：双 ID 匹配（文件名 ID + 内部 ID）

> **注意：** 子代理（waicode 等）的 session 可能是临时的。仅当 session 文件仍然存在时才能显示模型调用。

## 兼容性

- **Python**: 3.6+
- **操作系统**: Linux, macOS, Windows
- **浏览器**: Chrome, Firefox, Safari, Edge

## License

[MIT](LICENSE) © 2026 wujiaming88
