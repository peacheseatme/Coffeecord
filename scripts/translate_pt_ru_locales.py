#!/usr/bin/env python3
"""Fill Portuguese and Russian locale strings that still match English."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from deep_translator import GoogleTranslator

STRINGS_DIR = Path(__file__).resolve().parent.parent / "Modules" / "i18n" / "strings"

SKIP_EXACT: set[str] = {
    "English",
    "Español",
    "Português",
    "Русский",
    "Ko-fi",
    "Coffeecord",
    "ON",
    "OFF",
    "—",
}

SKIP_KEY_SUFFIXES = (
    ".choice_en",
    ".choice_es",
    ".choice_pt",
    ".choice_ru",
)

PH_RE = re.compile(
    r"\{[^{}]+\}|"
    r"<t:\{[^{}]+\}:[^>]+>|"
    r"\*\*[^*]+\*\*|"
    r"`[^`]+`|"
    r"/[\w-]+(?: [\w-]+)*|"
    r"https?://\S+|"
    r"<@[!&]?\d+>|"
    r"<#\d+>|"
    r"<@&\d+>"
)


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        elif isinstance(v, str):
            out[key] = v
    return out


def unflatten(flat: dict[str, str]) -> dict:
    root: dict = {}
    for key in sorted(flat):
        parts = key.split(".")
        cur = root
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = flat[key]
    return root


def should_skip(key: str, value: str) -> bool:
    if any(key.endswith(s) for s in SKIP_KEY_SUFFIXES):
        return True
    if value.strip() in SKIP_EXACT:
        return True
    if not value.strip():
        return True
    return False


def protect(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"ZZX{len(tokens) - 1}ZZX"

    return PH_RE.sub(repl, text), tokens


def restore(text: str, tokens: list[str]) -> str:
    for i, tok in enumerate(tokens):
        text = text.replace(f"ZZX{i}ZZX", tok)
    return text


def fill_locale(locale: str, target: str) -> int:
    en_flat = flatten(json.loads((STRINGS_DIR / "en.json").read_text(encoding="utf-8")))
    loc_path = STRINGS_DIR / f"{locale}.json"
    loc_flat = flatten(json.loads(loc_path.read_text(encoding="utf-8")))
    translator = GoogleTranslator(source="en", target=target)

    pending: list[tuple[str, str, list[str]]] = []
    for key, en_val in en_flat.items():
        if loc_flat.get(key, en_val) != en_val:
            continue
        if should_skip(key, en_val):
            continue
        protected, tokens = protect(en_val)
        pending.append((key, protected, tokens))

    total = len(pending)
    print(f"{locale}: translating {total} keys…", flush=True)
    updated = 0

    for idx, (key, protected, tokens) in enumerate(pending, 1):
        try:
            raw = translator.translate(protected)
        except Exception as exc:
            print(f"  skip {key}: {exc}", flush=True)
            time.sleep(0.5)
            continue
        final = restore(raw, tokens)
        if final and final != en_flat[key]:
            loc_flat[key] = final
            updated += 1
        if idx % 25 == 0 or idx == total:
            print(f"  {locale}: {idx}/{total} ({updated} updated)", flush=True)
            loc_path.write_text(
                json.dumps(unflatten(loc_flat), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        time.sleep(0.08)

    loc_path.write_text(
        json.dumps(unflatten(loc_flat), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{locale}: done — updated {updated}/{total}", flush=True)
    return updated


def main() -> None:
    locales = sys.argv[1:] or ["pt", "ru"]
    for loc in locales:
        target = "pt" if loc == "pt" else "ru"
        fill_locale(loc, target)


if __name__ == "__main__":
    main()
