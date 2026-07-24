import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import t, t_sync
from Modules.log_actor import (
    LogActor,
    format_log_actor,
    log_actor_from_context,
    log_actor_from_interaction,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "logging.json"

EVENT_DEFAULTS = {
    "message_delete": True,
    "message_edit": True,
    "member_join": True,
    "member_leave": True,
    "timeout": True,
    "ban": True,
    "unban": True,
    "warn": True,
    "automod": True,
    "ticket_event": True,
    "command_use": True,
    "role_create": True,
    "role_delete": True,
    "role_update": True,
    "channel_create": True,
    "channel_delete": True,
    "channel_update": True,
    "voice_join": True,
    "voice_leave": True,
    "voice_move": True,
    "nickname_change": True,
    "role_assign": True,
    "role_remove": True,
}

MODULE_DEFAULTS = {
    "messages": True,
    "members": True,
    "moderation": True,
    "automod": True,
    "tickets": True,
    "commands": True,
    "polls": True,
    "translation": True,
    "verification": True,
    "supporters": True,
    "leveling": True,
    "calls": True,
    "applications": True,
    "autorole": True,
    "adaptive_slowmode": True,
}

EVENT_MODULE_MAP = {
    "message_delete": "messages",
    "message_edit": "messages",
    "member_join": "members",
    "member_leave": "members",
    "timeout": "moderation",
    "ban": "moderation",
    "unban": "moderation",
    "warn": "moderation",
    "automod": "automod",
    "ticket_event": "tickets",
    "command_use": "commands",
    "role_create": "moderation",
    "role_delete": "moderation",
    "role_update": "moderation",
    "channel_create": "messages",
    "channel_delete": "messages",
    "channel_update": "messages",
    "voice_join": "members",
    "voice_leave": "members",
    "voice_move": "members",
    "nickname_change": "members",
    "role_assign": "moderation",
    "role_remove": "moderation",
}

LOGGER = logging.getLogger("coffeecord.logging")
_CONFIG_LOCK = asyncio.Lock()
AUDIT_LOG_MAX_AGE_SECONDS = 12.0
AUDIT_LOG_FETCH_DELAY_SECONDS = 0.6
LOGGING_EMBED_KEY_PREFIX = "logging.embed."
LOGGING_EVENT_KEY_PREFIX = "logging.events."


def _audit_entry_matches(entry: discord.AuditLogEntry, target_id: int) -> bool:
    target = entry.target
    tid = getattr(target, "id", None)
    if tid != target_id:
        return False
    age = (discord.utils.utcnow() - entry.created_at).total_seconds()
    return age <= AUDIT_LOG_MAX_AGE_SECONDS


def _embed_action_by(
    embed: discord.Embed,
    actor: LogActor,
    guild_id: int | None = None,
    *,
    inline: bool = True,
) -> None:
    embed.add_field(
        name=t_sync(None, f"{LOGGING_EMBED_KEY_PREFIX}action_by", default="Action by"),
        value=format_log_actor(actor),
        inline=inline,
    )


def _guild_default() -> dict[str, Any]:
    return {
        "enabled": False,
        "log_channel_id": None,
        "events": dict(EVENT_DEFAULTS),
        "modules": dict(MODULE_DEFAULTS),
    }


def _normalize_guild_config(raw: Any) -> dict[str, Any]:
    data = _guild_default()
    if not isinstance(raw, dict):
        return data

    data["enabled"] = bool(raw.get("enabled", False))

    channel_id = raw.get("log_channel_id")
    data["log_channel_id"] = channel_id if isinstance(channel_id, int) else None

    events = raw.get("events", {})
    if isinstance(events, dict):
        for event_name, default in EVENT_DEFAULTS.items():
            data["events"][event_name] = bool(events.get(event_name, default))

    modules = raw.get("modules", {})
    if isinstance(modules, dict):
        for module_name, default in MODULE_DEFAULTS.items():
            data["modules"][module_name] = bool(modules.get(module_name, default))
        # Preserve unknown module flags so future modules are not lost on save.
        for module_name, enabled in modules.items():
            if module_name not in data["modules"]:
                data["modules"][str(module_name)] = bool(enabled)
    return data


def _normalize_root_config(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}

    # Backward compatibility with legacy wrapper {"guilds": {...}}
    if "guilds" in raw and isinstance(raw.get("guilds"), dict):
        raw = raw["guilds"]

    normalized: dict[str, dict[str, Any]] = {}
    for guild_id, guild_cfg in raw.items():
        if not str(guild_id).isdigit():
            continue
        normalized[str(guild_id)] = _normalize_guild_config(guild_cfg)
    return normalized


def _read_config_sync() -> dict[str, dict[str, Any]]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as fp:
            json.dump({}, fp, indent=2, ensure_ascii=True)
        return {}

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return {}
    return _normalize_root_config(raw)


def _write_config_sync(data: dict[str, dict[str, Any]]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


class LoggingCog(
    commands.GroupCog,
    group_name="logging",
    group_description="Server logging configuration commands.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._config: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _embed_label(user_id: int | None, key: str, default: str) -> str:
        return t_sync(user_id, f"{LOGGING_EMBED_KEY_PREFIX}{key}", default=default)

    @staticmethod
    def _event_title(guild_id: int | None, key: str, default: str) -> str:
        return t_sync(None, f"{LOGGING_EVENT_KEY_PREFIX}{key}", default=default)

    async def cog_load(self) -> None:
        await self.reload_config()

    async def reload_config(self) -> None:
        async with _CONFIG_LOCK:
            self._config = await asyncio.to_thread(_read_config_sync)
            # Re-write once to ensure defaults/normalization are persisted.
            await asyncio.to_thread(_write_config_sync, self._config)

    async def load_logging_config(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        cfg = self._config.get(key)
        if cfg is None:
            cfg = _guild_default()
            self._config[key] = cfg
            await self.save_logging_config(guild_id, cfg)
        else:
            normalized = _normalize_guild_config(cfg)
            if normalized != cfg:
                self._config[key] = normalized
                await self.save_logging_config(guild_id, normalized)
            cfg = self._config[key]
        return cfg

    async def save_logging_config(self, guild_id: int, data: dict[str, Any]) -> None:
        async with _CONFIG_LOCK:
            self._config[str(guild_id)] = _normalize_guild_config(data)
            await asyncio.to_thread(_write_config_sync, self._config)

    async def _get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        cfg = await self.load_logging_config(guild.id)
        if not cfg.get("enabled", False):
            return None

        channel_id = cfg.get("log_channel_id")
        if not isinstance(channel_id, int):
            return None

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            # Edge case: channel deleted. Disable logging.
            cfg["enabled"] = False
            cfg["log_channel_id"] = None
            await self.save_logging_config(guild.id, cfg)
            if guild.system_channel and guild.me and guild.system_channel.permissions_for(guild.me).send_messages:
                try:
                    await guild.system_channel.send(
                        "Logging was automatically disabled because the configured log channel no longer exists."
                    )
                except discord.HTTPException:
                    pass
            return None

        perms = channel.permissions_for(guild.me) if guild.me else None
        if perms and (not perms.send_messages or not perms.embed_links):
            LOGGER.warning("Missing permissions in log channel %s for guild %s", channel.id, guild.id)
            return None

        return channel

    async def _fetch_audit_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        *,
        target_id: int,
        wait: bool = True,
    ) -> discord.User | None:
        if wait:
            await asyncio.sleep(AUDIT_LOG_FETCH_DELAY_SECONDS)
        try:
            async for entry in guild.audit_logs(action=action, limit=8):
                if not _audit_entry_matches(entry, target_id):
                    continue
                user = entry.user
                if user is not None:
                    return user
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    async def _send_event_embed(
        self,
        guild: discord.Guild,
        event_name: str,
        embed: discord.Embed,
        module_name: Optional[str] = None,
    ) -> None:
        cfg = await self.load_logging_config(guild.id)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("events", {}).get(event_name, False):
            return
        module_key = module_name or EVENT_MODULE_MAP.get(event_name)
        if module_key and not cfg.get("modules", {}).get(module_key, True):
            return

        channel = await self._get_log_channel(guild)
        if channel is None:
            return

        embed.timestamp = embed.timestamp or discord.utils.utcnow()
        embed.set_footer(text="Coffeecord Logging")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            LOGGER.warning("Failed to send logging embed for guild %s", guild.id)

    @staticmethod
    def _event_color(event_name: str) -> discord.Color:
        if event_name in {"ban", "timeout", "warn", "automod"}:
            return discord.Color.orange()
        if event_name in {"member_leave", "message_delete"}:
            return discord.Color.red()
        return discord.Color.blurple()

    def _build_status_embed(self, guild: discord.Guild, cfg: dict[str, Any], *, user_id: int | None = None) -> discord.Embed:
        enabled = bool(cfg.get("enabled", False))
        channel_id = cfg.get("log_channel_id")
        uid = user_id
        channel_text = (
            f"<#{channel_id}>"
            if isinstance(channel_id, int)
            else t_sync(uid, "common.not_configured", default="Not configured.")
        )
        lines = []
        events = cfg.get("events", {})
        for name in EVENT_DEFAULTS.keys():
            marker = "☑" if bool(events.get(name, False)) else "☐"
            lines.append(f"{marker} `{name}`")
        module_lines = []
        modules = cfg.get("modules", {})
        for name in MODULE_DEFAULTS.keys():
            marker = "☑" if bool(modules.get(name, False)) else "☐"
            module_lines.append(f"{marker} `{name}`")
        embed = discord.Embed(
            title=t_sync(uid, "logging.status.title", default="Logging Status"),
            color=discord.Color.green() if enabled else discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=self._embed_label(uid, "enabled", "Enabled"),
            value=t_sync(uid, "common.yes", default="Yes") if enabled else t_sync(uid, "common.no", default="No"),
            inline=True,
        )
        embed.add_field(name=self._embed_label(uid, "log_channel", "Log Channel"), value=channel_text, inline=True)
        embed.add_field(name=self._embed_label(uid, "events", "Events"), value="\n".join(lines), inline=False)
        embed.add_field(name=self._embed_label(uid, "modules", "Modules"), value="\n".join(module_lines), inline=False)
        embed.set_footer(text="Coffeecord Logging")
        return embed

    @app_commands.command(
        name="status",
        description="Show logging status for this server.",
)
    async def logging_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        await interaction.response.send_message(embed=self._build_status_embed(interaction.guild, cfg, user_id=interaction.user.id), ephemeral=True)

    @app_commands.command(
        name="setup",
        description="Set logging channel and enable logging.",
)
    @app_commands.describe(channel="Channel where logs should be sent")
    async def logging_setup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        cfg["enabled"] = True
        cfg["log_channel_id"] = channel.id
        await self.save_logging_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            await t(interaction.user.id,
                "logging.setup_success",
                channel=channel.mention,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="toggle",
        description="Enable or disable a specific logging event.",
)
    @app_commands.describe(event="Event to toggle")
    @app_commands.choices(
        event=[
            app_commands.Choice(name="Message Delete", value="message_delete"),
            app_commands.Choice(name="Message Edit", value="message_edit"),
            app_commands.Choice(name="Member Join", value="member_join"),
            app_commands.Choice(name="Member Leave", value="member_leave"),
            app_commands.Choice(name="Timeout", value="timeout"),
            app_commands.Choice(name="Ban", value="ban"),
            app_commands.Choice(name="Unban", value="unban"),
            app_commands.Choice(name="Warn", value="warn"),
            app_commands.Choice(name="Automod", value="automod"),
            app_commands.Choice(name="Ticket Event", value="ticket_event"),
            app_commands.Choice(name="Module Event", value="module_event"),
            app_commands.Choice(name="All Commands", value="command_use"),
            app_commands.Choice(name="Role Create", value="role_create"),
            app_commands.Choice(name="Role Delete", value="role_delete"),
            app_commands.Choice(name="Role Update", value="role_update"),
            app_commands.Choice(name="Channel Create", value="channel_create"),
            app_commands.Choice(name="Channel Delete", value="channel_delete"),
            app_commands.Choice(name="Channel Update", value="channel_update"),
            app_commands.Choice(name="Voice Join", value="voice_join"),
            app_commands.Choice(name="Voice Leave", value="voice_leave"),
            app_commands.Choice(name="Voice Move", value="voice_move"),
            app_commands.Choice(name="Nickname Change", value="nickname_change"),
            app_commands.Choice(name="Role Assign", value="role_assign"),
            app_commands.Choice(name="Role Remove", value="role_remove"),
        ]
    )
    async def logging_toggle(self, interaction: discord.Interaction, event: app_commands.Choice[str]) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        current = bool(cfg.get("events", {}).get(event.value, EVENT_DEFAULTS[event.value]))
        cfg["events"][event.value] = not current
        await self.save_logging_config(interaction.guild.id, cfg)
        state_key = "modules_cmd.state_enabled" if cfg["events"][event.value] else "modules_cmd.state_disabled"
        await interaction.response.send_message(
            await t(interaction.user.id,
                "logging.toggle_event",
                event=event.value,
                state=await t(interaction.user.id, state_key),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="module",
        description="Enable or disable a logging module.",
)
    @app_commands.describe(module="Module to toggle", enabled="Whether this module should log")
    @app_commands.choices(
        module=[
            app_commands.Choice(name="Messages", value="messages"),
            app_commands.Choice(name="Members", value="members"),
            app_commands.Choice(name="Moderation", value="moderation"),
            app_commands.Choice(name="Automod", value="automod"),
            app_commands.Choice(name="Tickets", value="tickets"),
            app_commands.Choice(name="Commands", value="commands"),
            app_commands.Choice(name="Polls", value="polls"),
            app_commands.Choice(name="Translation", value="translation"),
            app_commands.Choice(name="Verification", value="verification"),
            app_commands.Choice(name="Supporters", value="supporters"),
            app_commands.Choice(name="Leveling", value="leveling"),
            app_commands.Choice(name="Calls", value="calls"),
            app_commands.Choice(name="Applications", value="applications"),
            app_commands.Choice(name="Autorole", value="autorole"),
            app_commands.Choice(name="Adaptive Slowmode", value="adaptive_slowmode"),
        ]
    )
    async def logging_module(
        self,
        interaction: discord.Interaction,
        module: app_commands.Choice[str],
        enabled: bool,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        cfg["modules"][module.value] = enabled
        await self.save_logging_config(interaction.guild.id, cfg)
        state_key = "modules_cmd.state_enabled" if enabled else "modules_cmd.state_disabled"
        await interaction.response.send_message(
            await t(interaction.user.id,
                "logging.module_set",
                module=module.value,
                state=await t(interaction.user.id, state_key),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="disable",
        description="Disable logging for this server.",
)
    async def logging_disable(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        cfg["enabled"] = False
        await self.save_logging_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            await t(interaction.user.id, "logging.disabled"),
            ephemeral=True,
        )

    # ----- Event listeners -----
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.application_command:
            return
        if interaction.guild is None:
            return
        if interaction.user.bot:
            return

        command_name = "unknown"
        if interaction.command is not None:
            command_name = interaction.command.qualified_name
        elif interaction.data and isinstance(interaction.data, dict):
            command_name = str(interaction.data.get("name", "unknown"))

        guild_id = interaction.guild.id
        channel_value = (
            interaction.channel.mention
            if interaction.channel
            else self._embed_label(None, "unknown_channel", "Unknown")
        )
        embed = discord.Embed(
            title=self._event_title(guild_id, "command_use", "Command Used"),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=self._embed_label(None, "type", "Type"),
            value=self._embed_label(None, "slash", "Slash"),
            inline=True,
        )
        embed.add_field(name=self._embed_label(None, "command", "Command"), value=f"`/{command_name}`", inline=True)
        _embed_action_by(embed, log_actor_from_interaction(interaction), guild_id)
        embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=channel_value, inline=True)
        await self._send_event_embed(interaction.guild, "command_use", embed, module_name="commands")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return
        if ctx.author.bot:
            return
        if ctx.command is None:
            return

        command_name = ctx.command.qualified_name
        guild_id = ctx.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "command_use", "Command Used"),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=self._embed_label(None, "type", "Type"),
            value=self._embed_label(None, "prefix", "Prefix"),
            inline=True,
        )
        embed.add_field(
            name=self._embed_label(None, "command", "Command"),
            value=f"`{ctx.clean_prefix}{command_name}`",
            inline=True,
        )
        _embed_action_by(embed, log_actor_from_context(ctx), guild_id)
        embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=ctx.channel.mention, inline=True)
        await self._send_event_embed(ctx.guild, "command_use", embed, module_name="commands")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        actor: LogActor = message.author
        if message.guild.me and self.bot.user:
            audit_user = await self._fetch_audit_actor(
                message.guild,
                discord.AuditLogAction.message_delete,
                target_id=message.author.id,
            )
            if audit_user is not None and audit_user.id != message.author.id:
                actor = audit_user
        guild_id = message.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "message_delete", "Message Deleted"),
            color=self._event_color("message_delete"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "author", "Author"), value=message.author.mention, inline=True)
        embed.add_field(
            name=self._embed_label(None, "channel", "Channel"),
            value=(
                message.channel.mention
                if isinstance(message.channel, discord.abc.GuildChannel)
                else self._embed_label(None, "unknown_channel", "Unknown")
            ),
            inline=True,
        )
        _embed_action_by(embed, actor, guild_id)
        content = (message.content or "").strip()
        embed.add_field(
            name=self._embed_label(None, "content", "Content"),
            value=content[:1024] if content else self._embed_label(None, "no_text", "No text content"),
            inline=False,
        )
        await self._send_event_embed(message.guild, "message_delete", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        guild_id = before.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "message_edit", "Message Edited"),
            color=self._event_color("message_edit"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "author", "Author"), value=before.author.mention, inline=True)
        embed.add_field(
            name=self._embed_label(None, "channel", "Channel"),
            value=(
                before.channel.mention
                if isinstance(before.channel, discord.abc.GuildChannel)
                else self._embed_label(None, "unknown_channel", "Unknown")
            ),
            inline=True,
        )
        _embed_action_by(embed, before.author, guild_id)
        embed.add_field(
            name=self._embed_label(None, "before", "Before"),
            value=(before.content or self._embed_label(None, "no_text_short", "No text"))[:1024],
            inline=False,
        )
        embed.add_field(
            name=self._embed_label(None, "after", "After"),
            value=(after.content or self._embed_label(None, "no_text_short", "No text"))[:1024],
            inline=False,
        )
        await self._send_event_embed(before.guild, "message_edit", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_id = member.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "member_join", "Member Joined"),
            color=self._event_color("member_join"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(
            name=self._embed_label(None, "account_created", "Account Created"),
            value=discord.utils.format_dt(member.created_at, style="R"),
            inline=False,
        )
        await self._send_event_embed(member.guild, "member_join", embed, module_name="members")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild_id = member.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "member_leave", "Member Left"),
            color=self._event_color("member_leave"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{member} (`{member.id}`)", inline=False)
        await self._send_event_embed(member.guild, "member_leave", embed, module_name="members")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild_id = after.guild.id
        before_to = before.timed_out_until
        after_to = after.timed_out_until
        if before_to != after_to:
            is_timeout_added = after_to is not None and (before_to is None or after_to > before_to)
            title = self._event_title(
                guild_id,
                "timeout_added" if is_timeout_added else "timeout_removed",
                "Timeout Added" if is_timeout_added else "Timeout Removed",
            )
            embed = discord.Embed(
                title=title,
                color=self._event_color("timeout"),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{after.mention} (`{after.id}`)", inline=False)
            if after_to is not None:
                embed.add_field(
                    name=self._embed_label(None, "until", "Until"),
                    value=discord.utils.format_dt(after_to, style="F"),
                    inline=False,
                )
            audit_user = await self._fetch_audit_actor(
                after.guild,
                discord.AuditLogAction.member_update,
                target_id=after.id,
            )
            _embed_action_by(embed, audit_user, guild_id)
            await self._send_event_embed(after.guild, "timeout", embed, module_name="moderation")

        if before.nick != after.nick:
            embed = discord.Embed(
                title=self._event_title(guild_id, "nickname_change", "Nickname Changed"),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(
                name=self._embed_label(None, "before", "Before"),
                value=before.nick or before.name,
                inline=True,
            )
            embed.add_field(
                name=self._embed_label(None, "after", "After"),
                value=after.nick or after.name,
                inline=True,
            )
            audit_user = await self._fetch_audit_actor(
                after.guild,
                discord.AuditLogAction.member_update,
                target_id=after.id,
            )
            _embed_action_by(embed, audit_user, guild_id)
            await self._send_event_embed(after.guild, "nickname_change", embed, module_name="members")

        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}
        added_role_ids = after_roles - before_roles
        removed_role_ids = before_roles - after_roles
        role_audit_user: discord.User | None = None
        if added_role_ids or removed_role_ids:
            role_audit_user = await self._fetch_audit_actor(
                after.guild,
                discord.AuditLogAction.member_role_update,
                target_id=after.id,
            )

        if added_role_ids:
            role_mentions = [f"<@&{role_id}>" for role_id in added_role_ids]
            embed = discord.Embed(
                title=self._event_title(guild_id, "role_assign", "Roles Added"),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(
                name=self._embed_label(None, "roles", "Roles"),
                value=", ".join(role_mentions)[:1024],
                inline=False,
            )
            _embed_action_by(embed, role_audit_user, guild_id)
            await self._send_event_embed(after.guild, "role_assign", embed, module_name="moderation")

        if removed_role_ids:
            role_mentions = [f"<@&{role_id}>" for role_id in removed_role_ids]
            embed = discord.Embed(
                title=self._event_title(guild_id, "role_remove", "Roles Removed"),
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(
                name=self._embed_label(None, "roles", "Roles"),
                value=", ".join(role_mentions)[:1024],
                inline=False,
            )
            _embed_action_by(embed, role_audit_user, guild_id)
            await self._send_event_embed(after.guild, "role_remove", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        audit_user = await self._fetch_audit_actor(
            guild,
            discord.AuditLogAction.ban,
            target_id=user.id,
        )
        guild_id = guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "ban", "Member Banned"),
            color=self._event_color("ban"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "user", "User"), value=f"{user} (`{user.id}`)", inline=False)
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(guild, "ban", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        audit_user = await self._fetch_audit_actor(
            guild,
            discord.AuditLogAction.unban,
            target_id=user.id,
        )
        guild_id = guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "unban", "Member Unbanned"),
            color=self._event_color("unban"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "user", "User"), value=f"{user} (`{user.id}`)", inline=False)
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(guild, "unban", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        audit_user = await self._fetch_audit_actor(
            role.guild,
            discord.AuditLogAction.role_create,
            target_id=role.id,
        )
        guild_id = role.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "role_create", "Role Created"),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "role", "Role"), value=f"{role.mention} (`{role.id}`)", inline=False)
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(role.guild, "role_create", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        audit_user = await self._fetch_audit_actor(
            role.guild,
            discord.AuditLogAction.role_delete,
            target_id=role.id,
        )
        guild_id = role.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "role_delete", "Role Deleted"),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "role", "Role"), value=f"{role.name} (`{role.id}`)", inline=False)
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(role.guild, "role_delete", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"name: `{before.name}` -> `{after.name}`")
        if before.color != after.color:
            changes.append(f"color: `{before.color}` -> `{after.color}`")
        if before.permissions != after.permissions:
            changes.append("permissions updated")
        if not changes:
            return
        audit_user = await self._fetch_audit_actor(
            after.guild,
            discord.AuditLogAction.role_update,
            target_id=after.id,
        )
        guild_id = after.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "role_update", "Role Updated"),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "role", "Role"), value=f"{after.mention} (`{after.id}`)", inline=False)
        embed.add_field(
            name=self._embed_label(None, "changes", "Changes"),
            value="\n".join(changes)[:1024],
            inline=False,
        )
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(after.guild, "role_update", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        audit_user = await self._fetch_audit_actor(
            channel.guild,
            discord.AuditLogAction.channel_create,
            target_id=channel.id,
        )
        guild_id = channel.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "channel_create", "Channel Created"),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=self._embed_label(None, "channel", "Channel"),
            value=f"{getattr(channel, 'mention', channel.name)} (`{channel.id}`)",
            inline=False,
        )
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(channel.guild, "channel_create", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        audit_user = await self._fetch_audit_actor(
            channel.guild,
            discord.AuditLogAction.channel_delete,
            target_id=channel.id,
        )
        guild_id = channel.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "channel_delete", "Channel Deleted"),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=self._embed_label(None, "channel", "Channel"),
            value=f"{channel.name} (`{channel.id}`)",
            inline=False,
        )
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(channel.guild, "channel_delete", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"name: `{before.name}` -> `{after.name}`")
        if getattr(before, "category_id", None) != getattr(after, "category_id", None):
            changes.append("category updated")
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append("slowmode updated")
        if not changes:
            return
        audit_user = await self._fetch_audit_actor(
            after.guild,
            discord.AuditLogAction.channel_update,
            target_id=after.id,
        )
        guild_id = after.guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "channel_update", "Channel Updated"),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=self._embed_label(None, "channel", "Channel"),
            value=f"{getattr(after, 'mention', after.name)} (`{after.id}`)",
            inline=False,
        )
        embed.add_field(
            name=self._embed_label(None, "changes", "Changes"),
            value="\n".join(changes)[:1024],
            inline=False,
        )
        _embed_action_by(embed, audit_user, guild_id)
        await self._send_event_embed(after.guild, "channel_update", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        guild_id = member.guild.id
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(
                title=self._event_title(guild_id, "voice_join", "Voice Join"),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=after.channel.mention, inline=False)
            _embed_action_by(embed, member, guild_id)
            await self._send_event_embed(member.guild, "voice_join", embed, module_name="members")
            return
        if before.channel is not None and after.channel is None:
            embed = discord.Embed(
                title=self._event_title(guild_id, "voice_leave", "Voice Leave"),
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=before.channel.mention, inline=False)
            _embed_action_by(embed, member, guild_id)
            await self._send_event_embed(member.guild, "voice_leave", embed, module_name="members")
            return
        if before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
            embed = discord.Embed(
                title=self._event_title(guild_id, "voice_move", "Voice Move"),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name=self._embed_label(None, "member", "Member"), value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name=self._embed_label(None, "from", "From"), value=before.channel.mention, inline=True)
            embed.add_field(name=self._embed_label(None, "to", "To"), value=after.channel.mention, inline=True)
            audit_user = await self._fetch_audit_actor(
                member.guild,
                discord.AuditLogAction.member_move,
                target_id=member.id,
            )
            _embed_action_by(embed, audit_user or member, guild_id)
            await self._send_event_embed(member.guild, "voice_move", embed, module_name="members")

    @commands.Cog.listener("on_coffeecord_warn")
    async def on_coffeecord_warn(
        self,
        guild: discord.Guild,
        moderator: Optional[discord.abc.User],
        target: discord.abc.User,
        reason: str,
        source: str = "manual",
    ) -> None:
        guild_id = guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "warn", "Warn Issued"),
            color=self._event_color("warn"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "target", "Target"), value=f"{target.mention} (`{target.id}`)", inline=False)
        _embed_action_by(embed, moderator, guild_id)
        embed.add_field(name=self._embed_label(None, "source", "Source"), value=source, inline=True)
        embed.add_field(
            name=self._embed_label(None, "reason", "Reason"),
            value=(reason or self._embed_label(None, "no_reason", "No reason provided"))[:1024],
            inline=False,
        )
        await self._send_event_embed(guild, "warn", embed, module_name="moderation")

    @commands.Cog.listener("on_coffeecord_automod_action")
    async def on_coffeecord_automod_action(
        self,
        guild: discord.Guild,
        target: discord.abc.User,
        rule: str,
        action: str,
        reason: str,
        channel_id: int,
        message_id: int,
    ) -> None:
        guild_id = guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "automod", "Automod Action"),
            color=self._event_color("automod"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "target", "Target"), value=f"{target.mention} (`{target.id}`)", inline=False)
        embed.add_field(name=self._embed_label(None, "rule", "Rule"), value=rule, inline=True)
        embed.add_field(name=self._embed_label(None, "action", "Action"), value=action, inline=True)
        embed.add_field(
            name=self._embed_label(None, "action_by", "Action by"),
            value=self._embed_label(None, "automod", "Automod"),
            inline=True,
        )
        embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=f"<#{channel_id}>", inline=True)
        embed.add_field(
            name=self._embed_label(None, "message", "Message"),
            value=f"https://discord.com/channels/{guild.id}/{channel_id}/{message_id}",
            inline=False,
        )
        embed.add_field(
            name=self._embed_label(None, "reason", "Reason"),
            value=(reason or self._embed_label(None, "no_reason", "No reason provided"))[:1024],
            inline=False,
        )
        await self._send_event_embed(guild, "automod", embed, module_name="automod")

    @commands.Cog.listener("on_coffeecord_ticket_event")
    async def on_coffeecord_ticket_event(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        action: str,
        channel_id: int,
        details: str = "",
    ) -> None:
        guild_id = guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "ticket_event", "Ticket Event"),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "action", "Action"), value=action, inline=True)
        _embed_action_by(embed, actor, guild_id)
        embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=f"<#{channel_id}>", inline=True)
        if details:
            embed.add_field(name=self._embed_label(None, "details", "Details"), value=details[:1024], inline=False)
        await self._send_event_embed(guild, "ticket_event", embed, module_name="tickets")

    @commands.Cog.listener("on_coffeecord_module_event")
    async def on_coffeecord_module_event(
        self,
        guild: discord.Guild,
        module_name: str,
        action: str,
        actor: LogActor = None,
        details: str = "",
        channel_id: Optional[int] = None,
    ) -> None:
        module_key = (module_name or "misc").strip().lower()
        guild_id = guild.id
        embed = discord.Embed(
            title=self._event_title(guild_id, "module_event", "Module Event"),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=self._embed_label(None, "module", "Module"), value=module_key, inline=True)
        embed.add_field(
            name=self._embed_label(None, "action", "Action"),
            value=action or t_sync(None, "common.unknown", default="unknown"),
            inline=True,
        )
        _embed_action_by(embed, actor, guild_id, inline=False)
        if channel_id is not None:
            embed.add_field(name=self._embed_label(None, "channel", "Channel"), value=f"<#{channel_id}>", inline=True)
        if details:
            embed.add_field(name=self._embed_label(None, "details", "Details"), value=details[:1024], inline=False)
        await self._send_event_embed(guild, "module_event", embed, module_name=module_key)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        # Optional cleanup to avoid stale config rows.
        if str(guild.id) in self._config:
            async with _CONFIG_LOCK:
                self._config.pop(str(guild.id), None)
                await asyncio.to_thread(_write_config_sync, self._config)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoggingCog(bot))
