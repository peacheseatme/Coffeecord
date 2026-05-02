import discord
from discord.ext import commands
import logging
from logging.handlers import RotatingFileHandler
from discord.ext import commands, tasks
import os
import sys
from dotenv import load_dotenv
import asyncio
import importlib
from datetime import timedelta
from discord.ext import commands
from discord.ui import View, Button
from discord import ButtonStyle
import random
import json
from discord import ButtonStyle, Interaction
from datetime import datetime, timedelta
from discord import app_commands
from discord.ui import Select, View, Button
import io
from discord import File
from io import BytesIO
import tempfile
from discord import Interaction, SelectOption
import hashlib
from datetime import datetime, timedelta
import typing
from discord import Member
from typing import List
import re
import secrets
import time
import traceback
import urllib.parse
from contextlib import asynccontextmanager

import aiohttp

# Cache for "Show technical details" button; key -> technical_details str (cleaned periodically)
_error_details_cache: dict[str, str] = {}
_ERROR_DETAILS_PREFIX = "err:"
_REDACTED_DISCORD_TOKEN_MARKER = "[REDACTED_DISCORD_TOKEN]"

# install.sh writes Src/.env; c-cord runs with cwd=project root, so bare load_dotenv() never sees Src/.env.
_COFFEECORD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PROJECT = os.path.join(_COFFEECORD_ROOT, ".env")
_ENV_SRC = os.path.join(_COFFEECORD_ROOT, "Src", ".env")
_TICKET_ENV_PATH = os.path.join(_COFFEECORD_ROOT, "Src", "ticket.env")
load_dotenv(_ENV_PROJECT)
load_dotenv(_ENV_SRC, override=True)
load_dotenv(_TICKET_ENV_PATH, override=True)
token = os.getenv("DISCORD_TOKEN")


_BRANDING_CONFIG_PATH = os.path.join(
    _COFFEECORD_ROOT, "Storage", "Config", "bot_branding.json"
)


def _load_bot_branding_config() -> dict:
    if not os.path.isfile(_BRANDING_CONFIG_PATH):
        return {}
    try:
        with open(_BRANDING_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_BOT_BRANDING = _load_bot_branding_config()


def _env_or_branding_str(env_key: str, branding_key: str, default: str) -> str:
    v = (os.getenv(env_key) or "").strip()
    if v:
        return v
    raw = _BOT_BRANDING.get(branding_key)
    if raw is not None:
        s = str(raw).strip()
        if s:
            return s
    return default


def _env_or_branding_app_id() -> str:
    for env_key in ("DISCORD_APPLICATION_ID", "DISCORD_CLIENT_ID"):
        v = (os.getenv(env_key) or "").strip()
        if v:
            return v
    raw = _BOT_BRANDING.get("discord_application_id")
    if raw is not None:
        t = str(raw).strip()
        if t:
            return t
    return ""


def _env_or_branding_owner_id() -> int:
    raw_env = os.getenv("COFFEECORD_OWNER_ID", "").strip()
    if raw_env:
        try:
            return int(raw_env)
        except ValueError:
            return 0
    v = _BOT_BRANDING.get("owner_id")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return 0


_INVITE_PERMISSIONS = (os.getenv("DISCORD_INVITE_PERMISSIONS") or "8866461766385655").strip()
_DISCORD_APP_ID = _env_or_branding_app_id()


def _oauth_invite_url(application_id: str) -> str:
    inner = f"https://discord.com/oauth2/authorize?client_id={application_id}"
    redirect = urllib.parse.quote(inner, safe="")
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&permissions={_INVITE_PERMISSIONS}"
        "&response_type=code"
        f"&redirect_uri={redirect}"
        "&integration_type=0"
        "&scope=bot+identify+applications.commands+applications.commands.permissions.update"
    )


def _redact_discord_token_in_text(text: str) -> str:
    """Strip DISCORD_TOKEN from user-visible error strings if it ever appears."""
    if not text:
        return text
    tok = (os.getenv("DISCORD_TOKEN") or "").strip()
    if len(tok) < 8:
        return text
    return text.replace(tok, _REDACTED_DISCORD_TOKEN_MARKER)


try:
    import psutil as _psutil

    _PSUTIL_PROCESS = _psutil.Process()
    _PSUTIL_PROCESS.cpu_percent(interval=None)
except Exception:
    _psutil = None
    _PSUTIL_PROCESS = None

_BOT_START_MONO = time.monotonic()


def _print_redacted(*parts: object) -> None:
    print(_redact_discord_token_in_text(" ".join(str(p) for p in parts)))


def _print_traceback_redacted(exc: typing.Optional[BaseException] = None) -> None:
    if exc is not None:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        text = traceback.format_exc()
    print(_redact_discord_token_in_text(text), file=sys.stderr)


class _DiscordTokenRedactFilter(logging.Filter):
    """Redact DISCORD_TOKEN from formatted log lines (e.g. discord.py file handler)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = _redact_discord_token_in_text(msg)
            if red != msg:
                record.msg = red
                record.args = ()
        except Exception:
            pass
        return True


# ───────── staff applications – storage helpers ─────────
# Path setup (must be before Modules import)
_bot_dir = os.path.dirname(os.path.abspath(__file__))
_discordbot_root = _COFFEECORD_ROOT
_modules_path = os.path.join(_discordbot_root, "Modules")
_storage_dir = os.path.join(_discordbot_root, "Storage")
if _discordbot_root not in sys.path:
    sys.path.insert(0, _discordbot_root)
if _modules_path not in sys.path:
    sys.path.insert(0, _modules_path)

from Modules.bot_config import get_bot_version_display, get_discord_library_log_settings

_COFFEECORD_DISCORD_LOG_ATTR = "_coffeecord_discord_file_handler"


def _configure_discord_library_logging() -> None:
    """Send discord.py library logs to a rotating file (see Storage/Config/bot_sys.cfg; env DISCORD_LOG_* overrides)."""
    settings = get_discord_library_log_settings()
    if not settings.enabled:
        return
    log_dir = str(settings.path.parent)
    os.makedirs(log_dir, exist_ok=True)
    dlog = logging.getLogger("discord")
    dlog.setLevel(settings.level)
    for h in dlog.handlers:
        if getattr(h, _COFFEECORD_DISCORD_LOG_ATTR, False):
            return
    handler = RotatingFileHandler(
        settings.path,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
        encoding="utf-8",
    )
    setattr(handler, _COFFEECORD_DISCORD_LOG_ATTR, True)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    handler.addFilter(_DiscordTokenRedactFilter())
    dlog.addHandler(handler)


_configure_discord_library_logging()

from Modules import anti_abuse
from Modules import checks as module_checks
from Modules import command_perm_overrides
from Modules import json_cache
from Modules.themes import get_command_response, get_command_response_for_interaction


class CoffeecordCommandTree(app_commands.CommandTree):
    """Slash tree with anti-abuse + configurable per-guild permission checks."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if not await anti_abuse.should_allow_interaction(interaction):
            return False
        return await command_perm_overrides.tree_interaction_perm_check(interaction)


def load_json(path: str, default: dict | list | None = None):
    """Load JSON from cache or disk."""
    return json_cache.get(path, default if default is not None else {})

def save_json(path: str, data) -> None:
    """Write JSON to disk and update cache."""
    json_cache.set_(path, data)

STAFF_APP_FILE = os.path.join(_storage_dir, "Data", "staff_applications.json")
DM_OPTOUT_FILE = os.path.join(_storage_dir, "Data", "dm_optout.json")

# In‑memory cache of the staff‑application config for *all* guilds
staff_app_cfg: dict[str, dict] = load_json(STAFF_APP_FILE, {})


def _dm_optout_ids() -> set:
    """Load set of user IDs who opted out of receiving staff DMs."""
    data = load_json(DM_OPTOUT_FILE, {"user_ids": []})
    return set(str(x) for x in data.get("user_ids", []))


def _dm_optout_add(user_id: int) -> None:
    ids = _dm_optout_ids()
    ids.add(str(user_id))
    save_json(DM_OPTOUT_FILE, {"user_ids": list(ids)})


def _dm_optout_remove(user_id: int) -> None:
    ids = _dm_optout_ids()
    ids.discard(str(user_id))
    save_json(DM_OPTOUT_FILE, {"user_ids": list(ids)})


# ─────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True
intents.members = True
ENABLE_PRESENCE_INTENT = os.getenv("ENABLE_PRESENCE_INTENT", "1").strip().lower() in {"1", "true", "yes", "on"}
intents.presences = ENABLE_PRESENCE_INTENT

bot = commands.Bot(command_prefix=".", intents=intents, tree_cls=CoffeecordCommandTree)

tree = bot.tree
anti_abuse.register_dev_group(bot)

OWNER_ID = _env_or_branding_owner_id()


def _dispatch_module_log_event(
    guild: discord.Guild | None,
    module_name: str,
    action: str,
    actor: discord.abc.User | None = None,
    details: str = "",
    channel_id: int | None = None,
) -> None:
    """Emit generic module events consumed by Modules/logging.py."""
    if guild is None:
        return
    try:
        bot.dispatch("coffeecord_module_event", guild, module_name, action, actor, details, channel_id)
    except Exception:
        pass


@asynccontextmanager
async def _http_session():
    """Yield shared aiohttp session, or a temporary one if on_ready has not run yet."""
    session = getattr(bot, "http_session", None)
    if session is not None and not session.closed:
        yield session
        return
    session = aiohttp.ClientSession()
    try:
        yield session
    finally:
        await session.close()


# Lazy-loaded modules
_tickets_module: typing.Any | None = None
_automod_module: typing.Any | None = None
_leveling_module: typing.Any | None = None


def _get_tickets_module() -> typing.Any:
    global _tickets_module
    if _tickets_module is None:
        _tickets_module = importlib.import_module("Modules.tickets")
    return _tickets_module


def _get_automod_module() -> typing.Any:
    global _automod_module
    if _automod_module is None:
        _automod_module = importlib.import_module("Modules.automod")
    return _automod_module


def _get_leveling_module() -> typing.Any:
    global _leveling_module
    if _leveling_module is None:
        _leveling_module = importlib.import_module("Modules.leveling")
    return _leveling_module

logging_enabled = True  # Toggle to enable/disable logs
log_channel_id = None   # Will be set with !log start

authoroles = set()
autorole_config = {}
verify_config = {}
CONFIG_FILE = os.path.join(_storage_dir, "Config", "autorole_config.json")
DONATION_URL = "https://ko-fi.com/coffeecord"
GITHUB_URL = "https://github.com/peacheseatme/Coffeecord"
PRIVACY_POLICY_URL = f"{GITHUB_URL}/blob/main/PRIVACY_POLICY.md"
TERMS_URL = f"{GITHUB_URL}/blob/main/TERMS_OF_SERVICE.md"
PERMISSIONS_DOC_URL = f"{GITHUB_URL}/blob/main/docs/commands/permissions.md"
TOPGG_URL = f"https://top.gg/bot/{_DISCORD_APP_ID}" if _DISCORD_APP_ID else "https://top.gg/"
BOT_INVITE_URL = (
    _oauth_invite_url(_DISCORD_APP_ID)
    if _DISCORD_APP_ID
    else "https://discord.com/developers/applications"
)
_SUPPORT_URL_DEFAULT = "https://discord.com/"
SUPPORT_SERVER = _env_or_branding_str(
    "COFFEECORD_SUPPORT_SERVER_URL", "support_server_url", _SUPPORT_URL_DEFAULT
)
PERMANENT_INVITE = _env_or_branding_str(
    "COFFEECORD_SUPPORT_INVITE", "support_invite_url", SUPPORT_SERVER
)
SUPPORTERS_FILE = os.path.join(_storage_dir, "Data", "supporters.json")
SUPPORTER_GRACE_DAYS = 35
pending_kofi_links: dict[str, list[int]] = {}


def _supporters_default() -> dict:
    return {"supporters": {}, "unlinked_donations": []}


