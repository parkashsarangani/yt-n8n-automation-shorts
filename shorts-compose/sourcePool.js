// Reserve an exact named-subject candidate, then interleave sources/queries.
function balancedPool(groups, limit, priority=[]) {
  const pools=groups.map(g=>g.slice()).filter(g=>g.length);
  const rank=s=>{const i=priority.indexOf(s);return i<0?priority.length:i;};
  pools.sort((a,b)=>rank(a[0].source)-rank(b[0].source));
  const out=[], seen=new Set();
  const add=c=>{if(c?.url&&!seen.has(c.url)&&out.length<limit){seen.add(c.url);out.push(c);}};
  for(const g of pools) if(g[0]?.source==='wikipedia')add(g.shift());
  while(out.length<limit && pools.some(g=>g.length))for(const g of pools)if(g.length)add(g.shift());
  return out;
}
module.exports={balancedPool};
