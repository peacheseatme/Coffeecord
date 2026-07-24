#!/usr/bin/env python3
"""
Download a GitHub release archive and overlay it onto an install root.

Usage:
  ccord_release_apply.py <repo_root> --url <archive_url> [--tag <tag>]
  ccord_release_apply.py <repo_root> --json-line '<resolve --json payload>'

Never overwrites local/runtime paths (Storage/, .venv/, Src/.env, etc.).
Temp files live under Storage/Temp/ and are removed on success or failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

USER_AGENT = "coffeecord-c-cord-update"
ACCEPT_ARCHIVE = "application/vnd.github+json"

# Top-level names never replaced by a release overlay.
PRESERVE_TOP_LEVEL = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "Storage",
        "data",
        "instance_data_backup.tgz",
        ".cursor",
        ".vscode",
        ".pytest_cache",
        "journal",
        "journal.log",
        "journal.pub",
    }
)


def _is_preserved(rel_posix: str) -> bool:
    name = rel_posix.strip("/")
    if not name:
        return True
    top = name.split("/", 1)[0]
    if top in PRESERVE_TOP_LEVEL or top.startswith("Storage.bak."):
        return True
    base = Path(name).name
    # Secrets / local env overrides (gitignore treats *.env as secret)
    if base.endswith(".env") or base.startswith(".env"):
        return True
    return False


def _download(url: str, dest: Path) -> None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": ACCEPT_ARCHIVE,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "objects.githubusercontent.com" in url or "/releases/download/" in url:
        headers["Accept"] = "application/octet-stream"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300] if e.fp else ""
        raise RuntimeError(f"Download failed HTTP {e.code}: {body or e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Download failed: {e}") from e


def _extract(archive: Path, dest_dir: Path) -> Path:
    """Extract archive into dest_dir; return the single top-level source directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()

    def _unzip() -> None:
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest_dir)

    def _untar(mode: str = "r:*") -> None:
        with tarfile.open(archive, mode) as tf:
            tf.extractall(dest_dir, filter=tarfile.data_filter)

    if name.endswith(".zip"):
        _unzip()
    elif name.endswith((".tar.gz", ".tgz", ".tar")):
        _untar()
    else:
        # GitHub zipball responses often lack a useful suffix; sniff magic.
        raw = archive.read_bytes()[:4]
        if raw[:2] == b"PK":
            _unzip()
        elif raw[:2] == b"\x1f\x8b":
            _untar("r:gz")
        else:
            raise RuntimeError(f"Unrecognized archive format: {archive.name}")

    children = [p for p in dest_dir.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest_dir


def _overlay(src_root: Path, install_root: Path) -> tuple[int, int]:
    """Copy files from src_root onto install_root, skipping preserved paths."""
    copied = 0
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(src_root):
        rel_dir = Path(dirpath).relative_to(src_root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        keep_dirs: list[str] = []
        for d in dirnames:
            child_rel = f"{rel_dir}/{d}" if rel_dir else d
            if _is_preserved(child_rel):
                skipped += 1
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for fname in filenames:
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            if _is_preserved(rel):
                skipped += 1
                continue
            src = Path(dirpath) / fname
            dest = install_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest, follow_symlinks=False)
            copied += 1
    return copied, skipped


def _archive_dest(work: Path, url: str) -> Path:
    if url.rstrip("/").endswith(".tar.gz") or "tarball" in url:
        return work / "release.tar.gz"
    return work / "release.zip"


def apply_archive(install_root: Path, urls: list[str], *, tag: str = "") -> int:
    install_root = install_root.resolve()
    if not install_root.is_dir():
        print(f"Not a directory: {install_root}", file=sys.stderr)
        return 1

    candidates = [u for u in urls if u]
    if not candidates:
        print("No archive URL to download.", file=sys.stderr)
        return 1

    temp_base = install_root / "Storage" / "Temp"
    temp_base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ccord-update-", dir=str(temp_base)))
    try:
        label = tag or "release"
        last_error: Exception | None = None
        archive_path: Path | None = None
        for url in candidates:
            try:
                print(f"Downloading release archive ({label})...", file=sys.stderr)
                dest = _archive_dest(work, url)
                _download(url, dest)
                if dest.stat().st_size < 64:
                    raise RuntimeError("Downloaded archive is empty or too small.")
                archive_path = dest
                break
            except Exception as e:
                last_error = e
                print(f"Archive download failed ({e}); trying next URL if any.", file=sys.stderr)
        if archive_path is None:
            print(f"Release archive apply failed: {last_error}", file=sys.stderr)
            return 1

        print("Extracting and overlaying (preserving Storage/, .venv/, secrets)...", file=sys.stderr)
        src_root = _extract(archive_path, work / "extract")
        copied, skipped = _overlay(src_root, install_root)
        print(f"Overlay complete: {copied} files updated, {skipped} preserved/skipped.", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Release archive apply failed: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a GitHub release archive onto an install root.")
    parser.add_argument("repo_root", type=Path, help="Install root to overlay")
    parser.add_argument("--url", default="", help="Archive download URL (zipball/tarball/asset)")
    parser.add_argument("--tag", default="", help="Tag label for logs")
    parser.add_argument(
        "--json-line",
        default="",
        help="JSON object from ccord_release_resolve.py --json",
    )
    args = parser.parse_args(argv)

    urls: list[str] = []
    tag = args.tag.strip()
    if args.json_line.strip():
        try:
            payload = json.loads(args.json_line)
        except json.JSONDecodeError as e:
            print(f"Invalid --json-line: {e}", file=sys.stderr)
            return 1
        if not isinstance(payload, dict):
            print("--json-line must be a JSON object.", file=sys.stderr)
            return 1
        tag = str(payload.get("tag") or tag)
        for key in ("preferred_archive_url", "zipball_url", "tarball_url", "asset_url"):
            val = str(payload.get(key) or "").strip()
            if val and val not in urls:
                urls.append(val)
    if args.url.strip() and args.url.strip() not in urls:
        urls.insert(0, args.url.strip())

    if not urls:
        print("Need --url or --json-line with an archive URL.", file=sys.stderr)
        return 1

    return apply_archive(args.repo_root, urls, tag=tag)


if __name__ == "__main__":
    sys.exit(main())
