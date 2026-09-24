const axios = require("axios");

const ROUTER_MODE = String(process.env.LLM_ROUTER_MODE || "freellmapi").trim().toLowerCase();
const FAIL_OPEN_TO_DIRECT = String(process.env.LLM_ROUTER_FAIL_OPEN_TO_DIRECT || "true").trim().toLowerCase() !== "false";
const FREELLMAPI_BASE_URL = String(process.env.FREELLMAPI_BASE_URL || "http://freellmapi:3001/v1").replace(/\/$/, "");
const FREELLMAPI_API_KEY = String(process.env.FREELLMAPI_API_KEY || "").trim();
const FREELLMAPI_TEXT_MODEL = String(process.env.FREELLMAPI_TEXT_MODEL || "auto:smart").trim();
const FREELLMAPI_VISION_MODEL = String(process.env.FREELLMAPI_VISION_MODEL || "auto:smart").trim();
const FREELLMAPI_ANTHROPIC_MODEL = String(process.env.FREELLMAPI_ANTHROPIC_MODEL || "claude-sonnet-4-5").trim();
const OPENAI_KEY = String(process.env.OPENAI_KEY || "").trim();
const ANTHROPIC_KEY = String(process.env.ANTHROPIC_KEY || process.env.ANTHROPIC_API_KEY || "").trim();
const REQUEST_TIMEOUT_MS = Math.max(1000, Number(process.env.LLM_ROUTER_TIMEOUT_MS || 120000));

const DIRECT_OPENAI_ORIGIN = "https://api.openai.com/v1";
const DIRECT_ANTHROPIC_ORIGIN = "https://api.anthropic.com/v1";
const rawAxios = axios.create();
let installed = false;
let warnedMissingFreeKey = false;

function isFreeMode() {
  return ROUTER_MODE !== "direct";
}

function parseBody(data) {
  if (data == null) return {};
  if (typeof data === "object") return data;
  if (typeof data !== "string") return {};
  try { return JSON.parse(data); } catch { return {}; }
}

function hasVisionPayload(body) {
  const seen = new Set();
  const visit = (value) => {
    if (value == null) return false;
    if (typeof value !== "object") return false;
    if (seen.has(value)) return false;
    seen.add(value);
    if (Array.isArray(value)) return value.some(visit);
    if (value.type === "image_url" || value.type === "input_image" || value.type === "image") return true;
    if (value.image_url || value.image || value.source?.type === "base64") return true;
    return Object.values(value).some(visit);
  };
  return visit(body?.messages) || visit(body?.input) || visit(body?.content);
}

function freeModelFor(body, surface = "chat") {
  if (surface === "messages") return FREELLMAPI_ANTHROPIC_MODEL;
  return hasVisionPayload(body) ? FREELLMAPI_VISION_MODEL : FREELLMAPI_TEXT_MODEL;
}

function normalizeHeaders(headers = {}) {
  const out = { ...headers };
  for (const key of Object.keys(out)) {
    if (/^(authorization|x-api-key)$/i.test(key)) delete out[key];
  }
  return out;
}

function directTarget(surface) {
  if (surface === "responses") return `${DIRECT_OPENAI_ORIGIN}/responses`;
  if (surface === "messages") return `${DIRECT_ANTHROPIC_ORIGIN}/messages`;
  return `${DIRECT_OPENAI_ORIGIN}/chat/completions`;
}

function freeTarget(surface) {
  if (surface === "responses") return `${FREELLMAPI_BASE_URL}/responses`;
  if (surface === "messages") return `${FREELLMAPI_BASE_URL}/messages`;
  return `${FREELLMAPI_BASE_URL}/chat/completions`;
}

function surfaceForUrl(url = "") {
  const value = String(url);
  if (/api\.anthropic\.com\/v1\/messages/i.test(value)) return "messages";
  if (/api\.openai\.com\/v1\/responses/i.test(value)) return "responses";
  if (/api\.openai\.com\/v1\/chat\/completions/i.test(value)) return "chat";
  return null;
}

function freeHeaders(surface, headers = {}) {
  const out = normalizeHeaders(headers);
  out.Authorization = `Bearer ${FREELLMAPI_API_KEY}`;
  out["content-type"] = out["content-type"] || out["Content-Type"] || "application/json";
  if (surface === "messages") out["anthropic-version"] = out["anthropic-version"] || "2023-06-01";
  if (String(process.env.FREELLMAPI_CACHE || "off").toLowerCase() === "on") out["X-FreeLLM-Cache"] = "on";
  return out;
}

