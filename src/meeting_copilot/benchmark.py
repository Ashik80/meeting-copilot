"""Offline, opt-in benchmark for consented English/Bangla WAV samples."""

import argparse
import json
import os
import sys
import time
import unicodedata
import uuid
import wave
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(" " if unicodedata.category(c).startswith("P") else c for c in text).split())


def distance(reference, hypothesis):
    row = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, 1):
        next_row = [i]
        for j, actual in enumerate(hypothesis, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (expected != actual)))
        row = next_row
    return row[-1]


def error_rates(reference: str, hypothesis: str) -> tuple[float, float]:
    reference, hypothesis = normalize(reference), normalize(hypothesis)
    if not reference:
        raise ValueError("Reference text must contain words.")
    return (
        distance(reference.split(), hypothesis.split()) / len(reference.split()),
        distance(reference, hypothesis) / len(reference),
    )


def load_samples(path: Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest must be a nonempty JSON array.")
    seen = set()
    samples = []
    for entry in entries:
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(k), str) for k in ("id", "audio", "reference", "group")
        ):
            raise ValueError("Each sample requires string id, audio, reference, and group.")
        if not entry["id"].strip() or entry["id"] in seen:
            raise ValueError("Sample IDs must be nonempty and unique.")
        seen.add(entry["id"])
        if entry["group"] not in ("english", "bangla", "mixed"):
            raise ValueError("Sample group must be english, bangla, or mixed.")
        reference = normalize(entry["reference"])
        if not reference or len(reference) > 20000:
            raise ValueError("Reference must contain words and be at most 20000 characters.")
        audio = (path.parent / entry["audio"]).resolve()
        if not audio.is_file() or audio.stat().st_size > 24 * 1024 * 1024:
            raise ValueError("Audio must exist and be smaller than 24 MiB.")
        try:
            with wave.open(str(audio), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
                    raise ValueError("Use mono, 16-bit PCM, 16000 Hz WAV files.")
                duration = source.getnframes() / source.getframerate()
        except (wave.Error, EOFError) as exc:
            raise ValueError("Invalid PCM WAV file.") from exc
        if not 0 < duration <= 300:
            raise ValueError("Use samples between zero and 300 seconds long.")
        samples.append({**entry, "audio": audio, "duration_seconds": duration})
    return samples


class TranscriptionProvider(Protocol):
    def transcribe(self, audio: Path) -> str: ...


class LocalWhisper:
    def __init__(self, model: str):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=4)

    def transcribe(self, audio) -> str:
        segments, _ = self.model.transcribe(
            str(audio) if isinstance(audio, Path) else audio,
            language=None,
            task="transcribe",
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(
            segment.text.strip()
            for segment in segments
            if segment.avg_logprob > -1.0 and segment.no_speech_prob < 0.8
        )


def multipart_audio(audio: bytes, model: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("model", model), ("response_format", "json")):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
    )
    parts.extend((audio, f"\r\n--{boundary}--\r\n".encode()))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class OpenAITranscription:
    def __init__(self, model: str, allow_cloud: bool):
        if not allow_cloud:
            raise ValueError("Cloud upload requires --allow-cloud-upload.")
        self.key = os.environ.get("OPENAI_API_KEY", "")
        if not self.key:
            raise ValueError("Set OPENAI_API_KEY in your environment, not on the command line.")
        if model not in ("gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"):
            raise ValueError("Select a supported OpenAI transcription model.")
        self.model = model

    def transcribe(self, audio: Path) -> str:
        body, content_type = multipart_audio(audio.read_bytes(), self.model)
        request = Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": content_type},
        )
        try:
            with urlopen(request, timeout=90) as response:
                payload = json.loads(response.read(1024 * 1024))
        except HTTPError as exc:
            raise RuntimeError(f"OpenAI HTTP {exc.code}; no automatic retry or provider switch.") from None
        except URLError:
            raise RuntimeError("OpenAI network failure.") from None
        except (TimeoutError, json.JSONDecodeError):
            raise RuntimeError("OpenAI timed out or returned invalid JSON.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise TypeError("OpenAI returned an invalid transcription response.")
        return payload["text"]


def run_benchmark(samples: list[dict], transcribe) -> dict:
    results = []
    totals = {}
    for sample in samples:
        start = time.perf_counter()
        hypothesis = transcribe(sample["audio"])
        elapsed = time.perf_counter() - start
        wer, cer = error_rates(sample["reference"], hypothesis)
        ref = normalize(sample["reference"])
        words, chars = len(ref.split()), len(ref)
        total = totals.setdefault(sample["group"], [0, 0, 0, 0])
        total[0] += round(wer * words)
        total[1] += words
        total[2] += round(cer * chars)
        total[3] += chars
        results.append(
            {
                "id": sample["id"],
                "group": sample["group"],
                "reference": sample["reference"],
                "hypothesis": hypothesis,
                "word_error_rate": wer,
                "character_error_rate": cer,
                "processing_seconds": elapsed,
                "duration_seconds": sample["duration_seconds"],
                "real_time_factor": elapsed / sample["duration_seconds"],
            }
        )
    return {
        "samples": results,
        "groups": {
            group: {"word_error_rate": values[0] / values[1], "character_error_rate": values[2] / values[3]}
            for group, values in totals.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--provider", choices=("local", "openai"), default="local")
    parser.add_argument("--model", help="Default: multilingual small / gpt-4o-mini-transcribe")
    parser.add_argument("--allow-cloud-upload", action="store_true")
    parser.add_argument(
        "--output", type=Path, required=True, help="New report file; contains reference and recognized text."
    )
    args = parser.parse_args()
    try:
        samples = load_samples(args.manifest)
        # Reserve a private file before any paid work. Never overwrite an existing report.
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except (ValueError, OSError) as exc:
        print(
            f"Invalid input/output ({type(exc).__name__}); check manifest and report path.", file=sys.stderr
        )
        return 1
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            model = args.model or ("small" if args.provider == "local" else "gpt-4o-mini-transcribe")
            if args.provider == "local" and model.endswith(".en"):
                raise ValueError("Use a multilingual model, not an English-only .en model.")
            start = time.perf_counter()
            provider = (
                LocalWhisper(model)
                if args.provider == "local"
                else OpenAITranscription(model, args.allow_cloud_upload)
            )
            setup = time.perf_counter() - start
            report = run_benchmark(samples, provider.transcribe)
            report.update(
                provider=args.provider,
                model=model,
                setup_seconds=setup,
                note="Offline whole-file test; not a live-streaming latency measurement. First inference may include warm-up. CER counts Unicode code points, including spaces.",
            )
            json.dump(report, output, ensure_ascii=False, indent=2)
        print("Benchmark complete. Private report saved; inspect hypotheses as well as scores.")
        return 0
    except Exception as exc:  # noqa: BLE001 -- prevent provider errors leaking credentials or speech
        args.output.unlink(missing_ok=True)
        # Provider/library exception text can contain credentials, paths or meeting content.
        if isinstance(exc, ValueError):
            print("Check provider consent, key, and multilingual model selection.", file=sys.stderr)
        else:
            print(f"Benchmark failed ({type(exc).__name__}); no transcript logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
