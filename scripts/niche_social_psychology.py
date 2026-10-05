"""Channel niche layer: social psychology of attraction, respect and hidden signals.

The base workflow and every earlier upgrade layer were written for a trivia /
surprising-fact channel. This is the LAST prompt transform in the production
build: it swaps only the niche-specific passages of the final prompts, keeps
every schema, evidence, retry and visual-routing contract the earlier layers
installed, and adds a deterministic promise-payoff gate.

Every replacement is anchor-checked. If an earlier layer changes the text an
anchor points at, the build fails loudly instead of silently shipping a
half-trivia, half-psychology prompt.
"""
from __future__ import annotations

MARKER = "NICHE_SOCIAL_PSYCHOLOGY_V1"
NICHE_ID = "social_psychology_v1"
# ElevenLabs premade "Brian" - deep, calm, authoritative narration.
VOICE_ID = "nPczCjzI2devNBz1zQrb"
LEGACY_VOICE_ID = "UgBBYS2sOqTuMpoF3BR0"

TOPIC = "Claude: Generate Topic"
COMMISSION = "Claude: Commission Topic Shortlist"
DRAFT = "Claude: Draft Script (Stage 1)"
CRITIC = "Claude: Critique Hooks"
VISUAL = "Claude: Visual Director"
REPAIR = "Claude: Repair Script"
EXTRACT = "Extract Generated Topic"
POOL = "Parse Topic Pool"
VALIDATE = "Validate Final Script"
LOG = "Log Published Video"
TTS = "ElevenLabs: TTS+Timestamps"

# Prompt text lives inside a JS double-quoted string literal in an n8n
# expression: newlines are written as the two characters \n, and these blocks
# must never contain a double quote.

TOPIC_CRITERIA = r"""NICHE_SOCIAL_PSYCHOLOGY_V1 - CHANNEL NICHE: the psychology of attraction, respect and hidden social signals, for a broad general audience. Every topic speaks to something viewers quietly worry about - am I attractive, am I respected, do people secretly like me, how do others see me - and promises a concrete, research-backed answer.\n\nTHREE PROVEN TOPIC FAMILIES (rotate between them, never default to one):\n1. HIDDEN SIGNALS FROM OTHERS: what someone's behaviour secretly reveals about how they feel about YOU (e.g. 'Signs someone secretly dislikes you', 'What it means when someone mirrors your posture').\n2. SELF-CHECK REASSURANCE OR WARNING: signs YOU are more attractive, respected or likeable than you think - or a habit quietly costing you respect (e.g. 'Signs you are more attractive than you think', 'A habit that makes people take you less seriously').\n3. SOCIAL LEVERS: a specific, research-backed habit, phrase or behaviour that changes how people see you (e.g. 'Why people trust you more when you say their name', 'The posture that makes you look instantly more confident').\n\nFORMATS: a promise of 3-5 specific items ('4 signs...', '3 habits...'), a single-signal deep dive ('If someone does X when you talk, it means Y'), or a myth vs. research reversal ('Playing hard to get backfires - here is what research shows'). Lists are allowed and encouraged, but never more than 5 items.\n\nEVERY TOPIC MUST: speak to the viewer as YOU; name a concrete, observable behaviour or situation a stranger instantly recognizes (eye contact, posture, texting back, laughing at your jokes, a first meeting); and be backed by psychology research or well-established social-science findings, never folk wisdom presented as fact.\n- WEAK (generic, vague, or saturated): '5 signs you are attractive' with no fresh angle. / 'Body language is important.' / 'Confidence is key.'\n- STRONG (specific, recognizable, promise-shaped): 'If someone laughs at your jokes when they are not funny, it says more about them than the joke.' / 'Three small signs someone respects you more than they will ever say.' / 'People judge whether you look trustworthy within a fraction of a second - mostly from your face at rest.' / 'Saying the other person's name once early in a conversation makes them like you more.'\n\nNO-GO LIST (any candidate touching these is worthless - discard it): mental-health diagnoses or clinical labels (narcissist, psychopath, BPD, trauma bond, ADHD, depression); medical or health claims; sexual content or sexualised framing; shaming or degrading any gender, body type or group; money-making promises or financial advice; manipulation tactics framed as instructions to exploit people.\n\nSATURATION GATE: dozens of channels post near-identical '5 Signs You Are Attractive' Shorts that stall at 500-2,000 views. A candidate wins only if its angle, its specific signals or its framing is noticeably different from that template. Ask: would a viewer who has seen ten of these still stop for this one?\n\nQUALITY_GATE - QUALITY OVER CADENCE: your job is not to fill a publishing slot. A merely decent topic is a failure. Prefer a candidate with an emotionally loaded promise, specific defensible points, and a human situation that reads visually in under half a second.\n\nEVIDENCEABILITY GATE: reward claims that can be checked against psychology research or reputable science reporting. Penalize pop-psychology folklore, invented percentages, and claims whose punch depends on a suspiciously precise number. The writer will receive external reference snippets after you choose, so pick topics with a clean searchable evidence trail.\n\nVISUAL VIRALITY GATE: score the FIRST FRAME. The topic must suggest an arresting vertical human situation that stock footage carries: two people talking, eye contact, a first date, a meeting, someone laughing, someone glancing at a phone. Abstract concepts with no visible situation are weak.\n\nSHARE TEST: would a viewer send this to one specific person ('this is so you', 'remember when they did this')? Recognition, reassurance and 'I knew it' are the share drivers here.\n\nBefore answering, think through several candidate topics, stress-test each one against the WEAK/STRONG bar and the saturation gate, and pick only the strongest.\n\n"""

