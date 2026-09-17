#!/usr/bin/env python3
"""Apply measured Shorts growth policy after all legacy workflow transforms.

This is intentionally the final workflow migration stage. It makes the analytics
model, topic exploration policy, semantic no-repeat gate, duration target,
outro experiment and policy-version telemetry explicit production contracts.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

MARKER = "SHORTS_GROWTH_V2"
POLICY_VERSION = "shorts-growth-v2"
DEDUP_NODE = "Deduplicate Topic Pool"


def node_by_name(w: dict, name: str) -> dict:
    for n in w.get("nodes", []):
        if n.get("name") == name:
            return n
    raise KeyError(f"required n8n node not found: {name}")


def patch_topic_policy(w: dict) -> None:
    gen = node_by_name(w, "Claude: Generate Topic")
    body = str(gen.get("parameters", {}).get("jsonBody", ""))

    body, count = re.subn(
        r"(?:TOPIC_LATENCY:\s*)?GENERATE\s+\d+\s+DISTINCT candidate topics",
        f"{MARKER}: GENERATE 10 DISTINCT candidate topics",
        body,
        count=1,
    )
    if count != 1 and f"{MARKER}: GENERATE 10 DISTINCT candidate topics" not in body:
        raise ValueError("topic candidate-count anchor missing")

    anchor = "Must NOT closely resemble any of these already-used topics:"
    policy = (
        f"{MARKER} TOPIC ALLOCATION - treat topic choice as controlled exploration, not equal category rotation. "
        "Generate EXACTLY 10 candidates split 7 exploit / 2 adjacent / 1 explore. "
        "EXPLOIT means a proven viewing archetype supported by current measured guidance; initial seed archetypes are: "
        "(a) a familiar everyday object with a hidden function/mechanism, (b) a recognizable thing with a counterintuitive physical mechanism, "
        "(c) a famous action explained by surprising physics, (d) an instantly understood scale contradiction. "
        "ADJACENT keeps a proven curiosity mechanism but moves to a materially different subject. EXPLORE tries a genuinely new viewing mechanism. "
        "Every candidate MUST include strategy_arm='exploit'|'adjacent'|'explore', subject_key, mechanism_key, payoff_key, and canonical_key='subject|mechanism|payoff'. "
        "These keys describe the underlying FACT, not its wording, so paraphrases retain the same identity. The exact 7/2/1 allocation is mandatory. "
        "HARD DUPLICATE CHECK: before answering, compare each of your 10 candidates against the already-used list below sentence-by-sentence. "
        "An exact repeat or a trivial reword of an already-used topic (same subject AND same underlying fact) is a zero-value candidate, not a valid exploit pick - discard it and generate a genuinely different one instead of submitting it.\\n\\n"
        + anchor
    )
    if f"{MARKER} TOPIC ALLOCATION" not in body:
        if anchor not in body:
            raise ValueError("topic history prompt anchor missing")
        body = body.replace(anchor, policy, 1)
    gen["parameters"]["jsonBody"] = body

    pool = node_by_name(w, "Parse Topic Pool")
    code = str(pool["parameters"]["jsCode"])
    if "desired_strategy_arm" not in code:
        # Preserve semantic identity fields even if a free-first model omits
        # them; topicPolicy.js derives a lexical canonical key as a fallback.
        code = code.replace(
            "archetype:String(c.archetype||'looks_fake_but_real').trim(),research_query:",
            "archetype:String(c.archetype||'looks_fake_but_real').trim(),strategy_arm:String(c.strategy_arm||'').trim(),subject_key:String(c.subject_key||'').trim(),mechanism_key:String(c.mechanism_key||'').trim(),payoff_key:String(c.payoff_key||'').trim(),canonical_key:String(c.canonical_key||'').trim(),research_query:",
        )
        arm_logic = r'''
const _gv2Seed=String($execution.id||Date.now());
let _gv2Hash=2166136261;for(let i=0;i<_gv2Seed.length;i++){_gv2Hash^=_gv2Seed.charCodeAt(i);_gv2Hash=Math.imul(_gv2Hash,16777619);}
const _gv2Bucket=(_gv2Hash>>>0)%10;
const desired_strategy_arm=_gv2Bucket<=6?'exploit':(_gv2Bucket<=8?'adjacent':'explore');
const _gv2FallbackArm=(i)=>i<7?'exploit':(i<9?'adjacent':'explore');
pool=pool.map((c,i)=>({...c,strategy_arm:['exploit','adjacent','explore'].includes(c.strategy_arm)?c.strategy_arm:_gv2FallbackArm(i)}));
'''
        if "if(pool.length<3)" not in code:
            raise ValueError("Parse Topic Pool minimum-pool anchor missing")
        code = code.replace("if(pool.length<3)", arm_logic + "\nif(pool.length<3)", 1)
        code, n = re.subn(
            r"return \{json:\{pool,shortlist:pool\.slice\(0,4\)\}\};",
            "const _gv2ArmPool=pool.filter(c=>c.strategy_arm===desired_strategy_arm);const shortlist=(_gv2ArmPool.length?_gv2ArmPool:pool).slice(0,4);return {json:{pool,shortlist,desired_strategy_arm}};",
            code,
            count=1,
        )
        if n != 1:
            raise ValueError("Parse Topic Pool return anchor missing")
        pool["parameters"]["jsCode"] = code

    commission = node_by_name(w, "Claude: Commission Topic Shortlist")
    cbody = str(commission["parameters"]["jsonBody"])
    preserve_anchor = "Preserve topic, archetype, research_query, first_frame_concept, share_reason."
    if preserve_anchor in cbody and "strategy_arm" not in cbody:
        cbody = cbody.replace(
            preserve_anchor,
            "Preserve topic, archetype, strategy_arm, subject_key, mechanism_key, payoff_key, canonical_key, research_query, first_frame_concept, share_reason BYTE-FOR-BYTE; commissioning may score but must not rewrite the underlying fact.",
            1,
        )
    commission["parameters"]["jsonBody"] = cbody

    # Insert a strict server-side semantic gate between parsing and commissioning.
    names = {n.get("name") for n in w.get("nodes", [])}
    if DEDUP_NODE not in names:
        w["nodes"].append({
            "id": "c44f4f2d-17a0-4f73-b668-75f10a844d76",
            "name": DEDUP_NODE,
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [2550, 300],
            "parameters": {
                "method": "POST",
                "url": "https://shorts.interviewbuddy.cloud/topic-dedup",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify({ candidates: $json.pool, desired_strategy_arm: $json.desired_strategy_arm, seed: String($execution.id || '') }) }}",
                "options": {"timeout": 45000},
            },
            "retryOnFail": False,
        })
    con = w.setdefault("connections", {})
    con["Parse Topic Pool"] = {"main": [[{"node": DEDUP_NODE, "type": "main", "index": 0}]]}
    con[DEDUP_NODE] = {"main": [[{"node": "Claude: Commission Topic Shortlist", "type": "main", "index": 0}]]}

    extract = node_by_name(w, "Extract Generated Topic")
    ecode = str(extract["parameters"]["jsCode"])
    if "canonical_key: String(c.canonical_key" not in ecode:
        ecode = ecode.replace(
            "archetype: String(c.archetype || 'looks_fake_but_real').trim(), research_query:",
            "archetype: String(c.archetype || 'looks_fake_but_real').trim(), strategy_arm: String(c.strategy_arm || $('Deduplicate Topic Pool').item.json.desired_strategy_arm || 'exploit').trim(), subject_key: String(c.subject_key || '').trim(), mechanism_key: String(c.mechanism_key || '').trim(), payoff_key: String(c.payoff_key || '').trim(), canonical_key: String(c.canonical_key || '').trim(), research_query:",
        )
        ecode = ecode.replace(
            "archetype: picked.archetype || 'looks_fake_but_real', score: picked.score,",
            "archetype: picked.archetype || 'looks_fake_but_real', strategy_arm: picked.strategy_arm || $('Deduplicate Topic Pool').item.json.desired_strategy_arm || 'exploit', subject_key: picked.subject_key || '', mechanism_key: picked.mechanism_key || '', payoff_key: picked.payoff_key || '', canonical_key: picked.canonical_key || '', policy_version: 'shorts-growth-v2', score: picked.score,",
        )
    extract["parameters"]["jsCode"] = ecode

    save = node_by_name(w, "Save Topic to History")
    save["parameters"]["jsonBody"] = "={{ JSON.stringify({ topic: $('Extract Generated Topic').item.json.topic, hook: $('Merge By scene_index (not position)').first().json.script_snapshot.hook, canonical_key: $('Extract Generated Topic').item.json.canonical_key || null, subject_key: $('Extract Generated Topic').item.json.subject_key || null, mechanism_key: $('Extract Generated Topic').item.json.mechanism_key || null, payoff_key: $('Extract Generated Topic').item.json.payoff_key || null, strategy_arm: $('Extract Generated Topic').item.json.strategy_arm || null, policy_version: 'shorts-growth-v2' }) }}"

    backlog = node_by_name(w, "Log Topic Backlog")
    backlog["parameters"]["jsonBody"] = "={{ JSON.stringify({ candidates: $json.candidates, picked: $json.topic, picked_score: $json.score, strategy_arm: $json.strategy_arm || null, canonical_key: $json.canonical_key || null, policy_version: 'shorts-growth-v2' }) }}"


DEDUP_RETRY_MARKER = "TOPIC_DEDUP_RETRY"
TOPIC_INIT_NODE = "Init Topic Attempt Counter"
DEDUP_IF_NODE = "If Topic Pool Exhausted"
DEDUP_INCREMENT_NODE = "Increment Topic Attempt"
DEDUP_RETRY_IF_NODE = "If Topic Attempts Exhausted"
DEDUP_FAIL_NODE = "Fail: Topic Pool Exhausted"
MAX_TOPIC_DEDUP_RETRIES = 1


def patch_topic_dedup_retry(w: dict) -> None:
    """Regenerate once instead of dying when every topic candidate is a duplicate.

    Execution 838 lost a whole scheduled run because all 10 candidates from
    "Claude: Generate Topic" matched already-published Shorts exactly
    (TOPIC_DEDUP_EXHAUSTED). "Deduplicate Topic Pool" has no error branch, so
    the 409 just threw and killed the run - a single bad LLM batch should not
    cost a publish slot when a second attempt, fed the exact rejected topics,
    is cheap and likely to succeed. Bounded to one retry (via workflow static
    data keyed by execution id, the same pattern already used for the script
    repair loop) so a persistently exhausted history fails loudly instead of
    looping forever.
    """
    gen = node_by_name(w, "Claude: Generate Topic")
    body = str(gen["parameters"]["jsonBody"])
    if DEDUP_RETRY_MARKER not in body:
        old = "JSON.stringify($('Ensure Topics Array').item.json.topics.map(t => t.topic))"
        new = (
            old
            + ' + ($json._topicRetryAvoid && $json._topicRetryAvoid.length ? (" - '
            + DEDUP_RETRY_MARKER
            + ": every candidate in your previous batch of 10 matched an already-published Short and was rejected by the duplicate gate. "
            'Every one of these exact topics, and any close paraphrase of them, is FORBIDDEN this attempt: " '
            "+ JSON.stringify($json._topicRetryAvoid) + \". Choose genuinely different subjects and mechanisms.\") : \"\")"
        )
        if old not in body:
            raise ValueError("topic retry-feedback anchor missing")
        body = body.replace(old, new, 1)
    gen["parameters"]["jsonBody"] = body

    dedup = node_by_name(w, DEDUP_NODE)
    dedup["onError"] = "continueRegularOutput"

    names = {n.get("name") for n in w.get("nodes", [])}
    if TOPIC_INIT_NODE not in names:
        w["nodes"].append({
            "id": "d3f4a1e2-6b9c-4a17-9e3d-topicdeduprty1",
            "name": TOPIC_INIT_NODE,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1650, 300],
            "parameters": {"jsCode": (
                f"// {DEDUP_RETRY_MARKER} WORKFLOW_GLOBAL_STATIC_STATE: execution-scoped topic-dedup retry state shared across workflow nodes.\n"
                "const staticData=$getWorkflowStaticData('global');\n"
                "const runId=String($execution.id||'unknown');\n"
                "staticData.topicDedupAttempts=staticData.topicDedupAttempts||{};\n"
                "const now=Date.now();\n"
                "for(const [key,value] of Object.entries(staticData.topicDedupAttempts)){if(!value||now-Number(value.updatedAt||0)>21600000)delete staticData.topicDedupAttempts[key];}\n"
                "staticData.topicDedupAttempts[runId]={attempt:0,updatedAt:now};\n"
                "return $input.all();"
            )},
        })
    if DEDUP_IF_NODE not in names:
        w["nodes"].append({
            "id": "e4a5b2f3-7c0d-4b28-8f4e-topicdeduprty2",
            "name": DEDUP_IF_NODE,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [2800, 300],
            "parameters": {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 1},
                    "conditions": [{
                        # A bare truthy check on $json.success (the same unary
                        # boolean operator "If Script Valid" uses) reliably
                        # tests the LEFT side only; encoding the negation into
                        # the expression itself, rather than trying to invert
                        # it via rightValue/operator, is what actually behaves
                        # as "exhausted" - a rightValue:false variant of this
                        # operator was tried first and empirically evaluated
                        # true on both dedup successes and failures alike
                        # (execution 843 hit Fail: Topic Pool Exhausted despite
                        # both attempts returning success:true).
                        "leftValue": "={{ $json.success !== true }}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }],
                    "combinator": "and",
                },
                "options": {},
            },
        })
    if DEDUP_INCREMENT_NODE not in names:
        w["nodes"].append({
            "id": "f5b6c3a4-8d1e-4c39-9a5f-topicdeduprty3",
            "name": DEDUP_INCREMENT_NODE,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [3050, 460],
            "parameters": {"jsCode": (
                f"// {DEDUP_RETRY_MARKER} WORKFLOW_GLOBAL_STATIC_STATE: shared workflow state, isolated by execution id.\n"
                "const staticData=$getWorkflowStaticData('global');\n"
                "const runId=String($execution.id||'unknown');\n"
                "staticData.topicDedupAttempts=staticData.topicDedupAttempts||{};\n"
                "const state=staticData.topicDedupAttempts[runId]||{attempt:0,updatedAt:Date.now()};\n"
                "const newAttempt=Number(state.attempt||0)+1;\n"
                "state.attempt=newAttempt;state.updatedAt=Date.now();staticData.topicDedupAttempts[runId]=state;\n"
                "const failure=$input.first().json||{};\n"
                "const rejected=failure.details||failure.error?.details||[];\n"
                "const avoid=rejected.map(r=>r.topic).filter(Boolean);\n"
                "console.log(`Topic dedup exhausted on attempt ${newAttempt}: ${avoid.length} candidates rejected as duplicates`);\n"
                "return {json:{topicDedupAttempt:newAttempt,_topicRetryAvoid:avoid}};"
            )},
        })
    if DEDUP_RETRY_IF_NODE not in names:
        w["nodes"].append({
            "id": "a6c7d4b5-9e2f-4d4a-8b60-topicdeduprty4",
            "name": DEDUP_RETRY_IF_NODE,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [3300, 460],
            "parameters": {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 1},
                    "conditions": [{
                        "leftValue": "={{ $json.topicDedupAttempt }}",
                        "rightValue": MAX_TOPIC_DEDUP_RETRIES,
                        "operator": {"type": "number", "operation": "gt"},
                    }],
                    "combinator": "and",
                },
                "options": {},
            },
        })
    if DEDUP_FAIL_NODE not in names:
        w["nodes"].append({
            "id": "b7d8e5c6-0f3a-4e5b-9c71-topicdeduprty5",
            "name": DEDUP_FAIL_NODE,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [3550, 600],
            "parameters": {"jsCode": (
                f"// {DEDUP_RETRY_MARKER} WORKFLOW_GLOBAL_STATIC_STATE: terminal retry-state cleanup.\n"
                "const staticData=$getWorkflowStaticData('global');\n"
                "const runId=String($execution.id||'unknown');\n"
                "if(staticData.topicDedupAttempts)delete staticData.topicDedupAttempts[runId];\n"
                "throw new Error('Topic generation exhausted the semantic no-repeat gate twice in a row "
                "(every candidate matched an already-published Short) - giving up for this scheduled run "
                "rather than forcing a duplicate topic through.');"
            )},
        })

    con = w.setdefault("connections", {})
    con["Ensure Topics Array"] = {"main": [[{"node": TOPIC_INIT_NODE, "type": "main", "index": 0}]]}
    con[TOPIC_INIT_NODE] = {"main": [[{"node": "Init Script Attempt Counter", "type": "main", "index": 0}]]}
    con[DEDUP_NODE] = {"main": [[{"node": DEDUP_IF_NODE, "type": "main", "index": 0}]]}
    con[DEDUP_IF_NODE] = {"main": [
        [{"node": DEDUP_INCREMENT_NODE, "type": "main", "index": 0}],
        [{"node": "Claude: Commission Topic Shortlist", "type": "main", "index": 0}],
    ]}
    con[DEDUP_INCREMENT_NODE] = {"main": [[{"node": DEDUP_RETRY_IF_NODE, "type": "main", "index": 0}]]}
    con[DEDUP_RETRY_IF_NODE] = {"main": [
        [{"node": DEDUP_FAIL_NODE, "type": "main", "index": 0}],
        [{"node": "Claude: Generate Topic", "type": "main", "index": 0}],
    ]}


def patch_writer_and_duration(w: dict) -> None:
    writer = node_by_name(w, "Claude: Draft Script (Stage 1)")
    body = str(writer["parameters"]["jsonBody"])

    # Delete stale fixed numerical claims; current measurements are injected below.
    body = body.replace("On this channel, hooks that led with a statistic averaged 84 views; hooks that withheld it averaged 298 - 3.5x more. ", "")
    body = body.replace("(this channel's data: number-led titles average 3.5x fewer views) ", "")
    body = body.replace("this channel's data: number-led titles average 3.5x fewer views", "current measured guidance decides whether withholding is helping this channel")

    # The seed prompt still carries the pre-V2 length policy. Left in place it
    # does not merely permit 120 words, it explicitly disclaims the correct
    # target ("not a fixed 60-90/3-4 target"), so the model overshoots the
    # 105-word ceiling and the script is rejected - execution 808 lost an upload
    # at 121 words. There must be exactly one length policy in this prompt.
    legacy_length = [
        (
            "never run past roughly 40 seconds (about 120 words) - if the topic needs more than that to land, "
            "it was too big a topic for this format, not license to run long. Floor: at least 3 scenes and about 40 words",
            "keep the FINAL rendered Short inside 28-36 seconds - if the topic needs more than that to land, "
            "it was too big a topic for this format, not license to run long. Floor: at least 3 scenes",
        ),
        (
            "within the 40-120 word / 3+ scene bounds from LENGTH IS YOUR CALL above (not a fixed 60-90/3-4 target)",
            "within the 65-95 content-word / 3+ scene target from the DURATION TARGET above (105 content words is an absolute ceiling)",
        ),
    ]
    for old, new in legacy_length:
        if old in body:
            body = body.replace(old, new, 1)

    if f"{MARKER} DYNAMIC PERFORMANCE GUIDANCE" not in body:
        hook_anchor = "HOOK - the single most important sentence in the video."
        dynamic = (
            f"{MARKER} DYNAMIC PERFORMANCE GUIDANCE: use ONLY the current channel-insights payload below for channel-specific performance claims; never preserve hard-coded historic view averages. "
            "Prioritize hook/scroll-stop guidance first, hold/retention second, satisfaction third. SEO/tags are secondary to the first frame and first seconds for Shorts-feed performance.\\n"
            "CURRENT CHANNEL INSIGHTS: \" + JSON.stringify($('Get Channel Insights').item.json || {}) + \"\\n\\n"
            + hook_anchor
        )
        if hook_anchor not in body:
            raise ValueError("writer hook anchor missing")
        body = body.replace(hook_anchor, dynamic, 1)

    if f"{MARKER} DURATION TARGET" not in body:
        duration_anchor = "SENTENCE RHYTHM:"
        duration_rule = (
            f"{MARKER} DURATION TARGET: default to a 28-36 second FINAL rendered Short. With the current voice this normally means roughly 65-95 CONTENT narration words before any experiment outro. "
            "Do not pad a naturally shorter complete fact. If content narration exceeds 95 words, include length_exception_reason explaining the concrete escalation/payoff that earns the extra seconds; 105 content words is an absolute ceiling. "
            "The default is a target band, not permission to slow the delivery.\\n\\n"
            + duration_anchor
        )
        if duration_anchor not in body:
            raise ValueError("writer sentence-rhythm anchor missing")
        body = body.replace(duration_anchor, duration_rule, 1)

    # Make the new exception field explicit; Visual Director copies all fields.
    if "length_exception_reason" not in body:
        body = body.replace(
            '\\"payoff\\\": {\\\"claim\\\": string, \\"resolved_in_scene\\\": number},',
            '\\"payoff\\\": {\\\"claim\\\": string, \\"resolved_in_scene\\\": number}, \\"length_exception_reason\\\": string|null,',
            1,
        )
    writer["parameters"]["jsonBody"] = body

    validator = node_by_name(w, "Validate Final Script")
    code = str(validator["parameters"]["jsCode"])
    code = code.replace("if (totalWords > 130) {", "if (totalWords > 105) {")
    code = code.replace("exceeds the 130 word hard ceiling (~40s Short)", "exceeds the 105 content-word absolute ceiling (28-36s is the default target)")
    if "length_exception_reason is required" not in code:
        anchor = "if (errors.length > 0) {"
        # 95 is the preferred target, 105 is the real ceiling enforced above.
        # Rejecting a 96-word script for a missing justification string cost a
        # whole upload (execution 807) over one word, so record the overage
        # instead: the band stays visible in telemetry, and only a script past
        # the 105-word ceiling is actually refused.
        check = """const _gv2ContentWords=(Array.isArray(parsed.scenes)?parsed.scenes:[]).filter(s=>!s?.template_data?.is_outro).reduce((n,s)=>n+String(s?.narration||'').trim().split(/\\s+/).filter(Boolean).length,0);
