const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/bl-cost/bootstrap.js','utf8');
const html='<body data-app-root="/bl-cost"><form id="lookup-form"></form></body>';
test('report routes do not redirect to the same normalized Netlify path',()=>{
  for(const line of fs.readFileSync(__dirname+'/_redirects','utf8').trim().split(/\r?\n/)){
    const [from,to,status]=line.split(/\s+/);
    if(status==='301'||status==='302')assert.notEqual(from.replace(/\/$/,''),to.replace(/\/$/,''));
  }
});
function run(responses,pathname='/bl-cost/'){
  const elements=Object.fromEntries(['prepare-retry','prepare-title','prepare-help','prepare-countdown','prepare-seconds'].map(id=>['#'+id,{hidden:true,textContent:''}]));
  const calls=[],written=[];
  let now=0,nextTimer=1;
  const timers=new Map();
  const context={document:{querySelector:id=>elements[id],open(){},write:value=>written.push(value),close(){}},location:{pathname,search:'?batch=example'},AbortSignal,
    Date:{now:()=>now},setInterval:fn=>{const id=nextTimer++;timers.set(id,fn);return id;},clearInterval:id=>timers.delete(id),
    setTimeout:fn=>queueMicrotask(fn),fetch:async(url,options)=>{calls.push({url,options});return responses.shift()||{ok:false,text:async()=>''};}};
  vm.runInNewContext(source,context);
  return {elements,calls,written,timers,advance:ms=>{now+=ms;for(const fn of [...timers.values()])fn();}};
}

test('reports open their own same-origin page through the warm-up screen',async()=>{
  for(const view of ['work-check','dashboard']){
    const page=`<body data-app-root="/bl-cost"><main data-report-page="${view}"></main></body>`;
    const r=run([{ok:true,text:async()=>page}],`/bl-cost/${view}/`);
    await new Promise(setImmediate);
    assert.equal(r.calls[0].url,`/bl-cost/app/${view}?batch=example`);
    assert.deepEqual(r.written,[page]);
  }
});
test('cold or temporarily unavailable server retries before opening the same-page app',async()=>{
  const r=run([{ok:false,text:async()=>''},{ok:true,text:async()=>html}]);
  await new Promise(setImmediate);
  assert.equal(r.calls.length,2);assert.equal(r.calls[0].url,'/bl-cost/app?batch=example');
  assert.equal(r.calls[0].options.cache,'no-store');assert.deepEqual(r.written,[html]);
  assert.equal(r.timers.size,0);
});
test('invalid response shows a manual retry and never replaces the page with an error document',async()=>{
  const responses=Array.from({length:4},()=>({ok:true,text:async()=>'<html>gateway error</html>'}));
  const r=run(responses);await new Promise(setImmediate);
  assert.equal(r.calls.length,4);assert.deepEqual(r.written,[]);assert.equal(r.elements['#prepare-retry'].hidden,false);
  responses.push({ok:true,text:async()=>html});await r.elements['#prepare-retry'].onclick();
  assert.deepEqual(r.written,[html]);assert.equal(r.elements['#prepare-retry'].hidden,true);
});

test('30-second guidance uses elapsed time and never claims that the server is ready',async()=>{
  let resolve;
  const r=run([new Promise(done=>{resolve=done;})]);
  assert.equal(r.elements['#prepare-countdown'].hidden,false);
  assert.equal(r.elements['#prepare-seconds'].textContent,'30');
  r.advance(12000);assert.equal(r.elements['#prepare-seconds'].textContent,'18');
  r.advance(20000);
  assert.equal(r.elements['#prepare-countdown'].hidden,true);
  assert.equal(r.elements['#prepare-title'].textContent,'조금 더 준비하고 있어요');
  assert.deepEqual(r.written,[]);assert.equal(r.timers.size,0);
  resolve({ok:true,text:async()=>html});await new Promise(setImmediate);
  assert.deepEqual(r.written,[html]);
});

test('automatic retries share one countdown; a manual retry starts a fresh countdown',async()=>{
  let resolve;
  const responses=[{ok:false,text:async()=>''},new Promise(done=>{resolve=done;})];
  const r=run(responses);r.advance(10000);await new Promise(setImmediate);
  assert.equal(r.calls.length,2);assert.equal(r.elements['#prepare-seconds'].textContent,'20');
  responses.push({ok:false,text:async()=>''},{ok:false,text:async()=>''});
  resolve({ok:false,text:async()=>''});await new Promise(setImmediate);
  assert.equal(r.elements['#prepare-retry'].hidden,false);
  assert.equal(r.elements['#prepare-countdown'].hidden,true);assert.equal(r.timers.size,0);
  let ready;
  responses.push(new Promise(done=>{ready=done;}));
  const retry=r.elements['#prepare-retry'].onclick();
  assert.equal(r.elements['#prepare-seconds'].textContent,'30');assert.equal(r.timers.size,1);
  r.advance(1000);assert.equal(r.elements['#prepare-seconds'].textContent,'29');
  ready({ok:true,text:async()=>html});await retry;
  assert.deepEqual(r.written,[html]);assert.equal(r.timers.size,0);
});
