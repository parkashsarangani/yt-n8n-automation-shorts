#!/usr/bin/env python3
"""Authoritative V5 production artifact builder with Shorts growth policy.

The retained V5 builder remains the owner of visual matching, resolver
hardening, final-render QA, attribution, BT.709 normalization and LLM routing.
This wrapper then applies only the measured-growth changes (analytics cohorts,
topic policy/dedup, duration/outro experiment and policy telemetry), validates
them, and rewrites the manifest. Keeping production_build_version=5 is
intentional: the render/resolver artifact contract is unchanged; the independent
`shorts_growth_policy` version tracks the creative/analytics experiment layer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import build_production_artifacts_v5 as v5

BUILD_VERSION = "5"
POLICY_VERSION = "shorts-growth-v2"
INTERNAL_SERVICE_ORIGIN = v5.INTERNAL_SERVICE_ORIGIN
PUBLIC_SERVICE_ORIGIN = v5.PUBLIC_SERVICE_ORIGIN
LLM_GATEWAY_ORIGIN = v5.LLM_GATEWAY_ORIGIN
REAL_MEDIA_MIX_GUARD = v5.REAL_MEDIA_MIX_GUARD
node_by_name = v5.node_by_name


def run(*args: str, cwd: Path) -> None:
    subprocess.run([sys.executable, *args], cwd=str(cwd), check=True)


def assert_growth_workflow(workflow: dict) -> None:
    if workflow.get("meta", {}).get("shorts_growth_policy") != POLICY_VERSION:
        raise RuntimeError("generated workflow lost Shorts growth policy version")

    names = {n.get("name") for n in workflow.get("nodes", [])}
    if "Deduplicate Topic Pool" not in names:
        raise RuntimeError("generated workflow lost semantic topic dedup gate")

    gen_body = str(node_by_name(workflow, "Claude: Generate Topic").get("parameters", {}).get("jsonBody", ""))
    for marker in ("GENERATE 10 DISTINCT candidate topics", "7 exploit / 2 adjacent / 1 explore", "canonical_key"):
        if marker not in gen_body:
            raise RuntimeError(f"generated topic policy missing marker: {marker}")

    writer_body = str(node_by_name(workflow, "Claude: Draft Script (Stage 1)").get("parameters", {}).get("jsonBody", ""))
    if "averaged 84 views" in writer_body or "3.5x fewer views" in writer_body:
        raise RuntimeError("stale fixed channel-performance claim survived production writer prompt")
    for marker in ("DYNAMIC PERFORMANCE GUIDANCE", "28-36 second FINAL rendered Short", "length_exception_reason"):
        if marker not in writer_body:
            raise RuntimeError(f"generated writer lost growth rule: {marker}")

    validator_code = str(node_by_name(workflow, "Validate Final Script").get("parameters", {}).get("jsCode", ""))
    for marker in ("outro_experiment_arm", "current_outro", "no_outro", "policy_version='shorts-growth-v2'", "length_exception_reason is required"):
        if marker not in validator_code:
            raise RuntimeError(f"generated validator lost growth invariant: {marker}")

    analytics_url = str(node_by_name(workflow, "YouTube: Video Analytics").get("parameters", {}).get("url", ""))
    if "engagedViews" not in analytics_url:
        raise RuntimeError("generated YouTube Analytics query does not request engagedViews")

    intervals = node_by_name(workflow, "Measure Schedule").get("parameters", {}).get("rule", {}).get("interval", [])
    if not intervals or intervals[0].get("expression") != "0 */6 * * *":
        raise RuntimeError("generated measurement schedule is not six-hourly")

    log_body = str(node_by_name(workflow, "Log Published Video").get("parameters", {}).get("jsonBody", ""))
    for marker in ("topic_strategy_arm", "topic_predicted_score", "duration_sec", "outro_experiment_arm", "policy_version"):
        if marker not in log_body:
            raise RuntimeError(f"generated performance log lost telemetry field: {marker}")

    dedup_url = str(node_by_name(workflow, "Deduplicate Topic Pool").get("parameters", {}).get("url", ""))
    if not dedup_url.startswith(INTERNAL_SERVICE_ORIGIN):
        raise RuntimeError(f"topic dedup still traverses public proxy: {dedup_url}")


def assert_growth_compose(text: str) -> None:
    required = (
        "SHORTS_GROWTH_V2_COMPOSE",
        "TOPIC_HISTORY_MAX = Math.max(500",
        'app.post("/topic-dedup"',
        "getMeasurementPlan",
        "allowFallbackOutro",
        "lastIsOutro = Boolean",
        "duration_sec: Number(totalVideoDuration.toFixed(3))",
        "strategy_arm",
        "canonical_key",
    )
    for marker in required:
        if marker not in text:
            raise RuntimeError(f"generated compose lost growth invariant: {marker}")
    if "const TOPIC_HISTORY_MAX = 90;" in text:
        raise RuntimeError("legacy 90-topic history cap survived production build")


def build(root: Path, output: Path) -> dict:
    root = root.resolve()
    output = output.resolve()

    # Build the exact V5 production artifacts first. This executes all existing
    # visual/resolver/QA validations before growth policy is allowed to touch the
    # workflow or compositor.
    v5.build(root, output)

    workflow_out = output / "workflow.json"
    compose_out = output / "compose.js"
    resolver_out = output / "brollResolver.js"

    # Growth policy is the final workflow/runtime layer by design, so no later
    # legacy transform can silently restore the old metrics, 90-topic cap, fixed
    # outro or stale performance assumptions.
    run("scripts/upgrade-shorts-growth-v2.py", str(workflow_out), str(workflow_out), cwd=root)
    run("scripts/upgrade-compose-growth-v2.py", str(compose_out), str(compose_out), cwd=root)

    # The V5 postprocessor internalized service calls before this policy existed.
    # Growth adds /topic-dedup afterward, so internalize once more to preserve
    # the production Docker-network transport invariant.
    workflow = json.loads(workflow_out.read_text())
    v5.internalize_compose_service_urls(workflow)
    workflow.setdefault("meta", {})["production_build_version"] = BUILD_VERSION
    workflow["meta"]["shorts_growth_policy"] = POLICY_VERSION
    workflow_out.write_text(json.dumps(workflow, indent=2) + "\n")

    assert_growth_workflow(workflow)
    assert_growth_compose(compose_out.read_text())

    # Recheck inherited V5 invariants. Recommendation #12 is therefore enforced
    # structurally: this growth pass cannot quietly replace the B-roll/resolver
    # subsystem that was already validated by the retained builder.
    resolver_text = resolver_out.read_text()
    for marker in ("RESOLVER_V5_RUNTIME", "V5_ALWAYS_PUBLISH_BEST_AVAILABLE", "best_available_below_quality_target"):
        if marker not in resolver_text:
            raise RuntimeError(f"V5 resolver invariant missing after growth build: {marker}")
    compose_text = compose_out.read_text()
    for marker in ("VISUAL_MATCHING_V4_COMPOSE", "NON_BLOCKING_FINAL_QA", "PRODUCTION_BT709_RANGE_NORMALIZATION", "reviewFinalVideo"):
        if marker not in compose_text:
            raise RuntimeError(f"V5 compose invariant missing after growth build: {marker}")

    artifact_paths = [workflow_out, compose_out, resolver_out]
    manifest = {
        "build_version": BUILD_VERSION,
        "policy_version": POLICY_VERSION,
        "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact_paths},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output_dir), sort_keys=True))


if __name__ == "__main__":
    main()
