"""
Per-channel sticky notices: multiple named rules, re-posted at the bottom when users chat.

Commands: /sticky_msg create | remove | list
Requires Manage Server. Uses Manage Messages + Send Messages in the target channel.
Limits: 10 sticky rules per server (50 for Ko-fi supporters on the account running the command).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.module_registry import is_module_enabled

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "sticky_messages.json"
MODULE_ID = "sticky_msg"

LOGGER = logging.getLogger("coffeecord.sticky_msg")
_CONFIG_LOCK = asyncio.Lock()

# Discord slash string options max at 100 characters; ids are normalized to lowercase.
STICKY_ID_MAX_LEN = 100
STICKY_ID_PATTERN = re.compile(rf"^[a-z0-9_-]{{1,{STICKY_ID_MAX_LEN}}}$")
MAX_STICKY_RULES_DEFAULT = 10
MAX_STICKY_RULES_SUPPORTER = 50
SUPPORTER_GRACE_DAYS = 35

SUPPORTERS_FILE = BASE_DIR / "Storage" / "Data" / "supporters.json"

STICKY_RATE_WINDOW_S = 60.0
STICKY_HIGH_RATE_THRESHOLD = 100
STICKY_BUMP_DEBOUNCE_LOW_S = 0.35
STICKY_BUMP_DEBOUNCE_HIGH_S = 5.0

# Serialize bumps per (guild, channel) so overlapping debounced tasks cannot post duplicates.
_CHANNEL_BUMP_LOCKS: dict[tuple[int, int], asyncio.Lock] = defaultdict(lambda: asyncio.Lock())


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


async def _load_root() -> dict[str, Any]:
    async with _CONFIG_LOCK:
        return await asyncio.to_thread(_read_json_sync, CONFIG_PATH, {})


async def _save_root(data: dict[str, Any]) -> None:
    async with _CONFIG_LOCK:
        await asyncio.to_thread(_write_json_sync, CONFIG_PATH, data)


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


def _is_supporter_user(user_id: int) -> bool:
    data = _read_json_sync(SUPPORTERS_FILE, {"supporters": {}})
    supporters = data.get("supporters", {})
    if isinstance(supporters, list):
        return str(user_id) in {str(x) for x in supporters}
    if not isinstance(supporters, dict):
        return False
    record = supporters.get(str(user_id))
    if isinstance(record, dict):
        return _supporter_record_is_active(record)
    return bool(record)


def _max_sticky_rules_for_user(user_id: int) -> int:
    if _is_supporter_user(user_id):
        return MAX_STICKY_RULES_SUPPORTER
    return MAX_STICKY_RULES_DEFAULT


def _normalize_rule_id(raw: str) -> Optional[str]:
    s = raw.strip().lower()
    if not STICKY_ID_PATTERN.match(s):
        return None
    return s


def _normalize_rule_dict(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    cid = raw.get("channel_id")
    if not isinstance(cid, int):
        return None
    msg = raw.get("message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    lid = raw.get("last_message_id")
    return {
        "channel_id": cid,
        "message": msg.strip(),
        "embed_enabled": bool(raw.get("embed_enabled", False)),
        "last_message_id": lid if isinstance(lid, int) else None,
    }


async def _get_guild_rules(guild_id: int) -> dict[str, dict[str, Any]]:
    root = await _load_root()
    g = root.get(str(guild_id), {})
    if not isinstance(g, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for rid, raw in g.items():
        if not isinstance(rid, str):
            continue
        norm = _normalize_rule_id(rid)
        if norm is None:
            continue
        r = _normalize_rule_dict(raw)
        if r is not None:
            out[norm] = r
    return out


async def _set_guild_rules(guild_id: int, rules: dict[str, dict[str, Any]]) -> None:
    root = await _load_root()
    root[str(guild_id)] = rules
    await _save_root(root)


async def _patch_rule(guild_id: int, rule_id: str, **updates: Any) -> None:
    rules = await _get_guild_rules(guild_id)
    if rule_id not in rules:
        return
    rules[rule_id].update(updates)
    await _set_guild_rules(guild_id, rules)


def _render_sticky_body(template: str, guild: discord.Guild) -> str:
    return (
        template.replace("{server_name}", guild.name)
        .replace("{member_count}", str(guild.member_count or 0))
    )


def _rules_for_channel(rules: dict[str, dict[str, Any]], channel_id: int) -> list[tuple[str, dict[str, Any]]]:
    matched = [(rid, r) for rid, r in rules.items() if r.get("channel_id") == channel_id]
    matched.sort(key=lambda x: x[0])
    return matched


class _ChannelBumpScheduler:
    def __init__(self) -> None:
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

    def note_message(self, guild_id: int, channel_id: int) -> None:
        self._history[(guild_id, channel_id)].append(time.monotonic())

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
                LOGGER.exception("sticky_msg bump failed guild=%s channel=%s", guild_id, channel_id)
            finally:
                self._pending.pop(key, None)

        self._pending[key] = asyncio.create_task(_run())


async def _bump_channel_stickies(bot: commands.Bot, guild: discord.Guild, channel_id: int) -> None:
    lock_key = (guild.id, channel_id)
    async with _CHANNEL_BUMP_LOCKS[lock_key]:
        rules = await _get_guild_rules(guild.id)
        pairs = _rules_for_channel(rules, channel_id)
        if not pairs:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if me is None:
            return
        perms = channel.permissions_for(me)
        if not perms.send_messages or not perms.view_channel or not perms.manage_messages:
            return

        for rule_id, _rule in pairs:
            fresh = await _get_guild_rules(guild.id)
            rule = fresh.get(rule_id)
            if rule is None:
                continue
            old_id = rule.get("last_message_id")
            if isinstance(old_id, int):
                try:
                    old = await channel.fetch_message(old_id)
                    await old.delete()
                except (discord.HTTPException, discord.NotFound):
                    pass
            body = _render_sticky_body(str(rule.get("message", "")), guild)
            try:
                if rule.get("embed_enabled") and perms.embed_links:
                    embed = discord.Embed(title="📌 Sticky", description=body[:4096], color=discord.Color.blurple())
                    embed.set_footer(text=f"{guild.name} • {rule_id}")
                    msg = await channel.send(embed=embed)
                else:
                    msg = await channel.send(body[:2000])
            except discord.HTTPException:
                LOGGER.warning("sticky_msg send failed guild=%s rule=%s", guild.id, rule_id)
                continue
            await _patch_rule(guild.id, rule_id, last_message_id=msg.id)


class StickyMsgCog(
    commands.GroupCog,
    group_name="sticky_msg",
    group_description="Named sticky messages: re-post at the bottom of a channel when people chat.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._scheduler = _ChannelBumpScheduler()

    @app_commands.command(name="create", description="Create or replace a named sticky in a channel.")
    @app_commands.rename(sticky_id="rule_id")
    @app_commands.describe(
        sticky_id="Unique rule id (1–100 chars: letters, digits, `_`, `-`; stored lowercase).",
        channel="Channel where this sticky is kept at the bottom.",
        message="Text. Placeholders: {server_name}, {member_count}.",
        use_embed="Send as embed.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def create(
        self,
        interaction: discord.Interaction,
        sticky_id: app_commands.Range[str, 1, 100],
        channel: discord.TextChannel,
        message: str,
        use_embed: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        rid = _normalize_rule_id(sticky_id)
        if rid is None:
            await interaction.response.send_message(
                "Invalid `rule_id`. Use 1–100 characters: letters, digits, `_`, `-` only.",
                ephemeral=True,
            )
            return
        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("Bot member not available.", ephemeral=True)
            return
        perms = channel.permissions_for(me)
        if not perms.send_messages or not perms.view_channel or not perms.manage_messages:
            await interaction.response.send_message(
                "I need **Send Messages**, **View Channel**, and **Manage Messages** in that channel.",
                ephemeral=True,
            )
            return
        if use_embed and not perms.embed_links:
            await interaction.response.send_message("I need **Embed Links** in that channel for embed stickies.", ephemeral=True)
            return

        rules = await _get_guild_rules(interaction.guild.id)
        limit = _max_sticky_rules_for_user(interaction.user.id)
        if rid not in rules and len(rules) >= limit:
            hint = (
                f"\nKo-fi supporters can create up to {MAX_STICKY_RULES_SUPPORTER} per server (`/kofi link`)."
                if limit < MAX_STICKY_RULES_SUPPORTER
                else ""
            )
            await interaction.response.send_message(
                f"Maximum **{limit}** sticky rules per server for your account. Remove one first.{hint}",
                ephemeral=True,
            )
            return

        rules[rid] = {
            "channel_id": channel.id,
            "message": message.strip()[:2000],
            "embed_enabled": bool(use_embed),
            "last_message_id": None,
        }
        await _set_guild_rules(interaction.guild.id, rules)
        await interaction.response.send_message(
            f"✅ Sticky `{rid}` set for {channel.mention}. It will move to the bottom after new messages.",
            ephemeral=True,
        )
        asyncio.create_task(_bump_channel_stickies(self.bot, interaction.guild, channel.id))

    @app_commands.command(name="remove", description="Remove a named sticky rule (and try to delete its last post).")
    @app_commands.rename(sticky_id="rule_id")
    @app_commands.describe(sticky_id="The sticky rule id to remove.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(
        self,
        interaction: discord.Interaction,
        sticky_id: app_commands.Range[str, 1, 100],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        rid = _normalize_rule_id(sticky_id)
        if rid is None:
            await interaction.response.send_message(
                "Invalid `rule_id`. Use 1–100 characters: letters, digits, `_`, `-` only.",
                ephemeral=True,
            )
            return
        rules = await _get_guild_rules(interaction.guild.id)
        if rid not in rules:
            await interaction.response.send_message(f"No sticky rule named `{rid}`.", ephemeral=True)
            return
        rule = rules.pop(rid)
        await _set_guild_rules(interaction.guild.id, rules)
        ch = interaction.guild.get_channel(int(rule["channel_id"]))
        old_id = rule.get("last_message_id")
        lock_key = (interaction.guild.id, int(rule["channel_id"]))
        async with _CHANNEL_BUMP_LOCKS[lock_key]:
            if isinstance(ch, discord.TextChannel) and isinstance(old_id, int):
                try:
                    m = await ch.fetch_message(old_id)
                    await m.delete()
                except (discord.HTTPException, discord.NotFound):
                    pass
        await interaction.response.send_message(f"✅ Removed sticky `{rid}`.", ephemeral=True)

    @app_commands.command(name="list", description="List sticky rules for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_rules(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        rules = await _get_guild_rules(interaction.guild.id)
        if not rules:
            await interaction.response.send_message("No sticky rules configured.", ephemeral=True)
            return
        lines = []
        for rid in sorted(rules.keys()):
            r = rules[rid]
            ch = interaction.guild.get_channel(int(r["channel_id"]))
            ch_name = ch.mention if isinstance(ch, discord.TextChannel) else f"`{r['channel_id']}`"
            lines.append(f"**{rid}** → {ch_name} ({'embed' if r.get('embed_enabled') else 'plain'})")
        embed = discord.Embed(
            title="Sticky rules",
            description="\n".join(lines)[:4096],
            color=discord.Color.dark_teal(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not await is_module_enabled(message.guild.id, MODULE_ID):
            return
        try:
            rules = await _get_guild_rules(message.guild.id)
            channel_ids = {int(r["channel_id"]) for r in rules.values() if isinstance(r.get("channel_id"), int)}
            if message.channel.id not in channel_ids:
                return
            self._scheduler.note_message(message.guild.id, message.channel.id)

            async def bump() -> None:
                g = self.bot.get_guild(message.guild.id)
                if g is None:
                    return
                await _bump_channel_stickies(self.bot, g, message.channel.id)

            self._scheduler.schedule(message.guild.id, message.channel.id, bump)
        except Exception:
            LOGGER.exception("sticky_msg on_message guild=%s", message.guild.id)


async def setup(bot: commands.Bot) -> None:
    await _load_root()
    await bot.add_cog(StickyMsgCog(bot))
