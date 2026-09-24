#!/usr/bin/env python3
"""Stage 1 draft resilience.

Execution on 2026-09-24 12:00 died in Parse Draft JSON with
"OpenAI draft did not return a complete script JSON object (finish_reason:
stop)": the free-tier writer answered HTTP 200 with JSON of the wrong shape.
Nothing downstream could recover because the parser threw, and a thrown error
is fatal - the script repair loop only engages for a returned
_scriptValid:false from Validate Final Script.

Three layers, cheapest first:
A. DRAFT_SHAPE_NORMALIZER - Parse Draft JSON unwraps {script|draft|data|...:
   {...}} envelopes and coerces an object-shaped hook / hook_candidates into
   the plain strings the rest of the workflow expects. Never invents narration.
B. x-llm-required-keys header on the writer node - llm-gateway falls back to
   the paid provider when a FreeLLMAPI response lacks these keys (see
   shorts-compose/llmRouting.js), so a bad free answer rarely reaches n8n.
C. DRAFT_RETRY_LOOP - Parse Draft JSON never throws. An unusable draft is
   routed back to the writer (same prompt, same inputs) for up to
   MAX_DRAFT_ATTEMPTS total calls before the run fails with the last error.

Runs in-process from build_production_artifacts.py after every other workflow
transform, so the retry loop feeds the writer's real final upstream node.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARKER = "DRAFT_RESILIENCE_V1"
NORMALIZER_MARKER = "DRAFT_SHAPE_NORMALIZER"
RETRY_MARKER = "DRAFT_RETRY_LOOP"
MAX_DRAFT_ATTEMPTS = 3
REQUIRED_KEYS_HEADER = "x-llm-required-keys"
REQUIRED_KEYS = "hook,scenes"

WRITER = "Claude: Draft Script (Stage 1)"
PARSER = "Parse Draft JSON"
CRITIC = "Claude: Critique Hooks"
IF_PARSED = "If Draft Parsed"
RETRY = "Retry Draft Script"
IF_UNDER_MAX = "If Under Max Draft Attempts"
FAIL = "Fail: Draft Generation Exhausted"


def node_by_name(w: dict, name: str) -> dict:
    for n in w.get("nodes", []):
        if n.get("name") == name:
            return n
    raise KeyError(f"required n8n node not found: {name}")


NORMALIZER_JS = r"""// DRAFT_SHAPE_NORMALIZER: accept the common near-miss shapes free models return
// (wrapped envelope, object hook, object hook_candidates). Never invents text.
function draftText(value) {
  if (typeof value === 'string') return value.trim();
  if (value && typeof value === 'object') {
    for (const key of ['text', 'hook', 'line', 'narration']) {
      if (typeof value[key] === 'string' && value[key].trim()) return value[key].trim();
    }
  }
  return '';
}
function normalizeDraftShape(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
  let obj = value;
  if (!Array.isArray(obj.scenes)) {
    for (const key of ['script', 'draft', 'data', 'result', 'output', 'response']) {
      const inner = obj[key];
      if (inner && typeof inner === 'object' && !Array.isArray(inner) && Array.isArray(inner.scenes)) { obj = inner; break; }
    }
  }
  obj = { ...obj };
  if (Array.isArray(obj.hook_candidates)) {
    obj.hook_candidates = obj.hook_candidates.map(draftText).filter(Boolean);
  }
  if (typeof obj.hook !== 'string' || !obj.hook.trim()) {
    const hook = draftText(obj.hook) || (Array.isArray(obj.hook_candidates) ? obj.hook_candidates[0] || '' : '');
    if (hook) obj.hook = hook;
  }
  return obj;
}
"""

RETRY_JS_TEMPLATE = r"""// DRAFT_RETRY_LOOP: re-run the writer with its original input after an unusable draft.
const attempt = $runIndex + 1;
const error = String($input.first().json._draftError || 'unknown Stage 1 draft failure').slice(0, 500);
console.log(`Stage 1 draft unusable on attempt ${attempt}: ${error}`);
return { json: { ...$(__UPSTREAM__).first().json, _draftAttempt: attempt, _lastDraftError: error } };"""

FAIL_JS = f"""// {RETRY_MARKER}: terminal failure after every writer attempt returned an unusable draft.
throw new Error('Stage 1 draft failed after {MAX_DRAFT_ATTEMPTS} attempts - giving up for this scheduled run. Last error: ' + String($input.first().json._lastDraftError || 'unknown'));"""


def patch_parser(w: dict) -> None:
    node = node_by_name(w, PARSER)
    code = node["parameters"]["jsCode"]
    anchor = "const parsed = parseLastValidJsonObject(raw);"
    if code.count(anchor) != 1:
        raise ValueError("Parse Draft JSON parse anchor changed")
    code = code.replace(anchor, NORMALIZER_JS + "const parsed = normalizeDraftShape(parseLastValidJsonObject(raw));", 1)
    # Every failure path inside the original parser (API error, empty text,
    # truncation, wrong shape) becomes a routable item instead of a fatal throw.
    node["parameters"]["jsCode"] = (
        f"// {RETRY_MARKER}: failures are returned as _draftValid:false and retried, never thrown.\n"
        "function parseDraft() {\n" + code + "\n}\n"
        "try {\n"
        "  const out = parseDraft();\n"
        "  return { json: { ...out.json, _draftValid: true } };\n"
        "} catch (e) {\n"
        "  return { json: { _draftValid: false, _draftError: String((e && e.message) || e).slice(0, 500) } };\n"
        "}"
    )


def patch_writer_header(w: dict) -> None:
    params = node_by_name(w, WRITER)["parameters"]
    params["sendHeaders"] = True
    headers = params.setdefault("headerParameters", {}).setdefault("parameters", [])
    headers[:] = [h for h in headers if str(h.get("name", "")).lower() != REQUIRED_KEYS_HEADER]
    headers.append({"name": REQUIRED_KEYS_HEADER, "value": REQUIRED_KEYS})


def edge(node: str) -> dict:
    return {"node": node, "type": "main", "index": 0}


def add_retry_loop(w: dict) -> None:
    con = w.setdefault("connections", {})
    upstream = [src for src, out in con.items()
                for branch in out.get("main", []) for e in branch if e.get("node") == WRITER]
    if len(upstream) != 1:
        raise ValueError(f"expected exactly one node feeding {WRITER}, found {upstream}")
    parser_out = con.get(PARSER, {}).get("main", [[]])
    if len(parser_out) != 1 or [e["node"] for e in parser_out[0]] != [CRITIC]:
        raise ValueError(f"{PARSER} must feed only {CRITIC} before the retry loop is inserted")

    x, y = node_by_name(w, PARSER)["position"]
    bool_true = lambda left: {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 1},
            "conditions": [left],
            "combinator": "and",
        },
        "options": {},
    }
    w["nodes"].extend([
        {
            "id": "5d2f7a1c-3e84-4b9a-a6c1-0f2d9e8b7a61",
            "name": IF_PARSED,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [x + 150, y - 160],
            "parameters": bool_true({
                "leftValue": "={{ $json._draftValid }}", "rightValue": True,
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }),
        },
        {
            "id": "7e3a9b2d-4f15-4c8b-b7d2-1a3e0f9c8b72",
            "name": RETRY,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [x + 300, y - 320],
            "parameters": {"jsCode": RETRY_JS_TEMPLATE.replace("__UPSTREAM__", json.dumps(upstream[0]).replace('"', "'"))},
        },
        {
            "id": "9f4b0c3e-5a26-4d9c-c8e3-2b4f1a0d9c83",
            "name": IF_UNDER_MAX,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [x + 450, y - 320],
            "parameters": bool_true({
                "leftValue": "={{ $json._draftAttempt }}", "rightValue": MAX_DRAFT_ATTEMPTS,
                "operator": {"type": "number", "operation": "lt"},
            }),
        },
        {
            "id": "0a5c1d4f-6b37-4e0d-d9f4-3c5a2b1e0d94",
            "name": FAIL,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [x + 600, y - 320],
            "parameters": {"jsCode": FAIL_JS},
        },
    ])
    con[PARSER] = {"main": [[edge(IF_PARSED)]]}
    con[IF_PARSED] = {"main": [[edge(CRITIC)], [edge(RETRY)]]}
    con[RETRY] = {"main": [[edge(IF_UNDER_MAX)]]}
    con[IF_UNDER_MAX] = {"main": [[edge(WRITER)], [edge(FAIL)]]}


def upgrade(w: dict) -> dict:
    if w.get("meta", {}).get(MARKER):
        return w
    patch_parser(w)
    patch_writer_header(w)
    add_retry_loop(w)
    w.setdefault("meta", {})[MARKER] = True
    return w


def assert_invariants(w: dict) -> None:
    code = node_by_name(w, PARSER)["parameters"]["jsCode"]
    for marker in (NORMALIZER_MARKER, RETRY_MARKER, "_draftValid: false"):
        if marker not in code:
            raise RuntimeError(f"{PARSER} lost draft resilience marker: {marker}")
    headers = node_by_name(w, WRITER)["parameters"].get("headerParameters", {}).get("parameters", [])
    if not any(h.get("name") == REQUIRED_KEYS_HEADER and h.get("value") == REQUIRED_KEYS for h in headers):
        raise RuntimeError(f"{WRITER} lost its {REQUIRED_KEYS_HEADER} header")
    critic_headers = node_by_name(w, CRITIC)["parameters"].get("headerParameters", {}).get("parameters", [])
    if any(h.get("name") == REQUIRED_KEYS_HEADER for h in critic_headers):
        raise RuntimeError(f"{CRITIC} must not require the writer's script keys")
    con = w.get("connections", {})
    expected = {
        PARSER: [[IF_PARSED]],
        IF_PARSED: [[CRITIC], [RETRY]],
        RETRY: [[IF_UNDER_MAX]],
        IF_UNDER_MAX: [[WRITER], [FAIL]],
    }
    for src, branches in expected.items():
        got = [[e["node"] for e in b] for b in con.get(src, {}).get("main", [])]
        if got != branches:
            raise RuntimeError(f"draft retry wiring drifted at {src}: {got}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: draft_resilience.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    w = json.loads(src.read_text(encoding="utf-8"))
    out = upgrade(w)
    assert_invariants(out)
    dst.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"{MARKER} workflow written to {dst}")


if __name__ == "__main__":
    main()
