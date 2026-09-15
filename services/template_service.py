"""Danh mục và mapping của các biểu mẫu Word được hỗ trợ."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import BASE_DIR


@dataclass(frozen=True)
class FormTemplate:
    id: str
    title: str
    file_name: str
    download_name: str
    required_fields: tuple[str, ...]

    @property
    def path(self) -> Path:
        return BASE_DIR / "templates_word" / self.file_name


_DECEASED_BENEFIT_REQUIRED_FIELDS = (
    "full_name",
    "date_of_birth",
    "citizen_id",
    "residence_address",
    "deceased_full_name",
    "deceased_death_date",
)

TEMPLATES = {
    "tro_cap_huu_tri": FormTemplate(
        id="tro_cap_huu_tri",
        title="Văn bản đề nghị hưởng trợ cấp hưu trí xã hội",
        file_name="tro_cap_huu_tri.docx",
        download_name="van-ban-de-nghi-huong-tro-cap-huu-tri-xa-hoi",
        required_fields=("full_name", "date_of_birth", "citizen_id", "residence_address"),
    ),
    "ho_tro_hoa_tang": FormTemplate(
        id="ho_tro_hoa_tang",
        title="Tờ khai nhận chi phí hỗ trợ khuyến khích hỏa táng",
        file_name="ho_tro_hoa_tang.docx",
        download_name="to-khai-ho-tro-khuyen-khich-hoa-tang",
        required_fields=_DECEASED_BENEFIT_REQUIRED_FIELDS,
    ),
    "ho_tro_mai_tang": FormTemplate(
        id="ho_tro_mai_tang",
        title="Tờ khai đề nghị hỗ trợ chi phí mai táng",
        file_name="ho_tro_mai_tang.docx",
        download_name="to-khai-de-nghi-ho-tro-chi-phi-mai-tang",
        required_fields=_DECEASED_BENEFIT_REQUIRED_FIELDS,
    ),
    "ho_tro_nq40": FormTemplate(
        id="ho_tro_nq40",
        title="Tờ khai thông tin cá nhân nhận hỗ trợ theo Nghị quyết số 40/NQ-HĐND",
        file_name="ho_tro_nq40.docx",
        download_name="to-khai-ho-tro-nghi-quyet-40",
        required_fields=(
            "full_name",
            "date_of_birth",
            "citizen_id",
            "citizen_id_issue_date",
            "citizen_id_issue_place",
            "residence_address",
            "support_category",
        ),
    ),
    "ho_tro_nq32": FormTemplate(
        id="ho_tro_nq32",
        title="Tờ khai thông tin cá nhân nhận hỗ trợ theo Nghị quyết số 32/2025/NQ-HĐND",
        file_name="ho_tro_nq32.docx",
        download_name="to-khai-ho-tro-nghi-quyet-32",
        required_fields=(
            "full_name",
            "date_of_birth",
            "citizen_id",
            "citizen_id_issue_date",
            "citizen_id_issue_place",
            "residence_address",
            "support_category",
        ),
    ),
}


def get_template(template_id: str) -> FormTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[dict[str, str]]:
    return [{"id": item.id, "title": item.title} for item in TEMPLATES.values()]
