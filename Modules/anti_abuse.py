from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import discord
from discord.ext import commands
from Modules.i18n import t
from Modules.dev_broadcast import register_dev_broadcast_commands

# -----------------------------
# Configuration
# -----------------------------
USER_RATE_LIMIT_COUNT = 5
USER_RATE_LIMIT_WINDOW_SECONDS = 5

USER_BLOCK_THRESHOLD_COUNT = 100
USER_BLOCK_THRESHOLD_WINDOW_SECONDS = 60
USER_TEMP_BLOCK_SECONDS = 10 * 60

GUILD_BLOCK_THRESHOLD_COUNT = 500
GUILD_BLOCK_THRESHOLD_WINDOW_SECONDS = 60
GUILD_TEMP_BLOCK_SECONDS = 5 * 60

MAX_CONCURRENT_TASKS = 10

USER_RATE_LIMIT_MESSAGE_KEY = "anti_abuse.rate_limit"
USER_BLOCK_MESSAGE_KEY = "anti_abuse.user_block"
GUILD_BLOCK_MESSAGE_KEY = "anti_abuse.guild_block"
USER_RATE_LIMIT_MESSAGE_DEFAULT = "You are sending commands too quickly. Please slow down."
USER_BLOCK_MESSAGE_DEFAULT = "You are temporarily blocked from using this bot due to excessive command usage."
GUILD_BLOCK_MESSAGE_DEFAULT = "This server has temporarily exceeded Coffeecord command limits. Please wait a few minutes."
BLACKLIST_REJECT_MESSAGE = "Error 1234"

_MENTION_RE = re.compile(r"^<@!?(\d+)>$")

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
BANNED_USERS_FILE = DATA_DIR / "banned_users.json"
BANNED_GUILDS_FILE = DATA_DIR / "banned_guilds.json"


# -----------------------------
# Runtime state
# -----------------------------
user_command_usage: dict[int, deque[float]] = {}
user_short_window_usage: dict[int, deque[float]] = {}
guild_command_usage: dict[int, deque[float]] = {}

blocked_users: dict[int, float] = {}
blocked_guilds: dict[int, float] = {}

_user_rate_notice_until: dict[int, float] = {}
_user_block_notice_until: dict[int, float] = {}
_guild_block_notice_until: dict[int, float] = {}

_last_cleanup_monotonic = 0.0

heavy_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


def _load_id_set(path: Path, key: str) -> set[int]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        raw_ids = payload.get(key, [])
        if not isinstance(raw_ids, list):
            return set()
        out: set[int] = set()
        for value in raw_ids:
            try:
                out.add(int(value))
            except (TypeError, ValueError):
                continue
        return out
    except (OSError, json.JSONDecodeError):
        return set()


def _save_id_set(path: Path, key: str, values: set[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: sorted(values)}
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, ensure_ascii=True)


banned_users: set[int] = _load_id_set(BANNED_USERS_FILE, "banned_users")
banned_guilds: set[int] = _load_id_set(BANNED_GUILDS_FILE, "banned_guilds")


def ban_user(user_id: int) -> None:
    banned_users.add(int(user_id))
    _save_id_set(BANNED_USERS_FILE, "banned_users", banned_users)


def unban_user(user_id: int) -> None:
    banned_users.discard(int(user_id))
    _save_id_set(BANNED_USERS_FILE, "banned_users", banned_users)


def ban_guild(guild_id: int) -> None:
    banned_guilds.add(int(guild_id))
    _save_id_set(BANNED_GUILDS_FILE, "banned_guilds", banned_guilds)


def unban_guild(guild_id: int) -> None:
    banned_guilds.discard(int(guild_id))
    _save_id_set(BANNED_GUILDS_FILE, "banned_guilds", banned_guilds)


async def resolve_user_identifier(bot: commands.Bot, identifier: str) -> int | None:
    """Resolve a Discord user id from numeric id, mention, or username.

    Usernames are matched against members in guilds this bot shares with the user
    (display name, global name, or legacy username#discriminator). Bans are always
    stored by numeric user id.
    """
    raw = identifier.strip()
    if not raw:
        return None

    mention = _MENTION_RE.match(raw)
    if mention:
        return int(mention.group(1))

    if raw.isdigit():
        return int(raw)

    lookup = raw.lstrip("@")
    for guild in bot.guilds:
        member = guild.get_member_named(lookup)
        if member is not None:
            return member.id

    return None


