// Final multimodal QA: inspect representative pixels from the completed Short
// after captions/branding and separate catastrophic publish blockers from softer
// aesthetic warnings. A wrong/missing visual, unreadable captions, clipping or
// leaked debug UI blocks publication; mild polish issues remain telemetry only.
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const axios = require("axios");
const { execFile } = require("child_process");
const ffmpegPath = require("ffmpeg-static");
const { buildVisualContract, buildScoringTarget, passesSemanticGate } = require("./visualContract");

const OPENAI_KEY = process.env.OPENAI_KEY || "";
const MODEL = process.env.BROLL_FINAL_QA_MODEL || process.env.BROLL_VISION_MODEL || "gpt-5.6-luna";
const ENABLED = String(process.env.BROLL_FINAL_QA_ENABLED || "true").toLowerCase() !== "false";
const REQUIRE_KEY = String(process.env.BROLL_FINAL_QA_REQUIRE_KEY || "true").toLowerCase() !== "false";
const MIN_SCORE = Math.max(0, Math.min(100, Number(process.env.BROLL_FINAL_QA_MIN_SCORE || 78)));
const HARD_SCORE = Math.max(0, Math.min(MIN_SCORE, Number(process.env.BROLL_FINAL_QA_HARD_SCORE || 65)));
const SAMPLE_COUNT = Math.max(1, Math.min(5, Number(process.env.BROLL_FINAL_QA_SAMPLE_FRAMES || 3)));
const HARD_READABILITY = Math.max(0, Math.min(100, Number(process.env.BROLL_FINAL_QA_HARD_READABILITY || 55)));
const SOFT_READABILITY = Math.max(HARD_READABILITY, Math.min(100, Number(process.env.BROLL_FINAL_QA_SOFT_READABILITY || 70)));
const HARD_LAYOUT = Math.max(0, Math.min(100, Number(process.env.BROLL_FINAL_QA_HARD_LAYOUT || 55)));
const SOFT_LAYOUT = Math.max(HARD_LAYOUT, Math.min(100, Number(process.env.BROLL_FINAL_QA_SOFT_LAYOUT || 70)));
// Below this semantic match, footage is treated as contradicting the narration.
const CRITICAL_SEMANTIC = Math.max(0, Math.min(HARD_SCORE, Number(process.env.BROLL_FINAL_QA_CRITICAL_SEMANTIC || 50)));

function run(args) {
  return new Promise((resolve, reject) => {
    execFile(ffmpegPath, args, { timeout: 90000, maxBuffer: 4 * 1024 * 1024 }, (err) => err ? reject(err) : resolve());
  });
}

async function sampleSceneContactSheet(videoPath, sceneStartSec, sceneDurationSec) {
  const duration = Math.max(0.2, Number(sceneDurationSec || 0));
  const out = path.join(os.tmpdir(), `finalqa-${crypto.randomUUID()}.jpg`);
  const fractions = SAMPLE_COUNT === 1 ? [0.5] : Array.from({ length: SAMPLE_COUNT }, (_, i) => 0.2 + (0.6 * i / (SAMPLE_COUNT - 1)));
  const times = fractions.map((f) => Number((Math.max(0, sceneStartSec) + Math.max(0.05, duration * f)).toFixed(3)));
  const frames = [];
  try {
    for (let i = 0; i < times.length; i++) {
      const framePath = `${out}.${i}.jpg`;
      await run(["-hide_banner", "-loglevel", "error", "-y", "-ss", String(times[i]), "-i", videoPath, "-frames:v", "1", "-vf", "scale=420:360:force_original_aspect_ratio=decrease,pad=420:360:(ow-iw)/2:(oh-ih)/2:color=black", "-q:v", "3", framePath]);
      frames.push(framePath);
    }
    if (!frames.length) return null;
    if (frames.length === 1) {
      const buf = await fsp.readFile(frames[0]);
      return { imageUrl: `data:image/jpeg;base64,${buf.toString("base64")}`, timestamps: times };
    }
    await run(["-hide_banner", "-loglevel", "error", "-y", ...frames.flatMap((p) => ["-i", p]), "-filter_complex", `${frames.map((_, i) => `[${i}:v]`).join("")}hstack=inputs=${frames.length}`, "-frames:v", "1", "-q:v", "3", out]);
    const buf = await fsp.readFile(out);
    return { imageUrl: `data:image/jpeg;base64,${buf.toString("base64")}`, timestamps: times };
  } catch {
    return null;
  } finally {
    await Promise.all([...frames, out].map((p) => fsp.unlink(p).catch(() => {})));
  }
}

