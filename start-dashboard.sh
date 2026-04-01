#!/usr/bin/env bash
# ============================================================
#  OpenClaw 诊断面板启动脚本
#  用法:
#    ./start-dashboard.sh              # 启动（自动检测 debug 日志）
#    ./start-dashboard.sh --port 8080  # 指定端口
#    ./start-dashboard.sh --setup-debug  # 配置 debug 日志并启动
#    ./start-dashboard.sh --help       # 帮助
# ============================================================
set -euo pipefail

# ======================== 默认配置 ========================
PORT="8765"
SETUP_DEBUG=false
OPENCLAW_JSON="${OPENCLAW_JSON:-$HOME/.openclaw/openclaw.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_PY="${SCRIPT_DIR}/openclaw-dashboard.py"
BACKUP_FILE=""
CONFIG_MODIFIED=false
GATEWAY_WAS_RUNNING=false

# ======================== 颜色 ========================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ======================== 工具函数 ========================
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { err "$*"; cleanup_on_failure; exit 1; }

cleanup_on_failure() {
    if [[ "$CONFIG_MODIFIED" == true && -n "$BACKUP_FILE" && -f "$BACKUP_FILE" ]]; then
        warn "检测到异常，正在恢复 openclaw.json 备份..."
        if cp "$BACKUP_FILE" "$OPENCLAW_JSON"; then
            ok "已恢复 openclaw.json 从备份: $BACKUP_FILE"
            if [[ "$GATEWAY_WAS_RUNNING" == true ]]; then
                warn "尝试用恢复后的配置重启 Gateway..."
                if timeout 30 openclaw gateway restart >/dev/null 2>&1; then
                    ok "Gateway 已恢复运行"
                else
                    err "Gateway 恢复重启失败，请手动执行: openclaw gateway start"
                fi
            fi
        else
            err "恢复备份失败！备份文件: $BACKUP_FILE"
            err "请手动恢复: cp $BACKUP_FILE $OPENCLAW_JSON"
        fi
    fi
}

# 捕获异常退出信号
trap cleanup_on_failure ERR

usage() {
    cat <<EOF
OpenClaw 诊断面板启动脚本

用法:
  $(basename "$0") [选项]

选项:
  --port <端口>       指定 Dashboard 端口（默认: 8765）
  --setup-debug       配置 debug 日志并重启 Gateway（用于启用完整诊断）
  --config <路径>     指定 openclaw.json 路径（默认: ~/.openclaw/openclaw.json）
  --help              显示帮助

Dashboard 会自动检测 debug 日志是否可用：
  - 有 debug 日志：显示 Run 级详情、推理分段甘特图、完整事件时间线
  - 无 debug 日志：显示 session 级统计、模型调用记录、工具统计

使用 --setup-debug 可自动启用 debug 日志（修改 openclaw.json 并重启 Gateway）。
退出 Dashboard 后会询问是否恢复原始配置。

示例:
  ./$(basename "$0")                    # 直接启动，自动检测
  ./$(basename "$0") --port 9090        # 指定端口
  ./$(basename "$0") --setup-debug      # 配置 debug 日志后启动
EOF
    exit 0
}

# ======================== 参数解析 ========================
while [[ $# -gt 0 ]]; do
    case "$1" in
        --setup-debug)
            SETUP_DEBUG=true
            shift
            ;;
        --port)
            [[ -z "${2:-}" ]] && die "--port 需要指定端口号"
            PORT="$2"
            shift 2
            ;;
        --config)
            [[ -z "${2:-}" ]] && die "--config 需要指定路径"
            OPENCLAW_JSON="$2"
            shift 2
            ;;
        --help|-h)
            usage
            ;;
        *)
            die "未知参数: $1（使用 --help 查看帮助）"
            ;;
    esac
done

# ======================== 前置检查 ========================
info "前置检查..."

# 1. Dashboard 脚本存在
if [[ ! -f "$DASHBOARD_PY" ]]; then
    die "找不到 Dashboard 脚本: $DASHBOARD_PY"
fi

