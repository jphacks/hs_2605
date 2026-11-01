// Conceptual geometry only. Scene units are arbitrary, not measured dimensions.
import {
  ACESFilmicToneMapping, BoxGeometry, CircleGeometry, Color, CylinderGeometry,
  DirectionalLight, DoubleSide, Group, HemisphereLight, MathUtils, Mesh,
  MeshBasicMaterial, MeshStandardMaterial, PCFSoftShadowMap, PerspectiveCamera,
  PlaneGeometry, Scene, SphereGeometry, Spherical, SRGBColorSpace, Vector3,
  WebGLRenderer,
} from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';

const stage=document.getElementById('scene-stage');
const canvas=document.getElementById('scene-canvas');
const fallback=document.getElementById('scene-fallback');
const status=document.getElementById('scene-status');
const buttons=[...document.querySelectorAll('[data-camera]')];
const labels=[document.getElementById('crib-label'),document.getElementById('radar-label')];
let renderer,controls,frame=null,resizeObserver,scene,camera;
let renderCount=0;

function showFallback(message){
  stage.dataset.state='fallback';
  if(frame!==null)cancelAnimationFrame(frame);
  frame=null;canvas.hidden=true;fallback.removeAttribute('hidden');
  labels.forEach(el=>el.hidden=true);
  buttons.forEach(el=>el.disabled=true);
  status.textContent=message;
}

