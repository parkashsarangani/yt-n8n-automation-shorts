// ---------------------------------------------------------------------------
// Competitor outlier mining for Shorts.
//
// V3 keeps the per-channel outlier normalization, but adds a second layer:
// analyze the public thumbnail/cover visual + title + duration of breakout
// Shorts to extract reproducible creative-execution signals. We deliberately
// label thumbnail analysis as a packaging/first-frame PROXY; we do not pretend
// to have watched or transcribed competitor videos.
// ---------------------------------------------------------------------------
const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const axios = require("axios");

const DATA_DIR = path.dirname(process.env.TOPIC_HISTORY_PATH || "/app/data/topic_history.json");
const SIGNALS_PATH = path.join(DATA_DIR, "competitor_signals.json");
const WATCHLIST_PATH = path.join(__dirname, "competitors.json");

const YT_KEY = process.env.YOUTUBE_API_KEY || "";
const OPENAI_KEY = process.env.OPENAI_KEY || "";
const DISTILL_MODEL = process.env.COMPETITOR_DISTILL_MODEL || process.env.STRATEGIST_MODEL || "gpt-5.6-luna";
const VISION_MODEL = process.env.COMPETITOR_VISION_MODEL || "gpt-5.6-luna";

const WINDOW_DAYS = Number(process.env.COMPETITOR_WINDOW_DAYS || 45);
const SHORT_MAX_SEC = Number(process.env.COMPETITOR_SHORT_MAX_SEC || 90);
const OUTLIER_MULTIPLE = Number(process.env.COMPETITOR_OUTLIER_MULTIPLE || 3);
const OUTLIER_MIN_VIEWS = Number(process.env.COMPETITOR_OUTLIER_MIN_VIEWS || 50000);
const MIN_SHORTS_FOR_BASELINE = Number(process.env.COMPETITOR_MIN_SHORTS || 5);
const MAX_OUTLIERS = Number(process.env.COMPETITOR_MAX_OUTLIERS || 30);
const EXECUTION_SAMPLE = Number(process.env.COMPETITOR_EXECUTION_SAMPLE || 12);
const UPLOADS_TO_SCAN = Number(process.env.COMPETITOR_UPLOADS_SCAN || 100);

const YT_API = "https://www.googleapis.com/youtube/v3";

async function writeJson(p, data) {
  await fsp.mkdir(path.dirname(p), { recursive: true });
  await fsp.writeFile(p, JSON.stringify(data, null, 2));
}

async function readJson(p, fallback) {
  try {
    if (!fs.existsSync(p)) return fallback;
    return JSON.parse(await fsp.readFile(p, "utf8"));
  } catch {
    return fallback;
  }
}

function parseChannelToken(tok) {
  const t = String(tok || "").trim();
  if (!t) return null;
  if (/^UC[A-Za-z0-9_-]{22}$/.test(t)) return { channel_id: t, name: t };
  return { handle: t, name: t };
}

function loadWatchlist() {
  const env = (process.env.COMPETITOR_CHANNELS || "").trim();
  if (env) {
    const list = env.split(",").map(parseChannelToken).filter(Boolean);
    if (list.length) return list;
  }
  try {
    const raw = JSON.parse(fs.readFileSync(WATCHLIST_PATH, "utf8"));
    return Array.isArray(raw.channels) ? raw.channels : [];
  } catch {
    return [];
  }
}

function isoDurationToSec(iso) {
  if (!iso || typeof iso !== "string") return null;
  const m = iso.match(/^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/);
  if (!m) return null;
  return Number(m[1] || 0) * 3600 + Number(m[2] || 0) * 60 + Number(m[3] || 0);
}

