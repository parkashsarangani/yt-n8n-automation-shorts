#!/usr/bin/env python3
"""Apply VISUAL_MATCHING_V4 contracts to the generated n8n workflow.

This patch is intentionally idempotent and compatible with both the early
creative-system Visual Director and the final QUALITY_ALIGNMENT Visual Director.
The final parser transform reapplies it after quality alignment, so no late
prompt replacement can silently remove the semantic visual contract.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

MARKER = "VISUAL_MATCHING_V4"
CONTRACT_GATE = "VISUAL_MATCHING_V4 contract gate"
LEGACY_AI_GATE_REMOVED = "V4_LEGACY_AI_VISUAL_GATE_REMOVED"
DETERMINISTIC_TEMPLATE_NORMALIZER = "V4_DETERMINISTIC_TEMPLATE_NORMALIZER"
PROOF_MODE_ROUTER = "V4_PROOF_MODE_ROUTER"
LEGACY_TEMPLATE_VALIDATOR_EXPANDED = "V4_LEGACY_TEMPLATE_VALIDATOR_EXPANDED"
LEGACY_TEMPLATE_RULE = "For template_explainer set visual_source=template and template_name ONLY stat_reveal, comparison, or kinetic_text with complete template_data."
V4_TEMPLATE_RULE = (
    "For template_explainer set visual_source=template and make template_name match visual_proof_mode exactly: "
    "comparison=>comparison, number_visualization=>stat_reveal, kinetic_text=>kinetic_text, diagram=>diagram, timeline=>timeline, map=>map; "
    "always provide the complete template_data required by that renderer. Renderer-aligned bounds are mandatory: "
    "map has 1-8 uniquely labeled locations and 0-8 connections whose from/to labels exist; timeline has 2-7 events with nonempty date+label; "
    "diagram has 2-8 nodes with unique nonempty ids+labels and 1-12 edges whose from/to ids exist."
)
OBSOLETE_AI_VISUAL_ERRORS = (
    "visual_prompt too short/missing",
    "missing negative_prompt",
    "visual_prompt likely requires readable text/numbers in the AI-generated video",
    "negative_prompt does not explicitly exclude readable text/numbers",
)


def node(workflow: dict, name: str) -> dict:
    for n in workflow.get("nodes", []):
        if n.get("name") == name:
            return n
    raise KeyError(name)


V4_INSTRUCTION = (
    "VISUAL_MATCHING_V4 - EXACT SCRIPT-TO-PIXEL CONTRACT. Before choosing a source, define the exact visual proof for EVERY content scene. "
    "Add visual_claim (one literal sentence describing what the pixels must communicate), global_subject (the overall selected topic/entity active throughout the Short), "
    "required_entities (array of concrete visible entities), required_actions (array of visible actions/states), required_relationships (array of visible spatial/scale/comparison relationships), "
    "forbidden_visuals (array including the tempting generic filler that would be topically related but narratively wrong), acceptable_visuals (0-3 valid alternate depictions), and visual_proof_mode. "
    "visual_proof_mode must be exactly one of literal_video, literal_image, annotated_real, comparison, number_visualization, kinetic_text, diagram, timeline, map. "
    "visual_prompt and negative_prompt are legacy AI-generation fields and are NOT required by V4; do not spend repair attempts recreating them. "
    "Do NOT collapse a scene into its broad named subject: if narration says an octopus squeezes through a narrow gap, visual_claim must require the squeezing-through-gap action, not merely 'octopus'. "
    "Resolve pronouns and vague local wording using OVERALL SELECTED TOPIC / MACRO CONTEXT. "
    "REPRESENTATION ROUTER: comparison=>visual_source template + template_name comparison; number_visualization=>template + stat_reveal; kinetic_text=>template + kinetic_text; "
    "map=>template + map with template_data {title,locations:[{label,lat,lon}],connections:[{from,to,label?}]} using 1-8 uniquely labeled locations and at most 8 connections whose endpoints match those labels; "
    "timeline=>template + timeline with template_data {title,events:[{date,label,detail?}]} using 2-7 events with nonempty date+label; "
    "diagram=>template + diagram with template_data {title,nodes:[{id,label,detail?}],edges:[{from,to,label?}]} using 2-8 uniquely identified nodes and 1-12 edges whose endpoints match node ids. "
    "Use map only when the script/context supports trustworthy coordinates; otherwise use annotated_real/archive proof. "
    "annotated_real MUST remain a real-media retrieval scene, not a template at planning time: the resolver will select the exact real image, visually locate callout boxes on those pixels, then convert it into the annotated_real Remotion composition. "
    "REMOTION TEMPLATE CAP: across the non-outro video, at most ONE scene may use visual_source=template or a deterministic Remotion proof mode, and it must NEVER be scene_index 0. Scene 0 must be real/archive footage or imagery. "
    "If a fact is fundamentally abstract and stock cannot literally prove it, choose annotated_real or a deterministic template instead of generic symbolic stock. literal_video is preferred whenever an observable action is required. "
    "For real/archive scenes, search_queries must separately cover the subject, action, and relationship where applicable. A beautiful broad-topic shot that omits the narrated action/relationship is wrong."
)


def _insert_once(body: str, anchor: str, text: str, *, after: bool = True) -> str:
    if text in body:
        return body
    if anchor not in body:
        return body
    if after:
        return body.replace(anchor, anchor + " " + text, 1)
    return body.replace(anchor, text + " " + anchor, 1)


def _strip_obsolete_ai_visual_gates(code: str) -> str:
    """Remove inherited AI-image-era validator errors that are invalid under V4."""
    lines = code.splitlines()
    cleaned = [line for line in lines if not any(token in line for token in OBSOLETE_AI_VISUAL_ERRORS)]
    code = "\n".join(cleaned)
    if LEGACY_AI_GATE_REMOVED not in code:
        marker = (
            f"// {LEGACY_AI_GATE_REMOVED}: visual_prompt/negative_prompt are legacy AI-generation fields; "
            "V4 validates visual_claim + semantic proof fields instead."
        )
        anchor = "const errors = [];"
        code = code.replace(anchor, anchor + "\n" + marker, 1) if anchor in code else marker + "\n" + code
    return code


def _expand_legacy_template_validator(code: str) -> str:
    """Extend the inherited three-template validator to every deterministic V4 renderer."""
    old_spec = 'const spec = {"stat_reveal":["statValue","label"],"comparison":["leftLabel","leftValue","rightLabel","rightValue"],"kinetic_text":["line"]}[s.template_name];'
    new_spec = 'const spec = {"stat_reveal":["statValue","label"],"comparison":["leftLabel","leftValue","rightLabel","rightValue"],"kinetic_text":["line"],"diagram":["nodes","edges"],"timeline":["events"],"map":["locations"]}[s.template_name];'
    if old_spec in code:
        code = code.replace(old_spec, new_spec, 1)
    elif new_spec not in code:
        raise ValueError("legacy template validator spec changed; V4 deterministic templates may be rejected")

    old_error = 'errors.push(`scene ${s.scene_index} has unknown template_name "${s.template_name}" - must be one of stat_reveal, comparison, kinetic_text`);'
    new_error = 'errors.push(`scene ${s.scene_index} has unknown template_name "${s.template_name}" - must be one of stat_reveal, comparison, kinetic_text, diagram, timeline, map`);'
    if old_error in code:
        code = code.replace(old_error, new_error, 1)
    elif new_error not in code:
        raise ValueError("legacy template validator error whitelist changed; V4 deterministic templates may be rejected")

    if LEGACY_TEMPLATE_VALIDATOR_EXPANDED not in code:
        anchor = new_spec
        code = code.replace(anchor, f"// {LEGACY_TEMPLATE_VALIDATOR_EXPANDED}\n" + anchor, 1)
    return code


def _patch_legacy_v2_template_normalizer(code: str) -> str:
    """Make the older V2 normalizer obey the authoritative V4 proof mode.

    The V2 normalizer used to recognize only stat_reveal/comparison/kinetic_text
    and silently rewrite map/timeline/diagram to kinetic_text before the V4 gate.
    It could also turn literal_video/literal_image/annotated_real into templates
    when stale visual_mode metadata said template_explainer. Both are derived
    routing decisions, so V4 proof mode must win deterministically without using
    a model repair attempt.
    """
    if "VISUAL_SOURCE_ROUTER_V2" not in code:
        return code
    if DETERMINISTIC_TEMPLATE_NORMALIZER in code and PROOF_MODE_ROUTER in code:
        return code

    old_helper = """const _vrValidTemplate=(obj,s)=>{
  if(!obj||typeof obj!=='object')return _vrKinetic(s);
  const name=String(obj.template_name||''); const d=obj.template_data&&typeof obj.template_data==='object'?obj.template_data:{};
  if(name==='stat_reveal'&&_vrClean(d.statValue,40)&&_vrClean(d.label,72))return {template_name:name,template_data:d};
  if(name==='comparison'&&_vrClean(d.leftLabel,48)&&_vrClean(d.leftValue,48)&&_vrClean(d.rightLabel,48)&&_vrClean(d.rightValue,48))return {template_name:name,template_data:d};
  if(name==='kinetic_text'&&_vrClean(d.line,120))return {template_name:name,template_data:d};
  return _vrKinetic(s);
};"""
    new_helper = f"""// {DETERMINISTIC_TEMPLATE_NORMALIZER}: V4 proof mode owns deterministic renderer selection.
