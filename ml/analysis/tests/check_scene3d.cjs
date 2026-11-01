// Optional browser QA. Playwright is isolated, never a Python or runtime dependency.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const args=process.argv.slice(2);
const option=(name,fallback)=>args.includes(name)?args[args.indexOf(name)+1]:fallback;
const modulePath=option('--playwright','playwright');
const {chromium}=require(modulePath.startsWith('.')?path.resolve(modulePath):modulePath);
const html=path.resolve(option('--html','analysis_runs/ibuki_crib_3d.html'));
const shots=path.resolve(option('--screenshots','analysis_runs/scene3d-browser'));
if(fs.existsSync(shots))throw Error('Screenshot directory exists; choose a fresh path');
fs.mkdirSync(shots,{recursive:true});
const camera=p=>p.locator('#scene-stage').getAttribute('data-camera').then(JSON.parse);
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
async function settle(p){await p.waitForTimeout(120);}
async function action(p,name){await p.locator(`[data-camera="${name}"]`).click();await settle(p);}
async function idle(p){
  await settle(p);
  const count=await p.locator('#scene-stage').getAttribute('data-render-count');
  await p.waitForTimeout(450);
  assert.equal(await p.locator('#scene-stage').getAttribute('data-render-count'),count,'Idle rendering');
}
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome',args:[
    '--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader',
  ]});
  const results=[];
  try{
    for(const width of [320,390,768,1280,1440]){
      const context=await browser.newContext({offline:true,hasTouch:true,viewport:{width,height:900},reducedMotion:'reduce'});
      const p=await context.newPage(),errors=[],requests=[];
      p.on('pageerror',e=>errors.push(e.message));
      p.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
      await p.goto(pathToFileURL(html).href);
      await p.waitForFunction(()=>document.getElementById('scene-stage').dataset.camera);
      await settle(p);
      assert.equal(await p.locator('#scene-stage').getAttribute('data-state'),'webgl');
      assert.equal(await p.locator('#scene-fallback').isVisible(),false);
      assert.equal(await p.locator('#scene-canvas').isVisible(),true);
      assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      for(const selector of ['#crib-label','#radar-label','[data-camera="reset"]']){
        assert.equal(await p.locator(selector).isVisible(),true);
        const box=await p.locator(selector).boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);
      }
      for(const button of await p.locator('[data-camera]').all()){
        const box=await button.boundingBox();assert.ok(box.height>=44);
      }
      const initial=await camera(p);
      await idle(p);
      if(width===390||width===1280)await p.screenshot({path:path.join(shots,width===390?'mobile.png':'desktop.png'),fullPage:true});
      const canvas=p.locator('#scene-canvas');
      await canvas.focus();await p.keyboard.press('ArrowRight');await settle(p);
      assert.ok((await camera(p)).azimuth>initial.azimuth);
      await p.keyboard.press('+');await settle(p);assert.ok((await camera(p)).distance<initial.distance);
      await p.keyboard.press('Home');await settle(p);near((await camera(p)).distance,initial.distance);
      await p.locator('[data-camera="left"]').focus();await p.keyboard.press('Enter');await settle(p);
      assert.ok((await camera(p)).azimuth<initial.azimuth);
      assert.equal(await p.locator('[data-camera="left"]').evaluate(el=>el===document.activeElement),true);
      assert.notEqual(await p.locator('[data-camera="left"]').evaluate(el=>getComputedStyle(el).outlineStyle),'none');
      await action(p,'reset');
      const box=await canvas.boundingBox(),x=box.x+box.width/2,y=box.y+box.height/2;
      await p.mouse.move(x,y);await p.mouse.down();await p.mouse.move(x+60,y+20,{steps:5});await p.mouse.up();await settle(p);
      assert.ok(Math.abs((await camera(p)).azimuth-initial.azimuth)>.01);
      if(width===1280)await p.screenshot({path:path.join(shots,'rotated.png'),fullPage:true});
      await p.mouse.move(x,y);const beforeWheel=await camera(p);
      await p.mouse.wheel(0,180);await settle(p);assert.ok((await camera(p)).distance>beforeWheel.distance);
      await action(p,'reset');
      const cdp=await context.newCDPSession(p);
      const touch=async(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([id,tx,ty])=>({id,x:tx,y:ty}))});
      await touch('touchStart',[[0,x,y]]);await touch('touchMove',[[0,x+45,y+10]]);await touch('touchEnd',[]);await settle(p);
      assert.ok(Math.abs((await camera(p)).azimuth-initial.azimuth)>.01,'Touch rotation');
      await action(p,'reset');
      await touch('touchStart',[[0,x-25,y],[1,x+25,y]]);
      await touch('touchMove',[[0,x-45,y],[1,x+45,y]]);await touch('touchEnd',[]);await settle(p);
      assert.ok((await camera(p)).distance<initial.distance,'Pinch zoom');
      // Exercise limits without hundreds of software-rendered clicks.
      await p.evaluate(()=>{for(let i=0;i<80;i++)for(const a of ['in','up','left'])document.querySelector(`[data-camera="${a}"]`).click();});
      await settle(p);let limited=await camera(p);near(limited.distance,5);near(limited.polar,.45);near(limited.azimuth,-Math.PI*.85);
      await p.evaluate(()=>{for(let i=0;i<80;i++)for(const a of ['out','down','right'])document.querySelector(`[data-camera="${a}"]`).click();});
      await settle(p);limited=await camera(p);near(limited.distance,12);near(limited.polar,1.35);near(limited.azimuth,Math.PI*.85);
      await action(p,'reset');near((await camera(p)).azimuth,initial.azimuth);near((await camera(p)).distance,initial.distance);
      await idle(p);await p.emulateMedia({reducedMotion:'no-preference'});await idle(p);
      assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
      results.push({width,webgl:true,keyboard:true,mouse:true,touch:true,limits:true,idle:true,httpRequests:requests.length,pageErrors:errors.length});
      // Explicit context loss should retain the explanatory static illustration.
      if(width===1280){
        await p.evaluate(()=>document.getElementById('scene-canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext());
        await p.waitForFunction(()=>document.getElementById('scene-stage').dataset.state==='fallback');
        assert.equal(await p.locator('#scene-fallback').isVisible(),true);
        assert.equal(await p.locator('[data-camera]:enabled').count(),0);
      }
      await context.close();
    }
    for(const javascriptEnabled of [true,false]){
      const context=await browser.newContext({offline:true,javaScriptEnabled:javascriptEnabled,viewport:{width:1280,height:900}});
      if(javascriptEnabled)await context.addInitScript(()=>{
        const original=HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext=function(type,...rest){return /webgl/i.test(type)?null:original.call(this,type,...rest);};
      });
      const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
      await p.goto(pathToFileURL(html).href);await settle(p);
      console.log(JSON.stringify({fallbackCase:javascriptEnabled?'no-webgl':'no-js',state:await p.locator('#scene-stage').getAttribute('data-state'),errors}));
      assert.equal(await p.locator('#scene-fallback').isVisible(),true);
      assert.equal(await p.locator('#scene-canvas').isVisible(),false);
      assert.equal(await p.locator('[data-camera]:enabled').count(),0);
      if(javascriptEnabled){
        assert.equal(await p.locator('#scene-stage').getAttribute('data-state'),'fallback');
        await p.screenshot({path:path.join(shots,'fallback.png'),fullPage:true});
      }
      assert.deepEqual(errors,[]);await context.close();
    }
    const report={browser:browser.version(),renderer:'Chromium software WebGL (SwiftShader), not hardware validation',results,fallback:true,noJavaScript:true,contextLoss:true};
    fs.writeFileSync(path.join(shots,'checks.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
    console.log(JSON.stringify(report,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
