// EDITING_V2: viewer-facing edit layer for the social-psychology niche.
//
// Everything here works on the already-timed scene list, so no effect ever
// changes a scene's duration - captions, voice and payoff timing stay exact.
//
// Each scene gets a ROLE from data the script already carries:
//   hook    - scene 0
//   payoff  - payoff.resolved_in_scene
//   item    - a promised list item (promised_points)
//   body    - everything else
// and the role decides the edit, so effects are deliberate, not uniform:
//   - opening: 106% -> 100% punch-in over the first 0.8s, plus a touch more
//     contrast/sharpness during the first second
//   - cuts: payoff = strong punch-in, item = micro zoom, body = mostly hard
//     cuts with an occasional slow push (most cuts stay hard cuts)
//   - captions: larger on the hook, largest on the payoff
//   - hook headline, item badges ("2/4") with a soft flash, thin progress bar
// Kill switch: EDITING_EFFECTS=false.

const ENABLED = String(process.env.EDITING_EFFECTS || "true").toLowerCase() !== "false";
const W = 1080;
const H = 1920;
const FPS = 30;
const HEADLINE_MAX_SEC = 2.8;
const FLASH_MS = 140;
const GOLD = "&H0046BEFF";   // channel gold, style colour (&HAABBGGRR)
const GOLD_TAG = "&H46BEFF&"; // same gold as an override tag (&HBBGGRR&)

// Motion profiles: [peak extra zoom, seconds, shape]. "decay" starts zoomed
// and eases back to 100% (a punch); "push" grows slowly from 100%.
const PROFILES = {
  open: [0.06, 0.8, "decay"],
  punch: [0.10, 0.35, "decay"],
  micro: [0.04, 0.25, "decay"],
  push: [0.03, 1.6, "push"],
  hard: null,
};

function toAssTime(sec) {
  const s = Math.max(0, Number(sec) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = (s % 60).toFixed(2);
  return `${h}:${String(m).padStart(2, "0")}:${String(rest).padStart(5, "0")}`;
}

function fromAssTime(text) {
  const m = String(text || "").match(/^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$/);
  return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) : null;
}

function assText(text) {
  return String(text || "").replace(/[\\{}]/g, "").replace(/\s+/g, " ").trim();
}

// Break a headline into at most three lines of similar length (~20 chars
// max), so a long title never ends on an orphaned single word.
function wrapHeadline(text, maxChars = 20) {
  const clean = assText(text);
  const words = clean.split(" ").filter(Boolean);
  if (!words.length) return "";
  const lineCount = Math.min(3, Math.ceil(clean.length / maxChars));
  const target = Math.ceil(clean.length / lineCount);
  const lines = [];
  let line = "";
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (line && candidate.length > target && lines.length < lineCount - 1) { lines.push(line); line = word; }
    else line = candidate;
  }
  lines.push(line);
  return lines.join("\\N");
}

const isOutro = (scene) => Boolean(scene && scene.template_data && scene.template_data.is_outro);

// Positions (array indexes) of the scenes that deliver each promised item,
// in promise order. Unknown or outro scenes are dropped.
function itemPositions(scenes, promisedPoints) {
  if (!Array.isArray(promisedPoints)) return [];
  const out = [];
  for (const idx of promisedPoints) {
    const pos = scenes.findIndex((s) => !isOutro(s) && Number(s && s.scene_index) === Number(idx));
    if (pos >= 0 && !out.includes(pos)) out.push(pos);
  }
  return out;
}

// Role of every scene position: hook | payoff | item | body | outro.
function sceneRoles(scenes, snapshot) {
  const snap = snapshot || {};
  const items = new Set(itemPositions(scenes, snap.promised_points));
  const payoffIdx = snap.payoff && snap.payoff.resolved_in_scene;
  return scenes.map((scene, pos) => {
    if (isOutro(scene)) return "outro";
    if (pos === 0) return "hook";
    if (payoffIdx != null && Number(scene && scene.scene_index) === Number(payoffIdx)) return "payoff";
    if (items.has(pos)) return "item";
    return "body";
  });
}

