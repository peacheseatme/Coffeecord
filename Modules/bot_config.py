"""
Bot-wide system config: Storage/Config/bot_sys.cfg (INI-style .cfg).

Covers discord.py library file logging, module registry/state paths, and other
system settings. Creates the file with defaults on first run if missing.

Precedence: where noted, environment variables override file values (for
containers and one-off overrides).
"""

from __future__ import annotations

import configparser
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "Storage" / "Config"
BOT_SYS_CFG_FILENAME = "bot_sys.cfg"
BOT_SYS_CFG_PATH = CONFIG_DIR / BOT_SYS_CFG_FILENAME

# Display string for /about and [bot] version= in bot_sys.cfg (may be semver, e.g. 1.0.1).
DEFAULT_BOT_VERSION_DISPLAY = "1"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": DEFAULT_BOT_VERSION_DISPLAY,
    "logging": {
        "discord_library": {
            "enabled": True,
            "level": "INFO",
            "path": "Storage/Logs/discord.log",
            "max_bytes": 10 * 1024 * 1024,
            "backup_count": 5,
        }
    },
    "modules": {
        "registry_path": "Storage/Config/modules.json",
        "state_path": "Storage/Config/module_states.json",
    },
}

# Written when bot_sys.cfg is missing; same content used by generate_storage_placeholders.
DEFAULT_BOT_SYS_CFG_BODY = """\
# CoffeeCord system configuration. Paths are relative to the project root unless absolute.
# Logging: env vars DISCORD_LOG_* override these values when set.
# [bot] version is shown in /about (e.g. 1.0.1).

[bot]
version = 1

[logging_discord_library]
enabled = true
level = INFO
path = Storage/Logs/discord.log
max_bytes = 10485760
backup_count = 5

[modules]
registry_path = Storage/Config/modules.json
state_path = Storage/Config/module_states.json
"""

LEGACY_JSON_PATH = CONFIG_DIR / "bot_config.json"

_config_cache: dict[str, Any] | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, ov in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(ov, dict):
            merged[key] = _deep_merge(merged[key], ov)
        else:
            merged[key] = ov
    return merged


def _deep_copy_config(base: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(base))


def _parser_apply_to_merged(
    parser: configparser.ConfigParser, merged: dict[str, Any]
) -> None:
    if parser.has_section("bot") and parser.has_option("bot", "version"):
        v = parser.get("bot", "version").strip()
        if v:
            merged["version"] = v

    sec_name = "logging_discord_library"
    if parser.has_section(sec_name):
        ldc = merged["logging"]["discord_library"]
        p = parser[sec_name]
        if "enabled" in p:
            try:
                ldc["enabled"] = p.getboolean("enabled")
            except ValueError:
                pass
        if "level" in p:
            ldc["level"] = p.get("level", fallback="INFO").strip()
        if "path" in p:
            pv = p.get("path", fallback="").strip()
            if pv:
                ldc["path"] = pv
        if "max_bytes" in p:
            try:
                ldc["max_bytes"] = p.getint("max_bytes")
            except ValueError:
                pass
        if "backup_count" in p:
            try:
                ldc["backup_count"] = p.getint("backup_count")
            except ValueError:
                pass

    if parser.has_section("modules"):
        m = merged["modules"]
        mp = parser["modules"]
        if "registry_path" in mp:
            rv = mp.get("registry_path", fallback="").strip()
            if rv:
                m["registry_path"] = rv
        if "state_path" in mp:
            sv = mp.get("state_path", fallback="").strip()
            if sv:
                m["state_path"] = sv


def _load_cfg_file(path: Path) -> dict[str, Any]:
    merged = _deep_copy_config(DEFAULT_CONFIG)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        read = parser.read(path, encoding="utf-8")
        if not read:
            return merged
    except (OSError, configparser.Error):
        return merged
    _parser_apply_to_merged(parser, merged)
    return merged


def _write_default_cfg_file() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with BOT_SYS_CFG_PATH.open("w", encoding="utf-8") as fp:
        fp.write(DEFAULT_BOT_SYS_CFG_BODY)
        if not DEFAULT_BOT_SYS_CFG_BODY.endswith("\n"):
            fp.write("\n")


def _ensure_config_file() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if BOT_SYS_CFG_PATH.exists():
        return _load_cfg_file(BOT_SYS_CFG_PATH)
    if LEGACY_JSON_PATH.exists():
        try:
            with LEGACY_JSON_PATH.open("r", encoding="utf-8") as fp:
                raw = json.load(fp)
            if isinstance(raw, dict):
                merged = _deep_merge(_deep_copy_config(DEFAULT_CONFIG), raw)
                _write_cfg_from_merged_dict(merged)
                return merged
        except (OSError, json.JSONDecodeError):
            pass
    _write_default_cfg_file()
    return _deep_copy_config(DEFAULT_CONFIG)


