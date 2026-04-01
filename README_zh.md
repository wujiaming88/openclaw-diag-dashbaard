# OpenClaw 诊断面板

面向 [OpenClaw](https://github.com/nicepkg/openclaw) AI Agent 平台的分布式诊断工具。包含 **Dashboard Server**（可视化面板）和 **Collector**（远程采集端）。零外部依赖 — 纯 Python 标准库。

> **v4.2** — 多节点分布式架构，Collector/Server 模型，gzip 传输，HMAC 节点认证，逐节点锁，敏感数据采集（环境变量、配置、命令历史、系统日志），全站登录门禁，TTL 缓存 API。

## 架构

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  节点 A (新加坡)  │     │  节点 B (香港)    │     │  节点 C (美国)    │
│  Collector      │     │  Collector      │     │  Collector      │
│  openclaw-      │     │  openclaw-      │     │  openclaw-      │
│  collector.sh   │     │  collector.sh   │     │  collector.sh   │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │ HTTPS + gzip          │                       │
         │ + HMAC 认证            │                       │
         └───────────────┬───────┴───────────────────────┘
                         ▼
              ┌─────────────────────┐
              │  Dashboard Server   │
              │  openclaw-          │
              │  dashboard.py       │
              │  (Web UI + API)     │
              └─────────────────────┘
```

## 📸 截图

### Dashboard 概览 — KPI 与系统探测

![Dashboard 概览](docs/screenshots/dashboard-overview.png)

*5 行 KPI 卡片 + 6 个一键系统探测面板。*

### 标准模式 — 完整页面

![标准模式](docs/screenshots/dashboard-standard.png)

*KPI 概览 → 探测 → 重启历史 → 模型调用 → 会话浏览 → 错误追踪。零配置即用。*

### 高级模式 — 完整页面

![高级模式](docs/screenshots/dashboard-advanced.png)

*在标准模式基础上增加 Run 级详情、事件时间线、debug 日志分析。*

### CLI 诊断

```
🦞 OpenClaw 诊断工具 v3.2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏥 健康检查 ... ✅ (9.9s)    🌐 Gateway 状态 ... ✅ (12.0s)
✅ 配置校验 ... ✅ (4.3s)    🔬 全面诊断 ... ✅ (17.9s)
📦 版本状态 ... ✅ (10.5s)   🤖 模型状态 ... ✅ (10.6s)
```

---

## 组件

### 1. Dashboard Server（`openclaw-dashboard.py`）

中心 Web 服务：
- 采集**本地**诊断数据（session 文件、日志、CLI 探测）
- 接收**远程** Collector 通过 HTTP API 上报的数据
- 提供 Web 可视化面板 + REST API
- 支持 CLI 探测模式（无需启动 Web 服务）

### 2. Collector（`openclaw-collector.sh`）

运行在各 OpenClaw 节点的轻量采集脚本：
- 采集 12 类数据源（会话指标、工具统计、重启事件、环境变量、配置、命令历史、系统日志等）
- gzip 压缩 + HMAC-SHA256 认证后上传到 Dashboard Server
- 支持 daemon / once / loop 运行模式
- 启动时自动开启 `diagnostics.enabled` + `logging.level=debug`，退出时恢复

### 3. Shell 诊断（`openclaw-diag.sh`）

独立终端诊断工具：
- 多 Agent 过滤（`-a/--agent NAME`）
- 实时跟踪（`-f`）、摘要模式（`-s`）
- 基于 session 的推理耗时和 Token 吞吐量
- 零服务依赖 — 离线可用

---

## 功能特性

### 多节点管理
- **节点注册** — 首次上报自动注册；在线/离线状态追踪（30 分钟超时）
- **节点选择器** — Dashboard 下拉切换本地/远程节点
- **节点隔离** — 每个 node_id 独立存储，线程安全锁
- **节点清理** — `DELETE /api/node/<id>` 移除过期节点

### 安全与认证
- **Dashboard 登录** — 全站登录门禁，API Key 认证，HMAC 签名 Cookie（HttpOnly、SameSite=Strict、24h）
- **Bearer Token** — API 访问 `Authorization: Bearer <api_key>`
- **节点认证** — Collector 使用 HMAC-SHA256(`api_key`, `openclaw-node:<node_id>`) 身份验证
- **敏感数据脱敏** — 自动匹配模式（sk-*、ghp_*、AKIA*）和关键字（key/token/secret/password）脱敏显示

### 数据分析
- **KPI 卡片（3 行）** — 模型调用、推理延迟、Token 吞吐、缓存命中率、工具统计、思考深度
- **推理耗时** — 从 session.jsonl 时间戳逐调用计算（标准模式，无需 debug 日志）
- **工具执行统计** — 逐工具调用次数、耗时、成功率（`toolResult.details`）
- **Gateway 重启历史** — 检测 SHUTDOWN/TRIGGER/STARTUP/CRASH 事件
- **会话浏览器** — 浏览所有会话的模型、Token、耗时详情
- **6 项系统探测** — health、gateway_status、config_validate、doctor、update_status、models_status

### 远程节点诊断（v4.x）
- **环境变量** — 可排序表格、关键字筛选、敏感值高亮
- **OpenClaw 配置** — JSON 树形查看器，语法着色
- **命令历史** — 倒序命令日志（500 行），支持搜索
- **系统日志** — 最近 24h journalctl（2000 行），可筛选

### 高级模式（需 debug 日志）
- **Run 分析** — 分页 Run 列表、甘特图、推理分段、工具参数
- **事件时间线** — 可筛选分页事件日志
- **消息管道** — 队列 → 入队 → 出队 → Run → 处理 可视化

### 性能优化
| 优化项 | 效果 |
|--------|------|
| TTL 缓存 (10s) | API: 3.5s → 30ms |
| gzip 传输 | Collector 负载压缩 ~36% |
| 逐节点锁 | 跨节点并行读写 |
| 批量 API | `/api/dashboard` 一次返回全部数据 |
| ETag + 304 | 静态文件缓存验证 |

---

## 快速开始

### Dashboard Server

```bash
# 标准模式（仅本地诊断）
python3 openclaw-dashboard.py

# 带认证（远程 Collector 必须）
python3 openclaw-dashboard.py --api-key your-secret-key

# 高级模式
python3 openclaw-dashboard.py --advanced --api-key your-secret-key

# 访问 http://localhost:9090
```

### Collector（远程节点）

```bash
# 交互式配置（首次使用）
./openclaw-collector.sh

# 守护进程模式
./openclaw-collector.sh daemon

# 单次采集
./openclaw-collector.sh once

# 查看状态 / 停止
./openclaw-collector.sh status
./openclaw-collector.sh stop
```

### CLI 探测

```bash
python3 openclaw-dashboard.py --cli                        # 全部 6 项探测
python3 openclaw-dashboard.py --cli --probe health         # 单项探测
python3 openclaw-dashboard.py --cli --json                 # JSON 输出
```

### Shell 诊断

```bash
./openclaw-diag.sh                      # 今日诊断
./openclaw-diag.sh -f                   # 实时跟踪
./openclaw-diag.sh -s                   # 仅摘要
./openclaw-diag.sh -a waicode           # 按 Agent 过滤
./openclaw-diag.sh 2026-03-19           # 指定日期
```

---

## 🚀 部署

### 方式一：systemd 常驻（推荐生产环境）

一键安装：

```bash
sudo ./deploy/install.sh --api-key your-secret --port 9090
```

管理：

```bash
sudo systemctl status openclaw-diag
sudo systemctl restart openclaw-diag
sudo journalctl -u openclaw-diag -f
```

卸载：

```bash
sudo ./deploy/install.sh --uninstall
```

### 方式二：Docker

```bash
# 构建并运行
docker compose up -d

# 自定义配置
OC_DIAG_PORT=9090 OC_DIAG_API_KEY=mysecret docker compose up -d

# 高级模式
OC_DIAG_ADVANCED=1 docker compose up -d
```

Docker 将宿主机日志和 session 目录以只读方式挂载用于本地诊断。

### 方式三：直接运行（开发调试）

```bash
python3 openclaw-dashboard.py --port 9090 --api-key your-key
```

---

## API 参考

### 认证方式

| 方式 | 场景 |
|------|------|
| Session Cookie | 浏览器访问（通过 `/login` 页面） |
| `Authorization: Bearer <api_key>` | API 访问和 Collector 上报 |
| HMAC-SHA256 `node_token` | Collector 节点身份验证 |

### Dashboard 端点

| 端点 | 说明 |
|------|------|
| `GET /api/dashboard?date=` | 批量：摘要 + 模型调用 + 重启 |
| `GET /api/summary?date=` | KPI 统计 |
| `GET /api/model_calls?date=&page=&per_page=` | 分页模型调用记录 |
| `GET /api/tool_stats?date=` | 逐工具统计 |
| `GET /api/sessions?date=` | 会话浏览器 |
| `GET /api/restarts?date=` | Gateway 重启历史 |
| `GET /api/dates` | 可用日期列表 |
| `GET /api/mode` | 当前模式信息 |
| `GET /api/system_info` | 系统元数据 |
| `POST /api/probe/<name>` | 执行探测 |
| `POST /api/probe/all` | 执行全部探测 |

### 多节点端点

| 端点 | 说明 |
|------|------|
| `POST /api/report` | Collector 数据上报（gzip + HMAC） |
| `GET /api/nodes` | 列出所有已注册节点 |
| `GET /api/node/<id>/dashboard` | 远程节点面板数据 |
| `GET /api/node/<id>/summary` | 远程节点 KPI 摘要 |
| `GET /api/node/<id>/model_calls` | 远程节点模型调用 |
| `GET /api/node/<id>/sessions` | 远程节点会话 |
| `GET /api/node/<id>/tool_stats` | 远程节点工具统计 |
| `GET /api/node/<id>/env_vars` | 远程节点环境变量 |
| `GET /api/node/<id>/openclaw_config` | 远程节点 OpenClaw 配置 |
| `GET /api/node/<id>/bash_history` | 远程节点命令历史 |
| `GET /api/node/<id>/journalctl` | 远程节点系统日志 |
| `DELETE /api/node/<id>` | 删除节点数据 |

### 高级模式端点（额外）

| 端点 | 说明 |
|------|------|
| `GET /api/events?date=` | 事件摘要 + 消息管道 |
| `GET /api/events/timeline?date=` | 分页事件时间线 |
| `GET /api/events/errors?date=` | 错误列表（可筛选） |
| `GET /api/runs?date=&page=&per_page=` | Run 列表 |
| `GET /api/run/<id>?date=` | Run 详情（甘特图数据） |

所有端点支持 `date` 参数（默认最近可用日期）。JSON 响应，支持 gzip。

---

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port PORT` | `9090` | 监听端口 |
| `--host HOST` | `0.0.0.0` | 绑定地址 |
| `--api-key KEY` | *(无)* | 认证密钥（启用登录门禁 + Collector 认证） |
| `--advanced` | `false` | 启用高级模式（需 debug 日志） |
| `--no-browser` | `false` | 不自动打开浏览器 |
| `--no-local` | `false` | 纯远程模式（不加载本地数据） |
| `--log-dir DIR` | 自动检测 | 日志目录 |
| `--sessions-dir DIR` | 自动检测 | Session 文件目录 |
| `--token TOKEN` | *(无)* | 旧版访问令牌 |
| `--cli` | `false` | CLI 模式（不启动 Web 服务） |
| `--probe NAME` | `all` | CLI：指定探测项 |
| `--json` | `false` | CLI：JSON 输出格式 |

---

## 前置条件

### 标准模式（默认）
无需配置。读取 `~/.openclaw/agents/*/sessions/*.jsonl`。

### 高级模式（`--advanced`）
需在 `~/.openclaw/openclaw.json` 中配置：
```json
{
  "diagnostics": { "enabled": true },
  "logging": { "level": "debug" }
}
```

---

## 文件结构

```
openclaw-diag-dashbaard/
├── openclaw-dashboard.py      # Dashboard Server（4000+ 行）
├── openclaw-collector.sh      # 远程 Collector 采集脚本
├── openclaw-diag.sh           # 独立 Shell 诊断工具
├── start-dashboard.sh         # 交互式启动辅助
├── Dockerfile                 # Docker 镜像
├── docker-compose.yml         # Docker Compose 配置
├── static/
│   ├── index.html             # 面板布局
│   ├── app.js                 # 前端逻辑（1500+ 行）
│   └── style.css              # 暗色主题样式
├── deploy/
│   ├── install.sh             # 一键 systemd 安装
│   ├── openclaw-diag.service  # systemd 服务文件
│   └── openclaw-diag.env      # 环境变量配置模板
├── docs/
│   └── screenshots/           # 面板截图
├── README.md
└── README_zh.md
```

## 兼容性

- **Python**: 3.7+
- **操作系统**: Linux、macOS、Windows
- **浏览器**: Chrome、Firefox、Safari、Edge

## 更新日志

### v4.2 (2026-04-01)
- **多节点架构** — Collector/Server 分布式模型，HTTP 上报端点
- **Collector** — 12 类数据源，gzip 压缩，HMAC-SHA256 节点认证，daemon/once/loop 模式
- **Dashboard 登录** — 全站 API Key 认证，HMAC 签名 Session Cookie
- **敏感数据** — 环境变量、OpenClaw 配置、命令历史、系统日志的采集与展示
- **逐节点锁** — 线程安全的跨节点并发访问
- **节点管理** — 自动注册、在线/离线状态、DELETE 清理
- **Bearer 认证** — GET 端点支持 Bearer Token 和 Session Cookie 双认证
- **部署方案** — systemd 服务、Docker、一键安装脚本

### v3.2 (2026-03-20)
- **多 Agent 过滤**（`-a/--agent`）— 批量、摘要、实时跟踪模式的逐 Agent 诊断
- **Agent 活动分布** — 逐 Agent 推理次数、延迟、吞吐量、会话数
- **Bug 修复** — 日期过滤精度、Agent 范围统计、虚拟 Run 日期过滤

### v3.0 (2026-03-19)
- Session 优先架构 — 全部分析基于 `session.jsonl`
- 工具执行统计（`toolResult.details`）
- 思考深度分析
- 标准模式无需 debug 配置

## 许可证

[MIT](LICENSE) © 2026 wujiaming88