TOPIC_ARCHETYPES = r"""CREATIVE_SYSTEM - SEARCH A LARGE CREATIVE SPACE: every candidate must use one viewing archetype from: hidden_signal, self_check_reassurance, warning_sign, social_lever, attraction_myth. The archetype is the viewer experience, not the subject. Rotate archetypes as aggressively as subjects.\n\nArchetype definitions: hidden_signal = what another person's behaviour secretly reveals about how they see you; self_check_reassurance = signs you are more attractive, respected or likeable than you think; warning_sign = signs someone dislikes, disrespects or is quietly testing you, or a habit of yours that costs respect; social_lever = one research-backed habit, phrase or behaviour that changes how people see you; attraction_myth = a common belief about attraction or respect that research contradicts.\n\n"""

DRAFT_INTRO = r"""You are the head writer for a faceless YouTube Shorts channel about the psychology of attraction, respect and hidden social signals, written for a broad general audience.\n\nHARD EXCLUSION (non-negotiable): never write about medical, health or mental-health-diagnosis topics - no diseases, conditions, symptoms, treatments, medications, or clinical labels such as narcissist, psychopath, BPD, trauma bond, ADHD or depression. Describe behaviour, never diagnose it. If the topic drifts toward this, reframe it toward observable everyday behaviour instead.\n\nNO-GO LIST (non-negotiable): no sexual content or sexualised framing; no shaming or degrading any gender, body type or group; no money-making promises; never teach manipulation as a tactic to exploit people - frame dark-psychology material as how to recognise it.\n\nTHE FORMULA: a sensational, insecurity-driven hook that makes a specific promise, and a script that honestly and completely delivers on it. Hook big, deliver real value - the viewer must finish feeling 'that was worth it', never tricked.\n\nAUDIENCE: everyday people who quietly wonder whether they are attractive, respected and liked, and how others really see them. They want concrete, recognizable signals and habits - no vague self-help, no lecturing.\n\n"""

DRAFT_VOICE = r"""VOICE: calm, perceptive and quietly confident - like a friend who reads people unusually well and is letting you in on what they notice. Speak directly to the viewer as YOU. Warm, never preachy; intriguing, never sleazy; a little dry humour is welcome when a behaviour is genuinely funny or relatable.\n\n"""

