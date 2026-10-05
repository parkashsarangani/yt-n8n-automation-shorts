const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const fsp = fs.promises;
const os = require("os");
const path = require("path");
const http = require("http");
const { execFileSync } = require("child_process");
const ffmpegPath = require("ffmpeg-static");

const cacheDir = path.join(os.tmpdir(), `v4-broll-cache-test-${process.pid}-${Date.now()}`);
process.env.BROLL_VERIFIED_CACHE_DIR = cacheDir;
process.env.BROLL_PUBLIC_BASE_URL = "http://127.0.0.1:4000/outputs";

const resolverPath = path.join(__dirname, "..", "brollResolver.js");
const transformed = fs.readFileSync(resolverPath, "utf8").includes("V4_VIDEO_SAMPLE_STAGING");
const { sampleVideoContactSheet, materializeVerifiedClip } = require("../brollResolver");

const { ffmpegBinarySkipReason } = require("./helpers/environment");
const maybeDescribe = transformed && !ffmpegBinarySkipReason() ? describe : describe.skip;

maybeDescribe("production V4 video contact-sheet runtime", () => {
  let dir;
  let videoPath;
  let server;
  let baseUrl;

  before(async () => {
    dir = await fsp.mkdtemp(path.join(os.tmpdir(), "v4-contact-sheet-test-"));
    videoPath = path.join(dir, "fixture.mp4");
    execFileSync(ffmpegPath, [
      "-hide_banner", "-loglevel", "error", "-y",
      "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=12",
      "-t", "2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", videoPath,
    ], { stdio: "pipe" });

    server = http.createServer(async (req, res) => {
      if (req.url === "/redirect") {
        res.writeHead(302, { Location: "/fixture.mp4" });
        res.end();
        return;
      }
      if (req.url === "/fixture.mp4") {
        const st = await fsp.stat(videoPath);
        res.writeHead(200, { "Content-Type": "video/mp4", "Content-Length": String(st.size) });
        fs.createReadStream(videoPath).pipe(res);
        return;
      }
      res.writeHead(404);
      res.end();
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    baseUrl = `http://127.0.0.1:${address.port}`;
  });

  after(async () => {
    if (server) await new Promise((resolve) => server.close(resolve));
    if (dir) await fsp.rm(dir, { recursive: true, force: true });
    await fsp.rm(cacheDir, { recursive: true, force: true });
  });

  it("stages an HTTP video locally before FFmpeg and renders a real contact sheet", async () => {
    const result = await sampleVideoContactSheet({
      duration: 2,
      sample_url: `${baseUrl}/redirect`,
      url: `${baseUrl}/fixture.mp4`,
      type: "video",
    });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.sample_source, "download");
    assert.match(result.imageUrl, /^data:image\/png;base64,/);
    assert.equal(result.timestamps.length, 12);
    assert.ok(result.sample_bytes > 1024);
  });

  it("uses an already-local verified clip without an HTTP round trip", async () => {
    const result = await sampleVideoContactSheet({
      duration: 2,
      local_path: videoPath,
      sample_url: "https://invalid.example/should-not-be-used.mp4",
      type: "video",
    });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.sample_source, "local");
    assert.match(result.imageUrl, /^data:image\/png;base64,/);
  });

  it("stages the selected full-quality HTTP asset before verified trim materialization", async () => {
    const result = await materializeVerifiedClip(
      { duration: 2, url: `${baseUrl}/redirect`, type: "video" },
      { verified_start_sec: 0.25, verified_end_sec: 1.5 },
    );
    const st = await fsp.stat(result.local_path);
    assert.ok(st.size > 4096);
    assert.equal(result.in_point_sec, 0.25);
    assert.equal(result.out_point_sec, 1.5);
  });

  it("classifies malformed sampling input before spending vision budget", async () => {
    const result = await sampleVideoContactSheet({ duration: 0, type: "video" });
    assert.equal(result.ok, false);
    assert.equal(result.reason, "video_duration_invalid");
  });
});
