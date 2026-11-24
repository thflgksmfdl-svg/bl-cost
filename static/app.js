"use strict";
const $ = (s) => document.querySelector(s);
const labels = {queued:"조회 대기",fetching:"중판 수집 중",review:"정산 검토",approved:"저장 대기",saving:"원본 재확인·저장 중",saved:"반영·검산 완료",error:"확인 필요"};
const money = (v) => `${Number(v || 0).toLocaleString("ko-KR", {maximumFractionDigits:0})}원`;
const qty = (v) => Number(v || 0).toLocaleString("ko-KR", {maximumFractionDigits:3});
const state = {batch:null, batches:[], status:{}, timer:null, order:null, previewTimer:null, previewVersion:0};
const csrf = () => $("input[name=csrfmiddlewaretoken]").value;
function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
async function api(url, data) {
  const response=await fetch(url, data===undefined?{cache:"no-store"}:{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify(data),cache:"no-store"});
  if(response.redirected){location.assign("/login/");throw Error("다시 로그인해 주세요.");}
  let result; try{result=await response.json();}catch{throw Error("서버 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");}
  if(!response.ok)throw Error(result.error || "요청을 처리하지 못했습니다.");
  return result;
}
function notice(text) { $("#notice").hidden=!text; $("#notice").textContent=text||""; }
function buttonBusy(button,busy) { button.disabled=busy; button.setAttribute("aria-busy",String(busy)); }
function renderHistory() {
  const list=$("#history-list"); list.replaceChildren();
  if(!state.batches.length){list.append(node("p","아직 조회한 BL이 없습니다.","muted"));return;}
  for(const b of state.batches){const bt=node("button",undefined,"history-item");bt.type="button";bt.setAttribute("aria-current",String(state.batch?.id===b.id));bt.append(node("strong",b.bl),node("span",`${labels[b.state]} / ${new Date(b.created_at).toLocaleDateString("ko-KR")}`));bt.onclick=()=>loadBatch(b.id);list.append(bt);}
}
async function loadHistory(){const d=await api("/api/batches/");state.batches=d.batches;renderHistory();}
function syncUrl(){const u=new URL(location.href);if(state.batch)u.searchParams.set("batch",state.batch.id);u.searchParams.set("filter",$("#filter").value);const q=$("#search").value;if(q)u.searchParams.set("q",q);else u.searchParams.delete("q");window.history.replaceState({},"",u);}
async function loadBatch(id){try{state.batch=await api(`/api/batches/${id}/`);syncUrl();render();renderHistory();schedule();}catch(e){$("#lookup-help").textContent=e.message;}}
function schedule(){clearTimeout(state.timer);if(state.batch&&["queued","fetching","approved","saving"].includes(state.batch.state)){state.timer=setTimeout(async()=>{if(!$("#review-dialog").open)await loadBatch(state.batch.id);await loadHistory();},3500);}}
function render(){
  const b=state.batch;if(!b)return;
  $("#empty").hidden=true;$("#batch-content").hidden=false;
  $("#batch-bl").textContent=`BL ${b.bl}`;$("#batch-meta").textContent=`마지막 변경 ${new Date(b.updated_at).toLocaleString("ko-KR")}`;$("#state").textContent=labels[b.state];
  notice(b.error || (b.state==="saved"?"공유시트 저장 및 재조회 검산을 완료했습니다.":""));
  const busy=["queued","fetching","approved","saving"].includes(b.state);$("#progress-area").hidden=!busy;
  $("#progress").max=b.total||1;$("#progress").value=b.progress;
  $("#progress-label").textContent=`${b.state==="saving"?"승인한 원본 다시 확인 중":"하위 주문 수집 중"} ${b.progress} / ${b.total||"확인 중"}`;
  $("#sum-orders").textContent=`${qty(b.summary.orders)}개`;$("#sum-products").textContent=`${qty(b.summary.products)}행`;$("#sum-hours").textContent=`${qty(b.summary.work?.["작업시간"])}시간`;
  $("#sum-amount").textContent=b.summary.calculation_ready?money(b.summary.total):"계산 대기";
  $("#approve-button").disabled=b.state!=="review"||!state.status.can_approve||!b.summary.calculation_ready||b.summary.pending>0;
  $("#rules-button").disabled=b.state!=="review";$("#refresh-button").disabled=!["review","error"].includes(b.state);$("#retry-button").hidden=b.state!=="error";
  $("#approval-help").textContent=b.state==="saved"?"승인된 금액과 원본을 이력으로 보관합니다.":`${b.summary.pending||0}개 주문의 검토가 남아 있습니다. 최종 승인 후에만 공유시트에 저장합니다.`;
  $("#rules-meta").textContent=b.rules?.version?`적용월 ${b.rules.month} / 환율 ${b.rules.exchange}원 / 시간당 ${money(b.rules.hourly)}(VAT 별도) / 무료 박스 ${b.rules.free_boxes}개 / 규칙 ${b.rules.version.slice(0,12)}`:"시트에서 규칙을 불러오면 금액을 계산합니다.";
  const ul=$("#audit-list");ul.replaceChildren();for(const a of b.audit||[])ul.append(node("li",`${new Date(a.created_at).toLocaleString("ko-KR")} / ${a.actor__username||"시스템"} / ${a.action}`));
  renderOrders();
}
function renderOrders(){
  const b=state.batch;if(!b)return;const tbody=$("#orders");tbody.replaceChildren();const f=$("#filter").value,q=$("#search").value.trim().toLowerCase();
  const orders=(b.orders||[]).filter(o=>{if(f==="pending"&&!(o.review_required&&!o.reviewed))return false;if(f==="work"&&!Number(o.work["작업시간"]))return false;if(f==="pack"&&!(/opp|박스|봉투|비용|청구/i.test(o.memo)||o.opp||o.boxes))return false;return !q||[o.customer,o.jungpan_order,...o.shop_orders,...o.trackings].join(" ").toLowerCase().includes(q);});
  $("#row-count").textContent=`${orders.length} / ${b.orders?.length||0}개`;
  if(!orders.length){const tr=node("tr"),td=node("td",b.orders?.length?"조건에 맞는 주문이 없습니다.":"수집이 끝나면 주문별 내역이 표시됩니다.","muted");td.colSpan=6;tr.append(td);tbody.append(tr);return;}
  for(const o of orders){const c=o.calculation,tr=node("tr"),name=node("td");name.append(node("strong",o.customer||"고객명 확인 필요"),node("small",o.jungpan_order,"identifier"),node("small",o.shop_orders.join(", "),"identifier"));
    const price=node("td",`￥${qty(o.amount)}`,"num"),hours=node("td",`${qty(o.work["작업시간"])} / ${c.ready?qty(c.free_hours):"미확정"}`,"num");hours.append(node("small",o.mode||"입고방식 확인 필요"));
    const pack=node("td",`OPP ${o.opp===null?"미기록":qty(o.opp)} / 박스 ${o.boxes===null?"미기록":qty(o.boxes)}`);if(o.memo)pack.append(node("small",o.memo.length>45?`${o.memo.slice(0,45)}…`:o.memo));
    const total=node("td",c.ready?money(c.total):"계산 대기","num"),review=node("td"),bt=node("button",o.review_required&&!o.reviewed?"검토 필요":"상세 보기","quiet");bt.type="button";bt.onclick=()=>openOrder(o.id);review.append(bt);if(o.reviewed)review.append(node("small","검토 완료"));
    const cells=[name,price,hours,pack,total,review];cells.forEach((td,i)=>td.dataset.label=["주문·고객","중판 금액","실제 / 무료시간","포장재","청구금액","검토"][i]);tr.append(...cells);tbody.append(tr);
  }
}
function openOrder(id){const o=state.batch.orders.find(o=>o.id===id);state.order=o;const form=$("#review-form");
  state.previewVersion++;
  $("#review-title").textContent=o.review_required?"추가 비용 검토":"주문 상세 검토";$("#review-subtitle").textContent=`중판 주문 ${o.jungpan_order}`;$("#review-amount").textContent=`￥${qty(o.amount)}`;$("#review-hours").textContent=`${qty(o.work["작업시간"])}시간`;
  $("#review-memo").textContent=o.memo||"등록된 입고오류메시지가 없습니다.";$("#review-source").textContent=`오더넘버: ${o.shop_orders.join(", ")}\n트래킹: ${o.trackings.join(", ")}\n회원: ${o.member}\n담당자메모: ${o.manager_memo}\n관리자메모: ${o.admin_memo}`;
  const warnings=$("#review-warnings");warnings.replaceChildren();for(const w of o.warnings)warnings.append(node("p",w,"notice"));
  for(const el of form.elements){if(!el.name)continue;if(el.type==="checkbox")el.checked=!!o[el.name];else el.value=o[el.name]??"";el.disabled=state.batch.state!=="review";}
  $("#review-save").disabled=state.batch.state!=="review";$("#review-error").textContent="";preview(o.calculation);$("#review-dialog").showModal();
}
function changes(){const out={};for(const el of $("#review-form").elements){if(el.name)out[el.name]=el.type==="checkbox"?el.checked:el.value;}return out;}
function preview(c){for(const [id,key] of [["base","base"],["time","extra_time_charge"],["box","box_charge"],["total","total"]])$(`#preview-${id}`).textContent=c.ready?money(c[key]):"계산 대기";}
async function action(name,data={}){state.batch=await api(`/api/batches/${state.batch.id}/${name}/`,{revision:state.batch.revision,...data});render();await loadHistory();schedule();}
$("#lookup-form").onsubmit=async(e)=>{e.preventDefault();const button=$("#lookup-button");buttonBusy(button,true);$("#lookup-help").textContent="조회 요청을 등록하고 있습니다.";try{state.batch=await api("/api/batches/",{bl:$("#bl").value.trim()});syncUrl();render();await loadHistory();schedule();$("#lookup-help").textContent="화면을 닫아도 초안과 진행 이력은 DB에 보관됩니다.";}catch(e){$("#lookup-help").textContent=e.message;}finally{buttonBusy(button,false);}};
$("#filter").onchange=()=>{syncUrl();renderOrders();};$("#search").oninput=()=>{syncUrl();renderOrders();};
$("#close-dialog").onclick=()=>{$("#review-dialog").close();schedule();};
$("#review-dialog").addEventListener("close",()=>{clearTimeout(state.previewTimer);state.previewVersion++;schedule();});
$("#review-form").oninput=()=>{if(state.batch.state!=="review")return;clearTimeout(state.previewTimer);const version=++state.previewVersion;state.previewTimer=setTimeout(async()=>{try{const r=await api(`/api/batches/${state.batch.id}/preview/`,{revision:state.batch.revision,order_id:state.order.id,changes:changes()});if(version===state.previewVersion){preview(r.calculation);$("#review-error").textContent="";}}catch(e){if(version===state.previewVersion)$("#review-error").textContent=e.message;}},450);};
$("#review-form").onsubmit=async(e)=>{e.preventDefault();const b=$("#review-save");buttonBusy(b,true);try{await action("edit",{order_id:state.order.id,changes:changes()});$("#review-dialog").close();}catch(e){$("#review-error").textContent=e.message;}finally{buttonBusy(b,state.batch.state!=="review");}};
$("#rules-button").onclick=async()=>{const b=$("#rules-button");buttonBusy(b,true);try{await action("rules");notice("");}catch(e){notice(e.message);}finally{b.disabled=state.batch.state!=="review";b.setAttribute("aria-busy","false");}};
$("#refresh-button").onclick=async()=>{if(!confirm("저장 전 검토 내용을 초기화하고 중판 원본을 다시 조회할까요? 이전 검토는 작업 이력에 남습니다."))return;try{await action("refresh",{confirm_discard:true});}catch(e){notice(e.message);}};
$("#retry-button").onclick=async()=>{try{await action("retry");}catch(e){notice(e.message);}};
$("#approve-button").onclick=async()=>{if(!confirm(`BL ${state.batch.bl}\n총 ${money(state.batch.summary.total)}\n원본을 다시 확인한 뒤 공유시트에 저장합니다. 승인할까요?`))return;const b=$("#approve-button");buttonBusy(b,true);try{await action("approve");}catch(e){notice(e.message);}finally{b.disabled=state.batch.state!=="review"||state.batch.summary.pending>0;b.setAttribute("aria-busy","false");}};
(async()=>{try{state.status=await api("/api/status/");$("#connection").textContent=`중판 ${state.status.jungpan_configured?"설정됨":"설정 필요"} / 구글시트 ${state.status.sheets_configured?"설정됨":"연결 필요"} / DB ${state.status.database_persistent?"연결됨":"개발용"}`;const u=new URL(location.href);$("#filter").value=u.searchParams.get("filter")||"all";$("#search").value=u.searchParams.get("q")||"";await loadHistory();const id=u.searchParams.get("batch");if(id)await loadBatch(id);}catch(e){$("#lookup-help").textContent=e.message;}})();
