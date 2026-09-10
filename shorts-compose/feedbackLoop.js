// ---------------------------------------------------------------------------
// SHORTS_GROWTH_V2 feedback loop.
//
// The old loop treated averageViewPercentage as the primary delivery signal.
// That is downstream of the swipe decision: YouTube Shorts average-view metrics
// are based on engaged views, while public views include play starts. V4 learns
// in three stages instead: hook/scroll-stop -> hold -> satisfaction, and compares
// videos only at like-for-like age cohorts (6h/24h/72h/7d/28d).
// ---------------------------------------------------------------------------
const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const axios = require("axios");

const DATA_DIR = path.dirname(process.env.TOPIC_HISTORY_PATH || "/app/data/topic_history.json");
const PERF_PATH = path.join(DATA_DIR, "performance_history.json");
const INSIGHTS_PATH = path.join(DATA_DIR, "channel_insights.json");
const PERF_MAX = Math.max(500, Number(process.env.PERF_HISTORY_MAX || 500));
const POLICY_VERSION = process.env.SHORTS_POLICY_VERSION || "shorts-growth-v2";

const OPENAI_KEY = process.env.OPENAI_KEY || "";
const STRATEGIST_MODEL = process.env.STRATEGIST_MODEL || "gpt-5.6-luna";

const SNAPSHOT_TARGETS = [
  { key: "t6h", hours: 6, maxLagHours: 8 },
  { key: "t24h", hours: 24, maxLagHours: 10 },
  { key: "t72h", hours: 72, maxLagHours: 12 },
  { key: "t7d", hours: 168, maxLagHours: 18 },
  { key: "t28d", hours: 672, maxLagHours: 24 },
];

async function readJson(p, fallback) {
  try {
    if (!fs.existsSync(p)) return fallback;
    return JSON.parse(await fsp.readFile(p, "utf8"));
  } catch {
    return fallback;
  }
}

async function writeJson(p, data) {
  await fsp.mkdir(path.dirname(p), { recursive: true });
  await fsp.writeFile(p, JSON.stringify(data, null, 2));
}

function numOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function boolOrNull(v) {
  if (v === null || v === undefined) return null;
  return Boolean(v);
}

function safeRate(numerator, denominator) {
  const n = numOrNull(numerator);
  const d = numOrNull(denominator);
  if (n === null || d === null || d <= 0) return null;
  return n / d;
}

function normalizeCreativeDna(entry = {}) {
  const dna = entry.creative_dna && typeof entry.creative_dna === "object" ? entry.creative_dna : entry;
  return {
    policy_version: dna.policy_version || entry.policy_version || POLICY_VERSION,
    topic_strategy_arm: dna.topic_strategy_arm || entry.topic_strategy_arm || null,
    topic_predicted_score: numOrNull(dna.topic_predicted_score ?? entry.topic_predicted_score),
    canonical_key: dna.canonical_key || entry.canonical_key || null,
    concept_archetype: dna.concept_archetype || null,
    creative_format: dna.creative_format || null,
    visual_grammar: dna.visual_grammar || null,
    first_frame_type: dna.first_frame_type || null,
    first_frame_source: dna.first_frame_source || null,
    first_frame_score: numOrNull(dna.first_frame_score),
    scene_count: numOrNull(dna.scene_count),
    word_count: numOrNull(dna.word_count),
    duration_sec: numOrNull(dna.duration_sec ?? entry.duration),
    target_duration_band: dna.target_duration_band || "28-36s",
    length_exception_reason: dna.length_exception_reason || null,
    payoff_position_pct: numOrNull(dna.payoff_position_pct),
    open_loop_count: numOrNull(dna.open_loop_count),
    template_count: numOrNull(dna.template_count),
    real_video_count: numOrNull(dna.real_video_count),
    still_image_count: numOrNull(dna.still_image_count),
    caption_mode: dna.caption_mode || null,
    transition_style: dna.transition_style || null,
    engagement_mode: dna.engagement_mode || null,
    comment_hook_present: boolOrNull(dna.comment_hook_present),
    outro_present: boolOrNull(dna.outro_present),
    outro_experiment_arm: dna.outro_experiment_arm || entry.outro_experiment_arm || null,
    asset_quality_min: numOrNull(dna.asset_quality_min),
    asset_quality_avg: numOrNull(dna.asset_quality_avg),
  };
}

