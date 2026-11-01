// Real-browser offline showcase checks; Playwright remains isolated QA tooling.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const args=process.argv.slice(2),option=(n,d)=>args.includes(n)?args[args.indexOf(n)+1]:d;
const modulePath=option('--playwright','playwright');
const {chromium}=require(modulePath.startsWith('.')?path.resolve(modulePath):modulePath);
const html=path.resolve(option('--html','analysis_runs/ibuki_showcase.html'));
const shots=path.resolve(option('--screenshots','analysis_runs/showcase-browser'));
if(fs.existsSync(shots))throw Error('Choose a fresh screenshot directory');
fs.mkdirSync(shots,{recursive:true});
const url=pathToFileURL(html).href;
const state=p=>p.locator('#showcase').evaluate(el=>({step:Number(el.dataset.step),started:el.dataset.started==='true'}));
const visual=p=>p.locator('#scene-canvas').evaluate(el=>el.ibukiShowcaseVisuals?.());
const messages=['ベッドのそばに設置。赤ちゃんに触れず、カメラも使いません。','レーダーで小さな動きを捉えるしくみを、イメージで紹介します。','変化に気づいたら、保護者に知らせることを目指します。'];
async function idle(p){await p.waitForTimeout(150);const n=await p.locator('#scene-stage').getAttribute('data-render-count');await p.waitForTimeout(450);assert.equal(await p.locator('#scene-stage').getAttribute('data-render-count'),n,'Idle render loop');}
async function checkStep(p,index,width){
  assert.equal((await state(p)).step,index);
  assert.equal(await p.locator('#story-message').textContent(),messages[index]);
  assert.equal(await p.locator('.steps [aria-current="step"]').count(),1);
  assert.ok((await p.locator('.steps [aria-current="step"]').textContent()).includes(`STEP ${index+1}`));
  assert.equal(await p.locator('#story-result').getAttribute('data-notification'),index===2?'simulated':'none');
  if(index===2)assert.ok((await p.locator('#story-status').textContent()).includes('説明はここまで'));
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  for(const selector of ['#story-title','#scene-limitations','.navigation']){
    const box=await p.locator(selector).boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width,selector);
  }
  if(width<=390){
    const scene=await p.locator('#scene-stage').boundingBox(),nav=await p.locator('.navigation').boundingBox();
    assert.ok(scene.y>=0&&nav.y+nav.height<=900,JSON.stringify({width,scene,nav}));
  }
  for(const button of await p.locator('button:visible').all())assert.ok((await button.boundingBox()).height>=44);
}
async function capture(browser,width,step,name,fallback=false){
  const context=await browser.newContext({offline:true,viewport:{width,height:900},reducedMotion:step===1&&!fallback?'no-preference':'reduce'});
  if(fallback)await context.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...rest){return /webgl/i.test(type)?null:original.call(this,type,...rest);};
  });
  const p=await context.newPage();await p.goto(url);await p.click('#show-start');
  for(let i=0;i<step;i++)await p.click('#show-next');
  await p.waitForTimeout(150);await p.screenshot({path:path.join(shots,name+'.png'),fullPage:true});await context.close();
}
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const checks=[];
  try{
    for(const width of [320,390,768,1280,1440]){
      const context=await browser.newContext({offline:true,hasTouch:width<=390,viewport:{width,height:900}}),p=await context.newPage(),errors=[],requests=[];
      p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
      await p.goto(url);await p.waitForFunction(()=>document.getElementById('scene-stage').dataset.camera);
      assert.deepEqual(await state(p),{step:0,started:false});assert.equal((await visual(p)).active,false);
      assert.equal(await p.locator('#radar-label').isVisible(),true);assert.equal(await p.locator('#scene-data').count(),0);assert.equal(await p.locator('#analysis-map').count(),0);
      await idle(p);await checkStep(p,0,width);
      await p.locator('#show-start').focus();await p.keyboard.press('Enter');
      assert.equal(await p.evaluate(()=>document.activeElement.id),'show-next');assert.ok(await p.locator('#show-next').evaluate(el=>el.matches(':focus-visible')));
      const top=await p.evaluate(()=>scrollY);await p.keyboard.press('Space');await checkStep(p,1,width);assert.equal(await p.evaluate(()=>scrollY),top);
      await p.waitForTimeout(150);assert.equal((await visual(p)).active,true);assert.equal((await visual(p)).wavesVisible,3);
      await p.click('#show-motion');assert.equal((await visual(p)).active,false);await idle(p);
      await p.click('#show-motion');await p.waitForTimeout(100);assert.equal((await visual(p)).active,true);
      await p.emulateMedia({reducedMotion:'reduce'});await p.waitForTimeout(500);
      assert.equal((await visual(p)).active,false);assert.equal(await p.locator('#show-motion').isDisabled(),true);
      assert.ok((await p.locator('#show-visual-note').textContent()).includes('静止図'));await idle(p);
      await p.emulateMedia({reducedMotion:'no-preference'});await p.waitForTimeout(200);assert.equal((await visual(p)).active,false);
      if(width<=390)await p.tap('#show-next');else await p.click('#show-next');
      await checkStep(p,2,width);assert.equal(await p.evaluate(()=>scrollY),top);
      assert.equal((await visual(p)).wavesVisible,0);assert.equal(await p.locator('#show-next').isDisabled(),true);
      assert.ok((await p.locator('#result-message').textContent()).includes('送信はしません'));
      // Camera gestures do not select story steps.
      const canvas=p.locator('#scene-canvas');await canvas.focus();await p.keyboard.press('ArrowRight');await p.keyboard.press('+');await p.waitForTimeout(100);
      assert.equal((await state(p)).step,2);
      await p.click('[data-camera="reset"]');assert.equal((await state(p)).step,2);
      const box=await canvas.boundingBox(),x=box.x+box.width/2,y=box.y+box.height/2;
      const before=JSON.parse(await p.locator('#scene-stage').getAttribute('data-camera'));
      await p.mouse.move(x,y);await p.mouse.down();await p.mouse.move(x+40,y+10,{steps:4});await p.mouse.up();await p.waitForTimeout(120);
      const moved=JSON.parse(await p.locator('#scene-stage').getAttribute('data-camera'));assert.ok(Math.abs(moved.azimuth-before.azimuth)>.01);assert.equal((await state(p)).step,2);
      await p.mouse.wheel(0,100);await p.waitForTimeout(100);assert.equal((await state(p)).step,2);
      if(width<=390){
        const cdp=await context.newCDPSession(p),touch=(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([id,tx,ty])=>({id,x:tx,y:ty}))});
        await touch('touchStart',[[0,x,y]]);await touch('touchMove',[[0,x+30,y+10]]);await touch('touchEnd',[]);await p.waitForTimeout(100);assert.equal((await state(p)).step,2);
      }
      await p.click('#show-previous');await checkStep(p,1,width);await p.click('#show-motion');await idle(p);
      await p.click('#show-motion');
      await p.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
      assert.equal((await visual(p)).active,false);
      await p.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});await idle(p);assert.equal((await visual(p)).active,false);
      await p.click('#show-motion');await p.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));await idle(p);assert.equal((await visual(p)).active,false);
      await p.click('#show-restart');await checkStep(p,0,width);assert.equal((await visual(p)).wavesVisible,0);
      assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await context.close();
      checks.push({width,threeSteps:true,manualOnly:true,keyboard:true,touch:width<=390,reducedMotion:true,idle:true,errors,requests});
    }
    // Shorter phone viewport: keep story navigation next to the active model.
    for(const width of [320,390]){
      const context=await browser.newContext({offline:true,viewport:{width,height:740},reducedMotion:'reduce'}),p=await context.newPage();
      await p.goto(url);await p.click('#show-start');
      for(let step=0;step<3;step++){
        const box=await p.locator('.navigation').boundingBox();assert.ok(box.y+box.height<=740,JSON.stringify({width,step,box}));
        assert.equal(await p.evaluate(()=>scrollY),0);if(step<2)await p.click('#show-next');
      }
      await context.close();
    }
    // A native finite animation completes, then stays idle without advancing steps.
    const native=await browser.newContext({offline:true}),p=await native.newPage();await p.goto(url);await p.click('#show-start');await p.click('#show-next');
    await p.waitForTimeout(4200);assert.equal((await visual(p)).active,false);assert.equal((await state(p)).step,1);await idle(p);
    await p.click('#show-motion');await p.waitForTimeout(100);assert.equal((await visual(p)).active,true);
    await p.evaluate(()=>document.getElementById('scene-canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext());
    await p.waitForFunction(()=>document.getElementById('scene-stage').dataset.state==='fallback');
    assert.equal((await visual(p)).active,false);assert.equal(await p.locator('#scene-fallback').isVisible(),true);
    await p.click('#show-next');assert.equal((await state(p)).step,2);await native.close();
    for(const javaScriptEnabled of [true,false]){
      const context=await browser.newContext({offline:true,javaScriptEnabled,viewport:{width:320,height:900}});
      if(javaScriptEnabled)await context.addInitScript(()=>{
        const original=HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext=function(type,...rest){return /webgl/i.test(type)?null:original.call(this,type,...rest);};
      });
      const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto(url);
      assert.equal(await p.locator('#scene-fallback').isVisible(),true);
      if(javaScriptEnabled){await p.click('#show-start');for(let step=0;step<3;step++){await checkStep(p,step,320);if(step<2)await p.click('#show-next');}assert.equal(await p.locator('[data-camera]:enabled').count(),0);}
      else{assert.equal(await p.locator('#show-start').isVisible(),false);assert.equal(await p.locator('noscript li').count(),3);}
      assert.deepEqual(errors,[]);await context.close();
    }
    for(let step=0;step<3;step++)await capture(browser,1280,step,`desktop-step-${step+1}`);
    await capture(browser,390,1,'mobile-step-2');await capture(browser,320,2,'mobile-step-3');await capture(browser,390,2,'fallback',true);
    const result={browser:browser.version(),renderer:'Software WebGL / SwiftShader; touch emulated',checks,finiteAnimation:true,contextLoss:true,noWebgl:true,noJavaScript:true,allPassed:true,
      lifecycle:'document.hidden override with visibilitychange; persisted pagehide event; native matchMedia and timers'};
    fs.writeFileSync(path.join(shots,'checks.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
