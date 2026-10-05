import json
import tempfile
import unittest
import wave
from pathlib import Path

from meeting_copilot.benchmark import (
    OpenAITranscription,
    error_rates,
    load_samples,
    multipart_audio,
    run_benchmark,
)


class BenchmarkTests(unittest.TestCase):
    def test_cloud_provider_requires_explicit_upload_consent(self):
        with self.assertRaisesRegex(ValueError, "--allow-cloud-upload"):
            OpenAITranscription("gpt-4o-mini-transcribe", allow_cloud=False)

    def test_word_and_character_error_rates(self):
        self.assertEqual(error_rates("Retry the payment.", "retry the payment"), (0, 0))
        self.assertAlmostEqual(error_rates("retry the payment", "retry payment")[0], 1 / 3)
        self.assertEqual(error_rates("Redis বন্ধ হলে retry করব", "Redis বন্ধ হলে retry করব"), (0, 0))
        self.assertEqual(error_rates("কথা", "কথা কথা")[0], 1)
        self.assertEqual(error_rates("a", "b"), (1, 1))
        with self.assertRaises(ValueError):
            error_rates("...", "hallucinated speech")

    def test_manifest_validation_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "mixed.wav"
            with wave.open(str(audio), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes(b"\x00\x00" * 16000)
            manifest = root / "samples.json"
            entry = {
                "id": "mixed",
                "audio": "mixed.wav",
                "reference": "Redis বন্ধ হলে retry করব",
                "group": "mixed",
            }
            manifest.write_text(json.dumps([entry]), encoding="utf-8")
            samples = load_samples(manifest)
            self.assertEqual(samples[0]["audio"], audio)
            self.assertEqual(samples[0]["duration_seconds"], 1)
            manifest.write_text(json.dumps([entry, entry]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_samples(manifest)
            entry["group"] = "unsupported"
            manifest.write_text(json.dumps([entry]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_samples(manifest)
            entry["group"] = "mixed"
            entry["audio"] = "missing.wav"
            manifest.write_text(json.dumps([entry]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_samples(manifest)

    def test_report_measures_quality_and_groups_without_storing_audio(self):
        samples = [
            {
                "id": "mixed",
                "audio": Path("unused.wav"),
                "reference": "Redis বন্ধ হলে retry করব",
                "group": "mixed",
                "duration_seconds": 10,
            }
        ]
        report = run_benchmark(samples, lambda _: "Redis বন্ধ হলে retry করব")
        self.assertEqual(report["samples"][0]["word_error_rate"], 0)
        self.assertEqual(report["groups"]["mixed"]["word_error_rate"], 0)
        self.assertEqual(report["samples"][0]["duration_seconds"], 10)
        self.assertGreaterEqual(report["samples"][0]["processing_seconds"], 0)
        self.assertNotIn("audio", report["samples"][0])

    def test_multipart_preserves_audio_and_omits_forced_language(self):
        body, content_type = multipart_audio(b"RIFFtest", "gpt-4o-mini-transcribe")
        self.assertIn(b"RIFFtest", body)
        self.assertIn(b"gpt-4o-mini-transcribe", body)
        self.assertIn(b'name="response_format"', body)
        self.assertNotIn(b'name="language"', body)
        self.assertIn("boundary=", content_type)


if __name__ == "__main__":
    unittest.main()
