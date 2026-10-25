import './source-binding.css';

const $ = id => document.getElementById(id);
const compare = $('compare');
function reveal() {
  const value = Number(compare.value);
  $('after-layer').style.clipPath = `inset(0 0 0 ${100 - value}%)`;
  $('divider').style.left = `${100 - value}%`;
  $('compare-value').textContent = `${value}% revised`;
  document.querySelector('.before-label').hidden = value === 100;
  document.querySelector('.after-label').hidden = value === 0;
  compare.setAttribute('aria-valuetext', `${value}% revised render`);
}
compare.addEventListener('input', reveal);
reveal();
function list(target, values) {
  $(target).replaceChildren(...values.map(text => {
    const item = document.createElement('li'); item.textContent = text; return item;
  }));
}
try {
  const response = await fetch('../assets/source-binding/case.json');
  if (!response.ok) throw new Error('Native evidence is unavailable.');
  const evidence = await response.json();
  if (evidence.schema !== 'source-binding-browser-case/v1' || evidence.kind !== 'recorded-native-execution') {
    throw new Error('Unexpected evidence format.');
  }
  $('render-tag').textContent = evidence.render.host;
  $('render-caption').textContent = evidence.render.caption;
  for (const [id, text] of Object.entries(evidence.metrics)) $(id).textContent = text;
  $('steps').replaceChildren(...evidence.steps.map((step, index) => {
    const li = document.createElement('li');
    const number = document.createElement('span'); number.className = 'step-number'; number.textContent = `0${index + 1}`;
    const content = document.createElement('div');
    const title = document.createElement('h3'); title.textContent = step.title;
    const detail = document.createElement('p'); detail.textContent = step.detail;
    const result = document.createElement('span'); result.className = 'step-result'; result.textContent = step.result;
    result.dataset.status = step.status;
    content.append(title, detail, result); li.append(number, content); return li;
  }));
  for (const [name, value] of Object.entries(evidence.identity)) {
    const term = document.createElement('dt'); term.textContent = name;
    const detail = document.createElement('dd'); detail.textContent = value;
    $('identity').append(term, detail);
  }
  list('checks', evidence.checks);
  list('unreal-checks', evidence.unreal.checks);
  list('limits', evidence.limitations);
  $('unreal-summary').textContent = evidence.unreal.summary;
  $('provenance').textContent = evidence.provenance;
  $('load-status').textContent = '';
} catch {
  $('load-status').textContent = 'Recorded evidence is unavailable. Reload this page, download the raw record, or open the source repository.';
}

// This follow-up is a separate native record; never infer it from the older case.
try {
  const response = await fetch('../assets/unreal-preflight/observed-native-proof.json');
  if (!response.ok) throw new Error('Unreal follow-up evidence unavailable.');
  const proof = await response.json();
  if (proof.schema_version !== 'unreal-preflight-native-example/v1' ||
      proof.provenance.kind !== 'observed-native-execution' ||
      proof.provenance.synthetic_adapter_used !== false || !proof.attempts.length ||
      !Object.keys(proof.checks).length || Object.values(proof.checks).some(value => value !== true)) {
    throw new Error('Unexpected follow-up record.');
  }
  const titles = {
    'reject-before-project': 'Before a project exists',
    'create-owned-project': 'Create the owned project',
    'reject-existing-project': 'Preserve an existing project',
    'explicit-gpu-build': 'Build with explicit GPU mode',
    'fresh-gpu-inspection': 'Reopen in a fresh process',
  };
  const buttons = [];
  const show = (attempt, index) => {
    buttons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === index)));
    $('attempt-title').textContent = titles[attempt.id] ?? attempt.id.replaceAll('-', ' ');
    const blocked = attempt.status === 'blocked';
    $('attempt-description').textContent = blocked
      ? 'The known compatibility rule rejected this request before Unreal launched. The project tree was preserved; the Engine still recorded the action and advanced its revision.'
      : attempt.id === 'fresh-gpu-inspection'
        ? `A separate editor process reopened ${proof.target.mesh_actors.toLocaleString('en-US')} mesh actors, ${proof.target.mesh_assets} shared mesh assets, ${proof.target.materials} materials, ${proof.target.lights} lights and ${proof.target.cameras} cameras. No image was captured in this run.`
        : attempt.id === 'explicit-gpu-build'
          ? 'The client explicitly selected GPU mode in a separate stage. Unreal imported and assembled the original public laboratory in a fresh destination.'
          : 'The real adapter created an owned project and verified its native bootstrap. This action uses the ordinary commandlet path.';
    const facts = {
      'Observed status': attempt.status,
      'Native process launches': attempt.native_process_launches,
      'Rendering mode': attempt.rendering,
      'Full editor session': attempt.editor_session ? 'Yes' : 'No',
      'Engine revision': `${attempt.revision_before} → ${attempt.revision_after}`,
      ...(blocked ? {'Project tree unchanged': attempt.project_effects.unchanged ? 'Yes' : 'No'} : {'Native exit code': attempt.returncode ?? 'Not reported'}),
    };
    $('attempt-facts').replaceChildren(...Object.entries(facts).map(([label, value]) => {
      const cell = document.createElement('div');
      const term = document.createElement('dt'); term.textContent = label;
      const detail = document.createElement('dd'); detail.textContent = String(value);
      cell.append(term, detail); return cell;
    }));
  };
  proof.attempts.forEach((attempt, index) => {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = `${index + 1}. ${titles[attempt.id] ?? attempt.id.replaceAll('-', ' ')}`;
    button.addEventListener('click', () => show(attempt, index));
    buttons.push(button); $('preflight-options').append(button);
  });
  list('preflight-checks', Object.keys(proof.checks).map(name => name.replaceAll('_', ' ')));
  list('preflight-limits', proof.limitations);
  show(proof.attempts[0], 0);
  $('preflight-evidence').hidden = false;
  $('preflight-status').textContent = '';
} catch {
  $('preflight-status').textContent = 'The follow-up evidence is unavailable. Reload this page or open the reproduction case on GitHub. The earlier revision record remains available above.';
}
