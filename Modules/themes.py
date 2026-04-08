# ================== THEMES ==================
# Moderation DM theming: preset and custom themes for ban/kick/timeout/warn messages.
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands

from Modules import json_cache


# Paths
_DISCORDBOT_ROOT = Path(__file__).resolve().parent.parent
THEME_STORAGE_DIR = _DISCORDBOT_ROOT / "Storage" / "Config" / "theme_storage"
THEMES_CONFIG_PATH = _DISCORDBOT_ROOT / "Storage" / "Config" / "themes_config.json"
COMMAND_RESPONSES_PATH = _DISCORDBOT_ROOT / "Storage" / "Config" / "command_responses.json"
RESPONSE_THEMES_DIR = _DISCORDBOT_ROOT / "Storage" / "Config" / "response_themes"
SUPPORTERS_FILE = _DISCORDBOT_ROOT / "Storage" / "Data" / "supporters.json"

REQUIRED_ACTIONS = ("ban", "kick", "timeout", "warn")
MAX_RESPONSES_FILE_BYTES = 100 * 1024
MAX_THEME_FILE_BYTES = 50 * 1024
MAX_CUSTOM_THEMES_PER_GUILD = 3
THEME_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
HEX_COLOR_PATTERN = re.compile(r"^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{6})$")

_theme_cache: dict[tuple[int, str], dict[str, Any]] = {}
DEFAULT_COLOR = discord.Color.orange()

__module_display_name__ = "Themes"
__module_description__ = "Customize moderation DM messages with preset or custom themes."
__module_category__ = "configuration"


def _load_json(path: Path, default: dict | None = None) -> dict:
    return json_cache.get(path, default if default is not None else {})


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_cache.set_(path, data)


def _supporter_active(user_id: int) -> bool:
    data = _load_json(SUPPORTERS_FILE, {"supporters": {}})
    supporters = data.get("supporters", {})
    if not isinstance(supporters, dict):
        return False
    record = supporters.get(str(user_id))
    if not isinstance(record, dict):
        return False
    return bool(record.get("active", False))


def _sanitize_theme_name(name: str) -> str | None:
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    return s if THEME_NAME_PATTERN.match(s) else None


def _parse_color(hex_str: str | None) -> discord.Color:
    if not hex_str or not isinstance(hex_str, str):
        return DEFAULT_COLOR
    if not HEX_COLOR_PATTERN.match(hex_str):
        return DEFAULT_COLOR
    try:
        return discord.Color.from_str(hex_str)
    except (ValueError, TypeError):
        return DEFAULT_COLOR


def _validate_url(url: str | None, field: str, action: str) -> str | None:
    if not url:
        return None
    if not isinstance(url, str) or len(url) > 512:
        return f"Invalid {field} in {action}: must be HTTPS and under 512 chars"
    if not url.startswith("https://"):
        return f"Invalid {field} in {action}: must be HTTPS"
    parsed = urlparse(url)
    if parsed.scheme not in ("https",) or not parsed.netloc:
        return f"Invalid {field} in {action}: must be valid HTTPS URL"
    return None


def validate_theme_json(data: dict | None) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "Invalid JSON: expected object"
    actions = data.get("actions")
    if not isinstance(actions, dict):
        return False, "Theme must define actions: ban, kick, timeout, warn"
    for action in REQUIRED_ACTIONS:
        if action not in actions:
            return False, f"Theme must define actions: ban, kick, timeout, warn"
        act = actions[action]
        if not isinstance(act, dict):
            return False, f"Action {action} must have title and description"
        if "title" not in act or "description" not in act:
            return False, f"Action {action} must have title and description"
        title = act.get("title", "")
        desc = act.get("description", "")
        if not isinstance(title, str) or len(title) > 256:
            return False, f"Action {action} title exceeds max length (256)"
        if not isinstance(desc, str) or len(desc) > 4096:
            return False, f"Action {action} description exceeds max length (4096)"
        if "color" not in act or not HEX_COLOR_PATTERN.match(str(act.get("color", ""))):
            return False, f"Invalid color for {action}: must be #RGB or #RRGGBB"
        footer = act.get("footer")
        if footer is not None and isinstance(footer, str) and len(footer) > 2048:
            return False, f"Action {action} footer exceeds max length (2048)"
        err = _validate_url(act.get("thumbnail_url"), "thumbnail_url", action)
        if err:
            return False, err
        err = _validate_url(act.get("image_url"), "image_url", action)
        if err:
            return False, err
    return True, ""


