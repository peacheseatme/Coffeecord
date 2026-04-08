import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

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

    def _build_status_embed(self, guild: discord.Guild, cfg: dict[str, Any]) -> discord.Embed:
        enabled = bool(cfg.get("enabled", False))
        channel_id = cfg.get("log_channel_id")
        channel_text = f"<#{channel_id}>" if isinstance(channel_id, int) else "Not set"
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
            title="Logging Status",
            color=discord.Color.green() if enabled else discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Enabled", value="Yes" if enabled else "No", inline=True)
        embed.add_field(name="Log Channel", value=channel_text, inline=True)
        embed.add_field(name="Events", value="\n".join(lines), inline=False)
        embed.add_field(name="Modules", value="\n".join(module_lines), inline=False)
        embed.set_footer(text="Coffeecord Logging")
        return embed

    @app_commands.command(name="status", description="Show logging status for this server.")
    async def logging_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        await interaction.response.send_message(embed=self._build_status_embed(interaction.guild, cfg), ephemeral=True)

    @app_commands.command(name="setup", description="Set logging channel and enable logging.")
    @app_commands.describe(channel="Channel where logs should be sent")
    async def logging_setup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        cfg["enabled"] = True
        cfg["log_channel_id"] = channel.id
        await self.save_logging_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            f"Logging enabled. Events will be sent to {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="toggle", description="Enable or disable a specific logging event.")
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
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        current = bool(cfg.get("events", {}).get(event.value, EVENT_DEFAULTS[event.value]))
        cfg["events"][event.value] = not current
        await self.save_logging_config(interaction.guild.id, cfg)
        state = "enabled" if cfg["events"][event.value] else "disabled"
        await interaction.response.send_message(f"`{event.value}` is now **{state}**.", ephemeral=True)

    @app_commands.command(name="module", description="Enable or disable a logging module.")
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
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        cfg["modules"][module.value] = enabled
        await self.save_logging_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            f"Module `{module.value}` is now **{'enabled' if enabled else 'disabled'}**.",
            ephemeral=True,
        )

    @app_commands.command(name="disable", description="Disable logging for this server.")
    async def logging_disable(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        cfg = await self.load_logging_config(interaction.guild.id)
        cfg["enabled"] = False
        await self.save_logging_config(interaction.guild.id, cfg)
        await interaction.response.send_message("Logging disabled for this server.", ephemeral=True)

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

        channel_value = interaction.channel.mention if interaction.channel else "Unknown"
        embed = discord.Embed(
            title="Command Used",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Type", value="Slash", inline=True)
        embed.add_field(name="Command", value=f"`/{command_name}`", inline=True)
        embed.add_field(name="User", value=interaction.user.mention, inline=True)
        embed.add_field(name="Channel", value=channel_value, inline=True)
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
        embed = discord.Embed(
            title="Command Used",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Type", value="Prefix", inline=True)
        embed.add_field(name="Command", value=f"`{ctx.clean_prefix}{command_name}`", inline=True)
        embed.add_field(name="User", value=ctx.author.mention, inline=True)
        embed.add_field(name="Channel", value=ctx.channel.mention, inline=True)
        await self._send_event_embed(ctx.guild, "command_use", embed, module_name="commands")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        embed = discord.Embed(
            title="Message Deleted",
            color=self._event_color("message_delete"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Channel", value=message.channel.mention if isinstance(message.channel, discord.abc.GuildChannel) else "Unknown", inline=True)
        content = (message.content or "").strip()
        embed.add_field(name="Content", value=content[:1024] if content else "No text content", inline=False)
        await self._send_event_embed(message.guild, "message_delete", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        embed = discord.Embed(
            title="Message Edited",
            color=self._event_color("message_edit"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Author", value=before.author.mention, inline=True)
        embed.add_field(name="Channel", value=before.channel.mention if isinstance(before.channel, discord.abc.GuildChannel) else "Unknown", inline=True)
        embed.add_field(name="Before", value=(before.content or "No text")[:1024], inline=False)
        embed.add_field(name="After", value=(after.content or "No text")[:1024], inline=False)
        await self._send_event_embed(before.guild, "message_edit", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        embed = discord.Embed(
            title="Member Joined",
            color=self._event_color("member_join"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style="R"), inline=False)
        await self._send_event_embed(member.guild, "member_join", embed, module_name="members")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        embed = discord.Embed(
            title="Member Left",
            color=self._event_color("member_leave"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
        await self._send_event_embed(member.guild, "member_leave", embed, module_name="members")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        before_to = before.timed_out_until
        after_to = after.timed_out_until
        if before_to != after_to:
            is_timeout_added = after_to is not None and (before_to is None or after_to > before_to)
            title = "Timeout Added" if is_timeout_added else "Timeout Removed"
            embed = discord.Embed(
                title=title,
                color=self._event_color("timeout"),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
            if after_to is not None:
                embed.add_field(name="Until", value=discord.utils.format_dt(after_to, style="F"), inline=False)
            await self._send_event_embed(after.guild, "timeout", embed, module_name="moderation")

        if before.nick != after.nick:
            embed = discord.Embed(
                title="Nickname Changed",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Before", value=before.nick or before.name, inline=True)
            embed.add_field(name="After", value=after.nick or after.name, inline=True)
            await self._send_event_embed(after.guild, "nickname_change", embed, module_name="members")

        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}
        added_role_ids = after_roles - before_roles
        removed_role_ids = before_roles - after_roles

        if added_role_ids:
            role_mentions = [f"<@&{role_id}>" for role_id in added_role_ids]
            embed = discord.Embed(
                title="Roles Added",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Roles", value=", ".join(role_mentions)[:1024], inline=False)
            await self._send_event_embed(after.guild, "role_assign", embed, module_name="moderation")

        if removed_role_ids:
            role_mentions = [f"<@&{role_id}>" for role_id in removed_role_ids]
            embed = discord.Embed(
                title="Roles Removed",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Roles", value=", ".join(role_mentions)[:1024], inline=False)
            await self._send_event_embed(after.guild, "role_remove", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = discord.Embed(
            title="Member Banned",
            color=self._event_color("ban"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
        await self._send_event_embed(guild, "ban", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = discord.Embed(
            title="Member Unbanned",
            color=self._event_color("unban"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
        await self._send_event_embed(guild, "unban", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = discord.Embed(title="Role Created", color=discord.Color.green(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)", inline=False)
        await self._send_event_embed(role.guild, "role_create", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = discord.Embed(title="Role Deleted", color=discord.Color.red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Role", value=f"{role.name} (`{role.id}`)", inline=False)
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
        embed = discord.Embed(title="Role Updated", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Role", value=f"{after.mention} (`{after.id}`)", inline=False)
        embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
        await self._send_event_embed(after.guild, "role_update", embed, module_name="moderation")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = discord.Embed(title="Channel Created", color=discord.Color.green(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Channel", value=f"{getattr(channel, 'mention', channel.name)} (`{channel.id}`)", inline=False)
        await self._send_event_embed(channel.guild, "channel_create", embed, module_name="messages")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = discord.Embed(title="Channel Deleted", color=discord.Color.red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Channel", value=f"{channel.name} (`{channel.id}`)", inline=False)
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
        embed = discord.Embed(title="Channel Updated", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Channel", value=f"{getattr(after, 'mention', after.name)} (`{after.id}`)", inline=False)
        embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
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
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title="Voice Join", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name="Channel", value=after.channel.mention, inline=False)
            await self._send_event_embed(member.guild, "voice_join", embed, module_name="members")
            return
        if before.channel is not None and after.channel is None:
            embed = discord.Embed(title="Voice Leave", color=discord.Color.red(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name="Channel", value=before.channel.mention, inline=False)
            await self._send_event_embed(member.guild, "voice_leave", embed, module_name="members")
            return
        if before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
            embed = discord.Embed(title="Voice Move", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name="From", value=before.channel.mention, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)
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
        embed = discord.Embed(
            title="Warn Issued",
            color=self._event_color("warn"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=False)
        embed.add_field(name="Moderator", value=(moderator.mention if moderator else "Automod/System"), inline=False)
        embed.add_field(name="Source", value=source, inline=True)
        embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024], inline=False)
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
        embed = discord.Embed(
            title="Automod Action",
            color=self._event_color("automod"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=False)
        embed.add_field(name="Rule", value=rule, inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
        embed.add_field(name="Message", value=f"https://discord.com/channels/{guild.id}/{channel_id}/{message_id}", inline=False)
        embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024], inline=False)
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
        embed = discord.Embed(
            title="Ticket Event",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="Actor", value=f"{actor.mention} (`{actor.id}`)", inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
        if details:
            embed.add_field(name="Details", value=details[:1024], inline=False)
        await self._send_event_embed(guild, "ticket_event", embed, module_name="tickets")

    @commands.Cog.listener("on_coffeecord_module_event")
    async def on_coffeecord_module_event(
        self,
        guild: discord.Guild,
        module_name: str,
        action: str,
        actor: Optional[discord.abc.User] = None,
        details: str = "",
        channel_id: Optional[int] = None,
    ) -> None:
        module_key = (module_name or "misc").strip().lower()
        embed = discord.Embed(
            title="Module Event",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Module", value=module_key, inline=True)
        embed.add_field(name="Action", value=action or "unknown", inline=True)
        if actor is not None:
            embed.add_field(name="Actor", value=f"{actor.mention} (`{actor.id}`)", inline=False)
        if channel_id is not None:
            embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
        if details:
            embed.add_field(name="Details", value=details[:1024], inline=False)
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
