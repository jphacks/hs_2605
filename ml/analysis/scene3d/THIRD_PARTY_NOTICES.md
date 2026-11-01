# Isolated offline visualization dependencies

- Three.js **0.187.0**, including OrbitControls: MIT. Source:
  https://github.com/mrdoob/three.js/tree/r187 . Full license is in
  `THREE-LICENSE.txt` and is embedded in every exported HTML.
- esbuild **0.28.2**: MIT, build tooling only. License: `ESBUILD-LICENSE.txt`.

`package-lock.json` pins package versions and registry integrity values. The
committed `scene.bundle.js` is produced from `scene.js` with `build.mjs`; its
SHA-256 is recorded in `scene.bundle.sha256` and checked by Python export.

To rebuild, from this directory run `npm ci` then `npm run build`. Neither Node.js,
npm, these packages nor any network connection is needed to export/open the
committed scene. They are not dependencies of the PC runtime, web app or firmware.

All crib, figure, sensor and direction geometry is procedural project code.
There are no external models, images, textures, fonts or tracking resources.
The geometry is illustrative and uncalibrated; it is not a product enclosure,
verified mounting guide, measured field of view or radar reconstruction.
