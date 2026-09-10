// SHORTS_GROWTH_V2 topic policy: strict 500-Short no-repeat horizon with
// canonical claim matching plus optional local sentence-embedding similarity.
const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const crypto = require("crypto");

const TOPIC_HISTORY_PATH = process.env.TOPIC_HISTORY_PATH || path.join(__dirname, "topic_history.json");
const EMBEDDING_CACHE_PATH = process.env.TOPIC_EMBEDDING_CACHE_PATH || path.join(path.dirname(TOPIC_HISTORY_PATH), "topic_embedding_cache.json");
const EMBEDDING_MODEL = process.env.TOPIC_DEDUP_EMBEDDING_MODEL || "Xenova/all-MiniLM-L6-v2";
const SEMANTIC_THRESHOLD = Math.max(0.5, Math.min(0.99, Number(process.env.TOPIC_DEDUP_COSINE_THRESHOLD || 0.82)));
const HISTORY_LIMIT = Math.max(500, Number(process.env.TOPIC_HISTORY_MAX || 500));
const EMBEDDING_TIMEOUT_MS = Math.max(1000, Number(process.env.TOPIC_DEDUP_EMBEDDING_TIMEOUT_MS || 30000));
// Tightened because the fallback is all that stands between a paraphrase and a
// repeat when embeddings are unavailable.
const LEXICAL_JACCARD = Math.max(0.2, Math.min(0.9, Number(process.env.TOPIC_DEDUP_LEXICAL_JACCARD || 0.45)));
const LEXICAL_CONTAINMENT = Math.max(0.2, Math.min(0.95, Number(process.env.TOPIC_DEDUP_LEXICAL_CONTAINMENT || 0.6)));
// The 500-Short no-repeat horizon is a hard product requirement, so by default a
// run refuses to pick a topic it could not semantically check at all rather than
// silently downgrading to the lexical gate.
const REQUIRE_SEMANTIC = String(process.env.TOPIC_DEDUP_REQUIRE_SEMANTIC || "true").toLowerCase() !== "false";
// The claim gate deliberately embeds mechanism+payoff WITHOUT the subject.
// Measured separation between real duplicates and genuinely different facts:
//   raw topic wording -0.151 (anti-correlated), full identity +0.064,
//   mechanism+payoff  +0.349.
// Including the subject makes two different facts about one subject look alike,
// which is the common case on a channel that revisits recognizable things.
const CLAIM_THRESHOLD = Math.max(0.1, Math.min(0.95, Number(process.env.TOPIC_DEDUP_CLAIM_THRESHOLD || 0.32)));
const DISABLE_EMBEDDINGS = String(process.env.TOPIC_DEDUP_DISABLE_EMBEDDINGS || "false").toLowerCase() === "true";

const STOP = new Set([
  "about","after","again","also","always","among","because","before","being","could","does","every","from","have","into","just","more","most","never","only","other","over","same","some","than","that","their","there","these","they","this","those","through","under","very","what","when","where","which","while","with","would","your"
]);

let extractorPromise = null;
let cachePromise = null;
let cacheDirty = false;

function normalizeText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function significantTokens(value) {
  return normalizeText(value).split(" ").filter((w) => w.length > 2 && !STOP.has(w));
}

function canonicalKey(candidate = {}) {
  const explicit = String(candidate.canonical_key || "").trim();
  if (explicit) return normalizeText(explicit);
  const parts = [candidate.subject_key, candidate.mechanism_key, candidate.payoff_key]
    .map(normalizeText)
    .filter(Boolean);
  if (parts.length >= 2) return parts.join(" | ");
  return significantTokens(candidate.topic || candidate.picked || "").sort().join(" ");
}

function tokenSimilarity(a, b) {
  const A = new Set(significantTokens(a));
  const B = new Set(significantTokens(b));
  if (!A.size || !B.size) return { jaccard: 0, containment: 0 };
  let common = 0;
  for (const x of A) if (B.has(x)) common++;
  return {
    jaccard: common / (A.size + B.size - common),
    containment: common / Math.min(A.size, B.size),
  };
}

