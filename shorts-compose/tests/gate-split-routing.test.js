const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs');
const os=require('os');
const path=require('path');
const {execFileSync}=require('child_process');
const root=path.resolve(__dirname,'../..');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'shorts-gates-'));
execFileSync(process.platform==='win32'?'python':'python3',['scripts/build_production_artifacts.py','--output-dir',tmp],{cwd:root,stdio:'pipe'});
test.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));

const workflow=JSON.parse(fs.readFileSync(path.join(tmp,'workflow.json'),'utf8'));
const node=(name)=>workflow.nodes.find(x=>x.name===name).parameters;
const validator=node('Validate Final Script').jsCode;
const slice=(start,end)=>{
  const i=validator.indexOf(start), j=validator.indexOf(end,i+1);
  assert.ok(i>=0&&j>i,`validator slice ${start} .. ${end}`);
  return validator.slice(i,j);
};

function qualityGate(quality){
  const errors=[], parsed={quality};
  new Function('parsed','errors',slice('// GATE_SPLIT_V1','if (!parsed.hook ||'))(parsed,errors);
  return {errors,parsed};
}

test('low virality predictions never reject a script, and are recorded', ()=>{
  const hard={evidence_strength:80,payoff_strength:80,information_density:80,visual_progression:80,naturalness:80};
  const {errors,parsed}=qualityGate({...hard,shareability:40,hook_strength:50,concept_strength:55,distinctiveness:30,overall:60});
  assert.deepEqual(errors,[]);
  assert.equal(parsed.predicted_virality.shareability,40);
  assert.equal(parsed.predicted_virality.score,47);
});

test('production floors still reject', ()=>{
  const {errors}=qualityGate({evidence_strength:60,payoff_strength:80,information_density:80,visual_progression:80,naturalness:80});
  assert.deepEqual(errors,['quality.evidence_strength=60 is below publish threshold 74']);
  assert.match(qualityGate(null).errors[0],/quality object missing/);
});

test('each failure is tagged with the response it needs', ()=>{
  const src=slice('const _routeFor','return { json: { _scriptValid: false');
  const route=new Function(src+'; return _routeFor;')();
  const cases={
    'hook opens with a generic phrase (e.g. did you know)':'NEW_HOOK_BRANCH',
    'quality.evidence_strength=60 is below publish threshold 74':'REWRITE_CLAIMS',
    'hook/title promises 3 items but promised_points lists 2 - deliver exactly 3 items, one scene each':'FIX_PROMISE_PAYOFF',
    'payoff.claim missing - state the specific promise':'FIX_PROMISE_PAYOFF',
    'scene 0 (scene_index 0, the first frame) must not be a template':'NEW_VISUAL_TREATMENT',
    'visual_plan_quality=70 below publish threshold 78':'NEW_VISUAL_TREATMENT',
    'script appears to touch medical/health content (keyword match) - this content type is excluded':'NEW_TOPIC',
    'script appears to be about a different topic than the one selected ("x")':'REWRITE_ON_TOPIC',
    'scene 2 narration too short/missing':'REPAIR_FIELDS',
    'title missing or out of bounds (5-60 chars)':'REPAIR_FIELDS',
    'scene 2 narration has 41 words (max 26) - split it into separate scenes, each with its own footage':'SPLIT_LONG_SCENE',
    'absolute claim "the most reliable" - restate it as a tendency the research supports (often / a sign that)':'REWRITE_CLAIMS',
  };
  for(const [error,expected] of Object.entries(cases)) assert.equal(route(error),expected,error);
});

// Run the built pacing + claim checks in isolation.
function pacingClaims(parsed){
  const errors=[];
  new Function('parsed','errors',slice('// SCENE_PACING_V1','// Medical/health exclusion backstop'))(parsed,errors);
  return errors;
}
const words=(n)=>Array.from({length:n},()=>'word').join(' ');

test('a scene over 26 spoken words must be split; the outro is exempt', ()=>{
  assert.deepEqual(pacingClaims({title:'t',scenes:[{scene_index:0,narration:words(22)},{scene_index:1,narration:words(26)}]}),[]);
  const errors=pacingClaims({title:'t',scenes:[{scene_index:0,narration:words(10)},{scene_index:2,narration:words(41)},{scene_index:3,narration:words(40),template_data:{is_outro:true}}]});
  assert.deepEqual(errors,['scene 2 narration has 41 words (max 26) - split it into separate scenes, each with its own footage']);
});

test('absolute claims are rejected, calibrated ones pass', ()=>{
  // The real failure from execution 1086.
  const bad=pacingClaims({title:'Your Feet Betray You',scenes:[{scene_index:2,narration:"It's the most reliable signal of interest. The conversation is over, regardless of what they say."}]});
  assert.match(bad.join(' '),/absolute claim "the most reliable"/);
  assert.match(pacingClaims({title:'This Works 100% of the Time',scenes:[]}).join(' '),/absolute claim "100%"/);
  assert.deepEqual(pacingClaims({title:'Your Feet Can Give You Away',scenes:[{scene_index:1,narration:'Feet pointing away is often a sign that someone wants to leave.'}]}),[]);
});

test('a candidate-pool topic switch is judged against the new topic', ()=>{
  const src=slice("const _seedBase",'const _gv2ContentWords');
  const run=(parsed)=>{
    const errors=[];
    const $=()=>({item:{json:{topic:'Signs someone secretly respects you',candidate_pool:[{topic:'Why people trust you more when you say their name'}]}}});
    new Function('parsed','errors','$',src)(parsed,errors,$);
    return errors;
  };
  const switched={resolved_topic:'Why people trust you more when you say their name',hook:'Say their name once',title:'The Name Trick',scenes:[{narration:'People trust you more when you use their name.'}]};
  assert.deepEqual(run(switched),[]);
  // Without a matching candidate the original topic still governs.
  assert.match(run({...switched,resolved_topic:'something invented'}).join(' '),/different topic/);
});

test('critic prompts no longer list virality scores as floors, repair prompt routes', ()=>{
  for(const name of ['Claude: Visual Director','Claude: Repair Script']){
    const body=node(name).jsonBody;
    assert.match(body,/GATE_SPLIT_V1/);
    assert.ok(!body.includes('shareability 76'),name);
  }
  assert.match(node('Claude: Repair Script').jsonBody,/\[NEW_HOOK_BRANCH\]: do not polish the failed hook/);
  assert.match(node('Log Published Video').jsonBody,/predicted_virality:/);
});
