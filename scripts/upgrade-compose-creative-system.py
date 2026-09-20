#!/usr/bin/env python3
"""Patch shorts-compose/compose.js with creative-system behavior.

The compose service is intentionally kept readable as its existing source; this
small deterministic transform is validated in CI and applied before the Docker
build. It makes engagement/outro optional, honors caption modes, passes the
visual director's multi-query commissioning metadata into the b-roll resolver,
and adds production preflight/media-format hardening.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "CREATIVE_SYSTEM_COMPOSE"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"could not patch {label}: anchor not found")
    return text.replace(old, new, 1)


def upgrade(text: str) -> str:
    if MARKER in text:
        return text

    text = text.replace(
        "function buildAssFromAlignment(scenes, offsets, commentHook, totalDuration) {",
        "function buildAssFromAlignment(scenes, offsets, commentHook, totalDuration, captionMode = 'karaoke') {",
        1,
    )
    text = replace_once(
        text,
        "  const WORDS_PER_CHUNK = 2;",
        "  // CREATIVE_SYSTEM_COMPOSE: caption density is commissioned per Short.\n  const WORDS_PER_CHUNK = captionMode === 'minimal' ? 4 : captionMode === 'key_phrases' ? 3 : 2;",
        "caption density",
    )

    old_pop = """              const hlStyle = /\\d/.test(item.text) ? "CaptionKey" : "CaptionHL";
              return `{\\\\r${hlStyle}\\\\fscx128\\\\fscy128\\\\t(0,100,\\\\fscx105\\\\fscy105)}${item.text}{\\\\r}`;
"""
    new_pop = """              const hlStyle = /\\d/.test(item.text) ? "CaptionKey" : "CaptionHL";
              if (captionMode === 'minimal') return `{\\\\r${hlStyle}}${item.text}{\\\\r}`;
              const pop = captionMode === 'key_phrases' ? 116 : 128;
              return `{\\\\r${hlStyle}\\\\fscx${pop}\\\\fscy${pop}\\\\t(0,100,\\\\fscx105\\\\fscy105)}${item.text}{\\\\r}`;
"""
    text = replace_once(text, old_pop, new_pop, "caption animation")

    text = replace_once(
        text,
        "    const { hook, caption_style, comment_hook, data: scenes } = reqBody;",
        "    const { hook, caption_style, caption_mode = 'karaoke', comment_hook, engagement_mode = 'none', creative_format = 'documentary_cinematic', data: scenes } = reqBody;",
        "compose payload creative fields",
    )

    old_scene_guard = """    if (!Array.isArray(scenes) || scenes.length === 0) {
      throw new Error("No scenes provided");
    }

    const mood = caption_style || "neutral";
"""
    new_scene_guard = """    if (!Array.isArray(scenes) || scenes.length === 0) {
      throw new Error("No scenes provided");
    }

    // CREATIVE_SYSTEM_COMPOSE: fail malformed payloads before starting any
    // Remotion/FFmpeg work. Promise.allSettled is still used for legitimate
    // parallel renders, but a known-bad scene no longer leaves the job in
    // `processing` while unrelated sibling renders finish.
    const allowedTemplates = new Set(["kinetic_text", "stat_reveal", "comparison"]);
    scenes.forEach((scene, i) => {
      if (!scene?.audio?.audio_base64 && !scene?.audio?.audio_url) {
        throw new Error(`Scene ${i} missing audio`);
      }
      if (scene?.visual_source === "template") {
        if (!scene.template_name) throw new Error(`Scene ${i}: visual_source=template but no template_name`);
        if (!allowedTemplates.has(scene.template_name)) throw new Error(`Unknown template_name "${scene.template_name}"`);
        return;
      }
      if (!scene?.video_url && (!Array.isArray(scene?.images) || scene.images.length === 0)) {
        throw new Error(`Scene ${i}: missing images array (and no video_url)`);
      }
    });

    const mood = caption_style || "neutral";
"""
    text = replace_once(text, old_scene_guard, new_scene_guard, "compose scene preflight")

    old_outro = """    const hasScriptOutro = scenes.some((s) => s?.template_data?.is_outro);
    if (!hasScriptOutro) {
      const outroAudioBase64 = await generateSilentAudioBase64(OUTRO_DURATION_SEC);
      scenes.push({
        scene_index: scenes.length,
        visual_source: "template",
        template_name: "kinetic_text",
        template_data: { line: reqBody.outro_line || DEFAULT_OUTRO_LINE, is_outro: true },
        audio: { audio_base64: outroAudioBase64 },
      });
    }

    // The payoff/reveal scene = the last content scene before the outro card.
    // It gets a stronger emphasis push-in (video) and a riser+impact accent.
    const emphasisIdx = scenes.length - 2;
