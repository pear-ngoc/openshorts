#!/usr/bin/env bash
#
# dev-restart-backend.sh — Restart only the backend container (no rebuild).
#
# Usage:
#   bash scripts/dev-restart-backend.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "🔄 Restarting backend container..."
exec docker compose -f docker-compose.yml -f docker-compose.dev.yml restart backend
