# YouTube Shorts Facts Automation Pipeline

A self-hosted pipeline that researches, writes, voices, composes, validates and
uploads vertical YouTube Shorts. The production path is n8n + a FreeLLMAPI-first
LLM gateway (bounded paid-provider fallback) +
ElevenLabs + multi-source real-media retrieval + FFmpeg/Remotion.

**Current contracts and upgrade instructions:** [post-PR163 coherence fixes](docs/post-pr163-coherence.md).
This documents the independent hook experiment, lossless topic/render handoffs,
publication checkpoints, storage, and the one-time safe deployment migration.

## Production architecture

```text
n8n
  │
  ├─ LLM gateway — topic selection, script, visual contract (free-first; bounded direct fallback)
  ├─ ElevenLabs — TTS + alignment
  │
  └─ shorts-compose (Docker-internal HTTP, not the public proxy)
       ├─ multi-source retrieval
       │    Pexels / Pixabay / Unsplash / Wikipedia / Wikimedia /
       │    Openverse / NASA
       ├─ local semantic reranking
       ├─ VLM verification against exact scene contracts
       ├─ verified video-frame range materialization
       ├─ deterministic Remotion proof templates when preferred/available
       ├─ FFmpeg composition + captions + audio + BT.709 normalization
       └─ final rendered-pixel QA
            ├─ severe defect => explicit telemetry/warning
            └─ softer aesthetic issue => telemetry/warning
  │
  └─ YouTube Data API — upload + metadata + AI-content disclosure
```

The service still exposes a public Cloudflare-backed endpoint for external
access, but production n8n-to-compose calls use `http://shorts-compose:4000`
inside the Docker Compose network. This removes the reverse-proxy timeout from
B-roll resolution and job polling.

## Visual correctness model

Each non-outro scene receives an executable visual contract:

- `visual_claim`
- `required_entities`
- `required_actions`
- `required_relationships`
- `forbidden_visuals`
- `acceptable_visuals`
- `visual_proof_mode`

Real media is ranked against configured overall, semantic, entity, action and
relationship quality targets. Those targets drive search retries, early
acceptance and telemetry; they are **not publication eligibility gates**. If no
real candidate clears the targets, the resolver prefers an allowed deterministic
template fallback when one is available, otherwise it selects the highest-scoring
technically usable real asset. A low model score therefore cannot fail a run by
itself.

`literal_video` remains a strong preference rather than an availability gate.
Video candidates are downloaded locally before FFmpeg sampling, evaluated
through a chronological contact sheet, and only the contiguous verified sample
range is materialized. If no usable motion candidate survives, a still can be
used as graceful degradation rather than failing the scheduled Short.

The resolver returns `ok:false` only when there is genuinely no technically
usable media/template path left, not because an asset missed a quality score.

## Clean verified-real frames

`annotated_real` is retained as a compatibility name in the V4 schema, but its
V5 behavior is now a **clean verified-real still**. Vision verification is used
to decide whether the image proves the narration; internal model diagnostics are
not audience-facing graphics.

The final renderer therefore does **not** draw:

- computer-vision bounding boxes,
- object labels,
- leader lines or dots,
- coordinate-derived overlays,
- diagnostic footers,
- generated verification titles.

The complete verified image is preserved with `objectFit: contain` and a blurred
canvas-fill copy behind it for the 9:16 frame.

## Final QA is telemetry-only

Final QA runs against representative frames from the **completed video**, after
crop, captions and branding. It evaluates:

- semantic/entity/action/relationship correctness,
- readability,
- editorial cleanliness,
- mobile safe-area usage,
- caption integrity and collisions,
- clipped/hidden critical evidence,
- leaked debug or diagnostic UI.

The QA subsystem still classifies severe findings in `hard_issues` and softer
findings in `soft_issues`, but these are severity labels for diagnostics and
learning signals only. `compose.js` logs them and continues. A model timeout,
frame-sampling failure, low semantic score, debug-artifact judgement, clipping
judgement or layout judgement cannot block an otherwise successfully rendered
Short from reaching the upload path.

Only genuine production failures — for example inability to produce the final
MP4 or absence of any technically usable scene representation — stop the run.

## Durable run budgets

