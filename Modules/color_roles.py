"""
Color role picker: exclusive self-assign color roles via buttons or emoji reactions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import t_sync

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "color_roles.json"
MUTE_ROLES_PATH = BASE_DIR / "Storage" / "Config" / "mute_roles.json"
_CONFIG_LOCK = asyncio.Lock()
LOGGER = logging.getLogger("coffeecord.color_roles")

COLOR_ROLE_PANEL_MAX = 10
DEFAULT_PANEL_TITLE = "Color Roles"
DEFAULT_PANEL_FOOTER = "Pick your color • Coffeecord"
DEFAULT_PANEL_COLOR = 0x5865F2
DEFAULT_PANEL_CONTENT = "Pick your color role below."
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_VALUE_MAX = 1024
COLOR_ROLES_I18N_PREFIX = "color_roles."

MODERATION_PERMISSIONS = (
    "administrator",
    "ban_members",
    "manage_guild",
    "moderate_members",
    "manage_roles",
)


def _color_roles_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{COLOR_ROLES_I18N_PREFIX}{key}", default=default, **params)


class ColorPreset(NamedTuple):
    key: str
    name: str
    hex_value: int
    emoji: str


DISCORD_COLOR_PRESETS: tuple[ColorPreset, ...] = (
    ColorPreset("red", "Red", 0xED4245, "🔴"),
    ColorPreset("orange", "Orange", 0xFAA61A, "🟠"),
    ColorPreset("yellow", "Yellow", 0xFEE75C, "🟡"),
    ColorPreset("green", "Green", 0x57F287, "🟢"),
    ColorPreset("blue", "Blue", 0x5865F2, "🔵"),
    ColorPreset("purple", "Purple", 0x9B59B6, "🟣"),
    ColorPreset("pink", "Pink", 0xEB459E, "🩷"),
    ColorPreset("teal", "Teal", 0x1ABC9C, "🩵"),
    ColorPreset("gray", "Gray", 0x99AAB5, "⚪"),
    ColorPreset("dark", "Dark", 0x23272A, "⚫"),
)

_PRESET_BY_NAME = {p.name.lower(): p for p in DISCORD_COLOR_PRESETS}
_PRESET_BY_KEY = {p.key: p for p in DISCORD_COLOR_PRESETS}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


@dataclass
class PickResult:
    ok: bool
    message: str
    changed: Optional[str] = None
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


def _read_mute_role_id_sync(guild_id: int) -> Optional[int]:
    if not MUTE_ROLES_PATH.exists():
        return None
    try:
        with MUTE_ROLES_PATH.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)
        if not isinstance(raw, dict):
            return None
        value = raw.get(str(guild_id))
        return int(value) if str(value).isdigit() else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _guild_default() -> dict[str, Any]:
    return {
        "enabled": True,
        "default_mode": "button",
        "preset_role_ids": [],
        "draft": {},
        "messages": {},
    }


def _normalize_mapping(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    role_id = raw.get("role_id")
    if not str(role_id).isdigit():
        return None
    mapping_id = str(raw.get("id") or f"cr_{uuid.uuid4().hex[:8]}")
    label = str(raw.get("label") or "Color").strip()[:80] or "Color"
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
    if mode not in {"button", "emoji"}:
        mode = "button"

    mappings_raw = raw.get("mappings", [])
    mappings: list[dict[str, Any]] = []
    if isinstance(mappings_raw, list):
        for item in mappings_raw:
            normalized = _normalize_mapping(item)
            if normalized is not None:
                mappings.append(normalized)
    if not mappings:
        return None

    embed_raw = raw.get("embed", {})
    if not isinstance(embed_raw, dict):
        embed_raw = {}

    return {
        "channel_id": int(channel_id),
        "mode": mode,
        "content": str(raw.get("content") or ""),
        "embed": {
            "title": str(embed_raw.get("title") or "")[:256],
            "description": str(embed_raw.get("description") or "")[:4000],
            "color": int(embed_raw.get("color", DEFAULT_PANEL_COLOR) or DEFAULT_PANEL_COLOR),
        },
        "mappings": mappings[:COLOR_ROLE_PANEL_MAX],
        "logging": bool(raw.get("logging", False)),
    }


def _normalize_guild(raw: Any) -> dict[str, Any]:
    data = _guild_default()
    if not isinstance(raw, dict):
        return data
    data["enabled"] = bool(raw.get("enabled", True))
    default_mode = str(raw.get("default_mode", "button")).lower()
    data["default_mode"] = default_mode if default_mode in {"button", "emoji"} else "button"
    preset_raw = raw.get("preset_role_ids", [])
    if isinstance(preset_raw, list):
        data["preset_role_ids"] = [int(x) for x in preset_raw if str(x).isdigit()]
    draft_raw = raw.get("draft", {})
    if isinstance(draft_raw, dict):
        data["draft"] = draft_raw
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
# Color / role helpers
# ---------------------------------------------------------------------------


def _join_embed_field_lines(lines: list[str], max_len: int = EMBED_FIELD_VALUE_MAX) -> str:
    """Join lines for an embed field without exceeding Discord's length cap."""
    if not lines:
        return ""
    included: list[str] = []
    current_len = 0
    for index, line in enumerate(lines):
        sep = 1 if included else 0
        if current_len + sep + len(line) > max_len:
            remaining = len(lines) - index
            if remaining > 0:
                suffix = f"_…and {remaining} more._"
                if current_len + (1 if included else 0) + len(suffix) <= max_len:
                    included.append(suffix)
            break
        included.append(line)
        current_len += sep + len(line)
    body = "\n".join(included)
    if len(body) > max_len:
        body = body[: max_len - 1] + "…"
    return body