def _safe_iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def load_supporters_db() -> dict:
    if not os.path.exists(SUPPORTERS_FILE):
        return _supporters_default()
    try:
        with open(SUPPORTERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _supporters_default()

    supporters = data.get("supporters", {})
    if isinstance(supporters, list):
        # Migrate legacy flat list format into record map.
        migrated: dict[str, dict] = {}
        now = _safe_iso_now()
        for raw_id in supporters:
            uid = str(raw_id)
            migrated[uid] = {
                "discord_id": int(raw_id),
                "email": None,
                "tier": "donation",
                "active": True,
                "first_seen": now,
                "last_payment": now,
                "total_usd": 0.0,
                "kofi_transaction_ids": [],
            }
        data["supporters"] = migrated
        data.setdefault("unlinked_donations", [])
        save_supporters_db(data)
        return data

    if not isinstance(supporters, dict):
        data["supporters"] = {}
    if not isinstance(data.get("unlinked_donations"), list):
        data["unlinked_donations"] = []
    return data


def save_supporters_db(data: dict) -> None:
    os.makedirs(os.path.dirname(SUPPORTERS_FILE), exist_ok=True)
    with open(SUPPORTERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _record_is_active(record: dict) -> bool:
    if not record or not record.get("active", False):
        return False
    if record.get("tier") != "subscription":
        return True
    last_payment_raw = record.get("last_payment")
    if not last_payment_raw:
        return False
    try:
        last_payment = datetime.fromisoformat(last_payment_raw)
    except ValueError:
        return False
    return (datetime.utcnow() - last_payment).days <= SUPPORTER_GRACE_DAYS


def is_supporter(user_id: int) -> bool:
    data = load_supporters_db()
    record = data.get("supporters", {}).get(str(user_id))
    if not record:
        return False
    active = _record_is_active(record)
    if record.get("active") != active:
        record["active"] = active
        save_supporters_db(data)
    return active


def get_staff_cfg(guild_id: str) -> dict:
    return staff_app_cfg.setdefault(guild_id, {
        "enabled": False,
        "questions": [],
        "review_channel_id": None,
        "reviewer_role_id": None
    })


THEME_DIR = os.path.join(_storage_dir, "Config", "theme_storage")
THEME_GUILD_MAP = os.path.join(_storage_dir, "Config", "themes.json")

def load_guild_theme(guild_id: str):
    # Load theme name for this guild
    if not os.path.exists(THEME_GUILD_MAP):
        return None

    with open(THEME_GUILD_MAP) as f:
        data = json.load(f)

    theme_name = data.get("guilds", {}).get(guild_id)
    if not theme_name:
        return None

    theme_path = os.path.join(THEME_DIR, f"{theme_name}.json")
    if not os.path.exists(theme_path):
        return None

    with open(theme_path) as f:
        theme = json.load(f)

    # Convert hex strings → integers for Discord embeds
    for key, value in theme["colors"].items():
        if isinstance(value, str) and value.startswith("#"):
            theme["colors"][key] = int(value.lstrip("#"), 16)

    return theme

class Theme:
    def __init__(self, config):
        self.config = config

    def get(self, tag: str, default=None):
        # Retrieve "moderation.ban.no_permission" style keys
        parts = tag.split(".")
        node = self.config
        for p in parts:
            if p in node:
                node = node[p]
            else:
                return default
        return node

    def color(self, name: str):
        return self.get(f"colors.{name}", 0x2B2D31)

    def button(self, name: str):
        return self.get(f"buttons.{name}", "grey")

    def response(self, tag: str, fallback: str = None):
        return self.get(f"responses.{tag}", fallback)

    async def send(
        self,
        interaction,
        tag: str,
        *,
        embed=False,
        buttons=False,
        ephemeral=False,
        **kwargs
    ):
        text = self.response(tag, f"Missing theme: {tag}")

        if embed:
            emb = discord.Embed(
                description=text,
                color=self.color("primary")
            )
            return await interaction.response.send_message(
                embed=emb,
                ephemeral=ephemeral,
                **kwargs
            )

        return await interaction.response.send_message(
            text,
            ephemeral=ephemeral,
            **kwargs
        )

COMMAND_CATEGORIES = {
    "General": [
        "/help", "/command_perms docs", "/command_perms edit", "/support us", "/say", "/dm", "/poll", "/nickname",
        "/optout", "/optin"
    ],
    "Ko-fi": [
        "/kofi link", "/kofi status", "/kofi claim", "/kofi add", "/kofi remove"
    ],
    "Logging": [
        "/logging status", "/logging setup", "/logging toggle", "/logging module", "/logging disable"
    ],
    "Moderation": [
        "/ban", "/unban", "/mute", "/unmute", "/hardmute",
        "/muterole create", "/muterole update",
        "/giverole", "/removerole", "/purge", "/specific_purge"
    ],
    "Automod": [
        "/automod overview", "/automod on", "/automod off", "/automod status",
        "/automod set log", "/automod toggle rule", "/automod badword add"
    ],
    "Fun": [
        "/8ball", "/bet", "/flipcoin", "/hug", "/kiss",
        "/lovecalc", "/truth", "/dare", "/uwuify", "/nuke", "/roast",
        "/ak47", "/petpet", "/dog", "/cat", "/abracadaberamotherafu"
    ],
    "Timers / Reminders": [
        "/remindme", "/starttimer", "/checktimers", "/endtimer"
    ],
    "Leveling": [
        "/level", "/levelcard customize", "/levelcard preset", "/xpset", "/xp config",
        "/levelreward add", "/levelreward remove",
        "/levelreward list", "/levelreward mode"
    ],
    "Calls": [
        "/call create", "/call join", "/call add", "/call remove", "/call end", "/call promote"
    ],
    "Tickets": [
        "/ticket setup", "/ticket add", "/ticket remove",
        "/ticket_export", "/ticket_import"
    ],
    "Translation": [
        "/translate text", "/translate settings", "/translate usage", "/translate reset"
    ],
    "Reaction Roles": [
        "/reactionrole create", "/reactionrole list", "/reactionrole delete",
        "/reactionrole config", "/reactionrole edit"
    ],
    "Welcome & Leave": [
        "/welcome config", "/welcome test", "/leave config", "/leave test"
    ],
    "Setup Wizard": [
        "/setup", "/setup_resume", "/setup_cancel"
    ],
    "Themes": [
        "/theme list", "/theme set", "/theme preview", "/theme info",
        "/theme upload", "/theme delete",
        "/theme responses presets", "/theme responses list", "/theme responses upload",
        "/theme responses discover", "/theme responses keys", "/theme responses clear",
    ],
    "Misc": [
        "/verifyconfig", "/autorole status", "/autorole toggle", "/autorole add",
        "/application", "/application setup", "/application toggle",
        "/modules status", "/modules toggle", "/modules enable", "/modules disable",
        "/adaptive_slowmode", "/uninstall", "/test"
    ]

}

class HelpMenu(discord.ui.View):
    def __init__(self, pages, index):
        super().__init__(timeout=120)
        self.pages = pages
        self.index = index

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


def build_help_pages():
    pages = []
    for category, cmds in COMMAND_CATEGORIES.items():
        embed = discord.Embed(
            title=f"📖 Help — {category}",
            description="\n".join(f"`{cmd}`" for cmd in cmds),
            color=discord.Color.blurple()
        )
        pages.append(embed)
    return pages


@bot.tree.command(name="help", description="View bot commands or search for a specific one")
@app_commands.describe(search="Search for a command name")
async def help_cmd(interaction: discord.Interaction, search: str = None):
    pages = build_help_pages()

    if search:
        search = search.lower()
        found = [cmd for cmds in COMMAND_CATEGORIES.values() for cmd in cmds if search in cmd.lower()]
        if not found:
            await interaction.response.send_message(f"❌ No commands found for `{search}`")
            return

        embed = discord.Embed(
            title=f"🔍 Search Results for: {search}",
            description="\n".join(f"`{cmd}`" for cmd in found),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
        return

    view = HelpMenu(pages, 0)
    await interaction.response.send_message(embed=pages[0], view=view)


@tree.command(name="about", description="Learn about Coffeecord")
async def about_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="About Coffeecord",
        color=discord.Color.from_str("#7B5EA7")
    )
    if interaction.client.user:
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
    embed.add_field(name="Version", value=get_bot_version_display(), inline=False)
    developer_value = f"<@{OWNER_ID}>" if OWNER_ID else "Not configured"
    embed.add_field(name="Developer", value=developer_value, inline=False)
    embed.add_field(name="Servers", value=f"{len(interaction.client.guilds)} servers", inline=False)
    embed.add_field(name="Ping", value=f"{round(interaction.client.latency * 1000)} ms", inline=False)
    uptime_s = time.monotonic() - _BOT_START_MONO
    if uptime_s >= 60.0:
        uh = int(uptime_s // 3600)
        um = int((uptime_s % 3600) // 60)
        embed.add_field(name="Uptime", value=f"{uh}h {um}m", inline=True)
    if _PSUTIL_PROCESS is not None:
        try:
            rss_mb = _PSUTIL_PROCESS.memory_info().rss / (1024 * 1024)
            cpu_pct = _PSUTIL_PROCESS.cpu_percent(interval=0.1)
            embed.add_field(name="Memory", value=f"{rss_mb:.1f} MB", inline=True)
            embed.add_field(name="CPU", value=f"{cpu_pct:.1f}%", inline=True)
        except Exception:
            pass
    embed.set_footer(text="Coffeecord • Made with ☕ and discord.py ❤")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="GitHub", url=GITHUB_URL, emoji="🐙", style=discord.ButtonStyle.link, row=0))
    view.add_item(discord.ui.Button(label="top.gg", url=TOPGG_URL, emoji="⬆️", style=discord.ButtonStyle.link, row=0))
    view.add_item(discord.ui.Button(label="Invite Me", url=BOT_INVITE_URL, emoji="🤖", style=discord.ButtonStyle.link, row=1))
    view.add_item(discord.ui.Button(label="Support Server", url=SUPPORT_SERVER, emoji="💬", style=discord.ButtonStyle.link, row=1))
    view.add_item(discord.ui.Button(label="Ko-fi", url=DONATION_URL, emoji="☕", style=discord.ButtonStyle.link, row=1))
    view.add_item(discord.ui.Button(label="Privacy Policy", url=PRIVACY_POLICY_URL, emoji="🔒", style=discord.ButtonStyle.link, row=2))
    view.add_item(discord.ui.Button(label="Terms of Service", url=TERMS_URL, emoji="📜", style=discord.ButtonStyle.link, row=2))

    await interaction.response.send_message(embed=embed, view=view)


COMMAND_PERM_MODE_CHOICES = [
    app_commands.Choice(name="Reset to bot default", value="reset"),
    app_commands.Choice(name="Everyone (no Discord permission)", value="everyone"),
    app_commands.Choice(name="Administrator", value="administrator"),
    app_commands.Choice(name="Manage Server", value="manage_guild"),
    app_commands.Choice(name="Ban Members", value="ban_members"),
    app_commands.Choice(name="Manage Messages", value="manage_messages"),
    app_commands.Choice(name="Moderate Members", value="moderate_members"),
    app_commands.Choice(name="Manage Channels", value="manage_channels"),
    app_commands.Choice(name="Manage Nicknames", value="manage_nicknames"),
    app_commands.Choice(name="Manage Roles", value="manage_roles"),
    app_commands.Choice(name="Mod combo (roles / moderate / server / admin)", value="roles_or_moderate"),
    app_commands.Choice(name="Custom (use custom_permissions)", value="custom"),
]


command_perms_group = app_commands.Group(
    name="command_perms",
    description="Command permission documentation and per-server slash overrides.",
)


@command_perms_group.command(name="docs", description="Open the permissions documentation.")
async def command_perms_docs(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Command Permissions",
        description=(
            "Full reference: which slash commands use which **member** permissions, and what **bot** permissions "
            "features need. Use `/command_perms edit` (Manage Server) to type any slash command name and set requirements."
        ),
        color=discord.Color.blurple(),
        url=PERMISSIONS_DOC_URL,
    )
    embed.add_field(
        name="Quick links",
        value=f"[📋 Permissions doc]({PERMISSIONS_DOC_URL})",
        inline=False,
    )
    embed.set_footer(text="Per-guild overrides apply after the next sync; defaults ship with the bot.")
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(label="Open permissions doc", url=PERMISSIONS_DOC_URL, emoji="📋", style=discord.ButtonStyle.link)
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@command_perms_group.command(name="list", description="List slash commands with non-default permission rules in this server.")
async def command_perms_list(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
        return
    ov = command_perm_overrides.get_guild_overrides(interaction.guild.id)
    lines: list[str] = []
    for qn in sorted(ov.keys()):
        try:
            human = command_perm_overrides.format_rule_human(ov[qn])
        except Exception:
            human = "(invalid stored rule)"
        lines.append(f"**/{qn}** → {human}")
    if not lines:
        await interaction.response.send_message(
            "No overrides in this server. Commands use bot defaults (see `/command_perms docs`).",
            ephemeral=True,
        )
        return
    desc = "\n".join(lines)[:4000]
    embed = discord.Embed(
        title="Command permission overrides",
        description=desc,
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@command_perms_group.command(name="edit", description="Change required Discord permissions for a slash command (this server).")
@app_commands.describe(
    command="Slash command: type the qualified name (e.g. `call create`, `call_create`, or `/help`)",
    mode="New requirement preset",
    custom_permissions="When mode is Custom: comma-separated flags, e.g. ban_members,kick_members",
)
@app_commands.choices(mode=COMMAND_PERM_MODE_CHOICES)
async def command_perms_edit(
    interaction: discord.Interaction,
    command: str,
    mode: app_commands.Choice[str],
    custom_permissions: str | None = None,
):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
        return
    resolved = command_perm_overrides.resolve_slash_command_input(interaction.client.tree, command)
    if resolved is None:
        await interaction.response.send_message(
            "❌ No matching slash command. Use the name Discord shows (e.g. `help`, `call create`, `modules toggle`).",
            ephemeral=True,
        )
        return
    qn = resolved
    if mode.value == "custom":
        if not (custom_permissions or "").strip():
            await interaction.response.send_message(
                "❌ For **Custom**, fill `custom_permissions` (comma-separated permission names).",
                ephemeral=True,
            )
            return
    try:
        if mode.value == "custom":
            rule = command_perm_overrides.parse_custom_permissions_csv(custom_permissions or "")
        else:
            rule = command_perm_overrides.preset_rule(mode.value)
    except ValueError as e:
        await interaction.response.send_message(
            _redact_discord_token_in_text(f"❌ {e}"),
            ephemeral=True,
        )
        return

    command_perm_overrides.set_guild_command_rule(interaction.guild.id, qn, rule)

    if rule is None:
        if qn in command_perm_overrides.DEFAULT_SLASH_COMMAND_RULES:
            msg = f"✅ Reset **`/{qn}`** to the bot default."
        else:
            msg = f"✅ Removed custom rules for **`/{qn}`** (no CoffeeCord member-permission gate)."
    else:
        msg = f"✅ **`/{qn}`** now requires: {command_perm_overrides.format_rule_human(rule)}"
    await interaction.response.send_message(msg, ephemeral=True)


tree.add_command(command_perms_group)


class DonateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Support us via Ko-fi",
            url=DONATION_URL,
            style=discord.ButtonStyle.link
        ))

support_group = app_commands.Group(name="support", description="Support Coffeecord")


@support_group.command(name="us", description="Support Coffeecord and get access to exclusive features!")
async def donate(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Support Coffeecord! 💙",
        description=(
            "Click the button below to support us via Ko-fi.\n\n"
            "✅ Link your Discord account to Ko-fi and buy us a coffee or membership to support us!\n\n"
            "**Perks:**\n"
            "- `Supporter` role!\n"
            "- Access to a private channel!\n"
            "- Early access to new features!\n"
            "- Play GIFs in your leveling card!\n"
            "- Unlimited translations!\n\n"
            "**How to activate your perks:**\n"
            "1. Click **Support us via Ko-fi** below and complete your donation or membership.\n"
            "2. Come back to Discord and run `/kofi link email:you@example.com` — use the same email you used on Ko-fi.\n"
            "3. Your perks activate instantly. Run `/kofi status` to confirm.\n\n"
            f"Need help? Join the support server: {PERMANENT_INVITE}"
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, view=DonateView(), ephemeral=True)


tree.add_command(support_group)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _queue_pending_kofi_link(email: str, user_id: int) -> int:
    """Queue a user for the next Ko-fi payment for this email."""
    queue = pending_kofi_links.setdefault(email, [])
    if user_id not in queue:
        queue.append(user_id)
    return queue.index(user_id) + 1


def _pop_next_pending_kofi_link(email: str) -> int | None:
    queue = pending_kofi_links.get(email)
    if not queue:
        return None
    user_id = queue.pop(0)
    if not queue:
        pending_kofi_links.pop(email, None)
    return user_id


def _is_valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()))


def _find_supporter_id_by_email(data: dict, email: str) -> str | None:
    normalized = _normalize_email(email)
    for uid, record in data.get("supporters", {}).items():
        if _normalize_email(str(record.get("email") or "")) == normalized:
            return uid
    return None


def _upsert_supporter_payment(
    data: dict,
    discord_user_id: int,
    *,
    email: str | None,
    tier: str,
    amount_usd: float,
    transaction_id: str | None,
    payment_ts: str | None,
) -> None:
    uid = str(discord_user_id)
    supporters = data.setdefault("supporters", {})
    now = _safe_iso_now()
    record = supporters.get(uid, {})

    tx_ids = record.get("kofi_transaction_ids", [])
    if not isinstance(tx_ids, list):
        tx_ids = []

    if transaction_id and transaction_id in tx_ids:
        return
    if transaction_id:
        tx_ids.append(transaction_id)

    first_seen = record.get("first_seen") or now
    total_usd = float(record.get("total_usd", 0.0) or 0.0) + max(amount_usd, 0.0)

    supporters[uid] = {
        "discord_id": discord_user_id,
        "email": _normalize_email(email) if email else record.get("email"),
        "tier": tier,
        "active": True,
        "first_seen": first_seen,
        "last_payment": payment_ts or now,
        "total_usd": round(total_usd, 2),
        "kofi_transaction_ids": tx_ids,
    }


def _claim_unlinked_for_user(discord_user_id: int, email: str) -> int:
    data = load_supporters_db()
    unlinked = data.get("unlinked_donations", [])
    if not isinstance(unlinked, list):
        unlinked = []
    normalized = _normalize_email(email)
    matched = [d for d in unlinked if _normalize_email(str(d.get("email") or "")) == normalized]
    if not matched:
        return 0

    for donation in matched:
        _upsert_supporter_payment(
            data,
            discord_user_id,
            email=normalized,
            tier="subscription" if donation.get("is_subscription_payment") else "donation",
            amount_usd=float(donation.get("amount_usd", 0.0) or 0.0),
            transaction_id=donation.get("kofi_transaction_id"),
            payment_ts=donation.get("timestamp"),
        )
    data["unlinked_donations"] = [d for d in unlinked if d not in matched]
    save_supporters_db(data)
    return len(matched)


async def _notify_supporter_dm(discord_user_id: int, message: str) -> None:
    user = bot.get_user(discord_user_id)
    if user is None:
        try:
            user = await bot.fetch_user(discord_user_id)
        except discord.DiscordException:
            return
    try:
        await user.send(message)
    except (discord.Forbidden, discord.HTTPException):
        return


async def handle_kofi_payload(payload: dict) -> None:
    email = _normalize_email(str(payload.get("email") or ""))
    tx_id = str(payload.get("kofi_transaction_id") or "")
    payment_ts = str(payload.get("timestamp") or _safe_iso_now())
    is_sub = bool(payload.get("is_subscription_payment")) or str(payload.get("type", "")).lower() == "subscription"
    tier = "subscription" if is_sub else "donation"
    try:
        amount_usd = float(payload.get("amount") or 0.0)
    except (TypeError, ValueError):
        amount_usd = 0.0

    data = load_supporters_db()
    linked_user_id = _pop_next_pending_kofi_link(email)

    if linked_user_id is None and email:
        existing_uid = _find_supporter_id_by_email(data, email)
        if existing_uid is not None:
            linked_user_id = int(existing_uid)

    if linked_user_id is not None:
        _upsert_supporter_payment(
            data,
            linked_user_id,
            email=email,
            tier=tier,
            amount_usd=amount_usd,
            transaction_id=tx_id or None,
            payment_ts=payment_ts,
        )
        save_supporters_db(data)
        guild_hint = next((g for g in bot.guilds if g.get_member(linked_user_id) is not None), None)
        _dispatch_module_log_event(
            guild_hint,
            "supporters",
            "kofi_webhook_linked",
            actor=None,
            details=f"email={email}; amount_usd={amount_usd:.2f}; tier={tier}; linked_user_id={linked_user_id}",
            channel_id=None,
        )
        await _notify_supporter_dm(
            linked_user_id,
            "Thank you for supporting Coffeecord on Ko-fi! Your supporter perks are now active.",
        )
        return

    unlinked = data.setdefault("unlinked_donations", [])
    unlinked.append(
        {
            "email": email,
            "amount": str(payload.get("amount") or "0"),
            "amount_usd": amount_usd,
            "timestamp": payment_ts,
            "kofi_transaction_id": tx_id or None,
            "is_subscription_payment": is_sub,
        }
    )
    save_supporters_db(data)


kofi_group = app_commands.Group(name="kofi", description="Manage Ko-fi supporter linking and status")


@kofi_group.command(name="link", description="Link your Discord to your Ko-fi email for supporter perks.")
@app_commands.describe(email="Email used on Ko-fi")
async def kofi_link(interaction: discord.Interaction, email: str):
    if not _is_valid_email(email):
        await interaction.response.send_message("❌ Please provide a valid email address.", ephemeral=True)
        return
    normalized = _normalize_email(email)
    queue_position = _queue_pending_kofi_link(normalized, interaction.user.id)
    claimed = _claim_unlinked_for_user(interaction.user.id, normalized)
    if claimed:
        await _notify_supporter_dm(
            interaction.user.id,
            f"Ko-fi link complete. {claimed} previous donation(s) were linked to your account.",
        )
        await interaction.response.send_message(
            f"✅ Linked. Claimed **{claimed}** existing Ko-fi payment(s) for `{normalized}`.",
            ephemeral=True,
        )
        _dispatch_module_log_event(
            interaction.guild,
            "supporters",
            "kofi_link",
            actor=interaction.user,
            details=f"email={normalized}; claimed={claimed}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        return
    await interaction.response.send_message(
        (
            f"✅ Pending — your next Ko-fi donation/subscription from `{normalized}` "
            f"will link automatically. Queue position: **#{queue_position}**."
        ),
        ephemeral=True,
    )
    _dispatch_module_log_event(
        interaction.guild,
        "supporters",
        "kofi_link_pending",
        actor=interaction.user,
        details=f"email={normalized}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )


@kofi_group.command(name="claim", description="Claim previously unlinked Ko-fi donations by email.")
@app_commands.describe(email="Email used on Ko-fi")
async def kofi_claim(interaction: discord.Interaction, email: str):
    if not _is_valid_email(email):
        await interaction.response.send_message("❌ Please provide a valid email address.", ephemeral=True)
        return
    normalized = _normalize_email(email)
    claimed = _claim_unlinked_for_user(interaction.user.id, normalized)
    if claimed:
        await interaction.response.send_message(
            f"✅ Claimed **{claimed}** unlinked payment(s) for `{normalized}`.",
            ephemeral=True,
        )
        _dispatch_module_log_event(
            interaction.guild,
            "supporters",
            "kofi_claim",
            actor=interaction.user,
            details=f"email={normalized}; claimed={claimed}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        return
    queue_position = _queue_pending_kofi_link(normalized, interaction.user.id)
    await interaction.response.send_message(
        (
            f"ℹ️ No unlinked payments found yet for `{normalized}`. "
            f"A pending link was created at queue position **#{queue_position}**."
        ),
        ephemeral=True,
    )
    _dispatch_module_log_event(
        interaction.guild,
        "supporters",
        "kofi_claim_pending",
        actor=interaction.user,
        details=f"email={normalized}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )


@kofi_group.command(name="status", description="View your Ko-fi supporter status.")
async def kofi_status(interaction: discord.Interaction):
    data = load_supporters_db()
    record = data.get("supporters", {}).get(str(interaction.user.id))
    if not record:
        await interaction.response.send_message("You are not marked as a supporter yet.", ephemeral=True)
        return
    active = _record_is_active(record)
    if record.get("active") != active:
        record["active"] = active
        save_supporters_db(data)

    embed = discord.Embed(title="Ko-fi Supporter Status", color=discord.Color.blurple())
    embed.add_field(name="Active", value="Yes" if active else "No", inline=True)
    embed.add_field(name="Tier", value=str(record.get("tier", "unknown")).title(), inline=True)
    embed.add_field(name="Last Payment", value=str(record.get("last_payment", "Unknown")), inline=False)
    embed.add_field(name="Total USD", value=f"{float(record.get('total_usd', 0.0) or 0.0):.2f}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@kofi_group.command(name="add", description="Manually add or update a supporter.")
@app_commands.describe(user="User to mark as supporter", email="Email used on Ko-fi")
async def kofi_add(interaction: discord.Interaction, user: discord.Member, email: str):
    if not _is_valid_email(email):
        await interaction.response.send_message("❌ Please provide a valid email address.", ephemeral=True)
        return
    data = load_supporters_db()
    now = _safe_iso_now()
    _upsert_supporter_payment(
        data,
        user.id,
        email=_normalize_email(email),
        tier="donation",
        amount_usd=0.0,
        transaction_id=None,
        payment_ts=now,
    )
    save_supporters_db(data)
    await interaction.response.send_message(f"✅ {user.mention} marked as an active supporter.", ephemeral=True)
    _dispatch_module_log_event(
        interaction.guild,
        "supporters",
        "supporter_add",
        actor=interaction.user,
        details=f"target_user_id={user.id}; email={_normalize_email(email)}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )


@kofi_group.command(name="remove", description="Disable supporter status for a user.")
@app_commands.describe(user="User to deactivate")
async def kofi_remove(interaction: discord.Interaction, user: discord.Member):
    data = load_supporters_db()
    record = data.get("supporters", {}).get(str(user.id))
    if not record:
        await interaction.response.send_message("❌ No supporter record exists for that user.", ephemeral=True)
        return
    record["active"] = False
    save_supporters_db(data)
    await interaction.response.send_message(f"✅ Supporter status disabled for {user.mention}.", ephemeral=True)
    _dispatch_module_log_event(
        interaction.guild,
        "supporters",
        "supporter_remove",
        actor=interaction.user,
        details=f"target_user_id={user.id}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )


tree.add_command(kofi_group)


# Ko-fi prefix commands (.kofi link/status/claim/add/remove)
@bot.group(name="kofi", invoke_without_command=True)
async def kofi_prefix(ctx: commands.Context):
    if ctx.invoked_subcommand is None:
        await ctx.send(
            "Usage: `.kofi link <email>` | `.kofi status` | `.kofi claim <email>` | "
            "`.kofi add <user> <email>` | `.kofi remove <user>` (add/remove: admin only)"
        )


@kofi_prefix.command(name="link")
async def kofi_link_prefix(ctx: commands.Context, email: str):
    if not _is_valid_email(email):
        await ctx.send("❌ Please provide a valid email address.")
        return
    normalized = _normalize_email(email)
    queue_position = _queue_pending_kofi_link(normalized, ctx.author.id)
    claimed = _claim_unlinked_for_user(ctx.author.id, normalized)
    if claimed:
        await _notify_supporter_dm(
            ctx.author.id,
            f"Ko-fi link complete. {claimed} previous donation(s) were linked to your account.",
        )
        await ctx.send(f"✅ Linked. Claimed **{claimed}** existing Ko-fi payment(s) for `{normalized}`.")
        _dispatch_module_log_event(
            ctx.guild,
            "supporters",
            "kofi_link",
            actor=ctx.author,
            details=f"email={normalized}; claimed={claimed}",
            channel_id=ctx.channel.id if ctx.channel else None,
        )
        return
    await ctx.send(
        f"✅ Pending — your next Ko-fi donation/subscription from `{normalized}` "
        f"will link automatically. Queue position: **#{queue_position}**."
    )
    _dispatch_module_log_event(
        ctx.guild,
        "supporters",
        "kofi_link_pending",
        actor=ctx.author,
        details=f"email={normalized}",
        channel_id=ctx.channel.id if ctx.channel else None,
    )


@kofi_prefix.command(name="status")
async def kofi_status_prefix(ctx: commands.Context):
    data = load_supporters_db()
    record = data.get("supporters", {}).get(str(ctx.author.id))
    if not record:
        await ctx.send("You are not marked as a supporter yet.")
        return
    active = _record_is_active(record)
    if record.get("active") != active:
        record["active"] = active
        save_supporters_db(data)
    embed = discord.Embed(title="Ko-fi Supporter Status", color=discord.Color.blurple())
    embed.add_field(name="Active", value="Yes" if active else "No", inline=True)
    embed.add_field(name="Tier", value=str(record.get("tier", "unknown")).title(), inline=True)
    embed.add_field(name="Last Payment", value=str(record.get("last_payment", "Unknown")), inline=False)
    embed.add_field(name="Total USD", value=f"{float(record.get('total_usd', 0.0) or 0.0):.2f}", inline=True)
    await ctx.send(embed=embed)


@kofi_prefix.command(name="claim")
async def kofi_claim_prefix(ctx: commands.Context, email: str):
    if not _is_valid_email(email):
        await ctx.send("❌ Please provide a valid email address.")
        return
    normalized = _normalize_email(email)
    claimed = _claim_unlinked_for_user(ctx.author.id, normalized)
    if claimed:
        await ctx.send(f"✅ Claimed **{claimed}** unlinked payment(s) for `{normalized}`.")
        _dispatch_module_log_event(
            ctx.guild,
            "supporters",
            "kofi_claim",
            actor=ctx.author,
            details=f"email={normalized}; claimed={claimed}",
            channel_id=ctx.channel.id if ctx.channel else None,
        )
        return
    queue_position = _queue_pending_kofi_link(normalized, ctx.author.id)
    await ctx.send(
        f"ℹ️ No unlinked payments found yet for `{normalized}`. "
        f"A pending link was created at queue position **#{queue_position}**."
    )
    _dispatch_module_log_event(
        ctx.guild,
        "supporters",
        "kofi_claim_pending",
        actor=ctx.author,
        details=f"email={normalized}",
        channel_id=ctx.channel.id if ctx.channel else None,
    )


@kofi_prefix.command(name="add")
@commands.has_permissions(administrator=True)
async def kofi_add_prefix(ctx: commands.Context, user: discord.Member, email: str):
    if not _is_valid_email(email):
        await ctx.send("❌ Please provide a valid email address.")
        return
    data = load_supporters_db()
    now = _safe_iso_now()
    _upsert_supporter_payment(
        data,
        user.id,
        email=_normalize_email(email),
        tier="donation",
        amount_usd=0.0,
        transaction_id=None,
        payment_ts=now,
    )
    save_supporters_db(data)
    await ctx.send(f"✅ {user.mention} marked as an active supporter.")
    _dispatch_module_log_event(
        ctx.guild,
        "supporters",
        "supporter_add",
        actor=ctx.author,
        details=f"target_user_id={user.id}; email={_normalize_email(email)}",
        channel_id=ctx.channel.id if ctx.channel else None,
    )


@kofi_prefix.command(name="remove")
@commands.has_permissions(administrator=True)
async def kofi_remove_prefix(ctx: commands.Context, user: discord.Member):
    data = load_supporters_db()
    record = data.get("supporters", {}).get(str(user.id))
    if not record:
        await ctx.send("❌ No supporter record exists for that user.")
        return
    record["active"] = False
    save_supporters_db(data)
    await ctx.send(f"✅ Supporter status disabled for {user.mention}.")
    _dispatch_module_log_event(
        ctx.guild,
        "supporters",
        "supporter_remove",
        actor=ctx.author,
        details=f"target_user_id={user.id}",
        channel_id=ctx.channel.id if ctx.channel else None,
    )


from discord import app_commands, ui, Interaction

# --- Purge: rate limits + emergency stop (see /purge, /specific_purge) ---
PURGE_DISCORD_BULK_MAX = 100
PURGE_DISCORD_BULK_MAX_AGE = timedelta(days=14)
PURGE_RATE_LIMIT_FALLBACK_SECONDS = 1.0
PURGE_PROGRESS_EDIT_INTERVAL_SEC = 12.0
PURGE_PROGRESS_EDIT_MIN_DELETES = 80


def _purge_429_sleep_seconds(exc: discord.HTTPException) -> float:
    if exc.status != 429:
        return PURGE_RATE_LIMIT_FALLBACK_SECONDS
    ra = getattr(exc, "retry_after", None)
    if ra is not None:
        return max(float(ra), 0.5)
    return PURGE_RATE_LIMIT_FALLBACK_SECONDS


def _purge_message_eligible_for_bulk_delete(message: discord.Message) -> bool:
    return (discord.utils.utcnow() - message.created_at) < PURGE_DISCORD_BULK_MAX_AGE


async def _purge_bulk_delete_with_retry(
    channel: discord.TextChannel,
    messages: list[discord.Message],
    cancel_event: asyncio.Event,
) -> None:
    if not messages:
        return
    while True:
        if cancel_event.is_set():
            return
        try:
            await channel.delete_messages(messages)
            return
        except discord.Forbidden:
            raise
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(_purge_429_sleep_seconds(e))
                continue
            raise


async def _purge_single_delete_with_retry(
    message: discord.Message,
    cancel_event: asyncio.Event,
) -> bool:
    while True:
        if cancel_event.is_set():
            return False
        try:
            await message.delete()
            return True
        except discord.NotFound:
            return True
        except discord.Forbidden:
            raise
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(_purge_429_sleep_seconds(e))
                continue
            raise


class PurgeEmergencyStopView(ui.View):
    """Ephemeral purge control: only the invoker may stop."""

    def __init__(self, invoker_id: int, cancel_event: asyncio.Event) -> None:
        super().__init__(timeout=None)
        self.invoker_id = invoker_id
        self.cancel_event = cancel_event

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the moderator who started this purge can use this button.",
                ephemeral=True,
            )
            return False
        return True

    @ui.button(label="Emergency stop", style=discord.ButtonStyle.danger, emoji="🛑", row=0)
    async def emergency_stop(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.cancel_event.set()
        button.disabled = True
        await interaction.response.edit_message(
            content="**Stopping purge** — finishing the current batch, then halting…",
            view=self,
        )


async def _run_cancellable_purge(
    channel: discord.TextChannel,
    limit: int,
    check,
    cancel_event: asyncio.Event,
    *,
    progress_message: discord.WebhookMessage | None,
    progress_view: ui.View | None,
    header_lines: list[str],
) -> tuple[int, bool]:
    """
    Scan at most `limit` messages (newest first), delete those passing `check`.
    Returns (deleted_count, stopped_early).
    """
    deleted = 0
    scanned = 0
    before: discord.Message | None = None
    bulk_buffer: list[discord.Message] = []
    loop = asyncio.get_running_loop()
    last_progress_edit = loop.time()
    last_shown_deleted = 0

    async def flush_bulk() -> None:
        nonlocal deleted, bulk_buffer
        while bulk_buffer and not cancel_event.is_set():
            chunk = bulk_buffer[:PURGE_DISCORD_BULK_MAX]
            bulk_buffer = bulk_buffer[len(chunk) :]
            await _purge_bulk_delete_with_retry(channel, chunk, cancel_event)
            deleted += len(chunk)

    async def maybe_update_progress() -> None:
        nonlocal last_progress_edit, last_shown_deleted
        if progress_message is None:
            return
        now = loop.time()
        if deleted - last_shown_deleted < PURGE_PROGRESS_EDIT_MIN_DELETES and (
            now - last_progress_edit
        ) < PURGE_PROGRESS_EDIT_INTERVAL_SEC:
            return
        last_progress_edit = now
        last_shown_deleted = deleted
        body = "\n".join(header_lines)
        content = (
            f"{body}\n\n"
            f"Scanned up to **{scanned}** / **{limit}** messages.\n"
            f"Deleted so far: **{deleted}**\n\n"
            "🛑 **Emergency stop** — halts after the current delete batch."
        )
        try:
            await progress_message.edit(content=content, view=progress_view)
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(_purge_429_sleep_seconds(e))

    while scanned < limit and not cancel_event.is_set():
        page_size = min(PURGE_DISCORD_BULK_MAX, limit - scanned)
        while True:
            if cancel_event.is_set():
                break
            try:
                page = [
                    m
                    async for m in channel.history(
                        limit=page_size,
                        before=before,
                        oldest_first=False,
                    )
                ]
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(_purge_429_sleep_seconds(e))
                    continue
                raise
        if cancel_event.is_set():
            break
        if not page:
            break
        before = page[-1]
        scanned += len(page)

        for msg in page:
            if cancel_event.is_set():
                break
            if not check(msg):
                continue

            if _purge_message_eligible_for_bulk_delete(msg):
                bulk_buffer.append(msg)
                if len(bulk_buffer) >= PURGE_DISCORD_BULK_MAX:
                    chunk = bulk_buffer[:PURGE_DISCORD_BULK_MAX]
                    bulk_buffer = bulk_buffer[PURGE_DISCORD_BULK_MAX:]
                    await _purge_bulk_delete_with_retry(channel, chunk, cancel_event)
                    deleted += len(chunk)
                    await maybe_update_progress()
            else:
                await flush_bulk()
                if cancel_event.is_set():
                    break
                if await _purge_single_delete_with_retry(msg, cancel_event):
                    deleted += 1
                await maybe_update_progress()

            await asyncio.sleep(0)

        await maybe_update_progress()

        if cancel_event.is_set():
            break

    await flush_bulk()
    stopped = cancel_event.is_set()
    return deleted, stopped


@bot.tree.command(name="legacy_purge", description="(Legacy) Bulk delete messages in a channel")
@app_commands.describe(
    amount="Number of messages to delete",
    channel="Channel to purge messages from",
    msg_type="Type of messages to delete (human/bot/all)",
)
async def purge(interaction: discord.Interaction, amount: int, channel: discord.TextChannel, msg_type: str):
    await interaction.response.defer(ephemeral=True)

    def check(msg: discord.Message):
        if msg_type.lower() == "bot":
            return msg.author.bot
        if msg_type.lower() == "human":
            return not msg.author.bot
        return True

    cancel_event = asyncio.Event()
    view = PurgeEmergencyStopView(interaction.user.id, cancel_event)
    header = (
        f"**Purge** in {channel.mention}\n"
        f"Type: **{msg_type}** · Scanning up to **{amount}** recent messages."
    )
    try:
        control_msg = await interaction.followup.send(
            f"{header}\n\nScanned **0** / **{amount}** · Deleted **0**\n\n"
            "🛑 **Emergency stop** — halts after the current delete batch.",
            view=view,
            ephemeral=True,
        )
    except discord.HTTPException:
        control_msg = None

    try:
        deleted, stopped = await _run_cancellable_purge(
            channel,
            amount,
            check,
            cancel_event,
            progress_message=control_msg,
            progress_view=view if control_msg else None,
            header_lines=[header],
        )
    except discord.Forbidden:
        if control_msg:
            try:
                await control_msg.edit(content="❌ I don't have permission to delete messages in that channel.", view=None)
            except discord.HTTPException:
                pass
        else:
            await interaction.followup.send(
                "❌ I don't have permission to delete messages in that channel.",
                ephemeral=True,
            )
        return
    except discord.HTTPException as e:
        err = _redact_discord_token_in_text(f"❌ Failed to purge: {e}")
        if control_msg:
            try:
                await control_msg.edit(content=err, view=None)
            except discord.HTTPException:
                pass
        else:
            await interaction.followup.send(err, ephemeral=True)
        return

    if control_msg:
        try:
            if stopped:
                await control_msg.edit(
                    content=(
                        f"⏹️ Purge **stopped** early.\n{header}\n\n"
                        f"Deleted **{deleted}** message(s) ({msg_type})."
                    ),
                    view=None,
                )
            else:
                await control_msg.edit(
                    content=(
                        f"✅ Purge **complete**.\n{header}\n\n"
                        f"Deleted **{deleted}** message(s) ({msg_type})."
                    ),
                    view=None,
                )
        except discord.HTTPException:
            pass
    else:
        if stopped:
            await interaction.followup.send(
                f"⏹️ Purge stopped. Deleted **{deleted}** messages from {channel.mention} ({msg_type})",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ Deleted **{deleted}** messages from {channel.mention} ({msg_type})",
                ephemeral=True,
            )

    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "purge",
        actor=interaction.user,
        details=(
            f"channel_id={channel.id}; requested={amount}; deleted={deleted}; type={msg_type}; "
            f"stopped_early={stopped}"
        ),
        channel_id=channel.id,
    )


@bot.tree.command(name="specific_purge", description="Delete messages from a specific user")
@app_commands.describe(
    user="The user to delete messages from",
    amount="Number of messages to delete (or leave empty to delete all found)",
    channel="Channel to purge messages from",
)
async def specific_purge(
    interaction: discord.Interaction,
    user: discord.Member,
    channel: discord.TextChannel,
    amount: int = 1000,
):
    await interaction.response.defer(ephemeral=True)

    def check(msg: discord.Message) -> bool:
        return msg.author.id == user.id

    cancel_event = asyncio.Event()
    view = PurgeEmergencyStopView(interaction.user.id, cancel_event)
    header = f"**Specific purge** — {user.mention} in {channel.mention}\nScanning up to **{amount}** recent messages."
    try:
        control_msg = await interaction.followup.send(
            f"{header}\n\nScanned **0** / **{amount}** · Deleted **0**\n\n"
            "🛑 **Emergency stop** — halts after the current delete batch.",
            view=view,
            ephemeral=True,
        )
    except discord.HTTPException:
        control_msg = None

    try:
        deleted_count, stopped = await _run_cancellable_purge(
            channel,
            amount,
            check,
            cancel_event,
            progress_message=control_msg,
            progress_view=view if control_msg else None,
            header_lines=[header],
        )
    except discord.Forbidden:
        if control_msg:
            try:
                await control_msg.edit(content="❌ I don't have permission to delete messages in that channel.", view=None)
            except discord.HTTPException:
                pass
        else:
            await interaction.followup.send(
                "❌ I don't have permission to delete messages in that channel.",
                ephemeral=True,
            )
        return
    except discord.HTTPException as e:
        err = _redact_discord_token_in_text(f"❌ Failed to purge: {e}")
        if control_msg:
            try:
                await control_msg.edit(content=err, view=None)
            except discord.HTTPException:
                pass
        else:
            await interaction.followup.send(err, ephemeral=True)
        return

    if control_msg:
        try:
            if stopped:
                await control_msg.edit(
                    content=(
                        f"⏹️ Specific purge **stopped** early.\n{header}\n\n"
                        f"Deleted **{deleted_count}** message(s)."
                    ),
                    view=None,
                )
            else:
                await control_msg.edit(
                    content=(
                        f"✅ Specific purge **complete**.\n{header}\n\n"
                        f"Deleted **{deleted_count}** message(s)."
                    ),
                    view=None,
                )
        except discord.HTTPException:
            pass
    else:
        if stopped:
            await interaction.followup.send(
                f"⏹️ Stopped. Deleted **{deleted_count}** messages from {user.mention} in {channel.mention}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ Deleted **{deleted_count}** messages from {user.mention} in {channel.mention}",
                ephemeral=True,
            )

    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "specific_purge",
        actor=interaction.user,
        details=(
            f"target_user_id={user.id}; channel_id={channel.id}; limit={amount}; "
            f"deleted={deleted_count}; stopped_early={stopped}"
        ),
        channel_id=channel.id,
    )
    
# ---------- TICKETS: loaded from Modules/tickets.py ----------

@bot.command()
async def loading(ctx):
    """Example command with a loading animation."""
    # Send initial message
    msg = await ctx.send("Processing: -")
    
    # Frames for animation
    frames = ["-", "\\", "|", "/"]
    idx = 0

    # Simulate some processing time (replace this with your actual task)
    for _ in range(20):  # number of updates
        await asyncio.sleep(0.2)  # time between frames
        await msg.edit(content=f"Processing: {frames[idx % len(frames)]}")
        idx += 1

    # Done processing
    await msg.edit(content="✅ Done!")
    await asyncio.sleep(1)
    await msg.delete()  # Remove the message after showing it's done

LOGGING_FILE = os.path.join(_storage_dir, "Config", "logging.json")

# --- Load & Save ---
def load_logging_config():
    if not os.path.exists(LOGGING_FILE):
        return {"guilds": {}}
    with open(LOGGING_FILE, "r") as f:
        return json.load(f)

def save_logging_config(data):
    with open(LOGGING_FILE, "w") as f:
        json.dump(data, f, indent=2)

def sanitize_logging_config(config):
    for guild_id, guild_cfg in config.get("guilds", {}).items():
        log_events = guild_cfg.setdefault("log_events", {})
        for cmd, cmd_cfg in log_events.items():
            if "enabled" not in cmd_cfg:
                cmd_cfg["enabled"] = "channel_id" in cmd_cfg
    return config

logging_config = sanitize_logging_config(load_logging_config())

# --- Logging Decision Logic ---
def should_log(guild_id: str, event: str):
    cfg = logging_config.get("guilds", {}).get(guild_id, {})
    log_events = cfg.get("log_events", {})
    is_enabled = cfg.get("enabled") and log_events.get(event, {}).get("enabled", False)
    print(f"[DEBUG] should_log → guild_id={guild_id}, event={event}, result={is_enabled}")
    return is_enabled

# --- Extract Options for Slash Commands ---
def extract_options(data):
    options = {}
    def recurse(opts):
        for opt in opts:
            if opt["type"] == 1:
                recurse(opt.get("options", []))
            else:
                options[opt["name"]] = opt["value"]
    recurse(data.get("options", []))
    return options

# --- Generate Log Message ---
async def get_log_message(interaction: discord.Interaction):
    cmd_name = interaction.command.name
    user = interaction.user
    options = extract_options(interaction.data or {})
    target_id = options.get("member") or options.get("user")
    reason = options.get("reason", "No reason provided")

    target_mention = "Unknown"
    if target_id and interaction.guild:
        try:
            target = await interaction.guild.fetch_member(int(target_id))
            target_mention = target.mention
        except Exception:
            target_mention = f"<@{target_id}>"

    if cmd_name == "mute":
        return f"🔇 {user.mention} muted {target_mention} — Reason: {reason}"
    if cmd_name == "unmute":
        return f"🔈 {user.mention} unmuted {target_mention}"
    if cmd_name == "ban":
        return f"🔨 {user.mention} banned {target_mention} — Reason: {reason}"
    if cmd_name == "kick":
        return f"👢 {user.mention} kicked {target_mention} — Reason: {reason}"

    return f"📘 `{cmd_name}` command used by {user.mention}"

# --- Send Log to Channel ---
async def log_action(interaction: discord.Interaction, message: str) -> None:
    if not interaction.guild_id or not interaction.guild:
        return
    guild_id = str(interaction.guild_id)
    cfg = logging_config.get("guilds", {}).get(guild_id, {})
    log_events = cfg.get("log_events", {})
    event = interaction.command.name
    channel_id = (
        log_events.get(event, {}).get("channel_id")
        or cfg.get("log_channel_id")
    )
    if not channel_id:
        print(f"[DEBUG] No channel_id set for event '{event}' in guild {guild_id}")
        return
    channel = interaction.guild.get_channel(channel_id)
    if channel:
        try:
            await channel.send(message)
            print(f"[DEBUG] Sent log message to channel {channel_id}")
        except Exception as e:
            print(_redact_discord_token_in_text(f"[ERROR] Failed to send log message: {e}"))
    else:
        print(f"[DEBUG] Channel ID {channel_id} not found in guild {guild_id}")

# ----- UI Components -----

class LoggingCommandSelector(discord.ui.Select):
    def __init__(self, commands_list, guild):
        self.commands_list = commands_list
        self.guild = guild
        options = []
        for cmd in commands_list:
            # Show enabled/disabled and channel info if exists
            guild_cfg = logging_config["guilds"].get(str(guild.id), {})
            log_events = guild_cfg.get("log_events", {})
            cmd_cfg = log_events.get(cmd, {})
            enabled = cmd_cfg.get("enabled", False)
            channel_id = cmd_cfg.get("channel_id")
            channel_name = guild.get_channel(channel_id).name if channel_id and guild.get_channel(channel_id) else "None"
            label = f"{cmd} [{'ON' if enabled else 'OFF'}]"
            description = f"Logs to: #{channel_name}" if channel_id else "Not configured"
            options.append(discord.SelectOption(label=label, description=description, value=cmd))
        super().__init__(
            placeholder="Step 1: Select a command to configure ✅",
            options=options,
            custom_id="log_command_select"
        )

    async def callback(self, interaction: discord.Interaction):
        command = self.values[0]
        await interaction.response.edit_message(
            content=f"**Step 2: Choose a log channel and toggle logging for `{command}` ✅**",
            view=LogCommandView(command, self.guild, self.commands_list)
        )

class ChannelSelector(discord.ui.Select):
    def __init__(self, command_name: str, guild: discord.Guild):
        self.command_name = command_name
        self.guild = guild
        options = [
            discord.SelectOption(label=ch.name, value=str(ch.id))
            for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages
        ]
        super().__init__(
            placeholder="Choose a channel to log this command",
            options=options,
            custom_id=f"channel_selector_{command_name}"
        )

    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        guild_id = str(self.guild.id)
        guild_cfg = logging_config["guilds"].setdefault(guild_id, {})
        event_cfg = guild_cfg.setdefault("log_events", {}).setdefault(self.command_name, {})
        event_cfg["channel_id"] = channel_id
        # Enable logging automatically when channel selected
        event_cfg["enabled"] = True
        save_logging_config(logging_config)
        await interaction.response.send_message(f"✅ `{self.command_name}` will now log to <#{channel_id}>.", ephemeral=True)

class ToggleLogging(discord.ui.Button):
    def __init__(self, command_name: str, guild_id: str):
        self.command_name = command_name
        self.guild_id = guild_id
        current = logging_config["guilds"].get(guild_id, {}).get("log_events", {}).get(command_name, {}).get("enabled", False)
        label = "✅ Enabled" if current else "❌ Disabled"
        style = discord.ButtonStyle.green if current else discord.ButtonStyle.red
        super().__init__(label=label, style=style, custom_id=f"toggle_{command_name}")

    async def callback(self, interaction: discord.Interaction):
        guild_cfg = logging_config["guilds"].setdefault(self.guild_id, {})
        event_cfg = guild_cfg.setdefault("log_events", {}).setdefault(self.command_name, {})

        event_cfg["enabled"] = not event_cfg.get("enabled", False)

        # Ensure channel_id exists, else default to guild's log_channel_id (optional)
        if "channel_id" not in event_cfg:
            event_cfg["channel_id"] = guild_cfg.get("log_channel_id", None)

        save_logging_config(logging_config)

        status = "Enabled" if event_cfg["enabled"] else "Disabled"
        label = "✅ Enabled" if event_cfg["enabled"] else "❌ Disabled"
        style = discord.ButtonStyle.green if event_cfg["enabled"] else discord.ButtonStyle.red

        # Update button label and style
        self.label = label
        self.style = style

        await interaction.response.edit_message(content=f"Logging for `{self.command_name}` is now **{status}**.", view=self.view)

class AddAnotherCommandButton(discord.ui.Button):
    def __init__(self, commands_list, guild):
        super().__init__(label="Configure Another Command", style=discord.ButtonStyle.blurple)
        self.commands_list = commands_list
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        view = LoggingConfigView(self.commands_list, self.guild)
        await interaction.response.edit_message(content="🛠️ Choose log channels and toggle logging per command:", view=view)

class LogCommandView(discord.ui.View):
    def __init__(self, command_name, guild, commands_list):
        super().__init__(timeout=180)
        self.add_item(ChannelSelector(command_name, guild))
        self.add_item(ToggleLogging(command_name, str(guild.id)))
        self.add_item(AddAnotherCommandButton(commands_list, guild))

class LoggingConfigView(discord.ui.View):
    def __init__(self, commands_to_configure, guild):
        super().__init__(timeout=180)
        self.add_item(LoggingCommandSelector(commands_to_configure, guild))
        self.guild = guild

# Legacy in-file logging commands were removed.
# The active production logging system is loaded from Modules/logging.py.

from typing import Optional

def _get_themes_module():
    import importlib
    return importlib.import_module("Modules.themes")


async def _notify_user_moderation_dm(
    member: discord.Member,
    action: str,
    guild_name: str,
    guild_id: int = 0,
    reason: str | None = None,
    duration_text: str | None = None,
):
    action_lower = action.lower()
    if action_lower in ("ban", "kick", "timeout", "warn"):
        themes_mod = _get_themes_module()
        await themes_mod.send_themed_moderation_dm(
            member,
            guild_id,
            action_lower,
            guild_name,
            reason=reason,
            duration_text=duration_text,
        )
        return
    embed = discord.Embed(
        title=f"Moderation Notice: {action}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Server", value=guild_name, inline=False)
    if duration_text:
        embed.add_field(name="Duration", value=duration_text, inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
    else:
        embed.add_field(name="Reason", value="No reason provided", inline=False)
    try:
        await member.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

# ------------------- BAN COMMAND -------------------
@tree.command(name="ban", description="Ban or tempban a member.")
@app_commands.describe(
    member="The member to ban.",
    reason="Reason for the ban.",
    duration="Ban duration in minutes. Leave empty for permanent."
)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: Optional[str] = None,
    duration: Optional[int] = None,  # in minutes
):
    if member == interaction.user:
        return await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)
    if member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ You cannot ban someone with an equal or higher role.", ephemeral=True)

    try:
        await _notify_user_moderation_dm(
            member,
            "Ban",
            interaction.guild.name if interaction.guild else "Unknown Server",
            guild_id=interaction.guild.id if interaction.guild else 0,
            reason=reason,
            duration_text=(f"{duration} minute(s)" if duration else "Permanent"),
        )
        await member.ban(reason=reason)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            "❌ I don't have permission to ban this member.",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    except Exception as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            f"❌ Failed to ban {member}.",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)

    if duration:
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "🔨 {member} has been banned for {duration} minutes. Reason: {reason}",
            member=str(member),
            duration=str(duration),
            reason=reason or "No reason provided",
        )
        try:
            await interaction.response.send_message(msg)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await interaction.guild.unban(member, reason="Rollback: failed to send response")
            except Exception:
                pass
            try:
                await _send_app_error(
                    interaction,
                    "❌ Ban was applied but I couldn't confirm. The ban has been removed.",
                )
            except Exception:
                pass
            return
        _dispatch_module_log_event(
            interaction.guild,
            "moderation",
            "ban",
            actor=interaction.user,
            details=f"target_user_id={member.id}; duration_minutes={duration}; reason={reason or 'No reason provided'}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        await asyncio.sleep(duration * 60)
        await interaction.guild.unban(member)
    else:
        msg = get_command_response_for_interaction(
            interaction,
            "success_permanent",
            "🔨 {member} has been permanently banned. Reason: {reason}",
            member=str(member),
            reason=reason or "No reason provided",
        )
        try:
            await interaction.response.send_message(msg)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await interaction.guild.unban(member, reason="Rollback: failed to send response")
            except Exception:
                pass
            try:
                await _send_app_error(
                    interaction,
                    "❌ Ban was applied but I couldn't confirm. The ban has been removed.",
                )
            except Exception:
                pass
            return
        _dispatch_module_log_event(
            interaction.guild,
            "moderation",
            "ban",
            actor=interaction.user,
            details=f"target_user_id={member.id}; duration_minutes=permanent; reason={reason or 'No reason provided'}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )

# ------------------- UNBAN COMMAND -------------------
@tree.command(name="unban", description="Unban a user from the server by ID (Admin only)")
@app_commands.describe(user_id="The ID of the user to unban")
async def unban(interaction: discord.Interaction, user_id: str):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)

    try:
        user_id = int(user_id)
    except ValueError:
        return await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)

    try:
        # Convert async iterator to list
        bans = [b async for b in guild.bans()]
        ban_entry = next((b for b in bans if b.user.id == user_id), None)

        if not ban_entry:
            return await interaction.response.send_message(f"❌ No banned user with ID `{user_id}` found.", ephemeral=True)

        await guild.unban(ban_entry.user, reason=f"Unbanned by {interaction.user}")
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "✅ Successfully unbanned {user}.",
            user=str(ban_entry.user),
        )
        await interaction.response.send_message(msg)
        _dispatch_module_log_event(
            interaction.guild,
            "moderation",
            "unban",
            actor=interaction.user,
            details=f"target_user_id={ban_entry.user.id}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to unban this user.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(
            _redact_discord_token_in_text(f"❌ An error occurred: {e}"),
            ephemeral=True,
        )

@tree.command(name="legacy_giverole", description="(Legacy) Give a role to a user.")
@app_commands.describe(member="User to give the role to", role="Role to give")
async def giverole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if role in member.roles:
        await interaction.response.send_message(
            f"❌ {member.mention} already has the role {role.name}.", ephemeral=True)
        return

    reason_cannot = _can_add_role_reason(interaction.guild, member, role)
    if reason_cannot:
        details = _format_error_details(reason=reason_cannot)
        friendly, view = _error_details_view(
            f"❌ I cannot add that role to {member.mention}.",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await member.add_roles(role)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            f"❌ I cannot add that role to {member.mention} (missing permission or role hierarchy).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    msg = get_command_response_for_interaction(
        interaction,
        "success",
        "✅ Added role {role} to {member}.",
        role=role.name,
        member=member.mention,
    )
    try:
        await interaction.response.send_message(msg)
    except (discord.Forbidden, discord.HTTPException):
        try:
            await member.remove_roles(role, reason="Rollback: failed to send response")
        except Exception:
            pass
        try:
            await _send_app_error(
                interaction,
                f"❌ Role was added but I couldn't confirm. The role has been removed from {member.mention}.",
            )
        except Exception:
            pass
        return
    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "giverole",
        actor=interaction.user,
        details=f"target_user_id={member.id}; role_id={role.id}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )

@tree.command(name="legacy_removerole", description="(Legacy) Remove a role from a user.")
@app_commands.describe(member="User to remove the role from", role="Role to remove")
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if role not in member.roles:
        await interaction.response.send_message(
            f"❌ {member.mention} does not have the role {role.name}.", ephemeral=True)
        return

    reason_cannot = _can_add_role_reason(interaction.guild, member, role)
    if reason_cannot:
        details = _format_error_details(reason=reason_cannot)
        friendly, view = _error_details_view(
            f"❌ I cannot remove that role from {member.mention}.",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await member.remove_roles(role)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            f"❌ I cannot remove that role from {member.mention} (missing permission or role hierarchy).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    msg = get_command_response_for_interaction(
        interaction,
        "success",
        "✅ Removed role {role} from {member}.",
        role=role.name,
        member=member.mention,
    )
    try:
        await interaction.response.send_message(msg)
    except (discord.Forbidden, discord.HTTPException):
        try:
            await member.add_roles(role, reason="Rollback: failed to send response")
        except Exception:
            pass
        try:
            await _send_app_error(
                interaction,
                f"❌ Role was removed but I couldn't confirm. The role has been restored to {member.mention}.",
            )
        except Exception:
            pass
        return
    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "removerole",
        actor=interaction.user,
        details=f"target_user_id={member.id}; role_id={role.id}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )
        

autorole_config = {}  # guild_id: {event_key: [role_ids]}

def save_autorole_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(autorole_config, f, indent=4)

def load_autorole_config():
    global autorole_config
    try:
        with open(CONFIG_FILE, "r") as f:
            autorole_config = json.load(f)
    except FileNotFoundError:
        autorole_config = {}

load_autorole_config()

class EventDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Member Join", value="member_join"),
            discord.SelectOption(label="Message Sent", value="message_sent"),
            discord.SelectOption(label="Thread Opened", value="thread_opened"),
            discord.SelectOption(label="Voice Channel Join", value="voice_join"),
            discord.SelectOption(label="Boost Server", value="boost"),
            discord.SelectOption(label="Reaction Added", value="reaction"),
            discord.SelectOption(label="Account Age Verified", value="verified"),
            discord.SelectOption(label="First Slash Command Used", value="slash"),
        ]
        super().__init__(placeholder="Choose a trigger event…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_event = self.values[0]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Event Selected",
                description=f"Selected event: `{self.view.selected_event.replace('_', ' ').title()}`",
                color=discord.Color.green()
            ),
            ephemeral=True
        )

