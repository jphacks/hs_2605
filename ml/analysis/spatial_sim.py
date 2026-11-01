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
