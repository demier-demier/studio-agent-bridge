import {readFile, access} from 'node:fs/promises';
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

const publicationBytes = await readFile(`${environmentRoot}/publication-proof.json`);
const publicationSource = await readFile(new URL('../../examples/publication_safety/observed-native-proof.json', import.meta.url));
if (!publicationBytes.equals(publicationSource)) throw new Error('Publication demo differs from its native record');
const publication = JSON.parse(publicationBytes);
const transferBytes = await readFile(`${environmentRoot}/environment-manifest.json`);
const publishedArchive = environmentManifest[publication.archive.filename];
if (publication.schema_version !== 'publication-safety-native-example/v1' ||
    publication.provenance.kind !== 'observed-native-execution' ||
    publication.provenance.synthetic_adapter_used !== false ||
    !Object.keys(publication.checks).length || Object.values(publication.checks).some(value => value !== true) ||
    publication.archive.filename !== 'industrial-lab-native-v2.zip' || publication.archive.scan_passed !== true ||
    publishedArchive?.sha256 !== publication.archive.sha256 || publishedArchive?.bytes !== publication.archive.bytes ||
    publication.scenes.length !== 2 || publication.scenes.some(scene =>
      scene.scan.passed !== true || scene.comparison.reopened.passed !== true ||
      scene.comparison.reopened.exact_categories_equal !== true ||
      !Number.isFinite(scene.comparison.reopened.max_evaluated_float_residual) ||
      scene.comparison.reopened.evaluated_float_tolerance !== 1e-6 ||
      scene.comparison.reopened.max_evaluated_float_residual > scene.comparison.reopened.evaluated_float_tolerance) ||
    publication.fbx.typed_comparison_passed !== true ||
    publication.fbx.publication_manifest_sha256 !== createHash('sha256').update(transferBytes).digest('hex') ||
    publication.unreal.target.status !== 'verified' || publication.unreal.target.reopened !== true ||
    publication.unreal.target.appearance_validated !== false ||
    publication.unreal.target.manifest_sha256 !== publication.fbx.publication_manifest_sha256 ||
    publication.unreal.attempts.length !== 3 || publication.unreal.attempts.some(action => action.status !== 'verified') ||
    !publication.unchanged_members.all_byte_identical ||
    !environment.downloads.some(entry => entry.file === publication.archive.filename) ||
    environment.downloads.some(entry => entry.file === 'industrial-lab-native.zip')) {
  throw new Error('Invalid versioned publication provenance, asset identity or acceptance boundary');
}
try {
  await access(`${environmentRoot}/industrial-lab-native.zip`);
  throw new Error('Historical native archive must not remain a current download');
} catch (error) { if (error.code !== 'ENOENT') throw error; }
console.log(`Verified v${publication.archive.version} publication identity, ${publication.scenes.length} reopened scenes and ${publication.fbx.files} checked FBX files; historical renders remain separate.`);

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

const preflightBytes = await readFile(`${assets}/unreal-preflight/observed-native-proof.json`);
const preflightSource = await readFile(new URL('../../examples/unreal_preflight/observed-native-proof.json', import.meta.url));
if (!preflightBytes.equals(preflightSource)) throw new Error('Unreal preflight demo differs from its native record');
const preflight = JSON.parse(preflightBytes);
const rejected = preflight.attempts.filter(attempt => attempt.id.startsWith('reject-'));
const gpuBuild = preflight.attempts.find(attempt => attempt.id === 'explicit-gpu-build');
const gpuInspect = preflight.attempts.find(attempt => attempt.id === 'fresh-gpu-inspection');
if (preflight.schema_version !== 'unreal-preflight-native-example/v1' ||
    preflight.provenance.kind !== 'observed-native-execution' ||
    preflight.provenance.synthetic_adapter_used !== false ||
    !Object.keys(preflight.checks).length || Object.values(preflight.checks).some(value => value !== true) ||
    rejected.length !== 2 || rejected.some(attempt => attempt.status !== 'blocked' ||
      attempt.native_process_launches !== 0 || attempt.subprocess_launches !== 0 ||
      attempt.project_effects.unchanged !== true || attempt.revision_after <= attempt.revision_before ||
      attempt.preflight?.code !== 'unreal_macos_5_7_4_environment_nullrhi') ||
    [gpuBuild, gpuInspect].some(attempt => !attempt || attempt.status !== 'verified' ||
      attempt.rendering !== 'gpu' || attempt.native_process_launches !== 1 || attempt.returncode !== 0) ||
    preflight.target.reopened !== true || preflight.target.appearance_validated !== false) {
  throw new Error('Invalid Unreal preflight native execution boundary');
}
console.log(`Verified ${preflight.attempts.length} actual Unreal actions and ${Object.keys(preflight.checks).length} preflight/transfer checks; no appearance claim.`);

