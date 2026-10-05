"""Split hard production gates from virality predictions, and route failures.

Two problems with the inherited quality gate:

1. Twelve AI-critic scores were all hard floors, including ones that are
   really forecasts of audience behaviour (shareability, hook, concept,
   distinctiveness...). A model guessing what strangers will do could kill a
   script. Those scores now only get recorded (predicted_virality), so they
   can be checked against real performance; only production-quality floors
   (evidence, payoff, density, visual progression, naturalness) still reject.

2. Every failure went to the same "repair in place" pass. Polishing a weak
   hook rarely produces a strong one. Each failure is now tagged with the
   response it needs (fix a field, write a new hook branch, replan visuals,
   rewrite claims, ...) and the repair prompt follows the tag.

Also fixes a latent bug: the repair pass's documented topic switch
(resolved_topic, used after a medical rejection) was always rejected by the
topic-drift backstop, which only knew the original topic.

Applied after the niche layer; every patch is anchor-checked.
"""
from __future__ import annotations

MARKER = "GATE_SPLIT_V1"
ROUTING_MARKER = "GATE_ROUTING_V1"

VALIDATE = "Validate Final Script"
VISUAL = "Claude: Visual Director"
REPAIR = "Claude: Repair Script"
LOG = "Log Published Video"

HARD_FLOORS = {
    "evidence_strength": 74,
    "payoff_strength": 76,
    "information_density": 74,
    "visual_progression": 74,
    "naturalness": 74,
}
VIRALITY_KEYS = [
    "concept_strength", "hook_strength", "first_frame_strength",
    "shareability", "distinctiveness", "voice_specificity", "overall",
]

QUALITY_GATE_JS = (
    "// GATE_SPLIT_V1: only production-quality floors reject a script. The\n"
    "// critic's audience forecasts are recorded as predicted_virality so they can\n"
    "// be checked against real performance - a model guessing what strangers\n"
    "// will share must never kill a script.\n"
    "const q = parsed.quality;\n"
    "const qualityMinimums = " + "{\n" + "".join(f"  {k}: {v},\n" for k, v in HARD_FLOORS.items()) + "};\n"
    "const viralityPredictionKeys = " + str(VIRALITY_KEYS).replace("'", "'") + ";\n"
    "if (!q || typeof q !== 'object') {\n"
    "  errors.push('quality object missing - final commissioning editor must score the Short');\n"
    "} else {\n"
    "  for (const [metric, minimum] of Object.entries(qualityMinimums)) {\n"
    "    const value = Number(q[metric]);\n"
    "    if (!Number.isFinite(value) || value < minimum) {\n"
    "      errors.push(`quality.${metric}=${q[metric]} is below publish threshold ${minimum}`);\n"
    "    }\n"
    "  }\n"
    "  const _pv = {};\n"
    "  for (const k of viralityPredictionKeys) { const v = Number(q[k]); if (Number.isFinite(v)) _pv[k] = v; }\n"
    "  const _pvValues = Object.values(_pv);\n"
    "  parsed.predicted_virality = _pvValues.length\n"
    "    ? { ..._pv, score: Math.round(_pvValues.reduce((a, b) => a + b, 0) / _pvValues.length) }\n"
    "    : null;\n"
    "}\n\n"
)

# Pacing and claim calibration, inserted before the medical backstop (errors and
# parsed are in scope there). A scene is one picture, so a long scene is a still
# held too long; absolutes overclaim what social-psychology research supports.
MAX_SCENE_WORDS = 26
PACING_CLAIMS_JS = r"""// SCENE_PACING_V1: one scene = one picture, so cap spoken words per scene.
if (Array.isArray(parsed.scenes)) {
  parsed.scenes.filter((s) => s && !(s.template_data && s.template_data.is_outro)).forEach((s) => {
    const n = String(s.narration || '').trim().split(/\s+/).filter(Boolean).length;
    if (n > __MAX__) errors.push(`scene ${s.scene_index} narration has ${n} words (max __MAX__) - split it into separate scenes, each with its own footage`);
  });
}
// CLAIM_CALIBRATION_V1: one behaviour is a clue, not proof.
const _ccText = [parsed.title, ...(Array.isArray(parsed.scenes) ? parsed.scenes.map((s) => s && s.narration) : [])].filter(Boolean).join(' ');
const _ccHit = _ccText.match(/\b(?:the most reliable|always means|never lies|guaranteed|regardless of what (?:they|he|she|people) says?|without exception|scientifically proven|proves that)\b|\b100 ?%/i);
if (_ccHit) errors.push(`absolute claim "${_ccHit[0]}" - restate it as a tendency the research supports (often / a sign that)`);

""".replace("__MAX__", str(MAX_SCENE_WORDS))

