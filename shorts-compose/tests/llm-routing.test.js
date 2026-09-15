const test = require("node:test");
const assert = require("node:assert/strict");
const axios = require("axios");

// Configure deterministic test-only credentials/models before llmRouting is
// loaded because its runtime policy is intentionally read once at process boot.
process.env.FREELLMAPI_API_KEY = "freellmapi-test-key";
process.env.FREELLMAPI_TEXT_MODEL = "auto:fast";
process.env.FREELLMAPI_VISION_MODEL = "auto:smart";
process.env.FREELLMAPI_ANTHROPIC_MODEL = "claude-sonnet-4-5";
process.env.OPENAI_KEY = "paid-openai-test-key";
process.env.ANTHROPIC_KEY = "paid-anthropic-test-key";

const routing = require("../llmRouting");

test("detects vision payloads and chooses the configured free vision route", () => {
  const body = {
    model: "gpt-5.6-luna",
    messages: [{ role: "user", content: [
      { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
      { type: "text", text: "judge this" },
    ] }],
  };
  assert.equal(routing.hasVisionPayload(body), true);
  assert.equal(routing.freeModelFor(body, "chat"), "auto:smart");
});

test("plain text uses the configured free text model", () => {
  const body = { model: "gpt-5.6-luna", messages: [{ role: "user", content: "write a script" }] };
  assert.equal(routing.hasVisionPayload(body), false);
  assert.equal(routing.freeModelFor(body, "chat"), "auto:fast");
});

test("recognizes current OpenAI and Anthropic LLM endpoints", () => {
  assert.equal(routing.surfaceForUrl("https://api.openai.com/v1/chat/completions"), "chat");
  assert.equal(routing.surfaceForUrl("https://api.openai.com/v1/responses"), "responses");
  assert.equal(routing.surfaceForUrl("https://api.anthropic.com/v1/messages"), "messages");
  assert.equal(routing.surfaceForUrl("https://api.pexels.com/v1/search"), null);
});

test("free targets stay on the internal FreeLLMAPI service", () => {
  assert.equal(routing.freeTarget("chat"), "http://freellmapi:3001/v1/chat/completions");
  assert.equal(routing.freeTarget("responses"), "http://freellmapi:3001/v1/responses");
  assert.equal(routing.freeTarget("messages"), "http://freellmapi:3001/v1/messages");
});

test("direct targets remain available as an emergency rollback path", () => {
  assert.equal(routing.directTarget("chat"), "https://api.openai.com/v1/chat/completions");
  assert.equal(routing.directTarget("responses"), "https://api.openai.com/v1/responses");
  assert.equal(routing.directTarget("messages"), "https://api.anthropic.com/v1/messages");

  const direct = routing.makeDirectRequest("chat", {
    model: "gpt-original-paid-model",
    messages: [{ role: "user", content: "hello" }],
  });
  assert.equal(direct.data.model, "gpt-original-paid-model");
  assert.equal(direct.url, "https://api.openai.com/v1/chat/completions");
  assert.equal(direct.headers.Authorization, "Bearer paid-openai-test-key");
});

test("free request replaces the paid model but preserves the request payload", () => {
  const original = {
    model: "gpt-paid-model",
    response_format: { type: "json_object" },
    messages: [{ role: "user", content: "return JSON" }],
  };
  const free = routing.makeFreeRequest("chat", original);
  assert.equal(free.url, "http://freellmapi:3001/v1/chat/completions");
  assert.equal(free.data.model, "auto:fast");
  assert.deepEqual(free.data.response_format, original.response_format);
  assert.deepEqual(free.data.messages, original.messages);
  assert.equal(free.headers.Authorization, "Bearer freellmapi-test-key");
});

test("Anthropic-compatible requests keep the Claude wire contract through FreeLLMAPI", () => {
  const original = {
    model: "claude-original-paid-model",
    max_tokens: 256,
    messages: [{ role: "user", content: "return JSON" }],
  };
  const free = routing.makeFreeRequest("messages", original);
  assert.equal(free.url, "http://freellmapi:3001/v1/messages");
  assert.equal(free.data.model, "claude-sonnet-4-5");
  assert.equal(free.headers.Authorization, "Bearer freellmapi-test-key");
  assert.equal(free.headers["anthropic-version"], "2023-06-01");
  assert.equal(free.data.max_tokens, 256);
  assert.deepEqual(free.data.messages, original.messages);

  const direct = routing.makeDirectRequest("messages", original);
  assert.equal(direct.data.model, "claude-original-paid-model");
  assert.equal(direct.headers["x-api-key"], "paid-anthropic-test-key");
  assert.equal(direct.headers["anthropic-version"], "2023-06-01");
});

test("free leg is capped well under the overall budget (execution 848 regression)", () => {
  // Before this fix, the free leg used the FULL overall timeout, so a stuck
  // upstream inside FreeLLMAPI's own provider chain could consume the whole
  // client-side budget before the direct-paid fallback ever got a turn - n8n
  // aborted the call at exactly the same moment llm-gateway was still mid-
  // fallback (execution 848: "timeout of 120000ms exceeded").
  assert.equal(routing.freeLegTimeout(120000), 45000);
  assert.equal(routing.freeLegTimeout(30000), 30000, "never exceeds a smaller caller-supplied budget");
  assert.equal(routing.freeLegTimeout(undefined), 45000, "falls back to the configured default overall timeout");
});

test("fallback leg always gets the remaining budget, never zero", () => {
  assert.equal(routing.remainingFallbackTimeout(120000, 45000), 75000);
  // Even if the free leg somehow ran past the entire overall budget, the
  // direct fallback still gets a real window instead of an instantly-doomed
  // near-zero timeout.
  assert.equal(routing.remainingFallbackTimeout(120000, 130000), 5000);
  assert.equal(routing.remainingFallbackTimeout(120000, 0), 120000);
});

test("requestViaRouter reserves the remaining budget for the direct fallback after a free-leg failure", async () => {
  // requestViaRouter's HTTP calls go through a dedicated internal axios
  // instance (rawAxios), not the shared axios default export, so mocking is
  // done per-request via axios's own `adapter` config field rather than
  // axios.defaults.adapter (which only the preloaded-interceptor path below
  // observes).
  const FREE_LEG_DELAY_MS = 200;
  const seen = [];
  const mockAdapter = async (config) => {
    seen.push({ url: config.url, timeout: config.timeout });
    if (config.url.includes("freellmapi")) {
      await new Promise((resolve) => setTimeout(resolve, FREE_LEG_DELAY_MS));
      const err = new Error("timeout of 45000ms exceeded");
      err.config = config;
      throw err;
    }
    return { data: { choices: [{ message: { content: "ok" } }] }, status: 200, statusText: "OK", headers: {}, config };
  };

  const result = await routing.requestViaRouter(
    "chat",
    { model: "gpt-paid-model", messages: [] },
    { timeout: 120000, adapter: mockAdapter },
  );
  assert.equal(result.route, "direct");
  assert.equal(result.fallback, true);

  assert.equal(seen.length, 2);
  assert.ok(seen[0].url.includes("freellmapi"));
  assert.equal(seen[0].timeout, 45000, "free leg capped, not given the full 120000ms budget");
  assert.ok(seen[1].url.includes("api.openai.com"));
  // The free leg actually spent FREE_LEG_DELAY_MS before failing, so the
  // fallback must get (roughly) the overall budget minus that elapsed time -
  // never the full 120000ms again, and never near-zero either.
  assert.ok(seen[1].timeout <= 120000 - FREE_LEG_DELAY_MS + 50, `direct fallback should reflect elapsed time, got ${seen[1].timeout}`);
  assert.ok(seen[1].timeout > 100000, `direct fallback should still keep most of the budget, got ${seen[1].timeout}`);
});

test("preloaded axios interceptor really rewrites an existing direct OpenAI call", async () => {
  const originalAdapter = axios.defaults.adapter;
  let observed = null;
  axios.defaults.adapter = async (config) => {
    observed = config;
    return {
      data: { choices: [{ message: { content: "ok" } }] },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    };
  };

  try {
    await axios.post(
      "https://api.openai.com/v1/chat/completions",
      { model: "gpt-paid-model", messages: [{ role: "user", content: "hello" }] },
      { headers: { Authorization: "Bearer should-be-replaced", "content-type": "application/json" } },
    );
  } finally {
    axios.defaults.adapter = originalAdapter;
  }

  assert.ok(observed);
  assert.equal(observed.url, "http://freellmapi:3001/v1/chat/completions");
  const body = routing.parseBody(observed.data);
  assert.equal(body.model, "auto:fast");
  const authorization = typeof observed.headers?.get === "function"
    ? observed.headers.get("Authorization")
    : observed.headers?.Authorization;
  assert.equal(authorization, "Bearer freellmapi-test-key");
});