function parseJsonObject(text) {
  const raw = String(text || "").trim();
  const a = raw.indexOf("{"), b = raw.lastIndexOf("}");
  if (a < 0 || b <= a) return null;
  try { return JSON.parse(raw.slice(a, b + 1)); } catch { return null; }
}

function score(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : 0;
}

function normalizeJudgement(raw = {}) {
  return {
    semantic_match: score(raw.semantic_match),
    entity_match: raw.entity_match == null ? 0 : score(raw.entity_match),
    action_match: raw.action_match == null ? 0 : score(raw.action_match),
    relationship_match: raw.relationship_match == null ? 0 : score(raw.relationship_match),
    readability: score(raw.readability),
    editorial_cleanliness: score(raw.editorial_cleanliness),
    safe_area: score(raw.safe_area),
    caption_integrity: score(raw.caption_integrity),
    overall: score(raw.overall),
    debug_artifact: raw.debug_artifact === true,
    critical_content_clipped: raw.critical_content_clipped === true,
    problem: String(raw.problem || "").slice(0, 700),
  };
}

async function judgeScene(imageUrl, contract, timestamps) {
  if (!OPENAI_KEY || !imageUrl) return null;
  const prompt = [
    "Judge these chronological RENDERED frames from ONE scene in a published-style YouTube Short. Inspect the final pixels, including captions, branding and overlays.",
    `Visual contract: ${buildScoringTarget(contract)}`,
    `Frame timestamps: ${(timestamps || []).join(", ")} seconds.`,
    `Forbidden visuals: ${(contract.forbidden_visuals || []).join("; ")}`,
    "Do not reward generic topic relevance. Verify that the exact subject/action/relationship required by the narration is actually visible across the sampled frames.",
    "Also audit editorial cleanliness: no debug/CV bounding boxes, coordinate labels, diagnostic footers, developer text, accidental UI, excessive callout clutter, caption/logo collisions, text outside safe areas, or important subject matter cropped off-screen.",
    "caption_integrity measures whether burned-in captions are readable, unclipped and not materially obscuring the key subject. safe_area measures whether important text/branding stays comfortably inside the mobile frame. editorial_cleanliness is 100 only for a clean audience-facing edit with no diagnostic-looking overlays.",
    "Set debug_artifact=true for any visible internal/debug annotation, computer-vision box/label/leader line, diagnostic footer, developer message, or other non-editorial artifact. Set critical_content_clipped=true only when important text or the key visual evidence is materially cut off or hidden.",
    "Return ONLY JSON with these exact keys: semantic_match, entity_match, action_match, relationship_match, readability, editorial_cleanliness, safe_area, caption_integrity, overall as integer 0-100 scores; debug_artifact and critical_content_clipped as booleans; problem as a short string (empty when none).",
    contract.is_deterministic_template
      ? "This is intentionally a designed text/graphic card, not a photograph. Do not penalize it for lacking photographic entities/actions; judge semantic fit, legibility, layout and cleanliness."
      : "If a required entity/action/relationship is absent from every sampled frame, score that dimension below 70.",
  ].join(" ");
  try {
    const r = await axios.post("https://api.openai.com/v1/chat/completions", {
      model: MODEL,
      max_completion_tokens: 360,
      reasoning_effort: "none",
      response_format: { type: "json_object" },
      messages: [{ role: "user", content: [
        { type: "image_url", image_url: { url: imageUrl } },
        { type: "text", text: prompt },
      ] }],
    }, { timeout: 45000, headers: { Authorization: `Bearer ${OPENAI_KEY}`, "content-type": "application/json" } });
    const parsed = parseJsonObject(r.data?.choices?.[0]?.message?.content || "");
    return parsed ? normalizeJudgement(parsed) : null;
  } catch {
    return null;
  }
}

