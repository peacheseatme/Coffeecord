#!/usr/bin/env bash
# Archive Storage/ to instance_data_backup.tgz in the project root (gitignored).
# Run from anywhere; paths are relative to the repo root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/instance_data_backup.tgz"
cd "${ROOT}"
if [[ ! -d Storage ]]; then
  echo "No Storage/ directory at ${ROOT}; nothing to back up." >&2
  exit 1
fi
tar -czf "${OUT}" Storage
echo "Wrote ${OUT} (keep this file private; do not commit)"