# Autorole View
class AutoRoleView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=90)
        self.guild = guild
        self.selected_event = None
        self.selected_roles = []
        self.add_item(EventDropdown())
        self.add_item(self.RoleSelect())
        self.add_item(self.RemoveButton())
        self.add_item(self.SubmitButton())  # Submit moved to bottom

    class RoleSelect(discord.ui.RoleSelect):
        def __init__(self):
            super().__init__(placeholder="Select role(s) to give", min_values=1, max_values=5)

        async def callback(self, interaction: discord.Interaction):
            self.view.selected_roles = self.values
            role_mentions = ", ".join([role.mention for role in self.view.selected_roles])
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Roles Selected",
                    description=f"Selected role(s): {role_mentions}",
                    color=discord.Color.green()
                ),
                ephemeral=True
            )

    class SubmitButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Submit", style=discord.ButtonStyle.green)

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if not view.selected_event or not view.selected_roles:
                await interaction.response.send_message(
                    "⚠️ Please select both an event and at least one role.",
                    ephemeral=True
                )
                return

            guild_id = str(interaction.guild.id)
            if guild_id not in autorole_config:
                autorole_config[guild_id] = {}

            autorole_config[guild_id][view.selected_event] = [role.id for role in view.selected_roles]
            save_autorole_config()

            roles_display = ", ".join(role.mention for role in view.selected_roles)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Autoroles Saved",
                    description=f"**Event:** `{view.selected_event}`\n**Roles:** {roles_display}",
                    color=discord.Color.blurple()
                ),
                ephemeral=True
            )

    class RemoveButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Remove Autorole", style=discord.ButtonStyle.red)

        async def callback(self, interaction: discord.Interaction):
            guild_id = str(interaction.guild.id)
            config = autorole_config.get(guild_id, {})

            if not config:
                await interaction.response.send_message("❌ No autoroles to remove.", ephemeral=True)
                return

            options = []
            for event, roles in config.items():
                for role_id in roles:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        options.append(discord.SelectOption(label=f"{event} – {role.name}", value=f"{event}:{role_id}"))

            class RemoveDropdown(discord.ui.Select):
                def __init__(self):
                    super().__init__(placeholder="Select autorole to remove", options=options)

                async def callback(inner_self, interaction: discord.Interaction):
                    val = inner_self.values[0]
                    event, role_id = val.split(":")
                    role_id = int(role_id)
                    autorole_config[guild_id][event].remove(role_id)
                    if not autorole_config[guild_id][event]:
                        del autorole_config[guild_id][event]
                    save_autorole_config()
                    await interaction.response.send_message(
                        f"🗑️ Removed autorole for `{event}` (role ID: {role_id})",
                        ephemeral=True
                    )
                    self.view.stop()

            remove_view = discord.ui.View(timeout=30)
            remove_view.add_item(RemoveDropdown())
            await interaction.response.send_message("🗑️ Choose an autorole to remove:", view=remove_view, ephemeral=True)

