#!/usr/bin/env bash
# Convenience wrapper (repo root). Implementation: scripts/backup_instance_data.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/backup_instance_data.sh" "$@"
