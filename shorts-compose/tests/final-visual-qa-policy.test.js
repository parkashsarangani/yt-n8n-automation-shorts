const { builtArtifactSkipReason } = require("./helpers/environment");
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { classifyRenderedIssue, normalizeJudgement } = require("../finalVisualQa");
const { buildVisualContract } = require("../visualContract");

const contract = buildVisualContract({
  visual_claim: "A real dashboard shows the fuel gauge",
  required_entities: ["dashboard", "fuel gauge"],
  required_actions: [],
  required_relationships: [],
  visual_proof_mode: "literal_image",
});

function good(overrides = {}) {
  return normalizeJudgement({
    semantic_match: 95,
    entity_match: 95,
    action_match: 95,
    relationship_match: 95,
    readability: 95,
    editorial_cleanliness: 95,
    safe_area: 95,
    caption_integrity: 95,
    overall: 95,
    debug_artifact: false,
    critical_content_clipped: false,
    problem: "",
    ...overrides,
  });
}

test("debug/CV overlays are recorded as severe QA telemetry", () => {
  const result = classifyRenderedIssue(2, good({ debug_artifact: true }), contract);
  assert.ok(result.hard.some((x) => /debug\/diagnostic/i.test(x.problem)));
});

test("severe caption collision is recorded as severe QA telemetry", () => {
  const result = classifyRenderedIssue(2, good({ caption_integrity: 30 }), contract);
  assert.ok(result.hard.some((x) => /captions/i.test(x.problem)));
});

test("mild editorial clutter remains soft telemetry", () => {
  const result = classifyRenderedIssue(2, good({ editorial_cleanliness: 60 }), contract);
  assert.equal(result.hard.length, 0);
  assert.ok(result.soft.some((x) => /cleanliness/i.test(x.problem)));
});

test("wrong required entity remains visible in severe semantic telemetry", () => {
  const result = classifyRenderedIssue(2, good({ entity_match: 45, overall: 82 }), contract);
  assert.ok(result.hard.some((x) => /scene contract/i.test(x.problem)));
});

// The non-blocking QA policy is injected by scripts/visual_matching_v4_compose.py,
// so it only exists in the built artifact CI tests against.
test("final visual QA can never block an otherwise successful compose", {
  skip: builtArtifactSkipReason("compose.js", "VISUAL_MATCHING_V4_COMPOSE"),
}, () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "compose.js"), "utf8");
  assert.match(source, /NON_BLOCKING_FINAL_QA/);
  assert.match(source, /publishing anyway/);
  assert.doesNotMatch(source, /Final visual QA rejected catastrophic render defects/);
  assert.doesNotMatch(source, /if \(finalVisualQa\.hard_failed\)[\s\S]{0,500}throw new Error/);
});