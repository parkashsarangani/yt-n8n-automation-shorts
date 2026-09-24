const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

// Regression for the 2026-09-24 12:00 run: a free-tier writer answered 200 with
// JSON of the wrong shape and Parse Draft JSON threw, killing the whole run.
const root = path.resolve(__dirname, '../..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'shorts-draft-resilience-'));
execFileSync(process.platform === 'win32' ? 'python' : 'python3', ['scripts/build_production_artifacts.py', '--output-dir', tmp], { cwd: root, stdio: 'pipe' });
const w = JSON.parse(fs.readFileSync(path.join(tmp, 'workflow.json')));
const n = Object.fromEntries(w.nodes.map((x) => [x.name, x]));
test.after(() => fs.rmSync(tmp, { recursive: true, force: true }));

function run(name, input, { prior = {}, runIndex = 0 } = {}) {
  const $ = (key) => ({ first: () => ({ json: prior[key] || {} }), item: { json: prior[key] || {} } });
  return new Function('$input', '$', '$execution', '$runIndex', n[name].parameters.jsCode)(
    { first: () => ({ json: input }) }, $, { id: '42' }, runIndex,
  ).json;
}
const reply = (content, finish_reason = 'stop') => ({ choices: [{ message: { content }, finish_reason }] });
const scenes = [{ scene_index: 0, point: 'p', narration: 'Real narration' }];
const edges = (name) => w.connections[name].main.map((branch) => branch.map((e) => e.node));

test('unusable drafts loop back to the writer instead of reaching the hook critic', () => {
  assert.deepEqual(edges('Parse Draft JSON'), [['If Draft Parsed']]);
  assert.deepEqual(edges('If Draft Parsed'), [['Claude: Critique Hooks'], ['Retry Draft Script']]);
  assert.deepEqual(edges('Retry Draft Script'), [['If Under Max Draft Attempts']]);
  assert.deepEqual(edges('If Under Max Draft Attempts'), [['Claude: Draft Script (Stage 1)'], ['Fail: Draft Generation Exhausted']]);
  const max = n['If Under Max Draft Attempts'].parameters.conditions.conditions[0];
  assert.equal(max.leftValue, '={{ $json._draftAttempt }}');
  assert.equal(max.rightValue, 3);
});

test('writer asks the gateway to enforce hook/scenes; the critic does not', () => {
  const header = (name) => (n[name].parameters.headerParameters?.parameters || []).find((h) => h.name === 'x-llm-required-keys');
  assert.equal(header('Claude: Draft Script (Stage 1)').value, 'hook,scenes');
  assert.equal(header('Claude: Critique Hooks'), undefined);
});

test('a complete draft passes through unchanged and is marked valid', () => {
  const draft = { hook: 'Real hook', hook_candidates: ['Real hook', 'Other'], scenes };
  const out = run('Parse Draft JSON', reply(JSON.stringify(draft)));
  assert.equal(out._draftValid, true);
  assert.deepEqual(out.draft, draft);
});

test('A: wrapped envelopes and object-shaped hooks are normalized', () => {
  const wrapped = run('Parse Draft JSON', reply(JSON.stringify({ script: { hook: 'Wrapped hook', scenes } })));
  assert.equal(wrapped._draftValid, true);
  assert.equal(wrapped.draft.hook, 'Wrapped hook');
  assert.deepEqual(wrapped.draft.scenes, scenes);

  const objectHook = run('Parse Draft JSON', reply(JSON.stringify({ hook: { text: 'Object hook' }, scenes })));
  assert.equal(objectHook.draft.hook, 'Object hook');

  const fromCandidates = run('Parse Draft JSON', reply(JSON.stringify({ hook_candidates: [{ text: 'First' }, 'Second'], scenes })));
  assert.equal(fromCandidates.draft.hook, 'First');
  assert.deepEqual(fromCandidates.draft.hook_candidates, ['First', 'Second']);
});

test('C: every parser failure is returned as a retryable item, never thrown', () => {
  for (const [label, response] of [
    ['wrong shape (the 12:00 failure)', reply(JSON.stringify({ title: 'no hook or scenes' }))],
    ['no scenes, hook only', reply(JSON.stringify({ hook: 'x' }))],
    ['not JSON', reply('I cannot help with that')],
    ['truncated', reply('{"hook":"x","scenes":[', 'length')],
    ['empty', reply('')],
    ['API error', { error: { message: 'boom' } }],
  ]) {
    const out = run('Parse Draft JSON', response);
    assert.equal(out._draftValid, false, label);
    assert.ok(out._draftError, label);
    assert.equal(out.draft, undefined, label);
  }
  const never = run('Parse Draft JSON', reply(JSON.stringify({ scenes })));
  assert.match(never._draftError, /complete script JSON object/, 'no hook is never invented from narration');
});

test('retry node replays the writer input and counts attempts from $runIndex', () => {
  const upstream = Object.entries(w.connections).find(([src, out]) => src !== 'If Under Max Draft Attempts'
    && out.main.some((b) => b.some((e) => e.node === 'Claude: Draft Script (Stage 1)')))[0];
  const writerInput = { topic: 'Sharks are older than trees', retention_experiment: { arm: 'a' } };
  const first = run('Retry Draft Script', { _draftValid: false, _draftError: 'bad shape' }, { prior: { [upstream]: writerInput } });
  assert.equal(first.topic, writerInput.topic);
  assert.equal(first._draftAttempt, 1);
  assert.equal(first._lastDraftError, 'bad shape');
  const third = run('Retry Draft Script', { _draftError: 'still bad' }, { prior: { [upstream]: writerInput }, runIndex: 2 });
  assert.equal(third._draftAttempt, 3);
});

test('exhausted retries fail the run with the last error', () => {
  assert.throws(() => run('Fail: Draft Generation Exhausted', { _lastDraftError: 'still bad' }), /after 3 attempts.*still bad/);
});
