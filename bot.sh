#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m';  GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m';     RESET='\033[0m'

ok()    { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
info()  { echo -e "${CYAN}[INFO]${RESET} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
VENV_ACTIVATE="${VENV_DIR}/bin/activate"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"

# Default paths (overridable via Storage/Config/c-cord.json)
BOT_ENTRY="${SCRIPT_DIR}/Src/Bot.py"
ENV_FILE="${SCRIPT_DIR}/Src/.env"
TICKET_ENV_FILE="${SCRIPT_DIR}/Src/ticket.env"
LOG_DIR="${SCRIPT_DIR}/Storage/Logs"
TEMP_DIR="${SCRIPT_DIR}/Storage/Temp"
MAX_LOG_BYTES=$((10 * 1024 * 1024))
MAX_ROTATED=5

# Load config overrides if present
CC_CONFIG_FILE="${SCRIPT_DIR}/Storage/Config/c-cord.json"
if [[ -f "${CC_CONFIG_FILE}" ]] && command -v python3 >/dev/null 2>&1; then
    eval "$(CC_SCRIPT_DIR="${SCRIPT_DIR}" CC_CONFIG_FILE="${CC_CONFIG_FILE}" \
        python3 "${SCRIPT_DIR}/scripts/get_cc_config.py" 2>/dev/null)" 2>/dev/null || true
fi

PID_FILE="${TEMP_DIR}/bot.pid"
NGROK_PID_FILE="${TEMP_DIR}/ngrok.pid"
NGROK_LOG="${LOG_DIR}/ngrok.log"
LOG_FILE="${LOG_DIR}/bot.log"
ROTATE_PREFIX="${LOG_DIR}/bot_"

_usage() {
    echo -e "${BOLD}Usage:${RESET} c-cord <command> [flags]   (per-clone bot.sh — see ~/.local/bin/c-cord for multi-instance)"
    echo ""
    echo -e "${BOLD}Multi-instance (global ~/.local/bin/c-cord):${RESET}"
    echo "  c-cord list | add <id> <path> | remove <id> | default <id> | new <id> [--path DIR] [--repo URL]"
    echo "  c-cord start [id|all] …   stop / restart / status / logs / console / update / repair  (optional id or all)"
    echo ""
    echo -e "${BOLD}Commands (this clone):${RESET}"
    echo "  start                     Start bot in the background"
    echo "  start -f / --force        Start — ignore non-fatal errors"
    echo ""
    echo "  stop                      Graceful stop (SIGTERM → SIGKILL)"
    echo "  stop -9 / --kill          Hard stop — SIGKILL immediately"
    echo ""
    echo "  restart                   stop + start"
    echo "  restart -f / --force      stop + start -f"
    echo ""
    echo "  status                    Show PID and uptime"
    echo "  status -v / --verbose     Status + last 10 log lines"
    echo ""
    echo "  logs                      Follow log (tail -f)"
    echo "  logs -n N                 Last N lines, no follow"
    echo ""
    echo "  console                   Interactive host REPL (run slash commands; bot must be running)"
    echo ""
    echo "  update [version]          Latest GitHub Release (or pin tag) → pip → restart"
    echo "  update --branch          git pull on current branch (dev) → pip → restart"
    echo "  update -f / --force      Continue even if git/archive step fails"
    echo ""
    echo "  module refresh            Scan Modules/ — add new files to registry"
    echo "  module refresh_registry   Alias for 'module refresh'"
    echo "  module refresh --dry-run  Preview additions without writing"
    echo ""
    echo "  repair [--repair] [--dry-run] [--exclude <file>]…   git restore missing tracked files, then Storage JSON + bot_sys.cfg"
    echo "    --storage-only   Skip git (only known Storage/Config, Storage/Data JSON, bot_sys.cfg)"
    echo "    [--remote origin] [--ref origin/main] [--no-fetch] [--no-storage]"
    echo "    (repeat --exclude basename e.g. xp.json to skip overwriting custom data)"
    echo ""
    echo "Runtime failures (token, network, Discord, your edits) are yours to diagnose."
}

_ensure_runtime_dirs() {
    mkdir -p "${LOG_DIR}" "${TEMP_DIR}"
}

_ensure_ticket_env() {
    local secret
    secret="$(openssl rand -hex 16 2>/dev/null)"
    if [[ -z "${secret}" ]]; then
        secret="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 32)"
    fi
    [[ -n "${secret}" ]] || secret="$(date +%s%N | sha256sum 2>/dev/null | head -c 32)"

    if [[ ! -f "${TICKET_ENV_FILE}" ]]; then
        info "Creating Src/ticket.env with default settings..."
        {
            echo "# ── Ticket system configuration ────────────────────────────"
            echo "# TICKET_SECRET is auto-generated for signing ticket exports."
            echo "# Restart the bot after making changes."
            echo "#"
            echo "TICKET_SECRET=${secret}"
            echo "#"
            echo "# HOST_CONSOLE_TOKEN secures the local c-cord console REPL (Unix socket only)."
            echo "HOST_CONSOLE_TOKEN=$(openssl rand -hex 24 2>/dev/null || echo "${secret}")"
            echo "#"
            echo "# BACKUP_SECRET signs encrypted server backup payloads (/backup)."
            echo "BACKUP_SECRET=$(openssl rand -hex 16 2>/dev/null || echo "${secret}")"
            echo "#"
            echo "# TICKET_LOG_CHANNEL_ID=   # Channel ID to post ticket event logs"
            echo "# TICKET_MAX_PER_USER=3    # Max open tickets per user (0 = unlimited)"
            echo "# TICKET_TRANSCRIPT_ENABLED=true  # Save transcript HTML on close"
        } > "${TICKET_ENV_FILE}"
        ok "Src/ticket.env created."
    elif ! grep -qE '^TICKET_SECRET=' "${TICKET_ENV_FILE}" 2>/dev/null; then
        info "Adding TICKET_SECRET to Src/ticket.env..."
        echo "" >> "${TICKET_ENV_FILE}"
        echo "TICKET_SECRET=${secret}" >> "${TICKET_ENV_FILE}"
        ok "TICKET_SECRET added."
    elif ! grep -qE '^HOST_CONSOLE_TOKEN=' "${TICKET_ENV_FILE}" 2>/dev/null; then
        info "Adding HOST_CONSOLE_TOKEN to Src/ticket.env..."
        secret="$(openssl rand -hex 24 2>/dev/null || LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 48)"
        echo "" >> "${TICKET_ENV_FILE}"
        echo "# HOST_CONSOLE_TOKEN secures the local c-cord console REPL (127.0.0.1 / Unix socket only)." >> "${TICKET_ENV_FILE}"
        echo "HOST_CONSOLE_TOKEN=${secret}" >> "${TICKET_ENV_FILE}"
        ok "HOST_CONSOLE_TOKEN added."
    fi

    if [[ -f "${TICKET_ENV_FILE}" ]] && ! grep -qE '^BACKUP_SECRET=' "${TICKET_ENV_FILE}" 2>/dev/null; then
        info "Adding BACKUP_SECRET to Src/ticket.env..."
        backup_secret="$(openssl rand -hex 16 2>/dev/null || echo "${secret}")"
        echo "" >> "${TICKET_ENV_FILE}"
        echo "# BACKUP_SECRET signs encrypted server backup payloads (/backup)." >> "${TICKET_ENV_FILE}"
        echo "BACKUP_SECRET=${backup_secret}" >> "${TICKET_ENV_FILE}"
        ok "BACKUP_SECRET added."
    fi
}

_is_pid_running() {
    local pid="${1:-}"
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    kill -0 "${pid}" 2>/dev/null
}

_read_pid() {
    [[ -f "${PID_FILE}" ]] || return 1
    local pid
    pid="$(<"${PID_FILE}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    printf "%s" "${pid}"
}

_cleanup_stale_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local pid
        pid="$(<"${PID_FILE}")" || true
        if ! _is_pid_running "${pid}"; then
            rm -f "${PID_FILE}"
        fi
    fi
    if [[ -f "${NGROK_PID_FILE}" ]]; then
        local pid
        pid="$(<"${NGROK_PID_FILE}")" || true
        if ! _is_pid_running "${pid}"; then
            rm -f "${NGROK_PID_FILE}"
        fi
    fi
}

# ── Ko-fi / ngrok helpers ────────────────────────────────────────────────────
_get_kofi_port() {
    [[ -f "${ENV_FILE}" ]] || { echo "5000"; return; }
    local line
    while IFS= read -r line; do
        if [[ "${line}" =~ ^KOFI_PORT=([0-9]+) ]]; then
            echo "${BASH_REMATCH[1]}"
            return
        fi
    done < "${ENV_FILE}"
    echo "5000"
}

_is_kofi_configured() {
    [[ -f "${ENV_FILE}" ]] || return 1
    grep -qE '^KOFI_VERIFICATION_TOKEN=.+$' "${ENV_FILE}" 2>/dev/null
}

_ngrok_bin() {
    if command -v ngrok >/dev/null 2>&1; then
        echo "ngrok"
        return
    fi
    local local_ngrok="${SCRIPT_DIR}/Storage/Tools/ngrok"
    if [[ -x "${local_ngrok}" ]]; then
        echo "${local_ngrok}"
        return
    fi
    echo ""
}

_ensure_ngrok() {
    local bin
    bin="$(_ngrok_bin)"
    if [[ -n "${bin}" ]]; then
        return 0
    fi
    info "ngrok not found. Attempting to install..."
    mkdir -p "${SCRIPT_DIR}/Storage/Tools"
    local arch url
    arch="$(uname -m)"
    case "${arch}" in
        x86_64|amd64) arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) arch="amd64" ;;
    esac
    url="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-${arch}.tgz"
    local tmpdir
    tmpdir="$(mktemp -d)"
    if curl -sSLf "${url}" 2>/dev/null | tar xz -C "${tmpdir}" 2>/dev/null; then
        local extracted
        extracted="$(find "${tmpdir}" -name ngrok -type f 2>/dev/null | head -1)"
        if [[ -n "${extracted}" ]]; then
            mv "${extracted}" "${SCRIPT_DIR}/Storage/Tools/ngrok"
            chmod +x "${SCRIPT_DIR}/Storage/Tools/ngrok"
            ok "ngrok installed to Storage/Tools/ngrok"
            rm -rf "${tmpdir}"
            return 0
        fi
    fi
    rm -rf "${tmpdir}"
    if command -v snap >/dev/null 2>&1; then
        info "Trying snap install ngrok..."
        if sudo snap install ngrok 2>/dev/null; then
            ok "ngrok installed via snap"
            return 0
        fi
    fi
    err "Could not install ngrok. Install manually: https://ngrok.com/download"
    err "Then run: ngrok config add-authtoken <your-token>"
    return 1
}