function issue(sceneIndex, severity, problem, normalized = {}) {
  return {
    scene_index: sceneIndex,
    severity,
    problem,
    score: normalized.overall ?? null,
    semantic_match: normalized.semantic_match ?? null,
    entity_match: normalized.entity_match ?? null,
    action_match: normalized.action_match ?? null,
    relationship_match: normalized.relationship_match ?? null,
    readability: normalized.readability ?? null,
    editorial_cleanliness: normalized.editorial_cleanliness ?? null,
    safe_area: normalized.safe_area ?? null,
    caption_integrity: normalized.caption_integrity ?? null,
  };
}

function classifyRenderedIssue(sceneIndex, normalized, contract) {
  const hard = [];
  const soft = [];
  const effectiveScore = contract.is_deterministic_template ? normalized.semantic_match : normalized.overall;

  const hardSemanticOk = passesSemanticGate(normalized, contract, {
    semantic: HARD_SCORE,
    entity: Math.max(HARD_SCORE, 70),
    action: Math.max(HARD_SCORE, 68),
    relationship: Math.max(HARD_SCORE, 68),
  });
  const publishSemanticOk = passesSemanticGate(normalized, contract, {
    semantic: MIN_SCORE,
    entity: Math.max(MIN_SCORE, 80),
    action: MIN_SCORE,
    relationship: MIN_SCORE,
  });

  if (normalized.debug_artifact) hard.push(issue(sceneIndex, "hard", "visible debug/diagnostic overlay leaked into final video", normalized));
  if (normalized.critical_content_clipped) hard.push(issue(sceneIndex, "hard", "critical visual evidence or text is clipped/hidden", normalized));
  if (!hardSemanticOk || effectiveScore < HARD_SCORE) {
    const contractFailure = issue(sceneIndex, "hard", normalized.problem || "rendered visual materially fails the scene contract", normalized);
    // FINAL_QA_REPICK_V1: critical means worth one automatic footage re-pick.
    // The first frame always qualifies (it decides the scroll); later scenes
    // only when the footage clearly contradicts the narration. Designed
    // templates are deterministic, so a re-pick cannot improve them.
    contractFailure.critical = !contract.is_deterministic_template
      && (Number(sceneIndex) === 0 || Number(normalized.semantic_match) < CRITICAL_SEMANTIC);
    hard.push(contractFailure);
  }
  if (normalized.readability < HARD_READABILITY) hard.push(issue(sceneIndex, "hard", "final frame readability is too low", normalized));
  if (normalized.safe_area < HARD_LAYOUT) hard.push(issue(sceneIndex, "hard", "important content violates the mobile safe area", normalized));
  if (normalized.caption_integrity < HARD_LAYOUT) hard.push(issue(sceneIndex, "hard", "captions are clipped, colliding or materially obscuring the subject", normalized));

  if (!hard.length) {
    if (!publishSemanticOk || effectiveScore < MIN_SCORE) soft.push(issue(sceneIndex, "soft", normalized.problem || "rendered visual is below the preferred quality target", normalized));
    if (normalized.readability < SOFT_READABILITY) soft.push(issue(sceneIndex, "soft", "readability is below the preferred target", normalized));
    if (normalized.editorial_cleanliness < 68) soft.push(issue(sceneIndex, "soft", "editorial cleanliness/clutter is below the preferred target", normalized));
    if (normalized.safe_area < SOFT_LAYOUT) soft.push(issue(sceneIndex, "soft", "safe-area composition is below the preferred target", normalized));
    if (normalized.caption_integrity < SOFT_LAYOUT) soft.push(issue(sceneIndex, "soft", "caption placement is below the preferred target", normalized));
  }
  return { hard, soft, effectiveScore };
}

function structuralSceneIssues(scene, sceneIndex) {
  const out = [];
  if (scene?.template_data?.render_debug_overlays === true || scene?.template_data?.debug === true) {
    out.push(issue(sceneIndex, "hard", "scene metadata explicitly enables debug overlays"));
  }
  if (scene?.template_name === "annotated_real" && Array.isArray(scene?.template_data?.annotations) && scene.template_data.annotations.length) {
    // Backwards-compatible payloads are tolerated because the renderer ignores
    // them, but flag them softly so stale annotation producers are visible.
    out.push(issue(sceneIndex, "soft", "legacy annotation metadata reached composition; renderer ignored it"));
  }
  return out;
}

