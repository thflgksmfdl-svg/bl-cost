"use strict";
const $ = (selector) => document.querySelector(selector);
const money = (v) => `${Number(v || 0).toLocaleString("ko-KR", {maximumFractionDigits:0})}원`;
const qty = (v) => Number(v || 0).toLocaleString("ko-KR", {maximumFractionDigits:3});
const labels={queued:"조회 대기",fetching:"중판 조회 중",review:"계산 완료",approved:"시트 반영 대기",saving:"시트 반영 중",saved:"시트 반영 완료",error:"처리 확인 필요"};
const state={batch:null,status:{},timer:null,order:null,previewTimer:null,previewVersion:0};
let lookupToastTimer;
function lookupToast(show){clearTimeout(lookupToastTimer);$("#lookup-toast").hidden=!show;if(show)lookupToastTimer=setTimeout(()=>{$("#lookup-toast").hidden=true;},4200);}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function notice(text){$("#notice").hidden=!text;$("#notice").textContent=text||"";}
function busy(button,value){button.disabled=value;button.setAttribute("aria-busy",String(value));}
async function api(url,data){
  const response=await fetch(url,data===undefined?{cache:"no-store"}:{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":$("input[name=csrfmiddlewaretoken]").value},body:JSON.stringify(data),cache:"no-store"});
  if(response.redirected){location.assign("/login/");throw Error("다시 로그인해 주세요.");}
  let result;try{result=await response.json();}catch{throw Error("서버 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");}
  if(!response.ok)throw Error(result.error||"요청을 처리하지 못했습니다.");return result;
}
async function loadHistory(){const {batches}=await api("/api/batches/");const list=$("#history-list");list.replaceChildren();if(!batches.length)list.append(node("p","아직 조회한 BL이 없습니다.","muted"));for(const b of batches){const button=node("button",`${b.bl} / ${labels[b.state]}`,"quiet");button.type="button";button.onclick=()=>loadBatch(b.id);list.append(button);}}
function syncUrl(){const url=new URL(location.href);url.search="";url.searchParams.set("batch",state.batch.id);window.history.replaceState({},"",url);}
function schedule(){clearTimeout(state.timer);if(state.batch&&["queued","fetching","approved","saving"].includes(state.batch.state))state.timer=setTimeout(async()=>{if($("#review-dialog").open){schedule();return;}await loadBatch(state.batch.id);await loadHistory();},3500);}
async function loadBatch(id){try{state.batch=await api(`/api/batches/${id}/`);syncUrl();render();schedule();}catch(e){$("#lookup-help").textContent=e.message;}}
function canEdit(){return state.batch.state==="review";}
function canDecline(o){return o.calculation.ready&&o.review_issues.every(i=>["opp","cost"].includes(i.kind));}
function render(){
  const b=state.batch;if(!b)return;const s=b.summary||{},loading=["queued","fetching"].includes(b.state),processing=["approved","saving"].includes(b.state);
  $("#empty").hidden=true;$("#batch-content").hidden=false;$("#batch-bl").textContent=`BL ${b.bl}`;$("#state").textContent=labels[b.state];notice(b.error);
  $("#progress-area").hidden=!(loading||processing);$("#progress").max=b.total||1;$("#progress").value=b.progress;
  $("#progress-label").textContent=`${loading?"중판 조회":"승인된 원본 재확인"} ${b.progress} / ${b.total||"확인 중"}`;
  $("#sum-invoices").textContent=loading?"조회 중":`${s.invoice_count||0}건`;$("#sum-amount").textContent=loading||!s.calculation_ready?"계산 중":money(s.invoice_total);$("#sum-pending").textContent=loading?"조회 중":`${s.pending||0}건`;
  $("#decision-section").hidden=!b.decisions?.length;renderDecisions();renderInvoices();
  $("#invoice-help").textContent=loading?"조회가 끝나면 청구 대상과 문구가 표시됩니다.":!s.calculation_ready?"추가 비용 결정 영역에서 계산에 필요한 정보를 확인해 주세요.":b.invoices.length?"부가세 포함 금액이며 10원 미만은 절사합니다. 청구 문구는 기존 결제 링크와 함께 전달하세요.":s.pending?"추가 청구를 결정하면 해당 주문이 여기에 표시됩니다.":"청구할 금액이 없습니다.";
  $("#approve-button").hidden=b.state==="saved"||(!loading&&!s.invoice_count&&!s.pending);$("#approve-button").disabled=!canEdit()||!state.status.can_approve||!s.calculation_ready||s.pending>0;
  $("#approval-help").textContent=b.state==="saved"?"청구 입력 시트에 반영하고 저장 금액을 검산했습니다.":s.pending?`추가 비용 ${s.pending}건을 결정하면 최종 내역을 시트에 반영할 수 있습니다.`:s.invoice_count?"확정한 내역을 청구 입력 시트에 반영하면 고객 청구(자동)에도 표시됩니다.":"";
  $("#rules-button").disabled=!canEdit();$("#refresh-button").disabled=!["review","error"].includes(b.state);$("#retry-button").hidden=b.state!=="error";
  $("#rules-meta").textContent=b.rules?.version?`적용월 ${b.rules.month} / 무료 박스 ${b.rules.free_boxes}개`:"청구규칙을 불러오고 있습니다.";
  const audit=$("#audit-list");audit.replaceChildren();for(const a of b.audit||[])audit.append(node("li",`${new Date(a.created_at).toLocaleString("ko-KR")} / ${a.action}`));
}
function identity(o){const block=node("div",undefined,"bill-identity");block.append(node("h3",o.customer||"고객명 확인 필요"),node("p",`주문 ${o.shop_orders.join(", ")}`,"identifier"));return block;}
function renderDecisions(){const list=$("#decisions");list.replaceChildren();for(const o of state.batch.decisions||[]){
  const article=node("article",undefined,"decision-row");article.append(identity(o),node("p",o.memo||o.review_issues.map(i=>i.text).join(" / "),"memo"));
  const reasons=node("p",o.review_issues.map(i=>i.text).join(" / "),"decision-reason");article.append(reasons,node("p",`주문금액 ￥${qty(o.amount)} / 기본 청구 ${o.calculation.ready?money(o.calculation.total):"계산 대기"}`,"muted"));
  const actions=node("div",undefined,"decision-actions");if(canDecline(o)){const decline=node("button","추가 청구 없음","quiet");decline.type="button";decline.disabled=!canEdit();decline.onclick=()=>declineOrder(o,decline);actions.append(decline);}
  const edit=node("button",canDecline(o)?"시간·금액 입력":"필요한 정보 입력","quiet");edit.type="button";edit.disabled=!canEdit();edit.onclick=()=>openOrder(o);actions.append(edit);article.append(actions);list.append(article);
}}
function renderInvoices(){const list=$("#invoices");list.replaceChildren();for(const o of state.batch.invoices||[]){
  const article=node("article",undefined,"invoice-row"),header=node("div",undefined,"invoice-heading");header.append(identity(o),node("strong",money(o.calculation.total),"invoice-amount"));article.append(header);
  const c=o.calculation,parts=[];if(Number(c.work_charge))parts.push(`작업비 ${money(c.work_charge)}`);if(Number(c.box_charge))parts.push(`박스비 ${money(c.box_charge)}`);if(Number(c.cbm_charge))parts.push(`부피 추가비 ${money(c.cbm_charge)}`);if(Number(c.extra_time_charge)||Number(c.extra_amount))parts.push(`별도 추가 ${money(Number(c.extra_time_charge)+Number(c.extra_amount))}`);
  article.append(node("p",parts.join(" / "),"muted"));const details=node("details",undefined,"message-preview"),summary=node("summary","청구 문구 보기"),text=node("textarea");text.value=o.message;text.readOnly=true;text.rows=7;text.setAttribute("aria-label",`${o.customer} 고객 청구 문구`);details.append(summary,text);article.append(details);
  const actions=node("div",undefined,"invoice-actions"),copy=node("button","청구 문구 복사","quiet");copy.type="button";copy.onclick=async()=>{try{await navigator.clipboard.writeText(o.message);copy.textContent="복사됨";}catch{details.open=true;text.focus();text.select();copy.textContent="선택된 문구를 복사하세요";}};actions.append(copy);
  if(canEdit()){const edit=node("button","추가금 수정","text-button");edit.type="button";edit.onclick=()=>openOrder(o);actions.append(edit);}article.append(actions);list.append(article);
}}
function openOrder(o){state.order=o;state.previewVersion++;const form=$("#review-form");$("#review-title").textContent="추가 비용 결정";$("#review-subtitle").textContent=`${o.customer||"고객명 확인 필요"} / 주문 ${o.shop_orders.join(", ")}`;$("#review-amount").textContent=`￥${qty(o.amount)}`;$("#review-base").textContent=o.calculation.ready?money(o.calculation.base):"계산 대기";$("#review-memo").textContent=o.memo||"등록된 추가 비용 메모가 없습니다.";
  $("#review-question").textContent="추가로 받을 시간이나 금액을 입력하세요. 두 항목이 모두 0이면 추가 비용을 받지 않습니다.";const decisions=$("#review-decisions");decisions.replaceChildren();for(const i of o.review_issues){const li=node("li");li.append(node("strong",i.text),node("p",i.action));decisions.append(li);}
  for(const el of form.elements){if(el.name){el.value=o[el.name]??"";el.disabled=false;el.required=false;}}form.elements.reason.value=o.reason||o.memo.slice(0,1000);
  for(const [id,name,show] of [["customer","customer",o.review_issues.some(i=>i.kind==="customer")],["mode","mode",o.review_issues.some(i=>i.kind==="mode")],["opp","opp",o.review_issues.some(i=>i.kind==="quantity")&&/opp|봉투/i.test(o.memo)],["boxes","boxes",o.review_issues.some(i=>i.kind==="quantity")&&/박스/.test(o.memo)],["free-hours","free_hours_override",o.review_issues.some(i=>i.kind==="free_hours")]]){const el=form.elements[name];$(`#${id}-field`).hidden=!show;el.disabled=!show;el.required=show&&name!=="free_hours_override";}
  $("#review-decline").hidden=!canDecline(o);$("#review-error").textContent="";preview(o.calculation);$("#review-dialog").showModal();
}
function changes(){const out={reviewed:true};for(const el of $("#review-form").elements)if(el.name&&!el.disabled)out[el.name]=el.value;return out;}
function preview(c){for(const [id,key] of [["base","base"],["time","extra_time_charge"],["extra","extra_amount"],["total","total"]])$(`#preview-${id}`).textContent=c.ready?money(c[key]):"계산 대기";$("#review-included").textContent=c.ready&&Number(c.box_charge)?`초과 박스비 ${money(c.box_charge)}은 기본 청구에 포함되어 있습니다. 추가 금액에 다시 더하지 마세요.`:"";}
async function action(name,data={}){state.batch=await api(`/api/batches/${state.batch.id}/${name}/`,{revision:state.batch.revision,...data});render();await loadHistory();schedule();}
async function declineOrder(o,button){busy(button,true);try{await action("edit",{order_id:o.id,changes:{extra_hours:"0",extra_amount:"0",reason:o.reason||"추가 청구 없음",reviewed:true}});$("#review-dialog").close();}catch(e){if($("#review-dialog").open)$("#review-error").textContent=e.message;else notice(e.message);}finally{busy(button,false);}}
$("#lookup-form").onsubmit=async e=>{e.preventDefault();busy($("#lookup-button"),true);lookupToast(true);$("#lookup-help").textContent="중판 조회를 요청하고 있습니다.";try{state.batch=await api("/api/batches/",{bl:$("#bl").value.trim()});syncUrl();render();await loadHistory();schedule();$("#lookup-help").textContent="조회가 끝나면 추가 비용 결정과 고객 청구 내역이 표시됩니다.";}catch(e){lookupToast(false);$("#lookup-help").textContent=e.message;}finally{busy($("#lookup-button"),false);}};
$("#close-dialog").onclick=()=>$("#review-dialog").close();$("#review-dialog").addEventListener("close",()=>{clearTimeout(state.previewTimer);state.previewVersion++;schedule();});
$("#review-form").oninput=()=>{clearTimeout(state.previewTimer);const version=++state.previewVersion;state.previewTimer=setTimeout(async()=>{try{const r=await api(`/api/batches/${state.batch.id}/preview/`,{revision:state.batch.revision,order_id:state.order.id,changes:changes()});if(version===state.previewVersion){preview(r.calculation);$("#review-error").textContent="";}}catch(e){if(version===state.previewVersion)$("#review-error").textContent=e.message;}},350);};
$("#review-decline").onclick=()=>declineOrder(state.order,$("#review-decline"));$("#review-form").onsubmit=async e=>{e.preventDefault();busy($("#review-save"),true);try{await action("edit",{order_id:state.order.id,changes:changes()});$("#review-dialog").close();}catch(e){$("#review-error").textContent=e.message;}finally{busy($("#review-save"),false);}};
for(const actionName of ["rules","refresh","retry"]){$(`#${actionName}-button`).onclick=async()=>{if(actionName==="refresh"&&!confirm("추가 비용 결정을 초기화하고 중판에서 다시 조회할까요? 이전 결정은 이력에 남습니다."))return;try{await action(actionName,actionName==="refresh"?{confirm_discard:true}:{});}catch(e){notice(e.message);}};}
$("#approve-button").onclick=async()=>{if(!confirm(`${state.batch.summary.invoice_count}건 / 총 ${money(state.batch.summary.invoice_total)}을 청구 입력 시트에 반영할까요?`))return;busy($("#approve-button"),true);try{await action("approve");}catch(e){notice(e.message);}finally{render();}};
(async()=>{try{state.status=await api("/api/status/");await loadHistory();const id=new URL(location.href).searchParams.get("batch");if(id)await loadBatch(id);}catch(e){$("#lookup-help").textContent=e.message;}})();
