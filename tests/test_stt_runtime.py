"""Integration check for the installed STT extra; no model download needed."""

import tempfile
import unittest
import wave
from pathlib import Path

from faster_whisper.audio import decode_audio


class STTRuntimeTests(unittest.TestCase):
    def test_pcm_wav_decodes_with_installed_pyav(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "silence.wav"
            with wave.open(str(audio), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes(b"\x00\x00" * 16000)
            self.assertEqual(len(decode_audio(str(audio))), 16000)


if __name__ == "__main__":
    unittest.main()
