"""Inactive member tracking and removal — simple mode (advanced rules deferred)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from Modules.i18n import t_sync
from Modules.module_registry import is_module_enabled

__module_display_name__ = "Inactive Members"
__module_description__ = "Kick or ban members who have not sent a message in a set number of days."
__module_category__ = "moderation"

BASE_DIR = Path(__file__).resolve().parent.parent
MODULE_ID = "inactive_members"
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "inactive_members.json"
ACTIVITY_PATH = BASE_DIR / "Storage" / "Data" / "member_activity.json"

LOGGER = logging.getLogger("coffeecord.inactive_members")
_CONFIG_LOCK = asyncio.Lock()
_ACTIVITY_LOCK = asyncio.Lock()

SECONDS_PER_DAY = 86400
DEFAULT_INACTIVE_DAYS = 30
DEFAULT_SCAN_INTERVAL_HOURS = 6
DEFAULT_WARN_BEFORE_DAYS = 3
DEFAULT_GRACE_DAYS = 7
MAX_ACTIONS_PER_SCAN = 10
LOOP_INTERVAL_HOURS = 1
VALID_ACTIONS = frozenset({"kick", "ban"})
EMBED_COLOR = 0x5865F2
INACTIVE_I18N_PREFIX = "inactive_members."

INFO_STEPS = (
    "**1.** Make sure this module is on: `/modules` → enable **Inactive Members**.\n"
    "**2.** Run `/inactive bootstrap` once. This marks everyone as active today "
    "(the bot only tracks messages from now on).\n"
    "**3.** Run `/inactive setup` — pick how many days without a message (e.g. `30`) "
    "and what happens (`kick` or `ban`).\n"
    "**4.** Optional: `/inactive warn` — DM members **X days before** removal "
    "(e.g. `7` warns at day 23 if removal is at day 30). Use `0` to turn warnings off.\n"
    "**5.** Run `/inactive enable` to turn on automatic checks.\n"
    "**6.** Optional: `/inactive exempt add @Role` for roles that should never be removed.\n\n"
    "**What counts as active?** Sending a normal text message in the server. "
    "GIF-only posts do not count.\n\n"
    "**What does not count?** Joining voice channels (not tracked in simple mode).\n\n"
    "Use `/inactive status` to see if everything is ready. "
    "Use `/inactive check @member` to see someone's last activity."
)


def _inactive_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{INACTIVE_I18N_PREFIX}{key}", default=default, **params)


def _default_guild_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "bootstrapped": False,
        "inactive_days": DEFAULT_INACTIVE_DAYS,
        "action": "kick",
        "warn_before_days": DEFAULT_WARN_BEFORE_DAYS,
        "log_channel_id": None,
        "grace_days_after_join": DEFAULT_GRACE_DAYS,
        "exempt_role_ids": [],
        "scan_interval_hours": DEFAULT_SCAN_INTERVAL_HOURS,
        "last_scan_at": None,
    }


def _migrate_legacy_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull simple settings from old multi-rule config if present."""
    rules = raw.get("rules")
    if not isinstance(rules, list):
        return raw
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        if str(rule.get("type", "absolute")) == "absolute":
            raw.setdefault("inactive_days", rule.get("inactive_days", DEFAULT_INACTIVE_DAYS))
            action = str(rule.get("action", "kick"))
            if action in VALID_ACTIONS:
                raw.setdefault("action", action)
            break
    warn = raw.get("warn_dm_days_before")
    if isinstance(warn, int):
        raw.setdefault("warn_before_days", warn)
    return raw


