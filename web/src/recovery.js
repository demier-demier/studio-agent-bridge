import './recovery.css';

const $ = (id) => document.getElementById(id);
let data;
let caseIndex = 0;
let stepIndex = 0;

function label(value) {
  return String(value).replaceAll('_', ' ');
}
function renderStep() {
  const scenario = data.cases[caseIndex];
  const step = scenario.steps[stepIndex];
  const report = step.report;
  $('step-number').textContent = `OBSERVATION ${String(stepIndex + 1).padStart(2, '0')}`;
  $('step-title').textContent = step.title;
  $('step-detail').textContent = step.detail;
  $('job-state').textContent = label(report?.status ?? step.status ?? 'not recorded');
  $('job-revision').textContent = step.revision ?? report?.revision ?? '—';
  $('adapter-calls').textContent = step.adapter_calls ?? '—';
  $('host-idle').textContent = report?.native_host_idle === 'unknown' || !report ? 'Unknown' : label(report.native_host_idle);
  const reconciliation = report?.reconciliation;
  $('decision').textContent = reconciliation
    ? `${reconciliation.allowed ? 'Explicit reconciliation available.' : reconciliation.required ? 'Recovery is blocked.' : 'No reconciliation required.'} ${reconciliation.reason}`
    : 'Recorded observation. No native action runs in this browser.';
  $('decision').dataset.kind = reconciliation?.required ? 'review' : 'normal';
  $('recorded-json').textContent = JSON.stringify(step, null, 2);
  $('position').textContent = `${stepIndex + 1} / ${scenario.steps.length}`;
  $('previous').disabled = stepIndex === 0;
  $('next').disabled = stepIndex === scenario.steps.length - 1;
  document.querySelectorAll('#timeline button').forEach((button, index) => {
    button.setAttribute('aria-current', index === stepIndex ? 'step' : 'false');
  });
}
function renderCase(index) {
  caseIndex = index;
  stepIndex = 0;
  const scenario = data.cases[index];
  $('case-title').textContent = scenario.title;
  $('case-description').textContent = scenario.description;
  $('timeline').replaceChildren();
  scenario.steps.forEach((step, i) => {
    const li = document.createElement('li');
    const button = document.createElement('button');
    const number = document.createElement('span');
    number.textContent = String(i + 1).padStart(2, '0');
    button.append(number, document.createTextNode(step.title));
    button.addEventListener('click', () => { stepIndex = i; renderStep(); });
    li.append(button); $('timeline').append(li);
  });
  $('case-checks').replaceChildren();
  for (const [name, passed] of Object.entries(scenario.checks)) {
    const li = document.createElement('li');
    li.textContent = `${passed === true ? 'PASS' : 'FAIL'} · ${label(name)}`;
    li.dataset.passed = String(passed === true);
    $('case-checks').append(li);
  }
  $('case-limits').replaceChildren();
  for (const limitation of scenario.limitations ?? []) {
    const li = document.createElement('li'); li.textContent = limitation; $('case-limits').append(li);
  }
  document.querySelectorAll('#case-list button').forEach((button, i) => button.setAttribute('aria-pressed', String(i === index)));
  renderStep();
}
$('previous').addEventListener('click', () => { if (stepIndex > 0) { stepIndex--; renderStep(); } });
$('next').addEventListener('click', () => { if (stepIndex < data.cases[caseIndex].steps.length - 1) { stepIndex++; renderStep(); } });
try {
  const response = await fetch('../assets/recovery.json');
  if (!response.ok) throw new Error('Evidence file unavailable');
  data = await response.json();
  if (!Array.isArray(data.cases) || !data.cases.length || data.cases.some(c => !c.steps?.length || !c.checks)) throw new Error('Invalid evidence file');
  data.cases.forEach((scenario, index) => {
    const button = document.createElement('button'); button.textContent = scenario.title;
    button.addEventListener('click', () => renderCase(index)); $('case-list').append(button);
  });
  $('provenance').textContent = `Recorded ${data.generated_at}. Source: ${data.provenance.generator}. Native applications used: ${data.provenance.native_apps_used ? 'yes' : 'no'}.`;
  $('load-status').textContent = '';
  renderCase(0);
} catch {
  $('load-status').textContent = 'Recovery evidence could not load. Download the source repository to reproduce the experiment.';
}
