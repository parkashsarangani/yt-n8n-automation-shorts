const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const fx = require("../editingEffects");

const scenes = [{ scene_index: 0 }, { scene_index: 1 }, { scene_index: 2 }, { scene_index: 3 }, { scene_index: 4, template_data: { is_outro: true } }];
const offsets = [0, 2.5, 5, 7.5, 10];
const durations = [2.5, 2.5, 2.5, 2.5, 2];
const snapshot = { title: "3 Signs Someone Secretly Respects You", promised_points: [1, 2, 3] };

test("headline wraps into balanced lines, never an orphaned word", () => {
  assert.equal(fx.wrapHeadline("3 Signs Someone Secretly Respects You"), "3 Signs Someone\\NSecretly Respects You");
  assert.equal(fx.wrapHeadline("If They Do This"), "If They Do This");
  assert.equal(fx.wrapHeadline("strip {\\override} tags"), "strip override tags");
});

test("item badges follow promised_points order and skip outro or unknown scenes", () => {
  assert.deepEqual(fx.itemPositions(scenes, [3, 1, 2]), [3, 1, 2]);
  assert.deepEqual(fx.itemPositions(scenes, [1, 4, 9]), [1]);
  assert.deepEqual(fx.itemPositions(scenes, null), []);
});

test("zoom-settle plays at every content cut but not the first frame or outro", () => {
  assert.deepEqual(fx.cutTimes(scenes, offsets), [2.5, 5, 7.5]);
  const filter = fx.cutZoomFilter(scenes, offsets);
  assert.match(filter, /^zoompan=z='1\+/);
  assert.equal((filter.match(/between\(/g) || []).length, 3);
  assert.equal(fx.cutZoomFilter([{ scene_index: 0 }], [0]), null);
});

test("overlays: headline, one badge per item, flash on item cuts, progress bar", () => {
  const { styles, events } = fx.overlayEvents({ scenes, offsets, durations, scriptSnapshot: snapshot, contentDuration: 10 });
  assert.equal(styles.length, 3);
  assert.match(events, /Headline,,0,0,0,,.*3 Signs Someone\\NSecretly Respects You/);
  for (const n of ["1/3", "2/3", "3/3"]) assert.ok(events.includes(`}${n}\n`), n);
  assert.equal((events.match(/\\alpha&H90&/g) || []).length, 3, "one flash per item cut");
  assert.match(events, /\\clip\(0,0,0,10\)\\t\(0,10000,\\clip\(0,0,1080,10\)\)/);
  assert.deepEqual(fx.itemWhooshTimes(scenes, offsets, snapshot.promised_points, 3), [2.38, 4.88]);
});

test("overlays merge into the caption file inside the styles section", () => {
  const ass = "[V4+ Styles]\nStyle: Caption,x\n\n[Events]\nFormat: x\nDialogue: 0,a\n";
  const merged = fx.withEditingOverlays(ass, { scenes, offsets, durations, scriptSnapshot: snapshot, contentDuration: 10 });
  assert.ok(merged.indexOf("Style: Headline") < merged.indexOf("[Events]"));
  assert.ok(merged.endsWith("\n") && merged.includes("Dialogue: 0,a\n"));
});

test("EDITING_EFFECTS=false turns the whole layer off", () => {
  const prior = process.env.EDITING_EFFECTS;
  process.env.EDITING_EFFECTS = "false";
  delete require.cache[require.resolve("../editingEffects")];
  try {
    const off = require("../editingEffects");
    assert.equal(off.cutZoomFilter(scenes, offsets), null);
    assert.equal(off.withEditingOverlays("x\n[Events]\n", { scenes, offsets, durations, scriptSnapshot: snapshot, contentDuration: 10 }), "x\n[Events]\n");
    assert.deepEqual(off.itemWhooshTimes(scenes, offsets, [1, 2], 9), []);
  } finally {
    if (prior === undefined) delete process.env.EDITING_EFFECTS; else process.env.EDITING_EFFECTS = prior;
    delete require.cache[require.resolve("../editingEffects")];
  }
});

// Real render through the bundled ffmpeg: the zoom filter and the ASS
// overlays must parse, the first frame must be picture (not black), and the
// frame right after a cut must be zoomed relative to the settled frame.
test("renders with ffmpeg: no black first frame, zoom at the cut", () => {
  const ff = require("ffmpeg-static");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "editing-fx-"));
  try {
    const sc = [{ scene_index: 0 }, { scene_index: 1 }];
    const off = [0, 1];
    fs.writeFileSync(path.join(dir, "t.ass"), fx.withEditingOverlays(
      "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
      { scenes: sc, offsets: off, durations: [1, 1], scriptSnapshot: { title: "Test", promised_points: [1] }, contentDuration: 2 }));
    const src = "testsrc2=s=1080x1920:r=30:d=1";
    execFileSync(ff, ["-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", src, "-filter_complex",
      `[0:v][1:v]concat=n=2:v=1:a=0[c];[c]${fx.cutZoomFilter(sc, off)}[z];[z]ass=t.ass[v]`,
      "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "t.mp4"], { cwd: dir, stdio: "pipe" });
    const meanLuma = (t) => {
      const raw = execFileSync(ff, ["-ss", String(t), "-i", "t.mp4", "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], { cwd: dir });
      return raw[0];
    };
    assert.ok(meanLuma(0) > 40, "first frame is picture, not black");
    const frame = (t) => execFileSync(ff, ["-ss", String(t), "-i", "t.mp4", "-frames:v", "1", "-vf", "crop=200:200:0:0,scale=8:8", "-f", "rawvideo", "-pix_fmt", "gray", "-"], { cwd: dir });
    const diff = Buffer.compare(frame(1.02), frame(1.6)) !== 0;
    assert.ok(diff, "the corner differs between just-after-cut and settled frames (zoom-settle ran)");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