def _write_cfg_from_merged_dict(merged: dict[str, Any]) -> None:
    """Overwrite bot_sys.cfg from an in-memory merged config (e.g. after JSON migration)."""
    ldc = merged.get("logging", {}).get("discord_library", {})
    mods = merged.get("modules", {})
    parser = configparser.ConfigParser(interpolation=None)
    parser.add_section("bot")
    ver = merged.get("version", DEFAULT_BOT_VERSION_DISPLAY)
    parser.set("bot", "version", str(ver).strip() or DEFAULT_BOT_VERSION_DISPLAY)

    parser.add_section("logging_discord_library")
    parser.set(
        "logging_discord_library",
        "enabled",
        "true" if bool(ldc.get("enabled", True)) else "false",
    )
    parser.set("logging_discord_library", "level", str(ldc.get("level", "INFO")))
    parser.set("logging_discord_library", "path", str(ldc.get("path", "Storage/Logs/discord.log")))
    parser.set("logging_discord_library", "max_bytes", str(int(ldc.get("max_bytes", 10 * 1024 * 1024))))
    parser.set("logging_discord_library", "backup_count", str(int(ldc.get("backup_count", 5))))

    parser.add_section("modules")
    parser.set(
        "modules",
        "registry_path",
        str(mods.get("registry_path", DEFAULT_CONFIG["modules"]["registry_path"])),
    )
    parser.set(
        "modules",
        "state_path",
        str(mods.get("state_path", DEFAULT_CONFIG["modules"]["state_path"])),
    )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with BOT_SYS_CFG_PATH.open("w", encoding="utf-8") as fp:
        fp.write(
            "# CoffeeCord system configuration (migrated from bot_config.json).\n"
            "# Paths are relative to the project root unless absolute.\n\n"
        )
        parser.write(fp)


def get_bot_config() -> dict[str, Any]:
    """Return merged bot config (defaults + bot_sys.cfg). Cached per process."""
    global _config_cache
    if _config_cache is None:
        _config_cache = _ensure_config_file()
    return _config_cache


def get_bot_version_display() -> str:
    """Version string from bot_sys.cfg [bot] version= (for /about, etc.)."""
    cfg = get_bot_config()
    v = cfg.get("version")
    if v is None:
        return "unknown"
    s = str(v).strip()
    return s if s else "unknown"


def reload_bot_config() -> dict[str, Any]:
    """Force re-read from disk (e.g. after manual file edit)."""
    global _config_cache
    _config_cache = None
    return get_bot_config()


def resolve_project_path(relative_or_absolute: str) -> Path:
    """Paths in bot_sys.cfg are relative to the project root unless absolute."""
    p = Path(relative_or_absolute.strip())
    if p.is_absolute():
        return p.resolve()
    return (BASE_DIR / p).resolve()


@dataclass(frozen=True)
class DiscordLibraryLogSettings:
    enabled: bool
    path: Path
    level: int
    max_bytes: int
    backup_count: int


def get_discord_library_log_settings() -> DiscordLibraryLogSettings:
    """
    Effective discord.py library logging options.

    Env overrides (when set): DISCORD_LOG_FILE, DISCORD_LOG_LEVEL, DISCORD_LOG_PATH,
    DISCORD_LOG_MAX_BYTES, DISCORD_LOG_BACKUP_COUNT.
    """
    cfg = get_bot_config()
    sec = cfg.get("logging", {}).get("discord_library", {})
    if not isinstance(sec, dict):
        sec = {}

    enabled = bool(sec.get("enabled", True))
    if "DISCORD_LOG_FILE" in os.environ:
        enabled = os.environ["DISCORD_LOG_FILE"].strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    level_name = str(sec.get("level", "INFO")).strip().upper()
    if "DISCORD_LOG_LEVEL" in os.environ:
        level_name = os.environ["DISCORD_LOG_LEVEL"].strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    path_rel = str(sec.get("path", "Storage/Logs/discord.log"))
    log_path = resolve_project_path(path_rel)
    env_path = os.getenv("DISCORD_LOG_PATH")
    if env_path:
        ep = Path(env_path).expanduser()
        log_path = ep.resolve() if ep.is_absolute() else (BASE_DIR / ep).resolve()

    max_bytes = int(sec.get("max_bytes", 10 * 1024 * 1024))
    if "DISCORD_LOG_MAX_BYTES" in os.environ:
        max_bytes = int(os.environ["DISCORD_LOG_MAX_BYTES"])

    backup_count = int(sec.get("backup_count", 5))
    if "DISCORD_LOG_BACKUP_COUNT" in os.environ:
        backup_count = int(os.environ["DISCORD_LOG_BACKUP_COUNT"])

    return DiscordLibraryLogSettings(
        enabled=enabled,
        path=log_path,
        level=level,
        max_bytes=max(max_bytes, 1_048_576),
        backup_count=max(backup_count, 0),
    )


def get_module_registry_path() -> Path:
    """Path to modules.json (list of cog modules and paths)."""
    cfg = get_bot_config()
    mods = cfg.get("modules", {})
    rel = str(mods.get("registry_path", DEFAULT_CONFIG["modules"]["registry_path"]))
    return resolve_project_path(rel)


def get_module_state_path() -> Path:
    """Path to module_states.json (per-guild enable flags)."""
    cfg = get_bot_config()
    mods = cfg.get("modules", {})
    rel = str(mods.get("state_path", DEFAULT_CONFIG["modules"]["state_path"]))
    return resolve_project_path(rel)
