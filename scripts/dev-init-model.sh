#!/usr/bin/env bash
#
# dev-init-model.sh — Download and cache the YOLO model into the backend_models volume.
#
# Usage:
#   bash scripts/dev-init-model.sh
#
# Run this once before the first dev session, or after wiping the volume.
# The model persists in the 'backend_models' Docker volume so you don't
# need to re-download on every container start.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

MODEL_PATH="/app/models/yolov8n.pt"

echo "📦 Initialising YOLO model in the backend_models volume..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend \
    sh -lc "mkdir -p /app/models && python -c \"from ultralytics import YOLO; YOLO('$MODEL_PATH')\" && ls -lh $MODEL_PATH"

echo ""
echo "✅ YOLO model ready at $MODEL_PATH inside the container."
