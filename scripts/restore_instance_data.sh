#!/usr/bin/env bash
# Restore Storage/ from instance_data_backup.tgz (from backup_instance_data.sh).
# Default: moves aside the current Storage/ tree, then extracts a clean copy from the archive
# (extracting on top of a fresh install leaves placeholder files and looks "half restored").
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
ARCHIVE="${ROOT}/instance_data_backup.tgz"
MERGE=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --merge) MERGE=true; shift ;;
    --verbose|-v) VERBOSE=true; shift ;;
    --archive)
      shift
      if [[ $# -lt 1 || "$1" == -* ]]; then
        echo "Usage: $0 [--archive /path/to/backup.tgz] [--merge] [--verbose]" >&2
        exit 1
      fi
      ARCHIVE="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--archive PATH] [--merge] [--verbose]"
      echo "  (default)  instance_data_backup.tgz in repo root; renames Storage/ to Storage.bak.<time>."
      echo "  --archive F  Use this .tgz (default: ${ROOT}/instance_data_backup.tgz)"
      echo "  --merge      Extract on top of current Storage/ (may leave extra files)."
      echo "  --verbose    Pass -v to tar extract."
      echo "  If backup has no Src/.env, set DISCORD_APPLICATION_ID in Src/.env for top.gg/invite URLs."
      exit 0
      ;;
    *)
      echo "Unknown option: $1  (try --help)" >&2
      exit 1
      ;;
  esac
done

cd "${ROOT}"

if [[ ! -f "${ARCHIVE}" ]]; then
  echo "Missing archive: ${ARCHIVE}" >&2
  echo "  Put your .tgz in the repo root as instance_data_backup.tgz, or use:" >&2
  echo "  $0 --archive /path/to/your-backup.tgz" >&2
  exit 1
fi

# Strip CR (CRLF listings). Accept Storage/ or ./Storage/ (ERE: \. is a literal dot).
if ! tar -tzf "${ARCHIVE}" | tr -d '\r' | grep -qE '^(\./)?Storage/'; then
  echo "Archive does not contain a Storage/ tree (wrong file or corrupt tarball?)." >&2
  echo "Listing first 20 entries:" >&2
  tar -tzf "${ARCHIVE}" | tr -d '\r' | head -20 >&2
  exit 1
fi

LATEST_BAK=""
if [[ "${MERGE}" != true ]]; then
  if [[ -d Storage ]]; then
    LATEST_BAK="${ROOT}/Storage.bak.$(date +%Y%m%d%H%M%S)"
    echo "Moving current Storage/ to ${LATEST_BAK}"
    mv Storage "${LATEST_BAK}"
    echo "  (if restore fails, run:  mv \"${LATEST_BAK}\" Storage )"
  fi
fi

# If backup overwrites .env, keep a one-time copy of the current file.
if tar -tzf "${ARCHIVE}" | tr -d '\r' | grep -q '^Src/\.env$' && [[ -f Src/.env ]]; then
  cp -a Src/.env "Src/.env.before-restore.$(date +%Y%m%d%H%M%S)"
  echo "  (saved previous Src/.env as Src/.env.before-restore.*)"
fi

if [[ "${VERBOSE}" == true ]]; then
  TAR_X=(tar -xzvf "${ARCHIVE}" -C "${ROOT}")
else
  TAR_X=(tar -xzf "${ARCHIVE}" -C "${ROOT}")
fi

echo "Extracting ${ARCHIVE} into ${ROOT} ..."
if ! "${TAR_X[@]}"; then
  echo "ERROR: tar extract failed (disk full? permissions?)." >&2
  if [[ -n "${LATEST_BAK}" && -d "${LATEST_BAK}" ]]; then
    echo "  Your previous tree is at: ${LATEST_BAK}" >&2
    echo "  To undo:  rm -rf Storage 2>/dev/null; mv \"${LATEST_BAK}\" Storage" >&2
  fi
  exit 1
fi

if [[ ! -d "${ROOT}/Storage" ]]; then
  echo "ERROR: After extract, ${ROOT}/Storage is missing." >&2
  if [[ -n "${LATEST_BAK}" && -d "${LATEST_BAK}" ]]; then
    echo "  Restore manually:  mv \"${LATEST_BAK}\" Storage" >&2
  fi
  exit 1
fi

FILE_COUNT="$(find "${ROOT}/Storage" -type f 2>/dev/null | wc -l)"
FILE_COUNT="${FILE_COUNT//[[:space:]]/}"
echo "Restored from ${ARCHIVE}"
echo "  Storage/ now has ${FILE_COUNT} file(s) under ${ROOT}/Storage"
if [[ "${FILE_COUNT}" -eq 0 ]]; then
  echo "  WARNING: no files under Storage/ — this archive may be empty or paths may be wrong." >&2
  echo "  Inspect with:  tar -tzf \"${ARCHIVE}\" | head -50" >&2
fi

HAS_ENV=false
if tar -tzf "${ARCHIVE}" | tr -d '\r' | grep -q '^Src/\.env$'; then
  echo "  Restored Src/.env (token, DISCORD_APPLICATION_ID / top.gg link, owner ID, …)"
  HAS_ENV=true
fi
if tar -tzf "${ARCHIVE}" | tr -d '\r' | grep -q '^Src/ticket\.env$'; then
  echo "  Restored Src/ticket.env"
fi
if [[ "${HAS_ENV}" != true ]]; then
  echo ""
  echo "  Note: This backup has no Src/.env. /about top.gg + invite buttons need DISCORD_APPLICATION_ID"
  echo "  (and related vars) in Src/.env — see env.example. Old backups: re-run backup without --storage-only."
fi

echo ""
echo "Data was restored under: ${ROOT}"
echo "If c-cord points at a different folder, fix ~/.local/bin/c-cord or run install.sh from this repo, then: c-cord restart"
