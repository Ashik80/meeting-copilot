# Meeting Copilot: Linux MVP setup

This is an English-first local desktop prototype, not yet a verified production meeting assistant. Whisper supports multilingual input, but Bangla accuracy has not been benchmarked. No meeting files have been uploaded during development.

## Install and launch

Run from a graphical terminal in your Ubuntu GNOME session:

```bash
sudo apt install pulseaudio-utils pavucontrol ffmpeg
uv sync --python 3.12 --extra stt --extra desktop --extra dev
uv run meeting-copilot
```

The application never starts listening on launch. Python 3.12 is pinned for the tested inference stack. The current machine already has PipeWire-Pulse, audio tools, the dependencies, and multilingual Whisper Base/Small in the local model cache. On another machine, first use downloads the model; start with Base if inference falls behind. Missing Qt system libraries may require installing `libxcb-cursor0`, `libxkbcommon0`, `libegl1`, or `libopengl0` as indicated by the startup error.

## Route Google Meet to your Bluetooth earbuds

1. Connect the earbuds and start Meet playback so the audio server exposes the active call profile.
2. Open the copilot and press **Refresh devices**. Select the earbuds as playback output.
3. If you want your own speech transcribed, check **Include microphone** and select the earbud microphone. The Bluetooth call profile can reduce playback quality or change device names. Select devices after that profile is active.
4. Press **Create isolated Meet route**.
5. Open `pavucontrol`. In **Playback**, move the Meet/browser audio stream to **MeetingCopilot**, not **MeetingCopilotTTS**. Browser streams may include other tabs: close unrelated audio tabs during the meeting.
6. In Meet, keep the microphone set to the actual earbud microphone. Never choose a monitor or loopback as Meet's microphone.
7. Press **Start**. Confirm the capture status and live transcript before relying on it.

The meeting sink loops back to the selected physical output; its monitor supplies STT. No device name is hard-coded, and no default microphone is changed. Disconnects retry the selected capture source rather than silently choosing another device. If a Bluetooth profile changes device identities, or the audio server restarts and destroys the virtual route, stop/clear and create a new route. Actual earbud disconnect/profile behavior remains unverified.

## Enable non-Gemini cloud analysis

Create an API key at https://openrouter.ai/settings/keys and add credits only if you approve paid use. Your ChatGPT subscription does not pay for this API.

In **Analysis & storage**:

- Paste the key into the password-mode session-only field, or set `OPENROUTER_API_KEY` in the launching environment.
- Check explicit consent to sending bounded transcript excerpts, meeting memory, prior issues, and selected project context.
- Choose a model, mode, confidence threshold, estimated per-million token prices, and meeting budget.
- Provisional default: `openai/gpt-4.1-mini`, switched from Qwen after persistent rate limits during live use. Two synthetic checks with the user's key succeeded after the switch was requested. GPT-4.1 Mini was faster but produced more unnecessary findings in the earlier quality trial. Neither model is proven better than Meet's assistant in real meetings. Default estimated prices are $0.40/M input and $1.60/M output tokens; the $0.50 meeting budget is unchanged.
- Without consent or a key, local transcription still works, but topic understanding and intelligent findings are not generated.

The adapter requests providers that deny data collection and disables inference-provider fallback. This is not a blanket privacy guarantee: review OpenRouter and the selected provider's retention policies. There are no web search, code execution, or external tool calls.

`environment.example` lists all supported environment variables. The harness prevented creating `.env.example`; copy the template to that name locally if needed. The app does **not** automatically load `.env` files. To use your local `.env`, launch with `uv run --env-file .env meeting-copilot`; protect the file with `chmod 600 .env`. Cloud-analysis consent is still required. Never paste keys into chat or commit a real key. Audio devices, STT model, TTS, retention, mode, confidence, and budget are UI settings, not required environment variables.

Budget tracking prefers provider-reported cost; otherwise it estimates using configured rates. Unknown billing after failed requests retains a conservative reservation. Set account/key-level spending limits too: local estimates cannot guarantee a provider bill, particularly if configured rates are stale.

## Quiet analysis and memory

Speech is transcribed in approximately eight-second chunks with voice activity detection. This is chunked inference, not native streaming. Short boundaries can clip words; simultaneous speakers and noisy audio remain difficult.

Analysis waits at least 20 seconds between attempts and requires enough new speech, or waits until meaningful pending speech is 30 seconds old. Silence generates no LLM requests. A model may return `NO_INSIGHT`; accepted findings need concrete evidence, confidence and relevance thresholds, and pass deduplication. Confidence is a model estimate, not a calibrated probability.

Recent context is limited to about ten minutes / 300 entries. Requests have a 24,000-byte context cap, at most 50 new entries per batch, and a bounded structured memory of 200 facts. Older hard constraints are prioritized; clipped context is reported. This is not perfect recall of arbitrarily long meetings. Default project context retrieval uses keyword matching over pasted text or local UTF-8 text/Markdown, up to 12,000 input characters. Semantic retrieval and large-document ingestion are deferred.

## Optional private local TTS

TTS is off by default. Install Piper separately, for example:

```bash
uv tool install --python 3.12 piper-tts
piper --help
```

Download a compatible English `.onnx` voice and its `.onnx.json` config from the upstream Piper voice catalog. Check the engine and voice licenses. Enter both paths in **Private TTS**, select a mode, and confirm headphones and verified private routing before starting.

TTS uses its own null sink and a fixed loopback to the selected output; it does not play through the default sink or captured meeting sink. If the selected output is absent, it mutes rather than intentionally falling back to speakers. Routing must still be tested on your actual device/server setup. User-created loopbacks and acoustic leakage cannot be ruled out by software.

**Before using TTS in a real meeting:**

