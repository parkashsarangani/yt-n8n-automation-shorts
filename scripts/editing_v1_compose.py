"""Wire the edit layer (shorts-compose/editingEffects.js) and the technical QA
(shorts-compose/technicalQa.js) into compose.

Edit layer (EDITING_V2 inside editingEffects.js):
- role-based motion: opening punch-in + first-second contrast lift, strong
  punch at the payoff, micro zoom on list items, mostly hard cuts elsewhere
  (applied to the concatenated video before captions, so text never zooms)
- hook headline, list item badges + soft flash, thin progress bar, captions
  sized by scene role (ASS overlays merged into the caption file)
- a quiet whoosh on list-item cuts (the payoff keeps its riser + impact)
- no fade from black at 0s (the first frame must be the image) and no fade to
  black at the end (a Short that loops cleanly invites a rewatch)

No effect changes a scene's duration, so captions, voice and payoff timing are
untouched. EDITING_EFFECTS=false restores the previous edit exactly.

Technical QA (TECHNICAL_QA_V1): one ffmpeg pass over the final file before it
is handed to upload. Only an unusable video is rejected; the rest is logged.
"""
from __future__ import annotations

MARKER = "EDITING_V1"

PATCHES = [
    (
        "technical qa require",
        'const { reviewFinalVideo } = require("./finalVisualQa");\n',
        'const { reviewFinalVideo } = require("./finalVisualQa");\n'
        '// TECHNICAL_QA_V1: deterministic pre-upload checks (stream, size, black, loudness).\n'
        'const { checkFinalVideo: technicalQaCheck } = require("./technicalQa");\n',
    ),
    (
        "require",
        'const { reviewFinalVideo } = require("./finalVisualQa");\n',
        'const { reviewFinalVideo } = require("./finalVisualQa");\n'
        '// EDITING_V1: transitions, headline, item badges, progress bar.\n'
        'const editingEffects = require("./editingEffects");\n',
    ),
    (
        "caption overlays",
        "    const assContent = buildAssFromAlignment(scenes, offsets, comment_hook, contentDuration, caption_mode);\n",
        "    const assContent = editingEffects.withEditingOverlays(\n"
        "      buildAssFromAlignment(scenes, offsets, comment_hook, contentDuration, caption_mode),\n"
        "      { scenes, offsets, durations, scriptSnapshot: reqBody.script_snapshot, contentDuration }\n"
        "    );\n",
    ),
    (
        "item whooshes",
        "      if (sfxAvailable.impact) sfxEvents.push({ type: \"impact\", time: emphasisOffset, volume: creative_format === 'comparison_reveal' ? 0.24 : 0.18 });\n    }\n",
        "      if (sfxAvailable.impact) sfxEvents.push({ type: \"impact\", time: emphasisOffset, volume: creative_format === 'comparison_reveal' ? 0.24 : 0.18 });\n    }\n"
        "    // EDITING_V1: a quiet whoosh into each list item (not every cut, and not\n"
        "    // the payoff, which already has its riser + impact).\n"
        "    if (sfxAvailable.whoosh) {\n"
        "      editingEffects.itemWhooshTimes(scenes, offsets, reqBody.script_snapshot?.promised_points, emphasisIdx)\n"
        "        .forEach((time) => sfxEvents.push({ type: \"whoosh\", time, volume: 0.10 }));\n"
        "    }\n",
    ),
    (
        "cut zoom",
        '    let vLabel = "0:v";\n',
        '    let vLabel = "0:v";\n'
        "    // EDITING_V1: role-based motion before captions so text stays still.\n"
        "    const cutZoom = editingEffects.cutZoomFilter(scenes, offsets, reqBody.script_snapshot);\n"
        "    if (cutZoom) {\n"
        "      videoFilterSegments.push(`[0:v]${cutZoom}[v_cut]`);\n"
        '      vLabel = "v_cut";\n'
        "    }\n",
    ),
    (
        "no black fades",
        "fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5,",
        "${editingEffects.ENABLED ? '' : `fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5,`}",
    ),
    (
        "technical qa check",
        "    await fsp.copyFile(finalPath, outputFullPath);\n",
        "    // TECHNICAL_QA_V1: only an unusable video (no audio/video stream, wrong\n"
        "    // size, black opening, mostly black) is rejected; the rest is logged.\n"
        "    const technicalQa = await technicalQaCheck(finalPath).catch((e) => ({ policy: 'technical-qa-v1', passed: true, fatal: [], warnings: [`technical QA unavailable: ${e.message}`] }));\n"
        "    if (technicalQa.warnings.length) console.warn(`[job ${jobId}] Technical QA warnings: ${technicalQa.warnings.join('; ')}`);\n"
        "    if (!technicalQa.passed) throw new Error(`Technical QA rejected the render: ${technicalQa.fatal.join('; ')}`);\n"
        "    await fsp.copyFile(finalPath, outputFullPath);\n",
    ),
    (
        "technical qa result",
        "return { success: true, output_path: outputFullPath, job_id: jobId, final_visual_qa: finalVisualQa,",
        "return { success: true, output_path: outputFullPath, job_id: jobId, final_visual_qa: finalVisualQa, technical_qa: technicalQa,",
    ),
]


def apply_compose(text: str) -> str:
    if "const editingEffects = require(" in text:
        return text
    for label, old, new in PATCHES:
        if text.count(old) != 1:
            raise RuntimeError(f"{MARKER}: compose anchor for {label} found {text.count(old)}x, expected 1x")
        text = text.replace(old, new)
    return text


def assert_applied(text: str) -> None:
    for needed in ("const editingEffects = require(", "editingEffects.withEditingOverlays(", "technicalQaCheck(finalPath)",
                   "editingEffects.cutZoomFilter(", "editingEffects.itemWhooshTimes("):
        if needed not in text:
            raise RuntimeError(f"{MARKER}: compose.js lost {needed}")
