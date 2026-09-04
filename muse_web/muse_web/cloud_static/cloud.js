const params = new URLSearchParams(location.search);
const role = params.get('role') === 'operator' ? 'operator' : 'pipeline';
const researcher = document.querySelector('#researcher');
const operatorPanel = document.querySelector('#operator');
const message = document.querySelector('#message');
let agents = [];

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  }[character]));
}

function formatTime(seconds) {
  if (!seconds) return 'sin datos';
  return new Date(seconds * 1000).toLocaleTimeString();
}

function rate(item, signal) {
  return item?.rates?.[signal] ?? item?.received_hz?.[signal] ?? 0;
}

function agentIsFresh(agent) {
  if (!agent?.last_seen) return false;
  return Date.now() / 1000 - agent.last_seen < 8;
}

function websocketBase() {
  return `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}`;
}

function updateAccessLinks() {
  if (role !== 'pipeline') return;
  const selected = selectedAgent() === '__all__'
    ? (agents[0]?.agent_id || 'lab-windows-01')
    : (selectedAgent() || 'lab-windows-01');
  document.querySelector('#operator-link').value = (
    `${location.origin}/?role=operator&token=CLAVE_OPERADOR`
  );
  document.querySelector('#agent-link').value = (
    `${websocketBase()}/ws/agent/${selected}`
  );
}

document.querySelector('#page-title').textContent = (
  role === 'operator' ? 'Evaluacion del participante' : 'Panel del investigador'
);
(role === 'operator' ? operatorPanel : researcher).classList.remove('hidden');

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function selectElement() {
  return document.querySelector(role === 'operator' ? '#operator-agent' : '#agent');
}

function selectedAgent() {
  return selectElement().value;
}

function fillSelect(select, values, preferred) {
  const previous = select.value || preferred;
  select.innerHTML = values.map(item => (
    `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`
  )).join('');
  if (values.some(item => item.value === previous)) select.value = previous;
}

function selectedResearchAgents() {
  if (selectedAgent() === '__all__') return agents.map(item => item.agent_id);
  return selectedAgent() ? [selectedAgent()] : [];
}

function renderAgentCards() {
  const container = document.querySelector('#agents');
  if (!container) return;
  if (!agents.length) {
    container.innerHTML = '<article class="agent-card stale"><strong>Sin agente local</strong><span>Esperando conexion WebSocket</span></article>';
    return;
  }
  container.innerHTML = agents.map(agent => {
    const operators = agent.operators || [];
    const connected = agentIsFresh(agent);
    const session = agent.session || {};
    return `
      <article class="agent-card ${connected ? 'online' : 'stale'}">
        <div>
          <strong>${escapeHtml(agent.agent_id)}</strong>
          <span>${connected ? 'online' : 'sin pulso reciente'}</span>
        </div>
        <dl>
          <dt>Ultima senal</dt><dd>${escapeHtml(formatTime(agent.last_seen))}</dd>
          <dt>Sesion</dt><dd>${escapeHtml(session.running ? 'activa' : 'sin sesion')}</dd>
          <dt>Registro</dt><dd>${escapeHtml(session.recording ? 'grabando' : 'pausado')}</dd>
          <dt>Diademas</dt><dd>${operators.length}</dd>
        </dl>
      </article>
    `;
  }).join('');
}

function renderOperators(current) {
  const sourceAgents = selectedAgent() === '__all__' ? agents : (current ? [current] : []);
  const cards = sourceAgents.flatMap(agent => (
    (agent.operators || []).map(item => `
      <article class="card">
        <strong>${escapeHtml(item.operator_id || item.operator || 'operador')}</strong>
        <span>${escapeHtml(agent.agent_id)}</span>
        <dl>
          <dt>Estado</dt><dd>${escapeHtml(item.state || 'sin estado')}</dd>
          <dt>Bateria</dt><dd>${escapeHtml(item.battery_percent ?? 'no disponible')}</dd>
          <dt>EEG</dt><dd>${escapeHtml(rate(item, 'eeg'))} Hz</dd>
          <dt>IMU</dt><dd>${escapeHtml(rate(item, 'imu'))} Hz</dd>
          <dt>PPG</dt><dd>${escapeHtml(rate(item, 'ppg'))} Hz</dd>
          <dt>Reconexiones</dt><dd>${escapeHtml(item.reconnect_count ?? 0)}</dd>
        </dl>
      </article>
    `)
  ));
  document.querySelector('#operators').innerHTML = cards.length
    ? cards.join('')
    : '<article class="card"><strong>Sin diadema activa</strong><span>Prepara el pipeline cuando el agente este conectado.</span></article>';
}