def _prune_window(bucket: deque[float], window_seconds: float, now_monotonic: float) -> None:
    cutoff = now_monotonic - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _cleanup_expired(now_monotonic: float) -> None:
    global _last_cleanup_monotonic
    if (now_monotonic - _last_cleanup_monotonic) < 15:
        return
    _last_cleanup_monotonic = now_monotonic

    for user_id, bucket in list(user_command_usage.items()):
        _prune_window(bucket, USER_BLOCK_THRESHOLD_WINDOW_SECONDS, now_monotonic)
        if not bucket:
            user_command_usage.pop(user_id, None)

    for user_id, bucket in list(user_short_window_usage.items()):
        _prune_window(bucket, USER_RATE_LIMIT_WINDOW_SECONDS, now_monotonic)
        if not bucket:
            user_short_window_usage.pop(user_id, None)

    for guild_id, bucket in list(guild_command_usage.items()):
        _prune_window(bucket, GUILD_BLOCK_THRESHOLD_WINDOW_SECONDS, now_monotonic)
        if not bucket:
            guild_command_usage.pop(guild_id, None)

    for user_id, unblock_at in list(blocked_users.items()):
        if unblock_at <= now_monotonic:
            blocked_users.pop(user_id, None)

    for guild_id, unblock_at in list(blocked_guilds.items()):
        if unblock_at <= now_monotonic:
            blocked_guilds.pop(guild_id, None)

    for table in (_user_rate_notice_until, _user_block_notice_until, _guild_block_notice_until):
        for key, expires_at in list(table.items()):
            if expires_at <= now_monotonic:
                table.pop(key, None)


