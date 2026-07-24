"""Per-guild slash command permission overrides (tree interaction_check)."""

from __future__ import annotations

import json
import os
from typing import Any

import discord
from discord import app_commands
from discord.app_commands.errors import AppCommandError

from Modules import json_cache
from Modules.i18n import t_sync

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STORAGE_DIR = os.path.join(_BASE_DIR, "Storage")
COMMAND_PERM_OVERRIDES_FILE = os.path.join(_STORAGE_DIR, "Config", "command_perm_overrides.json")

# OR-of-AND groups: user must satisfy one inner list (each inner list is AND of permission names).
ROLES_OR_MODERATE_RULE: dict[str, Any] = {
    "any": [
        ["manage_roles"],
        ["moderate_members"],
        ["manage_guild"],
        ["administrator"],
    ]
}


def _all(*perms: str) -> dict[str, Any]:
    return {"all": list(perms)}


def _everyone() -> dict[str, Any]:
    return {"everyone": True}


# Qualified names must match discord.py's Command.qualified_name (space-separated).
DEFAULT_SLASH_COMMAND_RULES: dict[str, dict[str, Any]] = {
    # --- Bot.py (Src/Bot.py) ---
    "kofi add": _all("administrator"),
    "kofi remove": _all("administrator"),
    "purge": _all("manage_messages"),
    "specific_purge": _all("manage_messages"),
    "ban": _all("ban_members"),
    "unban": _all("ban_members"),
    "giverole": ROLES_OR_MODERATE_RULE,
    "removerole": ROLES_OR_MODERATE_RULE,
    "mute": ROLES_OR_MODERATE_RULE,
    "unmute": ROLES_OR_MODERATE_RULE,
    "muterole create": ROLES_OR_MODERATE_RULE,
    "muterole update": ROLES_OR_MODERATE_RULE,
    "hardmute": ROLES_OR_MODERATE_RULE,
    "autorole_legacy": ROLES_OR_MODERATE_RULE,
    "setautorole_legacy": ROLES_OR_MODERATE_RULE,
    "say": _all("manage_guild"),
    "dm": _all("moderate_members"),
    "verifyconfig": _all("manage_guild"),
    "nickname": _all("manage_nicknames"),
    "adaptive_slowmode": _all("manage_channels"),
    "debugcommands": _all("manage_guild"),
    "call create": _everyone(),
    "uninstall": _all("administrator"),
    "ticket_export": _all("manage_channels"),
    "ticket_import": _all("manage_channels"),
    # --- Modules/quests.py ---
    "quests list": _everyone(),
    "quests checkin": _everyone(),
    "quests admin create": _all("manage_guild"),
    "quests admin list": _all("manage_guild"),
    "quests admin delete": _all("manage_guild"),
    "quests admin toggle": _all("manage_guild"),
    "quests admin reset": _all("manage_guild"),
    # --- Modules/leveling.py ---
    "xpset": _all("manage_guild"),
    "xp config": _all("manage_guild"),
    "levelreward add": ROLES_OR_MODERATE_RULE,
    "levelreward remove": ROLES_OR_MODERATE_RULE,
    "levelreward mode": ROLES_OR_MODERATE_RULE,
    # --- Modules/translate.py ---
    "translate reset": _all("administrator"),
    # --- Modules/tickets.py ---
    "ticket setup": _all("manage_guild"),
    # --- Modules/modules_cmd.py ---
    "modules status": _all("manage_guild"),
    "modules toggle": _all("manage_guild"),
    "modules enable": _all("manage_guild"),
    "modules disable": _all("manage_guild"),
    "modules info": _all("manage_guild"),
    # --- Modules/language.py ---
    "language status": _everyone(),
    "language set": _everyone(),
    # --- Modules/setup_wizard.py (top-level app commands on cog) ---
    "setup": _all("manage_guild"),
    "setup_resume": _all("manage_guild"),
    "setup_cancel": _all("manage_guild"),
    # --- Modules/welcome_leave.py ---
    "welcome config": _all("manage_guild"),
    "welcome test": _all("manage_guild"),
    "leave config": _all("manage_guild"),
    "leave test": _all("manage_guild"),
    # --- Modules/sticky_msg.py ---
    "sticky_msg create": _all("manage_guild"),
    "sticky_msg remove": _all("manage_guild"),
    "sticky_msg list": _all("manage_guild"),
    # --- Modules/server_backup.py ---
    "backup create": _all("manage_guild"),
    "backup list": _all("manage_guild"),
    "backup download": _all("manage_guild"),
    "backup delete": _all("manage_guild"),
    "backup restore": _all("manage_guild"),
    # --- Modules/reactionrole.py ---
    "reactionrole create": _all("manage_guild"),
    "reactionrole list": _all("manage_guild"),
    "reactionrole delete": _all("manage_guild"),
    "reactionrole config": _all("manage_guild"),
    "reactionrole edit": _all("manage_guild"),
    # --- Modules/color_roles.py ---
    "colorrole info": _all("manage_guild"),
    "colorrole generate": _all("manage_guild"),
    "colorrole setup": _all("manage_guild"),
    "colorrole publish": _all("manage_guild"),
    "colorrole list": _all("manage_guild"),
    "colorrole delete": _all("manage_guild"),
    "colorrole edit": _all("manage_guild"),
    "colorrole sync": _all("manage_guild"),
    "colorrole clear": _all("manage_guild"),
    # --- Modules/logging.py ---
    "logging status": _all("manage_guild"),
    "logging setup": _all("manage_guild"),
    "logging toggle": _all("manage_guild"),
    "logging module": _all("manage_guild"),
    "logging disable": _all("manage_guild"),
    # --- Modules/autorole.py ---
    "autorole status": _all("manage_guild"),
    "autorole toggle": _all("manage_guild"),
    "autorole add": _all("manage_guild"),
    "autorole remove": _all("manage_guild"),
    "autorole test": _all("manage_guild"),
    # --- Modules/themes.py ---
    "theme list": _all("manage_guild"),
    "theme set": _all("manage_guild"),
    "theme preview": _all("manage_guild"),
    "theme info": _all("manage_guild"),
    "theme upload": _all("manage_guild"),
    "theme delete": _all("manage_guild"),
    "theme responses presets": _all("manage_guild"),
    "theme responses list": _all("manage_guild"),
    "theme responses upload": _all("manage_guild"),
    "theme responses discover": _all("manage_guild"),
    "theme responses keys": _all("manage_guild"),
    "theme responses clear": _all("manage_guild"),
}

