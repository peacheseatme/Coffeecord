#!/usr/bin/env bash
# Convenience wrapper (repo root). Implementation: scripts/restore_instance_data.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/restore_instance_data.sh" "$@"
