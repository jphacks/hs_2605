"""Idealized synthetic E measurements; not physical radar or medical validation."""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import math

import numpy as np

from ml.antennas import ANTENNAS, FIRST_CM, NUM_BINS, STEP_CM


@dataclass(frozen=True)
class Simulation:
    range_bin: int = 9
    angle_deg: float = 15
    end_angle_deg: float | None = None
    motion_hz: float = .5
    motion_amplitude: float = 200
    background: float = 1000
    noise_std: float = 0
    seed: int = 7
    duration_sec: float = 12
    sample_ms: int = 50
    baseline_02_lambda: float = 1

    def validate(self):
        if type(self.range_bin) is not int or not 0 <= self.range_bin < NUM_BINS:
            raise ValueError("range_bin must be an integer in 0..23")
        for value in (self.angle_deg,self.end_angle_deg if self.end_angle_deg is not None else self.angle_deg):
            if type(value) not in (int,float) or not math.isfinite(value) or not -180 <= value <= 180:
                raise ValueError("angles must be finite and in -180..180 degrees")
        for key in ('motion_hz','motion_amplitude','background','noise_std'):
            value=getattr(self,key)
            if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        for key in ('duration_sec','baseline_02_lambda'):
            value=getattr(self,key)
            if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be finite and positive")
        if type(self.seed) is not int or self.seed < 0 or type(self.sample_ms) is not int or self.sample_ms <= 0:
            raise ValueError("seed must be nonnegative integer; sample_ms must be positive integer")
        if self.motion_hz >= 500/self.sample_ms:
            raise ValueError('motion_hz must be below the configured sampling Nyquist frequency')


def encode_e(z, times):
    """Encode antenna-major/bin-major interleaved signed int16 LE; never clip."""
    z=np.asarray(z)
    if z.ndim != 3 or z.shape[1:] != (ANTENNAS,NUM_BINS) or len(z)==0 or not np.isfinite(z).all():
        raise ValueError("E input must be finite nonempty (frames,3,24) complex I/Q")
    if len(times)!=len(z) or any(type(t) is not int or not 0 <= t <= 2**63-1 for t in times):
        raise ValueError("E times must match frames and be nonnegative int64 uptime")
    if any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError("Synthetic E timestamps must strictly increase")
    iq=np.stack((np.rint(z.real),np.rint(z.imag)),axis=-1)
    if np.any(iq < -32768) or np.any(iq > 32767):
        raise ValueError("Synthetic I/Q exceeds int16 wire range; reduce amplitudes/noise (no clipping)")
    wire=iq.astype('<i2')
    return ''.join(f'E,{k},{t},'+base64.b64encode(frame.tobytes()).decode('ascii')+'\n'
                   for k,(t,frame) in enumerate(zip(times,wire))).encode('ascii')


def generate(config: Simulation):
    """One moving bin, linear complex-amplitude modulation and ideal steering."""
    config.validate()
    n=math.ceil(config.duration_sec*1000/config.sample_ms)
    times=[100000+k*config.sample_ms for k in range(n)]
    seconds=np.arange(n)*config.sample_ms/1000
    angles=np.linspace(config.angle_deg,config.angle_deg if config.end_angle_deg is None else config.end_angle_deg,n)
    positions=np.array([0,.5,config.baseline_02_lambda])
    steering=np.exp(2j*np.pi*np.sin(np.radians(angles))[:,None]*positions)
    rng=np.random.default_rng(config.seed)
    clutter=config.background*np.exp(1j*rng.uniform(-np.pi,np.pi,(3,24)))
    z=np.broadcast_to(clutter,(n,3,24)).copy()
    motion=config.motion_amplitude*np.sin(2*np.pi*config.motion_hz*seconds)*np.exp(.7j)
    z[:,:,config.range_bin] += motion[:,None]*steering
    z += config.noise_std*(rng.normal(size=z.shape)+1j*rng.normal(size=z.shape))
    csv=b'C,15,37.5,24,10\n'+encode_e(z,times)
    truth=dict(format='ibuki.radar.synthetic_spatial_truth',version=1,synthetic=True,hardware_validated=False,
        input_sha256=hashlib.sha256(csv).hexdigest(),configuration=asdict(config),
        antenna_x_wavelengths=positions.tolist(),antenna_y_wavelengths=[0,0,0],
        geometry=dict(first_cm=FIRST_CM,step_cm=STEP_CM,bin_count=24,elevation_deg=0),
        board_uptime_ms=times,angle_deg=angles.tolist(),range_bin=config.range_bin,
        model='z=B + A*sin(2*pi*f*t)*exp(0.7j)*exp(2j*pi*x_lambda*sin(theta)) at one bin + Gaussian I/Q noise',
        limitations='Ideal uncalibrated collinear far-field array; no path loss, multipath, elevation or multi-person separation',
        numpy_version=np.__version__)
    return csv,truth


