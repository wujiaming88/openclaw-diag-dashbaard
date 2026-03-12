# OpenClaw Diagnostic Dashboard

A web-based performance diagnostic tool for the [OpenClaw](https://github.com/nicepkg/openclaw) AI agent platform. Zero external dependencies — just run and explore.

## Features

### Web Dashboard (`openclaw-dashboard.py`)

- **Zero Dependencies** — Uses only the Python standard library
- **Separated Frontend** — Backend API + static files (`static/` directory)
- **Summary Cards** — Two rows of KPI cards: run count, avg duration, inference ratio, token throughput, error count, token usage, cache hit rate
- **Message Pipeline** — Visual pipeline: Message Queued → Queue Enqueue → Queue Dequeue → Run Execution → Message Processed
- **Model Call Details** — Per-run LLM call breakdown showing input/output tokens, cache usage, cost, thinking preview, tool calls (embedded in run detail, matched via session files)
- **Run List** — Paginated run table with timing, model, channel, status
- **Run Detail** — Expandable Gantt chart, inference segments, tool call arguments, token summary
- **Gzip Compression** — Automatic gzip for responses >1KB (72-76% size reduction)
- **ETag Caching** — Static files support ETag + 304 Not Modified
- **Batch API** — Single `/api/dashboard` endpoint returns summary + events + runs in one request
- **Skeleton Loading** — Loading placeholders for instant perceived performance
- **Dark Theme** — GitHub-dark inspired UI
- **Auto Refresh** — Configurable 5s to 5min intervals
- **Access Token** — Optional `--token` flag for basic access control
- **Cross-Platform** — Linux, macOS, Windows
- **Python 3.6+** — No modern-only syntax
- **Graceful Errors** — Survives missing dirs, corrupt JSON, huge logs, port conflicts

### CLI Tool (`openclaw-diag.sh`)

- **Terminal Diagnostic** — Colored terminal output with run timelines
- **Live Follow Mode** — Real-time log streaming (`-f`)
- **Summary Mode** — Quick stats overview (`-s`)
- **Tool Parameters** — Extracts tool call arguments from session files
- **Token Tracking** — Per-inference token usage breakdown
- **Duration Breakdown** — Visual bar charts for inference vs. tool time

## Prerequisites: Enable OpenClaw Diagnostics

Edit `~/.openclaw/openclaw.json`:

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

Then restart: `openclaw gateway restart`

- `diagnostics.enabled: true` — Enables diagnostic events
- `logging.level: "debug"` — Records run lifecycle events

## Quick Start

### Web Dashboard

```bash
python3 openclaw-dashboard.py
# Open http://127.0.0.1:9090
```

### CLI Tool

```bash
./openclaw-diag.sh              # Today's runs
./openclaw-diag.sh 2026-03-11   # Specific date
./openclaw-diag.sh -f           # Live follow mode
./openclaw-diag.sh -l 5         # Last 5 runs
./openclaw-diag.sh -s           # Summary only
```

## Command Line Options (Web Dashboard)

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | `9090` | Listen port |
| `--host HOST` | `0.0.0.0` | Bind address |
| `--log-dir DIR` | auto-detect | Log directory |
| `--sessions-dir DIR` | auto-detect | Session files directory |
| `--token TOKEN` | *(none)* | Access token |
| `--no-browser` | `false` | Don't auto-open browser |

### Auto-Detection Order

**Log directory:** `--log-dir` → `$OPENCLAW_LOG_DIR` → `/tmp/openclaw/` → `~/Library/Logs/openclaw/` → `%TEMP%/openclaw/`

**Session directory:** `--sessions-dir` → `$OPENCLAW_SESSIONS_DIR` → `~/.openclaw/agents/*/sessions/` → `$OPENCLAW_STATE_DIR/agents/*/sessions/`

## API Reference

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard page |
| `GET /api/dashboard?date=` | **Batch**: summary + events + runs + errors |
| `GET /api/dates` | Available log dates |
| `GET /api/system_info` | System info (Python, memory, config, model_calls_total) |
| `GET /api/summary?date=` | Run summary stats |
| `GET /api/events?date=` | Event summary + message pipeline stats |
| `GET /api/runs?date=&page=&per_page=` | Paginated run list |
| `GET /api/run/<id>?date=` | Run detail with model_calls, gantt, tools |
| `GET /api/model_calls?date=&page=&per_page=` | All model calls (from session files) |
| `GET /api/events/errors?date=&severity=&type=` | Error list with filtering |

All responses: `Content-Type: application/json; charset=utf-8`, gzip supported.

## Architecture

### Data Sources

1. **Log files** (`/tmp/openclaw/openclaw-YYYY-MM-DD.log`) — Run events, timing, tool execution
2. **Session files** (`~/.openclaw/agents/*/sessions/*.jsonl`) — Model call details, token usage, tool arguments

### Model Call Matching

Model calls from session files are matched to runs by:
- Time range: model call timestamp falls within run start/end
- Session ID: dual matching (file-name ID + internal ID) to handle ID mismatches

> **Note:** Subagent sessions (waicode, etc.) may be ephemeral. Model calls are only available for runs whose session files still exist.

### Performance Optimizations

- Batch API reduces 3 HTTP requests to 1
- Gzip: app.js 28KB→8KB, style.css 13KB→3KB, API ~76% reduction
- ETag + 304 for static files
- Long cache (24h) for versioned static assets
- Critical CSS inlined, script deferred, resources preloaded
- Backend caching with mtime-based invalidation

## Compatibility

- **Python**: 3.6+
- **OS**: Linux, macOS, Windows
- **Browser**: Chrome, Firefox, Safari, Edge

## License

[MIT](LICENSE) © 2026 wujiaming88
