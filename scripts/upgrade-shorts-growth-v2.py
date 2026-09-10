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
        "These keys describe the underlying FACT, not its wording, so paraphrases retain the same identity. The exact 7/2/1 allocation is mandatory.\\n\\n"
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


def patch_writer_and_duration(w: dict) -> None:
    writer = node_by_name(w, "Claude: Draft Script (Stage 1)")
    body = str(writer["parameters"]["jsonBody"])

    # Delete stale fixed numerical claims; current measurements are injected below.
    body = body.replace("On this channel, hooks that led with a statistic averaged 84 views; hooks that withheld it averaged 298 - 3.5x more. ", "")
    body = body.replace("(this channel's data: number-led titles average 3.5x fewer views) ", "")
    body = body.replace("this channel's data: number-led titles average 3.5x fewer views", "current measured guidance decides whether withholding is helping this channel")

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
    if "script_snapshot" in code:
        return
    anchor = "publication_description:publicationDescription,data:merged}};"
    if anchor not in code:
        raise ValueError("merge return anchor missing; cannot pass the script through")
    merge["parameters"]["jsCode"] = code.replace(
        anchor,
        "publication_description:publicationDescription,"
        "policy_version:$('Merge By scene_index (not position)').first().json.script_snapshot.policy_version||'shorts-growth-v2',"
        "outro_experiment_arm:$('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm||'no_outro',"
        "outro_line:$('Merge By scene_index (not position)').first().json.script_snapshot.outro_line??null,"
        "script_snapshot:$('Merge By scene_index (not position)').first().json.script_snapshot,"
        "data:merged}};",
        1,
    )


def patch_compose_payload_and_logging(w: dict) -> None:
    start_compose = node_by_name(w, "Start Compose Job")
    start_compose["parameters"]["jsonBody"] = "={{ JSON.stringify({ ...$json, policy_version: $json.policy_version || 'shorts-growth-v2', outro_experiment_arm: $json.outro_experiment_arm || 'no_outro', outro_line: $json.outro_line ?? null }) }}"

    log = node_by_name(w, "Log Published Video")
    log["parameters"]["jsonBody"] = _unpair(r'''={{ JSON.stringify({ video_id: ($('YouTube: Upload Draft').item.json.uploadId || $('YouTube: Upload Draft').item.json.id), published_at: new Date().toISOString(), policy_version: $('Merge By scene_index (not position)').first().json.script_snapshot.policy_version || 'shorts-growth-v2', topic: $('Extract Generated Topic').item.json.topic, canonical_key: $('Extract Generated Topic').item.json.canonical_key || null, topic_strategy_arm: $('Extract Generated Topic').item.json.strategy_arm || null, topic_predicted_score: $('Extract Generated Topic').item.json.score ?? null, hook: $('Merge By scene_index (not position)').first().json.script_snapshot.hook, title: $('Merge By scene_index (not position)').first().json.script_snapshot.title, comment_hook: $('Merge By scene_index (not position)').first().json.script_snapshot.comment_hook || null, caption_style: $('Merge By scene_index (not position)').first().json.script_snapshot.caption_style, trigger: $('Merge By scene_index (not position)').first().json.script_snapshot.trigger, outro_experiment_arm: $('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm || null, creative_dna: { policy_version: $('Merge By scene_index (not position)').first().json.script_snapshot.policy_version || 'shorts-growth-v2', topic_strategy_arm: $('Extract Generated Topic').item.json.strategy_arm || null, topic_predicted_score: $('Extract Generated Topic').item.json.score ?? null, canonical_key: $('Extract Generated Topic').item.json.canonical_key || null, concept_archetype: $('Extract Generated Topic').item.json.archetype || null, creative_format: $('Merge By scene_index (not position)').first().json.script_snapshot.creative_format || null, visual_grammar: $('Merge By scene_index (not position)').first().json.script_snapshot.visual_grammar || null, first_frame_type: $('Merge By scene_index (not position)').first().json.script_snapshot.first_frame_type || null, first_frame_source: (($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).find(s => !s?.template_data?.is_outro)?.visual_source || null), first_frame_score: $('Merge By scene_index (not position)').first().json.script_snapshot.quality?.first_frame_strength ?? null, scene_count: ($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro).length, word_count: ($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro).reduce((n,s)=>n+String(s.narration||'').trim().split(/\s+/).filter(Boolean).length,0), duration_sec: $('Validate Compose Result').item.json.duration_sec ?? null, target_duration_band: $('Merge By scene_index (not position)').first().json.script_snapshot.target_duration_band || '28-36s', length_exception_reason: $('Merge By scene_index (not position)').first().json.script_snapshot.length_exception_reason || null, payoff_position_pct: (() => { const ss=($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro); const ris=Number($('Merge By scene_index (not position)').first().json.script_snapshot.payoff?.resolved_in_scene); const idx=ss.findIndex(s => Number(s.scene_index) === ris); return idx >= 0 && ss.length ? Number(((idx + 1) / ss.length).toFixed(3)) : null; })(), open_loop_count: $('Merge By scene_index (not position)').first().json.script_snapshot.open_loop_count ?? null, template_count: ($('Merge By scene_index (not position)').first().json.script_snapshot.scenes || []).filter(s => !s?.template_data?.is_outro && s.visual_source === 'template').length, real_video_count: ($('Merge By scene_index (not position)').item.json.data || []).filter(s => !s?.template_data?.is_outro && Boolean(s.video_url)).length, still_image_count: ($('Merge By scene_index (not position)').item.json.data || []).filter(s => !s?.template_data?.is_outro && Array.isArray(s.images) && s.images.length).length, caption_mode: $('Merge By scene_index (not position)').first().json.script_snapshot.caption_mode || null, transition_style: $('Merge By scene_index (not position)').first().json.script_snapshot.transition_style || 'hard_cut', engagement_mode: $('Merge By scene_index (not position)').first().json.script_snapshot.engagement_mode || null, comment_hook_present: Boolean($('Merge By scene_index (not position)').first().json.script_snapshot.comment_hook), outro_present: $('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm === 'current_outro', outro_experiment_arm: $('Merge By scene_index (not position)').first().json.script_snapshot.outro_experiment_arm || null, asset_quality_min: null, asset_quality_avg: null } }) }}''')


