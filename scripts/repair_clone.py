#!/usr/bin/env python3
"""
Clone repair: by default restores missing *tracked* files from the remote ref, then
runs Storage placeholder repair (same as generate_storage_placeholders.py --repair).

Compare + download uses local git (git fetch + git checkout from origin/<branch>);
no GitHub API. Works with whatever host `origin` points to (typically GitHub).

Use --storage-only to skip git (JSON / bot_sys.cfg repair only). Not a git clone?
Git step is skipped with a notice; storage repair still runs.

Examples (equivalent to ./bot.sh repair …):
  (cd <repo> && python3 scripts/repair_clone.py)
  (cd <repo> && python3 scripts/repair_clone.py --dry-run)
  (cd <repo> && python3 scripts/repair_clone.py --storage-only)
  (cd <repo> && python3 scripts/repair_clone.py --no-storage)
  (cd <repo> && python3 scripts/repair_clone.py --from-remote --ref origin/main --remote upstream)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

CHECKOUT_BATCH_SIZE = 200
DEFAULT_REMOTE = "origin"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_SCRIPT = PROJECT_ROOT / "scripts" / "generate_storage_placeholders.py"


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _is_git_work_tree(root: Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd=root)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _ensure_git_repo(root: Path) -> None:
    if not _is_git_work_tree(root):
        print("error: not a git repository (need a clone with remotes).", file=sys.stderr)
        sys.exit(1)


def _ref_exists(root: Path, ref: str) -> bool:
    r = _git(["rev-parse", "--verify", "--quiet", ref], cwd=root)
    return r.returncode == 0


def _resolve_ref(root: Path, remote: str, explicit_ref: str | None) -> str:
    if explicit_ref:
        if not _ref_exists(root, explicit_ref):
            print(f"error: ref not found: {explicit_ref}", file=sys.stderr)
            sys.exit(1)
        return explicit_ref
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    head = r.stdout.strip() if r.returncode == 0 else ""
    if head and head != "HEAD":
        cand = f"{remote}/{head}"
        if _ref_exists(root, cand):
            return cand
    r = _git(["symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD"], cwd=root)
    if r.returncode == 0:
        sym = r.stdout.strip()
        prefix = f"refs/remotes/{remote}/"
        if sym.startswith(prefix):
            short = f"{remote}/{sym[len(prefix) :]}"
            if _ref_exists(root, short):
                return short
    for fallback in (f"{remote}/main", f"{remote}/master"):
        if _ref_exists(root, fallback):
            return fallback
    print(
        f"error: could not resolve a ref like {remote}/<branch> after fetch. "
        "Pass --ref origin/main (or set up upstream).",
        file=sys.stderr,
    )
    sys.exit(1)


def _list_tracked_blob_paths(root: Path, ref: str) -> list[str]:
    r = _git(["ls-tree", "-r", "--name-only", ref], cwd=root)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        print(f"error: git ls-tree failed: {msg}", file=sys.stderr)
        sys.exit(1)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _missing_tracked_files(root: Path, paths: list[str]) -> list[str]:
    missing: list[str] = []
    for rel in paths:
        if not (root / rel).is_file():
            missing.append(rel)
    return missing


def _fetch_remote(root: Path, remote: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[git] dry run: would git fetch {remote}", flush=True)
        return
    print(f"[git] fetching {remote}…", flush=True)
    r = _git(["fetch", remote], cwd=root)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        print(f"error: git fetch {remote} failed: {msg}", file=sys.stderr)
        sys.exit(1)


def _checkout_paths(root: Path, ref: str, paths: list[str], *, dry_run: bool) -> int:
    if not paths:
        return 0
    if dry_run:
        print(f"[git] dry run: would restore {len(paths)} missing tracked file(s) from {ref}", flush=True)
        for i, p in enumerate(paths[:20]):
            print(f"  - {p}", flush=True)
        if len(paths) > 20:
            print(f"  … and {len(paths) - 20} more", flush=True)
        return len(paths)
    restored = 0
    batch: list[str] = []
    for rel in paths:
        batch.append(rel)
        if len(batch) >= CHECKOUT_BATCH_SIZE:
            r = _git(["checkout", ref, "--", *batch], cwd=root)
            if r.returncode != 0:
                restored += _checkout_paths_one_by_one(root, ref, batch)
            else:
                restored += len(batch)
            batch = []
    if batch:
        r = _git(["checkout", ref, "--", *batch], cwd=root)
        if r.returncode != 0:
            restored += _checkout_paths_one_by_one(root, ref, batch)
        else:
            restored += len(batch)
    return restored


def _checkout_paths_one_by_one(root: Path, ref: str, paths: list[str]) -> int:
    ok = 0
    for rel in paths:
        r = _git(["checkout", ref, "--", rel], cwd=root)
        if r.returncode == 0:
            ok += 1
        else:
            msg = (r.stderr or r.stdout).strip()
            print(f"warning: could not restore {rel}: {msg}", file=sys.stderr)
    return ok


def _run_storage_repair(excludes: list[str], *, dry_run: bool) -> int:
    if not STORAGE_SCRIPT.is_file():
        print(f"error: missing {STORAGE_SCRIPT}", file=sys.stderr)
        sys.exit(1)
    cmd = [sys.executable, str(STORAGE_SCRIPT), "--repair"]
    if dry_run:
        cmd.append("--dry-run")
    for ex in excludes:
        if ex.strip():
            cmd.extend(["--exclude", ex])
    env = os.environ | {"PYTHONUNBUFFERED": "1"}
    return subprocess.call(cmd, cwd=PROJECT_ROOT, env=env)


def _sync_from_remote(
    *,
    remote: str,
    ref: str | None,
    no_fetch: bool,
    dry_run: bool,
) -> None:
    _ensure_git_repo(PROJECT_ROOT)
    if not no_fetch:
        _fetch_remote(PROJECT_ROOT, remote, dry_run=dry_run)
    resolved = _resolve_ref(PROJECT_ROOT, remote, ref)
    all_paths = _list_tracked_blob_paths(PROJECT_ROOT, resolved)
    missing = _missing_tracked_files(PROJECT_ROOT, all_paths)
    if not missing and not dry_run:
        print(f"[git] all {len(all_paths)} tracked files already present at {resolved}.", flush=True)
    elif not missing and dry_run:
        print(f"[git] dry run: all {len(all_paths)} tracked files already present at {resolved}.", flush=True)
    else:
        n = _checkout_paths(PROJECT_ROOT, resolved, missing, dry_run=dry_run)
        if not dry_run:
            print(f"[git] restored {n} missing tracked file(s) from {resolved}.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Default: git fetch (unless --no-fetch) + restore missing tracked files from the remote ref, "
            "then repair known Storage JSON and bot_sys.cfg. Use --storage-only to skip git."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./bot.sh repair                    # full: git + storage\n"
            "  ./bot.sh repair --storage-only     # JSON / bot_sys.cfg only\n"
            "  ./bot.sh repair --no-fetch         # git without network; then storage\n"
            "  ./bot.sh repair --no-storage       # git only\n"
            "  ./bot.sh repair --dry-run          # preview both steps"
        ),
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="No-op for CLI compatibility with generate_storage_placeholders.py --repair (storage repair runs unless --no-storage).",
    )
    parser.add_argument(
        "--storage-only",
        action="store_true",
        help="Skip git: only repair known Storage JSON and bot_sys.cfg (old default behavior)",
    )
    parser.add_argument(
        "--from-remote",
        action="store_true",
        help="Explicit opt-in (default is already on): git fetch + restore missing tracked files from remote ref",
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        metavar="NAME",
        help=f"Remote name for fetch (default: {DEFAULT_REMOTE})",
    )
    parser.add_argument(
        "--ref",
        default=None,
        metavar="REF",
        help="Explicit ref (e.g. origin/main). Default: origin/<current branch> or origin's default branch",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not git fetch (use already-updated remote-tracking refs)",
    )
    parser.add_argument(
        "--no-storage",
        action="store_true",
        help="After git restore: skip Storage JSON / bot_sys.cfg repair",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only for both git and storage steps",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="FILE",
        help="Basename passed through to storage repair (--exclude repeatable)",
    )
    args = parser.parse_args()

    if args.storage_only and args.no_storage:
        print("error: --storage-only and --no-storage cannot be used together.", file=sys.stderr)
        return 2

    run_git = not args.storage_only
    if args.storage_only:
        print("[repair] storage-only: known Storage JSON + bot_sys.cfg (no git)", flush=True)
    elif args.no_storage:
        if _is_git_work_tree(PROJECT_ROOT):
            print("[repair] git only: restore missing tracked files (no storage step)", flush=True)
        else:
            print(
                "[repair] warning: --no-storage but not a git clone — nothing to run.",
                file=sys.stderr,
                flush=True,
            )
            return 0
    elif run_git and _is_git_work_tree(PROJECT_ROOT):
        print("[repair] full: git (missing tracked files) + storage JSON / bot_sys.cfg", flush=True)
    elif run_git:
        print("[repair] not a git clone — running storage repair only.", flush=True)

    if run_git:
        if _is_git_work_tree(PROJECT_ROOT):
            _sync_from_remote(
                remote=args.remote.strip() or DEFAULT_REMOTE,
                ref=args.ref,
                no_fetch=args.no_fetch,
                dry_run=args.dry_run,
            )
        # Non–git tree: already announced above; storage repair still runs unless --no-storage.

    if args.no_storage:
        return 0

    return _run_storage_repair(list(args.exclude), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
