const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const fsp = fs.promises;
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { hasSystemFfmpeg } = require("./helpers/environment");

// This case synthesizes a real MP4 to rebuild a trim from, so it needs system FFmpeg.
const FFMPEG_SKIP = hasSystemFfmpeg() ? false : "requires system FFmpeg on PATH (installed in CI)";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "broll-library-test-"));
const libraryPath = path.join(tmp, "broll_library.json");
const outputDir = path.join(tmp, "outputs");
process.env.BROLL_LIBRARY_PATH = libraryPath;
process.env.OUTPUT_DIR = outputDir;

const library = require("../clipLibrary");

before(async () => {
  await fsp.mkdir(path.join(outputDir, "broll-cache"), { recursive: true });
});

after(async () => {
  await fsp.rm(tmp, { recursive: true, force: true });
});

describe("persistent verified clip library", () => {
  it("reapplies current entity/action/relationship hard gates to old matches", () => {
    const contract = {
      required_entities: ["octopus"],
      required_actions: ["squeezing"],
      required_relationships: ["octopus passes through opening"],
    };
    assert.equal(library.meetsCurrentContractGate({
      semantic_match: 97, entity_match: 96, action_match: 42, relationship_match: 95,
    }, contract), false);
    assert.equal(library.meetsCurrentContractGate({
      semantic_match: 97, entity_match: 96, action_match: 94, relationship_match: 92,
    }, contract), true);
  });

  it("never reuses annotated_real without its pixel-grounded callout plan", () => {
    const contract = {
      visual_proof_mode: "annotated_real",
      required_entities: ["beak"],
      required_actions: [],
      required_relationships: [],
    };
    const scored = { semantic_match: 96, entity_match: 95, action_match: 90, relationship_match: 90 };
    assert.equal(library.meetsCurrentContractGate(scored, contract), false);
    assert.equal(library.meetsCurrentContractGate({
      ...scored,
      annotation_plan: [{ label: "beak", x_pct: 50, y_pct: 50, w_pct: 12, h_pct: 8 }],
    }, contract), true);
  });

  it("persists verified frame indices and annotation metadata", async () => {
    await library.save([]);
    await library.recordAccepted({
      url: "http://127.0.0.1:4000/outputs/broll-cache/annotated_test.jpg",
      original_url: "https://example.invalid/original.jpg",
      local_path: path.join(outputDir, "broll-cache", "annotated_test.jpg"),
      source: "wikimedia",
      type: "image",
      width: 1200,
      height: 800,
      annotation_plan: [{ label: "target", x_pct: 40, y_pct: 55, w_pct: 20, h_pct: 16 }],
      verified_frame_indices: [2, 3],
      semantic_match: 96,
      entity_match: 95,
      action_match: 90,
      relationship_match: 90,
      score: 92,
    }, {
      visual_claim: "show the target",
      visual_proof_mode: "annotated_real",
      required_entities: ["target"],
      required_actions: [],
      required_relationships: [],
    }, "test-run");
    const rows = await library.load();
    assert.equal(rows.length, 1);
    assert.deepEqual(rows[0].annotation_plan, [{ label: "target", x_pct: 40, y_pct: 55, w_pct: 20, h_pct: 16 }]);
    assert.deepEqual(rows[0].verified_frame_indices, [2, 3]);
    assert.equal(rows[0].width, 1200);
    assert.equal(rows[0].height, 800);
  });

  it("rebuilds an expired verified trim from original source and stored in/out points", { skip: FFMPEG_SKIP }, async () => {
    const source = path.join(tmp, "source.mp4");
    execFileSync("ffmpeg", [
      "-hide_banner", "-loglevel", "error", "-y",
      "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24:d=2",
      "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
    ]);

    const cachedUrl = "http://127.0.0.1:4000/outputs/broll-cache/verified_expired.mp4";
    await library.save([{
      url: cachedUrl,
      original_url: source,
      local_path: path.join(outputDir, "broll-cache", "verified_expired.mp4"),
      source: "pexels_video",
      type: "video",
      in_point_sec: 0.25,
      out_point_sec: 1.25,
      semantic_match: 96,
      entity_match: 96,
      action_match: 94,
      relationship_match: 92,
      score: 93,
      contract_text: "octopus squeezing through narrow opening octopus squeezing opening",
      last_used_at_ms: 1,
    }]);

    const contract = {
      visual_claim: "octopus squeezing through narrow opening",
      required_entities: ["octopus"],
      required_actions: ["squeezing"],
      required_relationships: ["octopus passes through opening"],
    };
    const found = await library.findReusable(contract, new Set());
    assert.equal(found.length, 1);
    assert.equal(found[0].cache_rebuilt, true);
    assert.ok(fs.existsSync(found[0].local_path));
    assert.ok(fs.statSync(found[0].local_path).size > 1024);
    assert.equal(found[0].url, cachedUrl);
    assert.equal(found[0].original_url, source);
    assert.equal(found[0].in_point_sec, 0.25);
    assert.equal(found[0].out_point_sec, 1.25);
  });
});
