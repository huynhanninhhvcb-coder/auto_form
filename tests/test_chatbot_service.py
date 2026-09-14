from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from services import chatbot_service


SAMPLE_PROCEDURE = """1. Trợ cấp hưu trí xã hội

2. Đối tượng áp dụng:
Công dân Việt Nam từ đủ 75 tuổi trở lên.

3. Thành phần hồ sơ:
- Đơn đề nghị hưởng trợ cấp
- Căn cước công dân (bản sao)

4. Địa chỉ tiếp nhận:
Trung tâm phục vụ hành chính công phường Minh Phụng
"""


class TextNormalisationTests(unittest.TestCase):
    def test_remove_accents_handles_dinamic_d(self):
        self.assertEqual(chatbot_service.remove_accents("Đường Điện Biên Phủ"), "duong dien bien phu")

    def test_chuan_hoa_tim_kiem_strips_punctuation(self):
        self.assertEqual(chatbot_service.chuan_hoa_tim_kiem("Lệ phí là bao nhiêu?"), "le phi la bao nhieu")

    def test_co_tu_khoa_matches_whole_word_only(self):
        # "hi" không được khớp bên trong "phi" sau khi bỏ dấu.
        self.assertFalse(chatbot_service.co_tu_khoa("le phi la bao nhieu", ["hi"]))
        self.assertTrue(chatbot_service.co_tu_khoa("xin chao ban", ["chao"]))

    def test_detect_intent_finds_fee_question(self):
        self.assertEqual(chatbot_service.detect_intent("le phi bao nhieu"), "phi")
        self.assertIsNone(chatbot_service.detect_intent("cau hoi khong lien quan gi ca"))


class ThutucFileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name)
        (self.folder / "Tro_cap_huu_tri.txt").write_text(SAMPLE_PROCEDURE, encoding="utf-8")

    def test_list_thutuc_files_creates_missing_folder(self):
        missing = self.folder / "chua-ton-tai"
        self.assertEqual(chatbot_service.list_thutuc_files(missing), [])
        self.assertTrue(missing.is_dir())

    def test_get_ten_thutuc_from_file_falls_back_to_first_line(self):
        name = chatbot_service.get_ten_thutuc_from_file(self.folder, "Tro_cap_huu_tri.txt")
        self.assertEqual(name, "Trợ cấp hưu trí xã hội")

    def test_load_thutuc_from_file_splits_sections(self):
        thutuc = chatbot_service.load_thutuc_from_file(self.folder, "Tro_cap_huu_tri.txt")
        self.assertIn("ho_so", thutuc["sections"])
        self.assertIn("Đơn đề nghị", thutuc["sections"]["ho_so"])
        self.assertIn("dia_chi", thutuc["sections"])

    def test_load_thutuc_from_file_missing_returns_none(self):
        self.assertIsNone(chatbot_service.load_thutuc_from_file(self.folder, "khong-ton-tai.txt"))

    def test_tim_kiem_thutuc_finds_exact_and_fuzzy_matches(self):
        exact, score = chatbot_service.tim_kiem_thutuc(self.folder, "Trợ cấp hưu trí xã hội")
        self.assertEqual(score, 100)
        self.assertEqual(exact["filename"], "Tro_cap_huu_tri.txt")

        fuzzy, fuzzy_score = chatbot_service.tim_kiem_thutuc(self.folder, "toi muon hoi ve tro cap huu tri")
        self.assertGreaterEqual(fuzzy_score, 20)
        self.assertEqual(fuzzy["filename"], "Tro_cap_huu_tri.txt")

        none_match, none_score = chatbot_service.tim_kiem_thutuc(self.folder, "mot cau hoi hoan toan khong lien quan")
        self.assertIsNone(none_match)
        self.assertEqual(none_score, 0)


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name)
        (self.folder / "Tro_cap_huu_tri.txt").write_text(SAMPLE_PROCEDURE, encoding="utf-8")

    def test_greeting_clears_context(self):
        answer = chatbot_service.answer_question(self.folder, "xin chao", "Tro_cap_huu_tri.txt")
        self.assertEqual(answer.context_action, "clear")
        self.assertIn("Xin chào", answer.reply)

    def test_matching_procedure_sets_context_and_returns_overview(self):
        answer = chatbot_service.answer_question(self.folder, "Trợ cấp hưu trí xã hội", None)
        self.assertEqual(answer.context_action, "set")
        self.assertEqual(answer.context_value, "Tro_cap_huu_tri.txt")
        self.assertIn("THỦ TỤC", answer.reply)

    def test_follow_up_intent_uses_last_procedure_from_context(self):
        answer = chatbot_service.answer_question(self.folder, "ho so can gi", "Tro_cap_huu_tri.txt")
        self.assertIsNone(answer.context_action)
        self.assertIn("Thành phần hồ sơ", answer.reply)

    def test_unmatched_question_suggests_known_procedures_and_offers_ai(self):
        answer = chatbot_service.answer_question(self.folder, "cau hoi khong lien quan gi ca", None)
        self.assertIsNone(answer.context_action)
        self.assertIn("Trợ cấp hưu trí xã hội", answer.suggestions)
        self.assertTrue(answer.offer_ai)

    def test_matched_procedure_does_not_offer_ai(self):
        answer = chatbot_service.answer_question(self.folder, "Trợ cấp hưu trí xã hội", None)
        self.assertFalse(answer.offer_ai)

    def test_blank_message_asks_to_repeat(self):
        answer = chatbot_service.answer_question(self.folder, "   ", None)
        self.assertIn("không nghe rõ", answer.reply)


class MauDonFoldersTests(unittest.TestCase):
    def setUp(self):
        # Kết quả gọi Drive API được cache theo (api_key, folder_id); xóa cache
        # trước mỗi test để một lần gọi thành công không làm sai kết quả của
        # test lỗi mạng chạy sau đó trên cùng cặp khóa.
        chatbot_service._mau_don_cache.clear()

    def test_missing_configuration_returns_friendly_error(self):
        folders, error = chatbot_service.list_mau_don_folders(None, None)
        self.assertEqual(folders, [])
        self.assertIsNotNone(error)

    def test_successful_call_returns_folder_list(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"files": [{"id": "abc", "name": "Mẫu đơn A"}]}
        with patch("services.chatbot_service.requests.get", return_value=response) as get:
            folders, error = chatbot_service.list_mau_don_folders("api-key", "folder-id")
        self.assertIsNone(error)
        self.assertEqual(folders, [{"id": "abc", "name": "Mẫu đơn A"}])
        get.assert_called_once()

    def test_request_failure_returns_error_message(self):
        import requests

        with patch("services.chatbot_service.requests.get", side_effect=requests.ConnectionError("boom")):
            folders, error = chatbot_service.list_mau_don_folders("api-key", "folder-id")
        self.assertEqual(folders, [])
        self.assertIn("boom", error)

    def test_second_call_within_ttl_does_not_hit_the_network_again(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"files": [{"id": "abc", "name": "Mẫu đơn A"}]}
        with patch("services.chatbot_service.requests.get", return_value=response) as get:
            chatbot_service.list_mau_don_folders("api-key", "folder-id")
            folders, error = chatbot_service.list_mau_don_folders("api-key", "folder-id")
        self.assertIsNone(error)
        self.assertEqual(folders, [{"id": "abc", "name": "Mẫu đơn A"}])
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
