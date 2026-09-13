"""Đọc text layer của PDF trước; chỉ OCR các trang PDF quét khi cần."""

from __future__ import annotations

from pathlib import Path
import re

import pymupdf as fitz
from PIL import Image

from services.ocr_service import OCRResult, detect_languages, ocr_pil_image


class PDFProcessingError(RuntimeError):
    pass


def _combine_pages(pages: list[str]) -> str:
    return "\n\n".join(f"--- TRANG {index + 1} ---\n{page}" for index, page in enumerate(pages) if page)


def extract_pdf_pages(file_path: str | Path, max_pages: int) -> list[str]:
    try:
        with fitz.open(file_path) as document:
            if document.page_count == 0:
                raise PDFProcessingError("PDF không có trang.")
            page_count = min(document.page_count, max_pages)
            return [document[index].get_text("text").strip() for index in range(page_count)]
    except fitz.FileDataError as error:
        raise PDFProcessingError("Tệp PDF không hợp lệ hoặc được bảo vệ.") from error


def extract_pdf_text(file_path: str | Path, max_pages: int) -> str:
    """Giữ API cũ cho các nơi chỉ cần chuỗi văn bản PDF."""
    return _combine_pages(extract_pdf_pages(file_path, max_pages=max_pages))


def ocr_pdf(file_path: str | Path, max_pages: int, tesseract_cmd: str | None = None) -> OCRResult:
    try:
        # Tra cứu gói ngôn ngữ một lần cho cả tệp: mỗi lần gọi tốn 150 ms-1 s
        # (đo thực tế trên Windows), nên để mỗi trang tự tra lại sẽ nhân chi
        # phí này lên tới `max_pages` lần một cách vô ích.
        languages = detect_languages(tesseract_cmd)
        pages: list[str] = []
        warnings: list[str] = []
        mrz_texts: list[str] = []
        with fitz.open(file_path) as document:
            for index in range(min(document.page_count, max_pages)):
                page = document.load_page(index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                result = ocr_pil_image(image, languages=languages)
                # Một trang CCCD có thể có thêm vùng OCR bố cục/MRZ để parser
                # ưu tiên thông tin rõ hơn; vẫn chỉ lặp tối đa số trang PDF cho phép.
                pages.extend(result.pages or [result.text])
                warnings.extend(result.warnings)
                mrz_texts.extend(result.mrz_texts)
            if document.page_count > max_pages:
                warnings.append(f"Chỉ OCR {max_pages}/{document.page_count} trang đầu của PDF.")
        return OCRResult(
            text=_combine_pages(pages),
            warnings=list(dict.fromkeys(warnings)),
            source="pdf_ocr",
            pages=pages,
            mrz_texts=mrz_texts,
        )
    except fitz.FileDataError as error:
        raise PDFProcessingError("Tệp PDF không hợp lệ hoặc được bảo vệ.") from error


def process_pdf(file_path: str | Path, max_pages: int, tesseract_cmd: str | None = None) -> OCRResult:
    pages = extract_pdf_pages(file_path, max_pages=max_pages)
    text = _combine_pages(pages)
    if len(text.replace(" ", "")) >= 30:
        try:
            with fitz.open(file_path) as document:
                warnings = [f"PDF có nhiều trang; chỉ dùng {max_pages}/{document.page_count} trang đầu để trích xuất."] if document.page_count > max_pages else []
        except fitz.FileDataError:
            warnings = []
        return OCRResult(text=text, warnings=warnings, source="pdf_text", pages=pages)
    return ocr_pdf(file_path, max_pages=max_pages, tesseract_cmd=tesseract_cmd)