function cognitiveScore(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(2) : 'sin modelo';
}

function renderCognitiveState(current) {
  const container = document.querySelector('#cognitive-state');
  if (!container) return;
  const sourceAgents = selectedAgent() === '__all__' ? agents : (current ? [current] : []);
  const cards = sourceAgents.flatMap(agent => {
    const cognitive = agent.session?.cognitive_state;
    if (!cognitive || cognitive.enabled === false) {
      return [`
        <article class="card cognitive-card">
          <strong>${escapeHtml(agent.agent_id)}</strong>
          <span>Monitoreo cognitivo desactivado</span>
        </article>
      `];
    }
    if (!cognitive.operators?.length) {
      return [`
        <article class="card cognitive-card">
          <strong>${escapeHtml(agent.agent_id)}</strong>
          <span>${escapeHtml(cognitive.state || 'esperando datos')}</span>
          <dl>
            <dt>Modelo</dt><dd>${escapeHtml(cognitive.model_loaded ? 'cargado' : 'sin configurar')}</dd>
            <dt>Ventana</dt><dd>${escapeHtml(cognitive.window_seconds || 60)} s</dd>
            <dt>Actualizacion</dt><dd>${escapeHtml(cognitive.update_seconds || 30)} s</dd>
          </dl>
        </article>
      `];
    }
    return cognitive.operators.map(item => {
      const factors = (item.top_factors || []).map(factor => (
        `<li>${escapeHtml(factor.feature)} <span>${Number(factor.contribution).toFixed(3)}</span></li>`
      )).join('');
      return `
        <article class="card cognitive-card">
          <strong>${escapeHtml(item.operator_id || 'operador')}</strong>
          <span>${escapeHtml(agent.agent_id)}</span>
          <div class="cognitive-score">${escapeHtml(cognitiveScore(item.score))}</div>
          <dl>
            <dt>Nivel</dt><dd>${escapeHtml(item.level || item.state || 'sin estimacion')}</dd>
            <dt>Confianza</dt><dd>${Math.round(Number(item.confidence || 0) * 100)}%</dd>
            <dt>EEG</dt><dd>${escapeHtml(item.n_eeg_samples || 0)} muestras</dd>
            <dt>PPG</dt><dd>${escapeHtml(item.n_ppg_samples || 0)} muestras</dd>
            <dt>Modalidad</dt><dd>${escapeHtml((item.model_modalities || cognitive.model_modalities || []).join('+') || 'no definida')}</dd>
            <dt>Metodo</dt><dd>${escapeHtml(item.method || cognitive.state)}</dd>
            ${Number.isFinite(Number(item.relative_scem))
              ? `<dt>SCEM relativo</dt><dd>${Number(item.relative_scem).toFixed(3)}</dd>`
              : ''}
            ${Number.isFinite(Number(item.calibration_anchor))
              ? `<dt>Calibracion</dt><dd>${Number(item.calibration_anchor).toFixed(2)}</dd>`
              : ''}
            ${Number.isFinite(Number(item.raw_score))
              ? `<dt>Sin suavizar</dt><dd>${Number(item.raw_score).toFixed(2)}</dd>`
              : ''}
            ${item.smoothing ? `<dt>Suavizado</dt><dd>${escapeHtml(item.smoothing)}</dd>` : ''}
          </dl>
          ${factors ? `<ol class="factor-list">${factors}</ol>` : ''}
          ${item.message ? `<p class="hint">${escapeHtml(item.message)}</p>` : ''}
        </article>
      `;
    });
  });
  container.innerHTML = cards.length
    ? cards.join('')
    : '<article class="card cognitive-card"><strong>Sin agente seleccionado</strong><span>Esperando conexion WebSocket</span></article>';
}

