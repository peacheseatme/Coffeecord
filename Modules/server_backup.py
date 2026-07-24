"""Encrypted Discord server + Coffeecord Storage backups (/backup)."""
from __future__ import annotations

import asyncio
import base64
import glob
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.backup_crypto import (
    PHRASE_LEN,
    BackupCryptoError,
    generate_passphrase,
    pack_backup,
    unpack_backup,
)
from Modules.i18n import t_sync
from Modules.module_registry import is_module_enabled

__module_display_name__ = "Server Backup"
__module_description__ = "Encrypted server structure + Coffeecord config backups with host slots."
__module_category__ = "configuration"

LOGGER = logging.getLogger("coffeecord.server_backup")

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "Storage"
BACKUPS_GUILD_DIR = STORAGE_DIR / "Backups" / "guild"
THEME_STORAGE_DIR = STORAGE_DIR / "Config" / "theme_storage"
SUPPORTERS_FILE = STORAGE_DIR / "Data" / "supporters.json"

MODULE_ID = "server_backup"
BACKUP_I18N_PREFIX = "backup."

BACKUP_SLOTS_FREE = 1
BACKUP_SLOTS_SUPPORTER = 3
SUPPORTER_GRACE_DAYS = 35
SLOT_ID_MAX_LEN = 32
SLOT_ID_PATTERN = re.compile(rf"^[a-z0-9_-]{{1,{SLOT_ID_MAX_LEN}}}$")
SCHEMA_VERSION = 2
CREATE_RATE_SLEEP_S = 0.35
RESTORE_HOLD_CHANNEL_NAME = "cc-restore-hold"
MAX_EMOJI_BYTES = 256 * 1024
MAX_ICON_BYTES = 8 * 1024 * 1024

RESTORE_MODE_REPAIR = "repair"
RESTORE_MODE_OVERWRITE = "overwrite"
MESSAGE_HISTORY_LIMIT_PER_CHANNEL = 500
MESSAGE_RESTORE_BATCH_SLEEP_S = 0.45
MAX_RESTORED_MESSAGE_CHARS = 1800
MESSAGE_EMBED_LIMIT = 10
# Break Discord mention tokens so replay never notifies users/roles.
_PINGABLE_MENTION_RE = re.compile(r"<@!?\d+>|<@&\d+>|@everyone|@here")
SPINNER_FRAMES = ("-", "\\", "|", "/")
PROGRESS_MARK_PENDING = "○"
PROGRESS_MARK_DONE = "✓"
PROGRESS_MARK_SKIPPED = "–"
PROGRESS_SPINNER_INTERVAL_S = 0.45
PROGRESS_EDIT_THROTTLE_S = 0.9

_INDEX_LOCK = asyncio.Lock()


