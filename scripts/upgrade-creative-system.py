#!/usr/bin/env python3
"""Apply the Shorts creative-system upgrade to a quality-gated n8n workflow."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

MARKER = "CREATIVE_SYSTEM"
POOL_NODE = "Parse Topic Pool"
COMMISSION_NODE = "Claude: Commission Topic Shortlist"
EDITOR_PARSE_NODE = "Parse Editorial For Visual Director"
VISUAL_NODE = "Claude: Visual Director"
REPAIR_NODE = "Claude: Repair Script"

ARCHETYPES = [
    "impossible_comparison", "visual_demonstration", "hidden_mechanism",
    "looks_fake_but_real", "counterfactual_consequence", "before_after",
    "misconception_reversal", "historical_moment", "breaking_point",
    "observable_experiment", "scale_transformation", "result_first_explanation",
]
FORMATS = [
    "documentary_cinematic", "comparison_reveal", "minimal_proof",
    "archival_history", "macro_detail", "kinetic_data",
]


def node_by_name(w: dict, name: str) -> dict:
    for n in w.get("nodes", []):
        if n.get("name") == name:
            return n
    raise KeyError(f"required n8n node not found: {name}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"could not patch {label}: anchor not found")
    return text.replace(old, new, 1)


def voice_bible() -> str:
    p = Path("shorts-compose/channel-voice.json")
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.dumps(json.loads(p.read_text(encoding="utf-8")), separators=(",", ":"))


def patch_topic_search(w: dict) -> None:
    n = node_by_name(w, "Claude: Generate Topic")
    body = n["parameters"]["jsonBody"]
    if MARKER not in body:
        anchor = "Must NOT closely resemble any of these already-used topics:"
        rules = (
            f"{MARKER} - SEARCH A LARGE CREATIVE SPACE: every candidate must use one viewing archetype from: "
            + ", ".join(ARCHETYPES)
            + ". The archetype is the viewer experience, not the subject category. Rotate archetypes as aggressively as subjects. "
            "A true fact with generic footage or no satisfying payoff is not a strong Short.\\n\\n"
            "Archetype constraints: visual_demonstration must be observable; looks_fake_but_real needs defensible proof; breaking_point needs a real threshold; historical_moment is one event/detail, never a biography; observable_experiment must be safe and instantly legible; result_first_explanation opens on the visible result.\\n\\n"
            + anchor
        )
        body = replace_once(body, anchor, rules, "topic archetype rules")
    body = body.replace("GENERATE 12 DISTINCT candidate topics", "GENERATE 36 DISTINCT candidate topics")
    body = body.replace("max_tokens: 4000", "max_tokens: 9000")
    body = body.replace(
        "where each item has fields: topic (the fact as one plain sentence), research_query",
        "where each item has fields: topic (the fact as one plain sentence), archetype (one exact archetype), research_query",
    )
    n["parameters"]["jsonBody"] = body


def add_topic_commissioning(w: dict) -> None:
    names = {n.get("name") for n in w.get("nodes", [])}
    gen = node_by_name(w, "Claude: Generate Topic")
    if POOL_NODE not in names:
        w["nodes"].append({
            "id": "b675bb82-bbeb-4efe-95ed-706a5ca43573", "name": POOL_NODE,
            "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [2400, 300],
            "parameters": {"jsCode": """const response=$input.first().json;
