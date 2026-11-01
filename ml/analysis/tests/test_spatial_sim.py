import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from ml.antennas import FIRST_CM, STEP_CM, chest_direction, read_e
from ml.analysis.ingest import ingest_recording
from ml.analysis.protocol import parse_e_line
from ml.analysis.report import load_dataset
from ml.analysis.schema import DATASET_FILES
from ml.analysis.spatial_sim import Simulation, build_figure, encode_e, estimate_window, evaluate, generate, run_demo


class SpatialSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def dataset(self,config=Simulation(duration_sec=8),csv=None):
        data,truth=generate(config)
        if csv is not None:
            data=csv(data)
            truth['input_sha256']=hashlib.sha256(data).hexdigest()
        folder=self.root/f'capture_{len(list(self.root.iterdir()))}'
        folder.mkdir()
        source=folder/'recording.csv'
        source.write_bytes(data)
        truth_path=folder/'truth.json'
        truth_path.write_text(json.dumps(truth))
        ingest_recording(source,folder/'canonical',source_type='synthetic',scenario='unit fixture',
                         scenario_provenance='explicit simulation only')
        return load_dataset(folder/'canonical'),truth,source

    def test_wire_encoding_order_extremes_and_existing_decoder(self):
        z=np.arange(144).reshape(2,3,24).astype(complex)
        z += 1j*(z.real+100)
        z[0,0,0]=-32768+32767j
        lines=encode_e(z,[100,150]).decode().splitlines()
        for n,line in enumerate(lines):
            decoded=parse_e_line(line)
            np.testing.assert_array_equal(np.array(decoded.i)+1j*np.array(decoded.q),z[n])
        wire=struct.unpack('<144h',base64.b64decode(lines[0].split(',')[3]))
        for a in range(3):
            for k in range(24):
                self.assertEqual(wire[a*48+k*2],z[0,a,k].real)
                self.assertEqual(wire[a*48+k*2+1],z[0,a,k].imag)
        source=self.root/'wire.csv'
        source.write_text('\n'.join(lines))
        np.testing.assert_array_equal(read_e(source)[1],z)

    def test_determinism_phase_relationship_and_static_background(self):
        config=Simulation(angle_deg=15,background=0,noise_std=0,duration_sec=4)
        csv,truth=generate(config)
        self.assertEqual((csv,truth),generate(config))
        d=parse_e_line(csv.decode().splitlines()[11])
        z=np.array(d.i)+1j*np.array(d.q)
        for a,spacing in ((1,.5),(2,1)):
            measured=np.angle(z[a,9]*np.conj(z[0,9]))
            expected=2*np.pi*spacing*np.sin(np.radians(15))
            self.assertLess(abs(np.angle(np.exp(1j*(measured-expected)))),.01)
        np.testing.assert_array_equal(np.delete(z,9,axis=1),np.zeros((3,23)))
        clutter,_=generate(replace(config,background=1000,motion_amplitude=0))
        frames=[parse_e_line(line) for line in clutter.decode().splitlines()[1:]]
        self.assertTrue(all(f.i==frames[0].i and f.q==frames[0].q for f in frames))
        self.assertNotEqual(generate(replace(config,background=1000,seed=8))[0],generate(replace(config,background=1000,seed=7))[0])

    def test_known_angles_range_selection_and_pair02_not_fused(self):
        for angle in (-30,-15,0,15,30):
            with self.subTest(angle=angle):
                dataset,truth,_=self.dataset(Simulation(angle_deg=angle,duration_sec=4,range_bin=12))
                result=evaluate(dataset,truth)
                w=result['windows'][0]
                self.assertTrue(w['estimation_available'])
                self.assertEqual(w['estimated_range_bin'],12)
                self.assertLessEqual(w['absolute_angular_error_deg'],.2)
                radius=FIRST_CM+12*STEP_CM
                self.assertAlmostEqual(w['position_cm']['x'],radius*np.sin(np.radians(w['raw_estimate']['angle_0_1_deg'])))
                if abs(angle)==15:
                    self.assertGreater(abs(w['raw_estimate']['angle_0_2_deg']-angle),10)

    def test_real_estimator_used_for_moving_target_not_truth_animation(self):
        dataset,truth,_=self.dataset(Simulation(angle_deg=-30,end_angle_deg=30,duration_sec=8))
        with patch('ml.antennas.chest_direction',wraps=chest_direction) as spy:
            result=evaluate(dataset,truth,hop_frames=20)
        self.assertEqual(spy.call_count,result['summary']['total_windows'])
        self.assertGreater(spy.call_count,1)
        self.assertLess(result['windows'][0]['position_cm']['x'],result['windows'][-1]['position_cm']['x'])
        def altered(z):
            raw=chest_direction(z)
            raw['angle_0_1_deg']=-5
            return raw
        with patch('ml.antennas.chest_direction',side_effect=altered):
            changed=evaluate(dataset,truth)
        self.assertEqual(changed['windows'][0]['estimated_angle_deg'],-5)
        self.assertAlmostEqual(changed['windows'][0]['position_cm']['x'],48.75*np.sin(np.radians(-5)))

    def test_front_back_alias_and_phase_wrapping_are_visible(self):
        self.assertEqual(generate(Simulation(angle_deg=30,duration_sec=4))[0],
                         generate(Simulation(angle_deg=150,duration_sec=4))[0])
        dataset,truth,_=self.dataset(Simulation(angle_deg=150,duration_sec=4))
        w=evaluate(dataset,truth)['windows'][0]
        self.assertTrue(w['truth_outside_front_hemisphere'])
        self.assertTrue(w['estimation_available'])  # Data alone cannot reject rear ambiguity.
        self.assertAlmostEqual(w['estimated_angle_deg'],30,delta=.2)
        self.assertAlmostEqual(w['absolute_angular_error_deg'],120,delta=.2)
        self.assertGreater(w['position_error_cm'],80)
        self.assertGreater(abs(w['raw_estimate']['angle_0_2_deg']),80)
        self.assertIn('front/back',evaluate(dataset,truth)['parameters']['ambiguity'])

    def test_weak_noise_constant_and_excluded_near_bin(self):
        for config,reason in ((Simulation(motion_amplitude=0,duration_sec=4),'insufficient_moving_signal'),
                               (Simulation(motion_amplitude=2,noise_std=40,duration_sec=4),'low_pair_01_coherence'),
                               (Simulation(range_bin=2,duration_sec=4),'insufficient_moving_signal')):
            with self.subTest(reason=reason):
                dataset,truth,_=self.dataset(config)
                result=evaluate(dataset,truth)
                self.assertEqual(result['summary']['available_windows'],0)
                self.assertIsNone(result['summary']['mean_absolute_angular_error_deg'])
                w=result['windows'][0]
                self.assertIn(reason,w['unavailable_reason'])
                self.assertIsNotNone(w['raw_estimate'])
                self.assertIsNone(w['position_cm'])

    def test_shape_range_configuration_and_short_input_errors(self):
        for z in ([],np.zeros((10,2,24)),np.zeros((10,3,23)),np.full((10,3,24),np.nan)):
            with self.assertRaises(ValueError): estimate_window(z,first_cm=15,step_cm=3.75)
            with self.assertRaises(ValueError): encode_e(z,list(range(10)))
        for times in ([0],[0,0],[-1,2]):
            with self.assertRaises(ValueError): encode_e(np.zeros((2,3,24)),times)
        with self.assertRaisesRegex(ValueError,'int16'): encode_e(np.full((2,3,24),40000),[0,1])
        dataset,truth,_=self.dataset(Simulation(duration_sec=.1))
        result=evaluate(dataset,truth)
        self.assertEqual(result['summary']['unavailable_windows'],1)
        self.assertIsNone(result['windows'][0]['estimated_angle_deg'])
        for cfg in (Simulation(range_bin=24),Simulation(angle_deg=181),Simulation(seed=-1),
                    Simulation(motion_hz=10),Simulation(noise_std=-1),Simulation(duration_sec=0)):
            with self.assertRaises(ValueError): generate(cfg)

    def test_missing_or_incompatible_geometry_and_truth_provenance(self):
        for rewrite in (lambda csv:csv.split(b'\n',1)[1],
                        lambda csv:csv.replace(b'C,15,37.5,24,10',b'C,20,50,24,10'),
                        lambda csv:csv.replace(b'C,15,37.5,24,10',b'C,15,37.5,1,10')):
            dataset,truth,_=self.dataset(Simulation(duration_sec=4),csv=rewrite)
            result=evaluate(dataset,truth)
            self.assertEqual(result['summary']['available_windows'],0)
            self.assertIn('geometry',result['windows'][0]['unavailable_reason'])
        dataset,truth,_=self.dataset()
        self.assertEqual(dataset.manifest['source_type'],'synthetic')
        self.assertFalse(dataset.manifest['scenario']['is_ground_truth'])
        self.assertTrue(truth['synthetic'])
        self.assertFalse(truth['hardware_validated'])
        for key,value in (('input_sha256','wrong'),('board_uptime_ms',[]),('antenna_x_wavelengths',None),('synthetic',False)):
            bad=deepcopy(truth)
            bad[key]=value
            with self.assertRaises(ValueError): evaluate(dataset,bad)

    def test_source_preservation_and_visualization(self):
        dataset,truth,source=self.dataset()
        files=[source,source.parent/'truth.json']+[dataset.path/name for name in DATASET_FILES]
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files}
        result=evaluate(dataset,truth)
        figure=build_figure(result)
        try:
            self.assertIn('SYNTHETIC DATA',figure._suptitle.get_text())
            labels=figure.axes[0].get_legend_handles_labels()[1]
            self.assertIn('GROUND TRUTH (SIMULATED)',labels)
            self.assertIn('ESTIMATED POSITION (0-1)',labels)
            figure.savefig(self.root/'plot.png',format='png')
        finally: figure.clear()
        self.assertTrue((self.root/'plot.png').read_bytes().startswith(b'\x89PNG'))
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files})
        _,decoded=read_e(source)
        np.testing.assert_array_equal(decoded,np.array([np.array(r['raw_i'])+1j*np.array(r['raw_q']) for r in dataset.frames]))

    def test_reproducible_demo_files_and_cli_guards(self):
        a,b=self.root/'a',self.root/'b'
        cfg=Simulation(duration_sec=4)
        run_demo(a,cfg)
        run_demo(b,cfg)
        first={p.relative_to(a):p.read_bytes() for p in a.rglob('*') if p.is_file()}
        self.assertEqual(first,{p.relative_to(b):p.read_bytes() for p in b.rglob('*') if p.is_file()})
        command=[sys.executable,'-m','ml.analysis.spatial_sim','--out-dir',str(a),'--scenario','single','--duration-sec','4']
        denied=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(denied.returncode,2,denied.stderr)
        self.assertNotIn('Traceback',denied.stderr)
        self.assertEqual(first,{p.relative_to(a):p.read_bytes() for p in a.rglob('*') if p.is_file()})
        command[command.index(str(a))]=str(self.root/'cli')
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['synthetic'])
        with self.assertRaises(ValueError): run_demo(self.root/'bad',cfg,window_frames=0)
        self.assertFalse((self.root/'bad').exists())


if __name__=='__main__':
    unittest.main()
