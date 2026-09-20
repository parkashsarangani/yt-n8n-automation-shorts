#!/usr/bin/env python3
"""Harden V4 video sampling/materialization against third-party CDN behavior.

Production previously passed remote Pexels/Pixabay URLs directly to FFmpeg. If
FFmpeg could not open those CDN URLs, every candidate failed before the paid
vision verifier ran, producing `video_contact_sheet_failed` with zero vision
calls. The same direct-URL dependency also existed after verification when the
selected trim was materialized. This transform stages bounded media through
axios first, then asks FFmpeg to decode a local file. Semantic thresholds and
proof requirements remain unchanged.
"""
from __future__ import annotations

from pathlib import Path

MARKER = "V4_VIDEO_SAMPLE_STAGING"

OLD_BLOCK = r'''// sampleVideoContactSheet: actual multi-frame verification, not thumbnail inference.
async function sampleVideoContactSheet(candidate) {
  const duration = Number(candidate.duration || 0); if (!duration || duration < 0.5) return null;
  const cols = 4, rows = Math.ceil(VIDEO_SAMPLE_FRAMES / cols), fps = Math.max(0.05, VIDEO_SAMPLE_FRAMES / duration);
  const tmp = path.join(os.tmpdir(), `broll-sheet-${crypto.randomUUID()}.jpg`);
  try {
    await runFfmpeg(["-hide_banner", "-loglevel", "error", "-y", "-i", candidate.sample_url || candidate.url, "-vf", `fps=${fps.toFixed(6)},scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2:color=black,tile=${cols}x${rows}:nb_frames=${VIDEO_SAMPLE_FRAMES}:padding=3:margin=3`, "-frames:v", "1", "-q:v", "3", tmp]);
    const buf = await fsp.readFile(tmp);
    return { imageUrl: `data:image/jpeg;base64,${buf.toString("base64")}`, timestamps: Array.from({ length: VIDEO_SAMPLE_FRAMES }, (_, i) => Number(Math.min(duration, (i + 0.5) * duration / VIDEO_SAMPLE_FRAMES).toFixed(3))) };
  } catch { return null; } finally { fsp.unlink(tmp).catch(() => {}); }
}
const buildVideoContactSheet = sampleVideoContactSheet;'''

