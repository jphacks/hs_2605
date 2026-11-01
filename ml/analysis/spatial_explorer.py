"""Generate a self-contained, synthetic-only spatial inspection page."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .report import _json as read_json, load_dataset
from .spatial_sim import evaluate


def load_case(directory: str | Path) -> dict:
    """Validate canonical inputs and reuse the existing spatial evaluator.

    A saved evaluation is checked against a fresh evaluation with its original
    parameters; it is never trusted as an independent source of positions.
    Hashes establish consistency, not authenticity or hardware calibration.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError("Input must be a spatial_sim case directory; generate one with --scenario sweep")
    truth_path = root / "truth.json"
    if not truth_path.is_file() or truth_path.is_symlink():
        raise ValueError("Missing or unsafe truth.json; use a spatial_sim case_00 directory")
    truth_bytes = truth_path.read_bytes()
    truth = read_json(truth_bytes, "truth.json")
    dataset = load_dataset(root / "canonical")
    if dataset.manifest["source_type"] != "synthetic":
        raise ValueError("Explorer requires source_type='synthetic'; physical/unknown captures are not supported")
    if not dataset.frames:
        raise ValueError("No E frames to inspect; generate a nonempty spatial_sim case")
    if any(r["message_type"] != "E" or r["antenna_count"] != 3 or r["bin_count"] != 24
           or r["configuration_status"] != "known" or r["first_cm"] != 15
           or r["step_mm"] != 37.5 or r["config_bin_count"] != 24 for r in dataset.frames):
        raise ValueError("Require E frames with three antennas, 24 bins and known 15 cm / 37.5 mm geometry; regenerate the case")
    if not isinstance(truth, dict) or truth.get("antenna_y_wavelengths") != [0, 0, 0]:
        raise ValueError("Require spatial_sim truth with the declared horizontal collinear array geometry")

    saved_path = root / "evaluation.json"
    parameters = {}
    saved = None
    saved_bytes = None
    if saved_path.exists() or saved_path.is_symlink():
        if not saved_path.is_file() or saved_path.is_symlink():
            raise ValueError("Unsafe evaluation.json; use an unmodified spatial_sim case")
        saved_bytes = saved_path.read_bytes()
        saved = read_json(saved_bytes, "evaluation.json")
        if not isinstance(saved, dict) or not isinstance(saved.get("parameters"), dict):
            raise ValueError("Invalid spatial_sim evaluation.json; regenerate the simulation case")
        keys = ("window_frames", "hop_frames", "min_movement", "min_coherence")
        if any(k not in saved["parameters"] for k in keys):
            raise ValueError("Missing saved evaluation parameters; regenerate the simulation case")
        parameters = {k: saved["parameters"][k] for k in keys}
        # bools/strings/objects must fail before entering numerical code.
        for key in ("min_movement", "min_coherence"):
            if type(parameters[key]) not in (int, float):
                raise ValueError(f"Invalid saved parameter: {key}")
    result = evaluate(dataset, truth, **parameters)
    if saved is not None and saved != result:
        raise ValueError("evaluation.json differs from canonical/truth evaluation; regenerate the case in this environment")
    result["explorer"] = dict(
        version=1, truth_sha256=hashlib.sha256(truth_bytes).hexdigest(),
        manifest_sha256=hashlib.sha256((dataset.path / "manifest.json").read_bytes()).hexdigest(),
        saved_evaluation_sha256=hashlib.sha256(saved_bytes).hexdigest() if saved_bytes is not None else None,
        evaluation_source="verified_saved_parameters" if saved is not None else "spatial_sim_defaults",
        frame_count=len(dataset.frames), event_count=len(dataset.events),
    )
    return result


def build_html(result: dict) -> str:
    """Embed data as inert JSON, including safe script-boundary characters."""
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for char, replacement in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                              ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        encoded = encoded.replace(char, replacement)
    return _PAGE.replace("__IBUKI_PLAYBACK__", _PLAYBACK_JS).replace("__IBUKI_DATA__", encoded)


