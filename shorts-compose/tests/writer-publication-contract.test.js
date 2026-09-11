const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');

// These assertions are about the GENERATED workflow, not the seed, so build it.
// Only unavailable Python skips; production build failures must fail this suite.
const REPO = path.join(__dirname, '..', '..');
let workflow = null;
let skipReason = false;
let python;
for (const candidate of ['python', 'python3']) {
  try { execFileSync(candidate, ['--version'], {stdio:'ignore'}); python=candidate; break; }
  catch { /* probe interpreter availability only */ }
}
if (!python) {
  skipReason = 'Python interpreter unavailable';
} else {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'wpc-'));
  try {
    // A broken build must fail the test, never silently skip it or read stale output.
    execFileSync(python, ['scripts/build_production_artifacts.py', '--output-dir', out], {cwd:REPO,stdio:'pipe'});
    workflow = JSON.parse(fs.readFileSync(path.join(out, 'workflow.json'), 'utf8'));
  } finally { fs.rmSync(out, {recursive:true,force:true}); }
}

const node = (name) => workflow.nodes.find((n) => n.name === name);
const body = (name) => String(node(name).parameters.jsonBody || '');
const code = (name) => String(node(name).parameters.jsCode || '');

test('writer prompt states exactly one length policy', { skip: skipReason }, () => {
  const writer = body('Claude: Draft Script (Stage 1)');
  // The pre-V2 policy did not just permit 120 words, it disclaimed the correct
  // target, so the model overshot the 105-word ceiling (execution 808).
  for (const legacy of ['40-120', 'about 120 words', '60-90/3-4']) {
    assert.ok(!writer.includes(legacy), `legacy length guidance survived: ${legacy}`);
  }
  for (const current of ['65-95', '28-36 second', '105 content words']) {
    assert.ok(writer.includes(current), `current duration policy missing: ${current}`);
  }
});

test('the accepted script reaches publication, never a rejected attempt', { skip: skipReason }, () => {
  // "Validate Final Script" runs once per attempt, so downstream reads of it are
  // ambiguous: .item gave undefined and .first() gave run 0, the REJECTED one.
  assert.ok(node('Capture Accepted Script'), 'accepted-script capture node missing');

  const capture = code('Capture Accepted Script');
  assert.match(capture, /_scriptValid !== true/, 'capture node must refuse an unvalidated script');
  assert.match(capture, /script_snapshot/);

  const valid = workflow.connections['If Script Valid'].main;
  assert.equal(valid[0][0].node, 'Capture Accepted Script', 'capture must sit on the accepted branch');

  for (const name of ['Merge By scene_index (not position)', 'Start Compose Job', 'Log Published Video']) {
    const text = name === 'Merge By scene_index (not position)' ? code(name) : body(name);
    assert.ok(!text.includes("$('Validate Final Script')"), `${name} still re-reads the multi-run validator`);
  }
});

test('the merge never reads its own output', { skip: skipReason }, () => {
  // A node reading itself yields undefined; a blanket rewrite introduced exactly
  // this once already.
  assert.ok(!code('Merge By scene_index (not position)').includes("$('Merge By scene_index (not position)')"));
});

test('publication telemetry is sourced from the accepted script', { skip: skipReason }, () => {
  const log = body('Log Published Video');
  assert.match(log, /script_snapshot/, 'creative DNA must come from the accepted snapshot');
  for (const field of ['policy_version', 'topic_strategy_arm', 'outro_experiment_arm', 'duration_sec', 'creative_dna']) {
    assert.ok(log.includes(field), `publication telemetry lost ${field}`);
  }
});

test('no HTTP expression uses a Code-node-only helper', { skip: skipReason }, () => {
  // $getWorkflowStaticData does not exist in HTTP expressions; it resolves to
  // undefined and the request body fails to parse.
  for (const n of workflow.nodes) {
    if (n.type !== 'n8n-nodes-base.httpRequest') continue;
    const params = JSON.stringify(n.parameters || {});
    assert.ok(!params.includes('getWorkflowStaticData'), `${n.name} uses getWorkflowStaticData in an HTTP expression`);
  }
});

function evalExpression(value, json, lookup) {
  return new Function('$json', '$', '$execution', `return (${value.slice(3, -2)});`)(json, lookup, {id:'contract'});
}
const wrapped = content => ({choices:[{finish_reason:'stop',message:{content:JSON.stringify(content)}}]});
function draftFixture() {
  const quality = Object.fromEntries(['concept_strength','hook_strength','evidence_strength','payoff_strength','information_density','first_frame_strength','visual_progression','shareability','naturalness','distinctiveness','voice_specificity','overall'].map(k=>[k,80]));
  return {hook:'a real hook that is long enough',title:'Accepted title',caption_style:'upbeat',trigger:'disbelief',caption_mode:'karaoke',creative_format:'documentary_cinematic',first_frame_type:'hero_motion',visual_plan_quality:84,
    tags:['a','b','c','d','e'],seo_description:'a description that is long enough to satisfy the minimum length check',payoff:{claim:'a specific promise the hook makes',resolved_in_scene:2},quality,
    scenes:[0,1,2].map(scene_index=>({scene_index,point:'the point of this scene',narration:'a real narration line that is definitely long enough to pass with clear evidence here',visual_source:'stock',visual_type:'real',visual_prompt:'a real visual prompt describing this specific scene in detail',negative_prompt:'no readable text',stock_search_query:'visible subject action',search_queries:['visible subject action','subject evidence','subject detail'],visual_role:'hero',visual_claim:'a literal visible scene proof',global_subject:'a recognizable broad topic',required_entities:['visible subject'],required_actions:['visible action'],required_relationships:[],forbidden_visuals:['unrelated filler'],acceptable_visuals:[],visual_proof_mode:'literal_video',visual_mode:'exact_real',must_show:'visible subject action',source_priority:['pexels','wikimedia'],template_fallback:{template_name:'kinetic_text',template_data:{line:'fallback'}}}))};
}
function validatePartial(partial, draft, state={}) {
  const lookup = name => {
    if(name==='Parse Draft JSON') return {first:()=>({json:{draft}}),item:{json:{draft}}};
    if(name==='Extract Generated Topic') return {item:{json:{topic:''}}};
    throw Error(`unexpected read ${name}`);
  };
  return new Function('$input','$','$execution','$getWorkflowStaticData',code('Validate Final Script'))({first:()=>({json:wrapped(partial)})},lookup,{id:'contract'},()=>state).json;
}

