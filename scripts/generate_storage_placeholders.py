#!/usr/bin/env python3
"""
Generate Storage/Config and Storage/Data placeholder JSON files for fresh installs.
Run from project root. Default mode does not overwrite existing files.

Repair mode (--repair): creates missing known storage files and overwrites broken
(empty or invalid JSON, or non-object JSON where a dict is expected). Use
--exclude basename (repeatable) to skip files you customized. --dry-run prints
actions without writing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Modules.bot_config import DEFAULT_BOT_SYS_CFG_BODY

STORAGE_CONFIG = PROJECT_ROOT / "Storage" / "Config"
STORAGE_DATA = PROJECT_ROOT / "Storage" / "Data"

REPAIR_NOOP_HINT = (
    "Note: this step only checks known Storage/Config and Storage/Data JSON plus bot_sys.cfg "
    "(empty or invalid JSON). Missing tracked Python files are restored when you run full "
    "`./bot.sh repair` (without --storage-only), which runs git first."
)

# Config files with placeholder data
CONFIG_PLACEHOLDERS: dict[str, object] = {
    "autorole_config.json": {},
    "automod.json": {
        "default": {
            "enabled": False,
            "count_rule_violations_as_warns": False,
            "log_channel_id": None,
            "whitelist": {"roles": [], "channels": []},
            "protected_roles": [],
            "channel_overrides": {},
            "bad_words": {"enabled": False, "words": [], "action": "warn", "delete_message": True, "escalation": []},
            "spam": {"enabled": True, "max_messages": 5, "per_seconds": 6, "action": "timeout", "timeout_seconds": 60, "escalation": []},
            "duplicate_messages": {"enabled": False, "window_seconds": 30, "min_duplicates": 3, "action": "delete", "escalation": []},
            "links": {"enabled": False, "block_invites": True, "block_links": False, "allowed_domains": [], "allowed_invite_codes": [], "action": "delete", "escalation": [], "delete_message": True},
            "mentions": {"enabled": False, "max_mentions": 5, "action": "warn", "escalation": []},
        }
    },
    "bot_branding.json": {
        "discord_application_id": "",
        "owner_id": 0,
        "support_server_url": "",
        "support_invite_url": "",
    },
    "backgrounds.json": {},
    "command_config.json": {"guild_id": 0, "command_config": {}},
    "exit_surveys.json": {},
    "level_rewards.json": {},
    "leveling.json": {},
    "leveling_announce.json": {},
    "leveling_config.json": {},
    "logging.json": {},
    "modquestions.json": {},
    "module_states.json": {},
    "modules.json": {
        "modules": [
            {"id": "adaptive_slowmode", "extension": "Modules.adaptive_slowmode", "path": "Modules/adaptive_slowmode.py", "display_name": "Adaptive Slowmode", "description": "Adaptive slowmode configuration.", "default_enabled": True, "category": "moderation"},
            {"id": "applications", "extension": "Modules.applications", "path": "Modules/applications.py", "display_name": "Applications", "description": "Staff application questions and submissions.", "default_enabled": True, "category": "utilities"},
            {"id": "automod", "extension": "Modules.automod", "path": "Modules/automod.py", "display_name": "Automod", "description": "Spam, caps, link, mention, keyword filters.", "default_enabled": True, "category": "moderation"},
            {"id": "autorole", "extension": "Modules.autorole", "path": "Modules/autorole.py", "display_name": "Auto Roles", "description": "Rule-based automatic role assignment.", "default_enabled": True, "category": "configuration"},
            {"id": "calls", "extension": "Modules.calls", "path": "Modules/calls.py", "display_name": "Calls", "description": "Private call channels.", "default_enabled": True, "category": "utilities"},
            {"id": "kofi", "extension": "Modules.kofi", "path": "Modules/kofi.py", "display_name": "Ko-fi Supporters", "description": "Ko-fi linking and supporter perks.", "default_enabled": True, "category": "integrations"},
            {"id": "leveling", "extension": "Modules.leveling", "path": "Modules/leveling.py", "display_name": "Leveling & XP", "description": "XP gain, level-up logic, level cards.", "default_enabled": True, "category": "engagement"},
            {"id": "logging", "extension": "Modules.logging", "path": "Modules/logging.py", "display_name": "Logging", "description": "Server event logging.", "default_enabled": True, "category": "configuration"},
            {"id": "modules_cmd", "extension": "Modules.modules_cmd", "path": "Modules/modules_cmd.py", "display_name": "Module Controls", "description": "Per-server module toggle.", "default_enabled": True, "category": "configuration"},
            {"id": "muterole", "extension": "Modules.muterole", "path": "Modules/muterole.py", "display_name": "Mute Role", "description": "Mute role configuration.", "default_enabled": True, "category": "moderation"},
            {"id": "nickname", "extension": "Modules.nickname", "path": "Modules/nickname.py", "display_name": "Nickname", "description": "Nickname management.", "default_enabled": True, "category": "utilities"},
            {"id": "polls", "extension": "Modules.polls", "path": "Modules/polls.py", "display_name": "Polls", "description": "Poll creation and voting.", "default_enabled": True, "category": "engagement"},
            {"id": "quests", "extension": "Modules.quests", "path": "Modules/quests.py", "display_name": "Quests", "description": "Quest board, daily check-in, and XP rewards.", "default_enabled": True, "category": "engagement"},
            {"id": "reactionrole", "extension": "Modules.reactionrole", "path": "Modules/reactionrole.py", "display_name": "Reaction Roles", "description": "Reaction/button self-role assignment.", "default_enabled": True, "category": "configuration"},
            {"id": "staff_utils", "extension": "Modules.staff_utils", "path": "Modules/staff_utils.py", "display_name": "Staff Utilities", "description": "Advanced purge, lockdown, notes, bulk roles.", "default_enabled": True, "category": "moderation"},
            {"id": "sticky_msg", "extension": "Modules.sticky_msg", "path": "Modules/sticky_msg.py", "display_name": "Sticky Messages", "description": "Named stickies re-posted at channel bottom.", "default_enabled": True, "category": "configuration"},
            {"id": "setup_wizard", "extension": "Modules.setup_wizard", "path": "Modules/setup_wizard.py", "display_name": "Setup Wizard", "description": "Interactive server setup.", "default_enabled": True, "category": "configuration"},
            {"id": "support", "extension": "Modules.support", "path": "Modules/support.py", "display_name": "Support Us", "description": "Support information and links.", "default_enabled": True, "category": "integrations"},
            {"id": "test_module", "extension": "Modules.test_module", "path": "Modules/test_module.py", "display_name": "Test Module", "description": "Test module for refresh_registry.", "default_enabled": True, "category": "utilities"},
            {"id": "tickets", "extension": "Modules.tickets", "path": "Modules/tickets.py", "display_name": "Tickets", "description": "Ticket panel and management.", "default_enabled": True, "category": "utilities"},
            {"id": "translate", "extension": "Modules.translate", "path": "Modules/translate.py", "display_name": "Translation", "description": "Manual and live translation.", "default_enabled": True, "category": "utilities"},
            {"id": "verification", "extension": "Modules.verification", "path": "Modules/verification.py", "display_name": "Verification", "description": "Verification UI and flow.", "default_enabled": True, "category": "moderation"},
            {"id": "welcome_leave", "extension": "Modules.welcome_leave", "path": "Modules/welcome_leave.py", "display_name": "Welcome & Leave", "description": "Welcome/leave messages.", "default_enabled": True, "category": "configuration"},
        ]
    },
    "mute_roles.json": {},
    "quests.json": {},
    "reactionrole_config.json": {},
    "slowmode.json": {},
    "sticky_messages.json": {},
    "translate_usage.json": {},
    "translate_users.json": {},
    "verify_config.json": {},
    "welcome_leave.json": {},
    "themes.json": {"guilds": {}},
    "themes_config.json": {"guilds": {}},
    "command_responses.json": {"guilds": {}},
    "adaptive_slowmode.json": {},
}

# Data files with placeholder data
DATA_PLACEHOLDERS: dict[str, object] = {
    "automod_strikes.json": {},
    "supporters.json": {"supporters": {}, "unlinked_donations": []},
    "tickets.json": {},
    "warns.json": {},
    "xp.json": {},
    "levelcard_styles.json": {},
    "yaps.json": {"guilds": {}, "stats": {}},
    "staff_applications.json": {},
    "staff_history.json": {"guilds": {}},
    "lockdown_state.json": {"guilds": {}},
}


def _write_if_missing(path: Path, data: object) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True


def _write_text_if_missing(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    with path.open("w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
    return True


def _normalize_exclude(token: str) -> str:
    """Basename only, lowercased (matches xp.json, Storage/Data/xp.json, etc.)."""
    return Path(token.strip().replace("\\", "/")).name.lower()


def _json_file_broken(path: Path) -> bool:
    if not path.is_file():
        return True
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return True
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True
    return not isinstance(data, dict)


def _bot_sys_cfg_broken(path: Path) -> bool:
    if not path.is_file():
        return True
    try:
        return len(path.read_text(encoding="utf-8").strip()) == 0
    except OSError:
        return True


def _write_json_atomic(path: Path, data: object, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _write_text_atomic(path: Path, text: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def run_create_missing() -> int:
    created = 0
    if _write_text_if_missing(STORAGE_CONFIG / "bot_sys.cfg", DEFAULT_BOT_SYS_CFG_BODY):
        created += 1
        print("  Created Storage/Config/bot_sys.cfg")
    for name, data in CONFIG_PLACEHOLDERS.items():
        if _write_if_missing(STORAGE_CONFIG / name, data):
            created += 1
            print(f"  Created Storage/Config/{name}")
    for name, data in DATA_PLACEHOLDERS.items():
        if _write_if_missing(STORAGE_DATA / name, data):
            created += 1
            print(f"  Created Storage/Data/{name}")
    if created:
        print(f"Generated {created} placeholder file(s).")
    else:
        print("Storage files already exist; nothing generated.")
    return 0


def run_repair(excludes: list[str], *, dry_run: bool) -> int:
    skip = {_normalize_exclude(x) for x in excludes if x.strip()}
    actions = 0

    def touch(rel: str, kind: str) -> None:
        nonlocal actions
        print(f"  [{kind}] {rel}")
        actions += 1

    bot_cfg = STORAGE_CONFIG / "bot_sys.cfg"
    bn = bot_cfg.name.lower()
    if bn not in skip:
        if not bot_cfg.exists() or _bot_sys_cfg_broken(bot_cfg):
            touch(str(bot_cfg.relative_to(PROJECT_ROOT)), "repair" if bot_cfg.exists() else "create")
            _write_text_atomic(bot_cfg, DEFAULT_BOT_SYS_CFG_BODY, dry_run=dry_run)

    for name, data in CONFIG_PLACEHOLDERS.items():
        key = name.lower()
        if key in skip:
            print(f"  [skip] Storage/Config/{name} (--exclude)")
            continue
        path = STORAGE_CONFIG / name
        if not path.exists() or _json_file_broken(path):
            kind = "repair" if path.exists() else "create"
            touch(f"Storage/Config/{name}", kind)
            _write_json_atomic(path, data, dry_run=dry_run)

    for name, data in DATA_PLACEHOLDERS.items():
        key = name.lower()
        if key in skip:
            print(f"  [skip] Storage/Data/{name} (--exclude)")
            continue
        path = STORAGE_DATA / name
        if not path.exists() or _json_file_broken(path):
            kind = "repair" if path.exists() else "create"
            touch(f"Storage/Data/{name}", kind)
            _write_json_atomic(path, data, dry_run=dry_run)

    if dry_run and actions:
        print(f"Dry run: would create/repair {actions} file(s). Run without --dry-run to apply.")
    elif dry_run and not actions:
        print("Dry run: nothing missing or broken (among known storage files).")
        print(REPAIR_NOOP_HINT)
    elif actions:
        print(f"Repair finished: {actions} file(s) created or replaced.")
    else:
        print("Repair: nothing missing or broken (among known storage files).")
        print(REPAIR_NOOP_HINT)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(
        description="Create missing Storage placeholders, or --repair broken/missing JSON and bot_sys.cfg.",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Create missing OR overwrite invalid/empty known config/data JSON and empty bot_sys.cfg",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="FILE",
        help="Basename (e.g. xp.json) or path; never overwrite this file in repair mode",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --repair: print actions only, do not write",
    )
    args = parser.parse_args(argv)

    if args.repair:
        if args.dry_run:
            print("c-cord repair (dry run)")
        else:
            print("c-cord repair")
        return run_repair(args.exclude, dry_run=args.dry_run)

    if args.exclude or args.dry_run:
        print("Note: --exclude and --dry-run only apply with --repair; running create-missing only.", file=sys.stderr)
    run_create_missing()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
