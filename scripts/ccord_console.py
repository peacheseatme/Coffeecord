#!/usr/bin/env python3
"""
Interactive host console REPL — talks to the running bot over a local Unix socket.
"""

from __future__ import annotations

import json
import os
import re
import readline  # noqa: F401 — enables line editing when available
import socket
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOCKET_PATH = BASE_DIR / "Storage" / "Temp" / "console.sock"
ENV_PATH = BASE_DIR / "Src" / ".env"
TICKET_ENV_PATH = BASE_DIR / "Src" / "ticket.env"
PID_FILE = BASE_DIR / "Storage" / "Temp" / "bot.pid"

TOKEN_ENV_KEY = "HOST_CONSOLE_TOKEN"
PROMPT = "> "

GUILD_ARG_KEYS = frozenset({"guild", "guild_id", "server"})
MEMBER_ARG_KEYS = frozenset({"member", "user"})
ROLE_ARG_KEYS = frozenset({"role"})
CHANNEL_ARG_KEYS = frozenset({"channel"})
DURATION_ARG_KEYS = frozenset({"time", "duration", "duration_minutes"})

# key:value, key=value, or :key@value (e.g. user:stan425, :user@stan425, server:138477)
_ARG_TOKEN_RE = re.compile(
    r'(?::)?(?P<key>[\w.-]+)(?P<sep>[:=@])(?P<val>"[^"]*"|\'[^\']*\'|[^\s]+)',
    re.IGNORECASE,
)

HELP_TEXT = """Coffeecord host console — one-line commands (no interactive pickers).

Meta:
  help              Short help
  commands          Every slash command with full arg syntax
  servers           Guild ids for server:<id>
  server info       Export guild snapshot to Storage/Temp/ (roles, members, …)
  help <command>    Params for one command + usage line
  ping              Bot status
  exit / quit       Leave

Run commands (all context on the line — like a shell):
  ban server:1384771470860746753 user:stan425 reason:spam time:1h
  server info server:1384771470860746753
  colorrole clear server:1384771470860746753
  modules status server:1384771470860746753
  synccommands

Arg syntax:
  server:<id>       Required for guild-scoped commands (see `servers`)
  user:<name|id>    member / user params
  time:1h           duration (also 30m, 2d, or plain minutes)
  reason:"text"     quoted values allowed
  key=value         still supported

Log tailing: c-cord logs
UI-only commands (/setup, /colorrole setup, …): run in Discord.
"""


