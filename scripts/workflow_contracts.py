#!/usr/bin/env python3
"""Shared production contracts for the Shorts workflow.

Keep policy values and runtime invariants here so transform assertions do not
silently drift apart.  The final workflow assertion consumes this table after
all deploy-time transforms have run.
"""
from __future__ import annotations

QUALITY_MINIMUMS = {
    "concept_strength": 76,
    "hook_strength": 78,
    "evidence_strength": 74,
    "payoff_strength": 76,
    "information_density": 74,
    "first_frame_strength": 78,
    "visual_progression": 74,
    "shareability": 76,
    "naturalness": 74,
    "distinctiveness": 74,
    "voice_specificity": 72,
    "overall": 77,
}
VISUAL_PLAN_MINIMUM = 78
OVERALL_WEAKEST_CAP = 5

# Media targets remain reliability-aware.  The 60-71 degraded band is allowed
# to preserve completion, but final workflow telemetry must expose it.
BROLL_SUPPORT_TARGET = 72
BROLL_FIRST_FRAME_TARGET = 78
BROLL_TEMPLATE_FALLBACK_THRESHOLD = 60

EXPECTED_NODE_CONTRACTS = {
    # 150s (not the gateway's 120s LLM_ROUTER_TIMEOUT_MS) so n8n's own
    # client-side abort always has margin over the gateway's own worst-case
    # free+direct-fallback budget - see TIMEOUT_MS in upgrade-topic-latency.py.
    "Claude: Generate Topic": {
        "timeout": 150000,
        "single_attempt": True,
        "body_markers": ("TOPIC_LATENCY", "max_completion_tokens: 6000"),
    },
    "Claude: Commission Topic Shortlist": {
        "timeout": 150000,
        "single_attempt": True,
        "body_markers": ("RELIABILITY_FIRST_VIDEO", "max_completion_tokens: 8192"),
    },
    "Claude: Draft Script (Stage 1)": {
        "timeout": 150000,
        "single_attempt": True,
        "body_markers": ("QUALITY_ALIGNMENT WRITER",),
    },
    "Claude: Visual Director": {
        "timeout": 180000,
        "single_attempt": True,
        "body_markers": (
            "QUALITY_ALIGNMENT",
            "REPAIRABLE_NEAR_MISS",
            "HARD_REJECT",
            "VISUAL_SOURCE_ROUTER_V2",
        ),
    },
    "Claude: Repair Script": {
        "timeout": 180000,
        "single_attempt": True,
        "body_markers": (
            "REPAIR PASS",
            "FAILED CHECKS",
            "_failedScript",
            "_validationErrors",
        ),
    },
    "ElevenLabs: TTS+Timestamps": {
        "timeout": 60000,
        "single_attempt": False,
        "body_markers": (),
    },
    "Resolve B-roll": {
        "timeout": 180000,
        "single_attempt": False,
        "body_markers": ("run_id", "must_show:", "template_fallback:"),
    },
}

SINGLE_ATTEMPT_MODEL_NODES = tuple(
    name for name, contract in EXPECTED_NODE_CONTRACTS.items()
    if contract.get("single_attempt")
)

REQUIRED_WORKFLOW_MARKERS = ("QUALITY_GATE", "CREATIVE_SYSTEM")
