// A Three.js layer in the same Scene, viewed independently of the crib.
// Normalization and z=0 are presentation conventions, never room coordinates.
import {CanvasTexture, DoubleSide, Group, Mesh, MeshBasicMaterial,
  OrthographicCamera, PlaneGeometry, RingGeometry, SRGBColorSpace} from 'three';

export const PLOT_RADIUS=1.45;
export function planePoint(position,extent){
  return [position.x/extent*PLOT_RADIUS,position.y/extent*PLOT_RADIUS,0];
}

export function createAnalysisPlane(scene){
  const root=new Group();root.name='synthetic-analysis-plane';scene.add(root);
  const camera=new OrthographicCamera(-2,2,2.2,-2.2,.1,10);
  camera.position.z=5;camera.layers.set(1);
  const textures=[];
  function mesh(geometry,color,x=0,y=0,order=2){
    const object=new Mesh(geometry,new MeshBasicMaterial({color,side:DoubleSide,depthTest:false,depthWrite:false,toneMapped:false}));
    object.position.set(x,y,0);object.layers.set(1);object.renderOrder=order;root.add(object);return object;
  }
  mesh(new PlaneGeometry(3.9,4.35),'#6d8274',0,0,0);
  mesh(new PlaneGeometry(3.86,4.31),'#fffef9',0,0,1);
  function label(width,height,x,y,fontSize=30){
    const canvas=document.createElement('canvas');canvas.width=768;canvas.height=Math.round(768*height/width);
    const context=canvas.getContext('2d');
    if(!context)throw Error('Canvas text unavailable');
    const texture=new CanvasTexture(canvas);texture.colorSpace=SRGBColorSpace;textures.push(texture);
    const object=mesh(new PlaneGeometry(width,height),'#ffffff',x,y,5);object.material.map=texture;object.material.transparent=true;
    return text=>{
      context.clearRect(0,0,canvas.width,canvas.height);
      context.font=`600 ${fontSize}px system-ui, sans-serif`;context.fillStyle='#254842';context.textAlign='center';context.textBaseline='middle';
      context.fillText(text,canvas.width/2,canvas.height/2,canvas.width-12);texture.needsUpdate=true;
    };
  }
  label(3.65,.28,0,1.93,52)('合成解析の座標面');
  label(3.65,.22,0,1.65,40)('模型とは別 · 奥行きは表示規約');
  label(2,.2,0,1.37,72)('↑ 前方（0°）');
  label(2,.2,0,-1.42,72)('後方');
  label(.5,.2,-1.65,-.12,300)('左');label(.5,.2,1.65,-.12,300)('右');
  const selectedLabel=label(3.65,.25,0,-1.75,40);
  const availabilityLabel=label(3.65,.23,0,-2.02,40);
  mesh(new PlaneGeometry(.008,2.75),'#849487',0,-.06);
  mesh(new PlaneGeometry(2.9,.008),'#849487',0,-.06);
  // The plot origin is offset vertically for labels; fixed z stays exactly zero.
  const originY=-.06;
  const reference=mesh(new RingGeometry(.09,.135,48),'#287269',0,0,4);
  reference.name='configured-reference';
  const estimated=mesh(new PlaneGeometry(.18,.18),'#a45424',0,0,4);
  estimated.rotation.z=Math.PI/4;estimated.name='estimated-position';
  let guidesCreated=false;
  function update(window,index,total,extent){
    if(!guidesCreated){
      for(let r=25;r<=extent;r+=25){
        const radius=r/extent*PLOT_RADIUS;
        mesh(new RingGeometry(radius-.004,radius+.004,80),'#849487',0,originY);
        label(.65,.18,radius*.74,originY+radius*.67,230)(`${r} cm`);
      }
      guidesCreated=true;
    }
    reference.position.fromArray(planePoint(window.truth_position_cm,extent));reference.position.y+=originY;
    // Remove, rather than retain an old/fabricated estimate, when unavailable.
    if(window.estimation_available){
      if(!estimated.parent)root.add(estimated);
      estimated.position.fromArray(planePoint(window.position_cm,extent));estimated.position.y+=originY;
    }else{root.remove(estimated);estimated.position.set(0,0,0);}
    selectedLabel(`区間 ${index+1} / ${total} · ○ 設定 / ◆ 推定`);
    availabilityLabel(window.estimation_available?'推定あり（正解とは限りません）':'推定できません（理由は詳細へ）');
  }
  function fit(width,height){
    const halfHeight=Math.max(2.25,2/(width/height));
    camera.top=halfHeight;camera.bottom=-halfHeight;
    camera.left=-halfHeight*width/height;camera.right=halfHeight*width/height;camera.updateProjectionMatrix();
  }
  // Read-only inspection of actual scene objects for browser parity checks.
  function snapshot(){
    const project=object=>({position:object.position.toArray(),ndc:object.position.clone().project(camera).toArray(),layer:object.layers.mask});
    return {reference:project(reference),estimate:estimated.parent===root?project(estimated):null,
      originY,plotRadius:PLOT_RADIUS,planeDepth:0,layer:1,rootName:root.name};
  }
  function dispose(){
    // Include a currently removed estimated mesh in cleanup.
    if(!estimated.parent)root.add(estimated);
    textures.forEach(texture=>texture.dispose());
  }
  return {camera,update,fit,snapshot,dispose};
}
