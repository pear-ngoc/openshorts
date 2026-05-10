#!/usr/bin/env bash
#
# dev-up.sh — Start OpenShorts in dev mode with hot-reload.
#
# Usage:
#   bash scripts/dev-up.sh
#
# This starts the stack using docker-compose.dev.yml which:
#   - Binds the source tree into the backend container (hot-reload)
#   - Runs uvicorn with --reload
#   - Keeps YOLO model in a named volume so it persists across restarts
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "📁 Project root: $PROJECT_DIR"
echo ""

# Ensure runtime directories exist on the host (created with correct permissions)
for dir in uploads output outputs temp clips; do
    mkdir -p "$dir"
    echo "   $dir/ — $(du -sh "$dir" 2>/dev/null | cut -f1 || echo 'new')"
done

echo ""
echo "🚀 Starting OpenShorts in DEV mode..."
echo "   - Backend: http://localhost:8000"
echo "   - Frontend: http://localhost:5175"
echo ""
echo "   Press Ctrl+C to stop, or run 'docker compose -f docker-compose.yml -f docker-compose.dev.yml down'"
echo ""

exec docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d "$@"
