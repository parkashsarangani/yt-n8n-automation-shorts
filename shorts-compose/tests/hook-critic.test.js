const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const root = path.resolve(__dirname, '../..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'shorts-hook-critic-'));
execFileSync(process.platform === 'win32' ? 'python' : 'python3', ['scripts/build_production_artifacts.py', '--output-dir', tmp], { cwd: root, stdio: 'pipe' });
const w = JSON.parse(fs.readFileSync(path.join(tmp, 'workflow.json')));
const n = Object.fromEntries(w.nodes.map((x) => [x.name, x]));
test.after(() => fs.rmSync(tmp, { recursive: true, force: true }));

function run(name, data, prior = {}, id = '42') {
  const $ = (key) => ({ first: () => ({ json: prior[key] || {} }), item: { json: prior[key] || {} } });
  // Validate Final Script's partial-recovery/best-effort paths call
  // $getWorkflowStaticData('global') - provide the same simplified mock
  // (ignores the scope argument, returns one shared mutable object) that
  // writer-publication-contract.test.js already uses for this same node.
  const staticData = {};
  return new Function('$input', '$', '$execution', '$getWorkflowStaticData', n[name].parameters.jsCode)(
    { first: () => ({ json: data }), all: () => data },
    $,
    { id },
    () => staticData
  ).json;
}

test('hook critic node exists and is wired between Parse Draft JSON and Claude: Visual Director', () => {
  // DRAFT_RETRY_LOOP gates the critic on a usable draft (true branch).
  assert.equal(w.connections['Parse Draft JSON'].main[0][0].node, 'If Draft Parsed');
  assert.equal(w.connections['If Draft Parsed'].main[0][0].node, 'Claude: Critique Hooks');
  assert.equal(w.connections['Claude: Critique Hooks'].main[0][0].node, 'Apply Hook Critique');
  assert.equal(w.connections['Apply Hook Critique'].main[0][0].node, 'Claude: Visual Director');
});

test('critic node fails open at the n8n level too, not only in Apply Hook Critique\'s JS', () => {
  // Apply Hook Critique's fail-open logic never runs at all if the critic node
  // itself aborts the whole execution on a real API error - n8n's default
  // onError is "stopWorkflow". Without this explicit override, a single failed
  // critic call (timeout, 429, gateway hiccup) would kill an otherwise-healthy
  // publish run, exactly the failure mode this feature exists to avoid.
  assert.equal(n['Claude: Critique Hooks'].onError, 'continueRegularOutput');
});

test('critic prompt sends the real candidates, topic, and archetype performance', () => {
  const body = n['Claude: Critique Hooks'].parameters.jsonBody;
  assert.match(body, /INDEPENDENT HOOK CRITIC/);
  assert.match(body, /hook_candidates/);
  assert.match(body, /\$\('Extract Generated Topic'\)\.item\|\|\{\}\)\.json\|\|\{\}\)\.topic/);
  assert.match(body, /\$\('Get Channel Insights'\)\.item\.json\.archetype_performance/);
  // Syntax sanity: this exact node bit Phase 3 with nested-escaping mistakes,
  // so confirm it's still valid JS after stripping the n8n expression wrapper.
  const stripped = body.replace(/^=\{\{/, '').replace(/\}\}$/, '');
  assert.doesNotThrow(() => new Function('$', stripped));
});

test('Apply Hook Critique overwrites hook/hook_type with the critic pick and records telemetry', () => {
  const draft = { hook: 'Writer original hook', hook_type: 'curiosity_gap', hook_candidates: ['Cand A', 'Cand B', 'Cand C'] };
  const critique = { chosen_index: 2, chosen_hook_type: 'disbelief', scores: [{ index: 2, total: 91 }] };
  const response = { choices: [{ message: { content: JSON.stringify(critique) } }] };
  const merged = run('Apply Hook Critique', response, { 'Parse Draft JSON': { draft } });
  assert.equal(merged.draft.hook, 'Cand C');
  assert.equal(merged.draft.hook_type, 'disbelief');
  assert.equal(merged.draft.hook_critique.chosen_index, 2);
  assert.equal(merged.draft.hook_critique.original_hook, 'Writer original hook');
  assert.deepEqual(merged.draft.hook_candidates, ['Cand A', 'Cand B', 'Cand C']);
});

