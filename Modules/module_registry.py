import ast
import asyncio
import json
import re
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "Storage" / "Config"
MODULES_DIR = BASE_DIR / "Modules"


def _registry_path() -> Path:
    from . import bot_config

    return bot_config.get_module_registry_path()


def _state_path() -> Path:
    from . import bot_config

    return bot_config.get_module_state_path()

# These files live in Modules/ but are not loadable cogs.
_DISCOVERY_EXCLUDE: frozenset[str] = frozenset({"module_registry", "kofi_webhook", "backup_crypto"})

_REGISTRY_LOCK = asyncio.Lock()
_STATE_LOCK = asyncio.Lock()

REGISTRY_DEFAULT: dict[str, list[dict[str, Any]]] = {
    "modules": [
        {
            "id": "automod",
            "extension": "Modules.automod",
            "path": "Modules/automod.py",
            "display_name": "Automod",
            "description": "Spam, caps, link, mention, keyword filters, and escalation.",
            "default_enabled": True,
            "category": "moderation",
        },
        {
            "id": "inactive_members",
            "extension": "Modules.inactive_members",
            "path": "Modules/inactive_members.py",
            "display_name": "Inactive Members",
            "description": "Track activity and remove inactive members by configurable rules.",
            "default_enabled": True,
            "category": "moderation",
        },
        {
            "id": "logging",
            "extension": "Modules.logging",
            "path": "Modules/logging.py",
            "display_name": "Logging",
            "description": "Server event and module action logging.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "tickets",
            "extension": "Modules.tickets",
            "path": "Modules/tickets.py",
            "display_name": "Tickets",
            "description": "Ticket panel, ticket controls, and ticket management commands.",
            "default_enabled": True,
            "category": "utilities",
        },
        {
            "id": "translate",
            "extension": "Modules.translate",
            "path": "Modules/translate.py",
            "display_name": "Translation",
            "description": "Manual and live translation with per-user settings.",
            "default_enabled": True,
            "category": "utilities",
        },
        {
            "id": "reactionrole",
            "extension": "Modules.reactionrole",
            "path": "Modules/reactionrole.py",
            "display_name": "Reaction Roles",
            "description": "Reaction/button based self-role assignment.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "color_roles",
            "extension": "Modules.color_roles",
            "path": "Modules/color_roles.py",
            "display_name": "Color Roles",
            "description": "Exclusive self-assign color roles via buttons or emoji reactions.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "autorole",
            "extension": "Modules.autorole",
            "path": "Modules/autorole.py",
            "display_name": "Auto Roles",
            "description": "Rule-based automatic role assignment.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "welcome_leave",
            "extension": "Modules.welcome_leave",
            "path": "Modules/welcome_leave.py",
            "display_name": "Welcome & Leave",
            "description": "Welcome/leave messages and optional exit surveys.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "sticky_msg",
            "extension": "Modules.sticky_msg",
            "path": "Modules/sticky_msg.py",
            "display_name": "Sticky Messages",
            "description": "Named sticky notices re-posted at the bottom of configured channels.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "setup_wizard",
            "extension": "Modules.setup_wizard",
            "path": "Modules/setup_wizard.py",
            "display_name": "Setup Wizard",
            "description": "Interactive server setup wizard.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "modules_cmd",
            "extension": "Modules.modules_cmd",
            "path": "Modules/modules_cmd.py",
            "display_name": "Module Controls",
            "description": "Per-server module visibility and toggle commands.",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "language",
            "extension": "Modules.language",
            "path": "Modules/language.py",
            "display_name": "Language",
            "description": "Choose your language for Coffeecord replies (English, Spanish, Portuguese, Russian).",
            "default_enabled": True,
            "category": "configuration",
        },
        {
            "id": "leveling",
            "extension": "Modules.leveling",
            "path": "Modules/leveling.py",
            "display_name": "Leveling & XP",
            "description": "XP gain, level-up logic, rewards, and level cards.",
            "default_enabled": True,
            "category": "engagement",
        },
        {
            "id": "kofi",
            "extension": "Modules.kofi",
            "path": "Modules/kofi.py",
            "display_name": "Ko-fi Supporters",
            "description": "Ko-fi linking/claims, supporter state, and webhook helpers.",
            "default_enabled": True,
            "category": "integrations",
        },
        {
            "id": "host_console",
            "extension": "Modules.host_console",
            "path": "Modules/host_console.py",
            "display_name": "Host Console",
            "description": "Local Unix-socket REPL for running commands from c-cord console.",
            "default_enabled": True,
            "category": "utilities",
        },
        {
            "id": "verification",
            "extension": "Modules.verification",
            "path": "Modules/verification.py",
            "display_name": "Verification",
            "description": "Verification UI, verification flow, and configuration.",
            "default_enabled": True,
            "category": "moderation",
        },
        {
            "id": "polls",
            "extension": "Modules.polls",
            "path": "Modules/polls.py",
            "display_name": "Polls",
            "description": "Poll creation, voting, and poll lifecycle events.",
            "default_enabled": True,
            "category": "engagement",
        },
        {
            "id": "quests",
            "extension": "Modules.quests",
            "path": "Modules/quests.py",
            "display_name": "Quests",
            "description": "Configurable quests, daily check-in, reaction and message goals with XP rewards.",
            "default_enabled": True,
            "category": "engagement",
        },
        {
            "id": "applications",
            "extension": "Modules.applications",
            "path": "Modules/applications.py",
            "display_name": "Applications",
            "description": "Staff application questions, submissions, and review actions.",
            "default_enabled": True,
            "category": "utilities",
        },
        {
            "id": "adaptive_slowmode",
            "extension": "Modules.adaptive_slowmode",
            "path": "Modules/adaptive_slowmode.py",
            "display_name": "Adaptive Slowmode",
            "description": "Adaptive slowmode configuration and trigger handling.",
            "default_enabled": True,
            "category": "moderation",
        },
        {
            "id": "muterole",
            "extension": "Modules.muterole",
            "path": "Modules/muterole.py",
            "display_name": "Mute Role",
            "description": "Mute role creation and configuration commands.",
            "default_enabled": True,
            "category": "moderation",
        },
        {
            "id": "calls",
            "extension": "Modules.calls",
            "path": "Modules/calls.py",
            "display_name": "Calls",
            "description": "Private call channels and call membership management.",
            "default_enabled": True,
            "category": "utilities",
        },
        {
            "id": "nickname",
            "extension": "Modules.nickname",
            "path": "Modules/nickname.py",
            "display_name": "Nickname",
            "description": "Nickname management commands and related checks.",
            "default_enabled": True,
            "category": "utilities",
        },
        {
            "id": "support",
            "extension": "Modules.support",
            "path": "Modules/support.py",
            "display_name": "Support Us",
            "description": "Support information, perks, and support links command.",
            "default_enabled": True,
            "category": "integrations",
        },
        {
            "id": "server_backup",
            "extension": "Modules.server_backup",
            "path": "Modules/server_backup.py",
            "display_name": "Server Backup",
            "description": "Encrypted Discord structure + Coffeecord config backups with host slots.",
            "default_enabled": True,
            "category": "configuration",
        },
    ]
}