_ngrok_enabled() {
    if [[ -f "${CC_CONFIG_FILE}" ]] && command -v python3 >/dev/null 2>&1; then
        local enabled
        enabled="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("ngrok_enabled", True))' "${CC_CONFIG_FILE}" 2>/dev/null)" || true
        [[ "${enabled}" == "True" || "${enabled}" == "true" ]] || return 1
    fi
    return 0
}

_start_ngrok() {
    _is_kofi_configured || return 0
    _ngrok_enabled || return 0

    if [[ -f "${NGROK_PID_FILE}" ]]; then
        local pid
        pid="$(<"${NGROK_PID_FILE}")" || true
        if _is_pid_running "${pid}"; then
            info "ngrok already running (PID ${pid})"
            return 0
        fi
        rm -f "${NGROK_PID_FILE}"
    fi

    _ensure_ngrok || return 1
    local bin port
    bin="$(_ngrok_bin)"
    port="$(_get_kofi_port)"

    echo "ngrok started at $(date '+%Y-%m-%d %H:%M:%S')" >> "${NGROK_LOG}"
    "${bin}" http "${port}" --log=stdout >> "${NGROK_LOG}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${NGROK_PID_FILE}"
    sleep 2
    if _is_pid_running "${pid}"; then
        ok "ngrok started (PID ${pid}) — port ${port}"
        local host
        host="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("kofi_webhook_host",""))' "${CC_CONFIG_FILE}" 2>/dev/null)" || true
        if [[ -n "${host}" ]]; then
            info "Ko-fi webhook URL: https://${host}/kofi-webhook"
        else
            info "Set Ko-fi webhook to: https://<ngrok-url>/kofi-webhook (add kofi_webhook_host in c-cord.json)"
        fi
    else
        rm -f "${NGROK_PID_FILE}"
        warn "ngrok may have failed. Check Storage/Logs/ngrok.log"
    fi
}

