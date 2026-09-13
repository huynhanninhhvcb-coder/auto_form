"""Kiểm thử đơn vị cho các nhánh OCR chuyên biệt của CCCD."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from services.ocr_service import (
    _detect_document_crop,
    _looks_like_cccd,
    _ocr_cccd_front_address,
    _ocr_cccd_front_details,
    _split_stacked_cccd,
    ocr_pil_image,
)


class CccdAddressOcrTests(unittest.TestCase):
    def test_region_ocr_recovers_glare_affected_cccd_fields(self):
        image = Image.new("RGB", (1600, 1000), "white")
        results = iter(
            (
                "Họ và tên / Full name:\nNGŨ KIM",
                "05/11/1988",
                "Nam Quốc tịch",
                "Nơi thường trú / Place of residence: 319/12 Tan - x",
                "Phước, P06, Quận 11, TP. Hồ Chí Minh",
            )
        )
        with patch("services.ocr_service.pytesseract.image_to_string", side_effect=results):
            text = _ocr_cccd_front_details(image, ["vie", "eng"], "vie+eng")

        self.assertIn("NGŨ KIM", text)
        self.assertIn("Ngày sinh: 05/11/1988", text)
        self.assertIn("Giới tính: Nam", text)
        self.assertIn("319/12 Tan", text)
        self.assertIn("Phước, P06, Quận 11", text)

    def test_does_not_treat_every_horizontal_official_document_as_cccd(self):
        image = Image.new("RGB", (1200, 800), "white")
        self.assertFalse(_looks_like_cccd("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", image))

    def test_crops_warm_card_from_large_neutral_phone_photo(self):
        image = Image.new("RGB", (900, 1600), (130, 145, 125))
        card = Image.new("RGB", (800, 500), (245, 225, 180))
        image.paste(card, (50, 650))

        cropped = _detect_document_crop(image)

        self.assertLess(cropped.height, image.height)
        self.assertGreater(cropped.width / cropped.height, 1.3)

    def test_prefers_vietnamese_single_line_ocr_for_cccd_address(self):
        image = Image.new("RGB", (1280, 800), "white")
        line_results = iter(
            (
                "Số 42, Đường Kiểm Thử\n",
                "Phường Mẫu, Quận 1, TP. Hà Nội\n",
            )
        )

        with patch(
            "services.ocr_service.pytesseract.image_to_string",
            side_effect=line_results,
        ) as ocr:
            result = _ocr_cccd_front_address(image, ["vie", "eng"], "vie+eng")

        self.assertEqual(
            result,
            "Nơi thường trú: Số 42, Đường Kiểm Thử\nPhường Mẫu, Quận 1, TP. Hà Nội",
        )
        self.assertEqual(ocr.call_count, 2)
        self.assertTrue(all(call.kwargs["lang"] == "vie" for call in ocr.call_args_list))
        self.assertTrue(all("--psm 7" in call.kwargs["config"] for call in ocr.call_args_list))


class StackedCccdSplitTests(unittest.TestCase):
    def test_splits_a_front_and_back_composite_into_two_card_shaped_halves(self):
        # Người dân thường ghép mặt trước (trên) và mặt sau (dưới) CCCD vào
        # chung một tệp ảnh; tỉ lệ khung ảnh tổng thể khi đó xấp xỉ 0.65-0.95.
        composite = Image.new("RGB", (1012, 1391), "white")

        halves = _split_stacked_cccd(composite)

        self.assertIsNotNone(halves)
        front, back = halves
        self.assertEqual(front.width, composite.width)
        self.assertEqual(back.width, composite.width)
        # Mỗi nửa phải có tỉ lệ của một mặt thẻ nằm ngang để các hàm đọc theo
        # vùng (vốn tính tọa độ theo tỉ lệ một mặt thẻ) chạy đúng.
        self.assertGreater(front.width / front.height, 1.3)
        self.assertGreater(back.width / back.height, 1.3)
        # Hai nửa phải chờm nhẹ lên nhau quanh điểm giữa để không hụt mép thẻ.
        self.assertGreater(front.height + back.height, composite.height)

    def test_does_not_split_a_single_horizontal_card(self):
        horizontal_card = Image.new("RGB", (1600, 1000), "white")

        self.assertIsNone(_split_stacked_cccd(horizontal_card))

    def test_ocr_pipeline_feeds_the_split_halves_to_the_region_readers(self):
        # Trước khi có _split_stacked_cccd, các hàm đọc theo vùng luôn nhận
        # nguyên ảnh ghép, khiến vùng MRZ và địa chỉ trật hoàn toàn khỏi vị trí
        # thật. Bài test này khóa lại hành vi: mặt trước/sau phải được tách
        # đôi trước khi tới các hàm đọc theo vùng.
        composite = Image.new("RGB", (1000, 1360), "white")
        whole_card_text = "CĂN CƯỚC CÔNG DÂN\nIDVNM123456789\n"

        with (
            patch("services.ocr_service.pytesseract.get_languages", return_value=["vie", "eng"]),
            patch("services.ocr_service.pytesseract.image_to_string", return_value=whole_card_text),
            patch("services.ocr_service._ocr_cccd_front_details", return_value="") as front_details,
            patch("services.ocr_service._ocr_cccd_front_address_lines", return_value="") as front_lines,
            patch("services.ocr_service._ocr_cccd_front_address", return_value="") as front_address,
            patch("services.ocr_service._ocr_cccd_mrz", return_value="") as mrz,
        ):
            ocr_pil_image(composite)

        front_details.assert_called_once()
        front_lines.assert_called_once()
        front_address.assert_called_once()
        mrz.assert_called_once()

        front_image = front_details.call_args.args[0]
        back_image = mrz.call_args.args[0]
        self.assertEqual(front_image.width, composite.width)
        self.assertEqual(back_image.width, composite.width)
        self.assertLess(front_image.height, composite.height)
        self.assertLess(back_image.height, composite.height)


if __name__ == "__main__":
    unittest.main()