1. Ask a test-call participant whether they hear the spoken insight.
2. Confirm the copilot does not transcribe its own TTS.
3. Verify Meet is using the physical microphone, not a monitor.
4. Disconnect the earbuds during a test and confirm no speech emerges from laptop speakers.
5. Check **Mute TTS** / `Ctrl+M` stops speech immediately.

Pause and dismiss mute TTS. Use **Enable TTS** explicitly to re-enable it. Voice synthesis/playback and Bluetooth leakage tests have not been performed here because Piper voices and the earbuds were unavailable. Bangla TTS is not included.

## Controls, summaries, and privacy

In-app shortcuts (not global Wayland shortcuts):

| Shortcut | Action |
| --- | --- |
| Ctrl+Return | Start |
| Ctrl+P | Pause capture |
| Ctrl+R | Resume capture |
| Ctrl+. | Stop and process final pending speech |
| Ctrl+L | Confirm delete/clear meeting |
| Ctrl+M | Mute/cancel TTS |

The panel is movable/resizable, collapsible, supports dark/light appearance, opacity, and an on-top request. GNOME Wayland may ignore opacity or on-top requests; try the window menu's **Always on Top** where available. Window shortcuts work only while the panel has focus. These compositor behaviors remain manual checks.

No raw captured audio is written to disk. With retention off, transcript and structured state stay in bounded session memory, and the SQLite feedback store is in memory. Opt-in transcript retention creates a unique private SQLite file under Qt's app-local data directory, in `meeting-copilot/meetings`. Files use owner-only permissions. The app has no encrypted database or persisted-meeting browser yet.

Stopping drains queued transcription, attempts final analysis, and prepares structured Markdown from successfully analyzed memory. The separate **Analysis** status keeps safe API/validation errors visible even while STT updates. If no speech was successfully analyzed, the app shows **Summary unavailable**, not an empty completed-summary template. Partial summaries carry a warning in their exported content too. Invalid individual findings are omitted without discarding otherwise valid, evidence-backed memory; invalid memory/envelopes still reject the batch. Providers may not fully honor the requested strict response schema, so local validation remains mandatory. HTTP 429 means provider/account rate limiting; pause and retry later rather than relying on an empty summary. Without cloud analysis, the summary has no inferred decisions or requirements. **Save** on an insight bookmarks it for the summary; export the final summary to keep it. Summary-only retention is achieved by leaving transcript retention off and explicitly exporting the summary.

**Delete / Clear meeting** deletes the current app-owned database and in-app content, not user-supplied recordings or separately saved exports. Exports are private and atomically replaced. Retained databases from earlier application runs must currently be removed manually from the app data directory. Do not interpret deletion as guaranteed forensic erasure on an SSD.

## Quality evaluation: synthetic comparison completed

The first trial covered six synthetic examples. Manual review prompted stronger evidence checks and five additional cases. Both Qwen and GPT-4.1 Mini were then tested against the same eleven examples and revised prompt. No real meeting data was uploaded.

| Model | Reviewed cases passed | Mean request time | Estimated cost for 11 calls |
| --- | --- | --- | --- |
| Qwen3 235B A22B 2507 | 10/11 | 4.86 seconds | $0.00125 |
| GPT-4.1 Mini | 9/11 | 2.13 seconds | $0.00532 |

Qwen caught duplicate-payment risk, earlier authentication/database conflicts, missing document authorization, and a project retention conflict. It stayed quiet for ordinary speech, addressed payment protection, intentional demo omissions, tentative choices, and dismissed repeats. Its known failure was flagging an explicitly retired constraint as an unresolved contradiction. GPT-4.1 Mini unnecessarily questioned already-addressed protection and an intentional demo omission. GPT-OSS's initial route had request/schema failures; those failures are not a clean reasoning-quality score.

Original reports are in `benchmark-results/*-quality-v2.json`; the reviewed comparison is `benchmark-results/quality-comparison-reviewed.json`. GPT-4.1 Mini's original automated score was 8/11: review corrected one category-only false failure, since a concrete conflict labeled CONCERN was useful too. This was one run per model, with synthetic examples and screening heuristics, not a statistical accuracy estimate or proof of superiority to Meet. Qwen was initially selected on those results, but persistent rate limits later made it unsuitable for current live use. The default is now GPT-4.1 Mini for availability, not because it won the initial noise-quality comparison. A consented real-meeting evaluation and repeated runs remain necessary.

To rerun using a local `.env` (use new output filenames):

```bash
mkdir -p benchmark-results
uv run --env-file .env copilot-evaluate --allow-cloud-upload \
  --model qwen/qwen3-235b-a22b-2507 --input-price 0.0875 --output-price 0.35 \
  --budget 0.05 --output benchmark-results/qwen-quality-repeat.json

uv run --env-file .env copilot-evaluate --allow-cloud-upload \
  --model openai/gpt-4.1-mini --input-price 0.40 --output-price 1.60 \
  --budget 0.05 --output benchmark-results/gpt41-quality-repeat.json
```

Advertised prices were checked against OpenRouter's public model catalog during this evaluation. Recheck them before later runs; routed/cached token costs can differ. Inspect the actual findings and evidence, not just the pass count.

## Verification commands

```bash
uv run ruff check src tests
uv run ruff format --check src tests
QT_QPA_PLATFORM=offscreen uv run python -m unittest discover -s tests -v
uv run python tests/check_audio.py
uv run python tests/check_pipeline.py recordings/live-check.wav
```

`check_audio.py` captures only a generated tone from an inaudible null sink. `check_pipeline.py` replays the specified prerecorded WAV into an inaudible null sink, with local STT, no microphone, and no cloud calls. Neither integration check starts a real meeting recording. Unit/offscreen tests do not establish visible GNOME behavior, live API quality, or private earbud TTS.