_stop_ngrok() {
    [[ -f "${NGROK_PID_FILE}" ]] || return 0
    local pid
    pid="$(<"${NGROK_PID_FILE}")" || true
    rm -f "${NGROK_PID_FILE}"
    if _is_pid_running "${pid}"; then
        kill "${pid}" 2>/dev/null || true
        local waited=0
        while _is_pid_running "${pid}" && (( waited < 5 )); do
            sleep 0.5
            waited=$((waited + 1))
        done
        _is_pid_running "${pid}" && kill -9 "${pid}" 2>/dev/null || true
        ok "ngrok stopped"
    fi
}

_check_venv() {
    if [[ ! -d "${VENV_DIR}" || ! -x "${PYTHON_BIN}" ]]; then
        err "Virtual environment not found. Run ./install.sh first."
        exit 1
    fi
}

_check_token() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        err "DISCORD_TOKEN is not set in Src/.env. Run ./install.sh to configure it."
        exit 1
    fi

    local token=""
    local line
    while IFS= read -r line; do
        case "${line}" in
            DISCORD_TOKEN=*)
                token="${line#DISCORD_TOKEN=}"
                break
                ;;
        esac
    done < "${ENV_FILE}"
    token="${token#"${token%%[![:space:]]*}"}"
    token="${token%"${token##*[![:space:]]}"}"
    if [[ -z "${token}" ]]; then
        err "DISCORD_TOKEN is not set in Src/.env. Run ./install.sh to configure it."
        exit 1
    fi
}