_EDIT_COMMAND_QN = "command_perms edit"
_LIST_COMMAND_QN = "command_perms list"

# Never configurable via overrides (avoid lockout). Hard requirement in tree check.
_COMMAND_PERM_ADMIN_QN = {_EDIT_COMMAND_QN, _LIST_COMMAND_QN}

VALID_PERMISSION_NAMES: frozenset[str] = frozenset(
    k for k in discord.Permissions.VALID_FLAGS.keys() if k != "value"
)


def _load_raw_overrides() -> dict[str, dict[str, Any]]:
    data = json_cache.get(COMMAND_PERM_OVERRIDES_FILE, {})
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for gid, guild_map in data.items():
        if not isinstance(guild_map, dict):
            continue
        out[str(gid)] = {str(k): v for k, v in guild_map.items() if isinstance(v, dict)}
    return out


def _save_raw_overrides(data: dict[str, dict[str, Any]]) -> None:
    json_cache.set_(COMMAND_PERM_OVERRIDES_FILE, data)


def get_guild_overrides(guild_id: int) -> dict[str, dict[str, Any]]:
    return dict(_load_raw_overrides().get(str(guild_id), {}))


def set_guild_command_rule(guild_id: int, qualified_name: str, rule: dict[str, Any] | None) -> None:
    """Persist override. rule None removes override for that command."""
    raw = _load_raw_overrides()
    gid = str(guild_id)
    guild_map = dict(raw.get(gid, {}))
    qn = qualified_name.strip()
    if rule is None:
        guild_map.pop(qn, None)
    else:
        guild_map[qn] = _normalize_rule(rule)
    if guild_map:
        raw[gid] = guild_map
    else:
        raw.pop(gid, None)
    _save_raw_overrides(raw)


