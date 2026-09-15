const fs=require('fs');
const path=require('path');
const crypto=require('crypto');
const TTL=24*3600*1000;
class DurableJobs extends Map {
  constructor(dir){
    super();this.dir=dir;fs.mkdirSync(dir,{recursive:true});
    for(const name of fs.readdirSync(dir)){
      if(!/^[a-f0-9-]{36}\.json$/.test(name))continue;
      const record=JSON.parse(fs.readFileSync(path.join(dir,name),'utf8'));
      if(record.status==='processing')Object.assign(record,{status:'failed',error:'Render interrupted by service restart; no automatic duplicate render was started',finishedAt:Date.now()});
      this.set(name.slice(0,-5),record);
    }
    this.prune();
    this.timer=setInterval(()=>this.prune(),60000);this.timer.unref();
  }
  set(id,value){
    if(!/^[a-f0-9-]{36}$/.test(id))throw new Error('Invalid job ID');
    const file=path.join(this.dir,`${id}.json`),tmp=`${file}.${crypto.randomUUID()}.tmp`;
    fs.writeFileSync(tmp,JSON.stringify(value));fs.renameSync(tmp,file);
    return super.set(id,value);
  }
  delete(id){if(!super.has(id))return false;fs.rmSync(path.join(this.dir,`${id}.json`),{force:true});return super.delete(id);}
  prune(){for(const [id,job] of this)if(job.finishedAt&&Date.now()-job.finishedAt>TTL)this.delete(id);}
}
function installLifecycle(app,jobs,output){
  let draining=false;
  const active=()=>[...jobs.values()].filter(j=>j.status==='processing').length;
  app.get('/health/jobs',(_q,r)=>r.json({draining,active:active()}));
  const local=(q,r,next)=>['127.0.0.1','::1','::ffff:127.0.0.1'].includes(q.socket.remoteAddress)?next():r.sendStatus(403);
  app.post('/admin/drain',local,(_q,r)=>{draining=true;r.json({draining,active:active()});});
  app.post('/admin/resume',local,(_q,r)=>{draining=false;r.json({draining});});
  app.use('/compose',(q,r,next)=>draining&&q.method==='POST'?r.status(503).json({error:'Service is draining; retry later'}):next());
  app.delete('/cleanup/:id',(q,r)=>{
    // Only final output basenames. Never arbitrary paths or B-roll cache files.
    if(!/^short_[a-f0-9-]{36}$/.test(q.params.id))return r.status(400).json({error:'Invalid final render ID'});
    const file=path.join(output,`${q.params.id}.mp4`);
    try{fs.rmSync(file,{force:true});r.json({success:true});}catch(e){r.status(500).json({error:e.message});}
  });
  return ()=>{draining=true;};
}
module.exports={DurableJobs,installLifecycle};
