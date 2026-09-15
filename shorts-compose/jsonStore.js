const fs = require('fs/promises');
const path = require('path');
const crypto = require('crypto');

async function readJson(file, fallback) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}
async function writeJson(file, value) {
  await fs.mkdir(path.dirname(file), {recursive:true});
  const temp = `${file}.${crypto.randomUUID()}.tmp`;
  try { await fs.writeFile(temp, JSON.stringify(value, null, 2)); await fs.rename(temp, file); }
  finally { await fs.unlink(temp).catch(()=>{}); }
}
// Cross-process transaction. Never steal a possibly live lock: time out and
// surface failure instead of silently resetting history or losing an update.
async function updateJson(file, fallback, mutate) {
  await fs.mkdir(path.dirname(file), {recursive:true});
  const lock = `${file}.lock`, until = Date.now()+15000;
  let handle;
  while (!handle) {
    try { handle = await fs.open(lock,'wx'); }
    catch (error) {
      if(error.code !== 'EEXIST' || Date.now()>=until) throw error;
      await new Promise(resolve=>setTimeout(resolve,20));
    }
  }
  try {
    const value = await readJson(file,fallback);
    const result = await mutate(value);
    await writeJson(file,value);
    return result;
  } finally { await handle.close(); await fs.unlink(lock); }
}
module.exports={readJson,writeJson,updateJson};