MUTEROLE_FILE = os.path.join(_storage_dir, "Config", "mute_roles.json")

def load_mute_config():
    try:
        with open(MUTEROLE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_mute_config(config):
    with open(MUTEROLE_FILE, "w") as f:
        json.dump(config, f, indent=2)

mute_config = load_mute_config()

# === Mute Commands ===

@tree.command(name="mute", description="Mute a member for a specified time.")
@app_commands.describe(member="User to mute", duration="Duration (number)", unit="Time unit: m for minutes, h for hours", reason="Reason for muting")
async def mute(interaction: discord.Interaction, member: discord.Member, duration: int, unit: str, reason: str = None):
    unit = unit.lower()
    if unit not in ("m", "h"):
        await interaction.response.send_message("❌ Invalid time unit. Use 'm' for minutes or 'h' for hours.", ephemeral=True)
        return

    duration_seconds = duration * 60 if unit == "m" else duration * 3600
    guild_id = str(interaction.guild_id)
    mute_role_id = mute_config.get(guild_id)

    if not mute_role_id:
        await interaction.response.send_message("⚠️ Mute role not set. Use `/muterole_create` or `/muterole_update`.", ephemeral=True)
        return

    mute_role = interaction.guild.get_role(mute_role_id)
    if not mute_role:
        await interaction.response.send_message("⚠️ Mute role does not exist. Please update it again.", ephemeral=True)
        return

    if mute_role in member.roles:
        await interaction.response.send_message(f"❌ {member.mention} is already muted.", ephemeral=True)
        return

    reason_cannot = _can_add_role_reason(interaction.guild, member, mute_role)
    if reason_cannot:
        details = _format_error_details(reason=reason_cannot)
        friendly, view = _error_details_view(
            f"❌ I cannot mute {member.mention}.",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await member.add_roles(mute_role, reason=reason)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            f"❌ I cannot add the mute role to {member.mention} (missing permission or role hierarchy).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    await _notify_user_moderation_dm(
        member,
        "Mute",
        interaction.guild.name if interaction.guild else "Unknown Server",
        guild_id=interaction.guild.id if interaction.guild else 0,
        reason=reason,
        duration_text=f"{duration}{unit}",
    )
    msg = get_command_response_for_interaction(
        interaction,
        "success",
        "🔇 {member} has been muted for {duration}{unit}. Reason: {reason}",
        member=member.mention,
        duration=str(duration),
        unit=unit,
        reason=reason or "No reason provided",
    )
    try:
        await interaction.response.send_message(msg)
    except (discord.Forbidden, discord.HTTPException):
        try:
            await member.remove_roles(mute_role, reason="Rollback: failed to send response")
        except Exception:
            pass
        try:
            await _send_app_error(
                interaction,
                "❌ Mute was applied but I couldn't confirm in this channel. The mute has been removed.",
            )
        except Exception:
            pass
        return
    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "mute",
        actor=interaction.user,
        details=f"target_user_id={member.id}; duration={duration}{unit}; reason={reason or 'No reason provided'}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )

    await asyncio.sleep(duration_seconds)
    await member.remove_roles(mute_role, reason="Mute expired")
    await _notify_user_moderation_dm(
        member,
        "Unmute",
        interaction.guild.name if interaction.guild else "Unknown Server",
        guild_id=interaction.guild.id if interaction.guild else 0,
        reason="Mute duration expired",
    )


@tree.command(name="unmute", description="Unmute a member manually")
@app_commands.describe(member="User to unmute")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    guild_id = str(interaction.guild_id)
    mute_role_id = mute_config.get(guild_id)
    if not mute_role_id:
        await interaction.response.send_message("⚠️ No mute role configured.", ephemeral=True)
        return

    mute_role = interaction.guild.get_role(mute_role_id)
    if mute_role in member.roles:
        reason_cannot = _can_add_role_reason(interaction.guild, member, mute_role)
        if reason_cannot:
            details = _format_error_details(reason=reason_cannot)
            friendly, view = _error_details_view(
                f"❌ I cannot unmute {member.mention}.",
                details,
            )
            return await interaction.response.send_message(friendly, ephemeral=True, view=view)
        try:
            await member.remove_roles(mute_role, reason="Manual unmute")
        except discord.Forbidden as e:
            details = _format_error_details(exc=e)
            friendly, view = _error_details_view(
                f"❌ I cannot remove the mute role from {member.mention} (missing permission or role hierarchy).",
                details,
            )
            return await interaction.response.send_message(friendly, ephemeral=True, view=view)
        await _notify_user_moderation_dm(
            member,
            "Unmute",
            interaction.guild.name if interaction.guild else "Unknown Server",
            guild_id=interaction.guild.id if interaction.guild else 0,
            reason="Manual unmute by staff",
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "🔊 {member} has been unmuted.",
            member=member.mention,
        )
        await interaction.response.send_message(msg)
        _dispatch_module_log_event(
            interaction.guild,
            "moderation",
            "unmute",
            actor=interaction.user,
            details=f"target_user_id={member.id}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
    else:
        await interaction.response.send_message(f"❌ {member.mention} is not muted.", ephemeral=True)


muterole_group = app_commands.Group(name="muterole", description="Mute role management commands")


@muterole_group.command(name="create", description="Create and set a mute role")
async def muterole_create(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    try:
        role = await guild.create_role(name="Muted", reason="Mute role creation")
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            "❌ I don't have permission to create roles. Grant **Manage Roles** and ensure my role is high enough.",
            details,
        )
        return await interaction.followup.send(friendly, ephemeral=True, view=view)
    for channel in guild.channels:
        try:
            await channel.set_permissions(role, send_messages=False, speak=False, add_reactions=False)
        except Exception:
            continue

    mute_config[str(guild.id)] = role.id
    save_mute_config(mute_config)
    msg = get_command_response_for_interaction(
        interaction,
        "success",
        "✅ Created and set mute role: `{role}`",
        role=role.name,
    )
    try:
        await interaction.followup.send(msg, ephemeral=True)
    except (discord.Forbidden, discord.HTTPException):
        try:
            await role.delete(reason="Rollback: failed to send response")
            del mute_config[str(guild.id)]
            save_mute_config(mute_config)
        except Exception:
            pass
        try:
            await interaction.followup.send(
                "❌ Mute role was created but I couldn't confirm. The role has been deleted.",
                ephemeral=True,
            )
        except Exception:
            pass
        return
    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "muterole_create",
        actor=interaction.user,
        details=f"role_id={role.id}; role_name={role.name}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )


@muterole_group.command(name="update", description="Update the mute role")
@app_commands.describe(role="The new mute role")
async def muterole_update(interaction: discord.Interaction, role: discord.Role):
    mute_config[str(interaction.guild_id)] = role.id
    save_mute_config(mute_config)
    msg = get_command_response_for_interaction(
        interaction,
        "success",
        "🔄 Mute role updated to `{role}`",
        role=role.name,
    )
    await interaction.response.send_message(msg, ephemeral=True)
    _dispatch_module_log_event(
        interaction.guild,
        "moderation",
        "muterole_update",
        actor=interaction.user,
        details=f"role_id={role.id}; role_name={role.name}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )


tree.add_command(muterole_group)


@tree.command(name="hardmute", description="Mute and remove all roles from a user")
@app_commands.describe(member="Member to hardmute", reason="Reason for hardmute")
async def hardmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    guild_id = str(interaction.guild_id)
    mute_role_id = mute_config.get(guild_id)
    if not mute_role_id:
        await interaction.response.send_message("⚠️ Mute role not set.", ephemeral=True)
        return

    mute_role = interaction.guild.get_role(mute_role_id)
    roles_to_remove = [r for r in member.roles if r != interaction.guild.default_role and r != mute_role]

    reason_cannot = _can_add_role_reason(interaction.guild, member, mute_role)
    if reason_cannot:
        details = _format_error_details(reason=reason_cannot)
        friendly, view = _error_details_view(
            f"❌ I cannot hardmute {member.mention}.",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await member.remove_roles(*roles_to_remove, reason="Hardmute")
        try:
            await member.add_roles(mute_role, reason=reason)
        except discord.Forbidden as e:
            try:
                await member.add_roles(*roles_to_remove, reason="Rollback: add mute role failed")
            except Exception:
                pass
            details = _format_error_details(exc=e)
            friendly, view = _error_details_view(
                "❌ I cannot add the mute role (missing permission or role hierarchy). Roles were restored.",
                details,
            )
            return await interaction.response.send_message(friendly, ephemeral=True, view=view)
        await _notify_user_moderation_dm(
            member,
            "Hardmute",
            interaction.guild.name if interaction.guild else "Unknown Server",
            guild_id=interaction.guild.id if interaction.guild else 0,
            reason=reason,
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "🔇 {member} has been hardmuted. Reason: {reason}",
            member=member.mention,
            reason=reason,
        )
        await interaction.response.send_message(msg)
        _dispatch_module_log_event(
            interaction.guild,
            "moderation",
            "hardmute",
            actor=interaction.user,
            details=f"target_user_id={member.id}; removed_roles={len(roles_to_remove)}; reason={reason}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            "❌ Missing permission to manage roles (or role hierarchy).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)

# Event Handlers
@bot.event
async def on_member_join(member):
    await _get_automod_module().process_member_join(member)

@bot.event
async def on_thread_join(thread, member):
    return

@bot.event
async def on_voice_state_update_stub(member, before, after):
    return

@bot.event
async def on_member_update(before, after):
    return

autoroles = set()  # Store roles IDs for auto-assigning

marriages = {}  # user_id -> partner_id
marriage_requests = {}  # user_id -> requester_id (pending requests)

reminders = []
timers = {}  # timer_id -> {user_id, end}
_timer_counter = 0
_timer_lock = asyncio.Lock()

REMINDERS_FILE = os.path.join(_storage_dir, "Data", "reminders.json")
TIMERS_FILE = os.path.join(_storage_dir, "Data", "timers.json")
_REMINDERS_TIMERS_LOADED = False


def _save_reminders() -> None:
    data = [
        {"user_id": r["user_id"], "message": r["message"], "time": r["time"].isoformat()}
        for r in reminders
    ]
    save_json(REMINDERS_FILE, data)


def _save_timers() -> None:
    data = {
        str(tid): {"user_id": t["user_id"], "end": t["end"].isoformat()}
        for tid, t in timers.items()
    }
    save_json(TIMERS_FILE, data)


async def _run_loaded_timer(timer_id: int, user_id: int, end: datetime) -> None:
    """Run a timer loaded from disk until it expires."""
    delay = (end - datetime.now()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    if timer_id in timers:
        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            await user.send(f"\u23F0 Timer #{timer_id} is up!")
        except Exception:
            pass
        del timers[timer_id]
        _save_timers()


def _load_reminders_and_timers() -> None:
    """Load reminders and timers from disk (call from on_ready, once)."""
    global _timer_counter, _REMINDERS_TIMERS_LOADED
    if _REMINDERS_TIMERS_LOADED:
        return
    _REMINDERS_TIMERS_LOADED = True
    data = load_json(REMINDERS_FILE, [])
    for r in data:
        try:
            reminders.append({
                "user_id": r["user_id"],
                "message": r["message"],
                "time": datetime.fromisoformat(r["time"]),
            })
        except (KeyError, ValueError):
            pass
    data = load_json(TIMERS_FILE, {})
    for tid_str, t in data.items():
        try:
            tid = int(tid_str)
            end = datetime.fromisoformat(t["end"])
            if end <= datetime.now():
                continue
            timers[tid] = {"user_id": t["user_id"], "end": end}
            _timer_counter = max(_timer_counter, tid)
            asyncio.create_task(_run_loaded_timer(tid, t["user_id"], end))
        except (KeyError, ValueError):
            pass


@tasks.loop(seconds=60)
async def reminder_checker():
    now = datetime.now()
    for reminder in reminders[:]:
        if reminder["time"] <= now:
            try:
                user = bot.get_user(reminder["user_id"]) or await bot.fetch_user(reminder["user_id"])
                await user.send(f"\u23F0 Reminder: {reminder['message']}")
            except Exception:
                pass
            reminders.remove(reminder)
            _save_reminders()

@tree.command(name="remindme", description="Set a reminder with a message and time.")
@app_commands.describe(message="Reminder text", date="Date (MM/DD/YY)", time="Time (HH:MM 24hr)")
async def remindme(interaction: discord.Interaction, message: str, date: str, time: str):
    try:
        remind_time = datetime.strptime(f"{date} {time}", "%m/%d/%y %H:%M")
        reminders.append({"user_id": interaction.user.id, "message": message, "time": remind_time})
        _save_reminders()
        await interaction.response.send_message(f"\u2705 Reminder set for {remind_time.strftime('%m/%d/%y %H:%M')}", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("\u274C Invalid date or time format. Use MM/DD/YY HH:MM.", ephemeral=True)

@tree.command(name="starttimer", description="Start a timer using s, m, or h (e.g., 10s, 5m, 2h)")
@app_commands.describe(duration="Duration of the timer like 10s, 5m, or 1h")
async def starttimer(interaction: discord.Interaction, duration: str):
    try:
        unit = duration[-1]
        value = int(duration[:-1])

        if unit == 's': seconds = value
        elif unit == 'm': seconds = value * 60
        elif unit == 'h': seconds = value * 3600
        else: raise ValueError("Invalid unit")

        async with _timer_lock:
            global _timer_counter
            _timer_counter += 1
            timer_id = _timer_counter
        end_time = datetime.now() + timedelta(seconds=seconds)
        timers[timer_id] = {"user_id": interaction.user.id, "end": end_time}
        _save_timers()

        await interaction.response.send_message(f"\u23F3 Timer #{timer_id} started for {value}{unit}", ephemeral=True)
        await asyncio.sleep(seconds)

        if timer_id in timers:
            try:
                user = bot.get_user(timers[timer_id]["user_id"]) or await bot.fetch_user(timers[timer_id]["user_id"])
                await user.send(f"\u23F0 Timer #{timer_id} is up!")
            except Exception:
                pass
            del timers[timer_id]
            _save_timers()

    except ValueError:
        await interaction.response.send_message("\u274C Invalid format. Use like `10s`, `5m`, or `2h`.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(
            _redact_discord_token_in_text(f"\u274C An error occurred: {e}"),
            ephemeral=True,
        )

@tree.command(name="checktimers", description="Check your active timers.")
async def checktimers(interaction: discord.Interaction):
    user_timers = [
        f"\u23F3 Timer #{tid} ends at <t:{int(timer['end'].timestamp())}:R>"
        for tid, timer in timers.items()
        if timer["user_id"] == interaction.user.id
    ]
    
    if user_timers:
        await interaction.response.send_message("\n".join(user_timers), ephemeral=True)
    else:
        await interaction.response.send_message("\u274C You have no active timers.", ephemeral=True)

@tree.command(name="endtimer", description="Cancel a running timer by ID.")
@app_commands.describe(timer_id="ID of the timer to cancel")
async def endtimer(interaction: discord.Interaction, timer_id: int):
    timer = timers.get(timer_id)
    if not timer:
        await interaction.response.send_message("\u274C Timer not found.", ephemeral=True)
        return

    if timer["user_id"] != interaction.user.id:
        await interaction.response.send_message("\u26D4 You can only end your own timers.", ephemeral=True)
        return

    del timers[timer_id]
    _save_timers()
    await interaction.response.send_message(f"\u23F9 Timer #{timer_id} has been cancelled.", ephemeral=True)

class SayView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel_select = discord.ui.Select(
            placeholder="Choose a channel to send message",
            options=[
                discord.SelectOption(label=channel.name, value=str(channel.id))
                for channel in guild.text_channels if channel.permissions_for(guild.me).send_messages
            ]
        )
        self.channel_select.callback = self.select_channel
        self.add_item(self.channel_select)

        self.message_input = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph)
        self.modal = discord.ui.Modal(title="Type your message")
        self.modal.add_item(self.message_input)
        self.modal.on_submit = self.send_message

    async def select_channel(self, interaction: discord.Interaction):
        self.selected_channel = self.guild.get_channel(int(self.channel_select.values[0]))
        await interaction.response.send_modal(self.modal)

    async def send_message(self, interaction: discord.Interaction):
        await self.selected_channel.send(self.message_input.value)
        await interaction.response.send_message("✅ Message sent!", ephemeral=True)


DM_OPTOUT_FOOTER = "\n\n_Use `/optout` in any server with this bot to stop receiving these messages._"


class DmModal(discord.ui.Modal, title="Type your DM"):
    def __init__(self, selected_user: discord.Member, guild: discord.Guild, staff_user: discord.User, anonymous: bool):
        super().__init__()
        self.selected_user = selected_user
        self.guild = guild
        self.staff_user = staff_user
        self.anonymous = anonymous
        self.msg_input = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.msg_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if str(self.selected_user.id) in _dm_optout_ids():
            await interaction.response.send_message(
                f"❌ {self.selected_user.display_name} has opted out of receiving staff DMs.",
                ephemeral=True,
            )
            return
        if self.anonymous:
            header = f"📬 **Anonymous message sent by a user in {self.guild.name}:**"
        else:
            header = f"📬 **Message sent by {self.staff_user} in {self.guild.name}:**"
        body = f"{header}\n{self.msg_input.value}{DM_OPTOUT_FOOTER}"
        try:
            await self.selected_user.send(body)
            await interaction.response.send_message("✅ Message sent!", ephemeral=True)
            if "log_action" in globals():
                author_label = "anonymous" if self.anonymous else str(self.staff_user)
                await log_action(
                    interaction,
                    f"📨 **DM sent** to {self.selected_user.mention} by {author_label} - Content: {self.msg_input.value}",
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Cannot DM {self.selected_user.display_name} (DMs disabled or blocked).",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                _redact_discord_token_in_text(f"❌ Error: {e}"),
                ephemeral=True,
            )


class DmAuthorChoiceView(discord.ui.View):
    def __init__(self, selected_user: discord.Member, guild: discord.Guild, staff_user: discord.User):
        super().__init__(timeout=60)
        self.selected_user = selected_user
        self.guild = guild
        self.staff_user = staff_user

    @discord.ui.button(label="Send as myself", style=discord.ButtonStyle.primary)
    async def as_myself(self, interaction: discord.Interaction, _button: discord.ui.Button):
        modal = DmModal(self.selected_user, self.guild, self.staff_user, anonymous=False)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Send anonymously", style=discord.ButtonStyle.secondary)
    async def as_anonymous(self, interaction: discord.Interaction, _button: discord.ui.Button):
        modal = DmModal(self.selected_user, self.guild, self.staff_user, anonymous=True)
        await interaction.response.send_modal(modal)


class DmView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=60)
        self.guild = guild
        options = [
            discord.SelectOption(label=member.display_name, value=str(member.id))
            for member in guild.members
            if not member.bot
        ][:25]
        self.member_select = discord.ui.Select(placeholder="Choose a user to DM", options=options)
        self.member_select.callback = self.select_member
        self.add_item(self.member_select)

    async def select_member(self, interaction: discord.Interaction) -> None:
        selected_user = self.guild.get_member(int(self.member_select.values[0]))
        if not selected_user:
            await interaction.response.send_message("❌ User not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Send to {selected_user.mention} as:",
            view=DmAuthorChoiceView(selected_user, self.guild, interaction.user),
            ephemeral=True,
        )

from discord.ui import Button, View
from discord import Interaction

from discord.ext import commands, tasks

@tree.command(name="autorole_legacy", description="Legacy autorole command (deprecated).")
async def autorole(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    config = autorole_config.get(guild_id, {})

    embed = discord.Embed(title="📋 Current Autorole Settings", color=discord.Color.gold())
    if config:
        for event, role_ids in config.items():
            role_mentions = [interaction.guild.get_role(rid).mention for rid in role_ids if interaction.guild.get_role(rid)]
            embed.add_field(name=event.replace('_', ' ').title(), value=", ".join(role_mentions), inline=False)
    else:
        embed.description = "No autoroles configured yet."

    await interaction.response.send_message(
        embed=embed,
        view=AutoRoleView(interaction.guild),
        ephemeral=True
    )
    _dispatch_module_log_event(
        interaction.guild,
        "autorole",
        "open_config",
        actor=interaction.user,
        details="Opened autorole configuration panel",
        channel_id=interaction.channel.id if interaction.channel else None,
    )

@tree.command(name="setautorole_legacy", description="Legacy autorole setup command (deprecated).")
@app_commands.describe(
    role="Role to be auto-managed",
    event="When to apply the role (on_join, on_message, on_thread)",
    action="Add or remove the role"
)
async def setautorole(interaction: discord.Interaction, role: discord.Role, event: str, action: str):
    if event not in ["on_join", "on_message", "on_thread"]:
        await interaction.response.send_message(
            "❌ Invalid event type. Choose from: on_join, , on_thread.",
            ephemeral=True
        )
        return

    if action not in ["add", "remove"]:
        await interaction.response.send_message(
            "❌ Invalid action. Choose 'add' or 'remove'.",
            ephemeral=True
        )
        return

    autorole_config[str(interaction.guild.id)] = {
        "role_id": role.id,
        "event": event,
        "action": action
    }

    await interaction.response.send_message(
        f"✅ Autorole set to {action} {role.name} on {event}.",
        ephemeral=True
    )
    _dispatch_module_log_event(
        interaction.guild,
        "autorole",
        "set",
        actor=interaction.user,
        details=f"role_id={role.id}; event={event}; action={action}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )

@bot.event
async def on_interaction(interaction: discord.Interaction):
    data = interaction.data if isinstance(interaction.data, dict) else {}
    if data.get("custom_id") == "open_setup_menu":
        await interaction.response.send_message(
            "**Setup Menu**\n\n"
            "1. Run `/logging setup` to choose your logging channel.\n"
            "2. Run `/verifyconfig` to set your verification method.\n"
            "3. Run `/autorole` if you want automatic roles.\n"
            "4. Use `/help` for the full command list.\n\n"
            "You're all set ☕",
            ephemeral=True
        )

@tree.command(name="say", description="Send a message as the bot to a specific channel")
async def say(interaction: discord.Interaction):
    await interaction.response.send_message("Select a channel to send a message:", view=SayView(interaction.guild), ephemeral=True)


@tree.command(name="dm", description="Send a DM as the bot (requires Moderate Members)")
async def dm(interaction: discord.Interaction):
    await interaction.response.send_message("Select a user to DM:", view=DmView(interaction.guild), ephemeral=True)


@tree.command(name="optout", description="Opt out of receiving staff DMs from this bot")
async def optout(interaction: discord.Interaction) -> None:
    _dm_optout_add(interaction.user.id)
    await interaction.response.send_message(
        "✅ You have opted out of receiving staff DMs. Use `/optin` to opt back in.",
        ephemeral=True,
    )


@tree.command(name="optin", description="Opt back in to receiving staff DMs from this bot")
async def optin(interaction: discord.Interaction) -> None:
    _dm_optout_remove(interaction.user.id)
    await interaction.response.send_message(
        "✅ You have opted back in to receiving staff DMs.",
        ephemeral=True,
    )


@tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="The poll question", duration_minutes="How many minutes the poll will last")
async def poll(interaction: discord.Interaction, question: str, duration_minutes: int):
    class PollChannelSelect(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label=channel.name, value=str(channel.id))
                for channel in interaction.guild.text_channels if channel.permissions_for(interaction.guild.me).send_messages
            ][:25]
            super().__init__(placeholder="Select a channel to send the poll to...", options=options)

        async def callback(self, i: discord.Interaction):
            channel = interaction.guild.get_channel(int(self.values[0]))
            embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blue())
            embed.set_footer(text=f"Poll ends in {duration_minutes} minute(s).")
            msg = await channel.send(embed=embed)
            await msg.add_reaction("👍")
            await msg.add_reaction("👎")
            _dispatch_module_log_event(
                interaction.guild,
                "polls",
                "create",
                actor=interaction.user,
                details=f"duration_minutes={duration_minutes}; question={question[:200]}",
                channel_id=channel.id,
            )
            await i.response.send_message(f"✅ Poll sent to {channel.mention}", ephemeral=True)
            await asyncio.sleep(duration_minutes * 60)
            try:
                await msg.clear_reactions()
                await channel.send("🛑 Poll ended! Thanks for voting.")
                _dispatch_module_log_event(
                    interaction.guild,
                    "polls",
                    "end",
                    actor=interaction.user,
                    details=f"duration_minutes={duration_minutes}; poll_message_id={msg.id}",
                    channel_id=channel.id,
                )
            except discord.Forbidden:
                await channel.send("⚠️ I do not have permission to clear reactions.")

    class PollChannelView(discord.ui.View):
        def __init__(self):
            super().__init__()
            self.add_item(PollChannelSelect())

    await interaction.response.send_message("📊 Choose a channel to send the poll:", view=PollChannelView(), ephemeral=True)

VERIFY_FILE = os.path.join(_storage_dir, "Config", "verify_config.json")

# ----------------------------------------
# UTILITIES
# ----------------------------------------
def load_verify_config():
    try:
        with open(VERIFY_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_verify_config(data):
    with open(VERIFY_FILE, "w") as f:
        json.dump(data, f, indent=4)


_VERIFY_LEGACY_START_CUSTOM_ID = "verify_start_button"
_VERIFY_START_CUSTOM_ID_PREFIX = "coffeecord_verify_"


# ----------------------------------------
# 1️⃣ SIMPLE BUTTON VERIFY
# ----------------------------------------
class SimpleButtonView(discord.ui.View):
    def __init__(self, user, role, log_channel):
        super().__init__(timeout=None)
        self.user = user
        self.role = role
        self.log_channel = log_channel

    @discord.ui.button(label="Verify Me ✅", style=discord.ButtonStyle.success)
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            return await interaction.response.send_message("❌ This isn’t for you.", ephemeral=True)
        try:
            await self.user.add_roles(self.role)
        except discord.Forbidden as e:
            details = _format_error_details(exc=e)
            friendly, view = _error_details_view(
                "❌ I cannot assign the role (missing permission or role hierarchy).",
                details,
            )
            return await interaction.response.send_message(friendly, ephemeral=True, view=view)
        await interaction.response.edit_message(content="✅ You’ve been verified!", view=None)
        _dispatch_module_log_event(
            interaction.guild,
            "verification",
            "verify_success",
            actor=self.user,
            details="method=button",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
        if self.log_channel:
            await self.log_channel.send(f"✅ {self.user.mention} verified via **Simple Button**.")


async def run_button_verify(interaction, user, role, log_channel):
    await interaction.response.send_message(
        "Press the button below to verify yourself:",
        view=SimpleButtonView(user, role, log_channel),
        ephemeral=True
    )


# ----------------------------------------
# 2️⃣ KEYPAD CODE VERIFY
# ----------------------------------------
class CodeVerifyModal(discord.ui.Modal, title="Enter Verification Code"):
    def __init__(self, user, correct_code, role, log_channel):
        super().__init__(timeout=None)
        self.user = user
        self.correct_code = correct_code
        self.role = role
        self.log_channel = log_channel

        self.code_input = discord.ui.TextInput(label="Verification Code", placeholder="Enter your 4-digit code")
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.code_input.value.strip() == self.correct_code:
            try:
                await self.user.add_roles(self.role)
            except discord.Forbidden as e:
                details = _format_error_details(exc=e)
                friendly, view = _error_details_view(
                    "❌ I cannot assign the role (missing permission or role hierarchy).",
                    details,
                )
                return await interaction.response.send_message(friendly, ephemeral=True, view=view)
            await interaction.response.send_message("✅ Verification successful!", ephemeral=True)
            _dispatch_module_log_event(
                interaction.guild,
                "verification",
                "verify_success",
                actor=self.user,
                details="method=code",
                channel_id=interaction.channel.id if interaction.channel else None,
            )
            if self.log_channel:
                await self.log_channel.send(f"✅ {self.user.mention} verified via **Code Method**.")
        else:
            await interaction.response.send_message("❌ Incorrect code. Try again later.", ephemeral=True)
            _dispatch_module_log_event(
                interaction.guild,
                "verification",
                "verify_fail",
                actor=self.user,
                details="method=code; reason=incorrect_code",
                channel_id=interaction.channel.id if interaction.channel else None,
            )


class CodeVerifyView(discord.ui.View):
    def __init__(self, user, code, role, log_channel):
        super().__init__(timeout=None)
        self.user = user
        self.code = code
        self.role = role
        self.log_channel = log_channel

    @discord.ui.button(label="Enter Code 🔢", style=discord.ButtonStyle.primary)
    async def enter_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await interaction.response.send_modal(CodeVerifyModal(self.user, self.code, self.role, self.log_channel))


async def run_code_verify(interaction, user, role, log_channel):
    code = "".join(random.choices("0123456789", k=4))
    try:
        await user.send(f"🔢 Your verification code for **{interaction.guild.name}**: `{code}`")
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ I can’t DM you the code. Please enable DMs or authorize me and try again.", ephemeral=True
        )

    await interaction.response.send_message(
        "📩 Check your DMs for the 4-digit code, then press below:",
        view=CodeVerifyView(user, code, role, log_channel),
        ephemeral=True
    )


# ------------------------------------------------
# 3️⃣ COLOR VERIFY
# ------------------------------------------------
class ColorVerifyButtons(discord.ui.View):
    def __init__(self, user, correct_color, role, log_channel):
        super().__init__(timeout=None)
        self.user = user
        self.correct_color = correct_color
        self.role = role
        self.log_channel = log_channel

        colors = ["Red", "Blue", "Green", "Yellow"]
        random.shuffle(colors)

        for color in colors:
            self.add_item(ColorButton(color, self))

class ColorButton(discord.ui.Button):
    def __init__(self, color, parent):
        super().__init__(label=color, style=discord.ButtonStyle.primary)
        self.color = color
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.parent.user:
            return await interaction.response.send_message("❌ This isn't your verification session.", ephemeral=True)

        if self.color == self.parent.correct_color:
            try:
                await self.parent.user.add_roles(self.parent.role)
            except discord.Forbidden as e:
                details = _format_error_details(exc=e)
                friendly, view = _error_details_view(
                    "❌ I cannot assign the role (missing permission or role hierarchy).",
                    details,
                )
                return await interaction.response.send_message(friendly, ephemeral=True, view=view)
            await interaction.response.send_message(f"✅ Correct! You’ve been verified.", ephemeral=True)
            _dispatch_module_log_event(
                interaction.guild,
                "verification",
                "verify_success",
                actor=self.parent.user,
                details="method=color",
                channel_id=interaction.channel.id if interaction.channel else None,
            )
            if self.parent.log_channel:
                await self.parent.log_channel.send(
                    f"✅ {self.parent.user.mention} was verified via **Color Method**."
                )
        else:
            await interaction.response.send_message("❌ Incorrect color. Try again!", ephemeral=True)
            _dispatch_module_log_event(
                interaction.guild,
                "verification",
                "verify_fail",
                actor=self.parent.user,
                details="method=color; reason=incorrect_color",
                channel_id=interaction.channel.id if interaction.channel else None,
            )


async def run_color_verify(interaction, user, role, log_channel):
    colors = ["Red", "Blue", "Green", "Yellow"]
    correct_color = random.choice(colors)

    try:
        await user.send(
            f"🎨 Your verification color for **{interaction.guild.name}** is **{correct_color}**.\n"
            f"Go back and click the **{correct_color}** button in the server!"
        )
        await interaction.response.send_message(
            "📩 I’ve sent you a DM with the color you need to click!", ephemeral=True
        )
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ I couldn’t DM you! Please enable DMs from server members and try again.",
            ephemeral=True
        )

    view = ColorVerifyButtons(user, correct_color, role, log_channel)
    await interaction.followup.send(
        "🎨 Click the button with your assigned color to verify!", view=view, ephemeral=True
    )


async def _handle_verify_start_interaction(
    interaction: discord.Interaction, guild_id_fallback: str
) -> None:
    data = load_verify_config()
    guild_id_key = str(interaction.guild.id) if interaction.guild else guild_id_fallback
    config = data.get(guild_id_key)
    if not config:
        await interaction.response.send_message("⚙️ Verification not set up.", ephemeral=True)
        return

    user = interaction.user
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("⚙️ Verification must be used in a server.", ephemeral=True)
        return

    try:
        verified_role_id = int(config["verified_role"])
    except (KeyError, TypeError, ValueError):
        await interaction.response.send_message(
            "⚙️ Verification config is invalid. Ask a moderator to run `/verifyconfig` again.",
            ephemeral=True,
        )
        return

    role = guild.get_role(verified_role_id)
    if role is None:
        await interaction.response.send_message(
            "❌ The verified role from setup no longer exists (it may have been deleted). "
            "Ask a moderator to run `/verifyconfig` and select the role again.",
            ephemeral=True,
        )
        return

    log_channel = None
    raw_log = config.get("log_channel")
    if raw_log is not None:
        try:
            _lcid = int(raw_log)
        except (TypeError, ValueError):
            _lcid = None
        if _lcid is not None:
            _ch = guild.get_channel(_lcid)
            if isinstance(_ch, (discord.TextChannel, discord.Thread)):
                log_channel = _ch

    method = config["method"]

    if role in user.roles:
        await interaction.response.send_message("✅ You’re already verified!", ephemeral=True)
        return

    if method == "button":
        await run_button_verify(interaction, user, role, log_channel)
    elif method == "code":
        await run_code_verify(interaction, user, role, log_channel)
    elif method == "color":
        await run_color_verify(interaction, user, role, log_channel)
    else:
        await interaction.response.send_message("❌ Invalid verification method.", ephemeral=True)


# ----------------------------------------
# VERIFY START VIEW (MAIN ENTRY)
# ----------------------------------------
class VerifyStartView(discord.ui.View):
    """Persistent panel; custom_id is per-guild so every guild can register after restart."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        btn = discord.ui.Button(
            label="Verify Me ☕",
            style=discord.ButtonStyle.success,
            custom_id=f"{_VERIFY_START_CUSTOM_ID_PREFIX}{self.guild_id}",
        )
        btn.callback = self._on_verify_start
        self.add_item(btn)

    async def _on_verify_start(self, interaction: discord.Interaction) -> None:
        await _handle_verify_start_interaction(interaction, self.guild_id)


class LegacyVerifyStartView(discord.ui.View):
    """Handles verification messages created before per-guild verify custom_ids."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Me ☕",
        style=discord.ButtonStyle.success,
        custom_id=_VERIFY_LEGACY_START_CUSTOM_ID,
    )
    async def verify_me(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_verify_start_interaction(interaction, "")


# ----------------------------------------
# CONFIGURE VERIFICATION COMMAND
# ----------------------------------------
@tree.command(name="verifyconfig", description="Configure Coffeecord’s verification system")
@app_commands.describe(
    method="Verification method",
    verified_role="Role to give after verification",
    verify_channel="Channel to post the verification message",
    log_channel="Channel where verification events are logged"
)
@app_commands.choices(method=[
    app_commands.Choice(name="Simple Button", value="button"),
    app_commands.Choice(name="Keypad Code", value="code"),
    app_commands.Choice(name="Color Buttons", value="color"),
])
async def verifyconfig(interaction: discord.Interaction,
    method: app_commands.Choice[str],
    verified_role: discord.Role,
    verify_channel: discord.TextChannel,
    log_channel: discord.TextChannel
):
    guild_id = str(interaction.guild.id)
    data = load_verify_config()
    data[guild_id] = {
        "method": method.value,
        "verified_role": verified_role.id,
        "verify_channel": verify_channel.id,
        "log_channel": log_channel.id
    }
    save_verify_config(data)
    _dispatch_module_log_event(
        interaction.guild,
        "verification",
        "config_update",
        actor=interaction.user,
        details=(
            f"method={method.value}; verified_role_id={verified_role.id}; "
            f"verify_channel_id={verify_channel.id}; log_channel_id={log_channel.id}"
        ),
        channel_id=interaction.channel.id if interaction.channel else None,
    )

    embed = discord.Embed(
        title="✅ Verification Configured",
        description=f"**Method:** {method.name}\n**Verified Role:** {verified_role.mention}\n"
                    f"**Verify Channel:** {verify_channel.mention}\n**Log Channel:** {log_channel.mention}",
        color=discord.Color.green()
    )
    embed.set_footer(text="Coffeecord Verification System - T.R.O.N")

    await interaction.response.send_message(embed=embed)

    try:
        await verify_channel.send(
            f"☕ Welcome to **{interaction.guild.name}**! Click below to verify:",
            view=VerifyStartView(interaction.guild.id)
        )
    except Exception as e:
        await interaction.followup.send(
            _redact_discord_token_in_text(
                f"⚠️ I couldn’t send the message in {verify_channel.mention}.\n`{e}`"
            ),
            ephemeral=True,
        )

MODQUESTIONS_FILE = os.path.join(_storage_dir, "Config", "modquestions.json")



class Nickname(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="nickname", description="Change Coffeecord's nickname in this server.")
    @app_commands.describe(name="The new nickname for Coffeecord.")
    async def nickname(self, interaction, name: str):
        # Check permissions
        if not interaction.guild.me.guild_permissions.manage_nicknames:
            await interaction.response.send_message("❌ I don’t have permission to change my nickname.", ephemeral=True)
            return

        try:
            await interaction.guild.me.edit(nick=name)
            await interaction.response.send_message(f"✅ Nickname changed to **{name}**")
            _dispatch_module_log_event(
                interaction.guild,
                "verification",
                "nickname_change",
                actor=interaction.user,
                details=f"new_nickname={name}",
                channel_id=interaction.channel.id if interaction.channel else None,
            )
        except Exception as e:
            await interaction.response.send_message(
                _redact_discord_token_in_text(f"❌ Failed to change nickname: {e}"),
                ephemeral=True,
            )

# leveling system extracted to Modules/leveling.py

mod_questions = load_json(MODQUESTIONS_FILE, {})      # guild‑scoped dict

# ─── helpers --------------------------------------------------------------
def gcfg(gid: str):
    """Ensure & return this guild's config dict."""
    return mod_questions.setdefault(gid, {
        "enabled": True,
        "channel_id": None,
        "review_role": None,
        "pass_role": None,
        "questions": []
    })

def save():  save_json(MODQUESTIONS_FILE, mod_questions)

def fmt_qs(qs: list[str]) -> str:
    return "\n".join(f"`{i+1}.` {q}" for i, q in enumerate(qs)) or "*no questions yet*"

class StaffAppGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="application", description="Staff application commands")

    @app_commands.command(name="toggle", description="Toggle staff applications on or off")
    async def toggle(self, interaction: discord.Interaction):
        # Your toggle logic here
        await interaction.response.send_message("Toggled staff applications!", ephemeral=True)
        _dispatch_module_log_event(
            interaction.guild,
            "applications",
            "app_toggle",
            actor=interaction.user,
            details="Toggled staff applications",
            channel_id=interaction.channel.id if interaction.channel else None,
        )

    @app_commands.command(name="addquestion", description="Add a staff application question")
    async def add_question(self, interaction: discord.Interaction, question: str):
        # Add question logic here
        await interaction.response.send_message(f"Added question: {question}", ephemeral=True)
        _dispatch_module_log_event(
            interaction.guild,
            "applications",
            "app_question_add",
            actor=interaction.user,
            details=f"question={question[:200]}",
            channel_id=interaction.channel.id if interaction.channel else None,
        )

        #  /staffapp question remove -------------------------------------------
    @app_commands.command(name="remove", description="Remove a question")
    async def question_remove(self, interaction: discord.Interaction):
        qs = gcfg(str(interaction.guild_id))["questions"]
        if not qs:
            return await interaction.response.send_message(
                "ℹ️ No questions set.", ephemeral=True)

        # dropdown with questions
        class QSelect(discord.ui.Select):
            def __init__(self):
                options=[discord.SelectOption(label=f"{i+1}. {q[:90]}",
                                              value=str(i)) for i,q in enumerate(qs)]
                super().__init__(placeholder="Pick question to delete",
                                 min_values=1, max_values=1, options=options)

            async def callback(self, inter: discord.Interaction):
                idx=int(self.values[0])
                removed=qs.pop(idx); save()
                await inter.response.edit_message(
                    content=f"🗑 Removed:\n> {removed}", view=None)
                _dispatch_module_log_event(
                    inter.guild,
                    "applications",
                    "app_question_remove",
                    actor=inter.user,
                    details=f"index={idx}; removed={removed[:200]}",
                    channel_id=inter.channel.id if inter.channel else None,
                )

        view = discord.ui.View(timeout=60)
        view.add_item(QSelect())
        await interaction.response.send_message("Select question to delete:", view=view, ephemeral=True)

          #  /staffapp question list ---------------------------------------------
    @app_commands.command(name="list", description="List current questions")
    async def question_list(self, interaction: discord.Interaction):
        qs = gcfg(str(interaction.guild_id))["questions"]
        await interaction.response.send_message(
            "📋 **Current questions:**\n" + fmt_qs(qs), ephemeral=True)
        
        #  /staffapp setup  -----------------------------------------------------
    @app_commands.command(name="setup", description="Set channel & roles")
    @app_commands.describe(
        channel="Channel where applicants will run /application",
        reviewer_role="Role notified of new applications",
        pass_role="Role granted when an application is approved")
    async def setup(self, interaction: discord.Interaction,
                    channel:      discord.TextChannel,
                    reviewer_role: discord.Role,
                    pass_role:     discord.Role):
        cfg = gcfg(str(interaction.guild_id))
        cfg.update(channel_id=channel.id,
                   review_role=reviewer_role.id,
                   pass_role=pass_role.id)
        save()
        await interaction.response.send_message(
            "✅ Staff‑application system configured.", ephemeral=True)    
        _dispatch_module_log_event(
            interaction.guild,
            "applications",
            "app_setup",
            actor=interaction.user,
            details=(
                f"channel_id={channel.id}; reviewer_role_id={reviewer_role.id}; "
                f"pass_role_id={pass_role.id}"
            ),
            channel_id=interaction.channel.id if interaction.channel else None,
        )

# StaffAppGroup uses name="application" which conflicts with @tree.command(name="application") below;
# the group is not registered on the command tree.

def get_staff_cfg(guild_id: str) -> dict:
    return staff_app_cfg.setdefault(guild_id, {
        "enabled": False,
        "questions": [],
        "review_channel_id": None,
        "reviewer_role_id": None
    })

staff_app_cfg = load_json(STAFF_APP_FILE)

# ─────────────────────────  /application  ──────────────────────────
@tree.command(
    name="application",
    description="Start a staff‑application and answer the server’s questions",
)
async def application(interaction: discord.Interaction):
    """Ask the configured questions via DM, then forward the transcript to reviewers."""
    guild_id = str(interaction.guild_id)
    cfg      = staff_app_cfg.get(guild_id)

    # ── Sanity / permission checks ─────────────────────────────────
    if not cfg or not cfg.get("enabled", False):
        return await interaction.response.send_message(
            "❌ Staff applications are not enabled on this server.", ephemeral=True)

    if cfg.get("channel_id") and interaction.channel.id != cfg["channel_id"]:
        return await interaction.response.send_message(
            f"❌ Please use this command in <#{cfg['channel_id']}>.", ephemeral=True)

    questions: list[str] = cfg.get("questions", [])
    if not questions:
        return await interaction.response.send_message(
            "❌ No application questions have been set up yet.", ephemeral=True)

    if interaction.user.bot:
        return

    # ── Try to open DM channel ─────────────────────────────────────
    try:
        await interaction.user.send(
            f"📋 **{interaction.guild.name} – Staff Application**\n"
            f"You will be asked **{len(questions)}** questions.\n"
            f"Type your answer to each and send it.  *(You have 5 minutes per question – "
            f"respond with `cancel` to abort.)*")
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ I can’t DM you. Please enable DMs from this server and try again.", ephemeral=True)

    await interaction.response.send_message("📨 Check your DMs to begin your application!", ephemeral=True)

    answers: list[str] = []
    def dm_check(m: discord.Message):
        return m.author == interaction.user and isinstance(m.channel, discord.DMChannel)

    for idx, q in enumerate(questions, 1):
        await interaction.user.send(f"**Q{idx}.** {q}")
        try:
            reply: discord.Message = await interaction.client.wait_for(
                "message", check=dm_check, timeout=300)
        except asyncio.TimeoutError:
            await interaction.user.send("⌛ Time‑out – application cancelled.")
            return
        if reply.content.lower().strip() == "cancel":
            await interaction.user.send("🚫 Application cancelled.")
            return
        answers.append(f"**Q{idx}.** {q}\n{reply.content}")

    # ── Build transcript embed ─────────────────────────────────────
    transcript = "\n\n".join(answers)
    embed = discord.Embed(
        title="📬 New Staff Application",
        description=transcript,
        colour=discord.Colour.blurple(),
    ).set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar)

    # ── Dispatch to review channel / reviewers ─────────────────────
    review_channel = interaction.guild.get_channel(cfg.get("review_channel_id", 0))
    reviewer_role  = interaction.guild.get_role(cfg.get("reviewer_role_id", 0))

    sent = False
    if review_channel and review_channel.permissions_for(interaction.guild.me).send_messages:
        await review_channel.send(content=reviewer_role.mention if reviewer_role else None,
                                  embed=embed)
        sent = True

    if not sent and reviewer_role:
        # Fallback – DM every reviewer (best‑effort)
        for member in reviewer_role.members:
            try:
                await member.send(embed=embed)
                sent = True
            except discord.Forbidden:
                continue

    if sent:
        await dm.send("✅ Your application has been submitted – thank you!")
        _dispatch_module_log_event(
            interaction.guild,
            "applications",
            "app_submit",
            actor=interaction.user,
            details=f"questions_answered={len(answers)}; delivered=true",
            channel_id=interaction.channel.id if interaction.channel else None,
        )
    else:
        await dm.send("⚠️ Unable to deliver your application to the staff. "
                      "Please inform an administrator.")
        _dispatch_module_log_event(
            interaction.guild,
            "applications",
            "app_submit_failed_delivery",
            actor=interaction.user,
            details=f"questions_answered={len(answers)}; delivered=false",
            channel_id=interaction.channel.id if interaction.channel else None,
        )

    # (Optional) store a copy in staff_app_cfg[guild_id]["applications"] …

import aiohttp

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Triggered when the bot joins a guild."""

    # --- WELCOME BUTTONS CLASS ---
    class WelcomeButtons(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(discord.ui.Button(
                label="Support Server",
                style=discord.ButtonStyle.link,
                url=SUPPORT_SERVER
            ))
            self.add_item(discord.ui.Button(
                label="Invite Me",
                style=discord.ButtonStyle.link,
                url=BOT_INVITE_URL
            ))

        @discord.ui.button(label="Getting Started", style=discord.ButtonStyle.primary)
        async def setup_info(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(
                "**Getting Started with Coffeecord:**\n"
                "• `/logging config` — enable logging\n"
                "• `/autorole` — set automatic roles\n"
                "• `/verifyconfig` — configure verification\n"
                "• `/ticket_setup` — setup tickets\n"
                "• Need help? Join the support server!",
                ephemeral=True
            )

    # --- CHOOSE TARGET CHANNEL ---
    target = None

    # system channel first
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        target = guild.system_channel

    # fallback: first writable text channel
    if not target:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                target = channel
                break

    if not target:
        print(f"[JOIN] Could not send welcome message in {guild.name}")
        return

    # --- SEND WELCOME MESSAGE ---
    try:
        await target.send(
            f"☕ **Thanks for inviting Coffeecord to `{guild.name}`!**\n"
            "I'm here to help with moderation, verification, leveling, applications, logging, and more.\n\n"
            "**Press a button below to get started.**",
            view=WelcomeButtons()
        )
        print(f"[JOIN] Sent welcome message in {guild.name}")
    except Exception as e:
        print(_redact_discord_token_in_text(f"[JOIN ERROR] Failed to send welcome in {guild.name}: {e}"))

# leveling reward and level-up logic extracted to Modules/leveling.py

from discord.ui import Modal, TextInput, View, Select
from typing import List

ADAPTIVE_SLOWMODE_FILE = os.path.join(_storage_dir, "Config", "adaptive_slowmode.json")
# Runtime state for adaptive slowmode.
channel_message_times: dict[int, list[float]] = {}
channel_last_edit: dict[int, float] = {}
SLOWMODE_EDIT_COOLDOWN = 10

def load_adaptive_slowmode_config():
    if not os.path.exists(ADAPTIVE_SLOWMODE_FILE):
        return {}
    with open(ADAPTIVE_SLOWMODE_FILE, "r") as f:
        return json.load(f)

def save_adaptive_slowmode_config(data):
    with open(ADAPTIVE_SLOWMODE_FILE, "w") as f:
        json.dump(data, f, indent=4)
# ================== COMMAND ==================

@bot.tree.command(
    name="adaptive_slowmode",
    description="Enable adaptive slowmode with up to 3 rules"
)
async def adaptive_slowmode(
    interaction: discord.Interaction,
    enabled: bool,
    rule1: str | None = None,
    rule2: str | None = None,
    rule3: str | None = None,
):
    """
    ruleX syntax: messages_per_minute/slowmode_seconds
    Example: 30/5
    """

    guild_id = str(interaction.guild.id)
    channel_id = str(interaction.channel.id)

    rules = []
    for r in (rule1, rule2, rule3):
        if r:
            try:
                mpm, delay = r.split("/")
                rules.append({
                    "mpm": int(mpm),
                    "delay": int(delay)
                })
            except ValueError:
                await interaction.response.send_message(
                    f"❌ Invalid rule `{r}`. Use `messages/delay` (example: 30/5)",
                    ephemeral=True
                )
                return

    rules.sort(key=lambda x: x["mpm"])

    data = load_json(ADAPTIVE_SLOWMODE_FILE, {})

    # ensure structure exists
    if guild_id not in data:
        data[guild_id] = {}

    data[guild_id][channel_id] = {
        "enabled": enabled,
        "rules": rules
    }

    save_json(ADAPTIVE_SLOWMODE_FILE, data)

    # reset runtime tracking
    cid = interaction.channel.id
    channel_message_times[cid] = []
    channel_last_edit[cid] = 0

    rules_text = "\n".join(
        f"- {r['mpm']} msg/min → {r['delay']} sec"
        for r in rules
    ) or "No rules set"

    await interaction.response.send_message(
        f"✅ Adaptive slowmode **{'enabled' if enabled else 'disabled'}**\n"
        f"📊 Rules:\n{rules_text}",
        ephemeral=True
    )
    _dispatch_module_log_event(
        interaction.guild,
        "adaptive_slowmode",
        "adaptive_slowmode_update",
        actor=interaction.user,
        details=f"enabled={enabled}; rules={len(rules)}; channel_id={interaction.channel.id}",
        channel_id=interaction.channel.id if interaction.channel else None,
    )

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # Automod (runs first; if action taken, skip XP etc.)
    if await _get_automod_module().process_automod(message):
        return

    now = time.time()
    guild_id = str(message.guild.id)
    channel_id = message.channel.id
    channel_id_str = str(channel_id)

    # ==================================================
    # ADAPTIVE SLOWMODE (UPDATED + FIXED)
    # ==================================================
    data = load_json(ADAPTIVE_SLOWMODE_FILE, {})
    channel_cfg = data.get(guild_id, {}).get(channel_id_str)

    if channel_cfg and channel_cfg.get("enabled"):
        rules = channel_cfg.get("rules", [])

        if rules:
            times = channel_message_times.setdefault(channel_id, [])
            times.append(now)

            # keep last 60 seconds
            times[:] = [t for t in times if now - t <= 60]
            mpm = len(times)

            # determine correct slowmode
            new_delay = 0
            for rule in rules:
                if mpm >= rule["mpm"]:
                    new_delay = rule["delay"]
            # Discord hard limit for slowmode is 21600 seconds.
            new_delay = max(0, min(int(new_delay), 21600))

            # apply slowmode only if changed + cooldown passed
            last_edit = channel_last_edit.get(channel_id, 0)
            if (
                new_delay != message.channel.slowmode_delay
                and now - last_edit >= SLOWMODE_EDIT_COOLDOWN
            ):
                try:
                    await message.channel.edit(slowmode_delay=new_delay)
                    channel_last_edit[channel_id] = now
                except (discord.Forbidden, discord.HTTPException):
                    # Keep bot running even if channel edit fails.
                    pass

    # ==================================================
    # XP SYSTEM (extracted)
    # ==================================================
    await _get_leveling_module().award_message_xp(bot, message)

    # ==================================================
    # TICKET LOGGING
    # ==================================================
    tickets_data = load_json(TICKETS_FILE, {})
    guild_tickets = tickets_data.get(guild_id, {}).get("tickets", {})

    if channel_id_str in guild_tickets:
        guild_tickets[channel_id_str].setdefault("messages", []).append({
            "author": str(message.author),
            "content": message.content
        })
        save_json(TICKETS_FILE, tickets_data)

    # ==================================================
    # COMMAND PROCESSING
    # ==================================================
    await bot.process_commands(message)


# ------------------- REACTION XP -------------------
@bot.event
async def on_reaction_add(reaction, user):
    await _get_leveling_module().award_reaction_xp(bot, reaction, user)


# ------------------- VOICE XP -------------------
active_vc_members = {}  # {guild_id: {user_id: join_time}}

@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = str(member.guild.id)
    user_id = str(member.id)
    calls = load_calls()
    guild_calls = calls.get(guild_id, {})

    # --- Joining a channel (from no VC or from another VC) ---
    if after.channel is not None:
        call_data = guild_calls.get(str(after.channel.id))
        if call_data:
            if str(member.id) not in call_data.get("members", []):
                try:
                    await member.move_to(None)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                password_hint = ""
                if call_data.get("password"):
                    password_hint = " Use the correct password with `/call join`."
                try:
                    await member.send(
                        "📞 **You need to use /call join to join this call.**\n"
                        f"Use `/call join` with channel <#{after.channel.id}> to join.{password_hint}"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return
            _cancel_call_empty_timer(after.channel.id)

        if before.channel is None:
            active_vc_members.setdefault(guild_id, {})[user_id] = asyncio.get_event_loop().time()

    # --- Leaving a channel (disconnect or move) ---
    if before.channel is not None:
        call_data = guild_calls.get(str(before.channel.id))
        if call_data and len(before.channel.members) == 0:
            _schedule_call_empty_delete(before.channel.id, guild_id)

        if after.channel is None:
            await _get_leveling_module().award_voice_xp(bot, member, active_vc_members)

# ─── Console logging: all commands and processes ─────────────────────────────
def _log_cmd(
    prefix: str,
    name: str,
    user: discord.abc.User | None,
    guild: discord.Guild | None,
    channel: discord.abc.Messageable | None,
) -> None:
    """Log command invocation to stdout (ends up in bot.log via c-cord)."""
    guild_name = guild.name if guild else "DM"
    ch_name = getattr(channel, "name", str(channel)) if channel else "?"
    full = f"{prefix}{name}"
    if user is None:
        print(f"[CMD] {full} | (no user) | {guild_name}#{ch_name}")
        return
    print(f"[CMD] {full} | {user} ({user.id}) | {guild_name}#{ch_name}")


@bot.listen("on_interaction")
async def _log_slash_command(interaction: discord.Interaction) -> None:
    """Log slash command invocations to console."""
    if interaction.type != discord.InteractionType.application_command:
        return
    # Guild slash commands often populate `member`; some payloads omit `user`.
    actor = interaction.user or interaction.member
    if actor is None or getattr(actor, "bot", False):
        return
    cmd = interaction.command.qualified_name if interaction.command else "unknown"
    _log_cmd("/", cmd, actor, interaction.guild, interaction.channel)


@bot.before_invoke
async def _log_prefix_command(ctx: commands.Context) -> None:
    """Log prefix command invocations to console."""
    if ctx.author.bot or ctx.command is None:
        return
    prefix = (ctx.clean_prefix or ".").rstrip()
    _log_cmd(prefix, ctx.command.qualified_name, ctx.author, ctx.guild, ctx.channel)


@bot.after_invoke
async def _log_prefix_completion(ctx: commands.Context) -> None:
    """Log prefix command completion to console."""
    if ctx.author.bot or ctx.command is None:
        return
    print(f"[CMD+] .{ctx.command.qualified_name} completed | {ctx.author} | {ctx.guild.name if ctx.guild else 'DM'}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingAnyRole):
        await ctx.send("🚫 You don’t have permission to use that command.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You’re missing required permissions to run this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❗ Missing argument: `{error.param.name}`")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Optional: silently ignore unknown commands
    else:
        # Show exact failure reason to avoid vague "failed" messages.
        await ctx.send(
            _redact_discord_token_in_text(f"⚠️ {type(error).__name__}: {error}")
        )

        # Optional: log error to console only
        print(
            _redact_discord_token_in_text(f"[ERROR] {type(error).__name__}: {error}")
        )

async def _send_app_error(
    interaction: discord.Interaction,
    message: str,
    view: discord.ui.View | None = None,
) -> None:
    """Send an ephemeral app-command error regardless of response state."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True, view=view)
        else:
            await interaction.response.send_message(message, ephemeral=True, view=view)
    except (discord.Forbidden, discord.HTTPException):
        pass

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)

    if isinstance(error, app_commands.MissingPermissions):
        perms = ", ".join(error.missing_permissions)
        details = _format_error_details(reason=f"MissingPermissions: {perms}")
        friendly, view = _error_details_view(
            f"🚫 You are missing required permission(s): `{perms}`.",
            details,
        )
        return await _send_app_error(interaction, friendly, view=view)

    if isinstance(error, app_commands.BotMissingPermissions):
        perms = ", ".join(error.missing_permissions)
        details = _format_error_details(reason=f"BotMissingPermissions: {perms}")
        friendly, view = _error_details_view(
            f"🤖 I am missing required permission(s): `{perms}`.",
            details,
        )
        return await _send_app_error(interaction, friendly, view=view)

    if isinstance(error, app_commands.CommandOnCooldown):
        details = _format_error_details(
            reason=f"CommandOnCooldown: retry_after={error.retry_after:.1f}s"
        )
        friendly, view = _error_details_view(
            f"⏳ This command is on cooldown. Try again in `{error.retry_after:.1f}`s.",
            details,
        )
        return await _send_app_error(interaction, friendly, view=view)

    if isinstance(error, module_checks.MissingRoleOrModeratePermission):
        details = _format_error_details(exc=error)
        friendly, view = _error_details_view(
            "🚫 You need **Manage Roles**, **Moderate Members**, or **Manage Server** to use this command.",
            details,
        )
        return await _send_app_error(interaction, friendly, view=view)

    if isinstance(error, app_commands.CheckFailure):
        details = _format_error_details(exc=error)
        friendly, view = _error_details_view(
            "🚫 You do not meet the requirements to use this command.",
            details,
        )
        return await _send_app_error(interaction, friendly, view=view)

    if isinstance(error, app_commands.TransformerError):
        details = _format_error_details(
            reason=f"TransformerError: value={error.value!r}, type={error.type.__name__}"
        )
        friendly, view = _error_details_view(
            f"❌ Invalid value for `{error.value}` ({error.type.__name__}).",
            details,
        )
        return await _send_app_error(interaction, friendly, view=view)

    # Unexpected error: show friendly message + button for full details.
    err_name = type(original).__name__
    err_text = str(original) or "No details provided."
    short_error = (err_text[:1200] + "...") if len(err_text) > 1200 else err_text
    tb_lines = traceback.format_exception(type(original), original, original.__traceback__)
    full_details = "".join(tb_lines).strip()
    details = _format_error_details(exc=original)
    if full_details:
        truncated = full_details[:1900] + "..." if len(full_details) > 1900 else full_details
        details = f"{details}\n\n**Traceback:**\n```\n{truncated}```"
    if len(details) > 1900:
        details = details[:1900] + "..."
    friendly, view = _error_details_view(
        f"⚠️ Command error: `{err_name}` — {short_error[:200]}",
        details,
    )
    await _send_app_error(interaction, friendly, view=view)

    cmd_name = interaction.command.qualified_name if interaction.command else "unknown"
    _print_redacted(f"[APP_COMMAND_ERROR] /{cmd_name}")
    _print_traceback_redacted(original)