if(response.error) throw new Error('Topic pool API error: '+JSON.stringify(response.error));
const choice=(response.choices||[])[0];
let raw=String((choice&&choice.message&&choice.message.content)||'').trim().replace(/^```(?:json)?\\s*/i,'').replace(/```\\s*$/,'');
if(!raw){
  if(choice&&choice.finish_reason==='length') throw new Error('Topic pool hit max_completion_tokens before producing any text (finish_reason: length)');
  throw new Error('Topic pool returned no text (finish_reason: '+(choice&&choice.finish_reason||'unknown')+')');
}
function extractBalancedObjects(s){
  const out=[];
  let i=0;
  while(i<s.length){
    const start=s.indexOf('{',i);
    if(start<0) break;
    let depth=0,inStr=false,esc=false,end=-1;
    for(let j=start;j<s.length;j++){
      const ch=s[j];
      if(inStr){ if(esc) esc=false; else if(ch==='\\\\') esc=true; else if(ch==='\"') inStr=false; }
      else if(ch==='\"') inStr=true;
      else if(ch==='{') depth++;
      else if(ch==='}'){ depth--; if(depth===0){ end=j; break; } }
    }
    if(end<0) break;
    out.push(s.slice(start,end+1));
    i=end+1;
  }
  return out;
}
// The model occasionally second-guesses itself mid-response (a broken false
// start, then "Wait, I need to output proper JSON only." followed by the
// real, correct JSON) - both land in the same response even though it
// completes normally (finish_reason:'stop', not truncated). A naive
// indexOf('{')..lastIndexOf('}') slice grabs from the broken false start's
// opening brace to the real JSON's closing brace, mashing garbage and valid
// JSON together. Instead, find every balanced top-level {...} block and try
// them from LAST to FIRST (the self-corrected answer is always the latest
// one), keeping the first one that both parses and has a candidates array.
const candidateBlocks=extractBalancedObjects(raw);
if(!candidateBlocks.length){
  if(choice&&choice.finish_reason==='length') throw new Error('Topic pool JSON was truncated by max_completion_tokens (no balanced JSON object found) - reduce the candidate pool size or raise max_completion_tokens on "Claude: Generate Topic"');
  throw new Error('Topic pool returned no JSON object (finish_reason: '+(choice&&choice.finish_reason||'unknown')+')');
}
let obj,lastErr;
for(let k=candidateBlocks.length-1;k>=0;k--){
  try{
    const parsed=JSON.parse(candidateBlocks[k]);
    if(Array.isArray(parsed.candidates)){ obj=parsed; break; }
  }catch(err){ lastErr=err; }
}
if(!obj){
  const reason=lastErr?lastErr.message:'no balanced JSON block contained a candidates array';
  if(choice&&choice.finish_reason==='length') throw new Error('Topic pool JSON was truncated by max_completion_tokens (parse error: '+reason+') - reduce the candidate pool size or raise max_completion_tokens on "Claude: Generate Topic"');
  throw new Error('Topic pool JSON failed to parse (finish_reason: '+(choice&&choice.finish_reason||'unknown')+', parse error: '+reason+')');
}
let pool=Array.isArray(obj.candidates)?obj.candidates:[];
pool=pool.filter(c=>c?.topic).map(c=>({topic:String(c.topic).trim(),archetype:String(c.archetype||'looks_fake_but_real').trim(),research_query:String(c.research_query||c.topic).trim(),first_frame_concept:String(c.first_frame_concept||'').trim(),share_reason:String(c.share_reason||'').trim(),evidence_score:Number(c.evidence_score)||0,visual_score:Number(c.visual_score)||0,share_score:Number(c.share_score)||0,reason:String(c.reason||''),score:Number(c.score)||0})).sort((x,y)=>y.score-x.score);
if(pool.length<3) throw new Error('Topic pool produced fewer than 3 usable candidates');
return {json:{pool,shortlist:pool.slice(0,4)}};"""},
        })
    if COMMISSION_NODE not in names:
        c = copy.deepcopy(gen)
        c["id"] = "44b8a598-b27f-4fe1-a760-dd6eb781ccda"
        c["name"] = COMMISSION_NODE
        c["position"] = [2700, 300]
        c["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ model: "gpt-5.6-luna", max_completion_tokens: 6000, reasoning_effort: "medium", response_format: { type: "json_object" }, messages: [{ role: "user", content: "You are commissioning ONE production-worthy YouTube Short from an already harsh shortlist. Judge the underlying viewing experience, not clever wording.\n\nSHORTLIST: " + JSON.stringify($json.shortlist) + "\n\nDeep-score each 0-100 on concept_strength, evidence_strength, first_frame_strength, payoff_strength, shareability, novelty, production_feasibility, naturalness. Overall may not exceed the weakest of concept/evidence/first-frame/payoff/shareability by more than 5. 80 is merely good; 90 is rare. Prefer intrinsically visual, defensible concepts and protect archetype variety. Reject interchangeable trivia.\n\nReturn ONLY JSON with candidates sorted best-first. Preserve topic, archetype, research_query, first_frame_concept, share_reason. Add evidence_score, visual_score, share_score, concept_score, payoff_score, novelty_score, execution_score, reason, score." }] }) }}'''
        c["parameters"].setdefault("options", {})["timeout"] = 90000
        w["nodes"].append(c)

    con = w.setdefault("connections", {})
    con["Claude: Generate Topic"] = {"main": [[{"node": POOL_NODE, "type": "main", "index": 0}]]}
    con[POOL_NODE] = {"main": [[{"node": COMMISSION_NODE, "type": "main", "index": 0}]]}
    con[COMMISSION_NODE] = {"main": [[{"node": "Extract Generated Topic", "type": "main", "index": 0}]]}

    ex = node_by_name(w, "Extract Generated Topic")
    code = ex["parameters"]["jsCode"]
    old_map = ".map(c => ({ topic: String(c.topic).trim(), research_query: String(c.research_query || c.topic).trim(), first_frame_concept: String(c.first_frame_concept || ''), share_reason: String(c.share_reason || ''), evidence_score: Number(c.evidence_score) || 0, visual_score: Number(c.visual_score) || 0, share_score: Number(c.share_score) || 0, reason: String(c.reason || ''), score: Number(c.score) || 0 }))"
    new_map = ".map(c => ({ topic: String(c.topic).trim(), archetype: String(c.archetype || 'looks_fake_but_real').trim(), research_query: String(c.research_query || c.topic).trim(), first_frame_concept: String(c.first_frame_concept || ''), share_reason: String(c.share_reason || ''), evidence_score: Number(c.evidence_score) || 0, visual_score: Number(c.visual_score) || 0, share_score: Number(c.share_score) || 0, concept_score: Number(c.concept_score) || 0, payoff_score: Number(c.payoff_score) || 0, novelty_score: Number(c.novelty_score) || 0, execution_score: Number(c.execution_score) || 0, reason: String(c.reason || ''), score: Number(c.score) || 0 }))"
    code = replace_once(code, old_map, new_map, "topic commission parser")
    old_ret = "return { json: { topic: picked.topic, score: picked.score, research_query: picked.research_query || picked.topic, first_frame_concept: picked.first_frame_concept || '', share_reason: picked.share_reason || '', evidence_score: picked.evidence_score || 0, visual_score: picked.visual_score || 0, share_score: picked.share_score || 0, candidates } };"
    new_ret = "return { json: { topic: picked.topic, archetype: picked.archetype || 'looks_fake_but_real', score: picked.score, research_query: picked.research_query || picked.topic, first_frame_concept: picked.first_frame_concept || '', share_reason: picked.share_reason || '', evidence_score: picked.evidence_score || 0, visual_score: picked.visual_score || 0, share_score: picked.share_score || 0, concept_score: picked.concept_score || 0, payoff_score: picked.payoff_score || 0, novelty_score: picked.novelty_score || 0, execution_score: picked.execution_score || 0, candidates, candidate_pool: $('Parse Topic Pool').item.json.pool || candidates } };"
    ex["parameters"]["jsCode"] = replace_once(code, old_ret, new_ret, "topic commission return")


def patch_writer(w: dict) -> None:
    voice = voice_bible().replace('"', '\\"')
    writer = node_by_name(w, "Claude: Draft Script (Stage 1)")
    body = writer["parameters"]["jsonBody"]
    if "CHANNEL VOICE BIBLE - CREATIVE_SYSTEM" not in body:
        anchor = "HOOK - the single most important sentence in the video."
        body = replace_once(body, anchor, "CHANNEL VOICE BIBLE - CREATIVE_SYSTEM: " + voice + "\\n\\nSound like this channel, not generic viral narration.\\n\\n" + anchor, "writer voice")
    if "ENGAGEMENT IS OPTIONAL - CREATIVE_SYSTEM" not in body:
        anchor = "COMMENT MECHANIC (critical for engagement):"
        rule = "ENGAGEMENT IS OPTIONAL - CREATIVE_SYSTEM: a comment question and share outro are optional. If either weakens the viewing experience, omit it, set the matching field to null, and do not hide a spoken CTA elsewhere in narration.\\n\\n" + anchor
        body = replace_once(body, anchor, rule, "writer engagement")
    body = body.replace('\\"comment_hook\\": string, \\"outro_line\\": string', '\\"comment_hook\\": string|null, \\"outro_line\\": string|null')
    topic_anchor = 'Topic: \" + $json.topic + \"\\nFirst-frame concept from selection:'
    if topic_anchor in body:
        body = body.replace(topic_anchor, 'Topic: \" + $json.topic + \"\\nConcept archetype: \" + ($json.archetype || \'looks_fake_but_real\') + \"\\nFirst-frame concept from selection:', 1)
    writer["parameters"]["jsonBody"] = body
    # EDITORIAL_STAGE_REMOVED: the editor-specific patching (archetype rules,
    # voice-bible re-check, structural field integrity) used to live here.
    # That stage no longer exists (see add_visual_director) - Visual Director
    # now owns structural field integrity and voice/quality grading directly
    # against Draft Script's own output.


def add_visual_director(w: dict) -> None:
    # EDITORIAL_STAGE_REMOVED: Claude: Editorial Rewrite (Stage 2) and its
    # parser node are deleted entirely. Editorial repeatedly discarded the
    # given draft's topic and substituted an unrelated one in production -
    # confirmed on multiple executions, still recurring even after two
    # rounds of explicit prompt instructions plus a deterministic validation
    # backstop. Draft Script -> Visual Director directly now; Visual
    # Director already performs its own independent quality critique and
    # grading pass, so nothing load-bearing is lost.
    w["nodes"] = [n for n in w["nodes"] if n.get("name") not in ("Claude: Editorial Rewrite (Stage 2)", EDITOR_PARSE_NODE)]
    # n8n's API rejects the whole workflow (400 unknown_connection_source) if
    # the connections dict still has a key naming a deleted node, even though
    # nothing points at it - deleting the node is not enough, the dangling
    # source entry inherited from the base workflow must go too.
    w.get("connections", {}).pop("Claude: Editorial Rewrite (Stage 2)", None)
    w.get("connections", {}).pop(EDITOR_PARSE_NODE, None)
    names = {n.get("name") for n in w.get("nodes", [])}
    writer = node_by_name(w, "Claude: Draft Script (Stage 1)")
    if VISUAL_NODE not in names:
        v = copy.deepcopy(writer)
        v["id"] = "f5bc27a1-e53c-4a35-88e4-b89898f9eb5b"
        v["name"] = VISUAL_NODE
        v["position"] = [5100, 300]
        v["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ model: "gpt-5.6-luna", max_completion_tokens: 8192, reasoning_effort: "medium", response_format: { type: "json_object" }, messages: [{ role: "user", content: "You are the VISUAL DIRECTOR for a high-end YouTube Shorts channel. Do NOT change hook, hook_candidates, hook_type, title, seo_description, tags, comment_hook, outro_line, full_script, narration text, payoff, caption_style, trigger, quality, scene order, or scene count - copy every one of these fields through byte-for-byte exactly as given in SCRIPT, even the ones you are not directly working with. This includes EVERY scene's scene_index and point fields specifically - these two are the most frequently dropped fields in the whole pipeline when scenes get rebuilt to add visual fields, so copy them through explicitly, unchanged, on every single scene.\n\nSCRIPT: " + JSON.stringify($json.draft) + "\n\nChoose one creative_format: documentary_cinematic, comparison_reveal, minimal_proof, archival_history, macro_detail, kinetic_data. Set visual_grammar to that family. Scene 0 must work muted; set first_frame_type to hero_motion, macro_anomaly, face_reaction, scale_comparison, result_first, archive_proof, or kinetic_stat - this is REQUIRED, never leave it unset. Assign each content scene visual_role = hero/evidence/detail/comparison/breath/payoff. For each stock scene, REQUIRED: add 3-4 DISTINCT concrete search_queries (2-5 words, at least 2 required) and set stock_search_query to the strongest - never leave a stock scene without both. Preserve named_subject for exact real entities. Use templates only when the beat is fundamentally a number/comparison/punchy line. Choose caption_mode = karaoke/key_phrases/minimal. Set transition_style=hard_cut. Carry the CTA decision through and set engagement_mode=none/comment_only/share_only/comment_and_share; do not invent a CTA. Set open_loop_count honestly (usually 0-2). Set visual_plan_quality 0-100; generic scene 0 or repetitive roles must score below 78. Before returning, verify as a final pass: every scene still has its original scene_index and point; every stock scene has stock_search_query and at least 2 search_queries; first_frame_type is set. Return ONLY the COMPLETE script JSON: every field from the input SCRIPT copied through exactly (hook_candidates, caption_style, trigger, quality, and every scene's scene_index/point are the fields most often accidentally dropped while focusing on the new visual fields below - double check they are all still present and unchanged) plus these new visual fields added." }] }) }}'''
        v["parameters"].setdefault("options", {})["timeout"] = 90000
        w["nodes"].append(v)
    if REPAIR_NODE not in names:
        # REPAIR_LOOP_V1: on a failed quality gate, revise the SAME script using
        # the deterministic validator's exact failure reasons instead of
        # discarding the topic and starting a brand-new generation from scratch.
        # jsonBody gets its real content from quality_alignment_impl.patch_visual_director,
        # which builds it from the same template as Visual Director's prompt.
        r = copy.deepcopy(writer)
        r["id"] = "9b7b6a2b-7a7f-4b3f-9a4a-2f8b6e1c9a3d"
        r["name"] = REPAIR_NODE
        r["position"] = [5100, 500]
        r["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ model: "gpt-5.6-luna", max_completion_tokens: 8192, reasoning_effort: "medium", response_format: { type: "json_object" }, messages: [{ role: "user", content: "placeholder - overwritten by quality_alignment_impl.patch_visual_director" }] }) }}'''
        r["parameters"].setdefault("options", {})["timeout"] = 90000
        w["nodes"].append(r)
    c = w.setdefault("connections", {})
    c["Parse Draft JSON"] = {"main": [[{"node": VISUAL_NODE, "type": "main", "index": 0}]]}
    c[VISUAL_NODE] = {"main": [[{"node": "Validate Final Script", "type": "main", "index": 0}]]}
    c[REPAIR_NODE] = {"main": [[{"node": "Validate Final Script", "type": "main", "index": 0}]]}
    # REPAIR_LOOP_V1: a failed quality gate now revises the same script instead
    # of burning a fresh topic - "Claude: Generate Topic" runs at most once per
    # scheduled run.
    c["If Under Max Script Attempts"] = {"main": [
        [{"node": REPAIR_NODE, "type": "main", "index": 0}],
        [{"node": "Fail: Script Generation Exhausted", "type": "main", "index": 0}],
    ]}


def patch_validator(w: dict) -> None:
    n = node_by_name(w, "Validate Final Script")
    code = n["parameters"]["jsCode"]
    code = code.replace("  naturalness: 74,\n  overall: 77,", "  naturalness: 74,\n  distinctiveness: 74,\n  voice_specificity: 72,\n  overall: 77,")
    code = code.replace(
        "if (!parsed.comment_hook || parsed.comment_hook.length < 8) {\n  errors.push('comment_hook missing or too short');\n}",
        "if (parsed.comment_hook != null && String(parsed.comment_hook).trim() && String(parsed.comment_hook).trim().length < 8) {\n  errors.push('comment_hook, when used, is too short');\n}",
    )
    old = """const outroLine = (parsed.outro_line && String(parsed.outro_line).trim())
  ? String(parsed.outro_line).trim()
  : 'If this got you, send it to someone who needs to see it.';
