#!/usr/bin/env python3
"""
Resolve a GitHub release tag for c-cord update.

Usage:
  ccord_release_resolve.py <repo_root> [version]

  version: optional; if omitted, use latest published release from the API.
  GITHUB_REPO=owner/repo  optional override (from c-cord.json).
  GITHUB_TOKEN            optional; raises API rate limits and helps private repos.

  With an explicit version, GitHub Releases API is tried first, then
  git ls-remote on origin (so downgrades to older tags work even when the tag
  was never a GitHub "release" asset).

On API failure for "latest", falls back to highest local semver-like tag (run
git fetch --tags first from the caller).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_API = "https://api.github.com"
ACCEPT_HEADER = "application/vnd.github+json"


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


def _repo_from_git_root(root: Path) -> tuple[str, str]:
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
            "Could not read git remote origin. Set GITHUB_REPO=owner/repo in c-cord.json.",
            file=sys.stderr,
        )
        sys.exit(1)
    parsed = _parse_github_remote(r.stdout)
    if not parsed:
        print(
            f"Origin URL is not a github.com remote: {r.stdout.strip()!r}. "
            "Set GITHUB_REPO=owner/repo in Storage/Config/c-cord.json.",
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


def _resolve_specific_release(root: Path, owner: str, repo: str, user_version: str) -> str:
    for tag in _tag_candidates(user_version):
        path = f"/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}"
        status, data, _ = _api_request(path)
        if status == 200 and isinstance(data, dict) and data.get("tag_name"):
            return str(data["tag_name"])
    origin_tag = _resolve_tag_on_origin(root, user_version)
    if origin_tag:
        print(
            f"Note: {origin_tag!r} resolved from origin tags (not a GitHub Release page).",
            file=sys.stderr,
        )
        return origin_tag
    print(
        f"No GitHub release or origin tag found for {user_version!r} (tried tag variants).",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_latest_api(owner: str, repo: str) -> str | None:
    status, data, _ = _api_request(f"/repos/{owner}/{repo}/releases/latest")
    if status == 200 and isinstance(data, dict) and data.get("tag_name"):
        return str(data["tag_name"])
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


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: ccord_release_resolve.py <repo_root> [version]", file=sys.stderr)
        return 1
    root = Path(sys.argv[1]).resolve()
    user_version = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    owner, repo = _repo_from_git_root(root)

    if user_version:
        tag = _resolve_specific_release(root, owner, repo, user_version)
        print(tag)
        return 0

    tag = _resolve_latest_api(owner, repo)
    if tag:
        print(tag)
        return 0

    print("Falling back to latest local semver tag (git fetch --tags recommended).", file=sys.stderr)
    local = _local_latest_semver_tag(root)
    if not local:
        print("No local semver tag found and GitHub latest release could not be resolved.", file=sys.stderr)
        return 1
    print(local)
    return 0


if __name__ == "__main__":
    sys.exit(main())
