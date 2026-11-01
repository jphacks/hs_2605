// Paired 3D/2D browser QA against already exported, validated cases; no generation.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const args=process.argv.slice(2),option=(n,d)=>args.includes(n)?args[args.indexOf(n)+1]:d;
const modulePath=option('--playwright','playwright');
const {chromium}=require(modulePath.startsWith('.')?path.resolve(modulePath):modulePath);
const reports=path.resolve(option('--reports','analysis_runs/scene-evaluation-final'));
const shots=path.resolve(option('--screenshots','analysis_runs/connected-browser'));
if(fs.existsSync(shots))throw Error('Choose a fresh screenshot directory');
fs.mkdirSync(shots,{recursive:true});
const names=['sweep','stationary','noisy','ambiguous','discontinuous'];
const ids=['truth-range','truth-angle','estimate-range','estimate-angle','range-difference','angle-difference',
  'position-difference','time-readout','window-info','window-label','status','map-selection'];
const url=(scenario,is2d=false)=>pathToFileURL(path.join(reports,scenario+(is2d?'-2d':'')+'.html')).href;
const state=p=>p.locator('.analysis-card').evaluate(el=>({selected:Number(el.dataset.selected),playing:el.dataset.playing==='true',stopReason:el.dataset.stopReason}));
async function values(p){
  return p.evaluate(ids=>({text:Object.fromEntries(ids.map(id=>[id,document.getElementById(id).textContent])),
    diagnostics:JSON.parse(document.getElementById('diagnostics').textContent),provenance:JSON.parse(document.getElementById('provenance').textContent),
    time:document.getElementById('timeline').getAttribute('aria-valuetext'),reason:document.getElementById('reason').textContent,
    reasonHidden:document.getElementById('reason').hidden}),ids);
}
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);
async function shot(p,name){
  // Use a native rendering clock for visual evidence. Full-page capture can
  // invalidate the software-WebGL compositor while the test clock freezes RAF.
  // Load the same artifact and selection, rather than photographing that blank buffer.
  const context=await p.context().browser().newContext({offline:true,viewport:p.viewportSize()});
  if(await p.locator('#scene-stage').getAttribute('data-state')==='fallback')await context.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...rest){return /webgl/i.test(type)?null:original.call(this,type,...rest);};
  });
  const visible=await context.newPage();
  await visible.goto(p.url());
  if(await p.locator('#analysis-controls').isVisible()){
    await visible.click('#analysis-start');
    await visible.selectOption('#window-select',String((await state(p)).selected));
  }
  if(name==='rear-ambiguity'){
    await visible.locator('#analysis-details summary').click();
    assert.equal(await visible.locator('#angle-difference').isVisible(),true);
    assert.equal(await visible.locator('#angle-difference').textContent(),'120.00°');
  }
  await visible.evaluate(()=>scrollTo(0,0));await visible.waitForTimeout(150);
  await visible.screenshot({path:path.join(shots,name+'.png'),fullPage:true});
  await context.close();
}
async function selection(p,reference,index){
  await p.clock.runFor(20); // Flush the one requested WebGL draw, not a second player.
  const w=reference.data.windows[index];
  const current=await state(p);
  assert.equal(current.selected,index);
  assert.equal(await p.locator('#window-select').inputValue(),String(index));
  assert.equal(await p.locator('#timeline').inputValue(),String(index));
  assert.deepEqual(await values(p),reference.values[index],'Actual 3D/2D displayed values differ');
  assert.ok((await p.locator('#playback-status').textContent()).startsWith(`${current.playing?'再生中':'一時停止'} · 区間 ${index+1} / ${reference.data.windows.length} · ${w.estimation_available?'推定あり':'推定できません'}`));
  const reduced=await p.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches);
  assert.equal(await p.locator('#play').isDisabled(),current.playing||index===reference.data.windows.length-1||reduced);
  assert.equal(await p.locator('#pause').isDisabled(),!current.playing);
  assert.equal(await p.locator('#previous').isDisabled(),index===0);
  assert.equal(await p.locator('#next').isDisabled(),index===reference.data.windows.length-1);
  const groups=await p.locator('#analysis-markers g').evaluateAll(els=>els.map(el=>({kind:el.dataset.kind,hidden:el.hasAttribute('hidden'),transform:el.getAttribute('transform'),index:el.dataset.window})));
  for(const g of groups){
    if(g.kind==='estimate'&&!w.estimation_available){assert.equal(g.hidden,true);assert.equal(g.transform,null);continue;}
    assert.equal(g.hidden,false);assert.equal(g.index,String(index));
    const actual=g.transform.match(/translate\(([-\d.e+]+) ([-\d.e+]+)\)/).slice(1).map(Number);
    const expected=reference.markers[index][g.kind];near(actual[0],expected[0]);near(actual[1],expected[1]);
  }
  if(await p.locator('#scene-stage').getAttribute('data-state')==='webgl'){
    const plane=await p.locator('#scene-canvas').evaluate(el=>el.ibukiAnalysisPlane());
    assert.equal(plane.rootName,'synthetic-analysis-plane');assert.equal(plane.planeDepth,0);
    for(const [kind,marker] of [['truth',plane.reference],['estimate',plane.estimate]]){
      if(kind==='estimate'&&!w.estimation_available){assert.equal(marker,null);continue;}
      assert.ok(marker);assert.equal(marker.layer,2);assert.equal(marker.position[2],0);
      assert.deepEqual(marker.world,marker.position);
      const expected=reference.markers[index][kind];
      near(marker.position[0]/plane.plotRadius,(expected[0]-300)/220);
      near((marker.position[1]-plane.originY)/plane.plotRadius,(285-expected[1])/220);
      assert.ok(Math.abs(marker.ndc[0])<1&&Math.abs(marker.ndc[1])<1,'3D marker clipped');
      assert.equal(marker.geometry,kind==='truth'?'RingGeometry':'PlaneGeometry');
      assert.equal(marker.color,kind==='truth'?'287269':'a45424');
      if(kind==='estimate')near(marker.rotation,Math.PI/4);
    }
  }
}
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome',args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const checks=[],reference={};
  try{
    // Read the actual unchanged 2D implementation, including every window/marker.
    for(const scenario of names){
      const context=await browser.newContext({offline:true}),p=await context.newPage();
      await p.goto(url(scenario,true));await p.click('#start');
      const data=JSON.parse(await p.locator('#explorer-data').textContent());
      const displayed=[],markers=[];
      for(let index=0;index<data.windows.length;index++){
        await p.selectOption('#window-select',String(index));displayed.push(await values(p));
        markers.push(await p.locator('.marker[aria-pressed="true"]').evaluateAll(els=>Object.fromEntries(els.map(el=>{
          if(el.dataset.kind==='truth'){const c=el.querySelector('circle');return ['truth',[Number(c.getAttribute('cx')),Number(c.getAttribute('cy'))]];}
          const xy=el.querySelector('path').getAttribute('d').match(/^M([-\d.e+]+) ([-\d.e+]+)/).slice(1).map(Number);
          return ['estimate',[xy[0],xy[1]+14]];
        }))));
      }
      reference[scenario]={data,values:displayed,markers};await context.close();
    }
    for(const scenario of names)for(const width of [320,390,768,1280,1440]){
      const context=await browser.newContext({offline:true,hasTouch:width<=390,viewport:{width,height:900}}),p=await context.newPage();
      const errors=[],requests=[];p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
      await p.clock.install({time:new Date('2026-11-01T00:00:00Z')});await p.clock.pauseAt(new Date('2026-11-01T00:00:01Z'));
      await p.goto(url(scenario));await p.clock.runFor(50);
      assert.equal(await p.locator('#scene-stage').getAttribute('data-state'),'webgl');
      assert.deepEqual(JSON.parse(await p.locator('#scene-data').textContent()),reference[scenario].data);
      assert.equal(await p.locator('#analysis-controls').isVisible(),false);
      await p.clock.runFor(5000);assert.equal((await state(p)).selected,0);assert.equal((await state(p)).playing,false);
      if(scenario==='sweep'&&width===1280)await shot(p,'default-plane');
      await p.click('#analysis-start');assert.equal(await p.evaluate(()=>document.activeElement.id),'window-select');
      const ref=reference[scenario];await selection(p,ref,0);
      if(width<=390){
        const stage=await p.locator('#scene-stage').boundingBox(),controls=await p.locator('.playback-actions').boundingBox();
        assert.ok(stage.y>=0&&stage.y<12&&controls.y+controls.height<=900,JSON.stringify({stage,controls}));
      }
      const idle=await p.locator('#scene-stage').getAttribute('data-render-count');
      await p.clock.runFor(3000);assert.equal(await p.locator('#scene-stage').getAttribute('data-render-count'),idle);
      if(scenario==='sweep'&&width===1280)await shot(p,'selected-window');
      await p.locator('#next').focus();await p.keyboard.press('Enter');await selection(p,ref,1);
      await p.locator('#previous').focus();await p.keyboard.press('Space');await selection(p,ref,0);
      await p.locator('#timeline').focus();await p.keyboard.press('ArrowRight');await selection(p,ref,1);
      assert.equal(await p.locator('#timeline').evaluate(el=>el.matches(':focus-visible')),true);
      if(width<=390){await p.tap('#next');await selection(p,ref,2);}
      for(let index=0;index<ref.data.windows.length;index++){
        await p.selectOption('#window-select',String(index));await selection(p,ref,index);
        assert.equal((await state(p)).playing,false);
      }
      await p.selectOption('#window-select','0');await p.click('#play');
      const scroll=await p.evaluate(()=>scrollY);await p.clock.runFor(1000);await selection(p,ref,1);
      assert.equal(await p.evaluate(()=>scrollY),scroll);
      await p.click('#pause');await p.clock.runFor(5000);await selection(p,ref,1);
      // Camera reset and direct camera keyboard changes never select another window.
      await p.click('[data-camera="right"]');await p.clock.runFor(50);
      await selection(p,ref,1); // Fixed analysis plane is independent of crib orbit.
      await p.click('[data-camera="reset"]');await p.clock.runFor(50);await selection(p,ref,1);
      if(scenario==='sweep'&&[320,390,1280].includes(width)){
        const box=await p.locator('#scene-canvas').boundingBox();
        const x=box.x+box.width*(width<=390?.5:.29),y=box.y+box.height*(width<=390?.2:.5);
        const camera=()=>p.locator('#scene-stage').getAttribute('data-camera').then(JSON.parse);
        const initial=await camera();
        await p.mouse.move(x,y);await p.mouse.down();await p.mouse.move(x+35,y+10,{steps:4});await p.mouse.up();await p.clock.runFor(50);
        assert.ok(Math.abs((await camera()).azimuth-initial.azimuth)>.01);await selection(p,ref,1);
        await p.mouse.wheel(0,150);await p.clock.runFor(50);assert.ok((await camera()).distance>initial.distance);
        await p.click('[data-camera="reset"]');await p.clock.runFor(50);
        if(width<=390){
          const cdp=await context.newCDPSession(p);
          const touch=(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([id,tx,ty])=>({id,x:tx,y:ty}))});
          await touch('touchStart',[[0,x,y]]);await touch('touchMove',[[0,x+35,y+10]]);await touch('touchEnd',[]);await p.clock.runFor(50);
          assert.ok(Math.abs((await camera()).azimuth-initial.azimuth)>.01);await selection(p,ref,1);
          await p.click('[data-camera="reset"]');await p.clock.runFor(50);
          await touch('touchStart',[[0,x-20,y],[1,x+20,y]]);await touch('touchMove',[[0,x-35,y],[1,x+35,y]]);await touch('touchEnd',[]);await p.clock.runFor(50);
          assert.ok((await camera()).distance<initial.distance);
        }
        await p.click('[data-camera="reset"]');await p.clock.runFor(50);near((await camera()).azimuth,initial.azimuth);
        await selection(p,ref,1);
      }
      for(let i=0;i<20;i++)await p.evaluate(()=>{document.getElementById('play').click();document.getElementById('pause').click();});
      await p.clock.runFor(5000);await selection(p,ref,1);
      await p.click('#play');await p.clock.runFor(1000);await selection(p,ref,2);
      if(scenario==='discontinuous'){
        assert.equal((await state(p)).stopReason,'boundary');await p.clock.runFor(5000);await selection(p,ref,2);
        await p.click('#next');await selection(p,ref,3);await p.click('#play');await p.clock.runFor(1000);await selection(p,ref,4);
        assert.equal((await state(p)).stopReason,'boundary');await p.click('#next');await selection(p,ref,5);
      }
      await p.selectOption('#window-select','0');await p.click('#play');
      await p.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
      await p.clock.runFor(5000);await selection(p,ref,0);assert.equal((await state(p)).stopReason,'hidden');
      await p.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
      await p.clock.runFor(3000);assert.equal((await state(p)).playing,false);
      await p.selectOption('#window-select',String(ref.data.windows.length-2));await p.click('#play');await p.clock.runFor(1000);
      await selection(p,ref,ref.data.windows.length-1);assert.equal((await state(p)).stopReason,'end');
      await p.clock.runFor(5000);assert.equal((await state(p)).playing,false);
      await p.selectOption('#window-select','1');
      if(width===1280&&scenario==='sweep')await shot(p,'later-window');
      if(width===390&&scenario==='sweep')await shot(p,'mobile');
      if(width===1280&&scenario==='noisy'){assert.equal(await p.locator('[data-kind="estimate"]').isVisible(),false);await shot(p,'unavailable');}
      if(width===1280&&scenario==='ambiguous'){assert.equal(await p.locator('#angle-difference').textContent(),'120.00°');await shot(p,'rear-ambiguity');}
      assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      for(const id of ['window-select','play','pause','previous','next','timeline']){
        const r=await p.locator('#'+id).boundingBox();assert.ok(r.x>=0&&r.x+r.width<=width&&r.height>=44,id);
      }
      if(width===1280&&scenario==='sweep'){
        await p.evaluate(()=>document.getElementById('scene-canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext());
        await p.clock.runFor(50);await p.waitForTimeout(150);
        assert.equal(await p.locator('#scene-stage').getAttribute('data-state'),'fallback');
        await selection(p,ref,1);
      }
      await p.click('#play');await p.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
      await p.clock.runFor(5000);assert.equal((await state(p)).playing,false);assert.equal((await state(p)).stopReason,'hidden');
      assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
      checks.push({scenario,width,windowsChecked:ref.data.windows.length,displayAndMarkerParity:true,offline:true,errors,requests});
      await context.close();
    }
    // Analysis does not depend on WebGL; no-JS viewers retain all static rows.
    for(const scenario of names)for(const javaScriptEnabled of [true,false]){
      const context=await browser.newContext({offline:true,javaScriptEnabled,viewport:{width:390,height:900}});
      if(javaScriptEnabled)await context.addInitScript(()=>{
        const original=HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext=function(type,...rest){return /webgl/i.test(type)?null:original.call(this,type,...rest);};
      });
      const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto(url(scenario));
      if(javaScriptEnabled){
        assert.equal(await p.locator('#scene-svg').isVisible(),true);
        assert.equal(await p.locator('#analysis-map').isVisible(),true);
        await p.click('#analysis-start');
        for(let index=0;index<reference[scenario].data.windows.length;index++){
          await p.selectOption('#window-select',String(index));await selection(p,reference[scenario],index);
        }
        if(scenario==='sweep'){await p.selectOption('#window-select','1');await shot(p,'webgl-fallback');}
      }else{
        assert.equal(await p.locator('#scene-fallback').isVisible(),true);
        assert.equal(await p.locator('.static-windows tbody tr').count(),reference[scenario].data.windows.length);
        assert.equal(await p.locator('.static-windows').isVisible(),true);
        assert.equal(await p.locator('#analysis-start').isVisible(),false);
        assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      }
      assert.deepEqual(errors,[]);await context.close();
    }
    // Native timer and native matchMedia delivery, independent of mocked clocks.
    const context=await browser.newContext({offline:true}),p=await context.newPage();
    await p.goto(url('sweep'));await p.click('#analysis-start');await p.click('#play');
    await p.waitForFunction(()=>Number(document.querySelector('.analysis-card').dataset.selected)>=1);
    await p.emulateMedia({reducedMotion:'reduce'});await p.waitForTimeout(500);
    assert.equal((await state(p)).playing,false);assert.equal(await p.locator('#play').isDisabled(),true);
    await p.click('#next');assert.equal((await state(p)).playing,false);
    await p.emulateMedia({reducedMotion:'no-preference'});await p.waitForTimeout(500);
    assert.equal((await state(p)).playing,false);assert.equal(await p.locator('#play').isEnabled(),true);
    await context.close();
    const result={browser:browser.version(),renderer:'SwiftShader software WebGL; not hardware validation',checks,webglFallback:true,noJavaScript:true,realTimerSmoke:true,nativeReducedMotion:true,
      lifecycleNote:'document.hidden override with visibilitychange; persisted pagehide dispatched; no automatic restart',allPassed:true};
    fs.writeFileSync(path.join(shots,'checks.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