if (Array.isArray(parsed.scenes) && parsed.scenes.length) {
  const maxIdx = parsed.scenes.reduce((m, s) => Math.max(m, Number(s.scene_index) || 0), -1);
  parsed.scenes.push({
    scene_index: maxIdx + 1,
    point: 'outro',
    narration: outroLine,
    visual_source: 'template',
    template_name: 'kinetic_text',
    template_data: { line: 'Like, Share, Follow', is_outro: true },
  });
}
"""
    new = """const outroLine = (parsed.outro_line && String(parsed.outro_line).trim()) ? String(parsed.outro_line).trim() : '';
const wantsShareOutro = ['share_only', 'comment_and_share'].includes(parsed.engagement_mode);
if (outroLine && wantsShareOutro && Array.isArray(parsed.scenes) && parsed.scenes.length) {
  const maxIdx = parsed.scenes.reduce((m, s) => Math.max(m, Number(s.scene_index) || 0), -1);
  parsed.scenes.push({scene_index:maxIdx+1,point:'outro',narration:outroLine,visual_source:'template',template_name:'kinetic_text',template_data:{line:'Like, Share, Subscribe',is_outro:true}});
}
"""
    code = replace_once(code, old, new, "optional outro")
    if "CREATIVE_SYSTEM visual commissioning gate" not in code:
        anchor = "// Medical/health exclusion backstop"
        gate = """// CREATIVE_SYSTEM visual commissioning gate.