function directHeaders(surface, headers = {}) {
  const out = normalizeHeaders(headers);
  out["content-type"] = out["content-type"] || out["Content-Type"] || "application/json";
  if (surface === "messages") {
    if (!ANTHROPIC_KEY) throw new Error("ANTHROPIC_KEY is required for direct Anthropic fallback");
    out["x-api-key"] = ANTHROPIC_KEY;
    out["anthropic-version"] = out["anthropic-version"] || "2023-06-01";
  } else {
    if (!OPENAI_KEY) throw new Error("OPENAI_KEY is required for direct OpenAI fallback");
    out.Authorization = `Bearer ${OPENAI_KEY}`;
  }
  return out;
}

function withFreeModel(body, surface) {
  if (!body || typeof body !== "object") return body;
  return { ...body, model: freeModelFor(body, surface) };
}

function makeFreeRequest(surface, body, config = {}) {
  if (!FREELLMAPI_API_KEY) throw new Error("FREELLMAPI_API_KEY is not configured");
  return {
    ...config,
    method: config.method || "post",
    url: freeTarget(surface),
    data: withFreeModel(body, surface),
    headers: freeHeaders(surface, config.headers),
    timeout: Number(config.timeout || REQUEST_TIMEOUT_MS),
    __llmBypass: true,
    __llmRoute: "freellmapi",
  };
}

function makeDirectRequest(surface, body, config = {}) {
  return {
    ...config,
    method: config.method || "post",
    url: directTarget(surface),
    data: body,
    headers: directHeaders(surface, config.headers),
    timeout: Number(config.timeout || REQUEST_TIMEOUT_MS),
    __llmBypass: true,
    __llmRoute: "direct",
  };
}

// FREE_JSON_CONTRACT: FreeLLMAPI can answer HTTP 200 with JSON of the wrong
// shape (2026-09-24 12:00 run: Stage 1 draft without hook/scenes). For JSON-mode
// chat requests, and for callers naming x-llm-required-keys, treat that as a free
// failure so the existing paid fail-open path gets one chance to answer instead.
function lastJsonObject(text) {
  const value = String(text || "").trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
  } catch {}
  let found;
  for (let start = value.indexOf("{"); start >= 0;) {
    let depth = 0, inStr = false, esc = false, next = start + 1;
    for (let i = start; i < value.length; i++) {
      const ch = value[i];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === "\\") esc = true;
        else if (ch === '"') inStr = false;
      } else if (ch === '"') inStr = true;
      else if (ch === "{") depth += 1;
      else if (ch === "}" && --depth === 0) {
        try {
          const parsed = JSON.parse(value.slice(start, i + 1));
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) found = parsed;
        } catch {}
        next = i + 1;
        break;
      }
    }
    start = value.indexOf("{", next);
  }
  return found;
}

function freeJsonProblem(surface, body, data, requiredKeys = []) {
  if (surface !== "chat") return null;
  const format = body?.response_format?.type;
  const wantsJson = format === "json_object" || format === "json_schema";
  if (!wantsJson && !requiredKeys.length) return null;
  const content = data?.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) return "empty message content";
  // Mirror the n8n parsers, which also accept a reply missing its leading "{".
  const parsed = lastJsonObject(content) || lastJsonObject(`{${content}`);
  if (!parsed) return "message content is not a JSON object";
  const missing = requiredKeys.filter((key) => !Object.prototype.hasOwnProperty.call(parsed, key));
  return missing.length ? `JSON missing required keys: ${missing.join(",")}` : null;
}

