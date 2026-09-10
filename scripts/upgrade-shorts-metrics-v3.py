#!/usr/bin/env python3
"""Capture the Shorts gate-two signals: retention curve and traffic source.

The batch analytics call answers "how did this Short do". It cannot answer
"why did distribution stop", because that is decided by where viewers swipe
away and whether the Short was served to the Shorts feed at all. Those two
reports are per-video (different dimensions, one video per call), so this stage
adds a bounded per-video pass after the batch ingest and feeds the results back
onto the same fixed-age cohort snapshot.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARKER = "SHORTS_METRICS_V3"
ANALYTICS_ORIGIN = "https://youtubeanalytics.googleapis.com/v2/reports"


def node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    raise KeyError(f"required workflow node not found: {name}")


def widen_batch_metrics(workflow: dict) -> None:
    """Add the cheap per-video metrics that ride along on the existing call."""
    node = node_by_name(workflow, "YouTube: Video Analytics")
    url = str(node.get("parameters", {}).get("url", ""))
    if "metrics=" not in url:
        raise RuntimeError("analytics node lost its metrics parameter")
    head, _, rest = url.partition("metrics=")
    current, sep, tail = rest.partition("&")
    wanted = [m for m in current.split(",") if m]
    for extra in ("estimatedMinutesWatched", "subscribersLost", "dislikes"):
        if extra not in wanted:
            wanted.append(extra)
    node["parameters"]["url"] = f"{head}metrics={','.join(wanted)}{sep}{tail}"


def _oauth(analytics_node: dict) -> dict:
    creds = analytics_node.get("credentials")
    if not creds:
        raise RuntimeError("analytics node has no OAuth credential to reuse")
    return json.loads(json.dumps(creds))


def add_deep_metric_pass(workflow: dict) -> None:
    analytics = node_by_name(workflow, "YouTube: Video Analytics")
    creds = _oauth(analytics)
    names = {n.get("name") for n in workflow.get("nodes", [])}

    def http(name, node_id, url, position):
        return {
            "parameters": {
                "url": url,
                # Match the batch analytics node exactly: these call the same
                # API with the same generic OAuth2 credential.
                "authentication": "genericCredentialType",
                "genericAuthType": "oAuth2Api",
                "options": {"timeout": 30000, "response": {"response": {"neverError": True}}},
            },
            "id": node_id,
            "name": name,
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": position,
            "credentials": creds,
            # A missing retention report must never fail the measurement pass;
            # low-view Shorts legitimately have no curve yet.
            "onError": "continueRegularOutput",
            "retryOnFail": True,
            "maxTries": 2,
            "waitBetweenTries": 2000,
        }

    window = (
        "startDate={{ $('Build Analytics Window').item.json.startDate }}"
        "&endDate={{ $('Build Analytics Window').item.json.endDate }}"
    )
    if "Expand Videos For Deep Metrics" not in names:
        # "Get Shorts to Measure" emits ONE item holding a video_ids array, and
        # this branch hangs off Ingest Analytics, whose output is the ingest
        # response. Without expanding first the loop ran a single iteration with
        # an empty id and the API rejected the filter as "Invalid value ()".
        workflow["nodes"].append({
            "parameters": {"jsCode": (
                "// SHORTS_METRICS_V3: one item per video so the per-video reports can loop.\n"
                "const ids = $('Get Shorts to Measure').first().json.video_ids || [];\n"
                "return ids.filter(Boolean).map((video_id) => ({ json: { video_id: String(video_id) } }));"
            )},
            "id": "c9b4e2a1-77d3-4c58-9f36-2ab8e7d41199",
            "name": "Expand Videos For Deep Metrics",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [6980, 1200],
        })
    if "Split Videos For Deep Metrics" not in names:
        workflow["nodes"].append({
            "parameters": {"batchSize": 1, "options": {}},
            "id": "b1d7a4e2-3c55-4f61-9a02-7c9a1f0d5511",
            "name": "Split Videos For Deep Metrics",
            "type": "n8n-nodes-base.splitInBatches",
            "typeVersion": 3,
            "position": [7200, 1200],
        })
    if "YouTube: Retention Curve" not in names:
        workflow["nodes"].append(http(
            "YouTube: Retention Curve",
            "c2e8b5f3-4d66-4a72-8b13-8dab2f1e6622",
            "=" + ANALYTICS_ORIGIN + "?ids=channel%3D%3DMINE&" + window
            + "&metrics=audienceWatchRatio,relativeRetentionPerformance"
            + "&dimensions=elapsedVideoTimeRatio"
            + "&filters=video%3D%3D{{ $json.video_id }}%3BaudienceType%3D%3DORGANIC",
            [7420, 1320],
        ))
    if "YouTube: Traffic Sources" not in names:
        workflow["nodes"].append(http(
            "YouTube: Traffic Sources",
            "d3f9c6a4-5e77-4b83-9c24-9ebc3a2f7733",
            "=" + ANALYTICS_ORIGIN + "?ids=channel%3D%3DMINE&" + window
            + "&metrics=views,estimatedMinutesWatched"
            + "&dimensions=insightTrafficSourceType"
            + "&filters=video%3D%3D{{ $('Split Videos For Deep Metrics').item.json.video_id }}",
            [7640, 1320],
        ))
    if "Collect Deep Metrics" not in names:
        workflow["nodes"].append({
            "parameters": {"jsCode": (
                "// SHORTS_METRICS_V3: accumulate one video's retention + traffic reports.\n"
                "const staticData = $getWorkflowStaticData('global');\n"
                "const runId = String($execution.id || '').trim();\n"
                "if (!runId) throw new Error('Missing n8n execution id for workflow-scoped state');\n"
                "staticData.deepMetrics = staticData.deepMetrics || {};\n"
                "const bucket = staticData.deepMetrics[runId] || [];\n"
                "const videoId = $('Split Videos For Deep Metrics').item.json.video_id;\n"
                "let retention = null; let traffic = null;\n"
                "try { retention = $('YouTube: Retention Curve').item.json; } catch (e) { retention = null; }\n"
                "try { traffic = $json; } catch (e) { traffic = null; }\n"
                "const usable = (r) => r && Array.isArray(r.rows) && r.rows.length ? r : null;\n"
                "bucket.push({ video_id: videoId, retention: usable(retention), traffic: usable(traffic) });\n"
                "staticData.deepMetrics[runId] = bucket;\n"
                "return { json: { video_id: videoId, collected: bucket.length } };"
            )},
            "id": "e4a1d7b5-6f88-4c94-8d35-afcd4b3a8844",
            "name": "Collect Deep Metrics",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [7860, 1320],
        })
    if "Build Deep Metrics Payload" not in names:
        # $getWorkflowStaticData is a Code-node helper and is NOT available to
        # HTTP-node expressions, where it evaluates to undefined and the body
        # fails to parse. Build the payload in a Code node instead.
        workflow["nodes"].append({
            "parameters": {"jsCode": (
                "// SHORTS_METRICS_V3: hand the accumulated per-video reports to the ingest call.\n"
                "const staticData = $getWorkflowStaticData('global');\n"
                "const runId = String($execution.id || '').trim();\n"
                "if (!runId) throw new Error('Missing n8n execution id for workflow-scoped state');\n"
                "staticData.deepMetrics = staticData.deepMetrics || {};\n"
                "const videos = staticData.deepMetrics[runId] || [];\n"
                "// Release this run's bucket so repeated passes cannot grow it.\n"
                "delete staticData.deepMetrics[runId];\n"
                "return { json: { measured_at: new Date().toISOString(), videos } };"
            )},
            "id": "a6c3f9d7-8b10-4eb6-af57-c1ef6d5c0066",
            "name": "Build Deep Metrics Payload",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [7200, 1080],
        })
    if "Ingest Deep Metrics" not in names:
        workflow["nodes"].append({
            "parameters": {
                "method": "POST",
                "url": "http://shorts-compose:4000/performance/ingest-deep",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json) }}",
                "options": {"timeout": 30000},
            },
            "id": "f5b2e8c6-7a99-4da5-9e46-b0de5c4b9955",
            "name": "Ingest Deep Metrics",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [7420, 1080],
        })

    conns = workflow.setdefault("connections", {})
    conns["Ingest Analytics"] = {"main": [[{"node": "Expand Videos For Deep Metrics", "type": "main", "index": 0}]]}
    conns["Expand Videos For Deep Metrics"] = {"main": [[{"node": "Split Videos For Deep Metrics", "type": "main", "index": 0}]]}
    # splitInBatches v3: output 0 is "done", output 1 is the per-item loop.
    conns["Split Videos For Deep Metrics"] = {"main": [
        [{"node": "Build Deep Metrics Payload", "type": "main", "index": 0}],
        [{"node": "YouTube: Retention Curve", "type": "main", "index": 0}],
    ]}
    conns["Build Deep Metrics Payload"] = {"main": [[{"node": "Ingest Deep Metrics", "type": "main", "index": 0}]]}
    conns["YouTube: Retention Curve"] = {"main": [[{"node": "YouTube: Traffic Sources", "type": "main", "index": 0}]]}
    conns["YouTube: Traffic Sources"] = {"main": [[{"node": "Collect Deep Metrics", "type": "main", "index": 0}]]}
    conns["Collect Deep Metrics"] = {"main": [[{"node": "Split Videos For Deep Metrics", "type": "main", "index": 0}]]}


def upgrade(workflow: dict) -> dict:
    widen_batch_metrics(workflow)
    add_deep_metric_pass(workflow)
    workflow.setdefault("meta", {})["shorts_metrics_version"] = MARKER
    return workflow


def assert_applied(workflow: dict) -> None:
    url = str(node_by_name(workflow, "YouTube: Video Analytics").get("parameters", {}).get("url", ""))
    for metric in ("engagedViews", "estimatedMinutesWatched", "subscribersLost", "dislikes"):
        if metric not in url:
            raise RuntimeError(f"batch analytics query lost metric: {metric}")
    retention = str(node_by_name(workflow, "YouTube: Retention Curve").get("parameters", {}).get("url", ""))
    for token in ("audienceWatchRatio", "relativeRetentionPerformance", "elapsedVideoTimeRatio"):
        if token not in retention:
            raise RuntimeError(f"retention query lost: {token}")
    traffic = str(node_by_name(workflow, "YouTube: Traffic Sources").get("parameters", {}).get("url", ""))
    if "insightTrafficSourceType" not in traffic:
        raise RuntimeError("traffic-source query lost its dimension")
    conns = workflow.get("connections", {})
    loop = conns.get("Split Videos For Deep Metrics", {}).get("main", [])
    if len(loop) < 2 or loop[1][0]["node"] != "YouTube: Retention Curve":
        raise RuntimeError("deep-metric loop branch is not wired to the retention report")
    if conns.get("Collect Deep Metrics", {}).get("main", [[]])[0][0]["node"] != "Split Videos For Deep Metrics":
        raise RuntimeError("deep-metric loop does not return to the batch splitter")
    if conns.get("Ingest Analytics", {}).get("main", [[]])[0][0]["node"] != "Expand Videos For Deep Metrics":
        raise RuntimeError("deep-metric pass must expand video_ids into per-video items before looping")
    expander = str(node_by_name(workflow, "Expand Videos For Deep Metrics").get("parameters", {}).get("jsCode", ""))
    if "video_ids" not in expander:
        raise RuntimeError("deep-metric expander does not read the measured video_ids")
    ingest_body = str(node_by_name(workflow, "Ingest Deep Metrics").get("parameters", {}).get("jsonBody", ""))
    if "getWorkflowStaticData" in ingest_body:
        raise RuntimeError("deep-metric ingest body uses a Code-node helper that HTTP expressions cannot resolve")
    if loop[0][0]["node"] != "Build Deep Metrics Payload":
        raise RuntimeError("deep-metric done branch must build its payload in a Code node first")


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: upgrade-shorts-metrics-v3.py INPUT [OUTPUT]")
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src
    workflow = upgrade(json.loads(src.read_text()))
    assert_applied(workflow)
    dst.write_text(json.dumps(workflow, indent=2) + "\n")
    print(f"{MARKER} workflow written to {dst}")


if __name__ == "__main__":
    main()