def _build_panel_embed(
    content: str,
    embed_title: str,
    embed_description: str,
    color: int,
    mappings: list[dict[str, Any]],
    guild: discord.Guild,
) -> discord.Embed:
    title = ((embed_title or "").strip() or DEFAULT_PANEL_TITLE)[:256]
    parts: list[str] = []
    body = (content or "").strip()
    desc = (embed_description or "").strip()
    if body:
        parts.append(body)
    if desc:
        if parts:
            parts.append("")
        parts.append(desc)
    description = "\n".join(parts) if parts else None
    if description and len(description) > EMBED_DESCRIPTION_MAX:
        description = description[: EMBED_DESCRIPTION_MAX - 1] + "…"
    try:
        clr = int(color) & 0xFFFFFF
    except (TypeError, ValueError):
        clr = DEFAULT_PANEL_COLOR
    emb = discord.Embed(title=title, description=description, color=clr)
    emb.set_footer(text=DEFAULT_PANEL_FOOTER)
    if guild.icon:
        emb.set_thumbnail(url=guild.icon.url)
    lines: list[str] = []
    for i, mapping in enumerate(mappings):
        if i >= COLOR_ROLE_PANEL_MAX:
            extra = len(mappings) - COLOR_ROLE_PANEL_MAX
            lines.append(f"_…and {extra} more._")
            break
        rid = int(mapping.get("role_id", 0))
        label = str(mapping.get("label") or "Color")[:40]
        emoji = mapping.get("emoji")
        bullet = str(emoji).strip() if emoji else "•"
        lines.append(f"{bullet} <@&{rid}> — {label}")
    if lines:
        field_val = _join_embed_field_lines(lines)
        emb.add_field(name="Colors", value=field_val, inline=False)
    return emb


def _preset_for_role(role: discord.Role) -> Optional[ColorPreset]:
    if role.name.lower() in _PRESET_BY_NAME:
        return _PRESET_BY_NAME[role.name.lower()]
    if not role.color or role.color.value == 0:
        return None
    best: Optional[ColorPreset] = None
    best_dist = float("inf")
    rv = role.color.value
    for preset in DISCORD_COLOR_PRESETS:
        dist = abs((rv & 0xFF) - (preset.hex_value & 0xFF))
        dist += abs(((rv >> 8) & 0xFF) - ((preset.hex_value >> 8) & 0xFF))
        dist += abs(((rv >> 16) & 0xFF) - ((preset.hex_value >> 16) & 0xFF))
        if dist < best_dist:
            best_dist = dist
            best = preset
    return best


def _emoji_for_role(role: discord.Role) -> str:
    preset = _preset_for_role(role)
    return preset.emoji if preset else "🎨"


def _label_for_role(role: discord.Role) -> str:
    preset = _preset_for_role(role)
    return preset.name if preset else (role.name or "Color")[:80]


def _colour_for_role(role: discord.Role) -> discord.Colour:
    preset = _preset_for_role(role)
    if preset is not None:
        return discord.Colour(preset.hex_value)
    if role.color and role.color.value:
        return role.color
    return discord.Colour.default()


def _role_has_moderation_perms(role: discord.Role) -> bool:
    perms = role.permissions
    return any(getattr(perms, name, False) for name in MODERATION_PERMISSIONS)


def _is_protected_color_role(role: discord.Role, mute_role_id: Optional[int]) -> bool:
    if role.is_default():
        return True
    if mute_role_id is not None and role.id == mute_role_id:
        return True
    return _role_has_moderation_perms(role)


def _get_protected_roles(guild: discord.Guild, mute_role_id: Optional[int]) -> list[discord.Role]:
    bot = guild.me
    protected: dict[int, discord.Role] = {}
    for role in guild.roles:
        if _is_protected_color_role(role, mute_role_id):
            protected[role.id] = role
            continue
        if bot is not None and role >= bot.top_role:
            protected[role.id] = role
    return list(protected.values())


def _protected_floor_position(guild: discord.Guild, mute_role_id: Optional[int]) -> int:
    protected = _get_protected_roles(guild, mute_role_id)
    positions = [r.position for r in protected if r.position > 0]
    if not positions:
        bot = guild.me
        return bot.top_role.position if bot is not None else 1
    return min(positions)