def load_theme(guild_id: int, theme_name: str) -> dict | None:
    cache_key = (guild_id, theme_name)
    if cache_key in _theme_cache:
        return _theme_cache[cache_key]
    safe_name = _sanitize_theme_name(theme_name)
    if not safe_name:
        return None
    preset_path = THEME_STORAGE_DIR / f"{safe_name}.json"
    custom_path = THEME_STORAGE_DIR / str(guild_id) / f"{safe_name}.json"
    path = custom_path if custom_path.exists() else preset_path
    if not path.exists():
        return None
    try:
        raw = _load_json(path, {})
        if not raw:
            return None
        ok, err = validate_theme_json(raw)
        if not ok:
            return None
        _theme_cache[cache_key] = raw
        return raw
    except Exception:
        return None


def get_active_theme(guild_id: int) -> dict:
    config = _load_json(THEMES_CONFIG_PATH, {"guilds": {}})
    guilds = config.get("guilds", {})
    theme_name = guilds.get(str(guild_id))
    if theme_name:
        theme = load_theme(guild_id, theme_name)
        if theme:
            return theme
    default = load_theme(0, "default")
    if default:
        return default
    return _builtin_default_theme()


def _builtin_default_theme() -> dict:
    return {
        "name": "Default",
        "actions": {
            "ban": {
                "title": "Moderation Notice: Ban",
                "description": "You have been banned from {guild_name}.",
                "color": "#E67E22",
            },
            "kick": {
                "title": "Moderation Notice: Kick",
                "description": "You have been kicked from {guild_name}.",
                "color": "#E67E22",
            },
            "timeout": {
                "title": "Moderation Notice: Timeout",
                "description": "You have been timed out in {guild_name}.",
                "color": "#E67E22",
            },
            "warn": {
                "title": "Moderation Notice: Warn",
                "description": "You have received a warning in {guild_name}.",
                "color": "#E67E22",
            },
        },
    }


def set_guild_theme(guild_id: int, theme_name: str) -> bool:
    if not _sanitize_theme_name(theme_name):
        return False
    theme = load_theme(guild_id, theme_name)
    if not theme:
        return False
    config = _load_json(THEMES_CONFIG_PATH, {"guilds": {}})
    config.setdefault("guilds", {})[str(guild_id)] = theme_name
    _save_json(THEMES_CONFIG_PATH, config)
    for key in list(_theme_cache.keys()):
        if key[0] == guild_id:
            del _theme_cache[key]
    return True


def _invalidate_guild_cache(guild_id: int) -> None:
    for key in list(_theme_cache.keys()):
        if key[0] == guild_id:
            del _theme_cache[key]
    json_cache.invalidate(THEMES_CONFIG_PATH)


def _substitute(text: str, **kwargs: str) -> str:
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v) if v is not None else "")
    return text


def render_moderation_embed(
    theme: dict,
    action: str,
    *,
    guild_name: str = "",
    user_mention: str = "",
    reason: str = "",
    duration: str = "",
    rule: str = "",
) -> discord.Embed:
    action = action.lower()
    actions = theme.get("actions", {})
    act = actions.get(action) or actions.get("ban", {})
    default = _builtin_default_theme()
    fallback = default["actions"].get(action, default["actions"]["ban"])
    title = _substitute(
        act.get("title") or fallback.get("title", "Moderation Notice"),
        guild_name=guild_name,
        user=user_mention,
        reason=reason or "No reason provided",
        duration=duration,
        rule=rule,
    )[:256]
    desc = _substitute(
        act.get("description") or fallback.get("description", "An action was taken."),
        guild_name=guild_name,
        user=user_mention,
        reason=reason or "No reason provided",
        duration=duration,
        rule=rule,
    )[:4096]
    color = _parse_color(act.get("color"))
    embed = discord.Embed(
        title=title,
        description=desc,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Server", value=guild_name or "Unknown", inline=False)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=True)
    if rule:
        embed.add_field(name="Rule", value=rule, inline=True)
    embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024], inline=False)
    if act.get("footer"):
        embed.set_footer(text=_substitute(act["footer"], guild_name=guild_name, user=user_mention, reason=reason, duration=duration, rule=rule)[:2048])
    if act.get("thumbnail_url") and str(act["thumbnail_url"]).startswith("https://"):
        embed.set_thumbnail(url=act["thumbnail_url"][:512])
    if act.get("image_url") and str(act["image_url"]).startswith("https://"):
        embed.set_image(url=act["image_url"][:512])
    return embed