// Crude suffix stripper. Without it "holds" and "holding" are separate tokens,
// which is enough for a barely-reworded duplicate to clear the lexical gate.
function stem(word) {
  let w = String(word || "");
  for (const suf of ["ingly", "edly", "ing", "ies", "ied", "es", "ed", "ly", "s"]) {
    if (w.length - suf.length >= 3 && w.endsWith(suf)) { w = w.slice(0, -suf.length); break; }
  }
  return w.endsWith("i") ? `${w.slice(0, -1)}y` : w;
}

function tokenSimilarityStemmed(a, b) {
  const A = new Set(significantTokens(a).map(stem));
  const B = new Set(significantTokens(b).map(stem));
  if (!A.size || !B.size) return { jaccard: 0, containment: 0 };
  let common = 0;
  for (const x of A) if (B.has(x)) common++;
  return {
    jaccard: common / (A.size + B.size - common),
    containment: common / Math.min(A.size, B.size),
  };
}

function lexicalNearDuplicate(a, b) {
  const na = normalizeText(a);
  const nb = normalizeText(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  if (Math.min(na.length, nb.length) >= 24 && (na.includes(nb) || nb.includes(na))) return true;
  const raw = tokenSimilarity(na, nb);
  const stemmed = tokenSimilarityStemmed(na, nb);
  return Math.max(raw.jaccard, stemmed.jaccard) >= LEXICAL_JACCARD
    || Math.max(raw.containment, stemmed.containment) >= LEXICAL_CONTAINMENT;
}

function cosineSimilarity(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || !a.length || a.length !== b.length) return null;
  let dot = 0, aa = 0, bb = 0;
  for (let i = 0; i < a.length; i++) {
    const x = Number(a[i]) || 0;
    const y = Number(b[i]) || 0;
    dot += x * y; aa += x * x; bb += y * y;
  }
  if (!aa || !bb) return null;
  return dot / Math.sqrt(aa * bb);
}

function withTimeout(promise, ms) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("topic embedding timeout")), ms); }),
  ]);
}

async function getExtractor() {
  if (DISABLE_EMBEDDINGS) return null;
  if (!extractorPromise) {
    extractorPromise = (async () => {
      try {
        const mod = await import("@huggingface/transformers");
        if (mod.env) {
          mod.env.cacheDir = process.env.HF_CACHE_DIR || "/app/data/hf-cache";
          mod.env.allowRemoteModels = true;
        }
        return await mod.pipeline("feature-extraction", EMBEDDING_MODEL);
      } catch (err) {
        console.warn("[topic-policy] local embeddings unavailable; canonical/lexical dedup remains active:", err?.message || err);
        return null;
      }
    })();
  }
  return extractorPromise;
}

async function getCache() {
  if (!cachePromise) {
    cachePromise = (async () => {
      try {
        if (!fs.existsSync(EMBEDDING_CACHE_PATH)) return {};
        const obj = JSON.parse(await fsp.readFile(EMBEDDING_CACHE_PATH, "utf8"));
        return obj && typeof obj === "object" ? obj : {};
      } catch { return {}; }
    })();
  }
  return cachePromise;
}

function cacheId(text) {
  return crypto.createHash("sha256").update(`${EMBEDDING_MODEL}\n${normalizeText(text)}`).digest("hex");
}

async function persistCache(cache) {
  if (!cacheDirty) return;
  cacheDirty = false;
  try {
    const entries = Object.entries(cache);
    const trimmed = entries.length > 2500 ? Object.fromEntries(entries.slice(entries.length - 2500)) : cache;
    await fsp.mkdir(path.dirname(EMBEDDING_CACHE_PATH), { recursive: true });
    await fsp.writeFile(EMBEDDING_CACHE_PATH, JSON.stringify(trimmed));
  } catch (err) {
    console.warn("[topic-policy] embedding cache write failed:", err?.message || err);
  }
}