"""
    new_outro = """    const hasScriptOutro = scenes.some((s) => s?.template_data?.is_outro);
    const requestedOutro = String(reqBody.outro_line || '').trim();
    const wantsShareOutro = ['share_only', 'comment_and_share'].includes(engagement_mode);
    // No mandatory end-card. If the editor removed the share ask, the Short
    // ends on its kicker instead of being forced through a generic CTA card.
    if (!hasScriptOutro && requestedOutro && wantsShareOutro) {
      const outroAudioBase64 = await generateSilentAudioBase64(OUTRO_DURATION_SEC);
      scenes.push({
        scene_index: scenes.length,
        visual_source: "template",
        template_name: "kinetic_text",
        template_data: { line: requestedOutro, is_outro: true },
        audio: { audio_base64: outroAudioBase64 },
      });
    }

    // Payoff/reveal = last CONTENT scene, whether or not an outro exists.
    const emphasisIdx = Math.max(0, scenes.reduce((last, s, i) => s?.template_data?.is_outro ? last : i, -1));
"""
    text = replace_once(text, old_outro, new_outro, "optional compose outro")

    text = replace_once(
        text,
        "    const outroDuration = durations.length > 1 ? durations[durations.length - 1] : 0;\n    const contentDuration = totalVideoDuration - outroDuration;\n    const assContent = buildAssFromAlignment(scenes, offsets, comment_hook, contentDuration);",
        "    const lastIsOutro = Boolean(scenes[scenes.length - 1]?.template_data?.is_outro);\n    const outroDuration = lastIsOutro && durations.length > 1 ? durations[durations.length - 1] : 0;\n    const contentDuration = totalVideoDuration - outroDuration;\n    const assContent = buildAssFromAlignment(scenes, offsets, comment_hook, contentDuration, caption_mode);",
        "caption/outro timing",
    )

    old_sfx = """    const sfxEvents = [];
    const emphasisOffset = offsets[emphasisIdx];
    if (emphasisIdx >= 1 && emphasisOffset != null) {
      if (sfxAvailable.riser) sfxEvents.push({ type: "riser", time: Math.max(0, emphasisOffset - 1.3), volume: 0.18 });
      if (sfxAvailable.impact) sfxEvents.push({ type: "impact", time: emphasisOffset, volume: 0.22 });
    }
"""
    new_sfx = """    const sfxEvents = [];
    const emphasisOffset = offsets[emphasisIdx];
    const wantsPayoffAccent = !['minimal_proof', 'archival_history'].includes(creative_format);
    if (wantsPayoffAccent && emphasisIdx >= 1 && emphasisOffset != null) {
      if (creative_format !== 'kinetic_data' && sfxAvailable.riser) sfxEvents.push({ type: "riser", time: Math.max(0, emphasisOffset - 1.3), volume: 0.16 });
      if (sfxAvailable.impact) sfxEvents.push({ type: "impact", time: emphasisOffset, volume: creative_format === 'comparison_reveal' ? 0.24 : 0.18 });
    }
"""
    text = replace_once(text, old_sfx, new_sfx, "format-aware payoff SFX")

    text = replace_once(
        text,
        "      audioFilters.push(`[${musicIdx}:a]aloop=loop=-1:size=2e9,volume=0.13[music]`);",
        "      const musicVolume = creative_format === 'minimal_proof' ? 0.07 : creative_format === 'archival_history' ? 0.10 : 0.13;\n      audioFilters.push(`[${musicIdx}:a]aloop=loop=-1:size=2e9,volume=${musicVolume}[music]`);",
        "format-aware music",
    )

    # Scene-level muxes should always emit a predictable audio format. This
    # avoids encoder-dependent 96 kHz/mono intermediates and makes concat/final
    # mixing deterministic across Debian/system FFmpeg and the old static build.
    concat_marker = "// ---------------------------------------------------------------------------\n// Scene Concatenation"
    if concat_marker not in text:
        raise ValueError("could not patch scene audio format: concat marker not found")
    before_concat, after_concat = text.split(concat_marker, 1)
    before_concat = before_concat.replace(
        '"-c:a", "aac", "-b:a", "192k"',
        '"-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"',
    )
    text = before_concat + concat_marker + after_concat

    old_endpoint = """    const { query, subject, description } = req.body || {};
    const result = await resolveBroll({ query, subject, description });
"""
    new_endpoint = """    const { query, queries, alternate_queries, subject, description, scene_index, first_frame, creative_format } = req.body || {};
    const result = await resolveBroll({ query, queries, alternate_queries, subject, description, scene_index, first_frame, creative_format });
"""
    text = replace_once(text, old_endpoint, new_endpoint, "b-roll resolver payload")

    # Permanent marker used by deployment/CI assertions.
    text = text.replace(
        "// ---------------------------------------------------------------------------\n// Async Job API",
        f"// {MARKER}\n// ---------------------------------------------------------------------------\n// Async Job API",
        1,
    )
    return text


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: upgrade-compose-creative-system.py INPUT_COMPOSE [OUTPUT_COMPOSE]")
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src
    upgraded = upgrade(src.read_text(encoding="utf-8"))
    dst.write_text(upgraded, encoding="utf-8")
    print(f"creative system compose written to {dst}")


if __name__ == "__main__":
    main()
