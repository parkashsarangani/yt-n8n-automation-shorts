#!/usr/bin/env python3
"""Apply topic/model latency guardrails and dataflow-independent workflow state.

This transform keeps expensive Anthropic calls single-attempt at the n8n
transport layer, keys script retries by execution id, and makes async render
polling independent of paired-item lineage. Cross-node state lives in workflow-
global static data and remains isolated by execution id.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARKER = "TOPIC_LATENCY"
RUNTIME_MARKER = "WORKFLOW_RUNTIME_GUARDS"
POLL_MARKER = "COMPOSE_POLL_STATE"
SCRIPT_RETRY_MARKER = "SCRIPT_RETRY_STATE"
GLOBAL_STATE_MARKER = "WORKFLOW_GLOBAL_STATIC_STATE"
TOPIC_NODE = "Claude: Generate Topic"
POOL_SIZE = 4
MAX_TOKENS = 6000
# llm-gateway's own worst-case budget for a single call (FreeLLMAPI leg plus
# a paid-direct fallback) is bounded at LLM_ROUTER_TIMEOUT_MS (120000ms by
# default - see llmRouting.js's requestViaRouter). A node timeout equal to
# that value leaves zero margin for network/processing overhead between n8n
# and the gateway, so n8n can abort a moment before the gateway's own
# in-budget response arrives (execution 848: "timeout of 120000ms exceeded"
# while the gateway was still mid-fallback). 150s gives real headroom above
# the gateway's bound without approaching the 180s ceiling already used for
# the heavier Visual Director/Repair Script calls.
TIMEOUT_MS = 150000


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise KeyError(f"required n8n node not found: {name}")


def patch_single_attempt_model(node: dict, timeout_ms: int) -> None:
    node["retryOnFail"] = False
    node["maxTries"] = 1
    node["waitBetweenTries"] = 0
    node.setdefault("parameters", {}).setdefault("options", {})["timeout"] = timeout_ms
    node["notes"] = RUNTIME_MARKER + ": single bounded model call; do not duplicate paid requests after a client timeout"


def patch_script_retry_state(workflow: dict) -> None:
    init = node_by_name(workflow, "Init Script Attempt Counter")
    init["parameters"]["jsCode"] = f"""// {SCRIPT_RETRY_MARKER} {GLOBAL_STATE_MARKER}: execution-scoped script retry state shared across workflow nodes.\nconst staticData=$getWorkflowStaticData('global');\nconst runId=String($execution.id||'unknown');\nstaticData.scriptAttempts=staticData.scriptAttempts||{{}};\nconst now=Date.now();\nfor(const [key,value] of Object.entries(staticData.scriptAttempts)){{if(!value||now-Number(value.updatedAt||0)>21600000)delete staticData.scriptAttempts[key];}}\nstaticData.scriptAttempts[runId]={{attempt:0,updatedAt:now}};\nreturn $input.all();"""

    increment = node_by_name(workflow, "Increment Script Attempt")
    increment["parameters"]["jsCode"] = f"""// {SCRIPT_RETRY_MARKER} {GLOBAL_STATE_MARKER}: shared workflow state, isolated by execution id.\nconst staticData=$getWorkflowStaticData('global');\nconst runId=String($execution.id||'unknown');\nstaticData.scriptAttempts=staticData.scriptAttempts||{{}};\nconst state=staticData.scriptAttempts[runId]||{{attempt:0,updatedAt:Date.now()}};\nconst newAttempt=Number(state.attempt||0)+1;\nstate.attempt=newAttempt;state.updatedAt=Date.now();staticData.scriptAttempts[runId]=state;\nconst errors=$input.first().json._validationErrors||[];\nconsole.log(`Script validation failed on attempt ${{newAttempt}}: ${{errors.join(' | ')}}`);\nreturn {{json:{{scriptAttempt:newAttempt,lastErrors:errors}}}};"""

    fail = node_by_name(workflow, "Fail: Script Generation Exhausted")
    fail["parameters"]["jsCode"] = f"""// {SCRIPT_RETRY_MARKER} {GLOBAL_STATE_MARKER}: terminal retry-state cleanup.\nconst staticData=$getWorkflowStaticData('global');\nconst runId=String($execution.id||'unknown');\nif(staticData.scriptAttempts)delete staticData.scriptAttempts[runId];\nconst lastErrors=$input.first().json.lastErrors||[];\nthrow new Error('Script failed the quality gate and exhausted its repair attempts - giving up for this scheduled run rather than posting a bad video. Last errors: '+lastErrors.join(' | '));"""


