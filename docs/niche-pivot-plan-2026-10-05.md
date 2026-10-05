# Niche pivot plan: trivia → insecurity / social-psychology Shorts
**Date:** 2026-10-05 · **Research:** `niche-research-insecurity-psychology-2026-10-05.md`

## Goal and kill criteria
- **Niche:** the psychology of attraction, respect and hidden social signals, for a broad audience. Faceless, fully automated.
- **Formula:** a sensational hook built on an insecurity, and a script that honestly delivers on the hook's promise.
- **Success test:** after ~20 Shorts (about 3 weeks), the median Short has ≥5K views and at least one Short has ≥50K. If not, stop the project or rethink it; don't keep tuning.

## Decisions (made 2026-10-05)
1. **Channel:** keep the existing channel. Analytics only learn from videos tagged with the new niche (`CHANNEL_NICHE=social_psychology_v1`).
2. **Voice:** ElevenLabs premade "Brian" (`nPczCjzI2devNBz1zQrb`), a deep, calm voice. Listen to a sample before going live; it is one constant (`VOICE_ID` in `scripts/niche_social_psychology.py`).
3. **Cadence:** 1 Short a day at 16:00 Europe/Berlin.

## Prerequisite
The HP server is unreachable (Cloudflare 530, both runners offline since around 2026-10-01). Nothing can be deployed or measured until it's back.

## Work items (each one a PR with tests, in this order)

### 1. Channel identity and creative archetypes
- Rewrite `shorts-compose/channel-voice.json`. The new identity is a calm, perceptive friend who reads people well: second person, reassuring or warning, specific, and never shaming.
- Replace `ARCHETYPES` in `scripts/upgrade-creative-system.py` (currently `impossible_comparison`, `hidden_mechanism`, …) with niche archetypes:
  `hidden_signal` (what others' behaviour secretly means), `self_check_reassurance` ("signs you're more attractive / respected than you think"), `social_lever` (a habit or trick that changes how people see you), `warning_sign` (signs someone dislikes, disrespects or manipulates you), `attraction_myth` (a common belief about attraction that research contradicts).
- Analytics: tag every new video with a `niche: "social_psychology_v1"` field in its creative DNA. Make `retentionPolicy.archetypePerformance` and the strategist in `feedbackLoop.js` ignore trivia-era history, so old results don't steer the new niche.

### 2. Topic generation
The prompt lives in the `Claude: Generate Topic` node of `n8n/workflow.json`.
- Replace the trivia criteria. The current prompt says "ONE surprising fact… never a list" and rotates through body, space, brands and so on. The new criteria use the three proven topic families: hidden signals from others, "am I attractive/respected?", and levers to be seen differently.
- Allow formats like "3–5 signs" and "if someone does X", a single-sign deep dive, and myth vs. research.
- Keep the semantic dedup in `topicPolicy.js` as-is. It works for any niche.
- Add the no-go list to the prompt: no mental-health diagnoses or medical claims, no gender shaming, no money promises.

### 3. Script writing (Draft and Editorial Rewrite)
- **Draft (Stage 1):** structure the script as hook (0–2 s) → open loop (2–5 s) → 3–5 concrete points (5–40 s) → payoff → self-check comment prompt. Write in the second person, and back each point with a recognisable situation or a research finding, never vague advice.
- **Editorial Rewrite (Stage 2):** remove the current "mortality / bodily horror / scale-breaking" escalation rules. They suit trivia and would be wrong here. Replace them with "sharpen the emotional pull, keep every claim defensible".
- **Medical exclusion:** extend it to mental-health conditions and diagnostic language ("narcissist", "BPD", "trauma bond" as a diagnosis). Behavioural framing is allowed.
- **Banned-words list:** revisit it. Some sensational words ("secretly", "instantly") are core to the new hooks and must be allowed. Keep the generic filler bans.

### 4. Hook critic (`scripts/upgrade-hook-critic-v1.py` → v2)
- New scoring rubric: self-relevance (is it about *you*?), emotional pull (insecurity, hope or fear), promise specificity (a number or a concrete outcome), **payoff-backable** (the script can deliver it), and visual supportability.
- Hard-penalise promises the draft can't back up and miracle-style claims.

### 5. Promise-payoff contract (new and the most important item)
- After `Validate Final Script`, extract the hook's promise: the count ("5 signs"), the outcome ("…instantly trust you") and the subject.
- **Deterministic checks:** the number of points in the hook matches the number of point scenes; every point is in its own scene; the payoff scene comes before the outro.
- **LLM judge:** does each point actually deliver what the hook promised? Is any point filler?
- On failure, send the script back through the existing `Increment Script Attempt` retry loop with the failure reason.
- Add tests in `shorts-compose/tests/coherence-contracts.test.js`, covering a pass case, a count mismatch, and a hook the script doesn't pay off.

### 6. Variety guard (inauthentic-content compliance)
- Rotate between at least 4 structural templates (list, single-sign deep dive, myth vs. research, "if they do X, it means Y"), choosing by seed the same way `assignHookExperiment` does. Record the template in the creative DNA.
- Reject a script whose template, opening phrasing *and* outro all match the previous 2 videos.
- Vary visual pacing and caption style per template, so videos don't all look the same.

### 7. Visuals
- B-roll queries in `brollResolver.js` (Pexels/Pixabay/Unsplash) should target people and situations: conversations, eye contact, body language, workplaces, dates.
- The first frame shows the human situation the hook names.
- Add a visual safety filter: no suggestive or sexualised imagery, and no AI-generated "attractive person" faces.

### 8. Competitor watchlist
- Replace `shorts-compose/competitors.json` (currently `@zackdfilms`, `@MatthewSantoro`, `@FactsMine`, `@SeanAndreww`) with niche channels.
- Check each channel's latest upload date before adding it, per the file's own rule. Start with `@Therealfacts-MD`, `@StoicLegend-MR` and `@CrushPsychologyOfficial`.

### 9. Metadata and engagement
- Title formula: an insecurity, plus a specific promise, plus the "you" angle. Use 3–5 niche hashtags.
- Make `Post First Comment` a self-check question ("Which one do you do without noticing?").
- Keep the AI-content disclosure.

## Rollout
1. Implement items 1–5 on a new branch. CI must pass all existing contract tests, updated for the niche.
2. **Dry run:** generate ~10 scripts without uploading, then review them by hand for hook quality, payoff honesty and the no-go list.
3. Fix what the review finds, then do items 6–9.
4. Go live. Watch the t72h analytics for the first 5 Shorts; the swipe-away rate tells us whether the hooks work.
5. Apply the kill criteria at ~20 Shorts.

## Risks
- **Saturation:** a generic listicle output will land at the same ~1K ceiling. The hook critic and the promise-payoff check are the main defence.
- **Policy:** inauthentic-content enforcement. The variety guard and lower cadence mitigate it.
- **Visual mismatch:** stock footage of "people talking" can feel generic. Watch AVP on the first batch.