const validFormats=['documentary_cinematic','comparison_reveal','minimal_proof','archival_history','macro_detail','kinetic_data'];
const validCaptionModes=['karaoke','key_phrases','minimal'];
const validEngagementModes=['none','comment_only','share_only','comment_and_share'];
if(!validFormats.includes(parsed.creative_format))errors.push('creative_format missing/invalid');
if(!validCaptionModes.includes(parsed.caption_mode))errors.push('caption_mode missing/invalid');
if(!validEngagementModes.includes(parsed.engagement_mode))errors.push('engagement_mode missing/invalid');
if(!Number.isFinite(Number(parsed.visual_plan_quality))||Number(parsed.visual_plan_quality)<78)errors.push(`visual_plan_quality=${parsed.visual_plan_quality} below publish threshold 78`);
if(!parsed.first_frame_type)errors.push('first_frame_type missing');
if(parsed.engagement_mode==='none'&&(parsed.comment_hook||parsed.outro_line))errors.push('engagement_mode=none but CTA fields populated');
if(parsed.engagement_mode==='comment_only'&&parsed.outro_line)errors.push('comment_only cannot carry a share outro');
if(Array.isArray(parsed.scenes)){parsed.scenes.forEach((s,i)=>{if(s?.template_data?.is_outro)return;if(!s.visual_role)errors.push(`scene ${s.scene_index} missing visual_role`);if(s.visual_source!=='template'&&(!Array.isArray(s.search_queries)||s.search_queries.filter(Boolean).length<2))errors.push(`scene ${s.scene_index} needs at least 2 search_queries`);});}

