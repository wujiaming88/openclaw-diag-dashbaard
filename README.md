# OpenClaw Diagnostic Dashboard

A web-based performance diagnostic tool for the [OpenClaw](https://github.com/nicepkg/openclaw) AI agent platform. Zero external dependencies — just run and explore.

## Features

### Web Dashboard (`openclaw-dashboard.py`)

- **Zero Dependencies** — Uses only the Python standard library
- **Separated Frontend** — Backend API + static files (`static/` directory)
- **Standard / Advanced Mode** — Standard mode reads session files only (no debug config needed); `--advanced` unlocks full diagnostics
- **Summary Cards** — KPI cards: run count, avg duration, inference latency, token throughput, error count, token usage, cache hit rate
- **Message Pipeline** — Visual pipeline: Message Queued → Queue Enqueue → Queue Dequeue → Run Execution → Message Processed
- **Model Call Details** — Per-run LLM call breakdown showing input/output tokens, cache usage, cost, thinking preview, tool calls
- **Inference Timing** — Precise per-call inference_ms and tokens_per_sec calculated from session.jsonl timestamps
- **Gateway Restart History** — Tracks SHUTDOWN/TRIGGER/STARTUP/CRASH events with KPI cards and collapsible detail table
- **Probe Panel** — 6 built-in probes (health, gateway status, config validate, doctor, update status, models status) with one-click execution and live status indicators
- **Run List** — Paginated run table with timing, model, channel, status, inference latency, tok/s
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

### CLI Mode

Run probes directly from the terminal — no web server needed:

```bash
python3 openclaw-dashboard.py --cli                        # Run all 6 probes
python3 openclaw-dashboard.py --cli --probe health         # Single probe
python3 openclaw-dashboard.py --cli --probe gateway_status # Gateway status
python3 openclaw-dashboard.py --cli --json                 # JSON output
```

**Available probes:**

| Probe | Description |
|-------|-------------|
| `health` | Agent list, session counts, channel status |
| `gateway_status` | PID, port, bind mode, service state, RPC check |
| `config_validate` | Configuration file syntax and structure |
| `doctor` | Full audit: security, skills, plugins, session locks, memory |
| `update_status` | Installed version, update channel, available updates |
| `models_status` | Default model, fallbacks, auth status, configured models |

### Shell Script (`openclaw-diag.sh`)

- **Terminal Diagnostic** — Colored terminal output with run timelines
- **Live Follow Mode** — Real-time log streaming (`-f`)
- **Summary Mode** — Quick stats overview (`-s`)
- **Tool Parameters** — Extracts tool call arguments from session files
- **Token Tracking** — Per-inference token usage breakdown
- **Duration Breakdown** — Visual bar charts for inference vs. tool time

## Prerequisites: Enable OpenClaw Diagnostics

### Standard Mode (default)

No configuration changes needed. Reads session files (`~/.openclaw/agents/*/sessions/*.jsonl`) for inference timing, token usage, and model call details.

### Advanced Mode (`--advanced`)

For full diagnostics including log-based run events and message pipeline:

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

Then restart: `openclaw gateway restart`

## Quick Start

### Web Dashboard

```bash
python3 openclaw-dashboard.py               # Standard mode
python3 openclaw-dashboard.py --advanced     # Advanced mode (needs debug logs)
# Open http://127.0.0.1:9090
```

### CLI Probes

```bash
python3 openclaw-dashboard.py --cli          # All probes, human-readable
python3 openclaw-dashboard.py --cli --json   # All probes, JSON output
```

### Shell Script

```bash
./openclaw-diag.sh              # Today's runs
./openclaw-diag.sh 2026-03-11   # Specific date
./openclaw-diag.sh -f           # Live follow mode
./openclaw-diag.sh -l 5         # Last 5 runs
./openclaw-diag.sh -s           # Summary only
```

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | `9090` | Listen port |
| `--host HOST` | `0.0.0.0` | Bind address |
| `--log-dir DIR` | auto-detect | Log directory |
| `--sessions-dir DIR` | auto-detect | Session files directory |
| `--token TOKEN` | *(none)* | Access token |
| `--no-browser` | `false` | Don't auto-open browser |
| `--advanced` | `false` | Enable advanced diagnostics (requires debug logs) |
| `--cli` | `false` | CLI mode (no web server) |
| `--probe NAME` | `all` | CLI: run specific probe (`health`, `gateway_status`, `config_validate`, `doctor`, `update_status`, `models_status`, `all`) |
| `--json` | `false` | CLI: JSON output format |

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
| `POST /api/probe/<name>` | Execute a single probe |
| `POST /api/probe/all` | Execute all probes |

All responses: `Content-Type: application/json; charset=utf-8`, gzip supported.

## Architecture

### Data Sources

1. **Session files** (`~/.openclaw/agents/*/sessions/*.jsonl`) — Model call details, token usage, inference timing, tool arguments
2. **Log files** (`/tmp/openclaw/openclaw-YYYY-MM-DD.log`) — Run events, timing, tool execution *(advanced mode)*
3. **OpenClaw CLI** — Live probes via `openclaw health`, `openclaw gateway status`, `openclaw doctor`, etc.

### Inference Timing

Precise inference duration calculated from session.jsonl message timestamps:
- Extracts `assistant` message timestamps and preceding `user`/`tool` timestamps
- Computes `inference_ms` = time between last input and assistant response
- Calculates `tokens_per_sec` = output tokens / inference duration

### Gateway Restart Detection

Identifies 4 event types from log files:
- **SHUTDOWN** — Graceful SIGTERM-initiated shutdown
- **TRIGGER** — External restart trigger (e.g., `openclaw gateway restart`)
- **STARTUP** — Gateway start event
- **CRASH** — Unexpected termination (no preceding SIGTERM)

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
