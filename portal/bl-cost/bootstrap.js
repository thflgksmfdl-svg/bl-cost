"use strict";
const retry=document.querySelector("#prepare-retry");
async function prepare(){
  retry.hidden=true;
  document.querySelector("#prepare-title").textContent="정산 화면을 준비하고 있어요";
  document.querySelector("#prepare-help").textContent="서버가 시작되는 동안 잠시만 기다려 주세요. 준비되면 자동으로 열립니다.";
  for(let attempt=0;attempt<4;attempt++){
    try{
      const response=await fetch("/bl-cost/app"+location.search,{cache:"no-store",signal:AbortSignal.timeout(30000)});
      const html=await response.text();
      if(!response.ok||!html.includes('data-app-root="/bl-cost"')||!html.includes('id="lookup-form"'))throw Error("not-ready");
      document.open();document.write(html);document.close();return;
    }catch{if(attempt<3)await new Promise(resolve=>setTimeout(resolve,1500));}
  }
  document.querySelector("#prepare-title").textContent="아직 연결하지 못했어요";
  document.querySelector("#prepare-help").textContent="서버 시작이 지연되고 있습니다. 잠시 후 다시 연결해 주세요.";
  retry.hidden=false;
}
retry.onclick=prepare;
prepare();