const workcellRoot = `${assets}/inspection-workcell`;
const workcellBytes = await readFile(`${workcellRoot}/evidence.json`);
const workcellProof = await readFile(new URL('../../examples/inspection_workcell/observed-utilities-proof.json', import.meta.url));
if (!workcellBytes.equals(workcellProof)) throw new Error('Workcell demo differs from its native evidence');
const workcell = JSON.parse(workcellBytes);
const workcellManifest = JSON.parse(await readFile(`${workcellRoot}/manifest.json`, 'utf8'));
for (const [name, item] of Object.entries(workcellManifest)) {
  if (!/^[a-zA-Z0-9_.-]+$/.test(name)) throw new Error('Unsafe workcell asset filename');
  const bytes = await readFile(`${workcellRoot}/${name}`);
  if (bytes.length !== item.bytes || createHash('sha256').update(bytes).digest('hex') !== item.sha256) {
    throw new Error(`Workcell asset integrity failed: ${name}`);
  }
}
const requiredWorkcellChecks = ['preserved_mesh_fields', 'six_routes_geometry', 'materials_resolve', 'metre_units', 'disconnected_endpoint_rejected'];
if (workcell.schema !== 'studio-inspection-workcell-public/v1' ||
    workcell.provenance.type !== 'observed-native-execution' || workcell.provenance.synthetic_adapter_used !== false ||
    workcell.blender.status !== 'verified' || workcell.blender.fresh_reopen !== true ||
    workcell.blender.preserved_source_meshes !== 1820 || workcell.blender.verified_routes !== 6 ||
    Object.keys(workcell.blender.checks).length !== requiredWorkcellChecks.length ||
    requiredWorkcellChecks.some(key => workcell.blender.checks[key] !== true) ||
    workcell.unreal.status !== 'verified' || workcell.unreal.fresh_reopen !== true ||
    workcell.unreal.manifest_sha256 !== workcell.transfer.clean_manifest_sha256 ||
    workcell.unreal.appearance_equivalence_validated !== false ||
    !Object.values(workcell.transfer.editable_scene.comparison).every(value => value.passed === true && value.max_evaluated_float_residual <= 1e-6) ||
    workcell.transfer.fbx_typed_tree_checks_passed !== true ||
    workcell.artifacts.native.publication_checked !== true) {
  throw new Error('Workcell acceptance boundary or source-to-target evidence is incomplete');
}
for (const host of ['blender', 'unreal']) {
  for (const view of ['close', 'wide']) {
    const record = workcell[host].views[view];
    if (record.status !== 'verified' || record.file !== `${host}-${view}.png` ||
        workcellManifest[record.file]?.sha256 !== record.sha256 ||
        workcellManifest[record.file]?.bytes !== record.bytes || record.rgb_pixels_preserved !== true) {
      throw new Error(`Workcell ${host}/${view} image is not bound to its evidence`);
    }
  }
}
for (const record of Object.values(workcell.artifacts)) {
  if (record.status !== 'verified' || workcellManifest[record.file]?.sha256 !== record.sha256 ||
      workcellManifest[record.file]?.bytes !== record.bytes) throw new Error('Workcell artifact differs from its proof');
}
if (workcellManifest['evidence.json']?.sha256 !== createHash('sha256').update(workcellBytes).digest('hex')) {
  throw new Error('Workcell asset manifest does not bind its proof');
}
console.log(`Verified ${Object.keys(workcellManifest).length} workcell assets, six measured routes and separate Blender/Unreal image evidence.`);
