import {createShowcase} from './showcase_state.js';
const steps=[
  {title:'ベッドのそばから、見守る。',message:'ベッドのそばに設置。赤ちゃんに触れず、カメラも使いません。',icon:'○',result:'カメラを使わず、触れずに。',detail:'センサーはベッドのそばに。レーダーの位置を、模型で確認できます。',note:'形・設置位置・検知方向は説明用です。実機の構成や測定範囲ではありません。'},
  {title:'小さな動きを、触れずに。',message:'レーダーで小さな動きを捉えるしくみを、イメージで紹介します。',icon:'≈',result:'波は、しくみを伝えるイメージ。',detail:'体に何も付けずに見守る構想です。呼吸の数値や赤ちゃんの動きを測定する体験ではありません。',note:'波は説明用のアニメーションです。実際のレーダー波・測定範囲ではありません。'},
  {title:'気づきを、保護者へ。',message:'変化に気づいたら、保護者に知らせることを目指します。',icon:'✉',result:'保護者へのお知らせ（通知の例）',detail:'シミュレーション：変化を想定した説明です。「様子を確認してください」という通知をイメージしています。送信はしません。',note:'シミュレーションの通知です。実際の乳児の検出や通知性能を示すものではありません。'},
];
export function initializeShowcase(visual){
  const root=document.getElementById('showcase');if(!root)return;
  const byId=id=>document.getElementById(id),reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const motion=byId('show-motion');
  function syncMotion(){
    motion.hidden=controller.snapshot().step!==1||!controller.snapshot().started;
    motion.disabled=reduced.matches||!visual.available();
    motion.textContent=visual.isAnimating()?'動きを止める':'動きを見る';
    motion.setAttribute('aria-pressed',String(visual.isAnimating()));
    motion.setAttribute('aria-describedby','show-visual-note');
    byId('show-visual-note').textContent=steps[controller.snapshot().step].note+(reduced.matches&&controller.snapshot().step===1?' 動きを抑える設定では静止図で表示します。':'');
  }
  function render(state){
    const content=steps[state.step];root.dataset.step=state.step;root.dataset.started=state.started;
    [...root.querySelectorAll('.steps li')].forEach((el,index)=>{if(index===state.step)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});
    byId('story-title').textContent=content.title;byId('story-message').textContent=content.message;
    byId('result-icon').textContent=content.icon;byId('result-title').textContent=content.result;byId('result-message').textContent=content.detail;
    byId('show-visual-note').textContent=content.note;
    byId('story-result').dataset.notification=state.step===2?'simulated':'none';
    byId('show-start').hidden=state.started;byId('show-next').hidden=!state.started;
    byId('show-previous').disabled=!state.started||state.step===0;byId('show-next').disabled=state.step===2;byId('show-restart').disabled=!state.started;
    byId('story-status').textContent=state.started?`STEP ${state.step+1} / 3 · ${['設置する','見守る','知らせる'][state.step]}。${state.step===2?'説明はここまでです。「最初から」で見直せます。':'次のステップは手動で選びます。'}`:'開始を押して、3つのステップを自分のペースで確認できます。';
    visual.select(state.step,state.started&&!reduced.matches);syncMotion();
  }
  const controller=createShowcase(render);
  byId('show-start').addEventListener('click',()=>{controller.start();byId('show-next').focus({preventScroll:true});});
  byId('show-next').addEventListener('click',()=>controller.next());
  byId('show-previous').addEventListener('click',()=>controller.previous());
  byId('show-restart').addEventListener('click',()=>controller.restart());
  motion.addEventListener('click',()=>{if(reduced.matches||!visual.available())return;if(visual.isAnimating())visual.pause();else visual.resume();syncMotion();});
  function pause(){visual.pause();syncMotion();}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});window.addEventListener('pagehide',pause);
  function syncRenderer(){
    const fallback=byId('scene-stage').dataset.state==='fallback';
    byId('scene-status').hidden=!fallback;
    if(fallback){byId('scene-status').textContent='この環境では平面図で紹介します。ステップの操作はそのまま使えます。';pause();}
  }
  byId('scene-stage').addEventListener('scene-fallback',syncRenderer);
  reduced.addEventListener('change',()=>{if(reduced.matches)pause();else syncMotion();});
  visual.onEnd(syncMotion);render(controller.snapshot());syncRenderer();
}
