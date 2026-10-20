import '@google/model-viewer';
import './environment.css';

const root = '../assets/environment/';
const image = document.querySelector('#scene-image');
const viewer = document.querySelector('#scene-viewer');
const state = document.querySelector('#load-state');
const meshButton = document.querySelector('#mesh-mode');
let selected;
let data;
let meshMode = false;

function setCamera(camera) {
  if (!camera) return;
  // glTF coordinates: Blender (x,y,z) -> (x,z,-y).
  const convert = ([x,y,z]) => [x,z,-y];
  const p = convert(camera.translationMeters);
  const m = camera.matrixWorld;
  const forward = convert([-m[0][2], -m[1][2], -m[2][2]]);
  const norm = Math.hypot(...forward);
  const target = p.map((v,i) => v + forward[i] / norm * 2);
  const delta = p.map((v,i) => v - target[i]);
  viewer.cameraTarget = target.map(v => `${v}m`).join(' ');
  viewer.cameraOrbit = `${Math.atan2(delta[0],delta[2])*180/Math.PI}deg ${Math.acos(delta[1]/2)*180/Math.PI}deg 2m`;
  viewer.jumpCameraToGoal();
}
function showView(view) {
  selected = view;
  image.src = root + view.file;
  image.alt = view.caption;
  document.querySelector('#view-caption').textContent = meshMode
    ? `Interactive daytime GLB · ${view.label}. Native source geometry with browser lighting.`
    : view.caption;
  document.querySelector('#host-badge').textContent = meshMode ? 'Exported mesh · browser' : view.host;
  document.querySelectorAll('#view-list button').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.id === view.id)));
  if (meshMode) setCamera(view.camera);
}
function setMeshMode(value) {
  if (value && selected && !selected.host.startsWith('Blender · day')) {
    selected = data.views.find(view => view.host === 'Blender · day');
  }
  meshMode = value;
  viewer.hidden = !value;
  image.hidden = value;
  meshButton.textContent = value ? 'Return to native render' : 'Explore 3D mesh';
  state.textContent = value ? 'Drag to look around · scroll to zoom · browser lighting differs from native renders' : '';
  if (selected) showView(selected);
}
meshButton.addEventListener('click',() => setMeshMode(!meshMode));
viewer.addEventListener('load',() => { viewer.dataset.loaded='true'; if(selected) setCamera(selected.camera); });
viewer.addEventListener('error',() => {
  setMeshMode(false); meshButton.disabled = true;
  state.textContent = '3D unavailable in this browser. Native renders and downloads remain available.';
});
image.addEventListener('error',() => {state.textContent='This view could not load. Try another view or inspect the source files.';});
try {
  const response = await fetch(root+'case.json');
  if (!response.ok) throw new Error('Evidence unavailable');
  data = await response.json();
  const views = document.querySelector('#view-list');
  for(const view of data.views) {
    const button = document.createElement('button');
    button.dataset.id=view.id; button.textContent=view.label; button.setAttribute('aria-pressed','false');
    button.addEventListener('click',()=>{
      if (meshMode && view.host !== 'Blender · day') setMeshMode(false);
      showView(view);
    }); views.append(button);
  }
  showView(data.views[0]); state.textContent='';
  if(data.mesh) {viewer.src=root+data.mesh; meshButton.disabled=false;}
  for(const entry of data.stats) {
    const item=document.createElement('div'), strong=document.createElement('strong'), label=document.createElement('span');
    strong.textContent=entry.value; label.textContent=entry.label; item.append(strong,label); document.querySelector('#scene-stats').append(item);
  }
  for(const [selector,values] of [['#observed-checks',data.observed],['#case-limits',data.limitations]]) {
    for(const value of values) {const li=document.createElement('li');li.textContent=value;document.querySelector(selector).append(li);}
  }
  for(const item of data.downloads) {
    const link=document.createElement('a');link.textContent=item.label+' ↓';link.href=root+item.file;link.download=item.file;document.querySelector('#downloads').append(link);
  }
} catch {
  state.textContent='The scene evidence could not load. The source repository remains available.';
}
document.querySelector('#copy-brief').addEventListener('click',async()=>{
  try {await navigator.clipboard.writeText(document.querySelector('#chat-brief').textContent);document.querySelector('#copy-status').textContent='Brief copied';}
  catch {document.querySelector('#copy-status').textContent='Select and copy the brief above.';}
});