async def send_themed_moderation_dm(
    member: discord.Member,
    guild_id: int,
    action: str,
    guild_name: str,
    *,
    reason: str | None = None,
    duration_text: str | None = None,
    duration_seconds: int | None = None,
    rule: str | None = None,
) -> None:
    if member.bot:
        return
    action = action.lower()
    duration = duration_text
    if duration is None and duration_seconds is not None:
        duration = f"{int(duration_seconds)} second(s)"
    theme = get_active_theme(guild_id)
    embed = render_moderation_embed(
        theme,
        action,
        guild_name=guild_name,
        user_mention=member.mention,
        reason=reason or "No reason provided",
        duration=duration or "",
        rule=rule or "",
    )
    try:
        await member.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ---------- Command response overrides ----------


def _command_name_from_interaction(interaction: discord.Interaction) -> str:
    """Get normalized command name from interaction (e.g. 'level xpset' -> 'level_xpset')."""
    if interaction.command is None:
        return "unknown"
    return str(interaction.command.qualified_name).replace(" ", "_")


def get_command_response_for_interaction(
    interaction: discord.Interaction, key: str, default: str, **params: str
) -> str:
    """Return custom response for a slash command. Uses interaction to infer guild and command name."""
    guild_id = interaction.guild.id if interaction.guild else 0
    command = _command_name_from_interaction(interaction)
    return get_command_response(guild_id, command, key, default, **params)


def get_command_response(guild_id: int, command: str, key: str, default: str, **params: str) -> str:
    """Return custom response for a command key, or default. Supports {placeholder} substitution."""
    config = _load_json(COMMAND_RESPONSES_PATH, {"guilds": {}})
    guilds = config.get("guilds", {})
    overrides = guilds.get(str(guild_id), {})
    if not isinstance(overrides, dict):
        return _substitute(default, **params)
    cmd_overrides = overrides.get(command, {})
    if not isinstance(cmd_overrides, dict):
        return _substitute(default, **params)
    template = cmd_overrides.get(key)
    if not isinstance(template, str):
        return _substitute(default, **params)
    return _substitute(template, **params)


def validate_command_responses_json(data: dict | None) -> tuple[bool, str]:
    """Validate uploaded command response overrides JSON."""
    if not isinstance(data, dict):
        return False, "Invalid JSON: expected object"
    if "overrides" not in data:
        return False, "Missing 'overrides' key"
    overrides = data.get("overrides", {})
    if not isinstance(overrides, dict):
        return False, "overrides must be an object"
    for cmd, keys in overrides.items():
        if not isinstance(cmd, str) or not cmd.replace("_", "").replace("-", "").isalnum():
            return False, f"Invalid command name: {cmd!r}"
        if not isinstance(keys, dict):
            return False, f"Command {cmd} must have an object of response keys"
        for k, v in keys.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return False, f"Command {cmd}.{k} must be a string"
            if len(v) > 2000:
                return False, f"Command {cmd}.{k} exceeds 2000 chars"
    return True, ""


def set_guild_command_responses(guild_id: int, overrides: dict) -> None:
    """Set command response overrides for a guild."""
    config = _load_json(COMMAND_RESPONSES_PATH, {"guilds": {}})
    config.setdefault("guilds", {})[str(guild_id)] = overrides
    _save_json(COMMAND_RESPONSES_PATH, config)
    json_cache.invalidate(COMMAND_RESPONSES_PATH)


def get_guild_command_responses(guild_id: int) -> dict:
    """Get all command response overrides for a guild."""
    config = _load_json(COMMAND_RESPONSES_PATH, {"guilds": {}})
    return config.get("guilds", {}).get(str(guild_id), {})


