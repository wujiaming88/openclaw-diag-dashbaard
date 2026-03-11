# OpenClaw Diagnostic Dashboard

A web-based performance diagnostic tool for the [OpenClaw](https://github.com/nicepkg/openclaw) AI agent platform. Single-file Python server with zero external dependencies — just run and explore.

![Dashboard Screenshot](docs/screenshot-placeholder.png)
<!-- Replace with actual screenshot once available -->

## Features

- **Zero Dependencies** — Uses only the Python standard library (`http.server`, `json`, `glob`, etc.)
- **Single File Deployment** — One `.py` file contains the entire backend + frontend
- **Real-time Run Analysis** — View every agent run with timing breakdowns
- **Gantt Chart Timeline** — Pure-CSS horizontal timeline showing inference vs. tool execution segments
- **Inference Segmentation** — Breaks down inference into per-step durations with token throughput (tok/s)
- **Tool Call Details** — Shows tool names, argument summaries, and execution times
- **Token Usage Tracking** — Correlates token consumption from session files with log events
- **Dark Theme UI** — Clean, modern dark interface with card layout and responsive design
- **Auto Refresh** — Optional 30-second auto-refresh for monitoring live runs
- **Access Token** — Optional `--token` flag for basic access control
- **Cross-Platform** — Runs on Linux, macOS, and Windows
- **Python 3.6+** — No walrus operators, no match/case, no bleeding-edge syntax
- **Graceful Error Handling** — Survives missing directories, corrupt JSON, huge log files, and port conflicts

## Prerequisites: Enable OpenClaw Diagnostics

The dashboard reads diagnostic log events that are **not emitted by default**. You must enable diagnostics in your OpenClaw configuration before the dashboard can display any data.

Edit `~/.openclaw/openclaw.json` and add:

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

Then restart the Gateway:

```bash
openclaw gateway restart
```

**Why is this needed?**
- `diagnostics.enabled: true` — Enables diagnostic events (`model.usage`, `message.processed`, `session.state`, etc.)
- `logging.level: "debug"` — Ensures run lifecycle events (`embedded run start/end`, `tool start/end`) are written to the log file. At the default `info` level, these events are not recorded.

**Optional: Targeted channel logs**

To also capture channel-specific HTTP details (e.g., Telegram or Feishu API calls), add diagnostic flags:

```json
{
  "diagnostics": {
    "enabled": true,
    "flags": ["telegram.http", "feishu.http"]
  },
  "logging": {
    "level": "debug"
  }
}
```

Available flags: `telegram.http`, `telegram.*`, `feishu.http`, `feishu.*`, `gateway.*`, `*` (all).

> **Note:** After enabling diagnostics and restarting the Gateway, send a few messages to generate log data. The dashboard will show "No data" until diagnostic events are recorded.

## Quick Start

```bash
# No install needed — just run it
python3 openclaw-dashboard.py

# Or specify options
python3 openclaw-dashboard.py --port 8080 --no-browser
```

Open `http://127.0.0.1:9090` in your browser.

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | `9090` | HTTP server listen port |
| `--host HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for remote access, `::` for IPv6) |
| `--log-dir DIR` | auto-detect | OpenClaw log directory path |
| `--sessions-dir DIR` | auto-detect | Session JSONL files directory |
| `--token TOKEN` | *(none)* | Access token (append `?token=xxx` to URL) |
| `--no-browser` | `false` | Don't auto-open browser on start |

### Auto-Detection Order

**Log directory:**
1. `--log-dir` argument
2. `OPENCLAW_LOG_DIR` environment variable
3. `/tmp/openclaw/` (Linux default)
4. `~/Library/Logs/openclaw/` (macOS)
5. `%TEMP%/openclaw/` (Windows)

**Session directory:**
1. `--sessions-dir` argument
2. `OPENCLAW_SESSIONS_DIR` environment variable
3. `~/.openclaw/agents/*/sessions/` (standard path)
4. `$OPENCLAW_STATE_DIR/agents/*/sessions/` (custom state dir)

## Architecture

### Data Sources

The dashboard reads two types of JSONL files:

1. **Log files** (`/tmp/openclaw/openclaw-YYYY-MM-DD.log`) — Contains timestamped events for agent runs: start, prompt build, API calls, tool execution, and completion.

2. **Session files** (`~/.openclaw/agents/*/sessions/*.jsonl`) — Contains message history with tool call arguments and token usage data.

### Run Event Chain

```
embedded run start → agent start → [tool start → tool end]* → agent end → run done
```

Inference time is computed as the gaps between tool executions:

```
Inference #1 = agent_start → first tool_start
Inference #2 = tool_end[0] → tool_start[1]
...
Inference #N = last tool_end → agent_end
```

## API Reference

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML page (embedded frontend) |
| `GET /api/dates` | List available log dates `["2026-03-11", ...]` |
| `GET /api/summary?date=YYYY-MM-DD` | Summary stats: run count, avg duration, token totals, error count |
| `GET /api/runs?date=YYYY-MM-DD` | Run list with timing, tool count, status |
| `GET /api/run/<run_id>?date=YYYY-MM-DD` | Full run detail: gantt data, inference segments, tool args, token usage |

All API endpoints return `Content-Type: application/json; charset=utf-8`.

## Compatibility

- **Python**: 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12+
- **OS**: Linux, macOS, Windows
- **Browser**: Any modern browser (Chrome, Firefox, Safari, Edge)
- **Network**: IPv4 and IPv6, with automatic fallback

## CLI Diagnostic Tool

The repository also includes `openclaw-diag.sh`, a command-line diagnostic script:

```bash
# Analyze today's runs
./openclaw-diag.sh

# Analyze a specific date
./openclaw-diag.sh 2026-03-11

# Analyze a specific run
./openclaw-diag.sh 2026-03-11 <run_id>
```

This produces terminal-friendly output with colored tables showing run timelines, inference breakdowns, and tool execution details.

## License

[MIT](LICENSE) © 2026 wujiaming88