_check_bot_entry() {
    if [[ ! -f "${BOT_ENTRY}" ]]; then
        err "Bot entry file not found at Src/Bot.py."
        exit 1
    fi
}

_check_syntax() {
    local output
    if ! output="$("${PYTHON_BIN}" -m py_compile "${BOT_ENTRY}" 2>&1)"; then
        err "Src/Bot.py has a syntax error. Fix it before starting."
        echo "${output}" >&2
        exit 1
    fi
}

_check_prereqs() {
    _check_venv
    _check_token
    _check_bot_entry
    _check_syntax
}

_log_size_bytes() {
    if [[ ! -f "${LOG_FILE}" ]]; then
        echo 0
        return
    fi
    local size
    size="$(stat -c%s "${LOG_FILE}" 2>/dev/null || true)"
    if [[ -z "${size}" ]]; then
        size="$(stat -f%z "${LOG_FILE}" 2>/dev/null || echo 0)"
    fi
    echo "${size}"
}

_trim_rotated_logs() {
    local files=("${ROTATE_PREFIX}"*.log)
    [[ -e "${files[0]}" ]] || return 0

    while (( ${#files[@]} > MAX_ROTATED )); do
        local oldest_file="" oldest_mtime="" f mtime
        for f in "${files[@]}"; do
            mtime="$(stat -c%Y "${f}" 2>/dev/null || stat -f%m "${f}" 2>/dev/null || echo 0)"
            if [[ -z "${oldest_file}" || "${mtime}" -lt "${oldest_mtime}" ]]; then
                oldest_file="${f}"
                oldest_mtime="${mtime}"
            fi
        done
        [[ -n "${oldest_file}" ]] || break
        rm -f "${oldest_file}"
        files=("${ROTATE_PREFIX}"*.log)
        [[ -e "${files[0]}" ]] || break
    done
}

_rotate_log_if_needed() {
    local size
    size="$(_log_size_bytes)"
    if (( size <= MAX_LOG_BYTES )); then
        return
    fi

    local ts rotated
    ts="$(date +%Y%m%d_%H%M%S)"
    rotated="${ROTATE_PREFIX}${ts}.log"
    mv "${LOG_FILE}" "${rotated}"
    : > "${LOG_FILE}"
    _trim_rotated_logs
    info "Log rotated to ${rotated}"
}

_bot_uptime_pretty() {
    local pid="${1}"
    local etimes
    etimes="$(ps -o etimes= -p "${pid}" 2>/dev/null | tr -d '[:space:]')"
    [[ "${etimes}" =~ ^[0-9]+$ ]] || { echo "unknown"; return; }
    local h=$((etimes / 3600))
    local m=$(((etimes % 3600) / 60))
    local s=$((etimes % 60))
    echo "${h}h ${m}m ${s}s"
}

# ── start ────────────────────────────────────────────────────────────────────
# Flags:
#   -f / --force   Ignore non-fatal errors: syntax warnings and immediate crash.
#                  Still exits on missing venv or missing token.
_start_bot() {
    local force="false"
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            -f|--force) force="true" ;;
            *)
                err "start: unknown argument \"${1}\" — only -f / --force is allowed."
                exit 1
                ;;
        esac
        shift
    done

    _ensure_runtime_dirs
    _ensure_ticket_env
    _cleanup_stale_pid

    if [[ "${force}" == "true" ]]; then
        _check_venv
        _check_token
        _check_bot_entry
        local syntax_out
        if ! syntax_out="$("${PYTHON_BIN}" -m py_compile "${BOT_ENTRY}" 2>&1)"; then
            warn "Src/Bot.py has a syntax error — starting anyway (-f)."
            warn "${syntax_out}"
        fi
    else
        _check_prereqs
    fi

    if [[ -f "${PID_FILE}" ]]; then
        local existing_pid
        existing_pid="$(_read_pid || true)"
        if _is_pid_running "${existing_pid}"; then
            warn "Bot is already running (PID ${existing_pid}). Use c-cord restart to reload."
            return 0
        fi
        rm -f "${PID_FILE}"
    fi

    _rotate_log_if_needed

    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "──────────────────────────────────────────────────" >> "${LOG_FILE}"
    echo "Bot started at ${ts}" >> "${LOG_FILE}"
    echo "──────────────────────────────────────────────────" >> "${LOG_FILE}"

    bash -c "cd \"${SCRIPT_DIR}\" && source \"${VENV_ACTIVATE}\" && PYTHONUNBUFFERED=1 exec python \"${BOT_ENTRY}\"" >> "${LOG_FILE}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${PID_FILE}"

    sleep 3
    if ! _is_pid_running "${pid}"; then
        rm -f "${PID_FILE}"
        if [[ "${force}" == "true" ]]; then
            warn "Coffeecord exited immediately — check Storage/Logs/bot.log"
            tail -n 5 "${LOG_FILE}" >&2
            return 0
        fi
        err "Coffeecord crashed immediately after starting. Last log lines:"
        tail -n 5 "${LOG_FILE}" >&2
        return 1
    fi

    ok "Coffeecord started (PID ${pid})"
    ok "Logging to Storage/Logs/bot.log"

    _start_ngrok || true
}

# ── stop ─────────────────────────────────────────────────────────────────────
# Flags:
#   -9 / --kill   SIGKILL immediately — skip graceful shutdown wait.
_stop_bot() {
    local kill_hard="false"
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            -9|--kill) kill_hard="true" ;;
            *)
                err "stop: unknown argument \"${1}\" — only -9 / --kill is allowed."
                exit 1
                ;;
        esac
        shift
    done

    _ensure_runtime_dirs
    _cleanup_stale_pid

    if [[ ! -f "${PID_FILE}" ]]; then
        info "Bot is not running."
        return 0
    fi

    local pid
    pid="$(_read_pid || true)"
    if ! _is_pid_running "${pid}"; then
        rm -f "${PID_FILE}"
        info "Bot is not running."
        return 0
    fi

    if [[ "${kill_hard}" == "true" ]]; then
        kill -9 "${pid}" 2>/dev/null || true
        rm -f "${PID_FILE}"
        ok "Coffeecord stopped (SIGKILL)."
        return 0
    fi

    kill "${pid}" 2>/dev/null || true
    local waited=0
    while _is_pid_running "${pid}" && (( waited < 20 )); do
        sleep 0.5
        waited=$((waited + 1))
    done

    if _is_pid_running "${pid}"; then
        warn "Bot did not stop in time; sending SIGKILL."
        kill -9 "${pid}" 2>/dev/null || true
    fi

    rm -f "${PID_FILE}"
    _stop_ngrok
    ok "Coffeecord stopped."
}