def estimate_window(z, *, first_cm, step_cm, min_movement=1, min_coherence=.5):
    """Wrap the unchanged estimator with explicit measured-data availability guards."""
    from ml.antennas import chest_direction

    try:
        z=np.asarray(z,dtype=np.complex128)
    except (TypeError,ValueError) as exc:
        raise ValueError("Direction input must be numeric (frames,3,24) I/Q") from exc
    if z.ndim!=3 or z.shape[1:]!=(3,24) or not len(z) or not np.isfinite(z).all():
        raise ValueError("Direction input must be finite nonempty (frames,3,24) I/Q")
    if np.any(z.real < -32768) or np.any(z.real > 32767) or np.any(z.imag < -32768) or np.any(z.imag > 32767):
        raise ValueError('Direction input must be decoded int16 E value units')
    if not math.isfinite(min_movement) or min_movement<0 or not math.isfinite(min_coherence) or not 0<=min_coherence<=1:
        raise ValueError("Availability guards require movement >= 0 and coherence in 0..1")
    out=dict(estimation_available=False,unavailable_reason=None,raw_estimate=None,
             candidate_range_bin=None,estimated_range_bin=None,estimated_angle_deg=None,
             pair_01_coherence=None,mean_movement=None,position_cm=None)
    if first_cm!=FIRST_CM or step_cm!=STEP_CM:
        out['unavailable_reason']='missing_or_incompatible_geometry: estimator hardcodes 15 cm + bin*3.75 cm'
        return out
    if len(z)<8:
        out['unavailable_reason']='insufficient_samples (minimum 8)'
        return out
    with np.errstate(over='ignore',invalid='ignore'):
        raw=chest_direction(z)
        k=int(round((raw['slot_cm']-FIRST_CM)/STEP_CM))
        moving=z-z.mean(axis=0)
        movement=float(np.abs(moving[:,:,k]).mean())
        cross=np.sum(moving[:,1,k]*np.conj(moving[:,0,k]))
        denominator=float(np.sqrt(np.sum(np.abs(moving[:,1,k])**2)*np.sum(np.abs(moving[:,0,k])**2)))
        coherence=float(min(1,abs(cross)/denominator)) if denominator>0 and math.isfinite(denominator) else None
    out.update(raw_estimate=raw,candidate_range_bin=k,mean_movement=movement,pair_01_coherence=coherence)
    if not math.isfinite(movement):
        raise ValueError('Direction arithmetic overflowed')
    if raw['slot_cm']<28 or movement<=min_movement:
        out['unavailable_reason']='insufficient_moving_signal'
    elif coherence is None or coherence<min_coherence:
        out['unavailable_reason']='low_pair_01_coherence'
    else:
        radius=FIRST_CM+k*STEP_CM
        angle=raw['angle_0_1_deg']
        out.update(estimation_available=True,estimated_range_bin=k,estimated_angle_deg=angle,
                   position_cm=dict(x=radius*math.sin(math.radians(angle)),y=radius*math.cos(math.radians(angle))))
    return out


