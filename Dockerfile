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
    PYTHONDONTWRITEBYTECODE=1 \
    OCR_MAX_WORKERS=1 \
    OCR_TARGET_WIDTH=1200 \
    OCR_LANGUAGE=vie \
    OCR_FAST_MODE=true \
    OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1
# Tesseract build sẵn trên Debian bật OpenMP cho mạng LSTM: mặc định nó tự
# spawn một luồng cho mỗi lõi mà /proc/cpuinfo báo cáo, kể cả khi cgroup chỉ
# cấp 0.1 CPU thật sự (Render free). Máy chủ vẫn thấy nhiều lõi ảo nên
# Tesseract tạo hàng chục luồng tranh nhau một phần CPU rất nhỏ, làm một lượt
# OCR chậm hơn nhiều so với chạy đơn luồng. Ép OMP_THREAD_LIMIT/OMP_NUM_THREADS
# về 1 loại bỏ chi phí chuyển ngữ cảnh này.

EXPOSE 8000

# Render (và nền tảng tương tự) cấp cổng lắng nghe qua biến PORT.
# 1 worker: gói free chỉ có 0.1 CPU/512MB, chạy 2 tiến trình gunicorn tranh
# chấp tài nguyên khiến OCR chậm/treo hơn thay vì nhanh hơn. Giữ 2 thread để
# /health và các yêu cầu tĩnh vẫn phản hồi được trong lúc một OCR khác đang
# chạy (tesseract chạy ở tiến trình con nên không giữ GIL trong lúc chờ).
# Timeout dài (300s) vì OCR ảnh trên CPU yếu có thể mất hơn 1 phút.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 2 --timeout 300 app:app"]
