"""
Local host console: Unix-socket RPC so `c-cord console` can run slash/prefix commands
against the live bot process (owner context, interactive pickers in the CLI client).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules import anti_abuse, command_perm_overrides

LOGGER = logging.getLogger("coffeecord.host_console")

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOCKET_PATH = BASE_DIR / "Storage" / "Temp" / "console.sock"
TICKET_ENV_PATH = BASE_DIR / "Src" / "ticket.env"
ENV_PATH = BASE_DIR / "Src" / ".env"

MAX_SEARCH_RESULTS = 15
MAX_CAPTURE_LINES = 40

# Commands that open Discord UI (Views/Modals) — host console cannot complete these.
UI_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "colorrole setup",
        "reactionrole create",
        "setup",
        "setup_resume",
        "setup_cancel",
        "ticket setup",
        "logging setup",
        "welcome config",
        "leave config",
        "theme upload",
        "application setup",
        "inactive setup",
    }
)

TOKEN_ENV_KEY = "HOST_CONSOLE_TOKEN"
DURATION_MINUTE_PARAM_NAMES = frozenset({"duration", "duration_minutes"})
_DURATION_SUFFIX_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
SERVER_INFO_DIR_PREFIX = "server-info"
MODULE_STATES_PATH = BASE_DIR / "Storage" / "Config" / "module_states.json"
COMMAND_PERM_OVERRIDES_PATH = BASE_DIR / "Storage" / "Config" / "command_perm_overrides.json"


class HostConsoleError(Exception):
    pass


class HostUIRequiredError(HostConsoleError):
    pass


@dataclass
class HostCapture:
    lines: list[str] = field(default_factory=list)
    deferred: bool = False
    ui_required: bool = False

    def add(self, text: str) -> None:
        for line in str(text).splitlines():
            stripped = line.strip()
            if stripped:
                self.lines.append(stripped)

    def summary(self) -> str:
        if self.ui_required:
            return "This command requires Discord UI (Views/Modals). Run it in Discord instead."
        if not self.lines:
            if self.deferred:
                return "Command deferred (no follow-up captured)."
            return "OK (no message returned)."
        body = self.lines[:MAX_CAPTURE_LINES]
        extra = ""
        if len(self.lines) > MAX_CAPTURE_LINES:
            extra = f"\n…({len(self.lines) - MAX_CAPTURE_LINES} more lines)"
        return "\n".join(body) + extra


class _HostWebhook:
    def __init__(self, capture: HostCapture) -> None:
        self._capture = capture

    async def send(
        self,
        content: Any = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        if view is not None:
            self._capture.ui_required = True
            raise HostUIRequiredError()
        if content:
            self._capture.add(str(content))
        for emb in (embeds or ([embed] if embed else [])):
            if emb.title:
                self._capture.add(emb.title)
            if emb.description:
                self._capture.add(emb.description)
            for fld in emb.fields:
                self._capture.add(f"{fld.name}: {fld.value}")


class HostResponse:
    def __init__(self, capture: HostCapture) -> None:
        self._capture = capture
        self._done = False

    def is_done(self) -> bool:
        return self._done

    def is_incomplete(self) -> bool:
        return not self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._capture.deferred = True
        self._done = True

    async def send_message(
        self,
        content: Any = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        self._done = True
        webhook = _HostWebhook(self._capture)
        await webhook.send(
            content=content,
            embed=embed,
            embeds=embeds,
            view=view,
            ephemeral=ephemeral,
            **kwargs,
        )

    async def send_modal(self, modal: discord.ui.Modal, /) -> None:
        self._capture.ui_required = True
        raise HostUIRequiredError()


class HostFollowup:
    def __init__(self, capture: HostCapture) -> None:
        self._capture = capture

    async def send(
        self,
        content: Any = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        webhook = _HostWebhook(self._capture)
        await webhook.send(
            content=content,
            embed=embed,
            embeds=embeds,
            view=view,
            ephemeral=ephemeral,
            **kwargs,
        )

    async def edit_message(self, message: Any, **kwargs: Any) -> None:
        content = kwargs.get("content")
        embed = kwargs.get("embed")
        view = kwargs.get("view")
        if view is not None:
            self._capture.ui_required = True
            raise HostUIRequiredError()
        if content:
            self._capture.add(str(content))
        if embed and embed.description:
            self._capture.add(str(embed.description))


def _interaction_data_for_command(cmd: app_commands.Command) -> dict[str, Any]:
    parts = cmd.qualified_name.split()
    root: dict[str, Any] = {"type": 1, "name": parts[0]}
    current = root
    for part in parts[1:]:
        sub: dict[str, Any] = {"type": 1, "name": part}
        current["options"] = [sub]
        current = sub
    return root


class HostInteraction:
    """Minimal Interaction stand-in for invoking slash command callbacks."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        guild: discord.Guild | None,
        channel: discord.abc.Messageable | None,
        user: discord.User | discord.Member,
        command: app_commands.Command | app_commands.Group,
    ) -> None:
        self.client = bot
        self.guild = guild
        self.guild_id = guild.id if guild else None
        self.channel = channel
        self.user = user
        self.member = user if isinstance(user, discord.Member) else None
        self.command = command
        self.type = discord.InteractionType.application_command
        self.data = _interaction_data_for_command(command) if isinstance(command, app_commands.Command) else {}
        self.capture = HostCapture()
        self.response = HostResponse(self.capture)
        self.followup = HostFollowup(self.capture)
        self.namespace = SimpleNamespace()
        self.is_host_console = True

    async def original_response(self) -> Any:
        return None


