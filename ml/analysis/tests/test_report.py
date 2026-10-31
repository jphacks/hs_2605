import base64
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from ml.analysis.ingest import ingest_recording
from ml.analysis.report import build_html, event_uptime, load_dataset, prepare_blocks, render_report
from ml.analysis.schema import DATASET_FILES, frame_schema, require_arrow

pa, pq = require_arrow()
ROOT = Path(__file__).resolve().parents[3]


def e_line(seq=1, uptime=1000):
    return f'E,{seq},{uptime},'+base64.b64encode(struct.pack('<144h', *range(100,244))).decode()


def rehash(directory, name):
    manifest = json.loads((directory/'manifest.json').read_text())
    manifest['artifacts'][name]['sha256'] = hashlib.sha256((directory/name).read_bytes()).hexdigest()
    (directory/'manifest.json').write_text(json.dumps(manifest))


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags, self.images = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'img':
            self.images.append(dict(attrs))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'hold.sample.csv'
        self.dataset = self.root/'dataset'
        self.output = self.root/'report.html'
        self.source.write_text('C,15,37.5,2,10\n'+''.join(
            f'D,{k},{1000+k*60},{100+k},200,30,40\n' for k in range(8)))
        ingest_recording(self.source, self.dataset, source_type='repository_sample')

    def write_manifest(self, manifest):
        (self.dataset/'manifest.json').write_text(json.dumps(manifest))

    def replace_capture(self, text):
        self.source.write_text(text)
        ingest_recording(self.source, self.dataset, force=True, source_type='repository_sample')

    def test_valid_dataset_and_self_contained_report_without_csv(self):
        dataset = load_dataset(self.dataset)
        self.assertEqual(len(dataset.frames), 8)
        self.assertEqual(dataset.manifest['source_type'], 'repository_sample')
        self.source.unlink()  # Reporting has no raw-CSV dependency/fallback.
        html = build_html(dataset, 1)
        document = Document(html)
        for title in ('A. Dataset overview', 'B. Data quality', 'C. Range-time heatmap',
                      'D. Selected-bin I/Q waveform', 'E. Sampling timeline', 'F. Interpretation'):
            self.assertIn(title, html)
        self.assertNotIn('script', document.tags)
        self.assertNotIn('link', document.tags)
        self.assertEqual(len(document.images), 2)
        for image in document.images:
            self.assertTrue(image['alt'])
            self.assertTrue(base64.b64decode(image['src'].split(',', 1)[1]).startswith(b'\x89PNG'))
        self.assertIn('18.75 cm', html)
        self.assertIn('not Unix', html)

    def test_missing_and_corrupted_files_and_hashes(self):
        for name in DATASET_FILES:
            with self.subTest(name=name):
                original = (self.dataset/name).read_bytes()
                (self.dataset/name).unlink()
                with self.assertRaisesRegex(ValueError, 'artifact'):
                    load_dataset(self.dataset)
                (self.dataset/name).write_bytes(original)
        for name in DATASET_FILES[1:]:
            with self.subTest(name=name):
                original = (self.dataset/name).read_bytes()
                (self.dataset/name).write_bytes(original+b'corrupt')
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    load_dataset(self.dataset)
                (self.dataset/name).write_bytes(original)
        for name in DATASET_FILES[1:]:
            with self.subTest(rehashed=name):
                original = (self.dataset/name).read_bytes()
                (self.dataset/name).write_bytes(b'not a valid artifact')
                rehash(self.dataset, name)
                with self.assertRaises(ValueError):
                    load_dataset(self.dataset)
                (self.dataset/name).write_bytes(original)
                rehash(self.dataset, name)

    def test_manifest_schema_metadata_and_counts_rejected(self):
        manifest = json.loads((self.dataset/'manifest.json').read_text())
        for key, value in (('format', 'other'), ('schema_version', 2), ('schema_version', True),
                           ('source_type', 'recorded'), ('source_type_provenance', 'invented'),
                           ('artifacts', []), ('session', None), ('counts', {}),
                           ('capture_configuration', [{'source_line': 0}])):
            with self.subTest(key=key, value=value):
                bad = deepcopy(manifest)
                bad[key] = value
                self.write_manifest(bad)
                with self.assertRaises(ValueError):
                    load_dataset(self.dataset)
        bad = deepcopy(manifest)
        bad['counts']['frames'] += 1
        self.write_manifest(bad)
        with self.assertRaisesRegex(ValueError, 'counts disagree'):
            load_dataset(self.dataset)
        self.write_manifest(manifest)
        table = pq.read_table(self.dataset/'frames.parquet').drop(['raw_q'])
        pq.write_table(table, self.dataset/'frames.parquet')
        rehash(self.dataset, 'frames.parquet')
        with self.assertRaisesRegex(ValueError, 'Parquet schema'):
            load_dataset(self.dataset)

    def test_frame_fields_rejected_even_with_matching_hash(self):
        original = pq.read_table(self.dataset/'frames.parquet').to_pylist()
        for field, value in (('seq', -1), ('board_uptime_ms', -1), ('source_line', 0),
                             ('antenna_count', 3), ('raw_i', [[None, 1]]), ('raw_q', [[1]]),
                             ('value_divisor', 1), ('config_source_line', 3),
                             ('configuration_status', 'missing_config'), ('session_id', 'wrong')):
            with self.subTest(field=field):
                rows = deepcopy(original)
                rows[0][field] = value
                pq.write_table(pa.Table.from_pylist(rows, schema=frame_schema()), self.dataset/'frames.parquet')
                rehash(self.dataset, 'frames.parquet')
                with self.assertRaises(ValueError):
                    load_dataset(self.dataset)

    def test_d_e_scale_and_antenna_zero_equivalence(self):
        d = prepare_blocks(load_dataset(self.dataset), 0)[0]['data']
        self.assertEqual(d.z[0,0], 10+20j)
        self.replace_capture(e_line()+'\nC,15,37.5,24,10\n'+e_line(2,1060)+'\n')
        dataset = load_dataset(self.dataset)
        block = prepare_blocks(dataset, 0)[0]
        self.assertEqual(block['kind'], 'E')
        self.assertEqual(block['data'].z[0,0], 100+101j)
        self.assertEqual(block['data'].z[0,23], 146+147j)
        np.testing.assert_array_equal(block['data'].variation, np.zeros((2,24)))
        html = build_html(dataset)
        self.assertIn('E int16 values already scaled; C.scale is not applied.', html)
        self.assertIn('Antenna 0 only', html)

    def test_missing_incompatible_configuration_and_empty_input(self):
        for recording in ('D,1,1000,10,20\n', e_line()+'\n',
                          'C,15,37.5,3,10\nD,1,1000,10,20\n', ''):
            with self.subTest(recording=recording[:30]):
                self.replace_capture(recording)
                dataset = load_dataset(self.dataset)
                html = build_html(dataset)
                self.assertIn('unavailable', html)
                self.assertEqual(len(Document(html).images), 1)  # Timeline only; no invented geometry.

    def test_gap_diagnostics_restart_blocks_and_actual_timestamps(self):
        self.replace_capture('C,15,37.5,1,10\nD,1,0,10,20\nD,2,60,10,20\nD,3,120,10,20\n'
                             'D,4,180,10,20\nD,bad,200,10,20\nD,6,300,10,20\nD,7,900,10,20\n'
                             'S,950,radar restart: fifo overflow\nD,0,0,10,20\nD,1,60,10,20\n')
        dataset = load_dataset(self.dataset)
        blocks = prepare_blocks(dataset, 0)
        self.assertEqual(len(blocks), 2)
        self.assertEqual([(s.start, s.stop) for s in blocks[0]['data'].trace_segments], [(0,4), (4,5), (5,6)])
        np.testing.assert_array_equal(blocks[0]['data'].elapsed_s, [0,.06,.12,.18,.3,.9])
        explicit = next(e for e in dataset.events if e['origin'] == 'explicit_board_status')
        self.assertEqual(event_uptime(explicit, {}), 950)
        malformed_s = dict(explicit, raw='S,unknown,radar restart')
        self.assertIsNone(event_uptime(malformed_s, {}))
        html = build_html(dataset)
        self.assertIn('explicit_board_status', html)
        self.assertIn('inferred_from_frames', html)
        self.assertIn('malformed_message', html)

    def test_repeated_time_config_and_message_type_transitions(self):
        self.replace_capture('C,15,37.5,1,10\nD,1,0,10,20\nD,2,0,10,20\n'
                             'C,20,50,1,10\nD,3,60,10,20\nC,15,37.5,24,10\n'+e_line(4,120)+'\n')
        blocks = prepare_blocks(load_dataset(self.dataset), 0)
        self.assertEqual([b['kind'] for b in blocks], ['D','D','D','E'])
        self.assertEqual([len(b['data'].elapsed_s) for b in blocks], [1,1,1,1])
        self.assertEqual(blocks[2]['data'].distances_cm[0], 20)

    def test_quality_json_is_authoritative_and_invalid_rows_not_missing_frames(self):
        self.replace_capture('C,15,37.5,2,10\nD,bad,0,1,2\nD,1,1000,10,20\n')
        quality = json.loads((self.dataset/'quality.json').read_text())
        quality['frame_quality']['estimated_missing_frames'] = 73
        (self.dataset/'quality.json').write_text(json.dumps(quality))
        rehash(self.dataset, 'quality.json')
        html = build_html(load_dataset(self.dataset))
        self.assertIn('<th scope="row">estimated missing frames</th><td>73</td>', html)
        self.assertIn('Rejected D/E source rows', html)
        self.assertEqual(load_dataset(self.dataset).quality['diagnostic_count'], 2)

    def test_escaping_metadata_and_event_payloads(self):
        payload = '<script>alert("x")</script><img src=x onerror="alert(1)"> & $bad$'
        self.replace_capture('C,15,37.5,1,10\nD,1,1000,10,20\nY,1,'+payload+'\n')
        manifest = json.loads((self.dataset/'manifest.json').read_text())
        manifest['input']['name'] = payload
        manifest['scenario']['name'] = payload
        manifest['scenario']['provenance'] = payload
        self.write_manifest(manifest)
        html = build_html(load_dataset(self.dataset))
        self.assertNotIn(payload, html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&quot;x&quot;', html)
        self.assertNotIn('script', Document(html).tags)
        self.assertEqual(len(Document(html).images), 2)

    def test_deterministic_output_force_and_unchanged_inputs(self):
        paths = [self.source]+[self.dataset/name for name in DATASET_FILES]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        render_report(self.dataset, self.output, 1)
        first = self.output.read_bytes()
        second = self.root/'second.html'
        render_report(self.dataset, second, 1)
        self.assertEqual(first, second.read_bytes())
        with self.assertRaises(FileExistsError):
            render_report(self.dataset, self.output, 1)
        render_report(self.dataset, self.output, 1, force=True)
        self.assertEqual(first, self.output.read_bytes())
        for path, state in before.items():
            self.assertEqual(state, (path.read_bytes(), path.stat().st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'outside'):
            render_report(self.dataset, self.dataset/'report.html', force=True)
        with self.assertRaisesRegex(ValueError, '.html'):
            render_report(self.dataset, self.source, force=True)

    def test_invalid_bins_and_cli_errors(self):
        for index in (-1, True, 1.2):
            with self.assertRaisesRegex(ValueError, 'Bin index'):
                build_html(load_dataset(self.dataset), index)
        self.assertIn('Plot unavailable', build_html(load_dataset(self.dataset), 99))
        command = [sys.executable, '-m', 'ml.analysis.report', str(self.dataset), '--output', str(self.output), '--bin', '1']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        result = subprocess.run(command+['--force'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, '-S', *command[1:], '--force'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('ml/analysis/requirements.txt', result.stderr)
        result = subprocess.run([sys.executable, '-m', 'ml.analysis.report', str(self.source), '--output', str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('dataset directory', result.stderr)

    def test_nonfinite_json_duplicate_keys_and_quality_event_mismatch(self):
        original = (self.dataset/'quality.json').read_bytes()
        for data in (b'{"x":NaN}', b'{"x":1e999}', b'{"x":1,"x":2}'):
            (self.dataset/'quality.json').write_bytes(data)
            rehash(self.dataset, 'quality.json')
            with self.assertRaises(ValueError):
                load_dataset(self.dataset)
        quality = json.loads(original)
        quality['explicit_restart_status_count'] = 1
        (self.dataset/'quality.json').write_text(json.dumps(quality))
        rehash(self.dataset, 'quality.json')
        with self.assertRaisesRegex(ValueError, 'counts disagree'):
            load_dataset(self.dataset)

    def test_all_public_samples(self):
        for name, expected in (('breathing',999), ('hold',1000), ('leave',996)):
            with self.subTest(name=name):
                dataset = self.root/name
                ingest_recording(ROOT/'recordings/samples'/f'{name}.sample.csv', dataset,
                                 source_type='repository_sample')
                before = {f: (dataset/f).read_bytes() for f in DATASET_FILES}
                output = self.root/(name+'.html')
                render_report(dataset, output, 9)
                self.assertEqual(load_dataset(dataset).manifest['counts']['frames'], expected)
                self.assertEqual(len(Document(output.read_text(encoding='utf-8')).images), 2)
                self.assertEqual(before, {f: (dataset/f).read_bytes() for f in DATASET_FILES})


if __name__ == '__main__':
    unittest.main()
