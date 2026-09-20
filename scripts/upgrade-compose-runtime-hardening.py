#!/usr/bin/env python3
"""Stable entry point for final compose/B-roll runtime hardening.

VISUAL_MATCHING_V4 already contains the former phase-2 recall, local CLIP,
multi-frame verification, transient retries and fail-closed quality behavior.
Do not re-apply legacy text transforms to it: those transforms target the old
resolver shape and, critically, re-prioritize broad subject text over the exact
scene claim.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path
from compose_retrieval_telemetry import patch_file as patch_compose_retrieval_telemetry
from retrieval_observability import patch_file as patch_retrieval_observability
from retrieval_recall_phase2 import patch_file as patch_retrieval_recall_phase2
from runtime_hardening_impl import upgrade as _upgrade
from v4_video_sampling_reliability import patch_file as patch_v4_video_sampling_reliability
from video_multiframe_phase3 import patch_file as patch_video_multiframe_phase3

V4_MARKER = "VISUAL_MATCHING_V4"
BT709_RANGE_MARKER = "PRODUCTION_BT709_RANGE_NORMALIZATION"
V4_BUDGET_MARKER = "V4_ADAPTIVE_VISION_BUDGET"
V4_EARLY_ACCEPT_MARKER = "V4_FIRST_PASS_EARLY_ACCEPT"
V4_MEDIA_TYPE_MARKER = "V4_PROOF_MEDIA_TYPE_FILTER"
V4_VIDEO_BUDGET_MARKER = "V4_VIDEO_VERIFY_USES_SCENE_BUDGET"
V4_VIDEO_SAMPLE_MARKER = "V4_VIDEO_SAMPLE_STAGING"
V4_TEMPLATE_FALLBACK_MARKER = "V4_TEMPLATE_FALLBACK_ON_NO_MATCH"


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label} anchor missing")
    return text.replace(old, new, 1)


def _patch_compose_bt709_range(compose_path: Path) -> None:
    if not compose_path.exists():
        return
    text = compose_path.read_text(encoding="utf-8")
    if BT709_RANGE_MARKER in text:
        return

    fade_anchor = "`[${vLabel}]fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5[final_v]`"
    fade_replacement = (
        "`[${vLabel}]fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5,"
        "scale=in_range=auto:out_range=tv,format=yuv420p,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[final_v]`"
    )
    if fade_anchor not in text:
        raise RuntimeError("compose BT.709 normalization anchor missing")
    text = text.replace(fade_anchor, fade_replacement, 1)

    option_anchor = '      "-colorspace", "bt709",\n      "-pix_fmt", "yuv420p",'
    option_replacement = (
        '      "-colorspace", "bt709",\n'
        '      "-color_range", "tv",\n'
        '      "-pix_fmt", "yuv420p",'
    )
    if option_anchor not in text:
        raise RuntimeError("compose color-range output option anchor missing")
    text = text.replace(option_anchor, option_replacement, 1)

    marker_anchor = "    // Studio-grade final encoding:\n"
    if marker_anchor not in text:
        raise RuntimeError("compose final-encoding marker anchor missing")
    text = text.replace(
        marker_anchor,
        f"    // {BT709_RANGE_MARKER}: normalize full-range Remotion frames to limited-range BT.709.\n" + marker_anchor,
        1,
    )
    compose_path.write_text(text, encoding="utf-8")


def _patch_v4_budget_reliability(path: Path) -> None:
    """Use real run topology and stop paying once a candidate genuinely passes.

    Production used a fixed three-call support-scene ceiling because 7 + 7*3
    exactly equals the 28-call worst case for eight scenes. That makes a normal
    3-4 scene Short fail after only three verifier attempts while most of the
    run budget is unreachable. V4 now supplies retrieval-scene topology; the
    shared budget allocator preserves future-scene reserves and lets a hard
    scene borrow genuinely spare calls without ever exceeding the run ceiling.

    The old V4 resolver also kept scoring after it already had a candidate that
    passed every semantic gate and the scene threshold. That needlessly consumed
    the run budget and starved later scenes. Accept the first passing candidate
    in CLIP-ranked order; the semantic/entity/action/relationship gates remain
    unchanged and fail closed.
    """
    text = path.read_text(encoding="utf-8")
    if all(marker in text for marker in [V4_BUDGET_MARKER, V4_EARLY_ACCEPT_MARKER, V4_MEDIA_TYPE_MARKER, V4_VIDEO_BUDGET_MARKER]):
        return

    old_state = '  const state = { run_id: String(input.run_id || "").trim(), scene_budget: { used: 0, limit: getSceneLimit(isFirstFrame) }, cache_hits: 0, budget_exhausted: null };'
    new_state = f'''  // {V4_BUDGET_MARKER}: allocate from the real retrieval-scene topology while preserving the 28-call run ceiling.\n  const runId = String(input.run_id || "").trim();\n  const sceneLimit = getSceneLimit(isFirstFrame, {{ runId, retrievalSceneCount: Number(input.retrieval_scene_count), retrievalScenePosition: Number(input.retrieval_scene_position) }});\n  const state = {{ run_id: runId, scene_budget: {{ used: 0, limit: sceneLimit }}, cache_hits: 0, budget_exhausted: null, early_accept: false }};'''
    text = _replace_once(text, old_state, new_state, "V4 budget state")

    old_type_filter = '  if (contract.visual_proof_mode === "annotated_real") candidates = candidates.filter((c) => c.type === "image");'
    new_type_filter = f'''  if (contract.visual_proof_mode === "annotated_real") candidates = candidates.filter((c) => c.type === "image");\n  // {V4_MEDIA_TYPE_MARKER}: a literal action proof must spend vision budget on actual video, never still photos.\n  if (contract.visual_proof_mode === "literal_video") candidates = candidates.filter((c) => c.type === "video");'''
    text = _replace_once(text, old_type_filter, new_type_filter, "literal-video candidate filter")

    old_video_cap = '      if (videoCalls++ >= VIDEO_VERIFY_TOP_N) continue;'
    new_video_cap = f'''      // {V4_VIDEO_BUDGET_MARKER}: when a hard scene borrows calls, video verification may use that allocation too.\n      if (videoCalls++ >= Math.max(VIDEO_VERIFY_TOP_N, state.scene_budget.limit)) continue;'''
    text = _replace_once(text, old_video_cap, new_video_cap, "video verifier budget cap")

    old_library = '    if (c.library_hit && Number(c.semantic_match || 0) >= 88 && reusableAnnotated) { scored.push({ ...c, score: Number(c.score || c.semantic_match), rejected: false }); continue; }'
    new_library = f'''    if (c.library_hit && Number(c.semantic_match || 0) >= 88 && reusableAnnotated) {{\n      const accepted = {{ ...c, score: Number(c.score || c.semantic_match), rejected: false }}; scored.push(accepted);\n      // {V4_EARLY_ACCEPT_MARKER}: semantic gates were already proven when this clip entered the library.\n      if (accepted.score >= threshold) {{ state.early_accept = true; break; }}\n      continue;\n    }}'''
    text = _replace_once(text, old_library, new_library, "library early accept")

    old_video = '      scored.push({ ...c, ...verification, ...materialized, original_url: c.url, score: verification.overall }); continue;'
    new_video = '''      const accepted = { ...c, ...verification, ...materialized, original_url: c.url, score: verification.overall, rejected: false }; scored.push(accepted);\n      if (accepted.score >= threshold) { state.early_accept = true; break; }\n      continue;'''
    text = _replace_once(text, old_video, new_video, "video early accept")

    old_annotated = '''      scored.push({ ...c, ...dimensions, ...materialized, score: dimensions.overall, rejected: false });
      continue;'''
    new_annotated = '''      const accepted = { ...c, ...dimensions, ...materialized, score: dimensions.overall, rejected: false }; scored.push(accepted);\n      if (accepted.score >= threshold) { state.early_accept = true; break; }\n      continue;'''
    text = _replace_once(text, old_annotated, new_annotated, "annotated-real early accept")

    old_image = '    scored.push({ ...c, ...dimensions, score: dimensions.overall, rejected: !annotationOk, reason: annotationOk ? dimensions.reason : "annotated_real_missing_grounded_callouts" });'
    new_image = '''    const accepted = { ...c, ...dimensions, score: dimensions.overall, rejected: !annotationOk, reason: annotationOk ? dimensions.reason : "annotated_real_missing_grounded_callouts" };\n    scored.push(accepted);\n    if (!accepted.rejected && accepted.score >= threshold) { state.early_accept = true; break; }'''
    text = _replace_once(text, old_image, new_image, "image early accept")

    old_failure = '  if (!best) return { ok: false, reason: state.budget_exhausted || "below_quality_threshold", threshold, semantic_threshold: SEMANTIC_THRESHOLD, first_frame: isFirstFrame, queries_tried: queriesTried, candidate_count: candidates.length, scored_count: scored.length, search_rounds: searchRounds, best_score: scored[0]?.score || 0, best_candidate: summarizeCandidate(scored[0]), recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget };'
    new_failure = '''  if (!best) {\n    const failure_reasons = [...new Set(scored.filter((x) => x.rejected).map((x) => String(x.reason || "semantic_gate_failed").slice(0, 120)))].slice(0, 6);\n    return { ok: false, reason: state.budget_exhausted || "below_quality_threshold", threshold, semantic_threshold: SEMANTIC_THRESHOLD, first_frame: isFirstFrame, queries_tried: queriesTried, candidate_count: candidates.length, scored_count: scored.length, search_rounds: searchRounds, vision_call_limit: state.scene_budget.limit, early_accept: state.early_accept, failure_reasons, best_score: scored[0]?.score || 0, best_candidate: summarizeCandidate(scored[0]), recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget };\n  }'''
    text = _replace_once(text, old_failure, new_failure, "budget failure telemetry")

    old_success = '    actual_video_verified: best.type === "video", library_hit: best.library_hit === true, recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget,'
    new_success = '    actual_video_verified: best.type === "video", library_hit: best.library_hit === true, vision_call_limit: state.scene_budget.limit, early_accept: state.early_accept, recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget,'
    text = _replace_once(text, old_success, new_success, "budget success telemetry")

    path.write_text(text, encoding="utf-8")


def _patch_v4_template_fallback(path: Path) -> None:
    """Use the model's own designed template before giving up on a scene.

    The legacy (V2) resolver had a soft fallback: when nothing cleared the
    quality bar, it degraded to the best real-media candidate rather than
    failing the whole run. V4's rewrite dropped that safety net entirely (its
    own budget-reliability patch above only changed the failure *telemetry*,
    not the failure *behavior*) - a scene with no real photo that satisfies
    the semantic gate (a specific animal's internal anatomy, e.g.) now just
    hard-fails the run, exhausting the retry loop for a topic the writer
    could genuinely tell.

    A degraded real photo is the wrong fallback here anyway - the Visual
    Director prompt already asks for a template_fallback on every real/archive
    scene specifically for this situation, and a designed graphic
    (stat_reveal/comparison/kinetic_text/diagram/timeline/map) can never be
    factually misleading the way a mismatched stock photo would be. Use it
    when the model supplied one and its own data actually satisfies that
    template's fields; only then fall through to a hard failure.
    """
    text = path.read_text(encoding="utf-8")
    if V4_TEMPLATE_FALLBACK_MARKER in text:
        return

    old_failure = '''  if (!best) {
    const failure_reasons = [...new Set(scored.filter((x) => x.rejected).map((x) => String(x.reason || "semantic_gate_failed").slice(0, 120)))].slice(0, 6);
    return { ok: false, reason: state.budget_exhausted || "below_quality_threshold", threshold, semantic_threshold: SEMANTIC_THRESHOLD, first_frame: isFirstFrame, queries_tried: queriesTried, candidate_count: candidates.length, scored_count: scored.length, search_rounds: searchRounds, vision_call_limit: state.scene_budget.limit, early_accept: state.early_accept, failure_reasons, best_score: scored[0]?.score || 0, best_candidate: summarizeCandidate(scored[0]), recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget };
  }'''
    new_failure = f'''  if (!best) {{
    const failure_reasons = [...new Set(scored.filter((x) => x.rejected).map((x) => String(x.reason || "semantic_gate_failed").slice(0, 120)))].slice(0, 6);
    const reason = state.budget_exhausted || "below_quality_threshold";
    // {V4_TEMPLATE_FALLBACK_MARKER}: try the model's own designed fallback before failing the scene.
    const tf = templateFallbackResult(input.template_fallback, reason, state.run_id, input.scene_index, isFirstFrame, input.planned_template_count);
    if (tf) return tf;
    return {{ ok: false, reason, threshold, semantic_threshold: SEMANTIC_THRESHOLD, first_frame: isFirstFrame, queries_tried: queriesTried, candidate_count: candidates.length, scored_count: scored.length, search_rounds: searchRounds, vision_call_limit: state.scene_budget.limit, early_accept: state.early_accept, failure_reasons, best_score: scored[0]?.score || 0, best_candidate: summarizeCandidate(scored[0]), recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget }};
  }}'''
    text = _replace_once(text, old_failure, new_failure, "V4 template fallback on no match")

    helper = f'''// {V4_TEMPLATE_FALLBACK_MARKER}: validate the model-supplied template_fallback
// against the exact fields its own template_name requires before trusting it -
// an unfixed name/data mismatch here would reach the renderer, not just a gate.
const TEMPLATE_FALLBACK_SPECS = {{
  stat_reveal: (d) => d && String(d.statValue || "").trim() && String(d.label || "").trim(),
  comparison: (d) => d && String(d.leftLabel || "").trim() && String(d.leftValue || "").trim() && String(d.rightLabel || "").trim() && String(d.rightValue || "").trim(),
  kinetic_text: (d) => d && String(d.line || "").trim(),
  diagram: (d) => d && Array.isArray(d.nodes) && d.nodes.length >= 2 && Array.isArray(d.edges),
  timeline: (d) => d && Array.isArray(d.events) && d.events.length >= 2,
  map: (d) => d && Array.isArray(d.locations) && d.locations.length >= 1,
}};
function templateFallbackResult(tf, reason, runId, sceneIndex, firstFrame, plannedTemplateCount) {{
  if (!tf || typeof tf !== "object") return null;
  const name = String(tf.template_name || "");
  const spec = TEMPLATE_FALLBACK_SPECS[name];
  if (!spec || !spec(tf.template_data || {{}})) return null;
  // A run-wide cap: this scene's own real-media search failing does not
  // mean a fallback template is free to use - if another scene already
  // has a template (planned by the Visual Director or an earlier
  // fallback in this same run), a second one would exceed the 1-template
  // ceiling enforced later at the merge stage, after TTS/resolution have
  // already run for every scene. Catch it here instead, before spending
  // any more of the run on a video that is guaranteed to be rejected.
  const reservation = reserveTemplateFallback(runId, sceneIndex, {{ firstFrame, plannedTemplateCount }});
  if (!reservation.allowed) return null;
  return {{ ok: true, type: "template", visual_source: "template", template_name: name, template_data: tf.template_data, degraded: true, quality_gate_passed: false, fallback_reason: reason }};
}}
'''
    anchor = "async function resolveBroll("
    if anchor not in text:
        raise RuntimeError("V4 template fallback: resolveBroll anchor missing")
    text = text.replace(anchor, helper + "\n" + anchor, 1)

    path.write_text(text, encoding="utf-8")


def _validate_v4(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    required = [
        V4_MARKER,
        "API_BUDGET",
        "PREPROD_BROLL_HARDENING",
        "RETRIEVAL_RECALL_PHASE2",
        "SOURCE_QUERY_COMPILER_V1",
        "MULTIFRAME_VIDEO_RERANK_V1",
        "sampleVideoContactSheet",
        "frame_similarity",
        "localSemanticRerank",
        "materializeVerifiedClip",
        "fromPixabayVideos",
        "fromWikimediaCommons",
        V4_BUDGET_MARKER,
        V4_EARLY_ACCEPT_MARKER,
        V4_MEDIA_TYPE_MARKER,
        V4_VIDEO_BUDGET_MARKER,
        V4_VIDEO_SAMPLE_MARKER,
        V4_TEMPLATE_FALLBACK_MARKER,
        "templateFallbackResult",
        "downloadVideoSample",
        "video_contact_sheet_ffmpeg_failed",
        "retrieval_scene_count",
        "vision_call_limit",
        "failure_reasons",
        'contract.visual_proof_mode === "literal_video"',
    ]
    missing = [x for x in required if x not in text]
    if missing:
        raise RuntimeError("V4 resolver missing production guarantees: " + ", ".join(missing))
    if "const target = subj ||" in text or "const target=subj||" in text.replace(" ", ""):
        raise RuntimeError("V4 regression: broad subject once again overrides the visual contract")
    p = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("V4 resolver syntax check failed:\n" + p.stdout + p.stderr)


def upgrade(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    compose_path = path.with_name("compose.js")
    _patch_compose_bt709_range(compose_path)

    if V4_MARKER in text:
        _patch_v4_budget_reliability(path)
        patch_v4_video_sampling_reliability(path)
        _patch_v4_template_fallback(path)
        _validate_v4(path)
        # Compose telemetry is independent of the old resolver internals; apply
        # it when its anchors remain compatible, otherwise V4's own telemetry is
        # authoritative and deployment must not fail just to preserve an old
        # instrumentation transform.
        if compose_path.exists():
            try:
                patch_compose_retrieval_telemetry(compose_path)
            except Exception as exc:
                print(f"V4: compose retrieval telemetry transform skipped: {exc}")
        return

    _upgrade(path)
    patch_retrieval_observability(path)
    patch_retrieval_recall_phase2(path)
    patch_video_multiframe_phase3(path)
    if compose_path.exists():
        patch_compose_retrieval_telemetry(compose_path)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: upgrade-compose-runtime-hardening.py BROLL_RESOLVER_JS")
    path = Path(sys.argv[1])
    upgrade(path)
    print(f"runtime-hardening validation complete for {path}")


if __name__ == "__main__":
    main()