NEW_BLOCK = r'''// V4_VIDEO_SAMPLE_STAGING: localize remote CDN video before FFmpeg decoding.
// This keeps CDN redirects/TLS/user-agent quirks out of the media verifier and
// selected-clip materializer and classifies failures before any paid vision call.
const VIDEO_SAMPLE_MAX_BYTES = Math.max(8 * 1024 * 1024, Number(process.env.BROLL_VIDEO_SAMPLE_MAX_BYTES || 96 * 1024 * 1024));

function compactSamplingError(err) {
  return String(err?.message || err || "unknown").replace(/\s+/g, " ").trim().slice(-500);
}

function classifySamplingFfmpegError(err) {
  const text = compactSamplingError(err);
  if (/timed out|ETIMEDOUT/i.test(text)) return "video_contact_sheet_timeout";
  if (/invalid data found|moov atom not found|could not find codec parameters/i.test(text)) return "video_contact_sheet_invalid_media";
  if (/no such filter|error initializing filter|failed to configure output pad/i.test(text)) return "video_contact_sheet_filter_failed";
  if (/unknown decoder|decoder .* not found/i.test(text)) return "video_contact_sheet_decoder_unavailable";
  return "video_contact_sheet_ffmpeg_failed";
}

async function downloadVideoInput(candidate, purpose = "sample") {
  const local = String(candidate?.local_path || "").trim();
  if (local && fs.existsSync(local)) return { ok: true, input: local, cleanup: false, sample_source: "local" };

  const rawUrls = purpose === "materialize" ? [candidate?.url] : [candidate?.sample_url, candidate?.url];
  const urls = [...new Set(rawUrls.map((v) => String(v || "").trim()).filter((v) => /^https?:\/\//i.test(v)))];
  const prefix = purpose === "materialize" ? "video_asset_download" : "video_sample_download";
  if (!urls.length) return { ok: false, reason: `${prefix}_url_invalid`, detail: "no http(s) video URL" };

  let last = null;
  for (const sourceUrl of urls) {
    const tmp = path.join(os.tmpdir(), `broll-source-${crypto.randomUUID()}.mp4`);
    try {
      const r = await axios.get(sourceUrl, {
        timeout: 20000,
        responseType: "arraybuffer",
        maxRedirects: 5,
        maxContentLength: VIDEO_SAMPLE_MAX_BYTES,
        maxBodyLength: VIDEO_SAMPLE_MAX_BYTES,
        headers: {
          "User-Agent": "Mozilla/5.0 (compatible; FavouriteFactsBot/1.0; +https://youtube.com)",
          Accept: "video/*,application/octet-stream;q=0.9,*/*;q=0.1",
        },
      });
      const buf = Buffer.from(r.data || []);
      if (buf.length < 1024) throw new Error(`${prefix}_empty`);
      if (buf.length > VIDEO_SAMPLE_MAX_BYTES) throw new Error(`${prefix}_too_large`);
      await fsp.writeFile(tmp, buf);
      return { ok: true, input: tmp, cleanup: true, sample_source: "download", sample_bytes: buf.length };
    } catch (err) {
      await fsp.unlink(tmp).catch(() => {});
      const status = Number(err?.response?.status || 0);
      const detail = compactSamplingError(err);
      const reason = status
        ? `${prefix}_http_${status}`
        : /maxContentLength|maxBodyLength|too_large/i.test(detail)
          ? `${prefix}_too_large`
          : /timeout|ETIMEDOUT|ECONNABORTED/i.test(detail)
            ? `${prefix}_timeout`
            : `${prefix}_failed`;
      last = { ok: false, reason, detail };
    }
  }
  return last || { ok: false, reason: `${prefix}_failed`, detail: "all video URLs failed" };
}

async function downloadVideoSample(candidate) {
  return downloadVideoInput(candidate, "sample");
}

// sampleVideoContactSheet: actual multi-frame verification, never thumbnail inference.
async function sampleVideoContactSheet(candidate) {
  const duration = Number(candidate?.duration || 0);
  if (!duration || duration < 0.5) return { ok: false, reason: "video_duration_invalid", detail: `duration=${candidate?.duration ?? "missing"}` };

  const staged = await downloadVideoSample(candidate);
  if (!staged.ok) return staged;

  const cols = 4, rows = Math.ceil(VIDEO_SAMPLE_FRAMES / cols), fps = Math.max(0.05, VIDEO_SAMPLE_FRAMES / duration);
  const tmp = path.join(os.tmpdir(), `broll-sheet-${crypto.randomUUID()}.png`);
  try {
    await runFfmpeg(["-hide_banner", "-loglevel", "error", "-y", "-i", staged.input, "-vf", `fps=${fps.toFixed(6)},scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2:color=black,tile=${cols}x${rows}:nb_frames=${VIDEO_SAMPLE_FRAMES}:padding=3:margin=3`, "-frames:v", "1", tmp]);
    const buf = await fsp.readFile(tmp);
    if (buf.length < 1024) return { ok: false, reason: "video_contact_sheet_empty", detail: `bytes=${buf.length}`, sample_source: staged.sample_source };
    return {
      ok: true,
      imageUrl: `data:image/png;base64,${buf.toString("base64")}`,
      timestamps: Array.from({ length: VIDEO_SAMPLE_FRAMES }, (_, i) => Number(Math.min(duration, (i + 0.5) * duration / VIDEO_SAMPLE_FRAMES).toFixed(3))),
      sample_source: staged.sample_source,
      sample_bytes: staged.sample_bytes || null,
    };
  } catch (err) {
    return { ok: false, reason: classifySamplingFfmpegError(err), detail: compactSamplingError(err), sample_source: staged.sample_source };
  } finally {
    await fsp.unlink(tmp).catch(() => {});
    if (staged.cleanup) await fsp.unlink(staged.input).catch(() => {});
  }
}
const buildVideoContactSheet = sampleVideoContactSheet;'''

