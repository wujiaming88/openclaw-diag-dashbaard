# OpenClaw 诊断面板

基于 Web 的 [OpenClaw](https://github.com/nicepkg/openclaw) AI 代理平台性能诊断工具。零外部依赖，纯 Python 标准库实现。

> **v3.2** — 多 Agent 过滤支持。新增 `-a/--agent` 参数，支持按 Agent 进行批量分析、摘要和实时跟踪。修复日期过滤准确性和 Agent 范围统计的关键 Bug。

## 📸 效果展示

### Dashboard 概览 — KPI 与系统探测

![Dashboard Overview](docs/screenshots/dashboard-overview.png)

*5 行 KPI 卡片：核心指标（Run 总数、推理占比、Token 吞吐量）、Token 详情、消息流水线、工具与 Thinking 统计。下方为 6 项系统探测面板。*

### 标准模式 — 完整页面

![Dashboard Standard](docs/screenshots/dashboard-standard.png)

*标准模式完整视图：KPI 概览 → 系统探测 → Gateway 重启历史 → 模型调用记录 → 会话浏览器 → 错误追踪。零配置即可使用。*

### 高级诊断模式 — 完整页面

![Dashboard Advanced](docs/screenshots/dashboard-advanced.png)

*高级模式在标准模式基础上，额外提供 Run 级别详情、事件时间线和调试日志分析。*

### CLI 命令行模式

```
🦞 OpenClaw 诊断工具 v3.2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏥 健康检查 ... ✅ (9.9s)
  Agent: 7 个 (Coordinator, Developer, Designer, Researcher, QA, ...)
    main: 49 sessions | waicode: 8 sessions | waidesign: 4 sessions ...
  Session 总数: 49

🌐 Gateway 状态 ... ✅ (12.0s)
  PID: 806521 | 端口: 18789 (loopback) | 状态: active/running | RPC: ✅ 正常

✅ 配置校验 ... ✅ (4.3s)
  Config valid: ~/.openclaw/openclaw.json

🔬 全面诊断 ... ✅ (17.9s)
  ┌  OpenClaw doctor
  ◇  Startup optimization ──────────────────────────────────╮
  │  - NODE_COMPILE_CACHE is not set                        │
  │  - OPENCLAW_NO_RESPAWN is not set to 1                  │
  ├─────────────────────────────────────────────────────────╯
```

### Shell 脚本 — Run 诊断报告

```
[OpenClaw 诊断报告]
日期: 2026-03-19

====================================================================
                             [摘要统计]
====================================================================
  Run 总数:        139
  工具调用总数:    1237
  工具调用:        1267 次 (失败 1, 成功率 100%)
  工具平均耗时:    876ms
  Top 工具:        exec(614), read(313), edit(195)

  推理延迟:    平均 9.4s  (基于 session 时间戳, 1304 次调用)
  Token 吞吐:  平均 32.4 tok/s  (基于 session 时间戳, 1295 次调用)

  Agent 活动分布:
    clawdoctor      推理  67次  平均     9.0s  吞吐 29.9 tok/s  会话 3
    main            推理 884次  平均     8.6s  吞吐 36.0 tok/s  会话 7
    waicode         推理 708次  平均     9.1s  吞吐 41.5 tok/s  会话 18
    waiqa           推理  25次  平均     8.5s  吞吐 49.6 tok/s  会话 1
    wairesearch     推理  23次  平均    15.6s  吞吐 47.6 tok/s  会话 1

  工具使用排行:
    exec     588次  平均耗时 1.5s
    read     311次  平均耗时 56ms
    edit     195次  平均耗时 38ms
    ...
```

## 功能特性

### Web 面板 (`openclaw-dashboard.py`)

**核心**
- **零依赖** — 仅使用 Python 标准库，无需 pip install
- **标准/高级模式** — 标准模式只读 session 文件（无需 debug 配置）；`--advanced` 解锁日志级 Run 事件和消息流水线
- **前后端分离** — 后端 API + 静态文件 (`static/` 目录)
- **暗色主题** — GitHub Dark 风格 UI
- **跨平台** — Linux、macOS、Windows；Python 3.6+

**KPI 卡片（3 行）**
- **第一行：核心指标** — 模型调用数、平均推理延迟、Token 吞吐量、缓存命中率
- **第二行：Token 详情** — 输入/输出 Token、缓存读写、推理占比
- **第三行：工具与思考** — 工具调用数、工具错误、工具平均耗时、Thinking 调用占比、平均思考深度（字符数）

**Session 数据分析 (v3.0)**
- **推理耗时** — 从 session.jsonl 时间戳精确计算每次 LLM 调用的 `inference_ms` 和 `tokens_per_sec`（assistant 时间戳 − 前一条 user/tool 时间戳）
- **工具执行统计** — 按工具分组：调用次数、总耗时/平均耗时、成功率、错误数；数据来源 `toolResult.details`（exitCode、durationMs）
- **思考深度** — Thinking 调用次数、平均字符数、每次调用的 Thinking 占比
- **对话树** — 消息链路可视化（user → assistant → toolCall → toolResult）、yield/resume 标记、模型切换点
- **系统事件** — 多代理协作事件（`custom_message`）、模型快照
- **模型切换追踪** — 追踪 `model-snapshot` 条目，记录 provider/model 变更历史

**Gateway 与探测**
- **Gateway 重启历史** — 检测 SHUTDOWN/TRIGGER/STARTUP/CRASH 四种事件；KPI 卡片 + 可折叠详情表格
- **探测面板** — 6 项内置探测，一键执行，实时状态指示（蓝色脉冲 → 绿色成功 / 红色失败）
- **探测列表**：health、gateway\_status、config\_validate、doctor、update\_status、models\_status

**Run 分析（高级模式）**
- **Run 列表** — 分页表格：耗时、模型、通道、状态、推理延迟、tok/s
- **Run 详情** — 可展开甘特图、推理分段、工具参数、Token 汇总
- **消息流水线** — 可视化管道：Queued → Enqueue → Dequeue → Run → Processed
- **事件时间线** — 可过滤的分页事件日志，含分类标签

**性能优化**
- **TTL 缓存** — summary、runs、restarts、tool\_stats 10 秒 TTL 缓存；API 响应从 3.5s 降至 30ms
- **批量接口** — `/api/dashboard` 一次返回全部数据
- **Gzip 压缩** — 自动压缩 >1KB 响应（节省 72-76%）
- **ETag 缓存** — 静态文件 304 缓存验证
- **骨架屏** — 首屏加载占位卡片，提升感知性能

**其他**
- **Session 文件覆盖** — 扫描 `*.jsonl`、`*.jsonl.reset.*`、`*.jsonl.deleted.*`（例如 7 个 Agent 共 309 个 session 文件）
- **自动刷新** — 5s 到 5min 可选间隔
- **访问令牌** — 可选 `--token` 参数
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

| 探测项 | 说明 | 超时 |
|--------|------|------|
| `health` | Agent 列表、Session 数量、频道状态 | 30s |
| `gateway_status` | PID、端口、绑定模式、服务状态、RPC 检测 | 30s |
| `config_validate` | 配置文件语法和结构校验 | 15s |
| `doctor` | 全面审计：安全、技能、插件、会话锁、内存 | 30s |
| `update_status` | 安装版本、更新通道、可用更新 | 20s |
| `models_status` | 默认模型、Fallback、认证状态、已配置模型 | 20s |

### Shell 脚本 (`openclaw-diag.sh` v3.2)

- **多 Agent 过滤** — `-a/--agent NAME` 按 Agent 过滤所有统计数据（如 main、waicode、wairesearch、waiqa）
- **Agent 活动分布** — 摘要新增每个 Agent 的推理次数、平均延迟、Token 吞吐量、会话数
- **Session 驱动推理耗时** — 从 session.jsonl 精确计算每次 LLM 调用的 `inference_ms` 和 `tokens_per_sec`（与 Python 版对齐）
- **工具执行详情** — 提取 `toolResult.details`（exitCode、durationMs、stderr 摘要）
- **工具成功率** — 按工具分组的成功/失败统计
- **错误列表** — 最多 20 条，无字符截断，按时间倒序
- **终端诊断** — 彩色输出，Run 时间线
- **实时跟踪** — `-f` 模式实时流式输出
- **摘要模式** — `-s` 快速统计概览
- **Token 统计** — 每次推理的 Token 用量明细
- **耗时分布** — 可视化条形图：推理 vs 工具时间

## 前置条件

### 标准模式（默认）

无需任何配置修改。自动读取 session 文件（`~/.openclaw/agents/*/sessions/*.jsonl`），提供：
- 推理耗时（每次调用的 ms 和 tok/s）
- Token 用量（输入、输出、缓存）
- 工具执行统计
- 思考深度分析
- 模型调用详情

### 高级模式（`--advanced`）

额外支持日志级 Run 事件、消息流水线和事件时间线：

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
python3 openclaw-dashboard.py               # 标准模式（端口 9090）
python3 openclaw-dashboard.py --advanced     # 高级模式
python3 openclaw-dashboard.py --port 8080    # 自定义端口
# 打开 http://127.0.0.1:9090
```

### CLI 探测

```bash
python3 openclaw-dashboard.py --cli          # 全部探测，人类可读输出
python3 openclaw-dashboard.py --cli --json   # 全部探测，JSON 格式
python3 openclaw-dashboard.py --cli --probe doctor  # 单项探测
```

### Shell 脚本

```bash
./openclaw-diag.sh              # 今天的 Run
./openclaw-diag.sh 2026-03-11   # 指定日期
./openclaw-diag.sh -f           # 实时跟踪
./openclaw-diag.sh -l 5         # 最近 5 个 Run
./openclaw-diag.sh -s           # 仅摘要
./openclaw-diag.sh -a waicode 2026-03-19    # 按 Agent 过滤
./openclaw-diag.sh -s -a main               # 指定 Agent 的摘要
./openclaw-diag.sh -f -a waicode            # 实时跟踪指定 Agent
```

## 🚀 部署方式

### 快速启动（开发环境）

```bash
python3 openclaw-dashboard.py                    # 标准模式，端口 9090
python3 openclaw-dashboard.py --advanced --port 8765  # 高级模式，自定义端口
```

### systemd 常驻服务（推荐生产环境）

一键安装：

```bash
sudo ./deploy/install.sh
sudo ./deploy/install.sh --port 8765 --advanced
sudo ./deploy/install.sh --api-key my-secret
```

或手动安装：

```bash
# 1. 复制文件
sudo mkdir -p /opt/openclaw-diag
sudo cp openclaw-dashboard.py /opt/openclaw-diag/
sudo cp -r static/ /opt/openclaw-diag/

# 2. 配置
sudo mkdir -p /etc/openclaw-diag
sudo cp deploy/openclaw-diag.env /etc/openclaw-diag/
sudo vi /etc/openclaw-diag/openclaw-diag.env

# 3. 安装服务
sudo cp deploy/openclaw-diag.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-diag

# 4. 检查状态
sudo systemctl status openclaw-diag
journalctl -u openclaw-diag -f
```

卸载：`sudo ./deploy/install.sh --uninstall`

### Docker 部署（推荐隔离环境）

```bash
# 构建并启动
docker compose up -d

# 自定义配置
OC_DIAG_PORT=8765 OC_DIAG_API_KEY=mysecret docker compose up -d

# 高级模式
OC_DIAG_ADVANCED=1 docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

Docker 部署会将宿主机的日志和 session 目录以只读方式挂载，用于本地诊断。

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

### 标准模式端点

| 端点 | 说明 |
|------|------|
| `GET /` | 面板页面 |
| `GET /api/dashboard?date=` | **批量接口**：summary + model\_calls + restarts（高级模式额外含 events/runs/errors） |
| `GET /api/dates` | 可用日志日期 |
| `GET /api/summary?date=` | KPI 统计：模型调用、推理耗时、Token、缓存、工具、Thinking |
| `GET /api/model_calls?date=&page=&per_page=` | 分页模型调用记录（来源 session 文件） |
| `GET /api/tool_stats?date=` | 按工具分组的调用次数、耗时、成功率 |
| `GET /api/restarts?date=` | Gateway 重启历史 |
| `GET /api/system_info` | Python 版本、内存、配置路径、模型调用总数 |
| `GET /api/mode` | 当前模式（standard/advanced）、数据源可用性 |
| `GET /api/probes` | 可用探测项列表 |
| `POST /api/probe/<name>` | 执行单项探测 |
| `POST /api/probe/all` | 执行全部探测 |

### 高级模式端点（额外）

| 端点 | 说明 |
|------|------|
| `GET /api/events?date=` | 事件摘要 + 消息流水线统计 |
| `GET /api/events/timeline?date=&page=&per_page=&category=` | 分页事件时间线（支持分类过滤） |
| `GET /api/events/webhooks?date=` | Webhook 事件 |
| `GET /api/events/messages?date=` | 消息队列事件 |
| `GET /api/events/errors?date=&severity=&type=` | 错误列表（支持过滤） |
| `GET /api/runs?date=&page=&per_page=` | 分页 Run 列表 |
| `GET /api/run/<id>?date=` | Run 详情：model\_calls、甘特图、工具、推理分段 |

所有端点支持 `date` 参数（默认使用最新可用日期）。响应格式：`Content-Type: application/json; charset=utf-8`，支持 gzip。

## 架构

### 数据源

| 数据源 | 适用模式 | 提供数据 |
|--------|---------|---------|
| **Session 文件** `~/.openclaw/agents/*/sessions/*.jsonl` | 标准 + 高级 | 模型调用、Token、推理耗时、工具详情、Thinking、对话树 |
| **日志文件** `/tmp/openclaw/openclaw-YYYY-MM-DD.log` | 仅高级 | Run 事件、消息流水线、事件时间线 |
| **OpenClaw CLI** | 两种模式 | 实时探测（health、doctor、gateway status 等） |

Session 文件扫描覆盖：`*.jsonl`、`*.jsonl.reset.*`、`*.jsonl.deleted.*`，跨所有 Agent 目录。

### 推理耗时计算（Session 驱动）

```
user 消息 (时间戳 T1)
    ↓
assistant 响应 (时间戳 T2)
    ↓
inference_ms = T2 - T1
tokens_per_sec = output_tokens / (inference_ms / 1000)
```

- 从 session.jsonl 的连续消息对中提取时间戳
- 标准模式即可使用 — 无需 debug 日志
- 精确到单次 LLM 调用级别（非 Run 级别估算）

### Gateway 重启检测

从日志文件识别 4 种事件类型：
- **SHUTDOWN** — 收到 SIGTERM 的优雅关闭
- **TRIGGER** — 外部触发重启（如 `openclaw gateway restart`）
- **STARTUP** — Gateway 启动事件
- **CRASH** — 意外终止（无前置 SIGTERM）

### 性能优化

| 优化措施 | 效果 |
|---------|------|
| TTL 缓存（10s） | summary/runs/restarts/tool\_stats：3.5s → 30ms |
| 批量接口 | 3 次 HTTP 请求 → 1 次 |
| Gzip 压缩 | app.js 28KB→8KB，API 约 76% 压缩 |
| ETag + 304 | 静态文件缓存验证 |
| 关键 CSS 内联 | 加速首次渲染 |
| Session 预加载 | 后台线程启动时加载 session 数据 |

**基准测试（2 vCPU, 4GB RAM, 309 个 session, 963 次模型调用）：**

| 指标 | 结果 |
|------|------|
| 15 个端点全部 < 500ms（缓存热） | ✅ |
| `/api/dashboard`（缓存热） | ~30ms |
| 10 次并发 dashboard | 平均 350ms |
| 完整页面加载（并行） | ~1s |

## 兼容性

- **Python**: 3.6+
- **操作系统**: Linux、macOS、Windows
- **浏览器**: Chrome、Firefox、Safari、Edge

## 文件结构

```
openclaw-diag-dashbaard/
├── openclaw-dashboard.py   # Web 面板 + CLI（3900+ 行）
├── openclaw-collector.sh   # 远程采集脚本
├── start-dashboard.sh      # 交互式启动脚本
├── Dockerfile              # Docker 镜像定义
├── docker-compose.yml      # Docker Compose 配置
├── static/
│   ├── index.html          # 面板布局
│   ├── app.js              # 前端逻辑（1400+ 行）
│   └── style.css           # 暗色主题样式
├── deploy/
│   ├── install.sh          # 一键安装脚本
│   ├── openclaw-diag.service  # systemd 服务文件
│   └── openclaw-diag.env   # 环境变量配置模板
├── docs/
│   └── screenshots/        # 面板截图
├── README.md               # English documentation
└── README_zh.md            # 中文文档
```

## 更新日志

### v3.2 (2026-03-20)

**新功能**
- **多 Agent 过滤**（`-a/--agent NAME`）— 按 Agent 名称过滤诊断数据（main、waicode、wairesearch、waiqa 等）
  - 批量分析：通过 session_uuid→agent 映射过滤 runs、sessions、errors、messages
  - 实时跟踪模式（`-f`）：按 Agent 过滤事件流
  - 摘要模式（`-s`）：展示单个 Agent 的统计数据
- **Agent 活动分布** — 摘要新增每个 Agent 的推理次数、平均延迟、Token 吞吐量、会话数

**Bug 修复**
- **[P1]** 修复日期过滤使用 ±1 天宽松匹配导致统计数据膨胀约 35% 的严重问题，改为严格 UTC 日期匹配
- **[P2]** 修复 Agent 过滤未覆盖错误/消息统计的问题
- **[P3]** 修复虚拟 Run 模式下全局统计未按日期过滤的问题

### v3.0

- Session 优先架构 — 所有分析数据由 `session.jsonl` 驱动
- 从 `toolResult.details` 提取工具执行统计
- Thinking 深度分析
- 标准模式无需配置 debug 日志

## License

[MIT](LICENSE) © 2026 wujiaming88
