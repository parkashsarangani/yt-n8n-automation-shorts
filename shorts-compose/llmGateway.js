const express = require("express");
const { requestViaRouter, routingStatus } = require("./llmRouting");

const app = express();
const port = Number(process.env.LLM_GATEWAY_PORT || 3100);

app.use(express.json({ limit: process.env.LLM_GATEWAY_BODY_LIMIT || "20mb" }));

app.get("/health", (_req, res) => {
  res.json({ ok: true, ...routingStatus() });
});

// Forward only headers that affect API semantics/routing. Provider credentials
// are deliberately terminated at this gateway and never forwarded from n8n.
function safeUpstreamHeaders(req) {
  const out = { "content-type": "application/json" };
  for (const name of [
    "x-session-id",
    "x-codex-session-id",
    "session-id",
    "x-claude-code-session-id",
    "anthropic-version",
    "openai-beta",
  ]) {
    const value = req.get(name);
    if (value) out[name] = value;
  }
  return out;
}

// Callers may name top-level JSON keys a usable answer must contain; a free
// answer without them falls back to the paid provider (see llmRouting.js).
// Consumed here, never forwarded upstream.
function requiredJsonKeys(req) {
  return String(req.get("x-llm-required-keys") || "")
    .split(",")
    .map((key) => key.trim())
    .filter((key) => /^[A-Za-z0-9_]{1,64}$/.test(key))
    .slice(0, 16);
}

async function proxy(surface, req, res) {
  const controller=new AbortController();
  const cancel=()=>{if(!res.writableEnded)controller.abort();};
  res.on('close',cancel);
  try {
    const result = await requestViaRouter(surface, req.body || {}, {
      timeout: Number(req.get("x-llm-timeout-ms") || process.env.LLM_ROUTER_TIMEOUT_MS || 120000),
      headers: safeUpstreamHeaders(req),
      signal: controller.signal,
      requiredJsonKeys: requiredJsonKeys(req),
    });
    const upstream = result.response;
    res.set("X-LLM-Route", result.route);
    res.set("X-LLM-Fallback", result.fallback ? "direct" : "none");
    const routedVia = upstream?.headers?.["x-routed-via"];
    const attempts = upstream?.headers?.["x-fallback-attempts"];
    if (routedVia) res.set("X-Routed-Via", String(routedVia));
    if (attempts) res.set("X-Fallback-Attempts", String(attempts));

    if (result.fallback) {
      // Never log prompts, provider keys, or response bodies. This warning is
      // intentionally cost-oriented: any paid fallback should be visible in
      // normal container logs without exposing user/content data.
      console.warn(
        `[llm-gateway] FreeLLMAPI failed; paid direct fallback used surface=${surface} error=${String(result.free_error || "unknown").replace(/\s+/g, " ").slice(0, 300)}`,
      );
    }

    res.status(Number(upstream?.status || 200)).json(upstream?.data ?? {});
  } catch (error) {
    if(controller.signal.aborted)return;
    const status = Number(error?.response?.status || 502);
    const data = error?.response?.data;
    console.error(`[llm-gateway] request failed surface=${surface} status=${status} error=${String(error?.message || error).replace(/\s+/g, " ").slice(0, 300)}`);
    res.status(status >= 400 && status < 600 ? status : 502).json({
      error: {
        message: String(data?.error?.message || data?.message || error?.message || error).slice(0, 1000),
        type: "llm_gateway_error",
        router: routingStatus(),
      },
    });
  } finally {res.off('close',cancel);}
}

app.post("/v1/chat/completions", (req, res) => proxy("chat", req, res));
app.post("/v1/responses", (req, res) => proxy("responses", req, res));
app.post("/v1/messages", (req, res) => proxy("messages", req, res));

app.listen(port, "0.0.0.0", () => {
  console.log(`[llm-gateway] listening on :${port} mode=${routingStatus().mode}`);
});

module.exports = { safeUpstreamHeaders, requiredJsonKeys };