OLD_VERIFY = '  const sheet = await sampleVideoContactSheet(candidate); if (!sheet) return { ok: false, reason: "video_contact_sheet_failed" };'
NEW_VERIFY = '  const sheet = await sampleVideoContactSheet(candidate); if (!sheet?.ok) return { ok: false, reason: sheet?.reason || "video_contact_sheet_failed", failure_detail: sheet?.detail || null, sample_source: sheet?.sample_source || null };'

OLD_MATERIALIZE = r'''async function materializeVerifiedClip(candidate, verification) {
  await cleanupVerifiedCache(); await fsp.mkdir(VERIFIED_CACHE_DIR, { recursive: true });
  const start = Number(verification.verified_start_sec || 0), end = Number(verification.verified_end_sec || 0), duration = Math.max(0.05, end - start), key = crypto.createHash("sha256").update(`${candidate.url}|${start}|${end}`).digest("hex").slice(0, 24), filename = `verified_${key}.mp4`, dest = path.join(VERIFIED_CACHE_DIR, filename);
  if (!fs.existsSync(dest) || fs.statSync(dest).size < 4096) { const tmp = `${dest}.tmp-${process.pid}-${Date.now()}.mp4`; await runFfmpeg(["-hide_banner", "-loglevel", "error", "-y", "-ss", String(start), "-i", candidate.url, "-t", String(duration), "-an", "-vf", "scale='min(1080,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], 120000); await fsp.rename(tmp, dest); }
  return { url: `${VERIFIED_PUBLIC_BASE}/broll-cache/${filename}`, local_path: dest, source_url: candidate.url, in_point_sec: start, out_point_sec: end, verified_duration_sec: Number(duration.toFixed(3)) };
}'''

NEW_MATERIALIZE = r'''async function materializeVerifiedClip(candidate, verification) {
  await cleanupVerifiedCache(); await fsp.mkdir(VERIFIED_CACHE_DIR, { recursive: true });
  const start = Number(verification.verified_start_sec || 0), end = Number(verification.verified_end_sec || 0), duration = Math.max(0.05, end - start), key = crypto.createHash("sha256").update(`${candidate.url}|${start}|${end}`).digest("hex").slice(0, 24), filename = `verified_${key}.mp4`, dest = path.join(VERIFIED_CACHE_DIR, filename);
  if (!fs.existsSync(dest) || fs.statSync(dest).size < 4096) {
    const staged = await downloadVideoInput(candidate, "materialize");
    if (!staged.ok) throw new Error(`${staged.reason}: ${staged.detail || "video staging failed"}`);
    const tmp = `${dest}.tmp-${process.pid}-${Date.now()}.mp4`;
    try {
      await runFfmpeg(["-hide_banner", "-loglevel", "error", "-y", "-ss", String(start), "-i", staged.input, "-t", String(duration), "-an", "-vf", "scale='min(1080,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], 120000);
      await fsp.rename(tmp, dest);
    } finally {
      await fsp.unlink(tmp).catch(() => {});
      if (staged.cleanup) await fsp.unlink(staged.input).catch(() => {});
    }
  }
  return { url: `${VERIFIED_PUBLIC_BASE}/broll-cache/${filename}`, local_path: dest, source_url: candidate.url, in_point_sec: start, out_point_sec: end, verified_duration_sec: Number(duration.toFixed(3)) };
}'''


def patch_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    for anchor, label in [
        (OLD_BLOCK, "V4 video contact-sheet block"),
        (OLD_VERIFY, "V4 video verifier contact-sheet"),
        (OLD_MATERIALIZE, "V4 verified clip materializer"),
    ]:
        if anchor not in text:
            raise RuntimeError(f"{label} anchor missing")
    text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    text = text.replace(OLD_VERIFY, NEW_VERIFY, 1)
    text = text.replace(OLD_MATERIALIZE, NEW_MATERIALIZE, 1)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("usage: v4_video_sampling_reliability.py BROLL_RESOLVER_JS")
    patch_file(Path(sys.argv[1]))
