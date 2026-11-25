"use strict";
const retry=document.querySelector("#prepare-retry");
async function prepare(){
  const view=location.pathname.match(/^\/bl-cost\/(work-check|dashboard)\/?$/)?.[1];
  retry.hidden=true;
  const countdown=document.querySelector("#prepare-countdown"),seconds=document.querySelector("#prepare-seconds");
  countdown.hidden=false;
  seconds.textContent="30";
  document.querySelector("#prepare-title").textContent="정산 화면을 준비하고 있어요";
  document.querySelector("#prepare-help").textContent="서버가 시작되는 동안 잠시만 기다려 주세요. 준비되면 자동으로 열립니다.";
  const deadline=Date.now()+30000;
  const timer=setInterval(()=>{
    const remaining=Math.max(0,Math.ceil((deadline-Date.now())/1000));
    if(remaining){seconds.textContent=String(remaining);return;}
    clearInterval(timer);
    countdown.hidden=true;
    document.querySelector("#prepare-title").textContent="조금 더 준비하고 있어요";
    document.querySelector("#prepare-help").textContent="서버 시작이 예상보다 길어지고 있어요. 연결되면 바로 열리니 잠시만 더 기다려 주세요.";
  },1000);
  for(let attempt=0;attempt<4;attempt++){
    try{
      const response=await fetch("/bl-cost/app"+(view?"/"+view:"")+location.search,{cache:"no-store",signal:AbortSignal.timeout(30000)});
      const html=await response.text();
      if(!response.ok||!html.includes('data-app-root="/bl-cost"')||!html.includes(view?`data-report-page="${view}"`:'id="lookup-form"'))throw Error("not-ready");
      clearInterval(timer);
      document.open();document.write(html);document.close();return;
    }catch{if(attempt<3)await new Promise(resolve=>setTimeout(resolve,1500));}
  }
  clearInterval(timer);
  countdown.hidden=true;
  document.querySelector("#prepare-title").textContent="아직 연결하지 못했어요";
  document.querySelector("#prepare-help").textContent="서버 시작이 지연되고 있습니다. 잠시 후 다시 연결해 주세요.";
  retry.hidden=false;
}
retry.onclick=prepare;
prepare();