# ── status ───────────────────────────────────────────────────────────────────
# Flags:
#   -v / --verbose   Also print the last 10 lines of the log.
_status_bot() {
    local verbose="false"
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            -v|--verbose) verbose="true" ;;
            *)
                err "status: unknown argument \"${1}\" — only -v / --verbose is allowed."
                exit 1
                ;;
        esac
        shift
    done

    _ensure_runtime_dirs
    _cleanup_stale_pid

    if [[ -f "${PID_FILE}" ]]; then
        local pid
        pid="$(_read_pid || true)"
        if _is_pid_running "${pid}"; then
            local uptime last_line
            uptime="$(_bot_uptime_pretty "${pid}")"
            if [[ -f "${LOG_FILE}" ]]; then
                last_line="$(tail -n 1 "${LOG_FILE}" 2>/dev/null || true)"
            else
                last_line=""
            fi
            echo -e "${GREEN}●${RESET} Coffeecord — running"
            echo "  PID     : ${pid}"
            echo "  Uptime  : ${uptime}"
            echo "  Log     : Storage/Logs/bot.log  (last line: ${last_line:-<empty>})"
            if [[ -f "${NGROK_PID_FILE}" ]]; then
                local ngrok_pid
                ngrok_pid="$(<"${NGROK_PID_FILE}" 2>/dev/null)" || true
                if [[ "${ngrok_pid}" =~ ^[0-9]+$ ]] && _is_pid_running "${ngrok_pid}"; then
                    echo "  ngrok   : running (PID ${ngrok_pid})"
                fi
            fi
            if [[ "${verbose}" == "true" && -f "${LOG_FILE}" ]]; then
                echo ""
                echo "  ── Last 10 log lines ──────────────────────────────"
                tail -n 10 "${LOG_FILE}" | sed 's/^/  /'
            fi
            return 0
        fi
    fi

    echo -e "○ Coffeecord — stopped"
}

