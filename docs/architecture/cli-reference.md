# CLI Reference (bot.sh / c-cord)

The bot is controlled via `bot.sh` or the `c-cord` symlink.

## Commands

| Command | Description |
|---------|-------------|
| `start` | Start bot in background |
| `start -f` / `start --force` | Start, ignore non-fatal errors |
| `stop` | Graceful stop (SIGTERM, then SIGKILL if needed) |
| `stop -9` / `stop --kill` | Hard stop (SIGKILL immediately) |
| `restart` | stop + start |
| `restart -f` | stop + start with force |
| `status` | Show PID and uptime |
| `status -v` / `status --verbose` | Status + last 10 log lines |
| `logs` | Follow log (tail -f) |
| `logs -n N` | Last N lines, no follow |
| `console` | Live console — tail bot log (commands, errors, etc.) |
| `console -n N` | Last N lines, no follow |
| `console clear` | Clear the bot log file |
| `update` | Latest **GitHub release** tag → fetch/checkout → pip install → restart |
| `update <version>` | Same, pinned to that release (e.g. `1.0.3` or `v1.0.3`) |
| `update --branch` | `git pull` on current branch (for development on `main`) → pip → restart |
| `update -f` | Continue even if resolve/fetch/checkout/pull fails |
| `module refresh` | Scan Modules/, add new files to registry |
| `module refresh_registry` | Alias for module refresh |
| `module refresh --dry-run` | Preview additions without writing |

## Config file

`Storage/Config/c-cord.json` optionally overrides paths and limits for `c-cord start` / `restart`:

| Key | Default | Description |
|-----|---------|-------------|
| `bot_entry` | `Src/Bot.py` | Bot entry script |
| `env_file` | `Src/.env` | Environment file |
| `log_dir` | `Storage/Logs` | Log directory |
| `temp_dir` | `Storage/Temp` | Temp directory |
| `ticket_env_file` | `Src/ticket.env` | Ticket config |
| `max_log_bytes` | `10485760` | Rotate log when larger (bytes) |
| `max_rotated` | `5` | Max rotated log files to keep |
| `ngrok_enabled` | `true` | Start ngrok with bot when Ko-fi is configured |
| `kofi_webhook_host` | *(none)* | Your ngrok host (e.g. `xxx.ngrok-free.dev`) — used to display the Ko-fi webhook URL on start |
| `github_repo` | *(from `git remote`)* | `owner/repo` for GitHub Releases API when `origin` is not github.com or you track upstream releases from another fork |

Paths are relative to the project root unless absolute. The config is loaded automatically when present.

### `c-cord update` (releases)

Default update uses the [GitHub Releases API](https://docs.github.com/en/rest/releases/releases) on the repo inferred from `git remote get-url origin` (HTTPS or `git@github.com:...`), unless `github_repo` is set in `c-cord.json`. Requires a **git clone** (not a plain zip). The working tree must be clean unless you pass `-f`.

After a release checkout the repo is in a **detached HEAD** state at the release tag — expected for production installs. To follow `main` again, use `c-cord update --branch` or `git checkout main && git pull`.

Unauthenticated API calls are rate-limited (about 60/hour per IP). For private repositories or heavier use, set environment variable `GITHUB_TOKEN` (fine-grained or classic PAT with `Contents: Read`) when running `c-cord update`.

If the latest release cannot be resolved (e.g. no published releases), the helper falls back to the newest **local** semver-looking tag after you run `git fetch --tags`; that may include tags that were never published as GitHub “releases.”

## ngrok (Ko-fi webhooks)

When `KOFI_VERIFICATION_TOKEN` is set in `Src/.env`, `c-cord start` and `restart` automatically:

1. Install ngrok if missing (download to `Storage/Tools/` or try `snap install`)
2. Start ngrok to expose `KOFI_PORT` (default 5000)
3. Stop ngrok when `c-cord stop` runs

Set `ngrok_enabled: false` in `Storage/Config/c-cord.json` to run ngrok manually. Run `ngrok config add-authtoken <token>` once after installing.

Add `kofi_webhook_host` (your ngrok host, e.g. `postmyxedematous-meadow-unswaggering.ngrok-free.dev`) to display the full webhook URL when ngrok starts.

## Paths

- **Entry**: `Src/Bot.py` (overridable via config)
- **Env**: `Src/.env` (DISCORD_TOKEN, etc.)
- **Logs**: `Storage/Logs/bot.log`
- **PID**: `Storage/Temp/bot.pid`
- **Venv**: `.venv/`

## Ko-fi setup

Use the helper script to add Ko-fi webhook configuration:

```bash
./scripts/add_kofi.sh
```

This prompts for `KOFI_VERIFICATION_TOKEN` and `KOFI_PORT`, updates `Src/.env`, and prints next steps. Then run `c-cord restart` — ngrok starts automatically.

## Module refresh

```bash
c-cord module refresh
```

Scans `Modules/*.py`, excludes `module_registry` and `kofi_webhook`, and appends any new modules to `Storage/Config/modules.json`. Use `--dry-run` to see what would be added without writing.
