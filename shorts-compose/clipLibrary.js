// Persistent proven-asset library. JSON storage avoids a native SQLite module
// while still giving the resolver cross-run memory on the existing data volume.
// Verified media can be re-materialized from the original source after the
// transient b-roll cache expires, so accepted visual knowledge is durable.
const axios = require("axios");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { execFile } = require("child_process");
const ffmpegPath = require("ffmpeg-static");

const LIBRARY_PATH = process.env.BROLL_LIBRARY_PATH || "/app/data/broll_library.json";
const OUTPUT_DIR = process.env.OUTPUT_DIR || "/outputs";
const MAX_ROWS = Math.max(100, Number(process.env.BROLL_LIBRARY_MAX || 2500));
const RECENT_WINDOW = Math.max(1, Number(process.env.BROLL_RECENT_WINDOW || 12));
const REMATERIALIZE_TIMEOUT_MS = Math.max(15000, Number(process.env.BROLL_LIBRARY_REMATERIALIZE_TIMEOUT_MS || 120000));
const REUSE_SEMANTIC_THRESHOLD = Math.max(88, Number(process.env.BROLL_LIBRARY_SEMANTIC_THRESHOLD || 88));
const REUSE_ENTITY_THRESHOLD = Math.max(0, Number(process.env.BROLL_ENTITY_THRESHOLD || 85));
const REUSE_ACTION_THRESHOLD = Math.max(0, Number(process.env.BROLL_ACTION_THRESHOLD || 80));
const REUSE_RELATIONSHIP_THRESHOLD = Math.max(0, Number(process.env.BROLL_RELATIONSHIP_THRESHOLD || 80));
let writeChain = Promise.resolve();