// {PROOF_MODE_ROUTER}: real proof modes also override stale template metadata.
const _vrProofTemplate={{comparison:'comparison',number_visualization:'stat_reveal',kinetic_text:'kinetic_text',diagram:'diagram',timeline:'timeline',map:'map'}};
const _vrRealProofModes=new Set(['literal_video','literal_image','annotated_real']);
const _vrTemplateDataValid={{
  stat_reveal:(d)=>Boolean(_vrClean(d.statValue,40)&&_vrClean(d.label,72)),
  comparison:(d)=>Boolean(_vrClean(d.leftLabel,48)&&_vrClean(d.leftValue,48)&&_vrClean(d.rightLabel,48)&&_vrClean(d.rightValue,48)),
  kinetic_text:(d)=>Boolean(_vrClean(d.line,120)),
  diagram:(d)=>Array.isArray(d.nodes)&&d.nodes.length>=2&&Array.isArray(d.edges),
  timeline:(d)=>Array.isArray(d.events)&&d.events.length>=2,
  map:(d)=>Array.isArray(d.locations)&&d.locations.length>=1,
}};
const _vrValidTemplate=(obj,s)=>{{
  if(!obj||typeof obj!=='object')return _vrKinetic(s);
  const name=String(obj.template_name||''); const d=obj.template_data&&typeof obj.template_data==='object'?obj.template_data:{{}};
  if(_vrTemplateDataValid[name]&&_vrTemplateDataValid[name](d))return {{template_name:name,template_data:d}};
  // V4_PROOF_TEMPLATE_DATA_VALIDATED: the proof-mode-implied template must
  // still pass its OWN field checks against the actual template_data - a
  // template_name/data mismatch (e.g. visual_proof_mode='comparison' but the
  // model supplied incomplete/kinetic-shaped data) must fall through to the
  // deterministic kinetic_text fallback, never be returned as if it were valid.
  const expected=_vrProofTemplate[String(s&&s.visual_proof_mode||'')];
  if(expected&&_vrTemplateDataValid[expected]&&_vrTemplateDataValid[expected](d))return {{template_name:expected,template_data:d}};
  return _vrKinetic(s);
}};"""

    intermediate_helper = f"""// {DETERMINISTIC_TEMPLATE_NORMALIZER}: V4 proof mode owns deterministic renderer selection.