DRAFT_HOOK = r"""HOOK - the single most important sentence in the video. It must hit a real insecurity or desire (being attractive, respected, liked, seen as confident, being secretly judged) and make a SPECIFIC promise the script will keep.\n\nHOOK PATTERNS THAT WORK IN THIS NICHE (vary the shape every time, never the same template twice in a row):\n- Number promise: 'Three signs someone secretly respects you more than they show.'\n- Hidden meaning: 'If someone does this when you talk, they like you more than they admit.'\n- Self-check: 'You are more attractive than you think if people do this around you.'\n- Warning: 'This tiny habit makes people take you less seriously - and almost everyone does it.'\n- Myth reversal: 'Playing hard to get backfires. Here is what actually works.'\nWEAK vs STRONG: 'Body language matters in dating' -> 'People notice this about you before your face.' / 'Some people do not like you' -> 'Four signs someone secretly dislikes you - the last one is the giveaway.'\n\nSENSATIONAL BUT HONEST: the hook may be bold, emotional and curiosity-driven - 'secretly', 'instantly', 'without realising' are welcome when true. It must never promise something the script cannot deliver: no miracle results, no guaranteed outcomes, no invented percentages. If the evidence only supports 'tends to' or 'research suggests', the hook may stay bold about the situation but the claim inside it must stay accurate.\n\nLEAD WITH THE PROMISE: the first two seconds state the situation and the promise - never a setup, greeting, or topic announcement. Withhold the actual signals for the body of the video; the hook names what the viewer will learn, the script delivers it.\n\nFIVE DIFFERENT HOOKS, FIVE DIFFERENT SHAPES: draft five real candidates using different patterns from the list above. Every candidate must make the same promise the script delivers, with the same item count if one is promised. Output all five in hook_candidates, the strongest in hook.\n\nPROMISE-PAYOFF CONTRACT (hard gate, checked by code): if the hook or title promises a number of items ('3 signs', 'four habits'), the script must contain EXACTLY that many items, each delivered in its own scene, and every item must be specific, observable and genuinely deliver the promise - no filler item, no vague 'be confident'. Output promised_points: an array of the scene_index values of those item scenes, in order (never scene 0, the hook). The hook and the title must promise the same number. If nothing is counted, output promised_points: [] and still deliver the hook's exact promise in payoff.resolved_in_scene.\n\nDRAMATIC LICENSE, LIMITED: tell true findings vividly, but never invent a study, statistic, quote or result, and never present folk wisdom as research.\n\n"""

DRAFT_STRUCTURE = r"""STRUCTURE - pick the one that fits the topic:\n- NUMBERED SIGNALS (3-5 items): scene 0 = hook with the promise; one scene per item, each naming a concrete observable behaviour and what it means, in one or two short sentences; order items so a strong one comes first and the most surprising one comes last; the last item scene is the payoff.\n- SINGLE-SIGNAL DEEP DIVE: scene 0 = the behaviour and the promise; middle scenes = what it looks like in real life, why research says it happens, and how to read it; final scene = the takeaway the viewer can use today.\n- MYTH VS RESEARCH: scene 0 = the belief and the reversal; middle = what research actually found; final = what to do instead.\nAcross all structures: every scene adds a new concrete signal, situation or finding - never a summary, never generic advice. Make each item feel like recognition ('you have seen this') followed by meaning ('here is what it says'). End on the strongest item or takeaway, with no summary and no generic outro, and NO question after it.\n\nSHAREABILITY: people share what makes them feel seen or validated, or what explains someone in their life. Frame items so a viewer thinks of one specific person - 'that is exactly what my coworker does'.\n\n"""

DRAFT_TITLE = r"""- title: the insecurity plus the specific promise, in the viewer's own words, with the same number as the hook if one is promised (e.g. '3 Signs Someone Secretly Respects You', 'If They Do This, They Like You'). Bold and curiosity-driven, never misleading. Front-load the punchiest words (mobile truncates the end). MAXIMUM 60 characters - a hard ceiling, not a target.\n"""

CRITIC_RUBRIC = r"""Score each candidate ONLY on: self-relevance (is it about the viewer, as YOU?); emotional pull (does it hit a real insecurity or desire - attractive, respected, liked, judged?); promise specificity (a number or a concrete outcome, not vague); promise_backed (could an honest script fully deliver it, with no miracle claim or invented statistic? a hook that oversells must lose); and is the opening situation visually supportable by real stock footage of people? Penalize generic openers such as did you know, in this video, you won't believe, or a greeting, and penalize near-copies of saturated titles like '5 signs you are attractive'. NICHE_SOCIAL_PSYCHOLOGY_V1."""

VISUAL_CHECK = r"""\n\nNICHE_SOCIAL_PSYCHOLOGY_V1 VISUAL AND PROMISE CHECK: this channel covers the psychology of attraction, respect and hidden social signals. Visuals are real people in recognizable situations - conversations, eye contact, first dates, meetings, friends laughing, someone glancing at a phone - chosen so the exact behaviour each scene names is visible. Search queries name the behaviour and situation (e.g. 'woman laughing in conversation', 'man avoiding eye contact', 'couple talking in cafe'). Never suggestive or sexualised imagery, never an identifiable celebrity, never an image that mocks or shames a body type. PROMISE CHECK: promised_points lists the scene_index of each item the hook promises - copy it through unchanged (update it only if you renumbered scenes, and keep its length equal to the number the hook promises). When grading payoff_strength, score it below 70 if any promised item is vague, generic, repeated, or fails to deliver the hook's promise.\n\nRETENTION_EXPERIMENT_V1 VISUAL AND EDITORIAL CHECK:"""

