# ================== AUTOMOD ==================
# Import bot and tree from the main script (must be imported after bot/tree are defined)
import copy
import json
import re
import sys
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord import app_commands

_main = sys.modules.get("__main__")
if _main and hasattr(_main, "bot") and hasattr(_main, "tree"):
    bot = _main.bot
    tree = _main.tree
else:
    raise RuntimeError(
        "automod.py must be imported from the main bot script after bot and tree are defined"
    )

from Modules import json_cache

# Paths relative to Discordbot root (parent of Modules)
_discordbot_root = Path(__file__).resolve().parent.parent
CONFIG_PATH = _discordbot_root / "Storage" / "Config" / "automod.json"
WARNS_PATH = _discordbot_root / "Storage" / "Data" / "warns.json"
STRIKES_PATH = _discordbot_root / "Storage" / "Data" / "automod_strikes.json"

RULE_NAMES = [
    "bad_words",
    "spam",
    "duplicate_messages",
    "links",
    "mentions",
    "caps",
    "attachments",
    "custom_regex",
    "anti_selfbot",
    "new_user",
]

ACTION_NAMES = ["delete", "warn", "timeout", "kick", "ban", "log_only"]

DEFAULT_TOKEN_PATTERN_STRINGS = [
    r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}",
    r"mfa\.[\w-]{60,}",
    r"(?:discord|bot).{0,20}(?:token|auth)",
]


def _dispatch_custom_event(event_name: str, *args) -> None:
    """Dispatch bot events without raising if logging/event hooks are absent."""
    try:
        bot.dispatch(event_name, *args)
    except Exception:
        pass


