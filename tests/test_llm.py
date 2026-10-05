import json
import unittest
from unittest.mock import patch

from meeting_copilot.llm import AnalysisError, OpenRouterLLM


class LLMTests(unittest.TestCase):
    def test_default_uses_verified_gpt41_mini_model_and_prices(self):
        provider = OpenRouterLLM(key="")
        self.assertEqual(provider.model, "openai/gpt-4.1-mini")
        self.assertEqual(provider.input_price, 0.40)
        self.assertEqual(provider.output_price, 1.60)

    def test_request_asks_for_strict_category_schema(self):
        from unittest.mock import MagicMock

        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
        ).encode()
        with patch("meeting_copilot.llm.urlopen", return_value=response) as network:
            OpenRouterLLM(key="test-not-a-secret").analyze("{}")
        payload = json.loads(network.call_args.args[0].data)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        schema = payload["response_format"]["json_schema"]["schema"]
        category = schema["properties"]["insights"]["items"]["properties"]["type"]
        self.assertIn("CONCERN", category["enum"])
        self.assertNotIn("SUMMARY", category["enum"])
        self.assertTrue(payload["provider"]["require_parameters"])

    def test_no_key_never_calls_the_network(self):
        provider = OpenRouterLLM(key="", budget=1)
        with patch("meeting_copilot.llm.urlopen") as network:
            with self.assertRaises(ValueError):
                provider.analyze("{}")
            network.assert_not_called()

    def test_budget_exhaustion_never_calls_the_network(self):
        provider = OpenRouterLLM(key="test-not-a-secret", budget=0)
        with patch("meeting_copilot.llm.urlopen") as network:
            with self.assertRaises(RuntimeError):
                provider.analyze("{}")
            network.assert_not_called()

    def test_unknown_billing_reserves_budget_when_response_is_malformed(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return b"invalid JSON"

        provider = OpenRouterLLM(key="test-not-a-secret", budget=1)
        with patch("meeting_copilot.llm.urlopen", return_value=Response()), self.assertRaises(AnalysisError):
            provider.analyze("{}")
        self.assertGreater(provider.usage["estimated_usd"], 0)

    def test_usage_is_accounted_even_when_model_returns_malformed_json(self):
        payload = {
            "id": "test",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "not json"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return json.dumps(payload).encode()

        provider = OpenRouterLLM(key="test-not-a-secret", budget=1)
        with patch("meeting_copilot.llm.urlopen", return_value=Response()), self.assertRaises(AnalysisError):
            provider.analyze("{}")
        self.assertEqual(provider.usage["input_tokens"], 1000)
        self.assertEqual(provider.usage["output_tokens"], 100)
        self.assertGreater(provider.usage["estimated_usd"], 0)


if __name__ == "__main__":
    unittest.main()
