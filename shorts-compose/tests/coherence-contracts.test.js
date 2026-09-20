const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs');
const os=require('os');
const path=require('path');
const {execFileSync}=require('child_process');
const root=path.resolve(__dirname,'../..');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'shorts-coherence-'));
execFileSync(process.platform==='win32'?'python':'python3',['scripts/build_production_artifacts.py','--output-dir',tmp],{cwd:root,stdio:'pipe'});
const w=JSON.parse(fs.readFileSync(path.join(tmp,'workflow.json')));
const n=Object.fromEntries(w.nodes.map(x=>[x.name,x]));
const compose=fs.readFileSync(path.join(tmp,'compose.js'),'utf8');
test.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));
function run(name,data,prior={},id='42'){
 const $=key=>({first:()=>({json:prior[key]||{}}),item:{json:prior[key]||{}}});
 return new Function('$input','$','$execution','$json',n[name].parameters.jsCode)({first:()=>({json:data}),all:()=>data},$,{id},data).json;
}
test('canonical identity and intended strategy survive commissioning',()=>{
 const c=Array.from({length:10},(_,i)=>({topic:`Distinct fact ${i} about a different subject`,score:100-i,strategy_arm:'explore',canonical_key:`s${i}|m|p`,subject_key:`s${i}`,mechanism_key:'m',payoff_key:'p'}));
 const response={choices:[{message:{content:JSON.stringify({candidates:c})},finish_reason:'stop'}]};
 const pool=run('Parse Topic Pool',response);
 assert.equal(pool.pool[0].strategy_arm,'explore');assert.equal(pool.pool[0].canonical_key,c[0].canonical_key);
 const prior={'Parse Topic Pool':pool,'Ensure Topics Array':{topics:[]},'Deduplicate Topic Pool':{shortlist:c}};
 const picked=run('Extract Generated Topic',response,prior);
 assert.equal(picked.canonical_key,c[0].canonical_key);assert.equal(picked.strategy_arm,'explore');
 assert.throws(()=>run('Extract Generated Topic',response,{...prior,'Deduplicate Topic Pool':{shortlist:[]}}),/approved/);
});
test('topic generation prompt references measured archetype performance, not only the static seed list',()=>{
 const body=n['Claude: Generate Topic'].parameters.jsonBody;
 assert.match(body,/MEASURED ARCHETYPE PERFORMANCE.*JSON\.stringify\(\$\('Get Channel Insights'\)\.item\.json\.archetype_performance \|\| \{\}\)/);
 assert.match(body,/fall back to these seed archetypes/);
});
test('all four joint hook/outro cells receive assignments under a new experiment version',()=>{
 const {assignHookExperiment,HOOK_EXPERIMENT}=require('../retentionPolicy');
 assert.equal(HOOK_EXPERIMENT,'hook-opening-v2');
 const v=n['Validate Final Script'].parameters.jsCode;
 const outro=v.slice(v.indexOf('const _gv2ExperimentSeed='),v.indexOf('if(Array.isArray(parsed.scenes))parsed.scenes=parsed.scenes.filter'));
 const cells={};for(let i=1;i<=2000;i++){const p={};new Function('parsed','$execution',outro)(p,{id:String(i)});const key=assignHookExperiment(String(i)).arm+'/'+p.outro_experiment_arm;cells[key]=(cells[key]||0)+1;}
 assert.equal(Object.keys(cells).length,4);for(const count of Object.values(cells))assert.ok(count>350&&count<650);
});
test('render controls and asset diagnostics survive the exact production merge',()=>{
 const script={hook:'Hook',full_script:'Script',caption_mode:'minimal',creative_format:'minimal_proof',engagement_mode:'none'};
 const accepted={...script,script_snapshot:script};
 const merged=run('Merge By scene_index (not position)',[{json:{data:[{scene_index:0,images:['test.jpg'],asset_score:91,retrieval:{score:91}}]}},{json:{data:[{scene_index:0,audio:{audio_base64:'test'}}]}}],{'Capture Accepted Script':accepted});
 assert.equal(merged.caption_mode,'minimal');assert.equal(merged.creative_format,'minimal_proof');assert.equal(merged.data[0].asset_score,91);assert.equal(merged.data[0].retrieval.score,91);
 assert.equal(n['Start Compose Job'].parameters.jsonBody,'={{ $json }}');
});
test('publication checkpoint precedes optional updates and cleanup has an implementation',()=>{
 assert.equal(w.connections['YouTube: Upload Draft'].main[0][0].node,'Log Published Video');
 assert.equal(w.connections['Log Published Video'].main[0][0].node,'Disclose AI-Generated Content');
 assert.ok(!n['Tag Video with scene_index']);
 assert.equal((compose.match(/app.post\("\/performance\/log"/g)||[]).length,1);
 assert.ok(compose.includes('installLifecycle'));assert.ok(!compose.includes('jobStore.delete(req.params.jobId)'));
});
test('first-comment share CTA names the specific recipient when the topic stage provided one, else falls back to the generic ask',()=>{
 const body=n['Post First Comment'].parameters.jsonBody;
 const stripped=body.replace(/^=\{\{/,'').replace(/\}\}$/,'');
 assert.doesNotThrow(()=>new Function('$',stripped));
 const mk=(json)=>({item:{json},first:()=>({json})});
 const evalWith=(sendToPerson)=>{
  const $=(key)=>{
   if(key==='YouTube: Upload Draft')return mk({uploadId:'vid123'});
   if(key==='Merge By scene_index (not position)')return mk({script_snapshot:{comment_hook:'Would you have guessed this? Yes or no?'}});
   if(key==='Extract Generated Topic')return mk({send_to_person:sendToPerson});
   return mk({});
  };
  const fn=new Function('$','return '+stripped.replace(/^=/,''));
  return JSON.parse(fn($)).snippet.topLevelComment.snippet.textOriginal;
 };
 const named=evalWith('the friend who never believes wild facts');
 assert.match(named,/send this to the friend who never believes wild facts\./);
 assert.doesNotMatch(named,/someone who needs to see it/);
 const fallback=evalWith('');
 assert.match(fallback,/send this to someone who needs to see it\./);
});
test('source cap retains exact Wikipedia hit and every populated provider',()=>{
 const {balancedPool}=require('../sourcePool');const sources=['pexels','pexels_video','pixabay','pixabay_video','unsplash','wikimedia','openverse','nasa'];
 const groups=sources.map(s=>Array.from({length:12},(_,i)=>({source:s,url:`https://example.invalid/${s}/${i}`})));
 groups.push([{source:'wikipedia',url:'https://example.invalid/exact'}]);
 const selected=balancedPool(groups,80,['wikipedia','wikimedia']);
 for(const s of [...sources,'wikipedia'])assert.ok(selected.some(c=>c.source===s));assert.equal(selected.length,80);
});
test('transaction serializes concurrent read-modify-write and rejects corrupt history',async()=>{
 const store=require('../jsonStore');const file=path.join(tmp,'history.json');
 await Promise.all(Array.from({length:20},(_,i)=>store.updateJson(file,[],rows=>rows.push(i))));
 assert.equal((await store.readJson(file,[])).length,20);
 fs.writeFileSync(file,'broken');await assert.rejects(store.updateJson(file,[],rows=>rows.push(21)));
 assert.equal(fs.readFileSync(file,'utf8'),'broken');
});
test('gateway owns the deadline and workflow disables duplicate transport retries',()=>{
 for(const node of w.nodes.filter(x=>x.parameters?.url?.startsWith('http://llm-gateway:'))){
  assert.equal(node.retryOnFail,false);
  const deadline=node.parameters.headerParameters.parameters.find(h=>h.name==='x-llm-timeout-ms');
  assert.ok(Number(deadline.value)<node.parameters.options.timeout);
 }
});
test('feedback API retains all simultaneous publications and visual evidence',async()=>{
 process.env.TOPIC_HISTORY_PATH=path.join(tmp,'feedback','topic_history.json');
 const feedback=require('../feedbackLoop');
 await Promise.all(Array.from({length:20},(_,i)=>feedback.logPublished({video_id:`v${i}`,retrieval_telemetry:[{scene_index:0,score:90}]})));
 const rows=JSON.parse(fs.readFileSync(path.join(tmp,'feedback','performance_history.json')));
 assert.equal(rows.length,20);assert.equal(rows[0].retrieval_telemetry[0].score,90);
});
test('durable completion survives restart and cleanup refuses arbitrary paths',async()=>{
 const {DurableJobs,installLifecycle}=require('../jobLifecycle');
 const express=require('express');const dir=path.join(tmp,'jobs');const id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
 const jobs=new DurableJobs(dir);jobs.set(id,{status:'done',result:{success:true},finishedAt:Date.now()});
 const restored=new DurableJobs(dir);assert.equal(restored.get(id).status,'done');assert.equal(restored.get(id).status,'done');
 const app=express();installLifecycle(app,jobs,tmp);const server=app.listen(0,'127.0.0.1');await new Promise(r=>server.once('listening',r));
 const url=`http://127.0.0.1:${server.address().port}`;
 try {
  assert.equal((await fetch(url+'/cleanup/not-a-render',{method:'DELETE'})).status,400);
  const file=path.join(tmp,`short_${id}.mp4`);fs.writeFileSync(file,'test');
  assert.equal((await fetch(url+`/cleanup/short_${id}`,{method:'DELETE'})).status,200);assert.ok(!fs.existsSync(file));
  assert.equal((await fetch(url+'/admin/drain',{method:'POST'})).status,200);
  assert.equal((await fetch(url+'/compose',{method:'POST'})).status,503);
 }finally{clearInterval(jobs.timer);clearInterval(restored.timer);await new Promise(r=>server.close(r));}
});
test('writer prompt lifts the hype-word ban but keeps the fact-accuracy hard line intact',()=>{
 const body=n['Claude: Draft Script (Stage 1)'].parameters.jsonBody;
 assert.ok(!body.includes('Generic hype words are also banned'),'old blanket hype-word ban must be gone');
 assert.ok(!body.includes('the hype words are banned and saying them out loud'),'old MAKE IT FEEL IMPOSSIBLE ban must be gone');
 assert.match(body,/Hype words are now a deliberate tool, not banned/);
 assert.match(body,/HYPE IS A GOAL, NOT A RISK/);
 assert.match(body,/Engineer the collision first so their brain already thinks/);
 // The one non-negotiable limit must survive every edit to this prompt.
 assert.match(body,/never invent a fact, statistic, or event that is not real/);
 const stripped=body.replace(/^=\{\{/,'').replace(/\}\}$/,'');
 assert.doesNotThrow(()=>new Function('$',stripped));
});
test('published creative DNA captures hook_type and hook_candidates from the accepted script',()=>{
 const body=n['Log Published Video'].parameters.jsonBody;
 assert.match(body,/concept_archetype:.*hook_type: \$\('Merge By scene_index \(not position\)'\)\.first\(\)\.json\.script_snapshot\.hook_type \|\| null/);
 assert.match(body,/hook_candidates: \$\('Merge By scene_index \(not position\)'\)\.first\(\)\.json\.script_snapshot\.hook_candidates \|\| null/);
});
test('fallback excludes outro from the content-template count',()=>{
 const body=n['Resolve B-roll'].parameters.jsonBody;
 assert.match(body,/planned_template_count:.*!sc.template_data\?\.is_outro/);
 const resolver=fs.readFileSync(path.join(tmp,'brollResolver.js'),'utf8');
 assert.match(resolver,/if \(!candidates.length\) \{\s*const fallback = templateFallbackResult/);
});
