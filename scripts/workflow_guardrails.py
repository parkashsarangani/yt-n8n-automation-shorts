#!/usr/bin/env python3
"""Final Shorts workflow normalization and shared-contract assertions."""
from __future__ import annotations

import json
from pathlib import Path

from workflow_contracts import (
    BROLL_FIRST_FRAME_TARGET,
    BROLL_SUPPORT_TARGET,
    BROLL_TEMPLATE_FALLBACK_THRESHOLD,
    EXPECTED_NODE_CONTRACTS,
    OVERALL_WEAKEST_CAP,
    QUALITY_MINIMUMS,
    REQUIRED_WORKFLOW_MARKERS,
    VISUAL_PLAN_MINIMUM,
)

ROOT = Path(__file__).resolve().parents[1]
STATE_NODES = (
    "Init Script Attempt Counter",
    "Increment Script Attempt",
    "Fail: Script Generation Exhausted",
    "Init Poll Counter",
    "Increment Poll Attempt",
    "Validate Compose Result",
    "Fail: Compose Polling Timeout",
)


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise RuntimeError(f"required workflow node missing: {name}")


def _require_execution_id(workflow: dict) -> None:
    """Fail loudly instead of letting concurrent runs share an `unknown` key."""
    old = "const runId=String($execution.id||'unknown');"
    new = (
        "const runId=String($execution.id||'').trim();"
        "if(!runId)throw new Error('Missing n8n execution id for workflow-scoped state');"
    )
    for name in STATE_NODES:
        node = node_by_name(workflow, name)
        code = str(node.get("parameters", {}).get("jsCode", ""))
        if old in code:
            code = code.replace(old, new)
        node["parameters"]["jsCode"] = code


def _fix_first_frame_selection(workflow: dict) -> None:
    """Use scene_index, not raw array position, for first-frame query treatment."""
    node = node_by_name(workflow, "Validate Final Script")
    code = str(node["parameters"]["jsCode"])
    code = code.replace(
        "const variantSuffix=i===0?'close up':",
        "const variantSuffix=Number(s.scene_index)===0?'close up':",
    )
    code = code.replace(
        "const wanted=i===0?4:3;",
        "const wanted=Number(s.scene_index)===0?4:3;",
    )
    node["parameters"]["jsCode"] = code


def _surface_degraded_broll(workflow: dict) -> None:
    """Persist degraded-media telemetry instead of silently accepting 60-71."""
    tag = node_by_name(workflow, "Tag B-roll")
    tag_code = str(tag["parameters"]["jsCode"])
    warning = (
        "if(r.degraded===true)console.warn('[broll] degraded asset scene='+sceneIndex+"
        "' score='+(r.score??'n/a')+' threshold='+(r.threshold??'n/a')+"
        "' reason='+(r.fallback_reason||'below_quality_target'));"
    )
    if warning not in tag_code:
        anchor = "const out={scene_index:"
        if anchor not in tag_code:
            raise RuntimeError("Tag B-roll telemetry anchor missing")
        tag_code = tag_code.replace(anchor, warning + "\n" + anchor, 1)
    tag["parameters"]["jsCode"] = tag_code

    merge = node_by_name(workflow, "Merge By scene_index (not position)")
    code = str(merge["parameters"]["jsCode"])
    if "const degraded_asset_count" not in code:
        anchor = "const asset_quality_avg ="
        pos = code.find(anchor)
        if pos < 0:
            raise RuntimeError("asset quality aggregate anchor missing")
        line_end = code.find("\n", pos)
        if line_end < 0:
            raise RuntimeError("asset quality aggregate line is malformed")
        insert = (
            "\nconst degraded_scene_indexes = merged.filter(v => v.asset_degraded === true).map(v => v.scene_index);"
            "\nconst degraded_asset_count = degraded_scene_indexes.length;"
            "\nconst quality_gate_fail_count = merged.filter(v => v.quality_gate_passed === false).length;"
        )
        code = code[:line_end] + insert + code[line_end:]
        return_anchor = "    asset_quality_avg,\n    data: merged"
        return_replacement = (
            "    asset_quality_avg,\n"
            "    degraded_asset_count,\n"
            "    quality_gate_fail_count,\n"
            "    degraded_scene_indexes,\n"
            "    data: merged"
        )
        if return_anchor not in code:
            raise RuntimeError("merge return telemetry anchor missing")
        code = code.replace(return_anchor, return_replacement, 1)
    merge["parameters"]["jsCode"] = code

    log = node_by_name(workflow, "Log Published Video")
    body = str(log["parameters"]["jsonBody"])
    if "degraded_asset_count:" not in body:
        anchor = "asset_quality_avg: ($('Merge By scene_index (not position)').item.json.asset_quality_avg ?? null)"
        replacement = (
            anchor
            + ", degraded_asset_count: ($('Merge By scene_index (not position)').item.json.degraded_asset_count ?? 0)"
            + ", quality_gate_fail_count: ($('Merge By scene_index (not position)').item.json.quality_gate_fail_count ?? 0)"
            + ", degraded_scene_indexes: ($('Merge By scene_index (not position)').item.json.degraded_scene_indexes || [])"
        )
        if anchor not in body:
            raise RuntimeError("performance-log asset telemetry anchor missing")
        body = body.replace(anchor, replacement, 1)
    log["parameters"]["jsonBody"] = body


