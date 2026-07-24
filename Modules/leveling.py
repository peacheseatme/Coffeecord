import asyncio
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageSequence

from . import anti_abuse
from Modules.i18n import t, t_sync
from .json_cache import get as _json_get, set_ as _json_set
from .module_registry import is_module_enabled
from .themes import get_command_response_for_interaction
from .log_actor import log_actor_from_interaction

BASE_DIR = Path(__file__).resolve().parent.parent
XP_FILE = BASE_DIR / "Storage" / "Data" / "xp.json"
CONFIG_FILE = BASE_DIR / "Storage" / "Config" / "leveling.json"
BACKGROUND_FILE = BASE_DIR / "Storage" / "Config" / "backgrounds.json"
LEVELCARD_STYLE_FILE = BASE_DIR / "Storage" / "Data" / "levelcard_styles.json"
LEVEL_REWARDS_FILE = BASE_DIR / "Storage" / "Config" / "level_rewards.json"
SUPPORTERS_FILE = BASE_DIR / "Storage" / "Data" / "supporters.json"
FONT_PATH = BASE_DIR / "Storage" / "Assets" / "Roboto-Regular.ttf"
BOLD_FONT_PATH = BASE_DIR / "Storage" / "Assets" / "Roboto-Bold.ttf"
CACHE_DIR = BASE_DIR / "Storage" / "Temp" / "level_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _guild_leveling_cfg(config: dict[str, Any], guild_id: str) -> dict[str, Any]:
    raw = config.get(guild_id, {})
    return raw if isinstance(raw, dict) else {}


def _default_xp_user(guild_cfg: dict[str, Any]) -> dict[str, Any]:
    """Initial user row; first level-up threshold matches _check_level_up (base_xp only for level 1)."""
    base = max(int(guild_cfg.get("base_xp", 10)), 1)
    return {"xp": 0, "level": 1, "next_level_xp": base}


def _normalize_user_level(user_data: dict[str, Any]) -> int:
    """Return level >= 1; fix legacy level 0 rows from old setdefault."""
    lvl = int(user_data.get("level", 1))
    if lvl < 1:
        user_data["level"] = 1
        return 1
    return lvl


def _next_level_xp_threshold(level: int, user_data: dict[str, Any], guild_cfg: dict[str, Any]) -> int:
    """
    XP needed in the current level pool to level up (same as stored next_level_xp).
    Must match _check_level_up: level 1 uses base_xp; level 2+ uses base * scale**level.
    """
    explicit = user_data.get("next_level_xp")
    if explicit is not None:
        return max(int(explicit), 1)
    base = max(int(guild_cfg.get("base_xp", 10)), 1)
    scale = float(guild_cfg.get("xp_scale", 1.1))
    level = max(1, level)
    if level <= 1:
        return base
    return max(int(base * (scale**level)), 1)


def _computation_next_level_xp_after_level_up(new_level: int, guild_cfg: dict[str, Any]) -> int:
    """Threshold after a level-up (xp reset to 0); matches existing _check_level_up assignment."""
    base = int(guild_cfg.get("base_xp", 10))
    scale = float(guild_cfg.get("xp_scale", 1.1))
    return max(int(base * (scale**new_level)), 1)


