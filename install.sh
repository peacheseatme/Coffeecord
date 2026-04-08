#!/usr/bin/env bash
# =============================================================
#  Coffeecord — Install & Setup Script
#  Run this once from the project root to get the bot running.
# =============================================================
set -euo pipefail

# ── Colour helpers ───────────────────────────────────────────
RED='\033[0;31m';  GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m';     RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

# ── Repository bootstrap settings ─────────────────────────────
REPO_URL="https://github.com/peacheseatme/Discordbot.git"
REPO_DIR_NAME="Discordbot"

# ── Locate the project root (the directory containing this script) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Bootstrap clone if installer is not running from a repo clone ──
if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   || [ ! -f "$SCRIPT_DIR/Src/Bot.py" ] \
   || [ ! -f "$SCRIPT_DIR/bot.sh" ]; then
    if ! command -v git >/dev/null 2>&1; then
        error "git is required to clone the repository but was not found."
        exit 1
    fi

    TARGET_DIR="${COFFEECORD_INSTALL_DIR:-$HOME/$REPO_DIR_NAME}"
    PARENT_DIR="$(dirname "$TARGET_DIR")"
    mkdir -p "$PARENT_DIR"

    echo ""
    header "Bootstrap — Preparing repository"

    if [ -d "$TARGET_DIR/.git" ]; then
        info "Repository already cloned at $TARGET_DIR"
    else
        info "Cloning repository to $TARGET_DIR"
        git clone "$REPO_URL" "$TARGET_DIR"
        success "Repository cloned."
    fi

    info "Re-running installer from cloned repository..."
    exec bash "$TARGET_DIR/install.sh" "$@"
fi

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║     Coffeecord  —  Setup Wizard      ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════╝${RESET}"
echo ""

# ──────────────────────────────────────────────────────────────
# 1. Python version check (requires 3.12+)
# ──────────────────────────────────────────────────────────────
header "Step 1 — Checking Python version"

PYTHON_CMD=""
for cmd in python3.12 python3.13 python3.14 python3; do
    if command -v "$cmd" &>/dev/null; then
        VER="$($cmd -c 'import sys; print(sys.version_info[:2])')"
        MAJOR=$($cmd -c 'import sys; print(sys.version_info[0])')
        MINOR=$($cmd -c 'import sys; print(sys.version_info[1])')
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    error "Python 3.12 or newer is required but was not found."
    error "Install it from https://www.python.org/downloads/ and re-run this script."
    exit 1
fi

PYTHON_VERSION="$($PYTHON_CMD --version 2>&1)"
success "Found $PYTHON_VERSION  →  using \`$PYTHON_CMD\`"

# ──────────────────────────────────────────────────────────────
# 2. Create virtual environment
# ──────────────────────────────────────────────────────────────
header "Step 2 — Setting up virtual environment"

VENV_DIR="$SCRIPT_DIR/.venv"

if [ -d "$VENV_DIR" ]; then
    warn "Virtual environment already exists at .venv/ — skipping creation."
    warn "Delete .venv/ and re-run to rebuild it from scratch."
else
    info "Creating virtual environment at .venv/ ..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    success "Virtual environment created."
fi

# Activate the venv for the rest of this script
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
success "Virtual environment activated."

# ──────────────────────────────────────────────────────────────
# 3. Install / upgrade pip, then install dependencies
# ──────────────────────────────────────────────────────────────
header "Step 3 — Installing dependencies"

info "Upgrading pip..."
pip install --upgrade pip --quiet

if [ ! -f "$SCRIPT_DIR/requirements.txt" ]; then
    error "requirements.txt not found in project root ($SCRIPT_DIR)."
    exit 1
fi

info "Installing packages from requirements.txt  (this may take a minute)..."
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
success "All dependencies installed."

if ! python -c 'import pkg_resources' 2>/dev/null; then
    warn "pkg_resources missing (setuptools too new). Installing compatible version..."
    pip install 'setuptools>=69.0,<75' --quiet
    if python -c 'import pkg_resources' 2>/dev/null; then
        success "pkg_resources restored."
    else
        error "Could not restore pkg_resources. pet-pet-gif may fail at runtime."
    fi
fi

# ──────────────────────────────────────────────────────────────
# 4. Create Storage directories and generate placeholder config/data
# ──────────────────────────────────────────────────────────────
header "Step 4 — Verifying Storage directory structure"

for dir in \
    Storage/Config \
    Storage/Data \
    Storage/Temp \
    Storage/Backups \
    Storage/Assets \
    Storage/Temp/level_cache \
    Storage/Data/ticket_transcripts \
    Storage/Config/theme_storage \
    Main
