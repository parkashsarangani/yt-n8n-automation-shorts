#!/usr/bin/env python3
"""Expose the per-video retention/traffic ingest endpoint on the compositor."""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "SHORTS_METRICS_V3_COMPOSE"

ENDPOINT = '''app.post("/performance/ingest-deep", async (req, res) => {
  try {
    const result = await feedback.ingestDeepMetrics(req.body || {});
    res.json({ success: true, ...result });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

'''


def upgrade(text: str) -> str:
    if MARKER in text:
        return text
    anchor = '// The topic generator reads this before picking a topic.'
    if anchor not in text:
        raise ValueError("channel-insights anchor missing; cannot install deep-metrics endpoint")
    text = text.replace(anchor, ENDPOINT + anchor, 1)
    return text + f"\n// {MARKER}\n"


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: upgrade-compose-metrics-v3.py INPUT [OUTPUT]")
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src
    out = upgrade(src.read_text())
    if 'app.post("/performance/ingest-deep"' not in out:
        raise RuntimeError("deep-metrics endpoint missing from generated compose")
    dst.write_text(out)
    print(f"{MARKER} compose written to {dst}")


if __name__ == "__main__":
    main()