# Order matters: the first matching rule wins.
ROUTING_JS = r"""// GATE_ROUTING_V1: tag every failure with the response it needs, so the
// repair pass regenerates creative work instead of polishing a weak hook.
const _routeFor = (e) => {
  if (/medical\/health content/.test(e)) return 'NEW_TOPIC';
  if (/different topic than the one selected/.test(e)) return 'REWRITE_ON_TOPIC';
  if (/^scene \d+ narration has \d+ words/.test(e)) return 'SPLIT_LONG_SCENE';
  if (/^absolute claim /.test(e)) return 'REWRITE_CLAIMS';
  if (/^hook (opens with a generic phrase|missing)/.test(e)) return 'NEW_HOOK_BRANCH';
  if (/^quality\.evidence_strength/.test(e)) return 'REWRITE_CLAIMS';
  if (/^quality\.payoff_strength|^payoff\.|promise/.test(e)) return 'FIX_PROMISE_PAYOFF';
  if (/^quality\.visual_progression|visual_plan_quality|first_frame|scene_index 0|Remotion template|template_|visual_|proof mode|required_|forbidden_visuals|acceptable_visuals|literal_|annotated_real| map | timeline | diagram /.test(e)) return 'NEW_VISUAL_TREATMENT';
  return 'REPAIR_FIELDS';
};
return { json: { _scriptValid: false, _validationErrors: errors.map((e) => `[${_routeFor(e)}] ${e}`), _failedScript: parsed } };"""

ROUTING_PROMPT = r"""\n\nGATE_ROUTING_V1 - every failed check below starts with an action tag. Follow it; where it conflicts with the preserve-everything instructions above, the tag wins for the parts it names:\n- [REPAIR_FIELDS]: fix exactly the named field(s) and preserve everything else.\n- [NEW_HOOK_BRANCH]: do not polish the failed hook. Discard it and its hook_candidates, write 5 NEW candidates in shapes the failed hook did not use, set hook to the strongest, and align the title and scene 0 narration with it. Keep the promise the rest of the script delivers.\n- [FIX_PROMISE_PAYOFF]: make the hook, title, promised_points and payoff agree, and make every promised item specific and fully delivered - rewrite weak items rather than renumbering around them.\n- [REWRITE_CLAIMS]: narrow every claim to what the supplied evidence supports and reframe the hook if it overclaims. State findings as tendencies (often, tends to, a sign that), never absolutes. Never invent evidence.\n- [SPLIT_LONG_SCENE]: split the named scene into two or more scenes of at most 22 words each, each with its own visual_claim, proof mode and search queries (a new picture per scene). Renumber scene_index sequentially from 0 and update payoff.resolved_in_scene and promised_points to match; this overrides the instruction to keep scene numbering unchanged.\n- [NEW_VISUAL_TREATMENT]: replan the visuals of the named scenes from scratch (new visual_claim, proof mode and search queries) instead of tweaking the failed plan.\n- [REWRITE_ON_TOPIC]: rewrite the script from scratch on the selected topic above, keeping none of the off-topic wording.\n- [NEW_TOPIC]: apply the CATEGORICAL TOPIC REJECTION exception above.\n\nFAILED CHECKS: """

FLOORS_PROMPT = (
    "GATE_SPLIT_V1 - you produce two kinds of score. HARD PRODUCTION FLOORS (below these the script is broken and must be fixed): "
    + "; ".join(f"{k} {v}" for k, v in HARD_FLOORS.items())
    + ". VIRALITY PREDICTIONS (" + ", ".join(VIRALITY_KEYS) + "): score them honestly as your forecast of audience response. "
    "They are recorded to rank ideas and to learn from real performance, and are NEVER a reason to reject, repair or downgrade a script - "
    "there is nothing to pass, so do not inflate them."
)

