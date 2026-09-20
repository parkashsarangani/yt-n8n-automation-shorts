#!/usr/bin/env python3
"""Add observational candidate-pool telemetry to the transformed b-roll resolver.

This layer must not change retrieval, ranking, thresholds, budgets, or fallbacks.
It only summarizes candidates already present in memory and attaches those
summaries to resolver responses that would already have been returned.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MARKER = "RETRIEVAL_OBSERVABILITY_V1"

HELPER = r'''// RETRIEVAL_OBSERVABILITY_V1: observational only; no retrieval/ranking behavior changes.
function withRetrievalTelemetry(response, candidates) {
  const sourceCounts = {};
  const typeCounts = {};
  let topLocalSimilarity = null;
  for (const candidate of (Array.isArray(candidates) ? candidates : [])) {
    const source = String(candidate?.source || 'unknown');
    const type = String(candidate?.type || 'unknown');
    sourceCounts[source] = (sourceCounts[source] || 0) + 1;
    typeCounts[type] = (typeCounts[type] || 0) + 1;
    const local = Number(candidate?.local_similarity);
    if (Number.isFinite(local)) topLocalSimilarity = topLocalSimilarity == null ? local : Math.max(topLocalSimilarity, local);
  }
  return {
    ...response,
    candidate_source_counts: sourceCounts,
    candidate_type_counts: typeCounts,
    top_local_similarity: topLocalSimilarity,
  };
}
'''


def patch_text(text: str) -> str:
    if MARKER in text:
        return text

    success_anchor = "function successResponse(best, context) {"
    if success_anchor not in text:
        raise ValueError("successResponse anchor missing for retrieval observability")
    text = text.replace(success_anchor, HELPER + "\n" + success_anchor, 1)

    old = "  return {\n    ok: true,\n    type: best.type,"
    new = "  return withRetrievalTelemetry({\n    ok: true,\n    type: best.type,"
    if old not in text:
        raise ValueError("success response opening anchor missing")
    text = text.replace(old, new, 1)

    old = '    attribution: best.attribution || "",\n  };\n}'
    new = '    attribution: best.attribution || "",\n  }, candidates);\n}'
    if old not in text:
        raise ValueError("success response closing anchor missing")
    text = text.replace(old, new, 1)

    old = '  return {ok:true,type:"template",visual_source:"template",visual_mode:context.visualMode||"template_explainer",'
    new = '  return withRetrievalTelemetry({ok:true,type:"template",visual_source:"template",visual_mode:context.visualMode||"template_explainer",'
    if old not in text:
        raise ValueError("template fallback response opening anchor missing")
    text = text.replace(old, new, 1)

    old = 'queries_tried:context.queriesTried||[],attribution:""};\n}'
    new = 'queries_tried:context.queriesTried||[],attribution:""},context.candidates||[]);\n}'
    if old not in text:
        raise ValueError("template fallback response closing anchor missing")
    text = text.replace(old, new, 1)

    old = "    return degraded;\n  }"
    new = "    return withRetrievalTelemetry(degraded, candidates);\n  }"
    if old not in text:
        raise ValueError("degraded fallback return anchor missing")
    text = text.replace(old, new, 1)

    return text


def self_test(text: str) -> None:
    if MARKER not in text or "candidate_source_counts" not in text or "top_local_similarity" not in text:
        raise RuntimeError("retrieval observability patch did not land")
    start = text.index(f"// {MARKER}")
    end = text.index("\nfunction successResponse(", start)
    helper = text[start:end]
    harness = helper + r'''
const r=withRetrievalTelemetry({ok:true},[
 {source:'pexels_video',type:'video',local_similarity:61},
 {source:'pexels_video',type:'video',local_similarity:74},
 {source:'wikimedia',type:'image',local_similarity:68},
]);
if(r.candidate_source_counts.pexels_video!==2||r.candidate_source_counts.wikimedia!==1)throw new Error('source counts wrong');
if(r.candidate_type_counts.video!==2||r.candidate_type_counts.image!==1)throw new Error('type counts wrong');
if(r.top_local_similarity!==74)throw new Error('top local similarity wrong');
console.log('retrieval observability helper OK');
'''
    p = subprocess.run(["node", "-e", harness], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("retrieval observability self-test failed:\n" + p.stdout + p.stderr)


def patch_file(path: Path) -> None:
    text = patch_text(path.read_text(encoding="utf-8"))
    self_test(text)
    path.write_text(text, encoding="utf-8")
    p = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("retrieval-observability b-roll syntax check failed:\n" + p.stdout + p.stderr)
