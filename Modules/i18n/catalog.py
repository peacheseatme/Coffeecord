"""Load locale string catalogs and resolve dotted keys."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

STRINGS_DIR = Path(__file__).resolve().parent / "strings"
SUPPORTED_LOCALES = frozenset({"en", "es", "pt", "ru"})
DEFAULT_LOCALE = "en"

LOGGER = logging.getLogger("coffeecord.i18n")
_CATALOGS: dict[str, dict[str, str]] = {}
_MISSING_LOGGED: set[str] = set()
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _flatten(data: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            full = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(value, full))
    elif isinstance(data, str):
        out[prefix] = data
    return out


def _load_locale_file(locale: str) -> dict[str, str]:
    path = STRINGS_DIR / f"{locale}.json"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to load locale %s: %s", locale, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return _flatten(raw)


def reload_catalogs() -> None:
    """Reload all locale catalogs from disk (for tests)."""
    _CATALOGS.clear()
    for locale in SUPPORTED_LOCALES:
        _CATALOGS[locale] = _load_locale_file(locale)


def _ensure_loaded() -> None:
    if not _CATALOGS:
        reload_catalogs()


def catalog_keys(locale: str = DEFAULT_LOCALE) -> set[str]:
    _ensure_loaded()
    return set(_CATALOGS.get(locale, {}).keys())


def all_catalog_key_sets() -> dict[str, set[str]]:
    _ensure_loaded()
    return {loc: set(catalog.keys()) for loc, catalog in _CATALOGS.items()}


def _substitute(template: str, params: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(params.get(name, match.group(0)))

    return _PLACEHOLDER_RE.sub(repl, template)


def lookup_string(locale: str, key: str, *, default: str | None = None) -> str:
    _ensure_loaded()
    loc = locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE
    chain = [loc]
    if loc != DEFAULT_LOCALE:
        chain.append(DEFAULT_LOCALE)
    for code in chain:
        value = _CATALOGS.get(code, {}).get(key)
        if isinstance(value, str) and value:
            return value
    if default is not None:
        return default
    if key not in _MISSING_LOGGED:
        _MISSING_LOGGED.add(key)
        LOGGER.warning("Missing i18n key: %s (locale=%s)", key, loc)
    return key


def translate_for_locale(locale: str, key: str, /, *, default: str | None = None, **params: str) -> str:
    template = lookup_string(locale, key, default=default)
    return _substitute(template, params) if params else template