def clear_guild_command_responses(guild_id: int) -> None:
    """Clear all command response overrides for a guild."""
    config = _load_json(COMMAND_RESPONSES_PATH, {"guilds": {}})
    guilds = config.get("guilds", {})
    if str(guild_id) in guilds:
        del guilds[str(guild_id)]
        _save_json(COMMAND_RESPONSES_PATH, config)
        json_cache.invalidate(COMMAND_RESPONSES_PATH)


def list_response_themes() -> list[str]:
    """List available preset response themes."""
    if not RESPONSE_THEMES_DIR.exists():
        return []
    return sorted(p.stem for p in RESPONSE_THEMES_DIR.glob("*.json"))


def load_response_theme(name: str) -> dict | None:
    """Load a preset response theme by name."""
    safe = _sanitize_theme_name(name)
    if not safe:
        return None
    path = RESPONSE_THEMES_DIR / f"{safe}.json"
    if not path.exists():
        return None
    data = _load_json(path, {})
    if not isinstance(data.get("overrides"), dict):
        return None
    return data


def list_preset_themes() -> list[str]:
    if not THEME_STORAGE_DIR.exists():
        return []
    names = []
    for p in THEME_STORAGE_DIR.iterdir():
        if p.is_file() and p.suffix == ".json" and p.name != "themes_config.json":
            names.append(p.stem)
    return sorted(names)


def list_custom_themes(guild_id: int) -> list[str]:
    custom_dir = THEME_STORAGE_DIR / str(guild_id)
    if not custom_dir.exists():
        return []
    return sorted(p.stem for p in custom_dir.glob("*.json"))


def list_available_themes(guild_id: int) -> list[str]:
    presets = set(list_preset_themes())
    custom = set(list_custom_themes(guild_id))
    return sorted(presets | custom)


