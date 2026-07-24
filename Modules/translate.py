import asyncio
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from urllib.request import urlopen

import discord
from discord import app_commands
from discord.ext import commands

from . import anti_abuse
from .i18n import t
from .module_registry import is_module_enabled
from .log_actor import log_actor_from_interaction

try:
    from google.cloud import translate_v2 as google_translate  # type: ignore
except Exception:
    google_translate = None

try:
    from deep_translator import GoogleTranslator as _DeepGT  # type: ignore
    _deep_translator_available = True
except Exception:
    _DeepGT = None  # type: ignore
    _deep_translator_available = False


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "Storage" / "Config"
DATA_DIR = BASE_DIR / "Storage" / "Data"
USERS_FILE = CONFIG_DIR / "translate_users.json"
USAGE_FILE = CONFIG_DIR / "translate_usage.json"
SUPPORTERS_FILE = DATA_DIR / "supporters.json"
LEGACY_SUPPORTERS_FILE = BASE_DIR / "Main" / "Storage" / "Data" / "supporters.json"

FREE_LIMIT = 15
WINDOW_SECONDS = 24 * 60 * 60
MAX_TRANSLATE_LENGTH = 1500
CACHE_TTL_SECONDS = 300


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            return data
        return default
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


def _normalize_supporters_payload(raw: dict) -> dict:
    """Normalize legacy and current supporter schemas to a dict-based format."""
    if not isinstance(raw, dict):
        return {"supporters": {}, "unlinked_donations": []}

    supporters_raw = raw.get("supporters", {})
    normalized_supporters: dict[str, dict] = {}

    if isinstance(supporters_raw, dict):
        for key, record in supporters_raw.items():
            user_id = str(key).strip()
            if not user_id:
                continue
            if isinstance(record, dict):
                record_id = record.get("discord_id", user_id)
                parsed_id = int(str(record_id)) if str(record_id).isdigit() else user_id
                normalized_supporters[user_id] = {
                    "discord_id": parsed_id,
                    "active": bool(record.get("active", True)),
                    **record,
                }
            else:
                # Very old payloads may map ids to booleans/strings.
                normalized_supporters[user_id] = {
                    "discord_id": int(user_id) if user_id.isdigit() else user_id,
                    "active": bool(record),
                }
    elif isinstance(supporters_raw, list):
        # Legacy schema: "supporters": [123, "456", ...]
        for entry in supporters_raw:
            if isinstance(entry, dict):
                raw_id = entry.get("discord_id", entry.get("user_id", entry.get("id")))
                if raw_id is None:
                    continue
                user_id = str(raw_id).strip()
                if not user_id:
                    continue
                normalized_supporters[user_id] = {
                    "discord_id": int(user_id) if user_id.isdigit() else user_id,
                    "active": bool(entry.get("active", True)),
                    **entry,
                }
                continue
            user_id = str(entry).strip()
            if not user_id:
                continue
            normalized_supporters[user_id] = {
                "discord_id": int(user_id) if user_id.isdigit() else user_id,
                "active": True,
            }

    unlinked = raw.get("unlinked_donations", [])
    if not isinstance(unlinked, list):
        unlinked = []

    return {"supporters": normalized_supporters, "unlinked_donations": unlinked}


def _load_supporters_data() -> dict:
    primary = _normalize_supporters_payload(_load_json(SUPPORTERS_FILE, {"supporters": {}}))
    supporters = primary.get("supporters", {})
    if isinstance(supporters, dict) and supporters:
        return primary

    legacy = _normalize_supporters_payload(_load_json(LEGACY_SUPPORTERS_FILE, {"supporters": {}}))
    legacy_supporters = legacy.get("supporters", {})
    if isinstance(legacy_supporters, dict) and legacy_supporters:
        # Promote usable legacy data into the primary storage path once.
        _save_json(SUPPORTERS_FILE, legacy)
        return legacy

    return primary


def _contains_code_block(text: str) -> bool:
    return "```" in text


