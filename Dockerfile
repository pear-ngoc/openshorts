# Multi-stage build for smaller final image
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt


# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install FFmpeg, OpenCV dependencies, Node.js, and fontconfig
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    nodejs \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Writable cache/config dirs for non-root runtime
ENV MPLCONFIGDIR=/tmp/matplotlib
ENV XDG_CACHE_HOME=/tmp/cache
ENV FONTCONFIG_FILE=/tmp/fontconfig/fonts.conf
ENV FONTCONFIG_PATH=/tmp/fontconfig
ENV HOME=/tmp
ENV YOLO_MODEL_PATH=/app/models/yolov8n.pt

# Always upgrade yt-dlp to latest
RUN pip install --upgrade --no-cache-dir yt-dlp

# Copy application code
COPY . .

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Create all writable runtime/cache directories
RUN mkdir -p \
    /app/uploads \
    /app/output/thumbnails \
    /app/outputs \
    /app/temp \
    /app/clips \
    /app/models \
    /app/fonts \
    /app/.config/matplotlib \
    /tmp/matplotlib \
    /tmp/fontconfig \
    /tmp/cache \
    /tmp/Ultralytics \
    && chown -R appuser:appuser \
    /app/uploads \
    /app/output \
    /app/outputs \
    /app/temp \
    /app/clips \
    /app/models \
    /app/fonts \
    /app/.config \
    /tmp/matplotlib \
    /tmp/fontconfig \
    /tmp/cache \
    /tmp/Ultralytics \
    && chmod -R 775 \
    /app/uploads \
    /app/output \
    /app/outputs \
    /app/temp \
    /app/clips \
    /app/models \
    /app/fonts \
    /app/.config \
    /tmp/matplotlib \
    /tmp/fontconfig \
    /tmp/cache \
    /tmp/Ultralytics

# Pre-download YOLO model into an absolute path while still root
# Using absolute path so Ultralytics never tries to write to cwd
RUN python -c "from ultralytics import YOLO; YOLO('/app/models/yolov8n.pt')" \
    && chown appuser:appuser /app/models/yolov8n.pt \
    && chmod 644 /app/models/yolov8n.pt

# Switch to non-root user
USER appuser

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]