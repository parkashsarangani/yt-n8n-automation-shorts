"""Wire the EDITING_V1 edit layer (shorts-compose/editingEffects.js) into compose.

- zoom-settle transition at every content cut (applied to the concatenated
  video before captions, so text never zooms)
- hook headline, list item badges + soft flash, top progress bar (ASS overlays
  merged into the caption file)
- a quiet whoosh on list-item cuts (the payoff keeps its riser + impact)
- no fade from black at 0s (the first frame must be the image) and no fade to
  black at the end (a Short that loops cleanly invites a rewatch)

No effect changes a scene's duration, so captions, voice and payoff timing are
untouched. EDITING_EFFECTS=false restores the previous edit exactly.
"""
from __future__ import annotations

MARKER = "EDITING_V1"

PATCHES = [
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
        "    // EDITING_V1: zoom-settle at each cut, before captions so text stays still.\n"
        "    const cutZoom = editingEffects.cutZoomFilter(scenes, offsets);\n"
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
    for needed in ("const editingEffects = require(", "editingEffects.withEditingOverlays(",
                   "editingEffects.cutZoomFilter(", "editingEffects.itemWhooshTimes("):
        if needed not in text:
            raise RuntimeError(f"{MARKER}: compose.js lost {needed}")