_DISCORD_MARKUP_RE = re.compile(
    r"<a?:[A-Za-z0-9_]+:\d+>"   # custom emoji  <:name:id> or <a:name:id>
    r"|<[@#!&]\d+>"              # user/role/channel mentions
    r"|<@!\d+>"                  # nickname mentions (redundant but safe)
)


def _strip_discord_markup(text: str) -> str:
    """Remove Discord mention/emoji tokens so the translator only sees plain prose."""
    return _DISCORD_MARKUP_RE.sub("", text).strip()


def _normalize_lang(value: Optional[str], default: str = "auto") -> str:
    if not value:
        return default
    return value.strip().lower()


@dataclass
class TranslateResult:
    translated_text: str
    detected_source_language: str


class GoogleTranslateBackend:
    def __init__(self) -> None:
        self._client = None
        if google_translate is not None:
            try:
                self._client = google_translate.Client()
            except Exception:
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def translate_sync(self, text: str, source_lang: str, target_lang: str) -> TranslateResult:
        if self._client is None:
            raise RuntimeError("Google Translate backend is not configured.")
        payload = self._client.translate(
            text,
            source_language=None if source_lang == "auto" else source_lang,
            target_language=target_lang,
            format_="text",
        )
        translated = html.unescape(str(payload.get("translatedText", "")))
        detected = str(payload.get("detectedSourceLanguage", source_lang if source_lang != "auto" else "unknown"))
        return TranslateResult(translated_text=translated, detected_source_language=detected)


