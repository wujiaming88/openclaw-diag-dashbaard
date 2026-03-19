# OpenClaw 诊断面板

基于 Web 的 [OpenClaw](https://github.com/nicepkg/openclaw) AI 代理平台性能诊断工具。零外部依赖，开箱即用。

## 功能特性

### Web 面板 (`openclaw-dashboard.py`)

- **零依赖** — 仅使用 Python 标准库
- **前后端分离** — 后端 API + 静态文件 (`static/` 目录)
- **标准/高级模式** — 标准模式只读 session 文件（无需配置 debug 日志）；`--advanced` 解锁全部诊断功能
- **指标卡片** — KPI 卡片：Run 数、平均耗时、推理延迟、Token 吞吐量、错误数、Token 用量、缓存命中率
- **消息流水线** — 可视化管道：Message Queued → Queue Enqueue → Queue Dequeue → Run → Message Processed
- **模型调用详情** — 每个 Run 内嵌 LLM 调用明细：输入/输出 Token、缓存、费用、Thinking 预览、工具调用
- **推理耗时** — 从 session.jsonl 时间戳精确计算每次 LLM 调用的 inference_ms 和 tokens_per_sec
- **Gateway 重启历史** — 追踪 SHUTDOWN/TRIGGER/STARTUP/CRASH 四种事件，KPI 卡片 + 可折叠详情表格
- **探测面板** — 6 项内置探测（health、gateway status、config validate、doctor、update status、models status），一键执行，实时状态指示
- **Run 列表** — 分页表格，含耗时、模型、通道、状态、推理延迟、tok/s
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
- **优雅容错** — 缺失目录、损坏 JSON、超大日志、端口冲突均可存活

### CLI 模式

无需启动 Web 服务，直接在终端执行探测：

```bash
python3 openclaw-dashboard.py --cli                        # 执行全部 6 项探测
python3 openclaw-dashboard.py --cli --probe health         # 单项探测
python3 openclaw-dashboard.py --cli --probe gateway_status # Gateway 状态
python3 openclaw-dashboard.py --cli --json                 # JSON 格式输出
```

**可用探测项：**

| 探测项 | 说明 |
|--------|------|
| `health` | Agent 列表、Session 数量、频道状态 |
| `gateway_status` | PID、端口、绑定模式、服务状态、RPC 检测 |
| `config_validate` | 配置文件语法和结构校验 |
| `doctor` | 全面审计：安全、技能、插件、会话锁、内存 |
| `update_status` | 安装版本、更新通道、可用更新 |
| `models_status` | 默认模型、Fallback、认证状态、已配置模型 |

### Shell 脚本 (`openclaw-diag.sh`)

- **终端诊断** — 彩色终端输出，Run 时间线
- **实时跟踪** — `-f` 模式实时流式输出
- **摘要模式** — `-s` 快速统计概览
- **工具参数** — 从 session 文件提取工具调用参数
- **Token 统计** — 每次推理的 Token 用量明细
- **耗时分布** — 可视化条形图展示推理 vs 工具时间

## 前置条件

### 标准模式（默认）

无需任何配置修改。自动读取 session 文件（`~/.openclaw/agents/*/sessions/*.jsonl`）获取推理耗时、Token 用量和模型调用详情。

### 高级模式（`--advanced`）

需要完整日志级别支持 Run 事件和消息流水线：

```json
// ~/.openclaw/openclaw.json
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
python3 openclaw-dashboard.py               # 标准模式
python3 openclaw-dashboard.py --advanced     # 高级模式（需要 debug 日志）
# 打开 http://127.0.0.1:9090
```

### CLI 探测

```bash
python3 openclaw-dashboard.py --cli          # 全部探测，人类可读输出
python3 openclaw-dashboard.py --cli --json   # 全部探测，JSON 格式
```

### Shell 脚本

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
| `--advanced` | `false` | 启用高级诊断模式（需要 debug 日志） |
| `--cli` | `false` | CLI 模式（不启动 Web 服务） |
| `--probe NAME` | `all` | CLI: 指定探测项（`health`、`gateway_status`、`config_validate`、`doctor`、`update_status`、`models_status`、`all`） |
| `--json` | `false` | CLI: JSON 格式输出 |

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
| `POST /api/probe/<name>` | 执行单项探测 |
| `POST /api/probe/all` | 执行全部探测 |

所有响应：`Content-Type: application/json; charset=utf-8`，支持 gzip 压缩。

## 架构

### 数据源

1. **Session 文件** (`~/.openclaw/agents/*/sessions/*.jsonl`) — 模型调用详情、Token 用量、推理耗时、工具参数
2. **日志文件** (`/tmp/openclaw/openclaw-YYYY-MM-DD.log`) — Run 事件、耗时、工具执行 *（高级模式）*
3. **OpenClaw CLI** — 实时探测：`openclaw health`、`openclaw gateway status`、`openclaw doctor` 等

### 推理耗时计算

从 session.jsonl 消息时间戳精确计算：
- 提取 `assistant` 消息时间戳和前一条 `user`/`tool` 消息时间戳
- 计算 `inference_ms` = 最后输入到 assistant 响应的时间差
- 计算 `tokens_per_sec` = 输出 Token 数 / 推理耗时

### Gateway 重启检测

从日志文件识别 4 种事件类型：
- **SHUTDOWN** — 收到 SIGTERM 的优雅关闭
- **TRIGGER** — 外部触发重启（如 `openclaw gateway restart`）
- **STARTUP** — Gateway 启动事件
- **CRASH** — 意外终止（无前置 SIGTERM）

### 模型调用匹配

Session 文件中的模型调用通过以下方式匹配到 Run：
- **时间范围**：调用时间戳在 Run 的起止时间内
- **Session ID**：双 ID 匹配（文件名 ID + 内部 ID）

> **注意：** 子代理（waicode 等）的 session 可能是临时的。仅当 session 文件仍然存在时才能显示模型调用。

### 性能优化

- 批量接口减少 3 次请求为 1 次
- Gzip：app.js 28KB→8KB，style.css 13KB→3KB，API 约 76% 压缩率
- ETag + 304 静态文件缓存
- 长缓存（24h）用于带版本号的静态资源
- 关键 CSS 内联、脚本延迟加载、资源预加载
- 后端缓存 + mtime 失效策略

## 兼容性

- **Python**: 3.6+
- **操作系统**: Linux, macOS, Windows
- **浏览器**: Chrome, Firefox, Safari, Edge

## License

[MIT](LICENSE) © 2026 wujiaming88
