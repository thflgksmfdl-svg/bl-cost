const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../static/app.js','utf8');

function setup(batch){
  const elements=new Map(),timers=[];
  const element=key=>{
    if(!elements.has(key))elements.set(key,{hidden:true,textContent:'',dataset:{src:'/static/bunny-working.svg'},attributes:{},
      classList:{toggle(){}},addEventListener(){},append(){},replaceChildren(){},
      querySelector:child=>element(key+' '+child),getAttribute(name){return this.attributes[name]??null;},
      hasAttribute(name){return name==='src'?!!this.src:name in this.attributes;},
      setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this[name];delete this.attributes[name];}});
    return elements.get(key);
  };
  const body=element('body');body.dataset.appRoot='';
  const context=vm.createContext({document:{body,querySelector:element,querySelectorAll:()=>[],createElement:element},
    fetch:()=>new Promise(()=>{}),setTimeout:fn=>timers.push(fn),clearTimeout(){},URL,location:{href:'http://localhost/'}});
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  run('state.batch='+JSON.stringify(batch));
  return {element,run,timers};
}

function saved(){
  return {state:'saved',revision:5,error:'',updated_at:'2026-11-25T02:16:13Z',summary:{invoice_count:2},invoices:[],
    sheet_result:{verified:true,spreadsheet_id:'example',targets:{'청구 입력(내가 입력)':{count:2,rows:[2389,2395],added_rows:[],sheet_id:1907300321}}}};
}

test('legacy verified saves link to the updated rows instead of claiming new inserts',()=>{
  const r=setup(saved());r.run('renderSheetReceipt()');
  assert.equal(r.element('#sheet-receipt').hidden,false);
  assert.match(r.element('#sheet-receipt').textContent,/청구 입력 2건 저장 · 신규 0건 \/ 기존 2건 갱신/);
  assert.equal(r.element('#sheet-record-link').href,'https://docs.google.com/spreadsheets/d/example/edit#gid=1907300321&range=A2389:AD2395');
});

test('mixed updates and additions report the actual saved counts and range',()=>{
  const b=saved(),t=b.sheet_result.targets['청구 입력(내가 입력)'];t.added_rows=[2396];t.rows=[2396,2389];
  b.sheet_result.synced_revision=b.revision;
  const r=setup(b);r.run('renderSheetReceipt()');
  assert.match(r.element('#sheet-receipt').textContent,/신규 1건 \/ 기존 1건 갱신/);
  assert.match(r.element('#sheet-record-link').href,/range=A2389:AD2396$/);
});

test('stale, failed and still-processing saves never show a successful receipt',()=>{
  for(const changes of [{state:'saving'},{error:'연결 실패'},{sheet_result:{...saved().sheet_result,synced_revision:4}}]){
    const r=setup({...saved(),...changes});r.run('renderSheetReceipt()');
    assert.equal(r.element('#sheet-receipt').hidden,true);
    assert.equal(r.element('#sheet-record-link').hidden,true);
  }
});

test('no-charge collections do not claim that billing rows were written',()=>{
  const b=saved();b.summary.invoice_count=0;b.sheet_result={verified:true,no_changes:true,targets:{}};
  const r=setup(b);r.run('render()');
  assert.equal(r.element('#state').textContent,'청구 없음');
  assert.match(r.element('#approval-help').textContent,/저장할 청구가 없습니다/);
  assert.equal(r.element('#sheet-receipt').hidden,true);
});

test('rabbit motion exists only while its notification is visible',()=>{
  const r=setup(saved()),img=r.element('#lookup-toast img');
  r.run('lookupToast(true)');assert.equal(img.src,'/static/bunny-working.svg');
  r.timers.at(-1)();assert.equal(r.element('#lookup-toast').hidden,true);assert.equal(img.src,undefined);
  r.run('lookupToast(true,"저장 중","검산 중",true)');
  assert.equal(r.element('#lookup-toast').hidden,false);assert.equal(r.timers.length,1);
  r.run('lookupToast(false)');assert.equal(img.src,undefined);
});

test('security failures show the server recovery message instead of a response parsing error',async()=>{
  const r=setup(saved());
  r.run(`fetch=async()=>({ok:false,status:403,redirected:false,json:async()=>({error:"보안 확인에 실패해 요청을 처리하지 못했습니다. 입력한 내용을 보관한 뒤 화면을 새로고침하고 다시 시도해 주세요."})})`);
  await assert.rejects(r.run('api("/api/batches/example/preview/",{})'),/보안 확인.*새로고침/);
});

test('duplicate charge prompts are removed while information corrections remain visible',()=>{
  const r=setup(saved());
  const issues=[{kind:'opp',text:'OPP 봉투 500개 사용'},{kind:'cost',text:'별도 비용 안내'},
    {kind:'quantity',text:'수량 확인'},{kind:'free_hours',text:'무료시간 확인'},
    {kind:'customer',text:'고객명 확인'},{kind:'mode',text:'입고방식 확인'},{kind:'future-warning',text:'추가 확인'}];
  const visible=JSON.parse(r.run('JSON.stringify(correctionIssues('+JSON.stringify({review_issues:issues})+'))'));
  assert.deepEqual(visible.map(i=>i.text),['수량 확인','무료시간 확인','고객명 확인','입고방식 확인','추가 확인']);
});

test('box CBM retains all six decimal places and makes included fees explicit',()=>{
  const r=setup(saved());
  assert.equal(r.run('cbmQty("0.013468")'),'0.013468');
  assert.match(r.run('volumeRule({ready:true,cbm_excess:"0.2",cbm_charge:"2200"})'),/초과 0.2 CBM.*2,200원.*VAT 포함.*자동 합산됨.*별도 추가금 입력 불필요/);
  assert.equal(r.run('volumeRule({ready:false,error:"호수 확인 필요"})'),'호수 확인 필요');
});
