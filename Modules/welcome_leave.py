import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import t, t_sync
from Modules.module_registry import is_module_enabled

BASE_DIR = Path(__file__).resolve().parent.parent
MODULE_ID = "welcome_leave"
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "welcome_leave.json"
SURVEY_PATH = BASE_DIR / "Storage" / "Config" / "exit_surveys.json"

LOGGER = logging.getLogger("coffeecord.welcome_leave")
_CONFIG_LOCK = asyncio.Lock()
_SURVEY_LOCK = asyncio.Lock()

WELCOME_DEFAULT_MESSAGE = "Welcome {user_mention} to {server_name}! We now have {member_count} members."
LEAVE_DEFAULT_MESSAGE = "Goodbye {user_name}. We're sad to see you go!"

DEFAULT_STICKY_MESSAGE = "📌 **Welcome** — read the rules, grab roles, and say hello!"
WELCOME_LEAVE_I18N_PREFIX = "welcome_leave."
STICKY_RATE_WINDOW_S = 60.0
STICKY_HIGH_RATE_THRESHOLD = 100
STICKY_BUMP_DEBOUNCE_LOW_S = 0.35
STICKY_BUMP_DEBOUNCE_HIGH_S = 5.0

_WELCOME_STICKY_BUMP_LOCKS: dict[tuple[int, int], asyncio.Lock] = defaultdict(lambda: asyncio.Lock())


def _wl_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{WELCOME_LEAVE_I18N_PREFIX}{key}", default=default, **params)


def _default_section(message: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "channel_id": None,
        "message": message,
        "embed_enabled": False,
    }


def _default_welcome_section() -> dict[str, Any]:
    d = _default_section(WELCOME_DEFAULT_MESSAGE)
    d["delivery"] = "channel"
    d["sticky_enabled"] = False
    d["sticky_message"] = DEFAULT_STICKY_MESSAGE
    d["sticky_embed_enabled"] = False
    d["sticky_last_message_id"] = None
    return d


def _default_guild_config() -> dict[str, Any]:
    leave_cfg = _default_section(LEAVE_DEFAULT_MESSAGE)
    leave_cfg["exit_survey_enabled"] = False
    leave_cfg["exit_survey_log_channel_id"] = None
    return {
        "welcome": _default_welcome_section(),
        "leave": leave_cfg,
    }


def _normalize_section(raw: Any, default_message: str, *, include_survey: bool = False) -> dict[str, Any]:
    section = _default_section(default_message)
    if isinstance(raw, dict):
        section["enabled"] = bool(raw.get("enabled", False))
        cid = raw.get("channel_id")
        section["channel_id"] = cid if isinstance(cid, int) else None
        message = raw.get("message")
        if isinstance(message, str) and message.strip():
            section["message"] = message.strip()
        section["embed_enabled"] = bool(raw.get("embed_enabled", False))
    if include_survey:
        section["exit_survey_enabled"] = bool(raw.get("exit_survey_enabled", False)) if isinstance(raw, dict) else False
        survey_log_channel_id = raw.get("exit_survey_log_channel_id") if isinstance(raw, dict) else None
        section["exit_survey_log_channel_id"] = survey_log_channel_id if isinstance(survey_log_channel_id, int) else None
    return section


def normalize_welcome_section(raw: Any) -> dict[str, Any]:
    """Merge raw welcome dict with defaults (delivery, sticky, persisted sticky id)."""
    base = _default_welcome_section()
    if not isinstance(raw, dict):
        return base
    base["enabled"] = bool(raw.get("enabled", False))
    cid = raw.get("channel_id")
    base["channel_id"] = cid if isinstance(cid, int) else None
    message = raw.get("message")
    if isinstance(message, str) and message.strip():
        base["message"] = message.strip()
    base["embed_enabled"] = bool(raw.get("embed_enabled", False))
    delivery = str(raw.get("delivery", "channel")).lower().strip()
    if delivery not in ("channel", "dm", "both"):
        delivery = "channel"
    base["delivery"] = delivery
    base["sticky_enabled"] = bool(raw.get("sticky_enabled", False))
    sm = raw.get("sticky_message")
    if isinstance(sm, str) and sm.strip():
        base["sticky_message"] = sm.strip()
    base["sticky_embed_enabled"] = bool(raw.get("sticky_embed_enabled", False))
    slid = raw.get("sticky_last_message_id")
    base["sticky_last_message_id"] = slid if isinstance(slid, int) else None
    return base