RETENTION_TOPIC_OLD = (
    "prefer one specific observable mechanism, useful everyday fact, or emotionally clear true story with a "
    "recognizable subject and available real/archive evidence. The opening must work in one sentence and one "
    "literal shot. Treat broad history/scale packaging as a hypothesis to improve, not a banned niche."
)
RETENTION_TOPIC_NEW = (
    "prefer one specific, research-backed social signal or habit with a recognizable human situation and "
    "stock-available footage of people. The opening must work in one sentence and one literal shot."
)

# Deterministic promise-payoff gate, inserted into Validate Final Script just
# before the existing click-confirmation gate (errors/parsed/_draftScript are
# all in scope there).
PROMISE_GATE_JS = r"""// NICHE_PROMISE_COUNT_GATE: a hook/title that promises N items must deliver
// exactly N item scenes, listed in promised_points. Recover the field from the
// writer's draft when a later pass drops it, like the payoff backstop above.
if(!Array.isArray(parsed.promised_points)&&_draftScript&&Array.isArray(_draftScript.promised_points)){
  parsed.promised_points=_draftScript.promised_points;
}
const _npCount=(()=>{
  const words={two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10};
  const items='signs?|things?|habits?|ways?|tricks?|reasons?|rules?|mistakes?|secrets?|behaviou?rs?|traits?|phrases?|words?|signals?|clues?|moves?|tells?|red flags?|green flags?|lessons?|truths?|cues?|gestures?|questions?|types?';
  const re=new RegExp('\\b(\\d{1,2}|'+Object.keys(words).join('|')+')\\s+(?:[a-z\'-]+\\s+){0,2}(?:'+items+')\\b','i');
  return (t)=>{const m=String(t||'').match(re);if(!m)return null;const k=m[1].toLowerCase();const v=words[k]!==undefined?words[k]:Number(k);return Number.isFinite(v)&&v>=2&&v<=10?v:null;};
})();
const _npHook=_npCount(parsed.hook), _npTitle=_npCount(parsed.title);
if(_npHook!==null&&_npTitle!==null&&_npHook!==_npTitle){
  errors.push(`hook promises ${_npHook} items but title promises ${_npTitle} - the hook and title must promise the same number`);
}
const _npPromised=_npHook!==null?_npHook:_npTitle;
if(_npPromised!==null&&Array.isArray(parsed.scenes)){
  const _npContent=new Set(parsed.scenes.filter(s=>!(s&&s.template_data&&s.template_data.is_outro)).map(s=>Number(s.scene_index)));
  const _npPoints=Array.isArray(parsed.promised_points)?[...new Set(parsed.promised_points.map(Number))]:null;
  if(!_npPoints){
    errors.push(`hook/title promises ${_npPromised} items - promised_points must list the scene_index of each item scene`);
  }else if(_npPoints.length!==_npPromised){
    errors.push(`hook/title promises ${_npPromised} items but promised_points lists ${_npPoints.length} - deliver exactly ${_npPromised} items, one scene each`);
  }else if(_npPoints.some(i=>i===0||!_npContent.has(i))){
    errors.push('promised_points must reference existing content scenes and never scene 0 (the hook)');
  }
}
"""

MEDICAL_OLD = r"|vaccine|antibiotic)\b/i;"
MEDICAL_NEW = (
    r"|vaccine|antibiotic|narcissis\w*|psychopath\w*|sociopath\w*|bipolar|borderline personality"
    r"|ptsd|adhd|autis\w*|ocd|trauma bond\w*)\b/i;"
)


def _nodes(workflow: dict) -> dict:
    return {n.get("name"): n for n in workflow.get("nodes", [])}


def _sub(text: str, old: str, new: str, label: str, count: int = 1) -> str:
    found = text.count(old)
    if found != count:
        raise RuntimeError(f"{MARKER}: anchor for {label} found {found}x, expected {count}x")
    return text.replace(old, new)


def _between(text: str, start: str, end: str, new: str, label: str) -> str:
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0 or text.count(start) != 1:
        raise RuntimeError(f"{MARKER}: anchors for {label} not found exactly once")
    return text[:i] + new + text[j:]


def _patch_body(node: dict, fn) -> None:
    params = node["parameters"]
    params["jsonBody"] = fn(params["jsonBody"])


