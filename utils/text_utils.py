"""Các hàm chuẩn hóa nhỏ, dùng chung cho OCR và kiểm tra dữ liệu."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: str | None) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def normalize_citizen_id(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("84") and len(digits) == 11:
        return f"0{digits[2:]}"
    return digits


def normalize_bank_account_number(value: str | None) -> str:
    """Bỏ khoảng trắng và dấu phân tách thường gặp trong số tài khoản.

    Không xóa chữ cái hay ký tự lạ: tầng kiểm tra dữ liệu cần nhận ra chúng
    thay vì âm thầm biến một số tài khoản sai thành số hợp lệ.
    """
    return re.sub(r"[\s.\-]", "", normalize_text(value))


def normalize_date(value: str | None) -> str:
    """Đưa ngày dạng d/m/yyyy hoặc d-m-yyyy về dd/mm/yyyy nếu hợp lệ sơ bộ."""
    value = normalize_text(value).replace("-", "/").replace(".", "/")
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", value)
    if not match:
        return value
    day, month, year = match.groups()
    if len(year) == 2:
        year = f"19{year}" if int(year) > 30 else f"20{year}"
    return f"{int(day):02d}/{int(month):02d}/{year}"