def _load_env_key(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, _, v = raw.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _console_token() -> str:
    token = os.getenv(TOKEN_ENV_KEY, "").strip()
    if token:
        return token
    token = _load_env_key(ENV_PATH, TOKEN_ENV_KEY)
    if token:
        return token
    return _load_env_key(TICKET_ENV_PATH, TOKEN_ENV_KEY)


def _socket_path() -> Path:
    raw = os.getenv("HOST_CONSOLE_SOCKET", "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_SOCKET_PATH


def _bot_pid() -> str | None:
    if not PID_FILE.is_file():
        return None
    try:
        pid = PID_FILE.read_text(encoding="utf-8").strip()
        return pid or None
    except OSError:
        return None


class ConsoleClient:
    def __init__(self, sock_path: Path, token: str) -> None:
        self.sock_path = sock_path
        self.token = token
        self._sock: socket.socket | None = None
        self._file = None
        self._req_id = 0
        self._command_names: frozenset[str] | None = None
        self._command_catalog: list[dict[str, Any]] | None = None

    def connect(self) -> None:
        if not self.sock_path.is_socket() and not self.sock_path.exists():
            raise RuntimeError(
                f"Console socket not found ({self.sock_path}). "
                "Start the bot with `c-cord start`."
            )
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.connect(str(self.sock_path))
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise RuntimeError(
                "Could not connect to the bot console. "
                "Is the bot running? Start with `c-cord start`."
            ) from exc
        self._file = self._sock.makefile("rwb", buffering=0)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._command_names = None
        self._command_catalog = None

    def _request_once(self, op: str, **payload: Any) -> dict[str, Any]:
        if self._file is None:
            raise RuntimeError("Not connected.")
        self._req_id += 1
        body = {"id": self._req_id, "op": op, "token": self.token, **payload}
        line = (json.dumps(body, ensure_ascii=True) + "\n").encode("utf-8")
        self._file.write(line)
        self._file.flush()
        raw = self._file.readline()
        if not raw:
            raise RuntimeError("Bot closed the console connection.")
        data = json.loads(raw.decode("utf-8"))
        if not data.get("ok"):
            raise RuntimeError(str(data.get("error") or "Request failed."))
        return data

    def request(self, op: str, **payload: Any) -> dict[str, Any]:
        try:
            return self._request_once(op, **payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close()
            self.connect()
            return self._request_once(op, **payload)

    def ping(self) -> str:
        data = self.request("ping")
        return (
            f"pong — ready={data.get('ready')} "
            f"guilds={data.get('guilds')} latency={data.get('latency_ms')}ms"
        )

    def list_commands(self) -> list[dict[str, str]]:
        return self.request("list_commands").get("commands") or []

    def command_names(self) -> frozenset[str]:
        if self._command_names is None:
            names = {
                str(row.get("name") or row.get("qualified") or "").strip()
                for row in self.list_commands()
            }
            self._command_names = frozenset(n for n in names if n)
        return self._command_names

    def command_catalog(self) -> list[dict[str, Any]]:
        if self._command_catalog is None:
            self._command_catalog = self.request("command_catalog").get("commands") or []
        return self._command_catalog

    def describe(self, command: str) -> dict[str, Any]:
        needle = _normalize_cmd_name(command)
        if self._command_catalog is not None:
            for entry in self._command_catalog:
                qn = str(entry.get("qualified_name") or "").strip()
                if qn.lower() == needle.lower():
                    return entry
        return self.request("describe", command=command).get("command") or {}

    def list_guilds(self) -> list[dict[str, Any]]:
        return self.request("list_guilds").get("guilds") or []

    def server_info(self, guild_id: str) -> str:
        data = self.request("server_info", guild_id=guild_id)
        return str(data.get("message") or f"Exported to {data.get('path')}")

    def execute(
        self,
        command: str,
        *,
        guild_id: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"command": command, "args": args or {}}
        if guild_id:
            payload["guild_id"] = guild_id
        data = self.request("execute", **payload)
        return str(data.get("message") or "OK")


def _strip_quotes(value: str) -> str:
    val = value.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
        return val[1:-1]
    return val


def _parse_command_line(line: str) -> tuple[list[str], dict[str, str]]:
    """Split a REPL line into command tokens and key:value / key=value args."""
    args: dict[str, str] = {}
    spans: list[tuple[int, int]] = []
    for match in _ARG_TOKEN_RE.finditer(line):
        key = match.group("key").lower().lstrip(":")
        val = _strip_quotes(match.group("val"))
        if val.startswith("@"):
            val = val[1:]
        if val:
            args[key] = val
        spans.append(match.span())

    remainder = line
    for start, end in reversed(spans):
        remainder = remainder[:start] + " " + remainder[end:]
    cmd_tokens = [t for t in remainder.split() if t]
    return cmd_tokens, args


def _normalize_cmd_name(raw: str) -> str:
    return raw.strip().lstrip("/")


def _resolve_command_name(client: ConsoleClient, tokens: list[str]) -> str | None:
    if not tokens:
        return None
    known_lower = {name.lower() for name in client.command_names()}
    for count in range(len(tokens), 0, -1):
        candidate = _normalize_cmd_name(" ".join(tokens[:count]))
        if not candidate:
            continue
        if candidate.startswith(".") or candidate in {"synccommands", "clearchache", "dev"}:
            return candidate
        if candidate.lower() in known_lower:
            return candidate
    return _normalize_cmd_name(tokens[0])


def _param_needs_guild(param: dict[str, Any]) -> bool:
    ptype = str(param.get("type") or "").lower()
    return ptype in {"member", "role", "channel", "mentionable"} or bool(param.get("guild_only"))


def _command_needs_guild(info: dict[str, Any]) -> bool:
    params = info.get("parameters") or []
    return bool(info.get("guild_only")) or any(_param_needs_guild(p) for p in params)


def _param_usage_alias(param: dict[str, Any]) -> str:
    pname = str(param.get("name") or "arg")
    ptype = str(param.get("type") or "string").lower()
    if ptype in {"member", "user", "mentionable"}:
        return "user"
    if ptype == "role":
        return "role"
    if ptype == "channel":
        return "channel"
    if param.get("duration_minutes") or pname in DURATION_ARG_KEYS or "duration" in pname.lower():
        return "time"
    return pname


def _usage_placeholder(param: dict[str, Any]) -> str:
    alias = _param_usage_alias(param)
    ptype = str(param.get("type") or "string").lower()
    if alias == "user":
        return "name-or-id"
    if alias == "role":
        return "role-name-or-id"
    if alias == "channel":
        return "channel-name-or-id"
    if alias == "time":
        return "1h"
    if ptype == "integer":
        return "number"
    if ptype == "boolean":
        return "true"
    return "value"


def _format_usage_line(command: str, info: dict[str, Any]) -> str:
    params = info.get("parameters") or []
    parts = [command]
    if _command_needs_guild(info):
        parts.append("server:<guild-id>")
    for param in params:
        alias = _param_usage_alias(param)
        placeholder = _usage_placeholder(param)
        token = f"{alias}:{placeholder}"
        if not param.get("required"):
            token = f"[{token}]"
        parts.append(token)
    return " ".join(parts)


def _map_arg_key(key: str, params: list[dict[str, Any]]) -> str | None:
    """Map user-facing arg names to slash parameter names. None = guild key."""
    lowered = key.lower()
    if lowered in GUILD_ARG_KEYS:
        return None
    if lowered in MEMBER_ARG_KEYS:
        return next(
            (p["name"] for p in params if p.get("type") in {"member", "user", "mentionable"}),
            "member",
        )
    if lowered in ROLE_ARG_KEYS:
        return next((p["name"] for p in params if p.get("type") == "role"), "role")
    if lowered in CHANNEL_ARG_KEYS:
        return next((p["name"] for p in params if p.get("type") == "channel"), "channel")
    if lowered in DURATION_ARG_KEYS:
        for pname in ("duration", "duration_minutes"):
            if any(p["name"] == pname for p in params):
                return pname
        return "duration"
    for param in params:
        if param["name"].lower() == lowered:
            return param["name"]
    return key



def _build_args_from_inline(
    inline: dict[str, str],
    params: list[dict[str, Any]],
) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for key, val in inline.items():
        if key in GUILD_ARG_KEYS:
            continue
        mapped = _map_arg_key(key, params)
        if mapped is None:
            continue
        args[mapped] = val
    return args


def _missing_required_tokens(
    info: dict[str, Any],
    guild_id: str | None,
    args: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    params = info.get("parameters") or []
    if _command_needs_guild(info) and not guild_id:
        missing.append("server:<guild-id>")
    for param in params:
        if not param.get("required"):
            continue
        if param["name"] not in args:
            alias = _param_usage_alias(param)
            missing.append(f"{alias}:<{_usage_placeholder(param)}>")
    return missing


class Repl:
    def __init__(self, client: ConsoleClient) -> None:
        self.client = client

    def _print_block(self, text: str) -> None:
        """Print multi-line output, then a blank line before the next prompt."""
        try:
            for line in str(text).splitlines():
                print(line)
            print()
        except BrokenPipeError:
            raise SystemExit(0) from None

    def _print_error(self, text: str) -> None:
        self._print_block(f"ERROR: {text}")

    def run(self) -> None:
        pid = _bot_pid()
        header = "Coffeecord host console"
        if pid:
            header += f" (PID {pid})"
        print(header)
        print("One-line commands — server:<id> user:<name> …  (see `commands`, `servers`, `help`)")
        print()
        while True:
            try:
                line = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            try:
                if cmd in {"exit", "quit"}:
                    break
                if cmd == "help":
                    self._cmd_help(parts[1:] if len(parts) > 1 else [])
                elif cmd == "commands":
                    self._cmd_commands()
                elif cmd == "servers":
                    self._cmd_servers()
                elif cmd == "server" and len(parts) >= 2 and parts[1].lower() == "info":
                    _, inline = _parse_command_line(line)
                    self._cmd_server_info(inline)
                elif cmd == "ping":
                    self._print_block(self.client.ping())
                elif cmd == "cmd":
                    if len(parts) < 2:
                        self._print_error("Usage: cmd <command> [key:value …]")
                        continue
                    cmd_tokens, inline = _parse_command_line(" ".join(parts[1:]))
                    if not cmd_tokens:
                        cmd_tokens = [p for p in parts[1:] if p]
                        inline = {}
                    self._cmd_execute(cmd_tokens, inline)
                else:
                    cmd_tokens, inline = _parse_command_line(line)
                    self._cmd_execute(cmd_tokens, inline)
            except RuntimeError as exc:
                self._print_error(str(exc))
            except Exception as exc:
                self._print_error(f"{type(exc).__name__}: {exc}")

    def _cmd_servers(self) -> None:
        guilds = self.client.list_guilds()
        if not guilds:
            self._print_block("No guilds visible to the bot.")
            return
        lines = ["Guild ids for server:<id> —"]
        for guild in sorted(guilds, key=lambda g: str(g.get("name", "")).lower()):
            lines.append(
                f"  {guild['id']}  {guild.get('name', '?')} ({guild.get('members', '?')} members)"
            )
        self._print_block("\n".join(lines))

    def _cmd_server_info(self, inline: dict[str, str]) -> None:
        guild_id = inline.get("server") or inline.get("guild") or inline.get("guild_id")
        if not guild_id:
            self._print_error("Usage: server info server:<guild-id>")
            return
        self._print_block(self.client.server_info(str(guild_id)))

    def _cmd_commands(self) -> None:
        catalog = self.client.command_catalog()
        if not catalog:
            self._print_block("No slash commands registered.")
            return
        lines = ["All commands (copy/adapt the usage line):"]
        for info in catalog:
            name = str(info.get("qualified_name") or info.get("name") or "").strip()
            if not name:
                continue
            usage = _format_usage_line(name, info)
            desc = (info.get("description") or "").strip()
            if desc:
                lines.append(f"  {usage}")
                lines.append(f"    — {desc}")
            else:
                lines.append(f"  {usage}")
        self._print_block("\n".join(lines))

    def _cmd_help(self, args: list[str]) -> None:
        if not args:
            self._print_block(HELP_TEXT)
            return
        name = _normalize_cmd_name(" ".join(args))
        try:
            info = self.client.describe(name)
        except RuntimeError as exc:
            self._print_error(str(exc))
            return
        lines = [
            f"/{info.get('qualified_name', name)}",
            _format_usage_line(name, info),
        ]
        if info.get("description"):
            lines.append(info["description"])
        params = info.get("parameters") or []
        if params:
            lines.append("")
            lines.append("Parameters:")
            for param in params:
                req = "required" if param.get("required") else "optional"
                alias = _param_usage_alias(param)
                ptype = param.get("type", "string")
                extra = ""
                if param.get("duration_minutes"):
                    extra = " (30m, 1h, or minutes)"
                if param.get("choices"):
                    extra = f" choices={param['choices']}"
                lines.append(f"  {param.get('name')} → {alias} ({ptype}, {req}){extra}")
        elif _command_needs_guild(info):
            lines.append("")
            lines.append("Requires server:<guild-id> on the command line.")
        self._print_block("\n".join(lines))

    def _cmd_execute(self, cmd_tokens: list[str], inline: dict[str, str]) -> None:
        command = _resolve_command_name(self.client, cmd_tokens)
        if not command:
            self._print_error("Usage: <command> server:<id> [key:value …]")
            return

        guild_id = inline.get("guild") or inline.get("guild_id") or inline.get("server")

        if command.startswith(".") or command in {"synccommands", "clearchache", "dev"}:
            if command == "dev":
                command = ".dev " + " ".join(cmd_tokens[1:])
            elif not command.startswith("."):
                command = f".{command}"
            msg = self.client.execute(command, guild_id=guild_id, args=inline)
            self._print_block(msg)
            return

        try:
            info = self.client.describe(command)
        except RuntimeError as exc:
            self._print_error(str(exc))
            return

        args = _build_args_from_inline(inline, info.get("parameters") or [])
        missing = _missing_required_tokens(info, guild_id, args)
        if missing:
            usage = _format_usage_line(command, info)
            self._print_error(f"Missing {' '.join(missing)}\nUsage: {usage}")
            return

        msg = self.client.execute(command, guild_id=guild_id, args=args)
        self._print_block(msg)


def main() -> int:
    token = _console_token()
    if not token:
        print(
            "ERROR: HOST_CONSOLE_TOKEN not found. Start the bot once so it can generate one.",
            file=sys.stderr,
        )
        return 1
    client = ConsoleClient(_socket_path(), token)
    try:
        client.connect()
        Repl(client).run()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
