"""Opt-in cloud quality evaluation using synthetic, non-confidential meeting examples."""

import argparse
import json
import os
import time
from pathlib import Path

from meeting_copilot.intelligence import MeetingContext, QuietAnalyzer
from meeting_copilot.llm import (
    DEFAULT_INPUT_PRICE,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_PRICE,
    AnalysisError,
    OpenRouterLLM,
)

CASES = [
    {
        "name": "ordinary",
        "text": "Thanks everyone. The demo is ready. Let us start with the overview.",
        "types": [],
        "keywords": [],
    },
    {
        "name": "payment-retries",
        "text": "The worker charges the card. If it times out, it retries the charge. We have not defined any duplicate protection.",
        "types": ["QUESTION", "CONCERN", "RISK", "MISSING REQUIREMENT", "EDGE CASE"],
        "keywords": ["idempot", "duplicate", "twice", "double"],
    },
    {
        "name": "database-contradiction",
        "text": "Let us introduce MongoDB to store these events.",
        "memory": [
            {
                "kind": "constraint",
                "text": "The team agreed no additional database may be introduced.",
                "evidence_ids": ["earlier:1"],
            }
        ],
        "types": ["CONTRADICTION", "CLARIFICATION", "QUESTION", "CONCERN"],
        "keywords": ["earlier", "constraint", "additional", "another", "previous"],
    },
    {
        "name": "auth-contradiction",
        "text": "Guests must use this feature without logging in.",
        "memory": [
            {
                "kind": "requirement",
                "text": "Authentication is mandatory for every user accessing this feature.",
                "evidence_ids": ["earlier:1"],
            }
        ],
        "types": ["CONTRADICTION", "CLARIFICATION", "QUESTION"],
        "keywords": ["auth", "login", "logging", "guest"],
    },
    {
        "name": "already-addressed",
        "text": "We retry charges. We already enforce a unique payment idempotency key and duplicate calls return the original result.",
        "types": [],
        "keywords": [],
    },
    {
        "name": "intentional-demo-limit",
        "text": "This is a local single-user onboarding demo, not production. We deliberately excluded multi-tenant authentication and will design it before deployment.",
        "types": [],
        "keywords": [],
    },
    {
        "name": "changed-constraint",
        "text": "The sponsor explicitly retired the no-new-database constraint and approved MongoDB. That decision replaces our earlier constraint.",
        "memory": [
            {
                "kind": "constraint",
                "text": "No additional database may be introduced.",
                "evidence_ids": ["earlier:1"],
            }
        ],
        "types": [],
        "keywords": [],
    },
    {
        "name": "tentative-options",
        "text": "We are comparing Redis and PostgreSQL caching. No implementation decision yet; failure-mode review is already scheduled before we decide.",
        "types": [],
        "keywords": [],
    },
    {
        "name": "dismissed-repeat",
        "text": "The payment worker still retries timed-out charges. Nothing about the retry behavior has changed since we last discussed it.",
        "prior_issues": [
            {
                "id": 1,
                "type": "CONCERN",
                "priority": "HIGH",
                "confidence": 0.95,
                "relevance": 0.95,
                "text": "Payment retries need idempotency to prevent duplicate charges.",
                "reason": "Timeouts do not imply failure.",
                "issue_key": "payment-idempotency",
                "evidence_ids": ["earlier:1"],
                "material_change": False,
                "status": "dismissed",
            }
        ],
        "types": [],
        "keywords": [],
    },
    {
        "name": "authorization-bypass",
        "text": "Users log in, but the download endpoint accepts any document ID from the URL without checking ownership. We are deploying to production next week.",
        "types": ["CONCERN", "RISK", "QUESTION", "MISSING REQUIREMENT", "EDGE CASE"],
        "keywords": ["ownership", "authorization", "permission", "unauthorized", "access"],
    },
    {
        "name": "project-retention-contradiction",
        "text": "We will keep every uploaded document indefinitely.",
        "project": "Documented requirement: every uploaded document must be deleted within 30 days, without exceptions.",
        "types": ["CONTRADICTION", "CONCERN", "RISK", "QUESTION", "CLARIFICATION"],
        "keywords": ["retention", "30 days", "indefinitely", "delet"],
    },
]


def score_case(case, insights):
    if not case["types"]:
        return not insights
    required = {e for fact in case.get("memory", []) for e in fact["evidence_ids"]}
    if case.get("project"):
        required.add("project:1")
    return any(
        item["type"] in case["types"]
        and any(word in (item["text"] + " " + item["reason"]).lower() for word in case["keywords"])
        and (not required or required.union({"t1"}).issubset(item.get("evidence_ids", [])))
        for item in insights
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL))
    parser.add_argument("--allow-cloud-upload", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=0.10)
    parser.add_argument("--input-price", type=float, default=DEFAULT_INPUT_PRICE)
    parser.add_argument("--output-price", type=float, default=DEFAULT_OUTPUT_PRICE)
    args = parser.parse_args()
    if not args.allow_cloud_upload or not os.getenv("OPENROUTER_API_KEY"):
        parser.error("Set OPENROUTER_API_KEY and explicitly permit synthetic-example uploads.")
    provider = OpenRouterLLM(
        os.environ["OPENROUTER_API_KEY"], args.model, args.budget, args.input_price, args.output_price
    )
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    results = []
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        for case in CASES:
            context = MeetingContext()
            context.memory = case.get("memory", []).copy()
            context.add(case["text"], "Meeting", 0)
            analyzer = QuietAnalyzer(context)
            analyzer.issues = case.get("prior_issues", []).copy()
            start = time.perf_counter()
            try:
                response = provider.analyze(
                    context.request("Technical", case.get("project", ""), analyzer.issues)
                )
                visible = analyzer.accept(response)
                results.append(
                    {
                        "case": case["name"],
                        "passed": score_case(case, visible) and analyzer.rejected_insights == 0,
                        "rejected_insights": analyzer.rejected_insights,
                        "insights": visible,
                        "request_seconds": time.perf_counter() - start,
                    }
                )
            except (ValueError, RuntimeError) as exc:
                results.append(
                    {
                        "case": case["name"],
                        "passed": False,
                        "error": str(exc)
                        if isinstance(exc, AnalysisError)
                        else "Response validation failed; check model compatibility.",
                        "request_seconds": time.perf_counter() - start,
                    }
                )
        json.dump(
            {
                "model": args.model,
                "results": results,
                "usage": provider.usage,
                "note": "Keyword checks are screening heuristics, not a substitute for human review or real-meeting evaluation.",
            },
            output,
            indent=2,
        )
    passed = sum(r["passed"] for r in results)
    print(f"Fixture checks: {passed}/{len(results)}. Review the report manually before choosing a model.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