def patch_compose_polling(workflow: dict) -> None:
    init = node_by_name(workflow, "Init Poll Counter")
    init["parameters"]["jsCode"] = f"""// {POLL_MARKER} {GLOBAL_STATE_MARKER}: execution-scoped render polling state shared across workflow nodes.\nconst staticData=$getWorkflowStaticData('global');\nconst runId=String($execution.id||'unknown');\nstaticData.composePolls=staticData.composePolls||{{}};\nconst now=Date.now();\nfor(const [key,value] of Object.entries(staticData.composePolls)){{if(!value||now-Number(value.updatedAt||0)>21600000)delete staticData.composePolls[key];}}\nconst jobId=String($input.first().json.job_id||'').trim();\nif(!jobId)throw new Error('Start Compose Job response missing job_id');\nstaticData.composePolls[runId]={{jobId,pollAttempt:0,updatedAt:now}};\nreturn {{json:{{jobId,pollAttempt:0}}}};"""

    check = node_by_name(workflow, "Check Compose Status")
    check["parameters"]["url"] = "={{ 'https://shorts.interviewbuddy.cloud/compose-status/' + encodeURIComponent(String($json.jobId || '')) }}"

    increment = node_by_name(workflow, "Increment Poll Attempt")
    increment["parameters"]["jsCode"] = f"""// {POLL_MARKER} {GLOBAL_STATE_MARKER}: dataflow-independent poll counter.\nconst staticData=$getWorkflowStaticData('global');\nconst runId=String($execution.id||'unknown');\nconst state=staticData.composePolls?.[runId];\nif(!state||!state.jobId)throw new Error('Compose poll state missing for execution '+runId);\nconst next=Number(state.pollAttempt||0)+1;\nstate.pollAttempt=next;state.updatedAt=Date.now();\nreturn {{json:{{jobId:state.jobId,pollAttempt:next}}}};"""

    cleanup = f"// {POLL_MARKER} {GLOBAL_STATE_MARKER}: terminal render-state cleanup.\nconst staticData=$getWorkflowStaticData('global');\nconst runId=String($execution.id||'unknown');\nif(staticData.composePolls)delete staticData.composePolls[runId];\n"
    validate = node_by_name(workflow, "Validate Compose Result")
    if POLL_MARKER not in validate["parameters"]["jsCode"]:
        validate["parameters"]["jsCode"] = cleanup + validate["parameters"]["jsCode"]

    fail = node_by_name(workflow, "Fail: Compose Polling Timeout")
    fail["parameters"]["jsCode"] = cleanup + "throw new Error('Compose job did not finish within 75 poll attempts (~600s) - job_id: '+$input.first().json.jobId+'. The render may be stuck; check shorts-compose logs directly.');"


def upgrade(workflow: dict) -> dict:
    node = node_by_name(workflow, TOPIC_NODE)
    params = node.setdefault("parameters", {})
    body = str(params.get("jsonBody", ""))

    if MARKER not in body:
        if "GENERATE 36 DISTINCT candidate topics" in body:
            body = body.replace(
                "GENERATE 36 DISTINCT candidate topics",
                f"{MARKER}: GENERATE {POOL_SIZE} DISTINCT candidate topics",
                1,
            )
        elif "GENERATE 24 DISTINCT candidate topics" in body:
            body = body.replace(
                "GENERATE 24 DISTINCT candidate topics",
                f"{MARKER}: GENERATE {POOL_SIZE} DISTINCT candidate topics",
                1,
            )
        else:
            raise ValueError("could not patch topic pool size: creative-system generation anchor not found")

    token_anchors = ("max_completion_tokens: 9000", "max_completion_tokens: 8192")
    if f"max_completion_tokens: {MAX_TOKENS}" not in body:
        for anchor in token_anchors:
            if anchor in body:
                body = body.replace(anchor, f"max_completion_tokens: {MAX_TOKENS}", 1)
                break
        else:
            raise ValueError("could not patch topic max_tokens: known token anchors not found")

    params["jsonBody"] = body
    patch_single_attempt_model(node, TIMEOUT_MS)
    patch_single_attempt_model(node_by_name(workflow, "Claude: Draft Script (Stage 1)"), TIMEOUT_MS)
    patch_script_retry_state(workflow)
    patch_compose_polling(workflow)
    return workflow


