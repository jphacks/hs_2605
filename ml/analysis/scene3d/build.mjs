// Offline-only build tooling. Python export uses the committed bundle, not npm.
import {build} from 'esbuild';
import {readFileSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

await build({entryPoints:['scene.js'],bundle:true,minify:true,format:'iife',target:'es2020',
  outfile:'scene.bundle.js',legalComments:'inline',charset:'utf8',sourcemap:false});
const bytes=readFileSync('scene.bundle.js');
if(/<\/script/i.test(bytes.toString('utf8')))throw Error('Unsafe inline script boundary in bundle');
writeFileSync('scene.bundle.sha256',createHash('sha256').update(bytes).digest('hex')+'\n');
console.log(`Offline scene bundle: ${bytes.length} bytes`);