# 2. Python3 可用
if ! command -v python3 &>/dev/null; then
    die "找不到 python3，请先安装 Python 3.6+"
fi

# 3. Python 版本检查
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" 2>/dev/null; then
    ok "Python $PYVER"
else
    die "Python 版本过低: $PYVER（需要 3.6+）"
fi

# 4. 端口是否被占用
if command -v ss &>/dev/null; then
    if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
        OCCUPIER=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | head -1 | grep -oP 'pid=\K\d+' || echo "未知")
        die "端口 ${PORT} 已被占用（PID: ${OCCUPIER}）。使用 --port 指定其他端口"
    fi
elif command -v lsof &>/dev/null; then
    if lsof -i ":${PORT}" -sTCP:LISTEN &>/dev/null; then
        die "端口 ${PORT} 已被占用。使用 --port 指定其他端口"
    fi
fi
ok "端口 ${PORT} 可用"

# ======================== 配置 debug 日志（可选）========================
if [[ "$SETUP_DEBUG" == true ]]; then
    if ! command -v openclaw &>/dev/null; then
        die "--setup-debug 需要 openclaw CLI，但未找到"
    fi
    ok "openclaw CLI 可用"

    echo ""
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "  🔬 配置 debug 日志"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    if [[ ! -f "$OPENCLAW_JSON" ]]; then
        die "找不到 openclaw.json: $OPENCLAW_JSON"
    fi

    # 读取当前配置
    CURRENT_LOG_LEVEL=$(python3 -c "
import json, sys
try:
    with open('$OPENCLAW_JSON') as f:
        d = json.load(f)
    print(d.get('logging', {}).get('level', 'info'))
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>&1) || die "读取 openclaw.json 失败: $CURRENT_LOG_LEVEL"

    CURRENT_DIAG=$(python3 -c "
import json
with open('$OPENCLAW_JSON') as f:
    d = json.load(f)
print(d.get('diagnostics', {}).get('enabled', False))
" 2>/dev/null || echo "False")

    info "当前日志级别: $CURRENT_LOG_LEVEL"
    info "当前诊断模式: $CURRENT_DIAG"

    if [[ "$CURRENT_LOG_LEVEL" != "debug" || "$CURRENT_DIAG" != "True" ]]; then
        info "需要修改配置（日志级别 → debug, 诊断 → 启用）"

        BACKUP_FILE="${OPENCLAW_JSON}.bak.$(date +%Y%m%d%H%M%S)"
        if ! cp "$OPENCLAW_JSON" "$BACKUP_FILE"; then
            die "备份失败: $OPENCLAW_JSON → $BACKUP_FILE"
        fi
        ok "已备份: $BACKUP_FILE"

        if ! python3 -c "
import json, sys
try:
    with open('$OPENCLAW_JSON', 'r') as f:
        d = json.load(f)
    if 'logging' not in d:
        d['logging'] = {}
    d['logging']['level'] = 'debug'
    if 'diagnostics' not in d:
        d['diagnostics'] = {}
    d['diagnostics']['enabled'] = True
    with open('$OPENCLAW_JSON', 'w') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print('OK')
except Exception as e:
    print(f'FAIL:{e}', file=sys.stderr)
    sys.exit(1)
" 2>&1; then
            CONFIG_MODIFIED=true
            die "修改 openclaw.json 失败"
        fi
        CONFIG_MODIFIED=true

        NEW_LEVEL=$(python3 -c "
import json
with open('$OPENCLAW_JSON') as f:
    d = json.load(f)
print(d['logging']['level'])
" 2>/dev/null)

        if [[ "$NEW_LEVEL" != "debug" ]]; then
            die "配置修改验证失败：期望 debug，实际 $NEW_LEVEL"
        fi
        ok "配置已更新: logging.level=debug, diagnostics.enabled=true"

        # 重启 Gateway
        info "重启 Gateway..."
        if openclaw gateway status --json 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    if d.get('service', {}).get('running') or d.get('running'):
        sys.exit(0)
    sys.exit(1)
except:
    sys.exit(1)
" 2>/dev/null; then
            GATEWAY_WAS_RUNNING=true
            ok "Gateway 当前运行中"
        else
            GW_STATUS_TEXT=$(openclaw gateway status 2>&1 || true)
            if echo "$GW_STATUS_TEXT" | grep -qi "running\|active\|pid"; then
                GATEWAY_WAS_RUNNING=true
                ok "Gateway 当前运行中"
            else
                warn "Gateway 未运行，跳过重启"
            fi
        fi

        if [[ "$GATEWAY_WAS_RUNNING" == true ]]; then
            info "正在重启 Gateway（超时 60s）..."
            RESTART_OUTPUT=$(timeout 60 openclaw gateway restart 2>&1) || {
                RESTART_EXIT=$?
                err "Gateway 重启失败（退出码: $RESTART_EXIT）"
                die "Gateway 重启失败，已自动恢复配置"
            }
            ok "Gateway 重启成功"

            info "等待 Gateway 就绪..."
            READY=false
            for i in $(seq 1 10); do
                sleep 2
                if openclaw gateway status 2>/dev/null | grep -qi "running\|active\|pid"; then
                    READY=true
                    break
                fi
            done

            if [[ "$READY" != true ]]; then
                warn "Gateway 状态确认超时，继续启动 Dashboard..."
            else
                ok "Gateway 已就绪"
            fi
        fi
    else
        info "配置已是 debug 模式，无需修改"
    fi
fi

# ======================== 启动 Dashboard ========================
echo ""
info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "  📊 启动 Dashboard（自动检测模式）"
info "  端口: ${PORT}"
info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 "$DASHBOARD_PY" --port "$PORT"
DASH_EXIT=$?

# ======================== 善后处理 ========================
echo ""

if [[ "$CONFIG_MODIFIED" == true && -n "$BACKUP_FILE" && -f "$BACKUP_FILE" ]]; then
    echo ""
    info "Dashboard 已退出（退出码: $DASH_EXIT）"
    echo ""

    if ! diff -q "$BACKUP_FILE" "$OPENCLAW_JSON" &>/dev/null; then
        echo -e "${YELLOW}是否恢复原始配置？${NC}"
        echo "  [y] 恢复原始配置并重启 Gateway（推荐）"
        echo "  [n] 保持当前 debug 配置"
        echo "  [b] 仅查看备份路径，稍后手动处理"
        echo ""
        read -r -p "选择 [y/n/b] (默认 y): " CHOICE
        CHOICE=${CHOICE:-y}

        case "$CHOICE" in
            y|Y)
                info "恢复原始配置..."
                if cp "$BACKUP_FILE" "$OPENCLAW_JSON"; then
                    ok "配置已恢复"
                    if [[ "$GATEWAY_WAS_RUNNING" == true ]]; then
                        info "重启 Gateway 使配置生效..."
                        if timeout 60 openclaw gateway restart >/dev/null 2>&1; then
                            ok "Gateway 已重启"
                        else
                            warn "Gateway 重启失败，请手动执行: openclaw gateway restart"
                        fi
                    fi
                    rm -f "$BACKUP_FILE"
                    ok "备份已清理"
                else
                    err "恢复失败！请手动恢复: cp $BACKUP_FILE $OPENCLAW_JSON"
                fi
                ;;
            n|N)
                info "保持当前 debug 配置"
                info "备份文件保留在: $BACKUP_FILE"
                info "手动恢复: cp $BACKUP_FILE $OPENCLAW_JSON && openclaw gateway restart"
                ;;
            b|B)
                info "备份文件: $BACKUP_FILE"
                info "手动恢复: cp $BACKUP_FILE $OPENCLAW_JSON && openclaw gateway restart"
                ;;
            *)
                warn "无效选择，保持当前配置"
                info "备份文件: $BACKUP_FILE"
                ;;
        esac
    else
        info "配置未变化，无需恢复"
        rm -f "$BACKUP_FILE" 2>/dev/null
    fi
fi

exit ${DASH_EXIT:-0}