async function embedTexts(texts) {
  const unique = [...new Set((texts || []).map((x) => String(x || "").trim()).filter(Boolean))];
  if (!unique.length) return new Map();
  const cache = await getCache();
  const result = new Map();
  const missing = [];
  for (const text of unique) {
    const id = cacheId(text);
    if (Array.isArray(cache[id])) result.set(text, cache[id]);
    else missing.push(text);
  }
  if (!missing.length) return result;
  const extractor = await withTimeout(getExtractor(), EMBEDDING_TIMEOUT_MS).catch(() => null);
  if (!extractor) return result;

  for (let i = 0; i < missing.length; i += 32) {
    const batch = missing.slice(i, i + 32);
    try {
      const output = await withTimeout(extractor(batch, { pooling: "mean", normalize: true }), EMBEDDING_TIMEOUT_MS);
      let rows = typeof output?.tolist === "function" ? output.tolist() : null;
      if (rows && rows.length && !Array.isArray(rows[0])) rows = [rows];
      if (!Array.isArray(rows) || rows.length !== batch.length) continue;
      batch.forEach((text, idx) => {
        const vec = Array.from(rows[idx] || []).map(Number);
        if (!vec.length) return;
        result.set(text, vec);
        cache[cacheId(text)] = vec;
        cacheDirty = true;
      });
    } catch (err) {
      console.warn("[topic-policy] embedding batch failed; lexical safeguards still apply:", err?.message || err);
      break;
    }
  }
  await persistCache(cache);
  return result;
}

async function loadHistory() {
  try {
    if (!fs.existsSync(TOPIC_HISTORY_PATH)) return [];
    const parsed = JSON.parse(await fsp.readFile(TOPIC_HISTORY_PATH, "utf8"));
    return Array.isArray(parsed) ? parsed.slice(-HISTORY_LIMIT) : [];
  } catch { return []; }
}

async function filterCandidates({ candidates, history, semanticThreshold = SEMANTIC_THRESHOLD } = {}) {
  const pool = (Array.isArray(candidates) ? candidates : [])
    .filter((c) => c && String(c.topic || "").trim())
    .map((c) => ({ ...c, topic: String(c.topic).trim(), canonical_key: canonicalKey(c) }));
  const used = (Array.isArray(history) ? history : await loadHistory()).slice(-HISTORY_LIMIT);
  const rejected = [];
  const lexicalSurvivors = [];

  for (const c of pool) {
    let duplicate = null;
    for (const h of used) {
      const ht = String(h?.topic || h?.picked || "").trim();
      if (!ht) continue;
      if (c.canonical_key && c.canonical_key === canonicalKey(h)) {
        duplicate = { reason: "canonical_key", matched_topic: ht, similarity: 1 };
        break;
      }
      if (lexicalNearDuplicate(c.topic, ht)) {
        duplicate = { reason: "lexical", matched_topic: ht, similarity: null };
        break;
      }
    }
    if (duplicate) rejected.push({ topic: c.topic, ...duplicate });
    else lexicalSurvivors.push(c);
  }

  if (!lexicalSurvivors.length || !used.length) {
    return { survivors: lexicalSurvivors, rejected, semantic_available: false, semantic_threshold: semanticThreshold };
  }

  // Compare the semantic identity of the fact (subject + mechanism + payoff),
  // not its wording. Embedding raw titles misses the duplicates that matter:
  // "The Eiffel Tower grows 15cm in summer" and "Paris's iron landmark gets
  // taller when the sun heats its metal" are the same Short, yet score 0.48.
  const identityOf = (item) => {
    const parts = [item?.subject_key, item?.mechanism_key, item?.payoff_key].map(normalizeText).filter(Boolean);
    const topic = String(item?.topic || item?.picked || "").trim();
    return parts.length >= 2 ? `${parts.join(" ")} ${topic}`.trim() : topic;
  };
  const claimOf = (item) => [item?.mechanism_key, item?.payoff_key].map(normalizeText).filter(Boolean).join(" ");
  const historyEntries = used
    .map((h) => ({ text: String(h?.topic || h?.picked || "").trim(), identity: identityOf(h), claim: claimOf(h) }))
    .filter((h) => h.text);
  const candidateIdentities = new Map(lexicalSurvivors.map((c) => [c.topic, identityOf(c)]));
  const candidateClaims = new Map(lexicalSurvivors.map((c) => [c.topic, claimOf(c)]));
  const embeddings = await embedTexts([
    ...historyEntries.map((h) => h.identity),
    ...historyEntries.map((h) => h.claim),
    ...lexicalSurvivors.map((c) => candidateIdentities.get(c.topic)),
    ...lexicalSurvivors.map((c) => candidateClaims.get(c.topic)),
  ]);
  const semanticAvailable = lexicalSurvivors.some((c) => embeddings.has(candidateIdentities.get(c.topic)))
    && historyEntries.some((h) => embeddings.has(h.identity));
  if (!semanticAvailable) {
    if (REQUIRE_SEMANTIC) {
      const err = new Error("TOPIC_DEDUP_SEMANTIC_UNAVAILABLE: refusing to pick a topic that could not be checked against the last "
        + `${HISTORY_LIMIT} Shorts (embedding model unavailable); set TOPIC_DEDUP_REQUIRE_SEMANTIC=false to publish on the lexical gate alone`);
      err.code = "TOPIC_DEDUP_SEMANTIC_UNAVAILABLE";
      throw err;
    }
    return { survivors: lexicalSurvivors, rejected, semantic_available: false, semantic_threshold: semanticThreshold };
  }

  const survivors = [];
  for (const c of lexicalSurvivors) {
    const cv = embeddings.get(candidateIdentities.get(c.topic));
    const cc = embeddings.get(candidateClaims.get(c.topic));
    let best = { similarity: -1, topic: null };
    let bestClaim = { similarity: -1, topic: null };
    for (const h of historyEntries) {
      if (cv) {
        const sim = cosineSimilarity(cv, embeddings.get(h.identity));
        if (sim != null && sim > best.similarity) best = { similarity: sim, topic: h.text };
      }
      if (cc) {
        const sim = cosineSimilarity(cc, embeddings.get(h.claim));
        if (sim != null && sim > bestClaim.similarity) bestClaim = { similarity: sim, topic: h.text };
      }
    }
    if (bestClaim.similarity >= CLAIM_THRESHOLD) {
      rejected.push({ topic: c.topic, reason: "semantic_claim", matched_topic: bestClaim.topic, similarity: Number(bestClaim.similarity.toFixed(4)) });
    } else if (best.similarity >= semanticThreshold) {
      rejected.push({ topic: c.topic, reason: "semantic", matched_topic: best.topic, similarity: Number(best.similarity.toFixed(4)) });
    } else {
      survivors.push({
        ...c,
        max_history_similarity: best.similarity >= 0 ? Number(best.similarity.toFixed(4)) : null,
        max_history_claim_similarity: bestClaim.similarity >= 0 ? Number(bestClaim.similarity.toFixed(4)) : null,
      });
    }
  }
  return { survivors, rejected, semantic_available: true, semantic_threshold: semanticThreshold };
}