// Motion plan: one entry per moment that gets movement. Body cuts are mostly
// hard cuts; every third body cut gets a slow push so long stretches of hard
// cuts still breathe. The outro card keeps its own animation.
function motionPlan(scenes, offsets, snapshot) {
  const roles = sceneRoles(scenes, snapshot);
  const plan = [];
  if (scenes.length && roles[0] !== "outro") plan.push({ time: 0, profile: "open", role: "hook" });
  let bodyCuts = 0;
  for (let i = 1; i < scenes.length; i++) {
    if (roles[i] === "outro" || !Number.isFinite(offsets[i])) continue;
    let profile = "hard";
    if (roles[i] === "payoff") profile = "punch";
    else if (roles[i] === "item") profile = "micro";
    else profile = bodyCuts++ % 3 === 1 ? "push" : "hard";
    plan.push({ time: offsets[i], profile, role: roles[i] });
  }
  return plan;
}

// Kept for callers/tests that only need the cut times.
function cutTimes(scenes, offsets) {
  const times = [];
  for (let i = 1; i < scenes.length; i++) {
    if (!isOutro(scenes[i]) && Number.isFinite(offsets[i])) times.push(offsets[i]);
  }
  return times;
}

// Filter chain for the concatenated video (before captions): a zoompan
// driven by the motion plan, then the first-second contrast/sharpness lift.
// Returns null when there is nothing to do.
function cutZoomFilter(scenes, offsets, snapshot) {
  if (!ENABLED || !Array.isArray(scenes) || !scenes.length) return null;
  const t = `(in/${FPS})`;
  const terms = [];
  for (const step of motionPlan(scenes, offsets, snapshot)) {
    const p = PROFILES[step.profile];
    if (!p) continue;
    const [amount, dur, shape] = p;
    const c = step.time.toFixed(3);
    const end = (step.time + dur).toFixed(3);
    terms.push(shape === "decay"
      ? `if(between(${t},${c},${end}),${amount}*pow(1-(${t}-${c})/${dur},2),0)`
      : `if(between(${t},${c},${end}),${amount}*(${t}-${c})/${dur},0)`);
  }
  if (!terms.length) return null;
  const zoom = `zoompan=z='1+${terms.join("+")}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=${W}x${H}:fps=${FPS}`;
  const opening = `eq=contrast=1.06:saturation=1.05:enable='lt(t,1)',unsharp=5:5:0.5:5:5:0.0:enable='lt(t,1)'`;
  return `${zoom},${opening}`;
}

const CAPTION_FORMAT = "Inter Bold,{size},{colour},&H000000FF,&H40000000,&H80000000,0,0,0,0,{scale},{scale},0,0,1,3,4,2,60,60,420,1";
const captionStyle = (name, size, colour, scale) =>
  `Style: ${name},${CAPTION_FORMAT.replace("{size}", size).replace("{colour}", colour).replace(/\{scale\}/g, scale)}`;

const STYLES = [
  `Style: Headline,Inter Black,84,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,8,70,70,300,1`,
  `Style: ItemBadge,Inter Black,96,${GOLD},&H000000FF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,7,70,70,250,1`,
  `Style: Overlay,Inter Bold,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1`,
  // Role-sized captions: hook a step up, payoff the largest. The active-word
  // highlight styles grow with them so the karaoke pop stays proportional.
  captionStyle("CaptionHook", 70, "&H00FFFFFF", 100),
  captionStyle("CaptionPayoff", 76, "&H00FFFFFF", 100),
  captionStyle("CaptionHLBig", 80, "&H0096E0FF", 105),
  captionStyle("CaptionKeyBig", 80, "&H0080FF60", 105),
];

// Re-style caption lines by the role of the scene they fall in.
function resizeCaptions(events, scenes, offsets, durations, snapshot) {
  const roles = sceneRoles(scenes, snapshot);
  const roleAt = (sec) => {
    for (let i = scenes.length - 1; i >= 0; i--) {
      if (Number.isFinite(offsets[i]) && sec >= offsets[i] - 0.01) return roles[i];
    }
    return "body";
  };
  return events.split("\n").map((line) => {
    const m = line.match(/^Dialogue: (\d+),([^,]+),([^,]+),Caption,(.*)$/);
    if (!m) return line;
    const role = roleAt(fromAssTime(m[2]));
    const style = role === "payoff" ? "CaptionPayoff" : role === "hook" ? "CaptionHook" : null;
    if (!style) return line;
    const rest = m[4].replace(/\\rCaptionHL\b/g, "\\rCaptionHLBig").replace(/\\rCaptionKey\b/g, "\\rCaptionKeyBig");
    return `Dialogue: ${m[1]},${m[2]},${m[3]},${style},${rest}`;
  }).join("\n");
}