function median(nums) {
  if (!nums.length) return 0;
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

async function ytGet(endpoint, params) {
  const r = await axios.get(`${YT_API}/${endpoint}`, {
    params: { key: YT_KEY, ...params },
    timeout: 20000,
  });
  return r.data;
}

async function resolveChannel(entry) {
  try {
    let data;
    if (entry.channel_id) {
      data = await ytGet("channels", { part: "statistics,snippet", id: entry.channel_id });
    } else if (entry.handle) {
      const handle = String(entry.handle).replace(/^@/, "");
      data = await ytGet("channels", { part: "statistics,snippet", forHandle: handle });
    } else {
      return null;
    }
    const item = data.items && data.items[0];
    if (!item) return null;
    return {
      id: item.id,
      title: item.snippet.title,
      subs: Number(item.statistics.subscriberCount || 0),
    };
  } catch {
    return null;
  }
}

async function recentVideoIds(channelId) {
  const publishedAfter = new Date(Date.now() - WINDOW_DAYS * 24 * 60 * 60 * 1000).toISOString();
  const ids = [];
  let pageToken;
  for (let page = 0; page < Math.max(1, Math.ceil(UPLOADS_TO_SCAN / 50)); page++) {
    const data = await ytGet("search", {
      part: "id",
      channelId,
      type: "video",
      order: "date",
      maxResults: 50,
      publishedAfter,
      pageToken,
    });
    for (const it of data.items || []) {
      if (it.id?.videoId) ids.push(it.id.videoId);
    }
    pageToken = data.nextPageToken;
    if (!pageToken) break;
  }
  return ids;
}

function bestThumbnail(snippet) {
  const t = snippet?.thumbnails || {};
  return t.maxres?.url || t.standard?.url || t.high?.url || t.medium?.url || t.default?.url || null;
}

async function videoStats(ids) {
  const out = [];
  for (let i = 0; i < ids.length; i += 50) {
    const batch = ids.slice(i, i + 50);
    const data = await ytGet("videos", {
      part: "statistics,contentDetails,snippet",
      id: batch.join(","),
    });
    for (const v of data.items || []) {
      out.push({
        id: v.id,
        title: v.snippet.title,
        description: String(v.snippet.description || "").slice(0, 300),
        views: Number(v.statistics.viewCount || 0),
        likes: Number(v.statistics.likeCount || 0),
        comments: Number(v.statistics.commentCount || 0),
        sec: isoDurationToSec(v.contentDetails.duration),
        published: v.snippet.publishedAt,
        thumbnail_url: bestThumbnail(v.snippet),
      });
    }
  }
  return out;
}

async function scanCompetitors() {
  const watchlist = loadWatchlist();
  const scanned = [];
  const unresolved = [];
  const outliers = [];

  for (const entry of watchlist) {
    const ch = await resolveChannel(entry);
    if (!ch) {
      unresolved.push(entry.name || entry.handle || entry.channel_id || "unknown");
      continue;
    }

    const ids = await recentVideoIds(ch.id);
    const vids = await videoStats(ids);
    const shorts = vids.filter((v) => v.sec != null && v.sec <= SHORT_MAX_SEC && v.views > 0);
    scanned.push({ name: ch.title, subs: ch.subs, recent_shorts: shorts.length });

    if (shorts.length < MIN_SHORTS_FOR_BASELINE) continue;
    const med = median(shorts.map((v) => v.views));
    if (med <= 0) continue;

    for (const v of shorts) {
      const multiple = v.views / med;
      if (multiple >= OUTLIER_MULTIPLE && v.views >= OUTLIER_MIN_VIEWS) {
        outliers.push({
          channel: ch.title,
          title: v.title,
          views: v.views,
          likes: v.likes,
          comments: v.comments,
          outlier_multiple: Number(multiple.toFixed(1)),
          duration_sec: v.sec,
          published_at: v.published,
          thumbnail_url: v.thumbnail_url,
          url: `https://www.youtube.com/shorts/${v.id}`,
        });
      }
    }
  }

  outliers.sort((a, b) => b.outlier_multiple - a.outlier_multiple);
  return { outliers: outliers.slice(0, MAX_OUTLIERS), scanned, unresolved };
}

function parseJsonObject(text) {
  let s = String(text || "").trim();
  if (s.startsWith("```")) s = s.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
  const at = s.indexOf("{");
  if (at < 0) throw new Error("no JSON object in reply: " + s.slice(0, 200));
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = at; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') inStr = true;
    else if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return JSON.parse(s.slice(at, i + 1));
    }
  }
  throw new Error("JSON object not closed");
}