# ── logs ─────────────────────────────────────────────────────────────────────
# Flags:
#   -n N   Print last N lines then exit (no follow).
#   (none) Follow log with tail -f.
_logs_bot() {
    _ensure_runtime_dirs

    if [[ "${1:-}" == "-n" ]]; then
        local n="${2:-}"
        if [[ ! "${n}" =~ ^[0-9]+$ ]]; then
            err "Usage: c-cord logs -n <number>"
            exit 1
        fi
        shift 2 || true
        if [[ $# -gt 0 ]]; then
            err "logs: unexpected arguments after -n: $*"
            exit 1
        fi
        if [[ ! -f "${LOG_FILE}" ]]; then
            err "No log file yet. Start the bot with c-cord start"
            exit 1
        fi
        tail -n "${n}" "${LOG_FILE}"
        return 0
    fi

    if [[ $# -gt 0 ]]; then
        err "logs: unknown argument \"$1\" — use no args (follow) or -n <lines>"
        exit 1
    fi

    if [[ ! -f "${LOG_FILE}" ]]; then
        info "No log file yet. Start the bot with c-cord start"
        return 0
    fi

    tail -f "${LOG_FILE}"
}

# ── console ───────────────────────────────────────────────────────────────────
# Interactive host REPL — run slash/owner commands against the live bot process.
_console_bot() {
    if [[ $# -gt 0 ]]; then
        if [[ "${1:-}" == "clear" || "${1:-}" == "-n" ]]; then
            warn "Log tailing moved to c-cord logs (e.g. c-cord logs -n ${2:-50})."
            _logs_bot "$@"
            return $?
        fi
        err "console: unknown argument \"$1\" — use no args for the REPL"
        exit 1
    fi

    if [[ ! -x "${PYTHON_BIN}" ]]; then
        err "Python venv not found. Run install.sh first."
        exit 1
    fi

    exec "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/ccord_console.py"
}

# ── update ───────────────────────────────────────────────────────────────────
# Optional positional: release version (e.g. 1.0.3); omit = latest GitHub release.
# Flags:
#   --branch       git pull on current branch instead of release checkout
#   -f / --force   Continue even when git fetch/checkout/pull / archive apply fails
#
# Release path: prefer git fetch+checkout of the resolved tag; on missing .git or
# git failure, download the GitHub zipball/tarball and overlay (preserves Storage/,
# .venv/, Src/.env, etc.).
_update_bot() {
    local force="false"
    local branch_mode="false"
    local version_arg=""
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            -f|--force) force="true" ;;
            --branch) branch_mode="true" ;;
            -*)
                err "Unknown option: ${1}"
                echo "Usage: c-cord update [--branch] [-f] [version]"
                exit 1
                ;;
            *)
                if [[ -n "${version_arg}" ]]; then
                    err "Unexpected extra argument: ${1}"
                    exit 1
                fi
                version_arg="${1}"
                ;;
        esac
        shift
    done

    if [[ "${branch_mode}" == "true" && -n "${version_arg}" ]]; then
        err "Cannot combine --branch with a release version."
        exit 1
    fi

    _ensure_runtime_dirs
    if [[ "${force}" == "true" ]]; then
        # In force mode, keep hard-blocker checks but allow non-fatal startup checks to be ignored.
        _check_venv
        _check_token
        _check_bot_entry
    else
        _check_prereqs
    fi

    local was_running=false
    local current_pid
    current_pid="$(_read_pid || true)"
    if _is_pid_running "${current_pid}"; then
        was_running=true
        _stop_bot
    fi

    local vcs_summary="skipped"
    if [[ "${branch_mode}" == "true" ]]; then
        if [[ -d "${SCRIPT_DIR}/.git" ]] && command -v git >/dev/null 2>&1; then
            info "Pulling latest changes on current branch..."
            if git -C "${SCRIPT_DIR}" pull; then
                vcs_summary="git pull: done"
            elif [[ "${force}" == "true" ]]; then
                warn "git pull failed — continuing anyway (-f)."
                vcs_summary="git pull: failed (ignored)"
            else
                err "git pull failed. Use c-cord update -f to continue anyway."
                exit 1
            fi
        else
            if [[ "${force}" == "true" ]]; then
                warn "Not a git repository or git unavailable; skipping git pull."
                vcs_summary="git pull: skipped"
            else
                err "c-cord update --branch requires a git clone and git installed."
                exit 1
            fi
        fi
    else
        local release_json=""
        local tag=""
        info "Resolving GitHub release${version_arg:+ (${version_arg})}..."
        if release_json="$(
            GITHUB_REPO="${GITHUB_REPO:-}" GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
                "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/ccord_release_resolve.py" --json \
                "${SCRIPT_DIR}" ${version_arg:+"${version_arg}"}
        )"; then
            tag="$(
                "${PYTHON_BIN}" -c 'import json,sys; print(json.loads(sys.argv[1])["tag"])' "${release_json}"
            )"
        else
            if [[ "${force}" == "true" ]]; then
                warn "Could not resolve a release tag — continuing anyway (-f)."
                vcs_summary="release: resolve failed (ignored)"
            else
                exit 1
            fi
        fi

        local applied="false"
        if [[ -n "${tag}" ]]; then
            local try_git="false"
            if [[ -d "${SCRIPT_DIR}/.git" ]] && command -v git >/dev/null 2>&1; then
                local dirty
                dirty="$(git -C "${SCRIPT_DIR}" status --porcelain 2>/dev/null || true)"
                if [[ -n "${dirty}" && "${force}" != "true" ]]; then
                    err "Working tree has uncommitted changes. Commit, stash, or use c-cord update -f."
                    exit 1
                fi
                if [[ -n "${dirty}" ]]; then
                    warn "Working tree has uncommitted changes; proceeding (-f). checkout may fail."
                fi
                try_git="true"
            fi

            if [[ "${try_git}" == "true" ]]; then
                info "Fetching and checking out ${tag}..."
                local fetch_ok="false"
                # '+' allows updating local tag ref when moving to an older release (non-FF tag moves).
                if git -C "${SCRIPT_DIR}" fetch -q origin "+refs/tags/${tag}:refs/tags/${tag}"; then
                    fetch_ok="true"
                else
                    warn "git fetch failed for tag ${tag}; will try release archive."
                fi
                if [[ "${fetch_ok}" == "true" ]]; then
                    if git -C "${SCRIPT_DIR}" checkout -q "${tag}"; then
                        vcs_summary="release: ${tag} (git)"
                        applied="true"
                    else
                        warn "git checkout ${tag} failed; will try release archive."
                    fi
                fi
            fi

            if [[ "${applied}" != "true" ]]; then
                info "Applying GitHub release archive for ${tag}..."
                if GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
                    "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/ccord_release_apply.py" \
                    "${SCRIPT_DIR}" --json-line "${release_json}"; then
                    vcs_summary="release: ${tag} (archive)"
                    applied="true"
                else
                    if [[ "${force}" == "true" ]]; then
                        warn "Release archive apply failed — continuing anyway (-f)."
                        vcs_summary="release: archive failed (ignored)"
                    else
                        err "Could not update to ${tag} via git or release archive."
                        exit 1
                    fi
                fi
            fi
        fi
    fi

    info "Updating dependencies..."
    "${PIP_BIN}" install -r "${SCRIPT_DIR}/requirements.txt" --quiet

    if [[ "${force}" == "true" ]]; then
        _start_bot -f
    else
        _check_syntax
        _start_bot
    fi

    echo ""
    ok "Update summary:"
    echo "  - Previously running: ${was_running}"
    echo "  - VCS               : ${vcs_summary}"
    echo "  - Dependencies      : updated"
    echo "  - Bot state         : running"
}

