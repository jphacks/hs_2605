"""Pure display-coordinate checks; Three.js integration is checked in a browser."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is needed for the pure JavaScript coordinate checks")
class PlaneCoordinateTest(unittest.TestCase):
    def transform(self, points, extent):
        source = Path(__file__).resolve().parents[1] / "scene3d" / "coordinates.js"
        script = """
const {planePoint,PLOT_RADIUS}=await import(process.argv[1]);
const points=JSON.parse(process.argv[2]),before=JSON.stringify(points);
const results=points.map(p=>planePoint(p,Number(process.argv[3])));
console.log(JSON.stringify({results,radius:PLOT_RADIUS,unchanged:JSON.stringify(points)===before}));
"""
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                 source.as_uri(), json.dumps(points), str(extent)],
                                capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(result.stdout)

    def test_normalized_coordinates_have_only_fixed_render_depth(self):
        report = self.transform([{"x":0,"y":50}, {"x":-25,"y":0}, {"x":25,"y":-25}], 50)
        radius = report["radius"]
        self.assertEqual(report["results"], [[0,radius,0], [-radius/2,0,0], [radius/2,-radius/2,0]])

    def test_source_points_are_unchanged_and_no_height_is_inferred(self):
        points = [{"x":3,"y":4,"z":123}, {"x":3,"y":4,"elevation_deg":45}]
        report = self.transform(points, 10)
        self.assertTrue(report["unchanged"])
        self.assertEqual(report["results"][0], report["results"][1])
        self.assertEqual(report["results"][0][2], 0)

    def test_window_changes_are_direct_not_smoothed_or_interpolated(self):
        points = [{"x":0,"y":10}, {"x":20,"y":-10}, {"x":0,"y":10}]
        report = self.transform(points, 25)
        self.assertEqual(report["results"][0], report["results"][2])
        self.assertAlmostEqual(report["results"][1][0], 20/25*report["radius"])
        self.assertAlmostEqual(report["results"][1][1], -10/25*report["radius"])


if __name__ == "__main__":
    unittest.main()