async function fetchImageAsBase64(url) {
  if (!url) return null;
  try {
    const r = await axios.get(url, {
      timeout: 15000,
      responseType: "arraybuffer",
      headers: { "User-Agent": "yt-shorts-competitor-miner/1.0" },
    });
    const buf = Buffer.from(r.data);
    if (!buf.length || buf.length > 4.5 * 1024 * 1024) return null;
    const mime = String(r.headers?.["content-type"] || "image/jpeg").split(";")[0].trim();
    return mime.startsWith("image/") ? { mime, data: buf.toString("base64") } : null;
  } catch {
    return null;
  }
}

async function analyzeExecution(outlier) {
  if (!OPENAI_KEY) return null;
  const encoded = await fetchImageAsBase64(outlier.thumbnail_url);
  if (!encoded) return null;

  const prompt =
    `Analyze PUBLIC PACKAGING SIGNALS for this breakout YouTube Short. You have only the cover/thumbnail image plus title and duration; ` +
    `do NOT claim to know the actual first frame, spoken hook, edit cadence, or payoff timing. ` +
    `Title: "${outlier.title}". Duration: ${outlier.duration_sec}s. ` +
    `Extract transferable creative choices for a faceless single-fact Shorts channel. ` +
    `Return ONLY JSON with: visual_hook_proxy (brief), dominant_subject, composition_type, visual_contrast, apparent_emotion, ` +
    `title_hook_shape, likely_concept_archetype, reproducible (boolean), caveat (must mention this is a thumbnail/cover proxy).`;

  try {
    const r = await axios.post(
      "https://api.openai.com/v1/chat/completions",
      {
        model: VISION_MODEL,
        max_completion_tokens: 350,
        reasoning_effort: "none",
        response_format: { type: "json_object" },
        messages: [{
          role: "user",
          content: [
            { type: "image_url", image_url: { url: `data:${encoded.mime};base64,${encoded.data}` } },
            { type: "text", text: prompt },
          ],
        }],
      },
      {
        timeout: 30000,
        headers: {
          Authorization: `Bearer ${OPENAI_KEY}`,
          "content-type": "application/json",
        },
      }
    );
    const text = r.data?.choices?.[0]?.message?.content || "";
    return parseJsonObject(text);
  } catch {
    return null;
  }
}

async function buildExecutionProfiles(outliers) {
  const sample = outliers.slice(0, Math.max(0, EXECUTION_SAMPLE));
  const profiles = [];
  for (const o of sample) {
    const execution = await analyzeExecution(o);
    if (execution) {
      profiles.push({
        channel: o.channel,
        title: o.title,
        outlier_multiple: o.outlier_multiple,
        views: o.views,
        duration_sec: o.duration_sec,
        execution,
      });
    }
  }
  return profiles;
}

const DISTILL_PROMPT = (outliersJson, executionJson) =>
`You are mining breakout Shorts for TRANSFERABLE SIGNALS, not copying videos.

Our format: faceless, one surprising mass-appeal fact per Short; no medical topics, no biographies, no obscure subjects.

BREAKOUT OUTLIERS (normalized within each channel):
${outliersJson}

PUBLIC CREATIVE-EXECUTION PROFILES:
${executionJson}

Important: execution profiles are based only on public thumbnail/cover imagery + title + duration. They are a packaging/first-frame PROXY, not direct observation of the actual opening frame, narration, editing, or payoff. Never overstate what they prove.

Extract two kinds of signal:
1. TOPIC/ANGLE: recurring subject/angle patterns that transfer to our single-fact format.
2. EXECUTION: recurring visual packaging/composition/hook-shape patterns that we can deliberately test in our own first frame and visual grammar.

RULES:
- Never reproduce a competitor title.
- Drop listicles, medical/health, compilations, creator-personality-dependent formats, or niche fandom deep-cuts.
- Prefer patterns supported by multiple outliers/channels.
- Distinguish a repeated pattern from a one-off hypothesis.
- No claims about competitor retention or exact editing unless those data are actually provided (they are not).

OUTPUT ONLY JSON:
{
  "summary": "<2-3 sentences>",
  "signals": [
    {
      "signal_type": "topic_angle"|"execution",
      "subject_category": "<category or null>",
      "angle": "<transferable pattern>",
      "likely_trigger": "curiosity_gap|disbelief|fear_stakes|scale_shock|taboo_secret|unknown",
      "why_it_works": "<brief hypothesis>",
      "supported_by": "<specific count/channels>",
      "confidence": "low|medium|high"
    }
  ],
  "avoid": ["<tempting but non-transferable pattern>"]
}
Prefer 4-8 strong signals.`;