// Extra ASS events for the edit layer.
function overlayEvents({ scenes, offsets, durations, scriptSnapshot, contentDuration }) {
  if (!ENABLED || !Array.isArray(scenes) || !scenes.length) return { styles: [], events: "" };
  const snapshot = scriptSnapshot || {};
  let events = "";

  // Hook headline over the first scene: pops in, fades out before the cut.
  const headline = wrapHeadline(snapshot.title || snapshot.hook || "");
  if (headline && Number(durations[0]) > 0.8) {
    const end = Math.min(Number(durations[0]) - 0.1, HEADLINE_MAX_SEC);
    events += `Dialogue: 3,${toAssTime(0)},${toAssTime(end)},Headline,,0,0,0,,{\\fad(0,180)\\fscx85\\fscy85\\t(0,160,\\fscx104\\fscy104)\\t(160,260,\\fscx100\\fscy100)}${headline}\n`;
  }

  // Item badges + flash on every promised list item.
  const items = itemPositions(scenes, snapshot.promised_points);
  items.forEach((pos, k) => {
    const start = offsets[pos];
    const end = start + Number(durations[pos] || 0);
    if (!Number.isFinite(start) || !(end > start)) return;
    events += `Dialogue: 3,${toAssTime(start)},${toAssTime(end - 0.05)},ItemBadge,,0,0,0,,{\\fad(80,120)\\fscx0\\fscy0\\t(0,170,\\fscx118\\fscy118)\\t(170,280,\\fscx100\\fscy100)}${k + 1}/${items.length}\n`;
    if (pos > 0) {
      events += `Dialogue: 4,${toAssTime(start)},${toAssTime(start + FLASH_MS / 1000)},Overlay,,0,0,0,,{\\an7\\pos(0,0)\\p1\\1c&HFFFFFF&\\alpha&H90&\\t(0,${FLASH_MS},\\alpha&HFF&)}m 0 0 l ${W} 0 ${W} ${H} 0 ${H}{\\p0}\n`;
    }
  });

  // Thin, mostly transparent progress bar across the top, over the content.
  const total = Number(contentDuration) || 0;
  if (total > 1) {
    const ms = Math.round(total * 1000);
    events += `Dialogue: 2,${toAssTime(0)},${toAssTime(total)},Overlay,,0,0,0,,{\\an7\\pos(0,0)\\p1\\1c${GOLD_TAG}\\alpha&H60&\\clip(0,0,0,6)\\t(0,${ms},\\clip(0,0,${W},6))}m 0 0 l ${W} 0 ${W} 6 0 6{\\p0}\n`;
  }

  return { styles: STYLES, events };
}

// Merge the edit layer into a complete ASS document.
function withEditingOverlays(assContent, context) {
  const { styles, events } = overlayEvents(context);
  if (!styles.length && !events) return assContent;
  const marker = "\n[Events]\n";
  if (!assContent.includes(marker)) return assContent;
  const [head, body] = assContent.split(marker);
  const { scenes, offsets, durations, scriptSnapshot } = context;
  return `${head}\n${styles.join("\n")}${marker}${resizeCaptions(body, scenes, offsets, durations, scriptSnapshot)}${events}`;
}

// Times (seconds) of list-item cuts that get a soft whoosh, skipping the
// payoff scene, which already has its riser + impact.
function itemWhooshTimes(scenes, offsets, promisedPoints, emphasisIdx) {
  if (!ENABLED) return [];
  return itemPositions(scenes, promisedPoints)
    .filter((pos) => pos > 0 && pos !== emphasisIdx && Number.isFinite(offsets[pos]))
    .map((pos) => Math.max(0, offsets[pos] - 0.12));
}

module.exports = {
  ENABLED,
  PROFILES,
  wrapHeadline,
  itemPositions,
  sceneRoles,
  motionPlan,
  cutTimes,
  cutZoomFilter,
  resizeCaptions,
  overlayEvents,
  withEditingOverlays,
  itemWhooshTimes,
};
