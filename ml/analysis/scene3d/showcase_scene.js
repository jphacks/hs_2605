// Abstract, finite visual pulses. They are not measured beams, motion or coverage.
import {DoubleSide,Mesh,MeshBasicMaterial,RingGeometry,Vector3} from 'three';
export function createShowcaseVisuals(scene,position,direction){
  const halo=new Mesh(new RingGeometry(.36,.395,64),new MeshBasicMaterial({color:'#b78c4c',side:DoubleSide,transparent:true,opacity:.85,depthWrite:false}));
  halo.position.copy(position);scene.add(halo);
  const geometry=new RingGeometry(.3,.317,48);
  const waves=Array.from({length:3},()=>{
    const mesh=new Mesh(geometry,new MeshBasicMaterial({color:'#648f7b',side:DoubleSide,transparent:true,opacity:.35,depthWrite:false}));
    mesh.quaternion.setFromUnitVectors(new Vector3(0,0,1),direction);mesh.visible=false;scene.add(mesh);return mesh;
  });
  let step=0,active=false,elapsed=0,last=null;
  const duration=3600;
  function draw(){
    waves.forEach((mesh,i)=>{
      const phase=active?((elapsed/1800+i/3)%1):(.2+i*.22);
      mesh.position.copy(position).addScaledVector(direction,.22+1.5*phase);
      mesh.scale.setScalar(.45+phase*.8);mesh.material.opacity=.4*(1-phase*.8);mesh.visible=step===1;
    });
  }
  function select(index,animate){step=index;active=index===1&&animate;elapsed=0;last=null;draw();}
  function update(time,camera){
    halo.quaternion.copy(camera.quaternion);
    if(active){
      if(last!==null)elapsed+=Math.max(0,time-last);last=time;
      if(elapsed>=duration){elapsed=duration;active=false;last=null;}
      draw();
    }
  }
  return {select,update,isAnimating:()=>active,
    pause(){active=false;last=null;draw();},
    resume(){if(step===1){if(elapsed>=duration)elapsed=0;active=true;last=null;draw();}},
    snapshot:()=>({step,active,elapsed,wavesVisible:waves.filter(mesh=>mesh.visible).length})};
}
