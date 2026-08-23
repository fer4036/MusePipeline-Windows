const params = new URLSearchParams(location.search);
const role = params.get('role') === 'operator' ? 'operator' : 'pipeline';
const researcher = document.querySelector('#researcher');
const operatorPanel = document.querySelector('#operator');
const message = document.querySelector('#message');
let agents = [];

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
}

document.querySelector('#page-title').textContent = role === 'operator' ? 'Evaluación del participante' : 'Panel del investigador';
(role === 'operator' ? operatorPanel : researcher).classList.remove('hidden');

async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, ...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function selectedAgent() {
  return document.querySelector(role === 'operator' ? '#operator-agent' : '#agent').value;
}

function fillSelect(select, values, preferred) {
  const previous = select.value || preferred;
  select.innerHTML = values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
  if (values.includes(previous)) select.value = previous;
}

async function refresh() {
  try {
    const endpoint = role === 'operator' ? '/api/cloud/operator/agents' : '/api/cloud/agents';
    agents = (await api(endpoint)).agents;
    const ids = agents.map(item => item.agent_id);
    fillSelect(document.querySelector(role === 'operator' ? '#operator-agent' : '#agent'), ids, params.get('agent'));
    const current = agents.find(item => item.agent_id === selectedAgent());
    document.querySelector('#connection').textContent = current ? `Agente ${current.agent_id} conectado` : 'No hay un agente local conectado';
    if (role === 'operator') {
      fillSelect(document.querySelector('#operator-id'), (current?.operators || []).map(item => item.operator_id || item.operator), 'operador_a');
    } else {
      document.querySelector('#operators').innerHTML = (current?.operators || []).map(item => `<article class="card"><strong>${escapeHtml(item.operator_id || item.operator)}</strong><br>${escapeHtml(item.state || 'Sin estado')}<br>Batería: ${escapeHtml(item.battery_percent ?? 'No disponible')}<br>EEG: ${escapeHtml(item.rates?.eeg ?? item.received_hz?.eeg ?? 0)} Hz</article>`).join('');
      document.querySelector('#session').textContent = JSON.stringify(current?.session || {}, null, 2);
    }
  } catch (error) { message.textContent = error.message; }
}

async function command(action, extra = {}) {
  const prefix = role === 'operator' ? '/api/cloud/operator' : '/api/cloud';
  const result = await api(`${prefix}/agents/${encodeURIComponent(selectedAgent())}/commands`, {method: 'POST', body: JSON.stringify({action, ...extra})});
  if (result.success === false) throw new Error(result.message || 'El agente rechazó el comando');
  return result;
}

document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async () => {
  try {
    const action = button.dataset.action;
    const extra = action === 'prepare_pipeline' ? {subject_code: document.querySelector('#subject').value, experiment: document.querySelector('#experiment').value, notes: document.querySelector('#notes').value, max_devices: Number(document.querySelector('#max-devices').value)} : {};
    await command(action, extra); message.textContent = 'Comando confirmado por el agente local.'; await refresh();
  } catch (error) { message.textContent = error.message; }
}));

const phrases = [
  ['task_engagement', 'Estuve involucrado/a con el tema que estaba trabajando.'],
  ['effort', 'Puse mucho esfuerzo.'],
  ['persistence', 'Me gustaría poder continuar con el trabajo un poco más.'],
  ['flow', 'Estuve tan involucrado/a que olvidé todo lo que ocurría a mi alrededor.'],
];
document.querySelector('#questions').innerHTML = phrases.map(([name, text]) => `<fieldset class="question"><legend>${text}</legend><div class="scale">${[1,2,3,4,5].map(value => `<label><input type="radio" name="${name}" value="${value}" required>${value}</label>`).join('')}</div></fieldset>`).join('');

document.querySelector('#start-section').addEventListener('click', async () => {
  try { await command('start_section', {operator: document.querySelector('#operator-id').value, section_id: document.querySelector('#section').value}); message.textContent = 'Sección iniciada.'; }
  catch (error) { message.textContent = error.message; }
});

document.querySelector('#submit-ground-truth').addEventListener('click', async () => {
  try {
    const payload = {operator: document.querySelector('#operator-id').value, section_id: document.querySelector('#section').value};
    for (const [name] of phrases) { const checked = document.querySelector(`input[name="${name}"]:checked`); if (!checked) throw new Error('Contesta las cuatro afirmaciones.'); payload[name] = Number(checked.value); }
    await command('submit_ground_truth', payload);
    document.querySelectorAll('#questions input').forEach(input => { input.checked = false; });
    message.textContent = 'Respuestas guardadas localmente. La escala quedó limpia.';
  } catch (error) { message.textContent = error.message; }
});

refresh();
setInterval(refresh, 2500);
