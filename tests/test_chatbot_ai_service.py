from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openai import OpenAIError

from services.chatbot_ai_service import ChatbotAIError, answer_question, is_configured


class ChatbotAIServiceTests(unittest.TestCase):
    def test_is_configured_reflects_api_key_presence(self):
        self.assertTrue(is_configured({"OPENAI_API_KEY": "sk-test"}))
        self.assertFalse(is_configured({"OPENAI_API_KEY": ""}))
        self.assertFalse(is_configured({}))

    @patch("services.chatbot_ai_service.OpenAI")
    def test_appends_disclaimer_and_sends_known_procedures_in_system_prompt(self, openai_class):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Bạn cần liên hệ UBND phường."))]
        )
        openai_class.return_value = client

        reply = answer_question(
            "Thủ tục đổi tên khai sinh cần gì?",
            ["Trợ cấp hưu trí xã hội"],
            api_key="test-key",
            model="gpt-4o-mini",
        )

        self.assertIn("Bạn cần liên hệ UBND phường.", reply)
        self.assertIn("tham khảo từ AI", reply)
        request = client.chat.completions.create.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertIn("Trợ cấp hưu trí xã hội", request["messages"][0]["content"])
        self.assertEqual(request["messages"][1]["content"], "Thủ tục đổi tên khai sinh cần gì?")

    @patch("services.chatbot_ai_service.OpenAI")
    def test_returns_safe_message_for_exhausted_credit(self, openai_class):
        error = OpenAIError("provider details must not be shown")
        error.code = "credit_balance_exhausted"
        client = MagicMock()
        client.chat.completions.create.side_effect = error
        openai_class.return_value = client

        with self.assertRaisesRegex(ChatbotAIError, "hết tín dụng"):
            answer_question("cau hoi", [], api_key="test-key", model="gpt-4o-mini")

    def test_blank_question_raises_before_calling_openai(self):
        with self.assertRaises(ChatbotAIError):
            answer_question("   ", [], api_key="test-key", model="gpt-4o-mini")

    @patch("services.chatbot_ai_service.OpenAI")
    def test_empty_reply_raises(self, openai_class):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="   "))]
        )
        openai_class.return_value = client

        with self.assertRaises(ChatbotAIError):
            answer_question("cau hoi", [], api_key="test-key", model="gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