# Font cache (avoids truetype load on every /level)
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except OSError:
            try:
                _FONT_CACHE[key] = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
            except OSError:
                _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _render_levelcard_sync(
    bg_bytes: bytes,
    bg_is_gif: bool,
    avatar_bytes: bytes | None,
    style_colors: dict[str, str],
    progress: float,
    display_name: str,
    level: int,
    rank: int,
    xp: int,
    xp_for_next_level: int,
    status_color: tuple[int, int, int, int],
    cached_path: Path,
    render_gif: bool,
    level_label: str,
    rank_label: str,
    xp_label: str,
) -> Path:
    """Run PIL level card rendering in thread pool to avoid blocking event loop."""
    if bg_is_gif:
        bg = Image.open(BytesIO(bg_bytes))
        frames = [f.convert("RGBA").resize((800, 240)) for f in ImageSequence.Iterator(bg)]
        duration = int(bg.info.get("duration", 100))
    else:
        bg = Image.open(BytesIO(bg_bytes)).convert("RGBA").resize((800, 240))
        frames = [bg]
        duration = 100

    avatar = None
    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((128, 128))
            mask = Image.new("L", avatar.size, 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.ellipse((0, 0, 128, 128), fill=255)
            avatar.putalpha(mask)
        except Exception:
            avatar = None

    bold_path = str(BOLD_FONT_PATH)
    name_font = _get_font(bold_path, 30)
    level_font = _get_font(bold_path, 28)
    rank_font = _get_font(bold_path, 30)
    xp_font = _get_font(bold_path, 32)

    avatar_x, avatar_y = 24, 56
    avatar_size = 128
    content_left = 180
    content_right = 760
    top_y = 50
    separator_y = 118
    xp_text_y = 132
    bar_top = 168
    bar_bottom = 198
    bar_radius = 50

    track_rgb = _hex_to_rgba(style_colors["progress_track"], 170)
    fill_rgb = _hex_to_rgba(style_colors["progress_fill"], 255)
    name_color = _hex_to_rgba(style_colors["name_text"], 255)
    level_color = _hex_to_rgba(style_colors["level_text"], 255)
    rank_color = _hex_to_rgba(style_colors["rank_text"], 255)
    xp_color = _hex_to_rgba(style_colors["xp_text"], 255)
    separator_color = _hex_to_rgba(style_colors["separator"], 130)
    stroke_color = _hex_to_rgba(style_colors["stroke"], 255)

    output_frames = []
    for frame in frames:
        plate = Image.new("RGBA", frame.size, LEVELCARD_CANVAS_BG)
        plate.alpha_composite(frame)
        frame = plate
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((content_left, bar_top, content_right, bar_bottom), radius=bar_radius, fill=track_rgb)
        fill_width = int((content_right - content_left) * progress)
        if fill_width > 0:
            draw.rounded_rectangle(
                (content_left, bar_top, content_left + fill_width, bar_bottom),
                radius=bar_radius,
                fill=fill_rgb,
            )
        if avatar:
            frame.paste(avatar, (avatar_x, avatar_y), avatar)
            dot_center_x = avatar_x + avatar_size - 8
            dot_center_y = avatar_y + avatar_size - 8
            draw.ellipse((dot_center_x - 13, dot_center_y - 13, dot_center_x + 13, dot_center_y + 13), fill=(32, 34, 37, 255))
            draw.ellipse((dot_center_x - 10, dot_center_y - 10, dot_center_x + 10, dot_center_y + 10), fill=status_color)

        level_text = f"{level_label} {level}"
        rank_text = f"{rank_label} #{rank}"
        xp_display = max(0, min(xp, xp_for_next_level))
        xp_text = f"{xp_label}: {xp_display:,} / {xp_for_next_level:,}"

        draw.text((content_left, top_y), display_name, font=name_font, fill=name_color, stroke_width=2, stroke_fill=stroke_color)
        level_bbox = draw.textbbox((0, 0), level_text, font=level_font)
        rank_bbox = draw.textbbox((0, 0), rank_text, font=rank_font)
        draw.text((content_right - (level_bbox[2] - level_bbox[0]), top_y), level_text, font=level_font, fill=level_color, stroke_width=2, stroke_fill=stroke_color)
        draw.text((content_right - (rank_bbox[2] - rank_bbox[0]), top_y + 38), rank_text, font=rank_font, fill=rank_color, stroke_width=2, stroke_fill=stroke_color)
        draw.line((content_left, separator_y, content_right, separator_y), fill=separator_color, width=2)
        draw.text((content_left, xp_text_y), xp_text, font=xp_font, fill=xp_color, stroke_width=2, stroke_fill=stroke_color)
        output_frames.append(frame)

    if render_gif:
        output_frames[0].save(str(cached_path), save_all=True, append_images=output_frames[1:], duration=duration, loop=0, disposal=1)
    else:
        output_frames[0].save(str(cached_path))
    return cached_path


LEVELCARD_CACHE_TTL_SECONDS = 10 * 60
LEVELCARD_CACHE_RETENTION_SECONDS = 30 * 60
LEVELCARD_CANVAS_BG = (30, 30, 30, 255)
LEVELCARD_IMAGE_EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
LEVELCARD_IMAGE_MAGIC = (b"GIF87a", b"GIF89a", b"\x89PNG", b"\xff\xd8\xff")
LEVELCARD_STATUS_COLORS = {
    "online": (67, 181, 129, 255),
    "idle": (250, 166, 26, 255),
    "dnd": (240, 71, 71, 255),
    "do_not_disturb": (240, 71, 71, 255),
    "offline": (116, 127, 141, 255),
    "invisible": (116, 127, 141, 255),
}
DEFAULT_LEVELCARD_BG_URL = "https://media1.tenor.com/m/CPKRTsSr14kAAAAd/rick-astley.gif"
DEFAULT_LEVELCARD_STYLE: dict[str, str] = {
    "name_text": "#50FF78",
    "level_text": "#50FF78",
    "rank_text": "#82FFA0",
    "xp_text": "#82FFA0",
    "progress_fill": "#00C8FF",
    "progress_track": "#323232",
    "separator": "#FFFFFF",
    "stroke": "#141414",
}
LEVELCARD_PRESETS: dict[str, dict[str, str]] = {
    "default": dict(DEFAULT_LEVELCARD_STYLE),
    "ocean": {
        "name_text": "#79E0FF",
        "level_text": "#79E0FF",
        "rank_text": "#B8F5FF",
        "xp_text": "#B8F5FF",
        "progress_fill": "#00B4D8",
        "progress_track": "#1D3557",
        "separator": "#DFF6FF",
        "stroke": "#0B1E33",
    },
    "sunset": {
        "name_text": "#FFB86C",
        "level_text": "#FFD166",
        "rank_text": "#FFE29A",
        "xp_text": "#FFE29A",
        "progress_fill": "#FF7F50",
        "progress_track": "#5A2A27",
        "separator": "#FFE5D0",
        "stroke": "#2B1614",
    },
    "neon": {
        "name_text": "#39FF14",
        "level_text": "#39FF14",
        "rank_text": "#9DFF8A",
        "xp_text": "#9DFF8A",
        "progress_fill": "#00E5FF",
        "progress_track": "#1A1A1A",
        "separator": "#E3FFF6",
        "stroke": "#0A0A0A",
    },
    "royal": {
        "name_text": "#C4A1FF",
        "level_text": "#C4A1FF",
        "rank_text": "#E2CCFF",
        "xp_text": "#E2CCFF",
        "progress_fill": "#7B2CBF",
        "progress_track": "#240046",
        "separator": "#EAD7FF",
        "stroke": "#16002A",
    },
}


def _load_json(path: Path, default: Any) -> Any:
    return _json_get(path, default if default is not None else {})


def _save_json(path: Path, data: Any) -> None:
    _json_set(path, data)


def _normalize_hex_color(raw: str) -> Optional[str]:
    value = (raw or "").strip().upper()
    if value.startswith("#"):
        value = value[1:]
    if len(value) != 6:
        return None
    if any(ch not in "0123456789ABCDEF" for ch in value):
        return None
    return f"#{value}"


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    normalized = _normalize_hex_color(hex_color) or "#FFFFFF"
    rgb = normalized.lstrip("#")
    return int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16), alpha


