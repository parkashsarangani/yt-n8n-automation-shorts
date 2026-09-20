#!/usr/bin/env python3
"""Final runtime hardening implementation for the transformed b-roll resolver."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from visual_retrieval_runtime import patch_visual_retrieval, self_test_visual_retrieval

MARKER = "PREPROD_BROLL_HARDENING"
SOFT_FALLBACK_MARKER = "function softFallbackResponse("


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if new in text: return text
    if old not in text: raise ValueError(f"could not patch {label}: anchor not found")
    return text.replace(old, new, 1)

SAFE_GET_OLD = '''async function safeGet(url, config = {}, timeout = 15000) {
  try {
    const r = await axios.get(url, { timeout, ...config });
    return r.data;
  } catch {
    return null;
  }
}'''
SAFE_GET_NEW = '''// PREPROD_BROLL_HARDENING: one bounded retry for transient/free stock lookups.
async function safeGet(url, config = {}, timeout = 15000) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await axios.get(url, { timeout, ...config });
      return r.data;
    } catch (err) {
      const status = Number(err?.response?.status || 0);
      const retryable = !status || status === 429 || status >= 500;
      if (!retryable || attempt === 1) return null;
      await new Promise((resolve) => setTimeout(resolve, 350 * (attempt + 1)));
    }
  }
  return null;
}'''
PARSE_SCORE_OLD = '''function parseScoreJson(text) {
  const raw = String(text || "").trim().replace(/^```(?:json)?\\s*/i, "").replace(/```\\s*$/, "");
  const a = raw.indexOf("{");
  const b = raw.lastIndexOf("}");
  if (a >= 0 && b > a) {
    try { return JSON.parse(raw.slice(a, b + 1)); } catch { /* fall through */ }
  }
  const n = raw.match(/\\d{1,3}/);
  const v = n ? Math.max(0, Math.min(100, Number(n[0]))) : 0;
  return {
    relevance: v,
    scroll_stop: v,
    mobile_clarity: v,
    composition: v,
    motion_energy: v,
    uniqueness: v,
    overall: v,
  };
}'''
PARSE_SCORE_NEW = '''// PREPROD_BROLL_HARDENING: choose the last complete score object after self-correction.
function extractScoreObjects(s) {
  const out = [];
  let i = 0;
  while (i < s.length) {
    const start = s.indexOf("{", i);
    if (start < 0) break;
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let j = start; j < s.length; j++) {
      const ch = s[j];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === "\\\\") esc = true;
        else if (ch === '"') inStr = false;
      } else if (ch === '"') inStr = true;
      else if (ch === "{") depth += 1;
      else if (ch === "}") {
        depth -= 1;
        if (depth === 0) { end = j; break; }
      }
    }
    if (end < 0) { i = start + 1; continue; }
    out.push(s.slice(start, end + 1));
    i = end + 1;
  }
  return out;
}
function parseScoreJson(text) {
  const raw = String(text || "").trim().replace(/^```(?:json)?\\s*/i, "").replace(/```\\s*$/, "");
  const blocks = extractScoreObjects(raw);
  for (let i = blocks.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(blocks[i]);
      if (parsed && typeof parsed === "object" && (Number.isFinite(Number(parsed.overall)) || Number.isFinite(Number(parsed.relevance)) || Number.isFinite(Number(parsed.scroll_stop)))) return parsed;
    } catch { /* try previous */ }
  }
  return {relevance:0,scroll_stop:0,mobile_clarity:0,composition:0,motion_energy:0,uniqueness:0,overall:0};
}'''
TARGET_OLD = '''  const desc = String(description || query || subject || "").trim();
  const subj = String(subject || "").trim();
  const target = subj || desc;'''
TARGET_NEW = '''  const desc = String(description || query || subject || "").trim();
  const subj = String(subject || "").trim();
  const searchTarget = String(query || "").trim();
  // BROLL_SOFT_FALLBACK: score against the exact subject/search target before
  // the longer prose visual description, which can be much narrower than the
  // stock query that actually produced the candidate.
  const target = subj || searchTarget || desc;'''
TERMINAL_OLD = '''  const runBudget = getRunBudgetState(state.run_id);
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
  };'''
TERMINAL_NEW = '''  const runBudget = getRunBudgetState(state.run_id);
  const reason = state.run_budget_exhausted
    ? "run_vision_budget_exhausted"
    : state.scene_vision_calls >= state.scene_vision_limit
      ? "scene_vision_budget_exhausted"
      : "below_quality_threshold";
  // BROLL_SOFT_FALLBACK: quality thresholds optimize selection but never kill
  // an otherwise complete Short solely because premium stock was unavailable.
  const fallback = (best && best.url) ? best : rankCandidates(candidates, target).find((c) => c && c.url);
  const degraded = softFallbackResponse(fallback, {threshold,isFirstFrame,queryList,queriesTried,candidates,state,searchRounds,runBudget}, reason);
  if (degraded) return degraded;
  return {ok:false,reason:"no_usable_candidate",threshold,first_frame:isFirstFrame,queries_tried:queriesTried,candidate_count:candidates.length,scored_count:state.scored.length,vision_calls:state.scene_vision_calls,vision_call_limit:sceneVisionLimit,cache_hits:state.cache_hits,search_rounds:searchRounds,run_vision_used:runBudget.used,run_vision_remaining:runBudget.remaining,best_score:best?.score||0};'''
SOFT_FALLBACK_HELPER = '''// BROLL_SOFT_FALLBACK: preserve completion while retaining quality telemetry.
function softFallbackResponse(best, context, reason) {
  if (!best || !best.url) return null;
  const { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, runBudget } = context;
  const score = Number.isFinite(Number(best.score)) ? Number(best.score) : 0;
  return {ok:true,degraded:true,quality_gate_passed:false,fallback_reason:reason,type:best.type,url:best.url,source:best.source,score,relevance:Number.isFinite(Number(best.relevance))?Number(best.relevance):0,scroll_stop:Number.isFinite(Number(best.scroll_stop))?Number(best.scroll_stop):0,mobile_clarity:Number.isFinite(Number(best.mobile_clarity))?Number(best.mobile_clarity):0,composition:Number.isFinite(Number(best.composition))?Number(best.composition):0,motion_energy:Number.isFinite(Number(best.motion_energy))?Number(best.motion_energy):0,uniqueness:Number.isFinite(Number(best.uniqueness))?Number(best.uniqueness):0,threshold,first_frame:isFirstFrame,selected_query:best.query||queryList[0],candidate_count:candidates.length,scored_count:state.scored.length,vision_calls:state.scene_vision_calls,vision_call_limit:state.scene_vision_limit,cache_hits:state.cache_hits,search_rounds:searchRounds,queries_tried:queriesTried,run_vision_used:runBudget.used,run_vision_remaining:runBudget.remaining,attribution:best.attribution||""};
}
'''

def self_test_soft_fallback(text: str) -> None:
    start=text.index("// BROLL_SOFT_FALLBACK: preserve completion while retaining quality telemetry.");end=text.index("async function resolveBroll({",start);helper=text[start:end]
    harness=helper+r'''
const candidate={type:'image',url:'https://example.invalid/fallback.jpg',source:'pexels',query:'shark underwater',score:15,relevance:22,scroll_stop:15,mobile_clarity:35,composition:20,motion_energy:10,uniqueness:12};
const result=softFallbackResponse(candidate,{threshold:78,isFirstFrame:true,queryList:['shark underwater'],queriesTried:['shark underwater'],candidates:[candidate],state:{scored:[candidate],scene_vision_calls:7,scene_vision_limit:7,cache_hits:0},searchRounds:2,runBudget:{used:7,remaining:21}},'scene_vision_budget_exhausted');
if(!result||result.ok!==true||result.degraded!==true||result.quality_gate_passed!==false)throw new Error('soft fallback did not preserve completion');
if(result.score!==15||result.threshold!==78||result.fallback_reason!=='scene_vision_budget_exhausted')throw new Error('soft fallback telemetry drifted');
console.log('b-roll soft fallback regression OK');'''
    p=subprocess.run(["node","-e",harness],text=True,capture_output=True)
    if p.returncode!=0: raise RuntimeError("b-roll soft fallback regression failed:\n"+p.stdout+p.stderr)

def upgrade(path: Path) -> None:
    text=path.read_text(encoding="utf-8");text=replace_required(text,SAFE_GET_OLD,SAFE_GET_NEW,"stock lookup retry");text=replace_required(text,PARSE_SCORE_OLD,PARSE_SCORE_NEW,"vision score JSON recovery")
    text=replace_required(text,'const RUN_MAX_VISION_CALLS = Math.max(1, Number(process.env.BROLL_RUN_MAX_VISION_CALLS || 18));','const RUN_MAX_VISION_CALLS = Math.max(1, Number(process.env.BROLL_RUN_MAX_VISION_CALLS || 28));',"run-level vision budget")
    text=replace_required(text,TARGET_OLD,TARGET_NEW,"b-roll scoring target priority")
    if SOFT_FALLBACK_MARKER not in text:
        anchor="async function resolveBroll({"
        if anchor not in text: raise ValueError("resolveBroll anchor missing")
        text=text.replace(anchor,SOFT_FALLBACK_HELPER+"\n"+anchor,1)
    text=replace_required(text,TERMINAL_OLD,TERMINAL_NEW,"b-roll terminal soft fallback")
    text=patch_visual_retrieval(text)
    if MARKER not in text: raise RuntimeError("b-roll runtime hardening marker missing after transform")
    if SOFT_FALLBACK_MARKER not in text or "quality_gate_passed:false" not in text.replace(" ",""): raise RuntimeError("b-roll soft fallback did not land")
    if "BROLL_RUN_MAX_VISION_CALLS || 18" in text: raise RuntimeError("stale 18-call run default survived runtime hardening")
    self_test_soft_fallback(text);self_test_visual_retrieval(text);path.write_text(text, encoding="utf-8")

def main() -> None:
    if len(sys.argv)!=2: raise SystemExit("usage: upgrade-compose-runtime-hardening.py BROLL_RESOLVER_JS")
    path=Path(sys.argv[1]);upgrade(path);print(f"runtime-hardened b-roll resolver written to {path}")

if __name__=="__main__": main()
