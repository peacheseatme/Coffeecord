#!/usr/bin/env python3
"""Output c-cord config as shell variable assignments for eval in bot.sh."""
from __future__ import annotations

import json
import os
import sys

CONFIG_PATH = os.environ.get("CC_CONFIG_FILE", "")
ROOT = os.environ.get("CC_SCRIPT_DIR", "")


def resolve(path: str | None, default: str) -> str:
    if not path:
        return os.path.join(ROOT, default) if ROOT else default
    if os.path.isabs(path):
        return path
    return os.path.join(ROOT, path) if ROOT else path


def main() -> int:
    if not CONFIG_PATH or not os.path.isfile(CONFIG_PATH):
        return 1
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 1

    path_defaults = {
        "bot_entry": "Src/Bot.py",
        "env_file": "Src/.env",
        "log_dir": "Storage/Logs",
        "temp_dir": "Storage/Temp",
        "ticket_env_file": "Src/ticket.env",
    }
    for cfg_key, default in path_defaults.items():
        val = d.get(cfg_key, default)
        if isinstance(val, str):
            resolved = resolve(val, default)
            var_name = cfg_key.upper()
            safe = resolved.replace("\\", "\\\\").replace('"', '\\"')
            print(f'{var_name}="{safe}"')

    if "max_log_bytes" in d and isinstance(d["max_log_bytes"], (int, float)):
        print(f'MAX_LOG_BYTES={int(d["max_log_bytes"])}')
    if "max_rotated" in d and isinstance(d["max_rotated"], (int, float)):
        print(f'MAX_ROTATED={int(d["max_rotated"])}')

    gr = d.get("github_repo", "")
    if isinstance(gr, str) and gr.strip():
        safe_repo = gr.strip().replace("\\", "\\\\").replace('"', '\\"')
        print(f'GITHUB_REPO="{safe_repo}"')

    return 0


if __name__ == "__main__":
    sys.exit(main())