def apply(workflow: dict) -> None:
    nodes = _nodes(workflow)
    if MARKER in nodes[TOPIC]["parameters"]["jsonBody"]:
        return

    def topic(b: str) -> str:
        b = _between(b, "CRITERIA: pick ONE surprising", "HARD EXCLUSION (non-negotiable): never a medical", TOPIC_CRITERIA, "topic criteria")
        b = _sub(b,
                 "HARD EXCLUSION (non-negotiable): never a medical or health topic - no diseases, symptoms, treatments, medications, procedures, even framed as a surprising fact.",
                 "HARD EXCLUSION (non-negotiable): never a medical, health or mental-health-diagnosis topic - no diseases, symptoms, treatments, medications, procedures or clinical labels, even framed as a signal.",
                 "topic medical exclusion")
        b = _between(b, "CREATIVE_SYSTEM - SEARCH A LARGE CREATIVE SPACE:", "SHORTS_GROWTH_V2 TOPIC ALLOCATION", TOPIC_ARCHETYPES, "topic archetypes")
        b = _sub(b,
                 "(a) a familiar everyday object with a hidden function/mechanism, (b) a recognizable thing with a counterintuitive physical mechanism, (c) a famous action explained by surprising physics, (d) an instantly understood scale contradiction.",
                 "(a) hidden signals that someone secretly likes or dislikes you, (b) signs you are more attractive or respected than you think, (c) one small habit that changes how people see you, (d) a popular attraction belief that research contradicts.",
                 "seed archetypes")
        b = _sub(b, "(single surprising fact, recognizable subject, the two hard bans, and the mass-appeal gate)",
                 "(channel niche, topic families, no-go list, saturation and evidence gates)", "candidate rule recap")
        b = _sub(b, "topic (the fact as one plain sentence)", "topic (the promise as one plain sentence)", "topic field")
        b = _sub(b, "from comparable fact/curiosity channels", "from comparable social-psychology Shorts channels", "signals source")
        b = _sub(b, "such as lists, biographies, medical topics, or obscure subjects",
                 "such as diagnoses, medical claims, sexualised angles or shaming", "signals discard rule")
        return _sub(b, RETENTION_TOPIC_OLD, RETENTION_TOPIC_NEW, "topic retention priority")

    def commission(b: str) -> str:
        return _sub(b, RETENTION_TOPIC_OLD, RETENTION_TOPIC_NEW, "commission retention priority")

    def draft(b: str) -> str:
        b = _between(b, "You are the head writer for a YouTube Shorts channel", "EVIDENCE CONTRACT - QUALITY_GATE:", DRAFT_INTRO, "draft intro")
        b = _between(b, "VOICE: Confident, direct, to the point", "CHANNEL VOICE BIBLE - CREATIVE_SYSTEM:", DRAFT_VOICE, "draft voice")
        b = _between(b, "HOOK - the single most important sentence in the video.", "PLAN THE ENDING FIRST:", DRAFT_HOOK, "draft hook")
        b = _between(b, "STRUCTURE - pick whichever fits the topic", "SHORTS RETENTION RHYTHM - QUALITY_GATE:", DRAFT_STRUCTURE, "draft structure")
        b = _sub(b,
                 "- Lean into: dramatic directional lighting, macro-level detail, high contrast, shallow depth of field, cinematic color, museum-grade clarity.",
                 "- Lean into: real people in recognizable social situations (conversations, eye contact, first dates, meetings, friends laughing, someone checking a phone), natural expressions and body language, cinematic color, shallow depth of field. Never suggestive or sexualised imagery, never an identifiable celebrity.",
                 "draft visual style")
        b = _between(b, "- title: lead with the STAKES and EMOTION", "- tags:", DRAFT_TITLE, "draft title")
        b = _sub(b, "mix broad viral tags (fun facts, how to, shorts, explained)",
                 "mix broad niche tags (psychology, psychology facts, body language, attraction, self improvement, shorts)", "draft tags")
        b = _sub(b, r'\"payoff\": {\"claim\": string, \"resolved_in_scene\": number}, \"scenes\":',
                 r'\"payoff\": {\"claim\": string, \"resolved_in_scene\": number}, \"promised_points\": array of numbers, \"scenes\":',
                 "draft output schema")
        b = _sub(b, "($json.archetype || 'looks_fake_but_real')", "($json.archetype || 'hidden_signal')", "draft default archetype")
        b = _sub(b,
                 "Choose one concrete, sourced contradiction with a visible subject, mechanism, or useful consequence. No invented stakes or unsupported certainty. Broad labels like 'history is not what you think' are not an opening.",
                 "Choose one concrete, research-backed social signal or habit with a visible human situation. No invented stakes or unsupported certainty. Broad labels like 'body language matters' are not an opening.",
                 "draft retention focus")
        return _sub(b, "OPENING: scene 0 starts on the actual object, action, or result.",
                    "OPENING: scene 0 starts on the human situation the hook names.", "draft retention opening")

    def critic(b: str) -> str:
        b = _between(b, "Score each candidate ONLY on:", r"\n\nCANDIDATES:", CRITIC_RUBRIC, "critic rubric")
        return _sub(b,
                    r'\"withholds_payoff\":number,\"specific\":number,\"surprising\":number,\"visually_supportable\":number,\"total\":number',
                    r'\"self_relevance\":number,\"emotional_pull\":number,\"specific\":number,\"promise_backed\":number,\"visually_supportable\":number,\"total\":number',
                    "critic score schema")

    def visual(b: str) -> str:
        return _sub(b, r"\n\nRETENTION_EXPERIMENT_V1 VISUAL AND EDITORIAL CHECK:", VISUAL_CHECK, "visual niche check")

    _patch_body(nodes[TOPIC], topic)
    _patch_body(nodes[COMMISSION], commission)
    _patch_body(nodes[DRAFT], draft)
    _patch_body(nodes[CRITIC], critic)
    _patch_body(nodes[VISUAL], visual)
    _patch_body(nodes[REPAIR], visual)

    for name in (EXTRACT, POOL):
        p = nodes[name]["parameters"]
        p["jsCode"] = _sub(p["jsCode"], "String(c.archetype||'looks_fake_but_real')", "String(c.archetype||'hidden_signal')", f"{name} default archetype")

    p = nodes[VALIDATE]["parameters"]
    code = _sub(p["jsCode"], MEDICAL_OLD, MEDICAL_NEW, "medical keyword scan")
    anchor = "// Click-confirmation gate: the hook makes a promise; one scene must deliver it."
    p["jsCode"] = _sub(code, anchor, PROMISE_GATE_JS + anchor, "promise gate insertion")

    _patch_body(nodes[LOG], lambda b: _sub(
        b, "concept_archetype: $('Extract Generated Topic').first().json.archetype || null,",
        f"niche: '{NICHE_ID}', concept_archetype: $('Extract Generated Topic').first().json.archetype || null,",
        "published niche tag"))

    p["jsCode"] = _sub(p["jsCode"], "'Send this to your fact friend — subscribe for the next one.'",
                       "'Send this to someone who needs to see it — and follow for more.'", "default outro line")

    tts = nodes[TTS]["parameters"]
    tts["url"] = _sub(tts["url"], f"/text-to-speech/{LEGACY_VOICE_ID}/", f"/text-to-speech/{VOICE_ID}/", "narration voice")


