#!/usr/bin/env python3
"""Upgrade the exported n8n Shorts workflow for a higher editorial quality bar.

This intentionally patches the workflow at deploy time instead of duplicating the
100KB n8n export. The source export remains importable; production gets the
quality-enhanced graph deterministically.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MARKER = "QUALITY_GATE"

WIKI_NODE = "Wikipedia: Research Topic"
RESEARCH_NODE = "Normalize Research Evidence"


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise KeyError(f"required n8n node not found: {name}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"could not patch {label}: anchor not found")
    return text.replace(old, new, 1)


def patch_topic_prompt(node: dict) -> None:
    body = node["parameters"]["jsonBody"]
    if MARKER in body:
        return

    anchor = "Before answering, think through several candidate topics, stress-test each one against the WEAK/STRONG bar above, and pick only the strongest."
    addition = f"""{MARKER} - QUALITY OVER CADENCE: your job is not to fill a publishing slot. A merely decent fact is a failure. Prefer a candidate that has a strong factual spine, a visual idea that reads in under half a second, and a payoff worth repeating to another person. If an idea depends on hype wording to seem interesting, score it low.\\n\\nEVIDENCEABILITY GATE: reward claims that can be independently checked from a reputable reference source. Penalize fuzzy folklore, unsourced celebrity trivia, disputed anecdotes, or claims whose punch depends on a suspiciously precise number. The writer will receive external reference snippets after you choose, so pick topics that have a clean searchable evidence trail.\\n\\nVISUAL VIRALITY GATE: score the FIRST FRAME, not just the sentence. The chosen fact must naturally suggest an arresting vertical visual: one dominant subject, obvious motion/scale/contrast, or a designed comparison/reveal. If the fact is good but the visuals would be generic stock filler, it is not a top candidate.\\n\\nSHARE TEST: ask whether a viewer would immediately want to send the video to one specific kind of person. 'Interesting' is not enough; the fact should create disbelief, recognition, status, humor, awe, or argument.\\n\\n{anchor}"""
    body = replace_once(body, anchor, addition, "topic quality gates")

    old = "where each item has fields: topic (the fact as one plain sentence), reason (why this score, brief), and score (integer 0-100)."
    new = "where each item has fields: topic (the fact as one plain sentence), research_query (3-8 words that will find the best reference evidence), first_frame_concept (one concrete arresting vertical shot), share_reason (why a viewer would send this to someone), evidence_score (integer 0-100), visual_score (integer 0-100), share_score (integer 0-100), reason (why the overall score, brief), and score (integer 0-100). Overall score must NOT exceed the lowest of evidence_score, visual_score, and share_score by more than 10 points."
    body = replace_once(body, old, new, "topic output schema")
    node["parameters"]["jsonBody"] = body


def patch_topic_parser(node: dict) -> None:
    code = node["parameters"]["jsCode"]
    old_map = ".map(c => ({ topic: String(c.topic).trim(), reason: String(c.reason || ''), score: Number(c.score) || 0 }))"
    new_map = ".map(c => ({ topic: String(c.topic).trim(), research_query: String(c.research_query || c.topic).trim(), first_frame_concept: String(c.first_frame_concept || ''), share_reason: String(c.share_reason || ''), evidence_score: Number(c.evidence_score) || 0, visual_score: Number(c.visual_score) || 0, share_score: Number(c.share_score) || 0, reason: String(c.reason || ''), score: Number(c.score) || 0 }))"
    code = replace_once(code, old_map, new_map, "topic candidate parser")

    old_return = "return { json: { topic: picked.topic, score: picked.score, candidates } };"
    new_return = "return { json: { topic: picked.topic, score: picked.score, research_query: picked.research_query || picked.topic, first_frame_concept: picked.first_frame_concept || '', share_reason: picked.share_reason || '', evidence_score: picked.evidence_score || 0, visual_score: picked.visual_score || 0, share_score: picked.share_score || 0, candidates } };"
    code = replace_once(code, old_return, new_return, "picked candidate fields")
    node["parameters"]["jsCode"] = code


def add_research_nodes(workflow: dict) -> None:
    names = {n.get("name") for n in workflow.get("nodes", [])}
    if WIKI_NODE not in names:
        workflow["nodes"].append({
            "id": "5a136c69-c4d8-4b42-9f24-viralquality001",
            "name": WIKI_NODE,
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [3300, 460],
            "onError": "continueRegularOutput",
            "parameters": {
                "method": "GET",
                "url": "={{ 'https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&utf8=1&srlimit=5&srsearch=' + encodeURIComponent($json.research_query || $json.topic) }}",
                "options": {"timeout": 15000},
            },
        })
    if RESEARCH_NODE not in names:
        workflow["nodes"].append({
            "id": "b4e7a5af-0ab0-4757-a952-viralquality002",
            "name": RESEARCH_NODE,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [3600, 300],
            "parameters": {
                "jsCode": "const picked = $('Extract Generated Topic').item.json;\nconst raw = $input.first().json || {};\nconst hits = Array.isArray(raw?.query?.search) ? raw.query.search : [];\nconst strip = s => String(s || '').replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();\nconst research_evidence = hits.slice(0, 5).map(h => ({ title: String(h.title || ''), snippet: strip(h.snippet), pageid: h.pageid }));\nreturn { json: { ...picked, research_evidence, research_source: 'Wikipedia search', research_available: research_evidence.length > 0 } };"
            },
        })

    conns = workflow.setdefault("connections", {})
    extract = conns.setdefault("Extract Generated Topic", {"main": [[]]})
    first = extract.setdefault("main", [[]])[0]
    # Remove direct writer edge; preserve backlog logging.
    first[:] = [e for e in first if e.get("node") != "Claude: Draft Script (Stage 1)"]
    if not any(e.get("node") == WIKI_NODE for e in first):
        first.append({"node": WIKI_NODE, "type": "main", "index": 0})
    conns[WIKI_NODE] = {"main": [[{"node": RESEARCH_NODE, "type": "main", "index": 0}]]}
    conns[RESEARCH_NODE] = {"main": [[{"node": "Claude: Draft Script (Stage 1)", "type": "main", "index": 0}]]}


def patch_writer_prompt(node: dict) -> None:
    body = node["parameters"]["jsonBody"]
    if "EVIDENCE CONTRACT - QUALITY_GATE" not in body:
        anchor = "AUDIENCE: Broad, general audience. They want either a concrete surprising fact OR a genuinely clear, useful explanation - no vague generalities, no lifestyle-influencer fluff."
        addition = anchor + "\\n\\nEVIDENCE CONTRACT - QUALITY_GATE: external reference search snippets are supplied below. Treat them as leads, not permission to invent. Build the script around claims supported by those snippets or by truly established common knowledge. If the exact viral claim is not supported, narrow/reframe it rather than bluffing. Never manufacture a quote, date, number, causal explanation, or anecdote to preserve a hook. Specificity is valuable only when defensible."
        body = replace_once(body, anchor, addition, "writer evidence contract")

    old = "SHORTS RETENTION PRINCIPLES (whatever length this specific topic earns, the middle is where it lives or dies): 1) The hook (scene 1) must be the single most dramatic, curiosity-igniting line in the whole script - state a contradiction or impossible-sounding claim and withhold the payoff. 2) RE-HOOK EVERY SCENE: each scene must end on a micro-cliffhanger, a raised stake, or a question the next scene answers - the viewer must never hit a moment where nothing new is coming. 3) ESCALATE, never list: each beat has to make the last one bigger, stranger, or higher-stakes (BUT / THEREFORE, never AND-THEN). 4) OPEN LOOPS: plant a question or tension early and only resolve it near the end, so viewers stay to close the loop. 5) NO FLAT MIDDLE: the instant a scene is just another fact with no forward pull, it is dead weight - rewrite it to escalate or cut it. 6) Weave the one spoken engagement question in naturally around two-thirds through (never at the end), then build to a payoff that tops the hook, then end on the kicker - nothing spoken after it except the separate outro_line share card."
    new = "SHORTS RETENTION RHYTHM - QUALITY_GATE: retention comes from changing cognitive gears, not from making every sentence sound like a cliffhanger. Scene 1 must create immediate curiosity or value. After that, deliberately alternate beat functions: TENSION (open/expand a question), CONTEXT (the minimum needed to understand), PAYOFF (close a loop with a concrete answer), BREATH (a quick human reaction, humor, visual beat, or satisfying observation), then new TENSION only if the material earns it. A strong Short can pay off one question early and open a better one; it does not have to hoard every answer until the end. Avoid repeated rhetorical questions, repeated 'but then' constructions, and synthetic micro-cliffhangers. Every scene must add new information, emotion, or visual meaning, but it does NOT need to end on a cliffhanger. The final payoff/kicker should leave the viewer satisfied enough to rewatch or share, not merely relieved that a withheld fact was finally revealed."
    body = replace_once(body, old, new, "writer retention rhythm")

    old_flow = "Either way: every transition must carry BUT or THEREFORE tension, never AND THEN listing - if a scene could be cut without breaking the chain, it is padding, cut it."
    new_flow = "Either way: transitions must feel causally or emotionally connected, but do not force the literal BUT/THEREFORE pattern onto every beat. Use contrast, consequence, reveal, reaction, or visual juxtaposition. If a scene could be cut without losing information, emotion, setup/payoff, or a necessary visual pattern interrupt, it is padding - cut it."
    body = replace_once(body, old_flow, new_flow, "writer transition rule")

    visual_anchor = "SCENE 1 IS THE THUMBNAIL: its image is the first frame in the feed and the de facto thumbnail. Show the subject at its most striking, clearest moment - never at rest or generic. Make it the most arresting composition of the set."
    visual_new = visual_anchor + "\\n- FIRST 500MS TEST - QUALITY_GATE: before narration can rescue the video, the opening frame/clip must already create a visual question. Prefer visible motion, extreme scale, a face/reaction when a real person is legitimately available, a physically unusual state, or a clean before/after/comparison composition. Reject generic establishing shots. The first_frame_concept from topic selection is a creative constraint: either execute it or replace it with a demonstrably stronger concrete visual.\\n- VISUAL PROGRESSION: do not illustrate each sentence literally with interchangeable stock. Across the Short, vary shot scale and function: hero image/motion -> evidence/detail -> scale/comparison/reveal -> satisfying payoff image. At least one beat should work with the sound off."
    body = replace_once(body, visual_anchor, visual_new, "writer visual impact")

    topic_anchor = "Topic: \" + $json.topic + \""
    topic_new = "Topic: \" + $json.topic + \"\\nFirst-frame concept from selection: \" + ($json.first_frame_concept || '') + \"\\nWhy this should be shared: \" + ($json.share_reason || '') + \"\\nExternal reference leads (do not overclaim beyond them): \" + JSON.stringify($json.research_evidence || []) + \""
    body = replace_once(body, topic_anchor, topic_new, "writer research input")
    node["parameters"]["jsonBody"] = body


def patch_validator(node: dict) -> None:
    code = node["parameters"]["jsCode"]
    if "QUALITY_GATE commissioning gate" in code:
        return
    anchor = "const errors = [];"
    gate = """const errors = [];

