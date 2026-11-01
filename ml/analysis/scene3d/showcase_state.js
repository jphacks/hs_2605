// Presentation navigation only; no measurements, classifier or timed step advance.
export function createShowcase(onChange){
  let started=false,step=0;
  const snapshot=()=>({started,step});
  const publish=()=>onChange(snapshot());
  return {snapshot,start(){if(started)return;started=true;publish();},
    next(){if(started&&step<2){step++;publish();}},
    previous(){if(started&&step>0){step--;publish();}},
    restart(){if(started){step=0;publish();}}};
}
