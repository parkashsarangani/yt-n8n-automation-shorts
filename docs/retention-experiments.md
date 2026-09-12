# Retention experiment rollout

The September 4–11 export motivates testing clearer openings and earlier delivery of the promised fact. It does not establish a fixed YouTube 1,000-view gate or identify why recommendations stopped. There is no guarantee this change increases reach.

## Evidence and corrections to the supplied handoff

The total row contains 8,397 views, 2,279 engaged views, 24.11% stayed to watch and 44.75% average percentage viewed (APV). Four subscribers were gained and four lost: net zero. Eight high-view videos have 920–1,057 views, but similar counts do not establish a distribution mechanism. Windshield dots has 60.86% APV; Mickey/Minnie has 70.73%. These are useful hypotheses about concrete subjects and stories, not causal evidence of a winning niche.

The table contains 108 videos, only 42 with populated Views, and mixes older uploads and non-Shorts. Missing or tiny counts do not identify publication maturity. Chart data covers only five selected videos over seven dates; it cannot establish a channel-wide second-push lifecycle. The export contains no complete retention curves or rendered-video inspection proving an exact dropout point or visual defect. APV above 100% remains valid input; tiny samples receive a warning.

## Changes

The final build layer `scripts/retention_workflow.py` runs after growth and metrics transforms. Topic selection prefers a specific visible subject with available evidence. Writer, visual director and repair prompts ask for immediate subject/tension, truthful opening proof, minimal setup, a payoff scene starting by roughly 70% of content time and a fact-specific participation question. Optional planning fields never create a new publication rejection. The existing length, editorial, medical and topic policies remain authoritative.

`Plan Retention Experiment` assigns one execution to `direct_contradiction` or `concrete_question`. Repairs keep the assignment. Both arms receive the same improved creative guidance; this compares hook framing, not the entire new policy against the old policy. Voice, duration target, renderer style and the separate outro experiment remain in place. Deterministic assignment is reproducible, not a guarantee of perfect balance or freedom from topic/time confounding.

Accepted-script diagnostics and experiment assignment travel through the accepted snapshot into publication creative DNA. Missing optional fields produce advisory warnings. The renderer emphasizes the declared payoff scene and records measured scene start/end times. These are scene boundaries, not verified timestamps of the spoken payoff or visual proof. A punctuation proxy checks hook assignment adherence; it cannot verify that a declarative sentence is actually a contradiction.

## Measurement integrity

- Missing counts and invalid denominators produce null rates. Gained, lost and net subscribers remain distinct.
- Engaged views / public views is explicitly a proxy, not Studio stayed-to-watch. The existing Analytics query has no documented stayed-to-watch field, so the pipeline stores null rather than inventing it. To assess that metric, obtain a matching fixed-age Studio export separately.
- APV is retained in percentage units, including rewatches above 100%. Thumbnail CTR is normalized from percentage units to a fraction.
- Snapshots retain measurement time, observed age, target maturity and cumulative metric-period labels. Deep reports can fill missing retention/traffic only within 30 minutes of the snapshot measurement and its allowed age window; later reports remain in latest_deep_metrics without overwriting cohorts. This cannot reconstruct already contaminated historical snapshots.
- Old cached strategist guidance is withheld until regenerated under the corrected measurement policy. No fixed distribution threshold is represented as fact.

## Review procedure

Read `hook_experiment` from the existing channel-insights response. Only t72h snapshots participate. Compare within matching policy, outro arm and caption style. Each stratum needs at least ten videos in each hook arm before it is marked ready_for_review; twenty total uploads may not satisfy that requirement. Low-view videos remain in distribution outcomes to avoid selecting only successful uploads. APV summaries require at least 100 engaged views. Satisfaction diagnostics require at least 500 public views. These are internal analysis rules, not YouTube thresholds or statistical significance guarantees.

Review median views, engaged-view proxy, APV, assignment mismatches, available shares/comments and actual videos before choosing a follow-up. No arm wins automatically. Broader historical comparisons are confounded by the new shared creative policy. Do not change voice, duration and hook framing simultaneously based on one successful upload.

## Build and validation

Build with `python scripts/build_production_artifacts.py --output-dir /tmp/prod` and run `python scripts/preprod-audit.py`. Copy built compose.js and brollResolver.js into shorts-compose before `npm test`, matching CI. The retention transform accepts an already-built compositor so workflow-contract tests can rebuild in that CI layout. Deploy workflow and compositor together using the existing production process; building or passing tests does not prove a live upload succeeded.

Sources for metric definitions:
- https://support.google.com/youtube/answer/12220281
- https://developers.google.com/youtube/analytics/metrics