async function logPublished(entry) {
  const vid = entry && entry.video_id;
  if (!vid) throw new Error("logPublished: video_id is required");
  let hist = await readJson(PERF_PATH, []);
  if (hist.some((h) => h.video_id === vid)) {
    return { logged: false, reason: "already_logged", count: hist.length };
  }

  const creativeDna = normalizeCreativeDna(entry);
  hist.push({
    video_id: vid,
    published_at: entry.published_at || new Date().toISOString(),
    policy_version: entry.policy_version || creativeDna.policy_version || POLICY_VERSION,
    topic: entry.topic || null,
    hook: entry.hook || null,
    title: entry.title || null,
    comment_hook: entry.comment_hook || null,
    caption_style: entry.caption_style || null,
    trigger: entry.trigger || null,
    duration: creativeDna.duration_sec,
    creative_dna: creativeDna,
    snapshots: {},
    latest_metrics: null,
    // Compatibility for older tooling. This is latest cumulative telemetry,
    // never the cohort used for cross-video strategist comparisons.
    metrics: null,
  });

  if (hist.length > PERF_MAX) hist = hist.slice(hist.length - PERF_MAX);
  await writeJson(PERF_PATH, hist);
  return { logged: true, count: hist.length, policy_version: creativeDna.policy_version };
}

function parseAnalytics(body) {
  const headers = (body && body.columnHeaders ? body.columnHeaders : []).map((h) => h.name);
  const rows = (body && body.rows) || [];
  const vi = headers.indexOf("video");
  if (vi < 0) return {};

  const out = {};
  for (const row of rows) {
    const id = row[vi];
    if (!id) continue;
    const m = {};
    headers.forEach((name, i) => { if (name !== "video") m[name] = row[i]; });
    const views = Math.round(m.views || 0);
    const engagedViews = m.engagedViews == null ? null : Math.round(m.engagedViews || 0);
    const likes = Math.round(m.likes || 0);
    const comments = Math.round(m.comments || 0);
    const shares = Math.round(m.shares || 0);
    const subscribersGained = Math.round(m.subscribersGained || 0);
    const pct = (v) => (v == null ? null : (v > 1 ? v / 100 : v));
    const satisfactionDenominator = engagedViews != null && engagedViews > 0 ? engagedViews : null;

    out[id] = {
      views,
      engaged_views: engagedViews,
      engaged_view_rate: safeRate(engagedViews, views),
      average_view_percentage: m.averageViewPercentage != null ? Number(m.averageViewPercentage) : null,
      average_view_duration_sec: m.averageViewDuration != null ? Number(m.averageViewDuration) : null,
      subscribers_gained: subscribersGained,
      likes,
      comments,
      shares,
      likes_per_engaged_view: safeRate(likes, satisfactionDenominator),
      comments_per_engaged_view: safeRate(comments, satisfactionDenominator),
      shares_per_engaged_view: safeRate(shares, satisfactionDenominator),
      subscribers_per_engaged_view: safeRate(subscribersGained, satisfactionDenominator),
      impressions: m.videoThumbnailImpressions != null ? Math.round(m.videoThumbnailImpressions) : null,
      click_through_rate: pct(m.videoThumbnailImpressionsClickRate),
      subscribers_lost: m.subscribersLost == null ? null : Math.round(m.subscribersLost),
      net_subscribers: m.subscribersLost == null ? subscribersGained : subscribersGained - Math.round(m.subscribersLost),
      dislikes: m.dislikes == null ? null : Math.round(m.dislikes),
      minutes_watched: m.estimatedMinutesWatched == null ? null : Number(m.estimatedMinutesWatched),
      // A Short that is genuinely liked returns a positive ratio; heavy dislikes
      // suppress distribution far more than a low like count does.
      like_ratio: m.dislikes == null ? null : safeRate(likes, likes + Math.round(m.dislikes)),
    };
  }
  return out;
}