# Owner-only prefix commands (non-owner: no response)
@bot.command(name="synccommands")
async def sync_commands_prefix(ctx: commands.Context):
    if ctx.author.id != OWNER_ID:
        return
    try:
        synced = await tree.sync()
        await ctx.send(f"✅ Synced {len(synced)} commands!")
    except Exception as e:
        await ctx.send(_redact_discord_token_in_text(f"❌ Failed to sync: {e}"))


@bot.command(name="clearchache")
async def clearchache_prefix(ctx: commands.Context):
    if ctx.author.id != OWNER_ID:
        return
    msg = None
    try:
        msg = await ctx.send("🧹 Clearing and syncing commands, please wait...")
        tree.clear_commands(guild=None)
        await tree.sync()
        await msg.edit(content="✅ Global commands cleared and resynced successfully!")
    except Exception as e:
        if msg is not None:
            await msg.edit(content=_redact_discord_token_in_text(f"❌ Error: {e}"))
        else:
            await ctx.send(_redact_discord_token_in_text(f"❌ Error: {e}"))
        print(_redact_discord_token_in_text(f"[Sync Error] {e}"))


@tree.command(name="debugcommands", description="Print all registered commands")
async def debug_commands(interaction: discord.Interaction):
    cmds = tree.get_commands()
    output = "\n".join(f"/{cmd.name}" for cmd in cmds)
    await interaction.response.send_message(f"Registered commands:\n```\n{output}\n```", ephemeral=True)

