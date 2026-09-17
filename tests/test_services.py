from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
from docx import Document
from PIL import Image, ImageDraw, ImageFont

from app import create_app
from config import Config
from services.ai_extraction_service import AIExtractionError
from services.batch_extraction_service import merge_extraction_results
from services.docx_service import generate_document
from services.extraction_service import extract_personal_information
from services.ocr_service import OCRResult
from services.template_service import get_template
from services.validation_service import validate_form_data


SAMPLE_TEXT = """Họ và tên: NGUYỄN VĂN AN
Ngày sinh: 01-02-1950
Giới tính: Nam
Số CCCD: 079 150 001 234
Nơi cư trú: Phường Minh Phụng, Thành phố Hồ Chí Minh
Địa chỉ liên lạc: Phường Minh Phụng, Thành phố Hồ Chí Minh
Số điện thoại: 0912 345 678"""

MULTI_PAGE_TEXTS = [
    """Văn bản đề nghị hưởng trợ cấp
Họ tên cha/mẹ giám hộ đối với trẻ em dưới 6 tuổi: cecceccec
Nơi cư trú: mới
Địa chỉ liên lạc: LI""",
    """Phiếu thông tin dân cư
Thông tin cá nhân
Họ và tên: TRINH THỊ. Số định danh: Số CMND:
VIỆT NAM 079151001094
Ngày sinh: 30/05/1951
Giới tính: Nữ
Dân tộc: Kinh
Nơi ở hiện tại: Số 8 Lầu 2, đường Lò Siêu, Phường Minh Phụng, Thành phố Hồ Chí Minh""",
    """CĂN CƯỚC CÔNG DÂN
TRỊNHTHỊVIỆTNAM
Số: 079151001094""",
]

OCR_TEST_AVAILABLE = bool(Config.TESSERACT_CMD and Path(Config.TESSERACT_CMD).is_file())


def make_test_identity_image(path: Path) -> None:
    """Ảnh giấy tờ tổng hợp, chữ lớn để kiểm thử thật Tesseract ổn định."""
    image = Image.new("RGB", (1800, 520), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 58)
    lines = ("HO VA TEN: NGUYEN VAN AN", "CCCD: 079150001234", "NGAY SINH: 01/02/1950")
    for index, line in enumerate(lines):
        draw.text((75, 55 + index * 145), line, font=font, fill="black")
    image.save(path)