// A Shorts view is won or lost in the first seconds, so the shape of the
// retention curve is far more actionable than its average. Reduce YouTube's
// per-ratio rows to the few numbers a strategist can act on: how many viewers
// survive the hook, where the steepest drop happens, and how the whole curve
// compares with similar videos (relativeRetentionPerformance is YouTube's own
// benchmark and is the closest direct read on why distribution stalls).
function summarizeRetention(body) {
  const headers = ((body && body.columnHeaders) || []).map((h) => h.name);
  const rows = (body && body.rows) || [];
  const ri = headers.indexOf("elapsedVideoTimeRatio");
  const wi = headers.indexOf("audienceWatchRatio");
  const pi = headers.indexOf("relativeRetentionPerformance");
  if (ri < 0 || wi < 0 || !rows.length) return null;

  const points = rows
    .map((r) => ({ at: Number(r[ri]), watch: Number(r[wi]), relative: pi >= 0 ? Number(r[pi]) : null }))
    .filter((p) => Number.isFinite(p.at) && Number.isFinite(p.watch))
    .sort((a, b) => a.at - b.at);
  if (!points.length) return null;

  const at = (ratio) => {
    let best = points[0];
    for (const p of points) if (Math.abs(p.at - ratio) < Math.abs(best.at - ratio)) best = p;
    return best.watch;
  };
  let steepest = { from: null, to: null, drop: 0 };
  for (let i = 1; i < points.length; i++) {
    const drop = points[i - 1].watch - points[i].watch;
    if (drop > steepest.drop) steepest = { from: points[i - 1].at, to: points[i].at, drop };
  }
  const relatives = points.map((p) => p.relative).filter((v) => Number.isFinite(v));

  return {
    points: points.length,
    watch_ratio_at_start: points[0].watch,
    watch_ratio_at_3pct: at(0.03),
    watch_ratio_at_10pct: at(0.10),
    watch_ratio_at_25pct: at(0.25),
    watch_ratio_at_50pct: at(0.50),
    watch_ratio_at_end: points[points.length - 1].watch,
    // How much of the opening audience is still there a tenth of the way in -
    // the practical hook-survival number for a 30s Short.
    hook_survival: points[0].watch > 0 ? at(0.10) / points[0].watch : null,
    steepest_drop_from_ratio: steepest.from,
    steepest_drop_to_ratio: steepest.to,
    steepest_drop: steepest.drop || null,
    relative_retention_avg: relatives.length ? relatives.reduce((a, b) => a + b, 0) / relatives.length : null,
  };
}

// Whether a Short is actually being served in the Shorts feed, or is only
// reaching the few people who already follow the channel, changes what a weak
// view count means - the same number is a content problem in one case and a
// distribution problem in the other.
function summarizeTrafficSources(body) {
  const headers = ((body && body.columnHeaders) || []).map((h) => h.name);
  const rows = (body && body.rows) || [];
  const si = headers.indexOf("insightTrafficSourceType");
  const vi = headers.indexOf("views");
  if (si < 0 || vi < 0 || !rows.length) return null;

  const bySource = {};
  let total = 0;
  for (const r of rows) {
    const src = String(r[si] || "UNKNOWN");
    const v = Number(r[vi]) || 0;
    bySource[src] = (bySource[src] || 0) + v;
    total += v;
  }
  const share = (key) => (total > 0 ? (bySource[key] || 0) / total : null);
  return {
    total_views: total,
    by_source: bySource,
    shorts_feed_share: share("SHORTS"),
    browse_share: share("BROWSE_FEATURES"),
    suggested_share: share("RELATED_VIDEO"),
    search_share: share("YT_SEARCH"),
    channel_share: share("YT_CHANNEL"),
  };
}

function ageHours(publishedAt, measuredAt) {
  const published = new Date(publishedAt || 0).getTime();
  const measured = new Date(measuredAt || Date.now()).getTime();
  if (!Number.isFinite(published) || !Number.isFinite(measured) || published <= 0 || measured < published) return null;
  return (measured - published) / 3600000;
}

