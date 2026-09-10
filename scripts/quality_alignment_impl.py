#!/usr/bin/env python3
"""Align Shorts prompts/runtime settings to reliable, retrievable video production."""
from __future__ import annotations

import re

MARKER = "QUALITY_ALIGNMENT"
RELIABILITY_MARKER = "RELIABILITY_FIRST_VIDEO"
VISUAL_ROUTER_MARKER = "VISUAL_SOURCE_ROUTER_V2"
VISUAL_TELEMETRY_MARKER = "VISUAL_RETRIEVAL_TELEMETRY_V2"

PUBLISH_MINIMUMS = {
    "concept_strength": 68,
    "hook_strength": 70,
    "evidence_strength": 70,
    "payoff_strength": 68,
    "information_density": 66,
    "first_frame_strength": 70,
    "visual_progression": 66,
    "shareability": 71,
    "naturalness": 66,
    "distinctiveness": 64,
    "voice_specificity": 62,
    "overall": 69,
}
VISUAL_PLAN_MINIMUM = 70


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise KeyError(f"required n8n node not found: {name}")


def _prepend_user_instruction(body: str, instruction: str) -> str:
    marker = 'content: "'
    if instruction in body:
        return body
    if marker not in body:
        raise ValueError("Claude JSON body has no user content anchor")
    return body.replace(marker, marker + instruction.replace('"', '\\"') + "\\n\\n", 1)