const _vrProofTemplate={{comparison:'comparison',number_visualization:'stat_reveal',kinetic_text:'kinetic_text',diagram:'diagram',timeline:'timeline',map:'map'}};
const _vrValidTemplate=(obj,s)=>{{
  if(!obj||typeof obj!=='object')return _vrKinetic(s);
  const name=String(obj.template_name||''); const d=obj.template_data&&typeof obj.template_data==='object'?obj.template_data:{{}};
  if(name==='stat_reveal'&&_vrClean(d.statValue,40)&&_vrClean(d.label,72))return {{template_name:name,template_data:d}};
  if(name==='comparison'&&_vrClean(d.leftLabel,48)&&_vrClean(d.leftValue,48)&&_vrClean(d.rightLabel,48)&&_vrClean(d.rightValue,48))return {{template_name:name,template_data:d}};
  if(name==='kinetic_text'&&_vrClean(d.line,120))return {{template_name:name,template_data:d}};
  if(name==='diagram'&&Array.isArray(d.nodes)&&d.nodes.length>=2&&Array.isArray(d.edges))return {{template_name:name,template_data:d}};
  if(name==='timeline'&&Array.isArray(d.events)&&d.events.length>=2)return {{template_name:name,template_data:d}};
  if(name==='map'&&Array.isArray(d.locations)&&d.locations.length>=1)return {{template_name:name,template_data:d}};
  const expected=_vrProofTemplate[String(s&&s.visual_proof_mode||'')];
  if(expected)return {{template_name:expected,template_data:d}};
  return _vrKinetic(s);
}};"""

    if old_helper in code:
        code = code.replace(old_helper, new_helper, 1)
    elif intermediate_helper in code:
        code = code.replace(intermediate_helper, new_helper, 1)
    elif PROOF_MODE_ROUTER not in code:
        raise ValueError("V2 template normalizer helper changed; refusing to leave V4 proof routing ambiguous")

    old_route = """  if(mode==='template_explainer'||s.visual_source==='template'){
    const chosen=_vrValidTemplate({template_name:s.template_name,template_data:s.template_data},s);
    s.visual_mode='template_explainer';s.visual_source='template';s.template_name=chosen.template_name;s.template_data=chosen.template_data;s.visual_type='template';
  }else{s.visual_source='stock';if(s.visual_type==='ai'||!s.visual_type)s.visual_type='real';s.template_fallback=_vrValidTemplate(s.template_fallback,s);}"""
    intermediate_route = """  const proofTemplate=_vrProofTemplate[String(s.visual_proof_mode||'')];
  if(proofTemplate){
    const chosen=_vrValidTemplate({template_name:proofTemplate,template_data:s.template_data},s);
    s.visual_mode='template_explainer';s.visual_source='template';s.template_name=proofTemplate;s.template_data=chosen.template_data;s.visual_type='template';
  }else if(mode==='template_explainer'||s.visual_source==='template'){
    const chosen=_vrValidTemplate({template_name:s.template_name,template_data:s.template_data},s);
    s.visual_mode='template_explainer';s.visual_source='template';s.template_name=chosen.template_name;s.template_data=chosen.template_data;s.visual_type='template';
  }else{s.visual_source='stock';if(s.visual_type==='ai'||!s.visual_type)s.visual_type='real';s.template_fallback=_vrValidTemplate(s.template_fallback,s);}"""
    new_route = """  const proofMode=String(s.visual_proof_mode||'');
  const proofTemplate=_vrProofTemplate[proofMode];
  if(proofTemplate){
    const chosen=_vrValidTemplate({template_name:proofTemplate,template_data:s.template_data},s);
    // If the requested proof-mode template's data was incomplete, _vrValidTemplate
    // downgrades to kinetic_text - keep visual_proof_mode in lockstep so the V4
    // contract gate's template_name/visual_proof_mode consistency check doesn't
    // reject a scene this normalizer just finished repairing.
    if(chosen.template_name!==proofTemplate)s.visual_proof_mode=chosen.template_name;
    s.visual_mode='template_explainer';s.visual_source='template';s.template_name=chosen.template_name;s.template_data=chosen.template_data;s.visual_type='template';
  }else if(_vrRealProofModes.has(proofMode)){
    if(mode==='template_explainer')mode=_vrClean(s.named_subject,80)?'exact_real':'context_real';
    s.visual_mode=mode;s.visual_source='stock';s.visual_type='real';s.template_fallback=_vrValidTemplate(s.template_fallback,s);
  }else if(mode==='template_explainer'||s.visual_source==='template'){
    const chosen=_vrValidTemplate({template_name:s.template_name,template_data:s.template_data},s);
    s.visual_mode='template_explainer';s.visual_source='template';s.template_name=chosen.template_name;s.template_data=chosen.template_data;s.visual_type='template';
  }else{s.visual_source='stock';if(s.visual_type==='ai'||!s.visual_type)s.visual_type='real';s.template_fallback=_vrValidTemplate(s.template_fallback,s);}"""

    if old_route in code:
        code = code.replace(old_route, new_route, 1)
    elif intermediate_route in code:
        code = code.replace(intermediate_route, new_route, 1)
    elif PROOF_MODE_ROUTER not in code:
        raise ValueError("V2 template routing block changed; refusing to leave V4 proof mode non-authoritative")
    return code


def _align_visual_prompt(body: str) -> str:
    """Remove the V2 three-template-only instruction that contradicts V4."""
    return body.replace(LEGACY_TEMPLATE_RULE, V4_TEMPLATE_RULE)


def patch_visual_director(workflow: dict) -> None:
    n = node(workflow, "Claude: Visual Director")
    body = str(n["parameters"]["jsonBody"])
    if MARKER not in body:
        quality_anchor = "PHASE 2 - RETRIEVABLE VISUALS"
        legacy_anchor = "For each stock scene, REQUIRED: add 3-4 DISTINCT concrete search_queries (2-5 words, at least 2 required) and set stock_search_query to the strongest - never leave a stock scene without both."
        if quality_anchor in body:
            body = _insert_once(body, quality_anchor, V4_INSTRUCTION, after=True)
        elif legacy_anchor in body:
            body = _insert_once(body, legacy_anchor, V4_INSTRUCTION, after=True)
        else:
            raise ValueError("Visual Director has neither QUALITY_ALIGNMENT nor creative-system V4 insertion anchor")
    body = _align_visual_prompt(body)

    if "OVERALL SELECTED TOPIC / MACRO CONTEXT" not in body:
        old_quality = 'SCRIPT: " + JSON.stringify($json.draft) + "\\n\\nCOMMISSIONING INTENT:'
        new_quality = 'SCRIPT: " + JSON.stringify($json.draft) + "\\n\\nOVERALL SELECTED TOPIC / MACRO CONTEXT: " + String($(\'Extract Generated Topic\').item.json.topic || \'\') + "\\n\\nCOMMISSIONING INTENT:'
        old_legacy = 'SCRIPT: " + JSON.stringify($json.draft) + "\\n\\nChoose one creative_format:'
        new_legacy = 'SCRIPT: " + JSON.stringify($json.draft) + "\\n\\nOVERALL SELECTED TOPIC / MACRO CONTEXT: " + String($(\'Extract Generated Topic\').item.json.topic || \'\') + "\\n\\nChoose one creative_format:'
        if old_quality in body:
            body = body.replace(old_quality, new_quality, 1)
        elif old_legacy in body:
            body = body.replace(old_legacy, new_legacy, 1)
        else:
            raise ValueError("Visual Director macro-context SCRIPT anchor missing")
    n["parameters"]["jsonBody"] = body

    try:
        repair = node(workflow, "Claude: Repair Script")
        repair_body = str(repair["parameters"].get("jsonBody", ""))
        if repair_body and MARKER not in repair_body:
            if "PHASE 2 - RETRIEVABLE VISUALS" in repair_body:
                repair_body = _insert_once(repair_body, "PHASE 2 - RETRIEVABLE VISUALS", V4_INSTRUCTION, after=True)
            elif "PHASE 1 - QUALITY CHECK" in repair_body:
                repair_body = _insert_once(repair_body, "PHASE 1 - QUALITY CHECK", V4_INSTRUCTION, after=False)
        repair["parameters"]["jsonBody"] = _align_visual_prompt(repair_body)
    except KeyError:
        pass


def patch_validator(workflow: dict) -> None:
    n = node(workflow, "Validate Final Script")
    code = _strip_obsolete_ai_visual_gates(str(n["parameters"]["jsCode"]))
    code = _expand_legacy_template_validator(code)
    code = _patch_legacy_v2_template_normalizer(code)
    n["parameters"]["jsCode"] = code
    if CONTRACT_GATE in code:
        return
    anchor = "// Medical/health exclusion backstop"
    if anchor not in code:
        anchor = "const errors = [];"
        before = False
    else:
        before = True
    gate = r'''// VISUAL_MATCHING_V4 contract gate.
const proofModes=['literal_video','literal_image','annotated_real','comparison','number_visualization','kinetic_text','diagram','timeline','map'];
const deterministicModes=['comparison','number_visualization','kinetic_text','diagram','timeline','map'];
const expectedTemplate={comparison:'comparison',number_visualization:'stat_reveal',kinetic_text:'kinetic_text',diagram:'diagram',timeline:'timeline',map:'map'};
if(Array.isArray(parsed.scenes)){
const remotionTemplateIndexes=parsed.scenes.filter(s=>s&&!s?.template_data?.is_outro&&(s.visual_source==='template'||deterministicModes.includes(s.visual_proof_mode))).map(s=>Number(s.scene_index));
if(remotionTemplateIndexes.length>1)errors.push(`video uses ${remotionTemplateIndexes.length} Remotion template scenes (${remotionTemplateIndexes.join(',')}); maximum is 1`);
if(remotionTemplateIndexes.includes(0))errors.push('scene 0 cannot be a Remotion template; the opening visual must be real/archive footage or imagery');
parsed.scenes.forEach((s,i)=>{
  if(s?.template_data?.is_outro)return;
  if(!s.visual_claim||String(s.visual_claim).trim().length<8)errors.push(`scene ${s.scene_index} missing visual_claim`);
  if(!Array.isArray(s.required_entities))errors.push(`scene ${s.scene_index} missing required_entities array`);
  if(!Array.isArray(s.required_actions))errors.push(`scene ${s.scene_index} missing required_actions array`);
  if(!Array.isArray(s.required_relationships))errors.push(`scene ${s.scene_index} missing required_relationships array`);
  if(!Array.isArray(s.forbidden_visuals)||s.forbidden_visuals.length<1)errors.push(`scene ${s.scene_index} missing forbidden_visuals`);
  if(!Array.isArray(s.acceptable_visuals))errors.push(`scene ${s.scene_index} missing acceptable_visuals array`);
  if(!proofModes.includes(s.visual_proof_mode))errors.push(`scene ${s.scene_index} invalid visual_proof_mode=${s.visual_proof_mode}`);
  if(deterministicModes.includes(s.visual_proof_mode)){
    if(s.visual_source!=='template')errors.push(`scene ${s.scene_index} proof mode ${s.visual_proof_mode} must use deterministic template`);
    if(s.template_name!==expectedTemplate[s.visual_proof_mode])errors.push(`scene ${s.scene_index} proof mode ${s.visual_proof_mode} requires template_name=${expectedTemplate[s.visual_proof_mode]}`);
  }
  if(s.visual_proof_mode==='annotated_real'&&s.visual_source==='template')errors.push(`scene ${s.scene_index} annotated_real must retrieve a verified real image before template conversion`);
  if(s.visual_proof_mode==='literal_video'&&s.visual_source==='template')errors.push(`scene ${s.scene_index} literal_video cannot be represented by a template`);
  if(s.visual_source!=='template'&&Array.isArray(s.required_actions)&&s.required_actions.length>0&&s.visual_proof_mode==='literal_image')errors.push(`scene ${s.scene_index} requires visible action but is routed as literal_image`);
  if(s.visual_proof_mode==='map'){
    const locs=s?.template_data?.locations;
    if(!Array.isArray(locs)||locs.length<1)errors.push(`scene ${s.scene_index} map requires template_data.locations`);
    else{
      if(locs.length>8)errors.push(`scene ${s.scene_index} map supports at most 8 locations`);
      const labels=new Set();
      locs.forEach((p,j)=>{
        const label=String(p?.label||'').trim();const key=label.toLowerCase();
        if(!label||!Number.isFinite(Number(p?.lat))||!Number.isFinite(Number(p?.lon))||Number(p.lat)<-90||Number(p.lat)>90||Number(p.lon)<-180||Number(p.lon)>180)errors.push(`scene ${s.scene_index} map location ${j} invalid`);
        if(label&&labels.has(key))errors.push(`scene ${s.scene_index} map location ${j} duplicates label ${label}`);
        if(label)labels.add(key);
      });
      const conns=s?.template_data?.connections;
      if(conns!==undefined&&!Array.isArray(conns))errors.push(`scene ${s.scene_index} map connections must be an array`);
      if(Array.isArray(conns)){
        if(conns.length>8)errors.push(`scene ${s.scene_index} map supports at most 8 connections`);
        conns.forEach((c,j)=>{const from=String(c?.from||'').trim().toLowerCase();const to=String(c?.to||'').trim().toLowerCase();if(!from||!to||!labels.has(from)||!labels.has(to))errors.push(`scene ${s.scene_index} map connection ${j} must reference existing location labels`);});
      }
    }
  }
  if(s.visual_proof_mode==='timeline'){
    const events=s?.template_data?.events;
    if(!Array.isArray(events)||events.length<2)errors.push(`scene ${s.scene_index} timeline requires at least two events`);
    else{
      if(events.length>7)errors.push(`scene ${s.scene_index} timeline supports at most 7 events`);
      events.forEach((e,j)=>{if(!String(e?.date||'').trim()||!String(e?.label||'').trim())errors.push(`scene ${s.scene_index} timeline event ${j} requires date and label`);});
    }
  }
  if(s.visual_proof_mode==='diagram'){
    const nodes=s?.template_data?.nodes;const edges=s?.template_data?.edges;
    if(!Array.isArray(nodes)||nodes.length<2)errors.push(`scene ${s.scene_index} diagram requires at least two nodes`);
    else{
      if(nodes.length>8)errors.push(`scene ${s.scene_index} diagram supports at most 8 nodes`);
      const ids=new Set();
      nodes.forEach((n,j)=>{const id=String(n?.id||'').trim();const label=String(n?.label||'').trim();if(!id||!label)errors.push(`scene ${s.scene_index} diagram node ${j} requires id and label`);if(id&&ids.has(id))errors.push(`scene ${s.scene_index} diagram node ${j} duplicates id ${id}`);if(id)ids.add(id);});
      if(!Array.isArray(edges)||edges.length<1)errors.push(`scene ${s.scene_index} diagram requires at least one edge`);
      else{
        if(edges.length>12)errors.push(`scene ${s.scene_index} diagram supports at most 12 edges`);
        edges.forEach((e,j)=>{const from=String(e?.from||'').trim();const to=String(e?.to||'').trim();if(!from||!to||!ids.has(from)||!ids.has(to))errors.push(`scene ${s.scene_index} diagram edge ${j} must reference existing node ids`);});
      }
    }
  }
});}

'''
    if anchor not in code:
        raise ValueError("validator V4 gate anchor missing")
    if before:
        code = code.replace(anchor, gate + anchor, 1)
    else:
        code = code.replace(anchor, anchor + "\n" + gate, 1)
    n["parameters"]["jsCode"] = code


def contract_expr(prefix: str = "$('Split Out Scenes').item.json") -> str:
    p = prefix
    return (
        f"narration: ({p}.narration || ''), point: ({p}.point || ''), global_subject: ({p}.global_subject || $('Extract Generated Topic').item.json.topic || ''), "
        f"visual_claim: ({p}.visual_claim || {p}.must_show || {p}.visual_prompt || ''), required_entities: ({p}.required_entities || []), required_actions: ({p}.required_actions || []), "
        f"required_relationships: ({p}.required_relationships || []), forbidden_visuals: ({p}.forbidden_visuals || []), acceptable_visuals: ({p}.acceptable_visuals || {p}.acceptable_substitutes || []), "
        f"visual_proof_mode: ({p}.visual_proof_mode || ''), prefer_template: ['comparison','number_visualization','kinetic_text','diagram','timeline','map'].includes({p}.visual_proof_mode), "
        f"template_fallback: ({p}.template_fallback || null)"
    )


def patch_resolver(workflow: dict) -> None:
    r = node(workflow, "Resolve B-roll")
    p = "$('Split Out Scenes').item.json"
    body = (
        "={{ JSON.stringify({ query: (" + p + ".stock_search_query || " + p + ".visual_prompt), queries: (" + p + ".search_queries || []), "
        "alternate_queries: (" + p + ".search_queries || []).slice(1), subject: (" + p + ".named_subject || ''), description: (" + p + ".visual_prompt || " + p + ".must_show || " + p + ".stock_search_query), "
        "scene_index: " + p + ".scene_index, first_frame: Number(" + p + ".scene_index) === 0, creative_format: ($('Validate Final Script').item.json.creative_format || ''), "
        "planned_template_count: ($('Validate Final Script').item.json.scenes || []).filter((sc) => sc && sc.visual_source === 'template' && sc.template_name !== 'annotated_real').length, "
        + contract_expr(p) + ", run_id: String($execution.id || '') }) }}"
    )
    r["parameters"]["jsonBody"] = body
    r["parameters"].setdefault("options", {})["timeout"] = 180000

    tag = node(workflow, "Tag B-roll")
    tag["parameters"]["jsCode"] = r'''const r=$json;const s=$('Split Out Scenes').item.json;const sceneIndex=s.scene_index;
if(!r||r.ok!==true||!r.url)throw new Error('B-roll commissioning rejected scene '+sceneIndex+': '+(r?.reason||'no asset')+' best_score='+(r?.best_score??'n/a')+' threshold='+(r?.threshold??'n/a')+' proof_mode='+(r?.recommended_visual_proof_mode||s.visual_proof_mode||'n/a'));
const out={scene_index:sceneIndex,point:s.point,narration:s.narration,visual_prompt:s.visual_prompt,named_subject:s.named_subject||'',visual_claim:s.visual_claim||s.must_show||s.visual_prompt||'',global_subject:s.global_subject||$('Extract Generated Topic').item.json.topic||'',required_entities:s.required_entities||[],required_actions:s.required_actions||[],required_relationships:s.required_relationships||[],forbidden_visuals:s.forbidden_visuals||[],acceptable_visuals:s.acceptable_visuals||s.acceptable_substitutes||[],visual_proof_mode:s.visual_proof_mode||r.recommended_visual_proof_mode||'',_source:r.source||'broll',_attribution:r.attribution||'',asset_score:r.score??null,asset_semantic_match:r.semantic_match??null,asset_entity_match:r.entity_match??null,asset_action_match:r.action_match??null,asset_relationship_match:r.relationship_match??null,asset_local_similarity:r.local_similarity??null,asset_frame_similarity:r.frame_similarity??null,frame_similarity:r.frame_similarity??null,frame_sampling_status:r.frame_sampling_status||null,asset_scroll_stop:r.scroll_stop??null,asset_mobile_clarity:r.mobile_clarity??null,selected_query:r.selected_query||null,actual_video_verified:r.actual_video_verified===true,in_point_sec:r.in_point_sec??null,out_point_sec:r.out_point_sec??null,verified_frame_indices:r.verified_frame_indices||null,annotation_plan:r.annotation_plan||null,library_hit:r.library_hit===true};
if(out.visual_proof_mode==='annotated_real'){
  if(r.type!=='image'||!Array.isArray(r.annotation_plan)||r.annotation_plan.length<1)throw new Error('annotated_real scene '+sceneIndex+' lacks a verified image/callout plan');
  out.visual_source='template';out.template_name='annotated_real';out.template_data={imageUrl:r.url,imageWidth:r.width||1080,imageHeight:r.height||1920,title:s?.template_data?.title||s.visual_claim||s.point||'',annotations:r.annotation_plan};
}else if(r.type==='video')out.video_url=r.url;else out.images=[r.url];return {json:out};'''

    template = node(workflow, "Tag Template Video")
    template["parameters"]["jsCode"] = r'''const s=$('Split Out Scenes').item.json;return {json:{scene_index:s.scene_index,point:s.point,narration:s.narration,visual_source:'template',template_name:s.template_name,template_data:s.template_data,visual_prompt:s.visual_prompt||'',named_subject:s.named_subject||'',visual_claim:s.visual_claim||s.must_show||s.point||s.narration||'',global_subject:s.global_subject||$('Extract Generated Topic').item.json.topic||'',required_entities:s.required_entities||[],required_actions:s.required_actions||[],required_relationships:s.required_relationships||[],forbidden_visuals:s.forbidden_visuals||[],acceptable_visuals:s.acceptable_visuals||s.acceptable_substitutes||[],visual_proof_mode:s.visual_proof_mode||''}};'''


def patch_merge(workflow: dict) -> None:
    n = node(workflow, "Merge By scene_index (not position)")
    code = str(n["parameters"]["jsCode"])
    if "asset_semantic_match: v.asset_semantic_match" in code:
        return
    common = "point: v.point, narration: v.narration, visual_prompt: v.visual_prompt, named_subject: v.named_subject, visual_claim: v.visual_claim, global_subject: v.global_subject, required_entities: v.required_entities, required_actions: v.required_actions, required_relationships: v.required_relationships, forbidden_visuals: v.forbidden_visuals, acceptable_visuals: v.acceptable_visuals, visual_proof_mode: v.visual_proof_mode, asset_semantic_match: v.asset_semantic_match, asset_entity_match: v.asset_entity_match, asset_action_match: v.asset_action_match, asset_relationship_match: v.asset_relationship_match, asset_local_similarity: v.asset_local_similarity, asset_frame_similarity: v.asset_frame_similarity, actual_video_verified: v.actual_video_verified, in_point_sec: v.in_point_sec, out_point_sec: v.out_point_sec, verified_frame_indices: v.verified_frame_indices, annotation_plan: v.annotation_plan, library_hit: v.library_hit,"
    stock_extra = "visual_source: v.visual_source, template_name: v.template_name, template_data: v.template_data, " + common
    anchors = [
        ("    selected_query: v.selected_query,\n    audio: match.audio,", "    selected_query: v.selected_query,\n    " + stock_extra + "\n    audio: match.audio,"),
        ("    template_data: v.template_data,\n    audio: match.audio,", "    template_data: v.template_data,\n    " + common + "\n    audio: match.audio,"),
    ]
    for old, new in anchors:
        if old in code:
            n["parameters"]["jsCode"] = code.replace(old, new, 1)
            return
    raise ValueError("merge visual metadata anchor missing")


def upgrade(workflow: dict) -> dict:
    patch_visual_director(workflow)
    patch_validator(workflow)
    patch_resolver(workflow)
    patch_merge(workflow)
    workflow.setdefault("meta", {})["visual_matching_version"] = "4"
    return workflow


def assert_applied(workflow: dict) -> None:
    if workflow.get("meta", {}).get("visual_matching_version") != "4":
        raise RuntimeError("V4 workflow marker missing")
    names = {n.get("name"): n for n in workflow.get("nodes", [])}
    visual = str(names["Claude: Visual Director"]["parameters"]["jsonBody"])
    for marker in [MARKER, "visual_claim", "required_entities", "required_actions", "required_relationships", "forbidden_visuals", "visual_proof_mode", "OVERALL SELECTED TOPIC / MACRO CONTEXT", "map=>template + map", "annotated_real", "REMOTION TEMPLATE CAP"]:
        if marker not in visual:
            raise RuntimeError(f"V4 Visual Director contract missing {marker}")
    if LEGACY_TEMPLATE_RULE in visual:
        raise RuntimeError("V4 Visual Director still limits template_explainer to three legacy renderers")
    validate = str(names["Validate Final Script"]["parameters"]["jsCode"])
    for marker in [CONTRACT_GATE, LEGACY_AI_GATE_REMOVED, LEGACY_TEMPLATE_VALIDATOR_EXPANDED, "deterministicModes", "template_data.locations", "timeline requires at least two events", "diagram requires at least two nodes", "map supports at most 8 locations", "map supports at most 8 connections", "timeline supports at most 7 events", "diagram supports at most 8 nodes", "diagram supports at most 12 edges", "must reference existing location labels", "must reference existing node ids", "maximum is 1", "scene 0 cannot be a Remotion template"]:
        if marker not in validate:
            raise RuntimeError(f"V4 validator gate missing {marker}")
    if "VISUAL_SOURCE_ROUTER_V2" in validate:
        if DETERMINISTIC_TEMPLATE_NORMALIZER not in validate:
            raise RuntimeError("V2 runtime normalizer can still downgrade V4 deterministic templates")
        if PROOF_MODE_ROUTER not in validate or "_vrRealProofModes" not in validate:
            raise RuntimeError("V2 runtime normalizer can still override V4 real-media proof modes")
    legacy_unknown = 'must be one of stat_reveal, comparison, kinetic_text`'
    if legacy_unknown in validate:
        raise RuntimeError("legacy validator still rejects V4 diagram/timeline/map templates")
    for obsolete in OBSOLETE_AI_VISUAL_ERRORS:
        if obsolete in validate:
            raise RuntimeError(f"V4 validator still contains obsolete AI-generation requirement: {obsolete}")
    resolver = str(names["Resolve B-roll"]["parameters"]["jsonBody"])
    for marker in ["visual_claim", "required_entities", "required_actions", "required_relationships", "forbidden_visuals", "visual_proof_mode", "template_fallback", "run_id", "$execution.id"]:
        if marker not in resolver:
            raise RuntimeError(f"V4 resolver payload missing {marker}")
    tag = str(names["Tag B-roll"]["parameters"]["jsCode"])
    for marker in ["annotated_real", "annotation_plan", "verified_frame_indices", "template_name='annotated_real'"]:
        if marker not in tag:
            raise RuntimeError(f"V4 B-roll tag missing {marker}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: visual_matching_v4_workflow.py INPUT OUTPUT")
    src, dst = map(Path, sys.argv[1:])
    workflow = json.loads(src.read_text(encoding="utf-8"))
    upgraded = upgrade(workflow)
    assert_applied(upgraded)
    dst.write_text(json.dumps(upgraded, indent=2) + "\n", encoding="utf-8")
    print(f"{MARKER} workflow written to {dst}")


if __name__ == "__main__":
    main()