async function reviewFinalVideo(videoPath, scenes, durations) {
  if (!ENABLED) return { enabled: false, passed: true, hard_failed: false, issues: [], hard_issues: [], soft_issues: [] };
  if (!OPENAI_KEY) {
    const missing = issue(null, REQUIRE_KEY ? "hard" : "soft", "final_visual_qa_missing_openai_key");
    return REQUIRE_KEY
      ? { enabled: true, passed: false, hard_failed: true, checked: 0, issues: [missing], hard_issues: [missing], soft_issues: [] }
      : { enabled: true, passed: false, hard_failed: false, checked: 0, skipped: "missing_openai_key", issues: [missing], hard_issues: [], soft_issues: [missing] };
  }
  if (!fs.existsSync(videoPath)) {
    const missing = issue(null, "hard", "final_video_missing");
    return { enabled: true, passed: false, hard_failed: true, checked: 0, issues: [missing], hard_issues: [missing], soft_issues: [] };
  }

  let offset = 0;
  const hardIssues = [];
  const softIssues = [];
  const results = [];
  for (let i = 0; i < scenes.length; i++) {
    const scene = scenes[i];
    const duration = Math.max(0, Number(durations[i] || 0));
    const sceneStart = offset;
    offset += duration;
    if (scene?.template_data?.is_outro) continue;
    const sceneIndex = scene?.scene_index ?? i;

    for (const structural of structuralSceneIssues(scene, sceneIndex)) {
      (structural.severity === "hard" ? hardIssues : softIssues).push(structural);
    }

    const contract = buildVisualContract({
      scene_index: sceneIndex,
      narration: scene?.narration || scene?.point || "",
      subject: scene?.named_subject || "",
      global_subject: scene?.global_subject || "",
      description: scene?.visual_claim || scene?.visual_prompt || scene?.selected_query || "",
      visual_claim: scene?.visual_claim,
      required_entities: scene?.required_entities,
      required_actions: scene?.required_actions,
      required_relationships: scene?.required_relationships,
      forbidden_visuals: scene?.forbidden_visuals,
      acceptable_visuals: scene?.acceptable_visuals,
      visual_proof_mode: scene?.visual_proof_mode,
      template_name: scene?.template_name,
      template_data: scene?.template_data,
    });

    const sheet = await sampleSceneContactSheet(videoPath, sceneStart, duration);
    if (!sheet) {
      hardIssues.push(issue(sceneIndex, "hard", "final_visual_qa_frame_sampling_failed"));
      continue;
    }
    const normalized = await judgeScene(sheet.imageUrl, contract, sheet.timestamps);
    if (!normalized) {
      hardIssues.push(issue(sceneIndex, "hard", "final_visual_qa_model_failed"));
      continue;
    }

    const classified = classifyRenderedIssue(sceneIndex, normalized, contract);
    hardIssues.push(...classified.hard);
    softIssues.push(...classified.soft);
    results.push({ scene_index: sceneIndex, timestamps: sheet.timestamps, effective_score: classified.effectiveScore, ...normalized });
  }

  const contentScenes = scenes.filter((s) => !s?.template_data?.is_outro).length;
  if (!contentScenes) hardIssues.push(issue(null, "hard", "final_visual_qa_no_content_scenes"));
  const failedChecks = hardIssues.filter((x) => String(x.problem || "").includes("frame_sampling_failed") || String(x.problem || "").includes("model_failed")).length;
  if (results.length + failedChecks < contentScenes) hardIssues.push(issue(null, "hard", "final_visual_qa_incomplete_scene_coverage"));

  const issues = [...hardIssues, ...softIssues];
  return {
    enabled: true,
    passed: issues.length === 0,
    hard_failed: hardIssues.length > 0,
    threshold: MIN_SCORE,
    hard_threshold: HARD_SCORE,
    checked: results.length,
    expected: contentScenes,
    issues,
    hard_issues: hardIssues,
    soft_issues: softIssues,
    results,
  };
}

module.exports = {
  reviewFinalVideo,
  sampleSceneContactSheet,
  judgeScene,
  parseJsonObject,
  normalizeJudgement,
  classifyRenderedIssue,
  structuralSceneIssues,
};
