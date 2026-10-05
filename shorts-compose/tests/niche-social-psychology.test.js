const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs');
const os=require('os');
const path=require('path');
const {execFileSync}=require('child_process');
const root=path.resolve(__dirname,'../..');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'shorts-niche-'));
execFileSync(process.platform==='win32'?'python':'python3',['scripts/build_production_artifacts.py','--output-dir',tmp],{cwd:root,stdio:'pipe'});
test.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));

const workflow=JSON.parse(fs.readFileSync(path.join(tmp,'workflow.json'),'utf8'));
const node=(name)=>{const n=workflow.nodes.find(x=>x.name===name);assert.ok(n,`missing node ${name}`);return n.parameters;};

// Run the promise-payoff gate exactly as built into Validate Final Script, in
// isolation: the slice between its marker and the click-confirmation gate.
function promiseGate(parsed,draft){
  const code=node('Validate Final Script').jsCode;
  const start=code.indexOf('// NICHE_PROMISE_COUNT_GATE');
  const end=code.indexOf('// Click-confirmation gate');
  assert.ok(start>=0&&end>start,'promise gate must sit before the click-confirmation gate');
  const errors=[];
  new Function('parsed','errors','_draftScript',code.slice(start,end))(parsed,errors,draft);
  return errors;
}
const scenes=(n)=>Array.from({length:n},(_,i)=>({scene_index:i,narration:'x'}));

test('a promise of N items passes only with exactly N item scenes', ()=>{
  const ok={hook:'Three signs someone secretly respects you',title:'3 Signs Someone Secretly Respects You',scenes:scenes(4),promised_points:[1,2,3]};
  assert.deepEqual(promiseGate(ok),[]);
  const short={...ok,promised_points:[1,2]};
  assert.match(promiseGate(short).join(' '),/promises 3 items but promised_points lists 2/);
});

test('hook and title must promise the same number', ()=>{
  const p={hook:'Four signs someone secretly dislikes you',title:'5 Signs Someone Dislikes You',scenes:scenes(6),promised_points:[1,2,3,4]};
  assert.match(promiseGate(p).join(' '),/hook promises 4 items but title promises 5/);
});

test('the hook scene can never count as a promised item', ()=>{
  const p={hook:'3 habits that make people respect you',title:'Habits People Respect',scenes:scenes(4),promised_points:[0,1,2]};
  assert.match(promiseGate(p).join(' '),/never scene 0/);
});

test('a missing promised_points is recovered from the draft, else rejected', ()=>{
  const p={hook:'Two small tells someone likes you',title:'If They Do This, They Like You',scenes:scenes(3)};
  assert.deepEqual(promiseGate({...p},{promised_points:[1,2]}),[]);
  assert.match(promiseGate({...p}).join(' '),/promised_points must list/);
});

test('an uncounted hook is not forced into a list', ()=>{
  const p={hook:'If someone mirrors your posture, they like you more than they admit',title:'If They Copy Your Posture',scenes:scenes(4),promised_points:[]};
  assert.deepEqual(promiseGate(p),[]);
});

test('medical scan rejects clinical labels the niche invites', ()=>{
  const code=node('Validate Final Script').jsCode;
  const m=code.match(/const medicalKeywords = (\/.*\/i);/);
  assert.ok(m,'medical keyword scan present');
  const re=eval(m[1]);
  for(const s of ['signs of a narcissist','he is a psychopath','classic trauma bonding']) assert.ok(re.test(s),s);
  for(const s of ['signs someone secretly respects you','eye contact during a first date']) assert.ok(!re.test(s),s);
});

test('prompts carry the niche and none of the trivia-era framing', ()=>{
  const topic=node('Claude: Generate Topic').jsonBody;
  const draft=node('Claude: Draft Script (Stage 1)').jsonBody;
  assert.match(topic,/NICHE_SOCIAL_PSYCHOLOGY_V1/);
  assert.match(topic,/hidden_signal, self_check_reassurance, warning_sign, social_lever, attraction_myth/);
  assert.match(draft,/PROMISE-PAYOFF CONTRACT/);
  assert.match(draft,/promised_points/);
  for(const stale of ['mind-bending truth','never a story about a person and never a list','museum-grade clarity','looks_fake_but_real']){
    assert.ok(!topic.includes(stale)&&!draft.includes(stale),`stale trivia text survived: ${stale}`);
  }
  for(const name of ['Claude: Visual Director','Claude: Repair Script','Claude: Critique Hooks']){
    assert.match(node(name).jsonBody,/NICHE_SOCIAL_PSYCHOLOGY_V1/,name);
  }
});

test('published videos are tagged with the niche and narrated by the new voice', ()=>{
  assert.match(node('Log Published Video').jsonBody,/niche: 'social_psychology_v1'/);
  const url=node('ElevenLabs: TTS+Timestamps').url;
  assert.ok(url.includes('/text-to-speech/nPczCjzI2devNBz1zQrb/'),url);
});

test('production publishes at most one Short per day', ()=>{
  const trigger=workflow.nodes.find(n=>n.name==='Schedule Trigger').parameters.rule.interval;
  assert.equal(trigger.length,1);
  assert.match(trigger[0].expression,/^\d+ \d+ \* \* \*$/);
});

test('insights learn only from the current niche once CHANNEL_NICHE is set', async () => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'niche-insights-'));
  const prior={h:process.env.TOPIC_HISTORY_PATH,n:process.env.CHANNEL_NICHE};
  process.env.TOPIC_HISTORY_PATH=path.join(dir,'topic_history.json');
  process.env.CHANNEL_NICHE='social_psychology_v1';
  delete require.cache[require.resolve('../feedbackLoop')];
  const fb=require('../feedbackLoop');
  try{
    const snap={t72h:{views:1000,engaged_views:300,engaged_view_rate:0.3,average_view_percentage:50}};
    fs.writeFileSync(path.join(dir,'performance_history.json'),JSON.stringify([
      {video_id:'old',creative_dna:{concept_archetype:'hidden_mechanism'},snapshots:snap},
      {video_id:'new',creative_dna:{concept_archetype:'hidden_signal',niche:'social_psychology_v1'},snapshots:snap},
    ]));
    // Trivia-era strategist guidance must not reach the writers.
    fs.writeFileSync(path.join(dir,'channel_insights.json'),JSON.stringify({generated_at:new Date().toISOString(),measurement_policy:require('../retentionPolicy').RETENTION_POLICY,guidance:['more space facts'],avoid:[]}));
    const insights=await fb.getInsights();
    assert.deepEqual(insights.guidance,[]);
    assert.equal(insights.niche,'social_psychology_v1');
    assert.equal(insights.archetype_performance.measured_videos,1);
    assert.equal(fb.normalizeCreativeDna({creative_dna:{niche:'social_psychology_v1'}}).niche,'social_psychology_v1');
  }finally{
    for(const [k,v] of [['TOPIC_HISTORY_PATH',prior.h],['CHANNEL_NICHE',prior.n]]){if(v===undefined)delete process.env[k];else process.env[k]=v;}
    delete require.cache[require.resolve('../feedbackLoop')];
    fs.rmSync(dir,{recursive:true,force:true});
  }
});