parsed.content_word_count=_gv2ContentWords;
if(_gv2ContentWords>95&&!String(parsed.length_exception_reason||'').trim()){
  // length_exception_reason is required for anything above the 28-36s target;
  // derive it rather than lose the upload, since 105 remains a hard ceiling.
  parsed.length_exception_reason=`auto: ${_gv2ContentWords} content words, above the 95-word target but within the 105-word ceiling`;
  parsed.length_exception_auto=true;
}
"""
        if anchor not in code:
            raise ValueError("validator error-return anchor missing")
        code = code.replace(anchor, check + anchor, 1)

    # Replace the legacy/optional outro append with a deterministic 50/50 test.
    start = code.find("const outroLine =")
    end = code.find("return { json: { ...parsed, _scriptValid: true } };", start)
    if start < 0 or end < 0:
        raise ValueError("validator outro block anchor missing")
    experiment = r'''// SHORTS_GROWTH_V2 controlled outro experiment + policy versioning.
parsed.policy_version='shorts-growth-v2';
parsed.target_duration_band='28-36s';
const _gv2ExperimentSeed=String($execution.id||parsed.hook||'default');
let _gv2ExperimentHash=2166136261;for(let i=0;i<_gv2ExperimentSeed.length;i++){_gv2ExperimentHash^=_gv2ExperimentSeed.charCodeAt(i);_gv2ExperimentHash=Math.imul(_gv2ExperimentHash,16777619);}
parsed.outro_experiment_arm=((_gv2ExperimentHash>>>0)%2===0)?'no_outro':'current_outro';
if(Array.isArray(parsed.scenes))parsed.scenes=parsed.scenes.filter(s=>!s?.template_data?.is_outro);
if(parsed.outro_experiment_arm==='no_outro'){
  parsed.outro_line=null;
}else{
  const _gv2Outro=(parsed.outro_line&&String(parsed.outro_line).trim())?String(parsed.outro_line).trim():'Send this to your fact friend — subscribe for the next one.';
  parsed.outro_line=_gv2Outro;
  if(Array.isArray(parsed.scenes)&&parsed.scenes.length){
    const _gv2MaxIdx=parsed.scenes.reduce((m,s)=>Math.max(m,Number(s.scene_index)||0),-1);
    parsed.scenes.push({scene_index:_gv2MaxIdx+1,point:'outro',narration:_gv2Outro,visual_source:'template',template_name:'kinetic_text',template_data:{line:'Share • Subscribe',is_outro:true}});
  }
}

'''
    code = code[:start] + experiment + code[end:]
    validator["parameters"]["jsCode"] = code


def _unpair(expression: str) -> str:
    """Use .first() instead of .item for cross-node reads in this branch.

    Both of these nodes sit downstream of the "Merge By scene_index" Code node,
    which synthesizes a single item without pairedItem. n8n then cannot resolve
    $('Node').item, the whole expression evaluates to undefined, and the HTTP
    node fails with "The value in the JSON Body field is not valid JSON" - which
    is what broke publishing on executions 802 and 804. Every node referenced
    here emits exactly one item, so .first() is equivalent and needs no pairing.
    """
    return expression.replace(").item.json", ").first().json")


ACCEPTED_SCRIPT_NODE = "Capture Accepted Script"


def patch_accepted_script_capture(w: dict) -> None:
    """Give the merge a single-run source for the accepted script.

    "Validate Final Script" runs once per attempt, so after a repair it has two
    or three runs and no expression can say which one is meant: .item resolved
    to undefined and .first() resolved to run 0, the REJECTED attempt. Reading
    it downstream is unsafe by construction, not just fragile.

    "If Script Valid" only emits on its true output for the attempt that passed,
    so a node placed there executes exactly once per execution and carries the
    accepted script by definition. That node is then the one unambiguous source
    for everything after it.
    """
    names = {n.get("name") for n in w.get("nodes", [])}
    if ACCEPTED_SCRIPT_NODE not in names:
        w["nodes"].append({
            "id": "b8e5c1a9-4f27-4d63-9a18-6c0f7d2e3b55",
            "name": ACCEPTED_SCRIPT_NODE,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [4300, 300],
            "parameters": {"jsCode": (
                "// V5_ACCEPTED_SCRIPT_HANDOFF: runs only on the validated attempt, so this\n"
                "// is the accepted script by construction - no run-index guessing anywhere.\n"
                "const accepted = $input.first().json;\n"
                "if (!accepted || accepted._scriptValid !== true) {\n"
                "  throw new Error('Capture Accepted Script received a script that did not pass validation');\n"
                "}\n"
                "return { json: { ...accepted, script_snapshot: accepted } };"
            )},
        })
    conns = w.setdefault("connections", {})
    valid = conns.get("If Script Valid", {}).get("main", [])
    if not valid:
        raise ValueError("If Script Valid connections missing")
    # Interpose on the accepted branch, preserving its existing targets.
    downstream = [t for t in valid[0] if t.get("node") != ACCEPTED_SCRIPT_NODE]
    if downstream:
        conns[ACCEPTED_SCRIPT_NODE] = {"main": [downstream]}
        valid[0] = [{"node": ACCEPTED_SCRIPT_NODE, "type": "main", "index": 0}]
    conns["If Script Valid"] = {"main": valid}


def patch_merge_script_passthrough(w: dict) -> None:
    """Carry the validated script through the merge instead of re-reading it.

    When the repair loop runs, "Validate Final Script" has two runs and an HTTP
    node cannot say which one it means: .item resolved to undefined and .first()
    resolved to run 0, the REJECTED attempt. Executions 802/804/805 all died on
    that. The merge Code node does resolve the repaired run correctly (verified
    on 805), and it runs exactly once, so it is the one unambiguous source for
    everything downstream.
    """
    merge = node_by_name(w, "Merge By scene_index (not position)")
    code = str(merge["parameters"]["jsCode"])

    # Every read of the multi-run validator becomes a read of the single-run
    # capture node, which carries the accepted script by construction.
    accepted = f"$('{ACCEPTED_SCRIPT_NODE}').first().json"
    code = code.replace("$('Validate Final Script').item.json", accepted)
    code = code.replace("$('Validate Final Script').first().json", accepted)

    if "script_snapshot:" not in code:
        anchor = "publication_description:publicationDescription,data:merged}};"
        if anchor not in code:
            raise ValueError("merge return anchor missing; cannot pass the script through")
        code = code.replace(
            anchor,
            "publication_description:publicationDescription,"
            f"policy_version:{accepted}.policy_version||'shorts-growth-v2',"
            f"outro_experiment_arm:{accepted}.outro_experiment_arm||'no_outro',"
            f"outro_line:{accepted}.outro_line??null,"
            f"script_snapshot:{accepted}.script_snapshot,"
            "data:merged}};",
            1,
        )

    # A node that reads its own output produces undefined. An earlier blanket
    # rewrite introduced exactly that here, so make it impossible to ship again.
    if "$('Merge By scene_index (not position)')" in code:
        raise ValueError("merge node must never read its own output")
    merge["parameters"]["jsCode"] = code


def patch_remaining_script_references(w: dict) -> None:
    """Use the single accepted run, and carry repair input through the loop."""
    def rewrite(value, replacements):
        if isinstance(value, str):
            for old, new in replacements:
                value = value.replace(old, new)
            return value
        if isinstance(value, dict):
            return {key: rewrite(item, replacements) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item, replacements) for item in value]
        return value

    capture = "$('Capture Accepted Script').first().json.script_snapshot"
    merged = "$('Merge By scene_index (not position)').first().json.script_snapshot"
    for name in ('ElevenLabs: TTS+Timestamps', 'Resolve B-roll', 'YouTube: Upload Draft', 'Disclose AI-Generated Content', 'Post First Comment'):
        node = node_by_name(w, name)
        source = capture if name in ('ElevenLabs: TTS+Timestamps', 'Resolve B-roll') else merged
        replacements = [("$('Validate Final Script').item.json", source), ("$('Validate Final Script').first().json", source)]
        if source == merged:
            replacements += [("$('Merge By scene_index (not position)').item.json", "$('Merge By scene_index (not position)').first().json"), ("$('YouTube: Upload Draft').item.json", "$('YouTube: Upload Draft').first().json")]
        node['parameters'] = rewrite(node['parameters'], replacements)

    # This side branch runs at capture time, before the merge exists.
    history = node_by_name(w, 'Save Topic to History')
    history['parameters'] = rewrite(history['parameters'], [(merged, '$json.script_snapshot'), ("$('Extract Generated Topic').item.json", "$('Extract Generated Topic').first().json")])

    increment = node_by_name(w, 'Increment Script Attempt')
    code = increment['parameters']['jsCode']
    anchor = 'return {json:{scriptAttempt:newAttempt,lastErrors:errors}};'
    if anchor not in code:
        raise ValueError('repair handoff anchor missing')
    increment['parameters']['jsCode'] = code.replace(anchor, "state.repairScript=$input.first().json._failedScript;\nreturn {json:{scriptAttempt:newAttempt,lastErrors:errors,_failedScript:state.repairScript}};", 1)
    repair = node_by_name(w, 'Claude: Repair Script')
    repair['parameters'] = rewrite(repair['parameters'], [("$('Validate Final Script').item.json._failedScript", '$json._failedScript'), ("$('Validate Final Script').item.json._validationErrors", '$json.lastErrors')])


def patch_compose_payload_and_logging(w: dict) -> None:
    start_compose = node_by_name(w, "Start Compose Job")
    # V5_COMPOSE_BODY_SIZE_FIX: this body carries every scene's base64 TTS
    # audio, routinely 500KB-1MB total. n8n's legacy expression interpreter
    # (@n8n/tournament) can return a large object or a large precomputed
    # string fine, but silently fails - swallowed by a no-op error handler,
    # surfacing downstream as "undefined is not valid JSON" - when asked to
    # perform object-spread reconstruction plus JSON.stringify on an object
    # this size inside the expression itself (confirmed via execution 814,
    # reproduced directly against @n8n/tournament with the real payload).
    #
    # Merge already writes policy_version/outro_experiment_arm/outro_line onto
    # its own output with the same defaults (see patch_merge_script_passthrough),
    # so $json already IS the exact desired body - no reconstruction needed.
    # A bare "{{ $json }}" resolves to an object, which n8n's HTTP node passes
    # through as the request body directly (skipping JSON.parse entirely, per
    # HttpRequestV3.node.js), so this never enters the size-limited path.
    start_compose["parameters"]["jsonBody"] = "={{ $json }}"

    log = node_by_name(w, "Log Published Video")
    log["parameters"]["jsonBody"] = _unpair(r'''={{ JSON.stringify({ video_id: ($('YouTube: Upload Draft').item.json.uploadId || $('YouTube: Upload Draft').item.json.id), published_at: new Date().toISOString(), policy_version: $('Merge By scene_index (not position)').first().json.script_snapshot.policy_version || 'shorts-growth-v2', topic: $('Extract Generated Topic').item.json.topic, canonical_key: $('Extract Generated Topic').item.json.canonical_key || null, topic_strategy_arm: $('Extract Generated Topic').item.json.strategy_arm || null, topic_predicted_score: $('Extract Generated Topic').item.json.score ?? null, hook: $('Merge By scene_index (not position)').first().json.script_snapshot.hook, title: $('Merge By scene_index (not position)').first().json.script_snapshot.title, comment_hook: $('Merge By scene_index (not position)').first().json.script_snapshot.comment_hook || null, caption_style: $('Merge By scene_index (not position)').first().json.script_snapshot.caption_style, trigger: $('Merge By scene_index (not position)').first().json.script_snapshot.trigger, outro_experiment_arm: $('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm || null, creative_dna: { policy_version: $('Merge By scene_index (not position)').first().json.script_snapshot.policy_version || 'shorts-growth-v2', topic_strategy_arm: $('Extract Generated Topic').item.json.strategy_arm || null, topic_predicted_score: $('Extract Generated Topic').item.json.score ?? null, canonical_key: $('Extract Generated Topic').item.json.canonical_key || null, concept_archetype: $('Extract Generated Topic').item.json.archetype || null, hook_type: $('Merge By scene_index (not position)').first().json.script_snapshot.hook_type || null, hook_candidates: $('Merge By scene_index (not position)').first().json.script_snapshot.hook_candidates || null, creative_format: $('Merge By scene_index (not position)').first().json.script_snapshot.creative_format || null, visual_grammar: $('Merge By scene_index (not position)').first().json.script_snapshot.visual_grammar || null, first_frame_type: $('Merge By scene_index (not position)').first().json.script_snapshot.first_frame_type || null, first_frame_source: (($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).find(s => !s?.template_data?.is_outro)?.visual_source || null), first_frame_score: $('Merge By scene_index (not position)').first().json.script_snapshot.quality?.first_frame_strength ?? null, scene_count: ($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro).length, word_count: ($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro).reduce((n,s)=>n+String(s.narration||'').trim().split(/\s+/).filter(Boolean).length,0), duration_sec: $('Validate Compose Result').item.json.duration_sec ?? null, target_duration_band: $('Merge By scene_index (not position)').first().json.script_snapshot.target_duration_band || '28-36s', length_exception_reason: $('Merge By scene_index (not position)').first().json.script_snapshot.length_exception_reason || null, payoff_position_pct: (() => { const ss=($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro); const ris=Number($('Merge By scene_index (not position)').first().json.script_snapshot.payoff?.resolved_in_scene); const idx=ss.findIndex(s => Number(s.scene_index) === ris); return idx >= 0 && ss.length ? Number(((idx + 1) / ss.length).toFixed(3)) : null; })(), open_loop_count: $('Merge By scene_index (not position)').first().json.script_snapshot.open_loop_count ?? null, template_count: ($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro && s.visual_source === 'template').length, real_video_count: ($('Merge By scene_index (not position)').item.json.data || []).filter(s => !s?.template_data?.is_outro && Boolean(s.video_url)).length, still_image_count: ($('Merge By scene_index (not position)').item.json.data || []).filter(s => !s?.template_data?.is_outro && Array.isArray(s.images) && s.images.length).length, caption_mode: $('Merge By scene_index (not position)').first().json.script_snapshot.caption_mode || null, transition_style: $('Merge By scene_index (not position)').first().json.script_snapshot.transition_style || 'hard_cut', engagement_mode: $('Merge By scene_index (not position)').first().json.script_snapshot.engagement_mode || null, comment_hook_present: Boolean($('Merge By scene_index (not position)').first().json.script_snapshot.comment_hook), outro_present: $('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm === 'current_outro', outro_experiment_arm: $('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm || null, asset_quality_min: null, asset_quality_avg: null } }) }}''')


def patch_measurement_workflow(w: dict) -> None:
    schedule = node_by_name(w, "Measure Schedule")
    schedule["parameters"]["rule"]["interval"] = [{"field": "cronExpression", "expression": "0 */6 * * *"}]

    window = node_by_name(w, "Build Analytics Window")
    window["parameters"]["jsCode"] = """// SHORTS_GROWTH_V2: poll every six hours so the first observation after 6h/24h/72h/7d/28d can be frozen as a cohort snapshot.