def _text(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{BACKUP_I18N_PREFIX}{key}", default=default, **params)


def _read_json_sync(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_sync(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


def _supporter_record_is_active(record: dict[str, Any]) -> bool:
    if not record or not record.get("active", False):
        return False
    if record.get("tier") != "subscription":
        return True
    last_payment_raw = record.get("last_payment")
    if not last_payment_raw:
        return False
    try:
        last_payment = datetime.fromisoformat(str(last_payment_raw))
    except ValueError:
        return False
    return (datetime.utcnow() - last_payment).days <= SUPPORTER_GRACE_DAYS


def is_supporter_user(user_id: int) -> bool:
    data = _read_json_sync(SUPPORTERS_FILE, {"supporters": {}})
    if not isinstance(data, dict):
        return False
    supporters = data.get("supporters", {})
    if isinstance(supporters, list):
        return str(user_id) in {str(x) for x in supporters}
    if not isinstance(supporters, dict):
        return False
    record = supporters.get(str(user_id))
    if isinstance(record, dict):
        return _supporter_record_is_active(record)
    return bool(record)


def max_backup_slots_for_user(user_id: int) -> int:
    return BACKUP_SLOTS_SUPPORTER if is_supporter_user(user_id) else BACKUP_SLOTS_FREE


def normalize_slot_id(raw: str) -> Optional[str]:
    s = raw.strip().lower()
    if not SLOT_ID_PATTERN.match(s):
        return None
    return s


# ---------------------------------------------------------------------------
# Storage slice (shared with uninstall-style guild JSON backups)
# ---------------------------------------------------------------------------


def collect_guild_storage_slice(guild_id: int) -> dict[str, Any]:
    """Collect per-guild JSON slices and theme assets under Storage/."""
    gid = str(guild_id)
    files: dict[str, Any] = {}
    for pattern in (
        str(STORAGE_DIR / "Config" / "*.json"),
        str(STORAGE_DIR / "Data" / "*.json"),
        str(STORAGE_DIR / "Temp" / "*.json"),
    ):
        for path in glob.glob(pattern):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            rel = os.path.relpath(path, STORAGE_DIR)
            if isinstance(data, dict) and gid in data:
                files[rel] = {"mode": "guild_key", "data": {gid: data[gid]}}
            elif os.path.basename(path).startswith(gid):
                files[rel] = {"mode": "full_file", "data": data}

    themes: dict[str, str] = {}
    theme_dir = THEME_STORAGE_DIR / gid
    if theme_dir.is_dir():
        for path in theme_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                themes[str(path.relative_to(theme_dir))] = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue

    return {"files": files, "theme_storage": themes}


def apply_guild_storage_slice(
    target_guild_id: int,
    coffeecord: dict[str, Any],
    *,
    id_map: dict[int, int],
) -> list[str]:
    """Write storage slices into target guild id, remapping snowflakes when possible."""
    notes: list[str] = []
    src_gid = str(coffeecord.get("source_guild_id") or "")
    tgt_gid = str(target_guild_id)
    files = coffeecord.get("files") or {}
    if not isinstance(files, dict):
        return ["Invalid coffeecord.files payload"]

    for rel, entry in files.items():
        if not isinstance(entry, dict):
            continue
        mode = entry.get("mode")
        data = entry.get("data")
        abs_path = STORAGE_DIR / rel
        try:
            if mode == "guild_key" and isinstance(data, dict):
                existing = _read_json_sync(abs_path, {})
                if not isinstance(existing, dict):
                    existing = {}
                # Prefer source key, else first value.
                slice_data = data.get(src_gid) if src_gid and src_gid in data else next(iter(data.values()), None)
                if slice_data is None:
                    continue
                remapped = remap_ids_in_obj(slice_data, id_map)
                existing[tgt_gid] = remapped
                if src_gid and src_gid != tgt_gid and src_gid in existing and src_gid in data:
                    # Do not delete source guild data when restoring to another server.
                    pass
                _write_json_sync(abs_path, existing)
                try:
                    from Modules import json_cache

                    json_cache.invalidate(abs_path)
                except Exception:
                    pass
            elif mode == "full_file":
                remapped = remap_ids_in_obj(data, id_map)
                _write_json_sync(abs_path, remapped)
                try:
                    from Modules import json_cache

                    json_cache.invalidate(abs_path)
                except Exception:
                    pass
            else:
                notes.append(f"Skipped unknown storage entry: {rel}")
        except OSError as exc:
            notes.append(f"Failed writing {rel}: {exc}")

    themes = coffeecord.get("theme_storage") or {}
    if isinstance(themes, dict) and themes:
        out_dir = THEME_STORAGE_DIR / tgt_gid
        out_dir.mkdir(parents=True, exist_ok=True)
        for rel, b64 in themes.items():
            try:
                raw = base64.b64decode(str(b64).encode("ascii"))
                dest = out_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
            except (OSError, ValueError) as exc:
                notes.append(f"Failed theme asset {rel}: {exc}")
    return notes


def remap_ids_in_obj(obj: Any, id_map: dict[int, int]) -> Any:
    """Best-effort remap of snowflake ints/strings found in id_map."""
    if isinstance(obj, dict):
        return {k: remap_ids_in_obj(v, id_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [remap_ids_in_obj(v, id_map) for v in obj]
    if isinstance(obj, int) and obj in id_map:
        return id_map[obj]
    if isinstance(obj, str) and obj.isdigit():
        n = int(obj)
        if n in id_map:
            return str(id_map[n])
    return obj


# ---------------------------------------------------------------------------
# Host slots
# ---------------------------------------------------------------------------


def _guild_backup_dir(guild_id: int) -> Path:
    path = BACKUPS_GUILD_DIR / str(guild_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path(guild_id: int) -> Path:
    return _guild_backup_dir(guild_id) / "index.json"


def _slot_blob_path(guild_id: int, slot_id: str) -> Path:
    return _guild_backup_dir(guild_id) / f"{slot_id}.ccbak"


def load_slot_index(guild_id: int) -> dict[str, Any]:
    data = _read_json_sync(_index_path(guild_id), {"slots": {}})
    if not isinstance(data, dict):
        return {"slots": {}}
    slots = data.get("slots")
    if not isinstance(slots, dict):
        data["slots"] = {}
    return data


def save_slot_index(guild_id: int, index: dict[str, Any]) -> None:
    _write_json_sync(_index_path(guild_id), index)


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------


async def _fetch_asset_b64(url: str | None, *, max_bytes: int) -> str | None:
    if not url:
        return None
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                if len(data) > max_bytes:
                    return None
                return base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def _channel_type_name(channel: discord.abc.GuildChannel) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.TextChannel):
        return "news" if channel.is_news() else "text"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    if isinstance(channel, discord.ForumChannel):
        try:
            if channel.type == discord.ChannelType.media:
                return "media"
        except Exception:
            pass
        return "forum"
    return getattr(channel.type, "name", "unknown")


def _serialize_overwrites(channel: discord.abc.GuildChannel) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for target, ow in channel.overwrites.items():
        allow, deny = ow.pair()
        entry: dict[str, Any] = {
            "allow": allow.value,
            "deny": deny.value,
        }
        if isinstance(target, discord.Role):
            entry["kind"] = "role"
            entry["id"] = target.id
            entry["name"] = target.name
        elif isinstance(target, discord.Member):
            entry["kind"] = "member"
            entry["id"] = target.id
        else:
            continue
        out.append(entry)
    return out


def _enum_int(value: Any, default: int = 0) -> int:
    """Serialize discord.py enum / IntEnum values across API versions."""
    if value is None:
        return default
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _serialize_embed(embed: discord.Embed) -> dict[str, Any] | None:
    """Store a Discord embed as JSON-compatible dict (Embed.to_dict)."""
    try:
        data = embed.to_dict()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # Not needed for reconstruction; can confuse older clients.
    data.pop("flags", None)
    if not data.get("type"):
        data["type"] = "rich"
    return data


def _neutralize_embed_tree(obj: Any) -> Any:
    """Recursively neutralize pingable mentions inside embed JSON."""
    if isinstance(obj, dict):
        return {k: _neutralize_embed_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_neutralize_embed_tree(v) for v in obj]
    if isinstance(obj, str):
        return _neutralize_mentions(obj)
    return obj


def _rebuild_embeds(raw_list: Any) -> list[discord.Embed]:
    """Rebuild discord.Embed objects from stored JSON (or legacy title/description dicts)."""
    out: list[discord.Embed] = []
    if not isinstance(raw_list, list):
        return out
    for item in raw_list[:MESSAGE_EMBED_LIMIT]:
        if not isinstance(item, dict):
            continue
        cleaned = _neutralize_embed_tree(dict(item))
        if not isinstance(cleaned, dict):
            continue
        cleaned.pop("flags", None)
        if not cleaned.get("type"):
            cleaned["type"] = "rich"
        try:
            emb = discord.Embed.from_dict(cleaned)
            out.append(emb)
            continue
        except Exception:
            pass
        # Legacy / partial payloads: title + description only.
        title = str(cleaned.get("title") or "").strip()[:256] or None
        desc = str(cleaned.get("description") or "").strip()[:4096] or None
        url = cleaned.get("url")
        color = cleaned.get("color")
        try:
            emb = discord.Embed(title=title, description=desc, url=url if isinstance(url, str) else None)
            if color is not None:
                try:
                    emb.colour = discord.Colour(int(color) & 0xFFFFFF)
                except (TypeError, ValueError):
                    pass
            if emb.title or emb.description or emb.url:
                out.append(emb)
        except Exception:
            continue
    return out


async def _capture_channel_messages(
    channel: discord.abc.GuildChannel,
    *,
    limit: int = MESSAGE_HISTORY_LIMIT_PER_CHANNEL,
) -> list[dict[str, Any]]:
    """Capture recent messages (including bots) oldest-first for restore replay."""
    if not isinstance(channel, discord.TextChannel):
        return []
    collected: list[dict[str, Any]] = []
    try:
        async for msg in channel.history(limit=limit, oldest_first=False):
            if msg.type not in (discord.MessageType.default, discord.MessageType.reply):
                continue
            embeds: list[dict[str, Any]] = []
            for emb in msg.embeds[:MESSAGE_EMBED_LIMIT]:
                serialized = _serialize_embed(emb)
                if serialized is not None:
                    embeds.append(serialized)
            attachments = [a.url for a in msg.attachments[:5] if a.url]
            content = msg.content or ""
            if not content and not embeds and not attachments:
                continue
            collected.append(
                {
                    "author_id": msg.author.id,
                    "author_name": str(msg.author),
                    "bot": bool(msg.author.bot),
                    "content": content[:4000],
                    "timestamp": int(msg.created_at.timestamp()),
                    "embeds": embeds,
                    "attachments": attachments,
                }
            )
    except (discord.Forbidden, discord.HTTPException):
        return []
    collected.reverse()  # oldest first for restore
    return collected


async def build_discord_snapshot(
    guild: discord.Guild,
    *,
    include_messages: bool = True,
    message_limit: int = MESSAGE_HISTORY_LIMIT_PER_CHANNEL,
) -> dict[str, Any]:
    roles_data: list[dict[str, Any]] = []
    skipped_roles: list[str] = []
    for role in sorted(guild.roles, key=lambda r: r.position):
        if role.is_default():
            roles_data.append(
                {
                    "id": role.id,
                    "name": role.name,
                    "everyone": True,
                    "permissions": role.permissions.value,
                    "position": role.position,
                }
            )
            continue
        if role.managed:
            skipped_roles.append(f"{role.name} ({role.id})")
            continue
        roles_data.append(
            {
                "id": role.id,
                "name": role.name,
                "everyone": False,
                "color": role.color.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "permissions": role.permissions.value,
                "position": role.position,
                "display_icon": None,
            }
        )

    categories = [c for c in guild.channels if isinstance(c, discord.CategoryChannel)]
    categories_sorted = sorted(categories, key=lambda c: c.position)
    channels_data: list[dict[str, Any]] = []

    def _base_channel(ch: discord.abc.GuildChannel) -> dict[str, Any]:
        return {
            "id": ch.id,
            "name": ch.name,
            "type": _channel_type_name(ch),
            "position": ch.position,
            "category_id": ch.category_id,
            "overwrites": _serialize_overwrites(ch),
        }

    for cat in categories_sorted:
        entry = _base_channel(cat)
        entry["nsfw"] = getattr(cat, "nsfw", False)
        channels_data.append(entry)

    non_cats = [c for c in guild.channels if not isinstance(c, discord.CategoryChannel)]
    for ch in sorted(non_cats, key=lambda c: (c.category_id or 0, c.position)):
        entry = _base_channel(ch)
        if isinstance(ch, discord.TextChannel):
            entry.update(
                {
                    "topic": ch.topic,
                    "nsfw": ch.nsfw,
                    "slowmode_delay": ch.slowmode_delay,
                    "default_auto_archive_duration": ch.default_auto_archive_duration,
                }
            )
        elif isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            entry.update(
                {
                    "bitrate": ch.bitrate,
                    "user_limit": ch.user_limit,
                    "rtc_region": str(ch.rtc_region) if ch.rtc_region else None,
                }
            )
        elif isinstance(ch, discord.ForumChannel):
            entry.update(
                {
                    "topic": ch.topic,
                    "nsfw": ch.nsfw,
                    "slowmode_delay": ch.slowmode_delay,
                    "default_auto_archive_duration": ch.default_auto_archive_duration,
                }
            )
        channels_data.append(entry)

    members_data: dict[str, Any] = {}
    missing_role_notes: list[str] = []
    async for member in guild.fetch_members(limit=None):
        role_ids = [r.id for r in member.roles if not r.is_default() and not r.managed]
        members_data[str(member.id)] = {
            "nick": member.nick,
            "role_ids": role_ids,
            "display_name": member.display_name,
        }

    emojis_data: list[dict[str, Any]] = []
    for emoji in guild.emojis:
        b64 = None
        try:
            b64 = await _fetch_asset_b64(str(emoji.url), max_bytes=MAX_EMOJI_BYTES)
        except Exception:
            b64 = None
        emojis_data.append(
            {
                "id": emoji.id,
                "name": emoji.name,
                "animated": emoji.animated,
                "image_b64": b64,
            }
        )

    icon_b64 = await _fetch_asset_b64(guild.icon.url if guild.icon else None, max_bytes=MAX_ICON_BYTES)
    banner_b64 = await _fetch_asset_b64(guild.banner.url if guild.banner else None, max_bytes=MAX_ICON_BYTES)

    invite_notes = [
        "Discord invite codes cannot be restored bit-for-bit.",
        "After restore, create a new invite for members who are no longer in the server.",
    ]
    if guild.vanity_url_code:
        invite_notes.append(f"Previous vanity code (informational): {guild.vanity_url_code}")

    settings = {
        "name": guild.name,
        "description": guild.description,
        "verification_level": _enum_int(guild.verification_level),
        "explicit_content_filter": _enum_int(guild.explicit_content_filter),
        "default_notifications": _enum_int(guild.default_notifications),
        "afk_timeout": guild.afk_timeout,
        "afk_channel_id": guild.afk_channel.id if guild.afk_channel else None,
        "system_channel_id": guild.system_channel.id if guild.system_channel else None,
        "system_channel_flags": _enum_int(guild.system_channel_flags, 0),
        "preferred_locale": str(guild.preferred_locale) if guild.preferred_locale else None,
        "icon_b64": icon_b64,
        "banner_b64": banner_b64,
    }

    messages_by_channel: dict[str, list[dict[str, Any]]] = {}
    if include_messages:
        for ch in guild.text_channels:
            msgs = await _capture_channel_messages(ch, limit=message_limit)
            if msgs:
                messages_by_channel[str(ch.id)] = msgs

    return {
        "settings": settings,
        "roles": roles_data,
        "channels": channels_data,
        "members": members_data,
        "emojis": emojis_data,
        "messages": messages_by_channel,
        "notes": {
            "skipped_managed_roles": skipped_roles,
            "invite_rejoin": invite_notes,
            "extra": missing_role_notes,
            "message_limit_per_channel": message_limit if include_messages else 0,
        },
    }


async def build_backup_payload(
    guild: discord.Guild,
    *,
    creator_id: int,
    slot_name: str,
    include_messages: bool = True,
) -> dict[str, Any]:
    discord_data = await build_discord_snapshot(guild, include_messages=include_messages)
    storage = await asyncio.to_thread(collect_guild_storage_slice, guild.id)
    storage["source_guild_id"] = str(guild.id)
    msg_note = (
        f"Text-channel messages included (up to {MESSAGE_HISTORY_LIMIT_PER_CHANNEL} per channel; "
        f"embeds stored as JSON for reconstruction)."
        if include_messages
        else "Message history was not included in this backup."
    )
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "created_at": int(time.time()),
            "source_guild_id": guild.id,
            "source_guild_name": guild.name,
            "creator_id": creator_id,
            "slot_name": slot_name,
            "include_messages": include_messages,
            "notes": [
                "Blueprint restore: regenerates structure from snapshot.",
                msg_note,
                "Restored messages are replayed by the bot with author + date labels; embeds are rebuilt from stored JSON.",
                "Keep your 18-character decrypt phrase offline — it is never stored on the host.",
            ],
        },
        "discord": discord_data,
        "coffeecord": storage,
    }


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


async def _list_guild_channels(guild: discord.Guild) -> list[discord.abc.GuildChannel]:
    try:
        return list(await guild.fetch_channels())
    except Exception:
        return list(guild.channels)


async def wipe_guild_structure(
    guild: discord.Guild,
    *,
    progress_cb=None,
) -> dict[str, Any]:
    """Delete channels, deletable roles, and custom emoji for overwrite restore.

    Discord refuses deleting the last channel, so we create a temporary hold
    channel first, wipe everything else, then leave the hold for the restore
    pass to remove after new channels exist.

    Categories are deleted only after their children are gone (Discord requirement).
    """
    stats: dict[str, Any] = {
        "channels_deleted": 0,
        "roles_deleted": 0,
        "emojis_deleted": 0,
        "hold_channel_id": None,
        "skipped": [],
    }

    async def _progress(msg: str) -> None:
        if progress_cb:
            await progress_cb(msg)

    async def _delete_one(ch: discord.abc.GuildChannel, *, label: str | None = None) -> bool:
        name = label or getattr(ch, "name", str(getattr(ch, "id", "?")))
        try:
            await ch.delete(reason="Coffeecord backup overwrite")
            stats["channels_deleted"] += 1
            await asyncio.sleep(CREATE_RATE_SLEEP_S)
            return True
        except Exception as exc:
            stats["skipped"].append(f"delete {name}: {exc}")
            return False

    me = guild.me
    hold: discord.TextChannel | None = None
    await _progress("Overwrite: creating temporary hold channel…")
    try:
        hold = await guild.create_text_channel(
            RESTORE_HOLD_CHANNEL_NAME,
            reason="Coffeecord backup overwrite hold",
        )
        stats["hold_channel_id"] = hold.id
        # Ensure hold is not nested under a category we need to delete.
        if hold.category_id is not None:
            try:
                await hold.edit(category=None, reason="Coffeecord backup overwrite hold")
            except Exception as exc:
                stats["skipped"].append(f"hold uncategorize: {exc}")
        await asyncio.sleep(CREATE_RATE_SLEEP_S)
    except Exception as exc:
        stats["skipped"].append(f"hold channel create: {exc}")
        LOGGER.warning("overwrite hold channel failed in guild %s: %s", guild.id, exc)

    def _is_hold(ch: discord.abc.GuildChannel) -> bool:
        return hold is not None and ch.id == hold.id

    async def _delete_non_categories() -> None:
        channels = await _list_guild_channels(guild)
        for ch in channels:
            if isinstance(ch, discord.CategoryChannel) or _is_hold(ch):
                continue
            await _delete_one(ch)

    async def _empty_and_delete_categories() -> None:
        """Delete every category after clearing its children (required by Discord)."""
        channels = await _list_guild_channels(guild)
        cats = [c for c in channels if isinstance(c, discord.CategoryChannel)]
        for cat in cats:
            # Prefer live category.channels list so we catch children the flat list missed.
            children: list[discord.abc.GuildChannel] = []
            try:
                children = list(cat.channels)
            except Exception:
                children = [
                    c
                    for c in await _list_guild_channels(guild)
                    if getattr(c, "category_id", None) == cat.id
                ]
            for child in children:
                if _is_hold(child):
                    try:
                        await child.edit(category=None, reason="Coffeecord backup overwrite hold")
                    except Exception as exc:
                        stats["skipped"].append(f"hold move out of {cat.name}: {exc}")
                    continue
                await _delete_one(child, label=f"{cat.name}/{getattr(child, 'name', child.id)}")
            # Re-check emptiness before deleting the category itself.
            remaining = []
            try:
                remaining = [c for c in cat.channels if not _is_hold(c)]
            except Exception:
                remaining = [
                    c
                    for c in await _list_guild_channels(guild)
                    if getattr(c, "category_id", None) == cat.id and not _is_hold(c)
                ]
            if remaining:
                for child in remaining:
                    await _delete_one(child, label=f"{cat.name}/{getattr(child, 'name', child.id)}")
            await _delete_one(cat, label=f"category:{cat.name}")

    await _progress("Overwrite: deleting channels…")
    await _delete_non_categories()
    await _delete_non_categories()  # race / delayed creates

    await _progress("Overwrite: deleting categories…")
    await _empty_and_delete_categories()
    await _empty_and_delete_categories()  # second pass for stubborn leftovers
    # Final sweep: any category still present.
    leftover_cats = [c for c in await _list_guild_channels(guild) if isinstance(c, discord.CategoryChannel)]
    if leftover_cats:
        await _empty_and_delete_categories()
        leftover_cats = [c for c in await _list_guild_channels(guild) if isinstance(c, discord.CategoryChannel)]
        for cat in leftover_cats:
            stats["skipped"].append(f"category still present after wipe: {cat.name} ({cat.id})")

    await _progress("Overwrite: deleting custom emoji…")
    for emoji in list(guild.emojis):
        try:
            await emoji.delete(reason="Coffeecord backup overwrite")
            stats["emojis_deleted"] += 1
            await asyncio.sleep(CREATE_RATE_SLEEP_S)
        except Exception as exc:
            stats["skipped"].append(f"delete emoji {emoji.name}: {exc}")

    await _progress("Overwrite: deleting roles…")
    if me is not None:
        for role in sorted(list(guild.roles), key=lambda r: r.position, reverse=True):
            if role.is_default() or role.managed:
                continue
            if role >= me.top_role:
                stats["skipped"].append(f"delete role {role.name}: above bot")
                continue
            try:
                await role.delete(reason="Coffeecord backup overwrite")
                stats["roles_deleted"] += 1
                await asyncio.sleep(CREATE_RATE_SLEEP_S)
            except Exception as exc:
                stats["skipped"].append(f"delete role {role.name}: {exc}")

    return stats


def _neutralize_mentions(text: str) -> str:
    """Insert zero-width space after @ so Discord will not notify anyone."""

    def _break(match: re.Match[str]) -> str:
        return match.group(0).replace("@", "@\u200b")

    return _PINGABLE_MENTION_RE.sub(_break, text)


def _format_restored_message(entry: dict[str, Any]) -> str:
    """Plain-text header + content + attachment URLs (embeds are sent separately)."""
    author = _neutralize_mentions(str(entry.get("author_name") or "Unknown"))
    bot_tag = " [bot]" if entry.get("bot") else ""
    try:
        ts = int(entry.get("timestamp") or 0)
    except (TypeError, ValueError):
        ts = 0
    header = f"**{author}**{bot_tag} · <t:{ts}:f>"
    parts: list[str] = [header]
    content = _neutralize_mentions(str(entry.get("content") or "").strip())
    if content:
        if len(content) > MAX_RESTORED_MESSAGE_CHARS:
            content = content[:MAX_RESTORED_MESSAGE_CHARS] + "…"
        parts.append(content)
    for url in entry.get("attachments") or []:
        if url:
            parts.append(str(url))
    text = "\n".join(parts)
    if len(text) > 2000:
        text = text[:1997] + "…"
    return text


async def restore_channel_messages(
    guild: discord.Guild,
    discord_data: dict[str, Any],
    id_map: dict[int, int],
    *,
    progress_cb=None,
) -> dict[str, Any]:
    """Replay stored messages into remapped text channels (bot-authored with date labels)."""
    stats: dict[str, Any] = {
        "messages_restored": 0,
        "embeds_restored": 0,
        "channels": 0,
        "skipped": [],
    }
    messages = discord_data.get("messages") or {}
    if not isinstance(messages, dict) or not messages:
        return stats

    async def _progress(msg: str) -> None:
        if progress_cb:
            await progress_cb(msg)

    allowed = discord.AllowedMentions.none()
    for old_id_str, msgs in messages.items():
        if not isinstance(msgs, list) or not msgs:
            continue
        try:
            old_id = int(old_id_str)
        except (TypeError, ValueError):
            continue
        new_id = id_map.get(old_id)
        channel = guild.get_channel(new_id) if new_id else None
        if not isinstance(channel, discord.TextChannel):
            stats["skipped"].append(f"messages: channel {old_id_str} not remapped")
            continue
        stats["channels"] += 1
        await _progress(f"Restoring messages in #{channel.name}…")
        for entry in msgs:
            if not isinstance(entry, dict):
                continue
            content = _format_restored_message(entry)
            embeds = _rebuild_embeds(entry.get("embeds"))
            if not content.strip() and not embeds:
                continue
            try:
                send_kwargs: dict[str, Any] = {
                    "allowed_mentions": allowed,
                    "silent": True,
                }
                if content.strip():
                    send_kwargs["content"] = content
                if embeds:
                    send_kwargs["embeds"] = embeds
                await channel.send(**send_kwargs)
                stats["messages_restored"] += 1
                stats["embeds_restored"] += len(embeds)
                await asyncio.sleep(MESSAGE_RESTORE_BATCH_SLEEP_S)
            except Exception as exc:
                stats["skipped"].append(f"message in #{channel.name}: {exc}")
                break

    return stats


async def republish_interactive_panels(
    bot: commands.Bot,
    guild: discord.Guild,
    *,
    force: bool = False,
    progress_cb=None,
) -> list[str]:
    """Re-post ticket/verify/reaction/color panels so buttons work after restore.

    force=True (overwrite): always create fresh panels.
    force=False (repair): keep existing panels when their messages still resolve.
    """
    notes: list[str] = []

    async def _progress(msg: str) -> None:
        if progress_cb:
            await progress_cb(msg)

    await _progress(
        "Checking interactive panels…" if not force else "Re-publishing interactive panels…"
    )

    # Tickets
    try:
        tickets_mod = sys.modules.get("Modules.tickets")
        if tickets_mod is None:
            import Modules.tickets as tickets_mod  # type: ignore
        republish = getattr(tickets_mod, "republish_ticket_panel", None)
        if callable(republish):
            note = await republish(bot, guild, force=force)
            if note:
                notes.append(note)
    except Exception as exc:
        notes.append(f"tickets panel: {exc}")
        LOGGER.warning("ticket panel republish failed: %s", exc)

    # Verification (defined on Bot.py / __main__)
    try:
        main = sys.modules.get("__main__")
        republish = getattr(main, "republish_verify_panel", None) if main else None
        if callable(republish):
            note = await republish(bot, guild, force=force)
            if note:
                notes.append(note)
    except Exception as exc:
        notes.append(f"verify panel: {exc}")
        LOGGER.warning("verify panel republish failed: %s", exc)

    # Reaction roles
    try:
        rr_cog = bot.get_cog("ReactionRoleCog")
        if rr_cog is not None and hasattr(rr_cog, "republish_panels_after_restore"):
            notes.extend(await rr_cog.republish_panels_after_restore(guild, force=force))
    except Exception as exc:
        notes.append(f"reactionrole panels: {exc}")
        LOGGER.warning("reactionrole republish failed: %s", exc)

    # Color roles
    try:
        cr_cog = bot.get_cog("ColorRoleCog")
        if cr_cog is not None and hasattr(cr_cog, "republish_panels_after_restore"):
            notes.extend(await cr_cog.republish_panels_after_restore(guild, force=force))
    except Exception as exc:
        notes.append(f"colorrole panels: {exc}")
        LOGGER.warning("colorrole republish failed: %s", exc)

    return notes

async def restore_discord_structure(
    guild: discord.Guild,
    discord_data: dict[str, Any],
    *,
    mode: str = RESTORE_MODE_REPAIR,
    hold_channel_id: int | None = None,
    progress_cb=None,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Restore roles/channels. repair=merge/update; overwrite=create fresh after wipe."""
    reuse_existing = mode != RESTORE_MODE_OVERWRITE
    id_map: dict[int, int] = {}
    stats: dict[str, Any] = {
        "roles_created": 0,
        "roles_updated": 0,
        "channels_created": 0,
        "channels_updated": 0,
        "emojis_created": 0,
        "members_updated": 0,
        "members_missing": 0,
        "skipped": [],
        "mode": mode,
    }

    async def _progress(msg: str) -> None:
        if progress_cb:
            await progress_cb(msg)

    # Roles
    await _progress("Restoring roles…")
    everyone = guild.default_role
    roles = discord_data.get("roles") or []
    for role_info in roles:
        if role_info.get("everyone"):
            id_map[int(role_info["id"])] = everyone.id
            try:
                await everyone.edit(
                    permissions=discord.Permissions(role_info.get("permissions", everyone.permissions.value)),
                    reason="Coffeecord backup restore",
                )
            except discord.HTTPException:
                stats["skipped"].append("@everyone permissions unchanged")
            break

    existing_by_name = {r.name.lower(): r for r in guild.roles if not r.is_default()}
    creatable = [r for r in roles if not r.get("everyone")]
    creatable_sorted = sorted(creatable, key=lambda r: int(r.get("position", 0)))

    for role_info in creatable_sorted:
        old_id = int(role_info["id"])
        name = str(role_info.get("name") or "role")
        existing = existing_by_name.get(name.lower()) if reuse_existing else None
        if existing is not None:
            id_map[old_id] = existing.id
            if reuse_existing and guild.me is not None and existing < guild.me.top_role:
                try:
                    await existing.edit(
                        permissions=discord.Permissions(int(role_info.get("permissions", 0))),
                        colour=discord.Colour(int(role_info.get("color", 0))),
                        hoist=bool(role_info.get("hoist", False)),
                        mentionable=bool(role_info.get("mentionable", False)),
                        reason="Coffeecord backup repair",
                    )
                    stats["roles_updated"] += 1
                    await asyncio.sleep(CREATE_RATE_SLEEP_S)
                except Exception as exc:
                    stats["skipped"].append(f"update role {name}: {exc}")
            continue
        try:
            created = await guild.create_role(
                name=name,
                permissions=discord.Permissions(int(role_info.get("permissions", 0))),
                colour=discord.Colour(int(role_info.get("color", 0))),
                hoist=bool(role_info.get("hoist", False)),
                mentionable=bool(role_info.get("mentionable", False)),
                reason="Coffeecord backup restore",
            )
            id_map[old_id] = created.id
            existing_by_name[name.lower()] = created
            stats["roles_created"] += 1
            await asyncio.sleep(CREATE_RATE_SLEEP_S)
        except Exception as exc:
            stats["skipped"].append(f"role {name}: {exc}")

    # Try to order roles (best-effort; bot must be above them)
    await _progress("Ordering roles…")
    try:
        if guild.me is not None:
            positions = {}
            for role_info in creatable_sorted:
                new_id = id_map.get(int(role_info["id"]))
                if not new_id:
                    continue
                role = guild.get_role(new_id)
                if role and role < guild.me.top_role:
                    positions[role] = int(role_info.get("position", 1))
            if positions:
                await guild.edit_role_positions(positions=positions, reason="Coffeecord backup restore")
    except Exception:
        stats["skipped"].append("role positions not fully applied")

    # Channels — categories first
    await _progress("Restoring channels…")
    channels = discord_data.get("channels") or []
    if not channels:
        stats["skipped"].append("backup has no channel entries")
    existing_channels = {c.name.lower(): c for c in await _list_guild_channels(guild)}

    def _resolve_overwrites(raw_list: list[dict[str, Any]]) -> dict[Any, discord.PermissionOverwrite]:
        overwrites: dict[Any, discord.PermissionOverwrite] = {}
        for entry in raw_list or []:
            kind = entry.get("kind")
            allow = discord.Permissions(int(entry.get("allow", 0)))
            deny = discord.Permissions(int(entry.get("deny", 0)))
            ow = discord.PermissionOverwrite.from_pair(allow, deny)
            if kind == "role":
                new_rid = id_map.get(int(entry["id"]))
                target = guild.get_role(new_rid) if new_rid else None
                if target is None and entry.get("name"):
                    target = discord.utils.get(guild.roles, name=entry["name"])
                if target is not None:
                    overwrites[target] = ow
            elif kind == "member":
                member = guild.get_member(int(entry["id"]))
                if member is not None:
                    overwrites[member] = ow
        return overwrites

    async def _apply_channel_overwrites(channel: discord.abc.GuildChannel, raw_list: list[dict[str, Any]]) -> None:
        overwrites = _resolve_overwrites(raw_list)
        for target, ow in overwrites.items():
            try:
                await channel.set_permissions(target, overwrite=ow, reason="Coffeecord backup repair")
            except Exception as exc:
                stats["skipped"].append(f"overwrite {channel.name}: {exc}")

    async def _create_guild_channel(ch_info: dict[str, Any]) -> discord.abc.GuildChannel | None:
        name = str(ch_info.get("name") or "channel")
        ctype = ch_info.get("type") or "text"
        category = None
        old_cat = ch_info.get("category_id")
        if old_cat:
            new_cat_id = id_map.get(int(old_cat))
            category = guild.get_channel(new_cat_id) if new_cat_id else None
            if category is not None and not isinstance(category, discord.CategoryChannel):
                category = None
        overwrites = _resolve_overwrites(ch_info.get("overwrites") or [])
        if ctype == "text":
            return await guild.create_text_channel(
                name=name,
                category=category,
                topic=ch_info.get("topic"),
                nsfw=bool(ch_info.get("nsfw", False)),
                slowmode_delay=int(ch_info.get("slowmode_delay") or 0),
                overwrites=overwrites,
                reason="Coffeecord backup restore",
            )
        if ctype == "news":
            return await guild.create_text_channel(
                name=name,
                category=category,
                topic=ch_info.get("topic"),
                nsfw=bool(ch_info.get("nsfw", False)),
                news=True,
                overwrites=overwrites,
                reason="Coffeecord backup restore",
            )
        if ctype == "voice":
            return await guild.create_voice_channel(
                name=name,
                category=category,
                bitrate=int(ch_info.get("bitrate") or 64000),
                user_limit=int(ch_info.get("user_limit") or 0),
                overwrites=overwrites,
                reason="Coffeecord backup restore",
            )
        if ctype == "stage":
            return await guild.create_stage_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                reason="Coffeecord backup restore",
            )
        if ctype == "forum":
            return await guild.create_forum(
                name=name,
                category=category,
                topic=ch_info.get("topic"),
                nsfw=bool(ch_info.get("nsfw", False)),
                overwrites=overwrites,
                reason="Coffeecord backup restore",
            )
        if ctype == "media":
            try:
                return await guild.create_forum(
                    name=name,
                    category=category,
                    topic=ch_info.get("topic"),
                    nsfw=bool(ch_info.get("nsfw", False)),
                    media=True,
                    overwrites=overwrites,
                    reason="Coffeecord backup restore",
                )
            except TypeError:
                # Older discord.py without media= support — fall back to forum.
                return await guild.create_forum(
                    name=name,
                    category=category,
                    topic=ch_info.get("topic"),
                    nsfw=bool(ch_info.get("nsfw", False)),
                    overwrites=overwrites,
                    reason="Coffeecord backup restore",
                )
        stats["skipped"].append(f"unsupported channel type {ctype}: {name}")
        return None

    # categories
    for ch_info in sorted(
        [c for c in channels if c.get("type") == "category"],
        key=lambda c: int(c.get("position", 0)),
    ):
        old_id = int(ch_info["id"])
        name = str(ch_info.get("name") or "category")
        existing = existing_channels.get(name.lower()) if reuse_existing else None
        if reuse_existing and isinstance(existing, discord.CategoryChannel):
            id_map[old_id] = existing.id
            try:
                await existing.edit(name=name, reason="Coffeecord backup repair")
                await _apply_channel_overwrites(existing, ch_info.get("overwrites") or [])
                stats["channels_updated"] += 1
                await asyncio.sleep(CREATE_RATE_SLEEP_S)
            except Exception as exc:
                stats["skipped"].append(f"update category {name}: {exc}")
            continue
        try:
            created = await guild.create_category(
                name=name,
                overwrites=_resolve_overwrites(ch_info.get("overwrites") or []),
                reason="Coffeecord backup restore",
            )
            id_map[old_id] = created.id
            existing_channels[name.lower()] = created
            stats["channels_created"] += 1
            await asyncio.sleep(CREATE_RATE_SLEEP_S)
        except Exception as exc:
            stats["skipped"].append(f"category {name}: {exc}")
            LOGGER.warning("category create failed %s: %s", name, exc)

    for ch_info in sorted(
        [c for c in channels if c.get("type") != "category"],
        key=lambda c: (c.get("category_id") or 0, int(c.get("position", 0))),
    ):
        old_id = int(ch_info["id"])
        name = str(ch_info.get("name") or "channel")
        ctype = ch_info.get("type") or "text"
        existing = None
        if reuse_existing:
            if old_id in id_map:
                existing = guild.get_channel(id_map[old_id])
            if existing is None:
                for ch in await _list_guild_channels(guild):
                    if ch.name.lower() == name.lower() and _channel_type_name(ch) == ctype:
                        existing = ch
                        break
        if existing is not None:
            id_map[old_id] = existing.id
            try:
                edit_kwargs: dict[str, Any] = {"reason": "Coffeecord backup repair"}
                if isinstance(existing, discord.TextChannel):
                    edit_kwargs.update(
                        topic=ch_info.get("topic"),
                        nsfw=bool(ch_info.get("nsfw", False)),
                        slowmode_delay=int(ch_info.get("slowmode_delay") or 0),
                    )
                elif isinstance(existing, (discord.VoiceChannel, discord.StageChannel)):
                    edit_kwargs.update(
                        bitrate=int(ch_info.get("bitrate") or getattr(existing, "bitrate", 64000)),
                        user_limit=int(ch_info.get("user_limit") or 0),
                    )
                elif isinstance(existing, discord.ForumChannel):
                    edit_kwargs.update(
                        topic=ch_info.get("topic"),
                        nsfw=bool(ch_info.get("nsfw", False)),
                    )
                old_cat = ch_info.get("category_id")
                if old_cat:
                    new_cat_id = id_map.get(int(old_cat))
                    cat = guild.get_channel(new_cat_id) if new_cat_id else None
                    if isinstance(cat, discord.CategoryChannel):
                        edit_kwargs["category"] = cat
                await existing.edit(**edit_kwargs)
                await _apply_channel_overwrites(existing, ch_info.get("overwrites") or [])
                stats["channels_updated"] += 1
                await asyncio.sleep(CREATE_RATE_SLEEP_S)
            except Exception as exc:
                stats["skipped"].append(f"update channel {name}: {exc}")
            continue

        try:
            created_ch = await _create_guild_channel(ch_info)
            if created_ch is not None:
                id_map[old_id] = created_ch.id
                existing_channels[name.lower()] = created_ch
                stats["channels_created"] += 1
                await asyncio.sleep(CREATE_RATE_SLEEP_S)
        except Exception as exc:
            stats["skipped"].append(f"channel {name} ({ctype}): {exc}")
            LOGGER.warning("channel create failed %s (%s): %s", name, ctype, exc)

    # Drop overwrite hold channel once at least one real channel exists.
    if hold_channel_id:
        hold = guild.get_channel(hold_channel_id)
        if hold is None:
            for ch in await _list_guild_channels(guild):
                if ch.id == hold_channel_id:
                    hold = ch
                    break
        if hold is not None and stats["channels_created"] + stats["channels_updated"] > 0:
            try:
                await hold.delete(reason="Coffeecord backup overwrite hold cleanup")
                await asyncio.sleep(CREATE_RATE_SLEEP_S)
            except Exception as exc:
                stats["skipped"].append(f"hold channel cleanup: {exc}")

    # Emojis
    await _progress("Restoring emoji…")
    existing_emoji_names = {e.name for e in guild.emojis}
    for emo in discord_data.get("emojis") or []:
        name = str(emo.get("name") or "emoji")
        if reuse_existing and name in existing_emoji_names:
            if emo.get("id"):
                match = discord.utils.get(guild.emojis, name=name)
                if match and emo.get("id"):
                    id_map[int(emo["id"])] = match.id
            continue
        b64 = emo.get("image_b64")
        if not b64:
            stats["skipped"].append(f"emoji {name}: no image data")
            continue
        try:
            image = base64.b64decode(str(b64).encode("ascii"))
            created_e = await guild.create_custom_emoji(name=name, image=image, reason="Coffeecord backup restore")
            if emo.get("id"):
                id_map[int(emo["id"])] = created_e.id
            existing_emoji_names.add(name)
            stats["emojis_created"] += 1
            await asyncio.sleep(CREATE_RATE_SLEEP_S)
        except Exception as exc:
            stats["skipped"].append(f"emoji {name}: {exc}")

    # Guild settings (best-effort)
    await _progress("Applying guild settings…")
    settings = discord_data.get("settings") or {}
    edit_kwargs: dict[str, Any] = {}
    if settings.get("name") and settings["name"] != guild.name:
        edit_kwargs["name"] = settings["name"]
    if "description" in settings:
        edit_kwargs["description"] = settings.get("description")
    try:
        if settings.get("verification_level") is not None:
            edit_kwargs["verification_level"] = discord.VerificationLevel(_enum_int(settings["verification_level"]))
    except (ValueError, KeyError):
        pass
    icon_b64 = settings.get("icon_b64")
    if icon_b64:
        try:
            edit_kwargs["icon"] = base64.b64decode(str(icon_b64).encode("ascii"))
        except ValueError:
            pass
    # Remap system/afk channels
    if settings.get("system_channel_id"):
        new_sys = id_map.get(int(settings["system_channel_id"]))
        if new_sys:
            edit_kwargs["system_channel"] = guild.get_channel(new_sys)
    if settings.get("afk_channel_id"):
        new_afk = id_map.get(int(settings["afk_channel_id"]))
        if new_afk:
            edit_kwargs["afk_channel"] = guild.get_channel(new_afk)
    if settings.get("afk_timeout") is not None:
        edit_kwargs["afk_timeout"] = int(settings["afk_timeout"])
    if edit_kwargs:
        try:
            await guild.edit(**edit_kwargs, reason="Coffeecord backup restore")
        except Exception as exc:
            stats["skipped"].append(f"guild settings: {exc}")

    # Members
    await _progress("Re-applying member roles and nicknames…")
    members = discord_data.get("members") or {}
    for uid_str, info in members.items():
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        member = guild.get_member(uid)
        if member is None:
            stats["members_missing"] += 1
            continue
        role_ids = info.get("role_ids") or []
        roles_to_add = []
        for rid in role_ids:
            new_rid = id_map.get(int(rid))
            role = guild.get_role(new_rid) if new_rid else None
            if role and guild.me is not None and role < guild.me.top_role and role not in member.roles:
                roles_to_add.append(role)
        try:
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Coffeecord backup restore")
            nick = info.get("nick")
            if nick and member.nick != nick and member != guild.owner:
                await member.edit(nick=nick, reason="Coffeecord backup restore")
            stats["members_updated"] += 1
            await asyncio.sleep(0.15)
        except Exception as exc:
            stats["skipped"].append(f"member {uid}: {exc}")

    return id_map, stats


# ---------------------------------------------------------------------------
# UI / Cog
# ---------------------------------------------------------------------------


class BackupProgressUI:
    """Uninstall-style checklist: spinner on the active step, ✓ when complete."""

    def __init__(
        self,
        message: discord.Message,
        *,
        title: str,
        color: discord.Color | None = None,
    ) -> None:
        self.message = message
        self.title = title
        self.color = color or discord.Color.yellow()
        self.steps: list[dict[str, str]] = []
        self.detail = ""
        self.wheel = 0
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_edit = 0.0
        self._lock = asyncio.Lock()

    def configure(self, steps: list[tuple[str, str]]) -> None:
        """steps: list of (step_id, label)."""
        self.steps = [{"id": sid, "label": label, "status": "pending"} for sid, label in steps]

    async def start(self) -> None:
        self._stop.clear()
        await self.render(force=True)
        self._task = asyncio.create_task(self._spin_loop(), name="backup-progress-spinner")

    async def stop_spinner(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _spin_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self.wheel += 1
                await self.render(force=True)
                await asyncio.sleep(PROGRESS_SPINNER_INTERVAL_S)
        except asyncio.CancelledError:
            return

    def _percent(self) -> int:
        if not self.steps:
            return 0
        done = sum(1 for s in self.steps if s["status"] in {"done", "skipped"})
        active = any(s["status"] == "active" for s in self.steps)
        raw = (done + (0.45 if active else 0.0)) / len(self.steps)
        return min(99 if active else 100, max(0, int(raw * 100)))

    def _body(self) -> str:
        percent = self._percent()
        filled = percent // 5
        bar = "█" * filled + "░" * (20 - filled)
        spinner = SPINNER_FRAMES[self.wheel % len(SPINNER_FRAMES)]
        lines = [f"[{bar}] {percent:>3}%  {spinner}", ""]
        for step in self.steps:
            status = step["status"]
            if status == "done":
                mark = PROGRESS_MARK_DONE
            elif status == "skipped":
                mark = PROGRESS_MARK_SKIPPED
            elif status == "active":
                mark = SPINNER_FRAMES[self.wheel % len(SPINNER_FRAMES)]
            else:
                mark = PROGRESS_MARK_PENDING
            lines.append(f"{mark} {step['label']}")
        if self.detail:
            lines.append("")
            lines.append(self.detail[:240])
        return "```\n" + "\n".join(lines) + "\n```"

    async def render(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_edit) < PROGRESS_EDIT_THROTTLE_S:
            return
        async with self._lock:
            if not force and (time.monotonic() - self._last_edit) < PROGRESS_EDIT_THROTTLE_S:
                return
            embed = discord.Embed(title=self.title, description=self._body(), color=self.color)
            try:
                await self.message.edit(content=None, embed=embed)
                self._last_edit = time.monotonic()
            except discord.HTTPException:
                pass

    async def begin(self, step_id: str, detail: str = "") -> None:
        for step in self.steps:
            if step["status"] == "active":
                step["status"] = "done"
            if step["id"] == step_id:
                step["status"] = "active"
        self.detail = detail
        await self.render(force=True)

    async def complete(self, step_id: str | None = None, detail: str = "") -> None:
        if step_id is None:
            for step in self.steps:
                if step["status"] == "active":
                    step["status"] = "done"
                    break
        else:
            for step in self.steps:
                if step["id"] == step_id:
                    step["status"] = "done"
                    break
        if detail:
            self.detail = detail
        await self.render(force=True)

    async def skip(self, step_id: str) -> None:
        for step in self.steps:
            if step["id"] == step_id:
                step["status"] = "skipped"
                break
        await self.render(force=True)

    async def set_detail(self, detail: str) -> None:
        self.detail = (detail or "")[:240]
        await self.render()

    async def fail(self, message: str) -> None:
        await self.stop_spinner()
        self.color = discord.Color.red()
        self.detail = message
        for step in self.steps:
            if step["status"] == "active":
                step["status"] = "pending"
        embed = discord.Embed(title=self.title, description=self._body(), color=self.color)
        try:
            await self.message.edit(content=None, embed=embed)
        except discord.HTTPException:
            pass

    async def finish_with_embed(self, embed: discord.Embed) -> None:
        await self.stop_spinner()
        try:
            await self.message.edit(content=None, embed=embed)
        except discord.HTTPException:
            pass


class RestoreConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int, *, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.invoker_id = invoker_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                _text(interaction.user.id, "owner_only", default="Only the command invoker can use this."),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm restore", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        self.stop()


class ServerBackupCog(
    commands.GroupCog,
    group_name="backup",
    group_description="Encrypted server structure + Coffeecord config backups.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _module_ok(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                t_sync(interaction.user.id, "common.guild_only"),
                ephemeral=True,
            )
            return False
        if not await is_module_enabled(interaction.guild.id, MODULE_ID):
            await interaction.response.send_message(
                t_sync(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(name="create", description="Create an encrypted server backup.")
    @app_commands.describe(
        name="Slot name (saved on host; letters, digits, _-)",
        overwrite="Overwrite an existing host slot with the same name",
        include_messages="Store recent text-channel messages (replayed by the bot on restore)",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def backup_create(
        self,
        interaction: discord.Interaction,
        name: str,
        overwrite: bool = False,
        include_messages: bool = True,
    ) -> None:
        if not await self._module_ok(interaction):
            return
        assert interaction.guild is not None
        slot_id = normalize_slot_id(name)
        if slot_id is None:
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "invalid_slot_id",
                    default="Invalid slot name. Use 1–32 characters: letters, digits, `_`, `-`.",
                ),
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if me is None or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "bot_missing_perms",
                    default="I need **Manage Channels** and **Manage Roles** to create useful backups.",
                ),
                ephemeral=True,
            )
            return

        limit = max_backup_slots_for_user(interaction.user.id)
        async with _INDEX_LOCK:
            index = load_slot_index(interaction.guild.id)
            slots: dict[str, Any] = index.setdefault("slots", {})
            if slot_id not in slots and len(slots) >= limit and not overwrite:
                hint = ""
                if limit < BACKUP_SLOTS_SUPPORTER:
                    hint = _text(
                        interaction.user.id,
                        "slot_limit_supporter_hint",
                        default="\nKo-fi supporters get up to {max} host slots (`/kofi link`).",
                        max=str(BACKUP_SLOTS_SUPPORTER),
                    )
                await interaction.response.send_message(
                    _text(
                        interaction.user.id,
                        "slot_limit_reached",
                        default="Host backup slots full ({used}/{limit}). Delete one or set `overwrite:True`.{hint}",
                        used=str(len(slots)),
                        limit=str(limit),
                        hint=hint,
                    ),
                    ephemeral=True,
                )
                return
            if slot_id in slots and not overwrite:
                await interaction.response.send_message(
                    _text(
                        interaction.user.id,
                        "slot_exists",
                        default="Slot `{slot}` already exists. Re-run with `overwrite:True` to replace it.",
                        slot=slot_id,
                    ),
                    ephemeral=True,
                )
                return
            if slot_id not in slots and len(slots) >= limit and overwrite:
                # overwrite requires existing name when at capacity
                await interaction.response.send_message(
                    _text(
                        interaction.user.id,
                        "slot_limit_overwrite_existing",
                        default="Slots are full. Overwrite an existing slot name, or delete one first.",
                    ),
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        progress_msg = await interaction.followup.send(
            embed=discord.Embed(
                title=_text(interaction.user.id, "create_progress_title", default="Creating backup…"),
                description="```\nStarting…\n```",
                color=discord.Color.yellow(),
            ),
            ephemeral=True,
            wait=True,
        )
        progress = BackupProgressUI(
            progress_msg,
            title=_text(interaction.user.id, "create_progress_title", default="Creating backup…"),
        )
        progress.configure(
            [
                (
                    "build",
                    _text(
                        interaction.user.id,
                        "step_build",
                        default="Snapshot Discord + Coffeecord Storage",
                    ),
                ),
                ("encrypt", _text(interaction.user.id, "step_encrypt", default="Compress & encrypt archive")),
                ("save", _text(interaction.user.id, "step_save_slot", default="Save host slot")),
            ]
        )
        await progress.start()

        phrase = generate_passphrase()
        try:
            detail = _text(
                interaction.user.id,
                "step_build_detail_msgs",
                default="Reading structure and up to {n} messages per channel…",
                n=str(MESSAGE_HISTORY_LIMIT_PER_CHANNEL),
            ) if include_messages else _text(
                interaction.user.id,
                "step_build_detail",
                default="Reading roles, channels, members, and Storage…",
            )
            await progress.begin("build", detail)
            payload = await build_backup_payload(
                interaction.guild,
                creator_id=interaction.user.id,
                slot_name=slot_id,
                include_messages=include_messages,
            )
            await progress.complete("build")
            await progress.begin(
                "encrypt",
                _text(interaction.user.id, "step_encrypt_detail", default="LZMA compress + encrypt…"),
            )
            blob = await asyncio.to_thread(pack_backup, payload, phrase)
            await progress.complete("encrypt")
        except Exception:
            LOGGER.exception("backup create failed")
            await progress.fail(
                _text(interaction.user.id, "create_failed", default="Failed to create backup.")
            )
            return

        await progress.begin("save", _text(interaction.user.id, "step_save_detail", default="Writing host slot…"))
        async with _INDEX_LOCK:
            index = load_slot_index(interaction.guild.id)
            slots = index.setdefault("slots", {})
            if slot_id not in slots and len(slots) >= limit:
                await progress.complete(
                    "save",
                    _text(
                        interaction.user.id,
                        "slot_limit_race",
                        default="Host slots filled while creating. Download below was still generated.",
                    ),
                )
            else:
                path = _slot_blob_path(interaction.guild.id, slot_id)
                path.write_bytes(blob)
                slots[slot_id] = {
                    "name": slot_id,
                    "created_at": int(time.time()),
                    "size": len(blob),
                    "schema_version": SCHEMA_VERSION,
                    "source_guild_id": interaction.guild.id,
                    "source_guild_name": interaction.guild.name,
                    "creator_id": interaction.user.id,
                }
                save_slot_index(interaction.guild.id, index)
                await progress.complete("save")

        done_embed = discord.Embed(
            title=_text(interaction.user.id, "create_progress_done_title", default="Backup ready"),
            description=_text(
                interaction.user.id,
                "create_phrase",
                default=(
                    "✅ Backup `{slot}` ready.\n\n"
                    "**Save this decrypt phrase now — it is shown once and never stored:**\n"
                    "`{phrase}`\n\n"
                    "Use it with `/backup restore` together with the `.ccbak` file."
                ),
                slot=slot_id,
                phrase=phrase,
            ),
            color=discord.Color.green(),
        )
        await progress.finish_with_embed(done_embed)

        filename = f"{interaction.guild.id}_{slot_id}_{int(time.time())}.ccbak"
        fp = io.BytesIO(blob)
        await interaction.followup.send(
            _text(
                interaction.user.id,
                "create_file",
                default="Encrypted backup file for `{guild}` (slot `{slot}`). Keep it with your phrase.",
                guild=interaction.guild.name,
                slot=slot_id,
            ),
            file=discord.File(fp, filename=filename),
        )

    @app_commands.command(name="list", description="List host-stored encrypted backups for this server.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def backup_list(self, interaction: discord.Interaction) -> None:
        if not await self._module_ok(interaction):
            return
        assert interaction.guild is not None
        limit = max_backup_slots_for_user(interaction.user.id)
        index = load_slot_index(interaction.guild.id)
        slots = index.get("slots") or {}
        if not slots:
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "list_empty",
                    default="No host backups yet. Create one with `/backup create`. Limit: {limit}.",
                    limit=str(limit),
                ),
                ephemeral=True,
            )
            return
        lines = []
        for slot_id, meta in sorted(slots.items()):
            if not isinstance(meta, dict):
                continue
            ts = meta.get("created_at")
            when = f"<t:{int(ts)}:f>" if ts else "?"
            size = meta.get("size", 0)
            lines.append(f"• `{slot_id}` — {when} — {size} bytes")
        await interaction.response.send_message(
            _text(
                interaction.user.id,
                "list_header",
                default="**Host backups** ({used}/{limit}):\n{lines}",
                used=str(len(slots)),
                limit=str(limit),
                lines="\n".join(lines),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="download", description="Re-download a host-stored encrypted backup.")
    @app_commands.describe(slot="Slot name")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def backup_download(self, interaction: discord.Interaction, slot: str) -> None:
        if not await self._module_ok(interaction):
            return
        assert interaction.guild is not None
        slot_id = normalize_slot_id(slot)
        if slot_id is None:
            await interaction.response.send_message(
                _text(interaction.user.id, "invalid_slot_id", default="Invalid slot name."),
                ephemeral=True,
            )
            return
        path = _slot_blob_path(interaction.guild.id, slot_id)
        index = load_slot_index(interaction.guild.id)
        if slot_id not in (index.get("slots") or {}) or not path.is_file():
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "slot_not_found",
                    default="No host backup named `{slot}`.",
                    slot=slot_id or slot,
                ),
                ephemeral=True,
            )
            return
        data = path.read_bytes()
        await interaction.response.send_message(
            _text(
                interaction.user.id,
                "download_ready",
                default="Encrypted backup `{slot}` (you still need your 18-character phrase to restore).",
                slot=slot_id,
            ),
            file=discord.File(io.BytesIO(data), filename=f"{interaction.guild.id}_{slot_id}.ccbak"),
        )

    @app_commands.command(name="delete", description="Delete a host-stored encrypted backup slot.")
    @app_commands.describe(slot="Slot name")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def backup_delete(self, interaction: discord.Interaction, slot: str) -> None:
        if not await self._module_ok(interaction):
            return
        assert interaction.guild is not None
        slot_id = normalize_slot_id(slot)
        if slot_id is None:
            await interaction.response.send_message(
                _text(interaction.user.id, "invalid_slot_id", default="Invalid slot name."),
                ephemeral=True,
            )
            return
        async with _INDEX_LOCK:
            index = load_slot_index(interaction.guild.id)
            slots = index.setdefault("slots", {})
            if slot_id not in slots:
                await interaction.response.send_message(
                    _text(
                        interaction.user.id,
                        "slot_not_found",
                        default="No host backup named `{slot}`.",
                        slot=slot_id,
                    ),
                    ephemeral=True,
                )
                return
            slots.pop(slot_id, None)
            save_slot_index(interaction.guild.id, index)
            path = _slot_blob_path(interaction.guild.id, slot_id)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        await interaction.response.send_message(
            _text(interaction.user.id, "delete_success", default="Deleted host backup `{slot}`.", slot=slot_id),
            ephemeral=True,
        )

    @app_commands.command(name="restore", description="Restore a server from an encrypted backup.")
    @app_commands.describe(
        phrase="Your 18-character decrypt phrase",
        mode="repair = fix/create missing; overwrite = delete structure then rebuild",
        file="Uploaded .ccbak file (optional if using a host slot)",
        slot="Host slot name (optional if uploading a file)",
        target_guild_id="Restore into another guild id the bot is in (default: this server)",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Repair (merge / update)", value=RESTORE_MODE_REPAIR),
            app_commands.Choice(name="Overwrite (nuke and rebuild)", value=RESTORE_MODE_OVERWRITE),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def backup_restore(
        self,
        interaction: discord.Interaction,
        phrase: str,
        mode: app_commands.Choice[str],
        file: discord.Attachment | None = None,
        slot: str | None = None,
        target_guild_id: str | None = None,
    ) -> None:
        if not await self._module_ok(interaction):
            return
        assert interaction.guild is not None

        restore_mode = mode.value if mode.value in {RESTORE_MODE_REPAIR, RESTORE_MODE_OVERWRITE} else RESTORE_MODE_REPAIR

        phrase = phrase.strip()
        if len(phrase) != PHRASE_LEN:
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "phrase_bad_length",
                    default="Decrypt phrase must be exactly {n} characters.",
                    n=str(PHRASE_LEN),
                ),
                ephemeral=True,
            )
            return

        if file is None and not slot:
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "restore_need_source",
                    default="Provide an uploaded `.ccbak` file and/or a host `slot` name.",
                ),
                ephemeral=True,
            )
            return

        target = interaction.guild
        if target_guild_id:
            try:
                tid = int(target_guild_id.strip())
            except ValueError:
                await interaction.response.send_message(
                    _text(interaction.user.id, "invalid_target_guild", default="Invalid target guild id."),
                    ephemeral=True,
                )
                return
            found = self.bot.get_guild(tid)
            if found is None:
                await interaction.response.send_message(
                    _text(
                        interaction.user.id,
                        "target_guild_missing",
                        default="I am not in that guild (or cannot see it).",
                    ),
                    ephemeral=True,
                )
                return
            member = found.get_member(interaction.user.id)
            if member is None or not member.guild_permissions.manage_guild:
                await interaction.response.send_message(
                    _text(
                        interaction.user.id,
                        "target_guild_no_perms",
                        default="You need **Manage Server** in the target guild.",
                    ),
                    ephemeral=True,
                )
                return
            target = found

        me = target.me
        if me is None or not (
            me.guild_permissions.manage_channels
            and me.guild_permissions.manage_roles
            and me.guild_permissions.manage_guild
        ):
            await interaction.response.send_message(
                _text(
                    interaction.user.id,
                    "bot_missing_restore_perms",
                    default="I need **Manage Server**, **Manage Channels**, and **Manage Roles** in the target guild.",
                ),
                ephemeral=True,
            )
            return

        if restore_mode == RESTORE_MODE_OVERWRITE:
            confirm_text = _text(
                interaction.user.id,
                "restore_confirm_overwrite",
                default=(
                    "⚠️ **OVERWRITE** restore into **{guild}** (`{gid}`)?\n"
                    "This **deletes** channels/roles/emoji the bot can manage, then rebuilds from the backup. "
                    "Put my role near the top (Administrator recommended). Stored messages are replayed after rebuild. "
                    "This cannot be undone."
                ),
                guild=target.name,
                gid=str(target.id),
            )
        else:
            confirm_text = _text(
                interaction.user.id,
                "restore_confirm_repair",
                default=(
                    "**Repair** restore into **{guild}** (`{gid}`)?\n"
                    "Missing roles/channels are created; matching ones are updated. "
                    "Existing ticket/verify panels are kept. Message history is not replayed."
                ),
                guild=target.name,
                gid=str(target.id),
            )

        view = RestoreConfirmView(interaction.user.id)
        await interaction.response.send_message(
            confirm_text,
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            await interaction.followup.send(
                t_sync(interaction.user.id, "common.cancelled"),
                ephemeral=True,
            )
            return

        status = await interaction.followup.send(
            embed=discord.Embed(
                title=_text(interaction.user.id, "restore_progress_title", default="Restoring backup…"),
                description="```\nStarting…\n```",
                color=discord.Color.yellow(),
            ),
            ephemeral=True,
            wait=True,
        )
        progress = BackupProgressUI(
            status,
            title=_text(interaction.user.id, "restore_progress_title", default="Restoring backup…"),
        )
        restore_steps: list[tuple[str, str]] = [
            ("decrypt", _text(interaction.user.id, "step_decrypt", default="Decrypt backup")),
            ("wipe", _text(interaction.user.id, "step_wipe", default="Wipe existing structure")),
            ("structure", _text(interaction.user.id, "step_structure", default="Restore roles & channels")),
            ("messages", _text(interaction.user.id, "step_replay_messages", default="Replay channel messages")),
            ("storage", _text(interaction.user.id, "step_restore_storage", default="Restore Coffeecord Storage")),
            ("panels", _text(interaction.user.id, "step_panels", default="Republish interactive panels")),
        ]
        progress.configure(restore_steps)
        await progress.start()
        if restore_mode != RESTORE_MODE_OVERWRITE:
            await progress.skip("wipe")

        blob: bytes | None = None
        try:
            await progress.begin(
                "decrypt",
                _text(interaction.user.id, "step_decrypt_detail", default="Reading and decrypting `.ccbak`…"),
            )
            if file is not None:
                if not (file.filename or "").endswith(".ccbak"):
                    await progress.fail(
                        _text(
                            interaction.user.id,
                            "bad_extension",
                            default="Please upload a `.ccbak` backup file.",
                        )
                    )
                    return
                blob = await file.read()
            elif slot:
                slot_id = normalize_slot_id(slot)
                if slot_id is None:
                    await progress.fail(
                        _text(interaction.user.id, "invalid_slot_id", default="Invalid slot name.")
                    )
                    return
                path = _slot_blob_path(interaction.guild.id, slot_id)
                index = load_slot_index(interaction.guild.id)
                if slot_id not in (index.get("slots") or {}) or not path.is_file():
                    await progress.fail(
                        _text(
                            interaction.user.id,
                            "slot_not_found",
                            default="No host backup named `{slot}`.",
                            slot=slot_id,
                        )
                    )
                    return
                blob = path.read_bytes()
        except Exception:
            LOGGER.exception("failed reading backup source")
            await progress.fail(
                _text(interaction.user.id, "restore_read_failed", default="Could not read backup source.")
            )
            return

        assert blob is not None
        try:
            payload = await asyncio.to_thread(unpack_backup, blob, phrase)
        except BackupCryptoError:
            await progress.fail(
                _text(
                    interaction.user.id,
                    "decrypt_failed",
                    default="Could not decrypt backup. Check the phrase and file.",
                )
            )
            return

        discord_data = payload.get("discord") or {}
        coffeecord = payload.get("coffeecord") or {}
        if not isinstance(discord_data, dict):
            await progress.fail(
                _text(interaction.user.id, "payload_invalid", default="Backup payload is invalid.")
            )
            return
        await progress.complete("decrypt")

        async def progress_cb(msg: str) -> None:
            await progress.set_detail(msg)

        wipe_stats: dict[str, Any] = {}
        storage_notes: list[str] = []
        try:
            if restore_mode == RESTORE_MODE_OVERWRITE:
                await progress.begin(
                    "wipe",
                    _text(interaction.user.id, "step_wipe_detail", default="Deleting channels, categories, roles…"),
                )
                wipe_stats = await wipe_guild_structure(target, progress_cb=progress_cb)
                await progress.complete("wipe")
            await progress.begin(
                "structure",
                _text(interaction.user.id, "step_structure_detail", default="Creating roles and channels…"),
            )
            id_map, stats = await restore_discord_structure(
                target,
                discord_data,
                mode=restore_mode,
                hold_channel_id=wipe_stats.get("hold_channel_id"),
                progress_cb=progress_cb,
            )
            await progress.complete("structure")
            if wipe_stats.get("skipped"):
                stats.setdefault("skipped", []).extend(wipe_stats["skipped"])
            stats["channels_deleted"] = wipe_stats.get("channels_deleted", 0)
            stats["roles_deleted"] = wipe_stats.get("roles_deleted", 0)
            stats["emojis_deleted"] = wipe_stats.get("emojis_deleted", 0)
            if restore_mode == RESTORE_MODE_OVERWRITE:
                await progress.begin(
                    "messages",
                    _text(interaction.user.id, "step_replay_detail", default="Replaying stored messages…"),
                )
                msg_stats = await restore_channel_messages(
                    target,
                    discord_data,
                    id_map,
                    progress_cb=progress_cb,
                )
                stats["messages_restored"] = msg_stats.get("messages_restored", 0)
                stats["embeds_restored"] = msg_stats.get("embeds_restored", 0)
                if msg_stats.get("skipped"):
                    stats.setdefault("skipped", []).extend(msg_stats["skipped"])
                await progress.complete(
                    "messages",
                    _text(
                        interaction.user.id,
                        "step_replay_done",
                        default="Replayed {n} messages.",
                        n=str(stats.get("messages_restored", 0)),
                    ),
                )
            else:
                await progress.skip("messages")
                stats["messages_restored"] = 0
                stats["embeds_restored"] = 0
            await progress.begin(
                "storage",
                _text(interaction.user.id, "restore_storage", default="Restoring Coffeecord Storage…"),
            )
            storage_notes = await asyncio.to_thread(
                apply_guild_storage_slice,
                target.id,
                coffeecord if isinstance(coffeecord, dict) else {},
                id_map=id_map,
            )
            await progress.complete("storage")
            await progress.begin(
                "panels",
                _text(
                    interaction.user.id,
                    "step_panels_detail_repair" if restore_mode != RESTORE_MODE_OVERWRITE else "step_panels_detail",
                    default=(
                        "Checking ticket/verify panels (keep existing)…"
                        if restore_mode != RESTORE_MODE_OVERWRITE
                        else "Re-posting tickets, verify, reaction panels…"
                    ),
                ),
            )
            panel_notes = await republish_interactive_panels(
                self.bot,
                target,
                force=(restore_mode == RESTORE_MODE_OVERWRITE),
                progress_cb=progress_cb,
            )
            if panel_notes:
                storage_notes = list(storage_notes or []) + list(panel_notes)
            await progress.complete("panels")
        except Exception:
            LOGGER.exception("restore failed")
            await progress.fail(
                _text(
                    interaction.user.id,
                    "restore_failed",
                    default="Restore failed partway. Check my role hierarchy and permissions.",
                )
            )
            return

        invite_notes = (discord_data.get("notes") or {}).get("invite_rejoin") or []
        skipped = stats.get("skipped") or []
        skipped_preview = "\n".join(f"- {s}" for s in skipped[:15])
        if len(skipped) > 15:
            skipped_preview += f"\n- …and {len(skipped) - 15} more"

        mode_label = (
            _text(interaction.user.id, "mode_overwrite", default="Overwrite")
            if restore_mode == RESTORE_MODE_OVERWRITE
            else _text(interaction.user.id, "mode_repair", default="Repair")
        )
        embed = discord.Embed(
            title=_text(interaction.user.id, "restore_done_title", default="Backup restore complete"),
            description=_text(
                interaction.user.id,
                "restore_done_body",
                default="Restored into **{guild}** ({mode}).",
                guild=target.name,
                mode=mode_label,
            ),
            color=discord.Color.green(),
        )
        created_lines = (
            f"Roles: {stats.get('roles_created', 0)}"
            f" (updated {stats.get('roles_updated', 0)})\n"
            f"Channels: {stats.get('channels_created', 0)}"
            f" (updated {stats.get('channels_updated', 0)})\n"
            f"Emoji: {stats.get('emojis_created', 0)}\n"
            f"Messages replayed: {stats.get('messages_restored', 0)}"
            f" (embeds {stats.get('embeds_restored', 0)})"
        )
        if restore_mode == RESTORE_MODE_OVERWRITE:
            created_lines += (
                f"\nDeleted: ch {stats.get('channels_deleted', 0)}, "
                f"roles {stats.get('roles_deleted', 0)}, "
                f"emoji {stats.get('emojis_deleted', 0)}"
            )
        embed.add_field(
            name=_text(interaction.user.id, "field_created", default="Created"),
            value=created_lines,
            inline=True,
        )
        embed.add_field(
            name=_text(interaction.user.id, "field_members", default="Members"),
            value=(
                f"Updated: {stats.get('members_updated', 0)}\n"
                f"Missing (need rejoin): {stats.get('members_missing', 0)}"
            ),
            inline=True,
        )
        if invite_notes:
            embed.add_field(
                name=_text(interaction.user.id, "field_rejoin", default="Rejoin notes"),
                value="\n".join(str(x) for x in invite_notes[:5])[:1024],
                inline=False,
            )
        if skipped_preview:
            embed.add_field(
                name=_text(interaction.user.id, "field_skipped", default="Skipped / warnings"),
                value=skipped_preview[:1024],
                inline=False,
            )
        if storage_notes:
            embed.add_field(
                name=_text(interaction.user.id, "field_storage_notes", default="Storage notes"),
                value="\n".join(storage_notes[:10])[:1024],
                inline=False,
            )

        await progress.finish_with_embed(embed)


async def setup(bot: commands.Bot) -> None:
    BACKUPS_GUILD_DIR.mkdir(parents=True, exist_ok=True)
    await bot.add_cog(ServerBackupCog(bot))
