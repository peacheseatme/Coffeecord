#!/usr/bin/env python3
"""
Resolve a GitHub release tag (and archive URLs) for c-cord update.

Usage:
  ccord_release_resolve.py <repo_root> [version]
  ccord_release_resolve.py <repo_root> [version] --json

  version: optional; if omitted, use latest published release from the API.
  GITHUB_REPO=owner/repo  optional override (from c-cord.json).
  GITHUB_TOKEN            optional; raises API rate limits and helps private repos.

  Default stdout: tag name only (one line).
  --json stdout: stable schema with tag + archive URLs (see SCHEMA_VERSION).

  Prefer GitHub zipball/tarball (auto-created for release tags). Optional uploaded
  asset named exactly "coffeecord-release.zip" is reported as asset_url when present.

  With an explicit version, GitHub Releases API is tried first, then
  git ls-remote on origin (so downgrades to older tags work even when the tag
  was never a GitHub "release" asset). Archive URLs are still constructed for
  any resolved tag.

On API failure for "latest", falls back to highest local semver-like tag (run
git fetch --tags first from the caller).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

GITHUB_API = "https://api.github.com"
ACCEPT_HEADER = "application/vnd.github+json"
SCHEMA_VERSION = 1
OPTIONAL_ASSET_NAME = "coffeecord-release.zip"


def _parse_github_remote(url: str) -> tuple[str, str] | None:
    url = url.strip()
    if not url:
        return None
    m = re.match(
        r"^(?:https?://github\.com/|git@github\.com:)([^/]+)/([^/.]+?)(?:\.git)?/?$",
        url,
        re.I,
    )
    if not m:
        return None
    return m.group(1), m.group(2)


def _repo_from_root(root: Path) -> tuple[str, str]:
    env_repo = os.environ.get("GITHUB_REPO", "").strip()
    if env_repo:
        parts = env_repo.replace(" ", "").split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0], parts[1]
        print("Invalid GITHUB_REPO (expected owner/repo).", file=sys.stderr)
        sys.exit(1)
    r = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        print(
            "Could not read git remote origin. Set github_repo (owner/repo) in "
            "Storage/Config/c-cord.json or GITHUB_REPO in the environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    parsed = _parse_github_remote(r.stdout)
    if not parsed:
        print(
            f"Origin URL is not a github.com remote: {r.stdout.strip()!r}. "
            "Set github_repo=owner/repo in Storage/Config/c-cord.json.",
            file=sys.stderr,
        )
        sys.exit(1)
    return parsed


def _api_request(path: str) -> tuple[int, dict | list | None, str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    url = f"{GITHUB_API}{path}"
    req = urllib.request.Request(url, headers={"Accept": ACCEPT_HEADER, "User-Agent": "coffeecord-c-cord-update"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            if not body:
                return resp.status, None, ""
            return resp.status, json.loads(body), body
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        try:
            data = json.loads(body) if body else None
        except json.JSONDecodeError:
            data = None
        return e.code, data, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"GitHub API request failed: {e}", file=sys.stderr)
        return -1, None, str(e)


def _tag_candidates(user_version: str) -> list[str]:
    v = user_version.strip()
    if not v:
        return []
    out = [v]
    if not v.startswith("v"):
        out.append(f"v{v}")
    else:
        out.append(v[1:])
    # de-dup preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _archive_urls(owner: str, repo: str, tag: str) -> tuple[str, str]:
    quoted = urllib.parse.quote(tag, safe="")
    zipball = f"{GITHUB_API}/repos/{owner}/{repo}/zipball/{quoted}"
    tarball = f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{quoted}"
    return zipball, tarball


def _asset_url_from_release(data: dict[str, Any]) -> str | None:
    assets = data.get("assets")
    if not isinstance(assets, list):
        return None
    want = OPTIONAL_ASSET_NAME.lower()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if name.lower() == want and url:
            return url
    return None


def _payload(
    *,
    tag: str,
    owner: str,
    repo: str,
    source: str,
    zipball_url: str | None = None,
    tarball_url: str | None = None,
    asset_url: str | None = None,
) -> dict[str, Any]:
    z_default, t_default = _archive_urls(owner, repo, tag)
    return {
        "schema_version": SCHEMA_VERSION,
        "tag": tag,
        "owner": owner,
        "repo": repo,
        "source": source,
        "zipball_url": zipball_url or z_default,
        "tarball_url": tarball_url or t_default,
        "asset_url": asset_url,
        "preferred_archive_url": (zipball_url or z_default),
    }


def _resolve_tag_on_origin(root: Path, user_version: str) -> str | None:
    """Return a tag name that exists on origin, or None (supports downgrade / tag-only releases)."""
    for tag in _tag_candidates(user_version):
        r = subprocess.run(
            ["git", "-C", str(root), "ls-remote", "-q", "origin", f"refs/tags/{tag}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            continue
        if (r.stdout or "").strip():
            return tag
    return None


def _release_payload_from_api(owner: str, repo: str, data: dict[str, Any], source: str) -> dict[str, Any]:
    tag = str(data["tag_name"])
    zipball = str(data.get("zipball_url") or "") or None
    tarball = str(data.get("tarball_url") or "") or None
    return _payload(
        tag=tag,
        owner=owner,
        repo=repo,
        source=source,
        zipball_url=zipball,
        tarball_url=tarball,
        asset_url=_asset_url_from_release(data),
    )


def _resolve_specific_release(root: Path, owner: str, repo: str, user_version: str) -> dict[str, Any]:
    for tag in _tag_candidates(user_version):
        path = f"/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}"
        status, data, _ = _api_request(path)
        if status == 200 and isinstance(data, dict) and data.get("tag_name"):
            return _release_payload_from_api(owner, repo, data, "release")
    origin_tag = _resolve_tag_on_origin(root, user_version)
    if origin_tag:
        print(
            f"Note: {origin_tag!r} resolved from origin tags (not a GitHub Release page).",
            file=sys.stderr,
        )
        return _payload(tag=origin_tag, owner=owner, repo=repo, source="origin_tag")
    print(
        f"No GitHub release or origin tag found for {user_version!r} (tried tag variants).",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_latest_api(owner: str, repo: str) -> dict[str, Any] | None:
    status, data, _ = _api_request(f"/repos/{owner}/{repo}/releases/latest")
    if status == 200 and isinstance(data, dict) and data.get("tag_name"):
        return _release_payload_from_api(owner, repo, data, "release")
    if isinstance(data, dict) and data.get("message"):
        print(f"GitHub API: {data.get('message')}", file=sys.stderr)
    elif status > 0:
        print(f"GitHub API returned HTTP {status} for releases/latest.", file=sys.stderr)
    return None


def _semver_tuple(tag: str) -> tuple[int, ...] | None:
    s = tag[1:] if tag.startswith("v") else tag
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return None
    return tuple(int(m.group(i)) for i in range(1, 4))


def _local_latest_semver_tag(root: Path) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(root), "tag", "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    tags = []
    for line in r.stdout.splitlines():
        t = line.strip()
        if t and _semver_tuple(t) is not None:
            tags.append(t)
    if not tags:
        return None
    return max(tags, key=lambda t: _semver_tuple(t) or ())


def resolve(root: Path, user_version: str = "") -> dict[str, Any]:
    owner, repo = _repo_from_root(root)

    if user_version:
        return _resolve_specific_release(root, owner, repo, user_version)

    payload = _resolve_latest_api(owner, repo)
    if payload:
        return payload

    print("Falling back to latest local semver tag (git fetch --tags recommended).", file=sys.stderr)
    local = _local_latest_semver_tag(root)
    if not local:
        print("No local semver tag found and GitHub latest release could not be resolved.", file=sys.stderr)
        sys.exit(1)
    return _payload(tag=local, owner=owner, repo=repo, source="local_tag")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve GitHub release tag for c-cord update.")
    parser.add_argument("repo_root", type=Path, help="Install / clone root")
    parser.add_argument("version", nargs="?", default="", help="Optional pinned version / tag")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON with tag + archive URLs (schema_version=%s)" % SCHEMA_VERSION,
    )
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    try:
        payload = resolve(root, args.version.strip() if args.version else "")
    except SystemExit as e:
        code = e.code
        return int(code) if isinstance(code, int) else 1

    if args.json:
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(payload["tag"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
