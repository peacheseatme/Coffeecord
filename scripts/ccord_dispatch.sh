#!/usr/bin/env bash
# c-cord multi-instance dispatcher (installed to ~/.local/bin/c-cord).
# __COFFEECORD_SCRIPTS_DIR__ is replaced at install time with this repo's scripts/ path.
set -euo pipefail

CCORD_SCRIPTS='__COFFEECORD_SCRIPTS_DIR__'
REG_PY="${CCORD_SCRIPTS}/ccord_registry.py"
PY="${PYTHON:-python3}"

err() { echo "c-cord: $*" >&2; }

_usage() {
  cat <<'EOF'
Usage:
  c-cord list
  c-cord add <id> <path-to-clone>
  c-cord remove <id>
  c-cord default <id>
  c-cord new <id> [--path DIR] [--repo URL]   (flags may be before or after <id>)

  c-cord <start|stop|restart|status|logs|console|update> [id|all] [args...]
    id   — instance id from "c-cord list" (omit for default)
    all  — run for every registered instance

  c-cord module <refresh|refresh_registry> [args...]   (default instance only)

Other commands are forwarded to the selected clone's bot.sh.

Misconfiguration, typos, tokens, git remotes, and network issues are yours to fix.
EOF
}

_require_registry_py() {
  if [[ ! -f "${REG_PY}" ]]; then
    err "missing ${REG_PY} — re-run install.sh from a Coffeecord clone."
    exit 1
  fi
}

_collect_roots() {
  local spec="$1"
  _require_registry_py
  if ! mapfile -t ROOTS < <("${PY}" "${REG_PY}" resolve "${spec}"); then
    err "resolve ${spec} failed — fix your registry (c-cord list) or paths under ~/.config/c-cord/instances.json"
    return 1
  fi
  if [[ ${#ROOTS[@]} -eq 0 ]]; then
    err "no roots from resolve ${spec}"
    return 1
  fi
}

_run_bot_each() {
  local spec="$1"
  shift
  _collect_roots "${spec}" || exit 1
  local r rc=0
  for r in "${ROOTS[@]}"; do
    if [[ ! -x "${r}/bot.sh" ]]; then
      err "missing or not executable: ${r}/bot.sh"
      rc=1
      continue
    fi
    echo "==> [${r}] $*"
    if ! (cd "${r}" && "${r}/bot.sh" "$@"); then
      rc=1
    fi
  done
  return "${rc}"
}

_root_default() {
  _require_registry_py
  "${PY}" "${REG_PY}" resolve default
}

_cmd_new() {
  local dest="" repo="${COFFEECORD_REPO_URL:-https://github.com/peacheseatme/Coffeecord.git}"
  local positional=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --path)
        shift
        if [[ $# -lt 1 ]]; then
          err "usage: c-cord new <id> [--path DIR] [--repo URL]"
          exit 1
        fi
        dest="$1"
        shift
        ;;
      --repo)
        shift
        if [[ $# -lt 1 ]]; then
          err "usage: c-cord new <id> [--path DIR] [--repo URL]"
          exit 1
        fi
        repo="$1"
        shift
        ;;
      -*)
        err "unknown option: $1"
        exit 1
        ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done
  if [[ ${#positional[@]} -eq 0 ]]; then
    err "usage: c-cord new <id> [--path DIR] [--repo URL]"
    err "  (flags may go before or after <id>; id is one word, e.g. mybot or 2 — not \"new update mybot\")"
    exit 1
  fi
  if [[ ${#positional[@]} -gt 1 ]]; then
    err "too many id words: ${positional[*]}"
    err "  use a single instance id, e.g.:  c-cord new mybot --path ~/coffeecord2"
    exit 1
  fi
  local id="${positional[0]}"
  if [[ -z "${dest}" ]]; then
    dest="${HOME}/coffeecord-${id}"
  fi
  if [[ -e "${dest}" ]]; then
    err "path already exists: ${dest}"
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    err "git is required for c-cord new"
    exit 1
  fi
  echo "c-cord: cloning ${repo} -> ${dest}"
  git clone "${repo}" "${dest}"
  if [[ ! -f "${dest}/install.sh" ]]; then
    err "clone has no install.sh — check --repo URL"
    exit 1
  fi
  (cd "${dest}" && bash install.sh)
  _require_registry_py
  "${PY}" "${REG_PY}" add "${id}" "${dest}"
  echo "c-cord: instance [${id}] ready at ${dest}"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "${cmd}" in
    ""|-h|--help|help)
      _usage
      exit 0
      ;;
    list)
      _require_registry_py
      "${PY}" "${REG_PY}" list
      ;;
    add)
      _require_registry_py
      if [[ $# -lt 2 ]]; then
        err "usage: c-cord add <id> <path-to-clone>"
        exit 1
      fi
      "${PY}" "${REG_PY}" add "$1" "$2"
      ;;
    remove)
      _require_registry_py
      if [[ $# -lt 1 ]]; then
        err "usage: c-cord remove <id>"
        exit 1
      fi
      "${PY}" "${REG_PY}" remove "$1"
      ;;
    default)
      _require_registry_py
      if [[ $# -lt 1 ]]; then
        err "usage: c-cord default <id>"
        exit 1
      fi
      "${PY}" "${REG_PY}" default "$1"
      ;;
    new)
      _cmd_new "$@"
      ;;
    module)
      local root
      root="$(_root_default)" || exit 1
      if [[ ! -x "${root}/bot.sh" ]]; then
        err "missing bot.sh under ${root}"
        exit 1
      fi
      exec "${root}/bot.sh" module "$@"
      ;;
    start|stop|restart|status|logs|console)
      local spec="default"
      if [[ $# -ge 1 ]]; then
        if [[ "$1" == "all" ]]; then
          spec="all"
          shift
        elif [[ "$1" =~ ^- ]]; then
          :
        else
          _require_registry_py
          if "${PY}" "${REG_PY}" has-id "$1"; then
            spec="$1"
            shift
          else
            err "unknown instance id: $1 — run c-cord list (first token must be all, an id, or a flag like -f)"
            exit 1
          fi
        fi
      fi
      _run_bot_each "${spec}" "${cmd}" "$@"
      ;;
    update)
      local spec="default"
      if [[ $# -ge 1 ]]; then
        if [[ "$1" == "all" ]]; then
          spec="all"
          shift
        elif [[ "$1" =~ ^- ]]; then
          :
        else
          _require_registry_py
          if "${PY}" "${REG_PY}" has-id "$1"; then
            spec="$1"
            shift
          fi
        fi
      fi
      _run_bot_each "${spec}" "${cmd}" "$@"
      ;;
    *)
      err "unknown command: ${cmd}"
      echo ""
      _usage
      exit 1
      ;;
  esac
}

main "$@"
