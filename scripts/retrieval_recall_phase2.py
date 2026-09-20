#!/usr/bin/env python3
"""Phase 2 retrieval recall: Pixabay + source-specific query compilation.

Applied after visual-retrieval hardening and retrieval observability. The layer
increases cheap candidate recall while preserving paid-vision ceilings and
adds a hard total retrieval deadline per scene.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MARKER = "RETRIEVAL_RECALL_PHASE2"
QUERY_COMPILER_MARKER = "SOURCE_QUERY_COMPILER_V1"
PIXABAY_MARKER = "PIXABAY_RETRIEVAL_V1"

SOURCE_PRIORITY = r'''function sourcePriorityFor(visualMode, requested){
  const allowed=new Set(["wikimedia","wikipedia","pexels_video","pixabay_video","pexels","pixabay","unsplash"]);
  const explicit=(Array.isArray(requested)?requested:[]).map(x=>String(x||"").trim()).filter(x=>allowed.has(x));
  const baseline=visualMode==="archive_scientific"
    ? ["wikimedia","wikipedia","pixabay","pixabay_video","pexels_video","pexels","unsplash"]
    : visualMode==="exact_real"
      ? ["pixabay_video","pexels_video","wikimedia","pixabay","pexels","wikipedia","unsplash"]
      : ["pexels_video","pixabay_video","pexels","pixabay","unsplash","wikimedia","wikipedia"];
  return uniqStrings([...explicit,...baseline]).slice(0,7);
}'''

HELPERS = r'''// RETRIEVAL_RECALL_PHASE2 / SOURCE_QUERY_COMPILER_V1 / PIXABAY_RETRIEVAL_V1
const PIXABAY_KEY = process.env.PIXABAY_KEY || "";
const RETRIEVAL_TOTAL_TIMEOUT_MS = Math.max(5000, Number(process.env.BROLL_RETRIEVAL_TOTAL_TIMEOUT_MS || 22000));
const SOURCE_QUERY_LIMIT = Math.max(1, Math.min(4, Number(process.env.BROLL_SOURCE_QUERY_LIMIT || 2)));
const CANDIDATE_POOL_MAX = Math.max(24, Math.min(240, Number(process.env.BROLL_CANDIDATE_POOL_MAX || 120)));
const PIXABAY_CACHE_TTL_MS = Math.max(3600000, Number(process.env.BROLL_PIXABAY_CACHE_TTL_MS || 86400000));
const PIXABAY_CACHE_MAX = Math.max(50, Number(process.env.BROLL_PIXABAY_CACHE_MAX || 500));
const PIXABAY_CACHE_PATH = process.env.PIXABAY_CACHE_PATH || "/app/data/pixabay_search_cache.json";
let pixabayCacheLoaded = false;
const pixabayCache = new Map();

function cleanRetrievalQuery(value){
  return String(value||"")
    .toLowerCase()
    .replace(/[“”"'`]/g," ")
    .replace(/[^a-z0-9\-\s]/g," ")
    .replace(/\b(cinematic|dramatic|beautiful|stunning|epic|4k|hd|stock|footage|video|photo|image|render|rendered|illustration|illustrated)\b/g," ")
    .replace(/\s+/g," ").trim().slice(0,100);
}

function broadRetrievalQuery(value){
  const cleaned=cleanRetrievalQuery(value);
  const tokens=cleaned.split(/\s+/).filter(Boolean)
    .filter(x=>!["extreme","slow","motion","close","up","macro","wide","shot","view","showing","shows","visible"].includes(x));
  return tokens.slice(0,5).join(" ") || cleaned;
}

function compileSourceQueryPlan(genericQueries, subject, mustShow, visualMode){
  const raw=uniqStrings([mustShow,subject,...(Array.isArray(genericQueries)?genericQueries:[])])
    .map(cleanRetrievalQuery).filter(Boolean);
  const literal=cleanRetrievalQuery(mustShow)||raw[0]||cleanRetrievalQuery(subject);
  const entity=cleanRetrievalQuery(subject)||broadRetrievalQuery(literal)||raw[0];
  const broad=broadRetrievalQuery(entity||literal||raw[0]);
  const action=uniqStrings([literal,...raw,broad]).filter(Boolean);
  const archive=uniqStrings([entity,literal,...raw.map(broadRetrievalQuery),broad]).filter(Boolean);
  const still=uniqStrings([entity,broad,literal,...raw]).filter(Boolean);
  const video=uniqStrings([literal,...raw,broad,entity]).filter(Boolean);
  const limit=(xs)=>xs.slice(0,SOURCE_QUERY_LIMIT);
  return {
    pexels_video: limit(video),
    pixabay_video: limit(video),
    pexels: limit(still),
    pixabay: limit(still),
    unsplash: limit(still),
    wikimedia: limit(archive),
    wikipedia: limit(archive),
    _mode: String(visualMode||"context_real"),
  };
}

function mergeQueryPlans(a,b){
  const out={...(a||{})};
  for(const [source,queries] of Object.entries(b||{})){
    if(source.startsWith("_")){out[source]=queries;continue;}
    out[source]=uniqStrings([...(Array.isArray(out[source])?out[source]:[]),...(Array.isArray(queries)?queries:[])]).slice(0,4);
  }
  return out;
}

async function loadPixabayCache(){
  if(pixabayCacheLoaded)return;
  pixabayCacheLoaded=true;
  try{
    const raw=JSON.parse(await fsp.readFile(PIXABAY_CACHE_PATH,"utf8"));
    const now=Date.now();
    for(const [key,value] of Object.entries(raw||{})){
      if(value&&now-Number(value.at||0)<PIXABAY_CACHE_TTL_MS&&value.data)pixabayCache.set(key,value);
    }
  }catch{}
}

async function persistPixabayCache(){
  try{
    const rows=[...pixabayCache.entries()].sort((a,b)=>Number(b[1]?.at||0)-Number(a[1]?.at||0)).slice(0,PIXABAY_CACHE_MAX);
    const obj=Object.fromEntries(rows);
    await fsp.mkdir(path.dirname(PIXABAY_CACHE_PATH),{recursive:true});
    const tmp=PIXABAY_CACHE_PATH+".tmp";
    await fsp.writeFile(tmp,JSON.stringify(obj));
    await fsp.rename(tmp,PIXABAY_CACHE_PATH);
  }catch(err){console.warn("[broll] Pixabay cache write failed:",err?.message||err);}
}

async function pixabayCachedSearch(kind,query){
  if(!PIXABAY_KEY||!query)return null;
  await loadPixabayCache();
  const normalized=cleanRetrievalQuery(query);
  const key=kind+"|"+normalized;
  const hit=pixabayCache.get(key);
  if(hit&&Date.now()-Number(hit.at||0)<PIXABAY_CACHE_TTL_MS)return hit.data;
  const endpoint=kind==="video"?"https://pixabay.com/api/videos/":"https://pixabay.com/api/";
  const extra=kind==="video"?"&video_type=film":"&image_type=photo&orientation=vertical";
  const url=endpoint+"?key="+encodeURIComponent(PIXABAY_KEY)+"&q="+encodeURIComponent(normalized)+
    "&per_page="+encodeURIComponent(String(Math.min(200,SOURCE_PER_QUERY)))+"&safesearch=true"+extra;
  const data=await safeGet(url,{},15000);
  if(data){pixabayCache.set(key,{at:Date.now(),data});await persistPixabayCache();}
  return data;
}

async function fromPixabayPhotos(query){
  const d=await pixabayCachedSearch("image",query);
  return (d?.hits||[]).map(p=>({
    type:"image",url:p.largeImageURL||p.webformatURL,thumb:p.previewURL||p.webformatURL,
    width:p.imageWidth||p.webformatWidth||null,height:p.imageHeight||p.webformatHeight||null,
    alt:p.tags||query,query,source:"pixabay",attribution:`Image by ${p.user||"a contributor"} on Pixabay`,
  })).filter(c=>c.url);
}

async function fromPixabayVideos(query){
  const d=await pixabayCachedSearch("video",query);
  return (d?.hits||[]).map(v=>{
    const choices=[v?.videos?.medium,v?.videos?.small,v?.videos?.large,v?.videos?.tiny].filter(x=>x?.url);
    const best=choices.find(x=>Number(x.height||0)>=720)||choices[0];
    return best?{
      type:"video",url:best.url,thumb:best.thumbnail||null,width:best.width||null,height:best.height||null,
      alt:v.tags||query,query,source:"pixabay_video",attribution:`Video by ${v.user||"a contributor"} on Pixabay`,duration:v.duration||null,
    }:null;
  }).filter(Boolean);
}
'''

COLLECT_REPLACEMENT = r'''async function collectCandidates(queries, subject, {
  includeWikipedia = true,
  visualMode = "context_real",
  sourcePriority = [],
  mustShow = "",
  deadlineAt = Date.now() + RETRIEVAL_TOTAL_TIMEOUT_MS,
} = {}) {
  const priority=sourcePriorityFor(visualMode,sourcePriority);
  const queryPlan=compileSourceQueryPlan(queries,subject,mustShow,visualMode);
  const jobs=[];
  const routed=priority.filter(x=>x!=="wikipedia").slice(0,5);
  for(const source of routed){
    for(const q of (queryPlan[source]||[]).slice(0,SOURCE_QUERY_LIMIT)){
      const remaining=deadlineAt-Date.now();
      if(remaining<250)break;
      let promise=null;
      if(source==="pexels_video")promise=fromPexelsVideos(q);
      else if(source==="pixabay_video")promise=fromPixabayVideos(q);
      else if(source==="pexels")promise=fromPexelsPhotos(q);
      else if(source==="pixabay")promise=fromPixabayPhotos(q);
      else if(source==="unsplash")promise=fromUnsplash(q);
      else if(source==="wikimedia")promise=fromWikimediaCommons(q);
      if(promise)jobs.push(vrWithTimeout(promise,remaining));
    }
  }
  if(includeWikipedia&&priority.includes("wikipedia")&&deadlineAt-Date.now()>250){
    const wikiQuery=(queryPlan.wikipedia||[])[0]||subject;
    if(wikiQuery)jobs.push(vrWithTimeout(fromWikipedia(wikiQuery),deadlineAt-Date.now()));
  }
  const groups=jobs.length?await Promise.all(jobs):[];
  const candidates=dedupeCandidates(groups.filter(Array.isArray).flat()).slice(0,CANDIDATE_POOL_MAX);
  return {candidates,queryPlan};
}'''


def _replace_between(text: str, start_sig: str, next_sig: str, replacement: str) -> str:
    start = text.find(start_sig)
    if start < 0:
        raise ValueError(f"phase2 start anchor missing: {start_sig}")
    end = text.find(next_sig, start + len(start_sig))
    if end < 0:
        raise ValueError(f"phase2 end anchor missing: {next_sig}")
    return text[:start] + replacement + "\n\n" + text[end:]


def patch_text(text: str) -> str:
    if MARKER in text:
        return text

    require_anchor = 'const axios = require("axios");'
    if require_anchor not in text:
        raise ValueError("axios require anchor missing")
    text = text.replace(require_anchor, require_anchor + '\nconst fsp = require("fs/promises");\nconst path = require("path");', 1)

    text = _replace_between(text, "function sourcePriorityFor(", "async function fromWikimediaCommons", SOURCE_PRIORITY)

    helper_anchor = "// LOCAL_CLIP_RERANK_V2: local CLIP is a cheap coarse filter; Claude vision remains the final semantic/aesthetic judge."
    if helper_anchor not in text:
        raise ValueError("local rerank helper anchor missing")
    text = text.replace(helper_anchor, HELPERS + "\n\n" + helper_anchor, 1)

    text = _replace_between(text, "async function collectCandidates(", "function rankCandidates(", COLLECT_REPLACEMENT)

    target_anchor = "  const target=mustShow||subj||searchTarget||desc;"
    if target_anchor not in text:
        raise ValueError("target anchor missing")
    text = text.replace(target_anchor, target_anchor + "\n  const retrievalDeadline=Date.now()+RETRIEVAL_TOTAL_TIMEOUT_MS;\n  let queryPlan={};", 1)

    old = "let candidates = await collectCandidates(initialQueries, subj, { includeWikipedia: true, visualMode, sourcePriority });\n  candidates = await localSemanticRerank(candidates, target, sourcePriority, acceptableSubstitutes);"
    new = "const initialRetrieval=await collectCandidates(initialQueries,subj,{includeWikipedia:true,visualMode,sourcePriority,mustShow,deadlineAt:retrievalDeadline});\n  let candidates=initialRetrieval.candidates;\n  queryPlan=mergeQueryPlans(queryPlan,initialRetrieval.queryPlan);\n  candidates = await localSemanticRerank(candidates, target, sourcePriority, acceptableSubstitutes);"
    if old not in text:
        raise ValueError("initial retrieval anchor missing")
    text = text.replace(old, new, 1)

    old = "const expanded = await collectCandidates(extraQueries, subj, { includeWikipedia: false, visualMode, sourcePriority });\n    candidates = await localSemanticRerank(dedupeCandidates([...candidates, ...expanded]), target, sourcePriority, acceptableSubstitutes);"
    new = "const expandedRetrieval=await collectCandidates(extraQueries,subj,{includeWikipedia:false,visualMode,sourcePriority,mustShow,deadlineAt:retrievalDeadline});\n    const expanded=expandedRetrieval.candidates;\n    queryPlan=mergeQueryPlans(queryPlan,expandedRetrieval.queryPlan);\n    candidates = await localSemanticRerank(dedupeCandidates([...candidates, ...expanded]).slice(0,CANDIDATE_POOL_MAX), target, sourcePriority, acceptableSubstitutes);"
    if old not in text:
        raise ValueError("expanded retrieval anchor missing")
    text = text.replace(old, new, 1)

    text = text.replace(
        "const { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, visualMode } = context;",
        "const { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, visualMode, queryPlan } = context;",
        1,
    )
    text = text.replace("    queries_tried: queriesTried,\n", "    queries_tried: queriesTried,\n    query_plan: queryPlan || {},\n", 1)
    text = text.replace(
        "queries_tried:context.queriesTried||[],attribution:\"\"},context.candidates||[]);",
        "queries_tried:context.queriesTried||[],query_plan:context.queryPlan||{},attribution:\"\"},context.candidates||[]);",
        1,
    )
    text = text.replace("degraded.visual_mode=visualMode;", "degraded.visual_mode=visualMode;\n    degraded.query_plan=queryPlan;", 1)

    text = text.replace(
        "{ threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, visualMode });",
        "{ threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, visualMode, queryPlan });",
    )
    text = text.replace(
        "{templateFallback:template_fallback,mustShow,visualMode,threshold,isFirstFrame,queryList,queriesTried,candidates,state}",
        "{templateFallback:template_fallback,mustShow,visualMode,threshold,isFirstFrame,queryList,queriesTried,candidates,state,queryPlan}",
    )

    # Mark the final transformed source for deploy/CI diagnostics.
    text = text.replace("// RETRIEVAL_OBSERVABILITY_V1: observational only; no retrieval/ranking behavior changes.", "// RETRIEVAL_RECALL_PHASE2\n// RETRIEVAL_OBSERVABILITY_V1: observational only; no retrieval/ranking behavior changes.", 1)
    return text


def self_test(text: str) -> None:
    for marker in [MARKER, QUERY_COMPILER_MARKER, PIXABAY_MARKER, "fromPixabayVideos", "fromPixabayPhotos", "query_plan", "RETRIEVAL_TOTAL_TIMEOUT_MS"]:
        if marker not in text:
            raise RuntimeError(f"phase2 retrieval patch missing {marker}")
    if '"pixabay_video"' not in text or '"pixabay"' not in text:
        raise RuntimeError("Pixabay sources are not in the source router")
    if "CANDIDATE_POOL_MAX" not in text or "SOURCE_QUERY_LIMIT" not in text:
        raise RuntimeError("candidate/query caps missing")

    start = text.index("function cleanRetrievalQuery(")
    end = text.index("async function loadPixabayCache", start)
    pure = text[start:end]
    harness = r'''function uniqStrings(values){const seen=new Set(),out=[];for(const raw of values||[]){const v=String(raw||'').trim(),k=v.toLowerCase();if(!v||seen.has(k))continue;seen.add(k);out.push(v);}return out;}
const SOURCE_QUERY_LIMIT=2;
''' + pure + r'''
const p=compileSourceQueryPlan(['woodpecker pecking tree slow motion','bird skull x ray animation'],'woodpecker','woodpecker repeatedly striking tree trunk','exact_real');
if(!p.pexels_video.length||!p.pixabay_video.length||!p.wikimedia.length)throw new Error('query plan missing source families');
if(p.pexels_video.length>2||p.pixabay.length>2)throw new Error('query limit exceeded');
if(p.pexels_video.some(q=>/footage|video|cinematic/.test(q)))throw new Error('production words leaked into search query');
console.log('source query compiler OK');
'''
    p = subprocess.run(["node", "-e", harness], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("phase2 query compiler self-test failed:\n" + p.stdout + p.stderr)


def patch_file(path: Path) -> None:
    text = patch_text(path.read_text(encoding="utf-8"))
    self_test(text)
    path.write_text(text, encoding="utf-8")
    p = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("phase2 b-roll syntax check failed:\n" + p.stdout + p.stderr)