function initialize(){
  renderer=new WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));
  renderer.outputColorSpace=SRGBColorSpace;
  renderer.toneMapping=ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.25;
  renderer.shadowMap.enabled=true;
  renderer.shadowMap.type=PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate=false;renderer.shadowMap.needsUpdate=true;
  scene=new Scene();scene.background=new Color('#f4f0e7');
  camera=new PerspectiveCamera(40,1,.1,50);
  controls=new OrbitControls(camera,canvas);
  controls.target.set(.2,.9,0);
  controls.enablePan=false;controls.enableDamping=false;controls.autoRotate=false;
  controls.minDistance=5;controls.maxDistance=12;
  controls.minPolarAngle=.45;controls.maxPolarAngle=1.35;
  controls.minAzimuthAngle=-Math.PI*.85;controls.maxAzimuthAngle=Math.PI*.85;
  controls.rotateSpeed=.65;controls.zoomSpeed=.7;

  const material=(color)=>new MeshStandardMaterial({color,roughness:.85,metalness:0});
  const wood=material('#c6aa83'),lightWood=material('#dbc5a2'),sheet=material('#fffdf5');
  const skin=material('#e6b996'),blanket=material('#92ad9a'),teal=material('#315c53');
  function mesh(geometry,mat,position,parent=scene){
    const object=new Mesh(geometry,mat);object.position.set(...position);
    object.castShadow=true;object.receiveShadow=true;parent.add(object);return object;
  }
  const box=(size,mat,position,parent)=>mesh(new BoxGeometry(...size),mat,position,parent);
  const sphere=(scale,mat,position,parent)=>{
    const object=mesh(new SphereGeometry(1,24,16),mat,position,parent);object.scale.set(...scale);return object;
  };
  scene.add(new HemisphereLight('#fff5df','#9caa9b',2.6));
  const sun=new DirectionalLight('#fff4dc',3.2);sun.position.set(-3,7,5);sun.castShadow=true;
  sun.shadow.mapSize.set(1024,1024);sun.shadow.camera.left=-5;sun.shadow.camera.right=5;
  sun.shadow.camera.top=5;sun.shadow.camera.bottom=-5;sun.shadow.normalBias=.04;scene.add(sun);
  const floor=mesh(new PlaneGeometry(30,30),material('#f4f0e7'),[0,-.02,0]);floor.rotation.x=-Math.PI/2;floor.castShadow=false;
  const rug=mesh(new CircleGeometry(3.25,64),material('#e4e7dc'),[.1,0,0]);rug.rotation.x=-Math.PI/2;rug.castShadow=false;

  const crib=new Group();scene.add(crib);
  box([2.35,.18,4.05],wood,[0,.44,0],crib);
  box([2.13,.23,3.81],sheet,[0,.69,0],crib);
  for(const x of [-1.15,1.15])for(const z of [-1.98,1.98]){
    mesh(new CylinderGeometry(.075,.075,1.65,16),wood,[x,.825,z],crib);
    sphere([.09,.09,.09],lightWood,[x,1.66,z],crib);
  }
  for(const x of [-1.15,1.15]){
    box([.11,.12,4.05],wood,[x,1.45,0],crib);
    for(let i=0;i<12;i++)mesh(new CylinderGeometry(.026,.026,.9,10),lightWood,[x,.95,-1.8+i*3.6/11],crib);
  }
  for(const z of [-1.98,1.98]){
    box([2.35,.12,.11],wood,[0,1.45,z],crib);
    for(let i=0;i<7;i++)mesh(new CylinderGeometry(.026,.026,.9,10),lightWood,[-.95+i*1.9/6,.95,z],crib);
  }
  // A still, stylized sleeping figure, not anatomy or a breathing animation.
  sphere([.43,.24,.8],blanket,[0,.99,.2],crib);
  sphere([.3,.29,.31],skin,[0,1.07,-.78],crib);
  for(const x of [-.29,.29])sphere([.055,.075,.065],skin,[x,1.08,-.78],crib);
  for(const x of [-.105,.105]){
    const eye=box([.08,.009,.018],teal,[x,1.34,-.69],crib);eye.rotation.z=x<0?.12:-.12;
  }

  // Fictional enclosure and support. No real board/mount dimensions are implied.
  const sensor=new Group();sensor.position.set(1.95,1.85,-.25);
  const sensorDirection=new Vector3(0,1,0).sub(sensor.position).normalize();
  sensor.quaternion.setFromUnitVectors(new Vector3(0,0,1),sensorDirection);scene.add(sensor);
  box([.61,.45,.2],teal,[0,0,0],sensor);
  box([.51,.32,.035],teal,[0,0,.115],sensor);
  for(const x of [-.15,0,.15])box([.025,.16,.015],lightWood,[x,0,.14],sensor);
  sphere([.025,.025,.015],material('#e9ca82'),[.23,.15,.13],sensor);
  mesh(new CylinderGeometry(.035,.045,1.6,16),teal,[1.95,.88,-.25]);
  mesh(new CylinderGeometry(.29,.32,.08,32),lightWood,[1.95,.055,-.25]);
  const tip=sensor.position.clone().addScaledVector(sensorDirection,.17);
  const reach=1.65;
  const direction=mesh(new CylinderGeometry(0,.75,reach,32,1,true),
    new MeshBasicMaterial({color:'#7eaa99',transparent:true,opacity:.10,side:DoubleSide,depthWrite:false}),
    tip.clone().addScaledVector(sensorDirection,reach/2).toArray());
  direction.quaternion.setFromUnitVectors(new Vector3(0,-1,0),sensorDirection);
  direction.castShadow=false;direction.receiveShadow=false;

  const anchors=[new Vector3(-.8,1.8,-1.4),new Vector3(2.05,2.6,-.25)];
  function updateLabels(){
    const bounds=stage.getBoundingClientRect(),placed=[];
    labels.forEach((el,i)=>{
      const point=anchors[i].clone().project(camera);
      el.hidden=point.z< -1||point.z>1||Math.abs(point.x)>1||Math.abs(point.y)>1;
      if(el.hidden)return;
      let x=(point.x+1)*bounds.width/2,y=(1-point.y)*bounds.height/2;
      const w=el.offsetWidth,h=el.offsetHeight;
      x=MathUtils.clamp(x,w/2+8,bounds.width-w/2-8);
      y=MathUtils.clamp(y,8,bounds.height-h-8);
      if(placed.some(p=>Math.abs(x-p.x)<(w+p.w)/2+6&&Math.abs(y-p.y)<h+6))
        y=MathUtils.clamp(y+h+8,8,bounds.height-h-8);
      el.style.left=`${x}px`;el.style.top=`${y}px`;placed.push({x,y,w});
    });
  }
  function render(){
    frame=null;if(document.hidden||stage.dataset.state!=='webgl')return;
    renderer.render(scene,camera);updateLabels();
    const view=new Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    stage.dataset.camera=JSON.stringify({azimuth:view.theta,polar:view.phi,distance:view.radius});
    stage.dataset.renderCount=String(++renderCount);
  }
  function requestRender(){if(frame===null&&!document.hidden&&stage.dataset.state==='webgl')frame=requestAnimationFrame(render);}
  function reset(){
    const distance=camera.aspect<1?9.5:8;
    controls.target.set(.2,.9,0);
    camera.position.copy(controls.target).add(new Vector3().setFromSpherical(new Spherical(distance,.95,.65)));
    controls.update();requestRender();
  }
  function changeView(action){
    if(stage.dataset.state!=='webgl')return;
    if(action==='reset')reset();
    else{
      const view=new Spherical().setFromVector3(camera.position.clone().sub(controls.target));
      if(action==='left')view.theta-=.15;if(action==='right')view.theta+=.15;
      if(action==='up')view.phi-=.12;if(action==='down')view.phi+=.12;
      if(action==='in')view.radius*=.9;if(action==='out')view.radius/= .9;
      view.theta=MathUtils.clamp(view.theta,controls.minAzimuthAngle,controls.maxAzimuthAngle);
      view.phi=MathUtils.clamp(view.phi,controls.minPolarAngle,controls.maxPolarAngle);
      view.radius=MathUtils.clamp(view.radius,controls.minDistance,controls.maxDistance);
      camera.position.copy(controls.target).add(new Vector3().setFromSpherical(view));controls.update();requestRender();
    }
    status.textContent=action==='reset'?'初期の視点に戻しました。':'視点を変更しました。表示は設置イメージです。';
  }
  function resize(){
    const {width,height}=stage.getBoundingClientRect();
    if(width<=0||height<=0)return;
    camera.aspect=width/height;camera.updateProjectionMatrix();renderer.setSize(width,height,false);requestRender();
  }
  controls.addEventListener('change',requestRender);
  buttons.forEach(button=>{button.disabled=false;button.addEventListener('click',()=>changeView(button.dataset.camera));});
  canvas.addEventListener('keydown',event=>{
    const action={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down','+':'in','=':'in','-':'out',Home:'reset'}[event.key];
    if(action){event.preventDefault();changeView(action);}
  });
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();showFallback('3D の描画が停止したため、平面図に切り替えました。再度開くと3Dを試せます。');});
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden&&frame!==null){cancelAnimationFrame(frame);frame=null;}else requestRender();
  });
  window.addEventListener('pagehide',event=>{
    if(frame!==null)cancelAnimationFrame(frame);frame=null;
    if(event.persisted)return; // A back/forward-cache restoration keeps controls usable.
    resizeObserver.disconnect();controls.dispose();renderer.dispose();
    const geometries=new Set(),materials=new Set();
    scene.traverse(object=>{if(object.geometry)geometries.add(object.geometry);if(object.material)materials.add(object.material);});
    geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());
  });
  resizeObserver=new ResizeObserver(resize);resizeObserver.observe(stage);
  stage.dataset.state='webgl';fallback.setAttribute('hidden','');canvas.hidden=false;
  resize();reset();
  status.textContent='ドラッグやボタンで視点を操作できます。実測データは表示していません。';
}
try{initialize();}
catch(error){
  if(renderer)renderer.dispose();
  showFallback('この環境では 3D を表示できないため、設置イメージの平面図を表示しています。');
}