def _normalize_guild_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _default_guild_config()
    return {
        "welcome": normalize_welcome_section(raw.get("welcome")),
        "leave": _normalize_section(raw.get("leave"), LEAVE_DEFAULT_MESSAGE, include_survey=True),
    }


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


async def _load_root_config() -> dict[str, Any]:
    async with _CONFIG_LOCK:
        raw = await asyncio.to_thread(_read_json_sync, CONFIG_PATH, {})
        normalized: dict[str, Any] = {}
        for guild_id, cfg in raw.items():
            if not str(guild_id).isdigit():
                continue
            normalized[str(guild_id)] = _normalize_guild_config(cfg)
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, normalized)
        return normalized


async def load_welcome_leave_config(guild_id: int) -> dict[str, Any]:
    root = await _load_root_config()
    key = str(guild_id)
    cfg = root.get(key)
    if cfg is not None:
        return cfg
    cfg = _default_guild_config()
    root[key] = cfg
    async with _CONFIG_LOCK:
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, root)
    return cfg


async def save_welcome_leave_config(guild_id: int, data: dict[str, Any]) -> None:
    root = await _load_root_config()
    root[str(guild_id)] = _normalize_guild_config(data)
    async with _CONFIG_LOCK:
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, root)


async def _patch_welcome_keys(guild_id: int, **welcome_updates: Any) -> None:
    cfg = await load_welcome_leave_config(guild_id)
    w = dict(cfg["welcome"])
    w.update(welcome_updates)
    cfg["welcome"] = normalize_welcome_section(w)
    await save_welcome_leave_config(guild_id, cfg)


def _render_message(template: str, member: discord.Member) -> str:
    return (
        template.replace("{user_mention}", member.mention)
        .replace("{user_name}", member.display_name)
        .replace("{server_name}", member.guild.name)
        .replace("{member_count}", str(member.guild.member_count or 0))
    )


def _render_sticky_message(template: str, guild: discord.Guild) -> str:
    return (
        template.replace("{server_name}", guild.name)
        .replace("{member_count}", str(guild.member_count or 0))
        .replace("{user_mention}", "")
        .replace("{user_name}", "")
    )


def _resolve_text_channel(guild: discord.Guild, channel_id: Any) -> Optional[discord.TextChannel]:
    if not isinstance(channel_id, int):
        return None
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def _get_bot_member(guild: discord.Guild, bot_user_id: Optional[int]) -> Optional[discord.Member]:
    if bot_user_id is None:
        return None
    me = guild.me
    if me is not None:
        return me
    return guild.get_member(bot_user_id)


async def _send_embed_or_plain(
    destination: discord.abc.Messageable,
    *,
    text: str,
    title: str,
    color: discord.Color,
    embed_enabled: bool,
    thumbnail_url: Optional[str],
    footer_guild_name: str,
    me: discord.Member,
) -> tuple[bool, str]:
    if embed_enabled:
        if isinstance(destination, discord.TextChannel):
            perms = destination.permissions_for(me)
            if not perms.embed_links:
                return False, "missing_embed_permissions"
        embed = discord.Embed(title=title, description=text, color=color)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        embed.set_footer(text=f"{footer_guild_name} • Coffeecord")
        try:
            await destination.send(embed=embed)
            return True, "sent"
        except discord.HTTPException:
            return False, "send_failed"
    try:
        await destination.send(text)
        return True, "sent"
    except discord.HTTPException:
        return False, "send_failed"