do
    if [ ! -d "$SCRIPT_DIR/$dir" ]; then
        mkdir -p "$SCRIPT_DIR/$dir"
        success "Created  $dir/"
    fi
done

info "Generating Storage placeholder files (if missing)..."
if [ -f "$SCRIPT_DIR/scripts/generate_storage_placeholders.py" ]; then
    "$PYTHON_CMD" "$SCRIPT_DIR/scripts/generate_storage_placeholders.py" || true
else
    warn "scripts/generate_storage_placeholders.py not found; Storage files may need manual creation."
fi
success "Storage directories are ready."

# ──────────────────────────────────────────────────────────────
# 5. Configure environment variables (.env)
# ──────────────────────────────────────────────────────────────
header "Step 5 — Bot configuration"

ENV_FILE="$SCRIPT_DIR/Src/.env"

# ── Helper: read a value, optionally with a default ──
prompt_value() {
    local label="$1"
    local var_name="$2"
    local default_val="${3:-}"
    local current_val="${4:-}"
    local is_secret="${5:-false}"

    local display_default=""
    if [ -n "$default_val" ]; then
        display_default="  [default: $default_val]"
    fi
    local display_current=""
    if [ -n "$current_val" ]; then
        if [ "$is_secret" = "true" ]; then
            display_current="  [current: ****${current_val: -4}]"
        else
            display_current="  [current: $current_val]"
        fi
    fi

    echo -e "${YELLOW}  ➜  ${BOLD}${label}${RESET}${display_current}${display_default}"
    if [ "$is_secret" = "true" ]; then
        read -r -s -p "    Enter value (leave blank to keep current): " INPUT
        echo ""
    else
        read -r -p "    Enter value (leave blank to keep current): " INPUT
    fi

    # Return the entered value or keep the default/current
    if [ -n "$INPUT" ]; then
        eval "$var_name='$INPUT'"
    elif [ -n "$current_val" ]; then
        eval "$var_name='$current_val'"
    elif [ -n "$default_val" ]; then
        eval "$var_name='$default_val'"
    else
        eval "$var_name=''"
    fi
}

# ── Load existing Discord token from .env if it exists ──
EXISTING_TOKEN=""

if [ -f "$ENV_FILE" ]; then
    info "Found existing Src/.env — loading current token."
    while IFS='=' read -r KEY VALUE; do
        VALUE="${VALUE%\"}"
        VALUE="${VALUE#\"}"
        VALUE="${VALUE%\'}"
        VALUE="${VALUE#\'}"
        case "$KEY" in
            DISCORD_TOKEN) EXISTING_TOKEN="$VALUE" ;;
        esac
    done < <(grep -E '^[A-Z_]+=.*' "$ENV_FILE" 2>/dev/null || true)
fi

echo ""

# Discord token (required)
DISCORD_TOKEN=""
while [ -z "$DISCORD_TOKEN" ]; do
    prompt_value "Discord Bot Token" "DISCORD_TOKEN" "" "$EXISTING_TOKEN" "true"
    if [ -z "$DISCORD_TOKEN" ]; then
        error "  Discord token is required. Please enter it."
    fi
done

# ── Write .env file — Ko-fi lines are commented out by default ──
info "Writing Src/.env ..."
{
    echo "DISCORD_TOKEN=$DISCORD_TOKEN"
    echo ""
    echo "# ── Ko-fi integration (disabled by default) ────────────────"
    echo "# To enable Ko-fi supporter perks:"
    echo "#   1. Uncomment the two lines below and fill in your token."
    echo "#   2. Restart the bot."
    echo "#   3. Point your Ko-fi webhook URL to: https://<your-domain>/kofi"
    echo "#"
    echo "# KOFI_VERIFICATION_TOKEN=your_token_here"
    echo "# KOFI_PORT=5000"
    echo ""
    echo "# ── Optional: branding / owner (see env.example in repo root) ──"
    echo "# DISCORD_APPLICATION_ID=   # for Invite + top.gg URLs in /about"
    echo "# COFFEECORD_OWNER_ID=      # your user ID for /about + owner commands"
    echo "# COFFEECORD_SUPPORT_SERVER_URL="
    echo "# COFFEECORD_SUPPORT_INVITE="
} > "$ENV_FILE"
success "Src/.env written."

# ──────────────────────────────────────────────────────────────
# 6. Install the c-cord CLI command
# ──────────────────────────────────────────────────────────────
header "Step 6 — Installing c-cord command"

BIN_DIR="$HOME/.local/bin"
CMD_PATH="$BIN_DIR/c-cord"
BOT_SH_PATH="$SCRIPT_DIR/bot.sh"

mkdir -p "$BIN_DIR"
chmod +x "$BOT_SH_PATH"

