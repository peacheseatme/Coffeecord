"""
Guild quests (Storage/Config/quests.json).

Legacy types: daily_checkin, react_specific, messages (still supported).

Custom quests (`type: custom`) use a `rules` object for flexible tracking:

  rules.count              messages | emojis | reactions
  rules.window_seconds     rolling window (e.g. 600 = 10 minutes); omit = no window
  rules.require_role_ids   member must have ALL of these role ids
  rules.require_role_names member must have roles matching ALL names (case-insensitive)
  rules.require_any_role_* member must have at least ONE from any_* lists (OR)
  rules.channel_ids        empty = all text channels
  rules.message_id         reactions only
  rules.emoji              reactions only
  rules.min_chars          minimum message length
  rules.text_contains      substring match (case-insensitive)

Example — send 3 emojis in 10 minutes while wearing Purple:

  {
    "type": "custom",
    "goal": 3,
    "rules": {
      "count": "emojis",
      "window_seconds": 600,
      "require_role_names": ["Purple"]
    }
  }

Admins: `/quests admin create` … or edit JSON directly.
"""
from __future__ import annotations

import asyncio
import copy
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.json_cache import get as _json_get, set_ as _json_set
from Modules.i18n import t, t_sync
from Modules.leveling import _dispatch_module_event, award_quest_xp
from Modules.log_actor import log_actor_from_interaction
from Modules.module_registry import is_module_enabled

BASE_DIR = Path(__file__).resolve().parent.parent
QUESTS_FILE = BASE_DIR / "Storage" / "Config" / "quests.json"

QUEST_EMBED_COLOR = 0x5865F2
QUEST_ACCENT_COLOR = 0x57F287
QUEST_FOOTER = "Coffeecord Quests"
PROGRESS_BAR_WIDTH = 14
QUESTS_LOCK = asyncio.Lock()

QUEST_TYPES_ACTIVE = frozenset({"daily_checkin", "react_specific", "messages", "custom"})
CUSTOM_COUNT_CHOICES = ("messages", "emojis", "reactions")

_CUSTOM_EMOJI_RE = re.compile(r"<a?:[^:]+:\d+>")
_UNICODE_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF"
    r"\U0001F900-\U0001F9FF\U00002702-\U000027B0\u200d\uFE0F]+",
    flags=re.UNICODE,
)

DEFAULT_RULES: dict[str, Any] = {
    "count": "messages",
    "window_seconds": None,
    "require_role_ids": [],
    "require_role_names": [],
    "require_any_role_ids": [],
    "require_any_role_names": [],
    "channel_ids": [],
    "message_id": None,
    "emoji": None,
    "min_chars": 0,
    "text_contains": None,
}


def _load_quests() -> dict[str, Any]:
    raw = _json_get(QUESTS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def _save_quests(data: dict[str, Any]) -> None:
    _json_set(QUESTS_FILE, data)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_today() -> str:
    return _utc_now().date().isoformat()


def _parse_iso_dt(raw: Any) -> datetime | None:
    if raw is None or str(raw).strip() in ("", "null", "None"):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _quest_expired(quest: dict[str, Any]) -> bool:
    exp = _parse_iso_dt(quest.get("expires_at"))
    return exp is not None and _utc_now() > exp


def _quest_enabled(quest: dict[str, Any]) -> bool:
    return quest.get("enabled", True) is not False


def _new_quest_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32] or "quest"
    return f"{base}_{secrets.token_hex(3)}"


def _member_entry(quest: dict[str, Any], uid: str) -> dict[str, Any]:
    members = quest.setdefault("members", {})
    if not isinstance(members, dict):
        quest["members"] = {}
        members = quest["members"]
    if uid not in members or not isinstance(members.get(uid), dict):
        members[uid] = {
            "progress": 0,
            "completed": False,
            "last_claim_ymd": None,
            "window_start": None,
        }
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


