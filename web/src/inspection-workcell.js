import '@google/model-viewer';
import './inspection-workcell.css';

const root = '../assets/inspection-workcell/';
const image = document.querySelector('#scene-image');
const viewer = document.querySelector('#scene-viewer');
const pending = document.querySelector('#pending-view');
const state = document.querySelector('#load-state');
const meshButton = document.querySelector('#mesh-mode');
const expectedFiles = {
  blender: {close: 'blender-close.png', wide: 'blender-wide.png'},
  unreal: {close: 'unreal-close.png', wide: 'unreal-wide.png'},
};
const requiredBlenderChecks = [
  'preserved_mesh_fields',
  'six_routes_geometry',
  'materials_resolve',
  'metre_units',
  'disconnected_endpoint_rejected',
];
let evidence;
let selectedHost = 'blender';
let selectedView = 'close';
let meshMode = false;
let imageRequest = 0;

function hasProvenance() {
  return evidence?.schema === 'studio-inspection-workcell-public/v1' &&
    evidence?.provenance?.type === 'observed-native-execution';
}

function verifiedHost(host) {
  return hasProvenance() && evidence?.[host]?.status === 'verified' &&
    evidence?.[host]?.fresh_reopen === true;
}

function verifiedBlenderChecks() {
  const checks = evidence?.blender?.checks;
  return checks !== null && typeof checks === 'object' && !Array.isArray(checks) &&
    Object.keys(checks).length === requiredBlenderChecks.length &&
    requiredBlenderChecks.every(key => Object.hasOwn(checks, key) && checks[key] === true);
}

function verifiedView(host, view) {
  const record = evidence?.[host]?.views?.[view];
  return verifiedHost(host) && record?.status === 'verified' &&
    record?.file === expectedFiles[host][view];
}

function verifiedArtifact(kind) {
  const record = evidence?.artifacts?.[kind];
  if (!hasProvenance() || record?.status !== 'verified') return false;
  if (kind === 'glb') return record.file === 'inspection-utilities.glb';
  return record.publication_checked === true &&
    /^inspection-workcell-native-v[2-9]\d*\.zip$/.test(record.file);
}

function setPending(title, description) {
  image.hidden = true;
  pending.hidden = false;
  document.querySelector('#pending-title').textContent = title;
  document.querySelector('#pending-description').textContent = description;
}

function setCamera(cameraRecord) {
  const camera = cameraRecord?.transform;
  const validVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
  if (!camera || !validVector(camera.translationMeters) || !Array.isArray(camera.matrixWorld) ||
      camera.matrixWorld.length !== 4 || camera.matrixWorld.some(row => !Array.isArray(row) || row.length !== 4 || !row.every(Number.isFinite))) {
    viewer.cameraTarget = '-3.8m 1.1m -0.15m';
    viewer.cameraOrbit = selectedView === 'close' ? '42deg 68deg 5.5m' : '48deg 68deg 13m';
    viewer.jumpCameraToGoal();
    return;
  }
  // Native Blender coordinates become glTF (x,z,-y); use the recorded camera.
  const convert = ([x, y, z]) => [x, z, -y];
  const position = convert(camera.translationMeters);
  const matrix = camera.matrixWorld;
  const forward = convert([-matrix[0][2], -matrix[1][2], -matrix[2][2]]);
  const norm = Math.hypot(...forward);
  if (norm < 1e-8) return;
  const distance = selectedView === 'close' ? 4 : 8;
  const target = position.map((value, i) => value + forward[i] / norm * distance);
  const delta = position.map((value, i) => value - target[i]);
  viewer.cameraTarget = target.map(value => `${value}m`).join(' ');
  viewer.cameraOrbit = `${Math.atan2(delta[0], delta[2]) * 180 / Math.PI}deg ${Math.acos(Math.max(-1, Math.min(1, delta[1] / distance))) * 180 / Math.PI}deg ${distance}m`;
  viewer.jumpCameraToGoal();
}

function showSelection() {
  imageRequest += 1;
  const request = imageRequest;
  const hostLabel = selectedHost === 'blender' ? 'Blender' : 'Unreal';
  const framing = selectedView === 'close' ? 'Close' : 'Wide';
  document.querySelectorAll('[data-host]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.host === selectedHost)));
  document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === selectedView)));
  viewer.hidden = !meshMode;
  meshButton.textContent = meshMode ? 'Return to native render' : 'Explore 3D mesh';
  if (meshMode) {
    image.hidden = true;
    pending.hidden = true;
    document.querySelector('#host-badge').textContent = 'GLB · browser lighting';
    document.querySelector('#view-caption').textContent = `${framing} framing · exported surface geometry with browser lighting. This view is separate from the native Blender and Unreal images.`;
    state.textContent = viewer.dataset.loaded === 'true' ? 'Drag to orbit · scroll to zoom' : 'Loading the exported 3D scene…';
    setCamera(evidence?.blender?.views?.[selectedView]?.camera);
    return;
  }
  state.textContent = '';
  if (!verifiedView(selectedHost, selectedView)) {
    document.querySelector('#host-badge').textContent = `${hostLabel} · pending`;
    document.querySelector('#view-caption').textContent = `${hostLabel} ${selectedView} view · verified native image evidence is pending.`;
    setPending(`${hostLabel} ${selectedView} view pending`, selectedHost === 'unreal'
      ? 'A verified Unreal handoff, fresh reopening and a native image record are required before this view is shown.'
      : 'A verified Blender revision, fresh reopening and a native image record are required before this view is shown.');
    return;
  }
  document.querySelector('#host-badge').textContent = `${hostLabel} · native render`;
  document.querySelector('#view-caption').textContent = `${hostLabel} native renderer · ${selectedView} view of this utility revision. Separate renderers may produce different materials, lighting and shadows.`;
  setPending(`Loading ${hostLabel} ${selectedView} view`, 'Retrieving the recorded native image.');
  image.onload = () => {
    if (request !== imageRequest || meshMode) return;
    pending.hidden = true;
    image.hidden = false;
  };
  image.onerror = () => {
    if (request !== imageRequest || meshMode) return;
    document.querySelector('#host-badge').textContent = `${hostLabel} · image unavailable`;
    setPending('The recorded image could not load', 'Try another view, or inspect the public evidence and source repository below.');
  };
  image.alt = `${framing} native ${hostLabel} render of the original inspection workcell utility revision`;
  image.src = root + expectedFiles[selectedHost][selectedView];
}

