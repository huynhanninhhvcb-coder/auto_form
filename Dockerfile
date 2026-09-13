# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Tesseract + gói ngôn ngữ tiếng Việt để OCR ảnh/PDF quét.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-vie \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Render (và nền tảng tương tự) cấp cổng lắng nghe qua biến PORT.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 120 app:app"]