def _count_emojis_in_content(content: str) -> int:
    text = content or ""
    custom = len(_CUSTOM_EMOJI_RE.findall(text))
    stripped = _CUSTOM_EMOJI_RE.sub("", text)
    unicode_count = len(_UNICODE_EMOJI_RE.findall(stripped))
    return custom + unicode_count


def _normalize_rules(raw: Any) -> dict[str, Any]:
    rules = dict(DEFAULT_RULES)
    if isinstance(raw, dict):
        rules.update(raw)
    return rules


def _effective_rules(quest: dict[str, Any]) -> dict[str, Any]:
    qtype = str(quest.get("type") or "")
    if qtype == "custom":
        return _normalize_rules(quest.get("rules"))
    if qtype == "messages":
        return _normalize_rules(
            {
                "count": "messages",
                "channel_ids": quest.get("channel_ids") or [],
            }
        )
    if qtype == "react_specific":
        return _normalize_rules(
            {
                "count": "reactions",
                "message_id": quest.get("message_id"),
                "emoji": quest.get("emoji"),
            }
        )
    return dict(DEFAULT_RULES)


def _role_id_list(raw: Any) -> list[int]:
    out: list[int] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _role_name_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip().lower() for x in raw if str(x).strip()]


def _member_meets_role_rules(member: discord.Member, rules: dict[str, Any]) -> bool:
    member_ids = {r.id for r in member.roles}
    member_names = {r.name.lower() for r in member.roles}

    for rid in _role_id_list(rules.get("require_role_ids")):
        if rid not in member_ids:
            return False

    for name in _role_name_list(rules.get("require_role_names")):
        if not any(name == rn or name in rn for rn in member_names):
            return False

    any_ids = _role_id_list(rules.get("require_any_role_ids"))
    any_names = _role_name_list(rules.get("require_any_role_names"))
    if any_ids or any_names:
        ok = False
        if any(rid in member_ids for rid in any_ids):
            ok = True
        if any(name in member_names or any(name in rn for rn in member_names) for name in any_names):
            ok = True
        if not ok:
            return False
    return True


def _channel_allowed(rules: dict[str, Any], channel_id: int) -> bool:
    ch_ids = rules.get("channel_ids")
    if not isinstance(ch_ids, list) or not ch_ids:
        return True
    allowed = set(_role_id_list(ch_ids))
    return channel_id in allowed


def _window_seconds(rules: dict[str, Any]) -> int | None:
    raw = rules.get("window_seconds")
    if raw is None or str(raw).strip() in ("", "null", "None"):
        return None
    try:
        sec = int(raw)
        return sec if sec > 0 else None
    except (TypeError, ValueError):
        return None


def _reset_window_if_expired(m: dict[str, Any], window_seconds: int | None) -> None:
    if not window_seconds:
        return
    now = _utc_now()
    ws = _parse_iso_dt(m.get("window_start"))
    if ws is None:
        m["window_start"] = now.isoformat()
        return
    if (now - ws).total_seconds() > float(window_seconds):
        m["progress"] = 0
        m["window_start"] = now.isoformat()


def _increment_progress(m: dict[str, Any], quest: dict[str, Any], amount: int) -> int:
    goal = max(int(quest.get("goal", 1) or 1), 1)
    rules = _effective_rules(quest)
    window = _window_seconds(rules)
    _reset_window_if_expired(m, window)
    if window and m.get("window_start") is None:
        m["window_start"] = _utc_now().isoformat()
    m["progress"] = min(int(m.get("progress", 0)) + max(amount, 0), goal)
    return int(m["progress"])


def _message_increment(message: discord.Message, rules: dict[str, Any]) -> int:
    if str(rules.get("count") or "messages") not in ("messages", "emojis"):
        return 0
    content = message.content or ""
    min_chars = int(rules.get("min_chars") or 0)
    if len(content.strip()) < min_chars:
        return 0
    needle = rules.get("text_contains")
    if needle and str(needle).strip():
        if str(needle).lower() not in content.lower():
            return 0
    if str(rules.get("count")) == "emojis":
        count = _count_emojis_in_content(content)
        return count if count > 0 else 0
    return 1