async function refresh() {
  try {
    const endpoint = role === 'operator'
      ? '/api/cloud/operator/agents'
      : '/api/cloud/agents';
    agents = (await api(endpoint)).agents;
    const ids = agents.map(item => ({value: item.agent_id, label: item.agent_id}));
    const choices = role === 'pipeline' && ids.length > 1
      ? [{value: '__all__', label: 'Todos los agentes'}, ...ids]
      : ids;
    fillSelect(selectElement(), choices, params.get('agent') || choices[0]?.value || '');
    const current = agents.find(item => item.agent_id === selectedAgent());
    const freshCount = agents.filter(agentIsFresh).length;
    document.querySelector('#connection').textContent = (
      freshCount
        ? `${freshCount} agente(s) conectado(s) por WebSocket`
        : 'No hay agentes locales conectados'
    );
    if (role === 'pipeline') {
      document.querySelector('#agent-count').textContent = (
        freshCount
          ? `${freshCount} agente(s) conectado(s)`
          : 'Sin agentes conectados'
      );
      updateAccessLinks();
    }
    if (role === 'operator') {
      fillSelect(
        document.querySelector('#operator-id'),
        (current?.operators || []).map(item => ({
          value: item.operator_id || item.operator,
          label: item.operator_id || item.operator,
        })),
        'operador_a',
      );
    } else {
      renderAgentCards();
      renderOperators(current);
      renderCognitiveState(current);
      updateAccessLinks();
      const sessions = selectedAgent() === '__all__'
        ? Object.fromEntries(agents.map(agent => [agent.agent_id, agent.session || {}]))
        : (current?.session || {});
      document.querySelector('#session').textContent = JSON.stringify(sessions, null, 2);
    }
  } catch (error) {
    message.textContent = error.message;
  }
}

async function commandForAgent(agentId, action, extra = {}) {
  const prefix = role === 'operator' ? '/api/cloud/operator' : '/api/cloud';
  const result = await api(`${prefix}/agents/${encodeURIComponent(agentId)}/commands`, {
    method: 'POST',
    body: JSON.stringify({action, ...extra}),
  });
  if (result.success === false) throw new Error(result.message || 'El agente rechazo el comando');
  return result;
}

async function command(action, extra = {}) {
  if (role === 'operator') return commandForAgent(selectedAgent(), action, extra);
  const targets = selectedResearchAgents();
  if (!targets.length) throw new Error('No hay agentes conectados');
  const results = await Promise.allSettled(
    targets.map(agentId => commandForAgent(agentId, action, extra)),
  );
  const failed = results
    .map((result, index) => ({result, agentId: targets[index]}))
    .filter(item => item.result.status === 'rejected');
  if (failed.length) {
    throw new Error(failed.map(item => (
      `${item.agentId}: ${item.result.reason.message}`
    )).join(' | '));
  }
  return {success: true, agents: targets};
}

document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async () => {
  try {
    const action = button.dataset.action;
    const extra = action === 'prepare_pipeline' ? {
      subject_code: document.querySelector('#subject').value,
      experiment: document.querySelector('#experiment').value,
      notes: document.querySelector('#notes').value,
      max_devices: Number(document.querySelector('#max-devices').value),
    } : {};
    const result = await command(action, extra);
    const count = result.agents?.length || 1;
    message.textContent = `Comando confirmado por ${count} agente(s).`;
    await refresh();
  } catch (error) {
    message.textContent = error.message;
  }
}));

const researcherAgentSelect = document.querySelector('#agent');
if (researcherAgentSelect) {
  researcherAgentSelect.addEventListener('change', updateAccessLinks);
}

const phrases = [
  ['task_engagement', 'Estuve involucrado/a con el tema que estaba trabajando.'],
  ['effort', 'Puse mucho esfuerzo.'],
  ['persistence', 'Me gustaria poder continuar con el trabajo un poco mas.'],
  ['flow', 'Estuve tan involucrado/a que olvide todo lo que ocurria a mi alrededor.'],
];
document.querySelector('#questions').innerHTML = phrases.map(([name, text]) => (
  `<fieldset class="question"><legend>${escapeHtml(text)}</legend><div class="scale">${
    [1, 2, 3, 4, 5].map(value => (
      `<label><input type="radio" name="${name}" value="${value}" required>${value}</label>`
    )).join('')
  }</div></fieldset>`
)).join('');

document.querySelector('#start-section').addEventListener('click', async () => {
  try {
    await command('start_section', {
      operator: document.querySelector('#operator-id').value,
      section_id: document.querySelector('#section').value,
    });
    message.textContent = 'Seccion iniciada.';
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelector('#submit-ground-truth').addEventListener('click', async () => {
  try {
    const payload = {
      operator: document.querySelector('#operator-id').value,
      section_id: document.querySelector('#section').value,
    };
    for (const [name] of phrases) {
      const checked = document.querySelector(`input[name="${name}"]:checked`);
      if (!checked) throw new Error('Contesta las cuatro afirmaciones.');
      payload[name] = Number(checked.value);
    }
    await command('submit_ground_truth', payload);
    document.querySelectorAll('#questions input').forEach(input => {
      input.checked = false;
    });
    message.textContent = 'Respuestas guardadas localmente. La escala quedo limpia.';
  } catch (error) {
    message.textContent = error.message;
  }
});

refresh();
setInterval(refresh, 2500);