function armForSeed(seed) {
  const s = String(seed || "default");
  let hash = 2166136261;
  for (let i = 0; i < s.length; i++) { hash ^= s.charCodeAt(i); hash = Math.imul(hash, 16777619); }
  const bucket = (hash >>> 0) % 10;
  return bucket <= 6 ? "exploit" : bucket <= 8 ? "adjacent" : "explore";
}

async function shortlistCandidates({ candidates, desired_strategy_arm, seed, history } = {}) {
  const desired = ["exploit", "adjacent", "explore"].includes(desired_strategy_arm)
    ? desired_strategy_arm : armForSeed(seed);
  const filtered = await filterCandidates({ candidates, history });
  if (!filtered.survivors.length) {
    const err = new Error(`TOPIC_DEDUP_EXHAUSTED: all ${Array.isArray(candidates) ? candidates.length : 0} candidates repeat the last ${HISTORY_LIMIT} Shorts`);
    err.code = "TOPIC_DEDUP_EXHAUSTED";
    err.details = filtered.rejected;
    throw err;
  }
  const sorted = [...filtered.survivors].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  const sameArm = sorted.filter((c) => String(c.strategy_arm || "") === desired);
  const shortlist = (sameArm.length ? sameArm : sorted).slice(0, 4);
  return {
    pool: sorted,
    shortlist,
    desired_strategy_arm: desired,
    dedup: {
      history_size: Math.min(HISTORY_LIMIT, (Array.isArray(history) ? history.length : (await loadHistory()).length)),
      rejected_count: filtered.rejected.length,
      rejected: filtered.rejected.slice(0, 20),
      semantic_available: filtered.semantic_available,
      semantic_threshold: filtered.semantic_threshold,
    },
  };
}

module.exports = {
  HISTORY_LIMIT,
  SEMANTIC_THRESHOLD,
  CLAIM_THRESHOLD,
  normalizeText,
  significantTokens,
  canonicalKey,
  tokenSimilarity,
  lexicalNearDuplicate,
  cosineSimilarity,
  armForSeed,
  filterCandidates,
  shortlistCandidates,
};
