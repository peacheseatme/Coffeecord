"""Discord slash-command localization helpers."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from .catalog import catalog_keys, reload_catalogs, translate_for_locale

LOGGER = logging.getLogger("coffeecord.i18n.slash")

_DISCORD_LOCALE_BY_CODE = {
    "es": discord.Locale.spain_spanish,
    "pt": discord.Locale.brazil_portuguese,
    "ru": discord.Locale.russian,
}


def slash_description_loc(key: str, *, default: str | None = None) -> dict[discord.Locale, str]:
    out: dict[discord.Locale, str] = {}
    for code, locale in _DISCORD_LOCALE_BY_CODE.items():
        text = translate_for_locale(code, key, default=default)
        if text and text != key:
            out[locale] = text[:100]
    return out


def slash_name_loc(key: str, *, default: str | None = None) -> dict[discord.Locale, str]:
    out: dict[discord.Locale, str] = {}
    for code, locale in _DISCORD_LOCALE_BY_CODE.items():
        text = translate_for_locale(code, key, default=default)
        if text and text != key:
            out[locale] = text[:32]
    return out


def _resolve_tree_command(
    tree: app_commands.CommandTree,
    parts: list[str],
) -> app_commands.Command | app_commands.Group | None:
    if not parts:
        return None
    current: app_commands.Command | app_commands.Group | None = tree.get_command(parts[0])
    if current is None:
        return None
    for part in parts[1:]:
        if not isinstance(current, app_commands.Group):
            return None
        current = current.get_command(part)
        if current is None:
            return None
    return current


def apply_catalog_slash_localizations(tree: app_commands.CommandTree) -> int:
    """Apply description_localizations from slash.*.description catalog keys to registered commands."""
    reload_catalogs()
    applied = 0
    for key in sorted(catalog_keys("en")):
        if not key.startswith("slash.") or not key.endswith(".description"):
            continue
        rel = key[len("slash.") : -len(".description")]
        parts = [p for p in rel.split(".") if p]
        if not parts:
            continue
        target = _resolve_tree_command(tree, parts)
        if target is None:
            LOGGER.debug("Slash localization target not found for key %s", key)
            continue
        locs = slash_description_loc(key)
        if not locs:
            continue
        target.description_localizations = locs
        applied += 1
    return applied
