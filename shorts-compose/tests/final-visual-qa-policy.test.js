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
test("only a failed first frame or clearly contradicting footage is critical", () => {
  const critical = (sceneIndex, overrides) => classifyRenderedIssue(sceneIndex, good(overrides), contract).hard.filter((x) => x.critical);
  assert.equal(critical(0, { entity_match: 45, overall: 82 }).length, 1, "first frame failing its contract is critical");
  assert.equal(critical(2, { semantic_match: 60, overall: 60 }).length, 0, "a weak later scene is hard telemetry, not critical");
  assert.equal(critical(2, { semantic_match: 40, overall: 40 }).length, 1, "later footage contradicting the narration is critical");
  assert.equal(critical(0, { readability: 40 }).length, 0, "layout problems are never re-picked");
});

// Run the built FINAL_QA_REPICK_V1 block with stubbed dependencies.
function repickBlock() {
  const source = fs.readFileSync(path.join(__dirname, "..", "compose.js"), "utf8");
  const start = source.indexOf("    // FINAL_QA_REPICK_V1:");
  // The block ends where the technical QA (or, without it, the final copy) begins.
  const tqa = source.indexOf("    // TECHNICAL_QA_V1:", start);
  const end = tqa >= 0 ? tqa : source.indexOf("    await fsp.copyFile(finalPath, outputFullPath);", start);
  assert.ok(start >= 0 && end > start, "re-pick block sits before the final copy");
  return new Function("finalVisualQa", "reqBody", "scenes", "resolveBroll", "runComposeJob", "newTmpDir", "jobId",
    "return (async () => {" + source.slice(start, end) + "\nreturn null; })();");
}

test("a critical scene gets new footage and exactly one re-render", {
  skip: builtArtifactSkipReason("compose.js", "FINAL_QA_REPICK_V1"),
}, async () => {
  const run = repickBlock();
  const scenes = [{ scene_index: 0, video_url: "https://x/old.mp4", asset_original_url: "https://src/old", selected_query: "two people talking" }, { scene_index: 1, images: ["https://x/ok.jpg"] }];
  const qa = { hard_issues: [{ scene_index: 0, critical: true }, { scene_index: 1, critical: false }] };
  const calls = [];
  const resolve = async (input) => { calls.push(input); return { ok: true, type: "video", url: "https://x/new.mp4", original_url: "https://src/new" }; };
  let rerender = null;
  const result = await run(qa, { data: scenes }, scenes, resolve, async (body) => { rerender = body; return { success: true }; }, () => "tmp", "job1");
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].exclude_urls, ["https://x/old.mp4", "https://src/old"]);
  assert.equal(scenes[0].video_url, "https://x/new.mp4");
  assert.equal(rerender._qaRepickAttempted, true);
  assert.equal(result.qa_repick.scenes[0].replacement, "https://src/new");
  // The re-render itself never re-picks again.
  assert.equal(await run(qa, { _qaRepickAttempted: true }, scenes, resolve, async () => assert.fail("looped"), () => "tmp", "job1"), null);
});

test("no re-render when the resolver has no different real footage", {
  skip: builtArtifactSkipReason("compose.js", "FINAL_QA_REPICK_V1"),
}, async () => {
  const run = repickBlock();
  const scenes = [{ scene_index: 0, video_url: "https://x/old.mp4" }];
  const qa = { hard_issues: [{ scene_index: 0, critical: true }] };
  const never = async () => assert.fail("must not re-render");
  for (const answer of [{ ok: false, reason: "no_candidates" }, { ok: true, type: "video", url: "https://x/old.mp4" }, { ok: true, type: "template", template_name: "kinetic_text" }]) {
    assert.equal(await run(qa, {}, scenes, async () => answer, never, () => "tmp", "job1"), null);
  }
  assert.equal(await run(qa, {}, scenes, async () => { throw new Error("pexels down"); }, never, () => "tmp", "job1"), null);
});