# ── repair ───────────────────────────────────────────────────────────────────
# Same as: (cd repo && "${py}" scripts/repair_clone.py "$@") — exec so argv/exit match the script.
# Default: git fetch + restore missing tracked files, then storage repair. Use --storage-only for JSON only.
# Flags: [--repair] [--storage-only] --dry-run --exclude … | [--remote] [--ref] [--no-fetch] [--no-storage]
_repair_storage() {
    local py_cmd="${PYTHON_BIN}"
    if [[ ! -x "${py_cmd}" ]]; then
        warn "Venv python missing; using python3 for repair."
        py_cmd="python3"
    fi
    if [[ ! -f "${SCRIPT_DIR}/scripts/repair_clone.py" ]]; then
        err "Missing ${SCRIPT_DIR}/scripts/repair_clone.py"
        exit 1
    fi
    cd "${SCRIPT_DIR}" || exit 1
    exec "${py_cmd}" "./scripts/repair_clone.py" "$@"
}

# ── module refresh ────────────────────────────────────────────────────────────
# Flags:
#   --dry-run   Show what would be added without writing to modules.json.
_module_refresh() {
    local dry_run="false"
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            --dry-run) dry_run="true" ;;
            *)
                err "module refresh: unknown argument \"${1}\" — only --dry-run is allowed."
                exit 1
                ;;
        esac
        shift
    done

    _check_venv
    _ensure_runtime_dirs

    local result added total py_dry_arg=""
    [[ "${dry_run}" == "true" ]] && py_dry_arg="dry_run=True" || py_dry_arg="dry_run=False"

    if result="$(cd "${SCRIPT_DIR}" && "${PYTHON_BIN}" -c "
