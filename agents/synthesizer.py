import anthropic
from typing import List, Dict


SYNTHESIZER_PROMPT = """You are a senior staff engineer writing technical documentation for other senior/staff engineers.
You have researched the tool: **{tool}**

Source material:
{sources_block}

---

Write a comprehensive Markdown document. Be technically precise, opinionated, and direct.
No beginner handholding. Assume the reader knows how to code and has used similar tools.

Use this exact structure:

---
title: {tool} - Technical Overview
tags: [{tool_lower}, data-engineering, devtools]
---

# {tool} — Technical Overview

## What It Is
One sharp paragraph. What problem it solves, where it sits in the stack, what it is NOT.
No marketing language.

## Core Concepts
The 4-6 mental models required before using this tool effectively.
Each concept: **name** — explanation.

## Primary Use Cases
For each use case (3-5):
### [Use Case Name]
- **When to reach for it:** ...
- **When NOT to reach for it:** ...

## Senior / Staff Engineer Highlights

### Production Gotchas & Failure Modes
Concrete things that bite teams in production. Be specific, not generic.
At least 4-5 distinct gotchas with brief explanations.

### When NOT To Use {tool}
Honest assessment. Name specific alternative tools and when they win.

### How It Fits Into a Broader Stack
Common upstream/downstream integrations. Architectural patterns. Where it sits in a modern data/infra stack.

### Performance & Scale Considerations
Known limits, bottlenecks, tuning levers. What breaks first at scale and how to detect it early.

## Key Tradeoffs
Honest 4-5 point tradeoff table or list. Not marketing copy.

## Quick Reference
Most commonly used commands/APIs/patterns in a code block. The stuff you actually reach for daily.
"""


class SynthesizerAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def run(self, tool: str, sources: List[Dict]) -> str:
        print(f"[Synthesizer] Generating technical overview for: {tool}")

        sources_block = ""
        for i, s in enumerate(sources, 1):
            content = s.get("content") or s.get("description", "")
            preview = content[:2000]
            sources_block += f"\n### Source {i}: {s['title']}\nURL: {s['url']}\n{preview}\n---\n"

        prompt = SYNTHESIZER_PROMPT.format(
            tool=tool,
            tool_lower=tool.lower(),
            sources_block=sources_block,
        )

        response = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        print(f"[Synthesizer] Done — {len(content.splitlines())} lines\n")
        return content
