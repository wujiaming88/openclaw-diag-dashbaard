# OpenClaw 诊断面板

基于 Web 的 [OpenClaw](https://github.com/nicepkg/openclaw) AI 代理平台性能诊断工具。单文件 Python 服务，零外部依赖 — 开箱即用。

![Dashboard 截图](docs/screenshot-placeholder.png)
<!-- 有截图后替换 -->

## 功能特性

- **零依赖** — 仅使用 Python 标准库（`http.server`、`json`、`glob` 等）
- **单文件部署** — 一个 `.py` 文件包含完整的后端 + 前端
- **实时 Run 分析** — 查看每次代理运行的时间分解
- **甘特图时间线** — 纯 CSS 水平时间线，展示推理与工具执行段
- **推理分段** — 将推理拆解为逐步耗时，并显示 token 吞吐量（tok/s）
- **工具调用详情** — 显示工具名称、参数摘要和执行时间
- **Token 用量追踪** — 关联会话文件中的 token 消耗与日志事件
- **深色主题 UI** — 简洁现代的深色界面，卡片布局，响应式设计
- **自动刷新** — 可选 30 秒自动刷新，监控实时运行
- **访问令牌** — 可选 `--token` 参数实现基本访问控制
- **跨平台** — 支持 Linux、macOS 和 Windows
- **Python 3.6+** — 无 walrus 操作符，无 match/case，兼容旧版 Python
- **优雅容错** — 目录不存在、JSON 损坏、日志文件过大、端口冲突等均可正常处理

## 快速开始

```bash
# 无需安装，直接运行
python3 openclaw-dashboard.py

# 或指定选项
python3 openclaw-dashboard.py --port 8080 --no-browser
```

在浏览器中打开 `http://127.0.0.1:9090`。

## 命令行选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--port PORT` | `9090` | HTTP 监听端口 |
| `--host HOST` | `127.0.0.1` | 绑定地址（`0.0.0.0` 允许远程访问，`::` 支持 IPv6） |
| `--log-dir DIR` | 自动检测 | OpenClaw 日志目录路径 |
| `--sessions-dir DIR` | 自动检测 | 会话 JSONL 文件目录 |
| `--token TOKEN` | *(无)* | 访问令牌（URL 中追加 `?token=xxx`） |
| `--no-browser` | `false` | 启动时不自动打开浏览器 |

### 自动检测顺序

**日志目录：**
1. `--log-dir` 参数
2. `OPENCLAW_LOG_DIR` 环境变量
3. `/tmp/openclaw/`（Linux 默认）
4. `~/Library/Logs/openclaw/`（macOS）
5. `%TEMP%/openclaw/`（Windows）

**会话目录：**
1. `--sessions-dir` 参数
2. `OPENCLAW_SESSIONS_DIR` 环境变量
3. `~/.openclaw/agents/*/sessions/`（标准路径）
4. `$OPENCLAW_STATE_DIR/agents/*/sessions/`（自定义 state 目录）

## 架构

### 数据来源

Dashboard 读取两种 JSONL 文件：

1. **日志文件**（`/tmp/openclaw/openclaw-YYYY-MM-DD.log`）— 包含代理运行的时间戳事件：启动、Prompt 构建、API 调用、工具执行和完成。

2. **会话文件**（`~/.openclaw/agents/*/sessions/*.jsonl`）— 包含消息历史、工具调用参数和 token 用量数据。

### Run 事件链

```
embedded run start → agent start → [tool start → tool end]* → agent end → run done
```

推理时间 = 工具执行之间的间隔：

```
推理 #1 = agent_start → 第一个 tool_start
推理 #2 = tool_end[0] → tool_start[1]
...
推理 #N = 最后一个 tool_end → agent_end
```

## API 参考

| 端点 | 说明 |
|------|------|
| `GET /` | Dashboard HTML 页面（内嵌前端） |
| `GET /api/dates` | 可用日志日期列表 `["2026-03-11", ...]` |
| `GET /api/summary?date=YYYY-MM-DD` | 摘要统计：Run 数量、平均耗时、Token 总量、错误数 |
| `GET /api/runs?date=YYYY-MM-DD` | Run 列表：时间、工具数、状态 |
| `GET /api/run/<run_id>?date=YYYY-MM-DD` | Run 完整详情：甘特图、推理分段、工具参数、Token 用量 |

所有 API 返回 `Content-Type: application/json; charset=utf-8`。

## 兼容性

- **Python**：3.6、3.7、3.8、3.9、3.10、3.11、3.12+
- **操作系统**：Linux、macOS、Windows
- **浏览器**：所有现代浏览器（Chrome、Firefox、Safari、Edge）
- **网络**：IPv4 和 IPv6，自动回退

## CLI 诊断工具

仓库还包含 `openclaw-diag.sh` 命令行诊断脚本：

```bash
# 分析今日运行
./openclaw-diag.sh

# 分析指定日期
./openclaw-diag.sh 2026-03-11

# 分析指定 Run
./openclaw-diag.sh 2026-03-11 <run_id>
```

在终端中输出带颜色的表格，显示 Run 时间线、推理分解和工具执行详情。

## 许可证

[MIT](LICENSE) © 2026 wujiaming88
