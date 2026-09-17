/**
 * compose.js
 * -----------------------------------------------------------------------
 * Express endpoint: POST /compose
 * Studio-grade short-form video compositor (TikTok/Reels/Shorts)
 * Hybrid architecture: Remotion for templates/captions + ffmpeg for
 * stock scenes, audio mixing, transitions, and final encoding.
 * -----------------------------------------------------------------------
 */

const express = require("express");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const axios = require("axios");
const ffmpeg = require("fluent-ffmpeg");
const ffmpegPath = require("ffmpeg-static");
const { execFile } = require("child_process");
const { resolveBroll } = require("./brollResolver");
const feedback = require("./feedbackLoop");
const competitors = require("./competitorMining");
ffmpeg.setFfmpegPath(ffmpegPath);

const app = express();
app.use(express.json({ limit: "50mb" }));
// In-memory job store for the async compose pattern
const {DurableJobs,installLifecycle}=require('./jobLifecycle');
const jobStore = new DurableJobs(path.join(path.dirname(process.env.TOPIC_HISTORY_PATH || path.join(__dirname,'topic_history.json')),'compose-jobs'));

const OUTPUT_DIR = process.env.OUTPUT_DIR || "/outputs";
const beginDrain=installLifecycle(app,jobStore,OUTPUT_DIR);
app.use("/outputs", express.static(OUTPUT_DIR));

const MUSIC_DIR = process.env.MUSIC_DIR || path.join(__dirname, "music");
const MOTION_ASSETS_DIR = process.env.MOTION_ASSETS_DIR || path.join(__dirname, "motion-assets");
const REMOTION_DIR = path.join(__dirname, "remotion");
const TOPIC_HISTORY_PATH = process.env.TOPIC_HISTORY_PATH || path.join(__dirname, "topic_history.json");
const TOPIC_HISTORY_MAX = 90;

const TARGET_W = 1080;
const TARGET_H = 1920;
const FPS = 30;

// Detect video encoder hardware support (libx264 fallback)
const V_ENCODER = process.env.USE_NVENC ? "h264_nvenc" : "libx264";

// AI image/video generation (fal) has been removed. All b-roll now comes from
// real sources via the /resolve-broll resolver (Pexels photos + videos,
// Unsplash, Wikipedia), so scenes are either a real stock video, a real photo
// with Ken-Burns motion, or a Remotion template - never AI-generated.

// Fixed engagement outro appended to every video. 2.5s gives the four
// asks (comment, like, share, follow) room to land - the KineticText
// template reveals words one at a time, so a bare 2s felt rushed.
const OUTRO_DURATION_SEC = 2.5;
const DEFAULT_OUTRO_LINE = "Comment, like, share, and follow";

// Channel identity: a persistent gold wordmark + handle (matching the lightbulb
// brand) burned on every frame, an optional corner logo watermark, and a
// consistent signature music bed. All build recognition / a returning
// audience without delaying the hook.
const CHANNEL_WORDMARK = process.env.CHANNEL_WORDMARK || "Favourite Facts";
const CHANNEL_HANDLE = process.env.CHANNEL_HANDLE || "@YourFavouriteDailyFacts";
// Optional corner logo (gold lightbulb): drop a transparent PNG at
// shorts-compose/assets/logo.png to activate - see assets/README.md. Without
// it, this is a clean no-op, same pattern as music/signature.mp3.
const CHANNEL_LOGO_PATH = path.join(__dirname, "assets", "logo.png");

// ---------------------------------------------------------------------------
// Endpoints: Topic History
// ---------------------------------------------------------------------------

app.get("/topic-history", async (req, res) => {
  try {
    if (!fs.existsSync(TOPIC_HISTORY_PATH)) return res.json({ topics: [] });
    const raw = await fsp.readFile(TOPIC_HISTORY_PATH, "utf8");
    return res.json({ topics: JSON.parse(raw) });
  } catch (err) {
    console.error("Failed to read topic history:", err);
    return res.status(500).json({ topics: [], error: err.message });
  }
});

