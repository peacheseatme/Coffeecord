"""Per-user language preference storage."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .catalog import DEFAULT_LOCALE, SUPPORTED_LOCALES

BASE_DIR = Path(__file__).resolve().parent.parent.parent
USER_LANGUAGE_PATH = BASE_DIR / "Storage" / "Config" / "user_languages.json"
_CONFIG_LOCK = asyncio.Lock()

_DISCORD_LOCALE_MAP = (
    ("es", ("es", "es-es", "es-419")),
    ("pt", ("pt", "pt-br", "pt-pt")),
    ("ru", ("ru",)),
)


def normalize_language_code(raw: str | None) -> str:
    if not raw:
        return DEFAULT_LOCALE
    code = str(raw).strip().lower().replace("_", "-")
    if code in SUPPORTED_LOCALES:
        return code
    for stored, prefixes in _DISCORD_LOCALE_MAP:
        for prefix in prefixes:
            if code == prefix or code.startswith(f"{prefix}-"):
                return stored
    return DEFAULT_LOCALE


def map_discord_preferred_locale(preferred_locale: str | None) -> str:
    return normalize_language_code(preferred_locale)


def _read_config_sync() -> dict[str, Any]:
    if not USER_LANGUAGE_PATH.is_file():
        return {}
    try:
        with USER_LANGUAGE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config_sync(data: dict[str, Any]) -> None:
    USER_LANGUAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with USER_LANGUAGE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


async def get_user_language(user_id: int) -> str:
    async with _CONFIG_LOCK:
        root = await asyncio.to_thread(_read_config_sync)
        entry = root.get(str(user_id))
        if isinstance(entry, dict):
            return normalize_language_code(str(entry.get("language", DEFAULT_LOCALE)))
        if isinstance(entry, str):
            return normalize_language_code(entry)
        return DEFAULT_LOCALE


def get_user_language_sync(user_id: int) -> str:
    root = _read_config_sync()
    entry = root.get(str(user_id))
    if isinstance(entry, dict):
        return normalize_language_code(str(entry.get("language", DEFAULT_LOCALE)))
    if isinstance(entry, str):
        return normalize_language_code(entry)
    return DEFAULT_LOCALE


async def set_user_language(user_id: int, language: str) -> str:
    code = normalize_language_code(language)
    if code not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported language: {language}")
    async with _CONFIG_LOCK:
        root = await asyncio.to_thread(_read_config_sync)
        root[str(user_id)] = {"language": code}
        await asyncio.to_thread(_write_config_sync, root)
    return code