def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Validate structure; raises ValueError on bad data."""
    if rule.get("everyone") is True:
        return {"everyone": True}
    if "all" in rule:
        perms = rule["all"]
        if not isinstance(perms, list) or not perms:
            raise ValueError("`all` must be a non-empty list of permission names.")
        names = [str(p).strip() for p in perms]
        bad = [p for p in names if p not in VALID_PERMISSION_NAMES]
        if bad:
            raise ValueError(f"Unknown permission(s): {', '.join(bad)}")
        return {"all": names}
    if "any" in rule:
        groups = rule["any"]
        if not isinstance(groups, list) or not groups:
            raise ValueError("`any` must be a non-empty list of groups.")
        out_groups: list[list[str]] = []
        for g in groups:
            if not isinstance(g, list) or not g:
                raise ValueError("Each `any` group must be a non-empty list.")
            names = [str(p).strip() for p in g]
            bad = [p for p in names if p not in VALID_PERMISSION_NAMES]
            if bad:
                raise ValueError(f"Unknown permission(s): {', '.join(bad)}")
            out_groups.append(names)
        return {"any": out_groups}
    raise ValueError("Rule must include `everyone`, `all`, or `any`.")


def parse_custom_permissions_csv(text: str) -> dict[str, Any]:
    """Comma-separated permission names (AND)."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        raise ValueError("Provide at least one permission name.")
    bad = [p for p in parts if p not in VALID_PERMISSION_NAMES]
    if bad:
        raise ValueError(f"Unknown permission(s): {', '.join(bad)}")
    return {"all": parts}


def preset_rule(preset: str) -> dict[str, Any] | None:
    """Map UI preset to stored rule. None = remove override (use default)."""
    key = preset.strip().lower()
    if key in ("reset", "default", "bot_default"):
        return None
    if key in ("everyone", "open", "none"):
        return {"everyone": True}
    if key == "roles_or_moderate":
        return dict(ROLES_OR_MODERATE_RULE)
    if key in VALID_PERMISSION_NAMES:
        return {"all": [key]}
    raise ValueError(f"Unknown preset: {preset}")


def effective_rule(guild_id: int | None, qualified_name: str) -> dict[str, Any] | None:
    """Resolved rule: guild override wins, else built-in default, else no extra gate (everyone)."""
    qn = qualified_name.strip()
    if guild_id is not None:
        raw_ov = _load_raw_overrides().get(str(guild_id), {}).get(qn)
        if raw_ov is not None:
            return _normalize_rule(raw_ov)
    if qn in DEFAULT_SLASH_COMMAND_RULES:
        return dict(DEFAULT_SLASH_COMMAND_RULES[qn])
    return None


def collect_slash_qualified_names(tree: app_commands.CommandTree) -> frozenset[str]:
    """All chat-input slash command qualified names currently on the tree."""
    found: set[str] = set()

    def walk(cmd: app_commands.Command | app_commands.Group, prefix: str = "") -> None:
        if isinstance(cmd, app_commands.Group):
            base = f"{prefix}{cmd.name} " if prefix else f"{cmd.name} "
            for sub in cmd.commands:
                walk(sub, base)
        else:
            found.add(f"{prefix}{cmd.name}".strip())

    for top in tree.get_commands():
        walk(top)
    return frozenset(found)


