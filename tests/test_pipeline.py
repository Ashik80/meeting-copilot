import unittest

from meeting_copilot.pipeline import ChunkBuffer, Session


class PipelineTests(unittest.TestCase):
    def test_failed_analysis_preserves_safe_reason_without_fake_ready_state(self):
        from unittest.mock import patch
        from urllib.error import HTTPError

        from meeting_copilot.llm import OpenRouterLLM

        events = []
        session = Session(
            lambda kind, payload: events.append((kind, payload)), llm=OpenRouterLLM("test-not-a-secret")
        )
        self.addCleanup(session.store.close)
        session.context.add("Private meeting speech.", "Meeting", 0)
        session.finish_analysis.set()
        with patch(
            "meeting_copilot.llm.urlopen",
            side_effect=HTTPError("https://example.com", 429, "private-provider-body", None, None),
        ):
            session._analysis()
        summary = next(payload for kind, payload in events if kind == "summary")
        self.assertIn("HTTP 429", summary)
        self.assertIn("Summary unavailable", summary)
        self.assertNotIn("private-provider-body", summary)
        self.assertNotIn("Private meeting speech", summary)
        self.assertTrue(any(kind == "analysis_status" and "HTTP 429" in payload for kind, payload in events))
        self.assertNotIn(("state", "Stopped — summary ready"), events)
        self.assertTrue(session.context.pending())

    def test_tts_failure_does_not_hide_subsequent_insights(self):
        class Provider:
            def analyze(self, context):
                return {
                    "status": "INSIGHTS",
                    "topic": "Payments",
                    "memory_updates": [],
                    "insights": [
                        {
                            "type": "CONCERN",
                            "priority": "HIGH",
                            "confidence": 0.95,
                            "relevance": 0.95,
                            "text": text,
                            "reason": "New evidence",
                            "issue_key": str(i),
                            "evidence_ids": ["t1"],
                            "material_change": False,
                        }
                        for i, text in enumerate(
                            ("Prevent duplicate charges", "Define refunds after cancellation")
                        )
                    ],
                }

        class BrokenSpeech:
            def speak(self, insight):
                raise RuntimeError("unavailable")

        events = []
        session = Session(lambda kind, payload: events.append(kind), llm=Provider(), tts=BrokenSpeech())
        self.addCleanup(session.store.close)
        session.active = True
        session.context.add("Discuss payments and refunds", "Meeting", 1)
        session._analyze_once(final=True)
        self.assertEqual(events.count("insight"), 2)

    def test_transcription_provider_is_replaceable_and_source_is_preserved(self):
        class Provider:
            def transcribe(self, audio):
                return "What happens if Redis fails?"

        session = Session(lambda *args: None, stt_factory=Provider)
        self.addCleanup(session.store.close)
        session.audio.put(("Me", b"\x10\x10" * 16000, 1))
        session.stt_done.set()
        session._stt()
        self.assertEqual(session.context.recent[0]["source"], "Me")
        self.assertIn("Redis", session.context.recent[0]["text"])

    def test_pause_preserves_partial_speech_and_cannot_interrupt_stop(self):
        session = Session(lambda *args: None)
        self.addCleanup(session.store.close)
        session.active = True
        session.chunks.feed("Meeting", b"\x00" * 16000, 1)
        session.pause()
        self.assertEqual(session.audio.qsize(), 1)
        session.stopping = True
        session.active = True
        session.pause()
        self.assertTrue(session.active)

    def test_sources_are_separate_and_partial_audio_is_flushed(self):
        chunks = ChunkBuffer(seconds=1)
        self.assertEqual(chunks.feed("Meeting", b"\x00" * 16000, 1), [])
        self.assertEqual(chunks.feed("Me", b"\x01" * 16000, 2), [])
        packets = chunks.feed("Meeting", b"\x02" * 16000, 3)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0][0], "Meeting")
        self.assertEqual(len(packets[0][1]), 32000)
        self.assertEqual(chunks.flush()[0][0], "Me")
        self.assertEqual(chunks.flush(), [])


if __name__ == "__main__":
    unittest.main()