def evaluate(dataset, truth, *, window_frames=80, hop_frames=20, min_movement=1, min_coherence=.5):
    """Evaluate decoded E measurements, never substitute truth for estimator input."""
    from .report import prepare_blocks

    if any(type(v) is not int or v<=0 for v in (window_frames,hop_frames)) or hop_frames>window_frames:
        raise ValueError('Window/hop frames must be positive integers, hop <= window')
    if (not isinstance(truth,dict) or truth.get('format')!='ibuki.radar.synthetic_spatial_truth'
            or type(truth.get('version')) is not int or truth['version']!=1
            or truth.get('synthetic') is not True or truth.get('hardware_validated') is not False):
        raise ValueError('Require version-1 explicitly synthetic spatial truth')
    m=dataset.manifest
    if m['source_type']!='synthetic' or truth.get('input_sha256')!=m['input']['sha256']:
        raise ValueError('Synthetic provenance/input hash mismatch')
    angles,times=truth.get('angle_deg'),truth.get('board_uptime_ms')
    if not isinstance(angles,list) or not isinstance(times,list) or len(angles)!=len(dataset.frames) or len(times)!=len(angles):
        raise ValueError('Synthetic truth must match every E frame')
    if any(type(a) not in (int,float) or not math.isfinite(a) or not -180<=a<=180 for a in angles):
        raise ValueError('Invalid synthetic truth angles')
    if any(r['message_type']!='E' or type(t) is not int or t!=r['board_uptime_ms'] for r,t in zip(dataset.frames,times)):
        raise ValueError('Truth timestamps must align exactly to E frames')
    k=truth.get('range_bin')
    positions=truth.get('antenna_x_wavelengths')
    if (type(k) is not int or not 0<=k<24 or not isinstance(positions,list) or len(positions)!=3
            or positions[:2]!=[0,.5] or type(positions[2]) not in (int,float)
            or not math.isfinite(positions[2]) or positions[2]<=0
            or truth.get('geometry')!=dict(first_cm=FIRST_CM,step_cm=STEP_CM,bin_count=24,elevation_deg=0)):
        raise ValueError('Unsupported synthetic range/primary baseline truth')
    windows=[]
    offset=0
    for parent_index,parent in enumerate(prepare_blocks(dataset,0),1):
        rows=dataset.frames[offset:offset+parent['frame_count']]
        data=parent['data']
        for trace_index,section in enumerate(data.trace_segments if data is not None else (slice(0,len(rows)),),1):
            observed=rows[section]
            starts=range(0,len(observed)-window_frames+1,hop_frames) if len(observed)>=window_frames else (0,)
            for left in starts:
                sample=observed[left:left+window_frames]
                center=offset+section.start+left+(len(sample)-1)//2
                angle=angles[center]
                radius=FIRST_CM+k*STEP_CM
                position=dict(x=radius*math.sin(math.radians(angle)),y=radius*math.cos(math.radians(angle)))
                if data is None:
                    estimate=dict(estimation_available=False,unavailable_reason=parent['reason'],raw_estimate=None,
                                  estimated_angle_deg=None,estimated_range_bin=None,position_cm=None)
                else:
                    z=np.array([np.asarray(r['raw_i'])+1j*np.asarray(r['raw_q']) for r in sample])
                    estimate=estimate_window(z,first_cm=sample[0]['first_cm'],step_cm=sample[0]['step_mm']/10,
                                             min_movement=min_movement,min_coherence=min_coherence)
                    if len(sample)<window_frames:
                        estimate.update(estimation_available=False,unavailable_reason='insufficient_samples_for_window',
                                        estimated_angle_deg=None,estimated_range_bin=None,position_cm=None)
                error=abs((estimate['estimated_angle_deg']-angle+180)%360-180) if estimate['estimation_available'] else None
                position_error=math.hypot(estimate['position_cm']['x']-position['x'],estimate['position_cm']['y']-position['y']) if estimate['estimation_available'] else None
                windows.append(dict(window_index=len(windows),parent_block_index=parent_index,trace_index=trace_index,
                    source_line_start=sample[0]['source_line'],source_line_end=sample[-1]['source_line'],
                    frame_row_start=offset+section.start+left,frame_row_end_exclusive=offset+section.start+left+len(sample),
                    truth_frame_index=center,reference_board_uptime_ms=times[center],observed_samples=len(sample),
                    configured_angle_deg=angle,configured_range_bin=k,truth_position_cm=position,
                    truth_outside_front_hemisphere=abs(angle)>90,absolute_angular_error_deg=error,
                    position_error_cm=position_error,**estimate))
        offset+=len(rows)
    accepted=[w for w in windows if w['estimation_available']]
    reasons={}
    for w in windows:
        if not w['estimation_available']:
            reason=w['unavailable_reason']
            reasons[reason]=reasons.get(reason,0)+1
    return dict(format='ibuki.radar.synthetic_spatial_evaluation',version=1,synthetic=True,hardware_validated=False,
        input_sha256=m['input']['sha256'],source_type=m['source_type'],source_type_provenance=m['source_type_provenance'],
        verified_artifact_hashes=m['artifacts'],data_quality=dataset.quality,simulation=truth,
        parameters=dict(window_frames=window_frames,hop_frames=hop_frames,min_movement=min_movement,min_coherence=min_coherence,
            guards='Illustrative measurement-only guards, not calibrated confidence or physiological thresholds',
            estimator='unchanged ml.antennas.chest_direction; min_cm=28',position_baseline='0-1 only',
            pair_02='diagnostic only, not fused; raw estimator assumes 0.5 lambda; compare with declared baseline',
            ambiguity='Single collinear sine-phase geometry cannot resolve front/back or wrapped baselines',
            truth_alignment='earlier middle observed frame of each end-exclusive window; not used by estimator'),
        summary=dict(total_windows=len(windows),available_windows=len(accepted),unavailable_windows=len(windows)-len(accepted),
            unavailable_reasons=reasons,range_bin_hits=sum(w['estimated_range_bin']==k for w in accepted),
            mean_absolute_angular_error_deg=float(np.mean([w['absolute_angular_error_deg'] for w in accepted])) if accepted else None,
            max_absolute_angular_error_deg=max((w['absolute_angular_error_deg'] for w in accepted),default=None),
            mean_position_error_cm=float(np.mean([w['position_error_cm'] for w in accepted])) if accepted else None),windows=windows)


