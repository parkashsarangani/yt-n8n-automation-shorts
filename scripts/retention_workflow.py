"""Final retention layer, after growth/metrics transforms. No new upload gates."""
import json
import re
from pathlib import Path

MARKER = 'RETENTION_EXPERIMENT_V1'
WRITER = '''RETENTION_EXPERIMENT_V1 CREATIVE BRIEF (use this to resolve conflicting packaging advice):
Keep the existing 28-36 second, 65-95 content-word target and 105-word ceiling. Do not pad a shorter complete explanation.
Choose one concrete, sourced contradiction with a visible subject, mechanism, or useful consequence. No invented stakes or unsupported certainty. Broad labels like 'history is not what you think' are not an opening.
OPENING: scene 0 starts on the actual object, action, or result. The first spoken sentence names that subject and the surprising tension in at most 12 words, with no greeting, 'did you know', 'you won't believe', or channel intro. State the essential fact early; curiosity should come from HOW or WHY it works. Never withhold a simple answer until the last second merely to force retention.
STRUCTURE: demonstrate the promise in the first three seconds where a truthful visual can do so; otherwise show the exact subject and explicitly plan what can and cannot be demonstrated. Then give the minimum causal explanation, one evidence beat or escalation, and a concrete takeaway. Aim to START the payoff scene by 70% of content time so viewers have time to understand it. This is a writing target, not a factual claim about YouTube. No repeated summary scenes.
PARTICIPATION: write comment_hook as one brief question a viewer can answer about this exact fact (prediction, personal experience, or a meaningful choice). No generic 'did this surprise you', engagement bait, requests to do four actions, or invented controversy. The question is displayed during the existing in-content overlay and used for the first comment; do not add a CTA scene or pad narration for it. A share_trigger describes a real reason someone would send the useful fact to a specific person; do not automatically speak a separate sharing request.
Return optional retention_brief={first_frame_claim:string, first_3_second_proof:string, comment_prompt_type:'prediction'|'experience'|'choice', share_trigger:string}. Describe a renderable plan, not a self-awarded numeric score.
HOOK EXPERIMENT: use the assigned arm below. direct_contradiction: open with a short declarative contradiction. concrete_question: open with a short question naming the actual object and tension, then immediately start answering it. Both arms must be equally truthful and specific. Change only the hook framing; keep voice, duration target, visual house style and existing outro assignment unchanged.
SELF REVIEW: Does the first sentence name the subject and tension? Is the first visual literally relevant? Does each beat add information? Does the explanation deliver before the closing question? Preserve factual boundaries and remove padding.
Assigned experiment: '''
DIRECTOR = '''RETENTION_EXPERIMENT_V1 VISUAL AND EDITORIAL CHECK:
Preserve retention_brief and the assigned hook framing through visual direction and repairs. A valid repair must not turn a concrete opening into generic suspense.
Scene 0 must show the named subject or truthful result immediately. Select high-inventory real/archive proof with a meaningful action or camera movement where appropriate, never a logo or contextual landscape unrelated to the first sentence. Do not invent evidence, add diagnostic boxes, or replace a missing physical mechanism with misleading stock. For every later scene require a new evidence beat or visual detail supporting its narration.
Keep the claim understandable without waiting for the final frame. Aim to start the payoff scene by 70% of content time. This is an advisory target; never remove evidence to satisfy timing. Missing optional retention_brief fields must not prevent a valid script from being published. Keep all existing editorial and medical/topic gates.
Assigned experiment: '''


def append_prompt(node, text, assignment=True):
    body = node['parameters']['jsonBody']
    match = re.search(r'\s*}\s*]\s*}\s*\)\s*}}\s*$', body)
    if not match:
        raise ValueError(f"cannot find final prompt content: {node['name']}")
    addition = ' + ' + json.dumps('\n\n' + text)
    if assignment:
        addition += " + JSON.stringify($('Plan Retention Experiment').first().json.retention_experiment)"
    node['parameters']['jsonBody'] = body[:match.start()] + addition + body[match.start():]



