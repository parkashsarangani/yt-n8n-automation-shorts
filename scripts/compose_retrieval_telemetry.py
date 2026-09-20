#!/usr/bin/env python3
from __future__ import annotations
import subprocess
from pathlib import Path
MARKER="RETRIEVAL_TELEMETRY_ENDPOINT_V1"
def patch(text:str)->str:
    if MARKER in text:return text
    req='const feedback = require("./feedbackLoop");';rep=req+'\nconst retrievalTelemetry = require("./retrievalTelemetryStore");'
    if req not in text:raise ValueError("feedback require anchor missing")
    text=text.replace(req,rep,1)
    old='''app.post("/performance/log", async (req, res) => {
  try {
    const result = await feedback.logPublished(req.body || {});
    res.json({ success: true, ...result });
  } catch (err) {
    res.status(400).json({ success: false, error: err.message });
  }
});'''
    new='''// RETRIEVAL_TELEMETRY_ENDPOINT_V1: telemetry failure must never block normal publish logging.
app.post("/performance/log", async (req, res) => {
  try {
    try { await retrievalTelemetry.log(req.body || {}); }
    catch (telemetryErr) { console.warn("[retrieval-telemetry] write failed:", telemetryErr?.message || telemetryErr); }
    const result = await feedback.logPublished(req.body || {});
    res.json({ success: true, ...result });
  } catch (err) { res.status(400).json({ success: false, error: err.message }); }
});
app.get("/performance/retrieval", async (req, res) => {
  try { const limit=Math.max(1,Math.min(100,Number(req.query.limit)||20)); const items=await retrievalTelemetry.getRecent({limit}); res.json({count:items.length,items}); }
  catch (err) { res.status(500).json({count:0,items:[],error:err.message}); }
});'''
    if old not in text:raise ValueError("performance/log endpoint anchor missing")
    return text.replace(old,new,1)
def patch_file(path:Path)->None:
    text=patch(path.read_text(encoding="utf-8"));path.write_text(text, encoding="utf-8");p=subprocess.run(["node","--check",str(path)],text=True,capture_output=True)
    if p.returncode!=0:raise RuntimeError("compose retrieval telemetry syntax check failed:\n"+p.stdout+p.stderr)
    if MARKER not in text or '/performance/retrieval' not in text:raise RuntimeError("compose retrieval telemetry patch did not land")