def _rules_summary(user_id: int, quest: dict[str, Any]) -> str:
    qtype = str(quest.get("type") or "")
    if qtype == "daily_checkin":
        return t_sync(user_id, "quests.rules.daily_checkin")
    rules = _effective_rules(quest)
    parts: list[str] = []
    count = str(rules.get("count") or "messages")
    count_labels = {
        "messages": t_sync(user_id, "quests.rules.count_messages"),
        "emojis": t_sync(user_id, "quests.rules.count_emojis"),
        "reactions": t_sync(user_id, "quests.rules.count_reactions"),
    }
    parts.append(count_labels.get(count, count))
    window = _window_seconds(rules)
    if window:
        if window % 3600 == 0:
            parts.append(
                t_sync(user_id, "quests.rules.window_hours", hours=str(window // 3600))
            )
        elif window % 60 == 0:
            parts.append(
                t_sync(user_id, "quests.rules.window_minutes", minutes=str(window // 60))
            )
        else:
            parts.append(
                t_sync(user_id, "quests.rules.window_seconds", seconds=str(window))
            )
    req_names = _role_name_list(rules.get("require_role_names"))
    req_ids = _role_id_list(rules.get("require_role_ids"))
    if req_names:
        parts.append(
            t_sync(user_id, "quests.rules.roles", roles=", ".join(req_names))
        )
    elif req_ids:
        parts.append(
            t_sync(user_id, "quests.rules.role_ids", count=str(len(req_ids)))
        )
    any_names = _role_name_list(rules.get("require_any_role_names"))
    if any_names:
        parts.append(
            t_sync(user_id, "quests.rules.any_role", roles=", ".join(any_names))
        )
    ch_ids = rules.get("channel_ids")
    if isinstance(ch_ids, list) and ch_ids:
        parts.append(
            t_sync(user_id, "quests.rules.channels", count=str(len(ch_ids)))
        )
    separator = t_sync(user_id, "quests.rules.separator")
    return separator.join(parts) if parts else qtype


def _quest_status_line(user_id: int, quest: dict[str, Any], m: dict[str, Any], qtype: str) -> str:
    name = str(quest.get("name") or quest.get("id") or t_sync(user_id, "quests.quest_fallback_name"))
    goal = max(int(quest.get("goal", 1) or 1), 1)
    summary = _rules_summary(user_id, quest)
    if qtype == "daily_checkin":
        if _daily_claimed_today(m):
            return (
                f"**{name}** — {t_sync(user_id, 'quests.status.claimed_today')}\n"
                f"└ *{t_sync(user_id, 'quests.status.come_back_tomorrow')}*"
            )
        return (
            f"**{name}**\n└ {_progress_bar(0, goal)} — "
            f"{t_sync(user_id, 'quests.status.use_checkin')}"
        )
    if bool(m.get("completed")):
        return (
            f"**{name}** — {t_sync(user_id, 'quests.status.complete')}\n"
            f"└ {t_sync(user_id, 'quests.status.reward_claimed')}"
        )
    prog = int(m.get("progress", 0))
    desc = str(quest.get("description") or "")[:120]
    tail = f"\n└ _{desc}_" if desc else ""
    rule_line = f"\n└ `{summary}`" if summary else ""
    return f"**{name}**\n└ {_progress_bar(prog, goal)}{rule_line}{tail}"


class _RewardContext(NamedTuple):
    reward_xp: int
    quest_title: str
    role_reward: Optional[int]


quests_group = app_commands.Group(name="quests", description="Server quests, progress, and daily check-in")
quests_admin_group = app_commands.Group(
    name="admin",
    description="Create and manage custom server quests",
    parent=quests_group,
    default_permissions=discord.Permissions(manage_guild=True),
)

COUNT_TYPE_CHOICES = [
    app_commands.Choice(name="Count messages", value="messages"),
    app_commands.Choice(name="Count emojis sent", value="emojis"),
    app_commands.Choice(name="Count reactions", value="reactions"),
    app_commands.Choice(name="Daily check-in", value="daily_checkin"),
]


class QuestsCog(commands.Cog):
    """Quest board, custom rules, daily check-in, and admin management."""

    quests = quests_group

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _guild_quests_copy(self, guild_id: int) -> dict[str, Any]:
        data = _load_quests()
        g = data.get(str(guild_id))
        if not isinstance(g, dict):
            return {}
        return copy.deepcopy(g)

    def _parse_role_reward(self, quest: dict[str, Any]) -> Optional[int]:
        role_raw = quest.get("role_reward")
        if role_raw is None or str(role_raw).strip() in ("", "null", "None"):
            return None
        try:
            return int(role_raw)
        except (TypeError, ValueError):
            return None

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

        xp_part = (
            await t(guild.id, "quests.rewards.xp_part", reward_xp=str(reward_xp))
            if reward_xp > 0
            else await t(guild.id, "quests.rewards.no_xp_part")
        )
        role_part = (
            "\n"
            + await t(guild.id, "quests.rewards.role_part", role=f"<@&{role_reward}>")
            if role_reward
            else ""
        )
        emb = discord.Embed(
            title=await t(guild.id, "quests.rewards.complete_title"),
            description=f"**{quest_title}**\n{xp_part}{role_part}",
            color=QUEST_ACCENT_COLOR,
        )
        emb.set_footer(text=await t(guild.id, "quests.footer"))
        try:
            await member.send(embed=emb)
        except discord.HTTPException:
            pass

    async def _apply_quest_completions(
        self,
        guild: discord.Guild,
        member: discord.Member,
        completions: list[_RewardContext],
        channel: Optional[discord.abc.Messageable],
    ) -> None:
        leveling_on = await is_module_enabled(guild.id, "leveling")
        for ctx in completions:
            await self._deliver_rewards(
                guild,
                member,
                ctx.quest_title,
                ctx.reward_xp if leveling_on else 0,
                ctx.role_reward,
                channel,
            )

    def _completion_context(self, quest: dict[str, Any], qid: str) -> _RewardContext:
        return _RewardContext(
            reward_xp=int(quest.get("reward", 0) or 0),
            quest_title=str(quest.get("name") or qid),
            role_reward=self._parse_role_reward(quest),
        )

    async def _progress_tracked_quests_message(
        self,
        guild: discord.Guild,
        member: discord.Member,
        message: discord.Message,
    ) -> None:
        completions: list[_RewardContext] = []
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(guild.id), {}) or {})
            changed = False
            for qid, raw in list(guild_map.items()):
                if not isinstance(raw, dict) or _quest_expired(raw) or not _quest_enabled(raw):
                    continue
                qtype = str(raw.get("type") or "")
                if qtype not in ("messages", "custom"):
                    continue
                rules = _effective_rules(raw)
                if str(rules.get("count") or "messages") not in ("messages", "emojis"):
                    continue
                if not _channel_allowed(rules, message.channel.id):
                    continue
                if not _member_meets_role_rules(member, rules):
                    continue

                quest = dict(raw)
                m = _member_entry(quest, str(member.id))
                if bool(m.get("completed")) or qtype == "daily_checkin":
                    continue

                increment = _message_increment(message, rules)
                if increment <= 0:
                    continue

                goal = max(int(quest.get("goal", 1) or 1), 1)
                progress = _increment_progress(m, quest, increment)
                guild_map[qid] = quest
                changed = True

                if progress >= goal:
                    m["completed"] = True
                    completions.append(self._completion_context(quest, str(qid)))

            if changed:
                data[str(guild.id)] = guild_map
                _save_quests(data)

        if completions:
            await self._apply_quest_completions(guild, member, completions, message.channel)

    async def _progress_tracked_quests_reaction(
        self,
        guild: discord.Guild,
        member: discord.Member,
        channel: discord.TextChannel,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        ctx: Optional[_RewardContext] = None
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(guild.id), {}) or {})
            for qid, raw in list(guild_map.items()):
                if not isinstance(raw, dict) or _quest_expired(raw) or not _quest_enabled(raw):
                    continue
                qtype = str(raw.get("type") or "")
                if qtype not in ("react_specific", "custom"):
                    continue
                rules = _effective_rules(raw)
                if str(rules.get("count") or "") != "reactions" and qtype != "react_specific":
                    continue
                try:
                    mid = int(str(rules.get("message_id") or raw.get("message_id") or "0"))
                except (TypeError, ValueError):
                    continue
                if mid != payload.message_id:
                    continue
                emoji_cfg = rules.get("emoji") or raw.get("emoji")
                if not _emoji_matches(str(emoji_cfg or ""), payload.emoji):
                    continue
                if not _member_meets_role_rules(member, rules):
                    continue

                quest = dict(raw)
                m = _member_entry(quest, str(member.id))
                if bool(m.get("completed")):
                    break

                goal = max(int(quest.get("goal", 1) or 1), 1)
                progress = _increment_progress(m, quest, 1)
                guild_map[qid] = quest

                if progress >= goal:
                    m["completed"] = True
                    data[str(guild.id)] = guild_map
                    _save_quests(data)
                    ctx = self._completion_context(quest, str(qid))
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

    @quests_group.command(name="list", description="View active quests and your progress")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id or 0, i.user.id))
    async def quests_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "quests"):
            await interaction.response.send_message(
                await t(interaction.user.id, "quests.messages.module_disabled"),
                ephemeral=True,
            )
            return
        gq = self._guild_quests_copy(interaction.guild.id)
        if not gq:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=await t(interaction.user.id, "quests.board.title"),
                    description=await t(interaction.user.id, "quests.board.no_quests"),
                    color=QUEST_EMBED_COLOR,
                ).set_footer(text=await t(interaction.user.id, "quests.footer")),
                ephemeral=True,
            )
            return

        uid = str(interaction.user.id)
        lines: list[str] = []
        for qid, quest in sorted(gq.items(), key=lambda kv: str(kv[0])):
            if not isinstance(quest, dict):
                continue
            if _quest_expired(quest) or not _quest_enabled(quest):
                continue
            qtype = str(quest.get("type") or "")
            if qtype not in QUEST_TYPES_ACTIVE:
                continue
            m = _member_entry(quest, uid)
            lines.append(_quest_status_line(interaction.guild.id, quest, m, qtype))

        if not lines:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=await t(interaction.user.id, "quests.board.title"),
                    description=await t(interaction.user.id, "quests.board.none_active"),
                    color=QUEST_EMBED_COLOR,
                ).set_footer(text=await t(interaction.user.id, "quests.footer")),
                ephemeral=True,
            )
            return

        desc = "\n\n".join(lines)
        if len(desc) > 3800:
            desc = desc[:3797] + "…"

        emb = discord.Embed(
            title=await t(interaction.user.id, "quests.board.title"),
            description=desc,
            color=QUEST_EMBED_COLOR,
        )
        if interaction.guild.icon:
            emb.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url)
        else:
            emb.set_author(name=interaction.guild.name)
        emb.set_footer(
            text=await t(interaction.user.id, "quests.board.footer_daily_reset")
        )

        await interaction.response.send_message(embed=emb, ephemeral=True)

    @quests_group.command(name="checkin", description="Claim your daily check-in quest (UTC day)")
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: (i.guild_id or 0, i.user.id))
    async def quests_checkin(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "quests"):
            await interaction.response.send_message(
                await t(interaction.user.id, "quests.messages.module_disabled_short"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        uid = str(interaction.user.id)
        reward_ctx: Optional[_RewardContext] = None

        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(guild.id), {}) or {})
            if not guild_map:
                await interaction.followup.send(
                    await t(guild.id, "quests.checkin.no_quests_configured"),
                    ephemeral=True,
                )
                return

            target_key: Optional[str] = None
            target_quest: Optional[dict[str, Any]] = None
            for qid, raw in guild_map.items():
                if not isinstance(raw, dict) or _quest_expired(raw) or not _quest_enabled(raw):
                    continue
                if str(raw.get("type")) != "daily_checkin":
                    continue
                target_key = str(qid)
                target_quest = dict(raw)
                break

            if not target_key or target_quest is None:
                await interaction.followup.send(
                    await t(guild.id, "quests.checkin.no_daily_quest"),
                    ephemeral=True,
                )
                return

            m = _member_entry(target_quest, uid)
            if _daily_claimed_today(m):
                await interaction.followup.send(
                    embed=discord.Embed(
                        title=await t(guild.id, "quests.checkin.already_title"),
                        description=await t(guild.id, "quests.checkin.already_description"),
                        color=discord.Color.orange(),
                    ).set_footer(text=await t(guild.id, "quests.footer")),
                    ephemeral=True,
                )
                return

            m["last_claim_ymd"] = _utc_today()
            m["progress"] = max(int(target_quest.get("goal", 1) or 1), 1)
            guild_map[target_key] = target_quest
            data[str(guild.id)] = guild_map
            _save_quests(data)
            reward_ctx = self._completion_context(target_quest, target_key)

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
            else "\n*" + await t(guild.id, "quests.checkin.leveling_off_note") + "*"
        )
        checkin_title = reward_ctx.quest_title if reward_ctx else await t(guild.id, "quests.checkin.default_daily_name")
        await interaction.followup.send(
            embed=discord.Embed(
                title=await t(guild.id, "quests.checkin.title"),
                description=await t(
                    guild.id,
                    "quests.checkin.claimed_description",
                    quest_title=checkin_title,
                    xp_shown=str(xp_shown),
                    xp_note=xp_note,
                ),
                color=QUEST_ACCENT_COLOR,
            ).set_footer(text=await t(guild.id, "quests.footer")),
            ephemeral=True,
        )

    @quests_admin_group.command(name="create", description="Create a quest with custom rules")
    @app_commands.choices(count_type=COUNT_TYPE_CHOICES)
    @app_commands.describe(
        name="Quest title shown on the board",
        description="Short explanation for members",
        goal="How many actions to complete the quest",
        count_type="What to track toward the goal",
        reward_xp="XP reward (requires leveling module)",
        window_minutes="Rolling time limit (e.g. 10 = complete within 10 minutes)",
        require_role="Member must have this role while progressing",
        require_role_name="Or match a role by name (e.g. Purple for color roles)",
        reward_role="Optional role granted on completion",
        channel="Limit progress to one channel (omit for all text channels)",
        message_id="For reaction quests: target message id",
        emoji="For reaction quests: emoji to react with",
        expires_days="Quest auto-expires after this many days (optional)",
    )
    async def admin_create(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str,
        goal: app_commands.Range[int, 1, 10_000],
        count_type: app_commands.Choice[str],
        reward_xp: app_commands.Range[int, 0, 100_000] = 0,
        window_minutes: Optional[int] = None,
        require_role: Optional[discord.Role] = None,
        require_role_name: Optional[str] = None,
        reward_role: Optional[discord.Role] = None,
        channel: Optional[discord.TextChannel] = None,
        message_id: Optional[str] = None,
        emoji: Optional[str] = None,
        expires_days: Optional[app_commands.Range[int, 1, 365]] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "quests"):
            await interaction.response.send_message(
                await t(interaction.user.id, "quests.admin.enable_module_first"),
                ephemeral=True,
            )
            return

        kind = count_type.value
        if kind == "reactions" and (not message_id or not emoji):
            await interaction.response.send_message(
                await t(interaction.user.id, "quests.admin.reaction_requires_fields"),
                ephemeral=True,
            )
            return

        qid = _new_quest_id(name)
        rules: dict[str, Any] = dict(DEFAULT_RULES)
        rules["count"] = kind if kind != "daily_checkin" else "messages"
        if window_minutes:
            rules["window_seconds"] = int(window_minutes) * 60
        if require_role is not None:
            rules["require_role_ids"] = [require_role.id]
        if require_role_name and require_role_name.strip():
            rules["require_role_names"] = [require_role_name.strip()]
        if channel is not None:
            rules["channel_ids"] = [channel.id]
        if message_id:
            rules["message_id"] = str(message_id).strip()
        if emoji:
            rules["emoji"] = str(emoji).strip()

        quest_type = "daily_checkin" if kind == "daily_checkin" else "custom"
        if kind == "reactions":
            quest_type = "custom"

        expires_at = None
        if expires_days:
            exp = _utc_now().timestamp() + int(expires_days) * 86400
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()

        entry: dict[str, Any] = {
            "id": qid,
            "name": name.strip()[:100],
            "description": description.strip()[:500],
            "type": quest_type,
            "goal": int(goal),
            "reward": int(reward_xp),
            "role_reward": reward_role.id if reward_role else None,
            "expires_at": expires_at,
            "enabled": True,
            "members": {},
        }
        if quest_type == "custom":
            entry["rules"] = rules
        elif quest_type == "daily_checkin":
            entry["rules"] = {}
        else:
            entry.update(
                {
                    "message_id": rules.get("message_id"),
                    "emoji": rules.get("emoji"),
                    "channel_ids": rules.get("channel_ids") or [],
                }
            )

        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(interaction.guild.id), {}) or {})
            guild_map[qid] = entry
            data[str(interaction.guild.id)] = guild_map
            _save_quests(data)

        summary = _rules_summary(interaction.guild.id, entry)
        emb = discord.Embed(
            title=await t(interaction.user.id, "quests.admin.created_title"),
            description=(
                f"**{entry['name']}** (`{qid}`)\n"
                f"{await t(interaction.user.id, 'quests.admin.goal_line', goal=str(goal), summary=summary)}\n"
                f"{await t(interaction.user.id, 'quests.admin.reward_line', reward_xp=str(reward_xp))}"
                + (f" + {reward_role.mention}" if reward_role else "")
            ),
            color=QUEST_ACCENT_COLOR,
        )
        emb.set_footer(text=await t(interaction.user.id, "quests.footer"))
        await interaction.response.send_message(embed=emb, ephemeral=True)

        await _dispatch_module_event(
            self.bot,
            interaction.guild,
            "quests",
            "quest_created",
            actor=log_actor_from_interaction(interaction),
            details=f"id={qid}; type={quest_type}; goal={goal}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )

    @quests_admin_group.command(name="list", description="List all quests (including disabled/expired)")
    async def admin_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        gq = self._guild_quests_copy(interaction.guild.id)
        if not gq:
            await interaction.response.send_message(
                await t(interaction.user.id, "quests.admin.no_quests_configured"),
                ephemeral=True,
            )
            return
        lines: list[str] = []
        for qid, quest in sorted(gq.items(), key=lambda kv: str(kv[0])):
            if not isinstance(quest, dict):
                continue
            status: list[str] = []
            if not _quest_enabled(quest):
                status.append(await t(interaction.user.id, "common.disabled"))
            if _quest_expired(quest):
                status.append(await t(interaction.user.id, "quests.admin.expired"))
            flag = f" ({', '.join(status)})" if status else ""
            lines.append(
                f"`{qid}` — **{quest.get('name', '?')}**{flag}\n"
                f"└ {_rules_summary(interaction.guild.id, quest)} • "
                f"{await t(interaction.user.id, 'quests.admin.goal_label', goal=str(quest.get('goal', '?')))}"
            )
        desc = "\n\n".join(lines)
        if len(desc) > 3800:
            desc = desc[:3797] + "…"
        await interaction.response.send_message(
            embed=discord.Embed(
                title=await t(interaction.user.id, "quests.admin.list_title"),
                description=desc,
                color=QUEST_EMBED_COLOR,
            ).set_footer(
                text=await t(interaction.user.id, "quests.footer")
            ),
            ephemeral=True,
        )

    @quests_admin_group.command(name="delete", description="Delete a quest by id")
    async def admin_delete(self, interaction: discord.Interaction, quest_id: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        qid = quest_id.strip()
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(interaction.guild.id), {}) or {})
            if qid not in guild_map:
                await interaction.response.send_message(
                    await t(interaction.user.id, "quests.admin.quest_not_found", quest_id=qid),
                    ephemeral=True,
                )
                return
            removed = guild_map.pop(qid)
            data[str(interaction.guild.id)] = guild_map
            _save_quests(data)
        await interaction.response.send_message(
            await t(
                interaction.guild.id,
                "quests.admin.deleted",
                quest_name=str(removed.get("name", qid)),
                quest_id=qid,
            ),
            ephemeral=True,
        )

    @quests_admin_group.command(name="toggle", description="Enable or disable a quest")
    async def admin_toggle(self, interaction: discord.Interaction, quest_id: str, enabled: bool) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        qid = quest_id.strip()
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(interaction.guild.id), {}) or {})
            quest = guild_map.get(qid)
            if not isinstance(quest, dict):
                await interaction.response.send_message(
                    await t(interaction.user.id, "quests.admin.quest_not_found", quest_id=qid),
                    ephemeral=True,
                )
                return
            quest = dict(quest)
            quest["enabled"] = enabled
            guild_map[qid] = quest
            data[str(interaction.guild.id)] = guild_map
            _save_quests(data)
        state = await t(
            interaction.guild.id,
            "common.enabled" if enabled else "common.disabled",
        )
        await interaction.response.send_message(
            await t(
                interaction.guild.id,
                "quests.admin.toggled",
                quest_name=str(quest.get("name", qid)),
                state=state,
            ),
            ephemeral=True,
        )

    @quests_admin_group.command(name="reset", description="Reset progress for a quest (all members or one user)")
    @app_commands.describe(
        quest_id="Quest id from /quests admin list",
        member="Reset only this member (omit to reset everyone)",
    )
    async def admin_reset(
        self,
        interaction: discord.Interaction,
        quest_id: str,
        member: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(None, "quests.messages.use_in_server"), ephemeral=True)
            return
        qid = quest_id.strip()
        async with QUESTS_LOCK:
            data = _load_quests()
            guild_map = dict(data.get(str(interaction.guild.id), {}) or {})
            quest = guild_map.get(qid)
            if not isinstance(quest, dict):
                await interaction.response.send_message(
                    await t(interaction.user.id, "quests.admin.quest_not_found", quest_id=qid),
                    ephemeral=True,
                )
                return
            quest = dict(quest)
            members = dict(quest.get("members") or {})
            if member is None:
                quest["members"] = {}
            else:
                members.pop(str(member.id), None)
                quest["members"] = members
            guild_map[qid] = quest
            data[str(interaction.guild.id)] = guild_map
            _save_quests(data)
        target = member.mention if member else await t(interaction.user.id, "quests.admin.all_members")
        await interaction.response.send_message(
            await t(
                interaction.guild.id,
                "quests.admin.reset_progress",
                quest_name=str(quest.get("name", qid)),
                target=target,
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not await is_module_enabled(guild.id, "quests"):
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._progress_tracked_quests_reaction(guild, member, channel, payload)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not await is_module_enabled(message.guild.id, "quests"):
            return
        author = message.author
        member = author if isinstance(author, discord.Member) else message.guild.get_member(author.id)
        if member is None or member.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        await self._progress_tracked_quests_message(message.guild, member, message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuestsCog(bot))