class HostPrefixContext:
    """Minimal Context for owner prefix commands from the host console."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        author: discord.User | discord.Member,
        guild: discord.Guild | None,
        channel: discord.abc.Messageable,
        content: str,
        command: commands.Command | None = None,
    ) -> None:
        self.bot = bot
        self.author = author
        self.guild = guild
        self.channel = channel
        self.prefix = "."
        self.command = command
        self.invoked_with = content.lstrip(".").split()[0] if content.lstrip(".") else ""
        self.message = SimpleNamespace(
            content=content,
            author=author,
            guild=guild,
            channel=channel,
            clean_content=content,
        )
        self._lines: list[str] = []
        self.is_host_console = True

    async def send(self, content: Any = None, **kwargs: Any) -> None:
        if content:
            self._lines.append(str(content))
        embed = kwargs.get("embed")
        if embed and getattr(embed, "description", None):
            self._lines.append(str(embed.description))

    async def reply(self, content: Any = None, **kwargs: Any) -> None:
        await self.send(content, **kwargs)

    def captured(self) -> str:
        return "\n".join(self._lines) if self._lines else "OK"


def _load_dotenv_key(path: Path, key: str) -> str:
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


def ensure_console_token() -> str:
    token = os.getenv(TOKEN_ENV_KEY, "").strip()
    if token:
        return token
    token = _load_dotenv_key(ENV_PATH, TOKEN_ENV_KEY)
    if token:
        os.environ[TOKEN_ENV_KEY] = token
        return token
    token = _load_dotenv_key(TICKET_ENV_PATH, TOKEN_ENV_KEY)
    if token:
        os.environ[TOKEN_ENV_KEY] = token
        return token
    token = secrets.token_hex(24)
    try:
        TICKET_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TICKET_ENV_PATH.open("a", encoding="utf-8") as fp:
            fp.write(f"\n{TOKEN_ENV_KEY}={token}\n")
    except OSError as exc:
        LOGGER.warning("Could not persist %s: %s", TOKEN_ENV_KEY, exc)
    os.environ[TOKEN_ENV_KEY] = token
    return token


def _owner_id(bot: commands.Bot) -> int | None:
    raw = os.getenv("COFFEECORD_OWNER_ID", "").strip()
    if raw.isdigit():
        return int(raw)
    if bot.owner_id:
        return int(bot.owner_id)
    return None


def _socket_path() -> Path:
    raw = os.getenv("HOST_CONSOLE_SOCKET", "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_SOCKET_PATH


def _walk_commands(
    items: list[app_commands.Command | app_commands.Group],
    prefix: str = "",
) -> list[tuple[str, app_commands.Command | app_commands.Group, str]]:
    found: list[tuple[str, app_commands.Command | app_commands.Group, str]] = []
    for cmd in items:
        if isinstance(cmd, app_commands.Group):
            base = f"{prefix}{cmd.name} "
            found.append((f"/{(prefix + cmd.name).strip()}", cmd, (cmd.description or "").strip()))
            found.extend(_walk_commands(list(cmd.commands), base))
        else:
            qualified = f"/{(prefix + cmd.name).strip()}"
            found.append((qualified, cmd, (cmd.description or "").strip()))
    return found


def _normalize_qualified(name: str) -> str:
    raw = name.strip().lstrip("/")
    return raw.lower()


def _resolve_slash_command(
    tree: app_commands.CommandTree,
    qualified: str,
) -> app_commands.Command | None:
    parts = _normalize_qualified(qualified).split()
    if not parts:
        return None
    current: app_commands.Command | app_commands.Group | None = None
    top = tree.get_command(parts[0])
    if top is None:
        return None
    if len(parts) == 1:
        return top if isinstance(top, app_commands.Command) else None
    if not isinstance(top, app_commands.Group):
        return None
    current = top
    for part in parts[1:]:
        if not isinstance(current, app_commands.Group):
            return None
        nxt = current.get_command(part)
        if nxt is None:
            return None
        current = nxt
    return current if isinstance(current, app_commands.Command) else None


def _param_type_name(param: app_commands.Parameter, cmd: app_commands.Command | None = None) -> str:
    if cmd is not None:
        hinted = _param_type_from_hint(cmd, param.name)
        if hinted:
            return hinted
    try:
        opt = param.type
        mapping = {
            discord.AppCommandOptionType.string: "string",
            discord.AppCommandOptionType.integer: "integer",
            discord.AppCommandOptionType.boolean: "boolean",
            discord.AppCommandOptionType.number: "number",
            discord.AppCommandOptionType.user: "user",
            discord.AppCommandOptionType.channel: "channel",
            discord.AppCommandOptionType.role: "role",
            discord.AppCommandOptionType.mentionable: "mentionable",
            discord.AppCommandOptionType.attachment: "attachment",
        }
        return mapping.get(opt, "string")
    except Exception:
        return "string"


def _param_type_from_hint(cmd: app_commands.Command, param_name: str) -> str | None:
    callback = cmd.callback
    if callback is None:
        return None
    try:
        sig = inspect.signature(callback)
    except (TypeError, ValueError):
        return None
    if param_name not in sig.parameters:
        return None
    ann = sig.parameters[param_name].annotation
    if ann is inspect.Parameter.empty:
        return None
    if getattr(ann, "__origin__", None) is not None:
        args = getattr(ann, "__args__", ())
        ann = next((a for a in args if a is not type(None)), ann)
    if ann in {discord.Member, discord.User}:
        return "member" if ann is discord.Member else "user"
    if ann in {discord.Role}:
        return "role"
    if ann in {discord.TextChannel, discord.VoiceChannel, discord.abc.GuildChannel}:
        return "channel"
    if ann in {int}:
        return "integer"
    if ann in {float}:
        return "number"
    if ann in {bool}:
        return "boolean"
    if ann in {str}:
        return "string"
    return None


def _describe_param(param: app_commands.Parameter, cmd: app_commands.Command) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": param.name,
        "description": (param.description or "").strip(),
        "required": bool(param.required),
        "type": _param_type_name(param, cmd),
    }
    if entry["type"] == "integer" and (
        param.name in DURATION_MINUTE_PARAM_NAMES or "duration" in param.name.lower()
    ):
        entry["duration_minutes"] = True
    if param.choices:
        entry["choices"] = [str(c.name) for c in param.choices]
    if param.autocomplete is not None:
        entry["autocomplete"] = True
    return entry


def _describe_command(cmd: app_commands.Command) -> dict[str, Any]:
    params: list[dict[str, Any]] = []
    for p in cmd.parameters:
        params.append(_describe_param(p, cmd))
    return {
        "qualified_name": cmd.qualified_name,
        "description": (cmd.description or "").strip(),
        "parameters": params,
        "guild_only": bool(getattr(cmd, "guild_only", False)),
    }


def _build_command_catalog(tree: app_commands.CommandTree) -> list[dict[str, Any]]:
    """Full describe payload for every slash command in one pass."""
    rows = _walk_commands(list(tree.get_commands()))
    catalog: list[dict[str, Any]] = []
    for _qn, cmd_obj, _desc in rows:
        if isinstance(cmd_obj, app_commands.Command):
            catalog.append(_describe_command(cmd_obj))
    catalog.sort(key=lambda item: item.get("qualified_name", "").lower())
    return catalog


async def _resolve_owner_member(bot: commands.Bot, guild: discord.Guild | None) -> discord.User | discord.Member:
    oid = _owner_id(bot)
    if oid is None:
        raise HostConsoleError("COFFEECORD_OWNER_ID is not configured.")
    user = bot.get_user(oid) or await bot.fetch_user(oid)
    if guild is None:
        return user
    member = guild.get_member(oid)
    if member is None:
        try:
            member = await guild.fetch_member(oid)
        except discord.HTTPException:
            member = None
    return member or user


def _pick_channel(guild: discord.Guild) -> discord.TextChannel | None:
    if guild.system_channel and isinstance(guild.system_channel, discord.TextChannel):
        return guild.system_channel
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).view_channel:
            return ch
    return None


def _parse_duration_minutes(raw: str) -> int | None:
    """Parse duration text into whole minutes (e.g. 30, 30m, 1h, 90s)."""
    value = str(raw).strip().lower()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    match = _DURATION_SUFFIX_RE.match(value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        return amount
    if unit == "h":
        return amount * 60
    if unit == "d":
        return amount * 1440
    if unit == "s":
        return max(1, (amount + 59) // 60)
    return None


def _coerce_integer_param(param: app_commands.Parameter, raw: Any) -> int:
    text = str(raw).strip()
    if param.name in DURATION_MINUTE_PARAM_NAMES or "duration" in param.name.lower():
        parsed = _parse_duration_minutes(text)
        if parsed is not None:
            return parsed
        raise HostConsoleError(
            f"Invalid duration `{raw}`. Use minutes (e.g. 30), or suffixes like 30m, 1h, 2d."
        )
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    parsed = _parse_duration_minutes(text)
    if parsed is not None:
        return parsed
    raise HostConsoleError(f"Parameter `{param.name}` must be a number, got `{raw}`.")


async def _convert_param(
    bot: commands.Bot,
    guild: discord.Guild | None,
    param: app_commands.Parameter,
    raw: Any,
    cmd: app_commands.Command | None = None,
) -> Any:
    if raw is None:
        return None
    ptype = _param_type_from_hint(cmd, param.name) if cmd else None
    if not ptype:
        ptype = _param_type_name(param, cmd)
    if ptype in {"string"}:
        return str(raw)
    if ptype == "integer":
        return _coerce_integer_param(param, raw)
    if ptype == "number":
        return float(raw)
    if ptype == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in {"1", "true", "yes", "on"}
    if guild is None:
        raise HostConsoleError(f"Parameter `{param.name}` requires a server context.")
    if ptype in {"member", "user", "mentionable"}:
        rid = str(raw).strip()
        if rid.isdigit():
            uid = int(rid)
            if ptype == "member":
                m = guild.get_member(uid)
                if m is None:
                    m = await guild.fetch_member(uid)
                return m
            u = bot.get_user(uid) or await bot.fetch_user(uid)
            if ptype == "mentionable":
                m = guild.get_member(uid)
                if m:
                    return m
            return u
        lookup = rid.lstrip("@")
        for member in guild.members:
            if lookup.lower() in {
                (member.name or "").lower(),
                (member.global_name or "").lower(),
                (member.display_name or "").lower(),
            }:
                return member
        raise HostConsoleError(f"Could not resolve member/user `{raw}`.")
    if ptype == "role":
        rid = str(raw).strip()
        if rid.isdigit():
            role = guild.get_role(int(rid))
            if role is None:
                raise HostConsoleError(f"Role id `{rid}` not found.")
            return role
        lookup = rid.lstrip("@")
        for role in guild.roles:
            if role.name.lower() == lookup.lower():
                return role
        raise HostConsoleError(f"Could not resolve role `{raw}`.")
    if ptype == "channel":
        cid = str(raw).strip()
        if cid.isdigit():
            ch = guild.get_channel(int(cid))
            if ch is None:
                raise HostConsoleError(f"Channel id `{cid}` not found.")
            return ch
        lookup = cid.lstrip("#")
        for ch in guild.channels:
            if getattr(ch, "name", "").lower() == lookup.lower():
                return ch
        raise HostConsoleError(f"Could not resolve channel `{raw}`.")
    if ptype == "attachment":
        raise HostConsoleError("Attachment parameters are not supported in host console.")
    return raw


def _perm_flag_names(perms: discord.Permissions) -> list[str]:
    return sorted(name for name, value in perms if value)


def _write_export_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")


def _write_export_text(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


async def export_guild_info(bot: commands.Bot, guild_id: int) -> dict[str, Any]:
    """Export guild snapshot to Storage/Temp/server-info-<id>-<stamp>/ (console only)."""
    if not bot.is_ready():
        raise HostConsoleError("Bot is not ready yet.")
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise HostConsoleError(f"Guild `{guild_id}` not found.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    export_dir = BASE_DIR / "Storage" / "Temp" / f"{SERVER_INFO_DIR_PREFIX}-{guild_id}-{stamp}"
    export_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    notes: list[str] = []

    member_count_reported = guild.member_count or 0
    cached_before = len(guild.members)
    if member_count_reported and cached_before < member_count_reported:
        try:
            await guild.chunk()
            notes.append(
                f"Requested member chunk: {len(guild.members)}/{member_count_reported} cached."
            )
        except Exception as exc:
            notes.append(f"Member chunk incomplete ({cached_before}/{member_count_reported} cached): {exc}")

    overview = {
        "guild_id": str(guild.id),
        "name": guild.name,
        "owner_id": str(guild.owner_id) if guild.owner_id else None,
        "member_count_reported": member_count_reported,
        "members_cached": len(guild.members),
        "role_count": len(guild.roles),
        "channel_count": len(guild.channels),
        "created_at": guild.created_at.isoformat() if guild.created_at else None,
        "verification_level": str(guild.verification_level),
        "explicit_content_filter": str(guild.explicit_content_filter),
        "mfa_level": str(guild.mfa_level),
        "premium_tier": guild.premium_tier,
        "preferred_locale": str(guild.preferred_locale),
        "features": sorted(guild.features),
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    _write_export_json(export_dir / "overview.json", overview)
    written.append("overview.json")

    readme_lines = [
        f"Server info export — {guild.name} ({guild.id})",
        f"Exported (UTC): {overview['exported_at_utc']}",
        "",
        "Files:",
        "  overview.json          — guild metadata",
        "  roles.json / roles.txt — roles, colors, permissions",
        "  members.json / members.txt — cached members (see overview notes)",
        "  invites.json / invites.txt — active invites (if bot can read them)",
        "  channels.json / channels.txt — channels + bot effective permissions",
        "  channel_overwrites.txt — per-channel role/member overwrites",
        "  bot_member.json        — bot member record in this guild",
        "  command_permissions.json — Coffeecord slash perm rules for this guild",
        "  module_states.json     — per-guild module enable flags",
    ]
    if notes:
        readme_lines.extend(["", "Notes:"] + [f"  - {n}" for n in notes])
    _write_export_text(export_dir / "README.txt", "\n".join(readme_lines))
    written.append("README.txt")

    roles_data: list[dict[str, Any]] = []
    roles_lines: list[str] = []
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        entry = {
            "id": str(role.id),
            "name": role.name,
            "position": role.position,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "managed": role.managed,
            "permissions": _perm_flag_names(role.permissions),
        }
        roles_data.append(entry)
        roles_lines.append(
            f"{role.id}\t{role.position:>3}\t#{role.color.value:06x}\t{role.name}\t"
            f"perms={','.join(entry['permissions'][:12])}"
            f"{'…' if len(entry['permissions']) > 12 else ''}"
        )
    _write_export_json(export_dir / "roles.json", roles_data)
    _write_export_text(export_dir / "roles.txt", "\n".join(roles_lines))
    written.extend(["roles.json", "roles.txt"])

    members_data: list[dict[str, Any]] = []
    members_lines = ["id\tdisplay_name\tusername\tjoined_at\troles\tkey_permissions"]
    for member in sorted(guild.members, key=lambda m: (m.display_name or m.name).lower()):
        role_names = [r.name for r in member.roles if not r.is_default()]
        key_perms = _perm_flag_names(member.guild_permissions)[:20]
        members_data.append(
            {
                "id": str(member.id),
                "display_name": member.display_name,
                "username": str(member),
                "bot": member.bot,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                "roles": role_names,
                "guild_permissions": _perm_flag_names(member.guild_permissions),
            }
        )
        members_lines.append(
            f"{member.id}\t{member.display_name}\t{member}\t"
            f"{member.joined_at.isoformat() if member.joined_at else ''}\t"
            f"{','.join(role_names)}\t{','.join(key_perms)}"
        )
    _write_export_json(export_dir / "members.json", members_data)
    _write_export_text(export_dir / "members.txt", "\n".join(members_lines))
    written.extend(["members.json", "members.txt"])

    invites_data: list[dict[str, Any]] = []
    invites_lines: list[str] = []
    invite_error = ""
    try:
        invites = await guild.invites()
        for inv in invites:
            inv_entry = {
                "code": inv.code,
                "url": str(inv.url),
                "channel_id": str(inv.channel.id) if inv.channel else None,
                "channel_name": getattr(inv.channel, "name", None),
                "inviter_id": str(inv.inviter.id) if inv.inviter else None,
                "inviter": str(inv.inviter) if inv.inviter else None,
                "uses": inv.uses,
                "max_uses": inv.max_uses,
                "max_age": inv.max_age,
                "temporary": inv.temporary,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
            }
            invites_data.append(inv_entry)
            invites_lines.append(
                f"{inv.code}\t#{getattr(inv.channel, 'name', '?')}\t"
                f"uses={inv.uses}/{inv.max_uses or '∞'}\tby={inv.inviter or '?'}"
            )
    except discord.Forbidden:
        invite_error = "Bot lacks permission to view invites (Manage Server)."
    except discord.HTTPException as exc:
        invite_error = f"Could not fetch invites: {exc}"
    if invite_error:
        notes.append(invite_error)
        invites_lines.append(f"# {invite_error}")
    _write_export_json(export_dir / "invites.json", {"invites": invites_data, "error": invite_error or None})
    _write_export_text(export_dir / "invites.txt", "\n".join(invites_lines) if invites_lines else invite_error)
    written.extend(["invites.json", "invites.txt"])

    channels_data: list[dict[str, Any]] = []
    channels_lines: list[str] = []
    overwrite_lines: list[str] = []
    me = guild.me
    for channel in sorted(guild.channels, key=lambda c: (getattr(c, "position", 0), c.name or "")):
        ch_type = str(getattr(channel, "type", "unknown"))
        bot_perms: list[str] = []
        if me is not None:
            try:
                bot_perms = _perm_flag_names(channel.permissions_for(me))
            except Exception:
                bot_perms = []
        ch_entry = {
            "id": str(channel.id),
            "name": getattr(channel, "name", None),
            "type": ch_type,
            "category_id": str(channel.category_id) if channel.category_id else None,
            "position": getattr(channel, "position", None),
            "bot_permissions": bot_perms,
        }
        channels_data.append(ch_entry)
        channels_lines.append(
            f"{channel.id}\t{ch_type}\t{getattr(channel, 'name', '?')}\t"
            f"bot_perms={','.join(bot_perms[:10])}"
        )
        if getattr(channel, "overwrites", None):
            for target, ow in channel.overwrites.items():
                tname = getattr(target, "name", str(target))
                tid = getattr(target, "id", "?")
                allow = _perm_flag_names(ow.pair()[0])
                deny = _perm_flag_names(ow.pair()[1])
                overwrite_lines.append(
                    f"#{getattr(channel, 'name', channel.id)} ({channel.id})\t"
                    f"{tname} ({tid})\tallow={','.join(allow)}\tdeny={','.join(deny)}"
                )
    _write_export_json(export_dir / "channels.json", channels_data)
    _write_export_text(export_dir / "channels.txt", "\n".join(channels_lines))
    _write_export_text(export_dir / "channel_overwrites.txt", "\n".join(overwrite_lines) or "(none)")
    written.extend(["channels.json", "channels.txt", "channel_overwrites.txt"])

    bot_member: dict[str, Any] = {}
    if me is not None:
        bot_member = {
            "id": str(me.id),
            "display_name": me.display_name,
            "top_role": me.top_role.name if me.top_role else None,
            "roles": [r.name for r in me.roles if not r.is_default()],
            "guild_permissions": _perm_flag_names(me.guild_permissions),
        }
    _write_export_json(export_dir / "bot_member.json", bot_member)
    written.append("bot_member.json")

    from Modules.command_perm_overrides import collect_slash_qualified_names, effective_rule

    perm_rows: list[dict[str, Any]] = []
    raw_overrides = _read_json_file(COMMAND_PERM_OVERRIDES_PATH).get(str(guild_id), {})
    if not isinstance(raw_overrides, dict):
        raw_overrides = {}
    for qn in sorted(collect_slash_qualified_names(bot.tree)):
        rule = effective_rule(guild_id, qn)
        if rule is None:
            continue
        perm_rows.append(
            {
                "command": qn,
                "rule": rule,
                "guild_override": qn in raw_overrides,
            }
        )
    _write_export_json(
        export_dir / "command_permissions.json",
        {"guild_id": str(guild_id), "rules": perm_rows, "raw_overrides": raw_overrides},
    )
    written.append("command_permissions.json")

    module_states_all = _read_json_file(MODULE_STATES_PATH)
    guild_modules = module_states_all.get(str(guild_id), {})
    if not isinstance(guild_modules, dict):
        guild_modules = {}
    _write_export_json(export_dir / "module_states.json", guild_modules)
    written.append("module_states.json")

    manifest = {
        "guild_id": str(guild_id),
        "guild_name": guild.name,
        "export_dir": str(export_dir),
        "files": written,
        "notes": notes,
    }
    _write_export_json(export_dir / "manifest.json", manifest)

    summary_lines = [
        f"Exported {guild.name} ({guild_id})",
        f"Path: {export_dir}",
        f"Files: {len(written)}",
        f"Members: {len(guild.members)} cached / {member_count_reported} reported",
        f"Roles: {len(guild.roles)}",
        f"Channels: {len(guild.channels)}",
        f"Invites: {len(invites_data)}",
    ]
    if notes:
        summary_lines.append("Notes: " + "; ".join(notes))
    return {
        "path": str(export_dir),
        "files": written,
        "message": "\n".join(summary_lines),
    }


class HostConsoleServer:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.token = ensure_console_token()
        self.socket_path = _socket_path()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        try:
            os.chmod(self.socket_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        LOGGER.info("Host console listening on %s", self.socket_path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    await self._reply(writer, {"ok": False, "error": "Invalid JSON"})
                    continue
                if not isinstance(payload, dict):
                    await self._reply(writer, {"ok": False, "error": "Expected JSON object"})
                    continue
                req_id = payload.get("id")
                token = str(payload.get("token") or "")
                if not token or not secrets.compare_digest(token, self.token):
                    await self._reply(writer, {"id": req_id, "ok": False, "error": "Unauthorized"})
                    writer.close()
                    break
                op = str(payload.get("op") or "").strip().lower()
                try:
                    result = await self._dispatch(op, payload)
                    await self._reply(writer, {"id": req_id, "ok": True, **result})
                except HostConsoleError as exc:
                    await self._reply(writer, {"id": req_id, "ok": False, "error": str(exc)})
                except Exception as exc:
                    LOGGER.exception("Host console op %s failed", op)
                    await self._reply(
                        writer,
                        {"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Host console client handler error")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _reply(self, writer: asyncio.StreamWriter, data: dict[str, Any]) -> None:
        writer.write((json.dumps(data, ensure_ascii=True) + "\n").encode("utf-8"))
        await writer.drain()

    async def _dispatch(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        if op == "ping":
            return {
                "message": "pong",
                "guilds": len(self.bot.guilds),
                "ready": self.bot.is_ready(),
                "latency_ms": round(self.bot.latency * 1000),
            }
        if op == "list_commands":
            tree = self.bot.tree
            rows = _walk_commands(list(tree.get_commands()))
            cmds = [
                {"name": qn.lstrip("/"), "qualified": qn.lstrip("/"), "description": desc}
                for qn, cmd_obj, desc in rows
                if isinstance(cmd_obj, app_commands.Command)
            ]
            return {"commands": cmds}
        if op == "describe":
            name = str(payload.get("command") or "")
            cmd = _resolve_slash_command(self.bot.tree, name)
            if cmd is None:
                raise HostConsoleError(f"Unknown command `{name}`.")
            return {"command": _describe_command(cmd)}
        if op == "command_catalog":
            return {"commands": _build_command_catalog(self.bot.tree)}
        if op == "list_guilds":
            guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
            return {
                "guilds": [
                    {"id": str(g.id), "name": g.name, "members": g.member_count or 0}
                    for g in guilds
                ]
            }
        if op == "search_members":
            guild_id = payload.get("guild_id")
            query = str(payload.get("query") or "").strip().lower()
            if not str(guild_id).isdigit():
                raise HostConsoleError("guild_id is required.")
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                raise HostConsoleError("Guild not found.")
            matches: list[dict[str, str]] = []
            for member in guild.members:
                hay = f"{member.id} {member.name} {member.global_name or ''} {member.display_name}".lower()
                if not query or query in hay:
                    matches.append(
                        {
                            "id": str(member.id),
                            "name": member.display_name,
                            "username": str(member),
                        }
                    )
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
            return {"members": matches}
        if op == "list_roles":
            guild_id = payload.get("guild_id")
            if not str(guild_id).isdigit():
                raise HostConsoleError("guild_id is required.")
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                raise HostConsoleError("Guild not found.")
            roles = [
                {"id": str(r.id), "name": r.name, "color": r.color.value}
                for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
                if not r.is_default()
            ][:50]
            return {"roles": roles}
        if op == "list_channels":
            guild_id = payload.get("guild_id")
            if not str(guild_id).isdigit():
                raise HostConsoleError("guild_id is required.")
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                raise HostConsoleError("Guild not found.")
            channels = [
                {"id": str(c.id), "name": c.name}
                for c in guild.channels
                if isinstance(c, discord.TextChannel)
            ][:50]
            return {"channels": channels}
        if op == "server_info":
            guild_id = payload.get("guild_id")
            if not str(guild_id).isdigit():
                raise HostConsoleError("guild_id is required.")
            return await export_guild_info(self.bot, int(guild_id))
        if op == "execute":
            return await self._execute(payload)
        raise HostConsoleError(f"Unknown op `{op}`.")

    async def _execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.bot.is_ready():
            raise HostConsoleError("Bot is not ready yet.")
        raw_cmd = str(payload.get("command") or "").strip()
        if not raw_cmd:
            raise HostConsoleError("command is required.")
        if raw_cmd.startswith(".") or raw_cmd in {"synccommands", "clearchache"}:
            return await self._execute_prefix(raw_cmd, payload)
        qualified = _normalize_qualified(raw_cmd)
        if qualified in UI_ONLY_COMMANDS:
            raise HostConsoleError("This command requires Discord UI. Run it in Discord instead.")
        cmd = _resolve_slash_command(self.bot.tree, qualified)
        if cmd is None:
            raise HostConsoleError(f"Unknown slash command `{raw_cmd}`.")
        guild: discord.Guild | None = None
        guild_id = payload.get("guild_id")
        if str(guild_id).isdigit():
            guild = self.bot.get_guild(int(guild_id))
        elif getattr(cmd, "guild_only", False) or any(
            _param_type_name(p, cmd) in {"member", "role", "channel", "mentionable"}
            for p in cmd.parameters
        ):
            raise HostConsoleError("guild_id is required for this command.")
        actor = await _resolve_owner_member(self.bot, guild)
        channel = _pick_channel(guild) if guild else None
        interaction = HostInteraction(
            self.bot,
            guild=guild,
            channel=channel,
            user=actor,
            command=cmd,
        )
        tree = self.bot.tree
        if hasattr(tree, "interaction_check"):
            check_ok = await tree.interaction_check(interaction)  # type: ignore[arg-type]
        else:
            check_ok = True
        if not check_ok:
            raise HostConsoleError("Command blocked by permission or anti-abuse checks.")
        args_raw = payload.get("args") or {}
        if not isinstance(args_raw, dict):
            raise HostConsoleError("args must be an object.")
        kwargs: dict[str, Any] = {}
        for param in cmd.parameters:
            if param.name not in args_raw:
                if param.required:
                    raise HostConsoleError(f"Missing required argument `{param.name}`.")
                continue
            kwargs[param.name] = await _convert_param(
                self.bot,
                guild,
                param,
                args_raw[param.name],
                cmd,
            )
        try:
            interaction.namespace = SimpleNamespace(**kwargs)
            await cmd._do_call(interaction, kwargs)  # type: ignore[arg-type]
        except HostUIRequiredError:
            interaction.capture.ui_required = True
        except app_commands.AppCommandError as exc:
            raise HostConsoleError(str(exc)) from exc
        return {"message": interaction.capture.summary()}

    async def _execute_prefix(self, raw_cmd: str, payload: dict[str, Any]) -> dict[str, Any]:
        oid = _owner_id(self.bot)
        if oid is None:
            raise HostConsoleError("COFFEECORD_OWNER_ID is not configured.")

        guild_id = payload.get("guild_id")
        guild = self.bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
        if guild is None and self.bot.guilds:
            guild = self.bot.guilds[0]
        channel = _pick_channel(guild) if guild else None
        if channel is None:
            raise HostConsoleError("No channel available for prefix command context.")
        author = await _resolve_owner_member(self.bot, guild)
        if author.id != oid:
            raise HostConsoleError("Prefix command rejected (not owner).")

        text = raw_cmd.strip()
        if not text.startswith("."):
            text = f".{text.lstrip('.')}"
        args_obj = payload.get("args") or {}
        if isinstance(args_obj, dict) and args_obj:
            extra = " ".join(f"{k}={v}" for k, v in args_obj.items())
            if extra and extra not in text:
                text = f"{text} {extra}"

        base_name = text.lstrip(".").split()[0].lower()
        if base_name == "synccommands":
            synced = await self.bot.tree.sync()
            return {"message": f"Synced {len(synced)} command(s)."}
        if base_name == "clearchache":
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync()
            return {"message": "Global commands cleared and resynced successfully."}

        cmd: commands.Command | None = self.bot.get_command(text.lstrip(".").split()[0])
        if cmd is None and text.lstrip(".").startswith("dev "):
            parts = text.lstrip(".").split()
            sub = parts[1] if len(parts) > 1 else ""
            cmd = self.bot.get_command(f"dev {sub}") if sub else self.bot.get_command("dev")
        if cmd is None:
            raise HostConsoleError(f"Unknown prefix command `{raw_cmd}`.")

        ctx = HostPrefixContext(
            self.bot,
            author=author,
            guild=guild,
            channel=channel,
            content=text,
            command=cmd,
        )
        parts = text.lstrip(".").split()
        tail = parts[1:] if parts else []
        if cmd.parent is not None and cmd.parent.name == "dev":
            user_arg = " ".join(tail[1:]) if tail and tail[0] == cmd.name else " ".join(tail)
            if not user_arg and isinstance(args_obj, dict):
                user_arg = str(
                    args_obj.get("user")
                    or args_obj.get("member")
                    or args_obj.get("guild_id")
                    or ""
                ).strip()
            await cmd.callback(ctx, user_arg)
        elif len(tail) > 0 and cmd.params:
            await cmd.callback(ctx, " ".join(tail))
        else:
            await cmd.callback(ctx)
        return {"message": ctx.captured()}


class HostConsoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.server = HostConsoleServer(bot)

    async def cog_load(self) -> None:
        await self.server.start()
        print(f"Host console listening on {self.server.socket_path}", flush=True)

    async def cog_unload(self) -> None:
        await self.server.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HostConsoleCog(bot))
