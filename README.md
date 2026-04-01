# OpenClaw Diagnostic Dashboard

A distributed diagnostic platform for [OpenClaw](https://github.com/nicepkg/openclaw) AI agent deployments. Consists of a **Dashboard Server** for visualization and a **Collector** for remote data gathering. Zero external dependencies — pure Python standard library.

> **v4.2** — Multi-node distributed architecture with Collector/Server model, gzip transport, HMAC node authentication, per-node locking, sensitive data collection (env vars, config, bash history, journalctl), full-site login gate, and TTL-cached API.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Node A (SG)    │     │  Node B (HK)    │     │  Node C (US)    │
│  Collector      │     │  Collector      │     │  Collector      │
│  openclaw-      │     │  openclaw-      │     │  openclaw-      │
│  collector.sh   │     │  collector.sh   │     │  collector.sh   │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │ HTTPS + gzip          │                       │
         │ + HMAC auth           │                       │
         └───────────────┬───────┴───────────────────────┘
                         ▼
              ┌─────────────────────┐
              │  Dashboard Server   │
              │  openclaw-          │
              │  dashboard.py       │
              │  (Web UI + API)     │
              └─────────────────────┘
```

## 📸 Screenshots

### Dashboard Overview — KPI & System Probes

![Dashboard Overview](docs/screenshots/dashboard-overview.png)

*5 rows of KPI cards + system probe panel with 6 one-click diagnostics.*

### Standard Mode — Full Page

![Dashboard Standard](docs/screenshots/dashboard-standard.png)

*KPI overview → probes → restart history → model calls → sessions → errors. Zero config needed.*

### With Debug Logs — Full Page

![Dashboard Advanced](docs/screenshots/dashboard-advanced.png)

*When debug logs are available, auto-shows run-level details, event timeline, and log analysis.*

### CLI Diagnostics

```
🦞 OpenClaw 诊断工具 v3.2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏥 健康检查 ... ✅ (9.9s)    🌐 Gateway 状态 ... ✅ (12.0s)
✅ 配置校验 ... ✅ (4.3s)    🔬 全面诊断 ... ✅ (17.9s)
📦 版本状态 ... ✅ (10.5s)   🤖 模型状态 ... ✅ (10.6s)
```

---

## Components

### 1. Dashboard Server (`openclaw-dashboard.py`)

The central web server that:
- Collects **local** diagnostics (session files, logs, CLI probes)
- Receives **remote** data from Collectors via HTTP API
- Serves the web dashboard UI + REST API
- Provides CLI probe mode (no web server needed)

### 2. Collector (`openclaw-collector.sh`)

A lightweight bash script that runs on each OpenClaw node:
- Gathers 12 data sources (session metrics, tool stats, restarts, env vars, config, bash history, journalctl, etc.)
- Compresses with gzip and authenticates with HMAC-SHA256
- Uploads to Dashboard Server on schedule (daemon/once/loop modes)
- Auto-enables `diagnostics.enabled` + `logging.level=debug` on start, restores on exit

### 3. Shell Diagnostics (`openclaw-diag.sh`)

Standalone terminal diagnostic tool:
- Multi-agent filtering (`-a/--agent NAME`)
- Live follow mode (`-f`), summary mode (`-s`)
- Session-based inference timing and token throughput
- Zero server dependency — works offline

---

## Features

### Multi-Node Management
- **Node Registry** — Auto-registers nodes on first report; online/offline status tracking (30-min timeout)
- **Node Selector** — Dashboard dropdown to switch between local and remote nodes
- **Per-Node Data** — Isolated storage per node_id with thread-safe locking
- **Node Cleanup** — `DELETE /api/node/<id>` to remove stale nodes

### Security & Authentication
- **Dashboard Login** — Full-site login gate with API key; HMAC-signed session cookie (HttpOnly, SameSite=Strict, 24h)
- **Bearer Token** — API access via `Authorization: Bearer <api_key>`
- **Node Authentication** — Collectors authenticate with HMAC-SHA256(`api_key`, `openclaw-node:<node_id>`)
- **Sensitive Data Masking** — Patterns (sk-*, ghp_*, AKIA*, hex/base64) and key names (key/token/secret/password) auto-masked in display

### Data Analytics
- **KPI Cards (3 rows)** — Model calls, inference latency, token throughput, cache hit ratio, tool stats, thinking depth
- **Inference Timing** — Per-call `inference_ms` from session.jsonl timestamps (standard mode, no debug logs needed)
- **Tool Execution Stats** — Per-tool count, duration, success rate from `toolResult.details`
- **Gateway Restart History** — Detects SHUTDOWN/TRIGGER/STARTUP/CRASH events
- **Session Browser** — Browse all sessions with model, token, and timing details
- **6 System Probes** — health, gateway_status, config_validate, doctor, update_status, models_status

### Remote Node Diagnostics (v4.x)
- **Environment Variables** — Sortable table, keyword filter, masked sensitive values highlighted
- **OpenClaw Config** — JSON tree viewer with syntax coloring
- **Bash History** — Reverse-chronological command log (500 lines), searchable
- **System Journal** — Last 24h journalctl logs (2000 lines), filterable

### Debug Log Features (auto-detected)
When debug logs are available, the dashboard automatically shows:
- **Run Analysis** — Paginated run list with Gantt charts, inference segments, tool arguments
- **Event Timeline** — Filterable event log with category badges
- **Message Pipeline** — Queue → Enqueue → Dequeue → Run → Processed visualization

### Performance
| Optimization | Effect |
|-------------|--------|
| TTL cache (10s) | API: 3.5s → 30ms |
| Gzip transport | Collector payload: ~36% smaller |
| Per-node locking | Parallel reads/writes across nodes |
| Batch API | Single `/api/dashboard` returns all data |
| ETag + 304 | Static file cache validation |

---

## Quick Start

### Dashboard Server

```bash
# Standard mode (local diagnostics only)
python3 openclaw-dashboard.py

# With authentication (required for remote collectors)
python3 openclaw-dashboard.py --api-key your-secret-key

# Open http://localhost:9090
```

### Collector (on remote nodes)

```bash
# Interactive setup (first time)
./openclaw-collector.sh

# Daemon mode (background, periodic upload)
./openclaw-collector.sh daemon

# One-shot collection
./openclaw-collector.sh once

# Check status
./openclaw-collector.sh status

# Stop daemon
./openclaw-collector.sh stop
```

### CLI Probes

```bash
python3 openclaw-dashboard.py --cli                        # All 6 probes
python3 openclaw-dashboard.py --cli --probe health         # Single probe
python3 openclaw-dashboard.py --cli --json                 # JSON output
```

### Shell Script

```bash
./openclaw-diag.sh                      # Today's diagnostics
./openclaw-diag.sh -f                   # Live follow mode
./openclaw-diag.sh -s                   # Summary only
./openclaw-diag.sh -a waicode           # Filter by agent
./openclaw-diag.sh 2026-03-19           # Specific date
```

---

## 🚀 Deployment

### Option 1: systemd (Recommended for Production)

One-click install:

```bash
sudo ./deploy/install.sh --api-key your-secret --port 9090
```

Management:

```bash
sudo systemctl status openclaw-diag
sudo systemctl restart openclaw-diag
sudo journalctl -u openclaw-diag -f
```

Uninstall:

```bash
sudo ./deploy/install.sh --uninstall
```

### Option 2: Docker

```bash
# Build and run
docker compose up -d

# With configuration
OC_DIAG_PORT=9090 OC_DIAG_API_KEY=mysecret docker compose up -d

# Advanced mode
```

Docker mounts host log and session directories as read-only volumes for local diagnostics.

### Option 3: Direct (Development)

```bash
python3 openclaw-dashboard.py --port 9090 --api-key your-key
```

---

## API Reference

### Authentication

| Method | Use Case |
|--------|----------|
| Session Cookie | Browser access (via `/login` page) |
| `Authorization: Bearer <api_key>` | API access and Collector uploads |
| HMAC-SHA256 `node_token` | Collector node identity verification |

### Dashboard Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/dashboard?date=` | Batch: summary + model_calls + restarts |
| `GET /api/summary?date=` | KPI stats |
| `GET /api/model_calls?date=&page=&per_page=` | Paginated model call records |
| `GET /api/tool_stats?date=` | Per-tool stats |
| `GET /api/sessions?date=` | Session browser |
| `GET /api/restarts?date=` | Gateway restart history |
| `GET /api/dates` | Available dates |
| `GET /api/mode` | Current mode info |
| `GET /api/system_info` | System metadata |
| `POST /api/probe/<name>` | Execute probe |
| `POST /api/probe/all` | Execute all probes |

### Multi-Node Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/report` | Collector data upload (gzip + HMAC) |
| `GET /api/nodes` | List all registered nodes |
| `GET /api/node/<id>/dashboard` | Remote node dashboard data |
| `GET /api/node/<id>/summary` | Remote node KPI summary |
| `GET /api/node/<id>/model_calls` | Remote node model calls |
| `GET /api/node/<id>/sessions` | Remote node sessions |
| `GET /api/node/<id>/tool_stats` | Remote node tool stats |
| `GET /api/node/<id>/env_vars` | Remote node environment variables |
| `GET /api/node/<id>/openclaw_config` | Remote node OpenClaw config |
| `GET /api/node/<id>/bash_history` | Remote node command history |
| `GET /api/node/<id>/journalctl` | Remote node system journal |
| `DELETE /api/node/<id>` | Remove node data |

### Debug Log Endpoints (auto-available when logs detected)

| Endpoint | Description |
|----------|-------------|
| `GET /api/events?date=` | Event summary + message pipeline |
| `GET /api/events/timeline?date=` | Paginated event timeline |
| `GET /api/events/errors?date=` | Error list with filtering |
| `GET /api/runs?date=&page=&per_page=` | Run list |
| `GET /api/run/<id>?date=` | Run detail with Gantt chart data |

All endpoints support `date` parameter (defaults to latest available). JSON responses with optional gzip.

---

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | `9090` | Listen port |
| `--host HOST` | `0.0.0.0` | Bind address |
| `--api-key KEY` | *(none)* | Authentication key (enables login gate + collector auth) |
| `--no-browser` | `false` | Don't auto-open browser |
| `--no-local` | `false` | Pure remote mode (no local data) |
| `--log-dir DIR` | auto-detect | Log directory |
| `--sessions-dir DIR` | auto-detect | Session files directory |
| `--token TOKEN` | *(none)* | Legacy access token |
| `--cli` | `false` | CLI mode (no web server) |
| `--probe NAME` | `all` | CLI: specific probe |
| `--json` | `false` | CLI: JSON output format |

---

## Prerequisites

### Standard Mode (default)
No configuration needed. Reads `~/.openclaw/agents/*/sessions/*.jsonl`.

### Debug Log Features (auto-detected)
To enable run-level analysis, configure in `~/.openclaw/openclaw.json`:
```json
{
  "diagnostics": { "enabled": true },
  "logging": { "level": "debug" }
}
```

---

## File Structure

```
openclaw-diag-dashbaard/
├── openclaw-dashboard.py      # Dashboard Server (4000+ lines)
├── openclaw-collector.sh      # Remote Collector script
├── openclaw-diag.sh           # Standalone shell diagnostics
├── start-dashboard.sh         # Interactive start helper
├── Dockerfile                 # Docker image
├── docker-compose.yml         # Docker Compose config
├── static/
│   ├── index.html             # Dashboard layout
│   ├── app.js                 # Frontend logic (1500+ lines)
│   └── style.css              # Dark theme styles
├── deploy/
│   ├── install.sh             # One-click systemd installer
│   ├── openclaw-diag.service  # systemd unit file
│   └── openclaw-diag.env      # Environment config template
├── docs/
│   └── screenshots/           # Dashboard screenshots
├── README.md
└── README_zh.md
```

## Compatibility

- **Python**: 3.7+
- **OS**: Linux, macOS, Windows
- **Browser**: Chrome, Firefox, Safari, Edge

## Changelog

### v4.2 (2026-04-01)
- **Multi-Node Architecture** — Collector/Server distributed model with HTTP report endpoint
- **Collector** — 12 data sources, gzip compression, HMAC-SHA256 node auth, daemon/once/loop modes
- **Dashboard Login** — Full-site API key authentication with HMAC-signed session cookies
- **Sensitive Data** — Environment variables, OpenClaw config, bash history, journalctl collection and display
- **Per-Node Locking** — Thread-safe concurrent access across nodes
- **Node Management** — Auto-register, online/offline status, DELETE cleanup
- **Bearer Auth** — GET endpoints accept Bearer token alongside session cookie
- **Deployment** — systemd service, Docker, one-click install script

### v3.2 (2026-03-20)
- **Multi-Agent Filtering** (`-a/--agent`) — Per-agent diagnostics in batch, summary, and follow modes
- **Agent Activity Distribution** — Per-agent inference count, latency, throughput, session count
- **Bug Fixes** — Date filter accuracy, agent-scoped statistics, virtual Run date filtering

### v3.0 (2026-03-19)
- Session-first architecture — all analytics from `session.jsonl`
- Tool execution stats from `toolResult.details`
- Thinking depth analysis
- No debug config needed for standard mode

## License

[MIT](LICENSE) © 2026 wujiaming88