def build_figure(result):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure=Figure(figsize=(12,6),layout='constrained')
    FigureCanvasAgg(figure)
    spatial,angles=figure.subplots(1,2,gridspec_kw={'width_ratios':[1.1,1]})
    truth=result['simulation']
    radius=FIRST_CM+truth['range_bin']*STEP_CM
    limit=max(25,math.ceil(radius/25)*25)*1.2
    a=np.linspace(-np.pi,np.pi,361)
    for r in np.arange(25,limit,25):
        spatial.plot(r*np.sin(a),r*np.cos(a),color='#cbd5df',linewidth=.7)
        spatial.text(r*math.sin(math.radians(70)),r*math.cos(math.radians(70)),f'{r:g} cm',fontsize=8,color='#64748b')
    for degrees in (-60,-30,0,30,60):
        radians=math.radians(degrees)
        spatial.plot([0,limit*np.sin(radians)],[0,limit*np.cos(radians)],color='#cbd5df',linestyle=':',linewidth=.8)
        spatial.text(.9*limit*np.sin(radians),.9*limit*np.cos(radians),f'{degrees:+g}°',fontsize=8,color='#64748b')
    az=np.radians(truth['angle_deg'])
    spatial.plot(radius*np.sin(az),radius*np.cos(az),'o--',color='#137f73',markerfacecolor='none',
                 markersize=10,markevery=max(1,len(az)//12),label='GROUND TRUTH (SIMULATED)')
    spatial.scatter([0],[0],marker='^',s=85,color='#243b53',label='Radar (ideal origin)')
    available=[w for w in result['windows'] if w['estimation_available']]
    groups={}
    for w in available: groups.setdefault((w['parent_block_index'],w['trace_index']),[]).append(w)
    for index,group in enumerate(groups.values()):
        spatial.plot([w['position_cm']['x'] for w in group],[w['position_cm']['y'] for w in group],
                     'x-',color='#d56b22',label='ESTIMATED POSITION (0-1)' if index==0 else '_nolegend_',markersize=8)
    spatial.set(xlim=(-limit,limit),ylim=(-limit,limit),xlabel='Lateral x (cm)',ylabel='Forward y (cm)',
                title='Top-down range/azimuth only; no elevation\n±60° guides are illustrative, not hardware FoV')
    spatial.set_aspect('equal')
    spatial.legend(fontsize=8,loc='lower left')
    origin=truth['board_uptime_ms'][0] if truth['board_uptime_ms'] else 0
    time=[(w['reference_board_uptime_ms']-origin)/1000 for w in result['windows']]
    angles.plot(time,[w['configured_angle_deg'] for w in result['windows']],'--',color='#137f73',label='SIMULATED truth')
    angles.plot(time,[w['estimated_angle_deg'] if w['estimation_available'] else np.nan for w in result['windows']],
                'x-',color='#d56b22',label='Accepted 0-1 estimates')
    angles.plot(time,[w['raw_estimate']['angle_0_2_deg'] if w['raw_estimate'] else np.nan for w in result['windows']],
                ':',color='#7a59a5',label='Raw 0-2 diagnostic (not fused)')
    angles.set(xlabel='Observed window reference offset (s)',ylabel='Angle (degrees)',
               title=f"{result['summary']['available_windows']}/{result['summary']['total_windows']} available windows\nUnavailable windows: JSON only, no accepted position")
    angles.grid(alpha=.2)
    angles.legend(fontsize=8)
    figure.suptitle('SYNTHETIC DATA — idealized algorithm experiment\nNo physical radar validation, body tracking or medical claim',fontsize=14)
    return figure


def _json(value):
    return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8')


def run_demo(directory, config, *, scenario='single', window_frames=80, hop_frames=20):
    """Fresh output only: generate -> E CSV -> canonical decode -> estimator -> PNG."""
    from pathlib import Path
    from .ingest import ingest_recording
    from .report import load_dataset

    output=Path(directory)
    if output.exists() or output.is_symlink():
        raise FileExistsError('Demo output exists; choose a fresh directory (no overwrite)')
    config.validate()
    if any(type(v) is not int or v<=0 for v in (window_frames,hop_frames)) or hop_frames>window_frames:
        raise ValueError('Window/hop frames must be positive integers, hop <= window')
    configurations=[]
    if scenario=='angles':
        from dataclasses import replace
        configurations=[replace(config,angle_deg=a,end_angle_deg=None) for a in (-30,-15,0,15,30)]
    else:
        from dataclasses import replace
        changes={'single':{},'sweep':dict(angle_deg=-30,end_angle_deg=30),
                 'noisy':dict(motion_amplitude=2,noise_std=40),'constant':dict(motion_amplitude=0,noise_std=0),
                 'ambiguous':dict(angle_deg=150,end_angle_deg=None)}
        if scenario not in changes: raise ValueError('Unknown simulation scenario')
        configurations=[replace(config,**changes[scenario])]
    generated=[generate(c) for c in configurations]  # Validate before creating output.
    output.mkdir(parents=True,exist_ok=False)
    summaries=[]
    for index,((csv,truth),cfg) in enumerate(zip(generated,configurations)):
        case=output/f'case_{index:02d}'
        case.mkdir()
        source=case/'recording.csv'
        source.write_bytes(csv)
        (case/'truth.json').write_bytes(_json(truth))
        ingest_recording(source,case/'canonical',source_type='synthetic',scenario=scenario,
                         scenario_provenance='ml.analysis.spatial_sim/v1; ideal uncalibrated array; simulation-only truth.json')
        result=evaluate(load_dataset(case/'canonical'),truth,window_frames=window_frames,hop_frames=hop_frames)
        figure=build_figure(result)
        try: figure.savefig(case/'spatial.png',format='png',dpi=120,metadata={'Software':'IBUKI offline analysis'})
        finally: figure.clear()
        (case/'evaluation.json').write_bytes(_json(result))
        summaries.append(dict(case=f'case_{index:02d}',configuration=asdict(cfg),**result['summary']))
    summary=dict(synthetic=True,hardware_validated=False,scenario=scenario,cases=summaries)
    (output/'summary.json').write_bytes(_json(summary))
    return summary


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',required=True,help='fresh directory under ignored analysis_runs/')
    parser.add_argument('--scenario',choices=('single','angles','sweep','noisy','constant','ambiguous'),default='angles')
    for name,kind,default in (('range-bin',int,9),('angle-deg',float,15),('end-angle-deg',float,None),
                             ('motion-hz',float,.5),('motion-amplitude',float,200),('background',float,1000),
                             ('noise-std',float,0),('seed',int,7),('duration-sec',float,12),('sample-ms',int,50),
                             ('baseline-02-lambda',float,1),('window-frames',int,80),('hop-frames',int,20)):
        parser.add_argument('--'+name,type=kind,default=default)
    args=vars(parser.parse_args(argv))
    directory,scenario=args.pop('out_dir'),args.pop('scenario')
    window,hop=args.pop('window_frames'),args.pop('hop_frames')
    try:
        result=run_demo(directory,Simulation(**args),scenario=scenario,window_frames=window,hop_frames=hop)
    except (OSError,ValueError,ImportError,OverflowError) as exc:
        parser.exit(2,f'error: {exc}\n')
    print(json.dumps(result,sort_keys=True,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