def _normalize_guild_config(raw: Any) -> dict[str, Any]:
    base = _default_guild_config()
    if not isinstance(raw, dict):
        return base

    raw = _migrate_legacy_config(dict(raw))
    base["enabled"] = bool(raw.get("enabled", False))
    base["bootstrapped"] = bool(raw.get("bootstrapped", False))
    base["inactive_days"] = max(1, int(raw.get("inactive_days", DEFAULT_INACTIVE_DAYS)))

    action = str(raw.get("action", "kick")).lower().strip()
    base["action"] = action if action in VALID_ACTIONS else "kick"
    base["warn_before_days"] = max(0, int(raw.get("warn_before_days", DEFAULT_WARN_BEFORE_DAYS)))

    log_channel_id = raw.get("log_channel_id")
    base["log_channel_id"] = log_channel_id if isinstance(log_channel_id, int) else None
    base["grace_days_after_join"] = max(0, int(raw.get("grace_days_after_join", DEFAULT_GRACE_DAYS)))
    base["scan_interval_hours"] = max(1, int(raw.get("scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS)))

    last_scan = raw.get("last_scan_at")
    base["last_scan_at"] = last_scan if isinstance(last_scan, (int, float)) else None

    exempt = raw.get("exempt_role_ids", [])
    base["exempt_role_ids"] = [int(x) for x in exempt if str(x).isdigit()] if isinstance(exempt, list) else []
    base["warn_before_days"] = _clamp_warn_days(base["inactive_days"], base["warn_before_days"])
    return base


def _clamp_warn_days(inactive_days: int, warn_before_days: int) -> int:
    """Warning must happen before removal; 0 disables warnings."""
    inactive_days = max(1, int(inactive_days))
    warn_before_days = max(0, int(warn_before_days))
    if warn_before_days == 0:
        return 0
    return min(warn_before_days, inactive_days - 1)


def _warn_schedule_text(cfg: dict[str, Any]) -> str:
    limit = int(cfg.get("inactive_days", DEFAULT_INACTIVE_DAYS))
    warn_before = int(cfg.get("warn_before_days", 0))
    if warn_before <= 0:
        return "Off (no advance warning DM)"
    warn_at = max(1, limit - warn_before)
    return f"DM at day **{warn_at}**, {cfg.get('action', 'kick')} at day **{limit}**"


def _read_json_sync(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(default, fp, indent=2, ensure_ascii=True)
        return default
    try:
        with path.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)
        return raw if isinstance(raw, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_sync(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


async def _load_config_root() -> dict[str, Any]:
    async with _CONFIG_LOCK:
        raw = await asyncio.to_thread(_read_json_sync, CONFIG_PATH, {})
        normalized: dict[str, Any] = {}
        for guild_id, cfg in raw.items():
            if not str(guild_id).isdigit():
                continue
            normalized[str(guild_id)] = _normalize_guild_config(cfg)
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, normalized)
        return normalized


async def load_guild_config(guild_id: int) -> dict[str, Any]:
    root = await _load_config_root()
    key = str(guild_id)
    cfg = root.get(key)
    if cfg is not None:
        return cfg
    cfg = _default_guild_config()
    root[key] = cfg
    async with _CONFIG_LOCK:
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, root)
    return cfg


async def save_guild_config(guild_id: int, data: dict[str, Any]) -> None:
    root = await _load_config_root()
    root[str(guild_id)] = _normalize_guild_config(data)
    async with _CONFIG_LOCK:
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, root)


async def _load_activity_root() -> dict[str, Any]:
    async with _ACTIVITY_LOCK:
        raw = await asyncio.to_thread(_read_json_sync, ACTIVITY_PATH, {})
        return raw if isinstance(raw, dict) else {}


async def _save_activity_root(data: dict[str, Any]) -> None:
    async with _ACTIVITY_LOCK:
        await asyncio.to_thread(_write_json_sync, ACTIVITY_PATH, data)


def _default_member_activity(source: str = "bootstrap") -> dict[str, Any]:
    return {
        "last_active": int(time.time()),
        "source": source,
        "warned": False,
        "actioned": False,
    }


def _normalize_member_activity(raw: Any) -> dict[str, Any]:
    base = _default_member_activity()
    if not isinstance(raw, dict):
        return base
    last_active = raw.get("last_active")
    if isinstance(last_active, (int, float)):
        base["last_active"] = int(last_active)
    source = raw.get("source")
    if isinstance(source, str) and source.strip():
        base["source"] = source.strip()[:32]
    base["warned"] = bool(raw.get("warned", False))
    base["actioned"] = bool(raw.get("actioned", False))
    return base


async def get_member_activity(guild_id: int, user_id: int) -> dict[str, Any]:
    root = await _load_activity_root()
    guild_bucket = root.setdefault(str(guild_id), {})
    if not isinstance(guild_bucket, dict):
        guild_bucket = {}
        root[str(guild_id)] = guild_bucket
    entry = _normalize_member_activity(guild_bucket.get(str(user_id)))
    guild_bucket[str(user_id)] = entry
    return entry


async def update_member_activity(guild_id: int, user_id: int, entry: dict[str, Any]) -> None:
    root = await _load_activity_root()
    guild_bucket = root.setdefault(str(guild_id), {})
    if not isinstance(guild_bucket, dict):
        guild_bucket = {}
        root[str(guild_id)] = guild_bucket
    guild_bucket[str(user_id)] = _normalize_member_activity(entry)
    await _save_activity_root(root)


async def prune_member_data(guild_id: int, user_id: int) -> None:
    gid, uid = str(guild_id), str(user_id)
    activity = await _load_activity_root()
    if gid in activity and isinstance(activity[gid], dict):
        activity[gid].pop(uid, None)
        if not activity[gid]:
            activity.pop(gid, None)
        await _save_activity_root(activity)


async def prune_guild_data(guild_id: int) -> None:
    gid = str(guild_id)
    activity = await _load_activity_root()
    if gid in activity:
        activity.pop(gid, None)
        await _save_activity_root(activity)
    root = await _load_config_root()
    if gid in root:
        root.pop(gid, None)
        async with _CONFIG_LOCK:
            await asyncio.to_thread(_write_json_sync, CONFIG_PATH, root)


def _is_gif_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    if "gif" in content_type:
        return True
    return (attachment.filename or "").lower().endswith(".gif")


def qualify_message(message: discord.Message) -> bool:
    """Simple mode: text messages count; GIF-only posts do not."""
    if message.guild is None or message.author.bot:
        return False
    content = (message.content or "").strip()
    if len(content) >= 1:
        return True
    if not message.attachments:
        return False
    return not all(_is_gif_attachment(att) for att in message.attachments)


def _author_higher_or_equal(guild: discord.Guild, member: discord.Member) -> bool:
    me = guild.me
    if me is None:
        return True
    if member.id == guild.owner_id:
        return True
    return member.top_role >= me.top_role


def can_perform_action(guild: discord.Guild, member: discord.Member, action: str) -> tuple[bool, str]:
    me = guild.me
    if me is None:
        return False, "Bot member unavailable."
    perms = me.guild_permissions
    if action == "kick":
        if not perms.kick_members:
            return False, "Missing Kick Members permission."
        if _author_higher_or_equal(guild, member):
            return False, "Cannot kick due to role hierarchy."
        return True, ""
    if action == "ban":
        if not perms.ban_members:
            return False, "Missing Ban Members permission."
        if _author_higher_or_equal(guild, member):
            return False, "Cannot ban due to role hierarchy."
        return True, ""
    return True, ""


def _member_has_role(member: discord.Member, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)


def _should_skip_member(member: discord.Member, cfg: dict[str, Any]) -> bool:
    if member.bot or member.id == member.guild.owner_id:
        return True
    if _member_has_role(member, cfg.get("exempt_role_ids", [])):
        return True
    grace = int(cfg.get("grace_days_after_join", 0))
    if grace > 0 and member.joined_at is not None:
        if (discord.utils.utcnow() - member.joined_at).days < grace:
            return True
    return False


def _idle_days(last_active: int, now: int | None = None) -> float:
    now = now or int(time.time())
    return max(0.0, (now - last_active) / SECONDS_PER_DAY)


def _timestamp_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _dispatch_module_event(
    bot: commands.Bot,
    guild: discord.Guild,
    action: str,
    actor: discord.abc.User | None,
    details: str = "",
) -> None:
    try:
        bot.dispatch("coffeecord_module_event", guild, MODULE_ID, action, actor, details, None)
    except Exception:
        pass


async def _send_modlog(guild: discord.Guild, cfg: dict[str, Any], embed: discord.Embed) -> None:
    channel_id = cfg.get("log_channel_id")
    if not isinstance(channel_id, int):
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    me = guild.me
    if me is None or not channel.permissions_for(me).send_messages:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


async def _warn_member_dm(
    member: discord.Member,
    guild: discord.Guild,
    cfg: dict[str, Any],
    idle_days: float,
) -> None:
    limit_days = int(cfg.get("inactive_days", DEFAULT_INACTIVE_DAYS))
    warn_before = int(cfg.get("warn_before_days", 0))
    days_left = max(1, int(round(limit_days - idle_days)))
    try:
        await member.send(
            f"Heads up — you have not sent a message in **{guild.name}** for about "
            f"{idle_days:.0f} days.\n\n"
            f"The server {cfg.get('action', 'kick')}s inactive members after **{limit_days}** days "
            f"with no messages. You have about **{days_left}** day(s) left to stay — "
            f"send a message in the server to reset your activity."
        )
    except discord.HTTPException:
        pass

    embed = discord.Embed(
        title="Inactive Member — Advance Warning DM",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Idle", value=f"{idle_days:.1f} days", inline=True)
    embed.add_field(name="Warn lead time", value=f"{warn_before} days before removal", inline=True)
    embed.add_field(
        name="Removal if still inactive",
        value=f"{cfg.get('action', 'kick')} after {limit_days} days total",
        inline=False,
    )
    await _send_modlog(guild, cfg, embed)


async def _apply_removal(
    bot: commands.Bot,
    guild: discord.Guild,
    member: discord.Member,
    cfg: dict[str, Any],
    reason: str,
) -> str:
    action = str(cfg.get("action", "kick"))
    if action == "kick":
        ok, blocked = can_perform_action(guild, member, "kick")
        if not ok:
            return f"kick blocked: {blocked}"
        try:
            await member.kick(reason=f"Inactive: {reason}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return f"kick error: {type(exc).__name__}"
        _dispatch_module_event(bot, guild, "inactive_kick", member, reason)
        title = "Inactive Member — Kicked"
    else:
        ok, blocked = can_perform_action(guild, member, "ban")
        if not ok:
            return f"ban blocked: {blocked}"
        try:
            await member.ban(reason=f"Inactive: {reason}", delete_message_days=0)
        except (discord.Forbidden, discord.HTTPException) as exc:
            return f"ban error: {type(exc).__name__}"
        _dispatch_module_event(bot, guild, "inactive_ban", member, reason)
        title = "Inactive Member — Banned"

    embed = discord.Embed(title=title, color=discord.Color.red(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason[:1024], inline=False)
    await _send_modlog(guild, cfg, embed)
    return action


class InactiveMembersCog(
    commands.GroupCog,
    group_name="inactive",
    group_description="Remove members who have not messaged in a while.",
):
    exempt_group = app_commands.Group(name="exempt", description="Roles that are never removed for inactivity")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._config_cache: dict[str, dict[str, Any]] = {}

    async def cog_load(self) -> None:
        await self._reload_config()
        if not self._scan_loop.is_running():
            self._scan_loop.start()

    async def cog_unload(self) -> None:
        self._scan_loop.cancel()

    async def _reload_config(self) -> None:
        self._config_cache = await _load_config_root()

    async def _guild_cfg(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        if key not in self._config_cache:
            self._config_cache[key] = await load_guild_config(guild_id)
        return self._config_cache[key]

    async def _save_cfg(self, guild_id: int, cfg: dict[str, Any]) -> None:
        cfg = _normalize_guild_config(cfg)
        self._config_cache[str(guild_id)] = cfg
        await save_guild_config(guild_id, cfg)

    async def _module_gate(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return False
        if not await is_module_enabled(interaction.guild.id, MODULE_ID):
            await interaction.response.send_message(
                _inactive_text_sync(
                    interaction.guild.id,
                    "module_disabled",
                    default="Inactive members is off. An admin can turn it on with `/modules`.",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _admin_gate(self, interaction: discord.Interaction) -> bool:
        if not await self._module_gate(interaction):
            return False
        assert interaction.guild is not None
        user = interaction.user
        if isinstance(user, discord.Member) and user.guild_permissions.manage_guild:
            return True
        owner_id = getattr(self.bot, "owner_id", None)
        if owner_id and user.id == owner_id:
            return True
        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id if interaction.guild else None,
                "manage_server_required",
                default="You need **Manage Server** to change inactive member settings.",
            ),
            ephemeral=True,
        )
        return False

    @staticmethod
    def _resolve_last_active(member: discord.Member, entry: dict[str, Any]) -> int:
        last_active = entry.get("last_active")
        if isinstance(last_active, (int, float)):
            return int(last_active)
        if member.joined_at is not None:
            return int(member.joined_at.timestamp())
        return int(time.time())

    async def _record_activity(self, guild_id: int, user_id: int, source: str) -> None:
        entry = await get_member_activity(guild_id, user_id)
        entry["last_active"] = int(time.time())
        entry["source"] = source
        entry["warned"] = False
        entry["actioned"] = False
        await update_member_activity(guild_id, user_id, entry)

    async def _evaluate_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        cfg: dict[str, Any],
        actions_remaining: list[int],
    ) -> None:
        if _should_skip_member(member, cfg):
            return

        entry = await get_member_activity(guild.id, member.id)
        if entry.get("actioned"):
            return

        last_active = self._resolve_last_active(member, entry)
        idle = _idle_days(last_active)
        limit = int(cfg.get("inactive_days", DEFAULT_INACTIVE_DAYS))
        warn_before = int(cfg.get("warn_before_days", 0))

        if idle < limit - warn_before:
            entry["warned"] = False
            await update_member_activity(guild.id, member.id, entry)
            return

        if warn_before > 0 and idle >= limit - warn_before and idle < limit:
            if not entry.get("warned"):
                await _warn_member_dm(member, guild, cfg, idle)
                entry["warned"] = True
                _dispatch_module_event(
                    self.bot, guild, "inactive_warn_dm", member,
                    f"idle={idle:.1f}d; limit={limit}d",
                )
                await update_member_activity(guild.id, member.id, entry)
            return

        if idle < limit:
            return

        if actions_remaining[0] <= 0:
            return

        reason = f"No messages for {idle:.1f} days (limit: {limit} days)"
        result = await _apply_removal(self.bot, guild, member, cfg, reason)
        entry["actioned"] = True
        await update_member_activity(guild.id, member.id, entry)
        actions_remaining[0] -= 1
        LOGGER.info("Inactive removal guild=%s member=%s: %s", guild.id, member.id, result)

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        if not await is_module_enabled(guild.id, MODULE_ID):
            return 0
        cfg = await self._guild_cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("bootstrapped"):
            return 0

        now = int(time.time())
        interval_s = int(cfg.get("scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS)) * 3600
        last_scan = cfg.get("last_scan_at")
        if not force and isinstance(last_scan, (int, float)) and now - int(last_scan) < interval_s:
            return 0

        actions_remaining = [MAX_ACTIONS_PER_SCAN]
        processed = 0
        for member in guild.members:
            if member.bot:
                continue
            await self._evaluate_member(guild, member, cfg, actions_remaining)
            processed += 1
            if processed % 25 == 0:
                await asyncio.sleep(0)

        cfg["last_scan_at"] = now
        await self._save_cfg(guild.id, cfg)
        return processed

    @tasks.loop(hours=LOOP_INTERVAL_HOURS)
    async def _scan_loop(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._scan_guild(guild)
            except Exception:
                LOGGER.exception("Inactive scan failed for guild %s", guild.id)
            await asyncio.sleep(0)

    @_scan_loop.before_loop
    async def _before_scan_loop(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        if not await is_module_enabled(message.guild.id, MODULE_ID):
            return
        cfg = await self._guild_cfg(message.guild.id)
        if not cfg.get("enabled"):
            return
        if qualify_message(message):
            await self._record_activity(message.guild.id, message.author.id, "message")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or not await is_module_enabled(member.guild.id, MODULE_ID):
            return
        cfg = await self._guild_cfg(member.guild.id)
        if cfg.get("bootstrapped"):
            await self._record_activity(member.guild.id, member.id, "join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await prune_member_data(member.guild.id, member.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await prune_guild_data(guild.id)
        self._config_cache.pop(str(guild.id), None)

    @app_commands.command(
        name="info",
        description="How to set up inactive member removal.",
)
    async def inactive_info(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=_inactive_text_sync(
                interaction.guild.id if interaction.guild else None,
                "info_title",
                default="Inactive Members — Quick Guide",
            ),
            description=INFO_STEPS,
            color=EMBED_COLOR,
        )
        embed.set_footer(
            text=_inactive_text_sync(
                interaction.guild.id if interaction.guild else None,
                "info_footer",
                default="Advanced options (strike rules, regex, voice activity) may be added later.",
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="status",
        description="See if inactive removal is set up and running.",
)
    async def inactive_status(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        last_scan = cfg.get("last_scan_at")
        last_scan_text = _timestamp_to_iso(int(last_scan)) if isinstance(last_scan, (int, float)) else "Never"
        log_ch = cfg.get("log_channel_id")
        log_text = f"<#{log_ch}>" if isinstance(log_ch, int) else "Not set"

        ready = (
            cfg.get("bootstrapped")
            and cfg.get("enabled")
            and await is_module_enabled(interaction.guild.id, MODULE_ID)
        )

        embed = discord.Embed(
            title=_inactive_text_sync(interaction.user.id, "status_title", default="Inactive Members Status"),
            color=discord.Color.green() if ready else discord.Color.orange(),
        )
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_module_on", default="Module on (/modules)"), value=str(await is_module_enabled(interaction.guild.id, MODULE_ID)), inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_scanning_enabled", default="Scanning enabled"), value=str(cfg.get("enabled", False)), inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_bootstrapped", default="Bootstrapped"), value=str(cfg.get("bootstrapped", False)), inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_inactive_after", default="Inactive after"), value=f"{cfg.get('inactive_days')} days", inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_action", default="Action"), value=str(cfg.get("action", "kick")), inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_advance_warning", default="Advance warning"), value=_warn_schedule_text(cfg), inline=False)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_last_scan", default="Last scan"), value=last_scan_text, inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_log_channel", default="Log channel"), value=log_text, inline=True)
        embed.add_field(name=_inactive_text_sync(interaction.user.id, "field_exempt_roles", default="Exempt roles"), value=str(len(cfg.get("exempt_role_ids", []))), inline=True)
        if not cfg.get("bootstrapped"):
            embed.add_field(
                name=_inactive_text_sync(interaction.user.id, "field_next_step", default="Next step"),
                value=_inactive_text_sync(interaction.user.id, "next_step_bootstrap", default="Run `/inactive bootstrap` then `/inactive setup`."),
                inline=False,
            )
        elif not cfg.get("enabled"):
            embed.add_field(
                name=_inactive_text_sync(interaction.user.id, "field_next_step", default="Next step"),
                value=_inactive_text_sync(interaction.user.id, "next_step_enable", default="Run `/inactive enable` when you are ready."),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="setup",
        description="Set how many days without a message before kick or ban.",
)
    @app_commands.describe(
        inactive_days="Days without a text message before action (e.g. 30).",
        action="What happens when someone is inactive too long.",
        warn_before_days="Days before removal to DM a warning (0 = no warning).",
        log_channel="Optional channel for removal logs.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Kick", value="kick"),
            app_commands.Choice(name="Ban", value="ban"),
        ],
    )
    async def inactive_setup(
        self,
        interaction: discord.Interaction,
        inactive_days: int,
        action: app_commands.Choice[str],
        warn_before_days: Optional[int] = 3,
        log_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        cfg["inactive_days"] = max(1, inactive_days)
        cfg["action"] = action.value
        cfg["warn_before_days"] = _clamp_warn_days(cfg["inactive_days"], warn_before_days or 0)
        if log_channel is not None:
            cfg["log_channel_id"] = log_channel.id
        await self._save_cfg(interaction.guild.id, cfg)

        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id,
                "setup_done",
                default=(
                    "Done. Members with no messages for **{days}** days will be **{action}**.\n"
                    "{warn_schedule}\n"
                    "Change warnings anytime with `/inactive warn`. Run `/inactive bootstrap` if you have not yet, then `/inactive enable`."
                ),
                days=str(cfg["inactive_days"]),
                action=str(cfg["action"]),
                warn_schedule=_warn_schedule_text(cfg),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="warn",
        description="Set how many days before removal to DM an advance warning.",
)
    @app_commands.describe(
        days_before=(
            "Send a warning DM this many days before kick/ban. "
            "Example: 30-day removal with 7 here warns on day 23. Use 0 to disable."
        ),
    )
    async def inactive_warn(self, interaction: discord.Interaction, days_before: int) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        limit = int(cfg.get("inactive_days", DEFAULT_INACTIVE_DAYS))
        cfg["warn_before_days"] = _clamp_warn_days(limit, days_before)
        await self._save_cfg(interaction.guild.id, cfg)

        if cfg["warn_before_days"] <= 0:
            msg = _inactive_text_sync(
                interaction.guild.id,
                "warn_off",
                default="Advance warning DMs are **off**. Members will not get a heads-up before removal.",
            )
        else:
            msg = _inactive_text_sync(
                interaction.guild.id,
                "warn_set",
                default="Advance warning set. {warn_schedule}\nAffected members get a DM when they cross that idle threshold.",
                warn_schedule=_warn_schedule_text(cfg),
            )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="enable",
        description="Turn on automatic inactive member checks.",
)
    async def inactive_enable(self, interaction: discord.Interaction) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        if not cfg.get("bootstrapped"):
            await interaction.response.send_message(
                _inactive_text_sync(
                    interaction.guild.id,
                    "enable_requires_bootstrap",
                    default="Run `/inactive bootstrap` first — it marks current members as active.",
                ),
                ephemeral=True,
            )
            return
        cfg["enabled"] = True
        await self._save_cfg(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id,
                "enable_success",
                default="Inactive checks are **on**. Members idle for **{days}** days may be {action}ed.",
                days=str(cfg["inactive_days"]),
                action=str(cfg["action"]),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="disable",
        description="Turn off automatic inactive member checks.",
)
    async def inactive_disable(self, interaction: discord.Interaction) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        cfg["enabled"] = False
        await self._save_cfg(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id,
                "disable_success",
                default="Inactive checks are **off**. Tracking still runs if enabled later.",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="bootstrap",
        description="One-time setup: mark all current members as active today.",
)
    async def inactive_bootstrap(self, interaction: discord.Interaction) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        now = int(time.time())
        count = 0

        root = await _load_activity_root()
        guild_bucket = root.setdefault(str(guild.id), {})
        if not isinstance(guild_bucket, dict):
            guild_bucket = {}
            root[str(guild.id)] = guild_bucket

        for member in guild.members:
            if member.bot:
                continue
            guild_bucket[str(member.id)] = _default_member_activity("bootstrap")
            guild_bucket[str(member.id)]["last_active"] = now
            count += 1
            if count % 50 == 0:
                await asyncio.sleep(0)

        await _save_activity_root(root)
        cfg = await self._guild_cfg(guild.id)
        cfg["bootstrapped"] = True
        await self._save_cfg(guild.id, cfg)

        await interaction.followup.send(
            _inactive_text_sync(
                guild.id,
                "bootstrap_done",
                default="Marked **{count}** members as active today.\nNext: `/inactive setup` then `/inactive enable`.",
                count=str(count),
            ),
            ephemeral=True,
        )

    @exempt_group.command(
        name="add",
        description="Exempt a role from inactive removal.",
)
    async def exempt_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        exempt = list(cfg.get("exempt_role_ids", []))
        if role.id in exempt:
            await interaction.response.send_message(
                _inactive_text_sync(
                    interaction.guild.id,
                    "exempt_already",
                    default="{role} is already exempt.",
                    role=role.mention,
                ),
                ephemeral=True,
            )
            return
        exempt.append(role.id)
        cfg["exempt_role_ids"] = exempt
        await self._save_cfg(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id,
                "exempt_added",
                default="{role} will not be removed for inactivity.",
                role=role.mention,
            ),
            ephemeral=True,
        )

    @exempt_group.command(
        name="remove",
        description="Remove a role from the exempt list.",
)
    async def exempt_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        cfg["exempt_role_ids"] = [rid for rid in cfg.get("exempt_role_ids", []) if rid != role.id]
        await self._save_cfg(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id,
                "exempt_removed",
                default="{role} removed from exempt list.",
                role=role.mention,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="scan",
        description="Run an inactive check now (admin test).",
)
    async def scan_now(self, interaction: discord.Interaction) -> None:
        if not await self._admin_gate(interaction):
            return
        assert interaction.guild is not None

        await interaction.response.defer(ephemeral=True)
        processed = await self._scan_guild(interaction.guild, force=True)
        await interaction.followup.send(
            _inactive_text_sync(
                interaction.guild.id,
                "scan_done",
                default="Checked **{count}** members.",
                count=str(processed),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="check",
        description="See when a member last counted as active.",
)
    @app_commands.describe(member="Member to look up.")
    async def check_member(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await self._module_gate(interaction):
            return
        assert interaction.guild is not None

        cfg = await self._guild_cfg(interaction.guild.id)
        entry = await get_member_activity(interaction.guild.id, member.id)
        last_active = self._resolve_last_active(member, entry)
        idle = _idle_days(last_active)
        limit = int(cfg.get("inactive_days", DEFAULT_INACTIVE_DAYS))
        warn_before = int(cfg.get("warn_before_days", 0))
        skipped = _should_skip_member(member, cfg)
        warn_status = "not yet"
        if warn_before > 0 and idle >= limit - warn_before:
            warn_status = "sent" if entry.get("warned") else "due now"
        if idle >= limit:
            warn_status = "past removal threshold"

        await interaction.response.send_message(
            _inactive_text_sync(
                interaction.guild.id,
                "check_member",
                default=(
                    "**{member_name}**\n"
                    "Last active: {last_active}\n"
                    "Idle: **{idle_days}** days (limit: {limit})\n"
                    "Advance warning: **{warn_status}** ({warn_schedule})\n"
                    "Protected: **{protected}**"
                ),
                member_name=member.display_name,
                last_active=_timestamp_to_iso(last_active),
                idle_days=f"{idle:.1f}",
                limit=str(limit),
                warn_status=warn_status,
                warn_schedule=_warn_schedule_text(cfg),
                protected="yes" if skipped else "no",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InactiveMembersCog(bot))
