# FreeLLMAPI routing for the Shorts pipeline

This repository uses FreeLLMAPI as a **free-first inference router**, not as a
local model runtime. The Docker container runs the gateway/dashboard locally;
the selected LLM/VLM inference still runs at the configured upstream providers.

FreeLLMAPI's own project describes itself as a single-user/local-first router.
Each upstream provider has its own free-tier terms and limits. Configure only
providers whose terms fit your use of the channel.

## Runtime architecture

```text
n8n LLM nodes ---------------------> llm-gateway:3100
                                         |
shorts-compose OpenAI/VLM calls --axios interceptor
                                         |
                          LLM_ROUTER_MODE=freellmapi
                                         |
                                freellmapi:3001
                                         |
                    free hosted provider fallback chain

                          on outage/quota/error
                                         |
                     paid OpenAI/Anthropic direct fallback
```

The production workflow contains no provider credentials. The authoritative
artifact builder rewrites external OpenAI/Anthropic n8n HTTP nodes to the
Docker-internal `llm-gateway` and removes their n8n credentials.

`shorts-compose` preloads `llmRouting.js` through `NODE_OPTIONS`, so existing
OpenAI calls in B-roll verification, final rendered QA, competitor mining and
the performance strategist are routed without duplicating provider logic in
those modules.

## First-time setup

1. Generate a stable 32-byte encryption key. Keep it for the lifetime of the
   FreeLLMAPI SQLite volume:

   ```bash
   openssl rand -hex 32
   ```

2. Add it as the GitHub Actions secret `FREELLMAPI_ENCRYPTION_KEY`.

3. Deploy/re-run the production workflow. Deployment pulls the pinned
   `ghcr.io/tashfeenahmed/freellmapi:v0.9.5` image and exposes its dashboard
   only at `127.0.0.1:3001` on the server. `v0.9.5` was the latest published
   release when this integration was finalized (published 2026-09-03). The CI
   job verifies that the pinned container tag actually exists. Update this pin
   deliberately through a PR after validating a newer release.

4. Reach the dashboard with an SSH tunnel rather than exposing it publicly:

   ```bash
   ssh -L 3001:127.0.0.1:3001 <server>
   ```

   Then open `http://localhost:3001` locally.

5. Add the free-provider API keys you want FreeLLMAPI to use. A practical
   starting pool is Groq, Google, Cerebras and Mistral, subject to their current
   terms/quotas.

6. Copy FreeLLMAPI's unified `freellmapi-...` API key from its Keys page and
   store it as the GitHub Actions secret `FREELLMAPI_API_KEY`.

7. Re-run deployment. The internal gateway will now route primary calls through
   FreeLLMAPI.

For reproducible bootstrap, FreeLLMAPI also accepts declarative JSON through
`FREEAPI_CONFIG_JSON`. This repository maps the optional secret
`FREELLMAPI_CONFIG_JSON` into that setting. Do not commit provider keys to Git.

## Routing controls

Repository/environment variables:

```text
LLM_ROUTER_MODE=freellmapi
LLM_ROUTER_FAIL_OPEN_TO_DIRECT=true
FREELLMAPI_TEXT_MODEL=auto:smart
FREELLMAPI_VISION_MODEL=auto:smart
FREELLMAPI_BASE_URL=http://freellmapi:3001/v1
LLM_ROUTER_TIMEOUT_MS=120000
```

`LLM_ROUTER_TIMEOUT_MS` is only the gateway's fallback default. Every LLM node
in the generated n8n workflow instead sends its own `x-llm-timeout-ms` header
(its configured node timeout minus a 5s safety margin - see
`coherence_contracts.py`), so the gateway's actual per-request deadline always
tracks whatever timeout that specific node is set to, rather than a value that
can silently drift out of sync with it.

FreeLLMAPI's own `auto:smart` routing tries multiple upstream providers in
series internally, and any one of them can individually run close to the full
request deadline. `requestViaRouter` (`llmRouting.js`) gives the free leg up
to 55% of the remaining deadline and, on failure, gives the paid-direct
fallback whatever time is actually left - never the full budget again, so the
two legs together can never exceed the caller's deadline. The deadline is
also wired to an `AbortController`: if the n8n client disconnects (its own
timeout fires, or the execution is cancelled) the in-flight upstream request
is aborted immediately and no fallback is attempted, instead of wastefully
continuing a call nothing is waiting on.

`auto:smart` lets FreeLLMAPI choose among enabled free models. Vision requests
are detected from OpenAI-compatible image content and can be routed separately
with `FREELLMAPI_VISION_MODEL`. Once a stable model/profile has been benchmarked,
pin either variable to that model/profile for more consistent scoring.

Anthropic-compatible requests keep a Claude-family model name because
FreeLLMAPI maps Claude families to its free pool by default. Direct fallback
retains the original provider/model payload rather than the FreeLLMAPI rewrite.

## Immediate rollback

No code revert and no n8n workflow edit is required.

Set:

```text
LLM_ROUTER_MODE=direct
```

and restart/redeploy `llm-gateway` and `shorts-compose`. All requests will bypass
FreeLLMAPI and use the existing paid provider path.

The default `LLM_ROUTER_FAIL_OPEN_TO_DIRECT=true` is a second safety layer. In
free mode, if FreeLLMAPI is not configured, unavailable, rate-limited or rejects
a request, the same request is retried through the direct provider. This keeps
the scheduled-publishing policy intact while still making free inference the
normal path.

Set `LLM_ROUTER_FAIL_OPEN_TO_DIRECT=false` only if strict zero-paid-call behavior
is more important than keeping the Shorts pipeline running.

## Observability

The internal gateway returns:

```text
X-LLM-Route: freellmapi|direct
X-LLM-Fallback: none|direct
X-Routed-Via: <FreeLLMAPI provider/model when available>
X-Fallback-Attempts: <FreeLLMAPI upstream attempts when available>
```

FreeLLMAPI also records its own provider usage/health in its dashboard. These
headers make it possible to add per-Short cost/fallback telemetry later without
changing provider-specific code.

## Security

- FreeLLMAPI is bound to server loopback only (`127.0.0.1:3001`) for host access.
- n8n talks only to `llm-gateway` on the private Docker network.
- Provider keys and the FreeLLMAPI unified key are never embedded in generated
  n8n workflow JSON.
- FreeLLMAPI's SQLite state is on the persistent `freellmapi_data` Docker volume.
- Keep `FREELLMAPI_ENCRYPTION_KEY` stable; changing/losing it can make stored
  provider credentials unreadable.
