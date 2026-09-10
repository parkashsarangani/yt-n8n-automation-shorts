const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');

// These assertions are about the GENERATED workflow, not the seed, so build it.
// Skips rather than fails where python is unavailable, matching the rest of the
// suite's capability-probe convention.
const REPO = path.join(__dirname, '..', '..');
let workflow = null;
let skipReason = false;
try {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'wpc-'));
  for (const py of ['python', 'python3']) {
    try {
      execFileSync(py, ['scripts/build_production_artifacts.py', '--output-dir', out], { cwd: REPO, stdio: 'ignore' });
      break;
    } catch { /* try the next interpreter */ }
  }
  workflow = JSON.parse(fs.readFileSync(path.join(out, 'workflow.json'), 'utf8'));
} catch (err) {
  skipReason = `could not build production artifacts here (${err.message})`;
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
