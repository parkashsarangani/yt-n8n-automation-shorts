// Shared by generated n8n Code nodes and the compositor/analytics service.
// Diagnostics are advisory. These are experiment rules, not YouTube gates.
const RETENTION_POLICY = 'retention-v1';
const HOOK_EXPERIMENT = 'hook-opening-v1';
function finiteNumber(value) {
  if (value == null || typeof value === 'boolean' || String(value).trim() === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
function ratio(n, d) {
  n = finiteNumber(n); d = finiteNumber(d);
  return n !== null && n >= 0 && d !== null && d > 0 ? n / d : null;
}
function assignHookExperiment(seed) {
  let hash = 2166136261;
  for (const char of String(seed)) { hash ^= char.charCodeAt(0); hash = Math.imul(hash, 16777619); }
  return {id: HOOK_EXPERIMENT, policy: RETENTION_POLICY, arm: (hash >>> 0) % 2 ? 'concrete_question' : 'direct_contradiction', assignment_unit: 'execution', minimum_videos_per_arm: 10};
}
function contentScenes(script) {
  return (Array.isArray(script?.scenes) ? script.scenes : []).filter(s => s && !s.template_data?.is_outro);
}
function wordCount(text) { return String(text || '').trim().split(/\s+/).filter(Boolean).length; }
function creativeDiagnostics(script) {
  const scenes = contentScenes(script), first = scenes[0] || {};
  const words = scenes.map(s => wordCount(s.narration));
  const total = words.reduce((a,b) => a+b, 0);
  const payoffId = finiteNumber(script.payoff?.resolved_in_scene);
  const payoff = payoffId === null ? -1 : scenes.findIndex(s => Number(s.scene_index) === payoffId);
  const payoffStart = payoff >= 0 && total ? words.slice(0,payoff).reduce((a,b)=>a+b,0)/total : null;
  const hook = String(first.narration || script.hook || '').trim();
  const observedHook = hook.split(/[.!]/)[0].includes('?') ? 'concrete_question' : 'direct_contradiction';
  const brief = script.retention_brief && typeof script.retention_brief === 'object' ? script.retention_brief : {};
  const warnings = [];
  if (/^(did you know|in this video|today we|let.?s explore|you won.?t believe|have you ever wondered)/i.test(hook)) warnings.push('generic_opening');
  if (wordCount(hook.split(/[.!?]/)[0]) > 12) warnings.push('long_opening_sentence');
  if (payoffStart !== null && payoffStart > 0.7) warnings.push('late_payoff_estimate');
  if (!brief.first_frame_claim || !brief.first_3_second_proof) warnings.push('opening_plan_missing');
  if (first.visual_source === 'template') warnings.push('template_opening');
  if (!script.comment_hook || /^(did (this|you)|what do you think|comment|like|subscribe|follow)/i.test(script.comment_hook)) warnings.push('generic_or_missing_question');
  if (script.retention_experiment?.arm && observedHook !== script.retention_experiment.arm) warnings.push('hook_assignment_not_observed');
  return {policy:RETENTION_POLICY, advisory_only:true, hook_type_observed:observedHook,
    hook_type_method:'first_sentence_punctuation_proxy', first_frame_claim:brief.first_frame_claim || null,
    first_3_second_proof:brief.first_3_second_proof || null, comment_prompt_type:brief.comment_prompt_type || null,
    share_trigger:brief.share_trigger || null, payoff_start_word_fraction:payoffStart,
    payoff_timing_method:'narration_word_estimate', warnings};
}
function renderedTiming(script, scenes, durations) {
  const timeline = []; let offset = 0;
  for (let i=0;i<scenes.length;i++) {
    const duration = finiteNumber(durations[i]);
    if (duration === null || duration <= 0) return {status:'invalid_durations',timeline:[]};
    timeline.push({scene_index:scenes[i].scene_index,start_sec:offset,end_sec:offset+duration,is_outro:Boolean(scenes[i].template_data?.is_outro)});
    offset += duration;
  }
  const content = timeline.filter(s=>!s.is_outro);
  const contentDuration = content.reduce((n,s)=>n+s.end_sec-s.start_sec,0);
  const payoffId = finiteNumber(script?.payoff?.resolved_in_scene);
  const payoff = payoffId === null ? null : content.find(s=>Number(s.scene_index)===payoffId);
  return {status:'measured_scene_boundaries',timeline,content_duration_sec:contentDuration,
    payoff_scene_start_sec:payoff?.start_sec ?? null, payoff_scene_end_sec:payoff?.end_sec ?? null,
    payoff_scene_start_fraction:payoff && contentDuration ? payoff.start_sec/contentDuration : null,
    note:'Scene bounds, not verified spoken payoff or visual-proof timestamps.'};
}
function diagnoseMetrics(metrics={}) {
  const views=finiteNumber(metrics.views), engaged=finiteNumber(metrics.engaged_views);
  const stayed=finiteNumber(metrics.stayed_to_watch_pct), apv=finiteNumber(metrics.average_view_percentage);
  const flags=[];
  if(views===null)flags.push('views_unavailable');
  if(engaged===null || engaged<100)flags.push('insufficient_engaged_sample');
  if(apv!==null && apv>100)flags.push('rewatch_possible');
  if(stayed===null)flags.push('stayed_to_watch_unavailable');
  return {policy:RETENTION_POLICY,advisory_only:true,flags,
    hold:engaged===null || engaged<100 || apv===null ? 'insufficient_data' : apv<45 ? 'investigate_retention' : 'observe',
    scroll_stop:views===null || views<500 || stayed===null ? 'insufficient_data' : stayed<25 ? 'investigate_opening' : 'observe',
    satisfaction:views===null || views<500 || [metrics.likes,metrics.shares,metrics.comments].some(v=>finiteNumber(v)===null) ? 'insufficient_data' : ratio(metrics.likes,views)<0.005 && Number(metrics.shares)===0 && Number(metrics.comments)===0 ? 'investigate_value_and_participation' : 'observe',
    thresholds:'Internal diagnostic hypotheses; no prediction of recommendation eligibility.'};
}
function median(values) {
  values=values.map(finiteNumber).filter(v=>v!==null).sort((a,b)=>a-b);
  if(!values.length)return null;
  const m=Math.floor(values.length/2);return values.length%2 ? values[m] : (values[m-1]+values[m])/2;
}
function experimentReport(history) {
  // Always t72h; a missing or late cohort never becomes a younger cohort.
  const eligible=history.filter(h=>h.creative_dna?.retention_experiment?.id===HOOK_EXPERIMENT && h.snapshots?.t72h);
  const strata={};
  for(const h of eligible) {
    const dna=h.creative_dna, arm=dna.retention_experiment.arm;
    if(!['concrete_question','direct_contradiction'].includes(arm))continue;
    const key=JSON.stringify([dna.retention_experiment.policy,dna.outro_experiment_arm,h.caption_style]);
    const stratum=strata[key] ||= {policy:dna.retention_experiment.policy,outro_arm:dna.outro_experiment_arm,caption_style:h.caption_style,arms:{}};
    (stratum.arms[arm] ||= []).push(h);
  }
  const summarize=rows=>({videos:rows.length,video_ids:rows.map(r=>r.video_id),
    adequately_sampled_videos:rows.filter(r=>finiteNumber(r.snapshots.t72h.engaged_views)>=100).length,
    median_views:median(rows.map(r=>r.snapshots.t72h.views)),
    median_engaged_view_rate:median(rows.map(r=>r.snapshots.t72h.engaged_view_rate)),
    median_apv:median(rows.filter(r=>finiteNumber(r.snapshots.t72h.engaged_views)>=100).map(r=>r.snapshots.t72h.average_view_percentage)),
    median_stayed_to_watch_pct:median(rows.map(r=>r.snapshots.t72h.stayed_to_watch_pct)),
    assignment_mismatches:rows.filter(r=>r.creative_dna.retention_diagnostics?.warnings?.includes('hook_assignment_not_observed')).length});
  return {id:HOOK_EXPERIMENT,cohort:'t72h',measured_videos:eligible.length,minimum_videos_per_arm:10,
    inference:'Descriptive intention-to-treat comparison; no automatic winner or second-push guarantee. Topic and audience differences remain.',
    strata:Object.values(strata).map(s=>{
      const a=s.arms.direct_contradiction||[], b=s.arms.concrete_question||[];
      return {...s,status:a.length>=10 && b.length>=10 ? 'ready_for_review' : 'collecting',arms:{direct_contradiction:summarize(a),concrete_question:summarize(b)}};
    })};
}
module.exports={RETENTION_POLICY,HOOK_EXPERIMENT,finiteNumber,ratio,assignHookExperiment,creativeDiagnostics,renderedTiming,diagnoseMetrics,experimentReport};
