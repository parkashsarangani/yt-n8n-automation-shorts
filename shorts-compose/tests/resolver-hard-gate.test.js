const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

process.env.BROLL_SEMANTIC_THRESHOLD = "82";
process.env.BROLL_ENTITY_THRESHOLD = "85";
process.env.BROLL_ACTION_THRESHOLD = "80";
process.env.BROLL_RELATIONSHIP_THRESHOLD = "80";

const { builtArtifactSkipReason } = require("./helpers/environment");

const { candidatePassesGate, normalizeTemplateFallback } = require("../brollResolver");
const { buildVisualContract } = require("../visualContract");

test("quality gate remains useful for ranking and retry telemetry", () => {
  const contract = buildVisualContract({
    visual_claim: "A driver hand points at the fuel gauge",
    required_entities: ["driver hand", "fuel gauge"],
    required_actions: ["points"],
    required_relationships: [],
    visual_proof_mode: "literal_video",
  });

  assert.equal(candidatePassesGate({ score: 92, semantic_match: 92, entity_match: 60, action_match: 92, relationship_match: 92 }, contract, 72), false);
  assert.equal(candidatePassesGate({ score: 92, semantic_match: 92, entity_match: 92, action_match: 72, relationship_match: 92 }, contract, 72), false);
  assert.equal(candidatePassesGate({ score: 92, semantic_match: 92, entity_match: 92, action_match: 92, relationship_match: 92 }, contract, 72), true);
  assert.equal(candidatePassesGate({ score: 60, semantic_match: 95, entity_match: 95, action_match: 95, relationship_match: 95 }, contract, 72), false);
});

test("deterministic template fallback accepts only known renderers with data", () => {
  assert.deepEqual(normalizeTemplateFallback({ template_name: "stat_reveal", template_data: { statValue: "3x", label: "MORE" } }), {
    template_name: "stat_reveal",
    template_data: { statValue: "3x", label: "MORE" },
  });
  assert.equal(normalizeTemplateFallback({ template_name: "annotated_real", template_data: {} }), null);
  assert.equal(normalizeTemplateFallback({ template_name: "comparison" }), null);
  assert.equal(normalizeTemplateFallback(null), null);
});

// RESOLVER_V5_RUNTIME is injected by scripts/resolver_v5_runtime.py, so this
// invariant only exists in the built artifact CI tests against.
test("production resolver always selects best usable asset when quality target is missed", {
  skip: builtArtifactSkipReason("brollResolver.js", "RESOLVER_V5_RUNTIME"),
}, () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "brollResolver.js"), "utf8");
  assert.match(source, /V5_ALWAYS_PUBLISH_BEST_AVAILABLE/);
  assert.match(source, /best_available_below_quality_target/);
  assert.match(source, /no_technically_usable_candidate/);
  assert.match(source, /quality_gate_passed/);
  assert.match(source, /selection_reason/);
  assert.doesNotMatch(source, /V5_PROOF_MEDIA_TYPE_FILTER/);
  assert.doesNotMatch(source, /reason: state\.budget_exhausted \|\| "below_semantic_quality_gate"/);
});

test("resolver no longer contains audience callout/annotation generation", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "brollResolver.js"), "utf8");
  assert.doesNotMatch(source, /normalizeAnnotationPlan/);
  assert.doesNotMatch(source, /annotations as an array/i);
  assert.doesNotMatch(source, /annotation_plan/);
  assert.match(source, /deterministic_template_fallback/);
});