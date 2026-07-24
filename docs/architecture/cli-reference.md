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
| `console` | Interactive host REPL — run slash/owner commands against the live bot |
| `update` | Latest **GitHub Release** → git checkout if possible, else zipball overlay → pip → restart |
| `update <version>` | Same, pinned to that release tag (e.g. `1.0.3` or `v1.0.3`) |
| `update --branch` | `git pull` on current branch (for development on `main`) → pip → restart |
| `update -f` | Continue even if resolve/git/archive/pull fails |
| `publish-release <ver>` | Maintainer: push branch + create tag `v<ver>` (for `c-cord update`) |
| `publish-release <ver> -f` | Same, but move/overwrite an existing tag and force-push it |
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

Default update uses the [GitHub Releases API](https://docs.github.com/en/rest/releases/releases) on the repo inferred from `git remote get-url origin` (HTTPS or `git@github.com:...`), unless `github_repo` is set in `c-cord.json`. Publishing a Release on a tag (e.g. `1.0.4` / `v1.0.4`) is enough — custom release assets are optional.

**Fallback order:** resolve the release tag → if this is a git clone with a clean tree, `git fetch` + `git checkout` that tag → otherwise (or if git fails) download GitHub’s auto **zipball** for the tag and overlay it onto the install root. Overlay never replaces `Storage/`, `.venv/`, `Src/.env`, or `Src/ticket.env`. Draft/prerelease tags are not treated as “latest” (`/releases/latest` already excludes them).

Works without a `.git` directory when `github_repo` (or `GITHUB_REPO`) identifies `owner/repo`. For git checkouts, the working tree must be clean unless you pass `-f`. After a successful git release checkout the repo is in a **detached HEAD** at the tag — expected for production. To follow `main` again, use `c-cord update --branch` or `git checkout main && git pull`.

Optional uploaded asset: if you attach a file named exactly `coffeecord-release.zip`, the resolver reports it as `asset_url`; the updater still prefers the automatic zipball unless you change tooling later.

Unauthenticated API calls are rate-limited (about 60/hour per IP). For private repositories or heavier use, set environment variable `GITHUB_TOKEN` (fine-grained or classic PAT with `Contents: Read`) when running `c-cord update`.

If the latest release cannot be resolved (e.g. no published releases) on a git clone, the helper falls back to the newest **local** semver-looking tag after you run `git fetch --tags`; that may include tags that were never published as GitHub “releases.”

### `c-cord publish-release` (maintainers)

From a clean git clone on `main`:

```bash
c-cord publish-release 1.0.5          # push main + create tag v1.0.5
c-cord publish-release 1.0.4 -f       # move tag v1.0.4 to HEAD and force-push
```

Also available as `./scripts/ccord_publish_release.sh`. If `gh` is logged in, it creates/updates the GitHub Release so zipball updates work; otherwise publish the Release in the GitHub UI after the tag push.

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

## Host console REPL

`c-cord console` opens an interactive shell on the host machine. Commands run against the **already-running** bot process (same Discord session) via a local Unix socket at `Storage/Temp/console.sock`.

| REPL input | Description |
|--------------|-------------|
| `help` | List slash commands |
| `commands` | Every slash command with full usage syntax |
| `help ban` | Describe one command (parameters, types) |
| `servers` | Guild ids for `server:<id>` |
| `server info server:<id>` | Export roles, members, invites, channels, perms to `Storage/Temp/server-info-…/` |
| `ban server:138477 user:stan425 reason:spam time:1h` | One-line command (no pickers) |
| `synccommands` / `.dev banuser …` | Owner prefix commands |
| `ping` | Bot ready state, guild count, latency |
| `exit` / `quit` | Leave the REPL |

Arg syntax: `key:value` (also `key=value`, `:user@name`, quoted values). Examples:

```text
ban server:138477 user:stan425 reason:spam time:1h
colorrole clear server:138477
server info server:1384771470860746753
```

Missing required args print a usage line instead of interactive pickers. Run `commands` for every command’s syntax; `servers` for guild ids.

**Security:** Authentication uses `HOST_CONSOLE_TOKEN` in `Src/ticket.env` (auto-generated on first start). The socket is filesystem-local only — not exposed to the network. Commands execute as the configured owner (`COFFEECORD_OWNER_ID`).

**Limitations:** Commands that open Discord UI (Views/Modals), such as `/colorrole setup` or `/setup`, must be run in Discord. Use `c-cord logs` for log tailing (not `console`).

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