def assert_applied(workflow: dict) -> None:
    nodes = _nodes(workflow)
    checks = {
        TOPIC: [MARKER, "hidden_signal, self_check_reassurance, warning_sign, social_lever, attraction_myth"],
        DRAFT: ["PROMISE-PAYOFF CONTRACT", "promised_points", "faceless YouTube Shorts channel about the psychology"],
        CRITIC: [MARKER, "promise_backed"],
        VISUAL: [MARKER],
        REPAIR: [MARKER],
        LOG: [f"niche: '{NICHE_ID}'"],
    }
    for name, markers in checks.items():
        body = nodes[name]["parameters"]["jsonBody"]
        for m in markers:
            if m not in body:
                raise RuntimeError(f"{MARKER}: {name} lost niche invariant: {m}")
    stale = ["mind-bending truth", "museum-grade clarity", "looks_fake_but_real", "fun facts, how to"]
    for name in (TOPIC, DRAFT):
        body = nodes[name]["parameters"]["jsonBody"]
        for s in stale:
            if s in body:
                raise RuntimeError(f"{MARKER}: trivia-era text survived in {name}: {s}")
    if "NICHE_PROMISE_COUNT_GATE" not in nodes[VALIDATE]["parameters"]["jsCode"]:
        raise RuntimeError(f"{MARKER}: promise-payoff gate missing from {VALIDATE}")
    if VOICE_ID not in nodes[TTS]["parameters"]["url"]:
        raise RuntimeError(f"{MARKER}: narration voice not switched")