async function load() {
  try {
    const parsed = JSON.parse(await fsp.readFile(LIBRARY_PATH, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

async function save(rows) {
  const data = JSON.stringify(rows.slice(-MAX_ROWS), null, 2);
  writeChain = writeChain.then(async () => {
    await fsp.mkdir(path.dirname(LIBRARY_PATH), { recursive: true });
    const tmp = `${LIBRARY_PATH}.tmp-${process.pid}`;
    await fsp.writeFile(tmp, data);
    await fsp.rename(tmp, LIBRARY_PATH);
  }).catch(() => {});
  return writeChain;
}

function contractText(contract) {
  return [contract?.visual_claim, ...(contract?.required_entities || []), ...(contract?.required_actions || []), ...(contract?.required_relationships || [])].filter(Boolean).join(" ").toLowerCase();
}
function tokenSet(text) { return new Set(String(text || "").toLowerCase().match(/[a-z0-9][a-z0-9'-]{2,}/g) || []); }
function similarity(a, b) {
  const A = tokenSet(a), B = tokenSet(b); if (!A.size || !B.size) return 0;
  let common = 0; for (const x of A) if (B.has(x)) common += 1;
  return common / Math.sqrt(A.size * B.size);
}
function cachedPathFromUrl(url) {
  const marker = "/broll-cache/";
  const i = String(url || "").indexOf(marker);
  return i >= 0 ? path.join(OUTPUT_DIR, "broll-cache", path.basename(String(url).slice(i + marker.length))) : null;
}

function meetsCurrentContractGate(row, contract) {
  if (Number(row?.semantic_match || 0) < REUSE_SEMANTIC_THRESHOLD) return false;
  if ((contract?.required_entities || []).length && Number(row?.entity_match || 0) < REUSE_ENTITY_THRESHOLD) return false;
  if ((contract?.required_actions || []).length && Number(row?.action_match || 0) < REUSE_ACTION_THRESHOLD) return false;
  if ((contract?.required_relationships || []).length && Number(row?.relationship_match || 0) < REUSE_RELATIONSHIP_THRESHOLD) return false;
  if (contract?.visual_proof_mode === "annotated_real" && (row?.type !== 'image' || !row?.original_url)) return false;
  return true;
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    execFile(ffmpegPath, args, { timeout: REMATERIALIZE_TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) return reject(new Error(String(stderr || err.message).slice(-1200)));
      resolve({ stdout, stderr });
    });
  });
}

async function rematerializeVideo(row) {
  if (row?.type !== "video") return row;
  const local = row.local_path || cachedPathFromUrl(row.url);
  if (local && fs.existsSync(local)) return { ...row, local_path: local, cache_rebuilt: false };

  const source = String(row.original_url || "").trim();
  const start = Number(row.in_point_sec);
  const end = Number(row.out_point_sec);
  if (!local || !source || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;

  try {
    await fsp.mkdir(path.dirname(local), { recursive: true });
    const duration = Math.max(0.05, end - start);
    const tmp = `${local}.tmp-${process.pid}-${crypto.randomUUID()}.mp4`;
    try {
      await runFfmpeg([
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", String(Math.max(0, start)), "-i", source,
        "-t", String(duration), "-an",
        "-vf", "scale='min(1080,iw)':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp,
      ]);
      if (!fs.existsSync(tmp) || fs.statSync(tmp).size < 1024) throw new Error("rematerialized clip is empty");
      await fsp.rename(tmp, local);
    } finally {
      await fsp.unlink(tmp).catch(() => {});
    }
    return { ...row, local_path: local, cache_rebuilt: true };
  } catch (err) {
    console.warn("[broll-library] could not rebuild verified clip:", err?.message || err);
    return null;
  }
}

async function rematerializeAnnotatedImage(row) {
  if (row?.type !== "image" || row?.visual_proof_mode !== "annotated_real") return row;
  const local = row.local_path || cachedPathFromUrl(row.url);
  if (local && fs.existsSync(local)) return { ...row, local_path: local, cache_rebuilt: false };
  const source = String(row.original_url || "").trim();
  if (!local || !source) return null;

  try {
    const r = await axios.get(source, {
      timeout: Math.min(REMATERIALIZE_TIMEOUT_MS, 45000),
      responseType: "arraybuffer",
      headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" },
    });
    const buf = Buffer.from(r.data || []);
    const mime = String(r.headers?.["content-type"] || "image/jpeg").split(";")[0].trim();
    if (!buf.length || buf.length > 16 * 1024 * 1024 || !mime.startsWith("image/")) return null;
    await fsp.mkdir(path.dirname(local), { recursive: true });
    const tmp = `${local}.tmp-${process.pid}-${crypto.randomUUID()}`;
    try {
      await fsp.writeFile(tmp, buf);
      if (fs.statSync(tmp).size < 1024) throw new Error("rematerialized image is empty");
      await fsp.rename(tmp, local);
    } finally {
      await fsp.unlink(tmp).catch(() => {});
    }
    return { ...row, local_path: local, cache_rebuilt: true };
  } catch (err) {
    console.warn("[broll-library] could not rebuild annotated image:", err?.message || err);
    return null;
  }
}

async function findReusable(contract, excludedUrls = new Set()) {
  const rows = await load(), target = contractText(contract), usable = [];
  for (const r of rows) {
    if (!r?.url || excludedUrls.has(r.url) || excludedUrls.has(r.original_url)) continue;
    const library_similarity = similarity(target, String(r.contract_text || ""));
    if (library_similarity < 0.82 || !meetsCurrentContractGate(r, contract)) continue;
    let available = r;
    if (r.type === "video") available = await rematerializeVideo(r);
    else if (r.visual_proof_mode === "annotated_real") available = await rematerializeAnnotatedImage(r);
    if (!available) continue;
    usable.push({ ...available, library_similarity });
  }
  return usable.sort((a, b) => (b.library_similarity - a.library_similarity) || (Number(b.last_used_at_ms || 0) - Number(a.last_used_at_ms || 0))).slice(0, 8);
}

async function recentUrls() {
  const rows = await load();
  return new Set(rows.slice(-RECENT_WINDOW).flatMap((r) => [r.url, r.original_url].filter(Boolean)));
}

async function recordAccepted(asset, contract, runId) {
  if (!asset?.url) return;
  const rows = await load(), key = asset.original_url || asset.url, now = Date.now();
  const existing = rows.find((r) => (r.original_url || r.url) === key);
  const row = {
    url: asset.url, original_url: asset.original_url || asset.url, local_path: asset.local_path || cachedPathFromUrl(asset.url),
    source: asset.source || "", type: asset.type || "", width: asset.width ?? null, height: asset.height ?? null,
    attribution: asset.attribution || existing?.attribution || '',
    license_url: asset.license_url || existing?.license_url || null,
    source_page: asset.source_page || existing?.source_page || null,
    in_point_sec: asset.in_point_sec ?? null, out_point_sec: asset.out_point_sec ?? null, verified_frame_indices: asset.verified_frame_indices || null,
    annotation_plan: Array.isArray(asset.annotation_plan) ? asset.annotation_plan : null,
    semantic_match: asset.semantic_match ?? null, entity_match: asset.entity_match ?? null, action_match: asset.action_match ?? null,
    relationship_match: asset.relationship_match ?? null, score: asset.score ?? null, contract_text: contractText(contract),
    visual_proof_mode: contract?.visual_proof_mode || null, run_id: runId || null,
    usage_count: Number(existing?.usage_count || 0) + 1, created_at_ms: existing?.created_at_ms || now, last_used_at_ms: now,
  };
  const next = rows.filter((r) => (r.original_url || r.url) !== key); next.push(row); await save(next);
}

module.exports = {
  load, save, findReusable, recentUrls, recordAccepted, similarity, contractText, cachedPathFromUrl,
  meetsCurrentContractGate, rematerializeVideo, rematerializeAnnotatedImage,
};
