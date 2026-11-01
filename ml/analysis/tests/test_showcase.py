"""Showcase export/navigation tests without data, npm, a browser or a GPU."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ml.analysis import spatial_scene3d as scene


ROOT=Path(__file__).resolve().parents[3]


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags=[]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))


class NotificationText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inside=False
        self.text=[]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get('id')=='guardian-notification':
            self.inside=True

    def handle_endtag(self, tag):
        if tag=='article':
            self.inside=False

    def handle_data(self, data):
        if self.inside:
            self.text.append(data)


class ShowcaseTest(unittest.TestCase):
    def setUp(self):
        (ROOT/'analysis_runs').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'analysis_runs')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def cli(self,*args):
        return subprocess.run([sys.executable,'-X','utf8','-m','ml.analysis.spatial_scene3d',*map(str,args)],
                              cwd=ROOT,capture_output=True,text=True,encoding='utf-8')

    def test_self_contained_showcase_and_honest_labels(self):
        html=scene.build_html(showcase=True)
        tags=Document(html).tags
        self.assertEqual([a for t,a in tags if t=='script'],[{}])
        self.assertFalse(any('src' in a or 'href' in a for _,a in tags))
        self.assertIn("connect-src 'none'",html)
        self.assertIn('説明用シミュレーションです。実測データや医療性能を示すものではありません。',html)
        self.assertIn('カメラも使いません',html)
        self.assertIn('実際の送信・音・カメラ・医療性能の検証はありません',html)
        self.assertNotIn('id="scene-data"',html)
        self.assertNotIn('id="analysis-map"',html)
        self.assertNotIn('__INSTALLATION_FALLBACK__',html)

    def test_export_is_deterministic_and_protects_existing_outputs(self):
        first=scene.render_scene(self.root/'one.html',showcase=True)
        second=scene.render_scene(self.root/'nested'/'two.html',showcase=True)
        self.assertEqual(first.read_bytes(),second.read_bytes())
        with self.assertRaises(FileExistsError):
            scene.render_scene(first,showcase=True)
        self.assertEqual(first.read_bytes(),second.read_bytes())

    def test_modes_cannot_be_combined_and_do_not_create_outputs(self):
        target=self.root/'new'/'out.html'
        with self.assertRaisesRegex(ValueError,'separate presentation'):
            scene.render_scene(target,case=self.root,showcase=True)
        with self.assertRaisesRegex(ValueError,'separate modes'):
            scene.build_html({},showcase=True)
        result=self.cli('--showcase','--case',self.root,'--output',target)
        self.assertEqual(result.returncode,2)
        self.assertNotIn('Traceback',result.stderr)
        self.assertFalse(target.parent.exists())

    def test_no_autoplay_and_no_javascript_fallback_markup(self):
        tags=Document(scene.build_html(showcase=True)).tags
        main=next(a for t,a in tags if t=='main')
        self.assertEqual(main['data-step'],'0')
        self.assertEqual(main['data-started'],'false')
        self.assertTrue(any(t=='noscript' for t,_ in tags))
        svg=next(a for t,a in tags if t=='svg')
        self.assertEqual(svg['id'],'scene-fallback')
        self.assertNotIn('hidden',svg)
        for t,a in tags:
            if t=='button' and a.get('id') in ('show-previous','show-restart'):
                self.assertIn('disabled',a)

    def test_same_installation_fallback_is_reused(self):
        def svg(html):
            return html[html.index('<svg id="scene-fallback"'):html.index('</svg>')+6]
        self.assertEqual(svg(scene.build_html()),svg(scene.build_html(showcase=True)))

    def test_notification_is_single_hidden_non_live_presentation(self):
        tags=Document(scene.build_html(showcase=True)).tags
        cards=[a for _,a in tags if a.get('id')=='guardian-notification']
        self.assertEqual(len(cards),1)
        self.assertIn('hidden',cards[0])
        self.assertNotIn('aria-live',cards[0])
        self.assertNotIn('role',cards[0])
        live=[a for _,a in tags if a.get('aria-live')]
        self.assertEqual([a['id'] for a in live],['story-status'])
        connector=next(a for _,a in tags if a.get('id')=='notification-connector')
        self.assertEqual(connector['aria-hidden'],'true')
        self.assertEqual(connector['focusable'],'false')

    def test_notification_has_exact_japanese_copy(self):
        text=''.join(NotificationText(scene.build_html(showcase=True)).text)
        self.assertEqual(text,'通知の例 · シミュレーション赤ちゃんの様子を確認してくださいデモ表示 · 実際には送信されません')

    def test_missing_showcase_resource_is_actionable(self):
        resources=self.root/'resources'
        shutil.copytree(scene.RESOURCES,resources,ignore=shutil.ignore_patterns('node_modules'))
        (resources/'showcase.html').unlink()
        with patch.object(scene,'RESOURCES',resources):
            with self.assertRaisesRegex(ValueError,'Missing offline scene resources'):
                scene.render_scene(self.root/'out.html',showcase=True)
        self.assertFalse((self.root/'out.html').exists())

    def test_cli_success_and_invalid_output(self):
        target=self.root/'show.html'
        result=self.cli('--showcase','--output',target)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('id="showcase"',target.read_text(encoding='utf-8'))
        for output in (target,self.root/'bad.json'):
            result=self.cli('--showcase','--output',output)
            self.assertEqual(result.returncode,2)
            self.assertIn('error:',result.stderr)
            self.assertNotIn('Traceback',result.stderr)

    def test_showcase_does_not_import_data_dependencies(self):
        result=subprocess.run([sys.executable,'-X','utf8','-c',
            'import sys; from ml.analysis.spatial_scene3d import build_html; '
            'build_html(showcase=True); assert "numpy" not in sys.modules; '
            'assert "ml.analysis.spatial_explorer" not in sys.modules'],
            cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'),'Node needed for pure navigation check')
    def test_actual_navigation_controller_has_three_manual_steps(self):
        module=(scene.RESOURCES/'showcase_state.js').resolve().as_uri()
        script='''
import assert from 'node:assert/strict';
const {createShowcase}=await import(process.argv[1]);
globalThis.setTimeout=()=>{throw Error('No timed step advance');};
const changes=[],controller=createShowcase(s=>changes.push(s));
assert.deepEqual(controller.snapshot(),{step:0,started:false});
controller.next();controller.previous();controller.restart();assert.equal(changes.length,0);
controller.start();controller.start();assert.equal(changes.length,1);
controller.next();assert.equal(controller.snapshot().step,1);
controller.next();controller.next();assert.equal(controller.snapshot().step,2);
controller.previous();assert.equal(controller.snapshot().step,1);
controller.restart();assert.deepEqual(controller.snapshot(),{step:0,started:true});
controller.previous();assert.equal(controller.snapshot().step,0);
assert.deepEqual(changes.map(s=>s.step),[0,1,2,1,0]);console.log(JSON.stringify(changes));
'''
        result=subprocess.run([shutil.which('node'),'--input-type=module','-e',script,module],
                              capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(json.loads(result.stdout)),5)


if __name__=='__main__':
    unittest.main()
