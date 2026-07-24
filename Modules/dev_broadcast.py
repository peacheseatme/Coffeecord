"""Owner-only broadcast helpers (dev prefix commands)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import discord
from discord.ext import commands

from Modules.i18n import SUPPORTED_LOCALES, t_for_locale

ROOT_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = ROOT_DIR / "Storage"
DM_OPTOUT_FILE = STORAGE_DIR / "Data" / "dm_optout.json"
LANGUAGE_ANNOUNCEMENT_SENT_FILE = STORAGE_DIR / "Data" / "language_announcement_sent.json"

ANNOUNCE_DM_DELAY_SECONDS = 1.1
ANNOUNCE_PROGRESS_EVERY = 50
ANNOUNCE_I18N_PREFIX = "announcement.language_support."

_LANGUAGE_LABELS = {
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "ru": "Русский",
}
_LANGUAGE_FLAGS = {
    "en": "🇬🇧",
    "es": "🇪🇸",
    "pt": "🇵🇹",
    "ru": "🇷🇺",
}


def _load_json_ids(path: Path, key: str = "user_ids") -> set[int]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    raw = data.get(key, [])
    if not isinstance(raw, list):
        return set()
    out: set[int] = set()
    for value in raw:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _save_json_ids(path: Path, ids: set[int], *, extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"user_ids": sorted(ids)}
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _announce_text(key: str, *, default: str) -> str:
    return t_for_locale("en", f"{ANNOUNCE_I18N_PREFIX}{key}", default=default)


def build_language_support_announcement() -> str:
    """Single DM body with the announcement in every supported language."""
    sections: list[str] = []
    for locale in SUPPORTED_LOCALES:
        body = t_for_locale(
            locale,
            f"{ANNOUNCE_I18N_PREFIX}body",
            default=_announce_text(
                "body",
                default=(
                    "Coffeecord now has support for Spanish, Portuguese, and Russian! "
                    "To set your language use `/language set`. "
                    "We will not message you for feature updates in the future unless you enable it."
                ),
            ),
        )
        optout = t_for_locale(
            locale,
            f"{ANNOUNCE_I18N_PREFIX}optout_note",
            default=_announce_text(
                "optout_note",
                default="Use `/optout` in any server with this bot to stop receiving these messages.",
            ),
        )
        label = _LANGUAGE_LABELS.get(locale, locale)
        flag = _LANGUAGE_FLAGS.get(locale, "")
        sections.append(f"{flag} **{label}**\n{body}\n\n_{optout}_")
    return "\n\n".join(sections)


async def collect_language_announcement_recipients(bot: commands.Bot) -> set[int]:
    """Unique non-bot member ids across all guilds the bot is in."""
    recipients: set[int] = set()
    for guild in bot.guilds:
        try:
            if not guild.chunked:
                await guild.chunk()
        except (discord.HTTPException, discord.Forbidden):
            pass
        for member in guild.members:
            if not member.bot:
                recipients.add(member.id)
    return recipients


def _filter_recipients(all_ids: set[int], *, skip_sent: bool) -> set[int]:
    optout = _load_json_ids(DM_OPTOUT_FILE)
    sent = _load_json_ids(LANGUAGE_ANNOUNCEMENT_SENT_FILE) if skip_sent else set()
    return {uid for uid in all_ids if uid not in optout and uid not in sent}


async def preview_language_announcement(bot: commands.Bot) -> dict[str, int]:
    all_ids = await collect_language_announcement_recipients(bot)
    optout = _load_json_ids(DM_OPTOUT_FILE)
    sent = _load_json_ids(LANGUAGE_ANNOUNCEMENT_SENT_FILE)
    pending = _filter_recipients(all_ids, skip_sent=True)
    return {
        "total_members": len(all_ids),
        "opted_out": len(all_ids & optout),
        "already_sent": len(all_ids & sent),
        "pending": len(pending),
    }


async def run_language_support_announcement(
    bot: commands.Bot,
    *,
    status_channel: discord.abc.Messageable | None = None,
) -> dict[str, int]:
    """DM pending recipients; returns send/skip/fail counts."""
    message = build_language_support_announcement()
    if len(message) > 2000:
        raise ValueError(f"Announcement exceeds Discord 2000-char limit ({len(message)} chars).")

    all_ids = await collect_language_announcement_recipients(bot)
    pending = sorted(_filter_recipients(all_ids, skip_sent=True))
    sent_ids = _load_json_ids(LANGUAGE_ANNOUNCEMENT_SENT_FILE)

    stats = {"sent": 0, "failed": 0, "skipped": len(all_ids) - len(pending), "pending": len(pending)}
    print(
        f"[ANNOUNCE] language_support: pending={stats['pending']} skipped={stats['skipped']}",
        flush=True,
    )

    for idx, user_id in enumerate(pending, 1):
        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            await user.send(message)
            sent_ids.add(user_id)
            stats["sent"] += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            stats["failed"] += 1
            print(f"[ANNOUNCE] failed uid={user_id}: {exc}", flush=True)
        except Exception as exc:
            stats["failed"] += 1
            print(f"[ANNOUNCE] error uid={user_id}: {exc}", flush=True)

        if idx % ANNOUNCE_PROGRESS_EVERY == 0:
            _save_json_ids(LANGUAGE_ANNOUNCEMENT_SENT_FILE, sent_ids)
            print(
                f"[ANNOUNCE] progress {idx}/{len(pending)} sent={stats['sent']} failed={stats['failed']}",
                flush=True,
            )
            if status_channel is not None:
                try:
                    await status_channel.send(
                        f"📨 Language announcement: {idx}/{len(pending)} processed "
                        f"({stats['sent']} sent, {stats['failed']} failed)."
                    )
                except discord.HTTPException:
                    pass

        await asyncio.sleep(ANNOUNCE_DM_DELAY_SECONDS)

    _save_json_ids(
        LANGUAGE_ANNOUNCEMENT_SENT_FILE,
        sent_ids,
        extra={"last_run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    )
    print(
        f"[ANNOUNCE] complete sent={stats['sent']} failed={stats['failed']} skipped={stats['skipped']}",
        flush=True,
    )
    return stats


def register_dev_broadcast_commands(dev_group: commands.Group) -> None:
    @dev_group.command(name="announce_languages", aliases=["announce_language"])
    @commands.is_owner()
    async def dev_announce_languages(ctx: commands.Context, action: str | None = None) -> None:
        """
        Broadcast multilingual language-support DMs to all members in bot guilds.
        Usage: `.dev announce_language` (preview) | `.dev announce_language send`
        """
        if action is None:
            stats = await preview_language_announcement(ctx.bot)
            preview = build_language_support_announcement()
            if len(preview) > 1900:
                preview = preview[:1900] + "\n…"
            await ctx.send(
                "Language announcement **preview** (dry run):\n"
                f"- Total unique members: **{stats['total_members']}**\n"
                f"- Opted out (`/optout`): **{stats['opted_out']}**\n"
                f"- Already sent: **{stats['already_sent']}**\n"
                f"- Would send now: **{stats['pending']}**\n\n"
                f"Run `.dev announce_language send` to DM pending users.\n\n"
                f"```\n{preview}\n```"
            )
            return

        if action.lower() != "send":
            await ctx.send("Usage: `.dev announce_language` or `.dev announce_language send`")
            return

        stats = await preview_language_announcement(ctx.bot)
        if stats["pending"] == 0:
            await ctx.send("No pending recipients (everyone was already sent, or all opted out).")
            return

        await ctx.send(
            f"Starting language announcement to **{stats['pending']}** users. "
            "Progress is logged to the bot console."
        )

        async def _run() -> None:
            try:
                result = await run_language_support_announcement(ctx.bot, status_channel=ctx.channel)
                await ctx.send(
                    "Language announcement finished.\n"
                    f"- Sent: **{result['sent']}**\n"
                    f"- Failed: **{result['failed']}**\n"
                    f"- Skipped (opt-out / already sent): **{result['skipped']}**"
                )
            except Exception as exc:
                await ctx.send(f"Announcement aborted: {exc}")
                print(f"[ANNOUNCE] aborted: {exc}", flush=True)

        asyncio.create_task(_run())
