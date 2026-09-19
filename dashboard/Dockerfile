FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN mkdir -p /data/media
ENV DATA_DIR=/data
CMD ["sh","-c","gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-8080} app:app"]