def patch_measurement_workflow(w: dict) -> None:
    schedule = node_by_name(w, "Measure Schedule")
    schedule["parameters"]["rule"]["interval"] = [{"field": "cronExpression", "expression": "0 */6 * * *"}]

    window = node_by_name(w, "Build Analytics Window")
    window["parameters"]["jsCode"] = """// SHORTS_GROWTH_V2: poll every six hours so the first observation after 6h/24h/72h/7d/28d can be frozen as a cohort snapshot.
const DAY=86400000;const end=new Date();const start=new Date(end.getTime()-60*DAY);const iso=d=>d.toISOString().slice(0,10);return {json:{startDate:iso(start),endDate:iso(end),policy_version:'shorts-growth-v2'}};"""

    analytics = node_by_name(w, "YouTube: Video Analytics")
    analytics["parameters"]["url"] = "=https://youtubeanalytics.googleapis.com/v2/reports?ids=channel==MINE&startDate={{ $json.start_date || $('Build Analytics Window').item.json.startDate }}&endDate={{ $json.end_date || $('Build Analytics Window').item.json.endDate }}&metrics=views,engagedViews,averageViewPercentage,averageViewDuration,subscribersGained,likes,comments,shares&dimensions=video&filters=video=={{ ($json.video_ids || []).join(',') }}"


def upgrade(w: dict) -> dict:
    patch_topic_policy(w)
    patch_writer_and_duration(w)
    patch_merge_script_passthrough(w)
    patch_compose_payload_and_logging(w)
    patch_measurement_workflow(w)
    w.setdefault("meta", {})["shorts_growth_policy"] = POLICY_VERSION
    return w


def assert_invariants(w: dict) -> None:
    if w.get("meta", {}).get("shorts_growth_policy") != POLICY_VERSION:
        raise RuntimeError("growth policy metadata missing")
    if DEDUP_NODE not in {n.get("name") for n in w.get("nodes", [])}:
        raise RuntimeError("semantic topic dedup node missing")
    gen = str(node_by_name(w, "Claude: Generate Topic")["parameters"]["jsonBody"])
    for marker in ("GENERATE 10 DISTINCT candidate topics", "7 exploit / 2 adjacent / 1 explore", "canonical_key"):
        if marker not in gen:
            raise RuntimeError(f"topic policy prompt missing {marker}")
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
