import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const assets = fileURLToPath(new URL('../public/assets/', import.meta.url));
const expected = JSON.parse(await readFile(new URL('../public/assets/manifest.json', import.meta.url), 'utf8'));
for (const [name, item] of Object.entries(expected)) {
  const bytes = await readFile(`${assets}/${name}`);
  const digest = createHash('sha256').update(bytes).digest('hex');
  if (digest !== item.sha256 || bytes.length !== item.bytes) throw new Error(`Asset integrity failed: ${name}`);
}
const evidence = JSON.parse(await readFile(`${assets}/evidence.json`, 'utf8'));
if (!evidence.browser_mesh.fresh_process_import.dimensions_preserved ||
    !evidence.browser_mesh.fresh_process_import.mesh_count_preserved ||
    evidence.browser_mesh.sha256 !== expected['rpi5-open-tray.glb'].sha256) {
  throw new Error('GLB does not match its native verification');
}
if (Object.values(evidence.baseline_reopened.checks).some(x => x !== true) ||
    Object.values(evidence.variant_reopened.checks).some(x => x !== true)) {
  throw new Error('Native acceptance record contains a failed check');
}
console.log(`Verified ${Object.keys(expected).length} bundled assets and native GLB evidence.`);

const environmentRoot = `${assets}/environment`;
const environment = JSON.parse(await readFile(`${environmentRoot}/case.json`, 'utf8'));
const environmentManifest = JSON.parse(await readFile(`${environmentRoot}/manifest.json`, 'utf8'));
for (const [name, record] of Object.entries(environmentManifest)) {
  if (!/^[a-zA-Z0-9_.-]+$/.test(name)) throw new Error('Unsafe environment asset filename');
  const bytes = await readFile(`${environmentRoot}/${name}`);
  if (bytes.length !== record.bytes || createHash('sha256').update(bytes).digest('hex') !== record.sha256) {
    throw new Error(`Environment asset integrity failed: ${name}`);
  }
}
if (environment.views.length < 3 || !environment.views.some(v => v.host.startsWith('Blender'))) {
  throw new Error('Environment case requires multiple real native views');
}
for (const entry of [...environment.views, ...environment.downloads, {file: environment.mesh}]) {
  if (entry.file && !environmentManifest[entry.file]) throw new Error(`Missing environment evidence asset: ${entry.file}`);
}
console.log(`Verified ${Object.keys(environmentManifest).length} environment assets; evidence is recorded native output, not a CI render.`);
