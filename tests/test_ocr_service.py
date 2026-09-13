"""Kiểm thử đơn vị cho các nhánh OCR chuyên biệt của CCCD."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from services.ocr_service import (
    _detect_document_crop,
    _looks_like_cccd,
    _split_stacked_cccd,
    ocr_pil_image,
)


class CccdDetectionTests(unittest.TestCase):
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

    def test_ocr_pipeline_feeds_the_split_back_half_to_mrz(self):
        # Trước khi có _split_stacked_cccd, MRZ luôn nhận nguyên ảnh ghép,
        # khiến vùng MRZ (52%-97% chiều cao ảnh) trật hoàn toàn khỏi vị trí
        # thật. Bài test này khóa lại hành vi: mặt sau phải được tách ra
        # trước khi tới hàm đọc MRZ.
        composite = Image.new("RGB", (1000, 1360), "white")
        whole_card_text = "CĂN CƯỚC CÔNG DÂN\nIDVNM123456789\n"

        with (
            patch("services.ocr_service.pytesseract.get_languages", return_value=["vie", "eng"]),
            patch("services.ocr_service.pytesseract.image_to_string", return_value=whole_card_text),
            patch("services.ocr_service._ocr_cccd_mrz", return_value="") as mrz,
        ):
            ocr_pil_image(composite)

        mrz.assert_called_once()

        back_image = mrz.call_args.args[0]
        self.assertEqual(back_image.width, composite.width)
        self.assertLess(back_image.height, composite.height)


if __name__ == "__main__":
    unittest.main()
