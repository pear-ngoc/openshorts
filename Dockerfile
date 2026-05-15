FROM openshorts-backend-base:latest

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV YOLO_MODEL_PATH=/app/models/yolov8n.pt
ENV MPLCONFIGDIR=/tmp/matplotlib
ENV XDG_CACHE_HOME=/tmp/cache
ENV ULTRALYTICS_CONFIG_DIR=/tmp/Ultralytics

USER root

# Copy requirements để install dependency mới (nếu có)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN chown -R appuser:appuser /app

USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
