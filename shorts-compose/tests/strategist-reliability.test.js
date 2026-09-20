const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const axios = require('axios');

// Isolate this file's channel_insights.json/performance_history.json from
// every other test file, the same way coherence-contracts.test.js does.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'shorts-strategist-'));
process.env.TOPIC_HISTORY_PATH = path.join(tmp, 'topic_history.json');
process.env.OPENAI_KEY = 'test-key';
const feedback = require('../feedbackLoop');

test.after(() => fs.rmSync(tmp, { recursive: true, force: true }));

// One video with a single measured cohort is enough for selectStrategistCohort
// to pick a non-empty cohort (falls back to whichever snapshot key has the
// most measured videos when t72h has fewer than 3).
const HISTORY = [
  {
    video_id: 'v1',
    published_at: new Date().toISOString(),
    snapshots: { t24h: { views: 1000, engaged_views: 200 } },
  },
];

const VALID_INSIGHTS = {
  sample_size: 1,
  cohort: 't24h',
  confidence_note: 'Single video; directional only.',
  guidance: [{ area: 'hook', stage: 'hook', advice: 'test advice', evidence: 'test evidence' }],
  avoid: ['avoid this'],
  experiments: ['try this next'],
};

function mockAdapterSequence(responses) {
  const original = axios.defaults.adapter;
  let call = 0;
  axios.defaults.adapter = async (config) => {
    const content = responses[Math.min(call, responses.length - 1)];
    call += 1;
    return {
      data: { choices: [{ message: { content } }] },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  return {
    callCount: () => call,
    restore: () => { axios.defaults.adapter = original; },
  };
}

function seedInsights(value) {
  fs.mkdirSync(path.dirname(feedback.INSIGHTS_PATH), { recursive: true });
  fs.writeFileSync(feedback.INSIGHTS_PATH, JSON.stringify(value, null, 2));
}

test('malformed JSON (no braces) throws and never touches channel_insights.json', async () => {
  seedInsights(VALID_INSIGHTS);
  const mock = mockAdapterSequence(['not json at all']);
  try {
    await assert.rejects(feedback.runStrategist(HISTORY), /no JSON/);
  } finally {
    mock.restore();
  }
  assert.deepEqual(JSON.parse(fs.readFileSync(feedback.INSIGHTS_PATH, 'utf8')).avoid, VALID_INSIGHTS.avoid);
});

test('syntactically valid but wrong-shaped response is rejected, retried once, and the corrected retry is persisted', async () => {
  seedInsights({ avoid: ['stale'] });
  // First response: valid JSON, but `avoid` contains an object instead of a
  // string - exactly the shape that reached production (an error envelope
  // embedded inside the avoid array). Second response (the retry): valid.
  const badShape = JSON.stringify({ ...VALID_INSIGHTS, avoid: [{ error: { message: 'Invalid JSON' } }] });
  const goodShape = JSON.stringify(VALID_INSIGHTS);
  const mock = mockAdapterSequence([badShape, goodShape]);
  let result;
  try {
    result = await feedback.runStrategist(HISTORY);
  } finally {
    mock.restore();
  }
  assert.equal(mock.callCount(), 2);
  assert.deepEqual(result.avoid, VALID_INSIGHTS.avoid);
  assert.deepEqual(JSON.parse(fs.readFileSync(feedback.INSIGHTS_PATH, 'utf8')).avoid, VALID_INSIGHTS.avoid);
});

test('wrong-shaped response twice in a row throws and keeps the last-known-good file untouched', async () => {
  seedInsights(VALID_INSIGHTS);
  const badShape = JSON.stringify({ ...VALID_INSIGHTS, guidance: 'not an array' });
  const mock = mockAdapterSequence([badShape, badShape]);
  try {
    await assert.rejects(feedback.runStrategist(HISTORY), /invalid response shape twice/);
  } finally {
    mock.restore();
  }
  assert.equal(mock.callCount(), 2);
  assert.deepEqual(JSON.parse(fs.readFileSync(feedback.INSIGHTS_PATH, 'utf8')), VALID_INSIGHTS);
});

test('validateStrategistInsights accepts the documented contract and rejects common malformations', () => {
  assert.equal(feedback.validateStrategistInsights(VALID_INSIGHTS), null);
  assert.equal(feedback.validateStrategistInsights(null), 'response is not a JSON object');
  assert.equal(feedback.validateStrategistInsights({ avoid: 'not an array' }), 'avoid must be an array');
  assert.equal(feedback.validateStrategistInsights({ avoid: [1, 2] }), 'avoid must contain only strings');
  assert.equal(
    feedback.validateStrategistInsights({ guidance: [{ area: 1 }] }),
    'guidance.area must be a string'
  );
});
