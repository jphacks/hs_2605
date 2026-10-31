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

from ml.analysis.ingest import ingest_recording
from ml.analysis.report import load_dataset
from ml.analysis.schema import DATASET_FILES, frame_schema, require_arrow
from ml.analysis.signal_profile import build_profile, write_profile
from ml.analysis.visualize import variation_magnitude

ROOT = Path(__file__).resolve().parents[3]


class SignalProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.dataset = self.root/'capture.csv', self.root/'dataset'
        self.output = self.root/'profile.json'
        self.capture('C,15,37.5,2,10\nD,1,1000,0,0,50,0\nD,2,1060,20,0,50,0\nD,3,1120,80,0,50,0\n')

    def capture(self, text):
        self.source.write_text(text, encoding='utf-8')
        ingest_recording(self.source, self.dataset, force=self.dataset.exists(), source_type='synthetic')

    def profile(self, bins=None):
        return build_profile(load_dataset(self.dataset), bins)

    def test_formulas_counts_geometry_and_reused_variation(self):
        profile = self.profile()
        block = profile['blocks'][0]
        # D/10 gives z=[0,2,8]; mean=10/3, magnitudes=[10/3,4/3,14/3].
        stats, constant = block['bin_statistics']
        self.assertEqual(stats['sample_count'], 3)
        self.assertAlmostEqual(stats['median_variation_magnitude'], 10/3)
        self.assertAlmostEqual(stats['p95_variation_magnitude'], 4.533333333333333)
        self.assertAlmostEqual(stats['rms_variation_magnitude'], np.sqrt(104/9))
        self.assertEqual(constant['distance_cm'], 18.75)
        for key in ('median_variation_magnitude', 'p95_variation_magnitude', 'rms_variation_magnitude'):
            self.assertEqual(constant[key], 0)
        z = np.array([[0,5], [2,5], [8,5]], complex)
        np.testing.assert_allclose(variation_magnitude(z), np.abs(z-z.mean(axis=0)))
        self.assertEqual(block['source_lines'], [2,3,4])
        self.assertEqual((block['frame_row_start'], block['frame_row_end_exclusive']), (0,3))
        self.assertEqual(block['value_divisor'], 10)

    def test_empty_missing_geometry_and_mismatch_are_explicit(self):
        self.capture('')
        result = self.profile()
        self.assertEqual(result['blocks'], [])
        self.assertEqual(result['coverage']['analyzed_frames'], 0)
        self.assertEqual(result['unavailable_reason'], 'No exported frames')
        for text in ('D,1,0,10,20\n', 'C,15,37.5,2,10\nD,1,0,10,20\n'):
            with self.subTest(text=text):
                self.capture(text)
                result = self.profile()
                self.assertEqual(result['coverage']['unavailable_frames'], 1)
                self.assertEqual(result['blocks'][0]['status'], 'unavailable')
                self.assertIsNone(result['blocks'][0]['bin_statistics'])
                self.assertIn('geometry', result['blocks'][0]['unavailable_reason'])

    def test_gap_boundaries_center_independently_without_fill(self):
        self.capture('C,15,37.5,1,10\nD,1,0,0,0\nD,2,60,20,0\nD,4,180,1000,0\nD,5,240,1040,0\n')
        result = self.profile()
        self.assertEqual(result['coverage']['analyzed_frames'], 4)
        self.assertEqual(result['data_quality']['frame_quality']['estimated_missing_frames'], 1)
        a,b = result['blocks']
        self.assertEqual([a['sample_count'],b['sample_count']], [2,2])
        self.assertEqual([a['parent_block_index'],b['parent_block_index']], [1,1])
        self.assertEqual([a['bin_statistics'][0]['rms_variation_magnitude'],
                          b['bin_statistics'][0]['rms_variation_magnitude']], [1,2])
        self.assertEqual([a['source_lines'],b['source_lines']], [[2,3],[4,5]])
        self.assertEqual(b['frame_row_start'], 2)

    def test_malformed_rows_long_intervals_and_explicit_restart(self):
        self.capture('C,15,37.5,1,10\nD,1,0,0,0\nD,2,60,20,0\nD,3,120,40,0\nD,4,180,60,0\n'
                     'D,bad,200,1,2\nD,5,240,80,0\nD,6,1000,100,0\n'
                     'S,1020,radar restart: fifo\nD,7,1060,120,0\nD,8,1120,140,0\n')
        result = self.profile()
        self.assertEqual([b['sample_count'] for b in result['blocks']], [4,1,1,2])
        self.assertEqual(result['coverage']['analyzed_frames'], 8)
        self.assertEqual(result['data_quality']['diagnostic_count'], 1)
        self.assertEqual(result['data_quality']['explicit_restart_status_count'], 1)
        self.assertEqual(result['data_quality']['inferred_anomaly_counts']['long_interval'], 1)
        self.assertEqual(result['blocks'][-1]['parent_block_index'], 2)

    def test_reset_repeated_time_and_config_changes_do_not_combine(self):
        self.capture('C,15,37.5,1,10\nD,5,1000,0,0\nD,6,1060,20,0\nD,0,0,30,0\n'
                     'D,1,0,40,0\nC,20,50,1,2\nD,2,60,4,0\n')
        result = self.profile()
        self.assertEqual([b['sample_count'] for b in result['blocks']], [2,1,1,1])
        self.assertEqual([b['board_uptime_start_ms'] for b in result['blocks']], [1000,0,0,60])
        self.assertEqual(result['blocks'][-1]['geometry']['first_cm'], 20)
        self.assertEqual(result['blocks'][-1]['value_divisor'], 2)
        self.assertEqual(result['blocks'][1]['bin_statistics'][0]['median_variation_magnitude'], 0)

    def test_e_only_units_selected_antenna_and_d_e_transition(self):
        def e(seq, time, delta):
            # Antenna 0 changes by 2; other antennas change much more.
            values = [v+(delta if v < 48 else delta*100) for v in range(144)]
            return f'E,{seq},{time},'+base64.b64encode(struct.pack('<144h', *values)).decode()+'\n'
        self.capture('C,15,37.5,24,10\n'+e(1,0,0)+e(2,60,2))
        result = self.profile()
        block = result['blocks'][0]
        self.assertEqual(block['message_type'], 'E')
        self.assertEqual((block['selected_antenna'], block['recorded_antenna_count']), (0,3))
        self.assertEqual((block['recorded_config_scale'],block['value_divisor']), (10,1))
        self.assertEqual(len(block['bin_statistics']), 24)
        self.assertAlmostEqual(block['bin_statistics'][0]['rms_variation_magnitude'], np.sqrt(2))
        self.capture(e(1,0,0))
        self.assertEqual(self.profile()['blocks'][0]['status'], 'unavailable')
        d = ','.join(['0','0']*24)
        self.capture('C,15,37.5,24,10\n'+f'D,1,0,{d}\n'+e(2,60,0))
        self.assertEqual([b['message_type'] for b in self.profile()['blocks']], ['D','E'])

    def test_partial_coverage_and_no_hidden_exclusions(self):
        self.capture('C,15,37.5,2,10\nD,1,0,10,20\nC,15,37.5,1,10\nD,2,60,20,40\n')
        result = self.profile()
        self.assertEqual((result['coverage']['analyzed_frames'], result['coverage']['unavailable_frames']), (1,1))
        self.assertEqual([b['status'] for b in result['blocks']], ['unavailable','available'])
        self.assertEqual([line for b in result['blocks'] for line in b['source_lines']], [2,4])

    def test_invalid_bins_and_deterministic_selection_order(self):
        for bins in ((-1,), (2,), (True,), (0.5,), (0,0), (), 0):
            with self.subTest(bins=bins), self.assertRaises(ValueError):
                self.profile(bins)
        self.assertEqual(self.profile([1,0]), self.profile([0,1]))
        self.assertEqual([s['bin_index'] for s in self.profile([1])['blocks'][0]['bin_statistics']], [1])

    def test_provenance_quality_hashes_and_no_csv_fallback(self):
        dataset = load_dataset(self.dataset)
        self.source.unlink()
        result = write_profile(self.dataset, self.output)
        self.assertEqual(result['provenance']['source_type'], 'synthetic')
        self.assertEqual(result['provenance']['input'], dataset.manifest['input'])
        self.assertEqual(result['verified_artifact_hashes'], dataset.manifest['artifacts'])
        self.assertEqual(result['data_quality'], dataset.quality)
        result['data_quality']['diagnostic_count'] = 999
        self.assertEqual(dataset.quality['diagnostic_count'], 0)
        events = self.dataset/'events.jsonl'
        events.write_bytes(events.read_bytes()+b'bad')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            write_profile(self.dataset, self.root/'invalid.json')

    def test_invalid_timestamp_is_rejected_by_canonical_validation(self):
        pa,pq = require_arrow()
        rows = pq.read_table(self.dataset/'frames.parquet').to_pylist()
        rows[0]['board_uptime_ms'] = -1
        pq.write_table(pa.Table.from_pylist(rows, schema=frame_schema()), self.dataset/'frames.parquet')
        manifest = json.loads((self.dataset/'manifest.json').read_text())
        manifest['artifacts']['frames.parquet']['sha256'] = hashlib.sha256((self.dataset/'frames.parquet').read_bytes()).hexdigest()
        (self.dataset/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'sequence/uptime'):
            write_profile(self.dataset, self.output)
        self.assertFalse(self.output.exists())

    def test_irregular_samples_are_sample_weighted_and_rms_stays_finite(self):
        self.capture('C,15,37.5,1,1\nD,1,1000,0,0\nD,2,1010,2,0\nD,3,1023,8,0\n')
        stats = self.profile()['blocks'][0]['bin_statistics'][0]
        self.assertAlmostEqual(stats['rms_variation_magnitude'], np.sqrt(104/9))
        # These finite magnitudes would overflow if directly squared for RMS.
        self.capture('C,15,37.5,1,1e-200\nD,1,0,-1,0\nD,2,60,1,0\n')
        self.assertAlmostEqual(self.profile()['blocks'][0]['bin_statistics'][0]['rms_variation_magnitude']/1e200, 1)

    def test_determinism_safe_output_and_unchanged_inputs(self):
        paths = [self.source]+[self.dataset/n for n in DATASET_FILES]
        before = {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths}
        write_profile(self.dataset, self.output)
        original = self.output.read_bytes()
        second = self.root/'second.json'
        write_profile(self.dataset, second)
        self.assertEqual(original, second.read_bytes())
        with self.assertRaises(FileExistsError):
            write_profile(self.dataset, self.output)
        write_profile(self.dataset, self.output, force=True)
        self.assertEqual(original, self.output.read_bytes())
        with self.assertRaisesRegex(ValueError, 'outside'):
            write_profile(self.dataset, self.dataset/'profile.json', force=True)
        with self.assertRaisesRegex(ValueError, '.json'):
            write_profile(self.dataset, self.source, force=True)
        self.assertEqual(before, {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths})

    def test_cli_and_clear_errors(self):
        command = [sys.executable,'-m','ml.analysis.signal_profile',str(self.dataset),
                   '--output',str(self.output),'--bin','0']
        success = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertIn('3 analyzed frames', success.stdout)
        denied = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(denied.returncode, 2, denied.stderr)
        self.assertNotIn('Traceback', denied.stderr)
        forced = subprocess.run(command+['--force'], capture_output=True, text=True)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        invalid = subprocess.run(command+['--force','--bin','24'], capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertIn('outside parent block', invalid.stderr)
        self.assertNotIn('Traceback', invalid.stderr)
        self.assertEqual(json.loads(self.output.read_text())['requested_bins'], [0])
        missing = subprocess.run([sys.executable,'-m','ml.analysis.signal_profile',str(self.root/'missing'),
                                  '--output',str(self.root/'missing.json')], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn('dataset directory', missing.stderr)

    def test_three_public_samples_against_shared_variation(self):
        from ml.analysis.report import prepare_blocks
        for name,count in (('breathing',999),('hold',1000),('leave',996)):
            with self.subTest(name=name):
                dataset = self.root/name
                ingest_recording(ROOT/'recordings/samples'/f'{name}.sample.csv', dataset,
                                 source_type='repository_sample')
                loaded = load_dataset(dataset)
                result = build_profile(loaded)
                self.assertEqual(result['coverage']['analyzed_frames'], count)
                self.assertEqual(result['coverage']['unavailable_frames'], 0)
                self.assertEqual(len(result['blocks']), 1)
                reference = prepare_blocks(loaded, 0)[0]['data'].variation
                for k,stats in enumerate(result['blocks'][0]['bin_statistics']):
                    self.assertAlmostEqual(stats['median_variation_magnitude'], float(np.median(reference[:,k])))
                    self.assertAlmostEqual(stats['p95_variation_magnitude'], float(np.quantile(reference[:,k], .95)))
                    self.assertAlmostEqual(stats['rms_variation_magnitude'], float(np.sqrt(np.mean(reference[:,k]**2))))


if __name__ == '__main__':
    unittest.main()
