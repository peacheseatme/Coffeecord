import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import t_sync

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "reactionrole_config.json"
_CONFIG_LOCK = asyncio.Lock()
LOGGER = logging.getLogger("coffeecord.reactionrole")

# Discord UI limit for options on one panel.
REACTION_ROLE_PANEL_MAX_ROLES = 25

# Distinct default emojis for guided multi-role reaction panels (one per mapping).
REACTION_PANEL_DEFAULT_EMOJIS: tuple[str, ...] = (
    "✅",
    "🔵",
    "🟢",
    "🟡",
    "🟠",
    "🔴",
    "🟣",
    "⚫",
    "⚪",
    "🟤",
    "🔷",
    "🔶",
    "⬜",
    "⬛",
    "◾",
    "◽",
    "\u25aa",
    "\u25ab",
    "🔸",
    "🔹",
    "🔺",
    "🔻",
    "💠",
    "🔘",
    "⭐",
)

DEFAULT_PANEL_TITLE = "Reaction Roles"
DEFAULT_PANEL_FOOTER = "Pick your roles • Coffeecord"
DEFAULT_PANEL_COLOR = 0x5865F2
REACTIONROLE_I18N_PREFIX = "reactionrole."


def _rr_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{REACTIONROLE_I18N_PREFIX}{key}", default=default, **params)


def _build_panel_embed(
    content: str,
    embed_title: str,
    embed_description: str,
    color: int,
    mappings: list[dict[str, Any]],
    guild: discord.Guild,
) -> discord.Embed:
    """Single embed for published reaction-role panels (no plain message content)."""
    title = ((embed_title or "").strip() or DEFAULT_PANEL_TITLE)[:256]
    parts: list[str] = []
    c = (content or "").strip()
    d = (embed_description or "").strip()
    if c:
        parts.append(c)
    if d:
        if parts:
            parts.append("")
        parts.append(d)
    description = "\n".join(parts) if parts else None
    try:
        clr = int(color) & 0xFFFFFF
    except (TypeError, ValueError):
        clr = DEFAULT_PANEL_COLOR
    emb = discord.Embed(title=title, description=description, color=clr)
    emb.set_footer(text=DEFAULT_PANEL_FOOTER)
    if guild.icon:
        emb.set_thumbnail(url=guild.icon.url)
    lines: list[str] = []
    cap = REACTION_ROLE_PANEL_MAX_ROLES
    for i, m in enumerate(mappings):
        if i >= cap:
            extra = len(mappings) - cap
            lines.append(f"_…and {extra} more._")
            break
        rid = int(m.get("role_id", 0))
        label = str(m.get("label") or "Toggle Role")[:80]
        emoji = m.get("emoji")
        bullet = str(emoji).strip() if emoji else "•"
        lines.append(f"{bullet} <@&{rid}> — {label}")
    if lines:
        field_val = "\n".join(lines)
        if len(field_val) > 1024:
            field_val = field_val[:1020] + "…"
        emb.add_field(name="Roles", value=field_val, inline=False)
    return emb


@dataclass
class ToggleResult:
    ok: bool
    message: str
    changed: Optional[str] = None  # "added" | "removed" | None
    role_id: Optional[int] = None