class DeepTranslatorBackend:
    """Free backend using deep-translator (no credentials needed)."""

    @property
    def available(self) -> bool:
        return _deep_translator_available

    def _detect_source_language_sync(self, text: str) -> str:
        sample = (text or "").strip()
        if not sample:
            return "unknown"
        # Best-effort detection using Google's public endpoint.
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=en&dt=t&q={quote(sample[:500])}"
        )
        try:
            with urlopen(url, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            detected = payload[2] if isinstance(payload, list) and len(payload) > 2 else None
            return str(detected) if isinstance(detected, str) and detected else "unknown"
        except Exception:
            return "unknown"

    def translate_sync(self, text: str, source_lang: str, target_lang: str) -> TranslateResult:
        if _DeepGT is None:
            raise RuntimeError("deep-translator is not installed.")
        src = "auto" if source_lang in ("auto", "") else source_lang
        translator = _DeepGT(source=src, target=target_lang)
        translated = translator.translate(text)
        translated_str = str(translated) if translated is not None else text
        detected = source_lang if source_lang != "auto" else self._detect_source_language_sync(text)
        return TranslateResult(translated_text=translated_str, detected_source_language=detected)


class TranslateCog(
    commands.GroupCog,
    group_name="translate",
    group_description="Translate text and manage translation settings.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        gcloud = GoogleTranslateBackend()
        if gcloud.available:
            self.backend = gcloud
        else:
            self.backend = DeepTranslatorBackend()
        self.user_settings = _load_json(USERS_FILE, {})
        self.usage_data = _load_json(USAGE_FILE, {})
        self.translation_cache: dict[str, tuple[float, TranslateResult]] = {}
        self.message_cache: dict[tuple[int, int, str], float] = {}
        self.in_flight: set[tuple[int, int, str]] = set()

    def _dispatch_module_event(
        self,
        guild: Optional[discord.Guild],
        action: str,
        actor: Optional[discord.abc.User] = None,
        details: str = "",
        channel_id: Optional[int] = None,
    ) -> None:
        if guild is None:
            return
        try:
            self.bot.dispatch("coffeecord_module_event", guild, "translation", action, actor, details, channel_id)
        except Exception:
            return

    # ---------- Backend wrapper ----------
    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        result = await self._translate_with_details(text, source_lang, target_lang)
        return result.translated_text

    async def _translate_with_details(self, text: str, source_lang: str, target_lang: str) -> TranslateResult:
        source_lang = _normalize_lang(source_lang, "auto")
        target_lang = _normalize_lang(target_lang, "en")
        cache_key = f"{source_lang}|{target_lang}|{text}"
        now = time.time()
        cached = self.translation_cache.get(cache_key)
        if cached and (now - cached[0]) <= CACHE_TTL_SECONDS:
            return cached[1]

        result = await asyncio.to_thread(self.backend.translate_sync, text, source_lang, target_lang)
        self.translation_cache[cache_key] = (now, result)
        return result

    # ---------- Settings / usage ----------
    def _get_user_settings(self, user_id: int) -> dict:
        key = str(user_id)
        settings = self.user_settings.get(key)
        if not isinstance(settings, dict):
            settings = {
                "language": "en",
                "live_translate": False,
                "ephemeral": True,
                "dm_delivery": False,
            }
            self.user_settings[key] = settings
            _save_json(USERS_FILE, self.user_settings)
        return settings

    def _is_supporter(self, user_id: int) -> bool:
        data = _load_supporters_data()
        supporters = data.get("supporters", {})
        if isinstance(supporters, list):
            target = str(user_id)
            return any(str(item) == target for item in supporters)
        if not isinstance(supporters, dict):
            return False
        record = supporters.get(str(user_id))
        if isinstance(record, dict):
            return bool(record.get("active", False))
        # Old edge case: id -> bool/string instead of nested record
        return bool(record)

    def _check_and_increment_usage(self, user_id: int) -> tuple[bool, int, int]:
        if self._is_supporter(user_id):
            return True, -1, int(time.time() + WINDOW_SECONDS)

        now = int(time.time())
        key = str(user_id)
        row = self.usage_data.get(key, {})
        count = int(row.get("count", 0))
        reset_at = int(row.get("reset_at", 0))
        notice_sent = bool(row.get("notice_sent", False))

        if reset_at <= now:
            count = 0
            reset_at = now + WINDOW_SECONDS
            notice_sent = False

        if count >= FREE_LIMIT:
            self.usage_data[key] = {"count": count, "reset_at": reset_at, "notice_sent": notice_sent}
            _save_json(USAGE_FILE, self.usage_data)
            return False, max(FREE_LIMIT - count, 0), reset_at

        count += 1
        self.usage_data[key] = {"count": count, "reset_at": reset_at, "notice_sent": notice_sent}
        _save_json(USAGE_FILE, self.usage_data)
        return True, max(FREE_LIMIT - count, 0), reset_at

    def _mark_limit_notice_sent(self, user_id: int) -> bool:
        key = str(user_id)
        row = self.usage_data.get(key, {})
        if bool(row.get("notice_sent", False)):
            return False
        row["notice_sent"] = True
        self.usage_data[key] = row
        _save_json(USAGE_FILE, self.usage_data)
        return True

    # ---------- Commands ----------
    @app_commands.command(name="text", description="Translate text into another language.")
    @app_commands.checks.cooldown(5, 60.0, key=lambda i: i.user.id)
    @app_commands.describe(
        translate_to="Language to translate into.",
        text="The text you want to translate.",
        translate_from="Language the text is written in. Leave blank to auto-detect.",
        show_only_to_me="Only show the result to you (default: yes).",
    )
    @app_commands.choices(
        translate_to=[
            app_commands.Choice(name="English",            value="en"),
            app_commands.Choice(name="Spanish",            value="es"),
            app_commands.Choice(name="French",             value="fr"),
            app_commands.Choice(name="German",             value="de"),
            app_commands.Choice(name="Italian",            value="it"),
            app_commands.Choice(name="Portuguese",         value="pt"),
            app_commands.Choice(name="Dutch",              value="nl"),
            app_commands.Choice(name="Russian",            value="ru"),
            app_commands.Choice(name="Chinese (Simplified)", value="zh-CN"),
            app_commands.Choice(name="Chinese (Traditional)", value="zh-TW"),
            app_commands.Choice(name="Japanese",           value="ja"),
            app_commands.Choice(name="Korean",             value="ko"),
            app_commands.Choice(name="Arabic",             value="ar"),
            app_commands.Choice(name="Hindi",              value="hi"),
            app_commands.Choice(name="Turkish",            value="tr"),
            app_commands.Choice(name="Polish",             value="pl"),
            app_commands.Choice(name="Swedish",            value="sv"),
            app_commands.Choice(name="Norwegian",          value="no"),
            app_commands.Choice(name="Danish",             value="da"),
            app_commands.Choice(name="Finnish",            value="fi"),
            app_commands.Choice(name="Greek",              value="el"),
            app_commands.Choice(name="Hebrew",             value="he"),
            app_commands.Choice(name="Thai",               value="th"),
            app_commands.Choice(name="Vietnamese",         value="vi"),
            app_commands.Choice(name="Ukrainian",          value="uk"),
        ],
        translate_from=[
            app_commands.Choice(name="Auto-detect",          value="auto"),
            app_commands.Choice(name="English",              value="en"),
            app_commands.Choice(name="Spanish",              value="es"),
            app_commands.Choice(name="French",               value="fr"),
            app_commands.Choice(name="German",               value="de"),
            app_commands.Choice(name="Italian",              value="it"),
            app_commands.Choice(name="Portuguese",           value="pt"),
            app_commands.Choice(name="Dutch",                value="nl"),
            app_commands.Choice(name="Russian",              value="ru"),
            app_commands.Choice(name="Chinese (Simplified)", value="zh-CN"),
            app_commands.Choice(name="Chinese (Traditional)", value="zh-TW"),
            app_commands.Choice(name="Japanese",             value="ja"),
            app_commands.Choice(name="Korean",               value="ko"),
            app_commands.Choice(name="Arabic",               value="ar"),
            app_commands.Choice(name="Hindi",                value="hi"),
            app_commands.Choice(name="Turkish",              value="tr"),
            app_commands.Choice(name="Polish",               value="pl"),
            app_commands.Choice(name="Swedish",              value="sv"),
            app_commands.Choice(name="Norwegian",            value="no"),
            app_commands.Choice(name="Danish",               value="da"),
            app_commands.Choice(name="Finnish",              value="fi"),
            app_commands.Choice(name="Greek",                value="el"),
            app_commands.Choice(name="Hebrew",               value="he"),
            app_commands.Choice(name="Vietnamese",           value="vi"),
            app_commands.Choice(name="Ukrainian",            value="uk"),
        ],
    )
    async def translate_command(
        self,
        interaction: discord.Interaction,
        translate_to: app_commands.Choice[str],
        text: Optional[str] = None,
        translate_from: Optional[app_commands.Choice[str]] = None,
        show_only_to_me: bool = True,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "translate"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        # Map the Choice objects to plain ISO codes
        target_language = translate_to.value
        source_language = translate_from.value if translate_from is not None else "auto"
        ephemeral = show_only_to_me

        translate_text = _strip_discord_markup(text.strip()) if text else ""
        if not translate_text:
            try:
                resolved = (interaction.data or {}).get("resolved", {})  # type: ignore[union-attr]
                resolved_messages = resolved.get("messages", {})
                if isinstance(resolved_messages, dict) and resolved_messages:
                    first_message = next(iter(resolved_messages.values()))
                    if isinstance(first_message, dict):
                        raw = str(first_message.get("content", "")).strip()
                        translate_text = _strip_discord_markup(raw)
            except Exception:
                translate_text = ""
        if not translate_text:
            await interaction.response.send_message(
                await t(interaction.user.id, "translate.messages.provide_text"),
                ephemeral=True,
            )
            return

        if len(translate_text) > MAX_TRANSLATE_LENGTH:
            await interaction.response.send_message(
                await t(
                    interaction.guild.id,
                    "translate.messages.too_long",
                    max_length=str(MAX_TRANSLATE_LENGTH),
                ),
                ephemeral=True,
            )
            return

        if _contains_code_block(translate_text):
            await interaction.response.send_message(
                await t(interaction.user.id, "translate.messages.code_block_skipped"),
                ephemeral=True,
            )
            return

        allowed, _, reset_at = self._check_and_increment_usage(interaction.user.id)
        if not allowed:
            reset_ts = f"<t:{reset_at}:R>"
            await interaction.response.send_message(
                await t(
                    interaction.guild.id,
                    "translate.messages.free_limit_hit",
                    free_limit=str(FREE_LIMIT),
                    reset_ts=reset_ts,
                ),
                ephemeral=True,
            )
            return

        if not self.backend.available:
            await interaction.response.send_message(
                await t(interaction.user.id, "translate.messages.backend_unavailable"),
                ephemeral=True,
            )
            return

        try:
            async with anti_abuse.heavy_task_slot():
                result = await self._translate_with_details(translate_text, source_language, target_language)
        except Exception:
            await interaction.response.send_message(
                await t(interaction.user.id, "translate.messages.failed"),
                ephemeral=True,
            )
            return

        source_label = (
            translate_from.name
            if translate_from is not None
            else await t(
                interaction.guild.id,
                "translate.embed.auto_detected",
                language=result.detected_source_language,
            )
        )
        embed = discord.Embed(
            title=await t(interaction.user.id, "translate.embed.translation_title"),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=await t(interaction.user.id, "translate.embed.translate_from"),
            value=source_label,
            inline=True,
        )
        embed.add_field(
            name=await t(interaction.user.id, "translate.embed.translate_to"),
            value=translate_to.name,
            inline=True,
        )
        embed.add_field(
            name=await t(interaction.user.id, "translate.embed.result"),
            value=result.translated_text[:4000],
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        self._dispatch_module_event(
            interaction.guild,
            "manual_translate",
            actor=log_actor_from_interaction(interaction),
            details=(
                f"source={result.detected_source_language if source_language == 'auto' else source_language}; "
                f"target={target_language}; chars={len(translate_text)}; ephemeral={ephemeral}"
            ),
            channel_id=interaction.channel.id if interaction.channel else None,
        )

    @app_commands.command(name="settings", description="Configure your live translation preferences.")
    @app_commands.describe(
        preferred_language="Your default language for live translations.",
        live_translate="Enable or disable automatic live translation.",
        ephemeral="If on, slash command results are only visible to you.",
        dm_delivery="Send live translations to your DMs instead of replying in channel.",
    )
    @app_commands.choices(
        preferred_language=[
            app_commands.Choice(name="English",              value="en"),
            app_commands.Choice(name="Spanish",              value="es"),
            app_commands.Choice(name="French",               value="fr"),
            app_commands.Choice(name="German",               value="de"),
            app_commands.Choice(name="Italian",              value="it"),
            app_commands.Choice(name="Portuguese",           value="pt"),
            app_commands.Choice(name="Dutch",                value="nl"),
            app_commands.Choice(name="Russian",              value="ru"),
            app_commands.Choice(name="Chinese (Simplified)", value="zh-CN"),
            app_commands.Choice(name="Chinese (Traditional)", value="zh-TW"),
            app_commands.Choice(name="Japanese",             value="ja"),
            app_commands.Choice(name="Korean",               value="ko"),
            app_commands.Choice(name="Arabic",               value="ar"),
            app_commands.Choice(name="Hindi",                value="hi"),
            app_commands.Choice(name="Turkish",              value="tr"),
            app_commands.Choice(name="Polish",               value="pl"),
            app_commands.Choice(name="Swedish",              value="sv"),
            app_commands.Choice(name="Norwegian",            value="no"),
            app_commands.Choice(name="Danish",               value="da"),
            app_commands.Choice(name="Finnish",              value="fi"),
            app_commands.Choice(name="Greek",                value="el"),
            app_commands.Choice(name="Hebrew",               value="he"),
            app_commands.Choice(name="Thai",                 value="th"),
            app_commands.Choice(name="Vietnamese",           value="vi"),
            app_commands.Choice(name="Ukrainian",            value="uk"),
        ],
    )
    async def translate_settings(
        self,
        interaction: discord.Interaction,
        preferred_language: Optional[app_commands.Choice[str]] = None,
        live_translate: Optional[bool] = None,
        ephemeral: Optional[bool] = None,
        dm_delivery: Optional[bool] = None,
    ) -> None:
        if interaction.guild is not None and not await is_module_enabled(interaction.guild.id, "translate"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        settings = self._get_user_settings(interaction.user.id)
        if preferred_language is not None:
            settings["language"] = preferred_language.value
        if live_translate is not None:
            settings["live_translate"] = live_translate
        if ephemeral is not None:
            settings["ephemeral"] = ephemeral
        if dm_delivery is not None:
            settings["dm_delivery"] = dm_delivery
        self.user_settings[str(interaction.user.id)] = settings
        _save_json(USERS_FILE, self.user_settings)

        lang_display = preferred_language.name if preferred_language is not None else settings["language"]
        guild_id = interaction.user.id
        live_status = await t(interaction.user.id, "common.enabled" if settings["live_translate"] else "common.disabled")
        dm_status = await t(interaction.user.id, "translate.settings.on" if settings["dm_delivery"] else "translate.settings.off")
        priv_status = await t(
            guild_id,
            "translate.settings.private" if settings["ephemeral"] else "translate.settings.public",
        )
        embed = discord.Embed(
            title=await t(interaction.user.id, "translate.settings.title"),
            color=discord.Color.green(),
        )
        embed.add_field(name=await t(interaction.user.id, "translate.settings.my_language"), value=lang_display, inline=True)
        embed.add_field(name=await t(interaction.user.id, "translate.settings.live_translation"), value=live_status, inline=True)
        embed.add_field(name=await t(interaction.user.id, "translate.settings.response_privacy"), value=priv_status, inline=True)
        embed.add_field(name=await t(interaction.user.id, "translate.settings.send_to_dm"), value=dm_status, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self._dispatch_module_event(
            interaction.guild,
            "settings_update",
            actor=log_actor_from_interaction(interaction),
            details=(
                f"language={settings.get('language', 'en')}; "
                f"live_translate={bool(settings.get('live_translate', False))}; "
                f"ephemeral={bool(settings.get('ephemeral', True))}; "
                f"dm_delivery={bool(settings.get('dm_delivery', False))}"
            ),
            channel_id=interaction.channel.id if interaction.channel else None,
        )

    @app_commands.command(name="usage", description="See your translation usage limits.")
    async def translate_usage(self, interaction: discord.Interaction) -> None:
        if interaction.guild is not None and not await is_module_enabled(interaction.guild.id, "translate"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        if self._is_supporter(interaction.user.id):
            await interaction.response.send_message(
                await t(interaction.user.id, "translate.usage.supporter_unlimited"),
                ephemeral=True,
            )
            return

        now = int(time.time())
        row = self.usage_data.get(str(interaction.user.id), {})
        count = int(row.get("count", 0))
        reset_at = int(row.get("reset_at", now + WINDOW_SECONDS))
        if reset_at <= now:
            count = 0
            reset_at = now + WINDOW_SECONDS

        await interaction.response.send_message(
            await t(
                interaction.user.id,
                "translate.usage.free_plan",
                count=str(count),
                free_limit=str(FREE_LIMIT),
                reset_at=str(reset_at),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="reset", description="Reset a user's translation usage (admin only).")
    async def translate_reset_user(self, interaction: discord.Interaction, user: discord.User) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(await t(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        if not await is_module_enabled(interaction.guild.id, "translate"):
            await interaction.response.send_message(
                await t(interaction.user.id, "common.module_disabled"),
                ephemeral=True,
            )
            return
        self.usage_data.pop(str(user.id), None)
        _save_json(USAGE_FILE, self.usage_data)
        await interaction.response.send_message(
            await t(interaction.user.id, "translate.messages.reset_usage", user=user.mention),
            ephemeral=True,
        )
        self._dispatch_module_event(
            interaction.guild,
            "usage_reset",
            actor=log_actor_from_interaction(interaction),
            details=f"target_user_id={user.id}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )

    # ---------- Live translate listener ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if len(message.content or "") == 0:
            return
        if len(message.content) > MAX_TRANSLATE_LENGTH:
            return
        if _contains_code_block(message.content):
            return
        if not self.backend.available:
            return

        if isinstance(message.channel, discord.DMChannel):
            await self._handle_dm_message(message)
            return

        if message.guild is None:
            return

        if not await is_module_enabled(message.guild.id, "translate"):
            return

        target_users = await self._collect_relevant_users(message)
        if not target_users:
            return

        for target in target_users:
            await self._translate_for_target(message, target)

    async def _handle_dm_message(self, message: discord.Message) -> None:
        settings = self._get_user_settings(message.author.id)
        if not settings.get("live_translate", False):
            return
        await self._translate_for_target(message, message.author)

    async def _collect_relevant_users(self, message: discord.Message) -> list[discord.Member]:
        candidates: dict[int, discord.Member] = {}

        for mentioned in message.mentions:
            if isinstance(mentioned, discord.Member):
                candidates[mentioned.id] = mentioned

        if message.reference and message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            ref_author = message.reference.resolved.author
            if isinstance(ref_author, discord.Member):
                candidates[ref_author.id] = ref_author

        lowered = _strip_discord_markup(message.content).lower()
        for user_id, settings in self.user_settings.items():
            if not isinstance(settings, dict) or not settings.get("live_translate", False):
                continue
            member = message.guild.get_member(int(user_id))
            if member is None:
                continue
            if member.bot:
                continue
            names = {member.name.lower()}
            if member.nick:
                names.add(member.nick.lower())
            for name in names:
                if not name:
                    continue
                if re.search(rf"\b{re.escape(name)}\b", lowered):
                    candidates[member.id] = member
                    break

        # Never auto-translate back to the sender
        candidates.pop(message.author.id, None)
        return list(candidates.values())

    async def _translate_for_target(self, message: discord.Message, target_user: discord.abc.User) -> None:
        settings = self._get_user_settings(target_user.id)
        if not settings.get("live_translate", False):
            return

        guild_id = message.guild.id if message.guild else None
        language = _normalize_lang(settings.get("language"), "en")
        msg_key = (message.id, target_user.id, language)
        now = time.time()

        cached_at = self.message_cache.get(msg_key)
        if cached_at and (now - cached_at) <= CACHE_TTL_SECONDS:
            return
        if msg_key in self.in_flight:
            return

        allowed, _, reset_at = self._check_and_increment_usage(target_user.id)
        if not allowed:
            if self._mark_limit_notice_sent(target_user.id):
                try:
                    await target_user.send(
                        await t(
                            guild_id,
                            "translate.messages.limit_notice_dm",
                            free_limit=str(FREE_LIMIT),
                            reset_at=str(reset_at),
                        )
                    )
                except discord.HTTPException:
                    pass
            return

        clean_content = _strip_discord_markup(message.content)
        if not clean_content:
            return

        self.in_flight.add(msg_key)
        try:
            result = await self._translate_with_details(clean_content, "auto", language)
        except Exception:
            return
        finally:
            self.in_flight.discard(msg_key)

        self.message_cache[msg_key] = now

        translated_text = result.translated_text.strip()
        if not translated_text or translated_text == clean_content:
            return

        embed = discord.Embed(
            title=await t(interaction.user.id, "translate.embed.live_translation_title"),
            description=translated_text[:4000],
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=await t(
                guild_id,
                "translate.embed.detected_footer",
                source_language=result.detected_source_language,
                target_language=language,
            )
        )

        if settings.get("dm_delivery", False):
            try:
                await target_user.send(embed=embed)
                self._dispatch_module_event(
                    message.guild,
                    "live_translate_dm",
                    actor=message.author,
                    details=(
                        f"target_user_id={target_user.id}; detected={result.detected_source_language}; "
                        f"target_lang={language}; source_message_id={message.id}"
                    ),
                    channel_id=message.channel.id if message.channel else None,
                )
            except discord.HTTPException:
                pass
            return

        try:
            await message.reply(content=target_user.mention, embed=embed, mention_author=False)
            self._dispatch_module_event(
                message.guild,
                "live_translate_reply",
                actor=message.author,
                details=(
                    f"target_user_id={target_user.id}; detected={result.detected_source_language}; "
                    f"target_lang={language}; source_message_id={message.id}"
                ),
                channel_id=message.channel.id if message.channel else None,
            )
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    cog = TranslateCog(bot)
    await bot.add_cog(cog)
