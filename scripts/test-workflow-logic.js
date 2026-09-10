#!/usr/bin/env node
/**
 * Standalone regression test for the n8n workflow's deploy-time transform
 * chain and the JS logic embedded in its Code nodes.
 *
 * This does NOT touch the production server, n8n, or any API - it builds the
 * fully-transformed workflow.json locally (by actually running the same
 * Python transform chain deploy.yml runs) and then exercises the resulting
 * node code with `new Function()` against synthetic inputs, the same way a
 * real n8n Code/HTTP node would receive them.
 *
 * Usage: node scripts/test-workflow-logic.js
 * Exit code 0 = all pass, 1 = at least one failure.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..');
const PY = process.platform === 'win32' ? 'python' : 'python3';

// ---------------------------------------------------------------------------
// Minimal test harness (zero dependencies)
// ---------------------------------------------------------------------------
let pass = 0;
let fail = 0;
const failures = [];

function check(label, condition, detail) {
  if (condition) {
    pass += 1;
    console.log(`  ✓ ${label}`);
  } else {
    fail += 1;
    failures.push(label + (detail ? ` -- ${detail}` : ''));
    console.log(`  ✗ ${label}${detail ? ` -- ${detail}` : ''}`);
  }
}

function section(title) {
  console.log(`\n${title}`);
}

function throws(fn, label, matcher) {
  try {
    fn();
    check(label, false, 'expected to throw, did not');
  } catch (e) {
    const ok = matcher ? matcher(e.message) : true;
    check(label, ok, ok ? undefined : `wrong error: ${e.message}`);
  }
}

function doesNotThrow(fn, label) {
  try {
    fn();
    check(label, true);
  } catch (e) {
    check(label, false, `threw: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// Build the fully-transformed workflow by running the real deploy-time chain
// ---------------------------------------------------------------------------
function buildWorkflow() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-test-'));
  const scriptsDir = path.join(tmp, 'scripts');
  const shortsDir = path.join(tmp, 'shorts-compose');
  const n8nDir = path.join(tmp, 'n8n');
  fs.mkdirSync(scriptsDir);
  fs.mkdirSync(shortsDir);
  fs.mkdirSync(n8nDir);

  for (const f of fs.readdirSync(path.join(REPO_ROOT, 'scripts'))) {
    if (f.endsWith('.py')) {
      fs.copyFileSync(path.join(REPO_ROOT, 'scripts', f), path.join(scriptsDir, f));
    }
  }
  fs.copyFileSync(
    path.join(REPO_ROOT, 'shorts-compose', 'channel-voice.json'),
    path.join(shortsDir, 'channel-voice.json'),
  );
  fs.copyFileSync(path.join(REPO_ROOT, 'n8n', 'workflow.json'), path.join(n8nDir, 'workflow.json'));
  fs.copyFileSync(path.join(REPO_ROOT, 'docker-compose.yml'), path.join(tmp, 'docker-compose.yml'));

  const steps = [
    ['upgrade-viral-shorts.py', 'workflow.json', 'v1.json'],
    ['upgrade-creative-system.py', 'v1.json', 'v2.json'],
    ['visual_matching_v4_workflow.py', 'v2.json', 'v2v4.json'],
    ['upgrade-workflow-api-budget.py', 'v2v4.json', 'v3.json'],
    ['upgrade-topic-latency.py', 'v3.json', 'v4.json'],
    ['upgrade-anthropic-parser.py', 'v4.json', 'final.json'],
  ];
  for (const [script, input, output] of steps) {
    execFileSync(PY, [path.join(scriptsDir, script), path.join(n8nDir, input), path.join(n8nDir, output)], {
      cwd: tmp,
      stdio: 'pipe',
    });
  }

  const finalPath = path.join(n8nDir, 'final.json');
  const raw = fs.readFileSync(finalPath, 'utf8');
  return { workflow: JSON.parse(raw), tmpDir: tmp };
}

function nodeMap(workflow) {
  const m = {};
  for (const n of workflow.nodes) m[n.name] = n;
  return m;
}

function runNodeCode(nodes, nodeName, input, extraGlobals) {
  const node = nodes[nodeName];
  if (!node) throw new Error(`node not found: ${nodeName}`);
  const staticData = {};
  const args = ['$input', '$execution', '$getWorkflowStaticData'];
  const vals = [
    { first: () => ({ json: input }) },
    { id: 'workflow-test-execution' },
    () => staticData,
  ];
  if (extraGlobals) {
    for (const [k, v] of Object.entries(extraGlobals)) {
      args.push(k);
      vals.push(v);
    }
  }
  const fn = new Function(...args, node.parameters.jsCode);
  return fn(...vals);
}

function openaiResponse(text, finishReason) {
  return { choices: [{ message: { content: text }, finish_reason: finishReason || 'stop' }] };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
function main() {
  console.log('Building fully-transformed workflow via the real 5-stage deploy chain...');
  const { workflow, tmpDir } = buildWorkflow();
  const nodes = nodeMap(workflow);
  console.log(`Built OK: ${workflow.nodes.length} nodes.\n`);
  console.log('='.repeat(70));

  // -------------------------------------------------------------------
  section('1. Overall workflow structure');
  // -------------------------------------------------------------------
  doesNotThrow(() => JSON.stringify(workflow), 'workflow serializes as valid JSON');
  check('exactly one workflow-level node array', Array.isArray(workflow.nodes), undefined);

  const seenPos = new Map();
  let collisions = 0;
  for (const n of workflow.nodes) {
    const key = n.position.join(',');
    if (seenPos.has(key)) { collisions += 1; console.log(`    collision at ${key}: ${seenPos.get(key)} vs ${n.name}`); }
    seenPos.set(key, n.name);
  }
  check('zero node position collisions', collisions === 0, `${collisions} collisions`);

  const c = workflow.connections;
  const first = (src) => c[src] && c[src].main[0][0] && c[src].main[0][0].node;
  const second = (src) => c[src] && c[src].main[1] && c[src].main[1][0] && c[src].main[1][0].node;
  check('topic chain: Generate Topic -> Parse Topic Pool', first('Claude: Generate Topic') === 'Parse Topic Pool');
  check('topic chain: Parse Topic Pool -> Commission Shortlist', first('Parse Topic Pool') === 'Claude: Commission Topic Shortlist');
  check('topic chain: Commission Shortlist -> Extract Generated Topic', first('Claude: Commission Topic Shortlist') === 'Extract Generated Topic');
  check('script chain: Draft Script -> Parse Draft JSON', first('Claude: Draft Script (Stage 1)') === 'Parse Draft JSON');
  check('script chain: Parse Draft JSON -> Visual Director (Editorial Rewrite removed)', first('Parse Draft JSON') === 'Claude: Visual Director');
  check('script chain: Visual Director -> Validate Final Script', first('Claude: Visual Director') === 'Validate Final Script');
  check('Claude: Editorial Rewrite (Stage 2) no longer exists in the graph', !nodes['Claude: Editorial Rewrite (Stage 2)']);
  check('Parse Editorial For Visual Director no longer exists in the graph', !nodes['Parse Editorial For Visual Director']);

  // n8n's deploy API rejects the whole workflow (400 unknown_connection_source)
  // if the connections dict has a key or a target naming a node that isn't in
  // the nodes array - production incident: a node deletion cleaned up the
  // node itself but left a dangling connections entry inherited from an
  // earlier patch stage, and the deploy failed against the real n8n API even
  // though every other local check (including preprod-audit.py at the time)
  // passed, since none of them talk to a real n8n instance.
  {
    const nodeNameSet = new Set(Object.keys(nodes));
    const danglingSources = Object.keys(c).filter((src) => !nodeNameSet.has(src));
    check('no connection source references a deleted node', danglingSources.length === 0, JSON.stringify(danglingSources));
    const danglingTargets = [];
    for (const [src, out] of Object.entries(c)) {
      for (const branch of out.main || []) {
        for (const edge of branch || []) {
          if (!nodeNameSet.has(edge.node)) danglingTargets.push(`${src} -> ${edge.node}`);
        }
      }
    }
    check('no connection target references a deleted node', danglingTargets.length === 0, JSON.stringify(danglingTargets));
  }

  // -------------------------------------------------------------------
  section('2. Request timeouts (the hung-execution bug)');
  // -------------------------------------------------------------------
  const timeoutExpectations = {
    'Claude: Generate Topic': 120000,
    'Claude: Commission Topic Shortlist': 120000,
    'Claude: Visual Director': 180000,
    'Claude: Repair Script': 180000,
    'Claude: Draft Script (Stage 1)': 120000,
    'ElevenLabs: TTS+Timestamps': 60000,
  };
  for (const [name, expected] of Object.entries(timeoutExpectations)) {
    const actual = nodes[name] && nodes[name].parameters.options && nodes[name].parameters.options.timeout;
    check(`${name} has timeout ${expected}`, actual === expected, `got ${actual}`);
  }

  // -------------------------------------------------------------------
  section('3. Retry-attempt counter (the unbounded-loop bug)');
  // -------------------------------------------------------------------
  // IMPORTANT: an earlier version of this test mocked $('NodeName').item as a
  // simple dictionary lookup and the fix appeared to pass - but that mock did
  // not reflect n8n's real behavior. $('NodeName').item depends on
  // paired-item lineage, which the loop-back edge through the independent
  // "Claude: Generate Topic" HTTP call does not reliably preserve. Confirmed
  // live in production: 5 full retry cycles, scriptAttempt=1 every single
  // time. The current implementation uses $getWorkflowStaticData('global'),
  // keyed by $execution.id so concurrent executions of the same workflow
  // cannot see or clobber each other's counters - a plain shared object plus
  // a mocked $execution.id is an accurate mock of it.
  {
    // Workflow static data persists across the whole n8n instance for this
    // workflow, not just within the mock - model that with one shared object
    // both nodes read/write, exactly like the real $getWorkflowStaticData('global').
    const staticStore = {};
    const getStaticData = () => staticStore;

    const initCode = nodes['Init Script Attempt Counter'].parameters.jsCode;
    const incrementCode = nodes['Increment Script Attempt'].parameters.jsCode;

    function runInit(inputItems, executionId) {
      const fn = new Function(
        '$getWorkflowStaticData', '$input', '$execution',
        'return (function(){' + initCode + '})()',
      );
      return fn(getStaticData, { all: () => inputItems }, { id: executionId });
    }
    function runIncrement(errs, executionId) {
      const fn = new Function(
        '$getWorkflowStaticData', '$input', '$execution',
        'return (function(){' + incrementCode + '})()',
      );
      const result = fn(
        getStaticData,
        { first: () => ({ json: { _validationErrors: errs } }) },
        { id: executionId },
      );
      return result.json.scriptAttempt;
    }

    // Simulate a stale counter left over from a PREVIOUS execution id (static
    // data persists across runs, keyed by execution id) - Init must reset
    // THIS execution's counter, not just skip if the key is already present.
    staticStore.scriptAttempts = { 'run-A': { attempt: 7, updatedAt: Date.now() } };
    runInit([{ json: { topics: [] } }], 'run-A');
    check('Init resets a stale counter for this execution id back to 0', staticStore.scriptAttempts['run-A'].attempt === 0, `got ${staticStore.scriptAttempts['run-A'].attempt}`);

    const seq = [runIncrement(['a'], 'run-A'), runIncrement(['b'], 'run-A'), runIncrement(['c'], 'run-A'), runIncrement(['d'], 'run-A')];
    check('counter increments 1,2,3,4 across loop iterations', JSON.stringify(seq) === JSON.stringify([1, 2, 3, 4]), `got ${JSON.stringify(seq)}`);
    check('counter would now correctly cap at attempt 3 (3 < 3 is false)', !(seq[2] < 3), undefined);

    // A second, concurrent execution id must not see or affect the first
    // execution's counter - this is the exact isolation the 'global' +
    // execution-id-keyed design exists to guarantee.
    runInit([{ json: { topics: [] } }], 'run-B');
    check('a second concurrent execution id starts its own counter at 0', staticStore.scriptAttempts['run-B'].attempt === 0, `got ${staticStore.scriptAttempts['run-B'].attempt}`);
    check("a second execution id's Init does not disturb the first execution's counter", staticStore.scriptAttempts['run-A'].attempt === 4, `got ${staticStore.scriptAttempts['run-A'].attempt}`);
    check('Init passes input items through unchanged', (() => {
      const passed = runInit([{ json: { topics: ['x', 'y'] } }], 'run-C');
      return Array.isArray(passed) && passed[0].json.topics.length === 2;
    })());
    throws(() => runInit([{ json: {} }], ''), 'Init throws without a usable $execution.id rather than silently sharing state');
  }

  // -------------------------------------------------------------------
  section('4. Parse Topic Pool (candidate pool = 4, truncation guard)');
  // -------------------------------------------------------------------
  {
    const mkCandidate = (i) => ({
      topic: `topic ${i}`, archetype: 'looks_fake_but_real', research_query: 'q', first_frame_concept: 'f',
      share_reason: 's', evidence_score: 80, visual_score: 80, share_score: 80, reason: 'r', score: 80,
    });
    const ok4 = openaiResponse(JSON.stringify({ candidates: [1, 2, 3, 4].map(mkCandidate) }));
    doesNotThrow(() => {
      const r = runNodeCode(nodes, 'Parse Topic Pool', ok4);
      check('  -> pool has 4 entries', r.json.pool.length === 4);
      check('  -> shortlist has 4 entries', r.json.shortlist.length === 4);
    }, '4 valid candidates -> succeeds');

    // A short pool used to throw, which lost the whole scheduled run over one
    // bad topic response. It now falls back to evergreen candidates, so the run
    // continues and only topic novelty degrades.
    const tooFew = openaiResponse(JSON.stringify({ candidates: [mkCandidate(1)] }));
    doesNotThrow(() => {
      const r = runNodeCode(nodes, 'Parse Topic Pool', tooFew);
      check('  -> falls back to a usable pool', r.json.pool.length >= 3);
      check('  -> shortlist is still populated', r.json.shortlist.length >= 1);
    }, 'only 1 candidate -> falls back instead of failing the run');

    const truncated = openaiResponse('{"candidates":[' + JSON.stringify(mkCandidate(1)) + ',{"topic":"cut off mid', 'length');
    throws(() => runNodeCode(nodes, 'Parse Topic Pool', truncated), 'max_completion_tokens truncation -> clear diagnostic, not raw SyntaxError',
      (m) => m.includes('cut off by max_completion_tokens'));

    const noTextBlock = openaiResponse('', 'length');
    throws(() => runNodeCode(nodes, 'Parse Topic Pool', noTextBlock), 'no text, finish_reason length -> clear diagnostic',
      (m) => m.includes('max_completion_tokens') && m.includes('before producing'));

    // Regression test for a real production failure: the model wrote a broken
    // false-start object, caught itself mid-response ("Wait, I need to
    // output proper JSON only."), then wrote the correct JSON right after -
    // both landed in the same complete (non-truncated) response. The old
    // naive indexOf('{')..lastIndexOf('}') extraction grabbed from the
    // false start's opening brace to the real JSON's closing brace, mashing
    // garbage and valid JSON together and crashing with a cryptic
    // SyntaxError even though finish_reason was 'stop', not truncated.
    const selfCorrected = openaiResponse(
      '{"candidates":[[]][0] || null}\n\nWait, I need to output proper JSON only.\n\n'
        + JSON.stringify({ candidates: [1, 2, 3, 4].map(mkCandidate) }),
    );
    doesNotThrow(() => {
      const r = runNodeCode(nodes, 'Parse Topic Pool', selfCorrected);
      check('  -> recovers the real 4-candidate JSON after a self-correction false-start', r.json.pool.length === 4);
    }, 'Claude self-correction false-start (real production failure) -> recovers correct JSON instead of crashing');
  }

  // -------------------------------------------------------------------
  section('5. Parse Draft JSON (undefined-return bug)');
  // -------------------------------------------------------------------
  {
    const draftOk = openaiResponse('{"hook":"h","scenes":[]}');
    const r1 = runNodeCode(nodes, 'Parse Draft JSON', draftOk);
    check('Parse Draft JSON returns a real item, not undefined', r1 !== undefined && r1.json && r1.json.draft.hook === 'h');

    const draftTruncated = openaiResponse('', 'length');
    throws(() => runNodeCode(nodes, 'Parse Draft JSON', draftTruncated), 'Parse Draft JSON max_completion_tokens truncation -> clear diagnostic',
      (m) => m.includes('max_completion_tokens'));
  }

  // -------------------------------------------------------------------
  section('6. Validate Final Script - quality gate thresholds + calculation correctness');
  // -------------------------------------------------------------------
  {
    const code = nodes['Validate Final Script'].parameters.jsCode;
    const thresholdMatch = code.match(/qualityMinimums = \{([^}]+)\}/);
    check('qualityMinimums block found', !!thresholdMatch);
    const thresholds = {};
    for (const line of thresholdMatch[1].split(',')) {
      const m = line.match(/(\w+):\s*(\d+)/);
      if (m) thresholds[m[1]] = Number(m[2]);
    }
    const expectedThresholds = {
      concept_strength: 76, hook_strength: 78, evidence_strength: 74, payoff_strength: 76,
      information_density: 74, first_frame_strength: 78, visual_progression: 74, shareability: 76,
      naturalness: 74, distinctiveness: 74, voice_specificity: 72, overall: 77,
    };
    for (const [k, v] of Object.entries(expectedThresholds)) {
      check(`threshold ${k} = ${v}`, thresholds[k] === v, `got ${thresholds[k]}`);
    }
    check('visual_plan_quality threshold = 78', code.includes('below publish threshold 78'), undefined);

    function goodQuality(overrides) {
      return Object.assign({
        concept_strength: 80, hook_strength: 80, evidence_strength: 80, payoff_strength: 80,
        information_density: 80, first_frame_strength: 80, visual_progression: 80,
        shareability: 80, naturalness: 80, distinctiveness: 80, voice_specificity: 80, overall: 80,
      }, overrides);
    }
    const scene = {
      scene_index: 0,
      point: 'the point of this scene',
      narration: 'a real narration line that is definitely long enough to pass',
      visual_source: 'stock',
      visual_type: 'real',
      visual_prompt: 'a real visual prompt describing this specific scene in detail',
      negative_prompt: 'no readable text, no legible numbers',
      stock_search_query: 'a real query',
      search_queries: ['query one', 'query two', 'query three'],
      visual_role: 'hero',
      visual_claim: 'a literal visible scene proof',
      global_subject: 'a recognizable broad topic',
      required_entities: ['visible subject'],
      required_actions: ['visible action'],
      required_relationships: [],
      forbidden_visuals: ['unrelated filler'],
      acceptable_visuals: ['related fallback'],
      visual_proof_mode: 'literal_video',
      visual_mode: 'exact_real',
      must_show: 'visible subject action',
      acceptable_substitutes: ['related fallback'],
      source_priority: ['pexels', 'wikimedia', 'unsplash'],
      template_fallback: { template_name: 'kinetic_text', template_data: { line: 'fallback' } },
    };
    function buildScript(quality) {
      return {
        hook: 'a real hook that is long enough', title: 'a real title', caption_style: 'neutral', trigger: 'disbelief',
        caption_mode: 'karaoke', creative_format: 'documentary_cinematic', engagement_mode: 'none', first_frame_type: 'hero_motion', visual_plan_quality: 84,
        tags: ['a', 'b', 'c', 'd', 'e'], seo_description: 'a description that is long enough to satisfy the minimum length check',
        comment_hook: null, payoff: { claim: 'a specific promise the hook makes', resolved_in_scene: 0 },
        scenes: [scene, { ...scene, scene_index: 1 }, { ...scene, scene_index: 2 }],
        quality,
      };
    }
    // These scenes use generic placeholder text with no real topic, so mock
    // Extract Generated Topic with an empty topic - the topic-substitution
    // backstop below intentionally skips its check when there is nothing to
    // compare against, keeping these unrelated-rule tests unaffected.
    const noTopicDollar = (name) => (name === 'Extract Generated Topic' ? { item: { json: { topic: '' } } } : { item: { json: {} } });
    function validate(quality) {
      const response = openaiResponse(JSON.stringify(buildScript(quality)));
      return runNodeCode(nodes, 'Validate Final Script', response, { $: noTopicDollar });
    }

    const passing = validate(goodQuality());
    check('a genuinely good script (all 80s, consistent overall) passes', passing.json._scriptValid === true, JSON.stringify(passing.json._validationErrors));

    const firstTemplateScene = {
      ...scene,
      visual_source: 'template',
      visual_type: 'template',
      template_name: 'kinetic_text',
      template_data: { line: 'opening template' },
      required_actions: [],
      visual_proof_mode: 'kinetic_text',
    };
    const scriptWithOpeningTemplate = buildScript(goodQuality());
    scriptWithOpeningTemplate.scenes = [firstTemplateScene, { ...scene, scene_index: 1 }, { ...scene, scene_index: 2 }];
    const openingTemplateResult = runNodeCode(nodes, 'Validate Final Script', openaiResponse(JSON.stringify(scriptWithOpeningTemplate)), { $: noTopicDollar });
    check('Validate Final Script rejects a Remotion template opener',
      openingTemplateResult.json._scriptValid === false &&
      openingTemplateResult.json._validationErrors.some((e) => e.includes('scene 0 cannot be a Remotion template')),
      JSON.stringify(openingTemplateResult.json._validationErrors));

    const secondTemplateScene = { ...firstTemplateScene, scene_index: 2, template_data: { line: 'second template' } };
    const scriptWithTwoTemplates = buildScript(goodQuality());
    scriptWithTwoTemplates.scenes = [{ ...scene, scene_index: 0 }, { ...firstTemplateScene, scene_index: 1 }, secondTemplateScene];
    const twoTemplateResult = runNodeCode(nodes, 'Validate Final Script', openaiResponse(JSON.stringify(scriptWithTwoTemplates)), { $: noTopicDollar });
    check('Validate Final Script rejects more than one Remotion template scene',
      twoTemplateResult.json._scriptValid === false &&
      twoTemplateResult.json._validationErrors.some((e) => e.includes('maximum is 1')),
      JSON.stringify(twoTemplateResult.json._validationErrors));

    const belowGate = validate(goodQuality({ hook_strength: 70 }));
    check('a script scoring below the new mid-70s gate is still rejected', belowGate.json._scriptValid === false &&
      belowGate.json._validationErrors.some((e) => e.includes('hook_strength')));
    check('a failed validation preserves the failed script for the repair loop, not just the error list',
      belowGate.json._failedScript && belowGate.json._failedScript.hook === buildScript(goodQuality()).hook,
      JSON.stringify(belowGate.json._failedScript));

    const inflated = validate(goodQuality({ concept_strength: 76, overall: 90 }));
    check('inflated overall (exceeds weakest dimension by >5) is rejected',
      inflated.json._validationErrors && inflated.json._validationErrors.some((e) => e.includes('exceeds the weakest scored dimension')));

    const consistent = validate(goodQuality({ concept_strength: 76, overall: 80 }));
    const hasOverallError = consistent.json._validationErrors && consistent.json._validationErrors.some((e) => e.includes('overall'));
    check('consistent overall (within 5 of weakest dimension) is not flagged by the overall-consistency check', !hasOverallError,
      JSON.stringify(consistent.json._validationErrors));

    // Regression test: production incident where the repair pass added a
    // visual_prompt to a template scene that never needed one (templates
    // render as deterministic graphics, never through AI image generation),
    // and its wording happened to match the readable-text guard - failing
    // the run over a check that should never apply to template scenes.
    const templateSceneWithStrayPrompt = {
      ...scene, scene_index: 1, visual_source: 'template', template_name: 'kinetic_text', template_data: { line: 'x' },
      required_actions: [],
      visual_proof_mode: 'kinetic_text',
      visual_prompt: 'Kinetic text template: a small teaspoon icon showing a readable comparison label',
    };
    const scriptWithStrayTemplatePrompt = buildScript(goodQuality());
    scriptWithStrayTemplatePrompt.scenes = [scene, templateSceneWithStrayPrompt, { ...scene, scene_index: 2 }];
    const strayResponse = openaiResponse(JSON.stringify(scriptWithStrayTemplatePrompt));
    const strayResult = runNodeCode(nodes, 'Validate Final Script', strayResponse, { $: noTopicDollar });
    check('a template scene with a stray visual_prompt is not flagged by the readable-text guard (templates never hit AI image generation)',
      strayResult.json._scriptValid === true, JSON.stringify(strayResult.json._validationErrors));

    // Regression test: production incident (execution 644) where Editorial
    // Rewrite discarded the selected topic (brain surgery / no pain
    // receptors in the brain) and substituted an unrelated, structurally
    // valid script ("The Ocean Floor Has a Layer of Solid Gold Nobody Can
    // Mine") that would otherwise pass every other check here silently.
    const substitutedTopicScript = buildScript(goodQuality());
    Object.assign(substitutedTopicScript, {
      hook: 'There is enough gold dissolved in the ocean to give every person on Earth nine pounds of it',
      title: 'The Ocean Floor Has a Layer of Solid Gold Nobody Can Mine',
    });
    const seedTopicDollar = (name) => (name === 'Extract Generated Topic'
      ? { item: { json: { topic: 'Your tongue can taste and your hands can feel, but you have zero pain receptors inside your brain, so surgeons can operate on a fully conscious brain and the patient feels nothing.' } } }
      : { item: { json: {} } });
    const substitutedResponse = openaiResponse(JSON.stringify(substitutedTopicScript));
    const substitutedResult = runNodeCode(nodes, 'Validate Final Script', substitutedResponse, { $: seedTopicDollar });
    check('a script substituting a completely different topic than the one selected is rejected',
      substitutedResult.json._scriptValid === false &&
      substitutedResult.json._validationErrors.some((e) => e.includes('different topic than the one selected')),
      JSON.stringify(substitutedResult.json._validationErrors));

    // Same seed topic, legitimately heavily rewritten (different vocabulary,
    // same subject) must NOT be flagged - the backstop should not punish
    // aggressive rewrites that stay on-topic.
    const sameTopicRewrite = buildScript(goodQuality());
    Object.assign(sameTopicRewrite, {
      hook: 'A single underwater mountain chain circles the planet and dwarfs every range standing on dry land',
      title: 'The Hidden Ridge Bigger Than Everest Combined',
    });
    const oceanTopicDollar = (name) => (name === 'Extract Generated Topic'
      ? { item: { json: { topic: 'There is a mountain range on Earth longer than the Andes, Rockies, and Himalayas combined, and it is hiding under the ocean.' } } }
      : { item: { json: {} } });
    const sameTopicResponse = openaiResponse(JSON.stringify(sameTopicRewrite));
    const sameTopicResult = runNodeCode(nodes, 'Validate Final Script', sameTopicResponse, { $: oceanTopicDollar });
    check('a heavily reworded script that stays on the same topic is not flagged as a substitution',
      !(sameTopicResult.json._validationErrors || []).some((e) => e.includes('different topic than the one selected')),
      JSON.stringify(sameTopicResult.json._validationErrors));

    // Editorial Rewrite (Stage 2) was removed entirely after repeatedly
    // substituting the selected topic in production, even after two rounds
    // of explicit prompt instructions - Visual Director now consumes Draft
    // Script's raw output directly, so it inherits the same risk and needs
    // the same explicit protection proactively, not just the deterministic
    // backstop above catching it after the fact.
    check('Visual Director prompt explicitly forbids substituting a different topic (inherited the risk when Editorial Rewrite was removed)',
      nodes['Claude: Visual Director'].parameters.jsonBody.includes('topic/subject/fact in the script below is fixed'));
    check('Claude: Repair Script prompt also forbids substituting a different topic (derived from the same Visual Director template)',
      nodes['Claude: Repair Script'].parameters.jsonBody.includes('topic/subject/fact in the script below is fixed'));
  }

  // -------------------------------------------------------------------
  section('6b. Repair loop - a failed quality gate revises the same script, not a fresh topic');
  // -------------------------------------------------------------------
  {
    check('If Under Max Script Attempts retries into Claude: Repair Script, not Claude: Generate Topic',
      first('If Under Max Script Attempts') === 'Claude: Repair Script');
    check('Claude: Repair Script feeds back into Validate Final Script',
      first('Claude: Repair Script') === 'Validate Final Script');
    check('the retry path still falls through to Fail: Script Generation Exhausted once attempts are used up',
      second('If Under Max Script Attempts') === 'Fail: Script Generation Exhausted');

    const repairBody = nodes['Claude: Repair Script'].parameters.jsonBody;
    check('Repair Script sources the script from the failed validation output, not a fresh draft',
      repairBody.includes("$('Validate Final Script').item.json._failedScript"));
    check('Repair Script is told exactly which checks failed',
      repairBody.includes("$('Validate Final Script').item.json._validationErrors"));

    const expr = repairBody.slice(3, -2).trim();
    const failedScript = { hook: 'old weak hook', scenes: [{ scene_index: 0, point: 'p' }] };
    const validationErrors = ['quality.hook_strength=65 is below publish threshold 78'];
    function fakeDollar(name) {
      if (name === 'Validate Final Script') return { item: { json: { _failedScript: failedScript, _validationErrors: validationErrors } } };
      return { item: { json: {} } };
    }
    const fn = new Function('$', '$json', 'return ' + expr);
    const raw = fn(fakeDollar, {});
    const built = JSON.parse(raw);
    const content = built.messages[0].content;
    check('Repair Script prompt embeds the actual failed script content', content.includes('old weak hook'));
    check('Repair Script prompt embeds the actual failure reason', content.includes('quality.hook_strength=65'));
    check('Repair Script node is bounded to a single attempt (no silent 3x retry cost)',
      nodes['Claude: Repair Script'].retryOnFail === false && nodes['Claude: Repair Script'].maxTries === 1);

    // Regression test: production incident where the repair pass received
    // "scene 0 narration too short/missing" but returned scenes with the
    // narration field omitted entirely, twice in a row. The prompt must
    // explicitly instruct Claude to write real content for a field a failed
    // check names, not just "preserve everything unchanged".
    const narrationFailedScript = { hook: 'h', scenes: [{ scene_index: 0, point: 'p' }] };
    const narrationErrors = ['scene 0 narration too short/missing', 'total narration word count (0) is under the 30 word floor - too thin to be a real story.'];
    function fakeDollarNarration(name) {
      if (name === 'Validate Final Script') return { item: { json: { _failedScript: narrationFailedScript, _validationErrors: narrationErrors } } };
      return { item: { json: {} } };
    }
    const rawNarration = new Function('$', '$json', 'return ' + expr)(fakeDollarNarration, {});
    const contentNarration = JSON.parse(rawNarration).messages[0].content;
    check('Repair Script prompt embeds a missing-narration failure reason', contentNarration.includes('scene 0 narration too short/missing'));
    check('Repair Script prompt explicitly instructs writing real content for a flagged-missing field, not just preserving fields unchanged',
      contentNarration.includes('You MUST write real, complete content'));
  }

  // -------------------------------------------------------------------
  section('7. Merge By scene_index - asset quality aggregation ("resources" fix)');
  // -------------------------------------------------------------------
  {
    const code = nodes['Merge By scene_index (not position)'].parameters.jsCode;
    const fn = new Function('$input', '$', code);
    const mockDollar = () => ({
      item: {
        json: {
          hook: 'h', comment_hook: 'c', full_script: 'f', caption_style: 'neutral', caption_mode: 'karaoke',
          creative_format: 'documentary_cinematic', visual_grammar: 'x', engagement_mode: 'none', outro_line: null,
        },
      },
    });

    function runMerge(videos, audios) {
      return fn({ all: () => [{ json: { data: videos } }, { json: { data: audios } }] }, mockDollar);
    }

    const mixed = runMerge(
      [
        { scene_index: 0, images: ['a'], visual_source: 'stock', asset_score: 82 },
        { scene_index: 1, images: ['b'], visual_source: 'stock', asset_score: 90 },
        { scene_index: 2, visual_source: 'template', template_name: 'kinetic_text', template_data: {} },
      ],
      [0, 1, 2].map((i) => ({ scene_index: i, audio: { a: 1 } })),
    );
    check('mixed stock+template scenes: asset_quality_min = 82', mixed.json.asset_quality_min === 82, `got ${mixed.json.asset_quality_min}`);
    check('mixed stock+template scenes: asset_quality_avg = 86', mixed.json.asset_quality_avg === 86, `got ${mixed.json.asset_quality_avg}`);
    check('template scene correctly excluded from per-scene asset_score', mixed.json.data[2].asset_score === undefined);

    throws(() => runMerge(
      [{ scene_index: 0, visual_source: 'template', template_name: 'kinetic_text', template_data: {} }],
      [{ scene_index: 0, audio: { a: 1 } }],
    ), 'Merge blocks a final Remotion template opener before compose/upload',
      (m) => m.includes('scene 0 cannot be a Remotion template'));

    throws(() => runMerge(
      [
        { scene_index: 0, images: ['a'], visual_source: 'stock', asset_score: 82 },
        { scene_index: 1, visual_source: 'template', template_name: 'kinetic_text', template_data: {} },
        { scene_index: 2, visual_source: 'template', template_name: 'stat_reveal', template_data: {} },
      ],
      [0, 1, 2].map((i) => ({ scene_index: i, audio: { a: 1 } })),
    ), 'Merge blocks more than one final Remotion template before compose/upload',
      (m) => m.includes('maximum is 1'));

    const logCode = nodes['Log Published Video'].parameters.jsonBody;
    check('Log Published Video no longer hardcodes null', !logCode.includes('asset_quality_min: null'));
    check('Log Published Video references the real aggregated field', logCode.includes("$('Merge By scene_index (not position)').item.json.asset_quality_min"));
  }

  // -------------------------------------------------------------------
  section('8. Prompt integrity - every HTTP node\'s n8n expression must build valid JSON');
  // -------------------------------------------------------------------
  // Catches exactly the class of bug found earlier this session: a prompt
  // edit whose escaping is wrong at some layer (Python source -> n8n export
  // JSON -> n8n expression -> JSON.stringify) can silently produce a broken
  // request body, or - worse - silently no-op a prompt addition entirely,
  // with no error anywhere until a real API call fails downstream.
  {
    const sampleCtx = {
      topic: 'a sample topic', archetype: 'looks_fake_but_real', draft: { hook: 'h' }, script: { hook: 'h', scenes: [] },
    };
    // Only syntax/escaping validity is under test here, not real cross-node
    // values, so a generic stand-in for any $('NodeName') reference is fine.
    // Unknown property access (e.g. .guidance, .signals, .topics from nodes
    // this test doesn't model in sampleCtx) defaults to [] so the common
    // .map()/.filter()/.join() chains prompts build over "prior data" don't
    // throw - only real syntax/escaping bugs should fail this check.
    const jsonStub = new Proxy(sampleCtx, { get: (target, prop) => (prop in target ? target[prop] : []) });
    const genericNodeStub = new Proxy(
      { item: { json: jsonStub }, first: () => ({ json: jsonStub }), all: () => [{ json: jsonStub }] },
      { get: (target, prop) => (prop in target ? target[prop] : jsonStub[prop]) },
    );
    const dollarStub = (_name) => genericNodeStub;
    const executionStub = { id: 'test-execution-id' };
    for (const n of workflow.nodes) {
      const body = n.parameters && n.parameters.jsonBody;
      if (typeof body !== 'string' || !body.includes('{{')) continue;
      const inner = body.slice(body.indexOf('{{') + 2, body.lastIndexOf('}}'));
      doesNotThrow(() => {
        const fn = new Function('$json', '$', '$execution', 'return (' + inner + ')');
        const payload = fn(jsonStub, dollarStub, executionStub);
        if (typeof payload !== 'string') throw new Error('expression did not produce a string');
        JSON.parse(payload); // must itself be valid JSON, not just valid JS
      }, `${n.name}: jsonBody expression evaluates and produces valid JSON`);
    }
  }
  // Claude: Editorial Rewrite (Stage 2) was removed entirely (see section 1 -
  // it repeatedly discarded the given draft's topic and substituted a
  // different one in production, traced across ~20 real executions,
  // happening on effectively every run, still recurring even after two
  // rounds of explicit prompt instructions). Its structural-integrity and
  // topic-preservation responsibilities now belong to Visual Director, which
  // consumes Draft Script's raw output directly - see the checks below and
  // in section 6b for its equivalent coverage.
  check('Visual Director prompt explicitly protects caption_style/trigger/quality from being dropped',
    nodes['Claude: Visual Director'].parameters.jsonBody.includes('Preserve/rebuild quality')
    && nodes['Claude: Visual Director'].parameters.jsonBody.includes('caption_style must be copied through unchanged')
    && nodes['Claude: Visual Director'].parameters.jsonBody.includes('trigger must be copied through unchanged'));
  check('Visual Director prompt explicitly requires payoff as a nested {claim, resolved_in_scene} object',
    nodes['Claude: Visual Director'].parameters.jsonBody.includes('payoff must be a nested object with exactly {claim: string, resolved_in_scene: number}'));
  check('Visual Director prompt explicitly protects per-scene scene_index/point from being dropped',
    nodes['Claude: Visual Director'].parameters.jsonBody.includes('Every scene needs sequential scene_index and non-empty point'));
  check('Visual Director prompt requires its own fields (first_frame_type, search_queries) as REQUIRED',
    nodes['Claude: Visual Director'].parameters.jsonBody.includes('first_frame_type is required'));

  // Consistency audit finding: Draft Script used to describe a "visual_type:
  // ai" option (AI image generation) that Visual Director's own prompt
  // explicitly says does not exist ("The renderer has NO AI image/video
  // generator"). A downstream normalizer silently forced 'ai' back to
  // 'real', but the stale guidance still steered Draft Script toward beats/
  // wording judged "impossible to photograph" on the (false) assumption AI
  // would render them - those then get force-converted to a real-stock
  // search that, by construction, has nothing good to find.
  check('Draft Script no longer describes a non-existent AI-image-generation option',
    !nodes['Claude: Draft Script (Stage 1)'].parameters.jsonBody.includes("Reserve 'ai' ONLY") &&
    nodes['Claude: Draft Script (Stage 1)'].parameters.jsonBody.includes('there is NO AI image or video generator'));
  // Draft Script's SELF-REVIEW checklist used to assert a fixed "60-90 words
  // across 3-4 scenes" target that directly contradicted the explicit
  // "LENGTH IS YOUR CALL, NOT A FIXED TARGET" policy earlier in the same
  // prompt (which allows 40-190 words / 3+ scenes based on what the topic
  // earns) - the self-review checklist, read last, could silently override
  // the flexible-length design intent.
  check('Draft Script self-review checklist no longer contradicts the flexible-length policy',
    !nodes['Claude: Draft Script (Stage 1)'].parameters.jsonBody.includes('Total narration 60-90 words across 3-4 scenes') &&
    nodes['Claude: Draft Script (Stage 1)'].parameters.jsonBody.includes('not a fixed 60-90/3-4 target'));

  // -------------------------------------------------------------------
  console.log('\n' + '='.repeat(70));
  console.log(`${pass} passed, ${fail} failed`);
  if (fail > 0) {
    console.log('\nFailures:');
    for (const f of failures) console.log('  - ' + f);
  }
  fs.rmSync(tmpDir, { recursive: true, force: true });
  process.exit(fail > 0 ? 1 : 0);
}

main();
