// VISUAL_MATCHING_V4
// API_BUDGET / PREPROD_BROLL_HARDENING / RETRIEVAL_RECALL_PHASE2
// SOURCE_QUERY_COMPILER_V1 / MULTIFRAME_VIDEO_RERANK_V1
// ---------------------------------------------------------------------------
// Multi-source real B-roll resolver — V4 semantic visual matching.
//
// Correctness order:
//   scene visual contract -> broad source recall -> local CLIP coarse rank ->
//   diversity -> actual-pixel VLM verification -> exact media materialization -> memory.
// Semantic correctness is a hard gate. Weak generic stock never silently wins.
// ---------------------------------------------------------------------------
const axios = require("axios");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const { execFile } = require("child_process");
const ffmpegPath = require("ffmpeg-static");
const {
  buildVisualContract,
  buildScoringTarget,
  shouldPreferTemplate,
  passesSemanticGate,
} = require("./visualContract");
const { localSemanticRerank, diversifyCandidates } = require("./semanticReranker");
const library = require("./clipLibrary");
const {balancedPool} = require('./sourcePool');
const {
  RUN_MAX_VISION_CALLS,
  getSceneLimit,
  reserveVisionCall,
  reserveTemplateFallback,
  getBudgetState,
  cacheKey,
  getCachedResult,
  putCachedResult,
} = require("./visualBudget");

const PEXELS_KEY = process.env.PEXELS_KEY || "";
const PIXABAY_KEY = process.env.PIXABAY_KEY || "";
const UNSPLASH_KEY = process.env.UNSPLASH_KEY || "";
const OPENAI_KEY = process.env.OPENAI_KEY || "";
const SCORE_THRESHOLD = Number(process.env.BROLL_SCORE_THRESHOLD || 82);
const FIRST_FRAME_THRESHOLD = Number(process.env.BROLL_FIRST_FRAME_THRESHOLD || 88);
const SEMANTIC_THRESHOLD = Number(process.env.BROLL_SEMANTIC_THRESHOLD || 82);
const ENTITY_THRESHOLD = Number(process.env.BROLL_ENTITY_THRESHOLD || 85);
const ACTION_THRESHOLD = Number(process.env.BROLL_ACTION_THRESHOLD || 80);
const RELATIONSHIP_THRESHOLD = Number(process.env.BROLL_RELATIONSHIP_THRESHOLD || 80);
const VISION_MODEL = process.env.BROLL_VISION_MODEL || "gpt-5.6-luna";
const VISION_TOP_N = Math.max(4, Number(process.env.BROLL_VISION_TOP_N || 10));
const VIDEO_VERIFY_TOP_N = Math.max(1, Number(process.env.BROLL_VIDEO_VERIFY_TOP_N || 4));
const VIDEO_SAMPLE_FRAMES = Math.max(6, Math.min(16, Number(process.env.BROLL_VIDEO_SAMPLE_FRAMES || 12)));
const SOURCE_PER_QUERY = Math.max(4, Number(process.env.BROLL_SOURCE_PER_QUERY || 12));
const INITIAL_SEARCH_QUERIES = Math.max(1, Number(process.env.BROLL_INITIAL_SEARCH_QUERIES || 2));
const MAX_SEARCH_QUERIES = Math.max(INITIAL_SEARCH_QUERIES, Number(process.env.BROLL_MAX_SEARCH_QUERIES || 5));
const CANDIDATE_POOL_MAX = Math.max(20, Number(process.env.BROLL_CANDIDATE_POOL_MAX || 80));
const MAX_RELEVANCE_RETRY_ROUNDS = Math.max(0, Number(process.env.BROLL_MAX_RELEVANCE_RETRY_ROUNDS || 2));
const RELEVANCE_RETRY_WALL_CLOCK_MS = Math.max(10000, Number(process.env.BROLL_RELEVANCE_RETRY_WALL_CLOCK_MS || 65000));
// n8n now calls this service over the Docker network rather than through the
// public reverse proxy, but the resolver still has an absolute deadline so a
// slow external source/model can never pin a workflow indefinitely.
const RESOLVE_DEADLINE_MS = Math.max(30000, Number(process.env.BROLL_RESOLVE_DEADLINE_MS || 135000));
const OUTPUT_DIR = process.env.OUTPUT_DIR || "/outputs";
const VERIFIED_CACHE_DIR = process.env.BROLL_VERIFIED_CACHE_DIR || path.join(OUTPUT_DIR, "broll-cache");
const VERIFIED_PUBLIC_BASE = String(process.env.BROLL_PUBLIC_BASE_URL || "http://127.0.0.1:4000/outputs").replace(/\/$/, "");
const VERIFIED_CACHE_MAX_AGE_HOURS = Number(process.env.BROLL_CACHE_MAX_AGE_HOURS || 72);
const ALLOWED_FALLBACK_TEMPLATES = new Set(["stat_reveal", "comparison", "kinetic_text", "map", "timeline", "diagram"]);

async function safeGet(url, config = {}, timeout = 15000) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await axios.get(url, { timeout, ...config });
      return r.data;
    } catch (err) {
      const status = Number(err?.response?.status || 0);
      const retryable = !status || status === 429 || status >= 500;
      if (!retryable || attempt === 1) return null;
      await new Promise((resolve) => setTimeout(resolve, 350 * (attempt + 1)));
    }
  }
  return null;
}

