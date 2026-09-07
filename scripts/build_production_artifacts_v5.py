#!/usr/bin/env python3
"""Build the exact production workflow + compose artifacts in one deterministic pass.

CI and deploy both consume the artifacts emitted here. The build also rewrites
all external LLM HTTP nodes to the Docker-internal llm-gateway. Provider choice
is then a runtime policy (FreeLLMAPI by default, direct providers as rollback),
so changing providers never requires editing the n8n workflow itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

BUILD_VERSION = "5"
INTERNAL_SERVICE_ORIGIN = "http://shorts-compose:4000"
PUBLIC_SERVICE_ORIGIN = "https://shorts.interviewbuddy.cloud"
LLM_GATEWAY_ORIGIN = "http://llm-gateway:3100"
REAL_MEDIA_MIX_GUARD = "V5_REAL_MEDIA_MIX_GUARD"


def run(*args: str, cwd: Path) -> None:
    subprocess.run([sys.executable, *args], cwd=str(cwd), check=True)


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise KeyError(f"required workflow node not found: {name}")


def internalize_compose_service_urls(workflow: dict) -> None:
    """Keep n8n->compose traffic on the Docker network, never the public proxy."""
    for node in workflow.get("nodes", []):
        params = node.get("parameters", {})
        value = params.get("url")
        if isinstance(value, str) and PUBLIC_SERVICE_ORIGIN in value:
            params["url"] = value.replace(PUBLIC_SERVICE_ORIGIN, INTERNAL_SERVICE_ORIGIN)


def route_llm_http_nodes(workflow: dict) -> int:
    """Route every first-party LLM HTTP request through our reversible gateway.

    The gateway owns credentials and provider selection. n8n therefore never
    needs FreeLLMAPI/provider keys and can switch between free-first and direct
    mode by changing one runtime env var on the gateway/compose services.
    """
    routed = 0
    targets = (
        ("https://api.openai.com/v1/chat/completions", f"{LLM_GATEWAY_ORIGIN}/v1/chat/completions"),
        ("https://api.openai.com/v1/responses", f"{LLM_GATEWAY_ORIGIN}/v1/responses"),
        ("https://api.anthropic.com/v1/messages", f"{LLM_GATEWAY_ORIGIN}/v1/messages"),
    )
    for node in workflow.get("nodes", []):
        params = node.get("parameters", {})
        value = params.get("url")
        if not isinstance(value, str):
            continue
        replacement = None
        for external, internal in targets:
            if value.startswith(external):
                replacement = value.replace(external, internal, 1)
                break
        if replacement is None:
            continue

        params["url"] = replacement
        params.pop("authentication", None)
        params.pop("genericAuthType", None)
        node.pop("credentials", None)

        # Preserve content-type and any non-secret functional headers while
        # making sure provider credentials cannot leak to the local gateway.
        hp = params.get("headerParameters", {}).get("parameters", [])
        if isinstance(hp, list):
            params.setdefault("headerParameters", {})["parameters"] = [
                h for h in hp
                if str(h.get("name", "")).lower() not in {"authorization", "x-api-key"}
            ]
        routed += 1

    workflow.setdefault("meta", {})["llm_gateway_transport"] = "docker_internal"
    workflow["meta"]["llm_gateway_routed_nodes"] = routed
    return routed


def clean_visual_director_annotation_contract(workflow: dict) -> None:
    """Redefine annotated_real as a clean verified still, not a CV overlay task."""
    visual = node_by_name(workflow, "Claude: Visual Director")
    body = str(visual.get("parameters", {}).get("jsonBody", ""))
    legacy = (
        "annotated_real MUST remain a real-media retrieval scene, not a template at planning time: "
        "the resolver will select the exact real image, visually locate callout boxes on those pixels, "
        "then convert it into the annotated_real Remotion composition."
    )
    replacement = (
        "annotated_real MUST remain a real-media retrieval scene, not a template at planning time: "
        "the resolver selects and visually verifies the exact real image, then the renderer presents that verified image cleanly. "
        "Do NOT request callout boxes, labels, coordinates, arrows, diagnostic overlays, or explanatory UI for annotated_real."
    )
    if legacy in body:
        body = body.replace(legacy, replacement, 1)
    body = body.replace(
        "visually locate callout boxes on those pixels, then convert it into the annotated_real Remotion composition",
        "visually verify those pixels, then render the real image cleanly without diagnostic overlays",
    )
    visual["parameters"]["jsonBody"] = body


def enforce_real_media_mix(workflow: dict) -> None:
    """Make real footage/imagery an episode invariant, independent of LLM quality.

    FreeLLMAPI can route planning to different model families. A weaker planner
    may ignore the prompt-level one-template cap and mark every scene as
    kinetic_text/timeline/etc. The final validator therefore repairs that model
    output deterministically before its existing contract checks run:

    * the opening content scene is always real-media retrieval;
    * at most one non-outro scene may remain a deterministic template;
    * excess template scenes are converted to literal video when they require a
      visible action, otherwise literal imagery;
    * converted scenes get deterministic 2-5-word retrieval queries so the
      existing stock-search validator cannot bounce them back to the model;
    * the original graphic is retained only as a resolver fallback candidate,
      subject to the existing one-fallback/never-scene-0 budget policy.
    """
    visual = node_by_name(workflow, "Claude: Visual Director")
    body = str(visual.get("parameters", {}).get("jsonBody", ""))
    prompt_anchor = (
        "REMOTION TEMPLATE CAP: across the non-outro video, at most ONE scene may use visual_source=template "
        "or a deterministic Remotion proof mode, and it must NEVER be scene_index 0. Scene 0 must be real/archive footage or imagery. "
    )
    real_media_rule = (
        "REAL-MEDIA MAJORITY: scene 0 and all but at most ONE non-outro content scene MUST use real/archive retrieval. "
        "Never use kinetic_text, timeline, map, diagram, comparison, or number_visualization merely as a convenient substitute when a real photo/video can carry the beat. "
        "Famous or historical people, places, objects, and events should default to real/archive retrieval. "
    )
    if real_media_rule not in body:
        if prompt_anchor not in body:
            raise RuntimeError("Visual Director template-cap anchor missing; cannot install real-media majority rule")
        body = body.replace(prompt_anchor, prompt_anchor + real_media_rule, 1)
    visual["parameters"]["jsonBody"] = body

    validator = node_by_name(workflow, "Validate Final Script")
    code = str(validator.get("parameters", {}).get("jsCode", ""))
    if REAL_MEDIA_MIX_GUARD not in code:
        anchor = "// VISUAL_MATCHING_V4 contract gate."
        if anchor not in code:
            raise RuntimeError("final validator lost VISUAL_MATCHING_V4 gate; cannot install real-media mix guard")
        guard = r'''// V5_REAL_MEDIA_MIX_GUARD: the episode visual mix is a deterministic runtime contract, not an LLM suggestion.
const _vmTemplateModes=new Set(['comparison','number_visualization','kinetic_text','diagram','timeline','map']);
const _vmTemplateNames=new Set(['comparison','stat_reveal','kinetic_text','diagram','timeline','map']);
const _vmTemplatePriority={timeline:60,map:55,diagram:50,comparison:45,number_visualization:40,kinetic_text:20};
const _vmIsOutro=(s)=>Boolean(s?.template_data?.is_outro);
const _vmIsTemplate=(s)=>Boolean(s&&!_vmIsOutro(s)&&(s.visual_source==='template'||_vmTemplateModes.has(String(s.visual_proof_mode||''))));
const _vmQuery=(value)=>String(value||'').toLowerCase().replace(/[^a-z0-9' -]+/g,' ').replace(/\s+/g,' ').trim().split(' ').filter(Boolean).slice(0,5).join(' ');
const _vmEnsureQueries=(s)=>{
  const queries=[];const seen=new Set();
  const add=(value)=>{const q=_vmQuery(value);const words=q.split(/\s+/).filter(Boolean);if(words.length<2||words.length>5||seen.has(q))return;seen.add(q);queries.push(q);};
  (Array.isArray(s.search_queries)?s.search_queries:[]).forEach(add);
  add(s.stock_search_query);
  const subject=String(s.named_subject||s.global_subject||s.point||'').trim();
  const action=Array.isArray(s.required_actions)&&s.required_actions.length?String(s.required_actions[0]||'').trim():'';
  const entity=Array.isArray(s.required_entities)&&s.required_entities.length?String(s.required_entities[0]||'').trim():'';
  add([subject,action].filter(Boolean).join(' '));
  add([subject,entity].filter(Boolean).join(' '));
  add(s.visual_claim);
  add(s.narration);
  add(`${subject||'subject'} real footage`);
  add(`${subject||'subject'} archival image`);
  add(`${subject||'subject'} documentary view`);
  s.search_queries=queries.slice(0,4);
  if(s.search_queries.length<3){
    ['real world footage','authentic archival image','documentary close view'].forEach(add);
    s.search_queries=queries.slice(0,4);
  }
  s.stock_search_query=s.search_queries[0]||_vmQuery(`${subject||'subject'} real footage`);
  if(!String(s.visual_prompt||'').trim())s.visual_prompt=String(s.visual_claim||s.stock_search_query||subject).trim();
};
const _vmDemote=(s,reason)=>{
  const oldName=String(s?.template_name||'').trim();
  const oldData=s?.template_data&&typeof s.template_data==='object'&&!s.template_data.is_outro?s.template_data:null;
  if(oldName&&_vmTemplateNames.has(oldName)&&oldData&&!s.template_fallback)s.template_fallback={template_name:oldName,template_data:oldData};
  const needsMotion=Array.isArray(s?.required_actions)&&s.required_actions.length>0;
  s.visual_proof_mode=needsMotion?'literal_video':'literal_image';
  s.visual_source='stock';s.visual_type='real';s.visual_mode=String(s.named_subject||'').trim()?'exact_real':'context_real';
  delete s.template_name;delete s.template_data;
  s.visual_mix_repair_reason=reason;
  _vmEnsureQueries(s);
};
if(Array.isArray(parsed.scenes)){
  const _vmContent=parsed.scenes.filter(s=>s&&!_vmIsOutro(s)).sort((a,b)=>Number(a?.scene_index??9999)-Number(b?.scene_index??9999));
  // Any template with an invalid/non-deterministic proof mode is safer as real media.
  _vmContent.filter(s=>s?.visual_source==='template'&&!_vmTemplateModes.has(String(s?.visual_proof_mode||''))).forEach(s=>_vmDemote(s,'invalid_template_mode_forced_real_media'));
  const _vmOpening=_vmContent.find(s=>Number(s?.scene_index)===0)||_vmContent[0];
  if(_vmOpening&&_vmIsTemplate(_vmOpening))_vmDemote(_vmOpening,'opening_scene_forced_real_media');
  let _vmTemplates=_vmContent.filter(_vmIsTemplate);
  if(_vmTemplates.length>1){
    _vmTemplates.sort((a,b)=>(_vmTemplatePriority[String(b?.visual_proof_mode||'')]||0)-(_vmTemplatePriority[String(a?.visual_proof_mode||'')]||0)||Number(a?.scene_index??9999)-Number(b?.scene_index??9999));
    const _vmKeep=_vmTemplates[0];
    _vmTemplates.slice(1).forEach(s=>_vmDemote(s,`episode_template_cap_keep_scene_${Number(_vmKeep?.scene_index)}`));
  }
  // V5_ACTION_MODE_AUTOFIX: a scene that requires visible action can never be
  // literal_image, whether the model planned it that way from the start or a
  // demotion above just assigned it - fix the mismatch deterministically
  // instead of failing the whole script over a one-field inconsistency.
  _vmContent.filter(s=>!_vmIsTemplate(s)&&s?.visual_proof_mode==='literal_image'&&Array.isArray(s?.required_actions)&&s.required_actions.length>0).forEach(s=>{
    s.visual_proof_mode='literal_video';
    s.visual_mix_repair_reason=s.visual_mix_repair_reason||'action_required_forced_literal_video';
  });
  _vmTemplates=_vmContent.filter(_vmIsTemplate);
  const _vmReal=_vmContent.filter(s=>!_vmIsTemplate(s));
  parsed.visual_mix_guard={version:'1',planned_template_count:_vmTemplates.length,planned_real_media_count:_vmReal.length,repaired_scene_indexes:_vmContent.filter(s=>s.visual_mix_repair_reason).map(s=>Number(s.scene_index))};
}
'''
        code = code.replace(anchor, guard + "\n" + anchor, 1)
    validator["parameters"]["jsCode"] = code
    workflow.setdefault("meta", {})["real_media_mix_guard"] = "1"


def clean_tag_broll(workflow: dict) -> None:
    """Consume verified-real images and deterministic fallback templates."""
    tag = node_by_name(workflow, "Tag B-roll")
    tag["parameters"]["jsCode"] = r'''const r=$json;const s=$('Split Out Scenes').item.json;const sceneIndex=s.scene_index;
if(!r||r.ok!==true)throw new Error('B-roll commissioning rejected scene '+sceneIndex+': '+(r?.reason||'no usable asset')+' best_score='+(r?.best_score??r?.score??'n/a')+' threshold='+(r?.threshold??'n/a')+' proof_mode='+(r?.recommended_visual_proof_mode||s.visual_proof_mode||'n/a'));
const proofByTemplate={stat_reveal:'number_visualization',comparison:'comparison',kinetic_text:'kinetic_text',map:'map',timeline:'timeline',diagram:'diagram'};
const out={scene_index:sceneIndex,point:s.point,narration:s.narration,visual_prompt:s.visual_prompt,named_subject:s.named_subject||'',visual_claim:s.visual_claim||s.must_show||s.visual_prompt||'',global_subject:s.global_subject||$('Extract Generated Topic').item.json.topic||'',required_entities:s.required_entities||[],required_actions:s.required_actions||[],required_relationships:s.required_relationships||[],forbidden_visuals:s.forbidden_visuals||[],acceptable_visuals:s.acceptable_visuals||s.acceptable_substitutes||[],visual_proof_mode:s.visual_proof_mode||r.recommended_visual_proof_mode||'',_source:r.source||'broll',_attribution:r.attribution||'',asset_score:r.score??null,asset_semantic_match:r.semantic_match??null,asset_entity_match:r.entity_match??null,asset_action_match:r.action_match??null,asset_relationship_match:r.relationship_match??null,asset_local_similarity:r.local_similarity??null,asset_frame_similarity:r.frame_similarity??null,frame_similarity:r.frame_similarity??null,frame_sampling_status:r.frame_sampling_status||null,asset_scroll_stop:r.scroll_stop??null,asset_mobile_clarity:r.mobile_clarity??null,selected_query:r.selected_query||null,actual_video_verified:r.actual_video_verified===true,in_point_sec:r.in_point_sec??null,out_point_sec:r.out_point_sec??null,verified_frame_indices:r.verified_frame_indices||null,library_hit:r.library_hit===true,quality_gate_passed:r.quality_gate_passed??null,selection_reason:r.selection_reason||null};
if(r.type==='template'){
  if(!r.template_name||!r.template_data)throw new Error('deterministic fallback for scene '+sceneIndex+' is incomplete');
  out.visual_source='template';out.template_name=r.template_name;out.template_data=r.template_data;out.visual_proof_mode=proofByTemplate[r.template_name]||out.visual_proof_mode;return {json:out};
}
if(!r.url)throw new Error('B-roll resolver returned no media URL for scene '+sceneIndex);
if(out.visual_proof_mode==='annotated_real'){
  if(r.type!=='image')throw new Error('verified-real scene '+sceneIndex+' requires a verified still image');
  out.visual_source='template';out.template_name='annotated_real';out.template_data={imageUrl:r.url,imageWidth:r.width||1080,imageHeight:r.height||1920};
}else if(r.type==='video')out.video_url=r.url;else out.images=[r.url];return {json:out};'''


def replace_merge_node(workflow: dict) -> None:
    merge = node_by_name(workflow, "Merge By scene_index (not position)")
    merge["parameters"]["jsCode"] = r'''// Both aggregate branches arrive through a real Merge node, so this executes
// only after visuals and audio are complete. Join strictly by scene_index.
const allItems=$input.all().map(i=>i.json);
const visualAgg=allItems.find(i=>Array.isArray(i.data)&&i.data[0]&&(i.data[0].images||i.data[0].video_url||i.data[0].visual_source==='template'));
const audioAgg=allItems.find(i=>Array.isArray(i.data)&&i.data[0]&&i.data[0].audio);
if(!visualAgg)throw new Error('Could not find visual branch in merged input');
if(!audioAgg)throw new Error('Could not find audio branch in merged input');
const audios=audioAgg.data;
const merged=visualAgg.data.map(v=>{const match=audios.find(a=>a.scene_index===v.scene_index);if(!match)throw new Error(`No audio found for scene_index ${v.scene_index}`);return {
  scene_index:v.scene_index,images:v.images,video_url:v.video_url,source_duration:v.source_duration,source_width:v.source_width,source_height:v.source_height,
  visual_source:v.visual_source,template_name:v.template_name,template_data:v.template_data,point:v.point,narration:v.narration,visual_prompt:v.visual_prompt,named_subject:v.named_subject,
  visual_claim:v.visual_claim,global_subject:v.global_subject,required_entities:v.required_entities,required_actions:v.required_actions,required_relationships:v.required_relationships,
  forbidden_visuals:v.forbidden_visuals,acceptable_visuals:v.acceptable_visuals,visual_proof_mode:v.visual_proof_mode,asset_semantic_match:v.asset_semantic_match,asset_entity_match:v.asset_entity_match,
  asset_action_match:v.asset_action_match,asset_relationship_match:v.asset_relationship_match,asset_local_similarity:v.asset_local_similarity,asset_frame_similarity:v.asset_frame_similarity,
  actual_video_verified:v.actual_video_verified,in_point_sec:v.in_point_sec,out_point_sec:v.out_point_sec,verified_frame_indices:v.verified_frame_indices,library_hit:v.library_hit,
  quality_gate_passed:v.quality_gate_passed,selection_reason:v.selection_reason,selected_query:v.selected_query,_source:v._source,_attribution:v._attribution,audio:match.audio
};});
merged.sort((a,b)=>a.scene_index-b.scene_index);
const sceneCredits=merged.map(s=>String(s._attribution||'').replace(/\s+/g,' ').trim()).filter(Boolean);
const credits=[...new Set([...sceneCredits,'Motion icon assets: useanimations.com (CC BY 4.0)'])];
const baseDescription=[$('Validate Final Script').item.json.seo_description,$('Validate Final Script').item.json.full_script].filter(Boolean).join('\n\n');
const publicationDescription=baseDescription+'\n\nSources / credits:\n'+credits.map(c=>'• '+c).join('\n');
return {json:{hook:$('Validate Final Script').item.json.hook,comment_hook:$('Validate Final Script').item.json.comment_hook,full_script:$('Validate Final Script').item.json.full_script,caption_style:$('Validate Final Script').item.json.caption_style,publication_description:publicationDescription,data:merged}};'''


def patch_publication_metadata(workflow: dict) -> None:
    upload = node_by_name(workflow, "YouTube: Upload Draft")
    upload.setdefault("parameters", {}).setdefault("options", {})["description"] = "={{ $('Merge By scene_index (not position)').item.json.publication_description }}"
    disclosure = node_by_name(workflow, "Disclose AI-Generated Content")
    disclosure["parameters"]["jsonBody"] = r'''={{ JSON.stringify({ id: $json.uploadId || $json.id, status: { privacyStatus: $json.status?.privacyStatus || 'public', selfDeclaredMadeForKids: $json.status?.selfDeclaredMadeForKids ?? false, containsSyntheticMedia: true }, snippet: { title: (($('Validate Final Script').item.json.title || $('Validate Final Script').item.json.hook) || '').slice(0, 50), categoryId: '22', description: ($('Merge By scene_index (not position)').item.json.publication_description || ''), tags: ($('Validate Final Script').item.json.tags || []), defaultLanguage: 'en', defaultAudioLanguage: 'en' } }) }}'''


def add_resolver_topology(workflow: dict) -> None:
    resolver = node_by_name(workflow, "Resolve B-roll")
    body = str(resolver.get("parameters", {}).get("jsonBody", ""))
    if "retrieval_scene_count:" not in body and ", run_id:" in body:
        topology = ", retrieval_scene_count: ($('Validate Final Script').item.json.scenes || []).filter((sc) => sc && sc.visual_source !== 'template').length, retrieval_scene_position: ($('Validate Final Script').item.json.scenes || []).filter((sc) => sc && sc.visual_source !== 'template').findIndex((sc) => Number(sc.scene_index) === Number($('Split Out Scenes').item.json.scene_index))"
        body = body.replace(", run_id:", topology + ", run_id:", 1)
    resolver["parameters"]["jsonBody"] = body
    resolver["parameters"].setdefault("options", {})["timeout"] = 160000


def postprocess_workflow(path: Path) -> None:
    workflow = json.loads(path.read_text())
    internalize_compose_service_urls(workflow)
    clean_visual_director_annotation_contract(workflow)
    enforce_real_media_mix(workflow)
    clean_tag_broll(workflow)
    replace_merge_node(workflow)
    patch_publication_metadata(workflow)
    add_resolver_topology(workflow)
    routed = route_llm_http_nodes(workflow)
    if routed < 1:
        raise RuntimeError("production workflow contains no routed LLM nodes; provider routing contract was lost")
    workflow.setdefault("meta", {})["production_build_version"] = BUILD_VERSION
    workflow["meta"]["compose_service_transport"] = "docker_internal"
    path.write_text(json.dumps(workflow, indent=2) + "\n")


def build(root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)

    # Workflow migration pipeline: one authoritative order.
    w0 = root / "n8n" / "workflow.json"
    stages = [output / f"workflow-stage-{i}.json" for i in range(1, 7)]
    run("scripts/upgrade-viral-shorts.py", str(w0), str(stages[0]), cwd=root)
    run("scripts/upgrade-creative-system.py", str(stages[0]), str(stages[1]), cwd=root)
    run("scripts/visual_matching_v4_workflow.py", str(stages[1]), str(stages[2]), cwd=root)
    run("scripts/upgrade-workflow-api-budget.py", str(stages[2]), str(stages[3]), cwd=root)
    run("scripts/upgrade-topic-latency.py", str(stages[3]), str(stages[4]), cwd=root)
    run("scripts/upgrade-anthropic-parser.py", str(stages[4]), str(stages[5]), cwd=root)
    workflow_out = output / "workflow.json"
    shutil.copy2(stages[5], workflow_out)
    postprocess_workflow(workflow_out)

    # Compose/resolver pipeline. Legacy runtime-hardening rewrites are no longer
    # re-applied to the V5 resolver; resolver_v5_runtime owns the small runtime
    # mechanics and final always-publish selection policy.
    compose_out = output / "compose.js"
    resolver_out = output / "brollResolver.js"
    shutil.copy2(root / "shorts-compose" / "compose.js", compose_out)
    shutil.copy2(root / "shorts-compose" / "brollResolver.js", resolver_out)
    run("scripts/upgrade-compose-creative-system.py", str(compose_out), cwd=root)
    run("scripts/upgrade-compose-api-budget.py", str(compose_out), str(resolver_out), cwd=root)
    run("scripts/resolver_v5_runtime.py", str(resolver_out), cwd=root)
    run("scripts/visual_matching_v4_compose.py", str(compose_out), cwd=root)

    workflow = json.loads(workflow_out.read_text())
    tag_code = node_by_name(workflow, "Tag B-roll")["parameters"]["jsCode"]
    merge_code = node_by_name(workflow, "Merge By scene_index (not position)")["parameters"]["jsCode"]
    resolver_url = str(node_by_name(workflow, "Resolve B-roll")["parameters"].get("url", ""))
    visual_body = str(node_by_name(workflow, "Claude: Visual Director")["parameters"].get("jsonBody", ""))
    validator_code = str(node_by_name(workflow, "Validate Final Script")["parameters"].get("jsCode", ""))
    if workflow.get("meta", {}).get("real_media_mix_guard") != "1":
        raise RuntimeError("generated workflow lost real-media mix guard metadata")
    for marker in (REAL_MEDIA_MIX_GUARD, "opening_scene_forced_real_media", "episode_template_cap_keep_scene_", "planned_real_media_count", "_vmEnsureQueries"):
        if marker not in validator_code:
            raise RuntimeError(f"final validator missing real-media mix guard marker: {marker}")
    if "REAL-MEDIA MAJORITY" not in visual_body:
        raise RuntimeError("Visual Director lost explicit real-media majority instruction")
    if "annotation_plan" in tag_code or "annotations:" in tag_code:
        raise RuntimeError("production Tag B-roll still exposes legacy VLM annotations")
    if "visually locate callout boxes" in visual_body or "locate callout boxes on those pixels" in visual_body:
        raise RuntimeError("production Visual Director still requests CV callout geometry")
    if "Do NOT request callout boxes" not in visual_body:
        raise RuntimeError("verified-real clean-frame contract missing from Visual Director")
    if "deterministic fallback" not in tag_code or "r.type==='template'" not in tag_code:
        raise RuntimeError("production Tag B-roll does not consume deterministic resolver fallback")
    if "quality_gate_passed" not in tag_code or "selection_reason" not in tag_code:
        raise RuntimeError("production Tag B-roll lost advisory quality telemetry")
    if "publication_description" not in merge_code or "_attribution" not in merge_code:
        raise RuntimeError("publication attribution propagation missing")
    if not resolver_url.startswith(INTERNAL_SERVICE_ORIGIN):
        raise RuntimeError(f"resolver still traverses public proxy: {resolver_url}")

    routed_llm_nodes = 0
    for node in workflow.get("nodes", []):
        url = node.get("parameters", {}).get("url")
        if not isinstance(url, str):
            continue
        if "api.openai.com" in url or "api.anthropic.com" in url:
            raise RuntimeError(f"generated workflow bypasses internal LLM gateway: {node.get('name')} -> {url}")
        if url.startswith(f"{LLM_GATEWAY_ORIGIN}/v1/"):
            routed_llm_nodes += 1
            if node.get("credentials"):
                raise RuntimeError(f"provider credential leaked into gateway-routed node: {node.get('name')}")
    if routed_llm_nodes != int(workflow.get("meta", {}).get("llm_gateway_routed_nodes", -1)) or routed_llm_nodes < 1:
        raise RuntimeError("generated workflow LLM gateway routing count is inconsistent")

    compose_text = compose_out.read_text()
    resolver_text = resolver_out.read_text()
    for marker in ("VISUAL_MATCHING_V4_COMPOSE", "NON_BLOCKING_FINAL_QA", "PRODUCTION_BT709_RANGE_NORMALIZATION", "reviewFinalVideo"):
        if marker not in compose_text:
            raise RuntimeError(f"compose artifact missing {marker}")
    if "Final visual QA rejected catastrophic render defects" in compose_text:
        raise RuntimeError("final visual QA can still block publication")
    for marker in ("candidatePassesGate", "deterministic_template_fallback", "RESOLVE_DEADLINE_MS", "RESOLVER_V5_RUNTIME", "V5_VIDEO_SAMPLE_STAGING", "V5_ALWAYS_PUBLISH_BEST_AVAILABLE", "best_available_below_quality_target", "no_technically_usable_candidate"):
        if marker not in resolver_text:
            raise RuntimeError(f"resolver artifact missing {marker}")
    if 'reason: state.budget_exhausted || "below_semantic_quality_gate"' in resolver_text:
        raise RuntimeError("resolver can still reject an asset solely for quality score")
    if "V5_PROOF_MEDIA_TYPE_FILTER" in resolver_text:
        raise RuntimeError("literal_video was reintroduced as a hard media-type availability gate")
    if "normalizeAnnotationPlan" in resolver_text or "return annotations as an array" in resolver_text or "annotation_plan" in resolver_text:
        raise RuntimeError("resolver artifact still generates annotation geometry")

    for stage in stages:
        stage.unlink(missing_ok=True)

    artifact_paths = [workflow_out, compose_out, resolver_out]
    manifest = {
        "build_version": BUILD_VERSION,
        "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact_paths},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output_dir.resolve()
    manifest = build(root, output)
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