def _looks_like_image_bytes(header: bytes) -> bool:
    if any(header.startswith(magic) for magic in LEVELCARD_IMAGE_MAGIC):
        return True
    return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"


def _url_has_image_extension(url: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return any(path.endswith(ext) for ext in LEVELCARD_IMAGE_EXTENSIONS)


async def _validate_levelcard_image_url(session: aiohttp.ClientSession, url: str) -> bool:
    """Validate a remote image URL (Tenor and similar CDNs often 404 on HEAD)."""
    headers = {"Range": "bytes=0-1023"}
    try:
        async with session.get(url, allow_redirects=True, headers=headers) as resp:
            if resp.status not in (200, 206):
                return False
            content_type = str(resp.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type.startswith("image/"):
                return True
            body = await resp.content.read(16)
            if _looks_like_image_bytes(body):
                return True
            return _url_has_image_extension(url) and bool(body)
    except Exception:
        return False


def _member_status_key(member: discord.abc.User) -> str:
    raw = getattr(member, "raw_status", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    status = getattr(member, "status", None)
    if isinstance(status, discord.Status):
        return status.value
    if status is not None:
        text = str(status).lower()
        if text.startswith("status."):
            return text.split(".", 1)[-1]
        return text
    return "offline"


def _get_levelcard_config(user_id: str) -> tuple[str, dict[str, str]]:
    style_data = _load_json(LEVELCARD_STYLE_FILE, {})
    raw_user = style_data.get(user_id, {})
    if not isinstance(raw_user, dict):
        raw_user = {}

    background_url = str(raw_user.get("background_url", "")).strip()
    if not background_url:
        legacy_backgrounds = _load_json(BACKGROUND_FILE, {})
        background_url = str(legacy_backgrounds.get(user_id, DEFAULT_LEVELCARD_BG_URL))

    colors = dict(DEFAULT_LEVELCARD_STYLE)
    for key in DEFAULT_LEVELCARD_STYLE:
        parsed = _normalize_hex_color(str(raw_user.get(key, "")))
        if parsed:
            colors[key] = parsed
    return background_url, colors


def _save_levelcard_config(user_id: str, data: dict[str, str]) -> None:
    all_data = _load_json(LEVELCARD_STYLE_FILE, {})
    existing = all_data.get(user_id, {})
    if not isinstance(existing, dict):
        existing = {}
    existing.update(data)
    all_data[user_id] = existing
    _save_json(LEVELCARD_STYLE_FILE, all_data)


def _build_levelcard_cache_key(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _prune_levelcard_cache(now_ts: Optional[float] = None) -> None:
    now_ts = now_ts if now_ts is not None else time.time()
    for path in CACHE_DIR.glob("card_*"):
        try:
            age = now_ts - path.stat().st_mtime
            if age > LEVELCARD_CACHE_RETENTION_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _supporter_active(user_id: int) -> bool:
    data = _load_json(SUPPORTERS_FILE, {"supporters": {}})
    supporters = data.get("supporters", {})
    if not isinstance(supporters, dict):
        return False
    record = supporters.get(str(user_id))
    if not isinstance(record, dict):
        return False
    return bool(record.get("active", False))


async def _dispatch_module_event(
    bot: commands.Bot,
    guild: Optional[discord.Guild],
    module_name: str,
    action: str,
    actor: Optional[discord.abc.User] = None,
    details: str = "",
    channel_id: Optional[int] = None,
) -> None:
    if guild is None:
        return
    try:
        bot.dispatch("coffeecord_module_event", guild, module_name, action, actor, details, channel_id)
    except Exception:
        return


async def _check_level_up(bot: commands.Bot, guild_id: str, user_id: str, channel: Optional[discord.abc.Messageable]) -> None:
    xp_data = _load_json(XP_FILE, {})
    config = _load_json(CONFIG_FILE, {})
    rewards_data = _load_json(LEVEL_REWARDS_FILE, {})

    guild_cfg = _guild_leveling_cfg(config, guild_id)
    guild_rewards = rewards_data.get(guild_id, {})
    rewards = guild_rewards.get("rewards", {})
    replace_old = bool(guild_rewards.get("replace_old_roles", False))

    xp_data.setdefault(guild_id, {})
    user_data = xp_data[guild_id].get(user_id)
    if not user_data:
        user_data = _default_xp_user(guild_cfg)
        xp_data[guild_id][user_id] = user_data

    before_level = int(user_data.get("level", 1))
    level = _normalize_user_level(user_data)
    level_fixed = before_level < 1
    xp = int(user_data.get("xp", 0))
    next_level_xp = _next_level_xp_threshold(level, user_data, guild_cfg)
    if xp < next_level_xp:
        if level_fixed:
            _save_json(XP_FILE, xp_data)
        return

    old_level = level
    user_data["level"] = level + 1
    user_data["xp"] = 0
    user_data["next_level_xp"] = _computation_next_level_xp_after_level_up(int(user_data["level"]), guild_cfg)

    xp_data[guild_id][user_id] = user_data
    _save_json(XP_FILE, xp_data)

    guild: Optional[discord.Guild] = None
    if isinstance(channel, discord.TextChannel):
        guild = channel.guild
        try:
            await channel.send(
                await t(
                    guild.id,
                    "leveling.level_up_announcement",
                    user=f"<@{user_id}>",
                    level=str(user_data["level"]),
                )
            )
        except discord.HTTPException:
            pass
        await _dispatch_module_event(
            bot,
            guild,
            "leveling",
            "level_up",
            actor=guild.get_member(int(user_id)) if guild else None,
            details=f"user_id={user_id}; old_level={old_level}; new_level={user_data['level']}",
            channel_id=channel.id if hasattr(channel, "id") else None,
        )

    if guild is None:
        return
    member = guild.get_member(int(user_id))
    if member is None:
        return
    reward = rewards.get(str(user_data["level"]))
    if not isinstance(reward, dict):
        return
    role = guild.get_role(int(reward.get("role_id", 0)))
    if role is None:
        return

    try:
        await member.add_roles(role)
    except discord.HTTPException:
        return

    if replace_old:
        for lvl, info in rewards.items():
            if lvl == str(user_data["level"]) or not isinstance(info, dict):
                continue
            old_role = guild.get_role(int(info.get("role_id", 0)))
            if old_role and old_role in member.roles:
                try:
                    await member.remove_roles(old_role)
                except discord.HTTPException:
                    pass

    message_tpl = str(
        reward.get(
            "message",
            await t(guild.id, "leveling.default_reward_message"),
        )
    )
    content = (
        message_tpl.replace("{user}", member.mention)
        .replace("{role}", role.mention)
        .replace("{level}", str(user_data["level"]))
        .replace("{server}", guild.name)
    )
    try:
        await channel.send(content)  # type: ignore[arg-type]
    except Exception:
        pass

    await _dispatch_module_event(
        bot,
        guild,
        "leveling",
        "reward_granted",
        actor=member,
        details=f"level={user_data['level']}; role_id={role.id}",
        channel_id=channel.id if hasattr(channel, "id") else None,
    )


async def award_message_xp(bot: commands.Bot, message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    if not await is_module_enabled(message.guild.id, "leveling"):
        return
    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    xp_data = _load_json(XP_FILE, {})
    config = _load_json(CONFIG_FILE, {})
    guild_cfg = _guild_leveling_cfg(config, guild_id)
    xp_data.setdefault(guild_id, {})
    if user_id not in xp_data[guild_id]:
        xp_data[guild_id][user_id] = _default_xp_user(guild_cfg)
    u = xp_data[guild_id][user_id]
    _normalize_user_level(u)
    u["xp"] = int(u.get("xp", 0)) + int(guild_cfg.get("message_xp", 0))
    _save_json(XP_FILE, xp_data)
    await _check_level_up(bot, guild_id, user_id, message.channel)


async def award_reaction_xp(bot: commands.Bot, reaction: discord.Reaction, user: discord.abc.User) -> None:
    if user.bot or reaction.message.guild is None:
        return
    if not await is_module_enabled(reaction.message.guild.id, "leveling"):
        return
    guild_id = str(reaction.message.guild.id)
    user_id = str(user.id)
    xp_data = _load_json(XP_FILE, {})
    config = _load_json(CONFIG_FILE, {})
    guild_cfg = _guild_leveling_cfg(config, guild_id)
    xp_data.setdefault(guild_id, {})
    if user_id not in xp_data[guild_id]:
        xp_data[guild_id][user_id] = _default_xp_user(guild_cfg)
    u = xp_data[guild_id][user_id]
    _normalize_user_level(u)
    u["xp"] = int(u.get("xp", 0)) + int(guild_cfg.get("reaction_xp", 0))
    _save_json(XP_FILE, xp_data)
    await _check_level_up(bot, guild_id, user_id, reaction.message.channel)


async def award_quest_xp(
    bot: commands.Bot,
    guild: discord.Guild,
    user_id: int,
    xp_amount: int,
    channel: Optional[discord.abc.Messageable] = None,
) -> None:
    """Grant flat XP for quest rewards (same path as message/reaction XP)."""
    if xp_amount <= 0:
        return
    if not await is_module_enabled(guild.id, "leveling"):
        return
    guild_id = str(guild.id)
    uid = str(user_id)
    xp_data = _load_json(XP_FILE, {})
    config = _load_json(CONFIG_FILE, {})
    guild_cfg = _guild_leveling_cfg(config, guild_id)
    xp_data.setdefault(guild_id, {})
    if uid not in xp_data[guild_id]:
        xp_data[guild_id][uid] = _default_xp_user(guild_cfg)
    u = xp_data[guild_id][uid]
    _normalize_user_level(u)
    u["xp"] = int(u.get("xp", 0)) + int(xp_amount)
    _save_json(XP_FILE, xp_data)
    announce: Optional[discord.abc.Messageable] = channel
    if announce is None:
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            announce = guild.system_channel
        else:
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.send_messages and perms.view_channel:
                    announce = ch
                    break
    if announce is not None:
        await _check_level_up(bot, guild_id, uid, announce)


async def award_voice_xp(
    bot: commands.Bot,
    member: discord.Member,
    active_vc_members: dict[str, dict[str, float]],
) -> None:
    guild_id = str(member.guild.id)
    user_id = str(member.id)
    if not await is_module_enabled(member.guild.id, "leveling"):
        return
    if guild_id not in active_vc_members or user_id not in active_vc_members[guild_id]:
        return
    join_time = active_vc_members[guild_id].pop(user_id)
    duration = asyncio.get_event_loop().time() - join_time
    minutes = int(duration / 60)
    if minutes <= 0:
        return
    xp_data = _load_json(XP_FILE, {})
    config = _load_json(CONFIG_FILE, {})
    guild_cfg = _guild_leveling_cfg(config, guild_id)
    xp_data.setdefault(guild_id, {})
    if user_id not in xp_data[guild_id]:
        xp_data[guild_id][user_id] = _default_xp_user(guild_cfg)
    u = xp_data[guild_id][user_id]
    _normalize_user_level(u)
    gained_xp = int(guild_cfg.get("vc_minute_xp", 0)) * minutes
    u["xp"] = int(u.get("xp", 0)) + gained_xp
    _save_json(XP_FILE, xp_data)
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel is not None:
        await _check_level_up(bot, guild_id, user_id, channel)


class LevelingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    xp_group = app_commands.Group(
        name="xp",
        description="XP and leveling configuration commands",
)
    levelreward_group = app_commands.Group(
        name="levelreward",
        description="Level reward role commands",
)
    levelcard_group = app_commands.Group(
        name="levelcard",
        description="Level card customization commands",
)

    @levelcard_group.command(name="customize", description="Customize your level card background and colors.")
    @app_commands.describe(
        url="Background image URL (GIFs require supporter)",
        name_text="Display name color (hex, e.g. #50FF78)",
        level_text="Level text color (hex)",
        rank_text="Rank text color (hex)",
        xp_text="XP text color (hex)",
        progress_fill="Progress bar fill color (hex)",
        progress_track="Progress bar track color (hex)",
        separator="Separator line color (hex)",
        stroke="Text outline color (hex)",
    )
    async def levelcard_customize(
        self,
        interaction: discord.Interaction,
        url: Optional[str] = None,
        name_text: Optional[str] = None,
        level_text: Optional[str] = None,
        rank_text: Optional[str] = None,
        xp_text: Optional[str] = None,
        progress_fill: Optional[str] = None,
        progress_track: Optional[str] = None,
        separator: Optional[str] = None,
        stroke: Optional[str] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return

        updates: dict[str, str] = {}
        if url is not None:
            session = getattr(self.bot, "http_session", None)
            close_session = session is None
            if session is None:
                session = aiohttp.ClientSession()
            try:
                if not await _validate_levelcard_image_url(session, url):
                    await interaction.response.send_message(
                        await t(interaction.user.id, "leveling.levelcard.invalid_image_url"),
                        ephemeral=True,
                    )
                    return
            finally:
                if close_session:
                    await session.close()

            if url.lower().endswith(".gif") and not _supporter_active(interaction.user.id):
                await interaction.response.send_message(
                    await t(interaction.user.id, "leveling.levelcard.supporter_gif_only"),
                    ephemeral=True,
                )
                return
            updates["background_url"] = url

        raw_colors = {
            "name_text": name_text,
            "level_text": level_text,
            "rank_text": rank_text,
            "xp_text": xp_text,
            "progress_fill": progress_fill,
            "progress_track": progress_track,
            "separator": separator,
            "stroke": stroke,
        }
        for key, raw_value in raw_colors.items():
            if raw_value is None:
                continue
            parsed = _normalize_hex_color(raw_value)
            if parsed is None:
                await interaction.response.send_message(
                    await t(
                        interaction.guild.id,
                        "leveling.levelcard.invalid_color",
                        key=key,
                    ),
                    ephemeral=True,
                )
                return
            updates[key] = parsed

        if not updates:
            await interaction.response.send_message(
                await t(interaction.user.id, "leveling.levelcard.no_changes"),
                ephemeral=True,
            )
            return

        _save_levelcard_config(str(interaction.user.id), updates)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "levelcard_customize",
            actor=log_actor_from_interaction(interaction),
            details="; ".join(f"{k}={v}" for k, v in updates.items()),
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        preview = ", ".join(f"{k}={v}" for k, v in updates.items())
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            await t(interaction.user.id, "leveling.levelcard.updated_response"),
            preview=preview,
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @levelcard_group.command(name="preset", description="Apply a predefined level card color theme.")
    @app_commands.describe(
        preset="Choose a predefined theme",
        url="Optional background image URL (GIFs require supporter)",
    )
    @app_commands.choices(
        preset=[
            app_commands.Choice(name="Default", value="default"),
            app_commands.Choice(name="Ocean", value="ocean"),
            app_commands.Choice(name="Sunset", value="sunset"),
            app_commands.Choice(name="Neon", value="neon"),
            app_commands.Choice(name="Royal", value="royal"),
        ]
    )
    async def levelcard_preset(
        self,
        interaction: discord.Interaction,
        preset: app_commands.Choice[str],
        url: Optional[str] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return

        preset_key = preset.value
        colors = LEVELCARD_PRESETS.get(preset_key)
        if colors is None:
            await interaction.response.send_message(
                await t(interaction.user.id, "leveling.levelcard.unknown_preset"),
                ephemeral=True,
            )
            return

        updates: dict[str, str] = dict(colors)
        if url is not None:
            session = getattr(self.bot, "http_session", None)
            close_session = session is None
            if session is None:
                session = aiohttp.ClientSession()
            try:
                if not await _validate_levelcard_image_url(session, url):
                    await interaction.response.send_message(
                        await t(interaction.user.id, "leveling.levelcard.invalid_image_url"),
                        ephemeral=True,
                    )
                    return
            finally:
                if close_session:
                    await session.close()

            if url.lower().endswith(".gif") and not _supporter_active(interaction.user.id):
                await interaction.response.send_message(
                    await t(interaction.user.id, "leveling.levelcard.supporter_gif_only"),
                    ephemeral=True,
                )
                return
            updates["background_url"] = url

        _save_levelcard_config(str(interaction.user.id), updates)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "levelcard_preset",
            actor=log_actor_from_interaction(interaction),
            details=f"preset={preset_key}; background_set={url is not None}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        await interaction.response.send_message(
            await t(
                interaction.guild.id,
                "leveling.levelcard.applied_preset",
                preset=preset.name,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="level",
        description="Show your or another user's level card.",
)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    async def level(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        target = user or interaction.user
        guild_id = str(interaction.guild.id)
        user_id = str(target.id)
        xp_data = _load_json(XP_FILE, {})
        config = _load_json(CONFIG_FILE, {})
        guild_cfg = _guild_leveling_cfg(config, guild_id)
        user_data = dict(xp_data.get(guild_id, {}).get(user_id) or _default_xp_user(guild_cfg))
        _normalize_user_level(user_data)
        xp = int(user_data.get("xp", 0))
        level = int(user_data.get("level", 1))
        xp_for_next_level = _next_level_xp_threshold(level, user_data, guild_cfg)
        progress = max(0.0, min(xp / xp_for_next_level, 1.0))
        bg_url, style_colors = _get_levelcard_config(user_id)
        supporter = _supporter_active(target.id)
        render_gif = bg_url.lower().endswith(".gif") and supporter

        guild_xp = xp_data.get(guild_id, {})
        sorted_users = sorted(
            guild_xp.items(),
            key=lambda x: (int(x[1].get("level", 1)), int(x[1].get("xp", 0))),
            reverse=True,
        )
        rank = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), 0)
        status_source = interaction.guild.get_member(target.id) or target
        status_key = _member_status_key(status_source)
        status_color = LEVELCARD_STATUS_COLORS.get(status_key, LEVELCARD_STATUS_COLORS["offline"])

        cache_payload = {
            "guild_id": guild_id,
            "user_id": user_id,
            "display_name": str(target.display_name),
            "avatar_url": str(target.display_avatar.url),
            "xp": xp,
            "level": level,
            "xp_for_next_level": xp_for_next_level,
            "rank": rank,
            "background_url": bg_url,
            "style_colors": style_colors,
            "status": status_key,
            "render_gif": render_gif,
        }
        cache_key = _build_levelcard_cache_key(cache_payload)
        cache_suffix = ".gif" if render_gif else ".png"
        cached_path = CACHE_DIR / f"card_{cache_key}{cache_suffix}"

        await interaction.response.defer()
        async with anti_abuse.heavy_task_slot():
            now_ts = time.time()
            _prune_levelcard_cache(now_ts)
            if cached_path.exists():
                try:
                    cache_age = now_ts - cached_path.stat().st_mtime
                    if cache_age <= LEVELCARD_CACHE_TTL_SECONDS:
                        await interaction.followup.send(file=discord.File(str(cached_path)))
                        return
                except OSError:
                    pass

            session = getattr(self.bot, "http_session", None)
            if session is None:
                session = aiohttp.ClientSession()
                close_session = True
            else:
                close_session = False
            try:
                async with session.get(bg_url) as resp:
                        if resp.status != 200:
                            await interaction.followup.send(
                                await t(interaction.user.id, "leveling.levelcard.download_failed"),
                                ephemeral=True,
                            )
                            return
                        bg_bytes = await resp.read()
            except Exception as e:
                await interaction.followup.send(
                    await t(
                        interaction.guild.id,
                        "leveling.levelcard.load_error",
                        error=str(e),
                    ),
                    ephemeral=True,
                )
                return
            finally:
                if close_session:
                    await session.close()

            avatar_bytes = None
            try:
                avatar_bytes = await target.display_avatar.replace(size=128).read()
            except Exception:
                pass

            try:
                level_label = await t(interaction.user.id, "leveling.level_card_label_level")
                rank_label = await t(interaction.user.id, "leveling.level_card_label_rank")
                xp_label = await t(interaction.user.id, "leveling.level_card_label_xp")
                await asyncio.to_thread(
                    _render_levelcard_sync,
                    bg_bytes,
                    bg_url.lower().endswith(".gif") and supporter,
                    avatar_bytes,
                    style_colors,
                    progress,
                    str(target.display_name),
                    level,
                    rank,
                    xp,
                    xp_for_next_level,
                    status_color,
                    cached_path,
                    render_gif,
                    level_label,
                    rank_label,
                    xp_label,
                )
            except Exception as e:
                await interaction.followup.send(
                    await t(
                        interaction.guild.id,
                        "leveling.levelcard.render_failed",
                        error=str(e),
                    ),
                    ephemeral=True,
                )
                return
            await interaction.followup.send(file=discord.File(str(cached_path)))

    @app_commands.command(
        name="xpset",
        description="Set a user's XP and level (Admin only)",
)
    @app_commands.describe(user="User", xp="XP value", level="Level value")
    async def xpset(self, interaction: discord.Interaction, user: discord.Member, xp: int, level: int) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)
        xp_data = _load_json(XP_FILE, {})
        config = _load_json(CONFIG_FILE, {})
        guild_cfg = _guild_leveling_cfg(config, guild_id)
        level_norm = max(1, int(level))
        xp_clamped = max(0, int(xp))
        next_xp = _next_level_xp_threshold(
            level_norm,
            {"xp": xp_clamped, "level": level_norm},
            guild_cfg,
        )
        xp_data.setdefault(guild_id, {})
        xp_data[guild_id][user_id] = {
            "xp": xp_clamped,
            "level": level_norm,
            "next_level_xp": next_xp,
        }
        _save_json(XP_FILE, xp_data)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "xp_set",
            actor=log_actor_from_interaction(interaction),
            details=f"target={user.id}; xp={xp}; level={level}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            await t(interaction.user.id, "leveling.xpset.success"),
            user=user.display_name,
            xp=str(xp_clamped),
            level=str(level_norm),
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @xp_group.command(name="config", description="Configure XP gain and leveling settings for your server (Admin only)")
    @app_commands.describe(
        message_xp="XP gained per message",
        reaction_xp="XP gained per reaction",
        vc_minute_xp="XP gained per minute in VC",
        poll_vote_xp="XP gained per poll vote",
        base_xp="XP required for Level 1",
        xp_scale="XP scaling factor (how much more XP per level)",
    )
    async def xp_config(
        self,
        interaction: discord.Interaction,
        message_xp: int,
        reaction_xp: int,
        vc_minute_xp: int,
        poll_vote_xp: int,
        base_xp: int,
        xp_scale: float,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        cfg = _load_json(CONFIG_FILE, {})
        guild_id = str(interaction.guild.id)
        cfg[guild_id] = {
            "message_xp": message_xp,
            "reaction_xp": reaction_xp,
            "vc_minute_xp": vc_minute_xp,
            "poll_vote_xp": poll_vote_xp,
            "base_xp": base_xp,
            "xp_scale": xp_scale,
        }
        _save_json(CONFIG_FILE, cfg)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "xp_config_update",
            actor=log_actor_from_interaction(interaction),
            details=(
                f"message_xp={message_xp}; reaction_xp={reaction_xp}; vc_minute_xp={vc_minute_xp}; "
                f"poll_vote_xp={poll_vote_xp}; base_xp={base_xp}; xp_scale={xp_scale}"
            ),
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        preview_lines: list[str] = []
        for lvl in [1, 2, 3, 5, 10]:
            preview_lines.append(
                await t(
                    interaction.guild.id,
                    "leveling.xp.preview_line",
                    level=str(lvl),
                    total_xp=str(int(sum(base_xp * (xp_scale ** (i - 1)) for i in range(1, lvl + 1)))),
                )
            )
        preview = "\n".join(preview_lines)
        embed = discord.Embed(
            title=await t(interaction.user.id, "leveling.xp.config_title"),
            description=(
                f"{await t(interaction.user.id, 'leveling.xp.message_xp', value=str(message_xp))}\n"
                f"{await t(interaction.user.id, 'leveling.xp.reaction_xp', value=str(reaction_xp))}\n"
                f"{await t(interaction.user.id, 'leveling.xp.vc_minute_xp', value=str(vc_minute_xp))}\n"
                f"{await t(interaction.user.id, 'leveling.xp.poll_vote_xp', value=str(poll_vote_xp))}\n"
                f"{await t(interaction.user.id, 'leveling.xp.base_xp', value=str(base_xp))}\n"
                f"{await t(interaction.user.id, 'leveling.xp.scale', value=str(xp_scale))}\n\n"
                f"{await t(interaction.user.id, 'leveling.xp.preview_header')}\n{preview}"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @levelreward_group.command(name="add", description="Add a level reward role.")
    async def levelreward_add(
        self,
        interaction: discord.Interaction,
        level: int,
        role: discord.Role,
        message: str = "🎉 Congrats {user}, you reached level {level} and earned {role}!",
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        data = _load_json(LEVEL_REWARDS_FILE, {})
        guild_data = data.get(guild_id, {"rewards": {}, "replace_old_roles": False})
        guild_data["rewards"][str(level)] = {"role_id": role.id, "message": message}
        data[guild_id] = guild_data
        _save_json(LEVEL_REWARDS_FILE, data)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "levelreward_add",
            actor=log_actor_from_interaction(interaction),
            details=f"level={level}; role_id={role.id}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            await t(interaction.user.id, "leveling.levelreward.added"),
            level=str(level),
            role=role.mention,
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @levelreward_group.command(name="remove", description="Remove a level reward role.")
    async def levelreward_remove(self, interaction: discord.Interaction, level: int) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        data = _load_json(LEVEL_REWARDS_FILE, {})
        if guild_id not in data or str(level) not in data[guild_id].get("rewards", {}):
            await interaction.response.send_message(
                await t(interaction.user.id, "leveling.levelreward.not_found"),
                ephemeral=True,
            )
            return
        del data[guild_id]["rewards"][str(level)]
        _save_json(LEVEL_REWARDS_FILE, data)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "levelreward_remove",
            actor=log_actor_from_interaction(interaction),
            details=f"level={level}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            await t(interaction.user.id, "leveling.levelreward.removed"),
            level=str(level),
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @levelreward_group.command(name="list", description="List all level reward roles.")
    async def levelreward_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        data = _load_json(LEVEL_REWARDS_FILE, {})
        guild_data = data.get(guild_id, {}).get("rewards", {})
        if not guild_data:
            await interaction.response.send_message(
                await t(interaction.user.id, "leveling.levelreward.none_configured"),
                ephemeral=True,
            )
            return
        desc = ""
        for lvl, info in sorted(guild_data.items(), key=lambda x: int(x[0])):
            role = interaction.guild.get_role(int(info["role_id"]))
            missing_role_label = await t(interaction.user.id, "leveling.levelreward.missing_role")
            desc += await t(
                interaction.guild.id,
                "leveling.levelreward.list_line",
                level=str(lvl),
                role=role.mention if role else missing_role_label,
            )
            desc += "\n"
        await interaction.response.send_message(
            embed=discord.Embed(
                title=await t(interaction.user.id, "leveling.levelreward.list_title"),
                description=desc,
                color=discord.Color.gold(),
            ),
            ephemeral=True,
        )

    @levelreward_group.command(name="mode", description="Choose whether to replace old reward roles.")
    async def levelreward_mode(self, interaction: discord.Interaction, replace_old_roles: bool) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        data = _load_json(LEVEL_REWARDS_FILE, {})
        guild_data = data.get(guild_id, {"rewards": {}, "replace_old_roles": False})
        guild_data["replace_old_roles"] = replace_old_roles
        data[guild_id] = guild_data
        _save_json(LEVEL_REWARDS_FILE, data)
        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "leveling",
            "levelreward_mode",
            actor=log_actor_from_interaction(interaction),
            details=f"replace_old_roles={replace_old_roles}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        await interaction.response.send_message(
            await t(
                interaction.guild.id,
                "leveling.levelreward.mode_replace_on" if replace_old_roles else "leveling.levelreward.mode_replace_off",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    cog = LevelingCog(bot)
    # add_cog() automatically registers any app_commands.Group class attributes,
    # so we must NOT call bot.tree.add_command() for xp_group / levelreward_group here.
    await bot.add_cog(cog)
