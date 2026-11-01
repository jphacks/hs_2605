// Optional real-browser regression check; no production/frontend build dependency.
// Run against generated HTML in file:// mode with all networking disabled.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {spawnSync}=require('node:child_process');
const args=process.argv.slice(2);
const option=(key,fallback)=>args.includes(key)?args[args.indexOf(key)+1]:fallback;
const {chromium}=require(option('--playwright','playwright').startsWith('.')?path.resolve(option('--playwright')):option('--playwright','playwright'));
const reports=path.resolve(option('--reports','analysis_runs/playback-demo'));
const reportFile=option('--file','explorer.html');
const ux=args.includes('--ux');
const shots=path.resolve(option('--screenshots','analysis_runs/playback-browser'));
if(fs.existsSync(shots))throw Error('Screenshot directory exists; choose a fresh --screenshots directory');
fs.mkdirSync(shots,{recursive:true});

async function waitUntil(page,predicate){
 await page.waitForFunction(predicate,null,{polling:'raf',timeout:5000});
}
async function motion(page,value){
 await page.emulateMedia({reducedMotion:value});
 // Chromium delivers matchMedia changes on its rendering cycle, not the
 // mocked playback clock. Let the native renderer run before polling: repeated
 // CDP/RAF polling can defer emulated media delivery in headless Chrome.
 await new Promise(resolve=>setTimeout(resolve,500));
 if(value==='reduce')await waitUntil(page,()=>reducedMotion.matches&&!player.snapshot().playing&&document.getElementById('play').disabled);
 else await waitUntil(page,()=>!reducedMotion.matches&&document.getElementById('play').disabled===(player.snapshot().playing||player.snapshot().selected===windows.length-1));
}

async function checkSelection(page,result,index){
 const w=result.windows[index],times=result.simulation.board_uptime_ms;
 const state=await page.evaluate(()=>player.snapshot());
 assert.equal(state.selected,index);
 assert.equal(await page.locator('#window-select').inputValue(),String(index));
 assert.equal(await page.locator('#timeline').inputValue(),String(index));
 assert.match(await page.locator('#window-label').textContent(),new RegExp(`区間 ${index+1} /`));
 assert.match(await page.locator('#playback-status').textContent(),new RegExp(`${state.playing?'再生中':'一時停止'} · 区間 ${index+1} /`));
 const expected={
  'truth-range':Math.hypot(w.truth_position_cm.x,w.truth_position_cm.y).toFixed(2)+' cm',
  'truth-angle':w.configured_angle_deg.toFixed(1)+'°',
  'estimate-range':w.estimation_available?Math.hypot(w.position_cm.x,w.position_cm.y).toFixed(2)+' cm':'—',
  'estimate-angle':w.estimation_available?w.estimated_angle_deg.toFixed(1)+'°':'—',
  'angle-difference':w.estimation_available?w.absolute_angular_error_deg.toFixed(2)+'°':'—',
  'position-difference':w.estimation_available?w.position_error_cm.toFixed(2)+' cm':'—',
  'range-difference':w.estimation_available?Math.abs(Math.hypot(w.position_cm.x,w.position_cm.y)-Math.hypot(w.truth_position_cm.x,w.truth_position_cm.y)).toFixed(2)+' cm':'—'
 };
 for(const [id,value] of Object.entries(expected))assert.equal(await page.locator('#'+id).textContent(),value,id);
 const period=`${(times[w.frame_row_start]/1000).toFixed(2)}–${(times[w.frame_row_end_exclusive-1]/1000).toFixed(2)}`;
 assert.ok((await page.locator('#time-readout').textContent()).includes(period));
 assert.ok((await page.locator('#window-info').textContent()).includes(period));
 assert.ok((await page.locator('#timeline').getAttribute('aria-valuetext')).includes(`区間 ${index+1} /`));
 const selected=await page.locator('.marker[aria-pressed="true"]').evaluateAll(els=>els.map(el=>({index:Number(el.dataset.window),kind:el.dataset.kind})));
 assert.equal(selected.length,w.estimation_available?2:1);
 assert.ok(selected.every(m=>m.index===index));
 assert.equal(selected.filter(m=>m.kind==='estimate').length,w.estimation_available?1:0);
 assert.equal(await page.locator('#reason').isVisible(),!w.estimation_available||w.truth_outside_front_hemisphere);
 assert.ok((await page.locator('#status').textContent()).includes(w.estimation_available?'推定あり':'推定できません'));
 if(ux){
  assert.match(await page.locator('#map-selection').textContent(),new RegExp(`区間 ${index+1} /`));
  assert.equal(await page.locator('.marker:visible').count(),w.estimation_available?2:1);
  assert.equal(await page.locator('.marker:not([aria-pressed="true"]):visible').count(),0);
  assert.equal(await page.locator('[role="status"]').count(),1);
 }
}

