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

const recoveryBytes = await readFile(`${assets}/recovery.json`);
const recoverySource = await readFile(new URL('../../examples/recovery/observed-recovery.json', import.meta.url));
if (!recoveryBytes.equals(recoverySource)) throw new Error('Recovery demo differs from the recorded experiment');
const recovery = JSON.parse(recoveryBytes);
if (recovery.provenance.native_apps_used !== false || recovery.provenance.cloud_used !== false ||
    recovery.provenance.kind !== 'observed-synthetic-adapter-execution') {
  throw new Error('Recovery demo must identify its synthetic-host execution boundary');
}
for (const scenario of recovery.cases) {
  if (!scenario.steps.length || !Object.keys(scenario.checks).length ||
      Object.values(scenario.checks).some(value => value !== true)) {
    throw new Error(`Failed or empty recovery evidence: ${scenario.id}`);
  }
  for (const step of scenario.steps) {
    if (step.report && (step.report.automatic_replay !== false || step.report.native_host_idle !== 'unknown')) {
      throw new Error(`Invalid recovery guarantee: ${scenario.id}`);
    }
  }
}
console.log(`Verified ${recovery.cases.length} recorded recovery scenarios; synthetic hosts, no browser execution.`);

const sourceRoot = `${assets}/source-binding`;
const sourceManifest = JSON.parse(await readFile(`${sourceRoot}/manifest.json`, 'utf8'));
for (const [name, record] of Object.entries(sourceManifest)) {
  if (!/^[a-zA-Z0-9_.-]+$/.test(name)) throw new Error('Unsafe source-binding asset filename');
  const bytes = await readFile(`${sourceRoot}/${name}`);
  if (bytes.length !== record.bytes || createHash('sha256').update(bytes).digest('hex') !== record.sha256) {
    throw new Error(`Source-binding asset integrity failed: ${name}`);
  }
}
const nativeBytes = await readFile(`${sourceRoot}/observed-native-proof.json`);
const nativeSource = await readFile(new URL('../../examples/source_binding/observed-native-proof.json', import.meta.url));
if (!nativeBytes.equals(nativeSource)) throw new Error('Source-binding native evidence differs from the recorded case');
const native = JSON.parse(nativeBytes);
const sourceCase = JSON.parse(await readFile(`${sourceRoot}/case.json`, 'utf8'));
if (native.provenance.synthetic_adapter_used !== false || native.provenance.kind !== 'observed-native-execution' ||
    Object.values(native.checks).some(value => value !== true) ||
    sourceCase.proof_sha256 !== createHash('sha256').update(nativeBytes).digest('hex')) {
  throw new Error('Invalid native source-binding provenance or acceptance record');
}
for (const record of [...Object.values(native.renders), native.native_download]) {
  if (sourceManifest[record.filename]?.sha256 !== record.sha256) throw new Error('Native source-binding file differs from measured output');
}
if (native.actions.find(action => action.id === 'stale-source-rejected')?.native_process_launches !== 0 ||
    native.unreal.appearance_validated !== false || native.unreal.attempts[0].status !== 'failed' ||
    native.unreal.attempts.at(-1).status !== 'verified') {
  throw new Error('Source-binding demo must preserve both failure and target verification boundaries');
}
const resources = await readFile(`${sourceRoot}/observed-resource-proof.json`);
if (!resources.equals(await readFile(new URL('../../examples/source_binding/observed-resource-proof.json', import.meta.url)))) {
  throw new Error('Resource boundary evidence differs from its native record');
}
console.log(`Verified ${Object.keys(sourceManifest).length} source-binding assets and recorded native revision/transfer evidence.`);
