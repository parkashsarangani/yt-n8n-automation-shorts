/**
 * Output Quality & Correctness Tests
 * -------------------------------------------------------------------
 * Exercises the compositor as an asynchronous service and validates stable
 * media invariants. These tests deliberately do not impose a minimum bitrate:
 * CRF encoding is content-adaptive, so a static template can be excellent at a
 * fraction of the bitrate required by detailed stock footage.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { execFileSync, spawn } = require("child_process");
const http = require("http");

const { renderToolchainSkipReason } = require("./helpers/environment");

// These tests boot the compositor and render real MP4s. Without the render
// toolchain they previously failed with "compose server exited early with 1",
// which reads exactly like a regression. Skip with a reason instead.
const RENDER_SKIP = renderToolchainSkipReason();

const COMPOSE_PORT = 4111;
const BASE_URL = `http://localhost:${COMPOSE_PORT}`;
const TEST_TIMEOUT = 180_000;
let serverProcess;

function generateAudioBase64(durationSec = 3) {
    const tmpPath = path.join(__dirname, `_test_audio_${Date.now()}_${Math.random().toString(16).slice(2)}.mp3`);
    // Quiet tone rather than digital silence: it exercises the normal speech
    // normalization path without manufacturing loudnorm -infinity/NaN input.
    execFileSync("ffmpeg", [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-af", "volume=0.04", "-t", String(durationSec),
        "-c:a", "libmp3lame", "-b:a", "64k", tmpPath,
    ]);
    const base64 = fs.readFileSync(tmpPath).toString("base64");
    fs.unlinkSync(tmpPath);
    return base64;
}

function alignmentFor(text = "Hello world") {
    const characters = [...text];
    const starts = characters.map((_, i) => i * 0.06);
    return {
        characters,
        character_start_times_seconds: starts,
        character_end_times_seconds: starts.map((x) => x + 0.055),
    };
}

function buildTestPayload(opts = {}) {
    const audioBase64 = generateAudioBase64(opts.audioDuration || 3);
    return {
        hook: "Test hook",
        caption_style: opts.mood || "neutral",
        caption_mode: opts.captionMode || "key_phrases",
        comment_hook: "Would you try this?",
        engagement_mode: opts.engagementMode || "none",
        outro_line: opts.outroLine || null,
        creative_format: opts.creativeFormat || "documentary_cinematic",
        data: [
            {
                scene_index: 0,
                point: "test point",
                narration: "Hello world",
                visual_source: "template",
                template_name: opts.templateName || "kinetic_text",
                template_data: opts.templateData || { line: "Hello World" },
                audio: { audio_base64: audioBase64, alignment: alignmentFor() },
            },
            ...(opts.extraScenes || []),
        ],
    };
}

function postJSON(urlPath, body) {
    return new Promise((resolve, reject) => {
        const data = JSON.stringify(body);
        const req = http.request(`${BASE_URL}${urlPath}`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
        }, (res) => {
            let chunks = "";
            res.on("data", (c) => (chunks += c));
            res.on("end", () => {
                try { resolve({ status: res.statusCode, body: JSON.parse(chunks) }); }
                catch { resolve({ status: res.statusCode, body: chunks }); }
            });
        });
        req.on("error", reject);
        req.write(data);
        req.end();
    });
}

function getJSON(urlPath) {
    return new Promise((resolve, reject) => {
        http.get(`${BASE_URL}${urlPath}`, (res) => {
            let chunks = "";
            res.on("data", (c) => (chunks += c));
            res.on("end", () => {
                try { resolve({ status: res.statusCode, body: JSON.parse(chunks) }); }
                catch { resolve({ status: res.statusCode, body: chunks }); }
            });
        }).on("error", reject);
    });
}

async function pollJob(jobId, predicate, timeoutMs = TEST_TIMEOUT) {
    const start = Date.now();
    let last = null;
    while (Date.now() - start < timeoutMs) {
        const { body } = await getJSON(`/compose-status/${jobId}`);
        last = body;
        if (predicate(body)) return body;
        await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error(`Job ${jobId} timed out; last status=${JSON.stringify(last)}`);
}

async function pollJobUntilDone(jobId, timeoutMs = TEST_TIMEOUT) {
    const result = await pollJob(jobId, (body) => body.status === "done" || body.status === "failed", timeoutMs);
    if (result.status === "failed") throw new Error(`Job failed: ${result.error}`);
    return result;
}

async function pollJobUntilFailed(jobId, timeoutMs = 60_000) {
    const result = await pollJob(jobId, (body) => body.status === "failed" || body.status === "done", timeoutMs);
    assert.strictEqual(result.status, "failed", `Expected failure, got ${result.status}`);
    return result;
}

function ffprobeJSON(filePath) {
    return JSON.parse(execFileSync("ffprobe", [
        "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filePath,
    ], { encoding: "utf8" }));
}

function ratio(value) {
    const [a, b] = String(value || "0/1").split("/").map(Number);
    return b ? a / b : a;
}

before(async () => {
    if (RENDER_SKIP) return;
    await fsp.mkdir(path.join(__dirname, "_test_outputs"), { recursive: true });
    serverProcess = spawn("node", ["compose.js"], {
        cwd: path.join(__dirname, ".."),
        env: {
            ...process.env,
            PORT: String(COMPOSE_PORT),
            OUTPUT_DIR: path.join(__dirname, "_test_outputs"),
            DEBUG_KEEP_TMP: "false",
            // Render mechanics are tested here; multimodal QA parsing/gating has
            // dedicated tests and must not require a live API key in CI.
            BROLL_FINAL_QA_ENABLED: "false",
            BROLL_LOCAL_RERANK_ENABLED: "false",
        },
        stdio: "pipe",
    });

    await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Server failed to start within 15s")), 15_000);
        serverProcess.stdout.on("data", (chunk) => {
            if (chunk.toString().includes("listening on")) {
                clearTimeout(timeout);
                resolve();
            }
        });
        serverProcess.stderr.on("data", (chunk) => console.error("[server]", chunk.toString()));
        serverProcess.on("exit", (code) => {
            if (code && code !== 0) reject(new Error(`compose server exited early with ${code}`));
        });
    });
});

after(async () => {
    if (RENDER_SKIP) return;
    if (serverProcess) {
        serverProcess.kill("SIGTERM");
        await new Promise((r) => setTimeout(r, 500));
    }
    await fsp.rm(path.join(__dirname, "_test_outputs"), { recursive: true, force: true });
});

describe("API endpoints", { skip: RENDER_SKIP }, () => {
    it("POST /compose returns 202 with job_id", async () => {
        const { status, body } = await postJSON("/compose", buildTestPayload());
        assert.strictEqual(status, 202);
        assert.ok(body.job_id);
        assert.strictEqual(body.status, "processing");
    });

    it("GET /compose-status/:id returns 404 for unknown job", async () => {
        const { status, body } = await getJSON("/compose-status/nonexistent-id");
        assert.strictEqual(status, 404);
        assert.strictEqual(body.status, "not_found");
    });

    it("GET /topic-history returns topics array", async () => {
        const { status, body } = await getJSON("/topic-history");
        assert.strictEqual(status, 200);
        assert.ok(Array.isArray(body.topics));
    });

    it("POST /topic-history stores a topic", async () => {
        const { status, body } = await postJSON("/topic-history", { topic: "Test topic", hook: "Test hook" });
        assert.strictEqual(status, 200);
        assert.strictEqual(body.success, true);
    });
});

describe("Video output quality", { timeout: TEST_TIMEOUT, skip: RENDER_SKIP }, () => {
    let outputPath;
    let probeData;

    before(async () => {
        const { body } = await postJSON("/compose", buildTestPayload({ audioDuration: 3, mood: "upbeat" }));
        const result = await pollJobUntilDone(body.job_id);
        outputPath = result.output_path;
        assert.ok(fs.existsSync(outputPath), `Output file should exist: ${outputPath}`);
        probeData = ffprobeJSON(outputPath);
    });

    it("produces a valid 1080x1920 H.264 High-profile MP4 at 30fps", () => {
        assert.strictEqual(probeData.format.format_name, "mov,mp4,m4a,3gp,3g2,mj2");
        const video = probeData.streams.find((s) => s.codec_type === "video");
        assert.ok(video);
        assert.strictEqual(video.width, 1080);
        assert.strictEqual(video.height, 1920);
        assert.strictEqual(video.codec_name, "h264");
        assert.ok(String(video.profile || "").toLowerCase().includes("high"));
        assert.ok(Math.abs(ratio(video.r_frame_rate) - 30) < 0.1);
        assert.strictEqual(video.pix_fmt, "yuv420p");
    });

    it("uses BT.709 metadata when color metadata is emitted", () => {
        const video = probeData.streams.find((s) => s.codec_type === "video");
        if (video.color_space) assert.strictEqual(video.color_space, "bt709");
        if (video.color_primaries) assert.strictEqual(video.color_primaries, "bt709");
    });

    it("produces standard AAC stereo audio", () => {
        const audio = probeData.streams.find((s) => s.codec_type === "audio");
        assert.ok(audio);
        assert.strictEqual(audio.codec_name, "aac");
        assert.ok([44100, 48000].includes(parseInt(audio.sample_rate)));
        assert.strictEqual(audio.channels, 2);
    });

    it("has sane duration and does not bloat CRF output", () => {
        const duration = parseFloat(probeData.format.duration);
        assert.ok(duration >= 2 && duration <= 10, `Unexpected duration ${duration}s`);
        const mbPerSecond = (fs.statSync(outputPath).size / (1024 * 1024)) / duration;
        assert.ok(mbPerSecond > 0 && mbPerSecond < 5, `Unexpected file density ${mbPerSecond.toFixed(2)} MB/s`);
        const video = probeData.streams.find((s) => s.codec_type === "video");
        if (video.bit_rate) {
            const bitrateMbps = parseInt(video.bit_rate) / 1_000_000;
            assert.ok(bitrateMbps > 0 && bitrateMbps < 50, `Unexpected bitrate ${bitrateMbps.toFixed(2)} Mbps`);
        }
    });
});

describe("Template scenes render correctly", { timeout: TEST_TIMEOUT, skip: RENDER_SKIP }, () => {
    const annotatedImage = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='600'%3E%3Crect width='800' height='600' fill='%23223344'/%3E%3Ccircle cx='400' cy='300' r='140' fill='%23eeeeee'/%3E%3C/svg%3E";
    for (const fixture of [
        { name: "stat_reveal", data: { statValue: "99.7%", label: "ACCURACY" }, mood: "serious" },
        { name: "comparison", data: { leftLabel: "BEFORE", leftValue: "$100", rightLabel: "AFTER", rightValue: "$10,000" }, mood: "upbeat" },
        { name: "kinetic_text", data: { line: "This changes everything" }, mood: "funny" },
        {
            name: "map",
            data: {
                title: "Berlin to Tokyo",
                locations: [
                    { label: "Berlin", lat: 52.52, lon: 13.405 },
                    { label: "Tokyo", lat: 35.6762, lon: 139.6503 },
                ],
                connections: [{ from: "Berlin", to: "Tokyo", label: "route" }],
            },
            mood: "serious",
        },
        {
            name: "timeline",
            data: {
                title: "Aviation milestones",
                events: [
                    { date: "1903", label: "First powered flight" },
                    { date: "1969", label: "Humans reach the Moon" },
                ],
            },
            mood: "neutral",
        },
        {
            name: "diagram",
            data: {
                title: "Cause and effect",
                nodes: [{ id: "a", label: "Input" }, { id: "b", label: "Output" }],
                edges: [{ from: "a", to: "b", label: "causes" }],
            },
            mood: "upbeat",
        },
        {
            name: "annotated_real",
            data: {
                imageUrl: annotatedImage,
                imageWidth: 800,
                imageHeight: 600,
                title: "Exact verified image",
                annotations: [{ label: "target", x_pct: 50, y_pct: 50, w_pct: 35, h_pct: 45 }],
            },
            mood: "serious",
        },
    ]) {
        it(`${fixture.name} produces valid vertical output`, async () => {
            const { body } = await postJSON("/compose", buildTestPayload({
                templateName: fixture.name,
                templateData: fixture.data,
                mood: fixture.mood,
            }));
            const result = await pollJobUntilDone(body.job_id);
            assert.ok(fs.existsSync(result.output_path));
            const video = ffprobeJSON(result.output_path).streams.find((s) => s.codec_type === "video");
            assert.strictEqual(video.width, 1080);
            assert.strictEqual(video.height, 1920);
        });
    }
});

describe("Multi-scene composition", { timeout: TEST_TIMEOUT * 2, skip: RENDER_SKIP }, () => {
    it("uses hard cuts and preserves all scene durations plus requested outro", async () => {
        const audioBase64 = generateAudioBase64(3);
        const alignment = alignmentFor("Test");
        const payload = {
            hook: "Multi-scene test",
            caption_style: "neutral",
            caption_mode: "key_phrases",
            comment_hook: "Thoughts?",
            engagement_mode: "share_only",
            outro_line: "Share this with someone curious",
            creative_format: "comparison_reveal",
            data: [
                {
                    scene_index: 0, point: "one", narration: "Scene one",
                    visual_source: "template", template_name: "kinetic_text",
                    template_data: { line: "Scene one" },
                    audio: { audio_base64: audioBase64, alignment },
                },
                {
                    scene_index: 1, point: "two", narration: "The answer is forty two",
                    visual_source: "template", template_name: "stat_reveal",
                    template_data: { statValue: "42", label: "THE ANSWER" },
                    audio: { audio_base64: audioBase64, alignment },
                },
            ],
        };
        const { body } = await postJSON("/compose", payload);
        const result = await pollJobUntilDone(body.job_id, TEST_TIMEOUT * 2);
        const duration = parseFloat(ffprobeJSON(result.output_path).format.duration);
        assert.ok(duration > 7.5 && duration < 9.5, `Unexpected hard-cut duration ${duration}s`);
    });
});

describe("Error handling", { skip: RENDER_SKIP }, () => {
    it("rejects empty scenes array", async () => {
        const { body } = await postJSON("/compose", { hook: "test", caption_style: "neutral", data: [] });
        if (body.job_id) {
            const status = await pollJobUntilFailed(body.job_id);
            assert.match(status.error, /No scenes/i);
        }
    });

    it("rejects scene without audio before expensive rendering", async () => {
        const started = Date.now();
        const { body } = await postJSON("/compose", {
            hook: "test", caption_style: "neutral", data: [{
                scene_index: 0, visual_source: "template", template_name: "kinetic_text", template_data: { line: "No audio" },
            }],
        });
        const status = await pollJobUntilFailed(body.job_id);
        assert.match(status.error, /audio/i);
        if (String(process.env.CREATIVE_SYSTEM_EXPECT_PREFLIGHT || "false") === "true") {
            assert.ok(Date.now() - started < 10_000, "invalid scene did not fail during preflight");
        }
    });

    it("rejects unknown template name before expensive rendering", async () => {
        const started = Date.now();
        const { body } = await postJSON("/compose", {
            hook: "test", caption_style: "neutral", data: [{
                scene_index: 0, visual_source: "template", template_name: "nonexistent_template", template_data: {},
                audio: { audio_base64: generateAudioBase64(2) },
            }],
        });
        const status = await pollJobUntilFailed(body.job_id);
        assert.match(status.error, /nonexistent_template/i);
        if (String(process.env.CREATIVE_SYSTEM_EXPECT_PREFLIGHT || "false") === "true") {
            assert.ok(Date.now() - started < 10_000, "unknown template did not fail during preflight");
        }
    });
});
