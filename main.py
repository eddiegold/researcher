#!/usr/bin/env python3
"""
Tool Researcher — agentic research pipeline
Usage:
  python main.py <tool>            # interactive prompt if tool already exists
  python main.py <tool> --force    # overwrite without prompting
  python main.py <tool> --provider brave   # use a different search provider
"""

import argparse
import os
import shutil
import sys

import anthropic

from agents.researcher import ResearcherAgent
from agents.synthesizer import SynthesizerAgent
from agents.example_generator import ExampleGeneratorAgent
from agents.writer import WriterAgent
from mkdocs_config import generate_mkdocs_yml
from search_providers import get_provider


# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_ROOT = os.path.join(PROJECT_ROOT, "docs")


def get_api_key(env_var: str, label: str) -> str:
    key = os.environ.get(env_var, "").strip()
    if not key:
        print(f"\n✗ Missing {label} API key.")
        print(f"  Set it with: export {env_var}=your_key_here\n")
        sys.exit(1)
    return key


def confirm_overwrite(tool: str) -> bool:
    """Interactive prompt when --force is not set and tool already exists."""
    print(f"\n⚠️  '{tool}' already exists in docs/")
    while True:
        answer = input("  Overwrite? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("  Please enter y or n.")


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run(tool: str, provider_name: str, force: bool):
    print(f"\n{'='*60}")
    print(f"  Tool Researcher — {tool.upper()}")
    print(f"  Provider: {provider_name}")
    print(f"{'='*60}\n")

    os.makedirs(DOCS_ROOT, exist_ok=True)

    # Check for existing output
    writer = WriterAgent(docs_root=DOCS_ROOT)
    if writer.tool_exists(tool):
        if force:
            print(f"[Main] --force set. Overwriting existing '{tool}' docs.")
            shutil.rmtree(os.path.join(DOCS_ROOT, tool.lower()))
        else:
            if not confirm_overwrite(tool):
                print("[Main] Skipping. Existing docs preserved.")
                sys.exit(0)
            shutil.rmtree(os.path.join(DOCS_ROOT, tool.lower()))

    # API clients
    search_key_env = {
        "tavily": "TAVILY_API_KEY",
        "brave": "BRAVE_API_KEY",
        "serper": "SERPER_API_KEY",
    }
    search_key = get_api_key(search_key_env[provider_name], provider_name.capitalize())
    anthropic_key = get_api_key("ANTHROPIC_API_KEY", "Anthropic")

    search_provider = get_provider(provider_name, search_key)
    claude = anthropic.Anthropic(api_key=anthropic_key)

    # Stage 1 — Research
    researcher = ResearcherAgent(search_provider, max_results=10)
    sources = researcher.run(tool)

    if not sources:
        print("✗ No sources found. Check your search API key and try again.")
        sys.exit(1)

    # Stage 2 — Synthesize overview
    synthesizer = SynthesizerAgent(claude)
    overview = synthesizer.run(tool, sources)

    # Stage 3 — Generate examples
    example_gen = ExampleGeneratorAgent(claude)
    examples = example_gen.run(tool, overview)

    # Stage 4 — Write files
    writer.write(tool, sources, overview, examples)

    # Regenerate mkdocs.yml
    generate_mkdocs_yml(PROJECT_ROOT, DOCS_ROOT)

    print(f"\n✅ Done! Docs written to: docs/{tool.lower()}/")
    print(f"\nNext steps:")
    print(f"  pip install mkdocs-material")
    print(f"  mkdocs serve          # preview at http://localhost:8000")
    print(f"  mkdocs gh-deploy      # push to GitHub Pages\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Research a tool and generate MkDocs documentation."
    )
    parser.add_argument("tool", help="Name of the tool to research (e.g. dbt, airflow, polars)")
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing docs without prompting"
    )
    parser.add_argument(
        "--provider", default="tavily",
        choices=["tavily", "brave", "serper"],
        help="Search provider to use (default: tavily)"
    )
    args = parser.parse_args()

    run(
        tool=args.tool,
        provider_name=args.provider,
        force=args.force,
    )


if __name__ == "__main__":
    main()