function captureDueSnapshots(entry, metrics, measuredAt) {
  entry.snapshots = entry.snapshots && typeof entry.snapshots === "object" ? entry.snapshots : {};
  const age = ageHours(entry.published_at, measuredAt);
  if (age === null) return [];
  const captured = [];
  for (const target of SNAPSHOT_TARGETS) {
    if (entry.snapshots[target.key]) continue;
    // Never backfill a historic video into an early cohort with late cumulative
    // metrics. A missed cohort remains missing; like-for-like comparisons are
    // more important than artificially filling every cell.
    if (age < target.hours || age > target.hours + target.maxLagHours) continue;
    entry.snapshots[target.key] = {
      ...metrics,
      target_age_hours: target.hours,
      observed_age_hours: Number(age.toFixed(2)),
      measured_at: measuredAt,
    };
    captured.push(target.key);
  }
  return captured;
}

function selectStrategistCohort(history) {
  const measured = history.filter((h) => h && h.snapshots && typeof h.snapshots === "object");
  const counts = Object.fromEntries(SNAPSHOT_TARGETS.map((t) => [t.key, measured.filter((h) => h.snapshots[t.key]).length]));

  // 72h is the default learning horizon. Until enough new V4 videos mature,
  // use one other single cohort rather than mixing ages. This prevents an old
  // 7-day video from being compared with a new 6-hour video.
  let key = counts.t72h >= 3 ? "t72h" : null;
  if (!key) {
    const fallbackOrder = ["t24h", "t7d", "t6h", "t28d"];
    key = fallbackOrder.sort((a, b) => (counts[b] || 0) - (counts[a] || 0))[0];
    if (!key || !counts[key]) return { key: null, count: 0, counts, rows: [] };
  }
  const rows = measured.filter((h) => h.snapshots[key]).map((h) => ({
    video_id: h.video_id,
    published_at: h.published_at,
    policy_version: h.policy_version || h.creative_dna?.policy_version || null,
    topic: h.topic,
    hook: h.hook,
    title: h.title,
    trigger: h.trigger,
    creative_dna: h.creative_dna || {},
    metrics: h.snapshots[key],
  }));
  return { key, count: rows.length, counts, rows };
}