def render_explorer(directory: str | Path, output: str | Path) -> Path:
    """Fresh HTML only, under the repository's ignored analysis_runs tree."""
    requested = Path(output)
    target = requested.resolve()
    runs = Path(__file__).resolve().parents[2] / "analysis_runs"
    if requested.is_symlink() or runs not in target.parents:
        raise ValueError("Output must be a new .html file under the repository's ignored analysis_runs/ directory (no symlinks)")
    if target.suffix.lower() != ".html":
        raise ValueError("Output must have a .html extension")
    case = Path(directory).resolve()
    canonical = (case / "canonical").resolve()
    if canonical == target or canonical in target.parents:
        raise ValueError("Output must stay outside the canonical dataset directory")
    if target.exists():
        raise FileExistsError("Output already exists; choose a fresh filename (no overwrite)")
    if not target.parent.is_dir():
        raise ValueError("Output parent directory must exist; generate the simulation run first")
    content = build_html(load_case(case)).encode("utf-8")
    with target.open("xb") as stream:
        stream.write(content)
    return target


_PLAYBACK_JS = """
// One selected index, one timer and one notification path for every control.
function createPlayback(windows, onChange, options = {}) {
 const delay = options.setTimeout || globalThis.setTimeout;
 const cancel = options.clearTimeout || globalThis.clearTimeout;
 const blocked = options.blockedReason || (() => null);
 let selected = 0, playing = false, timer = null, stopReason = 'initial';
 const snapshot = () => ({selected, playing, stopReason});
 const publish = () => onChange(snapshot());
 function sameSegment(a, b) {
  return ['parent_block_index','trace_index'].every(key => Number.isInteger(a[key]) && a[key]>0 && a[key]===b[key])
   && Number.isFinite(a.reference_board_uptime_ms) && a.reference_board_uptime_ms>=0
   && Number.isFinite(b.reference_board_uptime_ms) && b.reference_board_uptime_ms>a.reference_board_uptime_ms
   && Number.isInteger(a.source_line_start) && Number.isInteger(a.source_line_end)
   && Number.isInteger(b.source_line_start) && Number.isInteger(b.source_line_end)
   && a.source_line_start>0 && a.source_line_end>=a.source_line_start
   && b.source_line_start>a.source_line_start && b.source_line_end>a.source_line_end;
 }
 function pause(reason = 'pause') {
  if(timer!==null) cancel(timer);
  timer=null; playing=false; stopReason=reason; publish();
 }
 function nextStop() {
  if(selected>=windows.length-1) return 'end';
  if(!sameSegment(windows[selected], windows[selected+1])) return 'boundary';
  return blocked();
 }
 function schedule() {
  const id=delay(()=>{
   if(timer!==id || !playing) return; // Ignore a cancelled/stale callback.
   timer=null;
   const reason=nextStop();
   if(reason) { pause(reason); return; }
   selected+=1;
   const after=nextStop();
   if(after) { pause(after); return; }
   publish(); schedule();
  }, 1000);
  timer=id;
 }
 function play() {
  if(playing) return;
  const reason=nextStop();
  if(reason) { pause(reason); return; }
  playing=true; stopReason=null; publish(); schedule();
 }
 function select(index) {
  if(!Number.isInteger(index) || index<0 || index>=windows.length) return;
  selected=index; pause('manual');
 }
 return {snapshot, play, pause, select,
  previous:()=>select(Math.max(0,selected-1)),
  next:()=>select(Math.min(windows.length-1,selected+1))};
}
"""