test('partial director output recovers empty/absent scenes and keeps indexed patches on their own scenes', {skip:skipReason}, () => {
  const draft=draftFixture();
  for(const partial of [{creative_format:'documentary_cinematic'}, {hook:draft.hook, scenes:[]}, {hook:draft.hook,scenes:[{scene_index:2,visual_claim:'ONLY THIRD SCENE'}]}]) {
    const result=validatePartial(partial,draft);
    assert.equal(result._scriptValid,true,JSON.stringify(result._validationErrors));
    assert.equal(result.visual_director_recovered,true);
    assert.deepEqual(result.scenes.filter(s=>!s.template_data?.is_outro).map(s=>s.narration),draft.scenes.map(s=>s.narration));
    if(partial.scenes?.length) {
      assert.notEqual(result.scenes[0].visual_claim,'ONLY THIRD SCENE');
      assert.equal(result.scenes[2].visual_claim,'ONLY THIRD SCENE');
    }
  }
});

test('partial recovery cannot invent missing editorial scores or narration', {skip:skipReason}, () => {
  const draft=draftFixture();delete draft.quality;
  const missingQuality=validatePartial({creative_format:'minimal_proof'},draft);
  assert.equal(missingQuality._scriptValid,false);
  assert.ok(missingQuality._validationErrors.some(e=>e.includes('quality object missing')));
  draft.scenes[0].narration='';
  assert.equal(validatePartial({},draft)._scriptValid,false);
});

test('repair loop carries its latest failed script without re-reading validation run zero', {skip:skipReason}, () => {
  const original=draftFixture(),latest=draftFixture();latest.title='Latest repair';latest.scenes[0].narration+=' updated';
  const state={scriptAttempts:{contract:{attempt:0}}};
  const result=new Function('$input','$execution','$getWorkflowStaticData',code('Increment Script Attempt'))({first:()=>({json:{_failedScript:latest,_validationErrors:['repair requested']}})},{id:'contract'},()=>state).json;
  assert.deepEqual(result._failedScript,latest);
  const recovered=validatePartial({},original,state);
  assert.equal(recovered.title,latest.title);
  assert.equal(recovered.scenes[0].narration,latest.scenes[0].narration);
  const lookup=name=> {
    assert.notEqual(name,'Validate Final Script');
    return {item:{json:{}},first:()=>({json:{}})};
  };
  const payload=JSON.parse(evalExpression(body('Claude: Repair Script'),result,lookup));
  assert.ok(payload.messages[0].content.includes('Latest repair'));
});

test('history can execute immediately after capture, before merge exists', {skip:skipReason}, () => {
  const lookup=name=> {assert.equal(name,'Extract Generated Topic');return {first:()=>({json:{topic:'Topic'}})};};
  const payload=JSON.parse(evalExpression(body('Save Topic to History'),{script_snapshot:{hook:'Accepted hook'}},lookup));
  assert.equal(payload.hook,'Accepted hook');
});

test('TTS and upload metadata use accepted values when rejected validator reads are unavailable', {skip:skipReason}, () => {
  const accepted=draftFixture();
  const lookup=name=> {
    assert.notEqual(name,'Validate Final Script','no ambiguous validator reads');
    assert.ok(['Capture Accepted Script','Merge By scene_index (not position)','YouTube: Upload Draft'].includes(name));
    return {first:()=>({json:{script_snapshot:accepted,publication_description:'Accepted description',id:'video-id'}})};
  };
  const tts=JSON.parse(evalExpression(body('ElevenLabs: TTS+Timestamps'),{narration:'Scene narration'},lookup));
  assert.equal(tts.voice_settings.speed,1.05);
  const upload=node('YouTube: Upload Draft').parameters;
  assert.equal(evalExpression(upload.title,{},lookup),'Accepted title');
  assert.equal(evalExpression(upload.options.tags,{},lookup),'a,b,c,d,e');
  for(const name of ['Disclose AI-Generated Content','Post First Comment']) assert.doesNotThrow(()=>JSON.parse(evalExpression(body(name),{id:'video-id'},lookup)));
});

test('compose body is a bare $json reference, never a spread+stringify reconstruction', {skip:skipReason}, () => {
  // Executions 802/804/805/814 all failed at Start Compose Job with
  // "\"undefined\" is not valid JSON". Root cause (confirmed with @n8n/tournament
  // directly against a real 741KB payload): n8n's legacy expression interpreter
  // silently fails - swallowed by a no-op error handler - when asked to perform
  // object-spread reconstruction plus JSON.stringify on an object this size
  // inside the expression itself, even though it can return the same object as
  // a bare reference just fine. Merge already writes the same fields with the
  // same defaults onto its own output, so no reconstruction is needed at all.
  const startCompose = body('Start Compose Job');
  assert.equal(startCompose.trim(), "={{ $json }}", 'Start Compose Job must not reconstruct its body inside the expression');
});