def _enforce_remotion_template_cap(workflow: dict) -> None:
    """Block template-heavy renders after retrieval fallback, before compose/upload."""
    merge = node_by_name(workflow, "Merge By scene_index (not position)")
    code = str(merge["parameters"]["jsCode"])
    if "const remotion_template_scene_indexes" not in code:
        anchor = "const asset_quality_avg ="
        pos = code.find(anchor)
        if pos < 0:
            raise RuntimeError("asset quality aggregate anchor missing for Remotion template cap")
        line_end = code.find("\n", pos)
        if line_end < 0:
            raise RuntimeError("asset quality aggregate line malformed for Remotion template cap")
        insert = (
            # annotated_real is a REAL photo/footage candidate rendered through
            # a Remotion composition to burn in pixel-anchored callout labels -
            # Tag B-roll sets visual_source='template' for it purely as a
            # rendering-pipeline detail (see brollResolver.js), not because it
            # is a designed graphic card standing in for missing real content.
            # Counting it against the "avoid template-heavy renders" cap would
            # reject a scene that found genuinely well-matched real footage.
            "\nconst remotion_template_scene_indexes = merged.filter(v => !v?.template_data?.is_outro && v.visual_source === 'template' && v.template_name !== 'annotated_real').map(v => Number(v.scene_index));"
            "\nif (remotion_template_scene_indexes.includes(0)) throw new Error('Remotion template cap failed: scene 0 cannot be a Remotion template; the opening visual must be real/archive footage or imagery');"
            "\nif (remotion_template_scene_indexes.length > 1) throw new Error('Remotion template cap failed: video has ' + remotion_template_scene_indexes.length + ' Remotion template scenes (' + remotion_template_scene_indexes.join(',') + '); maximum is 1');"
        )
        code = code[:line_end] + insert + code[line_end:]
    merge["parameters"]["jsCode"] = code


def finalize_workflow(workflow: dict) -> dict:
    _require_execution_id(workflow)
    _fix_first_frame_selection(workflow)
    _surface_degraded_broll(workflow)
    _enforce_remotion_template_cap(workflow)
    return workflow


def assert_workflow_contracts(workflow: dict) -> None:
    for name, contract in EXPECTED_NODE_CONTRACTS.items():
        node = node_by_name(workflow, name)
        got_timeout = node.get("parameters", {}).get("options", {}).get("timeout")
        if got_timeout != contract["timeout"]:
            raise RuntimeError(f"timeout drift: {name}={got_timeout}, expected {contract['timeout']}")
        if contract.get("single_attempt"):
            if node.get("retryOnFail") is not False or node.get("maxTries") != 1:
                raise RuntimeError(f"duplicate-call risk: {name} is not single-attempt")
        body = str(node.get("parameters", {}).get("jsonBody", ""))
        for marker in contract.get("body_markers", ()):
            if marker not in body:
                raise RuntimeError(f"{name} lost required contract marker: {marker}")

    serialized = json.dumps(workflow, separators=(",", ":"))
    for marker in REQUIRED_WORKFLOW_MARKERS:
        if marker not in serialized:
            raise RuntimeError(f"generated workflow lost marker: {marker}")

    performance_body = str(node_by_name(workflow, "Log Published Video")["parameters"]["jsonBody"])
    for marker in ("creative_dna", "degraded_asset_count", "quality_gate_fail_count", "degraded_scene_indexes"):
        if marker not in performance_body:
            raise RuntimeError(f"performance log lost actionable telemetry: {marker}")

    validator = str(node_by_name(workflow, "Validate Final Script")["parameters"]["jsCode"])
    for metric, minimum in QUALITY_MINIMUMS.items():
        if f"{metric}: {minimum}" not in validator:
            raise RuntimeError(f"quality floor drift: {metric} expected {minimum}")
    if f"visual_plan_quality)<{VISUAL_PLAN_MINIMUM}" not in validator:
        raise RuntimeError("visual-plan quality floor drifted")
    if f"overall > weakest + {OVERALL_WEAKEST_CAP}" not in validator:
        raise RuntimeError("overall weakest-dimension cap drifted")
    if "const wanted=i===0?4:3;" in validator or "const variantSuffix=i===0?" in validator:
        raise RuntimeError("first-frame selection still depends on raw array index")
    if "Number(s.scene_index)===0" not in validator:
        raise RuntimeError("first-frame selection is not keyed by scene_index")

    for name in STATE_NODES:
        code = str(node_by_name(workflow, name)["parameters"]["jsCode"])
        if "$execution.id||'unknown'" in code or "$execution.id || 'unknown'" in code:
            raise RuntimeError(f"{name} still has a colliding execution-id fallback")
        if "Missing n8n execution id for workflow-scoped state" not in code:
            raise RuntimeError(f"{name} does not fail loudly when execution id is missing")

    merge_code = str(node_by_name(workflow, "Merge By scene_index (not position)")["parameters"]["jsCode"])
    for marker in ("degraded_asset_count", "quality_gate_fail_count", "degraded_scene_indexes", "remotion_template_scene_indexes"):
        if marker not in merge_code:
            raise RuntimeError(f"merge payload lost degraded-media telemetry: {marker}")
    if "maximum is 1" not in merge_code or "scene 0 cannot be a Remotion template" not in merge_code:
        raise RuntimeError("merge payload lost Remotion template cap")

    docker = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    expected_env = (
        f"BROLL_SCORE_THRESHOLD=${{BROLL_SCORE_THRESHOLD:-{BROLL_SUPPORT_TARGET}}}",
        f"BROLL_FIRST_FRAME_THRESHOLD=${{BROLL_FIRST_FRAME_THRESHOLD:-{BROLL_FIRST_FRAME_TARGET}}}",
        f"BROLL_TEMPLATE_FALLBACK_THRESHOLD=${{BROLL_TEMPLATE_FALLBACK_THRESHOLD:-{BROLL_TEMPLATE_FALLBACK_THRESHOLD}}}",
    )
    for value in expected_env:
        if value not in docker:
            raise RuntimeError(f"docker media policy drifted: missing {value}")
