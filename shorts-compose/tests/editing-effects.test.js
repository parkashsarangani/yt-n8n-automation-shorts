const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const fx = require("../editingEffects");
const tqa = require("../technicalQa");

// 0 hook, 1-3 list items (3 is also the payoff), 4 body, 5 body, 6 outro.
const scenes = [0, 1, 2, 3, 4, 5].map((i) => ({ scene_index: i })).concat([{ scene_index: 6, template_data: { is_outro: true } }]);
const offsets = [0, 2.5, 5, 7.5, 10, 12.5, 15];
const durations = [2.5, 2.5, 2.5, 2.5, 2.5, 2.5, 2];
const snapshot = { title: "3 Signs Someone Secretly Respects You", promised_points: [1, 2, 3], payoff: { claim: "x", resolved_in_scene: 3 } };

test("headline wraps into balanced lines, never an orphaned word", () => {
  assert.equal(fx.wrapHeadline("3 Signs Someone Secretly Respects You"), "3 Signs Someone\\NSecretly Respects You");
  assert.equal(fx.wrapHeadline("If They Do This"), "If They Do This");
  assert.equal(fx.wrapHeadline("strip {\\override} tags"), "strip override tags");
});

test("every scene gets a role from data the script already carries", () => {
  assert.deepEqual(fx.sceneRoles(scenes, snapshot), ["hook", "item", "item", "payoff", "body", "body", "outro"]);
  assert.deepEqual(fx.itemPositions(scenes, [3, 1, 2]), [3, 1, 2]);
  assert.deepEqual(fx.itemPositions(scenes, [1, 6, 9]), [1]);
});

