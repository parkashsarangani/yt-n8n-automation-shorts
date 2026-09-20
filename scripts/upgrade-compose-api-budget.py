#!/usr/bin/env python3
"""Apply API-budget guardrails to the compose service and b-roll resolver.

Run this AFTER upgrade-compose-creative-system.py. It is intentionally a deterministic
source transform so the readable source stays canonical while deployment
gets bounded, progressive asset commissioning.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "API_BUDGET"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"could not patch {label}: anchor not found")
    return text.replace(old, new, 1)


BROLL_CONSTANTS_OLD = '''const VISION_MODEL = process.env.BROLL_VISION_MODEL || "gpt-5.6-luna";
const VISION_TOP_N = Number(process.env.BROLL_VISION_TOP_N || 8);
const SOURCE_PER_QUERY = Number(process.env.BROLL_SOURCE_PER_QUERY || 12);
const MAX_SEARCH_QUERIES = Number(process.env.BROLL_MAX_SEARCH_QUERIES || 4);'''

BROLL_CONSTANTS_NEW = '''const VISION_MODEL = process.env.BROLL_VISION_MODEL || "gpt-5.6-luna";
const SOURCE_PER_QUERY = Number(process.env.BROLL_SOURCE_PER_QUERY || 12);
// API_BUDGET: progressive search + hard paid-vision ceilings.
const INITIAL_SEARCH_QUERIES = Math.max(1, Number(process.env.BROLL_INITIAL_SEARCH_QUERIES || 2));
const MAX_SEARCH_QUERIES = Math.max(INITIAL_SEARCH_QUERIES, Number(process.env.BROLL_MAX_SEARCH_QUERIES || 4));
const VISION_BATCH_SIZE = Math.max(1, Number(process.env.BROLL_VISION_BATCH_SIZE || 2));
const FIRST_FRAME_MAX_VISION_CALLS = Math.max(1, Number(process.env.BROLL_FIRST_FRAME_MAX_VISION_CALLS || 5));
const SUPPORT_MAX_VISION_CALLS = Math.max(1, Number(process.env.BROLL_SUPPORT_MAX_VISION_CALLS || 3));
const RUN_MAX_VISION_CALLS = Math.max(1, Number(process.env.BROLL_RUN_MAX_VISION_CALLS || 18));
const SCORE_CACHE_TTL_MS = Math.max(60000, Number(process.env.BROLL_SCORE_CACHE_TTL_MS || 21600000));
const SCORE_CACHE_MAX = Math.max(50, Number(process.env.BROLL_SCORE_CACHE_MAX || 600));
const RUN_BUDGET_TTL_MS = Math.max(60000, Number(process.env.BROLL_RUN_BUDGET_TTL_MS || 7200000));
const scoreCache = new Map();
const runBudgets = new Map();'''


BROLL_BLOCK = r'''async function collectCandidates(queries, subject, { includeWikipedia = true } = {}) {
  const jobs = [];
  for (const q of queries) {
    jobs.push(fromPexelsPhotos(q), fromPexelsVideos(q), fromUnsplash(q));
  }
  if (includeWikipedia) jobs.push(fromWikipedia(subject));
  const groups = jobs.length ? await Promise.all(jobs) : [];
  return dedupeCandidates(groups.flat());
}

function rankCandidates(candidates, target) {
  const wiki = candidates.filter((c) => c.source === "wikipedia");
  const videos = candidates
    .filter((c) => c.type === "video")
    .sort((a, b) => metadataOverlap(b, target) - metadataOverlap(a, target));
  const photos = candidates
    .filter((c) => c.type !== "video" && c.source !== "wikipedia")
    .sort((a, b) => metadataOverlap(b, target) - metadataOverlap(a, target));

  const ordered = [];
  const add = (c) => {
    if (c && !ordered.some((x) => x.url === c.url)) ordered.push(c);
  };
  wiki.forEach(add);
  for (let i = 0; i < videos.length || i < photos.length; i++) {
    add(videos[i]);
    add(photos[i]);
  }
  candidates.forEach(add);
  return ordered;
}

function scoreCacheKey(candidate, target, { firstFrame = false, creativeFormat = "" } = {}) {
  return [
    candidate?.url || "",
    String(target || "").trim().toLowerCase(),
    firstFrame ? "first" : "support",
    String(creativeFormat || "").trim().toLowerCase(),
  ].join("|");
}

function getCachedScore(key, now = Date.now()) {
  const hit = scoreCache.get(key);
  if (!hit) return null;
  if (now - hit.at > hit.ttl) {
    scoreCache.delete(key);
    return null;
  }
  scoreCache.delete(key);
  scoreCache.set(key, hit);
  return hit.score;
}

function putCachedScore(key, score, now = Date.now()) {
  const ttl = Number(score?.overall) > 0 ? SCORE_CACHE_TTL_MS : Math.min(SCORE_CACHE_TTL_MS, 300000);
  scoreCache.delete(key);
  scoreCache.set(key, { score, at: now, ttl });
  while (scoreCache.size > SCORE_CACHE_MAX) {
    const oldest = scoreCache.keys().next().value;
    if (oldest == null) break;
    scoreCache.delete(oldest);
  }
}

function cleanupRunBudgets(now = Date.now()) {
  for (const [key, value] of runBudgets) {
    if (now - value.updated_at > RUN_BUDGET_TTL_MS) runBudgets.delete(key);
  }
}

function reserveRunVisionCall(runId, now = Date.now()) {
  const key = String(runId || "").trim();
  if (!key) return { allowed: true, used: null, remaining: null };
  cleanupRunBudgets(now);
  const current = runBudgets.get(key) || { used: 0, updated_at: now };
  if (current.used >= RUN_MAX_VISION_CALLS) {
    current.updated_at = now;
    runBudgets.set(key, current);
    return { allowed: false, used: current.used, remaining: 0 };
  }
  current.used += 1;
  current.updated_at = now;
  runBudgets.set(key, current);
  return { allowed: true, used: current.used, remaining: Math.max(0, RUN_MAX_VISION_CALLS - current.used) };
}

function getRunBudgetState(runId) {
  const key = String(runId || "").trim();
  if (!key) return { used: null, remaining: null };
  const current = runBudgets.get(key);
  const used = current?.used || 0;
  return { used, remaining: Math.max(0, RUN_MAX_VISION_CALLS - used) };
}

async function evaluateCandidate(candidate, target, opts, state) {
  const key = scoreCacheKey(candidate, target, opts);
  const cached = getCachedScore(key);
  if (cached) {
    state.cache_hits += 1;
    return { candidate, dimensions: cached, cache_hit: true, api_call: false };
  }

  if (state.scene_vision_calls >= state.scene_vision_limit) {
    return { candidate, skipped: true, reason: "scene_vision_budget_exhausted" };
  }

  // With no OpenAI key the underlying scorer performs no paid call; do not
  // consume budget merely to return its zero-score fallback.
  if (!OPENAI_KEY) {
    const dimensions = await scoreVisualCandidate(candidate, target, opts);
    return { candidate, dimensions, cache_hit: false, api_call: false };
  }

  const reservation = reserveRunVisionCall(state.run_id);
  if (!reservation.allowed) {
    state.run_budget_exhausted = true;
    return { candidate, skipped: true, reason: "run_vision_budget_exhausted" };
  }

  // Reserve synchronously before awaiting so concurrent scene requests cannot
  // race past the per-execution ceiling.
  state.scene_vision_calls += 1;
  const dimensions = await scoreVisualCandidate(candidate, target, opts);
  putCachedScore(key, dimensions);
  return { candidate, dimensions, cache_hit: false, api_call: true };
}

function bestScored(scored) {
  if (!scored.length) return null;
  return [...scored].sort((a, b) => b.score - a.score)[0];
}

async function scoreOneBatch(candidates, target, opts, state) {
  const queue = rankCandidates(candidates, target)
    .filter((c) => !state.considered_urls.has(c.url))
    .slice(0, VISION_BATCH_SIZE);
  if (!queue.length) return { best: bestScored(state.scored), progressed: false };

  queue.forEach((c) => state.considered_urls.add(c.url));
  const results = await Promise.all(queue.map((c) => evaluateCandidate(c, target, opts, state)));
  for (const result of results) {
    if (!result.dimensions) continue;
    state.scored.push({
      ...result.candidate,
      ...result.dimensions,
      score: result.dimensions.overall,
      cache_hit: result.cache_hit === true,
    });
  }
  return {
    best: bestScored(state.scored),
    progressed: results.some((r) => r.dimensions || r.api_call),
  };
}

async function scoreUntilPassOrBudget(candidates, target, opts, threshold, state) {
  while (true) {
    const before = state.considered_urls.size;
    const { best, progressed } = await scoreOneBatch(candidates, target, opts, state);
    if (best && best.score >= threshold) return best;
    if (state.run_budget_exhausted) return best;
    if (state.scene_vision_calls >= state.scene_vision_limit) return best;
    if (!progressed && state.considered_urls.size === before) return best;
    if (state.considered_urls.size >= candidates.length) return best;
  }
}

function successResponse(best, context) {
  const { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds } = context;
  const runBudget = getRunBudgetState(state.run_id);
  return {
    ok: true,
    type: best.type,
    url: best.url,
    source: best.source,
    score: best.score,
    relevance: best.relevance,
    scroll_stop: best.scroll_stop,
    mobile_clarity: best.mobile_clarity,
    composition: best.composition,
    motion_energy: best.motion_energy,
    uniqueness: best.uniqueness,
    threshold,
    first_frame: isFirstFrame,
    selected_query: best.query || queryList[0],
    candidate_count: candidates.length,
    scored_count: state.scored.length,
    vision_calls: state.scene_vision_calls,
    cache_hits: state.cache_hits,
    search_rounds: searchRounds,
    queries_tried: queriesTried,
    run_vision_used: runBudget.used,
    run_vision_remaining: runBudget.remaining,
    attribution: best.attribution || "",
  };
}

async function resolveBroll({
  query,
  queries,
  alternate_queries,
  subject,
  description,
  scene_index,
  first_frame,
  creative_format,
  run_id,
} = {}) {
  const desc = String(description || query || subject || "").trim();
  const subj = String(subject || "").trim();
  const target = subj || desc;
  const queryList = uniqStrings([
    query,
    ...(Array.isArray(queries) ? queries : []),
    ...(Array.isArray(alternate_queries) ? alternate_queries : []),
    subj,
  ]).slice(0, MAX_SEARCH_QUERIES);

  if (!queryList.length || !target) return { ok: false, reason: "missing_search_target" };

  const isFirstFrame = first_frame === true || Number(scene_index) === 0;
  const threshold = isFirstFrame ? FIRST_FRAME_THRESHOLD : SCORE_THRESHOLD;
  const sceneVisionLimit = isFirstFrame ? FIRST_FRAME_MAX_VISION_CALLS : SUPPORT_MAX_VISION_CALLS;
  const opts = { firstFrame: isFirstFrame, creativeFormat: creative_format };
  const state = {
    run_id: String(run_id || "").trim(),
    scene_vision_limit: sceneVisionLimit,
    scene_vision_calls: 0,
    cache_hits: 0,
    run_budget_exhausted: false,
    considered_urls: new Set(),
    scored: [],
  };

  const initialCount = Math.min(INITIAL_SEARCH_QUERIES, queryList.length);
  const initialQueries = queryList.slice(0, initialCount);
  const extraQueries = queryList.slice(initialCount);
  const queriesTried = [...initialQueries];
  let searchRounds = 1;
  let candidates = await collectCandidates(initialQueries, subj, { includeWikipedia: true });

  // One tiny batch first. If either candidate clears the gate, stop paying.
  const firstPass = await scoreOneBatch(candidates, target, opts, state);
  if (firstPass.best && firstPass.best.score >= threshold) {
    return successResponse(firstPass.best, { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds });
  }

  // Only broaden from two queries to the remaining variants after the first
  // score batch fails. This keeps the common success path cheap.
  if (extraQueries.length && !state.run_budget_exhausted && state.scene_vision_calls < state.scene_vision_limit) {
    searchRounds += 1;
    queriesTried.push(...extraQueries);
    const expanded = await collectCandidates(extraQueries, subj, { includeWikipedia: false });
    candidates = dedupeCandidates([...candidates, ...expanded]);
  }

  if (!candidates.length) {
    return { ok: false, reason: "no_candidates", threshold, queries_tried: queriesTried, vision_calls: state.scene_vision_calls, search_rounds: searchRounds };
  }

  const best = await scoreUntilPassOrBudget(candidates, target, opts, threshold, state);
  if (best && best.score >= threshold) {
    return successResponse(best, { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds });
  }

  const runBudget = getRunBudgetState(state.run_id);
  const reason = state.run_budget_exhausted
    ? "run_vision_budget_exhausted"
    : state.scene_vision_calls >= state.scene_vision_limit
      ? "scene_vision_budget_exhausted"
      : "below_quality_threshold";

  return {
    ok: false,
    reason,
    threshold,
    first_frame: isFirstFrame,
    queries_tried: queriesTried,
    candidate_count: candidates.length,
    scored_count: state.scored.length,
    vision_calls: state.scene_vision_calls,
    vision_call_limit: sceneVisionLimit,
    cache_hits: state.cache_hits,
    search_rounds: searchRounds,
    run_vision_used: runBudget.used,
    run_vision_remaining: runBudget.remaining,
    best_score: best?.score || 0,
    best_candidate: best
      ? { type: best.type, source: best.source, score: best.score, relevance: best.relevance, scroll_stop: best.scroll_stop, mobile_clarity: best.mobile_clarity }
      : null,
  };
}
'''


def patch_broll(text: str) -> str:
    if MARKER in text:
        return text
    text = replace_once(text, BROLL_CONSTANTS_OLD, BROLL_CONSTANTS_NEW, "b-roll budget constants")
    start = text.find("async function collectCandidates(")
    end = text.find("\nmodule.exports = {", start)
    if start < 0 or end < 0:
        raise ValueError("could not patch b-roll resolver: function block not found")
    return text[:start] + BROLL_BLOCK + text[end:]


def patch_compose(text: str) -> str:
    marker = "API_BUDGET_COMPOSE"
    if marker in text:
        return text
    old = '''    const { query, queries, alternate_queries, subject, description, scene_index, first_frame, creative_format } = req.body || {};
    const result = await resolveBroll({ query, queries, alternate_queries, subject, description, scene_index, first_frame, creative_format });
'''
    new = '''    const { query, queries, alternate_queries, subject, description, scene_index, first_frame, creative_format, run_id } = req.body || {};
    const result = await resolveBroll({ query, queries, alternate_queries, subject, description, scene_index, first_frame, creative_format, run_id });
'''
    text = replace_once(text, old, new, "compose run-id forwarding")
    return text.replace("// CREATIVE_SYSTEM_COMPOSE", f"// CREATIVE_SYSTEM_COMPOSE\n// {marker}", 1)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-compose-api-budget.py COMPOSE_PATH BROLL_PATH")
    compose_path, broll_path = map(Path, sys.argv[1:])
    compose_path.write_text(patch_compose(compose_path.read_text(encoding="utf-8")), encoding="utf-8")
    broll_path.write_text(patch_broll(broll_path.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"API budget compose written to {compose_path}")
    print(f"API budget b-roll written to {broll_path}")


if __name__ == "__main__":
    main()