from Modules.module_registry import refresh_registry
a, t = refresh_registry(${py_dry_arg})
print(a, t)
" 2>&1)"; then
        read -r added total <<< "${result}"
        if [[ "${dry_run}" == "true" ]]; then
            info "Dry run: ${added} new module(s) would be added (${total} total)."
            if (( added > 0 )); then
                info "Run without --dry-run to register them."
            fi
        else
            ok "Module registry refreshed — added ${added} new module(s), ${total} total."
            if (( added > 0 )); then
                info "Run c-cord restart to load the new module(s)."
            fi
        fi
    else
        err "Module refresh failed:"
        echo "${result}" >&2
        exit 1
    fi
}

main() {
    local cmd="${1:-}"
    shift || true

    case "${cmd}" in
        start)
            _start_bot "$@"
            ;;
        stop)
            _stop_bot "$@"
            ;;
        restart)
            _stop_bot
            _start_bot "$@"
            ;;
        status)
            _status_bot "$@"
            ;;
        logs)
            _logs_bot "$@"
            ;;
        console)
            _console_bot "$@"
            ;;
        update)
            _update_bot "$@"
            ;;
        repair)
            _repair_storage "$@"
            ;;
        module)
            local subcmd="${1:-}"
            shift || true
            case "${subcmd}" in
                refresh|refresh_registry)
                    _module_refresh "$@"
                    ;;
                "")
                    echo "Usage: c-cord module <refresh|refresh_registry> [--dry-run]"
                    exit 1
                    ;;
                *)
                    err "Unknown module subcommand: '${subcmd}'"
                    echo "Available: refresh, refresh_registry"
                    exit 1
                    ;;
            esac
            ;;
        ""|-h|--help|help)
            _usage
            ;;
        *)
            err "Unknown command: '${cmd}'"
            echo ""
            _usage
            exit 1
            ;;
    esac
}

main "$@"
