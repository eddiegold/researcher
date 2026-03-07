import os
import yaml
from typing import List


def generate_mkdocs_yml(project_root: str, docs_root: str, site_name: str = "Tool Research"):
    """Scan docs/ and generate a mkdocs.yml with full nav."""

    nav = [{"Home": "index.md"}]

    tools = sorted([
        d for d in os.listdir(docs_root)
        if os.path.isdir(os.path.join(docs_root, d)) and not d.startswith(".")
    ])

    for tool in tools:
        tool_nav = []
        tool_dir = os.path.join(docs_root, tool)

        # Overview
        if os.path.exists(os.path.join(tool_dir, "index.md")):
            tool_nav.append({"Overview": f"{tool}/index.md"})

        # Sources
        if os.path.exists(os.path.join(tool_dir, "sources.md")):
            tool_nav.append({"Sources": f"{tool}/sources.md"})

        # Examples
        examples_dir = os.path.join(tool_dir, "examples")
        if os.path.isdir(examples_dir):
            example_files = sorted([
                f for f in os.listdir(examples_dir) if f.endswith(".md")
            ])
            if example_files:
                examples_nav = []
                for ef in example_files:
                    # Title from filename: 01_basic_model.md → Basic Model
                    label = ef.replace(".md", "").split("_", 1)[-1].replace("_", " ").title()
                    examples_nav.append({label: f"{tool}/examples/{ef}"})
                tool_nav.append({"Examples": examples_nav})

        if tool_nav:
            nav.append({tool.upper(): tool_nav})

    config = {
        "site_name": site_name,
        "site_description": "Senior-engineer-level research on tools, frameworks, and infrastructure.",
        "theme": {
            "name": "material",
            "palette": {"primary": "indigo", "accent": "indigo"},
            "features": [
                "navigation.tabs",
                "navigation.sections",
                "navigation.expand",
                "search.highlight",
                "content.code.copy",
            ],
        },
        "markdown_extensions": [
            "pymdownx.highlight",
            "pymdownx.superfences",
            "pymdownx.tabbed",
            "admonition",
            "tables",
            "toc",
        ],
        "nav": nav,
    }

    yml_path = os.path.join(project_root, "mkdocs.yml")
    with open(yml_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"[MkDocs] Updated mkdocs.yml with {len(tools)} tool(s)")
    return yml_path