function luminance(rgb){
 const values=rgb.match(/[\d.]+/g).slice(0,3).map(Number).map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);
 return values.reduce((sum,x,i)=>sum+x*[.2126,.7152,.0722][i],0);
}
function contrast(a,b){const values=[luminance(a),luminance(b)].sort((x,y)=>x-y);return (values[1]+.05)/(values[0]+.05);}
async function checkUx(page,width){
 const measurements=await page.evaluate(()=>{
  const background=el=>{for(let p=el;p;p=p.parentElement){const color=getComputedStyle(p).backgroundColor;if(!['rgba(0, 0, 0, 0)','transparent'].includes(color))return color;}return 'rgb(255,255,255)';};
  const text=[];
  for(const el of document.querySelectorAll('body *')){
   if(!el.getClientRects().length||el.closest('button:disabled')||['SCRIPT','STYLE','OPTION'].includes(el.tagName))continue;
   if(![...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
   const style=getComputedStyle(el);
   if(style.visibility==='hidden')continue;
   text.push({tag:el.tagName,color:el instanceof SVGElement?style.fill:style.color,background:background(el),sample:el.textContent.slice(0,32)});
  }
  const root=getComputedStyle(document.documentElement),paper=getComputedStyle(document.getElementById('map-card')).backgroundColor;
  const probe=document.createElement('span');document.body.appendChild(probe);
  const indicators=['--guide','--control','--focus','--truth','--estimate'].map(name=>{probe.style.color=root.getPropertyValue(name);return {name,color:getComputedStyle(probe).color,background:paper};});probe.remove();
  const controls=['play','pause','previous','next','timeline','window-select'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,width:r.width,height:r.height};});
  const mapLabels=[...document.querySelectorAll('#map text')].map(el=>{const r=el.getBoundingClientRect();return {text:el.textContent,x:r.x,y:r.y,right:r.right,bottom:r.bottom};});
  return {text,indicators,controls,mapLabels};
 });
 for(const item of measurements.text)assert.ok(contrast(item.color,item.background)>=4.5,JSON.stringify(item));
 for(const item of measurements.indicators)assert.ok(contrast(item.color,item.background)>=3,JSON.stringify(item));
 for(const item of measurements.controls)assert.ok(item.width>=44&&item.height>=44,JSON.stringify(item));
 for(let i=0;i<measurements.mapLabels.length;i++)for(let j=i+1;j<measurements.mapLabels.length;j++){
  const a=measurements.mapLabels[i],b=measurements.mapLabels[j];
  assert.ok(a.right<=b.x||b.right<=a.x||a.bottom<=b.y||b.bottom<=a.y,JSON.stringify({overlappingLabels:[a,b]}));
 }
 for(const id of ['play','pause','previous','next']){
  assert.ok(await page.locator('#'+id).getAttribute('aria-label'));
  assert.equal(await page.locator('#'+id).getAttribute('aria-controls'),'map inspection');
 }
 if(width>=1024){const map=await page.locator('#map-card').boundingBox(),panel=await page.locator('#inspection').boundingBox();assert.ok(panel.x>=map.x+map.width);}
 return {textSamples:measurements.text.length,minTextContrast:Math.min(...measurements.text.map(x=>contrast(x.color,x.background))),minIndicatorContrast:Math.min(...measurements.indicators.map(x=>contrast(x.color,x.background))),minTouchTarget:Math.min(...measurements.controls.map(x=>Math.min(x.width,x.height)))};
}