async def _safe_reply(interaction: discord.Interaction, message: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _guard_message(user_id: int, key: str, default: str) -> str:
    return await t(user_id, key, default=default)


async def should_allow_interaction(interaction: discord.Interaction) -> bool:
    """
    Global pre-command guard.
    Returns False to block command execution.
    """
    if interaction.type is not discord.InteractionType.application_command:
        return True
    # During CommandTree.interaction_check, interaction.command may not be filled yet; still rate-limit.
    if interaction.user.bot:
        return False

    now_monotonic = time.monotonic()
    _cleanup_expired(now_monotonic)

    user_id = int(interaction.user.id)
    guild_id = int(interaction.guild.id) if interaction.guild is not None else None

    # Permanent blacklist: generic rejection (no ban details leaked).
    if user_id in banned_users:
        await _safe_reply(interaction, BLACKLIST_REJECT_MESSAGE)
        return False
    if guild_id is not None and guild_id in banned_guilds:
        await _safe_reply(interaction, BLACKLIST_REJECT_MESSAGE)
        return False

    # Temporary user block.
    user_unblock_at = blocked_users.get(user_id)
    if user_unblock_at is not None and user_unblock_at > now_monotonic:
        notice_until = _user_block_notice_until.get(user_id, 0.0)
        if now_monotonic >= notice_until:
            _user_block_notice_until[user_id] = user_unblock_at
            await _safe_reply(
                interaction,
                await _guard_message(user_id, USER_BLOCK_MESSAGE_KEY, USER_BLOCK_MESSAGE_DEFAULT),
            )
        return False

    # Temporary guild block.
    if guild_id is not None:
        guild_unblock_at = blocked_guilds.get(guild_id)
        if guild_unblock_at is not None and guild_unblock_at > now_monotonic:
            notice_until = _guild_block_notice_until.get(guild_id, 0.0)
            if now_monotonic >= notice_until:
                _guild_block_notice_until[guild_id] = min(guild_unblock_at, now_monotonic + 15)
                await _safe_reply(
                    interaction,
                    await _guard_message(user_id, GUILD_BLOCK_MESSAGE_KEY, GUILD_BLOCK_MESSAGE_DEFAULT),
                )
            return False

    # Track usage.
    long_bucket = user_command_usage.setdefault(user_id, deque())
    _prune_window(long_bucket, USER_BLOCK_THRESHOLD_WINDOW_SECONDS, now_monotonic)
    long_bucket.append(now_monotonic)

    short_bucket = user_short_window_usage.setdefault(user_id, deque())
    _prune_window(short_bucket, USER_RATE_LIMIT_WINDOW_SECONDS, now_monotonic)
    short_bucket.append(now_monotonic)

    if guild_id is not None:
        guild_bucket = guild_command_usage.setdefault(guild_id, deque())
        _prune_window(guild_bucket, GUILD_BLOCK_THRESHOLD_WINDOW_SECONDS, now_monotonic)
        guild_bucket.append(now_monotonic)

    # Escalate to temporary user block.
    if len(long_bucket) >= USER_BLOCK_THRESHOLD_COUNT:
        unblock_at = now_monotonic + USER_TEMP_BLOCK_SECONDS
        blocked_users[user_id] = unblock_at
        _user_block_notice_until[user_id] = unblock_at
        print(f"[ANTI-SPAM] User blocked: {user_id}", flush=True)
        await _safe_reply(
            interaction,
            await _guard_message(user_id, USER_BLOCK_MESSAGE_KEY, USER_BLOCK_MESSAGE_DEFAULT),
        )
        return False

    # Short per-user rate limit.
    if len(short_bucket) > USER_RATE_LIMIT_COUNT:
        notice_until = _user_rate_notice_until.get(user_id, 0.0)
        if now_monotonic >= notice_until:
            _user_rate_notice_until[user_id] = now_monotonic + USER_RATE_LIMIT_WINDOW_SECONDS
            await _safe_reply(
                interaction,
                await _guard_message(user_id, USER_RATE_LIMIT_MESSAGE_KEY, USER_RATE_LIMIT_MESSAGE_DEFAULT),
            )
        return False

    # Guild abuse protection.
    if guild_id is not None:
        guild_bucket = guild_command_usage[guild_id]
        if len(guild_bucket) >= GUILD_BLOCK_THRESHOLD_COUNT:
            unblock_at = now_monotonic + GUILD_TEMP_BLOCK_SECONDS
            blocked_guilds[guild_id] = unblock_at
            _guild_block_notice_until[guild_id] = unblock_at
            print(f"[ANTI-SPAM] Guild cooldown triggered: {guild_id}", flush=True)
            await _safe_reply(
                interaction,
                await _guard_message(user_id, GUILD_BLOCK_MESSAGE_KEY, GUILD_BLOCK_MESSAGE_DEFAULT),
            )
            return False

    return True


@asynccontextmanager
async def heavy_task_slot():
    async with heavy_task_semaphore:
        yield


def register_dev_group(bot: commands.Bot) -> None:
    if bot.get_command("dev") is not None:
        return

    @bot.group(name="dev", invoke_without_command=True, hidden=True)
    async def dev_group(ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send(
                "Usage: `.dev banuser <id|@mention|username>`, `.dev unbanuser <id|@mention|username>`, "
                "`.dev banguild <id>`, `.dev unguild <id>`, `.dev announce_language [send]`"
            )

    @dev_group.command(name="banuser")
    @commands.is_owner()
    async def dev_banuser(ctx: commands.Context, user: str) -> None:
        user_id = await resolve_user_identifier(bot, user)
        if user_id is None:
            await ctx.send(
                "Could not resolve that user. Use a numeric id, @mention, or a username from a server "
                "this bot is in (usernames are matched to ids at ban time)."
            )
            return
        ban_user(user_id)
        await ctx.send(f"User `{user_id}` has been permanently blacklisted.")

    @dev_group.command(name="unbanuser")
    @commands.is_owner()
    async def dev_unbanuser(ctx: commands.Context, user: str) -> None:
        user_id = await resolve_user_identifier(bot, user)
        if user_id is None:
            await ctx.send(
                "Could not resolve that user. Use a numeric id, @mention, or a username from a server "
                "this bot is in."
            )
            return
        unban_user(user_id)
        await ctx.send(f"User `{user_id}` has been removed from blacklist.")

    @dev_group.command(name="banguild")
    @commands.is_owner()
    async def dev_banguild(ctx: commands.Context, guild_id: int) -> None:
        ban_guild(guild_id)
        await ctx.send(f"Guild `{guild_id}` has been permanently blacklisted.")

    @dev_group.command(name="unguild")
    @commands.is_owner()
    async def dev_unguild(ctx: commands.Context, guild_id: int) -> None:
        unban_guild(guild_id)
        await ctx.send(f"Guild `{guild_id}` has been removed from blacklist.")

    register_dev_broadcast_commands(dev_group)
