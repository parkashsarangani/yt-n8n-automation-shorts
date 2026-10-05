// TECHNICAL_QA_V1: deterministic checks on the finished Short before upload,
// from one ffmpeg pass (stream info, blackdetect, freezedetect, EBU R128).
//
// Only a video that is unusable as published is FATAL - no video or audio
// stream, wrong size, an absurd duration, a black opening frame, or mostly
// black. Everything else (frame rate drift, frozen stretches, loudness or
// true-peak outside target) is a WARNING that is logged with the render.
// The AI visual review stays separate and never blocks.

const { execFile } = require("child_process");
const ffmpegPath = require("ffmpeg-static");

const TARGET = { width: 1080, height: 1920, fps: 30 };
const LIMITS = {
  minDuration: 8,
  maxDuration: 75,
  openingBlackSec: 0.3,   // black from 0s for at least this long = fatal
  maxBlackShare: 0.4,     // more than 40% black = fatal
  freezeWarnSec: 2.5,
  lufsMin: -18,
  lufsMax: -11,
  truePeakMax: -0.5,
};

function runFfmpeg(args, timeoutMs = 120000) {
  return new Promise((resolve) => {
    execFile(ffmpegPath, args, { maxBuffer: 32 * 1024 * 1024, timeout: timeoutMs }, (err, stdout, stderr) => {
      resolve({ error: err, log: String(stderr || "") });
    });
  });
}

// Pure parser over ffmpeg's stderr, so it can be tested without rendering.
function parseLog(log) {
  const num = (v) => (v == null ? null : Number(v));
  const durationMatch = log.match(/Duration: (\d+):(\d+):(\d+(?:\.\d+)?)/);
  const duration = durationMatch ? Number(durationMatch[1]) * 3600 + Number(durationMatch[2]) * 60 + Number(durationMatch[3]) : null;
  const videoLine = (log.match(/Stream #\d+:\d+[^\n]*Video:[^\n]*/) || [])[0] || "";
  const size = videoLine.match(/, (\d{2,5})x(\d{2,5})[ ,\[]/);
  const fps = videoLine.match(/([\d.]+) fps/);
  const black = [...log.matchAll(/black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)/g)]
    .map((m) => ({ start: Number(m[1]), end: Number(m[2]), duration: Number(m[3]) }));
  const freezeStarts = [...log.matchAll(/freeze_start:\s*([\d.]+)/g)].map((m) => Number(m[1]));
  const freezeDurations = [...log.matchAll(/freeze_duration:\s*([\d.]+)/g)].map((m) => Number(m[1]));
  const freezes = freezeDurations.map((d, i) => ({ start: freezeStarts[i] ?? null, duration: d }));
  const summary = log.slice(log.lastIndexOf("Summary:"));
  const lufs = summary.includes("Summary:") ? (summary.match(/I:\s*(-?[\d.]+) LUFS/) || [])[1] : null;
  const peak = summary.includes("Summary:") ? (summary.match(/Peak:\s*(-?[\d.]+|-inf) dBFS/) || [])[1] : null;
  return {
    duration,
    hasVideo: Boolean(videoLine),
    hasAudio: /Stream #\d+:\d+[^\n]*Audio:/.test(log),
    width: size ? num(size[1]) : null,
    height: size ? num(size[2]) : null,
    fps: fps ? num(fps[1]) : null,
    black,
    freezes,
    integratedLufs: lufs == null ? null : Number(lufs),
    truePeakDb: peak == null ? null : peak === "-inf" ? -Infinity : Number(peak),
  };
}

function evaluate(m) {
  const fatal = [];
  const warnings = [];
  if (!m.hasVideo) fatal.push("no video stream");
  if (!m.hasAudio) fatal.push("no audio stream");
  if (m.hasVideo && (m.width !== TARGET.width || m.height !== TARGET.height)) fatal.push(`resolution ${m.width}x${m.height}, expected ${TARGET.width}x${TARGET.height}`);
  if (m.duration == null || m.duration < LIMITS.minDuration || m.duration > LIMITS.maxDuration) fatal.push(`duration ${m.duration}s outside ${LIMITS.minDuration}-${LIMITS.maxDuration}s`);
  const opening = m.black.find((b) => b.start <= 0.05 && b.duration >= LIMITS.openingBlackSec);
  if (opening) fatal.push(`black opening frame (${opening.duration.toFixed(2)}s from 0s)`);
  const blackTotal = m.black.reduce((a, b) => a + b.duration, 0);
  if (m.duration && blackTotal / m.duration > LIMITS.maxBlackShare) fatal.push(`${Math.round((blackTotal / m.duration) * 100)}% of the video is black`);
  if (m.fps != null && Math.abs(m.fps - TARGET.fps) > 0.5) warnings.push(`frame rate ${m.fps}, expected ${TARGET.fps}`);
  for (const f of m.freezes) if (f.duration >= LIMITS.freezeWarnSec) warnings.push(`frozen picture for ${f.duration.toFixed(1)}s at ${f.start ?? "?"}s`);
  if (m.integratedLufs != null && (m.integratedLufs < LIMITS.lufsMin || m.integratedLufs > LIMITS.lufsMax)) warnings.push(`loudness ${m.integratedLufs} LUFS outside ${LIMITS.lufsMin}..${LIMITS.lufsMax}`);
  if (m.truePeakDb != null && m.truePeakDb > LIMITS.truePeakMax) warnings.push(`true peak ${m.truePeakDb} dBFS above ${LIMITS.truePeakMax}`);
  return { passed: fatal.length === 0, fatal, warnings };
}

async function checkFinalVideo(videoPath) {
  const { log } = await runFfmpeg([
    "-hide_banner", "-nostats", "-i", videoPath,
    "-vf", "blackdetect=d=0.25:pix_th=0.08,freezedetect=n=0.001:d=2",
    "-af", "ebur128=peak=true",
    "-f", "null", "-",
  ]);
  const metrics = parseLog(log);
  // If ffmpeg could not even read the file, that is itself fatal.
  if (!metrics.hasVideo && !metrics.hasAudio && metrics.duration == null) {
    return { policy: "technical-qa-v1", passed: false, fatal: ["final video unreadable"], warnings: [], metrics };
  }
  return { policy: "technical-qa-v1", ...evaluate(metrics), metrics };
}

module.exports = { checkFinalVideo, parseLog, evaluate, TARGET, LIMITS };