async function distillSignals(outliers, executionProfiles) {
  if (!outliers.length) {
    return { summary: "No breakout Shorts found in the window.", signals: [], avoid: [] };
  }
  if (!OPENAI_KEY) throw new Error("distillSignals: OPENAI_KEY is not set");

  const compact = outliers.map((o) => ({
    title: o.title,
    channel: o.channel,
    views: o.views,
    outlier_multiple: o.outlier_multiple,
    duration_sec: o.duration_sec,
  }));

  const res = await axios.post(
    "https://api.openai.com/v1/chat/completions",
    {
      model: DISTILL_MODEL,
      max_completion_tokens: Number(process.env.COMPETITOR_DISTILL_MAX_TOKENS || 4500),
      reasoning_effort: "medium",
      response_format: { type: "json_object" },
      messages: [{
        role: "user",
        content: DISTILL_PROMPT(JSON.stringify(compact, null, 2), JSON.stringify(executionProfiles, null, 2)),
      }],
    },
    {
      timeout: 90000,
      headers: {
        Authorization: `Bearer ${OPENAI_KEY}`,
        "content-type": "application/json",
      },
    }
  );

  const choice = res.data?.choices?.[0];
  if (choice?.finish_reason === "length") {
    throw new Error("distiller reply hit max_completion_tokens - raise COMPETITOR_DISTILL_MAX_TOKENS");
  }
  const text = choice?.message?.content || "";
  return parseJsonObject(text);
}

async function mine() {
  if (!YT_KEY) throw new Error("mine: YOUTUBE_API_KEY is not set");
  const { outliers, scanned, unresolved } = await scanCompetitors();
  const executionProfiles = await buildExecutionProfiles(outliers);

  let distilled;
  try {
    distilled = await distillSignals(outliers, executionProfiles);
  } catch (e) {
    distilled = {
      summary: "Outliers found, but signal distillation failed.",
      signals: [],
      avoid: [],
      distill_error: String(e.message || e),
    };
  }

  const payload = {
    generated_at: new Date().toISOString(),
    window_days: WINDOW_DAYS,
    scanned,
    unresolved,
    outliers,
    execution_profiles: executionProfiles,
    ...distilled,
  };
  await writeJson(SIGNALS_PATH, payload);
  return {
    scanned_channels: scanned.length,
    unresolved_channels: unresolved.length,
    outlier_count: outliers.length,
    execution_profiles: executionProfiles.length,
    signal_count: Array.isArray(payload.signals) ? payload.signals.length : 0,
  };
}

async function getSignals() {
  const data = await readJson(SIGNALS_PATH, {
    generated_at: null,
    summary: "No competitor mine has completed yet.",
    signals: [],
    avoid: [],
    execution_profiles: [],
  });
  const generated=Date.parse(data.generated_at || '');
  if(!Number.isFinite(generated)||Date.now()-generated>14*24*3600*1000)return {...data,signals:[],avoid:[],execution_profiles:[],stale:true,summary:'Competitor signals unavailable or older than fourteen days; do not treat them as current trends.'};
  return {...data,stale:false};
}

module.exports = {
  mine,
  getSignals,
  scanCompetitors,
  distillSignals,
  analyzeExecution,
  buildExecutionProfiles,
  isoDurationToSec,
  median,
  parseJsonObject,
  SIGNALS_PATH,
};