async def _guild_only(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return False
    return True


theme_group = app_commands.Group(name="theme", description="Moderation message themes")


@theme_group.command(name="list", description="List available themes (moderation DMs + command responses)")
async def theme_list(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    guild_id = interaction.guild.id
    config = _load_json(THEMES_CONFIG_PATH, {"guilds": {}})
    current_mod = config.get("guilds", {}).get(str(guild_id), "default")
    presets = list_preset_themes()
    custom = list_custom_themes(guild_id)
    response_presets = set(list_response_themes())
    lines = []
    for name in sorted(set(presets) | set(custom) | response_presets):
        badge = " (current)" if name == current_mod else ""
        custom_badge = " [custom]" if name in custom else ""
        has_mod = _theme_has_moderation(name)
        has_resp = name in response_presets
        if has_mod and has_resp:
            scope = " — full"
        elif has_mod:
            scope = " — moderation only"
        elif has_resp:
            scope = " — command responses only"
        else:
            scope = ""
        lines.append(f"• **{name}**{badge}{custom_badge}{scope}")
    embed = discord.Embed(
        title="Available Themes",
        description="\n".join(lines) if lines else "No themes found. Default theme will be used.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Use /theme set <name> to apply (moderation DMs + command responses)")
    await interaction.response.send_message(embed=embed, ephemeral=True)


def _theme_has_moderation(name: str) -> bool:
    """True if theme exists in theme_storage (moderation DMs)."""
    safe = _sanitize_theme_name(name)
    if not safe:
        return False
    preset_path = THEME_STORAGE_DIR / f"{safe}.json"
    return preset_path.exists()


def _theme_has_responses(name: str) -> bool:
    """True if preset exists in response_themes (command responses)."""
    return load_response_theme(name) is not None


@theme_group.command(name="set", description="Set the server theme (moderation DMs + command responses)")
@app_commands.describe(name="Theme name to apply")
async def theme_set(interaction: discord.Interaction, name: str) -> None:
    if not await _guild_only(interaction):
        return
    guild_id = interaction.guild.id
    safe = _sanitize_theme_name(name)
    if not safe:
        await interaction.response.send_message("Invalid theme name. Use only letters, numbers, hyphens, and underscores.", ephemeral=True)
        return
    has_mod = _theme_has_moderation(safe)
    has_resp = _theme_has_responses(safe)
    if not has_mod and not has_resp:
        await interaction.response.send_message(f"Theme `{safe}` not found. Use /theme list to see available themes.", ephemeral=True)
        return

    parts = []
    if has_mod:
        set_guild_theme(guild_id, safe)
        parts.append("moderation DMs")

    if has_resp:
        data = load_response_theme(safe)
        if data:
            set_guild_command_responses(guild_id, data["overrides"])
            parts.append("command responses")
    else:
        clear_guild_command_responses(guild_id)

    msg = f"Theme set to **{safe}**."
    if parts:
        msg += f" Applied: {', '.join(parts)}."
    await interaction.response.send_message(msg, ephemeral=True)


@theme_group.command(name="preview", description="Preview a theme's moderation messages")
@app_commands.describe(name="Theme name to preview")
async def theme_preview(interaction: discord.Interaction, name: str) -> None:
    if not await _guild_only(interaction):
        return
    guild_id = interaction.guild.id
    safe = _sanitize_theme_name(name)
    if not safe:
        await interaction.response.send_message("Invalid theme name.", ephemeral=True)
        return
    theme = load_theme(guild_id, safe)
    if not theme:
        await interaction.response.send_message(f"Theme `{safe}` not found.", ephemeral=True)
        return
    guild_name = interaction.guild.name
    user = interaction.user.mention
    embeds = []
    for act in REQUIRED_ACTIONS:
        emb = render_moderation_embed(
            theme,
            act,
            guild_name=guild_name,
            user_mention=user,
            reason="Sample reason",
            duration="60 seconds" if act == "timeout" else ("Permanent" if act == "ban" else ""),
            rule="sample_rule" if act in ("warn", "timeout", "kick", "ban") else "",
        )
        embeds.append(emb)
    await interaction.response.send_message(embeds=embeds, ephemeral=True)


@theme_group.command(name="info", description="Show current theme and settings")
async def theme_info(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    guild_id = interaction.guild.id
    config = _load_json(THEMES_CONFIG_PATH, {"guilds": {}})
    mod_theme = config.get("guilds", {}).get(str(guild_id), "default")
    theme = get_active_theme(guild_id)
    desc = theme.get("description", "No description.")
    is_custom = mod_theme in list_custom_themes(guild_id)
    overrides = get_guild_command_responses(guild_id)
    resp_status = f"{len(overrides)} command(s)" if overrides else "None (default messages)"
    embed = discord.Embed(
        title="Theme Info",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Moderation DMs", value=mod_theme, inline=True)
    embed.add_field(name="Command Responses", value=resp_status, inline=True)
    embed.add_field(name="Type", value="Custom" if is_custom else "Preset", inline=True)
    embed.add_field(name="Description", value=desc[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@theme_group.command(name="upload", description="Upload a custom theme (supporters only)")
@app_commands.describe(file="JSON theme file (max 50KB)")
async def theme_upload(interaction: discord.Interaction, file: discord.Attachment) -> None:
    if not await _guild_only(interaction):
        return
    if not _supporter_active(interaction.user.id):
        await interaction.response.send_message(
            "Custom themes require Ko-fi supporter status. Use /kofi link to get perks.",
            ephemeral=True,
        )
        return
    guild_id = interaction.guild.id
    if file.size > MAX_THEME_FILE_BYTES:
        await interaction.response.send_message(
            f"Theme file exceeds 50KB limit ({file.size} bytes).",
            ephemeral=True,
        )
        return
    if not file.filename.lower().endswith(".json"):
        await interaction.response.send_message("Theme file must be a .json file.", ephemeral=True)
        return
    theme_name = _sanitize_theme_name(Path(file.filename).stem)
    if not theme_name:
        await interaction.response.send_message(
            "Invalid theme name. Filename (without .json) must use only letters, numbers, hyphens, and underscores.",
            ephemeral=True,
        )
        return
    custom_count = len(list_custom_themes(guild_id))
    if custom_count >= MAX_CUSTOM_THEMES_PER_GUILD and theme_name not in list_custom_themes(guild_id):
        await interaction.response.send_message(
            f"Maximum {MAX_CUSTOM_THEMES_PER_GUILD} custom themes per server. Delete one with /theme delete first.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        await interaction.followup.send(f"Invalid JSON: {e}", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"Failed to read file: {e}", ephemeral=True)
        return
    ok, err = validate_theme_json(data)
    if not ok:
        await interaction.followup.send(f"Invalid theme: {err}", ephemeral=True)
        return
    custom_dir = THEME_STORAGE_DIR / str(guild_id)
    custom_dir.mkdir(parents=True, exist_ok=True)
    out_path = custom_dir / f"{theme_name}.json"
    try:
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        await interaction.followup.send(f"Failed to save theme: {e}", ephemeral=True)
        return
    json_cache.invalidate(out_path)
    _invalidate_guild_cache(guild_id)
    await interaction.followup.send(f"Custom theme **{theme_name}** uploaded. Use /theme set {theme_name} to apply.", ephemeral=True)


@theme_group.command(name="delete", description="Delete a custom theme (supporters only)")
@app_commands.describe(name="Custom theme name to delete")
async def theme_delete(interaction: discord.Interaction, name: str) -> None:
    if not await _guild_only(interaction):
        return
    if not _supporter_active(interaction.user.id):
        await interaction.response.send_message(
            "Custom themes require Ko-fi supporter status. Use /kofi link to get perks.",
            ephemeral=True,
        )
        return
    guild_id = interaction.guild.id
    safe = _sanitize_theme_name(name)
    if not safe:
        await interaction.response.send_message("Invalid theme name.", ephemeral=True)
        return
    custom_themes = list_custom_themes(guild_id)
    if safe not in custom_themes:
        await interaction.response.send_message(
            f"`{safe}` is not a custom theme. You can only delete custom themes (uploaded by /theme upload).",
            ephemeral=True,
        )
        return
    custom_path = THEME_STORAGE_DIR / str(guild_id) / f"{safe}.json"
    if not custom_path.exists():
        await interaction.response.send_message(f"Theme `{safe}` not found.", ephemeral=True)
        return
    try:
        custom_path.unlink()
    except OSError as e:
        await interaction.response.send_message(f"Failed to delete theme: {e}", ephemeral=True)
        return
    json_cache.invalidate(custom_path)
    config = _load_json(THEMES_CONFIG_PATH, {"guilds": {}})
    guilds = config.get("guilds", {})
    if guilds.get(str(guild_id)) == safe:
        guilds[str(guild_id)] = "default"
        _save_json(THEMES_CONFIG_PATH, config)
    _invalidate_guild_cache(guild_id)
    await interaction.response.send_message(f"Custom theme **{safe}** deleted. Server theme reset to default.", ephemeral=True)


# ---------- Command response overrides (nested group) ----------
responses_group = app_commands.Group(name="responses", description="Customize command response messages")


@responses_group.command(name="presets", description="List preset response themes (use /theme set to apply)")
async def responses_presets(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    themes = list_response_themes()
    if not themes:
        await interaction.response.send_message("No preset response themes found.", ephemeral=True)
        return
    lines = []
    for name in themes:
        data = load_response_theme(name)
        if data:
            desc = data.get("description", "No description.")
            lines.append(f"• **{name}** — {desc}")
        else:
            lines.append(f"• **{name}**")
    embed = discord.Embed(
        title="Preset Response Themes",
        description="\n".join(lines),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Use /theme set <name> to apply (moderation DMs + command responses)")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@responses_group.command(name="list", description="List configured command response overrides")
async def responses_list(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    guild_id = interaction.guild.id
    overrides = get_guild_command_responses(guild_id)
    if not overrides:
        await interaction.response.send_message(
            "No command response overrides. Use /theme responses upload to add custom responses.",
            ephemeral=True,
        )
        return
    lines = []
    for cmd, keys in sorted(overrides.items()):
        for k, v in sorted(keys.items()):
            preview = (v[:50] + "…") if len(v) > 50 else v
            lines.append(f"• **{cmd}.{k}**: `{preview}`")
    embed = discord.Embed(
        title="Command Response Overrides",
        description="\n".join(lines) if lines else "None",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Supporters can upload JSON via /theme responses upload")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@responses_group.command(name="upload", description="Upload command response overrides JSON (supporters only)")
@app_commands.describe(file="JSON file with overrides (max 100KB)")
async def responses_upload(interaction: discord.Interaction, file: discord.Attachment) -> None:
    if not await _guild_only(interaction):
        return
    if not _supporter_active(interaction.user.id):
        await interaction.response.send_message(
            "Command response overrides require Ko-fi supporter status. Use /kofi link to get perks.",
            ephemeral=True,
        )
        return
    guild_id = interaction.guild.id
    if file.size > MAX_RESPONSES_FILE_BYTES:
        await interaction.response.send_message(
            f"File exceeds 100KB limit ({file.size} bytes).",
            ephemeral=True,
        )
        return
    if not file.filename.lower().endswith(".json"):
        await interaction.response.send_message("File must be a .json file.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        await interaction.followup.send(f"Invalid JSON: {e}", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"Failed to read file: {e}", ephemeral=True)
        return
    ok, err = validate_command_responses_json(data)
    if not ok:
        await interaction.followup.send(f"Invalid format: {err}", ephemeral=True)
        return
    overrides = data.get("overrides", {})
    set_guild_command_responses(guild_id, overrides)
    count = sum(len(v) for v in overrides.values() if isinstance(v, dict))
    await interaction.followup.send(
        f"✅ Uploaded {count} command response override(s). See docs for available keys.",
        ephemeral=True,
    )


def _collect_slash_commands(tree: discord.app_commands.CommandTree) -> list[str]:
    """Collect all slash command qualified names (normalized). Excludes prefix commands."""
    names: list[str] = []

    def walk(cmd: discord.app_commands.Command | discord.app_commands.Group, prefix: str = "") -> None:
        if isinstance(cmd, discord.app_commands.Group):
            for c in cmd.commands:
                walk(c, f"{prefix}{cmd.name} " if prefix else f"{cmd.name} ")
        else:
            full = f"{prefix}{cmd.name}".strip()
            names.append(full.replace(" ", "_"))

    for cmd in tree.get_commands():
        walk(cmd)

    return sorted(set(names))


@responses_group.command(name="discover", description="List all slash commands you can override")
async def responses_discover(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    tree = interaction.client.tree
    names = _collect_slash_commands(tree)
    if not names:
        await interaction.response.send_message("No slash commands found.", ephemeral=True)
        return
    # Split into chunks of 20 for embed
    chunk = names[:30]
    desc = "Use these as command keys in your JSON. Use keys like `success`, `success_permanent`, `error`.\n\n"
    desc += "`" + "`, `".join(chunk) + "`"
    if len(names) > 30:
        desc += f"\n\n...and {len(names) - 30} more. Run `/theme responses keys` for common keys."
    embed = discord.Embed(
        title="Slash Commands (Overrideable)",
        description=desc,
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@responses_group.command(name="keys", description="List common response keys and override format")
async def responses_keys(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    known = (
        "**Common keys:** `success`, `success_permanent`, `error`\n\n"
        "**Format:** Use `/theme responses discover` to list all slash commands. "
        "Command names use underscores (e.g. `level_xpset`, `muterole_create`).\n\n"
        "**Example JSON:**\n"
        "```json\n"
        '{"overrides": {"ban": {"success": "🔨 Banned {member}!"}, "giverole": {"success": "✅ Added {role}."}}}\n'
        "```\n\n"
        "Prefix commands (`.synccommands`, etc.) cannot be overridden."
    )
    embed = discord.Embed(
        title="Command Response Keys",
        description=known,
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@responses_group.command(name="clear", description="Clear all command response overrides (supporters only)")
async def responses_clear(interaction: discord.Interaction) -> None:
    if not await _guild_only(interaction):
        return
    if not _supporter_active(interaction.user.id):
        await interaction.response.send_message(
            "Clearing overrides requires Ko-fi supporter status.",
            ephemeral=True,
        )
        return
    guild_id = interaction.guild.id
    clear_guild_command_responses(guild_id)
    await interaction.response.send_message("Command response overrides cleared.", ephemeral=True)


# Add responses subgroup to theme group
theme_group.add_command(responses_group)


async def setup(bot: commands.Bot) -> None:
    """Called by discord.py's load_extension — registers the theme command group."""
    tree = bot.tree
    existing = tree.get_command("theme")
    if existing is None:
        tree.add_command(theme_group)