# ---------------------------
# Helper to load/save calls
# ---------------------------
CALLS_FILE = os.path.join(_storage_dir, "Temp", "active_calls.json")
CALL_EMPTY_DELETE_SECONDS = 15
_call_empty_timers = {}  # channel_id -> asyncio.Task


def _cancel_call_empty_timer(channel_id: int) -> None:
    """Cancel a pending auto-delete for an empty call channel."""
    task = _call_empty_timers.pop(str(channel_id), None)
    if task and not task.done():
        task.cancel()


async def _delete_call_after_empty(channel_id: int, guild_id: str) -> None:
    """Wait 15 seconds, then delete the call if still empty."""
    try:
        await asyncio.sleep(CALL_EMPTY_DELETE_SECONDS)
    except asyncio.CancelledError:
        return
    finally:
        _call_empty_timers.pop(str(channel_id), None)

    calls = load_calls()
    guild_calls = calls.get(guild_id, {})
    if str(channel_id) not in guild_calls:
        return

    channel = bot.get_channel(channel_id)
    if channel is not None and len(channel.members) > 0:
        return  # Someone joined in time
    if channel is not None:
        try:
            await channel.delete(reason="Call empty for 15 seconds")
        except (discord.Forbidden, discord.HTTPException):
            pass
    del guild_calls[str(channel_id)]
    if not guild_calls:
        del calls[guild_id]
    else:
        calls[guild_id] = guild_calls
    save_calls(calls)