async function requestViaRouter(surface, body, config = {}) {
  const deadline = Date.now() + Number(config.timeout || REQUEST_TIMEOUT_MS);
  const remaining = () => {
    if(config.signal?.aborted) throw new Error('LLM request cancelled');
    const ms=deadline-Date.now(); if(ms<=0)throw new Error('LLM request deadline exceeded'); return ms;
  };
  if (!isFreeMode()) {
    const {requiredJsonKeys, ...requestConfig} = config;
    const response = await rawAxios.request(makeDirectRequest(surface, body, requestConfig));
    return { response, route: "direct", fallback: false };
  }

  try {
    const {requiredJsonKeys, ...requestConfig} = config;
    const response = await rawAxios.request(makeFreeRequest(surface, body, {...requestConfig, timeout: Math.max(1,Math.floor(remaining()*0.55))}));
    const problem = FAIL_OPEN_TO_DIRECT ? freeJsonProblem(surface, body, response?.data, requiredJsonKeys || []) : null;
    if (problem) throw new Error(`FreeLLMAPI returned unusable JSON: ${problem}`);
    return { response, route: "freellmapi", fallback: false };
  } catch (freeError) {
    if (!FAIL_OPEN_TO_DIRECT) throw freeError;
    const {requiredJsonKeys, ...requestConfig} = config;
    const response = await rawAxios.request(makeDirectRequest(surface, body, {...requestConfig, timeout:remaining()}));
    return { response, route: "direct", fallback: true, free_error: String(freeError?.message || freeError).slice(0, 500) };
  }
}

function installAxiosRouting() {
  if (installed) return;
  installed = true;

  axios.interceptors.request.use((config) => {
    if (config?.__llmBypass) return config;
    const surface = surfaceForUrl(config?.url);
    if (!surface || !isFreeMode()) return config;

    const body = parseBody(config.data);
    if (!FREELLMAPI_API_KEY) {
      if (FAIL_OPEN_TO_DIRECT) {
        if (!warnedMissingFreeKey) {
          warnedMissingFreeKey = true;
          console.warn("[llm-routing] FreeLLMAPI mode is enabled but FREELLMAPI_API_KEY is missing; paid direct fail-open is active until FreeLLMAPI is configured");
        }
        return config;
      }
      throw new Error("FREELLMAPI_API_KEY is not configured and fail-open is disabled");
    }

    config.__llmOriginal = {
      surface,
      url: config.url,
      data: config.data,
      headers: { ...(config.headers || {}) },
      timeout: config.timeout,
      deadline: Date.now()+Number(config.timeout || REQUEST_TIMEOUT_MS),
      signal: config.signal,
    };
    config.__llmRouted = true;
    config.timeout = Math.max(1,Math.floor(Number(config.timeout || REQUEST_TIMEOUT_MS)*0.55));
    config.url = freeTarget(surface);
    config.data = withFreeModel(body, surface);
    config.headers = freeHeaders(surface, config.headers);
    return config;
  });

  axios.interceptors.response.use(
    (response) => response,
    async (error) => {
      const config = error?.config;
      if (!config?.__llmRouted || !FAIL_OPEN_TO_DIRECT || config?.__llmRetriedDirect) throw error;
      const original = config.__llmOriginal || {};
      const surface = original.surface || surfaceForUrl(original.url);
      if (!surface) throw error;
      const body = parseBody(original.data);
      const remaining=original.deadline-Date.now();
      if(original.signal?.aborted || remaining<=0)throw error;
      const fallbackConfig = makeDirectRequest(surface, body, {
        headers: original.headers,
        timeout: remaining,
        signal: original.signal,
      });
      fallbackConfig.__llmRetriedDirect = true;
      console.warn(`[llm-routing] FreeLLMAPI request failed; retrying ${surface} via direct provider: ${String(error?.message || error).slice(0, 300)}`);
      return rawAxios.request(fallbackConfig);
    }
  );
}

function routingStatus() {
  return {
    mode: ROUTER_MODE,
    fail_open_to_direct: FAIL_OPEN_TO_DIRECT,
    freellmapi_base_url: FREELLMAPI_BASE_URL,
    freellmapi_key_configured: Boolean(FREELLMAPI_API_KEY),
    openai_fallback_configured: Boolean(OPENAI_KEY),
    anthropic_fallback_configured: Boolean(ANTHROPIC_KEY),
    text_model: FREELLMAPI_TEXT_MODEL,
    vision_model: FREELLMAPI_VISION_MODEL,
  };
}

installAxiosRouting();

module.exports = {
  isFreeMode,
  parseBody,
  hasVisionPayload,
  freeModelFor,
  surfaceForUrl,
  freeTarget,
  directTarget,
  makeFreeRequest,
  makeDirectRequest,
  freeJsonProblem,
  requestViaRouter,
  installAxiosRouting,
  routingStatus,
};
