#!/usr/bin/env python3
"""Independent hook critic.

The writer (Claude: Draft Script (Stage 1)) already produces 5 hook_candidates
and self-picks one as `hook` - but nothing downstream ever re-scores or
re-selects among the 4 unused alternatives; Claude: Visual Director and
Claude: Repair Script explicitly preserve hook/hook_candidates byte-for-byte.
This inserts a genuinely independent scoring pass between the writer and
Visual Director: Claude: Critique Hooks ranks the candidates on their own
merits (never having seen which one the writer preferred), and Apply Hook
Critique merges the critic's pick back onto the draft before anything else
runs. Fails open by design: any problem with the critic call or its response
leaves the writer's original hook completely unchanged - this is a quality
upgrade, never a new way for a run to fail.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

MARKER = "HOOK_CRITIC_V1"
CRITIC_NODE = "Claude: Critique Hooks"
APPLY_NODE = "Apply Hook Critique"


def node_by_name(w: dict, name: str) -> dict:
    for n in w.get("nodes", []):
        if n.get("name") == name:
            return n
    raise KeyError(f"required n8n node not found: {name}")


APPLY_HOOK_CRITIQUE_JS = r"""const draft = (($('Parse Draft JSON').item || {}).json || {}).draft || {};
function extractJsonObject(s) {
  const at = s.indexOf('{');
  if (at === -1) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = at; i < s.length; i++) {
    const cc = s.charCodeAt(i);
    if (inStr) { if (esc) esc = false; else if (cc === 92) esc = true; else if (cc === 34) inStr = false; }
    else if (cc === 34) inStr = true;
    else if (cc === 123) depth++;
    else if (cc === 125) { depth--; if (depth === 0) return s.slice(at, i + 1); }
  }
  return null;
}
let critique = null;
try {
  const response = $input.first().json;
  if (response.error) throw new Error('critic API error');
  const choice = (response.choices || [])[0];
  const raw = String((choice && choice.message && choice.message.content) || '').trim();
  critique = JSON.parse(extractJsonObject(raw) || raw);
} catch (e) {
  critique = null;
}
const candidates = Array.isArray(draft.hook_candidates)
  ? draft.hook_candidates.filter((h) => typeof h === 'string' && h.trim())
  : [];