async def _send_welcome_to_dm(
    bot: commands.Bot,
    member: discord.Member,
    section: dict[str, Any],
    *,
    title: str,
    color: discord.Color,
) -> tuple[bool, str]:
    me = _get_bot_member(member.guild, bot.user.id if bot.user else None)
    if me is None:
        return False, "bot_member_missing"
    message_text = _render_message(str(section.get("message", "")).strip() or WELCOME_DEFAULT_MESSAGE, member)
    thumb = member.display_avatar.url if member.display_avatar else None
    try:
        dm = await member.create_dm()
    except discord.HTTPException:
        return False, "dm_open_failed"
    return await _send_embed_or_plain(
        dm,
        text=message_text,
        title=title,
        color=color,
        embed_enabled=bool(section.get("embed_enabled", False)),
        thumbnail_url=thumb,
        footer_guild_name=member.guild.name,
        me=me,
    )


async def _send_configured_message(
    bot: commands.Bot,
    member: discord.Member,
    section: dict[str, Any],
    *,
    title: str,
    color: discord.Color,
    ignore_enabled: bool = False,
) -> tuple[bool, str]:
    if not ignore_enabled and not section.get("enabled", False):
        return False, "disabled"

    channel = _resolve_text_channel(member.guild, section.get("channel_id"))
    if channel is None:
        return False, "invalid_channel"

    me = _get_bot_member(member.guild, bot.user.id if bot.user else None)
    if me is None:
        return False, "bot_member_missing"
    perms = channel.permissions_for(me)
    if not perms.view_channel or not perms.send_messages:
        return False, "missing_send_permissions"

    message_text = _render_message(str(section.get("message", "")).strip() or WELCOME_DEFAULT_MESSAGE, member)
    thumb = member.display_avatar.url if member.display_avatar else None
    if section.get("embed_enabled", False):
        if not perms.embed_links:
            return False, "missing_embed_permissions"
        return await _send_embed_or_plain(
            channel,
            text=message_text,
            title=title,
            color=color,
            embed_enabled=True,
            thumbnail_url=thumb,
            footer_guild_name=member.guild.name,
            me=me,
        )
    return await _send_embed_or_plain(
        channel,
        text=message_text,
        title=title,
        color=color,
        embed_enabled=False,
        thumbnail_url=thumb,
        footer_guild_name=member.guild.name,
        me=me,
    )


async def _send_welcome_for_member(
    bot: commands.Bot,
    member: discord.Member,
    section: dict[str, Any],
    *,
    title: str,
    color: discord.Color,
    ignore_enabled: bool = False,
) -> None:
    if not ignore_enabled and not section.get("enabled", False):
        return
    delivery = str(section.get("delivery", "channel")).lower()
    if delivery not in ("channel", "dm", "both"):
        delivery = "channel"

    if delivery in ("channel", "both"):
        ok, reason = await _send_configured_message(
            bot, member, section, title=title, color=color, ignore_enabled=True
        )
        if not ok and delivery == "channel":
            LOGGER.warning("Welcome channel send failed for guild %s: %s", member.guild.id, reason)

    if delivery in ("dm", "both"):
        ok_dm, reason_dm = await _send_welcome_to_dm(bot, member, section, title=title, color=color)
        if not ok_dm:
            LOGGER.warning("Welcome DM failed for guild %s user %s: %s", member.guild.id, member.id, reason_dm)


async def _save_exit_survey(guild_id: int, user_id: int, reason: str) -> None:
    async with _SURVEY_LOCK:
        root = await asyncio.to_thread(_read_json_sync, SURVEY_PATH, {})
        user_key = str(user_id)
        guild_key = str(guild_id)
        user_data = root.get(user_key, {})
        if not isinstance(user_data, dict):
            user_data = {}
        user_data[guild_key] = reason[:2000]
        root[user_key] = user_data
        await asyncio.to_thread(_write_json_sync, SURVEY_PATH, root)


def _normalize_survey_reason(raw_reason: str) -> str:
    reason = raw_reason.strip()
    presets = {
        "1": "Too many pings",
        "2": "Not active enough",
        "3": "Not my community",
        "4": "Moderation concerns",
        "5": "Other",
        "6": "__custom__",
    }
    return presets.get(reason, reason)


