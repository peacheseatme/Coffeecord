"""Tests for i18n catalogs and per-user language helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from Modules.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    all_catalog_key_sets,
    reload_catalogs,
    t_for_locale,
    translate_for_locale,
)
from Modules.i18n.catalog import _flatten
from Modules.i18n.user_locale import normalize_language_code

STRINGS_DIR = Path(__file__).resolve().parent.parent / "Modules" / "i18n" / "strings"


def _load_nested(locale: str) -> dict:
    with (STRINGS_DIR / f"{locale}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(autouse=True)
def _reload_i18n_catalogs() -> None:
    reload_catalogs()


def test_supported_locale_files_exist() -> None:
    for locale in SUPPORTED_LOCALES:
        assert (STRINGS_DIR / f"{locale}.json").is_file()


def test_catalog_key_parity_across_locales() -> None:
    key_sets = all_catalog_key_sets()
    en_keys = key_sets[DEFAULT_LOCALE]
    assert en_keys, "English catalog must not be empty"
    for locale in SUPPORTED_LOCALES:
        if locale == DEFAULT_LOCALE:
            continue
        assert key_sets[locale] == en_keys, f"{locale} keys differ from en"


def test_placeholder_substitution() -> None:
    text = t_for_locale("en", "language.set_success", language="English")
    assert "English" in text


def test_unknown_key_falls_back_to_default() -> None:
    result = translate_for_locale("es", "nonexistent.key.path", default="Fallback")
    assert result == "Fallback"


def test_normalize_language_codes() -> None:
    assert normalize_language_code("pt-BR") == "pt"
    assert normalize_language_code("es-419") == "es"
    assert normalize_language_code("ru") == "ru"
    assert normalize_language_code("de") == "en"


def test_flatten_nested_catalog() -> None:
    flat = _flatten({"common": {"ok": "Yes"}, "slash": {"help": {"description": "Help"}}})
    assert flat["common.ok"] == "Yes"
    assert flat["slash.help.description"] == "Help"


def test_nested_json_roundtrip_structure() -> None:
    for locale in SUPPORTED_LOCALES:
        nested = _load_nested(locale)
        flat = _flatten(nested)
        assert len(flat) >= 500
