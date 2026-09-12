const test=require('node:test');
const assert=require('node:assert/strict');
const p=require('../retentionPolicy');
const feedback=require('../feedbackLoop');

test('assignment is reproducible and splits one hook variable',()=>{
  const counts={};
  for(let i=0;i<1000;i++) {const a=p.assignHookExperiment(`run-${i}`);assert.deepEqual(a,p.assignHookExperiment(`run-${i}`));counts[a.arm]=(counts[a.arm]||0)+1;}
  assert.ok(counts.concrete_question>400 && counts.concrete_question<600);
});
test('unknown counts stay null and percentages retain their documented units',()=>{
  const result=feedback.parseAnalytics({columnHeaders:[{name:'video'},{name:'views'},{name:'videoThumbnailImpressionsClickRate'}],rows:[['v',1000,0.5]]}).v;
  assert.equal(result.engaged_views,null);assert.equal(result.comments,null);assert.equal(result.comment_rate,null);
  assert.equal(result.click_through_rate,0.005);assert.equal(result.net_subscribers,null);
  assert.equal(result.stayed_to_watch_pct,null);assert.equal(result.diagnostics.hold,'insufficient_data');
  assert.equal(p.ratio('',100),null);assert.equal(p.ratio(0,100),0);assert.equal(p.ratio(1,0),null);
});
test('metrics separate weak retention from missing data and do not reject rewatch values',()=>{
  const d=p.diagnoseMetrics({views:1000,engaged_views:200,average_view_percentage:40,likes:2,shares:0,comments:0});
  assert.equal(d.hold,'investigate_retention');assert.equal(d.scroll_stop,'insufficient_data');assert.equal(d.advisory_only,true);
  const replay=p.diagnoseMetrics({views:7,engaged_views:2,average_view_percentage:762.06});
  assert.equal(replay.hold,'insufficient_data');assert.ok(replay.flags.includes('rewatch_possible'));
});
test('payoff estimates use narration weight and measured timing uses actual scene durations',()=>{
  const script={payoff:{resolved_in_scene:1},scenes:[{scene_index:0,narration:'one two three four'},{scene_index:1,narration:'five'},{scene_index:2,narration:'six seven'}]};
  assert.equal(p.creativeDiagnostics(script).payoff_start_word_fraction,4/7);
  const timing=p.renderedTiming(script,[...script.scenes,{scene_index:3,template_data:{is_outro:true}}],[3,5,20,2]);
  assert.equal(timing.payoff_scene_start_sec,3);assert.equal(timing.payoff_scene_end_sec,8);assert.equal(timing.content_duration_sec,28);
  assert.equal(p.renderedTiming({},script.scenes,[3,5,20]).payoff_scene_start_sec,null);
});
test('deep snapshots fill once during the same pass, not hours or days later',()=>{
  const entry={published_at:'2026-09-01T00:00:00Z',snapshots:{t24h:{views:1000,measured_at:'2026-09-02T00:00:00Z'}}};
  const first={measured_at:'2026-09-02T00:10:00Z',retention:{points:5}};
  assert.equal(feedback.attachDeepSnapshot(entry,first),'t24h');
  assert.equal(feedback.attachDeepSnapshot(entry,{...first,retention:{points:99}}),null);
  assert.equal(entry.snapshots.t24h.retention.points,5);
  assert.equal(feedback.attachDeepSnapshot(entry,{measured_at:'2026-09-04T00:10:00Z',traffic:{total_views:4000}}),null);
  assert.equal(entry.snapshots.t24h.traffic,undefined);
});
test('experiment report compares matching cohorts and strata without dropping low-view outcomes',()=>{
  const make=(arm,i,outro='no_outro')=>({video_id:arm+i,caption_style:'neutral',creative_dna:{retention_experiment:{id:p.HOOK_EXPERIMENT,policy:p.RETENTION_POLICY,arm},outro_experiment_arm:outro},snapshots:{t72h:{views:i===0?0:1000,engaged_views:i===0?0:300,average_view_percentage:55}}});
  const history=[...Array.from({length:10},(_,i)=>make('concrete_question',i)),...Array.from({length:10},(_,i)=>make('direct_contradiction',i)),make('direct_contradiction',11,'current_outro')];
  history.push({...make('concrete_question',22),snapshots:{t24h:{views:99999}}});
  const report=p.experimentReport(history);
  assert.equal(report.measured_videos,21);assert.equal(report.strata.length,2);
  assert.equal(report.strata[0].status,'ready_for_review');assert.equal(report.strata[0].arms.concrete_question.videos,10);
  assert.equal(report.strata[0].arms.concrete_question.adequately_sampled_videos,9);
  assert.equal(report.strata[1].status,'collecting');
});
test('creative diagnostics are advisory and keep missing plans visible',()=>{
  const d=p.creativeDiagnostics({hook:'Did you know?',scenes:[],retention_experiment:{arm:'direct_contradiction'}});
  assert.ok(d.warnings.includes('generic_opening'));assert.ok(d.warnings.includes('opening_plan_missing'));assert.equal(d.advisory_only,true);
  assert.equal(d.payoff_start_word_fraction,null);
});
