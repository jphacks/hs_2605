import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from scipy.signal import periodogram

from ml.analysis.ingest import ingest_recording
from ml.analysis.report import load_dataset
from ml.analysis.schema import DATASET_FILES
from ml.analysis.signal import project_iq, sampling_cadence
from ml.analysis.spectrogram import build_figure, build_spectrogram, export_spectrogram

ROOT = Path(__file__).resolve().parents[3]


class SpectrogramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.dataset = self.root/'capture.csv', self.root/'dataset'
        self.output = self.root/'stft.png'

    def capture(self, rows):
        self.source.write_text('\n'.join(rows)+'\n' if rows else '', encoding='utf-8')
        ingest_recording(self.source,self.dataset,force=self.dataset.exists(),source_type='synthetic')

    def sinusoid(self, frequency=.5, step=50, seconds=20, start=100000):
        rows=['C,15,37.5,1,100']
        for k,t in enumerate(range(0,seconds*1000,step)):
            f = frequency if not callable(frequency) else frequency(t/1000)
            value = round(10000*np.sin(2*np.pi*f*t/1000))
            rows.append(f'D,{k},{start+t},{value},0')
        self.capture(rows)

    def analyze(self, **kwargs):
        return build_spectrogram(load_dataset(self.dataset),**kwargs)

    def test_frequency_localization_measured_rates_and_psd_normalization(self):
        for step in (25,50,100):
            with self.subTest(step=step):
                self.sinusoid(step=step)
                result=self.analyze(window_sec=4,hop_sec=1)
                s=result['segments'][0]
                self.assertEqual(s['status'],'available')
                self.assertEqual(s['sampling_rate_estimate_hz'],1000/step)
                self.assertEqual(s['frequency_spacing_hz'],.25)
                power=np.array(s['power_density'])
                frequencies=np.array(s['frequency_hz'])
                np.testing.assert_allclose(frequencies[power.argmax(axis=0)],.5)
                n=s['window_samples']
                z=np.array([complex(round(10000*np.sin(2*np.pi*.5*k*step/1000))/100,0)
                            for k in range(20000//step)])
                wave=project_iq(z)
                _,expected=periodogram(wave[:n],fs=1000/step,window='hann',detrend=False,scaling='density')
                np.testing.assert_allclose(power[:,0],expected,rtol=1e-10,atol=1e-12)
                self.assertAlmostEqual(float(power[:,0].sum()*.25),5000,delta=1)

    def test_changing_frequency_over_time(self):
        self.sinusoid(frequency=lambda t:.5 if t<16 else 1.5,seconds=32)
        s=self.analyze(window_sec=4,hop_sec=1)['segments'][0]
        peaks=np.array(s['frequency_hz'])[np.array(s['power_density']).argmax(axis=0)]
        centers=np.array([w['center_offset_s'] for w in s['windows']])
        np.testing.assert_allclose(peaks[centers<12],.5)
        np.testing.assert_allclose(peaks[centers>20],1.5)

    def test_actual_timestamps_large_uptime_and_rounding(self):
        start=2**60
        self.sinusoid(step=60,start=start)
        s=self.analyze(window_sec=1,hop_sec=.3)['segments'][0]
        self.assertEqual(s['window_samples'],17)
        self.assertEqual(s['hop_samples'],5)
        self.assertEqual(s['reference_board_uptime_ms'],start)
        self.assertEqual(s['windows'][0]['first_board_uptime_ms'],start)
        self.assertEqual(s['windows'][0]['last_board_uptime_ms'],start+960)
        self.assertEqual(s['windows'][0]['center_offset_s'],.48)
        self.assertAlmostEqual(s['effective_window_sec'],1.02)
        z=np.array([complex(round(10000*np.sin(2*np.pi*.5*k*.06))/100,0) for k in range(len(s['source_lines']))])
        _,expected=periodogram(project_iq(z)[:17],fs=1000/60,window='hann',detrend=False,scaling='density')
        np.testing.assert_allclose(np.array(s['power_density'])[:,0],expected,rtol=1e-10,atol=1e-12)
        self.assertEqual(sampling_cadence([start,start+60,start+120])['sampling_interval_ms'],60)
        for times in ([],[1],[1,1],[2,1]):
            with self.assertRaises(ValueError): sampling_cadence(times)

    def test_irregular_time_rejects_spectrum_not_interpolates(self):
        times=[k*50+(10 if k%2 else 0) for k in range(80)]
        self.capture(['C,15,37.5,1,10']+[f'D,{k},{t},{k},0' for k,t in enumerate(times)])
        s=self.analyze(window_sec=1,hop_sec=.5)['segments'][0]
        self.assertIn('irregular_timing',s['unavailable_reason'])
        self.assertIsNone(s['power_density'])
        self.assertIsNone(s['frequency_hz'])
        self.assertEqual(s['covered_frames'],0)
        self.assertGreater(s['cadence']['max_interval_deviation_ms'],s['cadence']['interval_tolerance_ms'])

    def test_empty_short_and_constant(self):
        self.capture([])
        result=self.analyze()
        self.assertEqual(result['segments'],[])
        self.assertEqual(result['unavailable_reason'],'No exported frames')
        for n in (1,5,20):
            self.capture(['C,15,37.5,1,10']+[f'D,{k},{k*50},10,20' for k in range(n)])
            s=self.analyze()['segments'][0]
            self.assertIn('insufficient_samples',s['unavailable_reason'])
            self.assertIsNone(s['power_density'])
        self.capture(['C,15,37.5,1,10']+[f'D,{k},{k*50},10,20' for k in range(80)])
        s=self.analyze(window_sec=1,hop_sec=.5)['segments'][0]
        self.assertTrue(s['constant_signal'])
        np.testing.assert_array_equal(s['power_density'],np.zeros((11,7)))

    def test_gaps_diagnostics_restarts_and_config_boundaries(self):
        rows=['C,15,37.5,1,10']
        for block in range(4):
            if block==1: rows.append('D,bad,1000,1,2')
            if block==2: rows.append('S,3000,radar restart: fifo')
            if block==3: rows.append('C,20,50,1,2')
            for k in range(30):
                seq=block*40+k if block<2 else k
                time=block*2000+k*50 if block<2 else k*50
                rows.append(f'D,{seq},{time},{k},0')
        self.capture(rows)
        result=self.analyze(window_sec=1,hop_sec=.5)
        self.assertEqual(len(result['segments']),4)
        self.assertTrue(all(s['status']=='available' for s in result['segments']))
        for segment in result['segments']:
            for window in segment['windows']:
                self.assertGreaterEqual(window['frame_row_start'],segment['frame_row_start'])
                self.assertLessEqual(window['frame_row_end_exclusive'],segment['frame_row_end_exclusive'])
        self.assertEqual(result['data_quality']['explicit_restart_status_count'],1)
        self.assertEqual(result['segments'][-1]['value_divisor'],2)

    def test_e_scale_antenna_and_missing_or_mismatched_geometry(self):
        rows=['C,15,37.5,24,10']
        for k in range(80):
            value=round(100*np.sin(2*np.pi*.5*k*.05))
            values=[value,0]*24+[k*100,1000]*48
            rows.append(f'E,{k},{k*50},'+base64.b64encode(struct.pack('<144h',*values)).decode())
        self.capture(rows)
        result=self.analyze(window_sec=2,hop_sec=1)
        s=result['segments'][0]
        self.assertEqual((s['value_divisor'],s['recorded_config_scale']),(1,10))
        self.assertEqual(s['recorded_antenna_count'],3)
        window=np.hanning(41)[:-1]  # Periodic Hann, one sine cycle in this window.
        wave=np.array([round(100*np.sin(2*np.pi*.5*k*.05)) for k in range(40)])
        weighted_energy=float(np.sum((wave*window)**2)/np.sum(window**2))
        self.assertAlmostEqual(np.array(s['power_density'])[:,0].sum()*s['frequency_spacing_hz'],weighted_energy,places=8)
        for bad in ([rows[1]],['C,15,37.5,2,10','D,1,0,1,2']):
            self.capture(bad)
            s=self.analyze()['segments'][0]
            self.assertIsNone(s['power_density'])
            self.assertIn('geometry',s['unavailable_reason'])

    def test_invalid_bins_and_parameters(self):
        self.sinusoid()
        for index in (-1,1,True,1.2):
            with self.subTest(index=index),self.assertRaises(ValueError): self.analyze(bin_index=index)
        for window,hop in ((0,1),(float('nan'),1),(1,2),(1,0),(True,1)):
            with self.assertRaises(ValueError): self.analyze(window_sec=window,hop_sec=hop)
        self.assertIn('too_short',self.analyze(window_sec=.05,hop_sec=.01)['segments'][0]['unavailable_reason'])

    def test_png_json_determinism_input_safety_and_cli(self):
        self.sinusoid(seconds=8)
        paths=[self.source]+[self.dataset/name for name in DATASET_FILES]
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths}
        result=export_spectrogram(self.dataset,self.output,window_sec=2,hop_sec=1)
        a,b=self.output.read_bytes(),self.output.with_suffix('.json').read_bytes()
        self.assertTrue(a.startswith(b'\x89PNG'))
        self.assertEqual(result['rendering']['png_sha256'],hashlib.sha256(a).hexdigest())
        export_spectrogram(self.dataset,self.root/'second.png',window_sec=2,hop_sec=1)
        self.assertEqual(a,(self.root/'second.png').read_bytes())
        self.assertEqual(b,(self.root/'second.json').read_bytes())
        with self.assertRaises(FileExistsError): export_spectrogram(self.dataset,self.output)
        export_spectrogram(self.dataset,self.output,window_sec=2,hop_sec=1,force=True)
        self.assertEqual(a,self.output.read_bytes())
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths})
        with self.assertRaisesRegex(ValueError,'outside'):
            export_spectrogram(self.dataset,self.dataset/'bad.png',force=True)
        self.output.unlink()
        with self.assertRaises(FileExistsError): export_spectrogram(self.dataset,self.output)
        self.assertFalse(self.output.exists())  # Companion JSON protected too.
        command=[sys.executable,'-m','ml.analysis.spectrogram',str(self.dataset),'--output',str(self.output),
                 '--window-sec','2','--hop-sec','1','--json','--force']
        cli=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertEqual(json.loads(cli.stdout),json.loads(self.output.with_suffix('.json').read_text()))
        bad=subprocess.run(command+['--bin','99'],capture_output=True,text=True)
        self.assertEqual(bad.returncode,2,bad.stderr)
        self.assertNotIn('Traceback',bad.stderr)
        self.assertEqual(json.loads(self.output.with_suffix('.json').read_text()),json.loads(b))

    def test_figure_axes_and_public_samples(self):
        for name,count in (('breathing',999),('hold',1000),('leave',996)):
            with self.subTest(name=name):
                directory=self.root/name
                ingest_recording(ROOT/'recordings/samples'/f'{name}.sample.csv',directory,source_type='repository_sample')
                result=build_spectrogram(load_dataset(directory),9)
                self.assertEqual(result['coverage']['exported_frames'],count)
                self.assertEqual(result['coverage']['available_segments'],1)
                s=result['segments'][0]
                self.assertEqual(s['covered_frames']+s['trailing_unwindowed_frames'],count)
                self.assertLessEqual(s['cadence']['max_grid_deviation_ms'],s['cadence']['interval_tolerance_ms'])
                fig=build_figure(result)
                try:
                    self.assertEqual(fig.axes[0].get_ylabel(),'Frequency (Hz)')
                    self.assertIn('board uptime',fig.axes[0].get_xlabel())
                    self.assertIn('PSD',fig.axes[1].get_ylabel())
                finally: fig.clear()


if __name__=='__main__':
    unittest.main()
