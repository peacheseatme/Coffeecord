import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "welcome_leave.json"
SURVEY_PATH = BASE_DIR / "Storage" / "Config" / "exit_surveys.json"

LOGGER = logging.getLogger("coffeecord.welcome_leave")
_CONFIG_LOCK = asyncio.Lock()
_SURVEY_LOCK = asyncio.Lock()

WELCOME_DEFAULT_MESSAGE = "Welcome {user_mention} to {server_name}! We now have {member_count} members."
LEAVE_DEFAULT_MESSAGE = "Goodbye {user_name}. We're sad to see you go!"


def _default_section(message: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "channel_id": None,
        "message": message,
        "embed_enabled": False,
    }


def _default_guild_config() -> dict[str, Any]:
    leave_cfg = _default_section(LEAVE_DEFAULT_MESSAGE)
    leave_cfg["exit_survey_enabled"] = False
    leave_cfg["exit_survey_log_channel_id"] = None
    return {
        "welcome": _default_section(WELCOME_DEFAULT_MESSAGE),
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


def _normalize_guild_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _default_guild_config()
    return {
        "welcome": _normalize_section(raw.get("welcome"), WELCOME_DEFAULT_MESSAGE),
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


def _render_message(template: str, member: discord.Member) -> str:
    return (
        template.replace("{user_mention}", member.mention)
        .replace("{user_name}", member.display_name)
        .replace("{server_name}", member.guild.name)
        .replace("{member_count}", str(member.guild.member_count or 0))
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
    if section.get("embed_enabled", False):
        if not perms.embed_links:
            return False, "missing_embed_permissions"
        embed = discord.Embed(title=title, description=message_text, color=color)
        avatar = member.display_avatar.url if member.display_avatar else None
        if avatar:
            embed.set_thumbnail(url=avatar)
        embed.set_footer(text=f"{member.guild.name} • Coffeecord")
        try:
            await channel.send(embed=embed)
            return True, "sent"
        except discord.HTTPException:
            return False, "send_failed"
    try:
        await channel.send(message_text)
        return True, "sent"
    except discord.HTTPException:
        return False, "send_failed"


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
        title="Exit Survey Response",
        color=discord.Color.dark_orange(),
        description=reason[:2000],
    )
    embed.add_field(name="User", value=f"<@{user_id}> (`{user_id}`)", inline=False)
    embed.add_field(name="Server Left", value=guild_name, inline=False)
    embed.set_footer(text="Coffeecord Exit Survey")
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
            f"Why did you leave **{guild_name}**?\n"
            "Reply with a short answer, or choose one of these:\n"
            "1) Too many pings\n"
            "2) Not active enough\n"
            "3) Not my community\n"
            "4) Moderation concerns\n"
            "5) Other\n"
            "6) Custom reason (write your own)\n\n"
            "Reply with a number or your own text. This request expires in 5 minutes."
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
            await member.send("Please type your custom reason in one message.")
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


class WelcomeCog(
    commands.GroupCog,
    group_name="welcome",
    group_description="Configure welcome messages for new members.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="config", description="Configure welcome message settings.")
    @app_commands.describe(
        channel="Channel where welcome messages are sent.",
        message="Message text. Supports placeholders like {user_mention}.",
        enabled="Enable or disable welcome messages.",
        use_embed="Send as embed instead of plain text.",
    )
    async def config(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
        enabled: bool = True,
        use_embed: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        cfg = await load_welcome_leave_config(interaction.guild.id)
        cfg["welcome"] = {
            "enabled": bool(enabled),
            "channel_id": channel.id,
            "message": message.strip() or WELCOME_DEFAULT_MESSAGE,
            "embed_enabled": bool(use_embed),
        }
        await save_welcome_leave_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            f"✅ Welcome config updated.\nChannel: {channel.mention}\nEnabled: `{enabled}`\nEmbed: `{use_embed}`",
            ephemeral=True,
        )

    @app_commands.command(name="test", description="Send a test welcome message.")
    async def test(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await load_welcome_leave_config(interaction.guild.id)
        ok, reason = await _send_configured_message(
            self.bot,
            interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.me,
            cfg["welcome"],
            title="Welcome!",
            color=discord.Color.green(),
            ignore_enabled=True,
        )
        if ok:
            from Modules.themes import get_command_response_for_interaction
            msg = get_command_response_for_interaction(
                interaction,
                "success",
                "✅ Sent a welcome test message.",
            )
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⚠️ Could not send welcome test message (`{reason}`). Check channel and bot permissions.",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            cfg = await load_welcome_leave_config(member.guild.id)
            await _send_configured_message(
                self.bot,
                member,
                cfg["welcome"],
                title="Welcome!",
                color=discord.Color.green(),
            )
        except Exception:
            LOGGER.exception("Failed to process welcome message for guild %s", member.guild.id)


class LeaveCog(
    commands.GroupCog,
    group_name="leave",
    group_description="Configure leave messages and exit surveys.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="config", description="Configure leave message settings.")
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
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
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
                f"✅ Leave config updated.\nChannel: {channel.mention}\nEnabled: `{enabled}`\n"
                f"Embed: `{use_embed}`\nExit survey: `{enable_exit_survey}`\n"
                f"Survey log channel: {log_target}"
            ),
            ephemeral=True,
        )

    @app_commands.command(name="test", description="Send a test leave message.")
    async def test(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await load_welcome_leave_config(interaction.guild.id)
        ok, reason = await _send_configured_message(
            self.bot,
            interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.me,
            cfg["leave"],
            title="Goodbye!",
            color=discord.Color.orange(),
            ignore_enabled=True,
        )
        if ok:
            from Modules.themes import get_command_response_for_interaction
            msg = get_command_response_for_interaction(
                interaction,
                "success",
                "✅ Sent a leave test message.",
            )
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⚠️ Could not send leave test message (`{reason}`). Check channel and bot permissions.",
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
                title="Goodbye!",
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
    # Auto-create JSON files on startup for easy first-time setup.
    await _load_root_config()
    async with _SURVEY_LOCK:
        survey_data = await asyncio.to_thread(_read_json_sync, SURVEY_PATH, {})
        await asyncio.to_thread(_write_json_sync, SURVEY_PATH, survey_data)
    await bot.add_cog(WelcomeCog(bot))
    await bot.add_cog(LeaveCog(bot))