def patch_commissioning(workflow: dict) -> None:
    node = node_by_name(workflow, "Claude: Commission Topic Shortlist")
    node["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ model: "gpt-5.6-luna", max_completion_tokens: 8192, reasoning_effort: "none", response_format: { type: "json_object" }, messages: [{ role: "user", content: "You are commissioning ONE production-worthy YouTube Short from a shortlist. QUALITY_ALIGNMENT RELIABILITY_FIRST_VIDEO. Choose a truthful, visually retrievable idea that can actually be produced today. Do not spend output tokens explaining your reasoning.\n\nSHORTLIST: " + JSON.stringify($json.shortlist) + "\n\nEvaluate every input candidate internally for concept, evidence, first-frame strength, payoff, shareability, distinctiveness, novelty, production feasibility, and naturalness. Evidence and stock feasibility matter more than clever wording.\n\nFor the TWO strongest candidates only, return: topic, archetype, research_query, first_frame_concept, share_reason, evidence_score, visual_score, share_score, concept_score, payoff_score, novelty_score, execution_score, distinctiveness_score, share_trigger, send_to_person, novelty_delta, proof_visual, stock_feasibility, stock_query_seed, reason, score. share_trigger must be one of disbelief, identity, argument, useful_warning, awe, humor, status. stock_query_seed must contain 3 short high-inventory visible-noun/action queries. Keep reason under 20 words.\n\nReturn ONLY compact JSON in exactly this shape: {\"candidates\":[...]} with at most 2 candidates, sorted best-first. No prose, no markdown, no thinking text." }] }) }}'''
    node["parameters"].setdefault("options", {})["timeout"] = 120000


def patch_writer(workflow: dict) -> None:
    node = node_by_name(workflow, "Claude: Draft Script (Stage 1)")
    instruction = (
        f"{MARKER} WRITER - {RELIABILITY_MARKER}: Finish a COMPLETE truthful JSON script before optimizing flourishes. "
        "Keep claims inside supplied evidence boundaries. Prefer concrete retrievable visuals and a clear repeatable payoff. "
        "A competent natural Short is publishable; do not make the structure fragile while chasing premium polish."
    )
    node["parameters"]["jsonBody"] = _prepend_user_instruction(node["parameters"]["jsonBody"], instruction)


def patch_visual_director(workflow: dict) -> None:
    node = node_by_name(workflow, "Claude: Visual Director")
    m = PUBLISH_MINIMUMS
    body = r'''={{ JSON.stringify({ model: "gpt-5.6-luna", max_completion_tokens: 8192, reasoning_effort: "none", response_format: { type: "json_object" }, messages: [{ role: "user", content: "You are the INDEPENDENT QUALITY CRITIC and VISUAL DIRECTOR for a YouTube Shorts channel. QUALITY_ALIGNMENT RELIABILITY_FIRST_VIDEO. Return a COMPLETE production-ready JSON script. Reliability and truthfulness outrank perfection. The topic/subject/fact in the script below is fixed and non-negotiable - grade it, polish its wording, plan its visuals, but never substitute a different topic or fact, no matter how strong an alternative seems. A weak topic fails PHASE 1 below; it does not get silently swapped out.\n\nSCRIPT: " + JSON.stringify($json.draft) + "\n\nCOMMISSIONING INTENT: " + JSON.stringify({ share_trigger: $('Extract Generated Topic').item.json.share_trigger || '', send_to_person: $('Extract Generated Topic').item.json.send_to_person || '', novelty_delta: $('Extract Generated Topic').item.json.novelty_delta || '', proof_visual: $('Extract Generated Topic').item.json.proof_visual || '', stock_feasibility: $('Extract Generated Topic').item.json.stock_feasibility || 0, stock_query_seed: $('Extract Generated Topic').item.json.stock_query_seed || [] }) + "\n\nPHASE 1 - QUALITY CHECK\nJudge the actual script against these publish floors: concept_strength __CONCEPT__; hook_strength __HOOK__; evidence_strength __EVIDENCE__; payoff_strength __PAYOFF__; information_density __INFO__; first_frame_strength __FIRST__; visual_progression __VISUAL__; shareability __SHARE__; naturalness __NATURAL__; distinctiveness __DISTINCT__; voice_specificity __VOICE__; overall __OVERALL__. Overall may not exceed the weakest of concept/evidence/first-frame/payoff/shareability by more than 8.\n\nClassify internally as PASS, REPAIRABLE_NEAR_MISS, or HARD_REJECT. HARD_REJECT only if evidence is below its gate, the factual premise itself must change, or a core dimension is more than 8 points below its floor. REPAIRABLE_NEAR_MISS applies when evidence is sound and packaging/writing can be fixed in one pass. Perform at most ONE bounded repair. Preserve factual boundaries, scene order/count, and every scene_index. Rebuild full_script after any wording repair. If the script is competent and truthful, prefer PASS over discarding it for premium-level polish. Set quality_route to pass, repaired_near_miss, or hard_reject and score the FINAL returned script. REQUIRED OUTPUT FIELD: put these twelve scores in a nested JSON object named quality with exactly these numeric keys: concept_strength, hook_strength, evidence_strength, payoff_strength, information_density, first_frame_strength, visual_progression, shareability, naturalness, distinctiveness, voice_specificity, overall. The quality object must always be present in your output - never omit it, never return the scores as separate top-level fields instead.\n\nPHASE 2 - RETRIEVABLE VISUALS\nREQUIRED OUTPUT FIELD: choose creative_format - it must be exactly one of documentary_cinematic, comparison_reveal, minimal_proof, archival_history, macro_detail, kinetic_data, never omitted. Set visual_grammar to that same family. Set first_frame_type to hero_motion, macro_anomaly, face_reaction, scale_comparison, result_first, archive_proof, or kinetic_stat. Assign visual_role=hero/evidence/detail/comparison/breath/payoff.\n\nVISUAL_SOURCE_ROUTER_V2 - EXECUTABLE VISUAL CONTRACT. The renderer has NO AI image/video generator. Never set visual_type=ai and never describe an impossible synthetic shot as if stock search can retrieve it. For every NON-OUTRO scene add visual_mode from exact_real, context_real, archive_scientific, template_explainer; must_show as one short literal visible subject/action/relationship; acceptable_substitutes as 0-3 short visible alternatives; source_priority as an ordered subset of wikimedia, wikipedia, pexels_video, pexels, unsplash. Use exact_real when the literal subject/action must appear. Use archive_scientific for anatomy, historical/scientific evidence, maps, specimens, spacecraft, oceanography, geology, or named artifacts. Use context_real only when contextual footage genuinely supports the narration. Use template_explainer only when the beat is genuinely unshowable any other way: an X-ray/cutaway, a global route, an impossible camera position, or a numeric/scale relationship with no plausible real visual proxy at all. REQUIRED HARD CAP: at most ONE scene in the entire script (excluding the outro) may use template_explainer/visual_source=template. Before reaching for a template, default to a real visual proxy instead - for a scale/numeric comparison, that is real footage/photos of the actual subjects (shown in sequence, side by side, or with the numbers overlaid on real footage via template_fallback), not a text/graphic card; for a map, route, or before/after, prefer archive_scientific or real footage of the actual place/subject over a designed diagram. If more than one beat seems to need a template, keep only the single most essential one and find the closest real visual proxy for every other beat - a video that is mostly real footage with one well-chosen template beats one that leans on templates repeatedly. REQUIRED: scene_index 0 (the first frame) must NEVER use template_explainer or visual_source=template, regardless of what the beat needs - it must always be a real photo or video, because it has to stop a scroll before the viewer has even processed audio, and a designed graphic reads as a slower, less arresting open. If scene 0's beat seems to need a template, choose exact_real/context_real/archive_scientific instead and find the closest real visual proxy. For template_explainer set visual_source=template and template_name ONLY stat_reveal, comparison, or kinetic_text with complete template_data. For every real/archive scene also provide template_fallback={template_name,template_data}; use stat_reveal for one number, comparison for two values, otherwise kinetic_text. A relevant simple template is better than beautiful misleading stock.\n\nFor every NON-TEMPLATE scene create at least 3 concrete Pexels/Unsplash/Wikipedia/Wikimedia search_queries: (1) literal query: visible subject/action, (2) broad fallback: high-inventory core visible noun, (3) a distinct visual variant. Scene 0 should have 4 when useful. Set stock_search_query to the strongest query. Queries must be short visible nouns/actions, not cinematic adjectives or abstract concepts.\n\nSet transition_style=hard_cut, engagement_mode from the actual CTA fields, open_loop_count honestly, and visual_plan_quality 0-100. A usable coherent plan should clear __VISUAL_PLAN__; reserve lower scores for genuinely unrenderable plans. REQUIRED OUTPUT FIELD: caption_mode must be exactly one of karaoke, key_phrases, minimal - never omitted.\n\nFINAL INTEGRITY PASS: return ONLY the COMPLETE script JSON. Every scene needs sequential scene_index and non-empty point. Every non-template scene needs visual_role, stock_search_query, >=3 search_queries, visual_mode, must_show, source_priority, and template_fallback. first_frame_type is required. Preserve/rebuild quality, full_script, title, tags, seo_description and all other required fields. REQUIRED OUTPUT FIELD: payoff must be a nested object with exactly {claim: string, resolved_in_scene: number}, where resolved_in_scene is the scene_index (from the SCRIPT you were given) of the scene that actually delivers the hook's promise - copy it through unchanged unless you renumbered scenes, in which case update it to match. Never omit payoff or return it without resolved_in_scene. REQUIRED OUTPUT FIELD: caption_style must be copied through unchanged from the input SCRIPT - it is exactly one of upbeat, serious, funny, neutral, never omitted. REQUIRED OUTPUT FIELD: trigger must be copied through unchanged from the input SCRIPT - it is exactly one of curiosity_gap, disbelief, fear_stakes, scale_shock, taboo_secret, never omitted. If the input SCRIPT has hook_candidates, copy that exact array through unchanged (3-6 strings); if it has none, leave hook_candidates out of your output entirely - never invent one or output it as anything other than an array of strings. Every scene must keep its exact original scene_index and point from the input SCRIPT unchanged - these identify the scene and are never renumbered or dropped. No prose, markdown, or thinking text." }] }) }}'''
    replacements = {
        "__CONCEPT__": m["concept_strength"], "__HOOK__": m["hook_strength"], "__EVIDENCE__": m["evidence_strength"],
        "__PAYOFF__": m["payoff_strength"], "__INFO__": m["information_density"], "__FIRST__": m["first_frame_strength"],
        "__VISUAL__": m["visual_progression"], "__SHARE__": m["shareability"], "__NATURAL__": m["naturalness"],
        "__DISTINCT__": m["distinctiveness"], "__VOICE__": m["voice_specificity"], "__OVERALL__": m["overall"],
        "__VISUAL_PLAN__": VISUAL_PLAN_MINIMUM,
    }
    for token, value in replacements.items():
        body = body.replace(token, str(value))
    node["parameters"]["jsonBody"] = body
    node["parameters"].setdefault("options", {})["timeout"] = 120000

    # REPAIR_LOOP_V1: reuse the identical critic/visual-director template for
    # the repair pass, but source the script from the failed validation output
    # instead of a fresh draft, and tell it exactly which deterministic
    # checks it needs to fix.
    repair_node = node_by_name(workflow, "Claude: Repair Script")
    script_source_old = "$json.draft"
    script_source_new = r"$('Validate Final Script').item.json._failedScript"
    if script_source_old not in body:
        raise ValueError("repair pass: visual-director script-source anchor missing")
    repair_body = body.replace(script_source_old, script_source_new, 1)
    phase1_anchor = "PHASE 1 - QUALITY CHECK"
    if phase1_anchor not in repair_body:
        raise ValueError("repair pass: PHASE 1 anchor missing")
    repair_preamble = (
        "REPAIR PASS - this script already failed the deterministic quality "
        "gate on the exact checks below. Fix precisely these issues while "
        "preserving everything else that already worked, especially every "
        "scene's scene_index/point and full_script consistency. Do not "
        "start over or change the topic. For every field named in a failed "
        "check below (most commonly a scene's narration), the input SCRIPT "
        "already has that field missing, empty, or too short - preserving "
        "it unchanged would just fail the same check again. You MUST write "
        "real, complete content for that exact field; a still-missing field "
        "is a repair failure, not a preserved one. A failed check named "
        "quality.<dimension> or quality.overall is DIFFERENT - nothing is "
        "missing there, the writing or visual plan simply was not strong "
        "enough by your own PHASE 1 grading. Re-read the named dimension's "
        "definition above and materially rewrite the weak part it measures "
        "(a flatter-than-it-should-be hook, thin evidence, a repetitive "
        "visual plan, etc.) - identify the true weakest link even if only "
        "quality.overall failed, since overall failing alone means your "
        "holistic read was harsher than the passing component scores "
        "justified. Re-score honestly after the real rewrite; raising the "
        "reported number alone without a genuine content change is not a "
        "repair, it is the same script re-failing on the next gate that "
        "reads it.\\n\\nEXCEPTION - SCENE COUNT: the instruction above to preserve scene order and "
        "count is overridden when a failed check below is about the number of scenes. The script must "
        "end up with 3-8 content scenes, so merge or split beats as needed in that case, renumber "
        "scene_index sequentially from 0, and update payoff.resolved_in_scene to match. Preserving a "
        "scene count the gate has already rejected only fails the same check again."
        "\\n\\nEXCEPTION - CATEGORICAL TOPIC REJECTION: if any entry in FAILED CHECKS below "
        "states that this content type is excluded (for example a medical/health-content exclusion), "
        "the topic itself is disqualified and cannot be repaired by rewording - ignore every instruction "
        "above about preserving the topic. Instead pick a DIFFERENT topic from CANDIDATE POOL below, "
        "skipping any candidate that is itself medical/health-related or otherwise excluded, and write a "
        "brand new complete script for that different topic from scratch, following the exact same JSON "
        "schema as a fresh draft. When you do this, set a top-level resolved_topic field to the exact new "
        "topic string you used, so it can be recorded correctly - never omit resolved_topic when you "
        "change topics. Do not use this exception for any other kind of failed check.\\n\\nCANDIDATE POOL: "
        + '" + '
        "JSON.stringify($('Extract Generated Topic').item.json.candidate_pool || []) + \""
        "\\n\\nFAILED CHECKS: " + '" + '
        "JSON.stringify($('Validate Final Script').item.json._validationErrors || []) + \""
        "\\n\\n" + phase1_anchor
    )
    repair_body = repair_body.replace(phase1_anchor, repair_preamble, 1
    )
    repair_node["parameters"]["jsonBody"] = repair_body
    repair_node["parameters"].setdefault("options", {})["timeout"] = 120000


def _prioritize_complete_json(node: dict, max_tokens: int) -> None:
    body = str(node.get("parameters", {}).get("jsonBody", ""))
    body = re.sub(r"max_completion_tokens:\s*\d+", f"max_completion_tokens: {max_tokens}", body, count=1)
    body = re.sub(r'reasoning_effort:\s*"[a-z]+"', 'reasoning_effort: "none"', body, count=1)
    if "reasoning_effort:" not in body:
        body = re.sub(r"(max_completion_tokens:\s*\d+\s*,)", r'\1 reasoning_effort: "none",', body, count=1)
    node["parameters"]["jsonBody"] = body


VISUAL_CONTRACT_NORMALIZER = r"""// VISUAL_SOURCE_ROUTER_V2: make source routing executable and remove impossible AI-only scene requests.
// DRAFT_PASSTHROUGH_BACKSTOP: fetch the original Draft Script output once so
// every field Visual Director is merely supposed to pass through (payoff,
// caption_style, trigger, hook_candidates, per-scene scene_index/point) can be
// recovered if the model drops or mangles it while rebuilding the rest of the
// script. Never throws: a missing/renamed upstream node just disables recovery.
let _draftScript;
try{_draftScript=$('Parse Draft JSON').item.json.draft;}catch(e){_draftScript=undefined;}
// QUALITY_OBJECT_BACKSTOP: some models occasionally return the quality dimensions as flat
// top-level fields instead of the required nested quality object - reconstruct it
// defensively so the deterministic quality gate below can still run.
const _qualityKeys=['concept_strength','hook_strength','evidence_strength','payoff_strength','information_density','first_frame_strength','visual_progression','shareability','naturalness','distinctiveness','voice_specificity','overall'];
if((!parsed.quality||typeof parsed.quality!=='object')&&_qualityKeys.some(k=>Number.isFinite(Number(parsed[k])))){
  parsed.quality={};
  for(const k of _qualityKeys){if(parsed[k]!==undefined)parsed.quality[k]=Number(parsed[k]);}
}
// PAYOFF_OBJECT_BACKSTOP: some models drop or mangle payoff.resolved_in_scene
// when rebuilding the script - recover it from the original draft, or fall back
// to the last content scene, before the click-confirmation gate runs.
if(Array.isArray(parsed.scenes)&&parsed.scenes.length){
  const _pbValidScene=(v)=>parsed.scenes.some(s=>Number(s?.scene_index)===Number(v));
  const _pbHasValid=parsed.payoff&&typeof parsed.payoff==='object'&&parsed.payoff.claim&&_pbValidScene(parsed.payoff.resolved_in_scene);
  if(!_pbHasValid){
    const _pbDraft=_draftScript&&_draftScript.payoff;
    if(_pbDraft&&_pbDraft.claim&&_pbValidScene(_pbDraft.resolved_in_scene)){
      parsed.payoff={claim:String(_pbDraft.claim),resolved_in_scene:Number(_pbDraft.resolved_in_scene)};
    }else{
      const _pbClaim=(parsed.payoff&&parsed.payoff.claim)||(_pbDraft&&_pbDraft.claim)||'the payoff delivered in the final scene';
      const _pbContentScenes=parsed.scenes.filter(s=>!s?.template_data?.is_outro);
      const _pbLast=_pbContentScenes.length?_pbContentScenes[_pbContentScenes.length-1]:parsed.scenes[parsed.scenes.length-1];
      parsed.payoff={claim:String(_pbClaim),resolved_in_scene:Number(_pbLast.scene_index)};
    }
  }
}
// CAPTION_TRIGGER_BACKSTOP: caption_style/trigger are supposed to be simple
// pass-throughs from the draft, but the model sometimes omits or invents an
// out-of-enum value. Recover from the draft's own value first; only fall back
// to a generic default if the draft itself never had a valid one either.
const _validCaptionStyles=['upbeat','serious','funny','neutral'];
if(!_validCaptionStyles.includes(parsed.caption_style)){
  parsed.caption_style=_validCaptionStyles.includes(_draftScript&&_draftScript.caption_style)?_draftScript.caption_style:'neutral';
}
const _validTriggers=['curiosity_gap','disbelief','fear_stakes','scale_shock','taboo_secret'];
if(!_validTriggers.includes(parsed.trigger)){
  parsed.trigger=_validTriggers.includes(_draftScript&&_draftScript.trigger)?_draftScript.trigger:'disbelief';
}
// CREATIVE_FORMAT_CAPTION_MODE_BACKSTOP: unlike caption_style/trigger these are
// Visual-Director-only fields with nothing in the draft to recover from, so a
// missing/invalid value just needs a safe, renderable default instead.
const _validCreativeFormats=['documentary_cinematic','comparison_reveal','minimal_proof','archival_history','macro_detail','kinetic_data'];
if(!_validCreativeFormats.includes(parsed.creative_format)){
  parsed.creative_format='documentary_cinematic';
  parsed.visual_grammar='documentary_cinematic';
}
const _validCaptionModes=['karaoke','key_phrases','minimal'];
if(!_validCaptionModes.includes(parsed.caption_mode))parsed.caption_mode='karaoke';
// VISUAL_PLAN_QUALITY_BACKSTOP: visual_plan_quality is another
// Visual-Director-only field, but unlike the two above it gates publication.
// When the model simply omits it the run used to die after exhausting every
// repair attempt, so derive a score from what the plan demonstrably contains
// instead. An honestly self-reported low score is left alone, and a genuinely
// thin plan still fails both this floor and the per-scene field checks.
if(!Number.isFinite(Number(parsed.visual_plan_quality))){
  const _vpScenes=(Array.isArray(parsed.scenes)?parsed.scenes:[]).filter((s)=>s&&!(s.template_data&&s.template_data.is_outro));
  const _vpComplete=_vpScenes.filter((s)=>{
    if(s.visual_source==='template')return Boolean(s.template_name&&s.template_data);
    return Boolean(String(s.stock_search_query||'').trim())&&Array.isArray(s.search_queries)&&s.search_queries.length>=3&&Boolean(s.visual_mode)&&Boolean(String(s.must_show||'').trim());
  }).length;
  const _vpRoles=new Set(_vpScenes.map((s)=>String(s.visual_role||'')).filter(Boolean));
  const _vpRatio=_vpScenes.length?_vpComplete/_vpScenes.length:0;
  let _vpScore=Math.round(50+40*_vpRatio);
  if(parsed.first_frame_type)_vpScore+=3;
  if(_vpRoles.size>=Math.min(3,_vpScenes.length))_vpScore+=3;
  parsed.visual_plan_quality=Math.max(0,Math.min(96,_vpScore));
  parsed.visual_plan_quality_derived=true;
}
// SCENE_COUNT_BACKSTOP: the validator caps content at 8 scenes, but the repair
// pass is told to preserve scene order/count, so an over-long script can never
// be repaired and burns every attempt on a bound it is forbidden to change.
// Merge the shortest adjacent content scenes until it fits - that keeps all
// narration instead of discarding a beat - then renumber and repoint payoff.
if(Array.isArray(parsed.scenes)){
  const _scIsOutro=(s)=>Boolean(s&&s.template_data&&s.template_data.is_outro);
  const _scOutro=parsed.scenes.filter(_scIsOutro);
  const _scContent=parsed.scenes.filter((s)=>s&&!_scIsOutro(s));
  if(_scContent.length>8){
    const _scPayoffIdx=Number(parsed.payoff&&parsed.payoff.resolved_in_scene);
    const _scWords=(s)=>String((s&&s.narration)||'').trim().split(/\s+/).filter(Boolean).length;
    while(_scContent.length>8){
      let _scAt=0,_scBest=Infinity;
      for(let i=0;i<_scContent.length-1;i++){
        const pair=_scWords(_scContent[i])+_scWords(_scContent[i+1]);
        if(pair<_scBest){_scBest=pair;_scAt=i;}
      }
      const _scA=_scContent[_scAt],_scB=_scContent[_scAt+1];
      const _scKeep=Number(_scB.scene_index)===_scPayoffIdx?_scB:_scA;
      _scContent.splice(_scAt,2,Object.assign({},_scA,{
        narration:[String(_scA.narration||'').trim(),String(_scB.narration||'').trim()].filter(Boolean).join(' '),
        point:_scA.point||_scB.point,
        scene_index:_scKeep.scene_index,
      }));
    }
    const _scRemap={};
    _scContent.forEach((s,i)=>{_scRemap[Number(s.scene_index)]=i;s.scene_index=i;});
    parsed.scenes=_scContent.concat(_scOutro);
    if(parsed.payoff&&typeof parsed.payoff==='object'){
      const _scMapped=_scRemap[_scPayoffIdx];
      parsed.payoff.resolved_in_scene=Number.isFinite(_scMapped)?_scMapped:_scContent.length-1;
    }
    parsed.full_script=_scContent.map((s)=>String(s.narration||'').trim()).filter(Boolean).join(' ');
    parsed.scene_count_repaired=true;
  }
}
// HOOK_CANDIDATES_BACKSTOP: this field is optional (the validator only checks
// its shape when present), so a model that garbles it just needs the garbage
// removed, not repaired - drop anything that isn't a clean 3-6 string array.
if(parsed.hook_candidates!==undefined){
  const _hc=Array.isArray(parsed.hook_candidates)?parsed.hook_candidates.filter((x)=>typeof x==='string'&&x.trim()):[];
  if(_hc.length>=3&&_hc.length<=6)parsed.hook_candidates=_hc;
  else if(Array.isArray(_draftScript&&_draftScript.hook_candidates)&&_draftScript.hook_candidates.length>=3&&_draftScript.hook_candidates.length<=6)parsed.hook_candidates=_draftScript.hook_candidates;
  else delete parsed.hook_candidates;
}
// SCENE_IDENTITY_BACKSTOP: scene_index/point identify a scene for every other
// backstop and for downstream b-roll/TTS matching by scene_index - recover
// them positionally from the original draft's scene at the same array index
// if the model dropped them while rebuilding visual fields.
if(Array.isArray(parsed.scenes)&&Array.isArray(_draftScript&&_draftScript.scenes)){
  parsed.scenes.forEach((s,i)=>{
    if(!s||s.template_data&&s.template_data.is_outro)return;
    const draftScene=_draftScript.scenes[i];
    if(!draftScene)return;
    if(typeof s.scene_index!=='number')s.scene_index=draftScene.scene_index;
    if(!s.point||String(s.point).trim().length<3)s.point=draftScene.point;
  });
}
const _vrModes=new Set(['exact_real','context_real','archive_scientific','template_explainer']);
const _vrClean=(v,max=140)=>String(v||'').replace(/\s+/g,' ').trim().slice(0,max);
const _vrStrings=(xs,max=3)=>Array.isArray(xs)?xs.map(x=>_vrClean(x,80)).filter(Boolean).slice(0,max):[];
const _vrKinetic=(s)=>({template_name:'kinetic_text',template_data:{line:_vrClean(s.must_show||s.point||s.narration||'Key fact',72)||'Key fact'}});
const _vrValidTemplate=(obj,s)=>{
  if(!obj||typeof obj!=='object')return _vrKinetic(s);
  const name=String(obj.template_name||''); const d=obj.template_data&&typeof obj.template_data==='object'?obj.template_data:{};
  if(name==='stat_reveal'&&_vrClean(d.statValue,40)&&_vrClean(d.label,72))return {template_name:name,template_data:d};
  if(name==='comparison'&&_vrClean(d.leftLabel,48)&&_vrClean(d.leftValue,48)&&_vrClean(d.rightLabel,48)&&_vrClean(d.rightValue,48))return {template_name:name,template_data:d};
  if(name==='kinetic_text'&&_vrClean(d.line,120))return {template_name:name,template_data:d};
  return _vrKinetic(s);
};
if(Array.isArray(parsed.scenes))parsed.scenes.forEach((s)=>{
  if(!s||s?.template_data?.is_outro)return;
  let mode=_vrClean(s.visual_mode,32);
  if(!_vrModes.has(mode))mode=s.visual_source==='template'?'template_explainer':(_vrClean(s.named_subject,80)?'exact_real':'context_real');
  s.visual_mode=mode;s.must_show=_vrClean(s.must_show||s.named_subject||s.stock_search_query||s.point||s.visual_prompt,120);s.acceptable_substitutes=_vrStrings(s.acceptable_substitutes,3);
  const allowedSources=new Set(['wikimedia','wikipedia','pexels_video','pexels','unsplash']);
  s.source_priority=_vrStrings(s.source_priority,7).filter(x=>allowedSources.has(x));
  if(!s.source_priority.length)s.source_priority=mode==='archive_scientific'?['wikimedia','wikipedia','pexels_video','pexels']:mode==='exact_real'?['wikimedia','wikipedia','pexels_video','pexels','unsplash']:['pexels_video','pexels','unsplash','wikimedia','wikipedia'];
  if(mode==='template_explainer'||s.visual_source==='template'){
    const chosen=_vrValidTemplate({template_name:s.template_name,template_data:s.template_data},s);
    s.visual_mode='template_explainer';s.visual_source='template';s.template_name=chosen.template_name;s.template_data=chosen.template_data;s.visual_type='template';
  }else{s.visual_source='stock';if(s.visual_type==='ai'||!s.visual_type)s.visual_type='real';s.template_fallback=_vrValidTemplate(s.template_fallback,s);}
});

"""

TAG_BROLL_CODE = r"""// VISUAL_RETRIEVAL_TELEMETRY_V2: accept real media or deterministic template fallback.
const r=$json;const sceneIndex=$('Split Out Scenes').item.json.scene_index;
if(!r||r.ok!==true)throw new Error('B-roll commissioning rejected scene '+sceneIndex+': '+(r?.reason||r?.fallback_reason||'no asset')+' best_score='+(r?.best_score??r?.score??'n/a')+' threshold='+(r?.threshold??'n/a'));
const out={scene_index:sceneIndex,_source:r.source||'broll',_attribution:r.attribution||'',asset_score:r.score??null,asset_relevance:r.relevance??null,asset_scroll_stop:r.scroll_stop??null,asset_mobile_clarity:r.mobile_clarity??null,asset_local_similarity:r.local_similarity??null,asset_degraded:r.degraded===true,quality_gate_passed:r.quality_gate_passed!==false,fallback_reason:r.fallback_reason||null,selected_query:r.selected_query||null,visual_mode:r.visual_mode||$('Split Out Scenes').item.json.visual_mode||null};
if(r.type==='template'||r.visual_source==='template'){out.visual_source='template';out.template_name=r.template_name||'kinetic_text';out.template_data=r.template_data||{line:String($('Split Out Scenes').item.json.must_show||$('Split Out Scenes').item.json.point||'Key fact').slice(0,72)};return {json:out};}
if(!r.url)throw new Error('B-roll commissioning returned ok without media URL for scene '+sceneIndex);if(r.type==='video')out.video_url=r.url;else out.images=[r.url];return {json:out};"""


def patch_visual_retrieval_contract(workflow: dict) -> None:
    validator = node_by_name(workflow, "Validate Final Script")
    code = str(validator["parameters"]["jsCode"])
    if VISUAL_ROUTER_MARKER not in code:
        anchor = "const errors = [];"
        if anchor not in code: raise ValueError("validator pre-error anchor missing for visual router")
        validator["parameters"]["jsCode"] = code.replace(anchor, VISUAL_CONTRACT_NORMALIZER + anchor, 1)
    resolver = node_by_name(workflow, "Resolve B-roll")
    body = str(resolver["parameters"]["jsonBody"])
    if "must_show:" not in body:
        anchor = "creative_format: ($('Validate Final Script').item.json.creative_format || ''), run_id: String($execution.id || '')"
        replacement = "creative_format: ($('Validate Final Script').item.json.creative_format || ''), visual_mode: ($('Split Out Scenes').item.json.visual_mode || 'context_real'), must_show: ($('Split Out Scenes').item.json.must_show || $('Split Out Scenes').item.json.named_subject || $('Split Out Scenes').item.json.stock_search_query || ''), acceptable_substitutes: ($('Split Out Scenes').item.json.acceptable_substitutes || []), source_priority: ($('Split Out Scenes').item.json.source_priority || []), template_fallback: ($('Split Out Scenes').item.json.template_fallback || null), run_id: String($execution.id || '')"
        if anchor not in body: raise ValueError("Resolve B-roll run_id anchor missing for visual contract")
        resolver["parameters"]["jsonBody"] = body.replace(anchor, replacement, 1)
    node_by_name(workflow, "Tag B-roll")["parameters"]["jsCode"] = TAG_BROLL_CODE
    merge = node_by_name(workflow, "Merge By scene_index (not position)")
    code = str(merge["parameters"]["jsCode"])
    if "asset_local_similarity:" not in code:
        anchor = "    selected_query: v.selected_query,\n    audio: match.audio,"
        replacement = "    selected_query: v.selected_query,\n    asset_local_similarity: v.asset_local_similarity,\n    asset_degraded: v.asset_degraded === true,\n    quality_gate_passed: v.quality_gate_passed !== false,\n    fallback_reason: v.fallback_reason || null,\n    visual_mode: v.visual_mode || match.visual_mode || null,\n    audio: match.audio,"
        if anchor not in code: raise ValueError("merge telemetry anchor missing")
        merge["parameters"]["jsCode"] = code.replace(anchor, replacement, 1)


def patch_reliability(workflow: dict) -> None:
    for name, tokens in {"Claude: Generate Topic":6000,"Claude: Commission Topic Shortlist":8192,"Claude: Draft Script (Stage 1)":8192,"Claude: Visual Director":8192}.items():
        _prioritize_complete_json(node_by_name(workflow, name), tokens)
    validator=node_by_name(workflow,"Validate Final Script");code=validator["parameters"]["jsCode"]
    for metric,minimum in PUBLISH_MINIMUMS.items(): code=re.sub(rf"({re.escape(metric)}:\s*)\d+",rf"\g<1>{minimum}",code,count=1)
    code=code.replace("if(!Number.isFinite(Number(parsed.visual_plan_quality))||Number(parsed.visual_plan_quality)<78)errors.push(`visual_plan_quality=${parsed.visual_plan_quality} below publish threshold 78`);",f"if(!Number.isFinite(Number(parsed.visual_plan_quality))||Number(parsed.visual_plan_quality)<{VISUAL_PLAN_MINIMUM})errors.push(`visual_plan_quality=${{parsed.visual_plan_quality}} below publish threshold {VISUAL_PLAN_MINIMUM}`);")
    code=code.replace("overall > weakest + 5","overall > weakest + 8").replace("by more than 5 points","by more than 8 points")
    validator["parameters"]["jsCode"]=code;validator["notes"]=RELIABILITY_MARKER+": competent-video floors; evidence remains fail-closed"


def patch_topic_history_resolved_topic(workflow: dict) -> None:
    """Record the topic that actually got published, not always the first pick.

    The categorical-topic-rejection repair path (see patch_visual_director)
    can swap to a different topic entirely when the original is disqualified
    (e.g. medical/health content). Topic History must reflect whichever topic
    the published script actually used, or a swapped-away topic never gets
    marked used and can be re-suggested indefinitely.
    """
    node = node_by_name(workflow, "Save Topic to History")
    old = "={{ JSON.stringify({ topic: $('Extract Generated Topic').item.json.topic, hook: $json.hook }) }}"
    new = "={{ JSON.stringify({ topic: ($json.resolved_topic || $('Extract Generated Topic').item.json.topic), hook: $json.hook }) }}"
    body = str(node["parameters"]["jsonBody"])
    if new in body:
        return
    if old not in body:
        raise RuntimeError("Save Topic to History body anchor missing; cannot record swapped topic")
    node["parameters"]["jsonBody"] = body.replace(old, new, 1)


def upgrade(workflow: dict) -> dict:
    patch_commissioning(workflow);patch_writer(workflow);patch_visual_director(workflow);patch_visual_retrieval_contract(workflow);patch_reliability(workflow);patch_topic_history_resolved_topic(workflow);return workflow


def assert_alignment(workflow: dict) -> None:
    commission=node_by_name(workflow,"Claude: Commission Topic Shortlist")["parameters"]["jsonBody"];writer=node_by_name(workflow,"Claude: Draft Script (Stage 1)")["parameters"]["jsonBody"];visual=node_by_name(workflow,"Claude: Visual Director")["parameters"]["jsonBody"];extractor=node_by_name(workflow,"Extract Generated Topic")["parameters"]["jsCode"];validator=node_by_name(workflow,"Validate Final Script")["parameters"]["jsCode"];resolver=node_by_name(workflow,"Resolve B-roll")["parameters"]["jsonBody"];tag=node_by_name(workflow,"Tag B-roll")["parameters"]["jsCode"]
    required=[(commission,RELIABILITY_MARKER,"commissioning reliability mode"),(commission,"TWO strongest candidates","bounded commissioner output"),(writer,f"{MARKER} WRITER","writer alignment"),(visual,"REPAIRABLE_NEAR_MISS","independent near-miss repair"),(visual,"HARD_REJECT","independent hard reject"),(visual,"literal query","retrieval query taxonomy"),(visual,"broad fallback","retrieval broad fallback"),(visual,"quality_route","quality routing telemetry"),(visual,VISUAL_ROUTER_MARKER,"visual source router prompt"),(validator,VISUAL_ROUTER_MARKER,"visual source router normalizer"),(resolver,"must_show:","resolver visual contract"),(resolver,"template_fallback:","resolver template fallback contract"),(tag,VISUAL_TELEMETRY_MARKER,"template/telemetry tagger"),(extractor,"stock_feasibility","commissioning metadata preservation"),(extractor,"novelty_delta","novelty metadata preservation")]
    missing=[label for text,marker,label in required if marker not in text]
    if missing: raise RuntimeError("quality alignment did not land: "+", ".join(missing))
    for name in ["Claude: Generate Topic","Claude: Commission Topic Shortlist","Claude: Draft Script (Stage 1)","Claude: Visual Director"]:
        body=str(node_by_name(workflow,name)["parameters"]["jsonBody"]).strip()
        if not(body.startswith("={{") and body.endswith("}}") and "JSON.stringify(" in body): raise RuntimeError(f"{name} JSON Body lost its n8n expression wrapper")
        if 'reasoning_effort: "none"' not in body: raise RuntimeError(f"{name} is not configured to prioritize complete JSON")
    if "max_completion_tokens: 8192" not in commission: raise RuntimeError("commissioner response budget is below reliability target")
    if f"concept_strength: {PUBLISH_MINIMUMS['concept_strength']}" not in validator: raise RuntimeError("validator concept floor did not move to reliability calibration")
    if f"shareability: {PUBLISH_MINIMUMS['shareability']}" not in validator: raise RuntimeError("validator shareability floor did not move to reliability calibration")
    if f"visual_plan_quality)<{VISUAL_PLAN_MINIMUM}" not in validator: raise RuntimeError("visual-plan floor did not move to reliability calibration")
    if "at most ONE bounded repair" not in visual: raise RuntimeError("quality repair is no longer explicitly bounded")
    if "Never set visual_type=ai" not in visual: raise RuntimeError("Visual Director can still commission non-existent AI shots")
    if "r.type==='template'" not in tag: raise RuntimeError("Tag B-roll cannot pass template fallbacks into compose")
    repair=node_by_name(workflow,"Claude: Repair Script")["parameters"]["jsonBody"]
    if "CATEGORICAL TOPIC REJECTION" not in repair: raise RuntimeError("repair pass lost its categorical-topic-rejection escape hatch")
    if "resolved_topic" not in repair: raise RuntimeError("repair pass no longer reports resolved_topic on a topic swap")
    history=node_by_name(workflow,"Save Topic to History")["parameters"]["jsonBody"]
    if "resolved_topic" not in history: raise RuntimeError("Topic History no longer records a swapped-topic repair")
