const test = require('node:test');
const assert = require('node:assert/strict');

// Keep unit tests offline. Production uses local MiniLM embeddings when the
// model is available; these tests exercise the deterministic canonical/lexical
// safeguards and the cosine-threshold primitive directly.
process.env.TOPIC_DEDUP_DISABLE_EMBEDDINGS = 'true';
// Production fails closed when a candidate cannot be semantically checked -
// that is what actually protects the 500-Short horizon. These tests exercise
// the offline fallback in isolation, so they opt out of that gate; the
// fail-closed default itself is asserted separately below.
process.env.TOPIC_DEDUP_REQUIRE_SEMANTIC = 'false';
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

test('stemmed lexical fallback catches a barely-reworded duplicate offline', async () => {
  const history = [{ topic: 'Your soda tab has been holding your straw wrong' }];
  const candidates = [{ topic: 'Your soda can tab holds your straw the wrong way' }];
  const result = await topicPolicy.filterCandidates({ candidates, history });
  assert.equal(result.survivors.length, 0, 'holds/holding must not read as a different fact');
  assert.equal(result.rejected[0].reason, 'lexical');
});

test('claim identity excludes the subject so one subject can carry several facts', () => {
  // Measured separation between duplicates and genuinely different facts:
  // raw wording -0.151 (anti-correlated), full identity +0.064, claim +0.349.
  assert.ok(topicPolicy.CLAIM_THRESHOLD > 0 && topicPolicy.CLAIM_THRESHOLD < topicPolicy.SEMANTIC_THRESHOLD);
});

test('refuses to publish a topic it could not semantically check', async () => {
  const prior = process.env.TOPIC_DEDUP_REQUIRE_SEMANTIC;
  process.env.TOPIC_DEDUP_REQUIRE_SEMANTIC = 'true';
  delete require.cache[require.resolve('../topicPolicy')];
  const strict = require('../topicPolicy');
  await assert.rejects(
    () => strict.filterCandidates({
      candidates: [{ topic: 'Sharks are older than trees' }],
      history: [{ topic: 'The Eiffel Tower grows taller in summer' }],
    }),
    (err) => err.code === 'TOPIC_DEDUP_SEMANTIC_UNAVAILABLE',
    'the 500-Short guarantee must not silently degrade to the lexical gate',
  );
  process.env.TOPIC_DEDUP_REQUIRE_SEMANTIC = prior;
  delete require.cache[require.resolve('../topicPolicy')];
});

test('retention summary locates hook survival and the steepest drop', () => {
  // Shape of a real Shorts curve: heavy loss in the opening, then a plateau.
  const body = {
    columnHeaders: [
      { name: 'elapsedVideoTimeRatio' }, { name: 'audienceWatchRatio' }, { name: 'relativeRetentionPerformance' },
    ],
    rows: [
      [0.00, 1.00, 0.60], [0.03, 0.82, 0.58], [0.10, 0.55, 0.44],
      [0.25, 0.50, 0.42], [0.50, 0.46, 0.41], [1.00, 0.30, 0.35],
    ],
  };
  const r = feedback.summarizeRetention(body);
  assert.equal(r.watch_ratio_at_start, 1);
  assert.equal(r.watch_ratio_at_10pct, 0.55);
  assert.equal(r.hook_survival, 0.55);
  // Biggest single drop is 0.82 -> 0.55, between 3% and 10% of the video.
  assert.equal(r.steepest_drop_from_ratio, 0.03);
  assert.equal(r.steepest_drop_to_ratio, 0.10);
  assert.ok(r.relative_retention_avg > 0.4 && r.relative_retention_avg < 0.5);
});

test('retention summary tolerates a Short with no curve yet', () => {
  assert.equal(feedback.summarizeRetention(null), null);
  assert.equal(feedback.summarizeRetention({ columnHeaders: [], rows: [] }), null);
});

test('traffic summary separates a distribution problem from a creative one', () => {
  const body = {
    columnHeaders: [{ name: 'insightTrafficSourceType' }, { name: 'views' }, { name: 'estimatedMinutesWatched' }],
    rows: [['SHORTS', 900, 60], ['YT_CHANNEL', 80, 5], ['YT_SEARCH', 20, 2]],
  };
  const t = feedback.summarizeTrafficSources(body);
  assert.equal(t.total_views, 1000);
  assert.equal(t.shorts_feed_share, 0.9);
  assert.equal(t.channel_share, 0.08);
});

test('deep metrics attach to the newest frozen cohort, never a new bucket', async () => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'deepmetrics-'));
  // PERF_PATH is derived from TOPIC_HISTORY_PATH's directory.
  const prior = process.env.TOPIC_HISTORY_PATH;
  process.env.TOPIC_HISTORY_PATH = path.join(dir, 'topic_history.json');
  delete require.cache[require.resolve('../feedbackLoop')];
  const fb = require('../feedbackLoop');
  const perfPath = path.join(dir, 'performance_history.json');

  fs.writeFileSync(perfPath, JSON.stringify([
    { video_id: 'vid1', published_at: '2026-09-01T00:00:00Z', snapshots: { t6h: { views: 10 }, t24h: { views: 40 } } },
  ]));

  const result = await fb.ingestDeepMetrics({
    measured_at: '2026-09-02T02:00:00Z',
    videos: [{
      video_id: 'vid1',
      retention: {
        columnHeaders: [{ name: 'elapsedVideoTimeRatio' }, { name: 'audienceWatchRatio' }],
        rows: [[0, 1], [0.1, 0.5], [1, 0.2]],
      },
      traffic: {
        columnHeaders: [{ name: 'insightTrafficSourceType' }, { name: 'views' }],
        rows: [['SHORTS', 40]],
      },
    }],
  });

  assert.equal(result.updated, 1);
  assert.equal(result.attached.vid1, 't24h', 'must attach to the newest frozen cohort');
  const saved = JSON.parse(fs.readFileSync(perfPath, 'utf8'));
  assert.ok(saved[0].snapshots.t24h.retention, 'retention lands on the t24h cohort');
  assert.equal(saved[0].snapshots.t6h.retention, undefined, 't6h must stay frozen as measured');

  if (prior === undefined) delete process.env.TOPIC_HISTORY_PATH; else process.env.TOPIC_HISTORY_PATH = prior;
  delete require.cache[require.resolve('../feedbackLoop')];
});
