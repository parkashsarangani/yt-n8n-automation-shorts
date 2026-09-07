#!/usr/bin/env python3
"""Apply Shorts growth/analytics V2 runtime policy to generated compose.js."""
from __future__ import annotations
import sys
from pathlib import Path

MARKER = "SHORTS_GROWTH_V2_COMPOSE"
POLICY_VERSION = "shorts-growth-v2"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"{label} anchor missing")
    return text.replace(old, new, 1)


def upgrade(text: str) -> str:
    if MARKER in text:
        return text

    text = replace_once(
        text,
        'const competitors = require("./competitorMining");',
        'const competitors = require("./competitorMining");\nconst topicPolicy = require("./topicPolicy");',
        "topic policy import",
    )
    text = replace_once(
        text,
        "const TOPIC_HISTORY_MAX = 90;",
        "const TOPIC_HISTORY_MAX = Math.max(500, Number(process.env.TOPIC_HISTORY_MAX || 500));",
        "500 topic history cap",
    )
    text = replace_once(
        text,
        "    const { topic, hook } = req.body;",
        "    const { topic, hook, canonical_key, subject_key, mechanism_key, payoff_key, strategy_arm, policy_version } = req.body || {};",
        "topic history metadata",
    )
    text = replace_once(
        text,
        "    topics.push({ topic, hook: hook || null, created_at: new Date().toISOString() });",
        "    topics.push({ topic, hook: hook || null, canonical_key: canonical_key || null, subject_key: subject_key || null, mechanism_key: mechanism_key || null, payoff_key: payoff_key || null, strategy_arm: strategy_arm || null, policy_version: policy_version || 'shorts-growth-v2', created_at: new Date().toISOString() });",
        "topic history rich entry",
    )

    feedback_anchor = "// ---------------------------------------------------------------------------\n// Feedback loop: log published videos, ingest analytics, serve learned insights\n// ---------------------------------------------------------------------------"
    dedup_endpoint = '''// SHORTS_GROWTH_V2_COMPOSE: strict semantic/canonical no-repeat gate before commissioning.
app.post("/topic-dedup", async (req, res) => {
  try {
    const result = await topicPolicy.shortlistCandidates({
      candidates: req.body?.candidates,
      desired_strategy_arm: req.body?.desired_strategy_arm,
      seed: req.body?.seed,
    });
    res.json({ success: true, policy_version: "shorts-growth-v2", ...result });
  } catch (err) {
    const status = err?.code === "TOPIC_DEDUP_EXHAUSTED" ? 409 : 500;
    res.status(status).json({ success: false, error: err?.message || String(err), code: err?.code || "topic_dedup_error", details: err?.details || [] });
  }
});

'''
    if dedup_endpoint not in text:
        if feedback_anchor not in text:
            raise ValueError("feedback endpoint anchor missing")
        text = text.replace(feedback_anchor, dedup_endpoint + feedback_anchor, 1)

    text = replace_once(
        text,
        "  try { res.json({ video_ids: await feedback.getMeasureIds() }); }",
        "  try { res.json(await feedback.getMeasurementPlan()); }",
        "measurement plan endpoint",
    )

    # A/B test must be real: no-outro renders truly end on the payoff instead of
    # the compositor silently adding its legacy 2.5s card back in.
    text = replace_once(
        text,
        "    const hasScriptOutro = scenes.some((s) => s?.template_data?.is_outro);\n    if (!hasScriptOutro) {",
        "    const hasScriptOutro = scenes.some((s) => s?.template_data?.is_outro);\n    const allowFallbackOutro = reqBody.outro_experiment_arm !== 'no_outro';\n    if (!hasScriptOutro && allowFallbackOutro) {",
        "outro experiment fallback",
    )
    text = replace_once(
        text,
        "    const emphasisIdx = scenes.length - 2;",
        "    let emphasisIdx = scenes.length - 1;\n    while (emphasisIdx > 0 && scenes[emphasisIdx]?.template_data?.is_outro) emphasisIdx--;",
        "payoff emphasis without outro",
    )
    text = replace_once(
        text,
        "    const outroDuration = durations.length > 1 ? durations[durations.length - 1] : 0;\n    const contentDuration = totalVideoDuration - outroDuration;",
        "    const lastSceneIsOutro = Boolean(scenes[scenes.length - 1]?.template_data?.is_outro);\n    const outroDuration = lastSceneIsOutro && durations.length ? durations[durations.length - 1] : 0;\n    const contentDuration = totalVideoDuration - outroDuration;",
        "content duration without forced outro",
    )

    final_return = "    return { success: true, output_path: outputFullPath, job_id: jobId, final_visual_qa: finalVisualQa };"
    final_return_v2 = "    return { success: true, output_path: outputFullPath, job_id: jobId, final_visual_qa: finalVisualQa, duration_sec: Number(totalVideoDuration.toFixed(3)), content_duration_sec: Number(contentDuration.toFixed(3)), outro_experiment_arm: reqBody.outro_experiment_arm || (hasScriptOutro ? 'current_outro' : 'legacy_fallback'), policy_version: reqBody.policy_version || 'shorts-growth-v2' };"
    text = replace_once(text, final_return, final_return_v2, "compose result growth telemetry")

    text += f"\n// {MARKER} policy={POLICY_VERSION}\n"
    return text


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: upgrade-compose-growth-v2.py INPUT [OUTPUT]")
    src = Path(sys.argv[1]); dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src
    out = upgrade(src.read_text())
    required = [
        MARKER,
        "TOPIC_HISTORY_MAX = Math.max(500",
        'app.post("/topic-dedup"',
        "getMeasurementPlan",
        "allowFallbackOutro",
        "lastSceneIsOutro",
        "duration_sec: Number(totalVideoDuration.toFixed(3))",
    ]
    for marker in required:
        if marker not in out:
            raise RuntimeError(f"growth compose invariant missing: {marker}")
    if "const TOPIC_HISTORY_MAX = 90;" in out:
        raise RuntimeError("90-topic history cap survived production transform")
    dst.write_text(out)
    print(f"{MARKER} compose written to {dst}")


if __name__ == "__main__":
    main()