def _can_add_role_reason(guild: discord.Guild, member: discord.Member, role: discord.Role) -> str | None:
    """Return the reason the bot cannot add the role to the member, or None if OK."""
    bot_member = guild.me
    if not bot_member:
        return "Bot member not found."
    if not bot_member.guild_permissions.manage_roles:
        return "The bot lacks **Manage Roles** permission. Grant it in Server Settings → Roles."
    if bot_member.top_role.position <= role.position:
        return (
            f"The bot's role is not above the mute role ({role.name}). "
            f"Move the bot's role above **{role.name}** in Server Settings → Roles."
        )
    if bot_member.top_role.position <= member.top_role.position:
        return (
            f"The bot's role is not above {member.display_name}'s highest role ({member.top_role.name}). "
            f"Move the bot's role higher in Server Settings → Roles."
        )
    return None


def _call_set_permissions_reason(guild: discord.Guild, target: discord.Member) -> str | None:
    """Return the specific reason the bot cannot set channel permissions for target, or None if OK."""
    bot_member = guild.me
    if not bot_member:
        return "Bot member not found."
    if not bot_member.guild_permissions.manage_roles:
        return "The bot lacks **Manage Roles** permission. Grant it in Server Settings → Roles."
    bot_top = bot_member.top_role
    target_top = target.top_role
    if bot_top.position <= target_top.position:
        bot_name = bot_member.display_name
        return (
            f"The bot's role is not above {target_top.name}. "
            f"Move **{bot_name}**'s role above the **{target_top.name}** role in Server Settings → Roles."
        )
    return None


def _format_error_details(reason: str | None = None, exc: BaseException | None = None) -> str:
    """Format technical details for the 'Show technical details' button."""
    parts = []
    if exc:
        exc_type = type(exc).__name__
        parts.append(f"**Exception:** `{exc_type}`")
        parts.append(f"**Message:** {exc!s}")
        if isinstance(exc, discord.HTTPException):
            parts.append(f"**Status:** {getattr(exc, 'status', 'N/A')}")
            if hasattr(exc, 'code') and exc.code:
                parts.append(f"**Code:** {exc.code}")
    if reason:
        parts.append(f"**Pre-check:** {reason}")
    raw = "\n".join(parts) if parts else "No details available."
    return _redact_discord_token_in_text(raw)


def _error_details_view(friendly_msg: str, technical_details: str) -> tuple[str, discord.ui.View]:
    """Build a user-friendly message and a View with a 'Show technical details' button."""
    friendly_msg = _redact_discord_token_in_text(friendly_msg)
    technical_details = _redact_discord_token_in_text(technical_details)
    key = secrets.token_hex(8)
    _error_details_cache[key] = technical_details
    view = _ErrorDetailsView(key)
    return friendly_msg, view


class _ErrorDetailsView(discord.ui.View):
    """View with a button that reveals technical error details."""

    def __init__(self, cache_key: str):
        super().__init__(timeout=300)
        custom_id = f"{_ERROR_DETAILS_PREFIX}{cache_key}"
        if len(custom_id) > 100:
            custom_id = _ERROR_DETAILS_PREFIX + cache_key[: 100 - len(_ERROR_DETAILS_PREFIX)]
        btn = discord.ui.Button(
            label="Show technical details",
            style=ButtonStyle.secondary,
            custom_id=custom_id,
        )
        btn.callback = self._on_click
        self.add_item(btn)
        # Do not use _cache_key — discord.py overwrites view._cache_key with message_id in store_view().
        self._error_details_storage_key = cache_key

    async def _on_click(self, interaction: discord.Interaction):
        storage_key = getattr(self, "_error_details_storage_key", None)
        if storage_key is None and interaction.data:
            cid = interaction.data.get("custom_id") or ""
            if cid.startswith(_ERROR_DETAILS_PREFIX):
                storage_key = cid[len(_ERROR_DETAILS_PREFIX) :]
        details = _error_details_cache.pop(storage_key, None) if storage_key else None
        if details:
            await interaction.response.send_message(
                f"```\n{details}```",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Details expired or already viewed.",
                ephemeral=True,
            )
        self.stop()

    async def on_timeout(self):
        key = getattr(self, "_error_details_storage_key", None)
        if key:
            _error_details_cache.pop(key, None)


def _schedule_call_empty_delete(channel_id: int, guild_id: str) -> None:
    """Schedule auto-delete if the call channel is empty for 15 seconds."""
    _cancel_call_empty_timer(channel_id)
    task = asyncio.create_task(_delete_call_after_empty(channel_id, guild_id))
    _call_empty_timers[str(channel_id)] = task


def load_calls():
    if not os.path.exists(CALLS_FILE):
        return {}
    with open(CALLS_FILE, "r") as f:
        return json.load(f)

def save_calls(data):
    with open(CALLS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_host_call(guild_id: int, user_id: int):
    calls = load_calls()
    guild_calls = calls.get(str(guild_id), {})

    for channel_id, data in guild_calls.items():
        if data.get("host_id") == str(user_id):
            return {
                "channel_id": int(channel_id),
                "host_id": int(data["host_id"]),
                "members": [int(m) for m in data.get("members", [])],
                "password": data.get("password")
            }

    return None

call_group = app_commands.Group(name="call", description="CoffeeCord call commands")


@call_group.command(name="create", description="Create a temporary private call channel.")
async def call(
    interaction: discord.Interaction,
    user1: discord.Member | None = None,
    user2: discord.Member | None = None,
    user3: discord.Member | None = None,
    user4: discord.Member | None = None,
    user5: discord.Member | None = None,
    password: str | None = None
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    actor = interaction.user or interaction.member
    if not guild:
        return await interaction.followup.send("❌ Guild not found.", ephemeral=True)
    if actor is None:
        return await interaction.followup.send("❌ Could not resolve your user for this command.", ephemeral=True)

    # Collect valid members
    invited = [
        m for m in [user1, user2, user3, user4, user5]
        if m is not None
    ]

    # Create private channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        actor: discord.PermissionOverwrite(view_channel=True, connect=True),
    }

    for member in invited:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=False)

    try:
        channel = await guild.create_voice_channel(
            name=f"{actor.display_name}'s Call",
            overwrites=overwrites,
            user_limit=len(invited) + 1 if password else 0
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            "❌ I need **Manage Channels** (and permission to manage that category) to create call channels. "
            "Ask an admin to grant the bot **Manage Channels**.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        return await interaction.followup.send(
            _redact_discord_token_in_text(f"❌ Could not create the call channel: {e}"),
            ephemeral=True,
        )

    # Save call info
    calls = load_calls()
    calls.setdefault(str(guild.id), {})

    calls[str(guild.id)][str(channel.id)] = {
        "host_id": str(actor.id),
        "channel_id": str(channel.id),
        "members": [str(m.id) for m in invited] + [str(actor.id)],
        "password": password
    }

    save_calls(calls)

    # --- DM NOTIFICATIONS ---
    for member in invited:
        try:
            msg = (
                f"📞 **{actor.display_name} is calling you!**\n"
                f"➡️ Use **/call join** to join: <#{channel.id}>\n"
            )
            if password:
                msg += f"🔑 **Password:** `{password}`"

            await member.send(msg)
        except Exception:
            # User has DMs off or blocked the bot — ignore
            pass

    await interaction.followup.send(
        f"📞 Call created: <#{channel.id}>\n"
        f"Invited users must use **/call join** to join.\n"
        f"{f'🔑 Password: `{password}`' if password else ''}"
    )
    _dispatch_module_log_event(
        interaction.guild,
        "calls",
        "call_create",
        actor=actor,
        details=f"channel_id={channel.id}; invited={len(invited)}; password_protected={bool(password)}",
        channel_id=channel.id,
    )