if (!critique || typeof critique.chosen_index !== 'number' || !candidates[critique.chosen_index]) {
  // Fail open: no usable critique, so the writer's own self-picked hook
  // passes through completely unchanged.
  return { json: { draft } };
}
const chosenHook = candidates[critique.chosen_index];
return { json: { draft: {
  ...draft,
  hook: chosenHook,
  hook_type: critique.chosen_hook_type || draft.hook_type,
  hook_critique: {
    chosen_index: critique.chosen_index,
    scores: Array.isArray(critique.scores) ? critique.scores : null,
    original_hook: draft.hook,
  },
} } };"""

CRITIC_PROMPT = r'''={{ JSON.stringify({ model: "gpt-5.6-luna", max_completion_tokens: 1200, reasoning_effort: "medium", response_format: { type: "json_object" }, messages: [{ role: "user", content: "You are an INDEPENDENT HOOK CRITIC for a YouTube Shorts channel - you did not write any of these hooks and have no attachment to any of them. Score each candidate ONLY on: does it withhold the payoff, number, or mechanism where that pattern applies? Is it specific and concrete, not generic? Would a real viewer feel genuine surprise or urgent curiosity, not a shrug? Is it visually supportable by a single real photo or video? Penalize generic openers such as did you know, in this video, you won't believe, or a greeting.\n\nCANDIDATES: " + JSON.stringify((($json.draft || {}).hook_candidates) || [($json.draft || {}).hook]) + "\nTOPIC: " + JSON.stringify((($('Extract Generated Topic').item||{}).json||{}).topic || '') + "\nMEASURED ARCHETYPE PERFORMANCE (use only if it clearly favors one candidate's underlying mechanism; a tiny sample must not override the other criteria): " + JSON.stringify($('Get Channel Insights').item.json.archetype_performance || {}) + "\n\nReturn ONLY JSON: {\"chosen_index\": number (0-based index into CANDIDATES), \"chosen_hook_type\": string, \"scores\": [{\"index\":number,\"withholds_payoff\":number,\"specific\":number,\"surprising\":number,\"visually_supportable\":number,\"total\":number}], \"rationale\": string}." }] }) }}'''


def upgrade(w: dict) -> dict:
    if w.get("meta", {}).get(MARKER):
        return w
    names = {n.get("name") for n in w.get("nodes", [])}
    draft = node_by_name(w, "Claude: Draft Script (Stage 1)")

    if CRITIC_NODE not in names:
        import copy
        critic = copy.deepcopy(draft)
        critic["id"] = "8f1c2d3e-4b5a-4c6d-9e7f-1a2b3c4d5e6f"
        critic["name"] = CRITIC_NODE
        critic["position"] = [draft["position"][0] + 300, draft["position"][1] + 160]
        critic["parameters"]["jsonBody"] = CRITIC_PROMPT
        critic["parameters"].setdefault("options", {})["timeout"] = 60000
        # Fail open for real, not just in Apply Hook Critique's JS: without this,
        # n8n's default onError ("stopWorkflow") means a critic API error/timeout
        # aborts the ENTIRE run instead of falling back to the writer's own hook -
        # exactly the failure mode this node exists to avoid. Same idiom already
        # used on Deduplicate Topic Pool, Post First Comment, Get Channel Insights.
        critic["onError"] = "continueRegularOutput"
        hp = critic["parameters"].get("headerParameters", {}).get("parameters", [])
        for h in hp:
            if h.get("name") == "x-llm-timeout-ms":
                h["value"] = "55000"
        w["nodes"].append(critic)
        # This node inherits its already-internalized llm-gateway URL/no-creds
        # shape from the deep-copied draft node (v5.build() ran first), so it
        # counts as one more routed node for preprod-audit.py's tally, which
        # was stamped by v5 before this node existed.
        meta = w.setdefault("meta", {})
        meta["llm_gateway_routed_nodes"] = int(meta.get("llm_gateway_routed_nodes", 0)) + 1

    if APPLY_NODE not in names:
        w["nodes"].append({
            "id": "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
            "name": APPLY_NODE,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [draft["position"][0] + 600, draft["position"][1] + 160],
            "parameters": {"jsCode": APPLY_HOOK_CRITIQUE_JS},
        })

    # Rewire: Parse Draft JSON -> Critique Hooks -> Apply Hook Critique ->
    # (whatever Parse Draft JSON used to feed directly).
    con = w.setdefault("connections", {})
    parse_edges = con.get("Parse Draft JSON", {}).get("main", [[]])[0]
    if len(parse_edges) != 1:
        raise ValueError("Parse Draft JSON must have exactly one outgoing edge to rewire")
    downstream = parse_edges[0]
    con["Parse Draft JSON"] = {"main": [[{"node": CRITIC_NODE, "type": "main", "index": 0}]]}
    con[CRITIC_NODE] = {"main": [[{"node": APPLY_NODE, "type": "main", "index": 0}]]}
    con[APPLY_NODE] = {"main": [[downstream]]}

    w.setdefault("meta", {})[MARKER] = True
    return w


def assert_invariants(w: dict) -> None:
    names = {n.get("name") for n in w.get("nodes", [])}
    for required in (CRITIC_NODE, APPLY_NODE):
        if required not in names:
            raise RuntimeError(f"hook critic node missing after upgrade: {required}")
    con = w.get("connections", {})
    if con.get("Parse Draft JSON", {}).get("main", [[]])[0][0].get("node") != CRITIC_NODE:
        raise RuntimeError("Parse Draft JSON does not route to the hook critic")
    if con.get(CRITIC_NODE, {}).get("main", [[]])[0][0].get("node") != APPLY_NODE:
        raise RuntimeError("hook critic does not route to Apply Hook Critique")
    apply_body = str(node_by_name(w, APPLY_NODE)["parameters"]["jsCode"])
    if "hook_critique" not in apply_body:
        raise RuntimeError("Apply Hook Critique lost its telemetry field")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-hook-critic-v1.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    w = json.loads(src.read_text())
    out = upgrade(w)
    assert_invariants(out)
    dst.write_text(json.dumps(out, indent=2) + "\n")
    print(f"{MARKER} workflow written to {dst}")


if __name__ == "__main__":
    main()