_PAGE = """<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>IBUKI Radar Explorer</title>
<style>
:root{color-scheme:light;--ink:#254842;--muted:#52665e;--line:#dce2d9;--paper:#fffef9;--sage:#e7eee5;--truth:#287269;--estimate:#a45424}
*{box-sizing:border-box}body{margin:0;background:#f5f3eb;color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI","Yu Gothic",sans-serif}
[hidden]{display:none!important}.safety{position:sticky;top:0;z-index:3;background:#e9eedf;border-bottom:1px solid #c4cebb;padding:10px 20px;text-align:center;font-size:13px;font-weight:700;letter-spacing:.03em}
.shell{max-width:1180px;margin:auto;padding:32px 28px 40px}.brand{font-size:13px;letter-spacing:.1em;color:var(--muted);margin:0 0 8px}h1{font-size:clamp(26px,3vw,38px);letter-spacing:-.035em;line-height:1.25;margin:0 0 12px}h2{font-size:20px;line-height:1.4;margin:0}p{margin:8px 0 16px}.lede{color:var(--muted);max-width:820px}.intro{padding:24px;border:1px solid var(--line);border-radius:16px;background:var(--paper);display:flex;align-items:center;justify-content:space-between;gap:24px;margin:24px 0}.intro p{margin:8px 0 0;max-width:650px}button,select{font:inherit}button{border:0;border-radius:9px;background:var(--ink);color:white;padding:12px 20px;font-weight:700;cursor:pointer;flex-shrink:0}button:hover{background:#356052}button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #ac6822;outline-offset:4px}a{color:var(--truth);text-underline-offset:3px}select{max-width:100%;min-width:0;background:var(--paper);border:1px solid #a5b4a7;border-radius:8px;color:var(--ink);padding:10px;width:100%}.toolbar{display:grid;grid-template-columns:1fr 1fr;align-items:end;gap:20px;margin:24px 0 18px}.toolbar label{font-weight:700;display:block;margin-bottom:6px}.toolbar p{color:var(--muted);font-size:14px;margin:0}.stats{display:flex;gap:20px;flex-wrap:wrap;padding:16px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:14px}.stats strong{font-size:23px;margin-right:5px}.grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(310px,1fr);gap:20px;align-items:start}.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:22px;min-width:0}.map-head{display:flex;justify-content:space-between;align-items:center;gap:12px}.map-head a{font-size:14px;white-space:nowrap}.hint{font-size:13px;color:var(--muted);margin:6px 0}.map{display:block;width:100%;height:auto;margin:8px 0;overflow:visible}.ring{fill:none;stroke:#d6dfd3;stroke-width:1.2}.axis{stroke:#c6d1c2;stroke-width:1;stroke-dasharray:4 5}.map text{font-family:inherit;fill:var(--muted);font-size:16px}.marker{cursor:pointer;outline:none}.marker .halo{fill:none;stroke:transparent;stroke-width:4}.marker[aria-pressed="true"] .halo{stroke:var(--ink);stroke-dasharray:3 3}.marker:focus-visible .halo{stroke:#ac6822;stroke-width:5}.legend{display:flex;gap:14px 20px;flex-wrap:wrap;font-size:14px}.legend span{display:flex;align-items:center;gap:8px}.dot{display:inline-block;width:15px;height:15px;border:3px solid var(--truth);border-radius:50%;flex:none}.diamond{display:inline-block;width:12px;height:12px;background:var(--estimate);transform:rotate(45deg);margin:0 3px;flex:none}.eyebrow{font-size:13px;color:var(--muted);margin:0 0 8px}.state{display:inline-block;font-size:13px;font-weight:700;padding:5px 10px;border-radius:6px;background:var(--sage);margin:12px 0}.notice{background:#f8eedc;border-left:3px solid #b97b34;padding:12px;font-size:14px;margin:12px 0 18px}.measure{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:18px 0}.measure>div{background:#f3f5ed;border-radius:9px;padding:12px}.measure h3{font-size:13px;margin:0 0 8px;color:var(--muted)}dl{margin:0}dt{font-size:13px;color:var(--muted)}dd{margin:0 0 6px;font-size:20px;font-weight:600}.difference{border-top:1px solid var(--line);padding-top:16px}.difference dl{display:grid;grid-template-columns:1fr auto;gap:5px 12px;align-items:baseline}.difference dd{font-size:18px}details{margin-top:20px;border-top:1px solid var(--line);padding-top:14px}summary{cursor:pointer;font-weight:600;font-size:14px}pre{font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0 0;max-height:340px;overflow:auto;background:#f0f2eb;padding:12px;border-radius:8px}.footnote{font-size:13px;color:var(--muted);margin-top:24px}.provenance{margin-top:24px}noscript p{padding:20px;background:#f8eedc}footer{margin-top:24px;font-size:13px;color:var(--muted)}
.map-head{flex-wrap:wrap}#inspection{scroll-margin-top:65px}
.playback{margin:0 0 20px;padding:18px 20px;background:var(--paper);border:1px solid var(--line);border-radius:12px}.playback-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.secondary{background:var(--sage);color:var(--ink);border:1px solid #a5b4a7}.secondary:hover{background:#d8e3d4}button:disabled{opacity:.5;cursor:default}button:disabled:hover{background:var(--ink)}button.secondary:disabled:hover{background:var(--sage)}.playback label{display:block;font-weight:700;margin-top:16px}input[type=range]{width:100%;margin:10px 0;accent-color:var(--truth);min-height:28px;cursor:pointer}input:focus-visible{outline:3px solid #ac6822;outline-offset:4px}.playback-status{font-size:14px;font-weight:700;margin:10px 0}.playback p:last-child{margin-bottom:0}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media(max-width:760px){.shell{padding:24px 18px}.intro{display:block;padding:20px}.intro button{width:100%;margin-top:20px}.toolbar{grid-template-columns:1fr;gap:10px}.grid{grid-template-columns:1fr}.card{padding:18px}.stats{gap:8px 18px}.stats strong{font-size:20px}.map text{font-size:19px}.legend{font-size:13px;gap:10px}h2{font-size:19px}.measure{gap:8px}dd{font-size:19px}.safety{font-size:12px;padding:9px 12px}.map-head a{font-size:13px}}
</style>
<div class="safety">シミュレーション / SYNTHETIC DATA · 実測ではありません</div>
<main class="shell">
<header><p class="brand">息吹 ｜ オフライン解析</p><h1>IBUKI Radar Explorer</h1>
<p class="lede">合成レーダー信号から求めた位置を、あらかじめ設定した位置と見比べるツールです。</p></header>
<section class="intro" id="welcome" aria-labelledby="intro-title"><div><h2 id="intro-title">位置の「設定」と「推定」を、見比べる。</h2>
<p>丸は合成の参照値、ひし形は既存の方向推定器の出力です。点や区間を選ぶと、その違いを確認できます。</p></div>
<button id="start" type="button" aria-controls="workspace">シミュレーションを開始</button></section>
<noscript><p>このファイルの操作には JavaScript が必要です。外部への通信は行いません。</p></noscript>
<section id="workspace" hidden aria-label="位置推定の比較">
<div class="stats" aria-label="評価区間の集計"><span><strong id="total">—</strong>区間</span><span><strong id="available">—</strong>推定あり</span><span><strong id="unavailable">—</strong>推定できません</span></div>
<div class="toolbar"><div><label for="window-select">調べる区間</label><select id="window-select"></select></div><p>計算済みの解析窓を選びます。隣り合う窓の観測期間は重なる場合があります。欠測や位置の間を補間しません。</p></div>
<section class="playback" aria-label="計算済み区間のデモ再生">
<div class="playback-actions"><button id="play" type="button">再生</button><button id="pause" class="secondary" type="button" disabled>一時停止</button><button id="previous" class="secondary" type="button">前へ</button><button id="next" class="secondary" type="button">次へ</button></div>
<p id="playback-status" class="playback-status" role="status" aria-live="polite">一時停止</p>
<label for="timeline">評価区間の目盛り</label><input id="timeline" type="range" min="0" max="0" step="1" value="0">
<p class="hint" id="time-readout"></p><p class="hint" id="playback-message"></p>
<p class="hint">デモ再生は目安 1 秒に 1 区間。実測時間の倍率ではありません。目盛りは区間番号で、実測時間に比例しません。</p>
</section>
<div class="grid">
<section class="card" aria-labelledby="map-title"><div class="map-head"><h2 id="map-title">レーダーから見た位置</h2><a href="#inspection">選択した結果を見る</a></div>
<p class="hint">上が前方、下が後方。距離は cm、レーダーが原点です。</p>
<svg id="map" class="map" viewBox="0 0 600 570" role="group" aria-labelledby="map-title map-description"><desc id="map-description">距離と水平方向のみの模式図。丸は設定位置、ひし形は推定位置です。点を選択できます。</desc><g id="guides"></g><g id="markers"></g></svg>
<div class="legend"><span><i class="dot" aria-hidden="true"></i>設定位置（合成の参照値）</span><span><i class="diamond" aria-hidden="true"></i>推定位置（アンテナ 0–1）</span></div>
<p class="hint">角度線は目安です。実機の視野角・部屋の座標・高さを示すものではありません。</p></section>
<section class="card" id="inspection" aria-labelledby="selection-title" tabindex="-1">
<p class="eyebrow" id="window-label"></p><h2 id="selection-title">選択した区間</h2><div id="status" class="state" role="status" aria-live="polite"></div>
<p class="notice" id="reason" hidden></p>
<div class="measure"><div><h3>設定（合成の参照値）</h3><dl><dt>距離</dt><dd id="truth-range"></dd><dt>角度</dt><dd id="truth-angle"></dd></dl></div>
<div><h3>推定（信号から計算）</h3><dl><dt>距離</dt><dd id="estimate-range"></dd><dt>角度</dt><dd id="estimate-angle"></dd></dl></div></div>
<div class="difference"><h3 class="eyebrow">参照値との差（絶対値）</h3><dl><dt>距離の差</dt><dd id="range-difference"></dd><dt>角度の差</dt><dd id="angle-difference"></dd><dt>位置の差</dt><dd id="position-difference"></dd></dl></div>
<p class="hint" id="window-info"></p>
<details><summary>アンテナの診断値</summary><p class="hint">アンテナ 0–2 は参考値で、位置に合成していません。I/Q は標準データセットに保持されています。</p><pre id="diagnostics"></pre></details>
</section></div></section>
<details class="provenance"><summary>検証・来歴と計算条件</summary><p class="hint">ハッシュは成果物の整合性確認であり、出所の真正性や実機の精度を証明しません。時刻はボード起動後の経過時間で、日時ではありません。</p><pre id="provenance"></pre></details>
<footer>理想化した単一の移動反射点による実験です。実機の校正・精度検証、人体追跡、乳児の呼吸精度や医療性能の検証ではありません。前後の方向を取り違える場合もあります。</footer>
</main>
<script type="application/json" id="explorer-data">__IBUKI_DATA__</script>
<script>
"use strict";
__IBUKI_PLAYBACK__
const data=JSON.parse(document.getElementById('explorer-data').textContent);
const windows=data.windows, byId=id=>document.getElementById(id), svgNS='http://www.w3.org/2000/svg';
const number=(v,digits=2)=>v===null||v===undefined?'—':Number(v).toFixed(digits);
const radius=p=>Math.hypot(p.x,p.y);
const maxRadius=windows.reduce((largest,w)=>Math.max(largest,radius(w.truth_position_cm),w.estimation_available?radius(w.position_cm):0),0);
const extent=Math.max(50,Math.ceil(maxRadius/25)*25)*1.18;
const scale=220/extent, xy=p=>[300+p.x*scale,285-p.y*scale];
function svg(tag,attributes,parent,text){const el=document.createElementNS(svgNS,tag);for(const [key,value] of Object.entries(attributes))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;parent.appendChild(el);return el;}
const guides=byId('guides');
for(let r=25;r<=extent;r+=25){svg('circle',{cx:300,cy:285,r:r*scale,class:'ring'},guides);svg('text',{x:306+r*scale*.8,y:280-r*scale*.6},guides,`${r} cm`);}
svg('line',{x1:300,y1:55,x2:300,y2:515,class:'axis'},guides);
svg('line',{x1:65,y1:285,x2:535,y2:285,class:'axis'},guides);
for(const degree of [-60,-30,30,60]){const rad=degree*Math.PI/180;svg('line',{x1:300,y1:285,x2:300+220*Math.sin(rad),y2:285-220*Math.cos(rad),class:'axis'},guides);}
svg('text',{x:300,y:28,'text-anchor':'middle'},guides,'↑ 前方（0°）');
svg('text',{x:300,y:555,'text-anchor':'middle'},guides,'後方');
svg('text',{x:56,y:315,'text-anchor':'middle'},guides,'左');svg('text',{x:544,y:315,'text-anchor':'middle'},guides,'右');
svg('path',{d:'M300 272 L289 292 L311 292 Z',fill:'#254842'},guides);
svg('text',{x:300,y:322,'text-anchor':'middle'},guides,'レーダー（原点）');
const markerElements=[];
function marker(position,index,isEstimate){
 const [x,y]=xy(position),group=svg('g',{class:'marker',role:'button',tabindex:'0','data-window':index,'data-kind':isEstimate?'estimate':'truth','aria-label':`区間 ${index+1} の${isEstimate?'推定位置':'設定位置'}`,'aria-pressed':'false'},byId('markers'));
 svg('circle',{cx:x,cy:y,r:21,class:'halo'},group);
 if(isEstimate)svg('path',{d:`M${x} ${y-10} l10 10 l-10 10 l-10 -10 Z`,fill:'#a45424',stroke:'#fffef9','stroke-width':2},group);
 else svg('circle',{cx:x,cy:y,r:14,fill:'#fffef9',stroke:'#287269','stroke-width':3},group);
 group.addEventListener('click',()=>player.select(index));
 group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();player.select(index);}});
 markerElements.push({group,index});
}
windows.forEach((w,index)=>{const option=document.createElement('option');option.value=index;option.textContent=`区間 ${index+1} · ${w.estimation_available?'推定あり':'推定できません'}`;byId('window-select').appendChild(option);marker(w.truth_position_cm,index,false);if(w.estimation_available)marker(w.position_cm,index,true);});
const reasons={insufficient_moving_signal:'動きの信号が小さく、位置を推定できません。',low_pair_01_coherence:'アンテナ 0–1 の信号の対応が弱いため、位置を推定できません。',insufficient_samples_for_window:'この区間は解析に必要な観測数が足りません。'};
function renderSelection(state){
 const index=state.selected;
 const w=windows[index],has=w.estimation_available;
 byId('window-select').value=index;
 byId('timeline').value=index;
 byId('play').disabled=state.playing||index===windows.length-1||reducedMotion.matches;
 byId('pause').disabled=!state.playing;
 byId('previous').disabled=index===0;
 byId('next').disabled=index===windows.length-1;
 byId('playback-status').textContent=`${state.playing?'再生中':'一時停止'} · 区間 ${index+1} / ${windows.length}`;
 const messages={initial:'再生は自動で始まりません。',pause:'表示を一時停止しました。',manual:'手動で区間を選びました。再生は停止中です。',end:'最後の区間で停止しました。',boundary:'データの区切りで停止しました。「次へ」や目盛りで別の区間を選べます。',hidden:'ページが非表示になったため停止しました。戻っても自動では再開しません。',reduced:'動きを抑える設定のため、自動再生は無効です。前後のボタンや目盛りで選べます。'};
 byId('playback-message').textContent=reducedMotion.matches?messages.reduced:(messages[state.stopReason]||'同じ連続区間の中で、計算済みの窓を順に表示しています。');
 for(const m of markerElements){m.group.setAttribute('aria-pressed',String(m.index===index));m.group.style.opacity=m.index===index?'1':'.35';}
 // Raise selected markers without connecting points or interpolating gaps.
 for(const m of markerElements.filter(m=>m.index===index))byId('markers').appendChild(m.group);
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
 byId('window-info').textContent=`観測 ${w.observed_samples} フレーム · 元の行 ${w.source_line_start}–${w.source_line_end} · 基準は起動後 ${number(w.reference_board_uptime_ms/1000)} 秒`;
 byId('diagnostics').textContent=JSON.stringify({unavailable_reason:w.unavailable_reason,configured_range_bin:w.configured_range_bin,estimated_range_bin:w.estimated_range_bin,raw_estimate:w.raw_estimate,pair_01_coherence:w.pair_01_coherence??null,mean_movement:w.mean_movement??null},null,2);
}
byId('provenance').textContent=JSON.stringify({synthetic:data.synthetic,hardware_validated:data.hardware_validated,source_type:data.source_type,source_type_provenance:data.source_type_provenance,input_sha256:data.input_sha256,verified_artifact_hashes:data.verified_artifact_hashes,explorer:data.explorer,parameters:data.parameters,data_quality:data.data_quality,simulation_configuration:data.simulation.configuration,antenna_x_wavelengths:data.simulation.antenna_x_wavelengths},null,2);
byId('total').textContent=data.summary.total_windows;byId('available').textContent=data.summary.available_windows;byId('unavailable').textContent=data.summary.unavailable_windows;
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
const player=createPlayback(windows,renderSelection,{blockedReason:()=>document.hidden?'hidden':reducedMotion.matches?'reduced':null});
byId('timeline').max=windows.length-1;
byId('window-select').addEventListener('change',event=>player.select(Number(event.target.value)));
byId('timeline').addEventListener('input',event=>player.select(Number(event.target.value)));
byId('play').addEventListener('click',()=>player.play());
byId('pause').addEventListener('click',()=>player.pause());
byId('previous').addEventListener('click',()=>player.previous());
byId('next').addEventListener('click',()=>player.next());
document.addEventListener('visibilitychange',()=>{if(document.hidden)player.pause('hidden');});
window.addEventListener('pagehide',()=>player.pause('hidden'));
reducedMotion.addEventListener('change',()=>{if(reducedMotion.matches)player.pause('reduced');else renderSelection(player.snapshot());});
byId('start').addEventListener('click',()=>{byId('workspace').hidden=false;byId('welcome').hidden=true;player.select(0);byId('window-select').focus();});
</script>
</html>"""


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="spatial_sim case directory containing canonical/ and truth.json")
    parser.add_argument("--output", required=True, help="new .html under ignored analysis_runs/; parent must exist")
    args = parser.parse_args(argv)
    try:
        target = render_explorer(args.case, args.output)
    except (OSError, ValueError, ImportError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved offline synthetic Explorer: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
