"""One automatic footage re-pick when final visual QA finds a critical mismatch.

Final QA stays non-blocking (NON_BLOCKING_FINAL_QA): a render is never
rejected because a judge dislikes it, and QA-tool outages change nothing.
What changes: when QA marks a scene critical (finalVisualQa.js - the first
frame fails its visual contract, or later footage clearly contradicts the
narration), compose re-runs the footage resolver for up to two flagged scenes
with the rejected clip excluded, and re-renders once. The second render
publishes whatever its own QA says.

Workflow side: Tag B-roll keeps the clip's original URL (a verified video is
served from a trimmed copy, so the copy's URL alone cannot exclude the source
clip), and the compose poll budget doubles to cover the extra render.
"""
from __future__ import annotations

MARKER = "FINAL_QA_REPICK_V1"
MAX_REPICK_SCENES = 2
POLL_ATTEMPTS_OLD = 75
POLL_ATTEMPTS_NEW = 150

QA_ANCHOR = (
    "      console.warn(`[job ${jobId}] Final visual QA warnings (publishing anyway): "
    "${summary || 'quality below preferred target'}`);\n    }\n"
)

REPICK_JS = r"""
    // FINAL_QA_REPICK_V1: one automatic retry for critical mismatches - re-pick
    // footage for the flagged scenes (first frame first) with the rejected clip
    // excluded, then re-render once. Never throws; the retry publishes as is.
    const criticalScenes = [...new Set((finalVisualQa.hard_issues || [])
      .filter((x) => x && x.critical === true && x.scene_index != null)
      .map((x) => Number(x.scene_index)))].sort((a, b) => a - b).slice(0, __MAX__);
    if (criticalScenes.length && !reqBody._qaRepickAttempted) {
      const repicked = [];
      for (const sceneIndex of criticalScenes) {
        const scene = scenes.find((s) => Number(s?.scene_index) === sceneIndex && !s?.template_data?.is_outro);
        if (!scene || scene.visual_source === 'template') continue;
        const rejected = [scene.video_url, ...(Array.isArray(scene.images) ? scene.images : []), scene.asset_original_url].filter(Boolean);
        const result = await resolveBroll({
          ...scene,
          query: scene.selected_query || scene.visual_claim || scene.point || '',
          description: scene.visual_claim || scene.point || '',
          subject: scene.named_subject || '',
          first_frame: sceneIndex === 0,
          run_id: `qa-repick-${jobId}`,
          exclude_urls: rejected,
        }).catch((e) => ({ ok: false, reason: String((e && e.message) || e) }));
        const replacement = result && (result.original_url || result.url);
        // A template swap could break the one-template cap or the real-footage
        // first frame, so only real footage counts as a re-pick.
        if (!result || result.ok !== true || result.type === 'template' || !result.url || rejected.includes(result.url) || rejected.includes(replacement)) {
          console.warn(`[job ${jobId}] FINAL_QA_REPICK_V1: no alternative footage for scene ${sceneIndex} (${(result && result.reason) || result?.type || 'same clip'})`);
          continue;
        }
        if (result.type === 'video') { scene.video_url = result.url; delete scene.images; }
        else { scene.images = [result.url]; delete scene.video_url; }
        scene.asset_original_url = replacement;
        scene.selected_query = result.selected_query || scene.selected_query;
        repicked.push({ scene_index: sceneIndex, rejected, replacement, source: result.source || null, score: result.score ?? null });
      }
      if (repicked.length) {
        console.warn(`[job ${jobId}] FINAL_QA_REPICK_V1: re-rendering with new footage for scene(s) ${repicked.map((r) => r.scene_index).join(', ')}`);
        const retry = await runComposeJob({ ...reqBody, data: scenes, _qaRepickAttempted: true }, jobId, newTmpDir());
        return { ...retry, qa_repick: { attempted: true, scenes: repicked, first_render_qa: finalVisualQa } };
      }
    }
""".replace("__MAX__", str(MAX_REPICK_SCENES))

TAG = "Tag B-roll"
POLL_IF = "If Under Max Poll Attempts"


def apply_compose(text: str) -> str:
    if MARKER in text:
        return text
    if text.count(QA_ANCHOR) != 1:
        raise RuntimeError(f"{MARKER}: final QA anchor not found exactly once in compose.js")
    for needed in ("const { resolveBroll } = require(", "function newTmpDir()", "async function runComposeJob(reqBody, jobId, tmpDir)"):
        if needed not in text:
            raise RuntimeError(f"{MARKER}: compose.js lost a dependency of the re-pick: {needed}")
    return text.replace(QA_ANCHOR, QA_ANCHOR + REPICK_JS)


def apply_workflow(workflow: dict) -> None:
    nodes = {n.get("name"): n for n in workflow.get("nodes", [])}
    p = nodes[TAG]["parameters"]
    if "asset_original_url" not in p["jsCode"]:
        old = "selection_reason:r.selection_reason||null};"
        if p["jsCode"].count(old) != 1:
            raise RuntimeError(f"{MARKER}: Tag B-roll output anchor not found exactly once")
        p["jsCode"] = p["jsCode"].replace(old, "selection_reason:r.selection_reason||null,asset_original_url:r.original_url||r.url||null};")
    cond = nodes[POLL_IF]["parameters"]["conditions"]["conditions"][0]
    if cond.get("rightValue") == POLL_ATTEMPTS_OLD:
        cond["rightValue"] = POLL_ATTEMPTS_NEW
    if cond.get("rightValue") != POLL_ATTEMPTS_NEW:
        raise RuntimeError(f"{MARKER}: unexpected compose poll limit {cond.get('rightValue')}")


def assert_applied(workflow: dict, compose_text: str) -> None:
    if MARKER not in compose_text or "NON_BLOCKING_FINAL_QA" not in compose_text or "publishing anyway" not in compose_text:
        raise RuntimeError(f"{MARKER}: compose.js lost the re-pick or the non-blocking QA policy")
    nodes = {n.get("name"): n for n in workflow.get("nodes", [])}
    if "asset_original_url" not in nodes[TAG]["parameters"]["jsCode"]:
        raise RuntimeError(f"{MARKER}: Tag B-roll does not keep the original clip URL")
