"""OpenRouter adapter. Cloud use requires a user-provided key and explicit UI consent."""

import json
import math
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from meeting_copilot.intelligence import ANALYSIS_SCHEMA, SYSTEM_PROMPT

DEFAULT_MODEL = "openai/gpt-4.1-mini"
DEFAULT_INPUT_PRICE = 0.40
DEFAULT_OUTPUT_PRICE = 1.60


class AnalysisError(RuntimeError):
    """Safe diagnostic: adapter messages contain no credentials, provider bodies or speech."""


class LLMProvider(Protocol):
    def analyze(self, context: str) -> dict: ...


class OpenRouterLLM:
    def __init__(
        self,
        key,
        model=DEFAULT_MODEL,
        budget=0.50,
        input_price=DEFAULT_INPUT_PRICE,
        output_price=DEFAULT_OUTPUT_PRICE,
    ):
        for value in (budget, input_price, output_price):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid budget or token price.")
        self.key, self.model = key, model
        self.budget, self.input_price, self.output_price = budget, input_price, output_price
        self.usage = {"calls": 0, "failures": 0, "input_tokens": 0, "output_tokens": 0, "estimated_usd": 0.0}

    def analyze(self, context):
        if not self.key:
            raise ValueError("No OpenRouter API key; transcription continues locally.")
        # Byte count is a conservative upper bound for common byte-based tokenizers.
        reserve = (
            (
                len(context.encode())
                + len(SYSTEM_PROMPT.encode())
                + len(json.dumps(ANALYSIS_SCHEMA).encode())
                + 128
            )
            * self.input_price
            + 2048 * self.output_price
        ) / 1_000_000
        if self.usage["estimated_usd"] + reserve > self.budget or self.budget == 0:
            raise AnalysisError("Meeting analysis budget reached; transcription continues.")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": context}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "meeting_analysis", "strict": True, "schema": ANALYSIS_SCHEMA},
            },
            "max_tokens": 2048,
            "provider": {"data_collection": "deny", "allow_fallbacks": False, "require_parameters": True},
        }
        request = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        self.usage["calls"] += 1
        # Keep this reservation when transport/usage fails: the call may still be billable.
        self.usage["estimated_usd"] += reserve
        try:
            with urlopen(request, timeout=45) as response:
                result = json.loads(response.read(1024 * 1024))
            usage = result.get("usage", {})
            incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if type(incoming) is not int or type(outgoing) is not int or incoming < 0 or outgoing < 0:
                raise ValueError("Invalid usage response.")
            self.usage["input_tokens"] += incoming
            self.usage["output_tokens"] += outgoing
            # Prefer reported cost when available; otherwise use configured, clearly estimated rates.
            reported = usage.get("cost")
            self.usage["estimated_usd"] += -reserve + (
                reported
                if type(reported) in (int, float) and math.isfinite(reported) and reported >= 0
                else (incoming * self.input_price + outgoing * self.output_price) / 1_000_000
            )
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                self.usage["failures"] += 1
                raise AnalysisError(
                    "Model output reached its token limit; pending speech retained."
                    if choice.get("finish_reason") == "length"
                    else "Model response did not finish; pending speech retained."
                )
            return json.loads(choice["message"]["content"])
        except HTTPError as exc:
            self.usage["failures"] += 1
            reason = {
                401: "invalid API key",
                402: "insufficient API credits",
                403: "access denied",
                404: "model or eligible provider unavailable",
                429: "provider/account rate limited; retry later",
            }.get(exc.code, "provider request failed")
            raise AnalysisError(
                f"Analysis HTTP {exc.code}: {reason}. Local transcription continues."
            ) from None
        except (URLError, TimeoutError):
            self.usage["failures"] += 1
            raise AnalysisError("Analysis network unavailable; transcription continues.") from None
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            self.usage["failures"] += 1
            raise AnalysisError("Malformed analysis response; pending transcript retained.") from None