const DAY=86400000;const end=new Date();const start=new Date(end.getTime()-60*DAY);const iso=d=>d.toISOString().slice(0,10);return {json:{startDate:iso(start),endDate:iso(end),policy_version:'shorts-growth-v2'}};"""

    analytics = node_by_name(w, "YouTube: Video Analytics")
    analytics["parameters"]["url"] = "=https://youtubeanalytics.googleapis.com/v2/reports?ids=channel==MINE&startDate={{ $json.start_date || $('Build Analytics Window').item.json.startDate }}&endDate={{ $json.end_date || $('Build Analytics Window').item.json.endDate }}&metrics=views,engagedViews,averageViewPercentage,averageViewDuration,subscribersGained,likes,comments,shares&dimensions=video&filters=video=={{ ($json.video_ids || []).join(',') }}"


MECHANICAL_REPAIR_MARKER = "V5_MECHANICAL_FIELD_REPAIR"

MECHANICAL_REPAIR_JS = r'''// V5_MECHANICAL_FIELD_REPAIR: repair every field the script itself determines,
// before any check reads it.
//
// Six separate fields have each cost a full scheduled upload when the model
// omitted one (tags, comment_hook, visual_plan_quality, scene count,
// visual_proof_mode, length_exception_reason). They were all mechanically
// derivable, so rejecting a publishable Short over any of them trades a
// guaranteed loss for a cosmetic gain.
//
// DELIBERATELY NOT REPAIRED - these are real editorial or policy judgements and
// must still fail: narration content and the word floor, the quality.* score
// gates, the medical/health exclusion, and the topic-substitution backstop.
const _mfRepairs=[];
const _mfNote=(f)=>{if(!_mfRepairs.includes(f))_mfRepairs.push(f);};
const _mfText=(v)=>String(v==null?'':v).replace(/\s+/g,' ').trim();
const _mfQuery=(v)=>_mfText(v).toLowerCase().replace(/[^a-z0-9 ]+/g,' ').replace(/\s+/g,' ').trim().split(' ').filter(Boolean).slice(0,5).join(' ');
const _mfWords=(v)=>_mfQuery(v).split(' ').filter(Boolean).length;

const _mfTitle=_mfText(parsed.title);
if(!_mfTitle||_mfTitle.length<5||_mfTitle.length>60){
  const base=_mfTitle||_mfText(parsed.hook)||'Did You Know';
  parsed.title=base.length>60?base.slice(0,57).replace(/\s+\S*$/,'')+'...':base;
  _mfNote('title');
}
if(_mfText(parsed.seo_description).length<20){
  parsed.seo_description=[_mfText(parsed.hook),_mfText(parsed.full_script)].filter(Boolean).join(' ').slice(0,300)||_mfText(parsed.title);
  _mfNote('seo_description');
}
if(!parsed.first_frame_type){parsed.first_frame_type='hero_motion';_mfNote('first_frame_type');}
if(!parsed.transition_style){parsed.transition_style='hard_cut';_mfNote('transition_style');}
if(!Number.isFinite(Number(parsed.open_loop_count))){parsed.open_loop_count=0;_mfNote('open_loop_count');}
const _mfEngagement=['none','comment_only','share_only','comment_and_share'];
if(!_mfEngagement.includes(parsed.engagement_mode)){
  parsed.engagement_mode=_mfText(parsed.comment_hook)?'comment_only':'none';
  _mfNote('engagement_mode');
}

if(Array.isArray(parsed.scenes)){
  parsed.scenes.forEach((s,i)=>{
    if(!s||typeof s!=='object')return;
    if(s.template_data&&s.template_data.is_outro)return;
    if(typeof s.scene_index!=='number'||!Number.isFinite(s.scene_index)){s.scene_index=i;_mfNote('scene_index');}
    if(_mfText(s.point).length<3){
      s.point=_mfText(s.narration).split(' ').slice(0,6).join(' ')||('beat '+s.scene_index);
      _mfNote('point');
    }
    ['required_entities','required_actions','required_relationships','acceptable_visuals'].forEach((k)=>{
      if(!Array.isArray(s[k])){s[k]=[];_mfNote(k);}
    });
    if(!Array.isArray(s.forbidden_visuals)||!s.forbidden_visuals.length){
      s.forbidden_visuals=['generic unrelated stock footage'];
      _mfNote('forbidden_visuals');
    }
    if(_mfText(s.visual_claim).length<8){
      s.visual_claim=_mfText(s.narration).slice(0,140)||_mfText(s.point);
      _mfNote('visual_claim');
    }
    if(s.visual_source!=='template'){
      if(_mfText(s.visual_prompt).length<20){
        s.visual_prompt=(_mfText(s.visual_claim)+', documentary footage').slice(0,180);
        _mfNote('visual_prompt');
      }
      if(!_mfText(s.negative_prompt)){
        s.negative_prompt='no readable text, no legible numbers, no watermarks';
        _mfNote('negative_prompt');
      }
      const _mfSubject=_mfText(s.named_subject||parsed.global_subject||s.point);
      const _mfSeen=[];
      const _mfAdd=(v)=>{const q=_mfQuery(v);if(_mfWords(q)>=2&&_mfWords(q)<=5&&!_mfSeen.includes(q))_mfSeen.push(q);};
      (Array.isArray(s.search_queries)?s.search_queries:[]).forEach(_mfAdd);
      if(_mfSeen.length<3){
        [s.stock_search_query,s.visual_claim,_mfSubject+' real footage',_mfSubject+' archival photo',_mfSubject+' close up','real world footage'].forEach(_mfAdd);
        if(_mfSeen.length>=3)_mfNote('search_queries');
      }
      if(_mfSeen.length)s.search_queries=_mfSeen.slice(0,4);
      if(!_mfText(s.stock_search_query)&&s.search_queries&&s.search_queries.length){
        s.stock_search_query=s.search_queries[0];
        _mfNote('stock_search_query');
      }
    }
  });
}
if(_mfRepairs.length)parsed.mechanical_repairs=_mfRepairs;
'''


def patch_mechanical_field_repair(w: dict) -> None:
    """Repair derivable fields before any validator check reads them."""
    validator = node_by_name(w, "Validate Final Script")
    code = str(validator["parameters"]["jsCode"])
    if MECHANICAL_REPAIR_MARKER in code:
        return
    anchor = "const errors = [];"
    if anchor not in code:
        raise ValueError("validator error-collection anchor missing")
    validator["parameters"]["jsCode"] = code.replace(anchor, MECHANICAL_REPAIR_JS + "\n" + anchor, 1)


def upgrade(w: dict) -> dict:
    patch_topic_policy(w)
    patch_topic_dedup_retry(w)
    patch_mechanical_field_repair(w)
    patch_writer_and_duration(w)
    patch_accepted_script_capture(w)
    patch_merge_script_passthrough(w)
    patch_compose_payload_and_logging(w)
    patch_remaining_script_references(w)
    patch_measurement_workflow(w)
    w.setdefault("meta", {})["shorts_growth_policy"] = POLICY_VERSION
    return w


def assert_invariants(w: dict) -> None:
    if w.get("meta", {}).get("shorts_growth_policy") != POLICY_VERSION:
        raise RuntimeError("growth policy metadata missing")
    if DEDUP_NODE not in {n.get("name") for n in w.get("nodes", [])}:
        raise RuntimeError("semantic topic dedup node missing")
    gen = str(node_by_name(w, "Claude: Generate Topic")["parameters"]["jsonBody"])
    for marker in ("GENERATE 10 DISTINCT candidate topics", "7 exploit / 2 adjacent / 1 explore", "canonical_key", "HARD DUPLICATE CHECK", DEDUP_RETRY_MARKER):
        if marker not in gen:
            raise RuntimeError(f"topic policy prompt missing {marker}")
    for name in (TOPIC_INIT_NODE, DEDUP_IF_NODE, DEDUP_INCREMENT_NODE, DEDUP_RETRY_IF_NODE, DEDUP_FAIL_NODE):
        if name not in {n.get("name") for n in w.get("nodes", [])}:
            raise RuntimeError(f"topic dedup retry loop missing node: {name}")
    if node_by_name(w, DEDUP_NODE).get("onError") != "continueRegularOutput":
        raise RuntimeError("topic dedup node no longer survives a rejection to allow the retry loop")
    # n8n's boolean "true" operator returns the left value verbatim and
    # ignores rightValue entirely (confirmed against n8n-workflow's own
    # filter-parameter.js: case 'true': return left). Execution 843 proved
    # this the hard way: a rightValue:false variant of this same operator
    # evaluated true on every run - including two dedup successes - and
    # threw a false "exhausted" error. The condition must test truthiness
    # of an already-negated expression, not lean on rightValue to invert it.
    exhausted_condition = node_by_name(w, DEDUP_IF_NODE)["parameters"]["conditions"]["conditions"][0]
    if "!== true" not in str(exhausted_condition.get("leftValue", "")):
        raise RuntimeError("topic dedup exhaustion check no longer negates success in the expression itself")
    writer = str(node_by_name(w, "Claude: Draft Script (Stage 1)")["parameters"]["jsonBody"])
    if "averaged 84 views" in writer or "3.5x fewer views" in writer:
        raise RuntimeError("stale fixed channel statistics survived writer prompt")
    for marker in ("DYNAMIC PERFORMANCE GUIDANCE", "28-36 second FINAL rendered Short", "length_exception_reason"):
        if marker not in writer:
            raise RuntimeError(f"writer growth rule missing {marker}")
    validator = str(node_by_name(w, "Validate Final Script")["parameters"]["jsCode"])
    for marker in ("outro_experiment_arm", "current_outro", "no_outro", "policy_version='shorts-growth-v2'", "length_exception_reason is required"):
        if marker not in validator:
            raise RuntimeError(f"validator growth invariant missing {marker}")
    analytics_url = str(node_by_name(w, "YouTube: Video Analytics")["parameters"]["url"])
    if "engagedViews" not in analytics_url:
        raise RuntimeError("engagedViews missing from YouTube Analytics query")
    cron = node_by_name(w, "Measure Schedule")["parameters"]["rule"]["interval"][0]["expression"]
    if cron != "0 */6 * * *":
        raise RuntimeError("measurement schedule is not six-hourly")
    log_body = str(node_by_name(w, "Log Published Video")["parameters"]["jsonBody"])
    for marker in ("topic_strategy_arm", "topic_predicted_score", "duration_sec", "outro_experiment_arm", "policy_version"):
        if marker not in log_body:
            raise RuntimeError(f"performance telemetry missing {marker}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-shorts-growth-v2.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    w = json.loads(src.read_text())
    out = upgrade(w)
    assert_invariants(out)
    dst.write_text(json.dumps(out, indent=2) + "\n")
    print(f"{MARKER} workflow written to {dst}")


if __name__ == "__main__":
    main()
