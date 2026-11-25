const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/bl-cost/bootstrap.js','utf8');
const html='<body data-app-root="/bl-cost"><form id="lookup-form"></form></body>';
function run(responses){
  const elements=Object.fromEntries(['prepare-retry','prepare-title','prepare-help'].map(id=>['#'+id,{hidden:true,textContent:''}]));
  const calls=[],written=[];
  const context={document:{querySelector:id=>elements[id],open(){},write:value=>written.push(value),close(){}},location:{search:'?batch=example'},AbortSignal,
    setTimeout:fn=>queueMicrotask(fn),fetch:async(url,options)=>{calls.push({url,options});return responses.shift()||{ok:false,text:async()=>''};}};
  vm.runInNewContext(source,context);
  return {elements,calls,written};
}
test('cold or temporarily unavailable server retries before opening the same-page app',async()=>{
  const r=run([{ok:false,text:async()=>''},{ok:true,text:async()=>html}]);
  await new Promise(setImmediate);
  assert.equal(r.calls.length,2);assert.equal(r.calls[0].url,'/bl-cost/app?batch=example');
  assert.equal(r.calls[0].options.cache,'no-store');assert.deepEqual(r.written,[html]);
});
test('invalid response shows a manual retry and never replaces the page with an error document',async()=>{
  const responses=Array.from({length:4},()=>({ok:true,text:async()=>'<html>gateway error</html>'}));
  const r=run(responses);await new Promise(setImmediate);
  assert.equal(r.calls.length,4);assert.deepEqual(r.written,[]);assert.equal(r.elements['#prepare-retry'].hidden,false);
  responses.push({ok:true,text:async()=>html});await r.elements['#prepare-retry'].onclick();
  assert.deepEqual(r.written,[html]);assert.equal(r.elements['#prepare-retry'].hidden,true);
});
