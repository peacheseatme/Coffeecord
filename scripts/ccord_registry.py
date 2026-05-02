#!/usr/bin/env python3
"""
c-cord multi-instance registry (~/.config/c-cord/instances.json).

Used by ccord_dispatch.sh and install.sh (add-self).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REGISTRY_DIR = Path.home() / ".config" / "c-cord"
REGISTRY_PATH = REGISTRY_DIR / "instances.json"
DEFAULT_FIRST_ID = "1"


def _ensure_dir() -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if not REGISTRY_PATH.is_file():
        return {"default": DEFAULT_FIRST_ID, "instances": {}}
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(
                f"warning: {REGISTRY_PATH} is not a JSON object — fix the file.",
                file=sys.stderr,
            )
            return {"default": DEFAULT_FIRST_ID, "instances": {}}
        inst = data.get("instances")
        if not isinstance(inst, dict):
            data["instances"] = {}
        if not str(data.get("default") or "").strip():
            data["default"] = DEFAULT_FIRST_ID
        return data
    except json.JSONDecodeError as e:
        print(
            f"warning: {REGISTRY_PATH} is invalid JSON ({e}) — repair or replace it.",
            file=sys.stderr,
        )
        return {"default": DEFAULT_FIRST_ID, "instances": {}}
    except OSError as e:
        print(f"warning: cannot read {REGISTRY_PATH}: {e}", file=sys.stderr)
        return {"default": DEFAULT_FIRST_ID, "instances": {}}


def _save(data: dict) -> None:
    _ensure_dir()
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=True)
        tmp.replace(REGISTRY_PATH)
    except OSError as e:
        print(f"error: cannot write registry {REGISTRY_PATH}: {e}", file=sys.stderr)
        raise SystemExit(1) from e


def _real(root: str) -> str:
    return str(Path(root).expanduser().resolve())


def _has_bot_sh(root: str) -> bool:
    p = Path(root) / "bot.sh"
    return p.is_file() and os.access(p, os.X_OK)


def cmd_list(_argv: list[str]) -> int:
    data = _load()
    default_id = str(data.get("default") or DEFAULT_FIRST_ID)
    instances: dict = data.get("instances") or {}
    if not instances:
        print("(no instances registered)")
        return 0
    print(f"Registry: {REGISTRY_PATH}")
    print(f"Default instance id: {default_id}")
    for iid in sorted(instances.keys(), key=lambda x: (len(x), x)):
        entry = instances.get(iid) or {}
        name = str(entry.get("name") or "")
        root = str(entry.get("root") or "")
        pid = ""
        temp = Path(root) / "Storage" / "Temp" / "bot.pid"
        if temp.is_file():
            try:
                raw = temp.read_text(encoding="utf-8").strip().splitlines()
                pid = raw[0] if raw else ""
            except OSError:
                pid = ""
        mark = "*" if iid == default_id else " "
        print(f" {mark} [{iid}] name={name!r} root={root} pid={pid or '-'}")
    return 0


def cmd_add_self(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: ccord_registry.py add-self <project_root>", file=sys.stderr)
        return 1
    root = _real(argv[0])
    if not _has_bot_sh(root):
        print(f"error: not a Coffeecord root (missing bot.sh): {root}", file=sys.stderr)
        return 1
    data = _load()
    instances: dict[str, dict] = data.setdefault("instances", {})
    for iid, entry in instances.items():
        if _real(str(entry.get("root", ""))) == root:
            print(f"already registered as [{iid}]")
            return 0
    if not instances:
        iid = DEFAULT_FIRST_ID
        data["default"] = DEFAULT_FIRST_ID
        instances[iid] = {"name": "default", "root": root}
        _save(data)
        print(f"registered [{iid}] (default) -> {root}")
        return 0
    numeric = []
    for k in instances:
        if str(k).isdigit():
            numeric.append(int(k))
    next_id = str(max(numeric) + 1) if numeric else "2"
    while next_id in instances:
        next_id = str(int(next_id) + 1)
    instances[next_id] = {"name": f"instance-{next_id}", "root": root}
    _save(data)
    print(f"registered [{next_id}] -> {root}")
    return 0


def cmd_add(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: ccord_registry.py add <id> <project_root>", file=sys.stderr)
        return 1
    iid, root = argv[0], _real(argv[1])
    if not _has_bot_sh(root):
        print(f"error: not a Coffeecord root (missing bot.sh): {root}", file=sys.stderr)
        return 1
    data = _load()
    instances: dict[str, dict] = data.setdefault("instances", {})
    if iid in instances:
        print(f"error: id [{iid}] already exists", file=sys.stderr)
        return 1
    instances[iid] = {"name": str(iid), "root": root}
    if not data.get("default") or data["default"] not in instances:
        data["default"] = iid
    _save(data)
    print(f"added [{iid}] -> {root}")
    return 0


def cmd_remove(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: ccord_registry.py remove <id>", file=sys.stderr)
        return 1
    iid = argv[0]
    data = _load()
    instances: dict = data.get("instances") or {}
    if iid not in instances:
        print(f"error: unknown id [{iid}]", file=sys.stderr)
        return 1
    del instances[iid]
    if str(data.get("default")) == iid:
        data["default"] = next(iter(sorted(instances.keys(), key=lambda x: (len(x), x))), "") or DEFAULT_FIRST_ID
        if data["default"] not in instances and instances:
            data["default"] = next(iter(instances))
        elif not instances:
            data["default"] = DEFAULT_FIRST_ID
    _save(data)
    print(f"removed [{iid}]")
    return 0


def cmd_default(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: ccord_registry.py default <id>", file=sys.stderr)
        return 1
    iid = argv[0]
    data = _load()
    instances: dict = data.get("instances") or {}
    if iid not in instances:
        print(f"error: unknown id [{iid}]", file=sys.stderr)
        return 1
    data["default"] = iid
    _save(data)
    print(f"default instance is now [{iid}]")
    return 0


def cmd_has_id(argv: list[str]) -> int:
    if not argv:
        return 1
    data = _load()
    return 0 if argv[0] in (data.get("instances") or {}) else 1


def cmd_resolve(argv: list[str]) -> int:
    spec = (argv[0] if argv else "").strip() or "default"
    data = _load()
    instances: dict = data.get("instances") or {}
    if spec == "all":
        if not instances:
            print("error: no instances registered", file=sys.stderr)
            return 1
        printed = 0
        for _iid, entry in sorted(instances.items(), key=lambda kv: (len(kv[0]), kv[0])):
            r = str((entry or {}).get("root") or "").strip()
            if r and _has_bot_sh(r):
                print(r)
                printed += 1
        if printed == 0:
            print("error: no valid instance roots in registry", file=sys.stderr)
            return 1
        return 0
    if spec in ("default", ""):
        did = str(data.get("default") or DEFAULT_FIRST_ID)
        entry = instances.get(did)
        if not entry and instances:
            did = sorted(instances.keys(), key=lambda x: (len(x), x))[0]
            entry = instances.get(did)
        if not entry:
            print("error: no default instance or registry empty", file=sys.stderr)
            return 1
        root = str(entry.get("root") or "").strip()
        if not root or not _has_bot_sh(root):
            print(f"error: invalid root for [{did}]", file=sys.stderr)
            return 1
        print(root)
        return 0
    entry = instances.get(spec)
    if not entry:
        print(f"error: unknown instance id [{spec}]", file=sys.stderr)
        return 1
    root = str(entry.get("root") or "").strip()
    if not root or not _has_bot_sh(root):
        print(f"error: invalid root for [{spec}]", file=sys.stderr)
        return 1
    print(root)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: ccord_registry.py <list|add-self|add|remove|default|resolve> [args...]",
            file=sys.stderr,
        )
        return 1
    cmd = sys.argv[1]
    argv = sys.argv[2:]
    if cmd == "list":
        return cmd_list(argv)
    if cmd == "add-self":
        return cmd_add_self(argv)
    if cmd == "add":
        return cmd_add(argv)
    if cmd == "remove":
        return cmd_remove(argv)
    if cmd == "default":
        return cmd_default(argv)
    if cmd == "resolve":
        return cmd_resolve(argv)
    if cmd == "has-id":
        return cmd_has_id(argv)
    print(f"error: unknown command {cmd!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
