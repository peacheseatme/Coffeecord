#!/usr/bin/env python3
"""
Generate Storage/Config and Storage/Data placeholder JSON files for fresh installs.
Run from project root. Does not overwrite existing files.
"""
from __future__ import annotations

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
            {"id": "kofi", "extension": "Modules.kofi", "path": "Modules.kofi.py", "display_name": "Ko-fi Supporters", "description": "Ko-fi linking and supporter perks.", "default_enabled": True, "category": "integrations"},
            {"id": "leveling", "extension": "Modules.leveling", "path": "Modules/leveling.py", "display_name": "Leveling & XP", "description": "XP gain, level-up logic, level cards.", "default_enabled": True, "category": "engagement"},
            {"id": "logging", "extension": "Modules.logging", "path": "Modules/logging.py", "display_name": "Logging", "description": "Server event logging.", "default_enabled": True, "category": "configuration"},
            {"id": "modules_cmd", "extension": "Modules.modules_cmd", "path": "Modules/modules_cmd.py", "display_name": "Module Controls", "description": "Per-server module toggle.", "default_enabled": True, "category": "configuration"},
            {"id": "muterole", "extension": "Modules.muterole", "path": "Modules/muterole.py", "display_name": "Mute Role", "description": "Mute role configuration.", "default_enabled": True, "category": "moderation"},
            {"id": "nickname", "extension": "Modules.nickname", "path": "Modules/nickname.py", "display_name": "Nickname", "description": "Nickname management.", "default_enabled": True, "category": "utilities"},
            {"id": "polls", "extension": "Modules.polls", "path": "Modules/polls.py", "display_name": "Polls", "description": "Poll creation and voting.", "default_enabled": True, "category": "engagement"},
            {"id": "reactionrole", "extension": "Modules.reactionrole", "path": "Modules/reactionrole.py", "display_name": "Reaction Roles", "description": "Reaction/button self-role assignment.", "default_enabled": True, "category": "configuration"},
            {"id": "staff_utils", "extension": "Modules.staff_utils", "path": "Modules/staff_utils.py", "display_name": "Staff Utilities", "description": "Advanced purge, lockdown, notes, bulk roles.", "default_enabled": True, "category": "moderation"},
            {"id": "setup_wizard", "extension": "Modules.setup_wizard", "path": "Modules/setup_wizard.py", "display_name": "Setup Wizard", "description": "Interactive server setup.", "default_enabled": True, "category": "configuration"},
            {"id": "support", "extension": "Modules.support", "path": "Modules.support.py", "display_name": "Support Us", "description": "Support information and links.", "default_enabled": True, "category": "integrations"},
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


def main() -> None:
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


if __name__ == "__main__":
    main()
