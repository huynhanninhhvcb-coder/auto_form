"""Kiểm tra dữ liệu trước khi chèn vào biểu mẫu."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from utils.text_utils import normalize_bank_account_number, normalize_citizen_id, normalize_date, normalize_phone, normalize_text


@dataclass
class ValidationResult:
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_form_data(data: dict, required_fields: tuple[str, ...] | list[str]) -> ValidationResult:
    errors: dict[str, str] = {}
    warnings: list[str] = []
    clean_data = {key: normalize_text(str(value)) for key, value in (data or {}).items()}

    for key in required_fields:
        if not clean_data.get(key):
            errors[key] = "Trường này là bắt buộc."

    for citizen_id_key in ("citizen_id", "deceased_citizen_id"):
        citizen_id = normalize_citizen_id(clean_data.get(citizen_id_key))
        if citizen_id and not re.fullmatch(r"\d{12}", citizen_id):
            errors[citizen_id_key] = "CCCD/số định danh phải gồm đúng 12 chữ số."

    for date_key in (
        "date_of_birth",
        "citizen_id_issue_date",
        "guardian_date_of_birth",
        "deceased_date_of_birth",
        "deceased_death_date",
        "death_certificate_date",
    ):
        value = clean_data.get(date_key, "")
        if value:
            normalized = normalize_date(value)
            try:
                parsed = datetime.strptime(normalized, "%d/%m/%Y")
                if parsed.date() > datetime.now().date():
                    errors[date_key] = "Ngày không được ở tương lai."
            except ValueError:
                errors[date_key] = "Ngày phải theo định dạng dd/mm/yyyy."

    phone = normalize_phone(clean_data.get("phone_number"))
    if phone and not re.fullmatch(r"0\d{9}", phone):
        warnings.append("Số điện thoại nên gồm 10 chữ số và bắt đầu bằng 0.")
    if not clean_data.get("phone_number"):
        warnings.append("Chưa có số điện thoại để cơ quan liên hệ khi cần.")

    bank_fields = {
        "bank_account_name": clean_data.get("bank_account_name", ""),
        "bank_account_number": normalize_bank_account_number(clean_data.get("bank_account_number")),
        "bank_name": clean_data.get("bank_name", ""),
    }
    if any(bank_fields.values()):
        missing_bank_fields = {
            "bank_account_name": "Vui lòng nhập tên chủ tài khoản.",
            "bank_account_number": "Vui lòng nhập số tài khoản.",
            "bank_name": "Vui lòng nhập tên ngân hàng.",
        }
        for field, message in missing_bank_fields.items():
            if not bank_fields[field]:
                errors[field] = message

    account_number = bank_fields["bank_account_number"]
    if account_number and not re.fullmatch(r"\d{6,19}", account_number):
        errors["bank_account_number"] = "Số tài khoản phải gồm từ 6 đến 19 chữ số."

    support_category = clean_data.get("support_category", "")
    allowed_support_categories = {
        "Phụ nữ sinh đủ hai con trước 35 tuổi",
        "Hộ nghèo",
        "Hộ cận nghèo",
        "Đối tượng bảo trợ xã hội",
        "Đối tượng sống tại xã đảo",
    }
    if support_category and support_category not in allowed_support_categories:
        errors["support_category"] = "Vui lòng chọn đúng một đối tượng nhận hỗ trợ trong danh sách."

    payment_method = clean_data.get("payment_method", "")
    if payment_method and payment_method not in {"Tài khoản ngân hàng", "Tiền mặt"}:
        errors["payment_method"] = "Vui lòng chọn nhận qua tài khoản ngân hàng hoặc tiền mặt."

    for time_field, maximum in (("deceased_death_hour", 23), ("deceased_death_minute", 59)):
        value = clean_data.get(time_field, "")
        if value and (not value.isdigit() or not 0 <= int(value) <= maximum):
            errors[time_field] = f"Giá trị phải là số từ 0 đến {maximum}."

    return ValidationResult(errors=errors, warnings=warnings)
