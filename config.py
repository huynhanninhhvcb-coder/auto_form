"""Cấu hình tập trung cho ứng dụng Auto Form."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - tiện ích tuỳ chọn, chưa "pip install -r requirements.txt"
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


def _find_tesseract() -> str | None:
    """Ưu tiên cấu hình triển khai, sau đó nhận diện vị trí cài chuẩn trên Windows."""
    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        return configured
    candidates = (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        Path("/usr/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
    )
    return next((str(candidate) for candidate in candidates if candidate.is_file()), None)


class Config:
    # Chatbot thủ tục hành chính dùng session để nhớ thủ tục đang hỏi dở.
    # Bắt buộc đặt biến AUTO_FORM_SECRET_KEY ở môi trường triển khai thực tế.
    SECRET_KEY = os.environ.get("AUTO_FORM_SECRET_KEY")
    # Một lần tải có thể gồm tối đa 5 giấy tờ. Flask giới hạn toàn bộ HTTP
    # request; giới hạn từng tệp được kiểm tra thêm khi lưu tạm ở app.py.
    MAX_UPLOAD_FILES = 5
    MAX_FILE_SIZE = 12 * 1024 * 1024  # 12 MB mỗi tệp
    MAX_BATCH_TOTAL_SIZE = 60 * 1024 * 1024  # tổng kích thước thực của các tệp
    # Chừa thêm phần header multipart để 5 tệp đúng 12 MB vẫn hợp lệ.
    MAX_CONTENT_LENGTH = 65 * 1024 * 1024  # khoảng 60 MB dữ liệu tệp
    UPLOAD_FOLDER = BASE_DIR / "uploads"
    OUTPUT_FOLDER = BASE_DIR / "outputs"
    TEMPLATE_FOLDER = BASE_DIR / "templates_word"
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
    MAX_PDF_PAGES = 8
    # Số tiến trình Tesseract tối đa chạy song song (nhiều tệp cùng lượt quét,
    # hoặc nhiều vùng đọc trên một ảnh CCCD). Mặc định 5 tận dụng tốt máy nhiều
    # lõi khi chạy nội bộ; trên máy chủ cloud chỉ có một phần nhỏ CPU (ví dụ
    # gói free của Render, ~0.1 CPU), chạy nhiều tiến trình song song khiến mỗi
    # tiến trình chậm đi rất nhiều thay vì nhanh hơn. Đặt OCR_MAX_WORKERS=1 ở
    # môi trường đó để buộc chạy tuần tự.
    OCR_MAX_WORKERS = max(1, int(os.environ.get("OCR_MAX_WORKERS", "5")))
    INTERVIEW_TTL_SECONDS = 30 * 60
    # Có thể ghi đè qua TESSERACT_CMD, ví dụ một bản cài portable.
    TESSERACT_CMD = _find_tesseract()
    # Tuỳ chọn: bật GPT vision để bổ sung trường OCR/regex không đọc được.
    # Để trống OPENAI_API_KEY để tắt hẳn tính năng này (không tốn phí, không gọi mạng ngoài).
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # Chatbot thủ tục hành chính (widget góc phải dưới).
    THUTUC_FOLDER = BASE_DIR / "thutuc_data"
    # Để trống hai biến dưới để tắt nút "Mẫu đơn, tờ khai" (chatbot vẫn trả
    # lời các câu hỏi về thủ tục bình thường, chỉ ẩn danh sách thư mục Drive).
    GOOGLE_DRIVE_API_KEY = os.environ.get("GOOGLE_DRIVE_API_KEY")
    MAU_DON_FOLDER_ID = os.environ.get("MAU_DON_FOLDER_ID")
    # Công tắc riêng cho nút "Hỏi trợ lý AI" của chatbot, tách khỏi
    # OPENAI_API_KEY để tắt/bật tính năng này mà không ảnh hưởng tính năng AI
    # hỗ trợ trích xuất OCR ở trên (hai tính năng dùng chung một API key,
    # nhưng có thể cần bật/tắt độc lập nhau). Mặc định tắt; đặt
    # CHATBOT_AI_ENABLED=true để bật lại.
    CHATBOT_AI_ENABLED = os.environ.get("CHATBOT_AI_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
