#!/usr/bin/env python3
"""Pre-production audit for the V5 Shorts pipeline.

This audit validates the same generated artifacts that deploy consumes instead
of rebuilding an older transform chain with contradictory assumptions. It
protects two explicit production policies:

1. quality scores and rendered-pixel QA are advisory and never block an
   otherwise renderable scheduled Short; and
2. all LLM/VLM traffic is free-first through the internal FreeLLMAPI gateway,
   with a runtime-selectable paid direct fallback so provider migration cannot
   become a new publishing single point of failure.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LLM_GATEWAY_ORIGIN = "http://llm-gateway:3100"
DIRECT_PROVIDER_HOSTS = ("api.openai.com", "api.anthropic.com")


def die(message: str) -> None:
    raise RuntimeError(message)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(cmd)}):\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    die(f"required workflow node missing: {name}")


def require(text: str, markers: list[str], label: str) -> None:
    missing = [m for m in markers if m not in text]
    if missing:
        die(f"{label} missing: {', '.join(missing)}")


def forbid(text: str, markers: list[str], label: str) -> None:
    present = [m for m in markers if m in text]
    if present:
        die(f"{label} contains forbidden behavior: {', '.join(present)}")


def audit_workflow(path: Path) -> None:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    meta = workflow.get("meta", {})
    if meta.get("visual_matching_version") != "4":
        die("generated workflow lost visual_matching_version=4 contract")
    if meta.get("production_build_version") != "5":
        die("generated workflow is not a V5 production build")
    if meta.get("compose_service_transport") != "docker_internal":
        die("generated workflow is not using Docker-internal compose transport")
    if meta.get("llm_gateway_transport") != "docker_internal":
        die("generated workflow is not using the Docker-internal LLM gateway")

    visual = str(node_by_name(workflow, "Claude: Visual Director").get("parameters", {}).get("jsonBody", ""))
    require(visual, [
        "VISUAL_MATCHING_V4", "visual_claim", "required_entities", "required_actions",
        "required_relationships", "forbidden_visuals", "visual_proof_mode",
        "Do NOT request callout boxes",
    ], "Visual Director contract")
    forbid(visual, ["visually locate callout boxes on those pixels", "return annotations as an array"], "Visual Director contract")

    validator = str(node_by_name(workflow, "Validate Final Script").get("parameters", {}).get("jsCode", ""))
    require(validator, ["VISUAL_MATCHING_V4 contract gate", "needs at least 3 search_queries", "deterministicModes"], "script validator")

    resolver_node = node_by_name(workflow, "Resolve B-roll")
    resolver_url = str(resolver_node.get("parameters", {}).get("url", ""))
    if not resolver_url.startswith("http://shorts-compose:4000/"):
        die(f"Resolve B-roll still traverses public proxy: {resolver_url}")
    resolver_body = str(resolver_node.get("parameters", {}).get("jsonBody", ""))
    require(resolver_body, [
        "visual_claim", "required_entities", "required_actions", "required_relationships",
        "forbidden_visuals", "visual_proof_mode", "template_fallback", "run_id",
        "retrieval_scene_count", "retrieval_scene_position", "$execution.id",
    ], "resolver workflow payload")
    if int(resolver_node.get("parameters", {}).get("options", {}).get("timeout", 0)) < 150000:
        die("Resolve B-roll node timeout is below the V5 internal-service deadline envelope")

    tag = str(node_by_name(workflow, "Tag B-roll").get("parameters", {}).get("jsCode", ""))
    require(tag, [
        "asset_semantic_match", "asset_entity_match", "asset_action_match", "asset_relationship_match",
        "verified_frame_indices", "actual_video_verified", "verified-real scene",
        "deterministic fallback", "r.type==='template'", "quality_gate_passed", "selection_reason",
    ], "Tag B-roll")
    forbid(tag, ["annotation_plan", "annotations:"], "Tag B-roll")

    merge = str(node_by_name(workflow, "Merge By scene_index (not position)").get("parameters", {}).get("jsCode", ""))
    require(merge, ["_attribution", "publicationDescription", "Sources / credits", "useanimations.com (CC BY 4.0)"], "publication metadata merge")
    upload_description = str(node_by_name(workflow, "YouTube: Upload Draft").get("parameters", {}).get("options", {}).get("description", ""))
    if "publication_description" not in upload_description:
        die("YouTube upload description no longer uses the attribution-bearing publication description")
    disclosure = str(node_by_name(workflow, "Disclose AI-Generated Content").get("parameters", {}).get("jsonBody", ""))
    if "publication_description" not in disclosure:
        die("post-upload disclosure step would overwrite source credits")

    routed = []
    for node in workflow.get("nodes", []):
        params = node.get("parameters", {})
        url = params.get("url")
        raw = json.dumps(node, sort_keys=True)
        node_type = str(node.get("type", "")).lower()

        if isinstance(url, str) and "shorts.interviewbuddy.cloud" in url:
            die(f"public compose proxy remains in workflow node {node.get('name')}: {url}")

        # Search the complete generated node, not only its top-level URL. This
        # catches a future Code node/expression/nested request that tries to
        # bypass the reversible gateway.
        if any(host in raw for host in DIRECT_PROVIDER_HOSTS):
            die(f"direct LLM provider host remains in generated node {node.get('name')}")
        if "openai" in node_type or "anthropic" in node_type:
            die(f"provider-specific n8n node bypasses internal LLM gateway: {node.get('name')} ({node.get('type')})")

        if isinstance(url, str) and url.startswith(f"{LLM_GATEWAY_ORIGIN}/v1/"):
            routed.append(node)
            if node.get("type") != "n8n-nodes-base.httpRequest":
                die(f"gateway-routed node has unexpected type: {node.get('name')} -> {node.get('type')}")
            if node.get("credentials"):
                die(f"provider credentials remain on gateway-routed node {node.get('name')}")
            if "authentication" in params or "genericAuthType" in params:
                die(f"provider authentication metadata remains on gateway-routed node {node.get('name')}")
            headers = params.get("headerParameters", {}).get("parameters", [])
            for header in headers if isinstance(headers, list) else []:
                if str(header.get("name", "")).lower() in {"authorization", "x-api-key"}:
                    die(f"provider authorization header remains on gateway-routed node {node.get('name')}")

    expected_routed = int(meta.get("llm_gateway_routed_nodes", 0))
    if not routed or len(routed) != expected_routed:
        die(f"LLM gateway routing count mismatch: generated={len(routed)} meta={expected_routed}")


def audit_resolver(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    require(text, [
        "VISUAL_MATCHING_V4", "RETRIEVAL_RECALL_PHASE2", "SOURCE_QUERY_COMPILER_V1",
        "MULTIFRAME_VIDEO_RERANK_V1", "localSemanticRerank", "diversifyCandidates",
        "fromPexelsVideos", "fromPixabayVideos", "fromPixabayPhotos", "fromWikimediaCommons",
        "candidatePassesGate", "passesSemanticGate", "deterministic_template_fallback",
        "RESOLVE_DEADLINE_MS", "RESOLVER_V5_RUNTIME", "V5_VIDEO_VERIFY_USES_SCENE_BUDGET",
        "V5_VIDEO_SAMPLE_STAGING", "V5_FULL_GATE_EARLY_ACCEPT", "V5_ALWAYS_PUBLISH_BEST_AVAILABLE",
        "best_available_below_quality_target", "no_technically_usable_candidate", "downloadVideoSample",
        "video_contact_sheet_ffmpeg_failed", "materializeVerifiedClip", "verifiedRangeFromFrameIndices",
        "vision_call_limit", "failure_reasons", "quality_gate_passed", "selection_reason",
    ], "production resolver")
    forbid(text, [
        "normalizeAnnotationPlan", "annotation_plan", "return annotations as an array",
        "annotated_real_missing_grounded_callouts", "const target = subj ||",
        "best_start_sec", "best_end_sec", "V5_PROOF_MEDIA_TYPE_FILTER",
        'reason: state.budget_exhausted || "below_semantic_quality_gate"',
    ], "production resolver")
    run(["node", "--check", str(path)])


def audit_compose(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    require(text, [
        "VISUAL_MATCHING_V4_COMPOSE", "reviewFinalVideo", "NON_BLOCKING_FINAL_QA",
        "publishing anyway", "PRODUCTION_BT709_RANGE_NORMALIZATION", '"-color_range", "tv"',
        "MapVisual", "TimelineVisual", "DiagramVisual", "AnnotatedReal",
    ], "production compose")
    forbid(text, ["Final visual QA rejected catastrophic render defects", "CATASTROPHIC_FINAL_QA_GATE"], "production compose")
    run(["node", "--check", str(path)])


def audit_llm_runtime() -> None:
    routing_path = ROOT / "shorts-compose/llmRouting.js"
    gateway_path = ROOT / "shorts-compose/llmGateway.js"
    routing = routing_path.read_text(encoding="utf-8")
    gateway = gateway_path.read_text(encoding="utf-8")

    require(routing, [
        "LLM_ROUTER_MODE", "freellmapi", "LLM_ROUTER_FAIL_OPEN_TO_DIRECT",
        "FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY", "FREELLMAPI_TEXT_MODEL",
        "FREELLMAPI_VISION_MODEL", "auto:smart", "requestViaRouter",
        "installAxiosRouting", "makeDirectRequest", "makeFreeRequest",
    ], "LLM routing runtime")
    require(gateway, [
        "requestViaRouter", "/v1/chat/completions", "/v1/responses", "/v1/messages",
        "X-LLM-Route", "X-LLM-Fallback", "paid direct fallback used",
        "safeUpstreamHeaders", "x-session-id",
    ], "internal LLM gateway")
    run(["node", "--check", str(routing_path)])
    run(["node", "--check", str(gateway_path)])

    # Direct URLs remain in existing source modules on purpose: preloading
    # llmRouting lets us switch routing centrally without invasive module edits.
    # Therefore every such source call must use Axios; a future raw fetch/SDK
    # path would escape the interceptor and must fail the audit.
    for path in (ROOT / "shorts-compose").rglob("*.js"):
        if "node_modules" in path.parts or path.name == "llmRouting.js":
            continue
        text = path.read_text(errors="ignore", encoding="utf-8")
        if not any(host in text for host in DIRECT_PROVIDER_HOSTS):
            continue
        if 'require("axios")' not in text and "require('axios')" not in text:
            die(f"direct provider URL is not interceptable by llmRouting Axios shim: {path.relative_to(ROOT)}")


def audit_static_runtime() -> None:
    budget = (ROOT / "shorts-compose/visualBudget.js").read_text(encoding="utf-8")
    require(budget, [
        "BROLL_RUN_MAX_VISION_CALLS || 28", "BROLL_BUDGET_STATE_PATH",
        "STATE_PATH", "acquireLock", "writeState", "durable_state: true",
    ], "durable visual budget")
    forbid(budget, ["const runBudgets = new Map()", "const resultCache = new Map()"], "durable visual budget")
    run(["node", "--check", str(ROOT / "shorts-compose/visualBudget.js")])

    final_qa = (ROOT / "shorts-compose/finalVisualQa.js").read_text(encoding="utf-8")
    require(final_qa, [
        "debug_artifact", "critical_content_clipped", "editorial_cleanliness",
        "safe_area", "caption_integrity", "hard_failed", "hard_issues", "soft_issues",
    ], "final rendered QA telemetry")
    run(["node", "--check", str(ROOT / "shorts-compose/finalVisualQa.js")])

    annotated = (ROOT / "shorts-compose/remotion/src/compositions/AnnotatedReal.tsx").read_text(encoding="utf-8")
    forbid(annotated, [
        "Callouts are positioned from visual verification", "annotations.slice", "annotations.map",
        "<line", "<circle", "{a.label}",
    ], "AnnotatedReal audience renderer")
    require(annotated, ['objectFit: "contain"', "must never leak into the final Short"], "AnnotatedReal audience renderer")

    dc = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    require(dc, [
        "BROLL_BUDGET_STATE_PATH=${BROLL_BUDGET_STATE_PATH:-/app/data/visual_budget_state.json}",
        "BROLL_RESOLVE_DEADLINE_MS=${BROLL_RESOLVE_DEADLINE_MS:-135000}",
        "BROLL_FINAL_QA_HARD_SCORE=${BROLL_FINAL_QA_HARD_SCORE:-65}",
        "BROLL_FINAL_QA_HARD_LAYOUT=${BROLL_FINAL_QA_HARD_LAYOUT:-55}",
        "ghcr.io/tashfeenahmed/freellmapi:v0.9.5",
        "127.0.0.1:3001:3001",
        "freellmapi_data:/app/server/data",
        "NODE_OPTIONS=--max-old-space-size=4096 --require=/app/llmRouting.js",
        "LLM_ROUTER_MODE=${LLM_ROUTER_MODE:-freellmapi}",
        "LLM_ROUTER_FAIL_OPEN_TO_DIRECT=${LLM_ROUTER_FAIL_OPEN_TO_DIRECT:-true}",
        "FREELLMAPI_BASE_URL=${FREELLMAPI_BASE_URL:-http://freellmapi:3001/v1}",
    ], "docker production settings")

    m = re.search(r"BROLL_RUN_MAX_VISION_CALLS=\$\{BROLL_RUN_MAX_VISION_CALLS:-(\d+)\}", dc)
    if not m or int(m.group(1)) < 28:
        die("run-wide vision budget unexpectedly dropped below 28")

    audit_llm_runtime()


def audit_build_wiring() -> None:
    quality = (ROOT / ".github/workflows/quality-check.yml").read_text(encoding="utf-8")
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    for text, label in [(quality, "quality CI"), (deploy, "deploy")]:
        require(text, ["scripts/build_production_artifacts.py"], label)
    if "upgrade-compose-runtime-hardening.py" in quality or "upgrade-compose-runtime-hardening.py" in deploy:
        die("CI/deploy reintroduced an independent legacy runtime transform chain")
    require(quality, [
        "cmp /tmp/production-a/manifest.json /tmp/production-b/manifest.json",
        "docker manifest inspect ghcr.io/tashfeenahmed/freellmapi:v0.9.5",
        "LLM_ROUTER_FAIL_OPEN_TO_DIRECT",
    ], "quality deterministic-build/LLM routing check")
    require(deploy, [
        "/tmp/yt-shorts-production/workflow.json", "/tmp/yt-shorts-production/compose.js",
        "/tmp/yt-shorts-production/brollResolver.js", "FREELLMAPI_ENCRYPTION_KEY",
        "FREELLMAPI_API_KEY", "LLM_ROUTER_MODE", "LLM_ROUTER_FAIL_OPEN_TO_DIRECT",
        "docker compose up -d --no-deps llm-gateway shorts-compose",
    ], "deploy artifact/LLM routing consumption")


def audit() -> None:
    with tempfile.TemporaryDirectory(prefix="shorts-preprod-v5-") as td:
        out = Path(td) / "production"
        run([sys.executable, str(ROOT / "scripts/build_production_artifacts.py"), "--output-dir", str(out)])
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("build_version") != "5" or set(manifest.get("artifacts", {})) != {"workflow.json", "compose.js", "brollResolver.js"}:
            die(f"invalid production artifact manifest: {manifest}")
        audit_workflow(out / "workflow.json")
        audit_resolver(out / "brollResolver.js")
        audit_compose(out / "compose.js")

    audit_static_runtime()
    audit_build_wiring()
    print("PREPROD AUDIT PASSED: V5 best-available publishing, non-blocking rendered QA, FreeLLMAPI-first reversible LLM routing, clean verified-real rendering, attribution, durable budgets, internal transport, and deterministic build wiring are consistent")


if __name__ == "__main__":
    try:
        audit()
    except Exception as exc:
        print(f"PREPROD AUDIT FAILED: {exc}", file=sys.stderr)
        raise