def _mappings_from_roles(roles: list[discord.Role]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for role in roles[:COLOR_ROLE_PANEL_MAX]:
        preset = _preset_for_role(role)
        mapping_id = f"cr_{preset.key}" if preset else f"cr_{uuid.uuid4().hex[:8]}"
        mappings.append(
            {
                "id": mapping_id,
                "role_id": role.id,
                "label": _label_for_role(role),
                "emoji": _emoji_for_role(role),
            }
        )
    return mappings


async def _get_mute_role_id(guild_id: int) -> Optional[int]:
    return await asyncio.to_thread(_read_mute_role_id_sync, guild_id)


async def sync_color_role_positions(
    guild: discord.Guild,
    role_ids: list[int],
    *,
    apply_colours: bool = True,
) -> list[str]:
    """Stack color roles below moderation roles; optionally apply preset colours."""
    warnings: list[str] = []
    mute_role_id = await _get_mute_role_id(guild.id)
    floor = _protected_floor_position(guild, mute_role_id)
    bot = guild.me
    if bot is None:
        return ["Bot member cache is unavailable."]

    ordered_ids = list(dict.fromkeys(role_ids))
    edit_queue: list[tuple[int, discord.Role, discord.Colour | None]] = []
    for index, role_id in enumerate(ordered_ids):
        role = guild.get_role(role_id)
        if role is None:
            warnings.append(f"Role `{role_id}` no longer exists.")
            continue
        if _is_protected_color_role(role, mute_role_id):
            warnings.append(f"Skipped protected role {role.mention}.")
            continue
        if role >= bot.top_role:
            warnings.append(f"Cannot move {role.mention} — it is at or above my top role.")
            continue
        target_pos = index + 1
        if target_pos >= floor:
            warnings.append(
                f"Cannot place {role.mention} at a safe position (hierarchy full). "
                "Move moderation roles higher or remove unused roles."
            )
            continue
        colour = _colour_for_role(role) if apply_colours else None
        edit_queue.append((target_pos, role, colour))

    # Apply highest target positions first to avoid Discord hierarchy conflicts.
    for target_pos, role, colour in sorted(edit_queue, key=lambda item: item[0], reverse=True):
        edit_kwargs: dict[str, Any] = {
            "position": target_pos,
            "hoist": False,
            "mentionable": False,
            "reason": "Color role hierarchy sync (Coffeecord)",
        }
        if colour is not None:
            edit_kwargs["colour"] = colour
        try:
            await role.edit(**edit_kwargs)
        except discord.Forbidden:
            warnings.append(f"No permission to edit {role.mention}.")
        except discord.HTTPException:
            warnings.append(f"Discord rejected edits for {role.mention}.")
    return warnings


# ---------------------------------------------------------------------------
# Setup UI
# ---------------------------------------------------------------------------


class CRMessageModal(discord.ui.Modal, title="Color Role Panel Message"):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        super().__init__(timeout=300)
        self.parent_view = parent
        self.message_content = discord.ui.TextInput(
            label="Panel message",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            default=parent.content or DEFAULT_PANEL_CONTENT,
        )
        self.add_item(self.message_content)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.content = str(self.message_content.value or "").strip() or DEFAULT_PANEL_CONTENT
        await self.parent_view.refresh(interaction, "Message updated.")


class CRChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="1) Select target channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        resolved: Optional[discord.TextChannel] = None
        if isinstance(selected, discord.TextChannel):
            resolved = selected
        elif interaction.guild is not None and selected is not None:
            selected_id = getattr(selected, "id", None)
            if isinstance(selected_id, int):
                maybe = interaction.guild.get_channel(selected_id)
                if isinstance(maybe, discord.TextChannel):
                    resolved = maybe
        self.parent_view.channel = resolved
        await self.parent_view.refresh(interaction, "Channel selected.")


class CRRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder=f"2) Select color roles (up to {COLOR_ROLE_PANEL_MAX})",
            min_values=1,
            max_values=COLOR_ROLE_PANEL_MAX,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.roles = list(self.values)
        await self.parent_view.refresh(
            interaction,
            f"{len(self.parent_view.roles)} color role(s) selected.",
        )


class CRModeSelect(discord.ui.Select):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Buttons (recommended)", value="button"),
            discord.SelectOption(label="Emoji reactions", value="emoji"),
        ]
        super().__init__(placeholder="3) Interaction style", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.mode = self.values[0]
        await self.parent_view.refresh(interaction, "Style updated.")


class CRSetContentButton(discord.ui.Button):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(label="4) Set panel message", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CRMessageModal(self.parent_view))


class CRPublishButton(discord.ui.Button):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(label="Publish", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.publish(interaction)


class CRCancelButton(discord.ui.Button):
    def __init__(self, parent: "ColorRoleSetupView") -> None:
        self.parent_view = parent
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Setup cancelled.", embed=None, view=None)
        self.parent_view.stop()


class ColorRoleSetupView(discord.ui.View):
    def __init__(self, cog: "ColorRoleCog", invoker_id: int, default_mode: str) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.invoker_id = invoker_id
        self.channel: Optional[discord.TextChannel] = None
        self.roles: list[discord.Role] = []
        self.mode = default_mode if default_mode in {"button", "emoji"} else "button"
        self.content = DEFAULT_PANEL_CONTENT
        self.add_item(CRChannelSelect(self))
        self.add_item(CRRoleSelect(self))
        self.add_item(CRModeSelect(self))
        self.add_item(CRSetContentButton(self))
        self.add_item(CRPublishButton(self))
        self.add_item(CRCancelButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                _color_roles_text_sync(
                    interaction.guild.id if interaction.guild else None,
                    "setup_owner_only",
                    default="This setup belongs to another moderator.",
                ),
                ephemeral=True,
            )
            return False
        return True

    def _preview_embed(self, status: str = "Configure your color role panel.") -> discord.Embed:
        embed = discord.Embed(title="Color Role Setup", color=discord.Color.blurple())
        embed.description = status
        embed.add_field(name="Channel", value=self.channel.mention if self.channel else "Not selected", inline=True)
        embed.add_field(name="Style", value=self.mode, inline=True)
        if not self.roles:
            roles_value = "Not selected"
        else:
            role_lines = [f"{_emoji_for_role(r)} {r.mention}" for r in self.roles]
            roles_value = _join_embed_field_lines(role_lines)
        embed.add_field(name="Roles", value=roles_value, inline=False)
        embed.add_field(name="Message", value=(self.content[:200] or DEFAULT_PANEL_CONTENT), inline=False)
        return embed

    async def refresh(self, interaction: discord.Interaction, status: str) -> None:
        await interaction.response.edit_message(embed=self._preview_embed(status), view=self)

    async def publish(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if self.channel is None:
            await interaction.response.send_message(
                _color_roles_text_sync(None, "select_channel_first", default="Select a channel first."),
                ephemeral=True,
            )
            return
        roles = list(self.roles)
        if not roles:
            await interaction.response.send_message(
                _color_roles_text_sync(None, "select_one_color_role", default="Select at least one color role."),
                ephemeral=True,
            )
            return
        if len(roles) > COLOR_ROLE_PANEL_MAX:
            await interaction.response.send_message(
                _color_roles_text_sync(
                    interaction.guild.id,
                    "max_roles",
                    default="Select at most {max_roles} roles.",
                    max_roles=str(COLOR_ROLE_PANEL_MAX),
                ),
                ephemeral=True,
            )
            return

        mute_role_id = await _get_mute_role_id(interaction.guild.id)
        for role in roles:
            if _is_protected_color_role(role, mute_role_id):
                await interaction.response.send_message(
                    _color_roles_text_sync(
                        interaction.guild.id,
                        "protected_role_blocked",
                        default="{role} is a protected moderation role and cannot be used as a color role.",
                        role=role.mention,
                    ),
                    ephemeral=True,
                )
                return

        sync_warnings = await sync_color_role_positions(interaction.guild, [r.id for r in roles])
        mappings = _mappings_from_roles(roles)
        panel_embed = _build_panel_embed(
            self.content,
            "",
            "",
            DEFAULT_PANEL_COLOR,
            mappings,
            interaction.guild,
        )
        panel_message = await self.channel.send(embed=panel_embed)
        item_cfg = {
            "channel_id": self.channel.id,
            "mode": self.mode,
            "content": self.content,
            "embed": {"title": "", "description": "", "color": DEFAULT_PANEL_COLOR},
            "mappings": mappings,
            "logging": False,
        }
        await self.cog.upsert_message_config(interaction.guild.id, panel_message.id, item_cfg)
        await self.cog._attach_panel_ui(panel_message, interaction.guild.id, panel_message.id, item_cfg)

        cfg = await self.cog.get_guild_config(interaction.guild.id)
        cfg["draft"] = {
            "channel_id": self.channel.id,
            "mode": self.mode,
            "content": self.content,
            "role_ids": [r.id for r in roles],
        }
        cfg["preset_role_ids"] = list(dict.fromkeys(cfg.get("preset_role_ids", []) + [r.id for r in roles]))
        self.cog._config[str(interaction.guild.id)] = cfg
        await self.cog.save_config()

        warn_text = ""
        if sync_warnings:
            warn_text = "\n\nSync notes:\n" + "\n".join(f"• {w}" for w in sync_warnings[:5])

        await interaction.response.edit_message(
            content=(
                _color_roles_text_sync(
                    interaction.guild.id,
                    "panel_created",
                    default="Color role panel created in {channel} (`{message_id}`) with {count} color(s). Members can hold one color at a time.{warn_text}",
                    channel=self.channel.mention,
                    message_id=str(panel_message.id),
                    count=str(len(mappings)),
                    warn_text=warn_text,
                )
            ),
            embed=None,
            view=None,
        )
        self.stop()


class ColorRoleButton(discord.ui.Button):
    def __init__(self, cog: "ColorRoleCog", guild_id: int, message_id: int, mapping: dict[str, Any]) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.mapping_id = mapping["id"]
        emoji = mapping.get("emoji")
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=(mapping.get("label") or "Color")[:80],
            emoji=emoji if emoji else None,
            custom_id=f"cr:{self.message_id}:{self.mapping_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        result = await self.cog.handle_color_pick(
            interaction.guild,
            interaction.user,
            self.message_id,
            self.mapping_id,
            source="button",
        )
        await interaction.response.send_message(result.message, ephemeral=True)


class ColorRoleButtonView(discord.ui.View):
    def __init__(self, cog: "ColorRoleCog", guild_id: int, message_id: int, mappings: list[dict[str, Any]]) -> None:
        super().__init__(timeout=None)
        for mapping in mappings[:COLOR_ROLE_PANEL_MAX]:
            self.add_item(ColorRoleButton(cog, guild_id, message_id, mapping))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class ColorRoleCog(
    commands.GroupCog,
    group_name="colorrole",
    group_description="Exclusive color role panels with buttons or emoji reactions.",
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
        normalized = _normalize_message(item_cfg)
        if normalized is None:
            return
        cfg["messages"][str(message_id)] = normalized
        self._config[str(guild_id)] = cfg
        await self.save_config()

    def build_button_view(self, guild_id: int, message_id: int, item_cfg: dict[str, Any]) -> ColorRoleButtonView:
        return ColorRoleButtonView(self, guild_id, message_id, item_cfg["mappings"])

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
        """Re-post color-role panels after backup restore.

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
                return None, None, "colorrole: panel missing channel_id"
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                return None, None, f"colorrole: channel `{channel_id}` missing"
            mappings = item.get("mappings") or []
            if not mappings:
                return None, None, None
            emb_raw = item.get("embed") or {}
            if not isinstance(emb_raw, dict):
                emb_raw = {}
            content = str(item.get("content") or "")
            panel_embed = _build_panel_embed(
                content,
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
                "content": content,
                "embed": emb_raw,
                "mappings": mappings,
                "logging": bool(item.get("logging", False)),
            }
            await self._attach_panel_ui(panel_message, guild.id, panel_message.id, item_cfg)
            return str(panel_message.id), item_cfg, f"colorrole: panel republished in #{channel.name}"

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
                        await self._attach_panel_ui(msg, guild.id, int(mid), item)
                        notes.append(f"colorrole: existing panel kept in #{channel.name}")
                        continue
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    if item.get("mode") == "button":
                        claimed = {int(k) for k in next_messages if str(k).isdigit()}
                        found = await self._find_existing_button_panel(channel, prefix="cr:", claimed=claimed)
                        if found is not None:
                            next_messages[str(found.id)] = item
                            await self._attach_panel_ui(found, guild.id, found.id, item)
                            notes.append(f"colorrole: existing panel kept in #{channel.name}")
                            continue
            try:
                new_mid, item_cfg, note = await _publish_one(item)
                if new_mid and item_cfg is not None:
                    next_messages[new_mid] = item_cfg
                if note:
                    notes.append(note)
            except Exception as exc:
                notes.append(f"colorrole: failed: {exc}")

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

    async def _attach_panel_ui(
        self,
        panel_message: discord.Message,
        guild_id: int,
        message_id: int,
        item_cfg: dict[str, Any],
    ) -> None:
        if item_cfg["mode"] == "button":
            view = self.build_button_view(guild_id, message_id, item_cfg)
            await panel_message.edit(view=view)
            self.bot.add_view(view, message_id=message_id)
            return
        failed: list[str] = []
        for mapping in item_cfg.get("mappings", []):
            emoji = mapping.get("emoji")
            if not emoji:
                continue
            try:
                await panel_message.add_reaction(str(emoji))
            except discord.HTTPException:
                failed.append(str(emoji))
        if failed:
            LOGGER.warning("Color role panel %s: failed reactions: %s", message_id, failed)

    def _find_mapping(self, item: dict[str, Any], mapping_id: str) -> Optional[dict[str, Any]]:
        for mapping in item.get("mappings", []):
            if mapping.get("id") == mapping_id:
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
                "colorrole",
                action,
                member,
                f"role_id={role_id}; message_id={message_id}",
                channel_id,
            )
        except Exception:
            return

    async def _check_assignable(self, guild: discord.Guild, member: discord.Member, role: discord.Role) -> Optional[str]:
        mute_role_id = await _get_mute_role_id(guild.id)
        if _is_protected_color_role(role, mute_role_id):
            return _color_roles_text_sync(member.id, "assign_protected_blocked", default="That role is protected and cannot be assigned through color roles.")
        me = guild.me
        if me is None:
            return _color_roles_text_sync(member.id, "bot_member_unavailable", default="Bot member cache is unavailable.")
        if not me.guild_permissions.manage_roles:
            return _color_roles_text_sync(member.id, "missing_manage_roles", default="I need `Manage Roles` permission.")
        if role >= me.top_role:
            return _color_roles_text_sync(member.id, "cannot_manage_role_hierarchy", default="I cannot manage that role due to role hierarchy.")
        if role >= member.top_role and member != guild.owner:
            return _color_roles_text_sync(member.id, "target_hierarchy_blocked", default="Role hierarchy prevents this change.")
        return None

    async def handle_color_pick(
        self,
        guild: discord.Guild,
        member: discord.Member,
        message_id: int,
        mapping_id: str,
        source: str,
    ) -> PickResult:
        cfg = await self.get_guild_config(guild.id)
        if not cfg.get("enabled", True):
            return PickResult(False, _color_roles_text_sync(member.id, "disabled", default="Color roles are disabled for this server."))

        item = cfg.get("messages", {}).get(str(message_id))
        if not item:
            return PickResult(False, _color_roles_text_sync(member.id, "panel_missing", default="This color role panel no longer exists."))

        mapping = self._find_mapping(item, mapping_id)
        if not mapping:
            return PickResult(False, _color_roles_text_sync(member.id, "option_missing", default="That color option no longer exists."))

        role = guild.get_role(int(mapping["role_id"]))
        if role is None:
            return PickResult(False, _color_roles_text_sync(member.id, "mapped_role_missing", default="That role no longer exists. Ask staff to update this panel."))

        hierarchy_error = await self._check_assignable(guild, member, role)
        if hierarchy_error:
            return PickResult(False, hierarchy_error)

        panel_role_ids = {int(m["role_id"]) for m in item.get("mappings", [])}
        mute_role_id = await _get_mute_role_id(guild.id)
        current_panel_roles = [
            r
            for r in member.roles
            if r.id in panel_role_ids and not _is_protected_color_role(r, mute_role_id)
        ]
        has_target = role in member.roles

        if has_target:
            try:
                await member.remove_roles(role, reason=f"Color role toggle ({source})")
            except discord.HTTPException:
                return PickResult(False, _color_roles_text_sync(member.id, "remove_failed", default="I couldn't remove that color role."))
            if item.get("logging", False):
                await self._emit_hook(guild, member, "role_removed", role.id, message_id, item["channel_id"])
            return PickResult(
                True,
                _color_roles_text_sync(member.id, "remove_success", default="Removed {role}.", role=role.mention),
                changed="removed",
                role_id=role.id,
            )

        to_remove = [r for r in current_panel_roles if r != role]
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="Color role exclusive selection")
            except discord.HTTPException:
                return PickResult(False, _color_roles_text_sync(member.id, "update_existing_failed", default="I couldn't update your existing color roles."))

        try:
            await member.add_roles(role, reason=f"Color role pick ({source})")
        except discord.HTTPException:
            return PickResult(False, _color_roles_text_sync(member.id, "add_failed", default="I couldn't add that color role."))

        if item.get("logging", False):
            await self._emit_hook(guild, member, "role_added", role.id, message_id, item["channel_id"])
        return PickResult(
            True,
            _color_roles_text_sync(member.id, "add_success", default="You now have {role}.", role=role.mention),
            changed="added",
            role_id=role.id,
        )

    async def _handle_emoji_payload(self, payload: discord.RawReactionActionEvent, removed: bool) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        cfg = await self.get_guild_config(guild.id)
        item = cfg.get("messages", {}).get(str(payload.message_id))
        if not item or item.get("mode") != "emoji":
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
                    return
                try:
                    await member.remove_roles(role, reason="Color role emoji removed")
                    if item.get("logging", False):
                        await self._emit_hook(
                            guild,
                            member,
                            "role_removed",
                            role.id,
                            payload.message_id,
                            payload.channel_id,
                        )
                except discord.HTTPException:
                    LOGGER.warning("Failed removing color role %s on reaction remove.", role.id)
            return

        await self.handle_color_pick(guild, member, payload.message_id, mapping["id"], source="emoji")

    async def _generate_preset_roles(self, guild: discord.Guild) -> tuple[list[discord.Role], list[str]]:
        created: list[discord.Role] = []
        warnings: list[str] = []
        existing_by_name = {r.name.lower(): r for r in guild.roles}
        for preset in DISCORD_COLOR_PRESETS:
            existing = existing_by_name.get(preset.name.lower())
            if existing is not None:
                created.append(existing)
                continue
            try:
                role = await guild.create_role(
                    name=preset.name,
                    colour=discord.Colour(preset.hex_value),
                    hoist=False,
                    mentionable=False,
                    reason="Color role preset (Coffeecord)",
                )
                created.append(role)
            except discord.Forbidden:
                warnings.append(f"No permission to create role `{preset.name}`.")
            except discord.HTTPException:
                warnings.append(f"Failed to create role `{preset.name}`.")
        return created, warnings

    # -----------------------------------------------------------------------
    # Slash commands
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="info",
        description="How to set up color roles in this server.",
)
    async def colorrole_info(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="Color Roles", color=discord.Color.blurple())
        embed.description = (
            "1. `/colorrole generate` — create 10 preset color roles (or reuse existing ones).\n"
            "2. `/colorrole sync` — fix role colours and hierarchy below moderation roles.\n"
            "3. `/colorrole setup` — publish a button or emoji panel (up to 10 colors).\n"
            "4. `/colorrole list` — view panels; `/colorrole delete` to remove one.\n\n"
            "Members can only hold **one** color from each panel. Moderation roles are never touched."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="generate",
        description="Create or reuse 10 preset color roles and sync hierarchy.",
)
    async def colorrole_generate(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not interaction.guild.me or not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "missing_manage_roles", default="I need `Manage Roles` permission."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        roles, create_warnings = await self._generate_preset_roles(interaction.guild)
        if not roles:
            await interaction.followup.send(
                _color_roles_text_sync(interaction.user.id, "no_roles_generated", default="Could not create or find any color roles."),
                ephemeral=True,
            )
            return

        sync_warnings = await sync_color_role_positions(interaction.guild, [r.id for r in roles])
        cfg = await self.get_guild_config(interaction.guild.id)
        cfg["preset_role_ids"] = [r.id for r in roles]
        self._config[str(interaction.guild.id)] = cfg
        await self.save_config()

        lines = [f"• {_emoji_for_role(r)} {r.mention}" for r in roles]
        embed = discord.Embed(
            title=_color_roles_text_sync(interaction.user.id, "preset_ready_title", default="Preset Color Roles Ready"),
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        all_warnings = create_warnings + sync_warnings
        if all_warnings:
            embed.add_field(
                name=_color_roles_text_sync(interaction.user.id, "field_notes", default="Notes"),
                value=_join_embed_field_lines([f"• {w}" for w in all_warnings[:10]]),
                inline=False,
            )
        embed.set_footer(
            text=_color_roles_text_sync(interaction.user.id, "setup_footer", default="Use /colorrole setup to publish a panel.")
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="setup",
        description="Guided setup for a color role panel.",
)
    async def colorrole_setup(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        view = ColorRoleSetupView(
            cog=self,
            invoker_id=interaction.user.id,
            default_mode=cfg.get("default_mode", "button"),
        )
        preset_ids = cfg.get("preset_role_ids", [])
        if preset_ids and interaction.guild is not None:
            preset_roles = [interaction.guild.get_role(rid) for rid in preset_ids]
            view.roles = [r for r in preset_roles if r is not None][:COLOR_ROLE_PANEL_MAX]
        await interaction.response.send_message(embed=view._preview_embed(), view=view, ephemeral=True)

    @app_commands.command(
        name="publish",
        description="Publish a panel from the saved draft.",
)
    async def colorrole_publish(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        draft = cfg.get("draft", {})
        if not isinstance(draft, dict) or not draft.get("role_ids"):
            await interaction.response.send_message(
                _color_roles_text_sync(
                    interaction.guild.id,
                    "draft_missing",
                    default="No draft saved. Run `/colorrole setup` or `/colorrole generate` first.",
                ),
                ephemeral=True,
            )
            return
        channel_id = draft.get("channel_id")
        if not str(channel_id).isdigit():
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "draft_missing_channel", default="Draft is missing a channel. Run `/colorrole setup` again."),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "draft_channel_missing", default="Draft channel no longer exists."),
                ephemeral=True,
            )
            return

        role_ids = [int(x) for x in draft.get("role_ids", []) if str(x).isdigit()][:COLOR_ROLE_PANEL_MAX]
        roles = [interaction.guild.get_role(rid) for rid in role_ids]
        roles = [r for r in roles if r is not None]
        if not roles:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "draft_roles_missing", default="Draft roles no longer exist."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        mute_role_id = await _get_mute_role_id(interaction.guild.id)
        for role in roles:
            if _is_protected_color_role(role, mute_role_id):
                await interaction.followup.send(
                    _color_roles_text_sync(
                        interaction.guild.id,
                        "protected_role_blocked",
                        default="{role} is a protected moderation role and cannot be used as a color role.",
                        role=role.mention,
                    ),
                    ephemeral=True,
                )
                return

        sync_warnings = await sync_color_role_positions(interaction.guild, [r.id for r in roles])
        mode = str(draft.get("mode", cfg.get("default_mode", "button")))
        if mode not in {"button", "emoji"}:
            mode = "button"
        content = str(draft.get("content") or DEFAULT_PANEL_CONTENT)
        mappings = _mappings_from_roles(roles)
        panel_embed = _build_panel_embed(content, "", "", DEFAULT_PANEL_COLOR, mappings, interaction.guild)
        panel_message = await channel.send(embed=panel_embed)
        item_cfg = {
            "channel_id": channel.id,
            "mode": mode,
            "content": content,
            "embed": {"title": "", "description": "", "color": DEFAULT_PANEL_COLOR},
            "mappings": mappings,
            "logging": False,
        }
        await self.upsert_message_config(interaction.guild.id, panel_message.id, item_cfg)
        await self._attach_panel_ui(panel_message, interaction.guild.id, panel_message.id, item_cfg)

        text = _color_roles_text_sync(
            interaction.guild.id,
            "publish_success",
            default="Published color panel in {channel} (`{message_id}`).",
            channel=channel.mention,
            message_id=str(panel_message.id),
        )
        if sync_warnings:
            text += "\n" + "\n".join(f"• {w}" for w in sync_warnings[:5])
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(
        name="list",
        description="List color role panels in this server.",
)
    async def colorrole_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        messages = cfg.get("messages", {})
        embed = discord.Embed(
            title=_color_roles_text_sync(interaction.user.id, "list_title", default="Color Role Panels"),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=_color_roles_text_sync(interaction.user.id, "field_enabled", default="Enabled"),
            value=t_sync(interaction.user.id, "common.yes") if cfg.get("enabled", True) else t_sync(interaction.user.id, "common.no"),
            inline=True,
        )
        embed.add_field(
            name=_color_roles_text_sync(interaction.user.id, "field_panels", default="Panels"),
            value=str(len(messages)),
            inline=True,
        )
        preset_ids = cfg.get("preset_role_ids", [])
        if preset_ids:
            embed.add_field(
                name=_color_roles_text_sync(interaction.user.id, "field_preset_roles", default="Preset roles"),
                value=str(len(preset_ids)),
                inline=True,
            )
        if not messages:
            embed.description = _color_roles_text_sync(interaction.user.id, "list_empty", default="No color role panels configured.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        lines = []
        for message_id, item in list(messages.items())[:20]:
            lines.append(
                f"`{message_id}` • <#{item['channel_id']}> • `{item['mode']}` • {len(item.get('mappings', []))} color(s)"
            )
        embed.add_field(
            name=_color_roles_text_sync(interaction.user.id, "field_configured_panels", default="Configured Panels"),
            value=_join_embed_field_lines(lines),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="delete",
        description="Delete a color role panel by message ID.",
)
    async def colorrole_delete(self, interaction: discord.Interaction, message_id: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not message_id.isdigit():
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "message_id_numeric", default="Message ID must be numeric."),
                ephemeral=True,
            )
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        item = cfg.get("messages", {}).pop(message_id, None)
        if item is None:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "panel_not_found", default="Panel not found."),
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
            _color_roles_text_sync(
                interaction.guild.id,
                "delete_success",
                default="Deleted panel `{message_id}`.",
                message_id=message_id,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="edit",
        description="Edit a color role panel.",
)
    @app_commands.describe(
        message_id="Target panel message ID.",
        role="Role to add or update in this panel.",
        remove_mapping="Remove the mapping for the provided role.",
        button_label="Button label for this color.",
        emoji="Emoji for button or reaction mode.",
        content="Panel message text.",
        logging_enabled="Log color role changes.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Buttons", value="button"),
            app_commands.Choice(name="Emoji reactions", value="emoji"),
        ]
    )
    async def colorrole_edit(
        self,
        interaction: discord.Interaction,
        message_id: str,
        role: Optional[discord.Role] = None,
        mode: Optional[app_commands.Choice[str]] = None,
        remove_mapping: bool = False,
        button_label: Optional[str] = None,
        emoji: Optional[str] = None,
        content: Optional[str] = None,
        logging_enabled: Optional[bool] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not message_id.isdigit():
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "message_id_numeric", default="Message ID must be numeric."),
                ephemeral=True,
            )
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        item = cfg.get("messages", {}).get(message_id)
        if item is None:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "panel_not_found", default="Panel not found."),
                ephemeral=True,
            )
            return

        if mode is not None:
            item["mode"] = mode.value
        if content is not None:
            item["content"] = content
        if logging_enabled is not None:
            item["logging"] = logging_enabled

        mute_role_id = await _get_mute_role_id(interaction.guild.id)
        if role is not None:
            if _is_protected_color_role(role, mute_role_id):
                await interaction.response.send_message(
                    _color_roles_text_sync(
                        interaction.guild.id,
                        "protected_role_blocked",
                        default="{role} is a protected moderation role and cannot be used as a color role.",
                        role=role.mention,
                    ),
                    ephemeral=True,
                )
                return
            existing = next((m for m in item["mappings"] if int(m["role_id"]) == role.id), None)
            if remove_mapping:
                if existing is None:
                    await interaction.response.send_message(
                        _color_roles_text_sync(interaction.user.id, "role_not_on_panel", default="That role is not on this panel."),
                        ephemeral=True,
                    )
                    return
                item["mappings"] = [m for m in item["mappings"] if int(m["role_id"]) != role.id]
            else:
                if existing is None:
                    if len(item["mappings"]) >= COLOR_ROLE_PANEL_MAX:
                        await interaction.response.send_message(
                            _color_roles_text_sync(
                                interaction.guild.id,
                                "panel_max_colors",
                                default="A panel can have at most {max_colors} colors.",
                                max_colors=str(COLOR_ROLE_PANEL_MAX),
                            ),
                            ephemeral=True,
                        )
                        return
                    existing = {
                        "id": f"cr_{uuid.uuid4().hex[:8]}",
                        "role_id": role.id,
                        "label": (button_label or _label_for_role(role))[:80],
                        "emoji": emoji or _emoji_for_role(role),
                    }
                    item["mappings"].append(existing)
                else:
                    if button_label is not None:
                        existing["label"] = button_label[:80]
                    if emoji is not None:
                        existing["emoji"] = emoji
        elif remove_mapping:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "provide_role_for_remove", default="Provide `role` when using `remove_mapping`."),
                ephemeral=True,
            )
            return

        if not item["mappings"]:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "panel_requires_mapping", default="Panel must keep at least one color mapping."),
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
                embed_raw = item.get("embed") or {}
                panel_embed = _build_panel_embed(
                    str(item.get("content") or ""),
                    str(embed_raw.get("title") or ""),
                    str(embed_raw.get("description") or ""),
                    int(embed_raw.get("color", DEFAULT_PANEL_COLOR) or DEFAULT_PANEL_COLOR),
                    list(item.get("mappings") or []),
                    interaction.guild,
                )
                if item["mode"] == "button":
                    view = self.build_button_view(interaction.guild.id, int(message_id), item)
                    await msg.edit(embed=panel_embed, view=view)
                    self.bot.add_view(view, message_id=int(message_id))
                else:
                    await msg.edit(embed=panel_embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            _color_roles_text_sync(
                interaction.guild.id,
                "panel_updated",
                default="Updated panel `{message_id}`.",
                message_id=message_id,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="sync",
        description="Fix color role colours and hierarchy positions.",
)
    async def colorrole_sync(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        role_ids: list[int] = list(cfg.get("preset_role_ids", []))
        for item in cfg.get("messages", {}).values():
            for mapping in item.get("mappings", []):
                rid = mapping.get("role_id")
                if str(rid).isdigit():
                    role_ids.append(int(rid))
        role_ids = list(dict.fromkeys(role_ids))
        if not role_ids:
            await interaction.response.send_message(
                _color_roles_text_sync(
                    interaction.guild.id,
                    "sync_no_roles",
                    default="No color roles configured. Run `/colorrole generate` or publish a panel first.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        warnings = await sync_color_role_positions(interaction.guild, role_ids)
        embed = discord.Embed(
            title=_color_roles_text_sync(interaction.user.id, "sync_title", default="Color Role Sync"),
            color=discord.Color.green(),
        )
        embed.add_field(
            name=_color_roles_text_sync(interaction.user.id, "field_roles_processed", default="Roles processed"),
            value=str(len(role_ids)),
            inline=True,
        )
        if warnings:
            embed.color = discord.Color.orange()
            embed.add_field(
                name="Notes",
                value=_join_embed_field_lines([f"• {w}" for w in warnings[:15]]),
                inline=False,
            )
        else:
            embed.description = _color_roles_text_sync(interaction.user.id, "sync_success", default="All configured color roles were synced successfully.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clear",
        description="Remove a member's color roles from all panels.",
)
    @app_commands.describe(member="Member to clear color roles from.")
    async def colorrole_clear(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.get_guild_config(interaction.guild.id)
        panel_role_ids: set[int] = set()
        for item in cfg.get("messages", {}).values():
            for mapping in item.get("mappings", []):
                if str(mapping.get("role_id")).isdigit():
                    panel_role_ids.add(int(mapping["role_id"]))
        if not panel_role_ids:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "list_empty", default="No color role panels configured."),
                ephemeral=True,
            )
            return

        mute_role_id = await _get_mute_role_id(interaction.guild.id)
        to_remove = [
            r
            for r in member.roles
            if r.id in panel_role_ids and not _is_protected_color_role(r, mute_role_id)
        ]
        if not to_remove:
            await interaction.response.send_message(
                _color_roles_text_sync(
                    interaction.guild.id,
                    "member_no_panel_roles",
                    default="{member} has no panel color roles.",
                    member=member.mention,
                ),
                ephemeral=True,
            )
            return
        try:
            await member.remove_roles(*to_remove, reason=f"Color roles cleared by {interaction.user}")
        except discord.HTTPException:
            await interaction.response.send_message(
                _color_roles_text_sync(interaction.user.id, "clear_failed", default="Could not remove color roles."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            _color_roles_text_sync(
                interaction.guild.id,
                "clear_success",
                default="Removed {count} color role(s) from {member}.",
                count=str(len(to_remove)),
                member=member.mention,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # Event listeners
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_emoji_payload(payload, removed=False)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_emoji_payload(payload, removed=True)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        cfg = await self.get_guild_config(role.guild.id)
        changed = False
        preset_ids = cfg.get("preset_role_ids", [])
        if role.id in preset_ids:
            cfg["preset_role_ids"] = [rid for rid in preset_ids if rid != role.id]
            changed = True
        for message_id, item in list(cfg.get("messages", {}).items()):
            old_len = len(item["mappings"])
            item["mappings"] = [m for m in item["mappings"] if int(m["role_id"]) != role.id]
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
    await bot.add_cog(ColorRoleCog(bot))
