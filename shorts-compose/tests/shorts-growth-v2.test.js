const test = require('node:test');
const assert = require('node:assert/strict');

// Keep unit tests offline. Production uses local MiniLM embeddings when the
// model is available; these tests exercise the deterministic canonical/lexical
// safeguards and the cosine-threshold primitive directly.
process.env.TOPIC_DEDUP_DISABLE_EMBEDDINGS = 'true';
process.env.TOPIC_HISTORY_MAX = '500';

const topicPolicy = require('../topicPolicy');
const feedback = require('../feedbackLoop');

test('topic history no-repeat horizon is at least 500 Shorts', () => {
  assert.equal(topicPolicy.HISTORY_LIMIT, 500);
  assert.equal(topicPolicy.SEMANTIC_THRESHOLD, 0.82);
});

test('canonical fact identity rejects a paraphrase inside the last 500 topics', async () => {
  const history = [
    {
      topic: 'Heat makes the Eiffel Tower grow taller in summer',
      canonical_key: 'eiffel tower|thermal expansion|height increases',
    },
  ];
  const candidates = [
    {
      topic: 'Paris heat can make the Eiffel Tower several centimetres taller',
      canonical_key: 'eiffel tower|thermal expansion|height increases',
      score: 95,
    },
    {
      topic: 'The small arrow beside your fuel icon tells you which side the fuel door is on',
      canonical_key: 'fuel gauge arrow|fuel door indicator|shows refuelling side',
      score: 90,
    },
  ];
  const result = await topicPolicy.filterCandidates({ candidates, history });
  assert.deepEqual(result.survivors.map((x) => x.topic), [candidates[1].topic]);
  assert.equal(result.rejected[0].reason, 'canonical_key');
});

test('lexical fallback rejects close duplicates even when canonical fields are absent', async () => {
  const result = await topicPolicy.filterCandidates({
    history: [{ topic: 'Your soda can tab has a hidden straw holder' }],
    candidates: [
      { topic: 'The tab on your soda can is a hidden holder for your straw', score: 90 },
      { topic: 'A soccer ball curves because its spin bends the airflow around it', score: 80 },
    ],
  });
  assert.equal(result.survivors.length, 1);
  assert.match(result.survivors[0].topic, /soccer ball/i);
  assert.equal(result.rejected[0].reason, 'lexical');
});

test('only the most recent 500 published facts are protected by the hard horizon', async () => {
  const history = [{ topic: 'A uniquely old fact outside the active history window' }];
  for (let i = 0; i < 500; i++) history.push({ topic: `Distinct active topic number ${i} alpha beta gamma` });

  const result = await topicPolicy.filterCandidates({
    history,
    candidates: [{ topic: 'A uniquely old fact outside the active history window', score: 90 }],
  });
  assert.equal(result.survivors.length, 1, 'the 501st-oldest fact may re-enter after 500 newer Shorts');
});

test('cosine similarity primitive enforces the configured semantic threshold', () => {
  const same = topicPolicy.cosineSimilarity([1, 0, 0], [1, 0, 0]);
  const different = topicPolicy.cosineSimilarity([1, 0, 0], [0, 1, 0]);
  assert.equal(same, 1);
  assert.equal(different, 0);
  assert.ok(same >= topicPolicy.SEMANTIC_THRESHOLD);
  assert.ok(different < topicPolicy.SEMANTIC_THRESHOLD);
});

test('deterministic strategy arm approximates 70/20/10 over a large seed set', () => {
  const counts = { exploit: 0, adjacent: 0, explore: 0 };
  for (let i = 0; i < 10000; i++) counts[topicPolicy.armForSeed(`run-${i}`)]++;
  assert.ok(counts.exploit > 6500 && counts.exploit < 7500, JSON.stringify(counts));
  assert.ok(counts.adjacent > 1500 && counts.adjacent < 2500, JSON.stringify(counts));
  assert.ok(counts.explore > 700 && counts.explore < 1300, JSON.stringify(counts));
});

test('analytics separates scroll-stop, hold, and satisfaction metrics', () => {
  const parsed = feedback.parseAnalytics({
    columnHeaders: [
      { name: 'video' }, { name: 'views' }, { name: 'engagedViews' },
      { name: 'averageViewPercentage' }, { name: 'averageViewDuration' },
      { name: 'subscribersGained' }, { name: 'likes' }, { name: 'comments' }, { name: 'shares' },
    ],
    rows: [['abc', 1000, 550, 41.5, 15.2, 4, 50, 10, 22]],
  });
  const m = parsed.abc;
  assert.equal(m.engaged_view_rate, 0.55);
  assert.equal(m.average_view_percentage, 41.5);
  assert.equal(m.average_view_duration_sec, 15.2);
  assert.equal(m.shares_per_engaged_view, 22 / 550);
  assert.equal(m.comments_per_engaged_view, 10 / 550);
  assert.equal(m.subscribers_per_engaged_view, 4 / 550);
});

test('snapshot capture freezes like-for-like cohorts and refuses late backfill', () => {
  const metrics = { views: 100, engaged_views: 60, engaged_view_rate: 0.6 };

  const at24 = { published_at: '2026-09-01T00:00:00Z', snapshots: {} };
  const captured24 = feedback.captureDueSnapshots(at24, metrics, '2026-09-02T01:00:00Z');
  assert.deepEqual(captured24, ['t24h']);
  assert.ok(at24.snapshots.t24h);
  assert.equal(at24.snapshots.t6h, undefined);

  const tooLate = { published_at: '2026-09-01T00:00:00Z', snapshots: {} };
  const capturedLate = feedback.captureDueSnapshots(tooLate, metrics, '2026-09-05T04:00:00Z');
  assert.deepEqual(capturedLate, []);
  assert.deepEqual(tooLate.snapshots, {});
});

test('72-hour cohort is the strategist default once three videos mature', () => {
  const mk = (id, snapshots) => ({
    video_id: id,
    published_at: '2026-09-01T00:00:00Z',
    topic: id,
    creative_dna: {},
    snapshots,
  });
  const history = [
    mk('a', { t24h: { views: 1 }, t72h: { views: 2 } }),
    mk('b', { t24h: { views: 1 }, t72h: { views: 2 } }),
    mk('c', { t24h: { views: 1 }, t72h: { views: 2 } }),
    mk('d', { t24h: { views: 1 } }),
    mk('e', { t24h: { views: 1 } }),
  ];
  const cohort = feedback.selectStrategistCohort(history);
  assert.equal(cohort.key, 't72h');
  assert.equal(cohort.count, 3);
  assert.equal(cohort.rows.length, 3);
});

test('creative DNA carries policy, duration target and outro experiment arm', () => {
  const dna = feedback.normalizeCreativeDna({
    policy_version: 'shorts-growth-v2',
    topic_strategy_arm: 'exploit',
    topic_predicted_score: 91,
    canonical_key: 'fuel gauge|arrow|door side',
    duration: 33.4,
    target_duration_band: '28-36s',
    outro_experiment_arm: 'no_outro',
  });
  assert.equal(dna.policy_version, 'shorts-growth-v2');
  assert.equal(dna.topic_strategy_arm, 'exploit');
  assert.equal(dna.duration_sec, 33.4);
  assert.equal(dna.target_duration_band, '28-36s');
  assert.equal(dna.outro_experiment_arm, 'no_outro');
});