""" + anchor
        code = replace_once(code, anchor, gate, "visual validator")
    n["parameters"]["jsCode"] = code


def patch_broll_and_payload(w: dict) -> None:
    r = node_by_name(w, "Resolve B-roll")
    r["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ query: ($('Split Out Scenes').item.json.stock_search_query || $('Split Out Scenes').item.json.visual_prompt), queries: ($('Split Out Scenes').item.json.search_queries || []), alternate_queries: ($('Split Out Scenes').item.json.search_queries || []).slice(1), subject: ($('Split Out Scenes').item.json.named_subject || ''), description: ($('Split Out Scenes').item.json.visual_prompt || $('Split Out Scenes').item.json.stock_search_query), scene_index: $('Split Out Scenes').item.json.scene_index, first_frame: Number($('Split Out Scenes').item.json.scene_index) === 0, creative_format: ($('Validate Final Script').item.json.creative_format || '') }) }}'''
    tag = node_by_name(w, "Tag B-roll")
    tag["parameters"]["jsCode"] = """const r=$json;const sceneIndex=$('Split Out Scenes').item.json.scene_index;if(!r||r.ok!==true||!r.url)throw new Error('B-roll commissioning rejected scene '+sceneIndex+': '+(r?.reason||'no asset')+' best_score='+(r?.best_score??'n/a')+' threshold='+(r?.threshold??'n/a'));const out={scene_index:sceneIndex,_source:r.source||'broll',_attribution:r.attribution||'',asset_score:r.score??null,asset_relevance:r.relevance??null,asset_scroll_stop:r.scroll_stop??null,asset_mobile_clarity:r.mobile_clarity??null,selected_query:r.selected_query||null};if(r.type==='video')out.video_url=r.url;else out.images=[r.url];return {json:out};"""
    merge = node_by_name(w, "Merge By scene_index (not position)")
    code = merge["parameters"]["jsCode"]
    code = code.replace("    template_data: v.template_data,\n    audio: match.audio,", "    template_data: v.template_data,\n    asset_score: v.asset_score,\n    asset_relevance: v.asset_relevance,\n    asset_scroll_stop: v.asset_scroll_stop,\n    asset_mobile_clarity: v.asset_mobile_clarity,\n    asset_source: v._source,\n    selected_query: v.selected_query,\n    audio: match.audio,")
    # Aggregate per-scene asset_score (added just above; template scenes have
    # no asset_score and are correctly excluded) into run-level min/avg, so
    # "Log Published Video" can report real b-roll quality instead of the
    # null placeholders it previously always sent.
    code = code.replace(
        "merged.sort((a, b) => a.scene_index - b.scene_index);\n\nreturn {",
        "merged.sort((a, b) => a.scene_index - b.scene_index);\n\nconst assetScores = merged.map((v) => Number(v.asset_score)).filter(Number.isFinite);\nconst asset_quality_min = assetScores.length ? Math.min(...assetScores) : null;\nconst asset_quality_avg = assetScores.length ? Number((assetScores.reduce((s, x) => s + x, 0) / assetScores.length).toFixed(2)) : null;\n\nreturn {",
    )
    code = code.replace("    caption_style: $('Validate Final Script').item.json.caption_style,\n    data: merged", "    caption_style: $('Validate Final Script').item.json.caption_style,\n    caption_mode: $('Validate Final Script').item.json.caption_mode,\n    creative_format: $('Validate Final Script').item.json.creative_format,\n    visual_grammar: $('Validate Final Script').item.json.visual_grammar,\n    engagement_mode: $('Validate Final Script').item.json.engagement_mode,\n    outro_line: $('Validate Final Script').item.json.outro_line || null,\n    asset_quality_min,\n    asset_quality_avg,\n    data: merged")
    merge["parameters"]["jsCode"] = code


def patch_performance_log(w: dict) -> None:
    n = node_by_name(w, "Log Published Video")
    n["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ video_id: ($('YouTube: Upload Draft').item.json.uploadId || $('YouTube: Upload Draft').item.json.id), topic: $('Extract Generated Topic').item.json.topic, hook: $('Validate Final Script').item.json.hook, title: $('Validate Final Script').item.json.title, comment_hook: $('Validate Final Script').item.json.comment_hook || null, caption_style: $('Validate Final Script').item.json.caption_style, trigger: $('Validate Final Script').item.json.trigger, creative_dna: { concept_archetype: $('Extract Generated Topic').item.json.archetype || null, creative_format: $('Validate Final Script').item.json.creative_format || null, visual_grammar: $('Validate Final Script').item.json.visual_grammar || null, first_frame_type: $('Validate Final Script').item.json.first_frame_type || null, first_frame_source: (($('Validate Final Script').item.json.scenes || [])[0]?.visual_source || null), first_frame_score: $('Validate Final Script').item.json.quality?.first_frame_strength ?? null, scene_count: ($('Validate Final Script').item.json.scenes || []).filter(s => !s?.template_data?.is_outro).length, word_count: String($('Validate Final Script').item.json.full_script || '').trim().split(/\s+/).filter(Boolean).length, payoff_position_pct: (() => { const ss=($('Validate Final Script').item.json.scenes || []).filter(s => !s?.template_data?.is_outro); const ris=Number($('Validate Final Script').item.json.payoff?.resolved_in_scene); const idx=ss.findIndex(s => Number(s.scene_index) === ris); return idx >= 0 && ss.length ? Number(((idx + 1) / ss.length).toFixed(3)) : null; })(), open_loop_count: $('Validate Final Script').item.json.open_loop_count ?? null, template_count: ($('Validate Final Script').item.json.scenes || []).filter(s => !s?.template_data?.is_outro && s.visual_source === 'template').length, real_video_count: ($('Validate Final Script').item.json.scenes || []).filter(s => !s?.template_data?.is_outro && s.visual_source !== 'template' && s.visual_type === 'real').length, still_image_count: ($('Validate Final Script').item.json.scenes || []).filter(s => !s?.template_data?.is_outro && s.visual_source !== 'template' && s.visual_type !== 'real').length, caption_mode: $('Validate Final Script').item.json.caption_mode || null, transition_style: $('Validate Final Script').item.json.transition_style || 'hard_cut', engagement_mode: $('Validate Final Script').item.json.engagement_mode || null, comment_hook_present: Boolean($('Validate Final Script').item.json.comment_hook), outro_present: Boolean($('Validate Final Script').item.json.outro_line), asset_quality_min: ($('Merge By scene_index (not position)').item.json.asset_quality_min ?? null), asset_quality_avg: ($('Merge By scene_index (not position)').item.json.asset_quality_avg ?? null) } }) }}'''


def upgrade(w: dict) -> dict:
    patch_topic_search(w)
    add_topic_commissioning(w)
    patch_writer(w)
    add_visual_director(w)
    patch_validator(w)
    patch_broll_and_payload(w)
    patch_performance_log(w)
    return w


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-creative-system.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    w = json.loads(src.read_text(encoding="utf-8"))
    dst.write_text(json.dumps(upgrade(w), indent=2) + "\n", encoding="utf-8")
    print(f"creative system workflow written to {dst}")


if __name__ == "__main__":
    main()