Paid vision limits and score-cache entries are persisted in the shared
`shorts_data` volume instead of process-local JavaScript `Map`s. An atomic
filesystem lock coordinates updates across processes using the same volume, so a
service restart or multiple compose workers cannot silently reset the run-wide
budget.

Relevant settings include:

```text
BROLL_RUN_MAX_VISION_CALLS=28
BROLL_FIRST_FRAME_MAX_VISION_CALLS=7
BROLL_SUPPORT_BASE_VISION_CALLS=3
BROLL_SUPPORT_BORROW_MAX_VISION_CALLS=7
BROLL_BUDGET_STATE_PATH=/app/data/visual_budget_state.json
BROLL_RESOLVE_DEADLINE_MS=135000
```

## One production build path

CI and deployment no longer carry separate copies of the transformation order.
Both call:

```bash
python3 scripts/build_production_artifacts.py --output-dir <dir>
```

The builder creates exactly three deployable artifacts plus a SHA-256 manifest:

```text
workflow.json
compose.js
brollResolver.js
manifest.json
```

CI builds them twice and requires identical manifests. Deployment consumes the
same artifact shape and does not reconstruct a different runtime through another
shell transform chain. Legacy upgrade modules are still used as migration stages
inside this single builder, but their order is centralized and final V5 policy is
validated before an artifact is considered deployable.

## Attribution

Third-party and retrieved-media credits are automatic. Each selected scene keeps
its `_attribution` metadata through the scene join. The production workflow
builds one deduplicated `Sources / credits` section and appends it to the YouTube
description.

The description also always includes:

```text
Motion icon assets: useanimations.com (CC BY 4.0)
```

The later YouTube disclosure/update call reuses the same
`publication_description`, so it cannot accidentally overwrite those credits.
See `LICENSES.md` for bundled-asset details.

## Repo layout

```text
n8n/workflow.json
    Human-readable base n8n workflow.

scripts/build_production_artifacts.py
    Authoritative production artifact builder used by both CI and deploy.

scripts/resolver_v5_runtime.py
    V5 resolver runtime finalization: video staging, adaptive verifier budget,
    target-based early acceptance and best-available publication fallback.

shorts-compose/
    compose.js
    brollResolver.js
    visualContract.js
    visualBudget.js
    finalVisualQa.js
    clipLibrary.js
    semanticReranker.js
    remotion/
    motion-assets/
    tests/

.github/workflows/
    quality-check.yml
    deploy.yml
    retrieval-recall-check.yml
    retrieval-telemetry-check.yml
    multiframe-phase3-check.yml
```

## Persistence

Named Docker volumes are used for state and outputs:

- `shorts_data`: topic history, durable visual budgets/cache, proven-clip
  metadata, model cache.
- `shorts_outputs`: rendered videos and verified-media cache.
- `n8n_data`: n8n state.

A Git clean/deployment cannot wipe these volumes.

## Development

Run JavaScript tests from the compose service:

```bash
cd shorts-compose
npm install
npm test
```

Preview Remotion compositions:

```bash
cd shorts-compose/remotion
npm install
npx remotion studio
```

Build the exact production artifacts locally:

```bash
python3 scripts/build_production_artifacts.py --output-dir /tmp/shorts-production
python3 scripts/preprod-audit.py
```

## Deployment

A push to `main` runs the self-hosted deployment workflow. It:

1. builds the exact production artifacts,
2. executes the V5 pre-production audit,
3. installs the generated `compose.js` and `brollResolver.js`,
4. rebuilds/restarts `shorts-compose`,
5. health-checks the service,
6. deploys the generated workflow to n8n through its API.

## External services

The pipeline can use OpenAI, ElevenLabs, Pexels, Pixabay and Unsplash API keys.
Wikipedia/Wikimedia/Openverse/NASA retrieval paths do not require the same paid
credentials. YouTube Data API/OAuth is used for publication.

## Known limitations

- Remotion/Chromium rendering is memory-intensive; the compose container is
  configured with a 6 GB limit.
- Music remains optional and depends on the tracks present in
  `shorts-compose/music/`.
- Custom YouTube thumbnails are not generated by this pipeline.
- `annotated_real` remains the legacy schema name even though its production
  semantics are now `verified_real`; renaming it would require a schema/version
  migration and is intentionally deferred to avoid breaking existing episode
  payloads.
