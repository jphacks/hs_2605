// STEP 3 layout/accessibility regression; no mockup assets or network resources.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const args=process.argv.slice(2),option=(n,d)=>args.includes(n)?args[args.indexOf(n)+1]:d;
const modulePath=option('--playwright','playwright');
const {chromium}=require(modulePath.startsWith('.')?path.resolve(modulePath):modulePath);
const html=path.resolve(option('--html','analysis_runs/notification-refined.html'));
const before=path.resolve(option('--before','analysis_runs/notification-before.html'));
const shots=path.resolve(option('--screenshots','analysis_runs/notification-browser'));
if(fs.existsSync(shots))throw Error('Choose a fresh screenshot directory');fs.mkdirSync(shots,{recursive:true});
const sameText=['story-title','story-message','result-title','result-message','show-visual-note','story-status','scene-limitations'];
async function snapshot(p){
  return p.evaluate(ids=>({text:Object.fromEntries(ids.map(id=>[id,document.getElementById(id).innerText])),
    boxes:Object.fromEntries(['scene-stage','story-result','radar-label','crib-label'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return [id,[r.x,r.y,r.width,r.height]];})),
    camera:JSON.parse(document.getElementById('scene-stage').dataset.camera)}),sameText);
}
async function notice(p,width){
  const card=p.locator('#guardian-notification');assert.equal(await card.isVisible(),true);
  for(const [id,text] of Object.entries({'notification-header':'通知の例 · シミュレーション','notification-message':'赤ちゃんの様子を確認してください','notification-footer':'デモ表示 · 実際には送信されません'})){
    assert.equal(await p.locator('#'+id).innerText().then(t=>t.replaceAll('\n','')),text);
  }
  assert.equal(await p.locator('#result-copy').isVisible(),false);assert.equal(await p.locator('#result-icon').isVisible(),false);
  assert.equal(await p.locator('[aria-live]').count(),1);assert.equal(await card.getAttribute('aria-live'),null);assert.equal(await card.getAttribute('role'),null);
  const c=await card.boundingBox(),s=await p.locator('#scene-stage').boundingBox();
  assert.ok(c.x>=0&&c.x+c.width<=width);
  if(width>=1024){assert.ok(c.x>=s.x+s.width,'Notification overlaps the 3D viewport');assert.ok(c.y>=s.y&&c.y+c.height<=s.y+s.height);}
  else{assert.ok(c.y>=s.y+s.height,'Mobile phone overlays the crib');assert.equal(await p.locator('#notification-connector').isVisible(),false);}
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
}
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const checks=[];
  try{
    for(const width of [320,390,768,1280,1440]){
      const baselineContext=await browser.newContext({offline:true,reducedMotion:'reduce',viewport:{width,height:900}}),baseline=await baselineContext.newPage();
      await baseline.goto(pathToFileURL(before).href);await baseline.click('#show-start');await baseline.waitForTimeout(150);
      const step1=await snapshot(baseline);await baseline.click('#show-next');await baseline.waitForTimeout(150);const step2=await snapshot(baseline);await baselineContext.close();
      const context=await browser.newContext({offline:true,hasTouch:width<=390,reducedMotion:'reduce',viewport:{width,height:900}}),p=await context.newPage(),errors=[],requests=[];
      p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
      await p.goto(pathToFileURL(html).href);await p.click('#show-start');await p.waitForTimeout(150);
      assert.deepEqual(await snapshot(p),step1,'STEP 1 changed');assert.equal(await p.locator('#guardian-notification').isVisible(),false);
      await p.click('#show-next');await p.waitForTimeout(150);assert.deepEqual(await snapshot(p),step2,'STEP 2 changed');assert.equal(await p.locator('#guardian-notification').isVisible(),false);
      const scroll=await p.evaluate(()=>scrollY);
      if(width<=390)await p.tap('#show-next');else{await p.locator('#show-next').focus();await p.keyboard.press('Space');}
      await p.waitForTimeout(180);await notice(p,width);assert.equal(await p.evaluate(()=>scrollY),scroll);
      assert.equal(await p.locator('#radar-label').isVisible(),true);assert.equal(await p.locator('#crib-label').isVisible(),true);
      if(width>=1024){assert.equal(await p.locator('#notification-connector').isVisible(),true);assert.ok(await p.locator('#notification-path').getAttribute('d'));}
      const draws=await p.locator('#scene-stage').getAttribute('data-render-count');await p.waitForTimeout(350);
      assert.equal(await p.locator('#scene-stage').getAttribute('data-render-count'),draws,'STEP 3 starts a render loop');
      if(width!==768)await p.screenshot({path:path.join(shots,`step3-${width}.png`),fullPage:true});
      // Reserved viewport keeps card clear of the entire model at other orbits.
      await p.locator('#scene-canvas').focus();await p.keyboard.press('ArrowRight');await p.keyboard.press('+');await p.waitForTimeout(120);await notice(p,width);
      await p.click('[data-camera="reset"]');await p.waitForTimeout(150);await notice(p,width);
      await p.click('#show-previous');assert.equal(await p.locator('#guardian-notification').isVisible(),false);assert.equal(await p.locator('#result-copy').isVisible(),true);
      await p.click('#show-next');await p.waitForTimeout(120);await notice(p,width);
      await p.click('#show-restart');assert.equal(await p.locator('#guardian-notification').isVisible(),false);assert.equal(await p.locator('#notification-connector').isVisible(),false);
      assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);checks.push({width,step1And2Unchanged:true,cardClearOfViewport:true,exactCopy:true,errors,requests});await context.close();
    }
    // Layout changes use the same element when crossing the breakpoint.
    const responsive=await browser.newContext({offline:true,reducedMotion:'reduce',viewport:{width:1280,height:900}}),p=await responsive.newPage();
    await p.goto(pathToFileURL(html).href);await p.click('#show-start');await p.click('#show-next');await p.click('#show-next');await p.waitForTimeout(150);
    await p.setViewportSize({width:390,height:900});await p.waitForTimeout(180);await notice(p,390);
    await p.setViewportSize({width:1440,height:900});await p.waitForTimeout(180);await notice(p,1440);assert.equal(await p.locator('#guardian-notification').count(),1);
    await p.evaluate(()=>document.getElementById('scene-canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext());
    await p.waitForFunction(()=>document.getElementById('scene-stage').dataset.state==='fallback');await notice(p,1440);assert.equal(await p.locator('#notification-connector').isVisible(),false);await responsive.close();
    for(const width of [320,1280]){
      const context=await browser.newContext({offline:true,reducedMotion:'reduce',viewport:{width,height:900}});
      await context.addInitScript(()=>{const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...rest){return /webgl/i.test(type)?null:original.call(this,type,...rest);};});
      const p=await context.newPage();await p.goto(pathToFileURL(html).href);await p.click('#show-start');await p.click('#show-next');await p.click('#show-next');await notice(p,width);
      assert.equal(await p.locator('#scene-fallback').isVisible(),true);assert.equal(await p.locator('#notification-connector').isVisible(),false);
      await p.screenshot({path:path.join(shots,`fallback-${width}.png`),fullPage:true});await context.close();
    }
    const result={browser:browser.version(),renderer:'SwiftShader software WebGL; emulated touch',checks,responsiveSingleCard:true,webglFallback:true,contextLoss:true,allPassed:true};
    fs.writeFileSync(path.join(shots,'checks.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