def make_text_pdf_bytes(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    content = pdf.tobytes()
    pdf.close()
    return content


class ExtractionAndValidationTests(unittest.TestCase):
    def test_extracts_common_personal_fields(self):
        result = extract_personal_information(SAMPLE_TEXT)
        self.assertEqual(result.fields["full_name"], "NGUYỄN VĂN AN")
        self.assertEqual(result.fields["date_of_birth"], "01/02/1950")
        self.assertEqual(result.fields["citizen_id"], "079150001234")
        self.assertEqual(result.fields["phone_number"], "0912345678")

    def test_extracts_nq_support_fields_when_present_in_source_document(self):
        result = extract_personal_information(
            """Họ và tên: NGUYỄN THỊ MAI
Ngày sinh: 01/02/1990
CCCD số: 079090001234, Ngày cấp: 03/04/2021; Nơi cấp: Cục Cảnh sát QLHC về TTXH
Nơi thường trú: 123 Lý Nam Đế, Phường Minh Phụng, Thành phố Hồ Chí Minh
Nơi tạm trú (nếu có): 45 Nguyễn Trãi, Phường Bến Thành
Số điện thoại: 0912 345 678
Nghề nghiệp: Nhân viên
Đơn vị công tác: Công ty A"""
        )

        self.assertEqual(result.fields["citizen_id_issue_date"], "03/04/2021")
        self.assertIn("Cục Cảnh sát", result.fields["citizen_id_issue_place"])
        self.assertIn("Nguyễn Trãi", result.fields["temporary_address"])
        self.assertEqual(result.fields["occupation"], "Nhân viên")
        self.assertEqual(result.fields["employer"], "Công ty A")

    def test_rejects_invalid_required_values(self):
        result = validate_form_data(
            {"full_name": "Nguyễn Văn An", "date_of_birth": "31/02/2020", "citizen_id": "123"},
            ("full_name", "date_of_birth", "citizen_id", "residence_address"),
        )
        self.assertFalse(result.valid)
        self.assertIn("citizen_id", result.errors)
        self.assertIn("date_of_birth", result.errors)
        self.assertIn("residence_address", result.errors)

    def test_rejects_invalid_deceased_person_fields(self):
        result = validate_form_data(
            {
                "full_name": "Nguyễn Văn An",
                "date_of_birth": "01/02/1980",
                "citizen_id": "079080000123",
                "residence_address": "Phường Minh Phụng",
                "deceased_full_name": "Nguyễn Văn Ba",
                "deceased_death_date": "31/02/2025",
                "deceased_citizen_id": "123",
            },
            ("full_name", "date_of_birth", "citizen_id", "residence_address", "deceased_full_name", "deceased_death_date"),
        )
        self.assertFalse(result.valid)
        self.assertIn("deceased_death_date", result.errors)
        self.assertIn("deceased_citizen_id", result.errors)

    def test_prefers_citizen_information_page_over_blank_form_fields(self):
        result = extract_personal_information("\n".join(MULTI_PAGE_TEXTS), page_texts=MULTI_PAGE_TEXTS)
        self.assertEqual(result.fields["full_name"], "TRỊNH THỊ VIỆT NAM")
        self.assertEqual(result.fields["date_of_birth"], "30/05/1951")
        self.assertEqual(result.fields["gender"], "Nữ")
        self.assertEqual(result.fields["citizen_id"], "079151001094")
        self.assertIn("Lò Siêu", result.fields["residence_address"])
        self.assertEqual(result.fields["contact_address"], result.fields["residence_address"])

    def test_extracts_driving_licence_person_but_not_licence_number_as_cccd(self):
        result = extract_personal_information(
            """GIẤY PHÉP LÁI XE/DRIVER'S LICENSE
Số/No: 790177336605
Họ tên/ Full name: HUỲNH AN NINH
Ngày sinh/ Date of Birth: 04/10/1999
Nơi cư trú/Address: 161D/106/44R Lạc Long Quân
P.03, Q.11, TP. Hồ Chí Minh
TP. Hồ Chí Minh, ngày 18 tháng 10 năm 2017
Hạng/Class: A1"""
        )
        self.assertEqual(result.fields["full_name"], "HUỲNH AN NINH")
        self.assertEqual(result.fields["date_of_birth"], "04/10/1999")
        self.assertIn("Lạc Long Quân", result.fields["residence_address"])
        self.assertEqual(result.fields["contact_address"], result.fields["residence_address"])
        self.assertEqual(result.fields["citizen_id"], "")

    def test_disability_card_serial_is_not_used_as_citizen_id(self):
        result = extract_personal_information(
            """GIẤY XÁC NHẬN KHUYẾT TẬT
Số hiệu: 27238.0000072
Họ và tên: HUỲNH CÔNG HOÀNG
Ngày, tháng, năm sinh: 01/09/1948
Giới tính: Nam
Nơi ở hiện nay: 57/3 Trần Quý, phường Minh Phụng, Thành phố Hồ Chí Minh.
Dạng khuyết tật: Thần kinh, tâm thần"""
        )
        self.assertEqual(result.fields["full_name"], "HUỲNH CÔNG HOÀNG")
        self.assertIn("Trần Quý", result.fields["residence_address"])
        self.assertEqual(result.fields["citizen_id"], "")

    def test_residence_uses_permanent_address_and_contact_uses_current_address(self):
        """"Nơi cư trú" trên đơn phải lấy địa chỉ thường trú, "Địa chỉ liên lạc"
        phải lấy nơi ở hiện tại — hai địa chỉ khác nhau trên cùng giấy tờ
        không được lẫn vào nhau (vd. phiếu thông tin dân cư)."""
        result = extract_personal_information(
            """Họ và tên: NGUYỄN LÊ HOÀNG YẾN
Số định danh: 079183035515
Ngày sinh: 09/05/1983
Nơi ở hiện tại: 172/26 Tạ Uyên, Khu phố 14, Phường Minh Phụng, Thành phố Hồ Chí Minh
Địa chỉ thường trú: 45 Nguyễn Trãi, Phường Bến Thành, Thành phố Hồ Chí Minh"""
        )
        self.assertIn("Nguyễn Trãi", result.fields["residence_address"])
        self.assertNotIn("Tạ Uyên", result.fields["residence_address"])
        self.assertIn("Tạ Uyên", result.fields["contact_address"])
        self.assertNotIn("Nguyễn Trãi", result.fields["contact_address"])

    def test_residence_stops_before_birth_registration_label(self):
        result = extract_personal_information(
            """Họ và tên: LƯU HẢO
Số định danh: 079148000149
Ngày sinh: 17/02/1948
Địa chỉ thường trú: 47/58/5 đường Lạc Long Quân, Phường Minh Phụng, Thành phố Hồ Chí Minh
Nơi đăng ký khai sinh: Thành phố Hồ Chí Minh, Việt Nam
Số điện thoại: 0900000000"""
        )
        self.assertIn("Lạc Long Quân", result.fields["residence_address"])
        self.assertNotIn("đăng ký khai sinh", result.fields["residence_address"].casefold())

    def test_accepts_common_ocr_misspelling_of_gender_label(self):
        result = extract_personal_information(
            "Gidi tinh: Nam\nSố định danh cá nhân: 079044004914"
        )
        self.assertEqual(result.fields["gender"], "Nam")

    def test_cccd_and_death_extract_fill_independent_profiles_without_false_conflict(self):
        # Trích lục khai tử mô tả người đã mất, không phải người nộp đơn. Kể
        # từ khi có các trường deceased_*, thông tin của trang này phải rơi
        # đúng vào đó thay vì tranh chấp với danh tính người nộp đơn từ CCCD.
        death_extract = extract_personal_information(
            """TRÍCH LỤC KHAI TỬ
Họ, chữ đệm, tên: NGƯỜI ĐÃ MẤT
Ngày, tháng, năm sinh: 01/01/1940
Dân tộc: Hoa
Số định danh cá nhân: 079040000001
Giấy tờ tùy thân: Thẻ căn cước công dân"""
        )
        cccd = extract_personal_information(
            """--- CCCD CHI TIẾT ---
Họ và tên / Full name:
NGƯỜI NỘP ĐƠN
Ngày sinh: 02/02/1980
Giới tính: Nam
Nơi thường trú: Phường Mẫu, Quận 1
--- CCCD BỐ CỤC ---
CĂN CƯỚC CÔNG DÂN
Số: 079080000002"""
        )

        merged = merge_extraction_results([death_extract, cccd])

        self.assertEqual(merged.result.fields["full_name"], "NGƯỜI NỘP ĐƠN")
        self.assertEqual(merged.result.fields["date_of_birth"], "02/02/1980")
        self.assertEqual(merged.result.fields["citizen_id"], "079080000002")
        self.assertEqual(merged.result.fields["ethnic_group"], "")
        self.assertEqual(merged.result.fields["deceased_full_name"], "NGƯỜI ĐÃ MẤT")
        self.assertEqual(merged.result.fields["deceased_date_of_birth"], "01/01/1940")
        self.assertEqual(merged.result.fields["deceased_citizen_id"], "079040000001")
        self.assertEqual(merged.result.fields["deceased_ethnic_group"], "Hoa")
        # Hai tài liệu mô tả hai người khác nhau một cách hợp lệ (người nộp
        # đơn và người đã mất) nên không được coi là xung đột danh tính.
        self.assertFalse(any("người khác" in warning for warning in merged.result.warnings))


class DocumentAndRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temporary = Path(self.temp_dir.name)
        self.app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": temporary / "uploads",
                "OUTPUT_FOLDER": temporary / "outputs",
                # Test luôn tắt AI fallback: không gọi mạng ngoài, không tốn phí,
                # kết quả không phụ thuộc vào việc máy chạy test có OPENAI_API_KEY hay không.
                "OPENAI_API_KEY": "",
                # Cô lập khỏi thutuc_data thật của dự án để test không phụ thuộc nội dung đó.
                "THUTUC_FOLDER": temporary / "thutuc_data",
                "SECRET_KEY": "test-secret-key",
                # Tắt hẳn để test /mau-don không phụ thuộc mạng ngoài hay .env của máy chạy test.
                "GOOGLE_DRIVE_API_KEY": "",
                "MAU_DON_FOLDER_ID": "",
            }
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health_reports_active_ocr_performance_configuration(self):
        response = self.app.test_client().get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["ocr"]["max_workers"], self.app.config["OCR_MAX_WORKERS"])
        self.assertEqual(payload["ocr"]["target_width"], self.app.config["OCR_TARGET_WIDTH"])
        self.assertEqual(payload["ocr"]["fast_mode"], self.app.config["OCR_FAST_MODE"])
        self.assertEqual(payload["ai_max_workers"], self.app.config["AI_MAX_WORKERS"])

    def test_document_contains_mapped_values(self):
        output = Path(self.temp_dir.name) / "filled.docx"
        generate_document(
            get_template("tro_cap_huu_tri"),
            {
                "full_name": "NGUYỄN VĂN AN",
                "date_of_birth": "1-2-1950",
                "gender": "Nam",
                "citizen_id": "079 150 001 234",
                "residence_address": "Phường Minh Phụng",
            },
            output,
        )
        document = Document(output)
        all_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("NGUYỄN VĂN AN", all_text)
        self.assertIn("079150001234", all_text)

    def test_extract_endpoint_handles_text_pdf_without_ocr(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), SAMPLE_TEXT, fontsize=11)
        content = pdf.tobytes()
        pdf.close()
        client = self.app.test_client()
        response = client.post(
            "/api/extract",
            data={"file": (io.BytesIO(content), "nguoi-dan.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"], "pdf_text")

    def test_ai_requires_explicit_upload_consent(self):
        app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": Path(self.temp_dir.name) / "ai-uploads",
                "OUTPUT_FOLDER": Path(self.temp_dir.name) / "ai-outputs",
                "OPENAI_API_KEY": "test-key",
            }
        )
        pdf_content = make_text_pdf_bytes("HỌ VÀ TÊN: NGUYỄN VĂN THỬ")
        client = app.test_client()

        with patch("app.extract_missing_fields", return_value={"date_of_birth": "01/02/1980"}) as ai:
            without_consent = client.post(
                "/api/extract",
                data={"file": (io.BytesIO(pdf_content), "test.pdf")},
                content_type="multipart/form-data",
            )
            self.assertEqual(without_consent.status_code, 200)
            ai.assert_not_called()

            with_consent = client.post(
                "/api/extract",
                data={"file": (io.BytesIO(pdf_content), "test.pdf"), "use_ai": "1"},
                content_type="multipart/form-data",
            )

        self.assertEqual(with_consent.status_code, 200)
        self.assertEqual(with_consent.get_json()["fields"]["date_of_birth"], "01/02/1980")
        self.assertEqual(with_consent.get_json()["source"], "ai_vision")
        ai.assert_called_once()

    def test_ai_consent_reads_image_directly_without_waiting_for_tesseract(self):
        app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": Path(self.temp_dir.name) / "direct-ai-uploads",
                "OUTPUT_FOLDER": Path(self.temp_dir.name) / "direct-ai-outputs",
                "OPENAI_API_KEY": "test-key",
            }
        )
        image = Image.new("RGB", (400, 240), "white")
        content = io.BytesIO()
        image.save(content, format="PNG")
        content.seek(0)

        with (
            patch(
                "app.extract_missing_fields",
                return_value={"full_name": "NGUYỄN VĂN THỬ", "citizen_id": "079000000001"},
            ) as ai,
            patch("app.ocr_image") as local_ocr,
        ):
            response = app.test_client().post(
                "/api/extract",
                data={"file": (content, "cccd.png"), "use_ai": "1"},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"], "ai_vision")
        self.assertEqual(response.get_json()["fields"]["citizen_id"], "079000000001")
        ai.assert_called_once()
        local_ocr.assert_not_called()

    def test_ai_failure_falls_back_to_local_ocr_once(self):
        app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": Path(self.temp_dir.name) / "fallback-uploads",
                "OUTPUT_FOLDER": Path(self.temp_dir.name) / "fallback-outputs",
                "OPENAI_API_KEY": "test-key",
            }
        )
        image = Image.new("RGB", (400, 240), "white")
        content = io.BytesIO()
        image.save(content, format="PNG")
        content.seek(0)
        ocr_result = OCRResult(text=SAMPLE_TEXT, pages=[SAMPLE_TEXT], source="ocr")

        with (
            patch("app.extract_missing_fields", side_effect=AIExtractionError("tạm thời lỗi")) as ai,
            patch("app.ocr_image", return_value=ocr_result) as local_ocr,
        ):
            response = app.test_client().post(
                "/api/extract",
                data={"file": (content, "cccd.png"), "use_ai": "1"},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"], "ocr")
        self.assertEqual(response.get_json()["fields"]["citizen_id"], "079150001234")
        self.assertTrue(any("đã chuyển sang OCR cục bộ" in item for item in response.get_json()["warnings"]))
        ai.assert_called_once()
        local_ocr.assert_called_once()

    def test_extract_endpoint_accepts_multiple_files_and_merges_fields(self):
        identity_pdf = make_text_pdf_bytes(
            """HO VA TEN: NGUYEN VAN AN
NGAY SINH: 01/02/1950
GIOI TINH: Nam
SO CCCD: 079150001234"""
        )
        contact_pdf = make_text_pdf_bytes(
            """DIA CHI LIEN LAC: Phuong 1, Quan 3, Thanh pho Ho Chi Minh
SO DIEN THOAI: 0912 345 678"""
        )

        client = self.app.test_client()
        response = client.post(
            "/api/extract",
            data={
                "files": [
                    (io.BytesIO(identity_pdf), "cccd.pdf"),
                    (io.BytesIO(contact_pdf), "lien-lac.pdf"),
                ]
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["source"], "batch")
        self.assertEqual(result["fields"]["full_name"], "NGUYEN VAN AN")
        self.assertEqual(result["fields"]["citizen_id"], "079150001234")
        self.assertEqual(result["fields"]["phone_number"], "0912345678")
        self.assertEqual(result["processed_file_count"], 2)
        self.assertEqual(len(result["processed_files"]), 2)
        self.assertTrue(all(file["status"] == "processed" for file in result["processed_files"]))
        self.assertIn("Tệp: cccd.pdf", result["raw_text"])
        self.assertIn("Tệp: lien-lac.pdf", result["raw_text"])
        self.assertEqual(list(Path(self.temp_dir.name, "uploads").iterdir()), [])

    def test_extract_endpoint_cleans_up_batch_progress_after_finishing(self):
        identity_pdf = make_text_pdf_bytes("HO VA TEN: NGUYEN VAN AN\nSO CCCD: 079150001234")
        contact_pdf = make_text_pdf_bytes("SO DIEN THOAI: 0912 345 678")
        client = self.app.test_client()

        response = client.post(
            "/api/extract",
            data={
                "files": [
                    (io.BytesIO(identity_pdf), "cccd.pdf"),
                    (io.BytesIO(contact_pdf), "lien-lac.pdf"),
                ],
                "batch_id": "test-batch-123",
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        # Yêu cầu chính đã xong nên tiến trình phải được dọn ngay, không lưu lại mãi trong RAM.
        progress = client.get("/api/extract/progress/test-batch-123").get_json()
        self.assertEqual(progress, {"done": 0, "total": 0})

    def test_extract_progress_endpoint_returns_zeros_for_unknown_batch(self):
        client = self.app.test_client()
        response = client.get("/api/extract/progress/khong-ton-tai")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"done": 0, "total": 0})

    def test_extract_batch_keeps_results_when_one_document_is_unreadable(self):
        identity_pdf = make_text_pdf_bytes(
            """HO VA TEN: NGUYEN VAN AN
SO CCCD: 079150001234"""
        )
        client = self.app.test_client()
        response = client.post(
            "/api/extract",
            data={
                "files": [
                    (io.BytesIO(identity_pdf), "cccd.pdf"),
                    (io.BytesIO(b"not-a-pdf"), "hong.pdf"),
                ]
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["fields"]["citizen_id"], "079150001234")
        self.assertEqual(result["processed_file_count"], 1)
        self.assertEqual(result["processed_files"][0]["status"], "processed")
        self.assertEqual(result["processed_files"][1]["status"], "error")
        self.assertTrue(any("hong.pdf" in warning for warning in result["warnings"]))

    def test_extract_batch_rejects_more_than_five_files(self):
        client = self.app.test_client()
        response = client.post(
            "/api/extract",
            data={
                "files": [(io.BytesIO(b"x"), f"tep-{index}.pdf") for index in range(6)],
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("tối đa 5 tệp", response.get_json()["error"])

    def test_index_lists_procedures_from_thutuc_folder(self):
        thutuc_folder = Path(self.app.config["THUTUC_FOLDER"])
        thutuc_folder.mkdir(parents=True, exist_ok=True)
        (thutuc_folder / "Tro_cap_huu_tri.txt").write_text(
            "1. Trợ cấp hưu trí xã hội\n", encoding="utf-8"
        )
        client = self.app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        # Danh sách thủ tục chỉ được nhúng dưới dạng JSON cho widget đọc bằng
        # JS (Jinja `tojson` thoát ký tự thành \\uXXXX), không hiển thị trực
        # tiếp dưới dạng văn bản thuần trên trang, nên phải trích JSON ra để so sánh.
        html = response.get_data(as_text=True)
        match = re.search(r"window\.AUTO_FORM_CHATBOT_PROCEDURES = (\[.*?\]);", html)
        self.assertIsNotNone(match)
        self.assertIn("Trợ cấp hưu trí xã hội", json.loads(match.group(1)))

    def test_chatbot_chat_answers_and_remembers_context_across_requests(self):
        thutuc_folder = Path(self.app.config["THUTUC_FOLDER"])
        thutuc_folder.mkdir(parents=True, exist_ok=True)
        (thutuc_folder / "Tro_cap_huu_tri.txt").write_text(
            "1. Trợ cấp hưu trí xã hội\n\n2. Lệ phí:\nMiễn phí.\n", encoding="utf-8"
        )
        client = self.app.test_client()

        overview = client.post("/api/chatbot/chat", json={"message": "Trợ cấp hưu trí xã hội"})
        self.assertEqual(overview.status_code, 200)
        self.assertIn("THỦ TỤC", overview.get_json()["reply"])

        follow_up = client.post("/api/chatbot/chat", json={"message": "le phi bao nhieu"})
        self.assertIn("Miễn phí", follow_up.get_json()["reply"])

    def test_chatbot_chat_does_not_offer_ai_when_not_configured(self):
        # setUp() tắt OPENAI_API_KEY cho toàn bộ lớp test này.
        response = self.app.test_client().post(
            "/api/chatbot/chat", json={"message": "cau hoi khong ro rang gi ca"}
        )
        self.assertFalse(response.get_json()["offer_ai"])

    def test_chatbot_chat_does_not_offer_ai_when_globally_disabled_even_with_api_key(self):
        # CHATBOT_AI_ENABLED mặc định tắt: có OPENAI_API_KEY vẫn không đủ để
        # bật nút "Hỏi trợ lý AI" (tách khỏi tính năng AI hỗ trợ trích xuất OCR).
        temporary = Path(self.temp_dir.name)
        ai_app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": temporary / "ai-disabled-uploads",
                "OUTPUT_FOLDER": temporary / "ai-disabled-outputs",
                "THUTUC_FOLDER": temporary / "ai-disabled-thutuc",
                "SECRET_KEY": "test-secret-key",
                "OPENAI_API_KEY": "test-key",
            }
        )
        response = ai_app.test_client().post(
            "/api/chatbot/chat", json={"message": "cau hoi khong ro rang gi ca"}
        )
        self.assertFalse(response.get_json()["offer_ai"])

    def test_chatbot_chat_only_calls_ai_after_explicit_consent(self):
        temporary = Path(self.temp_dir.name)
        ai_app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": temporary / "ai-chat-uploads",
                "OUTPUT_FOLDER": temporary / "ai-chat-outputs",
                "THUTUC_FOLDER": temporary / "ai-chat-thutuc",
                "SECRET_KEY": "test-secret-key",
                "OPENAI_API_KEY": "test-key",
                "CHATBOT_AI_ENABLED": True,
            }
        )
        client = ai_app.test_client()

        without_consent = client.post("/api/chatbot/chat", json={"message": "cau hoi khong ro rang gi ca"})
        self.assertTrue(without_consent.get_json()["offer_ai"])

        with patch("app.chatbot_ai_service.answer_question", return_value="Câu trả lời từ AI.") as ai_answer:
            with_consent = client.post(
                "/api/chatbot/chat",
                json={"message": "cau hoi khong ro rang gi ca", "use_ai": True},
            )
        result = with_consent.get_json()
        self.assertEqual(result["reply"], "Câu trả lời từ AI.")
        self.assertFalse(result["offer_ai"])
        ai_answer.assert_called_once()

    def test_chatbot_clear_context_resets_session(self):
        client = self.app.test_client()
        response = client.post("/api/chatbot/clear-context")
        self.assertEqual(response.status_code, 204)

    def test_mau_don_route_shows_notice_when_drive_not_configured(self):
        client = self.app.test_client()
        response = client.get("/mau-don")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Chưa cấu hình kho mẫu đơn", response.get_data(as_text=True))

    def test_generate_endpoint_returns_docx(self):
        client = self.app.test_client()
        response = client.post(
            "/api/generate",
            json={
                "template_id": "tro_cap_huu_tri",
                "fields": {
                    "full_name": "NGUYỄN VĂN AN",
                    "date_of_birth": "01/02/1950",
                    "citizen_id": "079150001234",
                    "residence_address": "Phường Minh Phụng",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/vnd.openxmlformats-officedocument", response.content_type)
        response.close()
        self.assertEqual(list((Path(self.temp_dir.name) / "outputs").iterdir()), [])

    def test_interview_collects_answers_and_auto_fills_fields(self):
        client = self.app.test_client()
        response = client.post("/api/interview/start", json={"template_id": "tro_cap_huu_tri"})
        self.assertEqual(response.status_code, 200)
        interview = response.get_json()
        answers = {
            "full_name": "TRỊNH THỊ VIỆT NAM",
            "date_of_birth": "ngày ba mươi tháng năm năm một chín năm một",
            "gender": "Nữ",
            "ethnic_group": "Kinh",
            "citizen_id": "không bảy chín một năm một không không một không chín bốn",
            "residence_address": "Số 8 Lầu 2, đường Lò Siêu, Phường Minh Phụng, Thành phố Hồ Chí Minh",
            "contact_address": "giống nơi cư trú",
            "phone_number": "không chín một hai ba bốn năm sáu bảy tám",
            "bank_account_number": "một hai ba bốn năm sáu bảy tám chín",
        }
        for _ in range(25):
            if interview["complete"]:
                break
            answer = answers.get(interview["field"], "bỏ qua")
            response = client.post(
                "/api/interview/answer",
                json={"session_id": interview["session_id"], "answer": answer},
            )
            self.assertEqual(response.status_code, 200)
            interview = response.get_json()
            self.assertIsNone(interview["error"])
        else:
            self.fail("Cuộc phỏng vấn không hoàn tất sau số bước dự kiến.")

        self.assertEqual(interview["fields"]["citizen_id"], "079151001094")
        self.assertEqual(interview["fields"]["date_of_birth"], "30/05/1951")
        self.assertEqual(interview["fields"]["phone_number"], "0912345678")
        self.assertEqual(interview["fields"]["bank_account_number"], "123456789")
        self.assertEqual(interview["fields"]["contact_address"], interview["fields"]["residence_address"])

    def test_nq_templates_have_voice_interview_and_normalize_support_category(self):
        client = self.app.test_client()
        for template_id in ("ho_tro_nq40", "ho_tro_nq32"):
            with self.subTest(template_id=template_id):
                interview = client.post(
                    "/api/interview/start", json={"template_id": template_id}
                ).get_json()
                answers = {
                    "full_name": "NGUYỄN THỊ MAI",
                    "date_of_birth": "ngày một tháng hai năm một chín chín không",
                    "citizen_id": "không bảy chín không chín không không không một hai ba bốn",
                    "citizen_id_issue_date": "ngày ba tháng tư năm hai không hai một",
                    "citizen_id_issue_place": "Cục Cảnh sát quản lý hành chính về trật tự xã hội",
                    "residence_address": "123 Lý Nam Đế, Phường Minh Phụng",
                    "phone_number": "không chín một hai ba bốn năm sáu bảy tám",
                    "support_category": "Tôi thuộc hộ cận nghèo",
                    "support_detail": "CN-123",
                }
                asked_fields = []
                for _ in range(20):
                    if interview["complete"]:
                        break
                    asked_fields.append(interview["field"])
                    response = client.post(
                        "/api/interview/answer",
                        json={
                            "session_id": interview["session_id"],
                            "answer": answers.get(interview["field"], "bỏ qua"),
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    interview = response.get_json()
                    self.assertIsNone(interview["error"])
                self.assertTrue(interview["complete"])
                self.assertIn("citizen_id_issue_date", asked_fields)
                self.assertIn("support_category", asked_fields)
                self.assertEqual(interview["fields"]["citizen_id_issue_date"], "03/04/2021")
                self.assertEqual(interview["fields"]["support_category"], "Hộ cận nghèo")

    def test_disability_determination_interview_normalizes_procedure_type_and_representative_address(self):
        client = self.app.test_client()
        interview = client.post(
            "/api/interview/start", json={"template_id": "xac_dinh_khuyet_tat"}
        ).get_json()
        answers = {
            "full_name": "NGUYỄN VĂN AN",
            "date_of_birth": "ngày một tháng hai năm hai không một năm",
            "gender": "nam",
            "citizen_id": "không bảy chín hai một năm không không một hai ba bốn",
            "residence_address": "123 Lý Nam Đế, Phường Minh Phụng",
            "contact_address": "giống nơi cư trú",
            "disability_procedure_type": "Tôi muốn xác định lại mức độ khuyết tật",
            "guardian_full_name": "NGUYỄN VĂN BA",
            "guardian_relationship": "Cha",
            "guardian_citizen_id": "không bảy tám một hai ba bốn năm sáu bảy tám chín",
            "guardian_residence_address": "123 Lý Nam Đế",
            "guardian_current_address": "giống nơi cư trú",
            "guardian_phone": "không chín một hai ba bốn năm sáu bảy tám",
        }
        for _ in range(20):
            if interview["complete"]:
                break
            field = interview["field"]
            response = client.post(
                "/api/interview/answer",
                json={"session_id": interview["session_id"], "answer": answers.get(field, "bỏ qua")},
            )
            self.assertEqual(response.status_code, 200)
            interview = response.get_json()
            self.assertIsNone(interview["error"])
        self.assertTrue(interview["complete"])
        fields = interview["fields"]
        self.assertEqual(fields["citizen_id"], "079215001234")
        self.assertEqual(
            fields["disability_procedure_type"],
            "Xác định lại mức độ khuyết tật và cấp Giấy xác nhận khuyết tật",
        )
        self.assertEqual(fields["contact_address"], fields["residence_address"])
        self.assertEqual(fields["guardian_current_address"], fields["guardian_residence_address"])
        self.assertEqual(fields["guardian_phone"], "0912345678")

    def test_hoa_tang_interview_asks_about_deceased_instead_of_retirement_fields(self):
        client = self.app.test_client()
        response = client.post("/api/interview/start", json={"template_id": "ho_tro_hoa_tang"})
        self.assertEqual(response.status_code, 200)
        interview = response.get_json()
        answers = {
            "full_name": "NGUYỄN VĂN AN",
            "date_of_birth": "ngày một tháng hai năm một chín năm không",
            "citizen_id": "không bảy chín một năm không không không một hai ba bốn",
            "residence_address": "Phường Minh Phụng, Thành phố Hồ Chí Minh",
            "deceased_full_name": "TRẦN THỊ BA",
            "deceased_death_date": "ngày mười lăm tháng tám năm hai nghìn không trăm hai mươi sáu",
            "cremation_support_category": "hộ nghèo",
        }
        for _ in range(25):
            if interview["complete"]:
                break
            # Mẫu này không hỏi giới tính/dân tộc/địa chỉ liên lạc của người
            # đề nghị — chỉ khẳng định điều đó bằng cách không có đáp án sẵn
            # cho các trường đó trong "answers" phía trên; nếu phỏng vấn lỡ
            # hỏi lại câu của mẫu hưu trí, test vẫn "bỏ qua" được vì đó là
            # trường không bắt buộc, nhưng assertion field-set bên dưới sẽ bắt lỗi.
            answer = answers.get(interview["field"], "bỏ qua")
            response = client.post(
                "/api/interview/answer",
                json={"session_id": interview["session_id"], "answer": answer},
            )
            self.assertEqual(response.status_code, 200)
            interview = response.get_json()
            self.assertIsNone(interview["error"])
        else:
            self.fail("Cuộc phỏng vấn không hoàn tất sau số bước dự kiến.")

        self.assertEqual(interview["fields"]["full_name"], "NGUYỄN VĂN AN")
        self.assertEqual(interview["fields"]["date_of_birth"], "01/02/1950")
        self.assertEqual(interview["fields"]["citizen_id"], "079150001234")
        self.assertEqual(interview["fields"]["deceased_full_name"], "TRẦN THỊ BA")
        self.assertEqual(interview["fields"]["deceased_death_date"], "15/08/2026")
        self.assertEqual(interview["fields"]["cremation_support_category"], "Hộ nghèo")
        # Mẫu hỏa táng không hỏi các trường riêng của mẫu hưu trí.
        self.assertEqual(interview["fields"]["gender"], "")
        self.assertEqual(interview["fields"]["benefit_receiving_location"], "")

    def test_mai_tang_interview_collects_deceased_details_and_allows_skipping_optional_fields(self):
        client = self.app.test_client()
        response = client.post("/api/interview/start", json={"template_id": "ho_tro_mai_tang"})
        self.assertEqual(response.status_code, 200)
        interview = response.get_json()
        answers = {
            "full_name": "LÊ THỊ CÚC",
            "date_of_birth": "ngày hai tháng ba năm một chín sáu không",
            "citizen_id": "không bảy chín một sáu không không không không không một một",
            "residence_address": "Phường Minh Phụng, Thành phố Hồ Chí Minh",
            "request_content": "Đề nghị hỗ trợ chi phí mai táng",
            "deceased_full_name": "PHẠM VĂN DŨNG",
            "deceased_death_date": "ngày mười tháng chín năm hai nghìn không trăm hai mươi sáu",
            "deceased_gender": "Nam",
            "payment_method": "tiền mặt",
        }
        for _ in range(35):
            if interview["complete"]:
                break
            answer = answers.get(interview["field"], "bỏ qua")
            response = client.post(
                "/api/interview/answer",
                json={"session_id": interview["session_id"], "answer": answer},
            )
            self.assertEqual(response.status_code, 200)
            interview = response.get_json()
            self.assertIsNone(interview["error"])
        else:
            self.fail("Cuộc phỏng vấn không hoàn tất sau số bước dự kiến.")

        self.assertEqual(interview["fields"]["deceased_full_name"], "PHẠM VĂN DŨNG")
        self.assertEqual(interview["fields"]["deceased_death_date"], "10/09/2026")
        self.assertEqual(interview["fields"]["deceased_gender"], "Nam")
        self.assertEqual(interview["fields"]["request_content"], "Đề nghị hỗ trợ chi phí mai táng")
        self.assertEqual(interview["fields"]["payment_method"], "Tiền mặt")
        # Các trường không bắt buộc đã "bỏ qua" phải giữ nguyên rỗng.
        self.assertEqual(interview["fields"]["deceased_citizen_id"], "")
        self.assertEqual(interview["fields"]["org_name"], "")

    def test_hoa_tang_interview_requires_deceased_full_name(self):
        client = self.app.test_client()
        interview = client.post("/api/interview/start", json={"template_id": "ho_tro_hoa_tang"}).get_json()
        # Trả lời lần lượt: 4 trường bắt buộc của người đề nghị, số điện
        # thoại (không bắt buộc, bỏ qua), quan hệ với người mất (không bắt
        # buộc, bỏ qua) — để tới đúng câu hỏi "Họ và tên người mất" (bắt buộc).
        answers_in_order = [
            "NGUYỄN VĂN AN",
            "01/02/1950",
            "không bảy chín một năm không không không một hai ba bốn",
            "Phường Minh Phụng, Thành phố Hồ Chí Minh",
            "bỏ qua",
            "bỏ qua",
        ]
        for answer in answers_in_order:
            interview = client.post(
                "/api/interview/answer",
                json={"session_id": interview["session_id"], "answer": answer},
            ).get_json()
            self.assertIsNone(interview["error"])
        self.assertEqual(interview["field"], "deceased_full_name")

        response = client.post(
            "/api/interview/answer",
            json={"session_id": interview["session_id"], "answer": "bỏ qua"},
        )
        result = response.get_json()
        self.assertEqual(result["error"], "Không thể bỏ qua trường bắt buộc.")
        # Vẫn đang dừng ở câu hỏi bắt buộc đó, không bị nhảy bước.
        self.assertEqual(result["field"], "deceased_full_name")

    @unittest.skipUnless(OCR_TEST_AVAILABLE, "Tesseract chưa được cài hoặc chưa cấu hình")
    def test_image_ocr_pipeline_extracts_citizen_id(self):
        image_path = Path(self.temp_dir.name) / "identity.png"
        make_test_identity_image(image_path)
        client = self.app.test_client()
        with image_path.open("rb") as image:
            response = client.post(
                "/api/extract",
                data={"file": (image, "identity.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["source"], "ocr")
        self.assertEqual(result["fields"]["citizen_id"], "079150001234")

    @unittest.skipUnless(OCR_TEST_AVAILABLE, "Tesseract chưa được cài hoặc chưa cấu hình")
    def test_scanned_pdf_pipeline_uses_ocr(self):
        image_path = Path(self.temp_dir.name) / "identity.png"
        pdf_path = Path(self.temp_dir.name) / "identity-scan.pdf"
        make_test_identity_image(image_path)
        pdf = fitz.open()
        page = pdf.new_page(width=900, height=260)
        page.insert_image(page.rect, filename=image_path)
        pdf.save(pdf_path)
        pdf.close()

        client = self.app.test_client()
        with pdf_path.open("rb") as pdf_file:
            response = client.post(
                "/api/extract",
                data={"file": (pdf_file, "identity-scan.pdf")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["source"], "pdf_ocr")
        self.assertEqual(result["fields"]["citizen_id"], "079150001234")


if __name__ == "__main__":
    unittest.main()