test('Apply Hook Critique fails open on API error, unparseable content, and out-of-range index', () => {
  const draft = { hook: 'Writer original hook', hook_type: 'curiosity_gap', hook_candidates: ['Cand A', 'Cand B'] };
  const prior = { 'Parse Draft JSON': { draft } };

  const apiError = run('Apply Hook Critique', { error: { message: 'gateway down' } }, prior);
  assert.equal(apiError.draft.hook, 'Writer original hook');
  assert.ok(!apiError.draft.hook_critique);

  const garbage = run('Apply Hook Critique', { choices: [{ message: { content: 'definitely not json' } }] }, prior);
  assert.equal(garbage.draft.hook, 'Writer original hook');

  const outOfRange = run('Apply Hook Critique', { choices: [{ message: { content: JSON.stringify({ chosen_index: 99 }) } }] }, prior);
  assert.equal(outOfRange.draft.hook, 'Writer original hook');
});

test('Apply Hook Critique fails open when the writer produced no usable hook_candidates', () => {
  const draft = { hook: 'Only hook, no candidates array' };
  const critique = { chosen_index: 0 };
  const response = { choices: [{ message: { content: JSON.stringify(critique) } }] };
  const merged = run('Apply Hook Critique', response, { 'Parse Draft JSON': { draft } });
  assert.equal(merged.draft.hook, 'Only hook, no candidates array');
  assert.ok(!merged.draft.hook_critique);
});

test('hard opening gate rejects a generic scene-0 narration and passes a specific one', () => {
  const base = {
    hook: 'placeholder',
    title: 'A Wild Volcano Fact That Actually Happened',
    caption_style: 'funny',
    trigger: 'disbelief',
    payoff: { claim: 'the wild fact', resolved_in_scene: 1 },
    tags: ['facts', 'shorts', 'science facts', 'curiosity', 'interesting facts'],
    seo_description: 'A description that is definitely long enough to pass validation.',
    comment_hook: 'Would you have guessed this? Yes or no?',
    quality: {
      concept_strength: 80, hook_strength: 80, evidence_strength: 80, payoff_strength: 80,
      information_density: 80, first_frame_strength: 80, visual_progression: 80, shareability: 80,
      naturalness: 80, distinctiveness: 80, voice_specificity: 80, overall: 80, visual_plan_quality: 80,
    },
    scenes: [
      { scene_index: 0, point: 'p0', visual_role: 'hero', narration: '', visual_source: 'stock', visual_prompt: 'a real volcano erupting at night with lava', negative_prompt: 'no readable text', stock_search_query: 'volcano eruption' },
      { scene_index: 1, point: 'p1', visual_role: 'payoff', narration: 'The lava reveals something genuinely surprising about how fast it cools down here', visual_source: 'stock', visual_prompt: 'a real volcano close up detail shot', negative_prompt: 'no readable text', stock_search_query: 'volcano detail' },
      { scene_index: 2, point: 'p2', visual_role: 'breath', narration: 'That single detail changes how scientists study every volcano on this list', visual_source: 'stock', visual_prompt: 'a real volcano wide landscape shot', negative_prompt: 'no readable text', stock_search_query: 'volcano landscape' },
    ],
  };
  const withOpening = (narration) => {
    const script = JSON.parse(JSON.stringify(base));
    script.scenes[0].narration = narration;
    const response = { choices: [{ message: { content: JSON.stringify(script) }, finish_reason: 'stop' } ] };
    return run('Validate Final Script', response);
  };
  const generic = withOpening('Did you know volcanoes can grow taller every single summer?');
  assert.equal(generic._scriptValid, false);
  assert.ok(generic._validationErrors.some((e) => e.includes('generic phrase')));

  const specific = withOpening('Volcanoes grow measurably taller every single summer without anyone noticing.');
  assert.ok(!(specific._validationErrors || []).some((e) => e.includes('generic phrase')));
});
