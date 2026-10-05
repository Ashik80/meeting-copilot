import json
import unittest

from meeting_copilot.intelligence import MeetingContext, QuietAnalyzer, validate_analysis


class IntelligenceTests(unittest.TestCase):
    def result(self, text="Payment retries lack an idempotency key."):
        return {
            "status": "INSIGHTS",
            "topic": "Payments",
            "memory_updates": [],
            "insights": [
                {
                    "type": "CONCERN",
                    "priority": "HIGH",
                    "confidence": 0.95,
                    "relevance": 0.96,
                    "text": text,
                    "reason": "Retries can charge twice.",
                    "issue_key": "payment-idempotency",
                    "evidence_ids": ["t1"],
                    "material_change": False,
                }
            ],
        }

    def test_unanalyzed_speech_has_no_fake_summary(self):
        context = MeetingContext()
        context.add("We agreed to use PostgreSQL.", "Meeting", 0)
        summary = QuietAnalyzer(context).summary_markdown()
        self.assertIn("Summary unavailable", summary)
        self.assertNotIn("Waiting for discussion", summary)
        self.assertNotIn("Not established", summary)

    def test_partial_summary_warns_inside_exported_content(self):
        context = MeetingContext()
        context.add("Use PostgreSQL.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        analyzer.accept(
            {
                "status": "NO_INSIGHT",
                "topic": "Storage",
                "memory_updates": [{"kind": "decision", "text": "Use PostgreSQL.", "evidence_ids": ["t1"]}],
                "insights": [],
            }
        )
        context.add("We also agreed to add a retry worker.", "Meeting", 1)
        self.assertIn("Incomplete", analyzer.summary_markdown())

    def test_invalid_insight_does_not_discard_valid_evidence_backed_memory(self):
        context = MeetingContext()
        context.add("Use PostgreSQL.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        response = self.result()
        response["memory_updates"] = [{"kind": "decision", "text": "Use PostgreSQL.", "evidence_ids": ["t1"]}]
        response["insights"][0]["type"] = "OPEN_QUESTION"
        self.assertEqual(analyzer.accept(response), [])
        self.assertEqual(context.memory[0]["text"], "Use PostgreSQL.")
        self.assertEqual(context.watermark, 1)
        self.assertEqual(analyzer.rejected_insights, 1)

    def test_rejecting_bad_insights_does_not_bypass_memory_evidence_validation(self):
        context = MeetingContext()
        context.add("Use PostgreSQL.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        response = self.result()
        response["insights"][0]["type"] = "OPEN QUESTION"
        response["memory_updates"] = [
            {"kind": "decision", "text": "Use PostgreSQL.", "evidence_ids": ["invented"]}
        ]
        with self.assertRaises(ValueError):
            analyzer.accept(response)
        self.assertFalse(context.memory)
        self.assertEqual(context.watermark, 0)

    def test_silence_does_not_schedule_analysis(self):
        context = MeetingContext()
        analyzer = QuietAnalyzer(context)
        self.assertFalse(analyzer.ready(100))
        context.add("hello", "Meeting", 100)
        self.assertFalse(analyzer.ready(110))
        self.assertTrue(analyzer.ready(131))

    def test_no_insight_updates_memory_without_a_popup(self):
        context = MeetingContext()
        context.add("No extra database is allowed.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        response = {
            "status": "NO_INSIGHT",
            "topic": "Storage",
            "insights": [],
            "memory_updates": [{"kind": "constraint", "text": "No extra database", "evidence_ids": ["t1"]}],
        }
        self.assertEqual(analyzer.accept(response), [])
        self.assertEqual(context.memory[0]["kind"], "constraint")
        self.assertFalse(context.pending())

    def test_failure_preserves_watermark_and_low_scores_are_suppressed(self):
        context = MeetingContext()
        context.add("Retry the payment.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        with self.assertRaises(ValueError):
            analyzer.accept({"status": "INSIGHTS", "insights": "bad"})
        self.assertTrue(context.pending())
        response = self.result()
        response["insights"][0]["confidence"] = 0.4
        self.assertEqual(analyzer.accept(response), [])

    def test_dismissed_issue_stays_suppressed_unless_evidence_materially_changes(self):
        context = MeetingContext()
        context.add("Retry the payment.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        self.assertEqual(len(analyzer.accept(self.result())), 1)
        analyzer.issues[0]["status"] = "dismissed"
        context.add("Retry the payment again.", "Meeting", 40)
        response = self.result()
        response["insights"][0]["evidence_ids"] = ["t2"]
        self.assertEqual(analyzer.accept(response), [])
        context.add("Now retry every second indefinitely.", "Meeting", 80)
        response["insights"][0]["evidence_ids"] = ["t3"]
        response["insights"][0]["material_change"] = True
        self.assertEqual(len(analyzer.accept(response)), 1)

    def test_untrusted_output_cannot_invent_evidence_or_nonfinite_scores(self):
        response = self.result()
        response["insights"][0]["evidence_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            validate_analysis(response, {"t1"})
        response["insights"][0]["evidence_ids"] = ["t1"]
        response["insights"][0]["confidence"] = float("nan")
        with self.assertRaises(ValueError):
            validate_analysis(response, {"t1"})

    def test_summary_uses_structured_sections_and_preserves_old_constraints(self):
        context = MeetingContext()
        context.memory = [{"kind": "constraint", "text": "No extra database", "evidence_ids": ["old"]}]
        analyzer = QuietAnalyzer(context)
        for i in range(220):
            context.add("A new topic", "Meeting", i)
            analyzer.accept(
                {
                    "status": "NO_INSIGHT",
                    "topic": "Topic",
                    "insights": [],
                    "memory_updates": [
                        {"kind": "topic", "text": f"Topic {i}", "evidence_ids": [f"t{i + 1}"]}
                    ],
                }
            )
        self.assertTrue(any(m["text"] == "No extra database" for m in context.memory))
        context.memory.append({"kind": "decision", "text": "Use an outbox", "evidence_ids": ["old"]})
        summary = analyzer.summary_markdown()
        self.assertIn("## Important decisions\n- Use an outbox", summary)

    def test_bounded_requests_send_oldest_pending_without_skipping_watermark(self):
        context = MeetingContext()
        for i in range(60):
            context.add(f"Discussion {i}", "Meeting", i)
        request = json.loads(context.request("Technical", "", []))
        self.assertEqual(request["new_transcript"][0]["id"], "t1")
        self.assertEqual(request["new_transcript"][-1]["id"], "t50")

    def test_issue_ids_remain_unique_after_history_is_pruned(self):
        context = MeetingContext()
        analyzer = QuietAnalyzer(context)
        for i in range(205):
            context.add("New payment consequence", "Meeting", i)
            response = self.result(text=f"Payment concern number {i}")
            response["insights"][0].update(evidence_ids=[f"t{i + 1}"], material_change=True)
            analyzer.accept(response)
        self.assertEqual(len({i["id"] for i in analyzer.issues}), len(analyzer.issues))

    def test_long_meeting_keeps_old_constraint_and_bounds_request(self):
        context = MeetingContext(max_chars=24000)
        context.add("No new database.", "Meeting", 0)
        analyzer = QuietAnalyzer(context)
        analyzer.accept(
            {
                "status": "NO_INSIGHT",
                "topic": "Storage",
                "insights": [],
                "memory_updates": [{"kind": "constraint", "text": "No new database", "evidence_ids": ["t1"]}],
            }
        )
        for i in range(400):
            context.add("Discuss ordinary details " * 50, "Meeting", 2000 + i)
        context.add("Use MongoDB.", "Meeting", 2500)
        request = context.request("Technical", "project requirement " * 1000, analyzer.issues)
        self.assertLessEqual(len(request), 24000)
        self.assertIn("No new database", request)
        while context.pending():
            request = context.request("Technical", "", analyzer.issues)
            sent = json.loads(request)["new_transcript"]
            through = int(sent[-1]["id"][1:])
            if any("MongoDB" in e["text"] for e in sent):
                break
            analyzer.accept(
                {"status": "NO_INSIGHT", "topic": "Storage", "insights": [], "memory_updates": []},
                through=through,
            )
        self.assertIn("Use MongoDB", request)
        self.assertLessEqual(len(context.recent), 300)
        self.assertIsInstance(json.loads(request), dict)


if __name__ == "__main__":
    unittest.main()