TOPIC_SEED_JS = r"""const _seedBase = (($('Extract Generated Topic').item||{}).json||{});
// GATE_ROUTING_V1: a categorical-rejection repair may switch to a candidate-pool
// topic and must name it in resolved_topic - judge drift against that topic.
const _resolvedTopic = String(parsed.resolved_topic||'').trim().toLowerCase();
const _resolvedItem = _resolvedTopic ? (Array.isArray(_seedBase.candidate_pool) ? _seedBase.candidate_pool : []).find((c) => c && String(c.topic||'').trim().toLowerCase() === _resolvedTopic) : null;
const seedTopicItem = _resolvedItem || _seedBase;"""


def _nodes(workflow: dict) -> dict:
    return {n.get("name"): n for n in workflow.get("nodes", [])}


def _sub(text: str, old: str, new: str, label: str) -> str:
    found = text.count(old)
    if found != 1:
        raise RuntimeError(f"{MARKER}: anchor for {label} found {found}x, expected 1x")
    return text.replace(old, new)


def _between(text: str, start: str, end: str, new: str, label: str) -> str:
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0 or text.count(start) != 1:
        raise RuntimeError(f"{MARKER}: anchors for {label} not found exactly once")
    return text[:i] + new + text[j:]


def apply(workflow: dict) -> None:
    nodes = _nodes(workflow)
    p = nodes[VALIDATE]["parameters"]
    if MARKER in p["jsCode"]:
        return
    code = p["jsCode"]
    code = _between(code, "const q = parsed.quality;", "if (!parsed.hook || parsed.hook.length < 5", QUALITY_GATE_JS, "quality floors")
    code = _sub(code, "return { json: { _scriptValid: false, _validationErrors: errors, _failedScript: parsed } };", ROUTING_JS, "failure routing")
    code = _sub(code, "const seedTopicItem = (($('Extract Generated Topic').item||{}).json||{});", TOPIC_SEED_JS, "resolved topic seed")
    medical = "// Medical/health exclusion backstop - a best-effort keyword scan, not"
    code = _sub(code, medical, PACING_CLAIMS_JS + medical, "pacing and claim checks")
    p["jsCode"] = code

    for name in (VISUAL, REPAIR):
        body = nodes[name]["parameters"]["jsonBody"]
        body = _between(body, "Judge the actual script against these publish floors:", r"\n\nClassify internally as PASS", FLOORS_PROMPT, f"{name} floors")
        nodes[name]["parameters"]["jsonBody"] = body
    body = nodes[REPAIR]["parameters"]["jsonBody"]
    nodes[REPAIR]["parameters"]["jsonBody"] = _sub(body, r"\n\nFAILED CHECKS: ", ROUTING_PROMPT, "repair routing prompt")

    log = nodes[LOG]["parameters"]
    anchor = "hook_candidates: $('Merge By scene_index (not position)').first().json.script_snapshot.hook_candidates || null,"
    log["jsonBody"] = _sub(log["jsonBody"], anchor,
                           anchor + " predicted_virality: $('Merge By scene_index (not position)').first().json.script_snapshot.predicted_virality || null,",
                           "published predicted_virality")


def assert_applied(workflow: dict) -> None:
    nodes = _nodes(workflow)
    code = nodes[VALIDATE]["parameters"]["jsCode"]
    for m in (MARKER, ROUTING_MARKER, "predicted_virality", "_resolvedItem", "SCENE_PACING_V1", "CLAIM_CALIBRATION_V1"):
        if m not in code:
            raise RuntimeError(f"{MARKER}: validator lost invariant: {m}")
    for k in ("shareability: 76", "hook_strength: 78", "concept_strength: 76"):
        if k in code:
            raise RuntimeError(f"{MARKER}: virality score is still a hard floor: {k}")
    for name in (VISUAL, REPAIR):
        if MARKER not in nodes[name]["parameters"]["jsonBody"]:
            raise RuntimeError(f"{MARKER}: {name} prompt still lists virality floors")
    if ROUTING_MARKER not in nodes[REPAIR]["parameters"]["jsonBody"]:
        raise RuntimeError(f"{MARKER}: repair prompt has no failure routing")
    if "predicted_virality" not in nodes[LOG]["parameters"]["jsonBody"]:
        raise RuntimeError(f"{MARKER}: predicted_virality is not logged at publish")