def resolve_slash_command_input(tree: app_commands.CommandTree, text: str) -> str | None:
    """
    Map free text to a registered slash qualified name.
    Accepts optional leading '/', spaces or underscores between segments, case-insensitive.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        raw = raw[1:].strip()
    names = collect_slash_qualified_names(tree)
    variants = {
        raw,
        " ".join(raw.split()),
        raw.replace("_", " "),
        " ".join(raw.replace("_", " ").split()),
    }
    for v in variants:
        if v in names:
            return v
    lower_names = {n.lower(): n for n in names}
    for v in variants:
        key = v.lower()
        if key in lower_names:
            return lower_names[key]
    return None


def _member_for_eval(interaction: discord.Interaction) -> discord.Member | None:
    if interaction.guild is None:
        return None
    u = interaction.user
    if isinstance(u, discord.Member):
        return u
    return interaction.guild.get_member(u.id)


def _user_passes_rule(member: discord.Member, rule: dict[str, Any]) -> bool:
    if rule.get("everyone"):
        return True
    if member.guild_permissions.administrator:
        return True
    if "all" in rule:
        return all(getattr(member.guild_permissions, name, False) for name in rule["all"])
    if "any" in rule:
        return any(
            all(getattr(member.guild_permissions, name, False) for name in group)
            for group in rule["any"]
        )
    return False


def _fail_message_for_rule(rule: dict[str, Any], user_id: int | None = None) -> str:
    if rule.get("everyone"):
        return t_sync(user_id, "bot.command_perms.deny_everyone", default="You cannot use this command.")
    if "all" in rule:
        need = ", ".join(p.replace("_", " ") for p in rule["all"])
        return t_sync(
            user_id,
            "bot.command_perms.deny_need_all",
            default="You need all of these permissions: **{permissions}**.",
            permissions=need,
        )
    if "any" in rule:
        return t_sync(
            user_id,
            "bot.command_perms.deny_mod_combo",
            default=(
                "You need **Manage Roles**, **Moderate Members**, **Manage Server**, "
                "or **Administrator** to use this command."
            ),
        )
    return t_sync(
        user_id,
        "bot.command_perms.deny_no_permission",
        default="You do not have permission to use this command.",
    )


async def _deny(
    interaction: discord.Interaction,
    message: str,
) -> bool:
    if interaction.type is discord.InteractionType.autocomplete:
        if not interaction.response.is_done():
            await interaction.response.autocomplete([])
        return False
    if not interaction.response.is_done():
        await interaction.response.send_message(message, ephemeral=True)
    return False


def _hard_manage_guild(interaction: discord.Interaction) -> bool:
    m = _member_for_eval(interaction)
    if m is None:
        return False
    return m.guild_permissions.manage_guild or m.guild_permissions.administrator


def _slash_command_for_perm_check(interaction: discord.Interaction) -> app_commands.Command[Any, ..., Any] | None:
    """
    Resolve the invoked slash command from the payload (same as CommandTree._call).

    Do not use interaction.command during tree.interaction_check: it runs before the tree
    fills interaction._cs_command, and discord.py may cache a stale None on Interaction.command,
    which would skip permission checks while the command still runs.
    """
    tree = getattr(interaction.client, "tree", None)
    data = interaction.data
    if tree is None or not isinstance(data, dict):
        return None
    if data.get("type", 1) != 1:
        return None
    try:
        cmd, _ = tree._get_app_command_options(data)  # type: ignore[attr-defined]
    except AppCommandError:
        return None
    if isinstance(cmd, app_commands.Group):
        return None
    return cmd


async def tree_interaction_perm_check(interaction: discord.Interaction) -> bool:
    """Return True to allow the interaction to proceed (slash + autocomplete + context menus)."""
    if interaction.type not in (
        discord.InteractionType.application_command,
        discord.InteractionType.autocomplete,
    ):
        return True

    cmd = _slash_command_for_perm_check(interaction)
    if cmd is None:
        return True

    qn = getattr(cmd, "qualified_name", None) or cmd.name
    qn = str(qn).strip()

    user_id = interaction.user.id if interaction.user else None

    if qn in _COMMAND_PERM_ADMIN_QN:
        if not _hard_manage_guild(interaction):
            return await _deny(
                interaction,
                "❌ "
                + t_sync(
                    user_id,
                    "bot.command_perms.deny_manage_guild",
                    default="You need **Manage Server** (or **Administrator**) to change command permissions.",
                ),
            )
        return True

    rule_model = effective_rule(interaction.guild.id if interaction.guild else None, qn)
    if rule_model is None:
        return True

    if interaction.guild is None:
        # Guild-only commands: let the command body respond; DM has no Member perms.
        if not rule_model.get("everyone"):
            return await _deny(
                interaction,
                "❌ "
                + t_sync(
                    user_id,
                    "bot.command_perms.deny_guild_only",
                    default="This command can only be used in a server.",
                ),
            )
        return True

    member = _member_for_eval(interaction)
    if member is None:
        return await _deny(
            interaction,
            "❌ "
            + t_sync(
                user_id,
                "bot.command_perms.deny_member_resolve",
                default="Could not resolve your member permissions.",
            ),
        )

    if _user_passes_rule(member, rule_model):
        return True

    if "any" in rule_model:
        return await _deny(
            interaction,
            "❌ "
            + t_sync(
                user_id,
                "bot.command_perms.deny_mod_combo",
                default=(
                    "You need **Manage Roles**, **Moderate Members**, **Manage Server**, "
                    "or **Administrator** to use this command."
                ),
            ),
        )

    return await _deny(interaction, "❌ " + _fail_message_for_rule(rule_model, user_id))


def format_rule_human(rule: dict[str, Any]) -> str:
    try:
        r = _normalize_rule(rule)
    except ValueError as e:
        return f"(invalid: {e})"
    if r.get("everyone"):
        return "Everyone (no Discord permission required)"
    if "all" in r:
        return " + ".join(p.replace("_", " ") for p in r["all"])
    if "any" in r:
        return "Manage roles OR moderate OR manage server OR administrator"
    return json.dumps(r)


def export_defaults_snapshot() -> dict[str, Any]:
    return {k: json.loads(json.dumps(v)) for k, v in DEFAULT_SLASH_COMMAND_RULES.items()}