// QUALITY_GATE commissioning gate. Structural validity is not enough:
// mediocre scripts are deliberately rejected so the existing retry loop spends
// the slot on a different concept instead of rendering disposable content.
const q = parsed.quality;
const qualityMinimums = {
  concept_strength: 76,
  hook_strength: 78,
  evidence_strength: 74,
  payoff_strength: 76,
  information_density: 74,
  first_frame_strength: 78,
  visual_progression: 74,
  shareability: 76,
  naturalness: 74,
  overall: 77,
};
if (!q || typeof q !== 'object') {
  errors.push('quality object missing - final commissioning editor must score the Short');
} else {
  for (const [metric, minimum] of Object.entries(qualityMinimums)) {
    const value = Number(q[metric]);
    if (!Number.isFinite(value) || value < minimum) {
      errors.push(`quality.${metric}=${q[metric]} is below publish threshold ${minimum}`);
    }
  }
  // The prompt tells the editor "overall may not exceed the lowest of
  // concept/evidence/first-frame/payoff/shareability by more than 5" but
  // nothing enforced it - a self-graded overall could inflate past what the
  // constituent dimensions actually support. Verify it in code.
  const overallCapDimensions = ['concept_strength', 'evidence_strength', 'first_frame_strength', 'payoff_strength', 'shareability'];
  const overallCapValues = overallCapDimensions.map((m) => Number(q[m])).filter(Number.isFinite);
  if (overallCapValues.length === overallCapDimensions.length) {
    const weakest = Math.min(...overallCapValues);
    const overall = Number(q.overall);
    if (Number.isFinite(overall) && overall > weakest + 5) {
      errors.push(`quality.overall=${overall} exceeds the weakest scored dimension (${weakest}) by more than 5 points - self-graded overall is inflated relative to its own component scores`);
    }
  }
}
"""
    code = replace_once(code, anchor, gate, "validator quality gate")
    node["parameters"]["jsCode"] = code


def upgrade(workflow: dict) -> dict:
    patch_topic_prompt(node_by_name(workflow, "Claude: Generate Topic"))
    patch_topic_parser(node_by_name(workflow, "Extract Generated Topic"))
    add_research_nodes(workflow)
    patch_writer_prompt(node_by_name(workflow, "Claude: Draft Script (Stage 1)"))
    # EDITORIAL_STAGE_REMOVED: Claude: Editorial Rewrite (Stage 2) was removed
    # from the pipeline (see upgrade-creative-system.py) after repeatedly
    # discarding the given draft's topic and substituting an unrelated one in
    # production, even after two rounds of explicit prompt instructions plus
    # a deterministic validation backstop. Draft Script -> Visual Director
    # directly now; Visual Director already performs its own independent
    # quality critique and grading pass.
    patch_validator(node_by_name(workflow, "Validate Final Script"))
    return workflow


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-viral-shorts.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    workflow = json.loads(src.read_text(encoding="utf-8"))
    upgraded = upgrade(workflow)
    dst.write_text(json.dumps(upgraded, indent=2) + "\n", encoding="utf-8")
    print(f"viral quality workflow written to {dst}")


if __name__ == "__main__":
    main()
