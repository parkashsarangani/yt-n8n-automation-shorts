#!/usr/bin/env python3
"""Attach a stable n8n execution id to b-roll requests for run-wide budgets."""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARKER = "API_BUDGET"


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise KeyError(f"required n8n node not found: {name}")


def upgrade(workflow: dict) -> dict:
    node = node_by_name(workflow, "Resolve B-roll")
    body = node["parameters"]["jsonBody"]
    if "run_id:" not in body:
        anchor = "creative_format: ($('Validate Final Script').item.json.creative_format || '') }) }}"
        replacement = "creative_format: ($('Validate Final Script').item.json.creative_format || ''), run_id: String($execution.id || '') }) }}"
        if anchor not in body:
            raise ValueError("could not patch Resolve B-roll run_id: anchor not found")
        body = body.replace(anchor, replacement, 1)
        node["parameters"]["jsonBody"] = body
    node["notes"] = f"{MARKER}: pass n8n execution id to compose service for a shared paid-vision budget"
    return workflow


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-workflow-api-budget.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    workflow = json.loads(src.read_text(encoding="utf-8"))
    dst.write_text(json.dumps(upgrade(workflow), indent=2) + "\n", encoding="utf-8")
    print(f"API budget workflow written to {dst}")


if __name__ == "__main__":
    main()