def _parse_duration_seconds(raw: str) -> int | None:
    """
    Parse duration text like:
    - 300
    - 300s
    - 5m
    - 1h
    - 1d
    """
    value = raw.strip().lower()
    if not value:
        return None
    if value.isdigit():
        return int(value)

    match = re.fullmatch(r"(\d+)\s*([smhd])", value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    if unit == "d":
        return amount * 86400
    return None


def _parse_warn_step(value: str) -> dict | None:
    """
    Supported examples:
    - warn
    - timeout 300
    - timeout:300
    - mute 1h
    - mute:3600
    - kick
    - ban
    - none / off / disable
    """
    text = value.strip().lower()
    if text in {"none", "off", "disable", "disabled", "clear"}:
        return None
    if text in {"warn", "kick", "ban", "log_only"}:
        return {"action": text}

    timeout_match = re.fullmatch(r"(timeout|mute)\s*[: ]\s*(.+)", text)
    if timeout_match:
        action = timeout_match.group(1)
        duration_raw = timeout_match.group(2).strip()
        seconds = _parse_duration_seconds(duration_raw)
        if seconds is None or seconds <= 0:
            return {"error": f"Invalid duration: `{duration_raw}`"}
        # "mute" is implemented as a timeout duration for reliability.
        return {"action": action, "seconds": seconds}

    return {"error": f"Unsupported action format: `{value}`"}

DEFAULT_GUILD_CONFIG = {
    "enabled": False,
    "count_rule_violations_as_warns": False,
    "log_channel_id": None,
    "whitelist": {"roles": [], "channels": []},
    "protected_roles": [],
    "channel_overrides": {},
    "bad_words": {
        "enabled": True,
        "words": [],
        "action": "warn",
        "delete_message": True,
        "escalation": [],
    },
    "spam": {
        "enabled": True,
        "max_messages": 5,
        "per_seconds": 6,
        "action": "timeout",
        "timeout_seconds": 60,
        "escalation": [],
    },
    "duplicate_messages": {
        "enabled": False,
        "window_seconds": 30,
        "min_duplicates": 3,
        "action": "delete",
        "escalation": [],
    },
    "links": {
        "enabled": True,
        "block_invites": True,
        "block_links": True,
        "allowed_domains": [],
        "allowed_invite_codes": [],
        "action": "delete",
        "escalation": [],
    },
    "mentions": {
        "enabled": True,
        "max_mentions": 5,
        "action": "warn",
        "escalation": [],
    },
    "caps": {
        "enabled": True,
        "min_length": 10,
        "caps_percent": 70,
        "action": "delete",
        "escalation": [],
    },
    "attachments": {
        "enabled": False,
        "max_attachments": 6,
        "max_embeds": 3,
        "action": "delete",
        "escalation": [],
    },
    "custom_regex": {
        "enabled": False,
        "rules": [],
        "escalation": [],
    },
    "anti_selfbot": {
        "enabled": False,
        "action": "delete",
        "use_builtin_patterns": True,
        "builtin_patterns": copy.deepcopy(DEFAULT_TOKEN_PATTERN_STRINGS),
        "extra_patterns": [],
        "escalation": [],
    },
    "new_user": {
        "enabled": False,
        "max_account_age_days": 7,
        "action": "warn",
        "channels_only": [],
        "escalation": [],
    },
    "anti_raid": {
        "enabled": False,
        "window_seconds": 10,
        "join_threshold": 10,
        "cooldown_seconds": 60,
        "action": "timeout",
        "timeout_seconds": 300,
    },
    "warn_thresholds": {
        "3": {"action": "timeout", "seconds": 300},
        "5": {"action": "kick"},
    },
}

DEFAULT_CONFIG = {"default": copy.deepcopy(DEFAULT_GUILD_CONFIG)}

# Applied to guild config when using `/automod preset` — tune with `/automod badword` etc.
AUTOMOD_PRESET_STARTER_BAD_WORDS: tuple[str, ...] = (
    # Slurs / harassment (substring list; customize per community)
    "nigger",
    "nigga",
    "faggot",
    "retard",
    "spic",
    "chink",
    "kys",
    "kill yourself",
    # Profanity (remove if too strict for your server)
    "fuck",
    "shit",
    "bitch",
    "cunt",
    "dick",
    "cock",
    "pussy",
    # Common scam / spam phrases
    "free nitro",
    "discord nitro for free",
    "claim your nitro",
    "verify wallet",
    "connect wallet",
    "airdrop",
    "send eth",
    "send btc",
)


def _automod_preset_custom_regex_rules(action: str) -> list[dict[str, str]]:
    """Concrete patterns so strict/dictatorship presets actually match messages."""
    specs: tuple[tuple[str, str], ...] = (
        (
            "Steam scam URL typo",
            r"(?i)https?://[^\s]*stea[mn][^\s]*commun",
        ),
        (
            "Nitro scam text + link",
            r"(?i)(?:claim|free|get)\s+[^\n]{0,40}nitro[^\n]{0,50}https?://",
        ),
        (
            "Fake discord-themed TLD",
            r"(?i)https?://[^\s]*discord[^\s]*\.(?:gift|fun|lol|cfd|skin)\b",
        ),
    )
    return [{"name": name, "pattern": pattern, "action": action} for name, pattern in specs]


class AutomodResult:
    def __init__(self, rule, action, reason, extra=None):
        self.rule = rule
        self.action = action
        self.reason = reason
        self.extra = extra or {}


def load_json(path: Path, default):
    return json_cache.get(path, copy.deepcopy(default) if default is not None else {})


def save_json(path: Path, data):
    json_cache.set_(path, data)


def merge_dicts(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_dicts(out[key], value)
        else:
            out[key] = value
    return out


def normalize_config(raw: dict) -> dict:
    if not isinstance(raw, dict) or not raw:
        return copy.deepcopy(DEFAULT_CONFIG)

    legacy_keys = {"enabled", "whitelist", "bad_words", "spam", "links", "mentions", "caps"}
    if "default" not in raw and any(k in raw for k in legacy_keys):
        raw = {"default": raw}

    normalized = {}
    default_cfg = raw.get("default", {})
    if not isinstance(default_cfg, dict):
        default_cfg = {}
    normalized["default"] = merge_dicts(DEFAULT_GUILD_CONFIG, default_cfg)

    for guild_id, guild_cfg in raw.items():
        if guild_id == "default" or not isinstance(guild_cfg, dict):
            continue
        normalized[str(guild_id)] = merge_dicts(DEFAULT_GUILD_CONFIG, guild_cfg)

    return normalized


config = normalize_config(load_json(CONFIG_PATH, DEFAULT_CONFIG))
warns = load_json(WARNS_PATH, {})
strikes = load_json(STRIKES_PATH, {})
spam_cache = defaultdict(list)
duplicate_cache = defaultdict(list)
join_cache = defaultdict(list)
raid_mode_until = defaultdict(float)
save_json(CONFIG_PATH, config)


def get_guild_config(guild_id: int) -> dict:
    guild_cfg = config.get(str(guild_id), {})
    return merge_dicts(config.get("default", DEFAULT_GUILD_CONFIG), guild_cfg)


def get_rule_config(guild_cfg: dict, rule_name: str, channel_id: int | None = None) -> dict:
    """Resolve rule config with optional channel overrides."""
    merged = copy.deepcopy(guild_cfg.get(rule_name, {}))
    if channel_id is None:
        return merged

    channel_overrides = guild_cfg.get("channel_overrides", {})
    channel_cfg = channel_overrides.get(str(channel_id), {})
    if not isinstance(channel_cfg, dict):
        return merged
    rule_override = channel_cfg.get(rule_name, {})
    if not isinstance(rule_override, dict):
        return merged
    return merge_dicts(merged, rule_override)


def get_channel_override(guild_id: int, channel_id: int) -> dict:
    override = update_guild_override(guild_id)
    channel_overrides = override.setdefault("channel_overrides", {})
    return channel_overrides.setdefault(str(channel_id), {})


def update_guild_override(guild_id: int) -> dict:
    key = str(guild_id)
    if key not in config or not isinstance(config.get(key), dict):
        config[key] = {}
    return config[key]


def add_warn(guild_id, user_id, reason, by: str = "Automod"):
    guild_id = str(guild_id)
    user_id = str(user_id)
    warns.setdefault(guild_id, {}).setdefault(user_id, [])
    warns[guild_id][user_id].append(
        {"reason": reason, "timestamp": int(time.time()), "by": by}
    )
    save_json(WARNS_PATH, warns)
    return len(warns[guild_id][user_id])


def add_rule_strike(guild_id, user_id, rule):
    guild_id = str(guild_id)
    user_id = str(user_id)
    strikes.setdefault(guild_id, {}).setdefault(user_id, {}).setdefault(rule, [])
    strikes[guild_id][user_id][rule].append(int(time.time()))
    save_json(STRIKES_PATH, strikes)
    return len(strikes[guild_id][user_id][rule])


def is_whitelisted(message: discord.Message, guild_cfg: dict):
    wl = guild_cfg.get("whitelist", {})
    if message.channel.id in wl.get("channels", []):
        return True
    if isinstance(message.author, discord.Member) and any(
        role.id in wl.get("roles", []) for role in message.author.roles
    ):
        return True
    return False


def has_protected_role(member: discord.abc.User, guild_cfg: dict):
    if not isinstance(member, discord.Member):
        return False
    protected = set(guild_cfg.get("protected_roles", []))
    return any(role.id in protected for role in member.roles)


def _author_higher_or_equal(guild: discord.Guild, member: discord.Member) -> bool:
    me = guild.me
    if me is None:
        return True
    if member.id == guild.owner_id:
        return True
    return member.top_role >= me.top_role


def can_perform_action(guild: discord.Guild, member: discord.Member, action: str):
    me = guild.me
    if me is None:
        return False, "Bot member unavailable."

    perms = me.guild_permissions
    if action == "delete":
        return perms.manage_messages, "Missing `Manage Messages` permission."
    if action == "timeout":
        if not perms.moderate_members:
            return False, "Missing `Moderate Members` permission."
        if _author_higher_or_equal(guild, member):
            return False, "Cannot timeout due to role hierarchy."
        return True, ""
    if action == "kick":
        if not perms.kick_members:
            return False, "Missing `Kick Members` permission."
        if _author_higher_or_equal(guild, member):
            return False, "Cannot kick due to role hierarchy."
        return True, ""
    if action == "ban":
        if not perms.ban_members:
            return False, "Missing `Ban Members` permission."
        if _author_higher_or_equal(guild, member):
            return False, "Cannot ban due to role hierarchy."
        return True, ""
    return True, ""


async def send_modlog_embed(guild: discord.Guild, guild_cfg: dict, embed: discord.Embed):
    channel_id = guild_cfg.get("log_channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        try:
            fetched = await guild.fetch_channel(int(channel_id))
            if isinstance(fetched, discord.TextChannel):
                channel = fetched
        except (discord.HTTPException, ValueError):
            return

    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            return


async def notify_user_automod_action(
    member: discord.Member,
    action: str,
    reason: str,
    guild_name: str,
    rule: str | None = None,
    duration_seconds: int | None = None,
):
    from Modules.themes import send_themed_moderation_dm

    guild_id = member.guild.id if member.guild else 0
    await send_themed_moderation_dm(
        member,
        guild_id,
        action,
        guild_name,
        reason=reason,
        duration_seconds=duration_seconds,
        rule=rule,
    )


async def log_message_action(
    message: discord.Message,
    guild_cfg: dict,
    result: AutomodResult,
    action_taken: str,
):
    embed = discord.Embed(
        title="Automod Action",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Rule", value=result.rule, inline=True)
    embed.add_field(name="Configured Action", value=result.action, inline=True)
    embed.add_field(name="Applied", value=action_taken, inline=True)
    embed.add_field(name="User", value=f"{message.author} (`{message.author.id}`)", inline=False)
    embed.add_field(name="Channel", value=message.channel.mention, inline=False)
    embed.add_field(name="Reason", value=result.reason[:1024], inline=False)

    if message.content:
        preview = message.content[:1000]
        embed.add_field(name="Message", value=preview, inline=False)
    embed.add_field(name="Jump", value=f"[Open Message]({message.jump_url})", inline=False)
    await send_modlog_embed(message.guild, guild_cfg, embed)


async def log_member_action(
    guild: discord.Guild,
    member: discord.Member,
    guild_cfg: dict,
    reason: str,
    action_taken: str,
):
    embed = discord.Embed(
        title="Automod Member Action",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Rule", value="anti_raid", inline=True)
    embed.add_field(name="Applied", value=action_taken, inline=True)
    embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason[:1024], inline=False)
    await send_modlog_embed(guild, guild_cfg, embed)


def apply_rule_escalation(
    message: discord.Message,
    rule_cfg: dict,
    result: AutomodResult,
):
    escalations = rule_cfg.get("escalation", [])
    if not isinstance(escalations, list) or not escalations:
        return result

    strike_count = add_rule_strike(message.guild.id, message.author.id, result.rule)
    matched = None
    for step in escalations:
        if not isinstance(step, dict):
            continue
        after = int(step.get("after", 0))
        if strike_count >= after:
            if matched is None or after >= int(matched.get("after", 0)):
                matched = step

    if matched is None:
        return result

    new_extra = dict(result.extra)
    if "seconds" in matched:
        new_extra["seconds"] = int(matched.get("seconds", 60))
    return AutomodResult(
        result.rule,
        matched.get("action", result.action),
        f"{result.reason} (strike #{strike_count})",
        new_extra,
    )


def _extract_urls(content: str):
    return re.findall(r"https?://[^\s<>()]+", content)


def _normalize_content(content: str) -> str:
    return " ".join(content.lower().split())


def _is_domain_allowed(domain: str, allowed_domains: list) -> bool:
    for allowed in allowed_domains:
        allowed = str(allowed).lower().strip()
        if not allowed:
            continue
        if domain == allowed or domain.endswith(f".{allowed}"):
            return True
    return False


def _extract_invite_code(url: str):
    lower = url.lower()
    if "discord.gg/" in lower:
        return lower.split("discord.gg/", 1)[1].split("?", 1)[0].strip("/")
    if "discord.com/invite/" in lower:
        return lower.split("discord.com/invite/", 1)[1].split("?", 1)[0].strip("/")
    return None


def _bad_word_matches_content(word_lower: str, content_lower: str) -> bool:
    """Match phrases with substring; single tokens use word boundaries to limit false positives."""
    if " " in word_lower or len(word_lower) >= 6:
        return word_lower in content_lower
    try:
        return re.search(rf"(?<!\w){re.escape(word_lower)}(?!\w)", content_lower) is not None
    except re.error:
        return word_lower in content_lower


def check_bad_words(message: discord.Message, cfg: dict):
    content = message.content.lower()
    for word in cfg.get("words", []):
        word = str(word).strip()
        if not word:
            continue
        wl = word.lower()
        if _bad_word_matches_content(wl, content):
            return AutomodResult(
                "bad_words",
                cfg.get("action", "warn"),
                f"Use of blocked word: {word}",
                {"delete_message": bool(cfg.get("delete_message", True))},
            )
    return None


def check_spam(message: discord.Message, cfg: dict):
    now = time.time()
    key = (message.guild.id, message.author.id)
    cache = spam_cache[key]
    cache.append(now)
    per_seconds = int(cfg.get("per_seconds", 10))
    cache[:] = [t for t in cache if now - t <= per_seconds]
    if len(cache) > int(cfg.get("max_messages", 5)):
        return AutomodResult(
            "spam",
            cfg.get("action", "timeout"),
            "Message spam",
            {"seconds": int(cfg.get("timeout_seconds", 60))},
        )
    return None


def check_duplicate_messages(message: discord.Message, cfg: dict):
    now = time.time()
    key = (message.guild.id, message.author.id)
    cache = duplicate_cache[key]
    normalized = _normalize_content(message.content)
    if not normalized:
        return None

    cache.append((now, normalized))
    window = int(cfg.get("window_seconds", 30))
    cache[:] = [(t, c) for (t, c) in cache if now - t <= window]

    min_duplicates = int(cfg.get("min_duplicates", 3))
    recent_identical = [c for (_, c) in cache if c == normalized]
    if len(recent_identical) >= min_duplicates:
        extra: dict = {}
        if str(cfg.get("action", "delete")).lower() == "timeout":
            extra["seconds"] = int(cfg.get("timeout_seconds", 60))
        return AutomodResult(
            "duplicate_messages",
            cfg.get("action", "delete"),
            "Repeated duplicate message detected",
            extra,
        )
    return None


def check_links(message: discord.Message, cfg: dict):
    urls = _extract_urls(message.content)
    if not urls:
        return None

    allowed_domains = cfg.get("allowed_domains", [])
    allowed_invites = [str(c).lower() for c in cfg.get("allowed_invite_codes", [])]
    action = cfg.get("action", "delete")

    for url in urls:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().split(":")[0]
        if domain.startswith("www."):
            domain = domain[4:]

        invite_code = _extract_invite_code(url)
        if cfg.get("block_invites", True) and invite_code:
            if invite_code not in allowed_invites:
                return AutomodResult("links", action, "Blocked Discord invite link")
            continue

        if not cfg.get("block_links", True):
            continue

        if not allowed_domains:
            return AutomodResult("links", action, "Links are not allowed here")
        if not _is_domain_allowed(domain, allowed_domains):
            return AutomodResult("links", action, f"Unapproved link domain: {domain}")
        return None


def check_mentions(message: discord.Message, cfg: dict):
    total = len(message.mentions) + len(message.role_mentions)
    if total > int(cfg.get("max_mentions", 5)):
        action = str(cfg.get("action", "warn")).lower()
        extra: dict = {}
        if action == "timeout":
            extra["seconds"] = int(cfg.get("timeout_seconds", 60))
        return AutomodResult(
            "mentions",
            cfg.get("action", "warn"),
            "Too many mentions",
            extra,
        )
    return None


def check_caps(message: discord.Message, cfg: dict):
    content = message.content
    if len(content) < int(cfg.get("min_length", 10)):
        return None
    letters = [c for c in content if c.isalpha()]
    if not letters:
        return None
    caps = sum(1 for c in letters if c.isupper())
    percent = (caps / len(letters)) * 100
    if percent >= float(cfg.get("caps_percent", 70)):
        action = str(cfg.get("action", "delete")).lower()
        extra: dict = {}
        if action == "timeout":
            extra["seconds"] = int(cfg.get("timeout_seconds", 60))
        return AutomodResult(
            "caps",
            cfg.get("action", "delete"),
            "Excessive caps usage",
            extra,
        )
    return None


def check_attachments(message: discord.Message, cfg: dict):
    max_attachments = int(cfg.get("max_attachments", 6))
    max_embeds = int(cfg.get("max_embeds", 3))

    if len(message.attachments) > max_attachments:
        return AutomodResult(
            "attachments",
            cfg.get("action", "delete"),
            f"Too many attachments ({len(message.attachments)}/{max_attachments})",
        )
    if len(message.embeds) > max_embeds:
        return AutomodResult(
            "attachments",
            cfg.get("action", "delete"),
            f"Too many embeds ({len(message.embeds)}/{max_embeds})",
        )
    return None


def check_custom_regex(message: discord.Message, cfg: dict):
    rules = cfg.get("rules", [])
    if not isinstance(rules, list):
        return None

    for rule in rules[:50]:
        if not isinstance(rule, dict):
            continue
        pattern = str(rule.get("pattern", "")).strip()
        if not pattern:
            continue
        try:
            if re.search(pattern, message.content):
                action = rule.get("action", "delete")
                name = str(rule.get("name", pattern))[:80]
                return AutomodResult(
                    "custom_regex",
                    action,
                    f"Matched custom regex rule: {name}",
                )
        except re.error:
            continue
    return None


def check_anti_selfbot(message: discord.Message, cfg: dict):
    pattern_strings = []
    if cfg.get("use_builtin_patterns", True):
        pattern_strings.extend(cfg.get("builtin_patterns", []))
    pattern_strings.extend(cfg.get("extra_patterns", []))

    patterns = []
    for pattern in pattern_strings:
        try:
            patterns.append(re.compile(str(pattern), re.IGNORECASE))
        except re.error:
            continue

    for compiled in patterns:
        if compiled.search(message.content):
            extra: dict[str, bool | int] = {"delete_message": True}
            if str(cfg.get("action", "delete")).lower() == "timeout":
                extra["seconds"] = int(cfg.get("timeout_seconds", 60))
            return AutomodResult(
                "anti_selfbot",
                cfg.get("action", "delete"),
                "Potential token/selfbot credential pattern detected",
                extra,
            )
    return None


def check_new_user(message: discord.Message, cfg: dict):
    channels_only = cfg.get("channels_only", [])
    if channels_only and message.channel.id not in channels_only:
        return None

    age_days = (discord.utils.utcnow() - message.author.created_at).days
    max_age_days = int(cfg.get("max_account_age_days", 7))
    if age_days < max_age_days:
        action = str(cfg.get("action", "warn")).lower()
        extra: dict = {}
        if action == "timeout":
            extra["seconds"] = int(cfg.get("timeout_seconds", 60))
        return AutomodResult(
            "new_user",
            cfg.get("action", "warn"),
            f"New account posting restriction ({age_days}d old account)",
            extra,
        )
    return None


async def apply_warn_threshold_action(
    member: discord.Member,
    warn_count: int,
    guild_cfg: dict,
) -> str | None:
    thresholds = guild_cfg.get("warn_thresholds", {})
    action_data = thresholds.get(str(warn_count))
    if not isinstance(action_data, dict):
        return None
    action = action_data.get("action")
    if not action:
        return None

    action = str(action).lower()
    reason = f"Reached {warn_count} warnings"
    action_taken = "log_only"

    try:
        if action == "warn":
            # Threshold can be configured to only issue a tracked warning event.
            action_taken = "warn"
        elif action == "mute":
            can_timeout, blocked = can_perform_action(member.guild, member, "timeout")
            if can_timeout:
                seconds = int(action_data.get("seconds", 3600))
                await notify_user_automod_action(
                    member,
                    "timeout",
                    reason,
                    member.guild.name,
                    rule="warn_threshold",
                    duration_seconds=seconds,
                )
                await member.timeout(
                    discord.utils.utcnow() + timedelta(seconds=seconds),
                    reason=reason,
                )
                action_taken = f"mute ({seconds}s timeout)"
            else:
                action_taken = f"blocked: {blocked}"
        elif action == "timeout":
            can_timeout, blocked = can_perform_action(member.guild, member, "timeout")
            if can_timeout:
                seconds = int(action_data.get("seconds", 60))
                await notify_user_automod_action(
                    member,
                    "timeout",
                    reason,
                    member.guild.name,
                    rule="warn_threshold",
                    duration_seconds=seconds,
                )
                await member.timeout(
                    discord.utils.utcnow() + timedelta(seconds=seconds),
                    reason=reason,
                )
                action_taken = f"timeout ({seconds}s)"
            else:
                action_taken = f"blocked: {blocked}"
        elif action == "kick":
            can_kick, blocked = can_perform_action(member.guild, member, "kick")
            if can_kick:
                await notify_user_automod_action(
                    member,
                    "kick",
                    reason,
                    member.guild.name,
                    rule="warn_threshold",
                )
                await member.kick(reason=reason)
                action_taken = "kick"
            else:
                action_taken = f"blocked: {blocked}"
        elif action == "ban":
            can_ban, blocked = can_perform_action(member.guild, member, "ban")
            if can_ban:
                await notify_user_automod_action(
                    member,
                    "ban",
                    reason,
                    member.guild.name,
                    rule="warn_threshold",
                )
                await member.ban(reason=reason, delete_message_days=0)
                action_taken = "ban"
            else:
                action_taken = f"blocked: {blocked}"
    except (discord.Forbidden, discord.HTTPException) as exc:
        action_taken = f"error: {type(exc).__name__}"

    await log_member_action(member.guild, member, guild_cfg, reason, action_taken)
    return action_taken


async def _record_automod_warn(
    message: discord.Message,
    guild_cfg: dict,
    reason: str,
    rule_name: str,
) -> tuple[int, str | None]:
    count = add_warn(message.guild.id, message.author.id, reason)
    _dispatch_custom_event(
        "coffeecord_warn",
        message.guild,
        None,
        message.author,
        reason,
        "automod",
    )

    threshold_action: str | None = None
    if isinstance(message.author, discord.Member):
        await notify_user_automod_action(
            message.author,
            "warn",
            f"{reason} (warning #{count})",
            message.guild.name,
            rule=rule_name,
        )
        threshold_action = await apply_warn_threshold_action(message.author, count, guild_cfg)
    return count, threshold_action


async def apply_action(
    message: discord.Message,
    result: AutomodResult,
    guild_cfg: dict,
    rule_cfg: dict | None = None,
):
    action = str(result.action).lower()
    action_taken = "none"

    if action == "log_only":
        await log_message_action(message, guild_cfg, result, "log_only")
        return

    delete_message = bool(
        result.extra.get("delete_message", False)
        or (rule_cfg or {}).get("delete_message", False)
        or guild_cfg.get(result.rule, {}).get("delete_message", False)
    )

    try:
        if action == "delete":
            can_delete, reason = can_perform_action(message.guild, message.author, "delete")
            if can_delete:
                await message.delete()
                action_taken = "delete"
            else:
                action_taken = f"blocked: {reason}"

        elif action == "warn":
            if delete_message:
                can_delete, _ = can_perform_action(message.guild, message.author, "delete")
                if can_delete:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
            count, threshold_action = await _record_automod_warn(
                message,
                guild_cfg,
                result.reason,
                result.rule,
            )
            action_taken = f"warn (count={count})"
            if threshold_action:
                action_taken += f", threshold={threshold_action}"

        elif action == "timeout":
            can_timeout, reason = can_perform_action(message.guild, message.author, "timeout")
            if can_timeout:
                default_seconds = int((rule_cfg or {}).get("timeout_seconds", 60))
                seconds = int(result.extra.get("seconds", default_seconds))
                if isinstance(message.author, discord.Member):
                    await notify_user_automod_action(
                        message.author,
                        "timeout",
                        result.reason,
                        message.guild.name,
                        rule=result.rule,
                        duration_seconds=seconds,
                    )
                await message.author.timeout(
                    discord.utils.utcnow() + timedelta(seconds=seconds),
                    reason=f"Automod: {result.reason}",
                )
                if delete_message:
                    can_delete, _ = can_perform_action(message.guild, message.author, "delete")
                    if can_delete:
                        try:
                            await message.delete()
                        except discord.HTTPException:
                            pass
                action_taken = f"timeout ({seconds}s)"
            else:
                action_taken = f"blocked: {reason}"

        elif action == "kick":
            can_kick, reason = can_perform_action(message.guild, message.author, "kick")
            if can_kick:
                if isinstance(message.author, discord.Member):
                    await notify_user_automod_action(
                        message.author,
                        "kick",
                        result.reason,
                        message.guild.name,
                        rule=result.rule,
                    )
                await message.author.kick(reason=f"Automod: {result.reason}")
                action_taken = "kick"
            else:
                action_taken = f"blocked: {reason}"

        elif action == "ban":
            can_ban, reason = can_perform_action(message.guild, message.author, "ban")
            if can_ban:
                if isinstance(message.author, discord.Member):
                    await notify_user_automod_action(
                        message.author,
                        "ban",
                        result.reason,
                        message.guild.name,
                        rule=result.rule,
                    )
                await message.author.ban(reason=f"Automod: {result.reason}", delete_message_days=0)
                action_taken = "ban"
            else:
                action_taken = f"blocked: {reason}"

        else:
            action_taken = f"unsupported action: {action}"
    except discord.Forbidden:
        action_taken = "blocked: discord.Forbidden"
    except discord.HTTPException as exc:
        action_taken = f"http_error: {type(exc).__name__}"

    # Optional setting: count any matched automod rule as a warning, not only `action=warn`.
    if action != "warn" and guild_cfg.get("count_rule_violations_as_warns", False):
        count, threshold_action = await _record_automod_warn(
            message,
            guild_cfg,
            f"{result.reason} (rule violation)",
            result.rule,
        )
        action_taken += f", warn_count={count}"
        if threshold_action:
            action_taken += f", threshold={threshold_action}"

    await log_message_action(message, guild_cfg, result, action_taken)
    _dispatch_custom_event(
        "coffeecord_automod_action",
        message.guild,
        message.author,
        result.rule,
        action_taken,
        result.reason,
        message.channel.id,
        message.id,
    )


async def process_automod(message: discord.Message) -> bool:
    """Process a message through automod. Returns True if a rule matched."""
    if not message.guild or message.author.bot:
        return False

    guild_cfg = get_guild_config(message.guild.id)
    if not guild_cfg.get("enabled", False):
        return False
    if is_whitelisted(message, guild_cfg):
        return False
    if has_protected_role(message.author, guild_cfg):
        return False

    checks = [
        ("bad_words", check_bad_words),
        ("spam", check_spam),
        ("duplicate_messages", check_duplicate_messages),
        ("links", check_links),
        ("mentions", check_mentions),
        ("caps", check_caps),
        ("attachments", check_attachments),
        ("custom_regex", check_custom_regex),
        ("anti_selfbot", check_anti_selfbot),
        ("new_user", check_new_user),
    ]

    for key, check_fn in checks:
        rule_cfg = get_rule_config(guild_cfg, key, message.channel.id)
        if not rule_cfg.get("enabled", False):
            continue
        result = check_fn(message, rule_cfg)
        if result:
            escalated = apply_rule_escalation(message, rule_cfg, result)
            await apply_action(message, escalated, guild_cfg, rule_cfg)
            return True
    return False


async def process_member_join(member: discord.Member):
    """Handle anti-raid logic for member joins."""
    guild_cfg = get_guild_config(member.guild.id)
    raid_cfg = guild_cfg.get("anti_raid", {})
    if not raid_cfg.get("enabled", False):
        return

    now = time.time()
    guild_key = str(member.guild.id)
    window_seconds = int(raid_cfg.get("window_seconds", 10))
    threshold = int(raid_cfg.get("join_threshold", 10))
    cooldown_seconds = int(raid_cfg.get("cooldown_seconds", 60))

    timestamps = join_cache[guild_key]
    timestamps.append(now)
    timestamps[:] = [t for t in timestamps if now - t <= window_seconds]

    if len(timestamps) >= threshold:
        raid_mode_until[guild_key] = max(raid_mode_until[guild_key], now + cooldown_seconds)

    raid_until = raid_mode_until.get(guild_key)
    # defaultdict(float) returns 0.0 for missing keys on [] access.
    # Use .get() so first-time guilds don't auto-create a 0.0 value and
    # accidentally short-circuit anti-raid handling forever.
    if raid_until is None or raid_until <= 0 or now > raid_until:
        return

    action = str(raid_cfg.get("action", "timeout")).lower()
    reason = f"Anti-raid mode active ({len(timestamps)} joins/{window_seconds}s)"
    action_taken = "none"

    try:
        if action == "timeout":
            can_timeout, reason_blocked = can_perform_action(member.guild, member, "timeout")
            if can_timeout:
                seconds = int(raid_cfg.get("timeout_seconds", 300))
                await notify_user_automod_action(
                    member,
                    "timeout",
                    reason,
                    member.guild.name,
                    rule="anti_raid",
                    duration_seconds=seconds,
                )
                await member.timeout(
                    discord.utils.utcnow() + timedelta(seconds=seconds),
                    reason="Automod anti-raid",
                )
                action_taken = f"timeout ({seconds}s)"
            else:
                action_taken = f"blocked: {reason_blocked}"
        elif action == "kick":
            can_kick, reason_blocked = can_perform_action(member.guild, member, "kick")
            if can_kick:
                await notify_user_automod_action(
                    member,
                    "kick",
                    reason,
                    member.guild.name,
                    rule="anti_raid",
                )
                await member.kick(reason="Automod anti-raid")
                action_taken = "kick"
            else:
                action_taken = f"blocked: {reason_blocked}"
        elif action == "ban":
            can_ban, reason_blocked = can_perform_action(member.guild, member, "ban")
            if can_ban:
                await notify_user_automod_action(
                    member,
                    "ban",
                    reason,
                    member.guild.name,
                    rule="anti_raid",
                )
                await member.ban(reason="Automod anti-raid", delete_message_days=0)
                action_taken = "ban"
            else:
                action_taken = f"blocked: {reason_blocked}"
        else:
            action_taken = "log_only"
    except (discord.Forbidden, discord.HTTPException) as exc:
        action_taken = f"error: {type(exc).__name__}"

    await log_member_action(member.guild, member, guild_cfg, reason, action_taken)


def _is_moderation_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.guild_permissions.manage_guild


async def _require_admin(interaction: discord.Interaction) -> bool:
    if not _is_moderation_admin(interaction):
        await interaction.response.send_message(
            "You need `Manage Server` to use automod admin commands.",
            ephemeral=True,
        )
        return False
    return True


def _rules_status_lines(guild_cfg: dict):
    lines = []
    for rule_name in RULE_NAMES + ["anti_raid"]:
        enabled = guild_cfg.get(rule_name, {}).get("enabled", False)
        lines.append(f"{rule_name}: {'ON' if enabled else 'OFF'}")
    return lines


def _save_config():
    save_json(CONFIG_PATH, config)


automod_group = app_commands.Group(name="automod", description="Automod management commands")
automod_set_group = app_commands.Group(name="set", description="Set automod values", parent=automod_group)
automod_toggle_group = app_commands.Group(name="toggle", description="Toggle automod settings", parent=automod_group)
automod_whitelist_group = app_commands.Group(name="whitelist", description="Manage automod whitelist", parent=automod_group)
automod_exempt_group = app_commands.Group(name="exempt", description="Exempt roles from automod enforcement", parent=automod_group)
automod_badword_group = app_commands.Group(name="badword", description="Manage blocked words", parent=automod_group)
automod_channel_group = app_commands.Group(name="channel", description="Manage channel overrides", parent=automod_group)
automod_warn_group = app_commands.Group(name="warn", description="Manage automod warns", parent=automod_group)
automod_escalation_group = app_commands.Group(name="escalation", description="Warn escalation settings", parent=automod_group)


@automod_group.command(name="overview", description="View automod status and enabled rules")
async def automod_overview(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("Guild-only command.", ephemeral=True)
        return
    guild_cfg = get_guild_config(interaction.guild_id)
    enabled = "ON" if guild_cfg.get("enabled") else "OFF"
    log_channel = guild_cfg.get("log_channel_id")

    embed = discord.Embed(title="Automod Overview")
    embed.add_field(name="Status", value=enabled, inline=False)
    embed.add_field(
        name="Log Channel",
        value=(f"<#{log_channel}>" if log_channel else "Not set"),
        inline=False,
    )
    embed.add_field(
        name="Violation Warn Counting",
        value=("ON" if guild_cfg.get("count_rule_violations_as_warns", False) else "OFF"),
        inline=False,
    )
    embed.add_field(name="Rules", value="\n".join(_rules_status_lines(guild_cfg)), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@automod_group.command(name="on", description="Enable automod for this guild")
async def automod_on(interaction: discord.Interaction):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    update_guild_override(interaction.guild_id)["enabled"] = True
    _save_config()
    await interaction.response.send_message("Automod enabled for this guild.", ephemeral=True)


@automod_group.command(name="off", description="Disable automod for this guild")
async def automod_off(interaction: discord.Interaction):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    update_guild_override(interaction.guild_id)["enabled"] = False
    _save_config()
    await interaction.response.send_message("Automod disabled for this guild.", ephemeral=True)


@automod_group.command(name="status", description="Show automod status")
async def automod_status(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("Guild-only command.", ephemeral=True)
        return
    guild_cfg = get_guild_config(interaction.guild_id)
    status = "enabled" if guild_cfg.get("enabled") else "disabled"
    await interaction.response.send_message(f"Automod is {status}.", ephemeral=True)


@automod_group.command(name="reload", description="Reload automod config from disk")
async def automod_reload(interaction: discord.Interaction):
    if not await _require_admin(interaction):
        return
    global config, warns, strikes
    config = normalize_config(load_json(CONFIG_PATH, DEFAULT_CONFIG))
    warns = load_json(WARNS_PATH, {})
    strikes = load_json(STRIKES_PATH, {})
    _save_config()
    await interaction.response.send_message("Automod config reloaded.", ephemeral=True)


@automod_set_group.command(name="log", description="Set automod log channel (or clear)")
@app_commands.describe(channel="Text channel for automod logs")
async def automod_set_log(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    override["log_channel_id"] = channel.id if channel else None
    _save_config()
    if channel:
        await interaction.response.send_message(
            f"Automod log channel set to {channel.mention}.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message("Automod log channel cleared.", ephemeral=True)


EXEMPT_ACTION_CHOICES = [
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove"),
]


@automod_exempt_group.command(name="role", description="Add/remove role exemption from automod")
@app_commands.choices(action=EXEMPT_ACTION_CHOICES)
async def automod_exempt_role(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    role: discord.Role,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    protected_roles = override.setdefault("protected_roles", [])
    if action.value == "add":
        if role.id not in protected_roles:
            protected_roles.append(role.id)
    else:
        protected_roles[:] = [rid for rid in protected_roles if rid != role.id]
    _save_config()
    await interaction.response.send_message(
        f"Exempt roles updated ({action.value}: {role.mention}).",
        ephemeral=True,
    )


@automod_exempt_group.command(name="list", description="List roles exempt from automod")
async def automod_exempt_list(interaction: discord.Interaction):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    guild_cfg = get_guild_config(interaction.guild_id)
    protected_roles = guild_cfg.get("protected_roles", [])
    if not protected_roles:
        await interaction.response.send_message("No exempt roles configured.", ephemeral=True)
        return
    mentions = " ".join(f"<@&{role_id}>" for role_id in protected_roles)
    await interaction.response.send_message(f"Exempt roles: {mentions}", ephemeral=True)


RULE_CHOICES = [
    app_commands.Choice(name="Bad Words", value="bad_words"),
    app_commands.Choice(name="Spam", value="spam"),
    app_commands.Choice(name="Duplicate Messages", value="duplicate_messages"),
    app_commands.Choice(name="Links", value="links"),
    app_commands.Choice(name="Mentions", value="mentions"),
    app_commands.Choice(name="Caps", value="caps"),
    app_commands.Choice(name="Attachments", value="attachments"),
    app_commands.Choice(name="Custom Regex", value="custom_regex"),
    app_commands.Choice(name="Anti Selfbot", value="anti_selfbot"),
    app_commands.Choice(name="New User", value="new_user"),
    app_commands.Choice(name="Anti Raid", value="anti_raid"),
]

ACTION_CHOICES = [app_commands.Choice(name=v.title(), value=v) for v in ACTION_NAMES]

PRESET_LIGHT = "light"
PRESET_MEDIUM = "medium"
PRESET_STRICT = "strict"
PRESET_DICTATORSHIP = "dictatorship"
PRESET_LABELS = {
    PRESET_LIGHT: "Light",
    PRESET_MEDIUM: "Medium",
    PRESET_STRICT: "Strict",
    PRESET_DICTATORSHIP: "Dictatorship",
}
AUTOMOD_PRESET_APPLIED_FOOTER = (
    "Starter blocked words + link filters are active; adjust with `/automod` subcommands."
)
AUTOMOD_PRESET_CHOICES = [
    app_commands.Choice(name=PRESET_LABELS[PRESET_LIGHT], value=PRESET_LIGHT),
    app_commands.Choice(name=PRESET_LABELS[PRESET_MEDIUM], value=PRESET_MEDIUM),
    app_commands.Choice(name=PRESET_LABELS[PRESET_STRICT], value=PRESET_STRICT),
    app_commands.Choice(name=PRESET_LABELS[PRESET_DICTATORSHIP], value=PRESET_DICTATORSHIP),
]
_PRESET_BAD_WORDS = {
    "enabled": True,
    "words": list(AUTOMOD_PRESET_STARTER_BAD_WORDS),
    "action": "warn",
    "delete_message": True,
}

AUTOMOD_PRESETS: dict[str, dict] = {
    PRESET_LIGHT: {
        "enabled": True,
        "count_rule_violations_as_warns": False,
        "bad_words": {**_PRESET_BAD_WORDS, "action": "warn"},
        "spam": {"enabled": True, "max_messages": 8, "per_seconds": 6, "action": "warn", "timeout_seconds": 60},
        "duplicate_messages": {"enabled": False},
        "links": {
            "enabled": True,
            "block_invites": True,
            "block_links": True,
            "action": "warn",
            "delete_message": True,
        },
        "mentions": {"enabled": True, "max_mentions": 8, "action": "warn"},
        "caps": {"enabled": False, "caps_percent": 85},
        "attachments": {"enabled": False},
        "custom_regex": {"enabled": False},
        "anti_selfbot": {"enabled": False},
        "new_user": {"enabled": False},
        "anti_raid": {"enabled": False},
    },
    PRESET_MEDIUM: {
        "enabled": True,
        "count_rule_violations_as_warns": False,
        "bad_words": {**_PRESET_BAD_WORDS, "action": "warn"},
        "spam": {"enabled": True, "max_messages": 6, "per_seconds": 5, "action": "timeout", "timeout_seconds": 120},
        "duplicate_messages": {"enabled": True, "window_seconds": 30, "min_duplicates": 3, "action": "delete"},
        "links": {"enabled": True, "block_invites": True, "block_links": True, "action": "delete"},
        "mentions": {"enabled": True, "max_mentions": 5, "action": "warn"},
        "caps": {"enabled": True, "min_length": 10, "caps_percent": 75, "action": "delete"},
        "attachments": {"enabled": False},
        "custom_regex": {"enabled": False},
        "anti_selfbot": {"enabled": True, "action": "delete"},
        "new_user": {"enabled": True, "max_account_age_days": 3, "action": "warn"},
        "anti_raid": {"enabled": True, "window_seconds": 10, "join_threshold": 12, "cooldown_seconds": 60, "action": "timeout", "timeout_seconds": 300},
    },
    PRESET_STRICT: {
        "enabled": True,
        "count_rule_violations_as_warns": True,
        "bad_words": {
            **_PRESET_BAD_WORDS,
            "action": "timeout",
            "timeout_seconds": 300,
            "escalation": [{"after": 2, "action": "kick"}],
        },
        "spam": {"enabled": True, "max_messages": 5, "per_seconds": 4, "action": "timeout", "timeout_seconds": 300},
        "duplicate_messages": {
            "enabled": True,
            "window_seconds": 25,
            "min_duplicates": 3,
            "action": "timeout",
            "timeout_seconds": 300,
        },
        "links": {"enabled": True, "block_invites": True, "block_links": True, "action": "delete"},
        "mentions": {"enabled": True, "max_mentions": 4, "action": "timeout", "timeout_seconds": 180},
        "caps": {"enabled": True, "min_length": 8, "caps_percent": 70, "action": "delete"},
        "attachments": {"enabled": True, "max_attachments": 4, "max_embeds": 2, "action": "delete"},
        "custom_regex": {"enabled": True, "rules": _automod_preset_custom_regex_rules("delete")},
        "anti_selfbot": {"enabled": True, "action": "timeout", "timeout_seconds": 600},
        "new_user": {"enabled": True, "max_account_age_days": 7, "action": "timeout", "timeout_seconds": 300},
        "anti_raid": {"enabled": True, "window_seconds": 8, "join_threshold": 8, "cooldown_seconds": 120, "action": "timeout", "timeout_seconds": 900},
    },
    PRESET_DICTATORSHIP: {
        "enabled": True,
        "count_rule_violations_as_warns": True,
        "bad_words": {
            **_PRESET_BAD_WORDS,
            "action": "kick",
            "escalation": [{"after": 2, "action": "ban"}],
        },
        "spam": {"enabled": True, "max_messages": 4, "per_seconds": 4, "action": "kick"},
        "duplicate_messages": {"enabled": True, "window_seconds": 20, "min_duplicates": 2, "action": "kick"},
        "links": {"enabled": True, "block_invites": True, "block_links": True, "action": "kick"},
        "mentions": {"enabled": True, "max_mentions": 3, "action": "timeout", "timeout_seconds": 600},
        "caps": {"enabled": True, "min_length": 6, "caps_percent": 65, "action": "timeout", "timeout_seconds": 300},
        "attachments": {"enabled": True, "max_attachments": 2, "max_embeds": 1, "action": "delete"},
        "custom_regex": {"enabled": True, "rules": _automod_preset_custom_regex_rules("kick")},
        "anti_selfbot": {"enabled": True, "action": "ban"},
        "new_user": {"enabled": True, "max_account_age_days": 30, "action": "timeout", "timeout_seconds": 1800},
        "anti_raid": {"enabled": True, "window_seconds": 6, "join_threshold": 5, "cooldown_seconds": 300, "action": "ban"},
    },
}


def _apply_automod_preset(guild_id: int, preset_key: str) -> None:
    preset = AUTOMOD_PRESETS.get(preset_key)
    if preset is None:
        raise ValueError(f"Unknown preset: {preset_key}")
    override = update_guild_override(guild_id)
    for key, value in preset.items():
        override[key] = copy.deepcopy(value)
    _save_config()


class AutomodPresetView(discord.ui.View):
    def __init__(self, guild_id: int, invoker_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the command invoker can use these preset buttons.", ephemeral=True)
            return False
        return True

    async def _apply_and_confirm(self, interaction: discord.Interaction, preset_key: str) -> None:
        _apply_automod_preset(self.guild_id, preset_key)
        await interaction.response.edit_message(
            content=(
                f"✅ Applied automod preset: **{PRESET_LABELS[preset_key]}**\n"
                f"{AUTOMOD_PRESET_APPLIED_FOOTER}"
            ),
            view=self,
        )

    @discord.ui.button(label="Light", style=discord.ButtonStyle.secondary)
    async def preset_light(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._apply_and_confirm(interaction, PRESET_LIGHT)

    @discord.ui.button(label="Medium", style=discord.ButtonStyle.primary)
    async def preset_medium(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._apply_and_confirm(interaction, PRESET_MEDIUM)

    @discord.ui.button(label="Strict", style=discord.ButtonStyle.danger)
    async def preset_strict(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._apply_and_confirm(interaction, PRESET_STRICT)

    @discord.ui.button(label="Dictatorship", style=discord.ButtonStyle.danger)
    async def preset_dictatorship(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._apply_and_confirm(interaction, PRESET_DICTATORSHIP)


@automod_group.command(name="preset", description="Apply an automod preset directly or via buttons")
@app_commands.choices(preset=AUTOMOD_PRESET_CHOICES)
async def automod_preset(
    interaction: discord.Interaction,
    preset: app_commands.Choice[str] | None = None,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    if preset is not None:
        _apply_automod_preset(interaction.guild_id, preset.value)
        await interaction.response.send_message(
            f"✅ Applied automod preset: **{PRESET_LABELS[preset.value]}**\n{AUTOMOD_PRESET_APPLIED_FOOTER}",
            ephemeral=True,
        )
        return

    view = AutomodPresetView(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(
        "Choose an automod preset:",
        view=view,
        ephemeral=True,
    )

RULE_SETTING_SPECS = {
    "spam": {
        "max_messages": int,
        "per_seconds": int,
        "timeout_seconds": int,
    },
    "duplicate_messages": {
        "window_seconds": int,
        "min_duplicates": int,
    },
    "mentions": {"max_mentions": int},
    "caps": {
        "min_length": int,
        "caps_percent": float,
    },
    "attachments": {
        "max_attachments": int,
        "max_embeds": int,
    },
    "new_user": {"max_account_age_days": int},
    "anti_raid": {
        "window_seconds": int,
        "join_threshold": int,
        "cooldown_seconds": int,
        "timeout_seconds": int,
    },
}


def _parse_setting_value(caster, value: str):
    raw = value.strip()
    if caster is int:
        parsed = int(raw)
        return parsed
    if caster is float:
        parsed = float(raw)
        return parsed
    return raw


@automod_toggle_group.command(name="rule", description="Enable/disable a specific automod rule")
@app_commands.choices(rule=RULE_CHOICES)
@app_commands.describe(rule="Rule to toggle", enabled="Whether this rule should be enabled")
async def automod_toggle_rule(
    interaction: discord.Interaction,
    rule: app_commands.Choice[str],
    enabled: bool,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    override.setdefault(rule.value, {})
    override[rule.value]["enabled"] = enabled
    _save_config()
    await interaction.response.send_message(
        f"Rule `{rule.value}` set to {'ON' if enabled else 'OFF'}.",
        ephemeral=True,
    )


@automod_set_group.command(name="action", description="Set the action for an automod rule")
@app_commands.choices(rule=RULE_CHOICES, action=ACTION_CHOICES)
async def automod_set_action(
    interaction: discord.Interaction,
    rule: app_commands.Choice[str],
    action: app_commands.Choice[str],
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    override.setdefault(rule.value, {})
    override[rule.value]["action"] = action.value
    _save_config()
    await interaction.response.send_message(
        f"Rule `{rule.value}` action set to `{action.value}`.",
        ephemeral=True,
    )


@automod_set_group.command(name="value", description="Set numeric config values for a rule")
@app_commands.choices(rule=RULE_CHOICES)
@app_commands.describe(
    rule="Rule to update",
    setting="Setting key to update (e.g. max_messages, caps_percent)",
    value="New value",
)
async def automod_set_value(
    interaction: discord.Interaction,
    rule: app_commands.Choice[str],
    setting: str,
    value: str,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    specs = RULE_SETTING_SPECS.get(rule.value, {})
    caster = specs.get(setting)
    if caster is None:
        allowed = ", ".join(specs.keys()) if specs else "no editable numeric settings"
        await interaction.response.send_message(
            f"Invalid setting for `{rule.value}`. Allowed: {allowed}.",
            ephemeral=True,
        )
        return

    try:
        parsed = _parse_setting_value(caster, value)
    except ValueError:
        await interaction.response.send_message(
            f"Invalid value `{value}` for `{setting}`.",
            ephemeral=True,
        )
        return

    override = update_guild_override(interaction.guild_id)
    override.setdefault(rule.value, {})
    override[rule.value][setting] = parsed
    _save_config()
    await interaction.response.send_message(
        f"Updated `{rule.value}.{setting}` to `{parsed}`.",
        ephemeral=True,
    )


@automod_set_group.command(name="deletemessage", description="Set delete_message behavior for a rule")
@app_commands.choices(rule=RULE_CHOICES)
async def automod_set_delete_message(
    interaction: discord.Interaction,
    rule: app_commands.Choice[str],
    enabled: bool,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    override.setdefault(rule.value, {})
    override[rule.value]["delete_message"] = enabled
    _save_config()
    await interaction.response.send_message(
        f"`{rule.value}.delete_message` set to `{enabled}`.",
        ephemeral=True,
    )


@automod_set_group.command(
    name="violationwarns",
    description="Count automod rule violations as warns (and trigger warn escalation).",
)
@app_commands.describe(enabled="If true, non-warn automod violations also increment warns")
async def automod_set_violation_warns(interaction: discord.Interaction, enabled: bool):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    override["count_rule_violations_as_warns"] = enabled
    _save_config()
    await interaction.response.send_message(
        f"`count_rule_violations_as_warns` set to `{enabled}`.",
        ephemeral=True,
    )


WHITELIST_ACTION_CHOICES = [
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove"),
]


@automod_whitelist_group.command(name="channel", description="Add/remove a channel from automod whitelist")
@app_commands.choices(action=WHITELIST_ACTION_CHOICES)
async def automod_whitelist_channel(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    channel: discord.TextChannel,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    whitelist = override.setdefault("whitelist", {})
    channels = whitelist.setdefault("channels", [])
    if action.value == "add":
        if channel.id not in channels:
            channels.append(channel.id)
    else:
        channels[:] = [cid for cid in channels if cid != channel.id]
    _save_config()
    await interaction.response.send_message(
        f"Whitelist channels updated ({action.value}: {channel.mention}).",
        ephemeral=True,
    )


@automod_whitelist_group.command(name="role", description="Add/remove a role from automod whitelist")
@app_commands.choices(action=WHITELIST_ACTION_CHOICES)
async def automod_whitelist_role(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    role: discord.Role,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    whitelist = override.setdefault("whitelist", {})
    roles = whitelist.setdefault("roles", [])
    if action.value == "add":
        if role.id not in roles:
            roles.append(role.id)
    else:
        roles[:] = [rid for rid in roles if rid != role.id]
    _save_config()
    await interaction.response.send_message(
        f"Whitelist roles updated ({action.value}: {role.mention}).",
        ephemeral=True,
    )


@automod_badword_group.command(name="add", description="Add a blocked word to bad_words")
async def automod_badword_add(interaction: discord.Interaction, word: str):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    normalized = word.strip().lower()
    if not normalized:
        await interaction.response.send_message("Word cannot be empty.", ephemeral=True)
        return

    override = update_guild_override(interaction.guild_id)
    bad_words = override.setdefault("bad_words", {})
    words = bad_words.setdefault("words", [])
    if normalized in words:
        await interaction.response.send_message(
            f"`{normalized}` is already in bad words.",
            ephemeral=True,
        )
        return
    words.append(normalized)
    bad_words["enabled"] = True
    _save_config()
    await interaction.response.send_message(
        f"Added `{normalized}` to bad words list.",
        ephemeral=True,
    )


@automod_badword_group.command(name="remove", description="Remove a blocked word from bad_words")
async def automod_badword_remove(interaction: discord.Interaction, word: str):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    normalized = word.strip().lower()
    override = update_guild_override(interaction.guild_id)
    bad_words = override.setdefault("bad_words", {})
    words = bad_words.setdefault("words", [])
    new_words = [w for w in words if w != normalized]
    if len(new_words) == len(words):
        await interaction.response.send_message(
            f"`{normalized}` was not in bad words list.",
            ephemeral=True,
        )
        return
    bad_words["words"] = new_words
    _save_config()
    await interaction.response.send_message(
        f"Removed `{normalized}` from bad words list.",
        ephemeral=True,
    )


@automod_badword_group.command(name="list", description="List blocked words")
async def automod_badword_list(interaction: discord.Interaction):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    guild_cfg = get_guild_config(interaction.guild_id)
    words = guild_cfg.get("bad_words", {}).get("words", [])
    if not words:
        await interaction.response.send_message("No blocked words configured.", ephemeral=True)
        return
    preview = ", ".join(words[:100])
    await interaction.response.send_message(
        f"Blocked words ({len(words)}): {preview}",
        ephemeral=True,
    )


@automod_channel_group.command(name="overrideset", description="Set a channel-specific rule value")
@app_commands.choices(rule=RULE_CHOICES)
@app_commands.describe(
    channel="Channel to override",
    rule="Rule to override",
    setting="Setting key (enabled/action/delete_message/max_messages/etc)",
    value="Value (true/false, action name, number, or text)",
)
async def automod_channel_override_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    rule: app_commands.Choice[str],
    setting: str,
    value: str,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return

    bool_keys = {"enabled", "delete_message", "block_invites", "use_builtin_patterns"}
    action_keys = {"action"}
    numeric_specs = RULE_SETTING_SPECS.get(rule.value, {})

    parsed: object
    raw = value.strip()
    if setting in bool_keys:
        if raw.lower() not in {"true", "false"}:
            await interaction.response.send_message(
                f"`{setting}` expects `true` or `false`.",
                ephemeral=True,
            )
            return
        parsed = raw.lower() == "true"
    elif setting in action_keys:
        if raw.lower() not in ACTION_NAMES:
            await interaction.response.send_message(
                f"`action` must be one of: {', '.join(ACTION_NAMES)}.",
                ephemeral=True,
            )
            return
        parsed = raw.lower()
    elif setting in numeric_specs:
        try:
            parsed = _parse_setting_value(numeric_specs[setting], raw)
        except ValueError:
            await interaction.response.send_message(
                f"`{setting}` expects a numeric value.",
                ephemeral=True,
            )
            return
    else:
        await interaction.response.send_message(
            f"Unsupported setting `{setting}` for `{rule.value}`.",
            ephemeral=True,
        )
        return

    channel_override = get_channel_override(interaction.guild_id, channel.id)
    channel_override.setdefault(rule.value, {})
    channel_override[rule.value][setting] = parsed
    _save_config()
    await interaction.response.send_message(
        f"Set override for {channel.mention}: `{rule.value}.{setting} = {parsed}`",
        ephemeral=True,
    )


@automod_channel_group.command(name="overrideclear", description="Clear channel-specific rule override")
@app_commands.choices(rule=RULE_CHOICES)
async def automod_channel_override_clear(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    rule: app_commands.Choice[str],
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    override = update_guild_override(interaction.guild_id)
    channel_overrides = override.setdefault("channel_overrides", {})
    channel_map = channel_overrides.get(str(channel.id), {})
    if not isinstance(channel_map, dict) or rule.value not in channel_map:
        await interaction.response.send_message(
            "No override exists for that channel/rule.",
            ephemeral=True,
        )
        return
    channel_map.pop(rule.value, None)
    if not channel_map:
        channel_overrides.pop(str(channel.id), None)
    _save_config()
    await interaction.response.send_message(
        f"Cleared override for {channel.mention} on `{rule.value}`.",
        ephemeral=True,
    )


@automod_warn_group.command(name="list", description="Show automod warns for a member")
async def automod_warns(interaction: discord.Interaction, member: discord.Member):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    guild_warns = warns.get(str(interaction.guild_id), {})
    user_warns = guild_warns.get(str(member.id), [])
    count = len(user_warns)
    latest = user_warns[-5:]
    if not latest:
        await interaction.response.send_message(
            f"{member.mention} has no automod warns.",
            ephemeral=True,
        )
        return
    lines = []
    for idx, entry in enumerate(latest, start=max(count - len(latest) + 1, 1)):
        timestamp = int(entry.get("timestamp", int(time.time())))
        issuer = str(entry.get("by", "Automod"))
        reason = str(entry.get("reason", "No reason"))
        lines.append(f"{idx}. <t:{timestamp}:f> by {issuer} - {reason}")
    await interaction.response.send_message(
        f"{member.mention} has **{count}** warn(s).\nRecent:\n" + "\n".join(lines),
        ephemeral=True,
    )


@automod_warn_group.command(name="add", description="Manually add a warn (uses automod warns storage)")
async def automod_warn_add(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    if member.bot:
        await interaction.response.send_message("Cannot warn a bot.", ephemeral=True)
        return
    warn_count = add_warn(
        interaction.guild_id,
        member.id,
        reason,
        by=f"Manual ({interaction.user.display_name})",
    )
    await notify_user_automod_action(
        member,
        "warn",
        f"{reason} (warning #{warn_count})",
        interaction.guild.name,
        rule="manual",
    )
    _dispatch_custom_event(
        "coffeecord_warn",
        interaction.guild,
        interaction.user,
        member,
        reason,
        "manual",
    )
    guild_cfg = get_guild_config(interaction.guild_id)
    threshold_action = await apply_warn_threshold_action(member, warn_count, guild_cfg)
    from Modules.themes import get_command_response_for_interaction

    response = get_command_response_for_interaction(
        interaction,
        "success",
        "Added warn for {member}. Total warns: **{count}**.",
        member=member.mention,
        count=str(warn_count),
    )
    if threshold_action:
        response += f"\nThreshold action: `{threshold_action}`"
    await interaction.response.send_message(response, ephemeral=True)


@automod_warn_group.command(name="remove", description="Remove one warn by index (or latest)")
async def automod_warn_remove(interaction: discord.Interaction, member: discord.Member, index: int | None = None):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    guild_id = str(interaction.guild_id)
    user_id = str(member.id)
    guild_warns = warns.setdefault(guild_id, {})
    user_warns = guild_warns.get(user_id, [])
    if not user_warns:
        await interaction.response.send_message(f"{member.mention} has no warns.", ephemeral=True)
        return

    remove_at = len(user_warns) - 1 if index is None else index - 1
    if remove_at < 0 or remove_at >= len(user_warns):
        await interaction.response.send_message(
            f"Invalid index. Use a value from 1 to {len(user_warns)}.",
            ephemeral=True,
        )
        return

    removed = user_warns.pop(remove_at)
    if user_warns:
        guild_warns[user_id] = user_warns
    else:
        guild_warns.pop(user_id, None)
    save_json(WARNS_PATH, warns)

    reason = str(removed.get("reason", "No reason"))
    await interaction.response.send_message(
        f"Removed warn #{remove_at + 1} for {member.mention}: {reason}",
        ephemeral=True,
    )


@automod_warn_group.command(name="clear", description="Clear all warns for a user")
async def automod_warn_clear(interaction: discord.Interaction, member: discord.Member):
    if not interaction.guild_id or not await _require_admin(interaction):
        return
    guild_id = str(interaction.guild_id)
    user_id = str(member.id)
    guild_warns = warns.setdefault(guild_id, {})
    removed = len(guild_warns.get(user_id, []))
    guild_warns.pop(user_id, None)
    save_json(WARNS_PATH, warns)
    await interaction.response.send_message(
        f"Cleared **{removed}** warn(s) for {member.mention}.",
        ephemeral=True,
    )


@automod_escalation_group.command(
    name="warn",
    description="Set warn escalation actions for warn 1 through warn 5",
)
@app_commands.describe(
    warn1="Warn #1 action (e.g. warn, timeout:300, mute:1h, kick, ban, none)",
    warn2="Warn #2 action (e.g. warn, timeout:300, mute:1h, kick, ban, none)",
    warn3="Warn #3 action (e.g. warn, timeout:300, mute:1h, kick, ban, none)",
    warn4="Warn #4 action (e.g. warn, timeout:300, mute:1h, kick, ban, none)",
    warn5="Warn #5 action (e.g. warn, timeout:300, mute:1h, kick, ban, none)",
)
async def automod_escalation_warn(
    interaction: discord.Interaction,
    warn1: str,
    warn2: str,
    warn3: str,
    warn4: str,
    warn5: str,
):
    if not interaction.guild_id or not await _require_admin(interaction):
        return

    raw_steps = [warn1, warn2, warn3, warn4, warn5]
    parsed_thresholds: dict[str, dict] = {}
    errors: list[str] = []

    for idx, raw in enumerate(raw_steps, start=1):
        parsed = _parse_warn_step(raw)
        if parsed is None:
            continue
        if "error" in parsed:
            errors.append(f"Warn {idx}: {parsed['error']}")
            continue
        parsed_thresholds[str(idx)] = parsed

    if errors:
        await interaction.response.send_message(
            "Invalid escalation configuration:\n- " + "\n- ".join(errors),
            ephemeral=True,
        )
        return

    override = update_guild_override(interaction.guild_id)
    override["warn_thresholds"] = parsed_thresholds
    _save_config()

    if not parsed_thresholds:
        await interaction.response.send_message(
            "Warn escalation cleared for warn 1-5 (all set to none/off).",
            ephemeral=True,
        )
        return

    lines = []
    for idx in range(1, 6):
        cfg = parsed_thresholds.get(str(idx))
        if not cfg:
            lines.append(f"{idx}: off")
            continue
        action = str(cfg.get("action", "off"))
        if action in {"timeout", "mute"}:
            seconds = int(cfg.get("seconds", 60))
            lines.append(f"{idx}: {action} ({seconds}s)")
        else:
            lines.append(f"{idx}: {action}")

    await interaction.response.send_message(
        "Warn escalation updated:\n" + "\n".join(lines),
        ephemeral=True,
    )


async def setup(bot_instance):
    """Called by discord.py's load_extension — registers the automod command group."""
    # Guard: only register once (handles both direct import and extension loading)
    existing = tree.get_command("automod")
    if existing is None:
        tree.add_command(automod_group)