app.post("/topic-history", async (req, res) => {
  try {
    const { topic, hook } = req.body;
    if (!topic) return res.status(400).json({ success: false, error: "topic is required" });

    let topics = [];
    if (fs.existsSync(TOPIC_HISTORY_PATH)) {
      topics = JSON.parse(await fsp.readFile(TOPIC_HISTORY_PATH, "utf8"));
    }
    topics.push({ topic, hook: hook || null, created_at: new Date().toISOString() });
    if (topics.length > TOPIC_HISTORY_MAX) {
      topics = topics.slice(topics.length - TOPIC_HISTORY_MAX);
    }
    // TOPIC_HISTORY_PATH may live in a mounted data dir (/app/data); ensure
    // the parent exists so the write works regardless of how the app is run.
    await fsp.mkdir(path.dirname(TOPIC_HISTORY_PATH), { recursive: true });
    await fsp.writeFile(TOPIC_HISTORY_PATH, JSON.stringify(topics, null, 2));
    return res.json({ success: true, count: topics.length });
  } catch (err) {
    console.error("Failed to write topic history:", err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Feedback loop: log published videos, ingest analytics, serve learned insights
// ---------------------------------------------------------------------------

// After a Short is published, record the metadata that produced it.
app.post("/performance/log", async (req, res) => {
  try {
    const result = await feedback.logPublished(req.body || {});
    res.json({ success: true, ...result });
  } catch (err) {
    res.status(400).json({ success: false, error: err.message });
  }
});

// Daily: n8n posts the raw YouTube Analytics (dimensions=video) response; we
// join it onto the log and re-run the strategist to refresh channel_insights.
app.post("/performance/ingest", async (req, res) => {
  try {
    const result = await feedback.ingestAnalytics(req.body || {});
    res.json({ success: true, ...result });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// The topic generator reads this before picking a topic.
app.get("/channel-insights", async (req, res) => {
  try {
    res.json(await feedback.getInsights());
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Competitor outlier mining: watch comparable fact/curiosity Shorts channels,
// find what is breaking out for them, and distill transferable topic/angle
// signals the topic generator can lean toward. Run weekly from n8n.
// ---------------------------------------------------------------------------

// Weekly job hits this: scan the watchlist, distill, persist competitor_signals.
app.post("/competitors/mine", async (req, res) => {
  try {
    const summary = await competitors.mine();
    res.json({ success: true, ...summary });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// The topic generator reads this alongside channel-insights before picking a topic.
app.get("/competitors/signals", async (req, res) => {
  try {
    res.json(await competitors.getSignals());
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function newTmpDir() {
  const dir = path.join(os.tmpdir(), "shorts-" + crypto.randomUUID());
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

async function downloadFile(url, destPath) {
  // Wikimedia enforces its User-Agent policy and 403s requests with no/generic
  // UA (axios's default) - real b-roll photos of named subjects come from
  // upload.wikimedia.org, so every download needs an identifying UA or those
  // scenes fail outright.
  const res = await axios.get(url, {
    responseType: "arraybuffer",
    timeout: 60000,
    headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" },
  });
  await fsp.writeFile(destPath, res.data);
  return destPath;
}

async function writeBase64(base64, destPath) {
  await fsp.writeFile(destPath, Buffer.from(base64, "base64"));
  return destPath;
}

function generateSilentAudioBase64(durationSec) {
  return new Promise((resolve, reject) => {
    const tmpPath = path.join(os.tmpdir(), `silence-${crypto.randomUUID()}.mp3`);
    execFile(
      ffmpegPath,
      ["-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", String(durationSec), "-c:a", "libmp3lame", "-b:a", "64k", tmpPath],
      (err) => {
        if (err) return reject(err);
        fs.readFile(tmpPath, (readErr, data) => {
          fs.unlink(tmpPath, () => {});
          if (readErr) return reject(readErr);
          resolve(data.toString("base64"));
        });
      }
    );
  });
}

function run(cmdBuilder) {
  return new Promise((resolve, reject) => {
    cmdBuilder
      .on("start", (cmd) => console.log("[ffmpeg]", cmd))
      .on("error", (err, stdout, stderr) => {
        console.error("[ffmpeg error]", err.message);
        console.error(stderr);
        reject(err);
      })
      .on("end", () => resolve())
      .run();
  });
}

// loudnorm computes a gain from the input's measured LUFS to the -16 LUFS
// target - on true digital silence (e.g. the outro's generated silent
// track) or other near-silent audio, measured loudness is -infinity, and
// the resulting gain is NaN, which crashes the AAC encoder entirely. Try
// with loudnorm first (normal case); if it fails, retry the identical
// encode without it rather than losing the whole scene.
async function runAudioMux(cmdFactory) {
  try {
    await run(cmdFactory(true));
  } catch (err) {
    console.warn(`[loudnorm] normalization failed (${err.message}), retrying without it`);
    await run(cmdFactory(false));
  }
}

function ffprobeDuration(filePath) {
  return new Promise((resolve, reject) => {
    ffmpeg.ffprobe(filePath, (err, data) => {
      if (err) return reject(err);
      resolve(data.format.duration);
    });
  });
}

// ---------------------------------------------------------------------------
// Studio Color Grading (split-tone with shadow lift and highlight rolloff)
// ---------------------------------------------------------------------------

function getColorGrade(mood) {
  // Natural, believable grade - deliberately NOT the old hyper-saturated
  // "punchy" look (sat 1.3-1.55), which stacked on the AI-art image prompt and
  // read as synthetic/AI-slop. Real photos aren't oversaturated. We keep clean
  // contrast (near-true blacks, full whites, so nothing looks foggy) but pull
  // saturation and colour tints back toward true-to-life, and gentle the mid
  // S-curve so it reads as a real, well-exposed photograph rather than AI art.
  const filmCurve = (b, s, m, h) => `curves=m='0/${b} 0.25/${s} 0.5/${m} 0.75/${h} 1/1.0'`;
  const grades = {
    upbeat: [
      "eq=contrast=1.09:saturation=1.16:brightness=0.02",
      "colorbalance=rs=0.05:gs=0.01:bs=-0.03:rh=0.02:gh=0.01:bh=-0.01",
      filmCurve("0.02", "0.23", "0.50", "0.80"),
    ].join(","),
    serious: [
      "eq=contrast=1.10:saturation=1.02:brightness=0.01",
      "colorbalance=rs=-0.02:gs=0.0:bs=0.03:rh=0.01:gh=0.0:bh=0.02",
      filmCurve("0.02", "0.22", "0.50", "0.79"),
    ].join(","),
    funny: [
      "eq=contrast=1.08:saturation=1.18:brightness=0.02",
      "colorbalance=rs=0.05:gs=0.03:bs=-0.03:rh=0.02:gh=0.02:bh=-0.01",
      filmCurve("0.02", "0.23", "0.50", "0.81"),
    ].join(","),
    neutral: [
      "eq=contrast=1.08:saturation=1.10:brightness=0.02",
      "colorbalance=rs=0.01:gs=0.0:bs=0.01:rh=0.02:gh=0.0:bh=-0.01",
      filmCurve("0.02", "0.23", "0.50", "0.80"),
    ].join(","),
  };
  return grades[mood] || grades.neutral;
}

function pickMusicTrack(mood) {
  // A single signature track across every video is a sonic identity - people
  // recognise (and return to) a channel by its consistent sound. If present it
  // always wins over the per-mood tracks.
  const signature = path.join(MUSIC_DIR, "signature.mp3");
  if (fs.existsSync(signature)) return signature;
  const candidate = path.join(MUSIC_DIR, `${(mood || "neutral").toLowerCase()}.mp3`);
  if (fs.existsSync(candidate)) return candidate;
  return path.join(MUSIC_DIR, "neutral.mp3");
}

// ---------------------------------------------------------------------------
// Remotion Render Bridge
// ---------------------------------------------------------------------------

function renderRemotion(compositionId, outputPath, durationSec, props) {
  return new Promise((resolve, reject) => {
    const bridgePath = path.join(REMOTION_DIR, "render-bridge.mjs");
    const args = [
      bridgePath,
      compositionId,
      outputPath,
      String(durationSec),
      JSON.stringify(props),
    ];

    console.log(`[remotion] Rendering ${compositionId} (${durationSec}s)...`);
    const child = execFile("node", args, {
      cwd: REMOTION_DIR,
      timeout: 300000, // 5 min max
      maxBuffer: 10 * 1024 * 1024,
    }, (err, stdout, stderr) => {
      if (stdout) console.log("[remotion stdout]", stdout);
      if (stderr) console.log("[remotion stderr]", stderr);
      if (err) {
        console.error("[remotion] Render failed:", err.message);
        return reject(err);
      }
      resolve(outputPath);
    });
  });
}

// ---------------------------------------------------------------------------
// Stock Scene Processing (ffmpeg — eased zoompan, cinematic grading)
// ---------------------------------------------------------------------------

async function buildStockVideoScene(stockVideoPath, audioPath, duration, outPath, sceneIdx, mood) {
  const stockDuration = await ffprobeDuration(stockVideoPath);

  // The AI clip (~5s) is usually SHORTER than the scene's narration. The old
  // behaviour froze the last frame (tpad clone) to fill the gap, so the motion
  // visibly STOPPED partway through the scene. Instead, slow the clip with
  // setpts so its movement spans the entire scene - continuous motion, never a
  // freeze. (+0.15s of headroom so the final -t trims cleanly at the end.)
  const stretch =
    stockDuration > 0.1 && stockDuration < duration
      ? (duration + 0.15) / stockDuration
      : 1;
  const stretchFilter = stretch > 1.01 ? `setpts=${stretch.toFixed(4)}*PTS,` : "";

  // Cinematic processing: scale to fill, crop, split-tone grade, and a
  // subtle unsharp for crispness (vignette removed - it read too heavy).
  const gradeFilter = getColorGrade(mood);

  const videoFilter = [
    `[0:v]${stretchFilter}scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase`,
    `crop=${TARGET_W}:${TARGET_H}`,
    gradeFilter,
    `unsharp=5:5:0.4:5:5:0.0`,
  ].join(",") + "[processed]";

  await runAudioMux((normalize) => {
    const opts = ["-map", "[processed]", "-map", "1:a", "-t", String(duration)];
    if (normalize) opts.push("-af", "loudnorm=I=-16:TP=-1.5:LRA=11");
    opts.push(
      "-c:v", V_ENCODER,
      "-r", String(FPS),
      "-preset", "veryfast",
      "-crf", "18",
      "-pix_fmt", "yuv420p",
      "-c:a", "aac",
      "-b:a", "192k",
    );
    return ffmpeg()
      .input(stockVideoPath)
      .input(audioPath)
      .complexFilter([videoFilter])
      .outputOptions(opts)
      .output(outPath);
  });

  return outPath;
}

async function buildImageScene(imagePaths, audioPath, duration, outPath, sceneIdx, mood, isEmphasis = false) {
  // Eased Ken Burns with sinusoidal motion (not linear)
  // Creates organic, handheld-feeling camera movement
  const fps = FPS;
  // ONE continuous Ken Burns move per distinct image - not one per time slice.
  // The old code split every scene into 3.5-4.5s segments and, with a single
  // image, re-ran the zoom FROM THE START in each segment, so one photo looked
  // like it zoomed over and over. Now a scene with 1 image is one smooth move
  // across the whole scene; a scene with 2 images cuts once between them.
  // Emphasis (payoff) scene is always a single deliberate push-in.
  const numSegments = isEmphasis ? 1 : Math.max(1, imagePaths.length);
  const segDuration = duration / numSegments;
  const totalFrames = Math.ceil(segDuration * fps);
  const gradeFilter = getColorGrade(mood);

  const segmentPaths = [];
  for (let seg = 0; seg < numSegments; seg++) {
    const imgPath = imagePaths[seg % imagePaths.length];
    const segOutPath = path.join(path.dirname(outPath), `seg_${sceneIdx}_${seg}.mp4`);
    const panLeftToRight = seg % 2 === 0;

    // Sinusoidal easing: slow start, slow end, gentle drift through the middle
    // zoompan expressions use on/d for normalized progress
    const xExpr = panLeftToRight
      ? `'iw*0.035*(1-cos(PI*on/${totalFrames}))/2'`
      : `'iw*0.035*(1+cos(PI*on/${totalFrames}))/2'`;

    // Alternate the camera move per SCENE for variety: even scenes slowly
    // push IN, odd scenes pull OUT (gently eased). Breaks the "same subtle
    // drift on every scene" monotony without speeding anything up, and keeps
    // the motion coherent within a scene (all its segments move the same way).
    const pushIn = sceneIdx % 2 === 0;
    const zoomExpr = pushIn
      ? `'1.05+0.05*(1-cos(PI*on/${totalFrames}))/2'`
      : `'1.10-0.05*(1-cos(PI*on/${totalFrames}))/2'`;

    // Gentle punch-in on the first segment only, eased over its own window
    const punchFrames = Math.min(18, Math.round(totalFrames * 0.3));
    const finalZoom = isEmphasis
      ? `'1.02+0.18*(1-cos(PI*on/${totalFrames}))/2'`
      : sceneIdx === 0 && seg === 0
        ? `'if(lte(on,${punchFrames}),1.13-0.04*on/${punchFrames},${zoomExpr.slice(1, -1)})'`
        : zoomExpr;

    const filterGraph = [
      // Upscale well past the output resolution before zoompan - cropping
      // from a source close to the output size makes the per-frame crop
      // window round to whole pixels unevenly, which reads as flicker/
      // vibration. The extra sub-pixel headroom eliminates that jitter.
      `[0:v]scale=-2:6000,zoompan=z=${finalZoom}:x=${xExpr}:y='ih*0.02*(1-cos(PI*on/${totalFrames}))/2':d=${totalFrames}:s=${TARGET_W}x${TARGET_H}:fps=${fps}[zoomed]`,
      `[zoomed]${gradeFilter}[graded]`,
      `[graded]unsharp=5:5:0.4:5:5:0.0[final]`,
    ];

    await run(
      ffmpeg()
        .input(imgPath)
        .complexFilter(filterGraph)
        .outputOptions([
          "-map", "[final]",
          "-t", String(segDuration),
          "-c:v", V_ENCODER,
          "-r", String(fps),
          "-preset", "veryfast",
          "-crf", "18",
          "-pix_fmt", "yuv420p",
          "-an",
        ])
        .output(segOutPath)
    );
    segmentPaths.push(segOutPath);
  }

  // Concatenate segments
  const sceneVideoPath = path.join(path.dirname(outPath), `scenevid_${sceneIdx}.mp4`);
  if (segmentPaths.length === 1) {
    fs.copyFileSync(segmentPaths[0], sceneVideoPath);
  } else {
    const listPath = path.join(path.dirname(outPath), `seglist_${sceneIdx}.txt`);
    const listContent = segmentPaths.map((p) => `file '${p.replace(/'/g, "'\\''")}'`).join("\n");
    await fsp.writeFile(listPath, listContent);
    await run(
      ffmpeg()
        .input(listPath)
        .inputOptions(["-f", "concat", "-safe", "0"])
        .outputOptions(["-c", "copy"])
        .output(sceneVideoPath)
    );
  }

  // Mux with audio
  await runAudioMux((normalize) => {
    const opts = ["-map", "0:v", "-map", "1:a", "-t", String(duration)];
    if (normalize) opts.push("-af", "loudnorm=I=-16:TP=-1.5:LRA=11");
    opts.push("-c:v", "copy", "-c:a", "aac", "-b:a", "192k");
    return ffmpeg()
      .input(sceneVideoPath)
      .input(audioPath)
      .outputOptions(opts)
      .output(outPath);
  });

  return outPath;
}

// ---------------------------------------------------------------------------
// Template Scene Processing (Remotion — studio motion graphics)
// ---------------------------------------------------------------------------

// Best-effort card for when Remotion is unavailable (render crash / no output).
// A brand-dark background + the template's key text, muxed with the scene audio,
// so ONE template failure degrades gracefully instead of failing the whole video
// (the outro is a template too, so a Remotion break would otherwise kill every
// render). Text is heavily sanitised for a drawtext filter.
async function buildTemplateFallback(templateName, templateData, duration, audioPath, outPath) {
  let text;
  if (templateName === "stat_reveal") text = [templateData?.statValue, templateData?.label].filter(Boolean).join("  ");
  else if (templateName === "comparison") text = [templateData?.leftValue, "vs", templateData?.rightValue].filter(Boolean).join("  ");
  else text = templateData?.line || "";
  const safe = String(text || "").replace(/[\\:'%{}\r\n]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 42) || " ";
  const draw = `drawtext=font=Inter:text='${safe}':fontcolor=0xF0D070:fontsize=84:x=(w-text_w)/2:y=(h-text_h)/2:borderw=4:bordercolor=0x000000`;
  await runAudioMux((normalize) => {
    const opts = ["-map", "[v]", "-map", "1:a", "-t", String(duration)];
    if (normalize) opts.push("-af", "loudnorm=I=-16:TP=-1.5:LRA=11");
    opts.push("-c:v", V_ENCODER, "-r", String(FPS), "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k");
    return ffmpeg()
      .input(`color=c=0x0A0E1A:s=${TARGET_W}x${TARGET_H}:r=${FPS}`)
      .inputOptions(["-f", "lavfi", "-t", String(duration + 0.2)])
      .input(audioPath)
      .complexFilter([`[0:v]${draw}[v]`])
      .outputOptions(opts)
      .output(outPath);
  });
  return outPath;
}

async function buildTemplateScene(templateName, templateData, duration, audioPath, outPath, tmpDir, mood) {
  // Map template names to Remotion composition IDs
  const compositionMap = {
    stat_reveal: "StatReveal",
    comparison: "Comparison",
    kinetic_text: "KineticText",
  };

  const compositionId = compositionMap[templateName];
  if (!compositionId) {
    throw new Error(`Unknown template_name "${templateName}"`);
  }

  // Build props for the Remotion composition
  let props = { mood: mood || "neutral" };
  if (templateName === "stat_reveal") {
    props.statValue = templateData?.statValue || "";
    props.label = templateData?.label || "";
    props.icon = templateData?.icon || "activity";
  } else if (templateName === "comparison") {
    props.leftLabel = templateData?.leftLabel || "";
    props.leftValue = templateData?.leftValue || "";
    props.rightLabel = templateData?.rightLabel || "";
    props.rightValue = templateData?.rightValue || "";
  } else if (templateName === "kinetic_text") {
    props.line = templateData?.line || "";
  }

  // Render template via Remotion. If it produces nothing (exited without writing
  // the file, or a render crash), fall back to a simple card so one template
  // failure never kills the whole video.
  const templateVideoPath = path.join(tmpDir, `remotion_${templateName}_${Date.now()}.mp4`);
  try {
    await renderRemotion(compositionId, templateVideoPath, duration, props);
    if (!fs.existsSync(templateVideoPath) || fs.statSync(templateVideoPath).size < 1024) {
      throw new Error("Remotion exited without a valid output file (check [remotion stderr] in the compose logs)");
    }
  } catch (e) {
    console.warn(`[template] ${templateName} render failed (${e.message}) - using text-card fallback`);
    return await buildTemplateFallback(templateName, templateData, duration, audioPath, outPath);
  }

  // Mux Remotion video with audio
  const templateDuration = await ffprobeDuration(templateVideoPath);
  const videoFilter = templateDuration < duration
    ? `[0:v]tpad=stop_mode=clone:stop_duration=${(duration - templateDuration + 0.1).toFixed(3)}[padded]`
    : `[0:v]null[padded]`;

  await runAudioMux((normalize) => {
    const opts = ["-map", "[padded]", "-map", "1:a", "-t", String(duration)];
    if (normalize) opts.push("-af", "loudnorm=I=-16:TP=-1.5:LRA=11");
    opts.push(
      "-c:v", V_ENCODER,
      "-r", String(FPS),
      "-preset", "veryfast",
      "-crf", "18",
      "-pix_fmt", "yuv420p",
      "-c:a", "aac",
      "-b:a", "192k",
    );
    return ffmpeg()
      .input(templateVideoPath)
      .input(audioPath)
      .complexFilter([videoFilter])
      .outputOptions(opts)
      .output(outPath);
  });

  return outPath;
}

// ---------------------------------------------------------------------------
// Karaoke ASS Caption Builder (burn-in via ffmpeg — no transparency issues)
// ---------------------------------------------------------------------------

function toAssTime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = (sec % 60).toFixed(2);
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(5, "0")}`;
}

function buildAssFromAlignment(scenes, offsets, commentHook, totalDuration) {
  const header = `[Script Info]
ScriptType: v4.00+
PlayResX: ${TARGET_W}
PlayResY: ${TARGET_H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Inter Bold,64,&H00FFFFFF,&H000000FF,&H40000000,&H80000000,0,0,0,0,100,100,0,0,1,3,4,2,60,60,420,1
Style: CaptionHL,Inter Bold,68,&H0096E0FF,&H000000FF,&H40000000,&H80000000,0,0,0,0,105,105,0,0,1,3,4,2,60,60,420,1
Style: CaptionKey,Inter Bold,68,&H0080FF60,&H000000FF,&H40000000,&H80000000,0,0,0,0,105,105,0,0,1,3,4,2,60,60,420,1
Style: CommentHook,Inter Bold,54,&H00FFFFFF,&H000000FF,&H40202020,&HC0000000,0,0,0,0,100,100,0,0,3,0,4,2,80,80,680,1
Style: Wordmark,Inter Bold,36,&H6046BEFF,&H000000FF,&HB0000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,40,40,55,1
Style: WordmarkHandle,Inter Bold,24,&H8046BEFF,&H000000FF,&HB0000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,40,40,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

  // Persistent channel wordmark + handle (top, gold, semi-transparent) on every
  // frame - brand recognition without delaying the hook. Spans the whole video.
  const wm = String(CHANNEL_WORDMARK).replace(/[{}]/g, "");
  const handle = String(CHANNEL_HANDLE).replace(/[{}]/g, "");
  let events = `Dialogue: 0,${toAssTime(0)},${toAssTime(totalDuration)},Wordmark,,0,0,0,,${wm}\n`;
  events += `Dialogue: 0,${toAssTime(0)},${toAssTime(totalDuration)},WordmarkHandle,,0,0,0,,${handle}\n`;
  const WORDS_PER_CHUNK = 2;

  scenes.forEach((scene, sceneIdx) => {
    // The outro is a branded KineticText card (Like / Share / Follow) with a
    // spoken share line - let the template + voice carry it, no burned captions.
    if (scene?.template_data?.is_outro) return;
    const alignment = scene?.audio?.alignment;
    if (!alignment) return;

    const chars = alignment.characters;
    const starts = alignment.character_start_times_seconds;
    const ends = alignment.character_end_times_seconds;

    const words = [];
    let current = "";
    let wordStart = null;
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i];
      if (ch === " " || ch === "\n") {
        if (current) {
          words.push({ text: current, start: wordStart, end: ends[i - 1] });
          current = "";
          wordStart = null;
        }
        continue;
      }
      if (wordStart === null) wordStart = starts[i];
      current += ch;
    }
    if (current) words.push({ text: current, start: wordStart, end: ends[ends.length - 1] });

    for (let i = 0; i < words.length; i += WORDS_PER_CHUNK) {
      const chunk = words.slice(i, i + WORDS_PER_CHUNK);
      if (!chunk.length || chunk[0].start == null) continue;

      chunk.forEach((w) => {
        const wStart = w.start + offsets[sceneIdx];
        const wEnd = w.end + offsets[sceneIdx];
        const highlightText = chunk
          .map((item) => {
            if (item === w) {
              // Active word "pops": snaps to 128% then settles to the style's
              // 105% over ~100ms as it's spoken - a karaoke bounce that makes
              // the captions feel alive and draws the eye to the current word.
              // A word containing a number (the key fact in this niche) pops in
              // a distinct green so stats stand out from ordinary highlights.
              const hlStyle = /\d/.test(item.text) ? "CaptionKey" : "CaptionHL";
              return `{\\r${hlStyle}\\fscx128\\fscy128\\t(0,100,\\fscx105\\fscy105)}${item.text}{\\r}`;
            }
            return item.text;
          })
          .join(" ");

        events += `Dialogue: 0,${toAssTime(wStart)},${toAssTime(wEnd)},Caption,,0,0,0,,${highlightText}\n`;
      });
    }
  });

  if (commentHook && totalDuration) {
    // Surface the comment prompt at ~62% of the content - while retention is
    // still high - instead of the final seconds, when most viewers have
    // already swiped away. Hold it ~5s so it's readable, ending before the
    // kicker so it doesn't collide with the payoff.
    const hookStart = totalDuration * 0.62;
    const hookEnd = Math.min(totalDuration, hookStart + 5);
    const escaped = commentHook.replace(/\\/g, "").replace(/\{/g, "").replace(/\}/g, "");
    events += `Dialogue: 1,${toAssTime(hookStart)},${toAssTime(hookEnd)},CommentHook,,0,0,0,,{\\fscx0\\fscy0\\t(0,200,\\fscx120\\fscy120)\\t(200,300,\\fscx100\\fscy100)}${escaped}\n`;
  }

  return header + events;
}

// ---------------------------------------------------------------------------
// Caption Overlay via Remotion (kept for future use with VP9/RGBA output)
// ---------------------------------------------------------------------------

function extractWordsFromAlignment(scenes, offsets) {
  const allWords = [];

  scenes.forEach((scene, sceneIdx) => {
    const alignment = scene?.audio?.alignment;
    if (!alignment) return;

    const chars = alignment.characters;
    const starts = alignment.character_start_times_seconds;
    const ends = alignment.character_end_times_seconds;

    let current = "";
    let wordStart = null;
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i];
      if (ch === " " || ch === "\n") {
        if (current) {
          allWords.push({
            text: current,
            start: wordStart + offsets[sceneIdx],
            end: ends[i - 1] + offsets[sceneIdx],
          });
          current = "";
          wordStart = null;
        }
        continue;
      }
      if (wordStart === null) wordStart = starts[i];
      current += ch;
    }
    if (current && wordStart !== null) {
      allWords.push({
        text: current,
        start: wordStart + offsets[sceneIdx],
        end: ends[ends.length - 1] + offsets[sceneIdx],
      });
    }
  });

  return allWords;
}

async function renderCaptionOverlay(scenes, offsets, commentHook, totalDuration, outPath) {
  const words = extractWordsFromAlignment(scenes, offsets);
  const props = { words, commentHook: commentHook || "", totalDuration };

  await renderRemotion("CaptionOverlay", outPath, totalDuration, props);
  return outPath;
}

// ---------------------------------------------------------------------------
// Scene Concatenation (hard cuts - no crossfade)
// ---------------------------------------------------------------------------

async function concatScenes(scenePaths, outPath) {
  if (scenePaths.length === 1) {
    fs.copyFileSync(scenePaths[0], outPath);
    return outPath;
  }

  const listPath = path.join(path.dirname(outPath), "concat_scenes.txt");
  const listContent = scenePaths.map((p) => `file '${p.replace(/'/g, "'\\''")}'`).join("\n");
  await fsp.writeFile(listPath, listContent);

  await run(
    ffmpeg()
      .input(listPath)
      .inputOptions(["-f", "concat", "-safe", "0"])
      .outputOptions(["-c", "copy"])
      .output(outPath)
  );

  return outPath;
}

// Rebuild the voiceover as ONE gapless track. Each scene's audio was AAC-
// encoded separately and then copy-concatenated, which stacks every segment's
// ~23ms encoder-priming silence at the joins - an audible click/hiccup at each
// scene cut. Decoding the raw per-scene voice and re-joining them with the
// concat filter produces a clean, sample-accurate join (no click), and a
// single loudnorm pass over the whole track removes the per-scene level jumps
// the old per-scene loudnorm caused. Total duration is unchanged, so it stays
// in sync with the concatenated video.
async function buildGaplessVoice(audioPaths, outPath) {
  if (audioPaths.length === 1) {
    await runAudioMux((normalize) => {
      const opts = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"];
      const c = ffmpeg().input(audioPaths[0]);
      if (normalize) c.audioFilters("loudnorm=I=-16:TP=-1.5:LRA=11");
      return c.outputOptions(opts).output(outPath);
    });
    return outPath;
  }
  await runAudioMux((normalize) => {
    const cmd = ffmpeg();
    audioPaths.forEach((p) => cmd.input(p));
    // Normalize each input to a common format before concat (defensive), then
    // join sample-accurately; optionally a single loudnorm over the whole thing.
    const norm = audioPaths.map((_, i) => `[${i}:a]aformat=sample_rates=44100:channel_layouts=mono[a${i}]`).join(";");
    const ins = audioPaths.map((_, i) => `[a${i}]`).join("");
    const tail = normalize
      ? `concat=n=${audioPaths.length}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11[a]`
      : `concat=n=${audioPaths.length}:v=0:a=1[a]`;
    return cmd
      .complexFilter([`${norm};${ins}${tail}`])
      .outputOptions(["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"])
      .output(outPath);
  });
  return outPath;
}

// ---------------------------------------------------------------------------
// Main Compose Pipeline
// ---------------------------------------------------------------------------

async function runComposeJob(reqBody, jobId, tmpDir) {
  try {
    const { hook, caption_style, comment_hook, data: scenes } = reqBody;
    if (!Array.isArray(scenes) || scenes.length === 0) {
      throw new Error("No scenes provided");
    }

    const mood = caption_style || "neutral";

    // Outro card. The pipeline now injects a SPOKEN outro as the final scene -
    // a real narrated share CTA, voiced in the same voice as the narration and
    // flagged template_data.is_outro. When that scene is present we use it as-is
    // (voice + branded KineticText card). Only when it is absent (legacy payload
    // or fallback) do we append the old SILENT branded card, so a render never
    // ends without an outro.
    const hasScriptOutro = scenes.some((s) => s?.template_data?.is_outro);
    if (!hasScriptOutro) {
      const outroAudioBase64 = await generateSilentAudioBase64(OUTRO_DURATION_SEC);
      scenes.push({
        scene_index: scenes.length,
        visual_source: "template",
        template_name: "kinetic_text",
        template_data: { line: reqBody.outro_line || DEFAULT_OUTRO_LINE, is_outro: true },
        audio: { audio_base64: outroAudioBase64 },
      });
    }

    // The payoff/reveal scene = the last content scene before the outro card.
    // It gets a stronger emphasis push-in (video) and a riser+impact accent.
    const emphasisIdx = scenes.length - 2;

    console.log(`[job ${jobId}] Composing ${scenes.length} scenes (mood: ${mood})`);

    // ===== PHASE 1: Build individual scenes in parallel =====
    // Use allSettled, not all: if one scene throws, all() rejects
    // immediately while sibling scenes' ffmpeg processes keep running - and
    // the job's finally block then deletes tmpDir out from under them,
    // producing confusing "No such file" errors and orphaned processes.
    // Wait for every scene to settle first, then fail with a clear message.
    const settled = await Promise.allSettled(
      scenes.map(async (scene, i) => {
        const audioPath = path.join(tmpDir, `voice_${i}.mp3`);
        if (scene?.audio?.audio_base64) {
          await writeBase64(scene.audio.audio_base64, audioPath);
        } else if (scene?.audio?.audio_url) {
          await downloadFile(scene.audio.audio_url, audioPath);
        } else {
          throw new Error(`Scene ${i} missing audio`);
        }

        const duration = await ffprobeDuration(audioPath);
        const outPath = path.join(tmpDir, `scene_${i}_final.mp4`);

        const isTemplate = scene?.visual_source === "template";
        const isStockVideo = !isTemplate && !!scene?.video_url;

        if (isTemplate) {
          if (!scene.template_name) throw new Error(`Scene ${i}: visual_source=template but no template_name`);
          await buildTemplateScene(scene.template_name, scene.template_data, duration, audioPath, outPath, tmpDir, mood);
        } else if (isStockVideo) {
          const stockVideoPath = path.join(tmpDir, `stock_${i}.mp4`);
          await downloadFile(scene.video_url, stockVideoPath);
          await buildStockVideoScene(stockVideoPath, audioPath, duration, outPath, i, mood);
        } else {
          const imageUrls = scene?.images;
          if (!Array.isArray(imageUrls) || !imageUrls.length) {
            throw new Error(`Scene ${i}: missing images array (and no video_url)`);
          }
          const imagePaths = await Promise.all(
            imageUrls.map(async (url, j) => {
              const p = path.join(tmpDir, `scene_${i}_img_${j}.png`);
              return await downloadFile(url, p);
            })
          );

          // Real photo scenes get an eased Ken-Burns move (real stock VIDEO
          // scenes are handled above via buildStockVideoScene). No AI animation.
          await buildImageScene(imagePaths, audioPath, duration, outPath, i, mood, i === emphasisIdx);
        }

        return { path: outPath, duration };
      })
    );

    const failures = settled
      .map((r, i) => (r.status === "rejected" ? `scene ${i}: ${r.reason?.message || r.reason}` : null))
      .filter(Boolean);
    if (failures.length) {
      throw new Error(`Scene build failed - ${failures.join("; ")}`);
    }

    const sceneResults = settled.map((r) => r.value);
    const scenePaths = sceneResults.map((r) => r.path);
    const durations = sceneResults.map((r) => r.duration);

    // ===== PHASE 2: Concatenate scenes (hard cuts) =====
    const concatPath = path.join(tmpDir, "concat.mp4");
    await concatScenes(scenePaths, concatPath);

    // Calculate actual scene offsets (no overlap - hard cuts)
    const offsets = [0];
    for (let i = 1; i < durations.length; i++) {
      offsets.push(offsets[i - 1] + durations[i - 1]);
    }
    const totalVideoDuration = durations.reduce((a, b) => a + b, 0);

    // ===== PHASE 3: Burn-in captions via ASS (proven approach) =====
    // Remotion caption overlay requires VP9/RGBA to preserve transparency,
    // which adds complexity. ASS captions burned in by ffmpeg are reliable,
    // fast, and visually good enough with proper styling.
    const assPath = path.join(tmpDir, "captions.ass");
    // comment_hook should land in the last moments of the actual content, not
    // over the outro card. The outro is now a variable-length SPOKEN scene, so
    // exclude the actual last-scene duration (not the old fixed 2.5s constant).
    const outroDuration = durations.length > 1 ? durations[durations.length - 1] : 0;
    const contentDuration = totalVideoDuration - outroDuration;
    const assContent = buildAssFromAlignment(scenes, offsets, comment_hook, contentDuration);
    await fsp.writeFile(assPath, assContent);

    // ===== PHASE 4: Sound design + Audio mixing + Final composite =====
    const musicPath = pickMusicTrack(mood);
    const sfxDir = path.join(MOTION_ASSETS_DIR, "sfx");
    const sfxFiles = {
      whoosh: path.join(sfxDir, "whoosh.wav"),
      impact: path.join(sfxDir, "impact.wav"),
      riser: path.join(sfxDir, "riser.wav"),
    };
    const sfxAvailable = {
      whoosh: fs.existsSync(sfxFiles.whoosh),
      impact: fs.existsSync(sfxFiles.impact),
      riser: fs.existsSync(sfxFiles.riser),
    };

    // Sound design: NO per-cut SFX (that whoosh-on-every-cut clashed with the
    // tone). Instead, ONE tasteful accent at the payoff/reveal - a soft riser
    // building into it, then a gentle impact as it lands. This is the single
    // intentional audio beat that punctuates the climax without the noise.
    const sfxEvents = [];
    const emphasisOffset = offsets[emphasisIdx];
    if (emphasisIdx >= 1 && emphasisOffset != null) {
      if (sfxAvailable.riser) sfxEvents.push({ type: "riser", time: Math.max(0, emphasisOffset - 1.3), volume: 0.18 });
      if (sfxAvailable.impact) sfxEvents.push({ type: "impact", time: emphasisOffset, volume: 0.22 });
    }

    // Gapless voiceover: rejoin the per-scene voice cleanly (no boundary
    // clicks) with a single loudnorm (consistent levels), used as the voice
    // track below instead of the concatenated video's gappy audio.
    const voicePath = path.join(tmpDir, "voice_full.m4a");
    await buildGaplessVoice(
      scenes.map((_, i) => path.join(tmpDir, `voice_${i}.mp3`)),
      voicePath
    );

    // ===== PHASE 5: Final composite — video + captions + logo + music + SFX =====
    const finalPath = path.join(tmpDir, "final.mp4");
    const outputFileName = `short_${jobId}.mp4`;
    const outputFullPath = path.join(OUTPUT_DIR, outputFileName);
    await fsp.mkdir(OUTPUT_DIR, { recursive: true });

    const hasMusic = fs.existsSync(musicPath);
    const hasLogo = fs.existsSync(CHANNEL_LOGO_PATH);
    const hasAss = fs.existsSync(assPath);
    const safeAssPath = assPath.replace(/\\/g, "/").replace(/:/g, "\\:");

    const finalCmd = ffmpeg().input(concatPath);   // [0] = video (+ ignored audio)
    finalCmd.input(voicePath);                     // [1] = gapless voiceover
    if (hasMusic) finalCmd.input(musicPath);
    if (hasLogo) finalCmd.input(CHANNEL_LOGO_PATH);
    sfxEvents.forEach((ev) => finalCmd.input(sfxFiles[ev.type]));

    // Track input indices ([0]=video, [1]=voice already taken)
    let nextIdx = 2;
    const musicIdx = hasMusic ? nextIdx++ : null;
    const logoIdx = hasLogo ? nextIdx++ : null;
    const sfxIndices = sfxEvents.map(() => nextIdx++);

    // Build video filter chain: ASS caption burn-in -> logo overlay (if
    // present) -> fade in/out. Each stage is optional and no-ops cleanly when
    // its input is absent, so behavior is unchanged when there's no logo file.
    const fadeOutStart = Math.max(0, totalVideoDuration - 0.5);
    const videoFilterSegments = [];
    let vLabel = "0:v";
    if (hasAss) {
      videoFilterSegments.push(`[${vLabel}]ass=${safeAssPath}[v_cc]`);
      vLabel = "v_cc";
    }
    if (hasLogo) {
      // Corner watermark (full gold badge - ring, bulb, wordmark, handle),
      // top-right, clear of the top-center wordmark and the bottom caption
      // safe zone. 130px (up from a bare-icon-sized 100px) since this badge
      // carries fine text/ring detail that needs a bit more room to read.
      videoFilterSegments.push(`[${logoIdx}:v]scale=130:-1,format=rgba[logo_scaled]`);
      videoFilterSegments.push(`[${vLabel}][logo_scaled]overlay=x=W-w-40:y=40[v_logo]`);
      vLabel = "v_logo";
    }
    videoFilterSegments.push(
      `[${vLabel}]fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5[final_v]`
    );

    // Build audio filter: gapless voice ([1:a]) + ducked music
    const audioFilters = [];
    const mixLabels = ["1:a"];

    if (hasMusic) {
      // Music is a quiet BED: low base level, and hard sidechain ducking so the
      // voiceover always sits clearly on top (voice [1:a] is the trigger).
      audioFilters.push(`[${musicIdx}:a]aloop=loop=-1:size=2e9,volume=0.13[music]`);
      audioFilters.push(`[music][1:a]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=250[duckedmusic]`);
      mixLabels.push("duckedmusic");
    }

    sfxEvents.forEach((ev, idx) => {
      const inputIdx = sfxIndices[idx];
      const ms = Math.round(ev.time * 1000);
      const label = `sfx${idx}`;
      audioFilters.push(`[${inputIdx}:a]volume=${ev.volume},adelay=${ms}|${ms}[${label}]`);
      mixLabels.push(label);
    });

    if (mixLabels.length > 1) {
      audioFilters.push(`[${mixLabels.join("][")}]amix=inputs=${mixLabels.length}:duration=first:normalize=0[final_a]`);
    }

    const allFilters = [...videoFilterSegments, ...audioFilters];
    finalCmd.complexFilter(allFilters);

    // Studio-grade final encoding:
    // - CRF 16 (higher quality source for YouTube's re-encode)
    // - Explicit BT.709 colorspace flags
    // - High profile for maximum quality at 1080p30
    finalCmd.outputOptions([
      "-map", "[final_v]",
      "-map", mixLabels.length > 1 ? "[final_a]" : "1:a",
      "-c:v", V_ENCODER,
      "-preset", "medium",
      "-crf", "16",
      "-profile:v", "high",
      "-level:v", "4.1",
      "-color_primaries", "bt709",
      "-color_trc", "bt709",
      "-colorspace", "bt709",
      "-pix_fmt", "yuv420p",
      "-c:a", "aac",
      "-b:a", "192k",
      "-ac", "2",
      "-shortest",
      "-movflags", "+faststart",
    ]);
    finalCmd.output(finalPath);
    await run(finalCmd);

    await fsp.copyFile(finalPath, outputFullPath);
    console.log(`[job ${jobId}] Done -> ${outputFullPath}`);
    return { success: true, output_path: outputFullPath, job_id: jobId };

  } catch (err) {
    console.error(`[job ${jobId}] FAILED:`, err);
    throw err;
  } finally {
    if (!process.env.DEBUG_KEEP_TMP) {
      await fsp.rm(tmpDir, { recursive: true, force: true }).catch(() => { });
    }
  }
}

// ---------------------------------------------------------------------------
// B-roll resolution: pick the best REAL asset for a scene from multiple free
// sources (Pexels photos/videos, Unsplash, Wikipedia), ranked by AI-vision
// relevance. Always returns a real asset { ok:true, type, url, source, ... }
// when any source has a match (no AI fallback); { ok:false } only if every
// source is empty/unreachable.
// ---------------------------------------------------------------------------
app.post("/resolve-broll", async (req, res) => {
  try {
    const { query, subject, description } = req.body || {};
    const result = await resolveBroll({ query, subject, description });
    res.json(result);
  } catch (e) {
    res.json({ ok: false, reason: "error", error: String((e && e.message) || e) });
  }
});

// Feedback loop's remaining route: /performance/log, /performance/ingest, and
// /channel-insights are registered once already, above (SHORTS-ONLY by
// construction: only videos this pipeline logs there are ever measured or
// strategized, so long-form videos on the same channel never enter the loop).
// This one has no earlier registration, so it lives here.
app.get("/performance/measure-ids", async (req, res) => {
  try { res.json({ video_ids: await feedback.getMeasureIds() }); }
  catch (e) { res.status(500).json({ video_ids: [], error: String((e && e.message) || e) }); }
});

// Topic intelligence store: every run generates many scored candidates and
// publishes the top one; we persist the whole ranked list (+ which was picked)
// so scored candidates accumulate. Joined later with the feedback loop's
// measured outcomes, this becomes a {predicted score -> actual performance}
// dataset - the foundation for real topic scoring once there's enough data.
app.post("/topic-backlog", async (req, res) => {
  try {
    const bp = path.join(path.dirname(TOPIC_HISTORY_PATH), "topic_candidates.json");
    const { candidates, picked, picked_score } = req.body || {};
    let log = [];
    if (fs.existsSync(bp)) log = JSON.parse(await fsp.readFile(bp, "utf8"));
    log.push({
      created_at: new Date().toISOString(),
      picked: picked || null,
      picked_score: picked_score != null ? picked_score : null,
      candidates: Array.isArray(candidates) ? candidates : [],
    });
    if (log.length > 500) log = log.slice(log.length - 500);
    await fsp.mkdir(path.dirname(bp), { recursive: true });
    await fsp.writeFile(bp, JSON.stringify(log, null, 2));
    res.json({ success: true, runs: log.length });
  } catch (e) { res.status(500).json({ success: false, error: String((e && e.message) || e) }); }
});

// ---------------------------------------------------------------------------
// Async Job API
// ---------------------------------------------------------------------------

app.post("/compose", (req, res) => {
  const jobId = crypto.randomUUID();
  const tmpDir = newTmpDir();

  jobStore.set(jobId, { status: "processing", startedAt: Date.now() });

  runComposeJob(req.body, jobId, tmpDir)
    .then((result) => {
      jobStore.set(jobId, { status: "done", result, finishedAt: Date.now() });
    })
    .catch((err) => {
      jobStore.set(jobId, { status: "failed", error: err.message, finishedAt: Date.now() });
    });

  return res.status(202).json({ job_id: jobId, status: "processing" });
});

app.get("/compose-status/:jobId", (req, res) => {
  const job = jobStore.get(req.params.jobId);
  if (!job) {
    return res.status(404).json({ status: "not_found", error: `No job with id ${req.params.jobId}` });
  }
  if (job.status === "done") {
    // Terminal results remain readable for 24 hours; polling is idempotent.
    return res.json({ status: "done", success: true, ...job.result });
  }
  if (job.status === "failed") {
    // Terminal failures also survive retries and service restarts.
    return res.status(500).json({ status: "failed", success: false, error: job.error });
  }
  return res.json({ status: "processing" });
});

const PORT = process.env.PORT || 4000;
const server=app.listen(PORT, () => console.log(`Studio compose engine listening on :${PORT}`));
process.on('SIGTERM',()=>{
  beginDrain();
  const finish=()=>{if(![...jobStore.values()].some(j=>j.status==='processing')){clearInterval(timer);server.close(()=>process.exit(0));}};
  const timer=setInterval(finish,1000);finish();
});
