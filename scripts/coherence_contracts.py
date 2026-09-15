"""Asserted post-migration contracts: field handoffs, not marker-only checks."""
import re
from retention_workflow import append_prompt

def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Coherence contract anchor changed: {old[:100]}')
    return text.replace(old,new,1)

def apply(workflow, compose):
    nodes={n['name']:n for n in workflow['nodes']}
    def code(name): return nodes[name]['parameters']['jsCode']
    def put(name, value): nodes[name]['parameters']['jsCode']=value
    # Retain identities through both whitelist maps, then bind commissioning to
    # the server-approved candidate rather than trusting model rewritten fields.
    pool=replace(code('Parse Topic Pool'),'.map(c=>({','.map(c=>({...c,')
    pool=replace(pool,"strategy_arm:_gv2FallbackArm(i)","strategy_arm:'explore'")
    pool=replace(pool,"const _gv2FallbackArm=(i)=>i<7?'exploit':(i<9?'adjacent':'explore');",'// Missing strategy labels remain exploration, never score-position relabeling.')
    put('Parse Topic Pool',pool)
    extract=replace(code('Extract Generated Topic'),'.map(c=>({','.map(c=>({...c,')
    extract=replace(extract,'const picked=candidates.find(c=>!tooSimilar(c.topic))||candidates[0];',r'''
const approved=$('Deduplicate Topic Pool').first().json.shortlist || [];
const identity=['topic','strategy_arm','subject_key','mechanism_key','payoff_key','canonical_key','research_query','archetype'];
candidates=candidates.map(c=>{const original=approved.find(a=>a.topic===c.topic);if(!original)return null;
  const bound={...c};for(const key of identity)bound[key]=original[key];return bound;}).filter(Boolean);
const picked=candidates.find(c=>!tooSimilar(c.topic));
if(!picked)throw new Error('Commissioning did not select an approved nonduplicate topic');''')
    extract=replace(extract,'return {json:{\n  topic:picked.topic','return {json:{\n  ...picked, topic:picked.topic')
    put('Extract Generated Topic',extract)
    append_prompt(nodes['Claude: Commission Topic Shortlist'], 'Return only candidates from the supplied approved shortlist. Preserve each topic exactly; do not introduce, paraphrase, or substitute a different fact. Scoring does not authorize changing its identity.', assignment=False)

    # Lossless scene handoff and one accepted render-settings contract.
    merge=replace(code('Merge By scene_index (not position)'), 'return {\n  scene_index:v.scene_index', 'return {...v,\n  scene_index:v.scene_index')
    merge=replace(merge,'return {json:{hook:', "const accepted=$('Capture Accepted Script').first().json.script_snapshot;\nreturn {json:{caption_mode:accepted.caption_mode||'karaoke',creative_format:accepted.creative_format||'documentary_cinematic',engagement_mode:accepted.engagement_mode||'none',hook:")
    put('Merge By scene_index (not position)',merge)
    resolver=nodes['Resolve B-roll']['parameters']
    resolver['jsonBody']=replace(resolver['jsonBody'],"sc && sc.visual_source === 'template'", "sc && !sc.template_data?.is_outro && sc.visual_source === 'template'")
    tag=replace(code('Tag B-roll'),'asset_score:r.score??null,', "asset_score:r.score??null,retrieval:{selected_source:r.source,score:r.score??null,quality_gate_passed:r.quality_gate_passed??null,candidate_count:r.candidate_count??null,search_rounds:r.search_rounds??null,queries_tried:r.queries_tried||[],vision_calls:r.scene_used??null,selection_reason:r.selection_reason||null},")
    put('Tag B-roll',tag)
    log=nodes['Log Published Video']
    body=log['parameters']['jsonBody']
    scores="($('Merge By scene_index (not position)').first().json.data||[]).filter(s=>!s.template_data?.is_outro&&Number.isFinite(s.asset_score)).map(s=>s.asset_score)"
    body=replace(body,'asset_quality_min: null',f"asset_quality_min: (()=>{{const s={scores};return s.length?Math.min(...s):null;}})()")
    body=replace(body,'asset_quality_avg: null',f"asset_quality_avg: (()=>{{const s={scores};return s.length?s.reduce((a,b)=>a+b,0)/s.length:null;}})()")
    body=replace(body,'creative_dna: {',"retrieval_telemetry: ($('Merge By scene_index (not position)').first().json.data||[]).map(s=>({scene_index:s.scene_index,...s.retrieval})), creative_dna: {")
    log['parameters']['jsonBody']=body
    log.pop('onError',None); log.pop('continueOnFail',None)
    log.update(retryOnFail=True,maxTries=3,waitBetweenTries=2000)
    # Log a durable publication checkpoint before optional post-upload work.
    con=workflow['connections']
    con['YouTube: Upload Draft']={'main':[[{'node':'Log Published Video','type':'main','index':0}]]}
    con['Log Published Video']={'main':[[{'node':'Disclose AI-Generated Content','type':'main','index':0}]]}
    con['Cleanup: Delete Local Render']={'main':[[]]}
    disclosure=nodes['Disclose AI-Generated Content']
    disclosure['parameters']['jsonBody']=replace(disclosure['parameters']['jsonBody'],'id: $json.uploadId || $json.id', "id: $('YouTube: Upload Draft').first().json.uploadId || $('YouTube: Upload Draft').first().json.id")
    disclosure.update(retryOnFail=True,maxTries=3,waitBetweenTries=2000)
    # A single title policy; never truncate a 60-character commissioned title at 50.
    for n in workflow['nodes']:
        for k,v in n.get('parameters',{}).items():
            if isinstance(v,str):n['parameters'][k]=v.replace('.slice(0, 50)', '.slice(0, 60)')
        if n.get('parameters',{}).get('url','').startswith('http://llm-gateway:'):
            n['retryOnFail']=False
            timeout=n['parameters'].setdefault('options',{}).get('timeout',120000)
            n['parameters']['options']['timeout']=timeout
            headers=n['parameters'].setdefault('headerParameters',{}).setdefault('parameters',[])
            n['parameters']['sendHeaders']=True
            headers.append({'name':'x-llm-timeout-ms','value':str(max(1000,timeout-5000))})
    workflow['nodes']=[n for n in workflow['nodes'] if n['name']!='Tag Video with scene_index']
    con.pop('Tag Video with scene_index',None)
    # Empty portfolios need no API request; genuine measurement errors must not
    # masquerade as a successful learning pass.
    guard='Skip Empty Measurement Plan'
    workflow['nodes'].append({'id':'coherence-measurement-guard','name':guard,'type':'n8n-nodes-base.code','typeVersion':2,'position':[nodes['Get Shorts to Measure']['position'][0]+100,nodes['Get Shorts to Measure']['position'][1]],'parameters':{'jsCode':"const plan=$input.first().json;if(plan.error)throw new Error('Measurement plan failed');return Array.isArray(plan.video_ids)&&plan.video_ids.length?[{json:plan}]:[];"}})
    con[guard]=con['Get Shorts to Measure']
    con['Get Shorts to Measure']={'main':[[{'node':guard,'type':'main','index':0}]]}
    for name in ['Get Shorts to Measure','YouTube: Video Analytics','Ingest Analytics']:
        nodes[name].pop('continueOnFail',None);nodes[name].pop('onError',None)
    workflow.setdefault('meta',{})['coherence_contract']='post-pr163-v1'
    # Remove later duplicate handlers, preserving the first authoritative routes.
    for method,route in [('post','/performance/log'),('post','/performance/ingest'),('get','/channel-insights')]:
        pattern=rf'app\.{method}\("{re.escape(route)}", async \(req, res\) => \{{.*?\n\}}\);'
        matches=list(re.finditer(pattern,compose,re.S))
        for m in reversed(matches[1:]):compose=compose[:m.start()]+compose[m.end():]
    return compose