(async()=>{
 const browser=await chromium.launch({headless:true,channel:option('--channel','chrome')});
 const checks=[];
 try{
  if(args.includes('--native-only')){
   const context=await browser.newContext({offline:true});const page=await context.newPage();
   await page.goto(pathToFileURL(path.join(reports,'sweep',reportFile)).href);
   await page.click('#start');await page.click('#play');
   await page.waitForFunction(()=>player.snapshot().selected>=1,null,{timeout:6000});
   await page.click('#pause');assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   await page.selectOption('#window-select','0');await motion(page,'reduce');
   await page.click('#next');assert.equal(await page.locator('#timeline').inputValue(),'1');
   await motion(page,'no-preference');await page.click('#play');await motion(page,'reduce');
   assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   await motion(page,'no-preference');assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   await context.close();
   const result={realTimerSmoke:true,nativeReducedMotion:true,allPassed:true};
   fs.writeFileSync(path.join(shots,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));return;
  }
  for(const scenario of ['sweep','stationary','noisy','ambiguous','discontinuous'])for(const width of (ux?[320,390,768,1280,1440]:[1280,390])){
   const context=await browser.newContext({viewport:{width,height:900},offline:true,hasTouch:ux&&width<=390});
   const page=await context.newPage(),errors=[],requests=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
   await page.clock.install({time:new Date('2026-11-01T00:00:00Z')});
   await page.clock.pauseAt(new Date('2026-11-01T00:00:01Z'));
   await page.goto(pathToFileURL(path.join(reports,scenario,reportFile)).href);
   const result=JSON.parse(await page.locator('#explorer-data').textContent());
   await page.clock.runFor(5000);
   assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   assert.equal(await page.locator('#workspace').isVisible(),false);
   await page.getByRole('button',{name:'シミュレーションを開始',exact:true}).click();
   if(ux){
    assert.equal(await page.evaluate(()=>document.activeElement.id),'map-title');
    await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'window-select');
    const map=await page.locator('#map').boundingBox(),actions=await page.locator('.playback-actions').boundingBox();
    if(width<=390){assert.ok(map.y>=0&&actions.y+actions.height<=900,JSON.stringify({width,map,actions}));}
    await page.screenshot({path:path.join(shots,`start-${scenario}-${width}.png`)});
    if(width<=390){
     await page.tap('#play');const top=await page.evaluate(()=>scrollY);await page.clock.runFor(1000);
     assert.equal(await page.evaluate(()=>scrollY),top);await page.tap('#pause');await checkSelection(page,result,1);
     const target=await page.locator('.marker[aria-pressed="true"]').first().locator('.hit-target').evaluate(el=>{
      const r=el.getBoundingClientRect();return {x:r.x+r.width/2+18,y:r.y+r.height/2,width:r.width,height:r.height};
     });
     assert.ok(target.width>=43.99&&target.height>=43.99,JSON.stringify(target));
     const hit=await page.evaluate(p=>{const el=document.elementFromPoint(p.x,p.y);return {selected:!!el?.closest('.marker[aria-pressed="true"]'),tag:el?.tagName,kind:el?.getAttribute('class')};},target);
     assert.ok(hit.selected,JSON.stringify({target,hit,width}));
     await page.touchscreen.tap(target.x,target.y);await checkSelection(page,result,1);
     await page.selectOption('#window-select','0');
    }
   }
   await page.clock.runFor(3000);
   await checkSelection(page,result,0);
   assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   assert.equal(await page.locator('polyline').count(),0);
   assert.equal(await page.locator('#timeline').getAttribute('step'),'1');
   assert.ok((await page.locator('.playback').textContent()).includes('実測時間の倍率ではありません'));
   // Native keyboard controls, slider and every option use the same state.
   await page.locator('#next').focus();await page.keyboard.press('Enter');await checkSelection(page,result,1);
   await page.locator('#previous').focus();await page.keyboard.press('Space');await checkSelection(page,result,0);
   await page.locator('#timeline').focus();await page.keyboard.press('ArrowRight');await checkSelection(page,result,1);
   for(let i=0;i<result.windows.length;i++){await page.selectOption('#window-select',String(i));await checkSelection(page,result,i);}
   await page.selectOption('#window-select','0');
   await page.locator('[data-kind="truth"][data-window="0"]').focus();
   await page.keyboard.press('Enter');await checkSelection(page,result,0);
   // All automatic changes update map, controls, time, errors and explanations.
   await page.locator('#play').focus();await page.keyboard.press('Enter');
   assert.ok(await page.evaluate(()=>player.snapshot().playing));
   await page.clock.runFor(1000);await checkSelection(page,result,1);
   await page.locator('#pause').focus();await page.keyboard.press('Space');
   await page.clock.runFor(5000);await checkSelection(page,result,1);
   for(let i=0;i<20;i++)await page.evaluate(()=>{document.getElementById('play').click();document.getElementById('pause').click();});
   await page.clock.runFor(5000);await checkSelection(page,result,1);
   await page.click('#play');await page.clock.runFor(1000);await checkSelection(page,result,2);
   if(scenario==='discontinuous'){
    assert.equal(await page.evaluate(()=>player.snapshot().stopReason),'boundary');
    await page.clock.runFor(10000);await checkSelection(page,result,2);
    await page.click('#next');await checkSelection(page,result,3);
    await page.click('#play');await page.clock.runFor(1000);await checkSelection(page,result,4);
    assert.equal(await page.evaluate(()=>player.snapshot().stopReason),'boundary');
    await page.click('#next');await checkSelection(page,result,5); // Explicit restart crossing.
   }
   // Hidden/pagehide events clear the chain; returning never resumes it.
   await page.selectOption('#window-select','0');await page.click('#play');
   await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
   await page.clock.runFor(5000);await checkSelection(page,result,0);
   assert.equal(await page.evaluate(()=>player.snapshot().stopReason),'hidden');
   await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
   await page.clock.runFor(3000);assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   await page.click('#play');await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   await page.clock.runFor(5000);await checkSelection(page,result,0);
   await page.selectOption('#window-select',String(result.windows.length-2));
   await page.click('#play');await page.clock.runFor(1000);await checkSelection(page,result,result.windows.length-1);
   assert.equal(await page.evaluate(()=>player.snapshot().stopReason),'end');
   await page.clock.runFor(5000);assert.equal(await page.evaluate(()=>player.snapshot().playing),false);
   if(scenario==='ambiguous')assert.equal(await page.locator('#angle-difference').textContent(),'120.00°');
   if(scenario==='noisy')assert.equal(await page.locator('[data-kind="estimate"]').count(),0);
   await page.selectOption('#window-select',scenario==='discontinuous'?'2':'1');
   await page.evaluate(()=>scrollTo(0,0));
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   for(const id of ['play','pause','previous','next','timeline']){
    const rect=await page.locator('#'+id).boundingBox();assert.ok(rect.x>=0&&rect.x+rect.width<=width);
   }
   await page.locator('#next').focus();await page.keyboard.press('Tab');
   assert.equal(await page.evaluate(()=>document.activeElement.id),'timeline');
   assert.ok(await page.locator('#timeline').evaluate(el=>el.matches(':focus-visible')));
   // Full-page captures should not paint the sticky badge at a scrolled offset.
   await page.evaluate(()=>scrollTo(0,0));
   await page.screenshot({path:path.join(shots,`${scenario}-${width}.png`),fullPage:true});
   await page.locator('#inspection').scrollIntoViewIfNeeded();
   const badge=await page.locator('.safety').boundingBox();assert.ok(badge.y>=0&&badge.y<2);
   assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
   const accessibility=ux?await checkUx(page,width):null;
   checks.push({scenario,width,windowsChecked:result.windows.length,offline:true,errors,requests,accessibility});
   await context.close();
  }
  // Isolate native rendering/media delivery from virtual-clock instrumentation.
  const version=await browser.version();await browser.close();
  const native=spawnSync(process.execPath,[__filename,'--native-only','--playwright',option('--playwright','playwright'),
   '--reports',reports,'--file',reportFile,'--screenshots',path.join(shots,'native'),'--channel',option('--channel','chrome')],{encoding:'utf8'});
  assert.equal(native.status,0,native.stderr||native.stdout);
  const nativeResult=JSON.parse(native.stdout);
  const result={browser:version,checks,...nativeResult,allPassed:true,
   lifecycleNote:'Visibility handler tested with document.hidden override plus visibilitychange; pagehide dispatched. Reduced motion uses browser media emulation.'};
  fs.writeFileSync(path.join(shots,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser.isConnected())await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
