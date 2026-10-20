import '@google/model-viewer';
import './style.css';

const viewer = document.querySelector('#tray-viewer');
const render = document.querySelector('#native-render');
const mode3d = document.querySelector('#mode-3d');
const modeRender = document.querySelector('#mode-render');
const reset = document.querySelector('#reset-view');
const hint = document.querySelector('#viewer-hint');
const loadState = document.querySelector('#load-state');

function setMode(threeD) {
  viewer.hidden = !threeD;
  render.hidden = threeD;
  reset.hidden = !threeD;
  hint.hidden = !threeD;
  mode3d.classList.toggle('active', threeD);
  modeRender.classList.toggle('active', !threeD);
  mode3d.setAttribute('aria-pressed', String(threeD));
  modeRender.setAttribute('aria-pressed', String(!threeD));
}
mode3d.addEventListener('click', () => setMode(true));
modeRender.addEventListener('click', () => setMode(false));
reset.addEventListener('click', () => {
  viewer.cameraOrbit = '35deg 45deg 0.25m';
  viewer.cameraTarget = 'auto auto auto';
  viewer.fieldOfView = 'auto';
});
viewer.addEventListener('load', () => {
  loadState.textContent = '3D mesh ready';
  viewer.dataset.loaded = 'true';
});
viewer.addEventListener('error', () => {
  setMode(false);
  mode3d.disabled = true;
  mode3d.textContent = '3D unavailable';
  document.querySelector('.model-caption').lastElementChild.textContent =
    '3D could not load in this browser. Native render and file downloads remain available.';
});

const labels = {
  one_connected_solid: 'One connected solid with positive volume',
  bounds_match_parameters: 'Measured bounds match each parameter configuration',
  four_mounting_bores_at_drawing_centres: 'Four board bores aligned with the drawing pattern',
  four_chassis_bores_through_plate: 'Four chassis holes pass through the base',
  bores_and_central_relief_are_void: 'Bores and central opening are geometrically void',
  all_sketches_fully_constrained: 'All sketches fully constrained',
  healthy_timeline: 'Native feature timeline remains healthy',
};
let checksum = '';
try {
  const [evidenceResponse, manifestResponse] = await Promise.all([
    fetch('./assets/evidence.json'), fetch('./assets/manifest.json'),
  ]);
  if (!evidenceResponse.ok || !manifestResponse.ok) throw new Error('Evidence unavailable');
  const evidence = await evidenceResponse.json();
  const manifest = await manifestResponse.json();
  checksum = manifest['rpi5-native-case.zip'].sha256;
  const list = document.querySelector('#native-checks');
  list.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const passed = evidence.baseline_reopened.checks[key] === true &&
      evidence.variant_reopened.checks[key] === true;
    const item = document.createElement('li');
    item.textContent = `${passed ? '✓' : '○'} ${label}`;
    item.className = passed ? 'passed' : 'unverified';
    list.append(item);
  }
  const tbody = document.querySelector('#bore-rows');
  evidence.baseline_reopened.mounting_bores.forEach((base, index) => {
    const revised = evidence.variant_reopened.mounting_bores[index];
    const row = document.createElement('tr');
    for (const value of [index + 1, base.centre_xy_mm.map(n => n.toFixed(1)).join(' / '),
      revised.centre_xy_mm.map(n => n.toFixed(1)).join(' / '), base.diameter_mm.toFixed(1)]) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    tbody.append(row);
  });
  document.querySelector('#triangle-count').textContent = evidence.browser_mesh.triangles.toLocaleString('en');
  document.querySelector('#mesh-count').textContent = evidence.browser_mesh.mesh_count;
  document.querySelector('#mesh-size').textContent = Math.round(evidence.browser_mesh.bytes / 1024);
} catch {
  document.querySelector('#native-checks').textContent = 'Evidence could not be loaded. Use the source links to inspect the recorded measurements.';
  document.querySelector('#copy-checksum').disabled = true;
}

document.querySelector('#copy-checksum').addEventListener('click', async () => {
  const status = document.querySelector('#checksum-status');
  try {
    await navigator.clipboard.writeText(checksum);
    status.textContent = 'SHA-256 copied';
  } catch {
    status.textContent = checksum;
  }
});