def _read_config_sync() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as fp:
            json.dump({}, fp, indent=2, ensure_ascii=True)
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config_sync(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


def _guild_default() -> dict[str, Any]:
    return {
        "enabled": True,
        "default_mode": "button",
        "default_logging": False,
        "messages": {},
    }


def _normalize_mapping(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    role_id = raw.get("role_id")
    if not str(role_id).isdigit():
        return None
    mapping_id = str(raw.get("id") or f"map_{uuid.uuid4().hex[:8]}")
    label = str(raw.get("label") or "Toggle Role").strip()[:80] or "Toggle Role"
    emoji = raw.get("emoji")
    emoji_text = str(emoji).strip()[:100] if emoji is not None else None
    return {
        "id": mapping_id,
        "role_id": int(role_id),
        "label": label,
        "emoji": emoji_text if emoji_text else None,
    }


def _normalize_message(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    channel_id = raw.get("channel_id")
    if not str(channel_id).isdigit():
        return None
    mode = str(raw.get("mode", "button")).lower()
    if mode not in {"button", "reaction"}:
        mode = "button"

    mappings_raw = raw.get("mappings", [])
    mappings: list[dict[str, Any]] = []
    if isinstance(mappings_raw, list):
        for m in mappings_raw:
            normalized = _normalize_mapping(m)
            if normalized is not None:
                mappings.append(normalized)
    if not mappings:
        return None

    embed_raw = raw.get("embed", {})
    if not isinstance(embed_raw, dict):
        embed_raw = {}

    max_roles = int(raw.get("max_roles", 0) or 0)
    required_raw = raw.get("required_role_ids", [])
    required_role_ids = [int(x) for x in required_raw if str(x).isdigit()] if isinstance(required_raw, list) else []

    return {
        "channel_id": int(channel_id),
        "mode": mode,
        "content": str(raw.get("content") or ""),
        "embed": {
            "title": str(embed_raw.get("title") or "")[:256],
            "description": str(embed_raw.get("description") or "")[:4000],
            "color": int(embed_raw.get("color", 0x5865F2) or 0x5865F2),
        },
        "mappings": mappings,
        "max_roles": max(max_roles, 0),
        "required_role_ids": required_role_ids,
        "remove_others": bool(raw.get("remove_others", False)),
        "logging": bool(raw.get("logging", False)),
    }


def _normalize_guild(raw: Any) -> dict[str, Any]:
    data = _guild_default()
    if not isinstance(raw, dict):
        return data
    data["enabled"] = bool(raw.get("enabled", True))
    default_mode = str(raw.get("default_mode", "button")).lower()
    data["default_mode"] = default_mode if default_mode in {"button", "reaction"} else "button"
    data["default_logging"] = bool(raw.get("default_logging", False))
    messages_raw = raw.get("messages", {})
    if isinstance(messages_raw, dict):
        for message_id, message_cfg in messages_raw.items():
            if not str(message_id).isdigit():
                continue
            normalized_message = _normalize_message(message_cfg)
            if normalized_message is not None:
                data["messages"][str(message_id)] = normalized_message
    return data


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------

class RRMessageModal(discord.ui.Modal, title="Reaction Role Message Setup"):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        super().__init__(timeout=300)
        self.parent_view = parent
        self.message_content = discord.ui.TextInput(
            label="Message content",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            default=parent.content or "",
            placeholder="Optional intro text (shown at top of embed)",
        )
        self.embed_title = discord.ui.TextInput(
            label="Embed title (optional)",
            required=False,
            max_length=256,
            default=parent.embed_title or "",
        )
        self.embed_description = discord.ui.TextInput(
            label="Embed description (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1500,
            default=parent.embed_description or "",
        )
        self.action_text = discord.ui.TextInput(
            label="Label / emoji (required for exactly one role)",
            required=False,
            max_length=80,
            default=parent.action_text or "",
            placeholder="One role: e.g. Verify Me or ✅. Several roles: optional (auto labels/emojis).",
        )
        self.add_item(self.message_content)
        self.add_item(self.embed_title)
        self.add_item(self.embed_description)
        self.add_item(self.action_text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.content = str(self.message_content.value or "").strip()
        self.parent_view.embed_title = str(self.embed_title.value or "").strip()
        self.parent_view.embed_description = str(self.embed_description.value or "").strip()
        self.parent_view.action_text = str(self.action_text.value or "").strip()
        await self.parent_view.refresh(interaction, "Message preview updated.")


class RRChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="1) Select target channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        resolved_channel: Optional[discord.TextChannel] = None
        if isinstance(selected, discord.TextChannel):
            resolved_channel = selected
        elif interaction.guild is not None and selected is not None:
            selected_id = getattr(selected, "id", None)
            if isinstance(selected_id, int):
                maybe_channel = interaction.guild.get_channel(selected_id)
                if isinstance(maybe_channel, discord.TextChannel):
                    resolved_channel = maybe_channel
        self.parent_view.channel = resolved_channel
        await self.parent_view.refresh(interaction, "Channel selected.")


class RRRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder=f"2) Select role(s) to toggle (up to {REACTION_ROLE_PANEL_MAX_ROLES})",
            min_values=1,
            max_values=REACTION_ROLE_PANEL_MAX_ROLES,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.roles = list(self.values)
        n = len(self.parent_view.roles)
        await self.parent_view.refresh(
            interaction,
            f"{n} role(s) selected — members can claim multiple from this panel unless you set exclusive mode in `/reactionrole edit`.",
        )


class RRModeSelect(discord.ui.Select):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Buttons (recommended)", value="button"),
            discord.SelectOption(label="Reactions", value="reaction"),
        ]
        super().__init__(placeholder="3) Choose interaction style", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.mode = self.values[0]
        await self.parent_view.refresh(interaction, "Style updated.")


class RRSetContentButton(discord.ui.Button):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(label="4) Set content + label/emoji", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RRMessageModal(self.parent_view))


class RRPublishButton(discord.ui.Button):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(label="Publish", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.publish(interaction)


class RRCancelButton(discord.ui.Button):
    def __init__(self, parent: "ReactionRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Setup cancelled.", embed=None, view=None)
        self.parent_view.stop()


class ReactionRoleSetupView(discord.ui.View):
    def __init__(self, cog: "ReactionRoleCog", invoker_id: int, default_mode: str, default_logging: bool) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.invoker_id = invoker_id
        self.channel: Optional[discord.TextChannel] = None
        self.roles: list[discord.Role] = []
        self.mode: str = default_mode if default_mode in {"button", "reaction"} else "button"
        self.logging_enabled: bool = default_logging
        self.content: str = ""
        self.embed_title: str = ""
        self.embed_description: str = ""
        self.action_text: str = "Toggle Role"
        self.add_item(RRChannelSelect(self))
        self.add_item(RRRoleSelect(self))
        self.add_item(RRModeSelect(self))
        self.add_item(RRSetContentButton(self))
        self.add_item(RRPublishButton(self))
        self.add_item(RRCancelButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                _rr_text_sync(
                    interaction.guild.id if interaction.guild else None,
                    "setup_owner_only",
                    default="This setup belongs to another moderator.",
                ),
                ephemeral=True,
            )
            return False
        return True

    def _preview_embed(self, status: str = "Configure your reaction role panel.") -> discord.Embed:
        embed = discord.Embed(title="Reaction Role Setup", color=discord.Color.blurple())
        embed.description = status
        embed.add_field(name="Channel", value=self.channel.mention if self.channel else "Not selected", inline=True)
        embed.add_field(name="Style", value=self.mode, inline=True)
        if not self.roles:
            roles_value = "Not selected"
        elif len(self.roles) <= 6:
            roles_value = ", ".join(r.mention for r in self.roles)
        else:
            roles_value = (
                f"**{len(self.roles)}** roles: "
                + ", ".join(r.mention for r in self.roles[:6])
                + " …"
            )
        embed.add_field(name="Roles", value=roles_value, inline=False)
        embed.add_field(name="Message content", value=(self.content[:200] or "None"), inline=False)
        embed.add_field(name="Embed title", value=self.embed_title or "None", inline=True)
        embed.add_field(name="Embed description", value=(self.embed_description[:200] or "None"), inline=False)
        embed.add_field(
            name="Button label / emoji",
            value=self.action_text or ("(auto per role)" if len(self.roles) > 1 else "Not set"),
            inline=True,
        )
        return embed

    async def refresh(self, interaction: discord.Interaction, status: str) -> None:
        await interaction.response.edit_message(embed=self._preview_embed(status), view=self)

    async def publish(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if self.channel is None:
            await interaction.response.send_message(
                _rr_text_sync(None, "select_channel_first", default="Select a channel first."),
                ephemeral=True,
            )
            return
        roles = list(self.roles)
        if not roles:
            await interaction.response.send_message(
                _rr_text_sync(None, "select_one_role", default="Select at least one role."),
                ephemeral=True,
            )
            return
        if len(roles) > REACTION_ROLE_PANEL_MAX_ROLES:
            await interaction.response.send_message(
                _rr_text_sync(
                    interaction.guild.id,
                    "max_roles_per_panel",
                    default="Select at most {max_roles} roles per panel.",
                    max_roles=str(REACTION_ROLE_PANEL_MAX_ROLES),
                ),
                ephemeral=True,
            )
            return

        action = (self.action_text or "").strip()
        if len(roles) == 1:
            if not action:
                await interaction.response.send_message(
                    _rr_text_sync(
                        interaction.guild.id,
                        "set_label_or_emoji_first",
                        default="Set the button label or reaction emoji first (step 4).",
                    ),
                    ephemeral=True,
                )
                return
            mappings = [
                {
                    "id": f"map_{uuid.uuid4().hex[:8]}",
                    "role_id": roles[0].id,
                    "label": action if self.mode == "button" else "Toggle Role",
                    "emoji": action if self.mode == "reaction" else None,
                }
            ]
        else:
            if self.mode == "reaction" and len(roles) > len(REACTION_PANEL_DEFAULT_EMOJIS):
                await interaction.response.send_message(
                    _rr_text_sync(
                        interaction.guild.id,
                        "reaction_mode_max_guided",
                        default="Reaction mode supports up to {max_roles} roles in guided setup. Use fewer roles here or add more with `/reactionrole edit`.",
                        max_roles=str(len(REACTION_PANEL_DEFAULT_EMOJIS)),
                    ),
                    ephemeral=True,
                )
                return
            mappings = []
            for i, role in enumerate(roles):
                if self.mode == "button":
                    label = (role.name or "Role")[:80]
                    emoji = None
                else:
                    label = "Toggle Role"
                    emoji = REACTION_PANEL_DEFAULT_EMOJIS[i]
                mappings.append(
                    {
                        "id": f"map_{uuid.uuid4().hex[:8]}",
                        "role_id": role.id,
                        "label": label,
                        "emoji": emoji,
                    }
                )

        panel_color = DEFAULT_PANEL_COLOR
        panel_embed = _build_panel_embed(
            self.content,
            self.embed_title,
            self.embed_description,
            panel_color,
            mappings,
            interaction.guild,
        )
        panel_message = await self.channel.send(embed=panel_embed)

        item_cfg = {
            "channel_id": self.channel.id,
            "mode": self.mode,
            "content": self.content,
            "embed": {
                "title": self.embed_title,
                "description": self.embed_description,
                "color": panel_color,
            },
            "mappings": mappings,
            "max_roles": 0,
            "required_role_ids": [],
            "remove_others": False,
            "logging": self.logging_enabled,
        }
        await self.cog.upsert_message_config(interaction.guild.id, panel_message.id, item_cfg)

        if self.mode == "button":
            view = self.cog.build_button_view(interaction.guild.id, panel_message.id, item_cfg)
            await panel_message.edit(view=view)
            self.cog.bot.add_view(view, message_id=panel_message.id)
        else:
            failed: list[str] = []
            for m in mappings:
                em = m.get("emoji")
                if not em:
                    continue
                try:
                    await panel_message.add_reaction(str(em))
                except discord.HTTPException:
                    failed.append(str(em))
            if failed:
                await interaction.response.send_message(
                    _rr_text_sync(
                        interaction.guild.id,
                        "reaction_add_partial_failure",
                        default="Panel created, but some reactions could not be added: {failed}. Use `/reactionrole edit` to fix emojis.",
                        failed=", ".join(failed),
                    ),
                    ephemeral=True,
                )
                return

        summary = f"{len(mappings)} role option(s)"
        await interaction.response.edit_message(
            content=(
                _rr_text_sync(
                    interaction.guild.id,
                    "panel_created",
                    default="✅ Reaction role panel created in {channel} (`{message_id}`) — {summary}. Members can pick multiple roles from this panel unless you set **remove_others** or **max_roles** in `/reactionrole edit`.",
                    channel=self.channel.mention,
                    message_id=str(panel_message.id),
                    summary=summary,
                )
            ),
            embed=None,
            view=None,
        )
        self.stop()


class ReactionRoleButton(discord.ui.Button):
    def __init__(self, cog: "ReactionRoleCog", guild_id: int, message_id: int, mapping: dict[str, Any]) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.mapping_id = mapping["id"]
        emoji = mapping.get("emoji")
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=(mapping.get("label") or "Toggle Role")[:80],
            emoji=emoji if emoji else None,
            custom_id=f"rr:{self.message_id}:{self.mapping_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        result = await self.cog.handle_toggle(interaction.guild, interaction.user, self.message_id, self.mapping_id, source="button")
        await interaction.response.send_message(result.message, ephemeral=True)


class ReactionRoleButtonView(discord.ui.View):
    def __init__(self, cog: "ReactionRoleCog", guild_id: int, message_id: int, mappings: list[dict[str, Any]]) -> None:
        super().__init__(timeout=None)
        for mapping in mappings[:25]:
            self.add_item(ReactionRoleButton(cog, guild_id, message_id, mapping))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ReactionRoleCog(
    commands.GroupCog,
    group_name="reactionrole",
    group_description="Create and manage reaction role panels.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._config: dict[str, Any] = {}

    async def cog_load(self) -> None:
        await self.reload_config()
        await self.register_persistent_views()

    async def reload_config(self) -> None:
        async with _CONFIG_LOCK:
            raw = await asyncio.to_thread(_read_config_sync)
            normalized: dict[str, Any] = {}
            for guild_id, guild_cfg in raw.items():
                if str(guild_id).isdigit():
                    normalized[str(guild_id)] = _normalize_guild(guild_cfg)
            self._config = normalized
            await asyncio.to_thread(_write_config_sync, self._config)

    async def save_config(self) -> None:
        async with _CONFIG_LOCK:
            await asyncio.to_thread(_write_config_sync, self._config)

    async def get_guild_config(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        cfg = self._config.get(key)
        if cfg is None:
            cfg = _guild_default()
            self._config[key] = cfg
            await self.save_config()
        return _normalize_guild(cfg)

    async def upsert_message_config(self, guild_id: int, message_id: int, item_cfg: dict[str, Any]) -> None:
        cfg = await self.get_guild_config(guild_id)
        cfg["messages"][str(message_id)] = _normalize_message(item_cfg) or item_cfg
        self._config[str(guild_id)] = cfg
        await self.save_config()

    def build_button_view(self, guild_id: int, message_id: int, item_cfg: dict[str, Any]) -> ReactionRoleButtonView:
        return ReactionRoleButtonView(self, guild_id, message_id, item_cfg["mappings"])

    async def register_persistent_views(self) -> None:
        for guild_id_str, guild_cfg in self._config.items():
            if not str(guild_id_str).isdigit():
                continue
            guild_id = int(guild_id_str)
            for message_id, item in guild_cfg.get("messages", {}).items():
                if item.get("mode") != "button":
                    continue
                if not str(message_id).isdigit():
                    continue
                view = self.build_button_view(guild_id, int(message_id), item)
                self.bot.add_view(view, message_id=int(message_id))

    async def republish_panels_after_restore(self, guild: discord.Guild, *, force: bool = False) -> list[str]:
        """Re-post reaction-role panels after backup restore.

        Repair (force=False): keep panels whose message IDs still resolve.
        Overwrite (force=True): always create fresh panels.
        """
        await self.reload_config()
        cfg = await self.get_guild_config(guild.id)
        old_messages = dict(cfg.get("messages") or {})
        if not old_messages:
            return []
        notes: list[str] = []
        next_messages: dict[str, Any] = {}

        async def _publish_one(item: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str | None]:
            channel_id = item.get("channel_id")
            if channel_id is None or not str(channel_id).isdigit():
                return None, None, "reactionrole: panel missing channel_id"
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                return None, None, f"reactionrole: channel `{channel_id}` missing"
            mappings = item.get("mappings") or []
            if not mappings:
                return None, None, None
            emb_raw = item.get("embed") or {}
            if not isinstance(emb_raw, dict):
                emb_raw = {}
            panel_embed = _build_panel_embed(
                str(item.get("content") or ""),
                str(emb_raw.get("title") or ""),
                str(emb_raw.get("description") or ""),
                int(emb_raw.get("color", DEFAULT_PANEL_COLOR) or DEFAULT_PANEL_COLOR),
                mappings,
                guild,
            )
            panel_message = await channel.send(embed=panel_embed)
            item_cfg = {
                "channel_id": channel.id,
                "mode": item.get("mode", "button"),
                "content": item.get("content") or "",
                "embed": emb_raw,
                "mappings": mappings,
                "max_roles": int(item.get("max_roles", 0) or 0),
                "required_role_ids": item.get("required_role_ids") or [],
                "remove_others": bool(item.get("remove_others", False)),
                "logging": bool(item.get("logging", False)),
            }
            if item_cfg["mode"] == "button":
                view = self.build_button_view(guild.id, panel_message.id, item_cfg)
                await panel_message.edit(view=view)
                self.bot.add_view(view, message_id=panel_message.id)
            else:
                for m in mappings:
                    em = m.get("emoji")
                    if not em:
                        continue
                    try:
                        await panel_message.add_reaction(str(em))
                    except discord.HTTPException:
                        pass
            return str(panel_message.id), item_cfg, f"reactionrole: panel republished in #{channel.name}"

        for mid, item in old_messages.items():
            if not isinstance(item, dict):
                continue
            if not force and str(mid).isdigit():
                channel_id = item.get("channel_id")
                channel = guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
                if isinstance(channel, discord.TextChannel):
                    try:
                        msg = await channel.fetch_message(int(mid))
                        next_messages[str(mid)] = item
                        if item.get("mode") == "button":
                            view = self.build_button_view(guild.id, int(mid), item)
                            self.bot.add_view(view, message_id=int(mid))
                            try:
                                await msg.edit(view=view)
                            except discord.HTTPException:
                                pass
                        notes.append(f"reactionrole: existing panel kept in #{channel.name}")
                        continue
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    # Backup message id may be stale after repair; reuse a live panel in-channel.
                    if item.get("mode") == "button":
                        claimed = {int(k) for k in next_messages if str(k).isdigit()}
                        found = await self._find_existing_button_panel(channel, prefix="rr:", claimed=claimed)
                        if found is not None:
                            next_messages[str(found.id)] = item
                            view = self.build_button_view(guild.id, found.id, item)
                            self.bot.add_view(view, message_id=found.id)
                            try:
                                await found.edit(view=view)
                            except discord.HTTPException:
                                pass
                            notes.append(f"reactionrole: existing panel kept in #{channel.name}")
                            continue
            try:
                new_mid, item_cfg, note = await _publish_one(item)
                if new_mid and item_cfg is not None:
                    next_messages[new_mid] = item_cfg
                if note:
                    notes.append(note)
            except Exception as exc:
                notes.append(f"reactionrole: failed: {exc}")

        cfg["messages"] = next_messages
        self._config[str(guild.id)] = cfg
        await self.save_config()
        return notes

    async def _find_existing_button_panel(
        self,
        channel: discord.TextChannel,
        *,
        prefix: str,
        claimed: set[int],
    ) -> discord.Message | None:
        try:
            async for msg in channel.history(limit=50):
                if msg.id in claimed:
                    continue
                for row in msg.components:
                    for child in row.children:
                        custom_id = getattr(child, "custom_id", None)
                        if isinstance(custom_id, str) and custom_id.startswith(prefix):
                            return msg
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    def _find_mapping(self, item_cfg: dict[str, Any], mapping_id: str) -> Optional[dict[str, Any]]:
        for mapping in item_cfg.get("mappings", []):
            if str(mapping.get("id")) == str(mapping_id):
                return mapping
        return None

    async def _emit_hook(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action: str,
        role_id: int,
        message_id: int,
        channel_id: int,
    ) -> None:
        try:
            self.bot.dispatch(
                "coffeecord_module_event",
                guild,
                "reactionrole",
                action,
                member,
                f"role_id={role_id}; message_id={message_id}",
                channel_id,
            )
        except Exception:
            return

    async def _check_assignable(self, guild: discord.Guild, member: discord.Member, role: discord.Role) -> Optional[str]:
        me = guild.me
        if me is None:
            return _rr_text_sync(member.id, "bot_member_unavailable", default="Bot member cache is unavailable.")
        if not guild.me.guild_permissions.manage_roles:
            return _rr_text_sync(member.id, "missing_manage_roles", default="I need `Manage Roles` permission.")
        if role >= me.top_role:
            return _rr_text_sync(member.id, "cannot_manage_role_hierarchy", default="I cannot manage that role due to role hierarchy.")
        if role >= member.top_role and member != guild.owner:
            return _rr_text_sync(member.id, "target_role_hierarchy_block", default="Role hierarchy prevents this change.")
        return None

    async def handle_toggle(
        self,
        guild: discord.Guild,
        member: discord.Member,
        message_id: int,
        mapping_id: str,
        source: str,
    ) -> ToggleResult:
        cfg = await self.get_guild_config(guild.id)
        if not cfg.get("enabled", True):
            return ToggleResult(False, _rr_text_sync(member.id, "disabled", default="Reaction roles are disabled for this server."))

        item = cfg.get("messages", {}).get(str(message_id))
        if not item:
            return ToggleResult(False, _rr_text_sync(member.id, "panel_missing", default="This reaction role panel no longer exists."))

        mapping = self._find_mapping(item, mapping_id)
        if not mapping:
            return ToggleResult(False, _rr_text_sync(member.id, "role_option_missing", default="This role option no longer exists."))

        role = guild.get_role(int(mapping["role_id"]))
        if role is None:
            return ToggleResult(False, _rr_text_sync(member.id, "mapped_role_missing", default="That role no longer exists. Ask staff to update this panel."))

        missing_required = [rid for rid in item.get("required_role_ids", []) if rid not in {r.id for r in member.roles}]
        if missing_required:
            return ToggleResult(
                False,
                _rr_text_sync(
                    guild.id,
                    "missing_required_roles",
                    default="You need required role(s): {roles}",
                    roles=", ".join(f"<@&{rid}>" for rid in missing_required),
                ),
            )

        hierarchy_error = await self._check_assignable(guild, member, role)
        if hierarchy_error:
            return ToggleResult(False, hierarchy_error)

        mapping_role_ids = {int(m["role_id"]) for m in item.get("mappings", [])}
        current_from_panel = [r for r in member.roles if r.id in mapping_role_ids]
        has_target = role in member.roles

        if has_target:
            try:
                await member.remove_roles(role, reason=f"ReactionRole toggle ({source})")
            except discord.HTTPException:
                return ToggleResult(False, _rr_text_sync(member.id, "remove_role_failed", default="I couldn't remove that role due to a Discord API error."))
            if item.get("logging", False):
                await self._emit_hook(guild, member, "role_removed", role.id, message_id, item["channel_id"])
            return ToggleResult(
                True,
                _rr_text_sync(member.id, "remove_role_success", default="Removed {role}.", role=role.mention),
                changed="removed",
                role_id=role.id,
            )

        # Add path
        max_roles = int(item.get("max_roles", 0) or 0)
        remove_others = bool(item.get("remove_others", False))
        if remove_others and current_from_panel:
            try:
                await member.remove_roles(*current_from_panel, reason="ReactionRole exclusive selection")
            except discord.HTTPException:
                return ToggleResult(False, _rr_text_sync(member.id, "update_existing_roles_failed", default="I couldn't update your existing panel roles."))
            current_from_panel = []

        if max_roles > 0 and len(current_from_panel) >= max_roles:
            return ToggleResult(
                False,
                _rr_text_sync(
                    guild.id,
                    "max_roles_reached",
                    default="You can only hold {max_roles} role(s) from this panel.",
                    max_roles=str(max_roles),
                ),
            )

        try:
            await member.add_roles(role, reason=f"ReactionRole toggle ({source})")
        except discord.HTTPException:
            return ToggleResult(False, _rr_text_sync(member.id, "add_role_failed", default="I couldn't add that role due to a Discord API error."))

        if item.get("logging", False):
            await self._emit_hook(guild, member, "role_added", role.id, message_id, item["channel_id"])
        return ToggleResult(
            True,
            _rr_text_sync(member.id, "add_role_success", default="Added {role}.", role=role.mention),
            changed="added",
            role_id=role.id,
        )

    async def _toggle_for_reaction_payload(self, payload: discord.RawReactionActionEvent, removed: bool) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        cfg = await self.get_guild_config(guild.id)
        item = cfg.get("messages", {}).get(str(payload.message_id))
        if not item or item.get("mode") != "reaction":
            return
        member = payload.member
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return

        emoji_text = str(payload.emoji)
        mapping = next((m for m in item.get("mappings", []) if str(m.get("emoji")) == emoji_text), None)
        if not mapping:
            return

        role = guild.get_role(int(mapping["role_id"]))
        if role is None:
            return

        if removed:
            if role in member.roles:
                err = await self._check_assignable(guild, member, role)
                if err:
                    LOGGER.warning("Reaction role remove blocked: %s", err)
                    return
                try:
                    await member.remove_roles(role, reason="ReactionRole reaction removed")
                    if item.get("logging", False):
                        await self._emit_hook(guild, member, "role_removed", role.id, payload.message_id, payload.channel_id)
                except discord.HTTPException:
                    LOGGER.warning("Failed removing role %s for reaction remove.", role.id)
            return

        await self.handle_toggle(guild, member, payload.message_id, mapping["id"], source="reaction")

    # -----------------------------------------------------------------------
    # Slash commands
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="create",
        description="Create a reaction role panel with guided setup (select multiple roles at once).",
)
    async def reactionrole_create(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        view = ReactionRoleSetupView(
            cog=self,
            invoker_id=interaction.user.id,
            default_mode=cfg.get("default_mode", "button"),
            default_logging=bool(cfg.get("default_logging", False)),
        )
        await interaction.response.send_message(embed=view._preview_embed(), view=view, ephemeral=True)

    @app_commands.command(
        name="list",
        description="List reaction role panels in this server.",
)
    async def reactionrole_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        messages = cfg.get("messages", {})
        embed = discord.Embed(
            title=_rr_text_sync(interaction.user.id, "list_title", default="Reaction Role Panels"),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=_rr_text_sync(interaction.user.id, "field_enabled", default="Enabled"),
            value=t_sync(interaction.user.id, "common.yes") if cfg.get("enabled", True) else t_sync(interaction.user.id, "common.no"),
            inline=True,
        )
        embed.add_field(
            name=_rr_text_sync(interaction.user.id, "field_panels", default="Panels"),
            value=str(len(messages)),
            inline=True,
        )
        if not messages:
            embed.description = _rr_text_sync(interaction.user.id, "list_empty", default="No reaction role panels configured.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        lines = []
        for message_id, item in list(messages.items())[:20]:
            lines.append(
                f"`{message_id}` • <#{item['channel_id']}> • `{item['mode']}` • {len(item.get('mappings', []))} role option(s)"
            )
        embed.add_field(
            name=_rr_text_sync(interaction.user.id, "field_configured_panels", default="Configured Panels"),
            value="\n".join(lines),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="delete",
        description="Delete a reaction role panel by message ID.",
)
    async def reactionrole_delete(self, interaction: discord.Interaction, message_id: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not message_id.isdigit():
            await interaction.response.send_message(
                _rr_text_sync(interaction.user.id, "message_id_numeric", default="Message ID must be numeric."),
                ephemeral=True,
            )
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        item = cfg.get("messages", {}).pop(message_id, None)
        if item is None:
            await interaction.response.send_message(
                _rr_text_sync(interaction.user.id, "panel_not_found", default="Panel not found."),
                ephemeral=True,
            )
            return
        self._config[str(interaction.guild.id)] = cfg
        await self.save_config()

        channel = interaction.guild.get_channel(int(item["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
        await interaction.response.send_message(
            _rr_text_sync(
                interaction.guild.id,
                "delete_success",
                default="Deleted panel `{message_id}`.",
                message_id=message_id,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="config",
        description="Set defaults for reaction role panels in this server.",
)
    @app_commands.describe(enabled="Enable or disable reaction roles globally for this guild.")
    @app_commands.choices(
        default_mode=[
            app_commands.Choice(name="Buttons (recommended)", value="button"),
            app_commands.Choice(name="Reactions", value="reaction"),
        ]
    )
    async def reactionrole_config(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        default_mode: Optional[app_commands.Choice[str]] = None,
        default_logging: Optional[bool] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        if enabled is not None:
            cfg["enabled"] = enabled
        if default_mode is not None:
            cfg["default_mode"] = default_mode.value
        if default_logging is not None:
            cfg["default_logging"] = default_logging
        self._config[str(interaction.guild.id)] = cfg
        await self.save_config()
        await interaction.response.send_message(
            _rr_text_sync(
                interaction.guild.id,
                "config_updated",
                default="Updated config: enabled={enabled}, default_mode={mode}, default_logging={logging}",
                enabled=str(cfg["enabled"]),
                mode=str(cfg["default_mode"]),
                logging=str(cfg["default_logging"]),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="edit",
        description="Edit mappings and advanced options for a panel.",
)
    @app_commands.describe(
        message_id="Target panel message ID.",
        role="Role to add/update/remove in this panel.",
        remove_mapping="If true, remove the mapping for the provided role.",
        button_label="Label for button mode mapping.",
        emoji="Emoji for reaction mode mapping.",
        max_roles="Max roles a user can hold from this panel (0 = unlimited).",
        required_role="Role users must already have to claim from this panel.",
        clear_required_roles="Clear all required roles from panel.",
        remove_others="If true, selecting one option removes others from same panel.",
        logging_enabled="Enable event hook dispatch when role changes.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Buttons", value="button"),
            app_commands.Choice(name="Reactions", value="reaction"),
        ]
    )
    async def reactionrole_edit(
        self,
        interaction: discord.Interaction,
        message_id: str,
        role: Optional[discord.Role] = None,
        mode: Optional[app_commands.Choice[str]] = None,
        remove_mapping: bool = False,
        button_label: Optional[str] = None,
        emoji: Optional[str] = None,
        max_roles: Optional[int] = None,
        required_role: Optional[discord.Role] = None,
        clear_required_roles: bool = False,
        remove_others: Optional[bool] = None,
        logging_enabled: Optional[bool] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not message_id.isdigit():
            await interaction.response.send_message(
                _rr_text_sync(interaction.user.id, "message_id_numeric", default="Message ID must be numeric."),
                ephemeral=True,
            )
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        item = cfg.get("messages", {}).get(message_id)
        if item is None:
            await interaction.response.send_message(
                _rr_text_sync(interaction.user.id, "panel_not_found", default="Panel not found."),
                ephemeral=True,
            )
            return

        if mode is not None:
            item["mode"] = mode.value
        if max_roles is not None:
            item["max_roles"] = max(max_roles, 0)
        if remove_others is not None:
            item["remove_others"] = remove_others
        if logging_enabled is not None:
            item["logging"] = logging_enabled
        if clear_required_roles:
            item["required_role_ids"] = []
        if required_role is not None and required_role.id not in item["required_role_ids"]:
            item["required_role_ids"].append(required_role.id)

        if role is not None:
            existing = next((m for m in item["mappings"] if int(m["role_id"]) == role.id), None)
            if remove_mapping:
                if existing is None:
                    await interaction.response.send_message(
                        _rr_text_sync(interaction.user.id, "role_not_mapped", default="That role is not mapped on this panel."),
                        ephemeral=True,
                    )
                    return
                item["mappings"] = [m for m in item["mappings"] if int(m["role_id"]) != role.id]
            else:
                if existing is None:
                    existing = {
                        "id": f"map_{uuid.uuid4().hex[:8]}",
                        "role_id": role.id,
                        "label": (button_label or role.name)[:80],
                        "emoji": emoji or None,
                    }
                    item["mappings"].append(existing)
                else:
                    if button_label is not None:
                        existing["label"] = button_label[:80]
                    if emoji is not None:
                        existing["emoji"] = emoji
        elif remove_mapping:
            await interaction.response.send_message(
                _rr_text_sync(interaction.user.id, "provide_role_for_remove", default="Provide `role` when using `remove_mapping`."),
                ephemeral=True,
            )
            return

        if not item["mappings"]:
            await interaction.response.send_message(
                _rr_text_sync(interaction.user.id, "panel_requires_one_mapping", default="Panel must keep at least one role mapping."),
                ephemeral=True,
            )
            return

        cfg["messages"][message_id] = item
        self._config[str(interaction.guild.id)] = cfg
        await self.save_config()

        channel = interaction.guild.get_channel(int(item["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            try:
                msg = await channel.fetch_message(int(message_id))
                emb_raw = item.get("embed") or {}
                if not isinstance(emb_raw, dict):
                    emb_raw = {}
                panel_embed = _build_panel_embed(
                    str(item.get("content") or ""),
                    str(emb_raw.get("title") or ""),
                    str(emb_raw.get("description") or ""),
                    int(emb_raw.get("color", DEFAULT_PANEL_COLOR) or DEFAULT_PANEL_COLOR),
                    list(item.get("mappings") or []),
                    interaction.guild,
                )
                if item["mode"] == "button":
                    view = self.build_button_view(interaction.guild.id, int(message_id), item)
                    await msg.edit(embed=panel_embed, view=view)
                    self.bot.add_view(view, message_id=int(message_id))
                else:
                    await msg.edit(embed=panel_embed, view=None)
                    for mapping in item["mappings"]:
                        emoji_text = mapping.get("emoji")
                        if emoji_text:
                            try:
                                await msg.add_reaction(emoji_text)
                            except discord.HTTPException:
                                LOGGER.warning("Invalid emoji in reactionrole panel %s", message_id)
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            _rr_text_sync(
                interaction.guild.id,
                "panel_updated",
                default="Updated panel `{message_id}`.",
                message_id=message_id,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # Event listeners
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._toggle_for_reaction_payload(payload, removed=False)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._toggle_for_reaction_payload(payload, removed=True)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        cfg = await self.get_guild_config(role.guild.id)
        changed = False
        for message_id, item in list(cfg.get("messages", {}).items()):
            old_len = len(item["mappings"])
            item["mappings"] = [m for m in item["mappings"] if int(m["role_id"]) != role.id]
            item["required_role_ids"] = [rid for rid in item.get("required_role_ids", []) if rid != role.id]
            if not item["mappings"]:
                cfg["messages"].pop(message_id, None)
                changed = True
                continue
            if len(item["mappings"]) != old_len:
                changed = True
        if changed:
            self._config[str(role.guild.id)] = cfg
            await self.save_config()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if str(guild.id) in self._config:
            self._config.pop(str(guild.id), None)
            await self.save_config()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoleCog(bot))
