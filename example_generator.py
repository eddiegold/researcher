import json
import anthropic
from typing import List, Dict


PLANNER_PROMPT = """You are a senior engineer planning hands-on examples for: **{tool}**

Based on this overview:
{overview_excerpt}

List 5 example walkthroughs to create. Range from foundational to advanced/production patterns.
Each should be a realistic scenario a senior engineer would actually build — not toy examples.

Respond ONLY with a raw JSON array (no markdown fences, no preamble):
[
  {{
    "id": "01_slug_here",
    "title": "Human Readable Title",
    "description": "One sentence: what this example demonstrates and why it matters",
    "difficulty": "intermediate|advanced"
  }}
]
"""

WRITER_PROMPT = """You are a senior engineer writing a technical walkthrough.

Tool: **{tool}**
Example: **{title}**
Description: {description}

Write a complete, production-quality Markdown walkthrough:

---
title: {title}
---

# {title}

## Overview
What this example demonstrates and why it matters in real systems.

## Prerequisites
What the reader needs installed / configured / understood before starting.

## Implementation

Walk through the implementation step by step. For each step:
- Explain the decision being made (not just what, but why)
- Show complete, runnable code with realistic naming
- Annotate non-obvious lines with inline comments

Use realistic scenarios (e.g. actual table names, real data shapes, plausible configs).
Do not use foo/bar/baz.

## Running It
Exact shell commands to execute this end-to-end.

## What To Watch For
3-4 specific gotchas or failure modes particular to this example pattern.

## Taking It Further
2-3 natural production extensions a senior engineer would consider next.
"""


class ExampleGeneratorAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def _plan(self, tool: str, overview: str) -> List[Dict]:
        print(f"[ExampleGen] Planning examples for: {tool}")
        response = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": PLANNER_PROMPT.format(
                tool=tool,
                overview_excerpt=overview[:2500],
            )}],
        )
        raw = response.content[0].text.strip()
        # Strip accidental markdown fences if model adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        examples = json.loads(raw.strip())
        print(f"[ExampleGen] Planned {len(examples)} examples")
        return examples

    def _write(self, tool: str, example: Dict) -> str:
        print(f"  → Writing: {example['title']}")
        response = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=3500,
            messages=[{"role": "user", "content": WRITER_PROMPT.format(
                tool=tool,
                title=example["title"],
                description=example["description"],
            )}],
        )
        return response.content[0].text

    def run(self, tool: str, overview: str) -> List[Dict]:
        """Returns list of {id, title, content} dicts."""
        plan = self._plan(tool, overview)
        results = []
        for ex in plan:
            content = self._write(tool, ex)
            results.append({
                "id": ex["id"],
                "title": ex["title"],
                "content": content,
            })
        print(f"[ExampleGen] Done — {len(results)} examples written\n")
        return results
