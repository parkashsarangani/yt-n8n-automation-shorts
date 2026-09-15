# Post-PR163 coherence fixes

PR #165 closed several gaps that opened up after the topic-dedup and
LLM-gateway-timeout work in PRs #162/#163/#164: places where an earlier stage
produced a correct result but a later stage didn't reliably carry it through,
or where the deploy pipeline itself could step on a live render. This
document is the reference for those contracts and for the one-time migration
the change required. `scripts/coherence_contracts.py` is the authoritative
implementation - it runs as the last step of `build_production_artifacts.py`
and asserts every contract below at build time.

## Independent hook/outro experiment (`hook-opening-v2`)

The retention-experiment framework (PR #161) assigns each execution an
independent hook-framing arm (`concrete_question` vs `direct_contradiction`)
and, separately, an outro arm (`current_outro` vs `no_outro`), so the two
experiments can be read as a clean 2x2 grid. Both assignments used the same
FNV-1a hash of `$execution.id` for their low bit, so the two arms were
correlated instead of independent - two of the four grid cells were
structurally under-represented.

`assignHookExperiment` (`shorts-compose/retentionPolicy.js`) now salts its
hash with `hook-opening-v2:` and runs a full avalanche mix (borrowed from
MurmurHash3's finalizer) before taking the low bit, so it no longer shares
parity with the outro assignment's own hash. `HOOK_EXPERIMENT` bumped from
`hook-opening-v1` to `hook-opening-v2` to mark the change; `experimentReport`
groups by that id, so the small number of videos already assigned under v1
are not read alongside v2 data. That's an intentional, one-time loss of a
few early samples in exchange for a report that isn't quietly confounded -
the alternative was letting a real correlation bug keep shaping every future
sample.

`shorts-compose/tests/coherence-contracts.test.js` verifies all four
hook x outro cells receive roughly even assignment over a large sample.

## Lossless topic and render handoffs

**Topic identity is bound to the approved shortlist.** `Extract Generated
Topic` previously trusted whatever `Claude: Commission Topic Shortlist`
echoed back for a candidate's identity fields (`canonical_key`,
`subject_key`, `strategy_arm`, etc.), with no check that the topic itself was
still one of the candidates `Deduplicate Topic Pool` had actually approved.
A commissioning response that paraphrased or substituted a topic could
therefore slip past the whole no-repeat gate PRs #162/#163 built. The node
now matches each candidate's `topic` string against
`$('Deduplicate Topic Pool').first().json.shortlist`, binds the approved
entry's identity fields onto it, and throws
`Commissioning did not select an approved nonduplicate topic` if nothing
matches. The commissioning prompt was also told explicitly to return
candidates from the supplied shortlist unmodified.

**Merge carries the full upstream object, not a hand-picked subset.**
`Merge By scene_index (not position)`'s per-scene and top-level return
objects previously reconstructed only the fields each earlier patch had
needed, which meant a field added by a later change could be silently
dropped by an earlier `return {...}` that didn't know about it. The merge
now spreads the full upstream object (`{...v, scene_index: ...}` and similar)
before setting the fields it actually needs to override, and explicitly pulls
`caption_mode`, `creative_format`, and `engagement_mode` from the accepted
script snapshot so they reach the compose payload.

**Per-scene retrieval diagnostics survive to publication telemetry.**
`Tag B-roll` now attaches a `retrieval` object to each scene (source,
score, quality-gate pass, candidate count, search rounds, queries tried,
vision calls used, selection reason). `Log Published Video` reads this back
as `retrieval_telemetry`, and computes `asset_quality_min`/`asset_quality_avg`
from the real per-scene `asset_score` values instead of the `null` placeholder
that shipped with the original growth-v2 telemetry fields.

**A single title-length policy.** Every place that truncated a title used
`.slice(0, 50)` in one spot and `.slice(0, 60)` in another; both now use 60,
matching the commissioned title's actual maximum length.

## Publication checkpoint before optional post-upload work

The upload chain now runs `YouTube: Upload Draft -> Log Published Video ->
Disclose AI-Generated Content` (previously disclosure/first-comment could run
- and fail - before the publication was durably logged). Writing the
performance-history row immediately after a successful upload means a later,
optional step failing (a disclosure-comment API hiccup, for instance) can
never cost the video its analytics telemetry. `Log Published Video` and
`Disclose AI-Generated Content` both retry transient failures
(`retryOnFail: true, maxTries: 3, waitBetweenTries: 2000`) rather than
silently dropping the call. Because disclosure now runs after an intervening
node, its upload-ID reference reads explicitly from
`$('YouTube: Upload Draft').first().json` instead of the ambient `$json`,
which would otherwise resolve to `Log Published Video`'s own output.

`Get Shorts to Measure`'s output is checked by a new
`Skip Empty Measurement Plan` node before the analytics call: an empty
portfolio short-circuits to no items (nothing to measure yet), while an
actual plan-fetch error still throws, so a real failure can never be
mistaken for "nothing to do."

## Storage

**`shorts-compose/jsonStore.js`** adds a cross-process file lock
(`fs.open(file.lock, 'wx')`, 15s timeout, 20ms poll) around read-modify-write
of the JSON history files (topic history, performance history), plus atomic
writes (write to a temp file, then rename). Concurrent scheduled executions
writing the same file can no longer race and lose an update, and a corrupted
file causes the transaction to reject rather than being silently overwritten
with reconstructed data.

**`shorts-compose/jobLifecycle.js`** makes render jobs durable across a
service restart: each job's state is written to `<dir>/<uuid>.json` on every
transition, and on startup any job still marked `processing` is finalized as
`failed` (`Render interrupted by service restart`) instead of vanishing.
It also adds the admin surface the deploy pipeline needs (below):
`GET /health/jobs`, `POST /admin/drain`, `POST /admin/resume` (all
loopback-only), and `DELETE /cleanup/:id` restricted to the exact
`short_<uuid>` filename pattern - never an arbitrary path.

**`shorts-compose/sourcePool.js`** balances asset-source diversity when
capping a scene's candidate pool: it reserves an exact Wikipedia hit first
(when present), then round-robins the remaining sources so every populated
provider keeps at least one slot in the final selection, rather than letting
one high-volume source crowd the rest out.

## The one-time safe deployment migration

Before this change, `deploy.yml` removed the running `shorts-compose`
container unconditionally (`docker rm -f`) before starting the new one. If a
scheduled execution's render was still in flight when a deploy landed, that
`docker rm -f` killed the in-flight HTTP request the same way a network
outage would - the exact failure mode diagnosed from execution 839's
"socket hang up." The workflow now:

1. Confirms the container it's about to remove actually belongs to the
   current Compose project (never removes a container just because it holds
   port 4000).
2. Calls the new `POST /admin/drain` to stop the service accepting new
   `/compose` requests, then polls `GET /health/jobs` for up to 30 minutes
   waiting for `active` renders to reach zero before removing the container.
3. Resumes the service (`POST /admin/resume`) and fails the deploy if
   draining doesn't complete in time, rather than removing a container with
   work still running.

**The running production container at the time PR #165 shipped predates
`/admin/drain`**, so step 2 fails safely on first contact (`Drain endpoint
unavailable`) and the workflow stops without touching anything. This is a
one-time gap: once the new image is running, every future deploy has the
endpoint and drains automatically. To get through it once:

1. Confirm no render is actually in progress (check n8n's execution history
   for anything with no `stoppedAt`, and check `shorts-compose`'s recent
   logs for render activity).
2. Dispatch `deploy.yml` manually with the `legacy_idle_confirmed` input set:
   ```bash
   gh workflow run deploy.yml -f legacy_idle_confirmed=true
   ```
   This is only accepted on a manual `workflow_dispatch` - an ordinary push
   to `main` cannot set it, so the unattended path can never silently skip
   the drain wait.

No further action is needed after that first successful deploy; subsequent
pushes to `main` drain normally through step 2 above.
