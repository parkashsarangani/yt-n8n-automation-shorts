// EDITING_V1: viewer-facing edit layer for the social-psychology niche.
//
// Everything here works on the already-timed scene list, so no effect ever
// changes a scene's duration - captions, voice and payoff timing stay exact.
//   - zoom-settle transition: each new scene lands slightly zoomed in and eases
//     back over ~0.3s (applied once to the concatenated video, before captions)
//   - hook headline: the title as on-screen text over the first scene
//   - item badges ("2/4") on every promised list item, with a soft white flash
//   - thin progress bar across the top
// Kill switch: EDITING_EFFECTS=false.

const ENABLED = String(process.env.EDITING_EFFECTS || "true").toLowerCase() !== "false";
const W = 1080;
const H = 1920;
const FPS = 30;
const CUT_ZOOM = 0.07;       // +7% at the cut
const CUT_ZOOM_SEC = 0.3;    // eased back to 1.0 over this long
const HEADLINE_MAX_SEC = 2.8;
const FLASH_MS = 140;
const GOLD = "&H0046BEFF";   // channel gold, style colour (&HAABBGGRR)
const GOLD_TAG = "&H46BEFF&"; // same gold as an override tag (&HBBGGRR&)

function toAssTime(sec) {
  const s = Math.max(0, Number(sec) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = (s % 60).toFixed(2);
  return `${h}:${String(m).padStart(2, "0")}:${String(rest).padStart(5, "0")}`;
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

// Cut times (seconds) where the zoom-settle plays: every content cut after the
// first frame. The outro card keeps its own animation.
function cutTimes(scenes, offsets) {
  const times = [];
  for (let i = 1; i < scenes.length; i++) {
    if (!isOutro(scenes[i]) && Number.isFinite(offsets[i])) times.push(offsets[i]);
  }
  return times;
}

// zoompan filter for the concatenated video, or null when there is no cut.
// Quadratic ease-out from 1+CUT_ZOOM back to 1.0 after each cut.
function cutZoomFilter(scenes, offsets) {
  if (!ENABLED) return null;
  const times = cutTimes(scenes, offsets);
  if (!times.length) return null;
  const t = `(in/${FPS})`;
  const terms = times.map((c) => {
    const start = c.toFixed(3);
    return `if(between(${t},${start},${(c + CUT_ZOOM_SEC).toFixed(3)}),${CUT_ZOOM}*pow(1-(${t}-${start})/${CUT_ZOOM_SEC},2),0)`;
  });
  const z = `1+${terms.join("+")}`;
  return `zoompan=z='${z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=${W}x${H}:fps=${FPS}`;
}

const STYLES = [
  `Style: Headline,Inter Black,84,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,8,70,70,300,1`,
  `Style: ItemBadge,Inter Black,96,${GOLD},&H000000FF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,7,70,70,250,1`,
  `Style: Overlay,Inter Bold,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1`,
];

// Extra ASS styles + events for the edit layer.
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

  // Progress bar across the top, filling over the content (not the outro).
  const total = Number(contentDuration) || 0;
  if (total > 1) {
    const ms = Math.round(total * 1000);
    events += `Dialogue: 2,${toAssTime(0)},${toAssTime(total)},Overlay,,0,0,0,,{\\an7\\pos(0,0)\\p1\\1c${GOLD_TAG}\\alpha&H30&\\clip(0,0,0,10)\\t(0,${ms},\\clip(0,0,${W},10))}m 0 0 l ${W} 0 ${W} 10 0 10{\\p0}\n`;
  }

  return { styles: STYLES, events };
}

// Merge the edit layer into a complete ASS document.
function withEditingOverlays(assContent, context) {
  const { styles, events } = overlayEvents(context);
  if (!styles.length && !events) return assContent;
  const marker = "\n[Events]\n";
  if (!assContent.includes(marker)) return assContent;
  return assContent.replace(marker, `\n${styles.join("\n")}${marker}`) + events;
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
  wrapHeadline,
  itemPositions,
  cutTimes,
  cutZoomFilter,
  overlayEvents,
  withEditingOverlays,
  itemWhooshTimes,
};
