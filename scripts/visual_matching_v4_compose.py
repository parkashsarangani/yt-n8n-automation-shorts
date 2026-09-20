#!/usr/bin/env python3
"""Finalize compose.js for V4/V5 proof renderers and rendered-pixel QA."""
from __future__ import annotations
import sys
from pathlib import Path

MARKER = "VISUAL_MATCHING_V4_COMPOSE"
NON_BLOCKING_QA_MARKER = "NON_BLOCKING_FINAL_QA"
BT709_MARKER = "PRODUCTION_BT709_RANGE_NORMALIZATION"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"{label} anchor missing")
    return text.replace(old, new, 1)


def patch_bt709(text: str) -> str:
    """Normalize full-range Remotion/FFmpeg frames to limited-range BT.709."""
    if BT709_MARKER in text:
        return text
    fade_anchor = "`[${vLabel}]fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5[final_v]`"
    fade_replacement = (
        "`[${vLabel}]fade=t=in:st=0:d=0.3,fade=t=out:st=${fadeOutStart.toFixed(2)}:d=0.5,"
        "scale=in_range=auto:out_range=tv,format=yuv420p,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[final_v]`"
    )
    text = replace_once(text, fade_anchor, fade_replacement, "BT.709 video filter")
    option_anchor = '      "-colorspace", "bt709",\n      "-pix_fmt", "yuv420p",'
    option_replacement = '      "-colorspace", "bt709",\n      "-color_range", "tv",\n      "-pix_fmt", "yuv420p",'
    text = replace_once(text, option_anchor, option_replacement, "BT.709 output range")
    marker_anchor = "    // Studio-grade final encoding:\n"
    text = replace_once(text, marker_anchor, f"    // {BT709_MARKER}: normalize final output to limited-range BT.709.\n" + marker_anchor, "BT.709 marker")
    return text


def upgrade(text: str) -> str:
    if MARKER in text:
        return text
    import_anchor = 'const { resolveBroll } = require("./brollResolver");'
    text = replace_once(text, import_anchor, import_anchor + '\nconst { reviewFinalVideo } = require("./finalVisualQa");', "final QA import")

    # V5 passes the complete typed resolver request. This avoids per-field
    # forwarding drift as the visual contract evolves.
    start = text.find('app.post("/resolve-broll", async (req, res) => {')
    if start < 0:
        raise ValueError("resolve-broll endpoint missing")
    end = text.find('\n});', start)
    if end < 0:
        raise ValueError("resolve-broll endpoint end missing")
    end += len('\n});')
    endpoint = '''app.post("/resolve-broll", async (req, res) => {
  try {
    const result = await resolveBroll(req.body || {});
    res.json(result);
  } catch (e) {
    res.json({ ok: false, reason: "error", error: String((e && e.message) || e) });
  }
});'''
    text = text[:start] + endpoint + text[end:]

    text = replace_once(
        text,
        '    const allowedTemplates = new Set(["kinetic_text", "stat_reveal", "comparison"]);',
        '    const allowedTemplates = new Set(["kinetic_text", "stat_reveal", "comparison", "map", "timeline", "diagram", "annotated_real"]);',
        "V4 template preflight",
    )

    old_map = '''  const compositionMap = {
    stat_reveal: "StatReveal",
    comparison: "Comparison",
    kinetic_text: "KineticText",
  };'''
    new_map = '''  const compositionMap = {
    stat_reveal: "StatReveal",
    comparison: "Comparison",
    kinetic_text: "KineticText",
    map: "MapVisual",
    timeline: "TimelineVisual",
    diagram: "DiagramVisual",
    annotated_real: "AnnotatedReal",
  };'''
    text = replace_once(text, old_map, new_map, "structured composition map")

    old_props = '''  } else if (templateName === "kinetic_text") {
    props.line = templateData?.line || "";
  }
'''
    new_props = '''  } else if (templateName === "kinetic_text") {
    props.line = templateData?.line || "";
  } else if (templateName === "map") {
    props.title = templateData?.title || "Where it happens";
    props.locations = Array.isArray(templateData?.locations) ? templateData.locations : [];
    props.connections = Array.isArray(templateData?.connections) ? templateData.connections : [];
  } else if (templateName === "timeline") {
    props.title = templateData?.title || "How it unfolded";
    props.events = Array.isArray(templateData?.events) ? templateData.events : [];
  } else if (templateName === "diagram") {
    props.title = templateData?.title || "How it connects";
    props.nodes = Array.isArray(templateData?.nodes) ? templateData.nodes : [];
    props.edges = Array.isArray(templateData?.edges) ? templateData.edges : [];
  } else if (templateName === "annotated_real") {
    props.imageUrl = templateData?.imageUrl || "";
    props.imageWidth = Number(templateData?.imageWidth || 1080);
    props.imageHeight = Number(templateData?.imageHeight || 1920);
  }
'''
    text = replace_once(text, old_props, new_props, "structured template props")

    old = '''    finalCmd.output(finalPath);
    await run(finalCmd);

    await fsp.copyFile(finalPath, outputFullPath);
    console.log(`[job ${jobId}] Done -> ${outputFullPath}`);
    return { success: true, output_path: outputFullPath, job_id: jobId };'''
    new = f'''    finalCmd.output(finalPath);
    await run(finalCmd);

    // VISUAL_MATCHING_V4_COMPOSE / {NON_BLOCKING_QA_MARKER}: inspect actual
    // final pixels after crop, captions and branding, but never reject an
    // otherwise valid render because a model, sampler, score, or layout judge
    // dislikes it. QA is telemetry only; the publish schedule remains reliable.
    const finalVisualQa = await reviewFinalVideo(finalPath, scenes, durations);
    if (!finalVisualQa.passed) {{
      const summary = (finalVisualQa.issues || []).map((x) => `scene ${{x.scene_index}}: ${{x.problem}} (severity=${{x.severity || 'n/a'}}, semantic=${{x.semantic_match ?? 'n/a'}}, score=${{x.score ?? 'n/a'}})`).join('; ');
      console.warn(`[job ${{jobId}}] Final visual QA warnings (publishing anyway): ${{summary || 'quality below preferred target'}}`);
    }}

    await fsp.copyFile(finalPath, outputFullPath);
    console.log(`[job ${{jobId}}] Done -> ${{outputFullPath}}`);
    return {{ success: true, output_path: outputFullPath, job_id: jobId, final_visual_qa: finalVisualQa }};'''
    text = replace_once(text, old, new, "final render QA")
    text = patch_bt709(text)
    return text + f"\n// {MARKER}\n"


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: visual_matching_v4_compose.py INPUT [OUTPUT]")
    src = Path(sys.argv[1]); dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src
    out = upgrade(src.read_text(encoding="utf-8"))
    for marker in (MARKER, NON_BLOCKING_QA_MARKER, BT709_MARKER):
        if marker not in out:
            raise RuntimeError(f"compose production guarantee missing: {marker}")
    if "Final visual QA rejected catastrophic render defects" in out:
        raise RuntimeError("blocking final visual QA was reintroduced")
    dst.write_text(out, encoding="utf-8")
    print(f"{MARKER} compose written to {dst}")


if __name__ == "__main__":
    main()