def upgrade(workflow, compose, root: Path):
    nodes = {n['name']: n for n in workflow['nodes']}
    if workflow.get('meta', {}).get('retention_policy') == MARKER:
        return compose
    shared = (root / 'shorts-compose/retentionPolicy.js').read_text().split('module.exports=')[0]
    name = 'Plan Retention Experiment'
    workflow['nodes'].append({'id':'retention-experiment-plan-v1','name':name,'type':'n8n-nodes-base.code','typeVersion':2,'position':[nodes['Normalize Research Evidence']['position'][0]+160, nodes['Normalize Research Evidence']['position'][1]+160],
        'parameters':{'jsCode':shared+"\nreturn {json:{...$input.first().json,retention_experiment:assignHookExperiment(String($execution.id))}};"}})
    edges = workflow['connections']['Normalize Research Evidence']['main'][0]
    old = [e for e in edges if e['node'] == 'Claude: Draft Script (Stage 1)']
    if len(old) != 1:
        raise ValueError('research to writer edge missing')
    edges[edges.index(old[0])] = {'node':name,'type':'main','index':0}
    workflow['connections'][name] = {'main':[old]}
    for target in ['Claude: Generate Topic','Claude: Commission Topic Shortlist']:
        append_prompt(nodes[target], "RETENTION_EXPERIMENT_V1 TOPIC PRIORITY: prefer one specific observable mechanism, useful everyday fact, or emotionally clear true story with a recognizable subject and available real/archive evidence. The opening must work in one sentence and one literal shot. Treat broad history/scale packaging as a hypothesis to improve, not a banned niche. Never manufacture conflict or claim a topic will pass a YouTube distribution gate. Keep existing evidence, medical exclusion, semantic deduplication and topic diversity requirements.", assignment=False)
    append_prompt(nodes['Claude: Draft Script (Stage 1)'], WRITER)
    for target in ['Claude: Visual Director','Claude: Repair Script']:
        append_prompt(nodes[target], DIRECTOR)
    validator = nodes['Validate Final Script']['parameters']
    anchor = 'return { json: { ...parsed, _scriptValid: true } };'
    if validator['jsCode'].count(anchor) != 1:
        raise ValueError('successful validator return anchor changed')
    # Assignment is owned by the workflow, not the model. Diagnostics never reject.
    validator['jsCode'] = validator['jsCode'].replace(anchor, shared + "\ntry { parsed.retention_experiment=$('Plan Retention Experiment').first().json.retention_experiment; } catch {}\nparsed.retention_diagnostics=creativeDiagnostics(parsed);\n" + anchor)
    log = nodes['Log Published Video']['parameters']
    anchor = 'creative_dna: {'
    if log['jsonBody'].count(anchor) != 1:
        raise ValueError('creative DNA log anchor changed')
    source = "$('Merge By scene_index (not position)').first().json.script_snapshot"
    log['jsonBody'] = log['jsonBody'].replace(anchor, anchor + f" retention_experiment: {source}.retention_experiment || null, retention_diagnostics: {source}.retention_diagnostics || null, rendered_timing: $('Validate Compose Result').first().json.rendered_timing || null,")
    import_anchor = 'const feedback = require("./feedbackLoop");'
    if import_anchor not in compose:
        raise ValueError('compose feedback import missing')
    if 'const retentionPolicy = require("./retentionPolicy");' not in compose:
        compose = compose.replace(import_anchor, import_anchor+'\nconst retentionPolicy = require("./retentionPolicy");',1)
    emphasis = 'const emphasisIdx = Math.max(0, scenes.reduce((last, s, i) => s?.template_data?.is_outro ? last : i, -1));'
    if emphasis not in compose and 'const payoffSceneId = reqBody.script_snapshot?.payoff?.resolved_in_scene;' not in compose:
        raise ValueError('payoff emphasis anchor changed')
    compose = compose.replace(emphasis, "const payoffSceneId = reqBody.script_snapshot?.payoff?.resolved_in_scene;\n    const plannedPayoffIdx = payoffSceneId == null ? -1 : scenes.findIndex(s => !s?.template_data?.is_outro && Number(s.scene_index) === Number(payoffSceneId));\n    const emphasisIdx = plannedPayoffIdx >= 0 ? plannedPayoffIdx : Math.max(0, scenes.reduce((last, s, i) => s?.template_data?.is_outro ? last : i, -1));",1)
    result_anchor = 'content_duration_sec: Number(contentDuration.toFixed(3)),'
    if result_anchor not in compose:
        raise ValueError('compose content duration missing')
    if 'rendered_timing: retentionPolicy.renderedTiming(' not in compose:
        compose = compose.replace(result_anchor, result_anchor+' rendered_timing: retentionPolicy.renderedTiming(reqBody.script_snapshot, scenes, durations),',1)
    workflow.setdefault('meta',{})['retention_policy'] = MARKER
    return compose


def assert_applied(workflow, compose):
    nodes = {n['name']:n for n in workflow['nodes']}
    for name in ['Claude: Draft Script (Stage 1)','Claude: Visual Director','Claude: Repair Script']:
        if MARKER not in nodes[name]['parameters']['jsonBody']:
            raise RuntimeError(f'{name} lost retention guidance')
    if nodes['Start Compose Job']['parameters']['jsonBody'].strip() != '={{ $json }}':
        raise RuntimeError('compose body size safeguard lost')
    if 'renderedTiming(reqBody.script_snapshot, scenes, durations)' not in compose:
        raise RuntimeError('measured scene timing missing')
