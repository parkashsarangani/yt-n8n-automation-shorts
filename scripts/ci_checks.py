#!/usr/bin/env python3
"""Contract checks on the built production artifacts (CI and local).

Consolidates the marker/contract checks that used to be spread over five
separate GitHub workflows (quality-check, multiframe-phase3, retrieval-recall,
retrieval-telemetry, workflow-contracts). No network, no rendering.

Usage: python3 scripts/ci_checks.py <artifact-dir>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures: list[str] = []


def check(cond: bool, message: str) -> None:
    if not cond:
        failures.append(message)


def has_all(text: str, markers, where: str) -> None:
    for m in markers:
        check(m in text, f"{where}: missing {m!r}")


def has_none(text: str, markers, where: str) -> None:
    for m in markers:
        check(m not in text, f"{where}: must not contain {m!r}")


def node_check(path: Path) -> None:
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    check(r.returncode == 0, f"node --check {path}: {r.stderr.strip()[:300]}")


def workflow_checks(w: dict) -> None:
    meta = w.get("meta", {})
    check(meta.get("visual_matching_version") == "4", "meta.visual_matching_version != 4")
    check(meta.get("production_build_version") == "5", "meta.production_build_version != 5")
    check(meta.get("compose_service_transport") == "docker_internal", "compose transport not docker_internal")
    check(meta.get("llm_gateway_transport") == "docker_internal", "llm gateway transport not docker_internal")
    check(int(meta.get("llm_gateway_routed_nodes", 0)) >= 1, "no LLM nodes routed through gateway")
    names = {n["name"]: n for n in w["nodes"]}

    visual = names["Claude: Visual Director"]["parameters"]["jsonBody"]
    has_all(visual, ["VISUAL_MATCHING_V4", "Do NOT request callout boxes"], "Visual Director")
    has_none(visual, ["visually locate callout boxes"], "Visual Director")
    validator = names["Validate Final Script"]["parameters"]["jsCode"]
    check("VISUAL_MATCHING_V4 contract gate" in validator, "Validate Final Script: missing V4 contract gate")

    resolver = names["Resolve B-roll"]
    check(resolver["parameters"]["url"].startswith("http://shorts-compose:4000/"), "Resolve B-roll not on internal compose URL")
    check(resolver["parameters"]["options"]["timeout"] == 160000, "Resolve B-roll timeout changed")
    rb = resolver["parameters"]["jsonBody"]
    has_all(rb, ["retrieval_scene_count", "retrieval_scene_position", "run_id", "$execution.id", "visual_claim",
                 "required_entities", "required_actions", "required_relationships", "forbidden_visuals", "visual_proof_mode"], "Resolve B-roll body")
    for f in ["visual_claim", "required_entities", "required_actions", "required_relationships", "forbidden_visuals", "visual_proof_mode"]:
        check(f in visual, f"Visual Director: missing {f}")

    tag = names["Tag B-roll"]["parameters"]["jsCode"]
    has_all(tag, ["r.type==='template'", "deterministic fallback", "verified-real scene", "asset_semantic_match", "asset_entity_match",
                  "asset_action_match", "asset_relationship_match", "asset_local_similarity", "asset_frame_similarity", "frame_similarity",
                  "actual_video_verified", "in_point_sec", "out_point_sec", "verified_frame_indices", "library_hit",
                  "quality_gate_passed", "selection_reason"], "Tag B-roll")
    has_none(tag, ["annotation_plan", "annotations:"], "Tag B-roll")

    merge = names["Merge By scene_index (not position)"]["parameters"]["jsCode"]
    has_all(merge, ["_attribution", "publicationDescription", "useanimations.com (CC BY 4.0)", "asset_semantic_match",
                    "asset_entity_match", "asset_action_match", "asset_relationship_match", "actual_video_verified"], "Merge node")
    check(names["YouTube: Upload Draft"]["parameters"]["options"]["description"].endswith("publication_description }}"),
          "Upload description must come from publication_description")
    check("publication_description" in names["Disclose AI-Generated Content"]["parameters"]["jsonBody"], "AI disclosure lost publication_description")

    routed = 0
    for node in w["nodes"]:
        url = node.get("parameters", {}).get("url")
        raw = json.dumps(node, sort_keys=True)
        node_type = str(node.get("type", "")).lower()
        name = node.get("name")
        check(not (isinstance(url, str) and "shorts.interviewbuddy.cloud" in url), f"{name}: compose call uses public proxy")
        check("api.openai.com" not in raw and "api.anthropic.com" not in raw, f"{name}: LLM provider host bypasses gateway")
        check("openai" not in node_type and "anthropic" not in node_type, f"{name}: native provider node bypasses gateway")
        if isinstance(url, str) and url.startswith("http://llm-gateway:3100/v1/"):
            routed += 1
            params = node.get("parameters", {})
            check(node.get("type") == "n8n-nodes-base.httpRequest", f"{name}: gateway node is not httpRequest")
            check(not node.get("credentials"), f"{name}: provider credential leaked into gateway node")
            check("authentication" not in params and "genericAuthType" not in params, f"{name}: auth params on gateway node")
    check(routed == int(meta.get("llm_gateway_routed_nodes", -1)), "routed LLM node count does not match meta")


def compose_checks(compose: str) -> None:
    has_all(compose, ["NON_BLOCKING_FINAL_QA", "PRODUCTION_BT709_RANGE_NORMALIZATION", '"-color_range", "tv"', "publishing anyway"], "compose.js")
    has_none(compose, ["Final visual QA rejected catastrophic render defects"], "compose.js")


def resolver_checks(resolver: str) -> None:
    has_all(resolver, [
        "candidatePassesGate", "deterministic_template_fallback", "RESOLVE_DEADLINE_MS", "RESOLVER_V5_RUNTIME",
        "V5_VIDEO_SAMPLE_STAGING", "V5_FULL_GATE_EARLY_ACCEPT", "V5_ALWAYS_PUBLISH_BEST_AVAILABLE",
        "best_available_below_quality_target", "no_technically_usable_candidate", "downloadVideoSample",
        "video_contact_sheet_ffmpeg_failed", "quality_gate_passed", "selection_reason",
        # multi-frame video verification
        "VISUAL_MATCHING_V4", "MULTIFRAME_VIDEO_RERANK_V1", "sampleVideoContactSheet", "VIDEO_SAMPLE_FRAMES",
        "best_frame_indices", "normalizeVerifiedFrameIndices", "verifiedRangeFromFrameIndices",
        "video_verified_frame_range_missing", "verified_frame_indices", "materializeVerifiedClip", "actual_video_verified",
        # multi-source retrieval recall
        "RETRIEVAL_RECALL_PHASE2", "SOURCE_QUERY_COMPILER_V1", "fromPexelsVideos", "fromPixabayVideos", "fromPixabayPhotos",
        "fromWikimediaCommons", "CANDIDATE_POOL_MAX", "localSemanticRerank", "diversifyCandidates",
        # telemetry and durable budgets
        "semantic_match", "entity_match", "action_match", "relationship_match", "local_similarity", "frame_similarity",
        "search_rounds", "queries_tried", "library_hit", "vision_call_limit", "failure_reasons", "getBudgetState",
    ], "brollResolver.js")
    has_none(resolver, ["V5_PROOF_MEDIA_TYPE_FILTER", 'reason: state.budget_exhausted || "below_semantic_quality_gate"',
                        "normalizeAnnotationPlan", "annotation_plan", "best_start_sec", "best_end_sec"], "brollResolver.js")


def source_checks() -> None:
    sc = ROOT / "shorts-compose"
    read = lambda p: (ROOT / p).read_text(encoding="utf-8")
    budget = read("shorts-compose/visualBudget.js")
    has_all(budget, ["BROLL_BUDGET_STATE_PATH", "durable_state: true", "BROLL_RUN_MAX_VISION_CALLS || 28",
                     "RUN_MAX_VISION_CALLS", "run_remaining", "scene_remaining", "STATE_PATH"], "visualBudget.js")
    has_all(read("shorts-compose/finalVisualQa.js"), ["debug_artifact", "caption_integrity", "hard_failed"], "finalVisualQa.js")
    has_all(read("shorts-compose/llmRouting.js"), ["LLM_ROUTER_MODE", "LLM_ROUTER_FAIL_OPEN_TO_DIRECT", "auto:smart"], "llmRouting.js")
    has_all(read("shorts-compose/llmGateway.js"), ["requestViaRouter", "paid direct fallback used", "x-session-id"], "llmGateway.js")
    compose_yml = read("docker-compose.yml")
    has_all(compose_yml, [
        "ghcr.io/tashfeenahmed/freellmapi:v0.9.5",
        "NODE_OPTIONS=--max-old-space-size=4096 --require=/app/llmRouting.js",
        "LLM_ROUTER_MODE=${LLM_ROUTER_MODE:-freellmapi}",
        "LLM_ROUTER_FAIL_OPEN_TO_DIRECT=${LLM_ROUTER_FAIL_OPEN_TO_DIRECT:-true}",
        "PIXABAY_KEY=${PIXABAY_KEY:-}",
        "BROLL_RUN_MAX_VISION_CALLS=${BROLL_RUN_MAX_VISION_CALLS:-28}",
        "BROLL_BUDGET_STATE_PATH=${BROLL_BUDGET_STATE_PATH:-/app/data/visual_budget_state.json}",
    ], "docker-compose.yml")
    check("PIXABAY_KEY: ${{ secrets.PIXABAY_KEY }}" in read(".github/workflows/deploy.yml"), "deploy.yml: PIXABAY_KEY secret not wired")

    # Every direct provider host left in compose source must go through Axios,
    # because llmRouting is preloaded specifically to intercept Axios.
    for f in sc.glob("*.js"):
        text = f.read_text(encoding="utf-8")
        if f.name != "llmRouting.js" and re.search(r"api\.openai\.com|api\.anthropic\.com", text):
            check(re.search(r"require\(['\"]axios['\"]\)", text) is not None, f"{f.name}: direct LLM call not interceptable by llmRouting")

    comps = sc / "remotion" / "src" / "compositions"
    for name in ["MapVisual.tsx", "TimelineVisual.tsx", "DiagramVisual.tsx", "AnnotatedReal.tsx"]:
        p = comps / name
        check(p.exists() and p.stat().st_size > 0, f"missing Remotion composition {name}")
    if (comps / "AnnotatedReal.tsx").exists():
        has_none((comps / "AnnotatedReal.tsx").read_text(encoding="utf-8"), ["Callouts are positioned from visual verification"], "AnnotatedReal.tsx")

    for js in ["visualContract.js", "visualBudget.js", "finalVisualQa.js", "llmRouting.js", "llmGateway.js", "semanticReranker.js",
               "clipLibrary.js", "editingEffects.js", "technicalQa.js"]:
        if (sc / js).exists():
            node_check(sc / js)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: ci_checks.py <artifact-dir>")
    out = Path(sys.argv[1])
    workflow_checks(json.loads((out / "workflow.json").read_text(encoding="utf-8")))
    compose_checks((out / "compose.js").read_text(encoding="utf-8"))
    resolver_checks((out / "brollResolver.js").read_text(encoding="utf-8"))
    node_check(out / "compose.js")
    node_check(out / "brollResolver.js")
    source_checks()
    if failures:
        print("CI contract checks FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("CI contract checks OK")


if __name__ == "__main__":
    main()