def assert_cross_node_state_model() -> None:
    """Model n8n static-data semantics: node stores differ; global is shared."""
    global_store: dict = {}
    node_stores = {"script_init": {}, "script_inc": {}, "poll_init": {}, "poll_inc": {}, "cleanup": {}}

    def get_store(scope: str, node: str) -> dict:
        return global_store if scope == "global" else node_stores[node]

    # Compose A/B interleaving through distinct nodes with one shared global store.
    get_store("global", "poll_init").setdefault("composePolls", {})["A"] = {"jobId": "job-A", "pollAttempt": 0}
    get_store("global", "poll_init")["composePolls"]["B"] = {"jobId": "job-B", "pollAttempt": 0}
    for run_id in ("A", "B", "A"):
        state = get_store("global", "poll_inc")["composePolls"][run_id]
        state["pollAttempt"] += 1
    if global_store["composePolls"]["A"] != {"jobId": "job-A", "pollAttempt": 2}:
        raise RuntimeError("compose global-state model lost execution A job/poll state")
    if global_store["composePolls"]["B"] != {"jobId": "job-B", "pollAttempt": 1}:
        raise RuntimeError("compose global-state model lost execution B isolation")
    del get_store("global", "cleanup")["composePolls"]["A"]
    if "A" in global_store["composePolls"] or "B" not in global_store["composePolls"]:
        raise RuntimeError("compose global-state terminal cleanup is not execution-scoped")

    # Script retries use the same cross-node/global contract.
    get_store("global", "script_init").setdefault("scriptAttempts", {})["A"] = {"attempt": 0}
    get_store("global", "script_init")["scriptAttempts"]["B"] = {"attempt": 0}
    for run_id in ("A", "B", "A", "A"):
        get_store("global", "script_inc")["scriptAttempts"][run_id]["attempt"] += 1
    if global_store["scriptAttempts"]["A"]["attempt"] != 3 or global_store["scriptAttempts"]["B"]["attempt"] != 1:
        raise RuntimeError("script retry global-state model failed concurrent execution isolation")

    # Demonstrate why node scope is forbidden for these cross-node state machines.
    get_store("node", "poll_init").setdefault("composePolls", {})["X"] = {"jobId": "job-X", "pollAttempt": 0}
    if get_store("node", "poll_inc").get("composePolls", {}).get("X") is not None:
        raise RuntimeError("static-data model unexpectedly shares node-local state across nodes")


def assert_guardrails(workflow: dict) -> None:
    for name in [TOPIC_NODE, "Claude: Draft Script (Stage 1)"]:
        node = node_by_name(workflow, name)
        if node.get("retryOnFail") is not False or node.get("maxTries") != 1:
            raise RuntimeError(f"automatic retry survived on {name}")

    script_nodes = [
        "Init Script Attempt Counter",
        "Increment Script Attempt",
        "Fail: Script Generation Exhausted",
    ]
    poll_nodes = [
        "Init Poll Counter",
        "Increment Poll Attempt",
        "Validate Compose Result",
        "Fail: Compose Polling Timeout",
    ]
    state_nodes = script_nodes + poll_nodes
    state_code = {name: node_by_name(workflow, name)["parameters"]["jsCode"] for name in state_nodes}

    script_init = state_code["Init Script Attempt Counter"]
    script_increment = state_code["Increment Script Attempt"]
    if SCRIPT_RETRY_MARKER not in script_init or SCRIPT_RETRY_MARKER not in script_increment:
        raise RuntimeError("script retry state hardening did not land")
    if ".scriptAttempt = 0" in script_init or "staticData.scriptAttempt ||" in script_increment or "staticData.scriptAttempt =" in script_increment:
        raise RuntimeError("shared scalar script retry state survived")
    if "$execution.id" not in script_init or "$execution.id" not in script_increment:
        raise RuntimeError("script retry state is not keyed by execution id")

    init_code = state_code["Init Poll Counter"]
    increment_code = state_code["Increment Poll Attempt"]
    if POLL_MARKER not in init_code or POLL_MARKER not in increment_code:
        raise RuntimeError("compose polling state hardening did not land")
    if "$('Init Poll Counter').item" in increment_code:
        raise RuntimeError("compose poll counter still depends on paired-item lineage")
    if "$json.jobId" not in node_by_name(workflow, "Check Compose Status")["parameters"]["url"]:
        raise RuntimeError("compose status lookup lost loop-carried jobId")

    for name, code in state_code.items():
        if "$getWorkflowStaticData('node')" in code:
            raise RuntimeError(f"{name} uses node-local static data for cross-node state")
        if "$getWorkflowStaticData('global')" not in code:
            raise RuntimeError(f"{name} does not use workflow-global static data")
        if GLOBAL_STATE_MARKER not in code:
            raise RuntimeError(f"{name} missing global-state contract marker")

    assert_cross_node_state_model()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: upgrade-topic-latency.py INPUT_WORKFLOW OUTPUT_WORKFLOW")
    src, dst = map(Path, sys.argv[1:])
    workflow = json.loads(src.read_text())
    upgraded = upgrade(workflow)
    assert_guardrails(upgraded)
    dst.write_text(json.dumps(upgraded, indent=2) + "\n")
    print(f"topic/runtime latency workflow written to {dst}")


if __name__ == "__main__":
    main()
