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
