#!/usr/bin/env bash
# Archive Storage/ and (by default) Src/.env + Src/ticket.env to instance_data_backup.tgz.
# top.gg, OAuth invite, owner ID, and Ko-fi settings live in .env — Storage/ alone is not enough.
# Run from repo root context; tarball path is gitignored. Keep copies private.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/instance_data_backup.tgz"
STORAGE_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --storage-only) STORAGE_ONLY=true ;;
    --include-env)
      : # Deprecated: .env is included by default; flag kept for old scripts/docs.
      ;;
    -h|--help)
      echo "Usage: $0 [--storage-only]"
      echo "  (default)    Storage/ plus Src/.env and Src/ticket.env if they exist (secrets in tarball)."
      echo "  --storage-only   Only Storage/ (safe to share; you must restore .env yourself)."
      exit 0
      ;;
  esac
done

cd "${ROOT}"
if [[ ! -d Storage ]]; then
  echo "No Storage/ directory at ${ROOT}; nothing to back up." >&2
  exit 1
fi

TMP_LIST="$(mktemp)"
trap 'rm -f "${TMP_LIST}"' EXIT

{
  printf '%s\n' Storage
  if [[ "${STORAGE_ONLY}" != true ]]; then
    [[ -f Src/.env ]] && printf '%s\n' Src/.env
    [[ -f Src/ticket.env ]] && printf '%s\n' Src/ticket.env
  fi
} > "${TMP_LIST}"

tar -C "${ROOT}" -czf "${OUT}" -T "${TMP_LIST}"

ENTRIES="$(tar -tzf "${OUT}" | wc -l)"
echo "Wrote ${OUT}  (${ENTRIES} path entries)"
echo "  Verify: tar -tzf ${OUT} | head -25"
if [[ "${STORAGE_ONLY}" == true ]]; then
  echo "  [storage-only] Re-add DISCORD_APPLICATION_ID etc. in Src/.env after restore (top.gg / invite links)."
else
  echo "  WARNING: archive may contain DISCORD_TOKEN and other secrets — do not commit or publish."
fi
