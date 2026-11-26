"use strict";
const $ = (selector) => document.querySelector(selector);
const appRoot = document.body.dataset.appRoot || "";
const money = (v) => `${Number(v || 0).toLocaleString("ko-KR", {maximumFractionDigits:0})}원`;
const qty = (v) => Number(v || 0).toLocaleString("ko-KR", {maximumFractionDigits:3});
const labels={queued:"조회 대기",fetching:"중판 조회 중",review:"추가 비용 결정",approved:"시트 자동 반영 대기",saving:"시트 자동 반영 중",saved:"시트 자동 반영 완료",error:"처리 확인 필요"};
const state={batch:null,status:{},timer:null,order:null,previewTimer:null,previewVersion:0,mutating:false};
let lookupToastTimer;
function lookupToast(show,title="조회 요청을 보냈어요",detail="잠시만 기다려 주세요. 진행 상황은 아래에 표시돼요.",pending=false){
  clearTimeout(lookupToastTimer);
  const toast=$("#lookup-toast"),image=toast.querySelector("img");
  if(show){const host=$("#review-dialog").open?$("#review-dialog"):document.body;if(toast.parentElement!==host)host.append(toast);toast.querySelector("strong").textContent=title;toast.querySelector("p").textContent=detail;if(!image.hasAttribute("src"))image.src=image.dataset.src;}
  else image.removeAttribute("src");
  toast.hidden=!show;toast.classList.toggle("is-processing",show&&pending);
  if(show&&!pending)lookupToastTimer=setTimeout(()=>lookupToast(false),4200);
}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function notice(text){for(const selector of ["#notice","#save-error"]){$(selector).hidden=!text;$(selector).textContent=text||"";}}
function busy(button,value){if(value){button.dataset.idleLabel=button.textContent;button.textContent=button.id==="lookup-button"?"조회 요청 중…":"처리 중…";}else if(button.dataset.idleLabel){button.textContent=button.dataset.idleLabel;delete button.dataset.idleLabel;}button.disabled=value;button.setAttribute("aria-busy",String(value));}
async function api(url,data){
  const response=await fetch(appRoot+url,data===undefined?{cache:"no-store"}:{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":$("input[name=csrfmiddlewaretoken]").value},body:JSON.stringify(data),cache:"no-store"});
  if(response.redirected){location.assign("/login/");throw Error("다시 로그인해 주세요.");}
  let result;try{result=await response.json();}catch{throw Error("서버 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");}
  if(!response.ok)throw Error(result.error||"요청을 처리하지 못했습니다.");return result;
}
async function loadHistory(){const {batches}=await api("/api/batches/");const list=$("#history-list");list.replaceChildren();if(!batches.length)list.append(node("p","아직 조회한 BL이 없습니다.","muted"));for(const b of batches){const button=node("button",`${b.bl} / ${labels[b.state]}`,"quiet");button.type="button";button.disabled=state.mutating;button.onclick=()=>loadBatch(b.id);list.append(button);}}
function syncUrl(){const url=new URL(location.href);url.search="";url.searchParams.set("batch",state.batch.id);window.history.replaceState({},"",url);}
function schedule(){clearTimeout(state.timer);if(state.batch&&["queued","fetching","approved","saving"].includes(state.batch.state))state.timer=setTimeout(async()=>{if($("#review-dialog").open){schedule();return;}await loadBatch(state.batch.id);await loadHistory();},3500);}
async function loadBatch(id){try{state.batch=await api(`/api/batches/${id}/`);syncUrl();render();schedule();}catch(e){$("#lookup-help").textContent=e.message;}}
function canEdit(){return ["review","saved"].includes(state.batch.state)&&!state.mutating;}
function canCopy(){const b=state.batch,r=b.sheet_result||{};return !b.error&&["review","saved"].includes(b.state)&&r.verified&&(r.synced_revision===b.revision||(r.synced_revision===undefined&&b.state==="saved"));}
function renderSheetReceipt(){
  const b=state.batch,r=b.sheet_result||{},target=r.targets?.["청구 입력(내가 입력)"],count=Number(target?.count||0),receipt=$("#sheet-receipt"),link=$("#sheet-record-link");
  receipt.hidden=link.hidden=!canCopy()||!count;
  receipt.textContent="";
  if(receipt.hidden)return;
  const added=target.added_rows?.length||0,rows=target.rows||Array.from({length:count},(_,i)=>target.start+i);
  const savedAt=new Date(r.synced_at||b.updated_at).toLocaleString("ko-KR");
  receipt.textContent=`청구 입력 ${count}건 저장 · 신규 ${added}건 / 기존 ${count-added}건 갱신 · ${savedAt} 검산`;
  link.href=`https://docs.google.com/spreadsheets/d/${r.spreadsheet_id}/edit#gid=${target.sheet_id}&range=A${Math.min(...rows)}:AD${Math.max(...rows)}`;
}
function canDecline(o){return o.calculation.ready&&o.review_issues.every(i=>["opp","cost"].includes(i.kind));}
function render(){
  const b=state.batch;if(!b)return;const s=b.summary||{},loading=["queued","fetching"].includes(b.state),processing=["approved","saving"].includes(b.state);
  $("#empty").hidden=true;$("#batch-content").hidden=false;$("#batch-bl").textContent=`BL ${b.bl}`;$("#state").textContent=b.state==="review"?(b.error?"연결 확인 필요":`추가 비용 ${s.pending||0}건 결정`):b.state==="saved"?(!canCopy()?"시트 반영 확인 필요":!s.invoice_count?"청구 없음":labels.saved):labels[b.state];$("#state").dataset.state=b.state;notice(b.error);
  $("#source-time").textContent=b.collected_at?`중판 조회 ${new Date(b.collected_at).toLocaleString("ko-KR")}`:"";
  $("#progress-area").hidden=!(loading||processing);$("#progress").max=b.total||1;$("#progress").value=b.progress;
  $("#progress-label").textContent=loading?`중판 조회 ${b.progress} / ${b.total||"확인 중"}`:"확정된 청구를 시트에 저장·검산하고 있어요.";if(processing)$("#progress").removeAttribute("value");
  $("#sum-invoices").textContent=loading?"조회 중":`${s.invoice_count||0}건`;$("#sum-amount").textContent=loading?"조회 중":b.rules?.version?money(s.invoice_total):"계산 기준 확인";$("#sum-pending").textContent=loading?"조회 중":`${s.pending||0}건`;
  const pending=b.decisions?.length||0,completed=b.completed_decisions?.length||0;
  $("#decision-section").hidden=!pending&&!completed;$("#decision-count").textContent=pending?`${pending}건 결정 필요`:"모두 결정했어요";$("#decision-help").textContent=pending?"입고오류메시지를 보고 추가로 받을 시간이나 금액을 정하세요. 결정한 내역은 언제든 다시 수정할 수 있습니다.":"추가 비용 결정을 마쳤습니다. 잘못 입력했다면 아래 완료 내역에서 수정하세요.";renderDecisions();renderCompleted();renderInvoices();
  $("#invoice-help").textContent=loading?"조회가 끝나면 확정된 청구부터 자동 반영됩니다.":processing?"시트 저장·검산이 끝나면 청구 문구를 복사할 수 있습니다.":b.error?"저장 문제를 해결한 뒤 청구 문구를 복사해 주세요.":b.invoices.length?"부가세 포함 금액이며 10원 미만은 절사합니다. 시트에 반영된 청구 문구를 기존 결제 링크와 함께 전달하세요.":s.pending?"추가 비용을 결정하면 해당 주문이 여기에 표시되고 시트도 자동 갱신됩니다.":"청구할 금액이 없습니다.";
  let syncHelp;
  if(loading)syncHelp="조회가 끝나면 확정된 청구부터 자동으로 시트에 저장합니다.";
  else if(processing)syncHelp="확정된 청구를 저장하고 금액을 확인합니다. 중판을 다시 조회하지 않습니다.";
  else if(b.error)syncHelp="결정한 내역은 보관되어 있습니다. 오류 내용을 확인한 뒤 다시 시도해 주세요.";
  else if(b.state==="saved"&&!canCopy())syncHelp="저장 결과를 확인하지 못했습니다. ‘시트 다시 동기화’로 확인해 주세요.";
  else if(s.pending)syncHelp=`${canCopy()?(s.invoice_count?`확정된 ${s.invoice_count}건은 시트 저장·검산을 완료했습니다.`:"현재 저장할 확정 청구가 없습니다."):"확정된 청구의 시트 반영이 필요합니다."} 추가 비용 ${s.pending}건은 결정 전까지 고객 청구에서 보류하며, 결정 저장 시 자동 갱신됩니다.`;
  else if(b.state==="saved")syncHelp=s.invoice_count?"시트 저장·금액 검산을 완료했습니다. 비용을 수정하면 시트도 자동 갱신됩니다.":b.sheet_result?.no_changes?"현재 저장할 청구가 없습니다. 조회 원본은 DB에 보관했습니다.":"청구할 금액이 없습니다. 기존 청구를 취소한 경우 해당 행도 0원으로 갱신했습니다.";
  else syncHelp="확정된 청구는 자동 반영됩니다. 연결이 멈췄다면 아래 연결 관리에서 다시 동기화하세요.";
  $("#approval-help").textContent=syncHelp;
  renderSheetReceipt();
  $("#rules-button").disabled=!canEdit();$("#refresh-button").disabled=state.mutating||!["review","saved","error"].includes(b.state);$("#sync-button").disabled=!canEdit();$("#retry-button").hidden=!b.error||loading||processing;$("#retry-button").disabled=state.mutating;
  $("#lookup-button").disabled=state.mutating||$("#lookup-button").getAttribute("aria-busy")==="true";$("#bl").readOnly=state.mutating;$("#review-save").disabled=!canEdit();$("#review-decline").disabled=!canEdit();$("#close-dialog").disabled=state.mutating;$("#review-dialog .dialog-body").inert=state.mutating;for(const button of document.querySelectorAll("#history-list button"))button.disabled=state.mutating;
  $("#rules-meta").textContent=b.rules?.version?`적용월 ${b.rules.month} / 무료 박스 ${b.rules.free_boxes}개`:"청구규칙을 불러오고 있습니다.";
  const audit=$("#audit-list");audit.replaceChildren();for(const a of b.audit||[])audit.append(node("li",`${new Date(a.created_at).toLocaleString("ko-KR")} / ${a.action}`));
  if(!state.mutating){if(processing)lookupToast(true,"시트에 자동으로 반영하고 있어요","확정된 청구를 저장하고 금액을 확인하고 있어요.",true);else if(!loading)lookupToast(false);}
}
function identity(o){const block=node("div",undefined,"bill-identity");block.append(node("h3",o.customer||"고객명 확인 필요"),node("p",`주문번호 ${o.shop_orders.join(", ")}`,"identifier"));return block;}
function correctionIssues(o){return o.review_issues.filter(i=>!["opp","cost"].includes(i.kind));}
function cbmQty(v){return Number(v||0).toLocaleString("ko-KR",{maximumFractionDigits:6});}
function volumeRule(c){return c.ready?`무료 3 CBM · 초과 ${cbmQty(c.cbm_excess)} CBM · 부피 추가비 ${money(c.cbm_charge)} (VAT 포함)${Number(c.cbm_charge)?" · 총 청구금액에 자동 합산됨 · 별도 추가금 입력 불필요":""}`:c.error;}
function volumeDetails(o){
  if(!o.box_volume)return null;
  const v=o.box_volume,c=o.calculation,details=node("details",undefined,"cbm-details");
  details.append(node("summary",c.ready?`${o.cbm_override?"직접 확정":"박스 규격 계산"} · ${cbmQty(o.cbm)} CBM`:"박스 규격 CBM · 확인 필요"));
  if(o.cbm_override)details.append(node("p","현장 확인 후 직접 입력한 CBM을 적용합니다.","help"));
  else if(v.error)details.append(node("p",v.error,"help"));
  else {const list=node("ul",undefined,"cbm-items");for(const item of v.items){const li=node("li");li.append(node("span",`${item.size}호 (${item.dimensions.join(" × ")} cm)`),node("span",`${qty(item.count)}개 × ${cbmQty(item.unit_cbm)} CBM = ${cbmQty(item.cbm)} CBM`));list.append(li);}details.append(list);}
  details.append(node("p",volumeRule(c),"cbm-rule"));return details;
}
function sourceLinks(o){const links=o.source_links||[],group=document.createDocumentFragment();if(!links.length)return group;let target=group;if(links.length>1){const details=node("details",undefined,"tracking-links");details.append(node("summary",`중판 내역 바로가기 (${links.length})`,"button quiet"));target=node("div");details.append(target);group.append(details);}for(const source of links){const link=node("a",links.length===1?"중판 내역 바로가기":`트래킹 ${source.tracking}`,links.length===1?"button quiet":"source-tracking");link.href=source.url;link.target="_blank";link.rel="noopener noreferrer";link.setAttribute("aria-label",`트래킹 ${source.tracking} 중판 내역을 새 탭에서 열기`);target.append(link);}return group;}
function renderDecisions(){const list=$("#decisions");list.replaceChildren();for(const o of state.batch.decisions||[]){
  const article=node("article",undefined,"decision-row"),content=node("div",undefined,"decision-copy");content.append(identity(o));if(o.memo){const memo=node("div",undefined,"decision-source");memo.append(node("span","입고오류메시지","source-label"),node("p",o.memo,"memo"));content.append(memo);}
  const issues=correctionIssues(o);if(issues.length)content.append(node("p",issues.map(i=>i.text).join(" / "),"decision-reason"));const volume=volumeDetails(o);if(volume&&o.calculation.ready)content.append(volume);content.append(node("p",`주문금액 ￥${qty(o.amount)} / 기본 청구 ${o.calculation.ready?money(o.calculation.total):"계산 대기"}`,"muted"));article.append(content);
  const actions=node("div",undefined,"decision-actions");if(canDecline(o)){const decline=node("button","추가 청구 없음","quiet");decline.type="button";decline.disabled=!canEdit();decline.onclick=()=>declineOrder(o,decline);actions.append(decline);}
  const edit=node("button",canDecline(o)?"시간·금액 입력":"필요한 정보 입력","quiet");edit.type="button";edit.disabled=!canEdit();edit.onclick=()=>openOrder(o);actions.append(edit,sourceLinks(o));article.append(actions);list.append(article);
}}
function renderCompleted(){const orders=state.batch.completed_decisions||[];$("#completed-section").hidden=!orders.length;$("#completed-heading").textContent=`결정 완료 ${orders.length}건 · 다시 수정 가능`;const list=$("#completed-decisions");list.replaceChildren();for(const o of orders){const article=node("article",undefined,"completed-row"),content=node("div");content.append(identity(o));const parts=[];if(Number(o.extra_hours))parts.push(`추가 ${qty(o.extra_hours)}시간`);if(Number(o.extra_amount))parts.push(`별도 ${money(o.extra_amount)}`);content.append(node("p",parts.join(" · ")||"추가 청구 없음","decision-result"));const edit=node("button","결정 수정","quiet");edit.type="button";edit.disabled=!canEdit();edit.onclick=()=>openOrder(o);article.append(content,edit);list.append(article);}}
function renderInvoices(){const list=$("#invoices");list.replaceChildren();for(const o of state.batch.invoices||[]){
  const article=node("article",undefined,"invoice-row"),header=node("div",undefined,"invoice-heading"),total=node("div",undefined,"invoice-total");total.append(node("span","최종 청구액 (VAT 포함)"),node("strong",money(o.calculation.total),"invoice-amount"));header.append(identity(o),total);article.append(header);
  const c=o.calculation,breakdown=node("dl",undefined,"invoice-breakdown");for(const [label,value] of [["작업비",c.work_charge],["박스비",c.box_charge],["부피 추가비 (VAT 포함)",c.cbm_charge],["추가시간",c.extra_time_charge],["별도 추가금",c.extra_amount]])if(Number(value)){const part=node("div");part.append(node("dt",label),node("dd",money(value)));breakdown.append(part);}article.append(breakdown);
  const volume=volumeDetails(o);if(volume)article.append(volume);
  const tools=node("div",undefined,"invoice-tools"),details=node("details",undefined,"message-preview"),summary=node("summary","청구 문구 보기"),text=node("textarea");details.hidden=!canCopy();text.value=o.message;text.readOnly=true;text.rows=7;text.setAttribute("aria-label",`${o.customer} 고객 청구 문구`);details.append(summary,text);
  const actions=node("div",undefined,"invoice-actions"),copy=node("button","청구 문구 복사","primary");copy.type="button";copy.disabled=!canCopy();copy.onclick=async()=>{try{await navigator.clipboard.writeText(o.message);copy.textContent="복사됨";}catch{details.open=true;text.focus();text.select();copy.textContent="선택된 문구를 복사하세요";}};actions.append(copy);
  if(canEdit()){const edit=node("button","비용 수정","quiet");edit.type="button";edit.onclick=()=>openOrder(o);actions.append(edit);}tools.append(details,actions);article.append(tools);list.append(article);
}}
function openOrder(o){if(!canEdit())return;state.order=o;state.previewVersion++;const form=$("#review-form");$("#review-title").textContent=o.reviewed?"추가 비용 수정":"추가 비용 결정";$("#review-save").textContent=o.reviewed?"수정 내용 저장":"추가 청구 반영";$("#review-subtitle").textContent=o.customer||"고객명 확인 필요";$("#review-order").textContent=`주문번호 ${o.shop_orders.join(", ")}`;$("#review-amount").textContent=`￥${qty(o.amount)}`;$("#review-base").textContent=o.calculation.ready?money(o.calculation.base):"계산 대기";$("#review-memo").textContent=o.memo||"등록된 입고오류메시지가 없습니다.";$("#review-source-links").replaceChildren(sourceLinks(o));
  const volume=volumeDetails(o);$("#review-volume").replaceChildren();$("#review-volume").hidden=!volume;if(volume){volume.open=!o.box_volume.error;$("#review-volume").append(volume);}
  $("#review-question").textContent="추가할 시간·금액만 입력하세요. 청구하지 않는 항목은 0으로 두세요.";const decisions=$("#review-decisions");decisions.replaceChildren();for(const i of correctionIssues(o)){const li=node("li");li.append(node("strong",i.text),node("p",i.action));decisions.append(li);}decisions.hidden=!decisions.childElementCount;
  for(const el of form.elements){if(el.name){el.value=o[el.name]??"";el.disabled=false;el.required=false;}}form.elements.reason.value=o.reason&&o.reason!=="추가 청구 없음"?o.reason:o.memo.slice(0,1000);
  for(const [id,name,show] of [["customer","customer",o.review_issues.some(i=>i.kind==="customer")],["mode","mode",o.review_issues.some(i=>i.kind==="mode")],["opp","opp",o.review_issues.some(i=>i.kind==="quantity")&&/opp|봉투/i.test(o.memo)],["boxes","boxes",o.review_issues.some(i=>i.kind==="cbm")||(o.review_issues.some(i=>i.kind==="quantity")&&/박스/.test(o.memo))],["cbm","cbm",o.box_volume&&o.review_issues.some(i=>["quantity","cbm"].includes(i.kind))],["free-hours","free_hours_override",o.review_issues.some(i=>i.kind==="free_hours")]]){const el=form.elements[name];$(`#${id}-field`).hidden=!show;el.disabled=!show;el.required=show&&name!=="free_hours_override";}
  $("#review-decline").hidden=!canDecline(o);$("#review-error").textContent="";preview(o.calculation);$("#review-dialog").showModal();
}
function changes(){const out={reviewed:true};for(const el of $("#review-form").elements)if(el.name&&!el.disabled)out[el.name]=el.value;return out;}
function preview(c){for(const [id,key] of [["base","base"],["time","extra_time_charge"],["extra","extra_amount"],["total","total"]])$(`#preview-${id}`).textContent=c.ready?money(c[key]):"계산 대기";const volume=$("#review-volume .cbm-rule");if(volume)volume.textContent=volumeRule(c);$("#review-included").hidden=!(c.ready&&Number(c.box_charge));$("#review-included").textContent=c.ready&&Number(c.box_charge)?`초과 박스비 ${money(c.box_charge)} 포함 · 추가금에 다시 더하지 마세요.`:"";}
async function action(name,data={}){
  if(state.mutating)throw Error("앞선 처리가 끝나면 다시 시도해 주세요.");
  state.mutating=true;
  clearTimeout(state.previewTimer);state.previewVersion++;
  const titles={edit:"결정을 저장하고 있어요",sync:"시트 자동 반영을 준비하고 있어요",rules:"청구규칙을 확인하고 있어요",refresh:"중판 조회를 준비하고 있어요",retry:"다시 처리를 준비하고 있어요"};
  try{render();lookupToast(true,titles[name]||"처리 중이에요","잠시만 기다려 주세요. 완료되면 화면에 반영돼요.",true);state.batch=await api(`/api/batches/${state.batch.id}/${name}/`,{revision:state.batch.revision,...data});render();schedule();await loadHistory();}
  finally{state.mutating=false;lookupToast(false);render();}
}
async function declineOrder(o,button){if(state.mutating)return;busy(button,true);try{await action("edit",{order_id:o.id,changes:{extra_hours:"0",extra_amount:"0",reason:o.reason||"추가 청구 없음",reviewed:true}});$("#review-dialog").close();}catch(e){if($("#review-dialog").open)$("#review-error").textContent=e.message;else notice(e.message);}finally{busy(button,false);}}
$("#lookup-form").onsubmit=async e=>{e.preventDefault();if(state.mutating)return;busy($("#lookup-button"),true);lookupToast(true);$("#lookup-help").textContent="중판 조회를 요청하고 있습니다.";try{state.batch=await api("/api/batches/",{bl:$("#bl").value.trim()});syncUrl();render();await loadHistory();schedule();$("#lookup-help").textContent="확정된 청구는 자동 반영됩니다. 결정이 필요한 추가 비용만 확인해 주세요.";}catch(e){lookupToast(false);$("#lookup-help").textContent=e.message;}finally{busy($("#lookup-button"),false);}};
$("#close-dialog").onclick=()=>$("#review-dialog").close();$("#review-dialog").addEventListener("cancel",e=>{if(state.mutating)e.preventDefault();});$("#review-dialog").addEventListener("close",()=>{clearTimeout(state.previewTimer);state.previewVersion++;if(["approved","saving"].includes(state.batch.state))render();schedule();});
$("#review-form").oninput=()=>{clearTimeout(state.previewTimer);const version=++state.previewVersion;state.previewTimer=setTimeout(async()=>{try{const r=await api(`/api/batches/${state.batch.id}/preview/`,{revision:state.batch.revision,order_id:state.order.id,changes:changes()});if(version===state.previewVersion){preview(r.calculation);$("#review-error").textContent="";}}catch(e){if(version===state.previewVersion)$("#review-error").textContent=e.message;}},350);};
$("#review-decline").onclick=()=>declineOrder(state.order,$("#review-decline"));$("#review-form").onsubmit=async e=>{e.preventDefault();if(state.mutating)return;busy($("#review-save"),true);try{await action("edit",{order_id:state.order.id,changes:changes()});$("#review-dialog").close();}catch(e){$("#review-error").textContent=e.message;}finally{busy($("#review-save"),false);}};
for(const actionName of ["rules","refresh","sync"]){$(`#${actionName}-button`).onclick=async()=>{if(actionName==="refresh"&&!confirm("기존 추가 비용 결정을 초기화하고 최신 중판 내역으로 다시 계산·자동 반영할까요? 이전 결정은 이력에 남습니다."))return;try{await action(actionName,actionName==="refresh"?{confirm_discard:true}:{});}catch(e){notice(e.message);}};}
$("#retry-button").onclick=async()=>{try{await action(state.batch.state==="error"?"retry":state.batch.rules?.version?"sync":"rules");}catch(e){notice(e.message);}};
(async()=>{try{state.status=await api("/api/status/");await loadHistory();const id=new URL(location.href).searchParams.get("batch");if(id)await loadBatch(id);}catch(e){$("#lookup-help").textContent=e.message;}})();