def _read_json_sync(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(default, fp, indent=2, ensure_ascii=True)
        return default
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_sync(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


def _normalize_registry(raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    modules_raw = raw.get("modules", [])
    if not isinstance(modules_raw, list):
        return REGISTRY_DEFAULT

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in modules_raw:
        if not isinstance(item, dict):
            continue
        module_id = str(item.get("id", "")).strip().lower()
        extension = str(item.get("extension", "")).strip()
        path = str(item.get("path", "")).strip()
        if not module_id or not extension or not path or module_id in seen_ids:
            continue
        seen_ids.add(module_id)
        normalized.append(
            {
                "id": module_id,
                "extension": extension,
                "path": path,
                "display_name": str(item.get("display_name", module_id)).strip() or module_id,
                "description": str(item.get("description", "")).strip(),
                "default_enabled": bool(item.get("default_enabled", True)),
                "category": str(item.get("category", "utilities")).strip() or "utilities",
            }
        )

    if not normalized:
        return REGISTRY_DEFAULT
    return {"modules": normalized}


async def load_module_registry() -> list[dict[str, Any]]:
    async with _REGISTRY_LOCK:
        raw = await asyncio.to_thread(_read_json_sync, _registry_path(), REGISTRY_DEFAULT)
        normalized = _normalize_registry(raw)
        await asyncio.to_thread(_write_json_sync, _registry_path(), normalized)
        return list(normalized["modules"])


async def get_registry_map() -> dict[str, dict[str, Any]]:
    modules = await load_module_registry()
    return {str(m["id"]): m for m in modules}


async def validate_registry_paths() -> list[str]:
    errors: list[str] = []
    modules = await load_module_registry()
    for module in modules:
        module_path = BASE_DIR / str(module["path"])
        if not module_path.exists():
            errors.append(
                f"Module '{module['id']}' points to missing path: {module['path']}"
            )
    return errors


async def get_guild_module_states(guild_id: int) -> dict[str, bool]:
    module_map = await get_registry_map()
    defaults = {
        module_id: bool(entry.get("default_enabled", True))
        for module_id, entry in module_map.items()
    }
    async with _STATE_LOCK:
        raw = await asyncio.to_thread(_read_json_sync, _state_path(), {})
        guild_key = str(guild_id)
        stored = raw.get(guild_key, {})
        if not isinstance(stored, dict):
            stored = {}
        merged = dict(defaults)
        for module_id, value in stored.items():
            if module_id in merged:
                merged[module_id] = bool(value)
        raw[guild_key] = merged
        await asyncio.to_thread(_write_json_sync, _state_path(), raw)
    return merged


async def is_module_enabled(guild_id: int, module_id: str) -> bool:
    module_id = str(module_id).strip().lower()
    states = await get_guild_module_states(guild_id)
    return bool(states.get(module_id, True))


def _stem_to_display_name(stem: str) -> str:
    """Convert snake_case filename stem to Title Case display name."""
    return re.sub(r"_([a-z])", lambda m: " " + m.group(1).upper(), stem).title()


def _extract_module_meta(file_path: Path) -> dict[str, str]:
    """
    Statically parse optional metadata variables from a module file:
        __module_display_name__ = "My Module"
        __module_description__  = "Does something useful."
        __module_category__     = "utilities"
    Returns only the keys that are present.
    """
    META_VARS = {"__module_display_name__", "__module_description__", "__module_category__"}
    KEY_MAP = {
        "__module_display_name__": "display_name",
        "__module_description__": "description",
        "__module_category__": "category",
    }
    meta: dict[str, str] = {}
    try:
        src = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(file_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name) or target.id not in META_VARS:
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    meta[KEY_MAP[target.id]] = node.value.value
    except (OSError, SyntaxError):
        pass
    return meta


def discover_modules_on_disk() -> list[dict[str, Any]]:
    """Scan Modules/ and return a registry entry for every loadable .py file found."""
    discovered: list[dict[str, Any]] = []
    if not MODULES_DIR.is_dir():
        return discovered
    for py_file in sorted(MODULES_DIR.glob("*.py")):
        stem = py_file.stem
        if stem in _DISCOVERY_EXCLUDE:
            continue
        meta = _extract_module_meta(py_file)
        discovered.append(
            {
                "id": stem.lower(),
                "extension": f"Modules.{stem}",
                "path": f"Modules/{py_file.name}",
                "display_name": meta.get("display_name") or _stem_to_display_name(stem),
                "description": meta.get("description", ""),
                "default_enabled": True,
                "category": meta.get("category", "utilities"),
            }
        )
    return discovered


def refresh_registry(dry_run: bool = False) -> tuple[int, int]:
    """
    Merge newly discovered modules on disk into modules.json.

    Existing entries are preserved unchanged (custom descriptions, categories, etc.).
    Only new entries — files not already in the registry — are appended.

    Returns:
        (added_count, total_count)
    """
    existing_raw = _read_json_sync(_registry_path(), REGISTRY_DEFAULT)
    existing_list: list[dict[str, Any]] = existing_raw.get("modules", [])
    if not isinstance(existing_list, list):
        existing_list = []

    existing_map: dict[str, dict[str, Any]] = {
        str(m.get("id", "")).strip().lower(): m
        for m in existing_list
        if isinstance(m, dict) and m.get("id")
    }

    added = 0
    for mod in discover_modules_on_disk():
        mid = mod["id"]
        if mid not in existing_map:
            existing_map[mid] = mod
            added += 1

    total = len(existing_map)
    if not dry_run:
        merged = [existing_map[k] for k in sorted(existing_map)]
        _write_json_sync(_registry_path(), {"modules": merged})
    return added, total


async def set_module_enabled(guild_id: int, module_id: str, enabled: bool) -> None:
    module_id = str(module_id).strip().lower()
    if module_id == "modules_cmd":
        return
    module_map = await get_registry_map()
    if module_id not in module_map:
        return
    async with _STATE_LOCK:
        raw = await asyncio.to_thread(_read_json_sync, _state_path(), {})
        guild_key = str(guild_id)
        stored = raw.get(guild_key, {})
        if not isinstance(stored, dict):
            stored = {}
        stored[module_id] = bool(enabled)
        raw[guild_key] = stored
        await asyncio.to_thread(_write_json_sync, _state_path(), raw)
