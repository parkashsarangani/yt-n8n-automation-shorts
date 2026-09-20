#!/usr/bin/env python3
"""Phase 3: bounded multi-frame semantic reranking for video candidates.

Applied after phase-2 retrieval recall. Only the highest-ranked few video
candidates are sampled. One contact sheet per candidate is scored with the
existing local CLIP model; Claude vision budgets are unchanged.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MARKER = "MULTIFRAME_VIDEO_RERANK_V1"

HELPERS = r'''// MULTIFRAME_VIDEO_RERANK_V1: bounded contact-sheet semantic rerank; no extra Claude calls.
const MULTIFRAME_ENABLED = String(process.env.BROLL_MULTIFRAME_ENABLED || "true").toLowerCase() !== "false";
const MULTIFRAME_CANDIDATES = Math.max(1, Math.min(5, Number(process.env.BROLL_MULTIFRAME_CANDIDATES || 3)));
const MULTIFRAME_FRAMES = Math.max(2, Math.min(5, Number(process.env.BROLL_MULTIFRAME_FRAMES || 3)));
const MULTIFRAME_TIMEOUT_MS = Math.max(2000, Number(process.env.BROLL_MULTIFRAME_TIMEOUT_MS || 12000));
const MULTIFRAME_MAX_BYTES = Math.max(2 * 1024 * 1024, Number(process.env.BROLL_MULTIFRAME_MAX_BYTES || 12582912));

function frameSemanticScore(candidate){
  const local=Number.isFinite(Number(candidate?.local_similarity))?Number(candidate.local_similarity):-1;
  const frame=Number.isFinite(Number(candidate?.frame_similarity))?Number(candidate.frame_similarity):-1;
  if(frame<0)return local;
  if(local<0)return frame;
  return Math.round((frame*0.75+local*0.25)*100)/100;
}

function emptyFrameSampling(){return {enabled:MULTIFRAME_ENABLED,attempted:0,completed:0,elapsed_ms:0,deadline_exhausted:false};}

async function sampleVideoContactSheet(candidate,target,acceptableSubstitutes,classifier,deadlineAt){
  const started=Date.now();
  const remaining=deadlineAt-Date.now();
  if(remaining<700)return {status:"deadline",elapsed_ms:Date.now()-started};
  const sampleUrl=String(candidate?.sample_url||candidate?.url||"").trim();
  if(!sampleUrl)return {status:"no_url",elapsed_ms:Date.now()-started};
  const token=process.pid+"-"+Date.now()+"-"+Math.random().toString(36).slice(2);
  const inputPath=path.join("/tmp","broll-mf-"+token+".mp4");
  const outputPath=path.join("/tmp","broll-mf-"+token+".jpg");
  try{
    const downloadBudget=Math.max(500,Math.min(6000,deadlineAt-Date.now()));
    if(downloadBudget<500)return {status:"deadline",elapsed_ms:Date.now()-started};
    const response=await axios.get(sampleUrl,{
      responseType:"arraybuffer",timeout:downloadBudget,maxContentLength:MULTIFRAME_MAX_BYTES,maxBodyLength:MULTIFRAME_MAX_BYTES,
      headers:{"User-Agent":"yt-shorts-broll/1.0 (contact: channel owner)"},
    });
    const buf=Buffer.from(response.data||[]);
    if(!buf.length||buf.length>MULTIFRAME_MAX_BYTES)return {status:"download_too_large",elapsed_ms:Date.now()-started};
    await fsp.writeFile(inputPath,buf);
    const duration=Math.max(2,Number(candidate?.duration)||6);
    const fps=Math.max(0.15,Math.min(2,MULTIFRAME_FRAMES/duration));
    const filter=`fps=${fps.toFixed(4)},scale=320:-2,tile=${MULTIFRAME_FRAMES}x1:nb_frames=${MULTIFRAME_FRAMES}:padding=2:margin=0`;
    const ffmpegBudget=Math.max(500,deadlineAt-Date.now());
    if(ffmpegBudget<500)return {status:"deadline",elapsed_ms:Date.now()-started};
    await execFileAsync(ffmpegPath,["-hide_banner","-loglevel","error","-i",inputPath,"-vf",filter,"-frames:v","1","-q:v","5","-y",outputPath],{timeout:ffmpegBudget,maxBuffer:1024*1024});
    const remainingForClip=deadlineAt-Date.now();
    if(remainingForClip<300)return {status:"deadline",elapsed_ms:Date.now()-started};
    const positives=uniqStrings([target,...(Array.isArray(acceptableSubstitutes)?acceptableSubstitutes:[])])
      .map(x=>String(x).slice(0,160)).slice(0,4);
    const labels=[...positives,"generic unrelated stock footage","generic background scenery"];
    const output=await vrWithTimeout(classifier(outputPath,labels),remainingForClip);
    if(!Array.isArray(output))return {status:"clip_failed",elapsed_ms:Date.now()-started};
    const hits=output.filter(x=>positives.includes(x?.label));
    const best=hits.reduce((m,x)=>Math.max(m,Number(x?.score)||0),0);
    if(!(best>0))return {status:"clip_failed",elapsed_ms:Date.now()-started};
    return {status:"ok",similarity:Math.round(best*100),frame_count:MULTIFRAME_FRAMES,elapsed_ms:Date.now()-started};
  }catch(err){
    const timedOut=String(err?.code||"")==="ETIMEDOUT"||String(err?.signal||"").includes("KILL");
    return {status:timedOut?"timeout":"failed",elapsed_ms:Date.now()-started};
  }finally{
    await Promise.allSettled([fsp.unlink(inputPath),fsp.unlink(outputPath)]);
  }
}

async function multiframeVideoRerank(candidates,target,acceptableSubstitutes,deadlineAt,previousTelemetry){
  const telemetry={...(previousTelemetry||emptyFrameSampling())};
  const stageStart=Date.now();
  if(!MULTIFRAME_ENABLED||!Array.isArray(candidates)||!candidates.length){return {candidates,telemetry};}
  if(deadlineAt-Date.now()<500){telemetry.deadline_exhausted=true;return {candidates,telemetry};}
  const classifier=await vrWithTimeout(getLocalClip(),Math.max(300,deadlineAt-Date.now()));
  if(!classifier)return {candidates,telemetry};
  const remainingSlots=Math.max(0,MULTIFRAME_CANDIDATES-Number(telemetry.attempted||0));
  const queue=rankCandidates(candidates,target)
    .filter(c=>c?.type==="video"&&!Number.isFinite(Number(c?.frame_similarity))&&!c?.frame_sampling_status)
    .slice(0,remainingSlots);
  for(const candidate of queue){
    if(deadlineAt-Date.now()<700){telemetry.deadline_exhausted=true;break;}
    telemetry.attempted+=1;
    const result=await sampleVideoContactSheet(candidate,target,acceptableSubstitutes,classifier,deadlineAt);
    candidate.frame_sampling_status=result.status;
    candidate.frame_sampling_ms=result.elapsed_ms||0;
    if(result.status==="ok"){
      candidate.frame_similarity=result.similarity;
      candidate.frame_sample_count=result.frame_count;
      telemetry.completed+=1;
    }
  }
  if(deadlineAt-Date.now()<300)telemetry.deadline_exhausted=true;
  telemetry.elapsed_ms=Number(telemetry.elapsed_ms||0)+(Date.now()-stageStart);
  return {candidates:rankCandidates(candidates,target),telemetry};
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"phase3 {label} anchor missing")
    return text.replace(old, new, 1)


def patch_text(text: str) -> str:
    if MARKER in text:
        return text
    if "RETRIEVAL_RECALL_PHASE2" not in text:
        raise ValueError("phase3 requires phase2 retrieval recall first")

    import_anchor = 'const path = require("path");'
    imports = import_anchor + '\nconst ffmpegPath = require("ffmpeg-static");\nconst { execFile } = require("child_process");\nconst { promisify } = require("util");\nconst execFileAsync = promisify(execFile);'
    text = replace_once(text, import_anchor, imports, "ffmpeg imports")

    helper_anchor = "// LOCAL_CLIP_RERANK_V2: local CLIP is a cheap coarse filter; Claude vision remains the final semantic/aesthetic judge."
    text = replace_once(text, helper_anchor, HELPERS + "\n\n" + helper_anchor, "helper insertion")

    # Prefer a small source rendition for sampling while preserving the higher-quality selected URL.
    pexels_best = '      const best = [...pool].sort((a, b) => (b.height || 0) - (a.height || 0))[0];'
    pexels_new = pexels_best + '\n      const sample = [...pool].sort((a,b)=>(Number(a.file_size)||Number.MAX_SAFE_INTEGER)-(Number(b.file_size)||Number.MAX_SAFE_INTEGER))[0] || best;'
    text = replace_once(text, pexels_best, pexels_new, "Pexels sample rendition")
    text = replace_once(text, '            url: best.link,\n            thumb: v.image,', '            url: best.link,\n            sample_url: sample?.link || best.link,\n            duration: v.duration || null,\n            thumb: v.image,', "Pexels sample fields")

    pixabay_best = '    const best=choices.find(x=>Number(x.height||0)>=720)||choices[0];'
    pixabay_new = pixabay_best + '\n    const sample=[v?.videos?.tiny,v?.videos?.small,v?.videos?.medium].find(x=>x?.url)||best;'
    text = replace_once(text, pixabay_best, pixabay_new, "Pixabay sample rendition")
    text = replace_once(text, '      type:"video",url:best.url,thumb:best.thumbnail||null,width:best.width||null,height:best.height||null,', '      type:"video",url:best.url,sample_url:sample.url,thumb:best.thumbnail||sample.thumbnail||null,width:best.width||null,height:best.height||null,', "Pixabay sample fields")

    rank_old = '''    const la=Number.isFinite(Number(a?.local_similarity))?Number(a.local_similarity):-1;
    const lb=Number.isFinite(Number(b?.local_similarity))?Number(b.local_similarity):-1;
    if(la!==lb)return lb-la;'''
    rank_new = '''    const sa=frameSemanticScore(a),sb=frameSemanticScore(b);
    if(sa!==sb)return sb-sa;'''
    text = replace_once(text, rank_old, rank_new, "frame-aware rank")

    init_anchor = '  let queryPlan={};'
    init_new = init_anchor + '\n  const frameSamplingDeadline=Date.now()+MULTIFRAME_TIMEOUT_MS;\n  let frameSampling=emptyFrameSampling();'
    text = replace_once(text, init_anchor, init_new, "frame sampling state")

    rerank = '  candidates = await localSemanticRerank(candidates, target, sourcePriority, acceptableSubstitutes);'
    initial_new = rerank + '\n  const initialFrames=await multiframeVideoRerank(candidates,target,acceptableSubstitutes,frameSamplingDeadline,frameSampling);\n  candidates=initialFrames.candidates;frameSampling=initialFrames.telemetry;'
    text = replace_once(text, rerank, initial_new, "initial multi-frame rerank")
    expanded_rerank = '    candidates = await localSemanticRerank(dedupeCandidates([...candidates, ...expanded]).slice(0,CANDIDATE_POOL_MAX), target, sourcePriority, acceptableSubstitutes);'
    expanded_new = expanded_rerank + '\n    const expandedFrames=await multiframeVideoRerank(candidates,target,acceptableSubstitutes,frameSamplingDeadline,frameSampling);\n    candidates=expandedFrames.candidates;frameSampling=expandedFrames.telemetry;'
    text = replace_once(text, expanded_rerank, expanded_new, "expanded multi-frame rerank")

    text = text.replace(
        'const { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, visualMode, queryPlan } = context;',
        'const { threshold, isFirstFrame, queryList, queriesTried, candidates, state, searchRounds, visualMode, queryPlan, frameSampling } = context;',
        1,
    )
    text = text.replace(
        '    local_similarity: best.local_similarity ?? null,\n',
        '    local_similarity: best.local_similarity ?? null,\n    frame_similarity: best.frame_similarity ?? null,\n    frame_sample_count: best.frame_sample_count ?? null,\n    frame_sampling_status: best.frame_sampling_status || null,\n    frame_sampling_ms: best.frame_sampling_ms ?? null,\n    frame_sampling: frameSampling || {},\n',
        1,
    )
    text = text.replace('visualMode, queryPlan });', 'visualMode, queryPlan, frameSampling });')
    text = text.replace(
        '{templateFallback:template_fallback,mustShow,visualMode,threshold,isFirstFrame,queryList,queriesTried,candidates,state,queryPlan}',
        '{templateFallback:template_fallback,mustShow,visualMode,threshold,isFirstFrame,queryList,queriesTried,candidates,state,queryPlan,frameSampling}',
    )
    text = text.replace(
        'mobile_clarity:Number(best?.mobile_clarity||0),local_similarity:Number(best?.local_similarity||0),threshold:context.threshold,',
        'mobile_clarity:Number(best?.mobile_clarity||0),local_similarity:Number(best?.local_similarity||0),frame_similarity:Number(best?.frame_similarity||0),frame_sample_count:best?.frame_sample_count??null,frame_sampling_status:best?.frame_sampling_status||null,frame_sampling_ms:best?.frame_sampling_ms??null,frame_sampling:context.frameSampling||{},threshold:context.threshold,',
        1,
    )
    text = text.replace(
        'degraded.query_plan=queryPlan;',
        'degraded.query_plan=queryPlan;\n    degraded.frame_similarity=fallback?.frame_similarity??null;degraded.frame_sample_count=fallback?.frame_sample_count??null;degraded.frame_sampling_status=fallback?.frame_sampling_status||null;degraded.frame_sampling_ms=fallback?.frame_sampling_ms??null;degraded.frame_sampling=frameSampling;',
        1,
    )

    text = text.replace('// RETRIEVAL_RECALL_PHASE2\n', '// MULTIFRAME_VIDEO_RERANK_V1\n// RETRIEVAL_RECALL_PHASE2\n', 1)
    return text


def self_test(text: str) -> None:
    required = [MARKER, "sampleVideoContactSheet", "multiframeVideoRerank", "frame_similarity", "frame_sampling", "ffmpeg-static"]
    for item in required:
        if item not in text:
            raise RuntimeError(f"phase3 multi-frame patch missing {item}")
    if "BROLL_RUN_MAX_VISION_CALLS || 28" not in text:
        raise RuntimeError("phase3 unexpectedly changed paid vision budget")
    start = text.index("function frameSemanticScore(")
    end = text.index("function emptyFrameSampling", start)
    helper = text[start:end]
    harness = helper + r'''
const a={type:'video',local_similarity:40,frame_similarity:90};
const b={type:'video',local_similarity:70};
if(!(frameSemanticScore(a)>frameSemanticScore(b)))throw new Error('frame evidence did not improve semantic rank');
if(frameSemanticScore({local_similarity:63})!==63)throw new Error('thumbnail fallback changed');
console.log('phase3 frame semantic ranking OK');
'''
    p = subprocess.run(["node", "-e", harness], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("phase3 ranking self-test failed:\n" + p.stdout + p.stderr)


def patch_file(path: Path) -> None:
    text = patch_text(path.read_text(encoding="utf-8"))
    self_test(text)
    path.write_text(text, encoding="utf-8")
    p = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("phase3 transformed resolver syntax check failed:\n" + p.stdout + p.stderr)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("usage: video_multiframe_phase3.py BROLL_RESOLVER_JS")
    patch_file(Path(sys.argv[1]))
