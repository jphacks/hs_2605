// View adapter only. Positions/errors come unchanged from load_case/evaluate.
// This SVG is a separate analysis plane: there is no mapping onto crib geometry.
export function initializeAnalysis(){
  const embedded=document.getElementById('scene-data');
  if(!embedded)return;
  const data=JSON.parse(embedded.textContent),windows=data.windows;
  const byId=id=>document.getElementById(id);
  const number=(v,digits=2)=>v===null||v===undefined?'—':Number(v).toFixed(digits);
  const radius=p=>Math.hypot(p.x,p.y);
  const maxRadius=windows.reduce((largest,w)=>Math.max(largest,radius(w.truth_position_cm),w.estimation_available?radius(w.position_cm):0),0);
  const extent=Math.max(50,Math.ceil(maxRadius/25)*25)*1.18;
  const scale=220/extent,xy=p=>[300+p.x*scale,285-p.y*scale];
  const svg=(tag,attributes,parent,text)=>{
    const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const [key,value] of Object.entries(attributes))el.setAttribute(key,value);
    if(text!==undefined)el.textContent=text;parent.appendChild(el);return el;
  };
  const guides=byId('analysis-guides');
  for(let r=25;r<=extent;r+=25){
    svg('circle',{cx:300,cy:285,r:r*scale,class:'guide'},guides);
    svg('text',{x:306+r*scale*.8,y:280-r*scale*.6},guides,`${r} cm`);
  }
  svg('line',{x1:300,y1:55,x2:300,y2:515,class:'guide'},guides);
  svg('line',{x1:65,y1:285,x2:535,y2:285,class:'guide'},guides);
  svg('text',{x:300,y:28,'text-anchor':'middle'},guides,'↑ 前方（0°）');
  svg('text',{x:300,y:555,'text-anchor':'middle'},guides,'後方');
  svg('text',{x:56,y:315,'text-anchor':'middle'},guides,'左');
  svg('text',{x:544,y:315,'text-anchor':'middle'},guides,'右');
  svg('path',{d:'M300 272 L289 292 L311 292 Z',fill:'#254842'},guides);
  const reference=svg('g',{'data-kind':'truth'},byId('analysis-markers'));
  svg('circle',{r:20,fill:'#fffef9',stroke:'#287269','stroke-width':3,'vector-effect':'non-scaling-stroke'},reference);
  const estimate=svg('g',{'data-kind':'estimate'},byId('analysis-markers'));
  svg('path',{d:'M0 -14 l14 14 l-14 14 l-14 -14 Z',fill:'#a45424',stroke:'#fffef9','stroke-width':2,'vector-effect':'non-scaling-stroke'},estimate);
  const reasons={insufficient_moving_signal:'動きの信号が小さく、位置を推定できません。',low_pair_01_coherence:'アンテナ 0–1 の信号の対応が弱いため、位置を推定できません。',insufficient_samples_for_window:'この区間は解析に必要な観測数が足りません。'};
  const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
  function renderSelection(state){
    const index=state.selected,w=windows[index],has=w.estimation_available;
    const card=byId('analysis-title').closest('section');
    card.dataset.selected=index;card.dataset.playing=state.playing;card.dataset.stopReason=state.stopReason??'';
    byId('window-select').value=index;byId('timeline').value=index;
    const times=data.simulation.board_uptime_ms;
    const start=times[w.frame_row_start],end=times[w.frame_row_end_exclusive-1];
    const timeText=`観測期間：起動後 ${number(start/1000)}–${number(end/1000)} 秒 · 比較の基準は ${number(w.reference_board_uptime_ms/1000)} 秒`;
    byId('time-readout').textContent=timeText;
    byId('timeline').setAttribute('aria-valuetext',`区間 ${index+1} / ${windows.length}。ブロック ${w.parent_block_index}、連続区間 ${w.trace_index}。${timeText}。${has?'推定あり':'推定できません'}`);
    byId('play').disabled=state.playing||index===windows.length-1||reducedMotion.matches;
    byId('pause').disabled=!state.playing;byId('previous').disabled=index===0;byId('next').disabled=index===windows.length-1;
    const messages={initial:'再生は自動で始まりません。',pause:'表示を一時停止しました。',manual:'手動で区間を選びました。再生は停止中です。',end:'最後の区間で停止しました。',boundary:'データの区切りで停止しました。「次へ」や目盛りで別の区間を選べます。',hidden:'ページが非表示になったため停止しました。戻っても自動では再開しません。',reduced:'動きを抑える設定のため、自動再生は無効です。前後のボタンや目盛りで選べます。'};
    byId('playback-message').textContent=reducedMotion.matches?messages.reduced:(messages[state.stopReason]||'同じ連続区間の中で、計算済みの窓を順に表示しています。');
    const availability=has?'推定あり':'推定できません';
    const stopNotice=['boundary','end','hidden','reduced'].includes(state.stopReason)?'。'+byId('playback-message').textContent:'';
    byId('playback-status').textContent=`${state.playing?'再生中':'一時停止'} · 区間 ${index+1} / ${windows.length} · ${availability}${stopNotice}`;
    byId('map-selection').textContent=`表示中：区間 ${index+1} / ${windows.length} · ${availability}`;
    for(const [group,position,kind] of [[reference,w.truth_position_cm,'truth'],[estimate,w.position_cm,'estimate']]){
      group.toggleAttribute('hidden',position===null);
      if(position===null){group.removeAttribute('transform');continue;}
      const [x,y]=xy(position);group.setAttribute('transform',`translate(${x} ${y})`);
      group.dataset.window=index;group.dataset.xCm=position.x;group.dataset.yCm=position.y;
      group.setAttribute('aria-label',`${kind==='truth'?'設定':'推定'}位置。距離 ${number(radius(position))} cm、角度 ${number(kind==='truth'?w.configured_angle_deg:w.estimated_angle_deg,1)} 度`);
    }
    byId('window-label').textContent=`区間 ${index+1} / ${windows.length} · ブロック ${w.parent_block_index}・連続区間 ${w.trace_index}`;
    byId('status').textContent=has?'推定あり · 正しさを保証するものではありません':'推定できません';
    const warning=byId('reason');warning.hidden=has&&!w.truth_outside_front_hemisphere;
    warning.textContent=!has?(reasons[w.unavailable_reason]||'この区間は推定できません。診断値と来歴をご確認ください。'):'前後の方向を区別できない例です。誤った推定もそのまま表示しています。設定値との差を確認してください。';
    byId('truth-range').textContent=number(radius(w.truth_position_cm))+' cm';
    byId('truth-angle').textContent=number(w.configured_angle_deg,1)+'°';
    byId('estimate-range').textContent=has?number(radius(w.position_cm))+' cm':'—';
    byId('estimate-angle').textContent=has?number(w.estimated_angle_deg,1)+'°':'—';
    byId('range-difference').textContent=has?number(Math.abs(radius(w.position_cm)-radius(w.truth_position_cm)))+' cm':'—';
    byId('angle-difference').textContent=has?number(w.absolute_angular_error_deg,2)+'°':'—';
    byId('position-difference').textContent=has?number(w.position_error_cm)+' cm':'—';
    byId('window-info').textContent=`${timeText}。観測 ${w.observed_samples} フレーム · 元の行 ${w.source_line_start}–${w.source_line_end}。解析窓全体の推定で、瞬間の測定位置ではありません。`;
    byId('diagnostics').textContent=JSON.stringify({unavailable_reason:w.unavailable_reason,configured_range_bin:w.configured_range_bin,estimated_range_bin:w.estimated_range_bin,raw_estimate:w.raw_estimate,pair_01_coherence:w.pair_01_coherence??null,mean_movement:w.mean_movement??null},null,2);
  }
  windows.forEach((w,index)=>{
    const option=document.createElement('option');option.value=index;option.textContent=`区間 ${index+1} · ${w.estimation_available?'推定あり':'推定できません'}`;byId('window-select').appendChild(option);
  });
  byId('timeline').max=windows.length-1;
  byId('provenance').textContent=JSON.stringify({synthetic:data.synthetic,hardware_validated:data.hardware_validated,source_type:data.source_type,source_type_provenance:data.source_type_provenance,input_sha256:data.input_sha256,verified_artifact_hashes:data.verified_artifact_hashes,explorer:data.explorer,parameters:data.parameters,data_quality:data.data_quality,simulation_configuration:data.simulation.configuration,antenna_x_wavelengths:data.simulation.antenna_x_wavelengths},null,2);
  renderSelection({selected:0,playing:false,stopReason:'initial'});
}
