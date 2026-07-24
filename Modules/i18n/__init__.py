"""Internationalization for Coffeecord bot messages."""
from __future__ import annotations

from .catalog import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    all_catalog_key_sets,
    catalog_keys,
    lookup_string,
    reload_catalogs,
    translate_for_locale,
)
from .user_locale import (
    USER_LANGUAGE_PATH,
    get_user_language,
    get_user_language_sync,
    map_discord_preferred_locale,
    normalize_language_code,
    set_user_language,
)
from .slash import apply_catalog_slash_localizations, slash_description_loc, slash_name_loc

_USER_LANG_CACHE: dict[int, str] = {}


async def t(user_id: int | None, key: str, /, *, default: str | None = None, **params: str) -> str:
    """Translate a catalog key for a user's configured language."""
    locale = DEFAULT_LOCALE
    if user_id:
        locale = await resolve_user_language(int(user_id))
    return translate_for_locale(locale, key, default=default, **params)


def t_for_locale(locale: str, key: str, /, *, default: str | None = None, **params: str) -> str:
    return translate_for_locale(normalize_language_code(locale), key, default=default, **params)


async def resolve_user_language(user_id: int) -> str:
    cached = _USER_LANG_CACHE.get(user_id)
    if cached is not None:
        return cached
    lang = await get_user_language(user_id)
    _USER_LANG_CACHE[user_id] = lang
    return lang


def t_sync(user_id: int | None, key: str, /, *, default: str | None = None, **params: str) -> str:
    locale = DEFAULT_LOCALE
    if user_id:
        cached = _USER_LANG_CACHE.get(int(user_id))
        locale = cached if cached is not None else get_user_language_sync(int(user_id))
        _USER_LANG_CACHE[int(user_id)] = locale
    return translate_for_locale(locale, key, default=default, **params)


def invalidate_user_language_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _USER_LANG_CACHE.clear()
    else:
        _USER_LANG_CACHE.pop(int(user_id), None)


__all__ = [
    "DEFAULT_LOCALE",
    "SUPPORTED_LOCALES",
    "USER_LANGUAGE_PATH",
    "all_catalog_key_sets",
    "apply_catalog_slash_localizations",
    "catalog_keys",
    "get_user_language",
    "get_user_language_sync",
    "invalidate_user_language_cache",
    "lookup_string",
    "map_discord_preferred_locale",
    "normalize_language_code",
    "reload_catalogs",
    "resolve_user_language",
    "set_user_language",
    "slash_description_loc",
    "slash_name_loc",
    "t",
    "t_sync",
    "t_for_locale",
    "translate_for_locale",
]
