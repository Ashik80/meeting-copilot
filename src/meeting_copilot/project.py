"""Small replaceable text/Markdown context adapter; lexical retrieval, no external service."""

import re
from typing import Protocol


class ProjectContextProvider(Protocol):
    def retrieve(self, query: str, limit: int = 4000) -> str: ...


class TextProjectContext:
    def __init__(self, text):
        self.text = text[:12000]
        self.sections = [self.text[i : i + 1200] for i in range(0, len(self.text), 1100)]

    def retrieve(self, query, limit=4000):
        words = set(re.findall(r"\w+", query.casefold())) - {
            "the",
            "a",
            "an",
            "and",
            "is",
            "of",
            "to",
            "we",
            "it",
            "for",
            "in",
        }
        ranked = sorted(
            enumerate(self.sections),
            key=lambda item: len(words & set(re.findall(r"\w+", item[1].casefold()))),
            reverse=True,
        )
        chosen = sorted(ranked[:3])
        return "\n\n".join(text for _, text in chosen)[:limit]
