FROM openshorts-backend-base:latest

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV YOLO_MODEL_PATH=/app/models/yolov8n.pt
ENV MPLCONFIGDIR=/tmp/matplotlib
ENV XDG_CACHE_HOME=/tmp/cache
ENV ULTRALYTICS_CONFIG_DIR=/tmp/Ultralytics
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=all
ENV CUDA_HOME=/usr/local/cuda

USER root

# Install CUDA-enabled PyTorch from NVIDIA's official wheel index
# This overrides whatever CPU torch may be in requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir \
    torch==2.3.1 \
    torchvision==0.18.1 \
    --index-url https://download.pytorch.org/whl/cu121

# Copy requirements để install dependency mới (nếu có)
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN chown -R appuser:appuser /app

USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