test("motion follows the role: opening punch, payoff punch, item micro-zoom, mostly hard cuts", () => {
  const plan = fx.motionPlan(scenes, offsets, snapshot);
  assert.deepEqual(plan.map((p) => p.profile), ["open", "micro", "micro", "punch", "hard", "push"]);
  const bodyOnly = [0, 1, 2, 3, 4, 5, 6].map((i) => ({ scene_index: i }));
  const profiles = fx.motionPlan(bodyOnly, [0, 2, 4, 6, 8, 10, 12], {}).slice(1).map((p) => p.profile);
  assert.ok(profiles.filter((p) => p === "hard").length >= 4, `most body cuts stay hard cuts: ${profiles}`);
  const filter = fx.cutZoomFilter(scenes, offsets, snapshot);
  assert.match(filter, /^zoompan=z='1\+/);
  assert.equal((filter.match(/between\(/g) || []).length, 5, "open + 2 micro + punch + push; hard cuts add nothing");
  assert.match(filter, /eq=contrast=1\.06:saturation=1\.05:enable='lt\(t,1\)',unsharp=.*enable='lt\(t,1\)'$/);
});

test("captions grow on the hook and payoff only", () => {
  const body = [
    "Dialogue: 0,0:00:00.50,0:00:01.00,Caption,,0,0,0,,{\\rCaptionHL\\fscx128}People{\\r} notice",
    "Dialogue: 0,0:00:05.20,0:00:05.60,Caption,,0,0,0,,plain body line",
    "Dialogue: 0,0:00:08.00,0:00:08.40,Caption,,0,0,0,,{\\rCaptionKey}3{\\r} signs",
    "Dialogue: 0,0:00:00.00,0:00:20.00,WordmarkHandle,,0,0,0,,@behindtheglance",
  ].join("\n");
  const out = fx.resizeCaptions(body, scenes, offsets, durations, snapshot).split("\n");
  assert.match(out[0], /,CaptionHook,.*\\rCaptionHLBig/);
  assert.match(out[1], /,Caption,,0,0,0,,plain body line$/);
  assert.match(out[2], /,CaptionPayoff,.*\\rCaptionKeyBig/);
  assert.equal(out[3], "Dialogue: 0,0:00:00.00,0:00:20.00,WordmarkHandle,,0,0,0,,@behindtheglance");
});

test("overlays: headline, badges, flashes, thin progress bar, styles in the styles section", () => {
  const { styles, events } = fx.overlayEvents({ scenes, offsets, durations, scriptSnapshot: snapshot, contentDuration: 15 });
  assert.ok(styles.some((s) => s.startsWith("Style: CaptionPayoff,")));
  assert.match(events, /Headline,,0,0,0,,.*3 Signs Someone\\NSecretly Respects You/);
  for (const n of ["1/3", "2/3", "3/3"]) assert.ok(events.includes(`}${n}\n`), n);
  assert.equal((events.match(/\\alpha&H90&/g) || []).length, 3, "one flash per item cut");
  assert.match(events, /\\alpha&H60&\\clip\(0,0,0,6\)\\t\(0,15000,\\clip\(0,0,1080,6\)\)/);
  assert.deepEqual(fx.itemWhooshTimes(scenes, offsets, snapshot.promised_points, 3), [2.38, 4.88]);
  const merged = fx.withEditingOverlays("[V4+ Styles]\nStyle: Caption,x\n\n[Events]\nFormat: x\nDialogue: 0,0:00:05.20,0:00:05.60,Caption,,0,0,0,,a\n",
    { scenes, offsets, durations, scriptSnapshot: snapshot, contentDuration: 15 });
  assert.ok(merged.indexOf("Style: Headline") < merged.indexOf("[Events]"));
  assert.ok(merged.includes("Dialogue: 0,0:00:05.20,0:00:05.60,Caption,,0,0,0,,a\n"));
});

test("EDITING_EFFECTS=false turns the whole layer off", () => {
  const prior = process.env.EDITING_EFFECTS;
  process.env.EDITING_EFFECTS = "false";
  delete require.cache[require.resolve("../editingEffects")];
  try {
    const off = require("../editingEffects");
    assert.equal(off.cutZoomFilter(scenes, offsets, snapshot), null);
    assert.equal(off.withEditingOverlays("x\n[Events]\n", { scenes, offsets, durations, scriptSnapshot: snapshot, contentDuration: 10 }), "x\n[Events]\n");
    assert.deepEqual(off.itemWhooshTimes(scenes, offsets, [1, 2], 9), []);
  } finally {
    if (prior === undefined) delete process.env.EDITING_EFFECTS; else process.env.EDITING_EFFECTS = prior;
    delete require.cache[require.resolve("../editingEffects")];
  }
});

test("technical QA parser and verdicts", () => {
  const log = [
    "  Duration: 00:00:32.16, start: 0.000000, bitrate: 3000 kb/s",
    "  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(tv, bt709), 1080x1920 [SAR 1:1 DAR 9:16], 2900 kb/s, 30 fps, 30 tbr",
    "  Stream #0:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo, fltp, 192 kb/s",
    "[blackdetect @ 0x1] black_start:12.4 black_end:12.6 black_duration:0.2",
    "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 20.1",
    "[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 3.2",
    "[Parsed_ebur128_0 @ 0x3] Summary:", "  Integrated loudness:", "    I:         -15.8 LUFS",
    "  True peak:", "    Peak:       -1.2 dBFS",
  ].join("\n");
  const m = tqa.parseLog(log);
  assert.equal(m.width, 1080); assert.equal(m.height, 1920); assert.equal(m.fps, 30);
  assert.equal(m.hasAudio, true); assert.equal(m.integratedLufs, -15.8); assert.equal(m.truePeakDb, -1.2);
  const ok = tqa.evaluate(m);
  assert.equal(ok.passed, true);
  assert.deepEqual(ok.warnings, ["frozen picture for 3.2s at 20.1s"]);
  assert.match(tqa.evaluate({ ...m, black: [{ start: 0, end: 0.6, duration: 0.6 }] }).fatal.join(), /black opening frame/);
  assert.match(tqa.evaluate({ ...m, hasAudio: false }).fatal.join(), /no audio stream/);
  assert.match(tqa.evaluate({ ...m, width: 720, height: 1280 }).fatal.join(), /resolution 720x1280/);
});

// Real renders through the bundled ffmpeg.
const { ffmpegBinarySkipReason } = require("./helpers/environment");

test("renders with ffmpeg: role motion applies, technical QA passes good and rejects broken renders", { skip: ffmpegBinarySkipReason() }, async () => {
  const ff = require("ffmpeg-static");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "editing-fx-"));
  try {
    const sc = [{ scene_index: 0 }, { scene_index: 1 }, { scene_index: 2 }, { scene_index: 3 }, { scene_index: 4 }];
    const off = [0, 2, 4, 6, 8];
    const snap = { title: "Test", promised_points: [1, 2], payoff: { resolved_in_scene: 3 } };
    fs.writeFileSync(path.join(dir, "t.ass"), fx.withEditingOverlays(
      "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
      { scenes: sc, offsets: off, durations: [2, 2, 2, 2, 2], scriptSnapshot: snap, contentDuration: 10 }));
    const v = "testsrc2=s=1080x1920:r=30:d=10";
    execFileSync(ff, ["-y", "-f", "lavfi", "-i", v, "-f", "lavfi", "-i", "sine=f=220:d=10", "-filter_complex",
      `[0:v]${fx.cutZoomFilter(sc, off, snap)}[z];[z]ass=t.ass[v]`,
      "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "good.mp4"], { cwd: dir, stdio: "pipe" });
    const corner = (t) => execFileSync(ff, ["-ss", String(t), "-i", "good.mp4", "-frames:v", "1", "-vf", "crop=200:200:0:0,scale=8:8", "-f", "rawvideo", "-pix_fmt", "gray", "-"], { cwd: dir });
    assert.notEqual(Buffer.compare(corner(6.02), corner(6.6)), 0, "payoff punch-in changes the framing");
    const good = await tqa.checkFinalVideo(path.join(dir, "good.mp4"));
    assert.equal(good.passed, true, JSON.stringify(good.fatal));
    assert.equal(good.metrics.width, 1080);

    execFileSync(ff, ["-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30:d=1", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=9",
      "-f", "lavfi", "-i", "sine=f=220:d=10", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
      "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "black.mp4"], { cwd: dir, stdio: "pipe" });
    const black = await tqa.checkFinalVideo(path.join(dir, "black.mp4"));
    assert.equal(black.passed, false);
    assert.match(black.fatal.join(), /black opening frame/);

    execFileSync(ff, ["-y", "-f", "lavfi", "-i", v, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "silent.mp4"], { cwd: dir, stdio: "pipe" });
    assert.match((await tqa.checkFinalVideo(path.join(dir, "silent.mp4"))).fatal.join(), /no audio stream/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
