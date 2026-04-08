#!/usr/bin/env bash
# Restore Storage/ from instance_data_backup.tgz created by backup_instance_data.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${ROOT}/instance_data_backup.tgz"
cd "${ROOT}"
if [[ ! -f "${ARCHIVE}" ]]; then
  echo "Missing ${ARCHIVE}" >&2
  exit 1
fi
tar -xzf "${ARCHIVE}"
echo "Restored Storage/ from ${ARCHIVE}"