function addCheck(text) {
  const item = document.createElement('li');
  item.textContent = text;
  document.querySelector('#observed-checks').append(item);
}

function showEvidence() {
  const blender = verifiedHost('blender');
  const unreal = verifiedHost('unreal');
  const geometricChecks = verifiedBlenderChecks();
  const routes = blender && geometricChecks && evidence.blender.verified_routes === 6;
  const preserved = blender && geometricChecks && evidence.blender.preserved_source_meshes === 1820;
  const unrealImages = unreal && ['close', 'wide'].every(view => verifiedView('unreal', view));
  document.querySelector('#route-count').textContent = routes ? '6' : 'Pending';
  document.querySelector('#preserved-count').textContent = preserved ? '1,820' : 'Pending';
  document.querySelector('#blender-state').textContent = blender ? 'Reopened' : 'Pending';
  document.querySelector('#unreal-state').textContent = unrealImages ? 'Reopened' : 'Pending';
  document.querySelector('#revision-status').textContent = blender && unrealImages
    ? 'Blender readback and Unreal image evidence available'
    : blender ? 'Blender readback available · Unreal evidence pending' : 'Native revision evidence pending';
  document.querySelector('#evidence-summary').textContent = routes && preserved
    ? 'Fresh native readback records six attached routes and preservation of 1,820 pre-existing mesh records. Each renderer and download has its own verification state.'
    : 'Completed checks appear when the public record supplies verified results. Missing evidence remains pending.';
  document.querySelector('#observed-checks').replaceChildren();
  addCheck(routes ? 'Six saved editable routes passed the declared endpoint and gland attachment checks.' : 'Route endpoint and attachment verification: pending.');
  addCheck(preserved ? '1,820 pre-existing mesh records retained the declared fields across saved native readback.' : 'Preservation of pre-existing mesh fields: pending.');
  addCheck(blender ? 'The Blender revision was read back after fresh reopening.' : 'Fresh Blender reopening: pending.');
  addCheck(unrealImages ? 'The Unreal handoff has fresh reopening and native close and wide image records.' : 'Fresh Unreal reopening and both native image records: pending.');
  for (const host of ['blender', 'unreal']) {
    const count = ['close', 'wide'].filter(view => verifiedView(host, view)).length;
    document.querySelector(`#${host}-view-status`).textContent = count === 2 ? '2 views' : count === 1 ? '1 view' : 'pending';
  }
  for (const limit of Array.isArray(evidence.limitations) ? evidence.limitations : []) {
    if (typeof limit !== 'string' || !limit.trim()) continue;
    const item = document.createElement('li');
    item.textContent = limit;
    document.querySelector('#case-limits').append(item);
  }
  document.querySelector('#evidence-download').hidden = false;
  const glb = verifiedArtifact('glb');
  const native = verifiedArtifact('native');
  for (const [kind, available] of [['glb', glb], ['native', native]]) {
    const link = document.querySelector(`#${kind}-download`);
    link.hidden = !available;
    if (available) {
      link.href = root + evidence.artifacts[kind].file;
      link.download = evidence.artifacts[kind].file;
    }
  }
  document.querySelector('#download-status').textContent = native && glb
    ? 'Publication-checked native package and verified GLB export available.'
    : native ? 'Publication-checked native package available. GLB export pending.'
      : glb ? 'Verified GLB export available. Native publication checks pending.'
        : 'Native publication checks and GLB export verification pending.';
  if (glb) {
    viewer.src = root + evidence.artifacts.glb.file;
    meshButton.disabled = false;
  }
}

document.querySelectorAll('[data-host]').forEach(button => button.addEventListener('click', () => {
  selectedHost = button.dataset.host;
  meshMode = false;
  showSelection();
}));
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
  selectedView = button.dataset.view;
  showSelection();
}));
meshButton.addEventListener('click', () => {
  if (!verifiedArtifact('glb')) return;
  meshMode = !meshMode;
  showSelection();
});
viewer.addEventListener('load', () => {
  viewer.dataset.loaded = 'true';
  if (meshMode) showSelection();
});
viewer.addEventListener('error', () => {
  meshMode = false;
  meshButton.disabled = true;
  showSelection();
  state.textContent = '3D is unavailable in this browser. Recorded native images and downloads remain separate.';
});

try {
  const response = await fetch(root + 'evidence.json');
  if (!response.ok) throw new Error('Public evidence unavailable');
  evidence = await response.json();
  if (!hasProvenance()) throw new Error('Public evidence schema or provenance unavailable');
  showEvidence();
} catch {
  evidence = undefined;
  document.querySelector('#revision-status').textContent = 'Public evidence unavailable · verification pending';
  document.querySelector('#evidence-summary').textContent = 'The public evidence record could not be verified here. Reproduction scripts remain available in the source repository.';
  document.querySelector('#observed-checks').replaceChildren();
  addCheck('The public evidence schema and native-execution provenance must load before measured claims are displayed.');
}
showSelection();