function uniqStrings(values) {
  const seen = new Set();
  const out = [];
  for (const raw of values || []) {
    const v = String(raw || "").replace(/\s+/g, " ").trim();
    const key = v.toLowerCase();
    if (!v || seen.has(key)) continue;
    seen.add(key);
    out.push(v);
  }
  return out;
}

function dedupeCandidates(candidates) {
  const seen = new Set();
  return (candidates || []).filter((c) => {
    const key = c?.id ? `${c.source}:${c.id}` : c?.url;
    if (!c || !c.url || !key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function compileQueries(input, contract) {
  const entities = contract.required_entities || [];
  const actions = contract.required_actions || [];
  const relationship = contract.required_relationships || [];
  const actionQuery = entities.length && actions.length ? `${entities[0]} ${actions[0]}` : "";
  const relationQuery = entities.length > 1 && relationship.length ? `${entities.slice(0, 2).join(" ")} ${relationship[0]}` : "";
  return uniqStrings([
    input.query,
    contract.visual_claim,
    ...(Array.isArray(input.queries) ? input.queries : []),
    ...(Array.isArray(input.alternate_queries) ? input.alternate_queries : []),
    actionQuery,
    relationQuery,
    ...entities,
    input.subject,
  ]).slice(0, MAX_SEARCH_QUERIES);
}

async function fromPexelsPhotos(query) {
  if (!PEXELS_KEY || !query) return [];
  const d = await safeGet(`https://api.pexels.com/v1/search?query=${encodeURIComponent(query)}&per_page=${SOURCE_PER_QUERY}&orientation=portrait&size=large`, { headers: { Authorization: PEXELS_KEY } });
  return (d?.photos || []).map((p) => ({
    id: p.id, type: "image", url: p.src?.original || p.src?.large2x || p.src?.large,
    thumb: p.src?.medium || p.src?.small, width: p.width || null, height: p.height || null,
    alt: p.alt || query, query, source: "pexels", attribution: "",
  })).filter((c) => c.url);
}

async function fromPexelsVideos(query) {
  if (!PEXELS_KEY || !query) return [];
  const d = await safeGet(`https://api.pexels.com/videos/search?query=${encodeURIComponent(query)}&per_page=${SOURCE_PER_QUERY}&orientation=portrait&size=medium`, { headers: { Authorization: PEXELS_KEY } });
  return (d?.videos || []).map((v) => {
    const files = (v.video_files || []).filter((f) => f.link && f.height && f.width);
    const portrait = files.filter((f) => f.height >= f.width);
    const pool = portrait.length ? portrait : files;
    const best = [...pool].sort((a, b) => Math.abs((a.height || 0) - 1080) - Math.abs((b.height || 0) - 1080))[0];
    const sample = [...pool].sort((a, b) => (Number(a.file_size) || Number.MAX_SAFE_INTEGER) - (Number(b.file_size) || Number.MAX_SAFE_INTEGER))[0] || best;
    return best ? {
      id: v.id, type: "video", url: best.link, sample_url: sample?.link || best.link,
      thumb: v.image, width: best.width || null, height: best.height || null,
      duration: Number(v.duration || 0) || null, alt: query, query, source: "pexels_video", attribution: "",
    } : null;
  }).filter(Boolean);
}

async function fromPixabayPhotos(query) {
  if (!PIXABAY_KEY || !query) return [];
  const d = await safeGet(`https://pixabay.com/api/?key=${encodeURIComponent(PIXABAY_KEY)}&q=${encodeURIComponent(query)}&image_type=photo&orientation=vertical&safesearch=true&per_page=${Math.min(50, SOURCE_PER_QUERY)}`);
  return (d?.hits || []).map((p) => ({
    id: p.id, type: "image", url: p.largeImageURL || p.webformatURL, thumb: p.previewURL || p.webformatURL,
    width: p.imageWidth || null, height: p.imageHeight || null, alt: p.tags || query, query,
    source: "pixabay", attribution: p.user ? `Pixabay: ${p.user}` : "Pixabay",
  })).filter((c) => c.url);
}

async function fromPixabayVideos(query) {
  if (!PIXABAY_KEY || !query) return [];
  const d = await safeGet(`https://pixabay.com/api/videos/?key=${encodeURIComponent(PIXABAY_KEY)}&q=${encodeURIComponent(query)}&video_type=film&safesearch=true&per_page=${Math.min(50, SOURCE_PER_QUERY)}`);
  return (d?.hits || []).map((v) => {
    const choices = [v?.videos?.large, v?.videos?.medium, v?.videos?.small, v?.videos?.tiny].filter((x) => x?.url);
    const best = choices.find((x) => Number(x.height || 0) >= 720) || choices[0];
    const sample = [v?.videos?.tiny, v?.videos?.small, v?.videos?.medium].find((x) => x?.url) || best;
    return best ? {
      id: v.id, type: "video", url: best.url, sample_url: sample.url,
      thumb: best.thumbnail || sample.thumbnail || null, width: best.width || null, height: best.height || null,
      duration: Number(v.duration || 0) || null, alt: v.tags || query, query, source: "pixabay_video",
      attribution: v.user ? `Pixabay: ${v.user}` : "Pixabay",
    } : null;
  }).filter(Boolean);
}

async function fromUnsplash(query) {
  if (!UNSPLASH_KEY || !query) return [];
  const d = await safeGet(`https://api.unsplash.com/search/photos?query=${encodeURIComponent(query)}&per_page=${SOURCE_PER_QUERY}&orientation=portrait`, { headers: { Authorization: `Client-ID ${UNSPLASH_KEY}`, "Accept-Version": "v1" } });
  return (d?.results || []).map((p) => ({
    id: p.id, type: "image", url: p.urls?.full || p.urls?.regular, thumb: p.urls?.small || p.urls?.thumb,
    width: p.width || null, height: p.height || null, alt: p.alt_description || p.description || query,
    query, source: "unsplash", attribution: `Photo by ${p.user?.name || "an artist"} on Unsplash`,
  })).filter((c) => c.url);
}

function stripHtml(v) { return String(v || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(); }

async function fromOpenverse(query) {
  if (!query) return [];
  const d = await safeGet(`https://api.openverse.org/v1/images/?q=${encodeURIComponent(query)}&page_size=${Math.min(20, SOURCE_PER_QUERY)}&license_type=commercial,modification&mature=false`, { headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" } });
  return (d?.results || []).map((p) => {
    const url = p.url || p.thumbnail; if (!url) return null;
    return {
      id: p.id, type: "image", url, thumb: p.thumbnail || url,
      width: p.width || null, height: p.height || null, alt: p.title || query,
      query, source: "openverse",
      attribution: stripHtml(`${p.title || "Image"} by ${p.creator || "unknown"} (${p.license ? p.license.toUpperCase() : "CC"}${p.license_version ? " " + p.license_version : ""}) via Openverse`),
    };
  }).filter(Boolean);
}

async function fromNasaImages(query) {
  if (!query) return [];
  const d = await safeGet(`https://images-api.nasa.gov/search?q=${encodeURIComponent(query)}&media_type=image`, {}, 20000);
  return (d?.collection?.items || []).slice(0, Math.min(12, SOURCE_PER_QUERY)).map((item) => {
    const meta = item?.data?.[0] || {}, links = item?.links || [];
    const canonical = links.find((l) => l.rel === "canonical" && l.render === "image");
    const preview = links.find((l) => l.rel === "preview" && l.render === "image") || links[0];
    const link = canonical || preview;
    const url = link?.href; if (!url) return null;
    return {
      id: meta.nasa_id || url, type: "image", url, thumb: preview?.href || url,
      width: link?.width || null, height: link?.height || null, alt: meta.title || query,
      query, source: "nasa", attribution: `NASA${meta.center ? ` / ${meta.center}` : ""}: ${meta.title || "image"}`,
    };
  }).filter(Boolean);
}

async function fromWikimediaCommons(query) {
  if (!query) return [];
  const d = await safeGet(`https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrnamespace=6&gsrlimit=${Math.min(12, SOURCE_PER_QUERY)}&gsrsearch=${encodeURIComponent(query)}&prop=imageinfo&iiprop=url|mime|extmetadata&iiurlwidth=1600&format=json&origin=*`, { headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" } }, 20000);
  return Object.values(d?.query?.pages || {}).map((p) => {
    const ii = p?.imageinfo?.[0], meta = ii?.extmetadata || {}, mime = String(ii?.mime || "");
    if (!["image/jpeg", "image/png", "image/webp"].includes(mime)) return null;
    const url = ii?.thumburl || ii?.url; if (!url) return null;
    return {
      id: p.pageid || p.title, type: "image", url, thumb: ii?.thumburl || url,
      width: ii?.thumbwidth || ii?.width || null, height: ii?.thumbheight || ii?.height || null,
      alt: stripHtml(meta.ImageDescription?.value || p?.title || query), query, source: "wikimedia",
      attribution: stripHtml([meta.Artist?.value ? `Image: ${meta.Artist.value}` : "", meta.LicenseShortName?.value || "", meta.Credit?.value || ""].filter(Boolean).join(" | ")),
    };
  }).filter(Boolean);
}

async function wikiSummaryImage(title) {
  const d = await safeGet(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`, { headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" } });
  const url = d?.originalimage?.source;
  return url ? { id: d?.pageid || d?.title || title, url, thumb: d?.thumbnail?.source || url, title: d?.title || title, width: d?.originalimage?.width || null, height: d?.originalimage?.height || null } : null;
}
async function fromWikipedia(subject) {
  if (!subject) return [];
  let hit = await wikiSummaryImage(subject);
  if (!hit) {
    const s = await safeGet(`https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(subject)}&srlimit=1&format=json&origin=*`, { headers: { "User-Agent": "yt-shorts-broll/1.0" } });
    if (s?.query?.search?.[0]?.title) hit = await wikiSummaryImage(s.query.search[0].title);
  }
  return hit ? [{ id: hit.id, type: "image", url: hit.url, thumb: hit.thumb, width: hit.width, height: hit.height, alt: hit.title, query: subject, source: "wikipedia", attribution: `Image via Wikipedia: ${hit.title}` }] : [];
}

async function collectCandidates(queries, subject, priority=[]) {
  const jobs = [];
  for (const q of queries) jobs.push(fromPexelsPhotos(q), fromPexelsVideos(q), fromPixabayPhotos(q), fromPixabayVideos(q), fromUnsplash(q), fromWikimediaCommons(q), fromOpenverse(q), fromNasaImages(q));
  jobs.push(fromWikipedia(subject));
  const groups = await Promise.all(jobs);
  return balancedPool(groups, CANDIDATE_POOL_MAX, priority);
}

async function fetchImageAsBase64(url) {
  try {
    const r = await axios.get(url, { timeout: 20000, responseType: "arraybuffer", headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" } });
    const buf = Buffer.from(r.data); if (!buf.length || buf.length > 8 * 1024 * 1024) return null;
    const mime = String(r.headers?.["content-type"] || "image/jpeg").split(";")[0].trim();
    return mime.startsWith("image/") ? { mime, data: buf.toString("base64") } : null;
  } catch { return null; }
}

function parseJsonObject(text, fallback = {}) {
  const raw = String(text || "").trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
  const blocks = []; let i = 0;
  while (i < raw.length) {
    const start = raw.indexOf("{", i); if (start < 0) break;
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let j = start; j < raw.length; j++) {
      const ch = raw[j];
      if (inStr) { if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === '"') inStr = false; }
      else if (ch === '"') inStr = true; else if (ch === "{") depth++; else if (ch === "}" && --depth === 0) { end = j; break; }
    }
    if (end < 0) break; blocks.push(raw.slice(start, end + 1)); i = end + 1;
  }
  for (let k = blocks.length - 1; k >= 0; k--) { try { return JSON.parse(blocks[k]); } catch {} }
  return fallback;
}
function parseScoreJson(text) {
  const parsed = parseJsonObject(text, null); if (parsed) return parsed;
  return { semantic_match: 0, entity_match: 0, action_match: 0, relationship_match: 0, relevance: 0, scroll_stop: 0, mobile_clarity: 0, composition: 0, motion_energy: 0, uniqueness: 0, overall: 0 };
}
function clampScore(v) { const n = Number(v); return Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : 0; }
function normalizeScore(parsed, candidate) {
  const semantic = clampScore(parsed.semantic_match ?? parsed.relevance);
  const result = {
    semantic_match: semantic, entity_match: clampScore(parsed.entity_match ?? semantic), action_match: clampScore(parsed.action_match ?? semantic),
    relationship_match: clampScore(parsed.relationship_match ?? semantic), relevance: clampScore(parsed.relevance ?? semantic),
    scroll_stop: clampScore(parsed.scroll_stop), mobile_clarity: clampScore(parsed.mobile_clarity), composition: clampScore(parsed.composition),
    motion_energy: clampScore(parsed.motion_energy ?? (candidate?.type === "video" ? 55 : 35)), uniqueness: clampScore(parsed.uniqueness),
    overall: clampScore(parsed.overall), reason: String(parsed.reason || "").slice(0, 500),
  };
  result.overall = Math.min(result.overall, Math.min(result.semantic_match, result.entity_match) + 8, 100);
  return result;
}

function remainingDeadlineMs(state) {
  if (!state?.deadline_at) return 45000;
  return Math.max(0, Number(state.deadline_at) - Date.now());
}

async function askVision(imageUrl, prompt, maxTokens, state, cacheParts = []) {
  if (!OPENAI_KEY || !imageUrl) return null;
  const key = cacheKey([VISION_MODEL, ...cacheParts, prompt]);
  const cached = getCachedResult(key); if (cached) { state.cache_hits++; return cached; }
  const remaining = remainingDeadlineMs(state);
  if (remaining < 1200) { state.budget_exhausted = "resolver_deadline_exhausted"; return null; }
  const reservation = reserveVisionCall(state.run_id, state.scene_budget);
  if (!reservation.allowed) { state.budget_exhausted = reservation.reason; return null; }
  try {
    const r = await axios.post("https://api.openai.com/v1/chat/completions", {
      model: VISION_MODEL, max_completion_tokens: maxTokens, reasoning_effort: "none", response_format: { type: "json_object" },
      messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: imageUrl } }, { type: "text", text: prompt }] }],
    }, { timeout: Math.max(1000, Math.min(45000, remaining - 500)), headers: { Authorization: `Bearer ${OPENAI_KEY}`, "content-type": "application/json" } });
    const parsed = parseJsonObject(r.data?.choices?.[0]?.message?.content || "", null); if (parsed) putCachedResult(key, parsed); return parsed;
  } catch { return null; }
}

function visualContractPrompt(contract, { firstFrame = false, videoContactSheet = false } = {}) {
  return [
    "Judge whether the ACTUAL pixels communicate the exact narrated beat, not merely the broad topic.",
    `VISUAL CONTRACT: ${buildScoringTarget(contract)}`,
    `Proof mode: ${contract.visual_proof_mode}. Forbidden: ${(contract.forbidden_visuals || []).join("; ")}.`,
    firstFrame ? "This is the first frame and must be instantly legible and swipe-stopping." : "Exact semantic communication outranks generic beauty.",
    videoContactSheet ? "This is a chronological contact sheet from one video. Identify ONLY a contiguous set of sampled frames that visibly proves the contract." : "Evaluate exactly what is shown.",
    "Return ONLY JSON with 0-100 semantic_match, entity_match, action_match, relationship_match, relevance, scroll_stop, mobile_clarity, composition, motion_energy, uniqueness, overall, reason.",
    videoContactSheet ? "Also return match:boolean and best_frame_indices:number[] using zero-based row-major frame indices. The indices MUST be non-empty and contiguous; do not return free-form trim timestamps." : "",
    "If the required action/relationship is absent, semantic_match MUST be below 70.",
  ].filter(Boolean).join(" ");
}

function runFfmpeg(args, timeout = 90000) { return new Promise((resolve, reject) => execFile(ffmpegPath, args, { timeout, maxBuffer: 6 * 1024 * 1024 }, (err, stdout, stderr) => err ? reject(new Error(String(stderr || err.message).slice(-1500))) : resolve({ stdout, stderr }))); }

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
const buildVideoContactSheet = sampleVideoContactSheet;

function normalizeRange(start, end, duration) {
  let s = Number(start), e = Number(end), d = Number(duration || 0); if (!Number.isFinite(s)) s = 0; if (!Number.isFinite(e) || e <= s) e = Math.min(d || s + 3, s + 3); s = Math.max(0, s);
  if (d > 0) { s = Math.min(s, Math.max(0, d - 0.05)); e = Math.min(Math.max(s + 0.05, e), d); }
  return { start: Number(s.toFixed(3)), end: Number(e.toFixed(3)) };
}

function normalizeVerifiedFrameIndices(raw, total) {
  if (!Array.isArray(raw) || !Number.isInteger(total) || total < 1) return [];
  const indices = [...new Set(raw.map(Number).filter((n) => Number.isInteger(n) && n >= 0 && n < total))].sort((a, b) => a - b);
  if (!indices.length) return [];
  for (let i = 1; i < indices.length; i++) if (indices[i] !== indices[i - 1] + 1) return [];
  return indices;
}

function verifiedRangeFromFrameIndices(indicesRaw, timestamps, duration) {
  const times = Array.isArray(timestamps) ? timestamps.map(Number) : [];
  const d = Number(duration || 0);
  const indices = normalizeVerifiedFrameIndices(indicesRaw, times.length);
  if (!indices.length || !d || times.some((t) => !Number.isFinite(t))) return null;
  const first = indices[0], last = indices[indices.length - 1];
  const start = first === 0 ? 0 : (times[first - 1] + times[first]) / 2;
  const end = last === times.length - 1 ? d : (times[last] + times[last + 1]) / 2;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return { start: Number(Math.max(0, start).toFixed(3)), end: Number(Math.min(d, end).toFixed(3)), indices };
}

async function verifyVideoCandidate(candidate, contract, opts, state) {
  const sheet = await sampleVideoContactSheet(candidate); if (!sheet) return { ok: false, reason: "video_contact_sheet_failed" };
  const parsed = await askVision(sheet.imageUrl, `${visualContractPrompt(contract, { ...opts, videoContactSheet: true })} Sample frame center-times row-major: ${sheet.timestamps.join(", ")} seconds.`, 420, state, [candidate.url, "video"]);
  if (!parsed) return { ok: false, reason: state.budget_exhausted || "video_visual_verifier_failed" };
  const dimensions = normalizeScore(parsed, candidate);
  const r = verifiedRangeFromFrameIndices(parsed.best_frame_indices, sheet.timestamps, candidate.duration);
  if (!r) return { ok: false, reason: "video_verified_frame_range_missing", ...dimensions, sampled_timestamps: sheet.timestamps };
  return { ok: true, ...dimensions, frame_similarity: dimensions.semantic_match, verified_start_sec: r.start, verified_end_sec: r.end, verified_frame_indices: r.indices, sampled_timestamps: sheet.timestamps };
}

async function scoreImageCandidate(candidate, contract, opts, state) {
  const encoded = await fetchImageAsBase64(candidate.url || candidate.thumb); if (!encoded) return normalizeScore({}, candidate);
  const parsed = await askVision(`data:${encoded.mime};base64,${encoded.data}`, visualContractPrompt(contract, opts), 300, state, [candidate.url, "image", contract.visual_proof_mode]);
  return normalizeScore(parsed || {}, candidate);
}

async function cleanupVerifiedCache() {
  try { await fsp.mkdir(VERIFIED_CACHE_DIR, { recursive: true }); const now = Date.now(), maxAge = VERIFIED_CACHE_MAX_AGE_HOURS * 3600 * 1000; for (const d of await fsp.readdir(VERIFIED_CACHE_DIR, { withFileTypes: true })) if (d.isFile()) { const p = path.join(VERIFIED_CACHE_DIR, d.name), st = await fsp.stat(p).catch(() => null); if (st && now - st.mtimeMs > maxAge) await fsp.unlink(p).catch(() => {}); } } catch {}
}

function imageExtensionForMime(mime) {
  const m = String(mime || "").toLowerCase();
  if (m.includes("png")) return "png";
  if (m.includes("webp")) return "webp";
  return "jpg";
}

async function materializeVerifiedImage(candidate) {
  await cleanupVerifiedCache();
  await fsp.mkdir(VERIFIED_CACHE_DIR, { recursive: true });
  const r = await axios.get(candidate.url, {
    timeout: 30000,
    responseType: "arraybuffer",
    headers: { "User-Agent": "yt-shorts-broll/1.0 (contact: channel owner)" },
  });
  const buf = Buffer.from(r.data || []);
  const mime = String(r.headers?.["content-type"] || "image/jpeg").split(";")[0].trim();
  if (!buf.length || buf.length > 16 * 1024 * 1024 || !mime.startsWith("image/")) throw new Error("verified image download invalid");
  const ext = imageExtensionForMime(mime);
  const key = crypto.createHash("sha256").update(String(candidate.url)).digest("hex").slice(0, 24);
  const filename = `verified_image_${key}.${ext}`;
  const dest = path.join(VERIFIED_CACHE_DIR, filename);
  if (!fs.existsSync(dest) || fs.statSync(dest).size < 1024) {
    const tmp = `${dest}.tmp-${process.pid}-${Date.now()}`;
    await fsp.writeFile(tmp, buf);
    await fsp.rename(tmp, dest);
  }
  return { url: `${VERIFIED_PUBLIC_BASE}/broll-cache/${filename}`, local_path: dest, original_url: candidate.url };
}

async function materializeVerifiedClip(candidate, verification) {
  await cleanupVerifiedCache(); await fsp.mkdir(VERIFIED_CACHE_DIR, { recursive: true });
  const start = Number(verification.verified_start_sec || 0), end = Number(verification.verified_end_sec || 0), duration = Math.max(0.05, end - start), key = crypto.createHash("sha256").update(`${candidate.url}|${start}|${end}`).digest("hex").slice(0, 24), filename = `verified_${key}.mp4`, dest = path.join(VERIFIED_CACHE_DIR, filename);
  if (!fs.existsSync(dest) || fs.statSync(dest).size < 4096) { const tmp = `${dest}.tmp-${process.pid}-${Date.now()}.mp4`; await runFfmpeg(["-hide_banner", "-loglevel", "error", "-y", "-ss", String(start), "-i", candidate.url, "-t", String(duration), "-an", "-vf", "scale='min(1080,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], 120000); await fsp.rename(tmp, dest); }
  return { url: `${VERIFIED_PUBLIC_BASE}/broll-cache/${filename}`, local_path: dest, source_url: candidate.url, in_point_sec: start, out_point_sec: end, verified_duration_sec: Number(duration.toFixed(3)) };
}

function words(text) { return new Set(String(text || "").toLowerCase().match(/[a-z0-9][a-z0-9'-]{2,}/g) || []); }
function metadataOverlap(candidate, target) { const ws = [...words(target)], hay = `${candidate?.alt || ""} ${candidate?.query || ""}`.toLowerCase(); return ws.reduce((n, w) => n + (hay.includes(w) ? 1 : 0), 0); }
function cheapSemanticRank(candidate, contract) { const target = buildScoringTarget(contract), lexical = metadataOverlap(candidate, target), portrait = candidate.height && candidate.width && candidate.height >= candidate.width ? 2 : 0, exact = contract.subject && String(candidate.alt || "").toLowerCase().includes(contract.subject.toLowerCase()) ? 4 : 0, motion = candidate.type === "video" && contract.visual_proof_mode === "literal_video" ? 2 : 0; return lexical * 4 + portrait + exact + motion; }
function summarizeCandidate(best) { return best ? { type: best.type, source: best.source, score: best.score, semantic_match: best.semantic_match, entity_match: best.entity_match, action_match: best.action_match, relationship_match: best.relationship_match, relevance: best.relevance, scroll_stop: best.scroll_stop, mobile_clarity: best.mobile_clarity, local_similarity: best.local_similarity ?? null, frame_similarity: best.frame_similarity ?? null } : null; }

function candidatePassesGate(candidate, contract, threshold) {
  if (!candidate || candidate.rejected || Number(candidate.score || 0) < Number(threshold || 0)) return false;
  return passesSemanticGate(candidate, contract, {
    semantic: SEMANTIC_THRESHOLD,
    entity: ENTITY_THRESHOLD,
    action: ACTION_THRESHOLD,
    relationship: RELATIONSHIP_THRESHOLD,
  });
}

function normalizeTemplateFallback(raw) {
  if (!raw || typeof raw !== "object") return null;
  const templateName = String(raw.template_name || "").trim();
  const templateData = raw.template_data && typeof raw.template_data === "object" ? raw.template_data : null;
  if (!ALLOWED_FALLBACK_TEMPLATES.has(templateName) || !templateData) return null;
  return { template_name: templateName, template_data: templateData };
}

function templateFallbackResult(input, contract, state, best, threshold, budget) {
  const fallback = normalizeTemplateFallback(input.template_fallback);
  if (!fallback) return null;
  const reservation = reserveTemplateFallback(state.run_id, input.scene_index, {
    first_frame: input.first_frame === true || Number(input.scene_index) === 0,
    planned_template_count: Number(input.planned_template_count || 0),
  });
  if (!reservation.allowed) return null;
  return {
    ok: true,
    type: "template",
    visual_source: "template",
    template_name: fallback.template_name,
    template_data: fallback.template_data,
    source: "deterministic_template_fallback",
    fallback_reason: "real_media_below_semantic_gate",
    score: Number(best?.score || 0),
    semantic_match: Number(best?.semantic_match || 0),
    entity_match: Number(best?.entity_match || 0),
    action_match: Number(best?.action_match || 0),
    relationship_match: Number(best?.relationship_match || 0),
    threshold,
    recommended_visual_proof_mode: contract.visual_proof_mode,
    visual_contract: contract,
    attribution: "",
    ...budget,
  };
}

async function resolveBroll(input = {}) {
  const resolveStartedAt = Date.now();
  const contract = buildVisualContract(input), desc = String(input.description || input.query || input.subject || "").trim(), subj = String(input.subject || input.named_subject || "").trim(), target = buildScoringTarget(contract) || desc || subj;
  const queryList = compileQueries(input, contract);
  if (!queryList.length || !target) return { ok: false, reason: "missing_search_target", visual_contract: contract };
  if (input.prefer_template === true && shouldPreferTemplate(contract)) return { ok: false, reason: "representation_prefers_template", recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract };

  const isFirstFrame = input.first_frame === true || Number(input.scene_index) === 0, threshold = isFirstFrame ? FIRST_FRAME_THRESHOLD : SCORE_THRESHOLD;
  const state = {
    run_id: String(input.run_id || "").trim(),
    scene_budget: { used: 0, limit: getSceneLimit(isFirstFrame, { run_id: input.run_id, retrieval_scene_count: input.retrieval_scene_count, retrieval_scene_position: input.retrieval_scene_position }) },
    cache_hits: 0,
    budget_exhausted: null,
    deadline_at: resolveStartedAt + RESOLVE_DEADLINE_MS,
  };
  const recent = await library.recentUrls();
  const reusable = await library.findReusable(contract, recent);
  let candidates = reusable.map((r) => ({ ...r, source: `library:${r.source || "proven"}`, alt: r.contract_text, query: queryList[0], library_hit: true }));

  let searchRounds = 0, queriesTried = [];
  let queryCursor = 0;
  const scored = []; let videoCalls = 0;
  const scoredUrls = new Set();

  for (let retryRound = 0; retryRound <= MAX_RELEVANCE_RETRY_ROUNDS; retryRound++) {
    for (; queryCursor < queryList.length && candidates.length < CANDIDATE_POOL_MAX; queryCursor += INITIAL_SEARCH_QUERIES) {
      const batch = queryList.slice(queryCursor, queryCursor + INITIAL_SEARCH_QUERIES); if (!batch.length) break;
      searchRounds++; queriesTried.push(...batch);
      const external = await collectCandidates(batch, subj, input.source_priority || []);
      candidates = dedupeCandidates([...candidates, ...external]).filter((c) => !recent.has(c.url) || c.library_hit).slice(0, CANDIDATE_POOL_MAX);
      if (candidates.length >= VISION_TOP_N * 2) break;
      if (remainingDeadlineMs(state) < 5000) { state.budget_exhausted = "resolver_deadline_exhausted"; break; }
    }
    if (contract.visual_proof_mode === "annotated_real") candidates = candidates.filter((c) => c.type === "image");
    if (!candidates.length) {
      const fallback = templateFallbackResult(input, contract, state, null, threshold, getBudgetState(state.run_id,state.scene_budget));
      if(fallback)return {...fallback,quality_gate_passed:false,selection_reason:'deterministic_template_fallback'};
      return { ok: false, reason: contract.visual_proof_mode === "annotated_real" ? "no_verified_image_candidates" : "no_candidates", threshold, queries_tried: queriesTried, visual_contract: contract };
    }

    candidates = await localSemanticRerank(candidates, target, contract.acceptable_visuals || []);
    candidates = diversifyCandidates(candidates, Math.max(VISION_TOP_N * 2, 16));
    candidates.sort((a, b) => {
      const la = Number.isFinite(Number(a.local_similarity)) ? Number(a.local_similarity) : -1, lb = Number.isFinite(Number(b.local_similarity)) ? Number(b.local_similarity) : -1;
      if (la !== lb) return lb - la; return cheapSemanticRank(b, contract) - cheapSemanticRank(a, contract);
    });

    const mixed = [], add = (c) => { if (c && !scoredUrls.has(c.url) && !mixed.some((x) => x.url === c.url)) mixed.push(c); };
    candidates.filter((c) => c.source === "wikipedia" || c.source === "wikimedia").slice(0, 1).forEach(add);
    const videos = candidates.filter((c) => c.type === "video"), photos = candidates.filter((c) => c.type !== "video" && c.source !== "wikipedia" && c.source !== "wikimedia");
    for (let i = 0; mixed.length < VISION_TOP_N && (i < videos.length || i < photos.length); i++) { add(videos[i]); if (mixed.length < VISION_TOP_N) add(photos[i]); }
    candidates.forEach((c) => { if (mixed.length < VISION_TOP_N) add(c); });

    for (const c of mixed) {
      if (state.budget_exhausted || remainingDeadlineMs(state) < 1200) { if (!state.budget_exhausted) state.budget_exhausted = "resolver_deadline_exhausted"; break; }
      scoredUrls.add(c.url);
      const reusableVerifiedImage = contract.visual_proof_mode !== "annotated_real" || (c.local_path && fs.existsSync(c.local_path));
      if (c.library_hit && Number(c.semantic_match || 0) >= 88 && reusableVerifiedImage) {
        scored.push({ ...c, score: Number(c.score || c.semantic_match), rejected: false });
        continue;
      }
      if (c.type === "video") {
        if (videoCalls++ >= VIDEO_VERIFY_TOP_N) continue;
        const verification = await verifyVideoCandidate(c, contract, { firstFrame: isFirstFrame }, state);
        if (!verification.ok) { scored.push({ ...c, ...verification, score: 0, rejected: true }); continue; }
        const materialized = await materializeVerifiedClip(c, verification).catch(() => null);
        if (!materialized) { scored.push({ ...c, ...verification, score: 0, rejected: true, reason: "verified_clip_materialization_failed" }); continue; }
        scored.push({ ...c, ...verification, ...materialized, original_url: c.url, score: verification.overall });
        continue;
      }
      const dimensions = await scoreImageCandidate(c, contract, { firstFrame: isFirstFrame }, state);
      if (contract.visual_proof_mode === "annotated_real") {
        const materialized = await materializeVerifiedImage(c).catch(() => null);
        if (!materialized) { scored.push({ ...c, ...dimensions, score: dimensions.overall, rejected: true, reason: "verified_image_materialization_failed" }); continue; }
        scored.push({ ...c, ...dimensions, ...materialized, score: dimensions.overall, rejected: false });
        continue;
      }
      scored.push({ ...c, ...dimensions, score: dimensions.overall, rejected: false });
    }

    scored.sort((a, b) => b.score - a.score);
    const roundPassing = scored.find((c) => candidatePassesGate(c, contract, threshold));
    const hasMoreToTry = queryCursor < queryList.length || candidates.some((c) => !scoredUrls.has(c.url));
    const retryBudgetExceeded = Date.now() - resolveStartedAt >= RELEVANCE_RETRY_WALL_CLOCK_MS;
    if (roundPassing || !hasMoreToTry || state.budget_exhausted || retryBudgetExceeded) break;
  }

  scored.sort((a, b) => b.score - a.score);
  const usableBest = scored.find((c) => !c.rejected);
  const best = scored.find((c) => candidatePassesGate(c, contract, threshold));
  const budget = getBudgetState(state.run_id, state.scene_budget);

  if (!best) {
    const fallback = templateFallbackResult(input, contract, state, usableBest, threshold, budget);
    if (fallback) return fallback;
    return {
      ok: false,
      reason: state.budget_exhausted || "below_semantic_quality_gate",
      threshold,
      semantic_threshold: SEMANTIC_THRESHOLD,
      entity_threshold: ENTITY_THRESHOLD,
      action_threshold: ACTION_THRESHOLD,
      relationship_threshold: RELATIONSHIP_THRESHOLD,
      first_frame: isFirstFrame,
      queries_tried: queriesTried,
      candidate_count: candidates.length,
      scored_count: scored.length,
      search_rounds: searchRounds,
      best_score: usableBest?.score || scored[0]?.score || 0,
      best_candidate: summarizeCandidate(usableBest || scored[0]),
      recommended_visual_proof_mode: contract.visual_proof_mode,
      visual_contract: contract,
      ...budget,
    };
  }

  const result = {
    ok: true, type: best.type, url: best.url, original_url: best.original_url || best.url, local_path: best.local_path || null, source: best.source,
    width: best.width ?? null, height: best.height ?? null,
    score: best.score, semantic_match: best.semantic_match, entity_match: best.entity_match, action_match: best.action_match,
    relationship_match: best.relationship_match, relevance: best.relevance, scroll_stop: best.scroll_stop,
    mobile_clarity: best.mobile_clarity, composition: best.composition, motion_energy: best.motion_energy, uniqueness: best.uniqueness,
    local_similarity: best.local_similarity ?? null, frame_similarity: best.frame_similarity ?? null, frame_sampling_status: best.type === "video" ? "verified_sample_bins" : null,
    threshold, first_frame: isFirstFrame, selected_query: best.query || queryList[0], candidate_count: candidates.length,
    search_rounds: searchRounds, queries_tried: queriesTried, attribution: best.attribution || "", in_point_sec: best.in_point_sec ?? null,
    out_point_sec: best.out_point_sec ?? null, verified_duration_sec: best.verified_duration_sec ?? null, verified_frame_indices: best.verified_frame_indices || null,
    actual_video_verified: best.type === "video", library_hit: best.library_hit === true, recommended_visual_proof_mode: contract.visual_proof_mode, visual_contract: contract, ...budget,
  };
  await library.recordAccepted(result, contract, state.run_id).catch(() => {});
  return result;
}

module.exports = {
  resolveBroll, parseScoreJson, uniqStrings, dedupeCandidates, metadataOverlap, cheapSemanticRank,
  normalizeRange, normalizeScore, normalizeVerifiedFrameIndices, verifiedRangeFromFrameIndices,
  buildVideoContactSheet, sampleVideoContactSheet, verifyVideoCandidate, materializeVerifiedClip, materializeVerifiedImage,
  candidatePassesGate, normalizeTemplateFallback, templateFallbackResult,
  fromPixabayVideos, fromPixabayPhotos, compileQueries, RUN_MAX_VISION_CALLS, INITIAL_SEARCH_QUERIES,
};