async def _forward_exit_survey_to_channel(
    bot: commands.Bot,
    *,
    guild_id: int,
    user_id: int,
    guild_name: str,
    reason: str,
    survey_log_channel_id: Any,
) -> None:
    guild = bot.get_guild(guild_id)
    if guild is None or not isinstance(survey_log_channel_id, int):
        return
    channel = guild.get_channel(survey_log_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if me is None:
        return
    perms = channel.permissions_for(me)
    if not perms.view_channel or not perms.send_messages:
        return
    embed = discord.Embed(
        title=_wl_text_sync(None, "exit_survey_response_title", default="Exit Survey Response"),
        color=discord.Color.dark_orange(),
        description=reason[:2000],
    )
    embed.add_field(
        name=_wl_text_sync(None, "exit_survey_field_user", default="User"),
        value=f"<@{user_id}> (`{user_id}`)",
        inline=False,
    )
    embed.add_field(
        name=_wl_text_sync(None, "exit_survey_field_server_left", default="Server Left"),
        value=guild_name,
        inline=False,
    )
    embed.set_footer(text=_wl_text_sync(None, "exit_survey_footer", default="Coffeecord Exit Survey"))
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        return


async def _attempt_exit_survey(
    bot: commands.Bot,
    member: discord.Member,
    guild_name: str,
    guild_id: int,
    survey_log_channel_id: Any = None,
) -> None:
    try:
        prompt = (
            _wl_text_sync(
                guild_id,
                "exit_survey_prompt",
                default=(
                    "Why did you leave **{guild_name}**?\n"
                    "Reply with a short answer, or choose one of these:\n"
                    "1) Too many pings\n"
                    "2) Not active enough\n"
                    "3) Not my community\n"
                    "4) Moderation concerns\n"
                    "5) Other\n"
                    "6) Custom reason (write your own)\n\n"
                    "Reply with a number or your own text. This request expires in 5 minutes."
                ),
                guild_name=guild_name,
            )
        )
        await member.send(prompt)
    except discord.HTTPException:
        return

    def _check(msg: discord.Message) -> bool:
        return msg.author.id == member.id and isinstance(msg.channel, discord.DMChannel)

    try:
        reply = await bot.wait_for("message", timeout=300, check=_check)
    except asyncio.TimeoutError:
        return
    except Exception:
        return

    reason = _normalize_survey_reason((reply.content or "").strip())
    if reason == "__custom__":
        try:
            await member.send(
                _wl_text_sync(
                    guild_id,
                    "exit_survey_custom_prompt",
                    default="Please type your custom reason in one message.",
                )
            )
            reply = await bot.wait_for("message", timeout=300, check=_check)
        except (asyncio.TimeoutError, discord.HTTPException):
            return
        except Exception:
            return
        reason = (reply.content or "").strip()
    if not reason:
        return
    await _save_exit_survey(guild_id, member.id, reason)
    await _forward_exit_survey_to_channel(
        bot,
        guild_id=guild_id,
        user_id=member.id,
        guild_name=guild_name,
        reason=reason,
        survey_log_channel_id=survey_log_channel_id,
    )


class _StickyBumpController:
    """Trailing debounce: bump delay is 5s when >100 msgs/min in channel, else ~0.35s."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._history: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=2000))
        self._pending: dict[tuple[int, int], asyncio.Task[None]] = {}

    def _debounce_seconds(self, key: tuple[int, int]) -> float:
        now = time.monotonic()
        dq = self._history[key]
        while dq and now - dq[0] > STICKY_RATE_WINDOW_S:
            dq.popleft()
        if len(dq) > STICKY_HIGH_RATE_THRESHOLD:
            return STICKY_BUMP_DEBOUNCE_HIGH_S
        return STICKY_BUMP_DEBOUNCE_LOW_S

    def schedule(self, guild_id: int, channel_id: int, coro_factory: Any) -> None:
        key = (guild_id, channel_id)
        if key in self._pending:
            self._pending[key].cancel()
        delay = self._debounce_seconds(key)

        async def _run() -> None:
            try:
                await asyncio.sleep(delay)
                await coro_factory()
            except asyncio.CancelledError:
                return
            except Exception:
                LOGGER.exception("Sticky bump failed for guild %s channel %s", guild_id, channel_id)
            finally:
                self._pending.pop(key, None)

        self._pending[key] = asyncio.create_task(_run())

    def note_user_message(self, guild_id: int, channel_id: int) -> None:
        self._history[(guild_id, channel_id)].append(time.monotonic())


async def _execute_sticky_bump(bot: commands.Bot, guild: discord.Guild, welcome_cfg: dict[str, Any]) -> None:
    if not welcome_cfg.get("sticky_enabled", False):
        return
    channel = _resolve_text_channel(guild, welcome_cfg.get("channel_id"))
    if channel is None:
        return
    lock_key = (guild.id, channel.id)
    async with _WELCOME_STICKY_BUMP_LOCKS[lock_key]:
        await _execute_sticky_bump_locked(bot, guild, welcome_cfg, channel)


async def _execute_sticky_bump_locked(
    bot: commands.Bot,
    guild: discord.Guild,
    welcome_cfg: dict[str, Any],
    channel: discord.TextChannel,
) -> None:
    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if me is None:
        return
    perms = channel.permissions_for(me)
    if not perms.send_messages or not perms.view_channel:
        return
    if not perms.manage_messages:
        LOGGER.warning("Sticky disabled for guild %s: bot needs Manage Messages to delete old sticky.", guild.id)
        return

    sticky_text = str(welcome_cfg.get("sticky_message") or "").strip() or DEFAULT_STICKY_MESSAGE
    body = _render_sticky_message(sticky_text, guild)
    old_id = welcome_cfg.get("sticky_last_message_id")
    if isinstance(old_id, int):
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except (discord.HTTPException, discord.NotFound):
            pass

    try:
        if welcome_cfg.get("sticky_embed_enabled", False) and perms.embed_links:
            embed = discord.Embed(title="📌 Notice", description=body, color=discord.Color.blurple())
            embed.set_footer(text=guild.name)
            msg = await channel.send(embed=embed)
        else:
            msg = await channel.send(body)
    except discord.HTTPException:
        LOGGER.warning("Sticky send failed in guild %s", guild.id)
        return

    await _patch_welcome_keys(guild.id, sticky_last_message_id=msg.id)


class WelcomeCog(
    commands.GroupCog,
    group_name="welcome",
    group_description="Configure welcome messages for new members.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sticky = _StickyBumpController(bot)

    @app_commands.command(
        name="config",
        description="Configure welcome message settings.",
)
    @app_commands.describe(
        channel="Channel where welcome messages are sent (and sticky, if enabled).",
        message="Message text. Supports placeholders like {user_mention}.",
        enabled="Enable or disable welcome messages.",
        use_embed="Send as embed instead of plain text.",
        delivery="Send welcome in channel, DM, or both.",
        sticky_enabled="Re-post a sticky notice at the bottom of the welcome channel when people chat.",
        sticky_message="Sticky text (use {server_name} {member_count}; not per-user).",
        sticky_use_embed="Send sticky as embed.",
    )
    @app_commands.choices(
        delivery=[
            app_commands.Choice(name="Channel only", value="channel"),
            app_commands.Choice(name="DM only", value="dm"),
            app_commands.Choice(name="Channel and DM", value="both"),
        ]
    )
    async def config(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
        enabled: bool = True,
        use_embed: bool = False,
        delivery: str = "channel",
        sticky_enabled: bool = False,
        sticky_message: Optional[str] = None,
        sticky_use_embed: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return

        dlv = delivery if delivery in ("channel", "dm", "both") else "channel"
        sm = sticky_message.strip() if isinstance(sticky_message, str) and sticky_message.strip() else DEFAULT_STICKY_MESSAGE

        cfg = await load_welcome_leave_config(interaction.guild.id)
        cfg["welcome"] = normalize_welcome_section(
            {
                **cfg.get("welcome", {}),
                "enabled": bool(enabled),
                "channel_id": channel.id,
                "message": message.strip() or WELCOME_DEFAULT_MESSAGE,
                "embed_enabled": bool(use_embed),
                "delivery": dlv,
                "sticky_enabled": bool(sticky_enabled),
                "sticky_message": sm,
                "sticky_embed_enabled": bool(sticky_use_embed),
            }
        )
        await save_welcome_leave_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _wl_text_sync(
                interaction.guild.id,
                "welcome_config_updated",
                default=(
                    "✅ Welcome config updated.\n"
                    "Channel: {channel}\nEnabled: `{enabled}`\nEmbed: `{embed}`\n"
                    "Delivery: `{delivery}`\nSticky: `{sticky}`\n"
                ),
                channel=channel.mention,
                enabled=str(enabled),
                embed=str(use_embed),
                delivery=dlv,
                sticky=str(sticky_enabled),
            ),
            ephemeral=True,
        )
        if sticky_enabled and interaction.guild:
            asyncio.create_task(
                _execute_sticky_bump(self.bot, interaction.guild, cfg["welcome"]),
            )

    @app_commands.command(
        name="test",
        description="Send a test welcome message.",
)
    async def test(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await load_welcome_leave_config(interaction.guild.id)
        member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.me
        if member is None:
            await interaction.response.send_message(
                _wl_text_sync(interaction.user.id, "member_resolve_failed", default="Could not resolve member."),
                ephemeral=True,
            )
            return
        delivery = str(cfg["welcome"].get("delivery", "channel")).lower()
        results: list[str] = []

        if delivery in ("channel", "both"):
            ok, reason = await _send_configured_message(
                self.bot,
                member,
                cfg["welcome"],
                title=_wl_text_sync(interaction.user.id, "welcome_title", default="Welcome!"),
                color=discord.Color.green(),
                ignore_enabled=True,
            )
            results.append(f"channel: {'ok' if ok else reason}")
        if delivery in ("dm", "both"):
            ok_dm, reason_dm = await _send_welcome_to_dm(
                self.bot,
                member,
                cfg["welcome"],
                title=_wl_text_sync(interaction.user.id, "welcome_title", default="Welcome!"),
                color=discord.Color.green(),
            )
            results.append(f"dm: {'ok' if ok_dm else reason_dm}")

        ch_ok = delivery in ("channel", "both") and any(r.startswith("channel: ok") for r in results)
        dm_ok = delivery in ("dm", "both") and any(r.startswith("dm: ok") for r in results)
        if ch_ok or dm_ok:
            from Modules.themes import get_command_response_for_interaction

            msg = get_command_response_for_interaction(
                interaction,
                "success",
                _wl_text_sync(
                    interaction.guild.id,
                    "welcome_test_success",
                    default="✅ Sent welcome test ({results}).",
                    results=", ".join(results),
                ),
            )
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.response.send_message(
                _wl_text_sync(
                    interaction.guild.id,
                    "welcome_test_failed",
                    default="⚠️ Could not send welcome test: {results}",
                    results=", ".join(results),
                ),
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            cfg = await load_welcome_leave_config(member.guild.id)
            w = cfg["welcome"]
            await _send_welcome_for_member(
                self.bot,
                member,
                w,
                title=_wl_text_sync(member.guild.id, "welcome_title", default="Welcome!"),
                color=discord.Color.green(),
            )
            if w.get("sticky_enabled", False) and isinstance(w.get("channel_id"), int):
                self._sticky.note_user_message(member.guild.id, int(w["channel_id"]))

                async def bump() -> None:
                    fresh = await load_welcome_leave_config(member.guild.id)
                    g = self.bot.get_guild(member.guild.id)
                    if g is None:
                        return
                    await _execute_sticky_bump(self.bot, g, fresh["welcome"])

                self._sticky.schedule(member.guild.id, int(w["channel_id"]), bump)
        except Exception:
            LOGGER.exception("Failed to process welcome message for guild %s", member.guild.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not await is_module_enabled(message.guild.id, MODULE_ID):
            return
        try:
            cfg = await load_welcome_leave_config(message.guild.id)
            w = cfg["welcome"]
            if not w.get("sticky_enabled", False):
                return
            if not isinstance(w.get("channel_id"), int) or message.channel.id != w["channel_id"]:
                return
            self._sticky.note_user_message(message.guild.id, message.channel.id)

            async def bump() -> None:
                fresh = await load_welcome_leave_config(message.guild.id)
                g = self.bot.get_guild(message.guild.id)
                if g is None:
                    return
                await _execute_sticky_bump(self.bot, g, fresh["welcome"])

            self._sticky.schedule(message.guild.id, message.channel.id, bump)
        except Exception:
            LOGGER.exception("Sticky on_message failed for guild %s", message.guild.id)


class LeaveCog(
    commands.GroupCog,
    group_name="leave",
    group_description="Configure leave messages and exit surveys.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="config",
        description="Configure leave message settings.",
)
    @app_commands.describe(
        channel="Channel where leave messages are sent.",
        message="Message text. Supports placeholders like {user_name}.",
        enabled="Enable or disable leave messages.",
        use_embed="Send as embed instead of plain text.",
        enable_exit_survey="Try DMing an optional survey when someone leaves.",
        exit_survey_log_channel="Channel where survey answers should be posted.",
    )
    async def config(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
        enabled: bool = True,
        use_embed: bool = False,
        enable_exit_survey: bool = False,
        exit_survey_log_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return

        cfg = await load_welcome_leave_config(interaction.guild.id)
        cfg["leave"] = {
            "enabled": bool(enabled),
            "channel_id": channel.id,
            "message": message.strip() or LEAVE_DEFAULT_MESSAGE,
            "embed_enabled": bool(use_embed),
            "exit_survey_enabled": bool(enable_exit_survey),
            "exit_survey_log_channel_id": exit_survey_log_channel.id if exit_survey_log_channel else None,
        }
        await save_welcome_leave_config(interaction.guild.id, cfg)
        log_target = (
            exit_survey_log_channel.mention
            if exit_survey_log_channel is not None
            else "`Not set`"
        )
        await interaction.response.send_message(
            (
                _wl_text_sync(
                    interaction.guild.id,
                    "leave_config_updated",
                    default=(
                        "✅ Leave config updated.\nChannel: {channel}\nEnabled: `{enabled}`\n"
                        "Embed: `{embed}`\nExit survey: `{exit_survey}`\n"
                        "Survey log channel: {log_channel}"
                    ),
                    channel=channel.mention,
                    enabled=str(enabled),
                    embed=str(use_embed),
                    exit_survey=str(enable_exit_survey),
                    log_channel=log_target,
                )
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="test",
        description="Send a test leave message.",
)
    async def test(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await load_welcome_leave_config(interaction.guild.id)
        ok, reason = await _send_configured_message(
            self.bot,
            interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.me,
            cfg["leave"],
            title=_wl_text_sync(interaction.user.id, "leave_title", default="Goodbye!"),
            color=discord.Color.orange(),
            ignore_enabled=True,
        )
        if ok:
            from Modules.themes import get_command_response_for_interaction

            msg = get_command_response_for_interaction(
                interaction,
                "success",
                _wl_text_sync(interaction.user.id, "leave_test_success", default="✅ Sent a leave test message."),
            )
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.response.send_message(
                _wl_text_sync(
                    interaction.guild.id,
                    "leave_test_failed",
                    default="⚠️ Could not send leave test message (`{reason}`). Check channel and bot permissions.",
                    reason=reason,
                ),
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        try:
            cfg = await load_welcome_leave_config(member.guild.id)
            leave_cfg = cfg["leave"]
            await _send_configured_message(
                self.bot,
                member,
                leave_cfg,
                title=_wl_text_sync(member.guild.id, "leave_title", default="Goodbye!"),
                color=discord.Color.orange(),
            )
            if leave_cfg.get("exit_survey_enabled", False) and not member.bot:
                asyncio.create_task(
                    _attempt_exit_survey(
                        self.bot,
                        member,
                        member.guild.name,
                        member.guild.id,
                        leave_cfg.get("exit_survey_log_channel_id"),
                    )
                )
        except Exception:
            LOGGER.exception("Failed to process leave message for guild %s", member.guild.id)


async def setup(bot: commands.Bot) -> None:
    await _load_root_config()
    async with _SURVEY_LOCK:
        survey_data = await asyncio.to_thread(_read_json_sync, SURVEY_PATH, {})
        await asyncio.to_thread(_write_json_sync, SURVEY_PATH, survey_data)
    await bot.add_cog(WelcomeCog(bot))
    await bot.add_cog(LeaveCog(bot))