# ---------------------------
# /call_join
# ---------------------------
@call_group.command(name="join", description="Join a temporary CoffeeCord call.")
@app_commands.describe(
    channel="The call channel",
    password="Password if required."
)
async def call_join(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    password: str | None = None
):
    guild = interaction.guild or getattr(channel, "guild", None)
    user = interaction.user or interaction.member
    if guild is None or user is None:
        return await interaction.response.send_message(
            "❌ Use this in a server (could not resolve guild or user).",
            ephemeral=True,
        )
    calls = load_calls()
    guild_calls = calls.get(str(guild.id), {})
    call_data = guild_calls.get(str(channel.id))

    if not call_data:
        return await interaction.response.send_message(
            "❌ This call does not exist or has expired.",
            ephemeral=True
        )

    # Password check
    call_password = call_data.get("password")
    if call_password:
        if password != call_password:
            return await interaction.response.send_message("❌ Incorrect password.", ephemeral=True)

    # Add user (requires Manage Roles + bot role above user's highest role)
    reason = _call_set_permissions_reason(guild, user)
    if reason:
        details = _format_error_details(reason=reason)
        friendly, view = _error_details_view(
            "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await channel.set_permissions(user, view_channel=True, connect=True)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)

    if str(user.id) not in call_data["members"]:
        call_data["members"].append(str(user.id))

    # Update user limit to current number of members
    if call_password:
        await channel.edit(user_limit=len(call_data["members"]))

    # Kick anyone currently in VC who isn't authorized
    for member in channel.members:
        if str(member.id) not in call_data["members"]:
            try:
                await member.move_to(None)
            except Exception:
                pass

    # Save changes
    guild_calls[str(channel.id)] = call_data
    calls[str(guild.id)] = guild_calls
    save_calls(calls)

    # DM invite link
    try:
        await user.send(f"✅ You joined the call! Click to join: <#{channel.id}>")
    except Exception:
        pass

    await interaction.response.send_message(f"✅ You joined <#{channel.id}>", ephemeral=True)
    _dispatch_module_log_event(
        interaction.guild,
        "calls",
        "call_join",
        actor=user,
        details=f"channel_id={channel.id}; password_protected={bool(call_password)}",
        channel_id=channel.id,
    )

# ---------------------------------------------------
# /call_add
# ---------------------------------------------------
@call_group.command(name="add", description="Add someone to your CoffeeCord call.")
async def call_add(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or interaction.user is None:
        return await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
    calls = load_calls()
    guild_id = str(interaction.guild.id)

    # Helper to find host session
    def get_host_call(calls, guild_id, host_id):
        sessions = calls.get(guild_id, {})
        for cid, info in sessions.items():
            if info["host_id"] == host_id:
                return cid, info
        return None, None

    call_id, info = get_host_call(calls, guild_id, str(interaction.user.id))
    if not info:
        await interaction.response.send_message("❌ You aren’t the host of any active call.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(int(info["channel_id"]))
    if not channel:
        await interaction.response.send_message("❌ Call channel no longer exists.", ephemeral=True)
        return

    # Give view_channel only; user must use /call join to get connect
    reason = _call_set_permissions_reason(interaction.guild, user)
    if reason:
        details = _format_error_details(reason=reason)
        friendly, view = _error_details_view(
            "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await channel.set_permissions(user, view_channel=True, connect=False)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)

    # Add to call data
    if str(user.id) not in info["members"]:
        info["members"].append(str(user.id))

        # Expand VC limit if call is password-protected
        if info.get("password"):
            await channel.edit(user_limit=len(info["members"]))

    save_calls(calls)

    # Build DM message
    password_note = ""
    if info.get("password"):
        password_note = f"\n🔑 **Password:** `{info['password']}`"

    dm_text = (
        f"📞 **{interaction.user.display_name} is calling you!**\n"
        f"You're invited to join a private CoffeeCord call.\n\n"
        f"➡️ Use **/call join** to join: <#{channel.id}>\n"
        f"_(The channel will appear once you use the command!)_"
        f"{password_note}"
    )

    # Send DM
    try:
        await user.send(dm_text)
    except Exception:
        pass

    await interaction.response.send_message(f"📨 Sent call invite to {user.mention}.")
    _dispatch_module_log_event(
        interaction.guild,
        "calls",
        "call_add",
        actor=interaction.user,
        details=f"channel_id={channel.id}; target_user_id={user.id}",
        channel_id=channel.id,
    )

# ---------------------------------------------------
# /call_remove
# ---------------------------------------------------
@call_group.command(name="remove", description="Remove someone from your CoffeeCord call.")
async def call_remove(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or interaction.user is None:
        return await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
    calls = load_calls()
    guild_id = str(interaction.guild.id)

    # Find the host's call session
    def get_host_call(calls, guild_id, host_id):
        sessions = calls.get(guild_id, {})
        for cid, info in sessions.items():
            if info["host_id"] == host_id:
                return cid, info
        return None, None

    call_id, info = get_host_call(calls, guild_id, str(interaction.user.id))
    if not info:
        await interaction.response.send_message("❌ You aren’t the host of any active call.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(int(info["channel_id"]))
    if not channel:
        await interaction.response.send_message("❌ Call channel no longer exists.", ephemeral=True)
        return

    # Remove viewing & connecting permissions
    reason = _call_set_permissions_reason(interaction.guild, user)
    if reason:
        details = _format_error_details(reason=reason)
        friendly, view = _error_details_view(
            "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)
    try:
        await channel.set_permissions(user, overwrite=None)
    except discord.Forbidden as e:
        details = _format_error_details(exc=e)
        friendly, view = _error_details_view(
            "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
            details,
        )
        return await interaction.response.send_message(friendly, ephemeral=True, view=view)

    # Remove from member list
    if str(user.id) in info["members"]:
        info["members"].remove(str(user.id))

    save_calls(calls)

    # ---------------------------------------------------
    # KICK FROM THE VC (if they are currently in it)
    # ---------------------------------------------------
    if user.voice and user.voice.channel and user.voice.channel.id == channel.id:
        try:
            await user.move_to(None, reason="Removed from CoffeeCord call")
        except Exception:
            pass

    # ---------------------------------------------------
    # DM the user
    # ---------------------------------------------------
    try:
        await user.send(
            f"🚫 **You were removed from a CoffeeCord call by {interaction.user.display_name}.**\n"
            f"If you think this was a mistake, you can ask them to invite you back."
        )
    except Exception:
        pass

    await interaction.response.send_message(f"🚫 Removed {user.mention} from the call.")
    _dispatch_module_log_event(
        interaction.guild,
        "calls",
        "call_remove",
        actor=interaction.user,
        details=f"channel_id={channel.id}; target_user_id={user.id}",
        channel_id=channel.id,
    )


# ---------------------------------------------------
# /call_end
# ---------------------------------------------------
@call_group.command(name="end", description="End your CoffeeCord call.")
async def call_end(interaction: discord.Interaction):
    if interaction.guild is None or interaction.user is None:
        return await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
    calls = load_calls()
    guild_id = str(interaction.guild.id)
    info = get_host_call(interaction.guild.id, interaction.user.id)
    call_id = str(info["channel_id"]) if info else None
    if not info:
        await interaction.response.send_message("❌ You don’t currently host any active call.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(int(info["channel_id"]))
    if channel:
        _cancel_call_empty_timer(channel.id)
        bot_member = interaction.guild.me
        if bot_member and not bot_member.guild_permissions.manage_channels:
            details = _format_error_details(
                reason="Bot lacks Manage Channels permission."
            )
            friendly, view = _error_details_view(
                "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
                details,
            )
            return await interaction.response.send_message(friendly, ephemeral=True, view=view)
        try:
            await channel.delete(reason="Call ended by host")
        except discord.Forbidden as e:
            details = _format_error_details(exc=e)
            friendly, view = _error_details_view(
                "❌ The bot is not high enough in the role hierarchy (or lacks permissions).",
                details,
            )
            return await interaction.response.send_message(friendly, ephemeral=True, view=view)

    del calls[guild_id][call_id]
    if not calls[guild_id]:
        del calls[guild_id]

    save_calls(calls)
    await interaction.response.send_message("📞 Call ended.")
    _dispatch_module_log_event(
        interaction.guild,
        "calls",
        "call_end",
        actor=interaction.user,
        details=f"channel_id={info['channel_id']}",
        channel_id=int(info["channel_id"]),
    )


# ---------------------------------------------------
# /call_promote
# ---------------------------------------------------
@call_group.command(name="promote", description="Transfer call host role to another user.")
async def call_promote(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or interaction.user is None:
        return await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
    calls = load_calls()
    guild_id = str(interaction.guild.id)
    info = get_host_call(interaction.guild.id, interaction.user.id)
    call_id = str(info["channel_id"]) if info else None
    if not info:
        await interaction.response.send_message("❌ You aren’t the host of any active call.", ephemeral=True)
        return

    if str(user.id) not in info["members"]:
        await interaction.response.send_message("❌ That user isn’t in the call.", ephemeral=True)
        return

    calls[guild_id][call_id]["host_id"] = str(user.id)
    save_calls(calls)
    await interaction.response.send_message(f"👑 {user.mention} is now the call host!")
    _dispatch_module_log_event(
        interaction.guild,
        "calls",
        "call_promote",
        actor=interaction.user,
        details=f"channel_id={info['channel_id']}; new_host_user_id={user.id}",
        channel_id=int(info["channel_id"]),
    )


tree.add_command(call_group)

import glob


# ==========================
# GLOBALS
# ==========================
ACTIVE_UNINSTALLS = {}      # guild_id → status
MAX_CONSOLE_LINES = 1000
SPINNER_FRAMES = ["-", "\\", "|", "/"]

_BACKUPS_DIR = os.path.join(_storage_dir, "Backups")
os.makedirs(_BACKUPS_DIR, exist_ok=True)


# ==========================
# BASIC UTILITIES
# ==========================
def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==========================
# BACKUP HELPERS
# ==========================
def backup_guild_data(guild: discord.Guild) -> dict:
    backup = {
        "meta": {
            "id": guild.id,
            "name": guild.name,
            "timestamp": int(time.time())
        },
        "files": {}
    }

    for pattern in (
        os.path.join(_storage_dir, "Config", "*.json"),
        os.path.join(_storage_dir, "Data", "*.json"),
        os.path.join(_storage_dir, "Temp", "*.json"),
    ):
        for path in glob.glob(pattern):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            if isinstance(data, dict) and str(guild.id) in data:
                backup["files"][path] = {str(guild.id): data[str(guild.id)]}

            if os.path.basename(path).startswith(str(guild.id)):
                backup["files"][path] = data

    return backup

def save_backup_to_disk(guild_id: int, backup: dict) -> str:
    path = os.path.join(_BACKUPS_DIR, f"{guild_id}_{backup['meta']['timestamp']}.json")
    save_json(path, backup)
    return path

async def send_backup_to_user(user: discord.User, path: str):
    try:
        await user.send("Backup of your server before uninstall:", file=discord.File(path))
        return True
    except Exception:
        return False


# ==========================
# PROGRESS BAR + SMOOTH ANIMATION
# ==========================
def make_progress_header(percent: int, spinner: str) -> str:
    filled = int(percent // 5)
    bar = "█" * filled + "░" * (20 - filled)
    return f"[{bar}] {percent:>3}%  {spinner}"

async def smooth_progress(msg, start, end, wheel, console, guild_id, view):
    """
    Smoothly animate the progress bar from start→end.
    This will keep updating the message while other tasks run in between calls.
    """
    percent = float(start)
    step = max((end - start) / 20, 1)

    while percent < end:
        if ACTIVE_UNINSTALLS.get(guild_id) == "cancel":
            console.append("[INFO] Uninstall cancelled by user.")
            try:
                # remove interactive view so it's not clickable anymore
                await msg.edit(view=None)
            except Exception:
                pass
            return wheel

        percent += step
        if percent > end:
            percent = end

        spinner = SPINNER_FRAMES[wheel % len(SPINNER_FRAMES)]
        wheel += 1

        header = make_progress_header(int(percent), spinner)
        safe_log = "\n".join(console[-MAX_CONSOLE_LINES:])

        # protect if msg has no embeds
        try:
            embed = msg.embeds[0]
        except Exception:
            embed = discord.Embed(title="Uninstalling Coffeecord...", color=discord.Color.yellow())

        embed.description = f"```\n{header}\n\n{safe_log}\n```"

        try:
            await msg.edit(embed=embed, view=view)
        except Exception as e:
            console.append(_redact_discord_token_in_text(f"[ERROR] Failed to update progress: {e}"))

        # small sleep to allow other tasks to run
        await asyncio.sleep(0.07)

    return wheel


# ==========================
# HELPERS FOR SAFE OPERATION
# ==========================
async def safe_sleep():
    """Yield control frequently to avoid blocking the event loop."""
    await asyncio.sleep(0.01)

def should_cancel(guild_id):
    return ACTIVE_UNINSTALLS.get(guild_id) == "cancel"

async def update_progress_embed_minimal(msg, console, guild_id, view=None):
    """
    Perform a minimal embed edit to surface current console logs (without touching percent).
    Useful inside long-running tasks to keep UI alive and surface warnings/errors.
    """
    try:
        try:
            embed = msg.embeds[0]
        except Exception:
            embed = discord.Embed(title="Uninstalling Coffeecord...", color=discord.Color.yellow())

        safe_log = "\n".join(console[-MAX_CONSOLE_LINES:])
        # keep existing header if present, otherwise show basic title
        header = ""
        if embed.description:
            # preserve first line(s) but replace logs
            lines = embed.description.splitlines()
            # fallback header if lines are empty
            header = lines[0] if lines else ""
        embed.description = f"{header}\n\n```\n{safe_log}\n```"
        await msg.edit(embed=embed, view=view)
    except Exception:
        # don't raise — progress will still be updated by smooth_progress
        pass


# ==========================
# CLEANUP OPERATIONS (BATCH + CANCELLABLE)
# ==========================
async def delete_bot_messages(guild: discord.Guild, console, msg, bot_user):
    """
    Delete bot messages across all text channels using batches.
    This is Option B: exhaustive deletion, cancellable, non-blocking.
    Returns True on success, False if cancelled.
    """
    console.append("[INFO] Deleting bot messages (batch mode, cancellable)...")
    deleted = 0

    # iterate channels in a snapshot
    for ch in list(guild.text_channels):
        if should_cancel(guild.id):
            console.append("[CANCEL] Stopping message deletion.")
            return False

        console.append(f"[INFO] Scanning #{ch.name} for bot messages...")
        batch = []

        try:
            # iterate newest → oldest to delete recent messages first
            async for m in ch.history(limit=None, oldest_first=False):
                if should_cancel(guild.id):
                    console.append("[CANCEL] Stopping message deletion during history scan.")
                    return False

                # skip the uninstall progress message itself
                try:
                    if m.id == msg.id:
                        continue
                except Exception:
                    pass

                # only consider messages authored by the bot
                try:
                    if m.author and m.author.id == bot_user.id:
                        batch.append(m)
                except Exception:
                    pass

                # if batch full, delete it
                if len(batch) >= 10:
                    for item in batch:
                        if should_cancel(guild.id):
                            console.append("[CANCEL] Stopping message deletion mid-batch.")
                            return False
                        try:
                            await item.delete()
                            deleted += 1
                        except Exception:
                            console.append(f"[WARN] Couldn't delete message {getattr(item, 'id', 'unknown')} in #{ch.name}")

                    batch = []
                    console.append(f"[OK] Deleted a batch; total deleted: {deleted}")
                    # surface progress to the message so the user sees activity
                    await update_progress_embed_minimal(msg, console, guild.id)
                    await safe_sleep()

        except discord.Forbidden:
            console.append(f"[WARN] Missing view/history permissions for #{ch.name}")
            await safe_sleep()
        except Exception:
            console.append(f"[WARN] Couldn't read history for #{ch.name}")
            await safe_sleep()

        # delete any remaining from last incomplete batch
        if batch:
            for item in batch:
                if should_cancel(guild.id):
                    console.append("[CANCEL] Stopping message deletion before final batch delete.")
                    return False
                try:
                    await item.delete()
                    deleted += 1
                except Exception:
                    console.append(f"[WARN] Couldn't delete message {getattr(item, 'id', 'unknown')} in #{ch.name}")
            batch = []
            console.append(f"[OK] Deleted remainder in #{ch.name}; total deleted: {deleted}")
            await update_progress_embed_minimal(msg, console, guild.id)
            await safe_sleep()

    console.append(f"[OK] Full message deletion complete → {deleted} messages removed.")
    return True


async def delete_bot_channels(guild: discord.Guild, console, msg_channel):
    """
    Delete channels that contain 'coffeecord' in their name, but preserve the progress channel.
    Cancellable and yields frequently.
    """
    console.append("[INFO] Removing Coffeecord channels (preserving progress channel)...")
    deleted = 0

    for ch in list(guild.channels):
        if should_cancel(guild.id):
            console.append("[CANCEL] Channel deletion stopped.")
            return False

        # preserve the channel that hosts the progress message
        try:
            if msg_channel and ch.id == getattr(msg_channel, "id", None):
                console.append(f"[INFO] Skipping progress channel #{getattr(ch, 'name', 'unknown')}")
                continue
        except Exception:
            pass

        name = (getattr(ch, "name", "") or "").lower()
        if "coffeecord" in name:
            try:
                await ch.delete()
                deleted += 1
                console.append(f"[OK] Deleted channel #{name}")
            except discord.Forbidden:
                console.append(f"[WARN] Missing permission to delete channel #{name}")
            except Exception:
                console.append(f"[WARN] Could not delete channel #{name}")

        # yield frequently
        await safe_sleep()

    console.append(f"[OK] Deleted {deleted} Coffeecord channels")
    return True


async def delete_bot_roles(guild: discord.Guild, console):
    """
    Delete roles with 'coffeecord' in their name. Cancellable and yields frequently.
    """
    console.append("[INFO] Removing Coffeecord roles...")
    deleted = 0

    for role in list(guild.roles):
        if should_cancel(guild.id):
            console.append("[CANCEL] Role deletion stopped.")
            return False

        try:
            name = (role.name or "").lower()
            if "coffeecord" in name:
                try:
                    await role.delete()
                    deleted += 1
                    console.append(f"[OK] Deleted role {role.name}")
                except discord.Forbidden:
                    console.append(f"[WARN] Missing permission to delete role {role.name}")
                except Exception:
                    console.append(f"[WARN] Could not delete role {role.name}")
        except Exception:
            pass

        await safe_sleep()

    console.append(f"[OK] Removed {deleted} roles")
    return True


async def cleanup_permissions(guild: discord.Guild, console):
    console.append("[INFO] Cleaning permissions...")
    # implement permission cleanup if you have specific overwrites to remove
    await asyncio.sleep(0.5)
    console.append("[OK] Permissions cleaned.")
    return True


async def cleanup_json(guild: discord.Guild, console):
    console.append("[INFO] Cleaning JSON entries...")

    for path in (
        glob.glob(os.path.join(_storage_dir, "Config", "*.json"))
        + glob.glob(os.path.join(_storage_dir, "Data", "*.json"))
        + glob.glob(os.path.join(_storage_dir, "Temp", "*.json"))
    ):
        if should_cancel(guild.id):
            console.append("[CANCEL] JSON cleanup stopped.")
            return False
        try:
            data = read_json(path)
            if isinstance(data, dict) and str(guild.id) in data:
                del data[str(guild.id)]
                save_json(path, data)
                console.append(f"[OK] Cleaned {path}")
        except Exception:
            console.append(f"[WARN] Couldn't clean JSON file {path}")
        await safe_sleep()

    return True


# ==========================
# UI VIEWS
# ==========================
class ConfirmView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.result = None

    @discord.ui.button(label="Yes — Uninstall", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, btn: discord.ui.Button):
        # keep view alive briefly while we start the uninstall
        self.result = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.result = False
        await interaction.response.send_message("Uninstall cancelled.", ephemeral=True)
        self.stop()

class InProgressControls(discord.ui.View):
    def __init__(self, guild_id):
        # never timeout so owner can cancel anytime during uninstall
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Cancel Uninstall", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, btn: discord.ui.Button):
        ACTIVE_UNINSTALLS[self.guild_id] = "cancel"
        await interaction.response.send_message("Cancellation requested. Stopping uninstall...", ephemeral=True)


# ==========================
# UNINSTALL COMMAND (TOP LEVEL)
# ==========================
@tree.command(name="uninstall", description="Uninstall Coffeecord from this server.")
async def uninstall(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    # confirmation
    confirm_view = ConfirmView(guild.id)
    embed = discord.Embed(
        title="☕ Confirm Uninstall",
        description="Are you sure you want to uninstall Coffeecord?",
        color=discord.Color.orange()
    )

    await interaction.response.send_message(embed=embed, view=confirm_view)
    await confirm_view.wait()

    if not confirm_view.result:
        return

    # START
    ACTIVE_UNINSTALLS[guild.id] = "running"
    controls = InProgressControls(guild.id)
    console = []
    wheel = 0

    start_embed = discord.Embed(
        title="Uninstalling Coffeecord...",
        description="```\nStarting...\n```",
        color=discord.Color.yellow()
    )

    # send the followup and get the actual message object by using wait=True
    msg = await interaction.followup.send(embed=start_embed, view=controls, wait=True)

    # BACKUP
    backup = backup_guild_data(guild)
    path = save_backup_to_disk(guild.id, backup)
    console.append(f"[OK] Backup saved → {os.path.basename(path)}")

    await send_backup_to_user(interaction.user, path)
    wheel = await smooth_progress(msg, 0, 15, wheel, console, guild.id, controls)

    # DELETE MESSAGES (skip progress message)
    bot_user = interaction.client.user
    ok = await delete_bot_messages(guild, console, msg, bot_user)
    if ok is False:
        # cancelled during message deletion
        console.append("[INFO] Uninstall cancelled during message deletion.")
        await update_progress_embed_minimal(msg, console, guild.id, controls)
        try:
            await msg.edit(view=None)
        except Exception:
            pass
        ACTIVE_UNINSTALLS.pop(guild.id, None)
        return
    wheel = await smooth_progress(msg, 15, 40, wheel, console, guild.id, controls)

    # DELETE CHANNELS (skip the progress channel)
    ok = await delete_bot_channels(guild, console, msg.channel)
    if ok is False:
        console.append("[INFO] Uninstall cancelled during channel deletion.")
        await update_progress_embed_minimal(msg, console, guild.id, controls)
        ACTIVE_UNINSTALLS.pop(guild.id, None)
        return
    wheel = await smooth_progress(msg, 40, 55, wheel, console, guild.id, controls)

    # DELETE ROLES
    ok = await delete_bot_roles(guild, console)
    if ok is False:
        console.append("[INFO] Uninstall cancelled during role deletion.")
        await update_progress_embed_minimal(msg, console, guild.id, controls)
        ACTIVE_UNINSTALLS.pop(guild.id, None)
        return
    wheel = await smooth_progress(msg, 55, 70, wheel, console, guild.id, controls)

    # CLEANUP JSON
    ok = await cleanup_json(guild, console)
    if ok is False:
        console.append("[INFO] Uninstall cancelled during JSON cleanup.")
        await update_progress_embed_minimal(msg, console, guild.id, controls)
        ACTIVE_UNINSTALLS.pop(guild.id, None)
        return
    wheel = await smooth_progress(msg, 70, 90, wheel, console, guild.id, controls)

    # PERMISSIONS
    ok = await cleanup_permissions(guild, console)
    if ok is False:
        console.append("[INFO] Uninstall cancelled during permission cleanup.")
        await update_progress_embed_minimal(msg, console, guild.id, controls)
        ACTIVE_UNINSTALLS.pop(guild.id, None)
        return
    wheel = await smooth_progress(msg, 90, 100, wheel, console, guild.id, controls)

    # COMPLETE
    final_embed = discord.Embed(
        title="☕ Coffeecord Uninstalled",
        description="```\nUninstall complete.\n```",
        color=discord.Color.red()
    )

    try:
        await msg.edit(embed=final_embed, view=None)
    except Exception as e:
        console.append(_redact_discord_token_in_text(f"[ERROR] Failed to edit final message: {e}"))

    # keep visible for a short moment so the server owner can read it
    await asyncio.sleep(5)

    # attempt to leave the guild
    try:
        await guild.leave()
    except Exception as e:
        console.append(_redact_discord_token_in_text(f"[WARN] Failed to leave guild: {e}"))

    ACTIVE_UNINSTALLS.pop(guild.id, None)


@bot.event
async def on_error(event, *args, **kwargs):
    _print_redacted("GLOBAL ERROR:")
    _print_traceback_redacted()

@bot.event
async def on_ready():
    try:
        print(f"🤖 Logged in as {bot.user}")
        _load_reminders_and_timers()
        for _verify_gid in load_verify_config():
            bot.add_view(VerifyStartView(_verify_gid))
        bot.add_view(LegacyVerifyStartView())

        # Shared aiohttp session for HTTP requests (dog, cat, level cards, etc.)
        # Close existing session first (on_ready can fire on reconnects)
        existing = getattr(bot, "http_session", None)
        if existing is not None and not existing.closed:
            await existing.close()
        bot.http_session = aiohttp.ClientSession()

        _get_tickets_module().register_persistent_views(bot)

        # Preload automod and leveling to remove first-use delay
        _get_automod_module()
        _get_leveling_module()

        for attempt in range(3):
            try:
                synced = await tree.sync()
                print("Synced:", len(synced))
                break
            except (discord.HTTPException, discord.ConnectionError) as e:
                if attempt < 2:
                    print(
                        _redact_discord_token_in_text(
                            f"[WARN] tree.sync attempt {attempt + 1} failed: {e}, retrying in 2s..."
                        )
                    )
                    await asyncio.sleep(2)
                else:
                    print(_redact_discord_token_in_text(f"[ERROR] tree.sync failed after 3 attempts: {e}"))
                    raise

    except Exception:
        _print_redacted("ERROR IN on_ready():")
        _print_traceback_redacted()

import json
import hmac
import hashlib
import base64

# Keep ticket storage path local to avoid importing ticket module at startup.
TICKETS_FILE = os.path.join(_storage_dir, "Data", "tickets.json")

# Load from ticket.env (generated by c-cord start); fallback for direct runs
_secret = os.getenv("TICKET_SECRET", "").strip()
TICKET_SECRET = _secret.encode() if _secret else os.urandom(32)

# ---------------- Signing helpers ----------------
def sign_payload(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True).encode()
    signature = hmac.new(TICKET_SECRET, raw, hashlib.sha256).digest()
    return base64.b64encode(signature).decode()

def verify_payload(payload: dict, signature: str) -> bool:
    raw = json.dumps(payload, sort_keys=True).encode()
    expected = hmac.new(TICKET_SECRET, raw, hashlib.sha256).digest()
    provided = base64.b64decode(signature.encode())
    return hmac.compare_digest(expected, provided)

# ---------------- /ticket_export ----------------
@bot.tree.command(name="ticket_export", description="Export this ticket as a downloadable signed JSON")
async def ticket_export(interaction: discord.Interaction):
    channel = interaction.channel
    guild_id = str(interaction.guild.id)
    channel_id = str(channel.id)

    data = load_json(TICKETS_FILE, {})
    ticket = data.get(guild_id, {}).get("tickets", {}).get(channel_id)

    if not ticket:
        await interaction.response.send_message(
            "❌ This channel is not a ticket.", ephemeral=True
        )
        return

    # ---------- collect transcript ----------
    transcript = []
    async for msg in channel.history(limit=None, oldest_first=True):
        # skip bot panels / empty messages if you want
        # if msg.author.bot and msg.type != discord.MessageType.default:
        #     continue
        if not msg.content and not msg.embeds:
            continue

        transcript.append({
            "author": str(msg.author),
            "author_id": msg.author.id,
            "content": msg.content,
            "timestamp": int(msg.created_at.timestamp())
        })

    # ---------- build payload ----------
    payload = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "ticket": ticket,
        "transcript": transcript,
        "exported_at": int(time.time())
    }

    export_blob = {
        "payload": payload,
        "signature": sign_payload(payload)  # assumes sign_payload exists
    }

    json_text = json.dumps(export_blob, indent=4)

    # ---------- create in-memory file and send as attachment ----------
    fp = io.BytesIO(json_text.encode("utf-8"))
    filename = f"ticket_{channel_id}_{int(time.time())}.json"

    try:
        # Note: ephemeral messages cannot contain attachments, so this is a normal message
        await interaction.response.send_message(
            "✅ Ticket exported — click the file below to download.",
            file=discord.File(fp, filename)
        )
    except Exception as e:
        # fallback: send the JSON as codeblock if sending file fails
        await interaction.response.send_message(
            "⚠️ Failed to attach file, here's the JSON (copy & save):\n"
            f"```json\n{json_text}\n```",
            ephemeral=True
        )

# ---------------- /ticket_import ----------------
@bot.tree.command(name="ticket_import", description="Import a ticket from a signed JSON file")
@app_commands.describe(file="The exported ticket JSON file")
async def ticket_import(interaction: discord.Interaction, file: discord.Attachment):
    # ---- Validate file ----
    if not file.filename.endswith(".json"):
        await interaction.response.send_message(
            "❌ Please upload a valid `.json` ticket export file.",
            ephemeral=True
        )
        return

    try:
        raw_bytes = await file.read()
        blob = json.loads(raw_bytes.decode("utf-8"))
        payload = blob["payload"]
        signature = blob["signature"]
    except Exception:
        await interaction.response.send_message(
            "❌ Invalid or corrupted ticket file.",
            ephemeral=True
        )
        return

    # ---- Verify signature ----
    if not verify_payload(payload, signature):
        await interaction.response.send_message(
            "❌ Ticket signature verification failed. This file may be forged.",
            ephemeral=True
        )
        return

    # ---- Guild check ----
    if payload["guild_id"] != str(interaction.guild.id):
        await interaction.response.send_message(
            "❌ This ticket belongs to a different server.",
            ephemeral=True
        )
        return

    ticket = payload["ticket"]
    transcript = payload.get("transcript", [])

    guild = interaction.guild
    guild_id = str(guild.id)

    # ---- Create channel ----
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
    }

    user_id = ticket.get("user")
    if user_id:
        member = guild.get_member(int(user_id))
        if member:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True
            )

    channel = await guild.create_text_channel(
        name="restored-ticket",
        overwrites=overwrites,
        reason="Imported archived ticket"
    )

    # ---- Save ticket back into JSON ----
    data = load_json(TICKETS_FILE, {})
    data.setdefault(guild_id, {}).setdefault("tickets", {})
    data[guild_id]["tickets"][str(channel.id)] = ticket
    save_json(TICKETS_FILE, data)

    # ---- Send transcript ----
    if transcript:
        embed = discord.Embed(
            title="📜 Ticket Transcript",
            description="Restored from archived ticket",
            color=discord.Color.blurple()
        )
        await channel.send(embed=embed)

        for entry in transcript:
            ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%I:%M %p")
            await channel.send(
                f"{entry['content']}\n"
                f"— **{entry['author']}** • {ts}"
            )

    # ---- Reattach control panel ----
    await channel.send(
        "🎛️ **Ticket Controls (Restored)**",
        view=_get_tickets_module().TicketControlPanel(guild_id, channel.id)
    )

    await interaction.response.send_message(
        f"✅ Ticket successfully restored in {channel.mention}",
        ephemeral=True
    )

import discord, sys
print("DISCORD VERSION:", discord.__version__)
print("DISCORD FILE:", discord.__file__)
print("PYTHON:", sys.executable)

async def _run_bot_with_kofi():
    kofi_server = None
    kofi_token = os.getenv("KOFI_VERIFICATION_TOKEN", "").strip()
    kofi_port = int(os.getenv("KOFI_PORT", "5000"))
    from kofi_webhook import KoFiWebhookServer
    from Modules.module_registry import load_module_registry, validate_registry_paths

    registry_errors = await validate_registry_paths()
    missing_module_ids = set()
    for _msg in registry_errors:
        if _msg.startswith("Module '") and "' points to missing path:" in _msg:
            try:
                missing_module_ids.add(_msg.split("'")[1].strip().lower())
            except Exception:
                pass
    if registry_errors:
        print(
            f"[module-registry] {len(registry_errors)} module file(s) are not present yet; "
            "those modules will be skipped during startup.",
            flush=True,
        )

    # This command module must always remain available.
    try:
        await bot.load_extension("Modules.modules_cmd")
        print("Loaded extension: Modules.modules_cmd", flush=True)
    except commands.ExtensionAlreadyLoaded:
        pass
    except Exception as e:
        print(
            _redact_discord_token_in_text(f"Failed to load mandatory extension Modules.modules_cmd: {e}"),
            flush=True,
        )

    skipped_missing_extensions: list[str] = []
    loaded_count = 0
    for module in await load_module_registry():
        extension = str(module.get("extension", "")).strip()
        module_id = str(module.get("id", "")).strip().lower()
        if not extension or module_id == "modules_cmd":
            continue
        if module_id in missing_module_ids:
            skipped_missing_extensions.append(extension)
            continue
        try:
            await bot.load_extension(extension)
            print(f"Loaded extension: {extension}", flush=True)
            loaded_count += 1
        except commands.ExtensionAlreadyLoaded:
            continue
        except Exception as e:
            print(
                _redact_discord_token_in_text(f"Failed to load extension {extension}: {e}"),
                flush=True,
            )

    print(f"[module-registry] Loaded {loaded_count} extension(s).", flush=True)

    if skipped_missing_extensions:
        print(
            f"[module-registry] Skipped {len(skipped_missing_extensions)} unavailable module extension(s): "
            + ", ".join(skipped_missing_extensions),
            flush=True,
        )

    if kofi_token:
        kofi_server = KoFiWebhookServer(
            verification_token=kofi_token,
            pending_links=pending_kofi_links,
            on_payload=handle_kofi_payload,
        )
        await kofi_server.start(host="0.0.0.0", port=kofi_port)
        print(f"Ko-fi webhook server listening on :{kofi_port}")
    else:
        print("Ko-fi webhook disabled (KOFI_VERIFICATION_TOKEN is not set).")

    try:
        await bot.start(token)
    finally:
        if kofi_server is not None:
            await kofi_server.stop()
        session = getattr(bot, "http_session", None)
        if session is not None and not session.closed:
            await session.close()


if __name__ == "__main__":
    asyncio.run(_run_bot_with_kofi())