cat > "$CMD_PATH" <<WRAPPER
#!/usr/bin/env bash
exec "$BOT_SH_PATH" "\$@"
WRAPPER
chmod +x "$CMD_PATH"
success "Installed c-cord  →  $CMD_PATH"

# Add ~/.local/bin to PATH in shell config if it isn't already on PATH
_ensure_path_entry() {
    local shell_rc="$1"
    local export_line='export PATH="$HOME/.local/bin:$PATH"'
    if [[ -f "$shell_rc" ]] && grep -qF '.local/bin' "$shell_rc" 2>/dev/null; then
        return 0
    fi
    echo "" >> "$shell_rc"
    echo "# Added by Coffeecord installer" >> "$shell_rc"
    echo "$export_line" >> "$shell_rc"
}

PATH_ADDED_TO=""
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.zshrc"; do
        if [[ -f "$rc" ]]; then
            _ensure_path_entry "$rc"
            PATH_ADDED_TO="$rc"
            break
        fi
    done
    if [[ -z "$PATH_ADDED_TO" ]]; then
        _ensure_path_entry "$HOME/.bashrc"
        PATH_ADDED_TO="$HOME/.bashrc"
    fi
    export PATH="$HOME/.local/bin:$PATH"
    if [[ -n "$PATH_ADDED_TO" ]]; then
        info "Added ~/.local/bin to PATH in $PATH_ADDED_TO"
    fi
fi

# ──────────────────────────────────────────────────────────────
# 7. Syntax-check the bot files
# ──────────────────────────────────────────────────────────────
header "Step 7 — Syntax check"

FILES_OK=true
for pyfile in Src/Bot.py Modules/automod.py Modules/tickets.py Modules/leveling.py; do
    if [ -f "$SCRIPT_DIR/$pyfile" ]; then
        if python -m py_compile "$SCRIPT_DIR/$pyfile" 2>/dev/null; then
            success "$pyfile"
        else
            error "$pyfile  ← syntax error"
            FILES_OK=false
        fi
    fi
done

if [ "$FILES_OK" = false ]; then
    error "One or more Python files have syntax errors. Fix them before starting the bot."
    exit 1
fi

# ──────────────────────────────────────────────────────────────
# 8. Done — print launch instructions
# ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           Setup Complete! ✓          ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Run the bot from anywhere:${RESET}"
echo ""
echo -e "    ${CYAN}c-cord start${RESET}                — Start bot in the background"
echo -e "    ${CYAN}c-cord start -f${RESET}             — Start, ignore non-fatal errors"
echo -e "    ${CYAN}c-cord stop${RESET}                 — Graceful stop"
echo -e "    ${CYAN}c-cord stop -9${RESET}              — Hard stop (SIGKILL)"
echo -e "    ${CYAN}c-cord restart${RESET}              — Stop then start"
echo -e "    ${CYAN}c-cord status${RESET}               — PID and uptime"
echo -e "    ${CYAN}c-cord status -v${RESET}            — Status + recent log lines"
echo -e "    ${CYAN}c-cord logs${RESET}                 — Follow log live"
echo -e "    ${CYAN}c-cord logs -n 50${RESET}           — Last 50 lines"
echo -e "    ${CYAN}c-cord update${RESET}               — latest GitHub release → deps → restart"
echo -e "    ${CYAN}c-cord update 1.0.3${RESET}         — pin to that release"
echo -e "    ${CYAN}c-cord update --branch${RESET}      — git pull (dev on main)"
echo -e "    ${CYAN}c-cord update -f${RESET}            — continue if git step fails"
echo -e "    ${CYAN}c-cord module refresh${RESET}       — Register new drop-in modules"
echo -e "    ${CYAN}c-cord module refresh_registry${RESET} — Alias for module refresh"
echo -e "    ${CYAN}c-cord module refresh --dry-run${RESET}  — Preview without writing"
echo ""

if [[ -n "$PATH_ADDED_TO" ]] && [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo -e "  ${YELLOW}NOTE: Restart your terminal (or run ${CYAN}source $PATH_ADDED_TO${YELLOW})"
    echo -e "  for the ${CYAN}c-cord${YELLOW} command to be available in new sessions.${RESET}"
    echo ""
fi

echo -e "  ${BOLD}Ko-fi integration (disabled by default):${RESET}"
echo -e "    Run ${CYAN}./scripts/add_kofi.sh${RESET} or edit ${CYAN}Src/.env${RESET} manually,"
echo -e "    then run ${CYAN}c-cord restart${RESET}."
echo ""
echo -e "  ${BOLD}Invite the bot to your server:${RESET}"
echo -e "    ${CYAN}https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=8&scope=bot+applications.commands${RESET}"
echo ""
