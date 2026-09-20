const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs');
const os=require('os');
const path=require('path');
const {execFileSync}=require('child_process');
const root=path.resolve(__dirname,'../..');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'shorts-encoding-'));
execFileSync(process.platform==='win32'?'python':'python3',['scripts/build_production_artifacts.py','--output-dir',tmp],{cwd:root,stdio:'pipe'});
test.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));

// Regression for a UTF-8 double-encoding bug in the build pipeline: every
// scripts/*.py stage moved workflow.json/compose.js/brollResolver.js between
// subprocesses via bare Path.read_text()/write_text(), which decode/encode
// using the platform's locale-default codec (cp1252 on Windows) instead of
// UTF-8. The very first stage (upgrade-viral-shorts.py) read the seed
// n8n/workflow.json's raw UTF-8 bytes for "\u{1F447}" (\xF0\x9F\x91\x87) as
// cp1252, permanently turning it into the four-character mojibake sequence
// "ðŸ‘‡" once re-serialized through json.dumps (which \u-escapes non-ASCII by
// default, so the corruption survives as the wrong *codepoints*, not as
// visibly broken bytes in the file). Every script now passes
// encoding="utf-8" explicitly on every read_text()/write_text() call.
test('Post First Comment jsonBody keeps the literal pointer emoji, not mojibake',()=>{
 const seed=JSON.parse(fs.readFileSync(path.join(root,'n8n','workflow.json'),'utf8'));
 const built=JSON.parse(fs.readFileSync(path.join(tmp,'workflow.json'),'utf8'));
 const seedBody=seed.nodes.find(n=>n.name==='Post First Comment').parameters.jsonBody;
 const builtBody=built.nodes.find(n=>n.name==='Post First Comment').parameters.jsonBody;
 assert.ok(seedBody.includes('\u{1F447}'),'seed fixture lost its pointer emoji; test is no longer exercising the bug');
 assert.ok(builtBody.includes('\u{1F447}'),'built workflow.json does not decode to the literal pointer-down emoji');
 assert.ok(!builtBody.includes('ðŸ'),'built workflow.json decodes to the cp1252-mojibake\'d emoji instead of the real one');
 // The decoded JS string's own UTF-16 code units must be the exact surrogate
 // pair for U+1F447, not four separate mis-decoded BMP characters.
 const emojiIndex=builtBody.indexOf('\u{1F447}');
 assert.equal(builtBody.codePointAt(emojiIndex),0x1F447);
});

test('no other non-ASCII content in the built artifacts is mojibake\'d by the same bug',()=>{
 // workflow.json round-trips through json.dumps (ensure_ascii=True), so a
 // correctly-built file \u-escapes every non-ASCII character; the corruption
 // this bug caused only shows up once the JSON is decoded back into a JS
 // string. compose.js/brollResolver.js are plain text, never JSON-escaped,
 // so their non-ASCII characters appear literally both before and after a
 // correct build.
 const collectCodepoints=text=>{
  const set=new Set();
  for(const ch of text) if(ch.codePointAt(0)>127) set.add(ch.codePointAt(0));
  return set;
 };
 const builtWorkflow=JSON.parse(fs.readFileSync(path.join(tmp,'workflow.json'),'utf8'));
 const decodedWorkflowText=JSON.stringify(builtWorkflow); // JS JSON.stringify does not \u-escape non-ASCII
 const builtPlainTextFiles=[path.join(tmp,'compose.js'),path.join(tmp,'brollResolver.js')];
 const textsToCheck=[decodedWorkflowText,...builtPlainTextFiles.map(f=>fs.readFileSync(f,'utf8'))];
 for(const text of textsToCheck){
  const codepoints=collectCodepoints(text);
  // A lone UTF-8 lead byte (0xC2-0xF4) decoded as Latin-1/cp1252 instead of
  // as the start of a multi-byte sequence surfaces as one of these BMP
  // characters once mis-decoded; none should ever appear in a correct build.
  for(const cp of codepoints){
   assert.ok(!(cp>=0xc2&&cp<=0xf4),`suspicious lone Latin-1 byte U+${cp.toString(16)} (likely a torn UTF-8 sequence)`);
  }
 }
 // The specific characters the seed files are known to carry must survive
 // unchanged in kind (their presence and codepoint identity), not merely in
 // count, since a mojibake round-trip can coincidentally preserve counts.
 // Scoped to "Claude: Draft Script (Stage 1)" rather than the whole file:
 // other nodes' prompt text is legitimately rewritten/renamed by the growth
 // policy stages, which would make a whole-file apostrophe count fragile for
 // reasons unrelated to encoding.
 const seedWorkflow=JSON.parse(fs.readFileSync(path.join(root,'n8n','workflow.json'),'utf8'));
 const draftNodeName='Claude: Draft Script (Stage 1)';
 const seedDraftBody=seedWorkflow.nodes.find(n=>n.name===draftNodeName).parameters.jsonBody;
 const builtDraftBody=builtWorkflow.nodes.find(n=>n.name===draftNodeName).parameters.jsonBody;
 const seedApostrophes=(seedDraftBody.match(/’/g)||[]).length;
 const builtApostrophes=(builtDraftBody.match(/’/g)||[]).length;
 assert.ok(seedApostrophes>0,'fixture assumption changed: no U+2019 apostrophes in seed draft-script prompt');
 assert.equal(builtApostrophes,seedApostrophes,'right single quotation marks were corrupted somewhere in the build pipeline');

 const seedEmDashCompose=(fs.readFileSync(path.join(root,'shorts-compose','compose.js'),'utf8').match(/—/g)||[]).length;
 const builtEmDashCompose=(fs.readFileSync(path.join(tmp,'compose.js'),'utf8').match(/—/g)||[]).length;
 assert.ok(seedEmDashCompose>0,'fixture assumption changed: no U+2014 em dashes in seed compose.js');
 assert.equal(builtEmDashCompose,seedEmDashCompose,'em dashes were corrupted in the built compose.js');
});