const STRATEGIST_PROMPT = (cohortKey, perfJson) =>
`You analyze measured YouTube Shorts outcomes and return narrow, testable guidance for the next Shorts.

ALL ROWS BELOW ARE FROM ONE LIKE-FOR-LIKE AGE COHORT: ${cohortKey}.
Never compare metrics across different video ages.

MEASURED VIDEOS (creative DNA + outcomes):
${perfJson}

MEASUREMENT MODEL - follow this order:
1. HOOK / SCROLL-STOP is primary: engaged_view_rate = engaged_views / public views. It estimates how many starts survived the opening. Optimize the first frame, hook wording, topic promise and first seconds against this first.
2. HOLD is second: average_view_percentage and average_view_duration_sec describe what happened after a viewer became engaged. A high hold metric cannot rescue a weak scroll-stop rate.
3. SATISFACTION is third: compare shares_per_engaged_view, comments_per_engaged_view, likes_per_engaged_view and subscribers_per_engaged_view. Prefer rates over raw counts.
4. views is a distribution/outcome metric, not the creative-quality objective by itself. This channel is currently capped near ~1000 views per Short, which is the size of YouTube's initial test audience: passing that gate is decided by the satisfaction and retention signals above, not by anything that raises views directly. Treat a cluster of Shorts at roughly the same view count as censored data, not as a quality ranking.
5. When metrics.retention is present, use it before any averaged number, because a Short is won or lost in its opening seconds:
   - hook_survival is the fraction of the opening audience still watching a tenth of the way in. This is the sharpest available read on the hook.
   - steepest_drop_from_ratio / steepest_drop_to_ratio locate exactly where viewers leave. Map that back to what happens at that point in the script (hook, mid-beat, payoff, outro) and make the recommendation about that specific beat.
   - relative_retention_avg is YouTube's own comparison against similar videos; above 0.5 is better than typical, below 0.5 is worse. It is the closest direct read on why distribution stalls.
6. When metrics.traffic is present, check shorts_feed_share before blaming the creative. A Short with a low shorts_feed_share was barely served to the feed at all, so its low view count is a distribution outcome and says little about the hook; one with a high shorts_feed_share and weak retention IS a creative problem.
7. Thumbnail impressions/CTR are secondary for Shorts-feed learning and should only be mentioned when non-null and materially informative.

DISCIPLINE:
- Fewer than ~8 videos in this cohort: make only small claims. With zero, return no guidance.
- Any comparison between creative choices requires at least 2 videos in EACH group; prefer 3+.
- Never infer causality from one winner. Say "associated with" unless repeated evidence is clear.
- Evaluate topic/archetype, hook wording, first-frame type/source, duration, payoff, visual grammar, captions and engagement mechanics separately when the data permits.
- For the outro_experiment_arm, compare current_outro versus no_outro only when both have at least 2 measured videos in the SAME cohort.
- Evidence must name groups and actual metric values. Never invent channel statistics or preserve stale numbers from an older prompt.

CREATIVE VARIABLES YOU MAY LEARN FROM:
- topic_strategy_arm, concept_archetype and trigger
- hook/title wording
- creative_format / visual_grammar
- first_frame_type and first_frame_source
- scene_count, word_count, duration_sec and target_duration_band
- payoff_position_pct and open_loop_count
- template_count / real_video_count / still_image_count
- caption_mode and transition_style
- engagement_mode, comment_hook_present, outro_present, outro_experiment_arm
- asset_quality_min / asset_quality_avg
- policy_version (do not pool materially different policies blindly)

OUTPUT ONLY JSON:
{
  "sample_size": <integer>,
  "cohort": "${cohortKey}",
  "confidence_note": "<specific limitation>",
  "guidance": [
    {
      "area": "topic"|"hook"|"structure"|"length"|"trigger"|"first_frame"|"visual_grammar"|"asset_quality"|"captions"|"engagement"|"payoff",
      "stage": "hook"|"hold"|"satisfaction",
      "advice": "<specific next action or experiment>",
      "evidence": "<specific compared groups/videos and numbers>"
    }
  ],
  "avoid": ["<measurably weak pattern only>"],
  "experiments": ["<one concrete next controlled test when useful>"]
}
Prefer 3 strong findings to 10 padded ones.`;

async function runStrategist(history) {
  const cohort = selectStrategistCohort(history);
  if (!cohort.count) {
    const empty = {
      sample_size: 0,
      cohort: null,
      confidence_note: "No like-for-like cohort snapshots are mature yet.",
      guidance: [],
      avoid: [],
      experiments: [],
      generated_at: new Date().toISOString(),
      measured_count: 0,
      cohort_counts: cohort.counts,
      policy_version: POLICY_VERSION,
    };
    await writeJson(INSIGHTS_PATH, empty);
    return empty;
  }
  if (!OPENAI_KEY) throw new Error("runStrategist: OPENAI_KEY is not set");

  const res = await axios.post(
    "https://api.openai.com/v1/chat/completions",
    {
      model: STRATEGIST_MODEL,
      max_completion_tokens: 2600,
      reasoning_effort: "medium",
      response_format: { type: "json_object" },
      messages: [{ role: "user", content: STRATEGIST_PROMPT(cohort.key, JSON.stringify(cohort.rows, null, 2)) }],
    },
    {
      timeout: 60000,
      headers: {
        Authorization: `Bearer ${OPENAI_KEY}`,
        "content-type": "application/json",
      },
    }
  );

  const text = res.data?.choices?.[0]?.message?.content || "";
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < 0) throw new Error("strategist returned no JSON: " + text.slice(0, 200));
  const insights = JSON.parse(text.slice(start, end + 1));
  insights.generated_at = new Date().toISOString();
  insights.measured_count = cohort.count;
  insights.cohort = cohort.key;
  insights.cohort_counts = cohort.counts;
  insights.policy_version = POLICY_VERSION;
  await writeJson(INSIGHTS_PATH, insights);
  return insights;
}

