"""
Guild quests (Storage/Config/quests.json).

Schema per guild id:
  "<guild_id>": {
    "<quest_id>": {
      "id": str,
      "name": str,
      "description": str,
      "type": "daily_checkin" | "react_specific" | "messages",
      "goal": int,
      "reward": int (XP; requires leveling enabled to apply),
      "role_reward": null | "<role_id>",
      "expires_at": null | ISO8601,
      "message_id": str (react_specific),
      "emoji": str (react_specific),
      "channel_ids": [] (optional; messages quest — empty = all text channels)
      "members": { "<user_id>": { "progress": int, "completed": bool, "last_claim_ymd": str|null } }
    }
  }

daily_checkin: /quests checkin once per UTC day (last_claim_ymd).
react_specific / messages: one-time; completed stays true.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.json_cache import get as _json_get, set_ as _json_set
from Modules.leveling import _dispatch_module_event, award_quest_xp
from Modules.module_registry import is_module_enabled

BASE_DIR = Path(__file__).resolve().parent.parent
QUESTS_FILE = BASE_DIR / "Storage" / "Config" / "quests.json"

QUEST_EMBED_COLOR = 0x5865F2
QUEST_ACCENT_COLOR = 0x57F287
QUEST_FOOTER = "Coffeecord Quests"
PROGRESS_BAR_WIDTH = 14
QUESTS_LOCK = asyncio.Lock()


def _load_quests() -> dict[str, Any]:
    raw = _json_get(QUESTS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def _save_quests(data: dict[str, Any]) -> None:
    _json_set(QUESTS_FILE, data)


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _quest_expired(quest: dict[str, Any]) -> bool:
    exp = quest.get("expires_at")
    if exp is None or str(exp).strip() in ("", "null", "None"):
        return False
    try:
        raw = str(exp).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > dt
    except (TypeError, ValueError):
        return False


def _member_entry(quest: dict[str, Any], uid: str) -> dict[str, Any]:
    members = quest.setdefault("members", {})
    if not isinstance(members, dict):
        quest["members"] = {}
        members = quest["members"]
    if uid not in members or not isinstance(members.get(uid), dict):
        members[uid] = {"progress": 0, "completed": False, "last_claim_ymd": None}
    return members[uid]


def _progress_bar(current: int, goal: int) -> str:
    goal = max(int(goal), 1)
    cur = max(0, min(int(current), goal))
    w = PROGRESS_BAR_WIDTH
    filled = int(round(w * cur / goal))
    bar = "█" * filled + "░" * (w - filled)
    return f"`{bar}` **{cur}** / **{goal}**"


def _emoji_matches(configured: str, emoji: discord.PartialEmoji) -> bool:
    q = str(configured or "").strip()
    if not q:
        return False
    if emoji.id:
        built = f"<a:{emoji.name}:{emoji.id}>" if emoji.animated else f"<:{emoji.name}:{emoji.id}>"
        return q == built or q == str(emoji)
    return q == str(emoji)


def _daily_claimed_today(m: dict[str, Any]) -> bool:
    return str(m.get("last_claim_ymd") or "") == _utc_today()


def _quest_status_line(quest: dict[str, Any], m: dict[str, Any], qtype: str) -> str:
    name = str(quest.get("name") or quest.get("id") or "Quest")
    goal = max(int(quest.get("goal", 1) or 1), 1)
    if qtype == "daily_checkin":
        if _daily_claimed_today(m):
            return f"**{name}** — ✅ Claimed today (UTC)\n└ *Come back tomorrow.*"
        return f"**{name}**\n└ {_progress_bar(0, goal)} — use `/quests checkin`"
    if bool(m.get("completed")):
        return f"**{name}** — ✅ Complete\n└ Reward claimed."
    prog = int(m.get("progress", 0))
    desc = str(quest.get("description") or "")[:120]
    tail = f"\n└ _{desc}_" if desc else ""
    return f"**{name}**\n└ {_progress_bar(prog, goal)}{tail}"


class _RewardContext(NamedTuple):
    reward_xp: int
    quest_title: str
    role_reward: Optional[int]


class QuestsCog(commands.Cog):
    """Quest board, daily check-in, and progress tracked in quests.json."""

    quests = app_commands.Group(name="quests", description="Server quests, progress, and daily check-in")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _guild_quests_copy(self, guild_id: int) -> dict[str, Any]:
        data = _load_quests()
        g = data.get(str(guild_id))
        if not isinstance(g, dict):
            return {}
        return copy.deepcopy(g)

    async def _deliver_rewards(
        self,
        guild: discord.Guild,
        member: discord.Member,
        quest_title: str,
        reward_xp: int,
        role_reward: Optional[int],
        channel: Optional[discord.abc.Messageable],
    ) -> None:
        if reward_xp > 0:
            await award_quest_xp(self.bot, guild, member.id, reward_xp, channel)

        if role_reward:
            role = guild.get_role(role_reward)
            if role and role < guild.me.top_role:
                try:
                    await member.add_roles(role, reason="Quest reward")
                except discord.HTTPException:
                    pass

        await _dispatch_module_event(
            self.bot,
            guild,
            "quests",
            "quest_completed",
            actor=member,
            details=f"quest={quest_title!r}; reward_xp={reward_xp}",
            channel_id=channel.id if isinstance(channel, discord.TextChannel) else None,
        )

        xp_part = f"+**{reward_xp}** XP" if reward_xp > 0 else "_No XP reward this time._"
        role_part = f"\n**Role:** <@&{role_reward}>" if role_reward else ""
        emb = discord.Embed(
            title="Quest complete",
            description=f"**{quest_title}**\n{xp_part}{role_part}",
            color=QUEST_ACCENT_COLOR,
        )
        emb.set_footer(text=QUEST_FOOTER)
        try:
            await member.send(embed=emb)
        except discord.HTTPException:
            pass

    @quests.command(name="list", description="View active quests and your progress")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id or 0, i.user.id))
    async def quests_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "quests"):
            await interaction.response.send_message(
                "Quests are disabled here. An admin can enable them with `/modules`.",
                ephemeral=True,
            )
            return
        gq = self._guild_quests_copy(interaction.guild.id)
        if not gq:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Quest board",
                    description="No quests configured yet.\n\n"
                    "Admins can add entries under `Storage/Config/quests.json` "
                    f"for guild id `{interaction.guild.id}`.",
                    color=QUEST_EMBED_COLOR,
                ).set_footer(text=QUEST_FOOTER),
                ephemeral=True,
            )
            return

        uid = str(interaction.user.id)
        lines: list[str] = []
        for qid, quest in sorted(gq.items(), key=lambda kv: str(kv[0])):
            if not isinstance(quest, dict):
                continue
            if _quest_expired(quest):
                continue
            qtype = str(quest.get("type") or "")
            if qtype not in ("daily_checkin", "react_specific", "messages"):
                continue
            m = _member_entry(quest, uid)
            lines.append(_quest_status_line(quest, m, qtype))

        if not lines:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Quest board",
                    description="All configured quests are expired or use an unknown `type`.",
                    color=QUEST_EMBED_COLOR,
                ).set_footer(text=QUEST_FOOTER),
                ephemeral=True,
            )
            return

        desc = "\n\n".join(lines)
        if len(desc) > 3800:
            desc = desc[:3797] + "…"

        emb = discord.Embed(
            title="Quest board",
            description=desc,
            color=QUEST_EMBED_COLOR,
        )
        if interaction.guild.icon:
            emb.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url)
        else:
            emb.set_author(name=interaction.guild.name)
        emb.set_footer(text=f"{QUEST_FOOTER} • Daily quests reset at midnight UTC")

        await interaction.response.send_message(embed=emb, ephemeral=True)

    @quests.command(name="checkin", description="Claim your daily check-in quest (UTC day)")
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: (i.guild_id or 0, i.user.id))
    async def quests_checkin(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "quests"):
            await interaction.response.send_message("Quests are disabled here.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        uid = str(interaction.user.id)
        reward_ctx: Optional[_RewardContext] = None

        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(guild.id), {}) or {})
            if not guild_map:
                await interaction.followup.send("No quests are configured for this server.", ephemeral=True)
                return

            target_key: Optional[str] = None
            target_quest: Optional[dict[str, Any]] = None
            for qid, raw in guild_map.items():
                if not isinstance(raw, dict) or _quest_expired(raw):
                    continue
                if str(raw.get("type")) != "daily_checkin":
                    continue
                target_key = str(qid)
                target_quest = dict(raw)
                break

            if not target_key or target_quest is None:
                await interaction.followup.send(
                    "There is no **daily_checkin** quest in `quests.json` for this server.",
                    ephemeral=True,
                )
                return

            m = _member_entry(target_quest, uid)
            if _daily_claimed_today(m):
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Already checked in",
                        description="You already claimed today’s daily (UTC). Try again after midnight UTC.",
                        color=discord.Color.orange(),
                    ).set_footer(text=QUEST_FOOTER),
                    ephemeral=True,
                )
                return

            m["last_claim_ymd"] = _utc_today()
            m["progress"] = max(int(target_quest.get("goal", 1) or 1), 1)
            guild_map[target_key] = target_quest
            data[str(guild.id)] = guild_map
            _save_quests(data)

            role_raw = target_quest.get("role_reward")
            role_id: Optional[int] = None
            if role_raw is not None and str(role_raw).strip() not in ("", "null", "None"):
                try:
                    role_id = int(role_raw)
                except (TypeError, ValueError):
                    role_id = None

            reward_ctx = _RewardContext(
                reward_xp=int(target_quest.get("reward", 0) or 0),
                quest_title=str(target_quest.get("name") or target_key),
                role_reward=role_id,
            )

        ch = interaction.channel
        member = interaction.user
        leveling_on = await is_module_enabled(guild.id, "leveling")
        if reward_ctx:
            await self._deliver_rewards(
                guild,
                member,
                reward_ctx.quest_title,
                reward_ctx.reward_xp if leveling_on else 0,
                reward_ctx.role_reward,
                ch,
            )

        xp_shown = reward_ctx.reward_xp if reward_ctx and leveling_on else 0
        xp_note = (
            ""
            if leveling_on
            else "\n*(Leveling is off — XP not awarded; role rewards still apply.)*"
        )
        await interaction.followup.send(
            embed=discord.Embed(
                title="Daily check-in",
                description=f"**{reward_ctx.quest_title if reward_ctx else 'Daily'}** claimed."
                f"\n+**{xp_shown}** XP{xp_note}",
                color=QUEST_ACCENT_COLOR,
            ).set_footer(text=QUEST_FOOTER),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        if not await is_module_enabled(guild.id, "quests"):
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        ctx: Optional[_RewardContext] = None
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(guild.id), {}) or {})
            for qid, raw in list(guild_map.items()):
                if not isinstance(raw, dict) or _quest_expired(raw):
                    continue
                if str(raw.get("type")) != "react_specific":
                    continue
                try:
                    mid = int(str(raw.get("message_id", "0")))
                except (TypeError, ValueError):
                    continue
                if mid != payload.message_id:
                    continue
                if not _emoji_matches(str(raw.get("emoji", "")), payload.emoji):
                    continue

                quest = dict(raw)
                m = _member_entry(quest, str(member.id))
                if bool(m.get("completed")):
                    break

                goal = max(int(quest.get("goal", 1) or 1), 1)
                m["progress"] = min(int(m.get("progress", 0)) + 1, goal)
                guild_map[qid] = quest

                if int(m["progress"]) >= goal:
                    m["completed"] = True
                    data[str(guild.id)] = guild_map
                    _save_quests(data)
                    role_raw = quest.get("role_reward")
                    role_id: Optional[int] = None
                    if role_raw is not None and str(role_raw).strip() not in ("", "null", "None"):
                        try:
                            role_id = int(role_raw)
                        except (TypeError, ValueError):
                            role_id = None
                    ctx = _RewardContext(
                        reward_xp=int(quest.get("reward", 0) or 0),
                        quest_title=str(quest.get("name") or qid),
                        role_reward=role_id,
                    )
                else:
                    data[str(guild.id)] = guild_map
                    _save_quests(data)
                break

        if ctx:
            leveling_on = await is_module_enabled(guild.id, "leveling")
            await self._deliver_rewards(
                guild,
                member,
                ctx.quest_title,
                ctx.reward_xp if leveling_on else 0,
                ctx.role_reward,
                channel,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        guild = message.guild
        if not await is_module_enabled(guild.id, "quests"):
            return
        author = message.author
        member = author if isinstance(author, discord.Member) else guild.get_member(author.id)
        if member is None or member.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        completions: list[_RewardContext] = []
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(guild.id), {}) or {})
            changed = False
            for qid, raw in list(guild_map.items()):
                if not isinstance(raw, dict) or _quest_expired(raw):
                    continue
                if str(raw.get("type")) != "messages":
                    continue
                ch_ids = raw.get("channel_ids")
                if isinstance(ch_ids, list) and len(ch_ids) > 0:
                    allowed: set[int] = set()
                    for x in ch_ids:
                        try:
                            allowed.add(int(x))
                        except (TypeError, ValueError):
                            continue
                    if message.channel.id not in allowed:
                        continue

                quest = dict(raw)
                m = _member_entry(quest, str(member.id))
                if bool(m.get("completed")):
                    continue

                goal = max(int(quest.get("goal", 1) or 1), 1)
                m["progress"] = min(int(m.get("progress", 0)) + 1, goal)
                guild_map[qid] = quest
                changed = True

                if int(m["progress"]) >= goal:
                    m["completed"] = True
                    role_raw = quest.get("role_reward")
                    role_id: Optional[int] = None
                    if role_raw is not None and str(role_raw).strip() not in ("", "null", "None"):
                        try:
                            role_id = int(role_raw)
                        except (TypeError, ValueError):
                            role_id = None
                    completions.append(
                        _RewardContext(
                            reward_xp=int(quest.get("reward", 0) or 0),
                            quest_title=str(quest.get("name") or qid),
                            role_reward=role_id,
                        )
                    )
            if changed:
                data[str(guild.id)] = guild_map
                _save_quests(data)

        leveling_on = await is_module_enabled(guild.id, "leveling")
        for ctx in completions:
            await self._deliver_rewards(
                guild,
                member,
                ctx.quest_title,
                ctx.reward_xp if leveling_on else 0,
                ctx.role_reward,
                message.channel,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuestsCog(bot))
