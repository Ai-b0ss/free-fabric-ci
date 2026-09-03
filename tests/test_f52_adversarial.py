from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import desktop.combined_acceptance as ca


class F52AdversarialContractTests(unittest.TestCase):
    def setUp(self):
        self.chat = ca.Surface("qwen", "http://127.0.0.1:18080", frozenset({"qwen-test"}), "openai-chat")
        self.responses = ca.Surface("notion", "http://127.0.0.1:18081", frozenset({"notion-test"}), "responses")
        self.nonce = "ff-f52-adversarial"

    def _chat_first(self, *, arguments=None, call_id="call-1", calls=1):
        call = {"id": call_id, "type": "function", "function": {"name": "ff_echo", "arguments": json.dumps(arguments if arguments is not None else {"value": self.nonce})}}
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call] * calls}}]}

    def _chat_final(self, *, tool_calls=None, content=None):
        message = {"role": "assistant", "content": self.nonce if content is None else content}
        if tool_calls is not None:
            message["tool_calls"] = tool_calls
        return {"choices": [{"message": message}]}

    def _responses_first(self, *, arguments=None, call_id="call-2"):
        return {"output": [{"type": "function_call", "name": "ff_echo", "call_id": call_id, "arguments": json.dumps(arguments if arguments is not None else {"value": self.nonce})}]}

    def test_chat_rejects_extra_arguments(self):
        with patch.object(ca, "_request_json", return_value=self._chat_first(arguments={"value": self.nonce, "extra": 1})):
            with self.assertRaisesRegex(RuntimeError, "tool_call_contract_invalid"):
                ca._openai_tool_roundtrip(self.chat, 1, self.nonce)

    def test_chat_rejects_whitespace_call_id(self):
        with patch.object(ca, "_request_json", return_value=self._chat_first(call_id="   ")):
            with self.assertRaisesRegex(RuntimeError, "tool_call_contract_invalid"):
                ca._openai_tool_roundtrip(self.chat, 1, self.nonce)

    def test_chat_rejects_multiple_initial_calls(self):
        with patch.object(ca, "_request_json", return_value=self._chat_first(calls=2)):
            with self.assertRaisesRegex(RuntimeError, "tool_call_count_invalid"):
                ca._openai_tool_roundtrip(self.chat, 1, self.nonce)

    def test_chat_rejects_second_tool_call(self):
        with patch.object(ca, "_request_json", side_effect=[self._chat_first(), self._chat_final(tool_calls=[{"id": "again"}])]):
            with self.assertRaisesRegex(RuntimeError, "tool_called_more_than_once"):
                ca._openai_tool_roundtrip(self.chat, 1, self.nonce)

    def test_chat_rejects_non_object_final_message(self):
        final = {"choices": [{"message": "not-an-object"}]}
        with patch.object(ca, "_request_json", side_effect=[self._chat_first(), final]):
            with self.assertRaisesRegex(RuntimeError, "tool_result_contract_invalid"):
                ca._openai_tool_roundtrip(self.chat, 1, self.nonce)

    def test_responses_rejects_extra_arguments(self):
        with patch.object(ca, "_request_json", return_value=self._responses_first(arguments={"value": self.nonce, "extra": 1})):
            with self.assertRaisesRegex(RuntimeError, "tool_call_contract_invalid"):
                ca._responses_tool_roundtrip(self.responses, 1, self.nonce)

    def test_responses_rejects_whitespace_call_id(self):
        with patch.object(ca, "_request_json", return_value=self._responses_first(call_id="  ")):
            with self.assertRaisesRegex(RuntimeError, "tool_call_contract_invalid"):
                ca._responses_tool_roundtrip(self.responses, 1, self.nonce)

    def test_responses_rejects_second_function_call(self):
        final = {"output": [{"type": "function_call", "name": "ff_echo", "call_id": "again", "arguments": "{}"}]}
        with patch.object(ca, "_request_json", side_effect=[self._responses_first(), final]):
            with self.assertRaisesRegex(RuntimeError, "tool_called_more_than_once"):
                ca._responses_tool_roundtrip(self.responses, 1, self.nonce)

    def test_responses_rejects_fake_text_part(self):
        final = {"output": [{"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": self.nonce}]}]}
        with patch.object(ca, "_request_json", side_effect=[self._responses_first(), final]):
            with self.assertRaisesRegex(RuntimeError, "tool_result_marker_invalid"):
                ca._responses_tool_roundtrip(self.responses, 1, self.nonce)


if __name__ == "__main__":
    unittest.main()