async function ingestAnalytics(body) {
  const analyticsBody = body && body.analytics && body.analytics.columnHeaders ? body.analytics : body;
  const metricsById = parseAnalytics(analyticsBody);
  const hist = await readJson(PERF_PATH, []);
  let updated = 0;
  const captured = {};
  const measuredAt = new Date().toISOString();

  for (const h of hist) {
    const m = metricsById[h.video_id];
    if (!m) continue;
    h.latest_metrics = { ...m, measured_at: measuredAt };
    h.metrics = h.latest_metrics;
    const keys = captureDueSnapshots(h, m, measuredAt);
    if (keys.length) captured[h.video_id] = keys;
    updated++;
  }
  await writeJson(PERF_PATH, hist);

  let insights = null;
  try {
    insights = await runStrategist(hist);
  } catch (e) {
    insights = { error: String(e.message || e), policy_version: POLICY_VERSION };
  }
  return { updated, total: hist.length, captured, insights, measured_at: measuredAt };
}

// Per-video retention/traffic reports need their own API call each, so they
// arrive after the batch metrics pass. Attach them to the cohort snapshot that
// was just frozen so a curve is never compared against a different video age.
async function ingestDeepMetrics(body) {
  const entries = Array.isArray(body && body.videos) ? body.videos : [];
  const hist = await readJson(PERF_PATH, []);
  const byId = new Map(hist.map((h) => [h.video_id, h]));
  const measuredAt = (body && body.measured_at) || new Date().toISOString();
  let updated = 0;
  const attached = {};

  for (const entry of entries) {
    const h = byId.get(entry && entry.video_id);
    if (!h) continue;
    const retention = summarizeRetention(entry.retention);
    const traffic = summarizeTrafficSources(entry.traffic);
    if (!retention && !traffic) continue;

    const deep = { retention, traffic, measured_at: measuredAt };
    h.latest_deep_metrics = deep;

    // Prefer the newest cohort this video already has, so the curve lands on a
    // like-for-like measurement instead of creating an unaged bucket.
    const snapshots = h.snapshots && typeof h.snapshots === "object" ? h.snapshots : null;
    const target = snapshots
      ? [...SNAPSHOT_TARGETS].reverse().find((t) => snapshots[t.key])
      : null;
    if (target) {
      snapshots[target.key].retention = retention;
      snapshots[target.key].traffic = traffic;
      attached[h.video_id] = target.key;
    }
    updated++;
  }
  await writeJson(PERF_PATH, hist);
  return { updated, attached, measured_at: measuredAt };
}

async function getInsights() {
  return await readJson(INSIGHTS_PATH, {
    sample_size: 0,
    cohort: null,
    confidence_note: "No like-for-like cohort snapshots are mature yet.",
    guidance: [],
    avoid: [],
    experiments: [],
    policy_version: POLICY_VERSION,
  });
}

async function getMeasureIds({ maxDays = 60, limit = 200 } = {}) {
  const hist = await readJson(PERF_PATH, []);
  const cutoff = Date.now() - maxDays * 24 * 60 * 60 * 1000;
  return hist
    .filter((h) => h.video_id && (!h.published_at || new Date(h.published_at).getTime() >= cutoff))
    .map((h) => h.video_id)
    .slice(-limit);
}

async function getMeasurementPlan({ maxDays = 60, limit = 200 } = {}) {
  const ids = await getMeasureIds({ maxDays, limit });
  const start = new Date(Date.now() - maxDays * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  return {
    video_ids: ids,
    start_date: start,
    end_date: new Date().toISOString().slice(0, 10),
    snapshot_targets: SNAPSHOT_TARGETS.map(({ key, hours }) => ({ key, hours })),
    policy_version: POLICY_VERSION,
  };
}

module.exports = {
  POLICY_VERSION,
  SNAPSHOT_TARGETS,
  logPublished,
  ingestAnalytics,
  ingestDeepMetrics,
  summarizeRetention,
  summarizeTrafficSources,
  getInsights,
  getMeasureIds,
  getMeasurementPlan,
  runStrategist,
  parseAnalytics,
  normalizeCreativeDna,
  captureDueSnapshots,
  selectStrategistCohort,
  safeRate,
  ageHours,
  PERF_PATH,
  INSIGHTS_PATH,
};
