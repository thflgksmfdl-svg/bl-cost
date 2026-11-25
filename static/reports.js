"use strict";
const $ = selector => document.querySelector(selector);
const root = document.body.dataset.appRoot || "";
const mode = $("#main").dataset.reportPage;
const qty = value => Number(value || 0).toLocaleString("ko-KR", {maximumFractionDigits:3});
const money = value => `${Number(value||0).toLocaleString("ko-KR",{maximumFractionDigits:0})}원`;
const workTypes = ["작업시간","인쇄스티커","스티커","바코드","벌크작업","재봉","도장"];
const report = {snapshot:null,timer:null,loading:false,renderKey:null};
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;const role={tr:"row",td:"cell",th:"rowheader"}[tag];if(role)n.setAttribute("role",role);return n;}
function addMetric(host,label,value,detail){const block=el("div");block.append(el("dt",label),el("dd",value));if(detail)block.append(el("p",detail,"help"));host.append(block);}
function aggregate(rows){
  const sum=key=>rows.reduce((n,r)=>n+Number(r[key]||0),0);
  return {bl_count:rows.length,products:sum("products"),invoice_count:sum("invoice_count"),invoice_total:sum("invoice_total"),pending:sum("pending"),
    pending_unknown:rows.filter(r=>r.pending===null).length,cost:sum("cost"),cost_missing:rows.filter(r=>r.cost===null).length,
    needs_check:rows.filter(r=>r.differences.length||r.verification==="확인 필요").length,work_missing:rows.filter(r=>r.source==="청구 시트만 존재").length,
    paid:sum("paid"),unpaid:sum("unpaid"),unknown:sum("unknown"),held:sum("held"),four_total:sum("four_total"),two_total:sum("two_total"),
    work:Object.fromEntries(workTypes.map(k=>[k,rows.reduce((n,r)=>n+Number(r.work[k]||0),0)]))};
}
function selectedRows(){const month=$("#report-month").value,search=$("#report-search")?.value.trim()||"",checks=$("#report-only-check")?.checked;return report.snapshot.data.rows.filter(r=>(!month||r.month===month)&&(!search||r.bl.includes(search))&&(!checks||r.differences.length||r.verification==="확인 필요"));}
function render(){
  const snapshot=report.snapshot,data=snapshot?.data,hasData=Array.isArray(data?.rows)&&data.rows.length>0;
  const updating=["queued","refreshing"].includes(snapshot?.state);
  $("#report-refresh").disabled=report.loading||updating;
  $("#report-refresh").textContent=updating?"집계를 갱신하고 있어요…":"공유시트 변경 불러오기";
  $("#report-content").hidden=!hasData;$("#report-empty").hidden=hasData||updating;
  $("#report-state").textContent=updating?`${hasData?"이전 집계를 표시합니다. ":""}공유시트를 읽고 같은 집계를 사이트와 시트에 반영하고 있어요. 중판 조회가 진행 중이면 그 작업 뒤에 이어집니다.`:snapshot?.state==="error"?"갱신을 완료하지 못했습니다. 이전 집계와 기준 시각을 확인해 주세요.":hasData?(snapshot.receipt?.verified?"사이트·공유시트의 집계를 대조했습니다.":"저장된 집계입니다. 공유시트 반영은 아직 확인되지 않았습니다."):"보고서 집계를 준비해 주세요.";
  $("#report-state").setAttribute("aria-busy",String(updating));
  $("#report-error").hidden=!snapshot?.error;$("#report-error").textContent=snapshot?.error||"";
  $("#report-time").textContent=snapshot?.synced_at?`시트 대조 ${new Date(snapshot.synced_at).toLocaleString("ko-KR")} · 서버 가동 중 10분마다 갱신`:"";
  if(!hasData)return;
  const key=JSON.stringify([data,$("#report-month").value,$("#report-search")?.value,$("#report-only-check")?.checked]);
  if(report.renderKey===key)return;report.renderKey=key;
  const month=$("#report-month"),selected=month.value;
  month.replaceChildren(new Option("전체 기간",""),...data.months.map(m=>new Option(m.month,m.month)));
  month.value=data.months.some(m=>m.month===selected)?selected:"";
  const rows=selectedRows(),s=aggregate(rows),legacy=rows.filter(r=>r.source==="기존 시트").length;
  $("#report-scope").textContent=`${month.value||"전체 기간"} · ${rows.length}개 BL · 중판 조회 ${rows.filter(r=>r.source==="중판 조회").length}개 / 기존 시트 ${legacy}개 · 동일 BL은 한 번만 집계${s.work_missing?` · 작업 원본 미확인 ${s.work_missing}개 BL`:""}`;
  const warning=$("#report-warning");warning.hidden=!s.needs_check;
  warning.textContent=`${s.needs_check}개 BL에 자료 차이 또는 확인할 항목이 있습니다. 검산 화면에서 기존 시트와 중판 값을 비교해 주세요.`;
  const metrics=$("#report-metrics");metrics.replaceChildren();
  if(mode==="work-check"){
    const workNote=s.work_missing?`${s.work_missing}개 BL의 작업 원본 미확인`:undefined,allMissing=s.bl_count&&s.work_missing===s.bl_count;
    addMetric(metrics,"집계한 BL",`${qty(s.bl_count)}개`);addMetric(metrics,"실제 작업시간",allMissing?"미확인":`${qty(s.work["작업시간"])}시간`,workNote);
    addMetric(metrics,"4종 작업 합계",allMissing?"미확인":`${qty(s.four_total)}개`,workNote||"인쇄·스티커·바코드·벌크");addMetric(metrics,"재봉·도장 합계",allMissing?"미확인":`${qty(s.two_total)}개`,workNote);
    renderWork(rows);
  }else{
    addMetric(metrics,"집계한 BL",`${qty(s.bl_count)}개`,s.work_missing?`${s.work_missing}개 BL 작업 원본 미확인`:`${qty(s.work["작업시간"])}시간 작업`);
    addMetric(metrics,"확정 청구액",money(s.invoice_total),`${qty(s.invoice_count)}건 · VAT 포함`);
    addMetric(metrics,"추가 비용 결정",s.bl_count&&s.pending_unknown===s.bl_count?"미확인":`${qty(s.pending)}건`,s.pending_unknown?`과거 ${s.pending_unknown}개 BL은 결정 여부 미확인`:"중판 조회 자료 기준");
    addMetric(metrics,"참고 원가",s.bl_count&&s.cost_missing===s.bl_count?"미확인":money(s.cost),s.cost_missing?`${s.cost_missing}개 BL 원가 제외`:"기존 원가·추정 원가 포함");
    renderDashboard(rows,s);
  }
}
function workCell(title,value){const cell=el("td",value);cell.dataset.label=title;cell.className="num";return cell;}
function renderWork(rows){
  const body=$("#work-report-rows"),open=new Set([...body.querySelectorAll("details[open]")].map(d=>d.dataset.key));
  const focus=document.activeElement?.closest("details")?.dataset.key;body.replaceChildren();
  for(const r of rows){
    const row=el("tr"),identity=el("th");identity.scope="row";
    const bl=el(r.batch_id?"a":"strong",r.bl);if(r.batch_id)bl.href=`${root}/?batch=${encodeURIComponent(r.batch_id)}`;
    identity.append(bl,el("p",r.source,"help"));if(r.source_at)identity.append(el("p",new Date(r.source_at).toLocaleString("ko-KR"),"help source-date"));row.append(identity);
    const missing=r.source==="청구 시트만 존재";
    row.append(workCell("하위 주문 / 상품 행",missing?"미확인":`${r.orders===null?"미확인":qty(r.orders)+"건"} / ${qty(r.products)}행`),workCell("실제 시간",missing?"미확인":`${qty(r.work["작업시간"])}h`));
    for(const [title,total,types] of [["4종 작업",r.four_total,workTypes.slice(1,5)],["재봉·도장",r.two_total,workTypes.slice(5)]]){
      const cell=workCell(title,"");if(missing){cell.append(el("span","미확인"));row.append(cell);continue;}const details=el("details",undefined,"work-counts");details.dataset.key=r.bl+title;details.open=open.has(details.dataset.key);details.append(el("summary",`${qty(total)}개`));const list=el("dl");for(const k of types){const group=el("div");group.append(el("dt",k),el("dd",qty(r.work[k])));list.append(group);}details.append(list);cell.append(details);row.append(cell);
    }
    const cell=el("td");cell.dataset.label="검산·확인사항";cell.append(el("p",r.verification,r.verification==="확인 필요"?"check-needed":"check-label"));
    if(r.differences.length){const details=el("details",undefined,"work-differences");details.dataset.key=r.bl+"차이";details.open=open.has(details.dataset.key);details.append(el("summary",`차이 ${r.differences.length}개 확인`));const list=el("ul");for(const d of r.differences)list.append(el("li",d));details.append(list);cell.append(details);}
    else cell.append(el("p",r.source==="기존 시트"?"중판 원본은 미대조":"청구 시트 대조 "+r.sheet_check,"help"));
    row.append(cell);body.append(row);
  }
  if(!rows.length){const row=el("tr"),cell=el("td","조건에 맞는 BL이 없습니다.");cell.colSpan=6;row.append(cell);body.append(row);}
  if(focus)for(const d of body.querySelectorAll("details"))if(d.dataset.key===focus)d.querySelector("summary").focus({preventScroll:true});
}
function renderDashboard(rows,s){
  const months=report.snapshot.data.months.filter(m=>!$("#report-month").value||m.month===$("#report-month").value),body=$("#monthly-report-rows");body.replaceChildren();
  for(const m of months){const row=el("tr"),month=el("th");month.scope="row";const link=el("a",m.month);link.href=`${root}/work-check/?month=${encodeURIComponent(m.month)}`;month.append(link);row.append(month,workCell("BL",qty(m.bl_count)),workCell("실제 시간",m.work_missing===m.bl_count?"미확인":`${qty(m.work["작업시간"])}h${m.work_missing?" · 일부 미확인":""}`));
    const charge=workCell("확정 청구",money(m.invoice_total));charge.append(el("p",`${qty(m.invoice_count)}건`,"help"));row.append(charge);
    const pending=workCell("추가 결정 대기",m.pending_unknown===m.bl_count?"미확인":`${qty(m.pending)}건`);if(m.pending_unknown&&m.pending_unknown!==m.bl_count)pending.append(el("p",`과거 ${qty(m.pending_unknown)}BL 미확인`,"help"));row.append(pending,workCell("참고 원가",m.cost_missing===m.bl_count?"미확인":money(m.cost)));body.append(row);}
  const payments=$("#payment-metrics");payments.replaceChildren();for(const [label,key] of [["입금 기록","paid"],["미결제 기록","unpaid"],["상태 미입력","unknown"],["결제 보류","held"]])addMetric(payments,label,money(s[key]));
  const work=$("#work-metrics");work.replaceChildren();for(const type of workTypes.slice(1))addMetric(work,type,s.work_missing===s.bl_count?"미확인":`${qty(s.work[type])}개`,s.work_missing?`${s.work_missing}개 BL 미확인`:undefined);
  $("#cost-coverage").textContent=s.cost_missing?`${s.cost_missing}개 BL은 원가를 계산할 수 없어 합계에서 제외했습니다.`:"표시된 모든 BL의 원가 집계가 포함되어 있습니다. 원가 기준은 각 자료의 출처에 따릅니다.";
}
async function load(refresh=false){
  if(report.loading)return;report.loading=true;clearTimeout(report.timer);$("#report-refresh").disabled=true;
  try{const response=await fetch(root+"/api/reports/",refresh?{method:"POST",headers:{"X-CSRFToken":$("input[name=csrfmiddlewaretoken]").value},cache:"no-store"}:{cache:"no-store"});
    if(!response.ok)throw Error("집계를 불러오지 못했습니다. 잠시 후 다시 불러오기를 눌러 주세요.");report.snapshot=await response.json();render();
  }catch(e){$("#report-error").hidden=false;$("#report-error").textContent=e.message;$("#report-state").textContent="연결을 확인해 주세요. 기존에 표시된 집계는 유지합니다.";}
  finally{report.loading=false;$("#report-refresh").disabled=["queued","refreshing"].includes(report.snapshot?.state);report.timer=setTimeout(()=>load(),["queued","refreshing"].includes(report.snapshot?.state)?4000:30000);}
}
$("#report-refresh-form").onsubmit=e=>{e.preventDefault();load(true);};
$("#report-filters").onsubmit=e=>e.preventDefault();$("#report-filters").oninput=()=>render();
const initialMonth=new URL(location.href).searchParams.get("month");if(initialMonth&&/^20\d{2}-\d{2}$/.test(initialMonth))$("#report-month").append(new Option(initialMonth,initialMonth,true,true));
load();
