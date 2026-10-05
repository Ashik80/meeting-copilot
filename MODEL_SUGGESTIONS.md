# Suggested models for Meeting Copilot

**Use GPT-4.1 Mini for now.** It is the current default and passed two recent checks with your OpenRouter key. If you want to compare alternatives, try GPT-5 Mini next—but treat it as an unverified candidate, not a guaranteed upgrade.

All options below are non-Gemini models available through OpenRouter. Prices were checked against the public model catalog on 2026-10-05. They are advertised USD prices per **million tokens**, not a fixed cost per meeting.

## Recommended order

| Order | Model ID to paste | Input $/1M | Output $/1M | Recommendation and evidence |
| --- | --- | ---: | ---: | --- |
| 1 | `openai/gpt-4.1-mini` | 0.40 | 1.60 | **Current default.** Faster in our earlier comparison; 9/11 reviewed synthetic cases. Two recent checks succeeded without rate-limit errors. It did raise unnecessary findings about an addressed payment safeguard and an intentional demo omission. |
| 2 | `openai/gpt-5-mini` | 0.25 | 2.00 | **Next comparison candidate.** Worth testing for reasoning about earlier constraints and requirements. Not benchmarked here; reasoning-token limits may require adapter changes. |
| 3 | `deepseek/deepseek-v3.2` | 0.28 | 0.42 | **Low-cost comparison candidate.** Not tested here for useful insights, quietness, availability, or response validation. |
| 4 | `anthropic/claude-haiku-4.5` | 1.00 | 5.00 | **Higher-cost comparison candidate.** Consider if the cheaper options miss important issues. Not tested here; do not assume it is better or less noisy. |

### Not recommended for your current setup

| Model ID | Input $/1M | Output $/1M | Why |
| --- | ---: | ---: | --- |
| `qwen/qwen3-235b-a22b-2507` | 0.0875 | 0.35 | Initially scored 10/11 reviewed synthetic cases, but your live use repeatedly hit rate limits. Longer synthetic batches also produced unsupported insight categories. Valid memory is now preserved when individual findings are invalid, but this does not resolve provider availability. Reconsider only after routing/limits improve. |
| `openai/gpt-oss-120b` | 0.037 | 0.17 | Our tested route had rate-limit and response/schema failures. Very cheap, but not a dependable choice in the current setup. Those failures are not proof that the underlying model cannot reason well. |

## Switching models

In **Analysis & storage**, set:

1. **Model** to the exact ID above.
2. **Input $ / 1M tokens** and **Output $ / 1M tokens** to that model's prices. These fields do not automatically update when you edit the model name.
3. Your meeting budget, then enable cloud-analysis consent.

Change settings before starting a fresh meeting. If a session has already ended, save any summary/transcript you want to keep before using **Delete / Clear meeting**. Clearing also removes pasted project context, so add it again if needed.

Watch **Analysis** for “Analysis accepted.” If HTTP 429 continues across different models, check OpenRouter account limits/credits and provider availability; switching models is not a guaranteed fix for an account-wide limit.

The adapter requests strict JSON output and privacy-restricted providers, with provider fallback disabled. Catalog support for structured output does not guarantee that every routed provider fully honors the schema; local validation still applies. Advertised prices can differ from the actual eligible route, cached-token billing, and fees. Keep an account/key spending limit as well as the app's estimated budget guard.

## Limits of our testing

- The 9/11 and 10/11 scores came from one earlier synthetic run per model plus content review—not statistical accuracy estimates or real-meeting comparisons with Meet's assistant.
- GPT-5 Mini, DeepSeek V3.2, and Claude Haiku 4.5 are suggestions to evaluate, not verified drop-in replacements.
- The current adapter caps generated output at 2,048 tokens and has no reasoning-effort control. Reasoning models can exhaust that allowance before producing usable JSON; adapter changes may be needed.
- Changing the analysis model does **not** improve local Whisper transcription accuracy.
- No real meeting recordings were uploaded for these comparisons.

Pricing source: https://openrouter.ai/api/v1/models
