const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../static/reports.js','utf8');
function setup(){
  const nodes=new Map();
  const get=key=>{
    if(!nodes.has(key))nodes.set(key,{dataset:{reportPage:'work-check'},value:'',checked:false,setAttribute(){}});
    return nodes.get(key);
  };
  const context=vm.createContext({document:{body:{dataset:{}},querySelector:get},URL,location:{href:'http://localhost/work-check/'},
    fetch:()=>new Promise(()=>{}),setTimeout(){},clearTimeout(){}});
  vm.runInContext(source,context);
  return {run:code=>vm.runInContext(code,context),get};
}
function row(changes={}){return {bl:'202611240025',source:'중판 조회',products:2,work:{작업시간:'2',인쇄스티커:'52'},four_total:'52',two_total:'0',
  invoice_count:0,invoice_total:'0',pending:0,cost:'20000',paid:'0',unpaid:'0',unknown:'0',held:'0',differences:[],verification:'원본·DB 일치',...changes};}

test('unbilled free work remains in filtered totals',()=>{
  const r=setup(),result=JSON.parse(r.run(`JSON.stringify(aggregate(${JSON.stringify([row()])}))`));
  assert.equal(result.invoice_count,0);assert.equal(result.work.작업시간,2);assert.equal(result.four_total,52);assert.equal(result.cost,20000);
});

test('missing work, cost and payment status remain separate from real zero values',()=>{
  const r=setup(),result=JSON.parse(r.run(`JSON.stringify(aggregate(${JSON.stringify([row({source:'청구 시트만 존재',pending:null,cost:null,unknown:'5000'}),row()])}))`));
  assert.equal(result.work_missing,1);assert.equal(result.cost_missing,1);assert.equal(result.pending_unknown,1);
  assert.equal(result.paid,0);assert.equal(result.unpaid,0);assert.equal(result.unknown,5000);
});

test('unchanged polling responses preserve existing table details and focus',()=>{
  const r=setup();
  r.run(`report.snapshot={state:'ready',receipt:{verified:true},data:{rows:${JSON.stringify([row()])},months:[]}};
    report.renderKey=JSON.stringify([report.snapshot.data,'','',false]);
    renderWork=()=>{throw Error('Existing details would be replaced');};render();`);
  assert.equal(r.get('#report-state').textContent,'사이트·공유시트의 집계를 대조했습니다.');
});
