import unittest

from meeting_copilot.evaluation import CASES, score_case


class EvaluationTests(unittest.TestCase):
    def test_cli_does_not_count_rejected_findings_as_successful_quiet_output(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch

        from meeting_copilot.evaluation import main

        provider = Mock(usage={})
        provider.analyze.return_value = {
            "status": "INSIGHTS",
            "topic": "Check",
            "memory_updates": [],
            "insights": [{"type": "OPEN QUESTION"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evaluation.json"
            with (
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "not-a-secret"}),
                patch("sys.argv", ["evaluate", "--allow-cloud-upload", "--output", str(output)]),
                patch("meeting_copilot.evaluation.OpenRouterLLM", return_value=provider),
            ):
                main()
            results = json.loads(output.read_text())["results"]
        self.assertFalse(any(item["passed"] for item in results))

    def test_quiet_case_rejects_generic_noise(self):
        case = next(c for c in CASES if c["name"] == "ordinary")
        self.assertTrue(score_case(case, []))
        self.assertFalse(
            score_case(case, [{"type": "QUESTION", "text": "Have we considered scalability?", "reason": ""}])
        )

    def test_contradiction_requires_both_new_and_earlier_evidence(self):
        case = next(c for c in CASES if c["name"] == "database-contradiction")
        insight = {
            "type": "CONTRADICTION",
            "text": "MongoDB violates the earlier constraint.",
            "reason": "An additional database is not allowed.",
            "evidence_ids": ["t1"],
        }
        self.assertFalse(score_case(case, [insight]))
        insight["evidence_ids"].append("earlier:1")
        self.assertTrue(score_case(case, [insight]))

    def test_specific_conflict_as_concern_is_still_useful(self):
        case = next(c for c in CASES if c["name"] == "database-contradiction")
        self.assertTrue(
            score_case(
                case,
                [
                    {
                        "type": "CONCERN",
                        "text": "MongoDB conflicts with the earlier constraint.",
                        "reason": "An additional database is forbidden.",
                        "evidence_ids": ["t1", "earlier:1"],
                    }
                ],
            )
        )

    def test_idempotency_case_requires_a_concrete_payment_issue(self):
        case = next(c for c in CASES if c["name"] == "payment-retries")
        self.assertFalse(
            score_case(case, [{"type": "CONCERN", "text": "Consider scalability", "reason": ""}])
        )
        self.assertTrue(
            score_case(
                case, [{"type": "CONCERN", "text": "What prevents duplicate payment charges?", "reason": ""}]
            )
        )


if __name__ == "__main__":
    unittest.main()
