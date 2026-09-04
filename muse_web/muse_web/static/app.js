const query = new URLSearchParams(location.search);
const requestedRole = query.get('role');
const suppliedToken = query.get('token') || query.get('access');
if (suppliedToken && ['operator', 'pipeline'].includes(requestedRole)) {
  sessionStorage.setItem('muse_access_token', suppliedToken);
  sessionStorage.setItem('muse_access_role', requestedRole);
  history.replaceState(null, '', `/?role=${requestedRole}`);
} else if (['operator', 'pipeline'].includes(requestedRole)) {
  // The server already exchanged the URL token for an HttpOnly cookie.
  // Remove credentials left by versions that relied only on sessionStorage.
  sessionStorage.removeItem('muse_access_token');
  sessionStorage.setItem('muse_access_role', requestedRole);
}
const accessRole = requestedRole || sessionStorage.getItem('muse_access_role') || 'operator';
const accessToken = sessionStorage.getItem('muse_access_token') || '';
const isResearcher = accessRole === 'pipeline';
const mutationHeaders = {'Content-Type': 'application/json', 'X-Muse-Request': 'muse-web-ui'};
const $ = (selector) => document.querySelector(selector);

const form = $('#session-form');
const prepareButton = $('#prepare-button');
const recordButton = $('#record-button');
const pauseButton = $('#pause-button');
const finishButton = $('#finish-button');
const previewButton = $('#preview-button');
const graphButton = $('#graph-button');
const message = $('#form-message');
const statusText = $('#pipeline-status');
const activeSession = $('#active-session');
const statusDot = $('#status-dot');
const recordingDot = $('#recording-dot');
const recordingStatus = $('#recording-status');
const recordingTime = $('#recording-time');
const logOutput = $('#log-output');
const graphOutput = $('#graph-output');
const usersGrid = $('#users-grid');
const previewOutput = $('#database-preview');
const sessionsBody = $('#sessions-body');
const workshopSections = $('#workshop-sections');
const workshopSummary = $('#workshop-summary');
const groundTruthForm = $('#ground-truth-form');
const groundTruthOperator = $('#ground-truth-operator');
const groundTruthSection = $('#ground-truth-section');
const groundTruthSubmit = $('#ground-truth-submit');
const groundTruthMessage = $('#ground-truth-message');
const engagementItems = $('#engagement-items');
const assessmentTiming = $('#assessment-timing');
const assessmentStatus = $('#assessment-status');
const assessmentCountdown = $('#assessment-countdown');
let latestStatus = null;
let latestWorkshop = null;

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  const response = await fetch(path, {...options, headers});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Error HTTP ${response.status}`);
  return body;
}

function escapeHtml(value = '') {
  const element = document.createElement('span');
  element.textContent = String(value);
  return element.innerHTML;
}

function showMessage(text, error = false) {
  message.textContent = text;
  message.classList.toggle('error', error);
}

function formatDuration(totalSeconds = 0) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = String(Math.floor(seconds / 3600)).padStart(2, '0');
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
  const s = String(seconds % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function recordingSeconds(session) {
  const intervals = session?.recording_intervals || [];
  return intervals.reduce((total, interval) => {
    const start = Date.parse(interval.started_at);
    const end = interval.ended_at ? Date.parse(interval.ended_at) : Date.now();
    return total + (Number.isFinite(start) && Number.isFinite(end) ? (end - start) / 1000 : 0);
  }, 0);
}

function stateLabel(state) {
  return ({streaming: 'Transmitiendo', connecting: 'Conectando', connected: 'Enlace BLE', reconnecting: 'Reconectando', waiting_for_device: 'Esperando diadema', disconnected: 'Desconectado', data_timeout: 'Sin datos', error: 'Error'})[state] || state;
}

function renderOperators(operators = []) {
  if (!operators.length) {
    usersGrid.innerHTML = '<p class="empty-state">Buscando diademas Muse…</p>';
    return;
  }
  usersGrid.innerHTML = operators.map((user) => {
    const connected = user.state === 'streaming';
    const battery = user.battery_percent == null ? 'No disponible' : `${Number(user.battery_percent).toFixed(0)}%`;
    const rates = user.rates || {};
    return `<article class="user-card ${connected ? 'online' : ''}">
      <div class="user-header"><div><span class="user-dot"></span><strong>${escapeHtml(user.operator)}</strong></div><span class="state-pill">${escapeHtml(stateLabel(user.state))}</span></div>
      <dl><div><dt>Tiempo conectado</dt><dd data-connected="${Number(user.connected_since) || 0}">${formatDuration(user.connected_seconds)}</dd></div><div><dt>Batería</dt><dd>${escapeHtml(battery)}</dd></div><div><dt>Adaptador</dt><dd>${escapeHtml(user.adapter || '—')}</dd></div><div><dt>MAC</dt><dd>${escapeHtml(user.mac || '—')}</dd></div><div><dt>Desconexiones</dt><dd>${Number(user.disconnect_count || 0)}</dd></div><div><dt>Reconexiones</dt><dd>${Number(user.reconnect_count || 0)}</dd></div></dl>
      <div class="rates"><span>EEG <b>${Number(rates.eeg || 0).toFixed(1)} Hz</b></span><span>IMU <b>${Number(rates.imu || 0).toFixed(1)} Hz</b></span><span>PPG <b>${Number(rates.ppg || 0).toFixed(1)} Hz</b></span></div>
      <div class="hz-actions">${['eeg', 'imu', 'ppg'].map((signal) => `<button class="hz-button" data-operator="${escapeHtml(user.operator)}" data-signal="${signal}" ${connected ? '' : 'disabled'}>Medir ${signal.toUpperCase()}</button>`).join('')}</div>
      <p class="hz-result" id="hz-${escapeHtml(user.operator)}">Usa un botón para medir la tasa recibida por el backend activo.</p>
    </article>`;
  }).join('');
}

function activeInterval(operator = groundTruthOperator.value) {
  return (latestWorkshop?.intervals || []).find((item) => item.operator === operator && item.ended_at == null);
}

function formatCountdown(totalSeconds = 0) {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function updateAssessmentAvailability() {
  const interval = activeInterval();
  const dueAt = Number(interval?.next_assessment_due_at || 0);
  const remaining = dueAt ? dueAt - Date.now() / 1000 : 0;
  const due = Boolean(interval && dueAt && remaining <= 0);
  const canAnswer = Boolean(latestStatus?.running && interval && due);
  groundTruthSubmit.disabled = !canAnswer;
  groundTruthForm.querySelectorAll('input[type="radio"]').forEach((input) => { input.disabled = !canAnswer; });
  assessmentTiming.classList.toggle('due', due);
  if (!interval) {
    assessmentStatus.textContent = 'Inicia una sección';
    assessmentCountdown.textContent = '10:00';
  } else if (due) {
    assessmentStatus.textContent = `Medición ${Number(interval.assessment_count || 0) + 1} disponible`;
    assessmentCountdown.textContent = 'RESPONDE AHORA';
  } else {
    assessmentStatus.textContent = `Medición ${Number(interval.assessment_count || 0) + 1}`;
    assessmentCountdown.textContent = formatCountdown(remaining);
  }
}

function renderQuestionnaireOptions() {
  if (!latestWorkshop) return;
  const selectedOperator = groundTruthOperator.value;
  const operators = new Set((latestStatus?.operators || []).map((item) => item.operator));
  (latestWorkshop.responses || []).forEach((item) => operators.add(item.operator));
  groundTruthOperator.innerHTML = '<option value="">Selecciona un usuario</option>' + [...operators].sort().map((operator) => `<option value="${escapeHtml(operator)}" ${operator === selectedOperator ? 'selected' : ''}>${escapeHtml(operator)}</option>`).join('');
  groundTruthOperator.disabled = !latestStatus?.running || !operators.size;

  const interval = activeInterval(selectedOperator);
  const section = (latestWorkshop.sections || []).find((item) => item.id === interval?.section_id);
  groundTruthSection.innerHTML = section
    ? `<option value="${section.id}" selected>Paso ${section.number}: ${escapeHtml(section.title)} · en curso</option>`
    : '<option value="">Primero inicia una sección</option>';
  groundTruthSection.disabled = true;
  updateAssessmentAvailability();
}

function renderWorkshop(workshop) {
  latestWorkshop = workshop;
  const selectedOperator = groundTruthOperator.value;
  const intervals = (workshop.intervals || []).filter((item) => item.operator === selectedOperator);
  const completed = intervals.filter((item) => item.ended_at != null).length;
  const nextSection = workshop.sections.find((section) => !intervals.some((item) => item.section_id === section.id));
  workshopSummary.textContent = selectedOperator
    ? `${completed} de ${workshop.sections.length} secciones de ${selectedOperator}`
    : 'Selecciona tu usuario';
  workshopSections.innerHTML = workshop.sections.map((section) => {
    const interval = intervals.find((item) => item.section_id === section.id);
    const active = interval && interval.ended_at == null;
    const done = interval && interval.ended_at != null;
    const state = active ? 'En curso' : (done ? 'Terminada' : 'Pendiente');
    const button = active
      ? `<button class="active-section-button section-action" data-action="finish" data-section="${section.id}" type="button">Terminar esta sección</button>`
      : `<button class="small-button section-action" data-action="start" data-section="${section.id}" ${done || section.id !== nextSection?.id || !selectedOperator || !latestStatus?.recording || intervals.some((item) => item.ended_at == null) ? 'disabled' : ''}>Iniciar esta sección</button>`;
    return `<article class="workshop-section ${active ? 'active' : ''} ${done ? 'done' : ''}">
      <div class="section-number">${section.number}</div><div class="section-copy"><strong>${escapeHtml(section.title)}</strong><span>${section.minutes} min objetivo · ${state}${active ? ` · ${Number(interval.assessment_count || 0)} mediciones` : ''}</span>${active ? `<b class="section-clock" data-section-start="${Number(interval.started_at)}">${formatDuration(Date.now() / 1000 - Number(interval.started_at))}</b>` : ''}</div>${button}
    </article>`;
  }).join('');

  if (!engagementItems.children.length) {
    engagementItems.innerHTML = workshop.items.map((item, itemIndex) => `<fieldset><legend><span>${itemIndex + 1}</span>${escapeHtml(item.text_es)}</legend><div class="likert-options">${workshop.likert_scale.map((point) => `<label title="${escapeHtml(point.label_es)}"><input type="radio" name="${item.id}" value="${point.value}" required><span>${point.value}</span></label>`).join('')}</div></fieldset>`).join('');
  }
  renderQuestionnaireOptions();
}

async function refreshWorkshop() {
  try { renderWorkshop(await api(isResearcher ? '/api/workshop' : '/api/operator/workshop')); }
  catch (error) { groundTruthMessage.textContent = error.message; groundTruthMessage.classList.add('error'); }
}

async function refreshStatus() {
  try {
    const status = await api(isResearcher ? '/api/status' : '/api/operator/status');
    latestStatus = status;
    if (!isResearcher) { renderQuestionnaireOptions(); return; }
    statusDot.classList.toggle('running', status.running);
    recordingDot.classList.toggle('active', status.recording);
    statusText.textContent = status.running ? 'Pipeline preparado' : 'Listo para preparar';
    recordingStatus.textContent = status.recording ? 'Registrando datos' : (status.running ? 'Conectado, sin guardar' : 'En espera');
    activeSession.textContent = status.session?.session_name || 'Sin sesión activa';
    recordingTime.textContent = formatDuration(recordingSeconds(status.session));
    prepareButton.disabled = status.running || Boolean(status.session);
    recordButton.disabled = !status.running || status.recording;
    pauseButton.disabled = !status.running || !status.recording;
    finishButton.disabled = !status.session;
    previewButton.disabled = !status.session;
    graphButton.disabled = !status.running;
    logOutput.textContent = status.log_tail?.length ? status.log_tail.join('\n') : 'Esperando eventos del pipeline…';
    logOutput.scrollTop = logOutput.scrollHeight;
    renderOperators(status.operators || []);
  } catch (error) {
    statusText.textContent = 'Servicio no disponible';
    showMessage(error.message, true);
  }
}

async function refreshSessions() {
  try {
    const sessions = await api('/api/sessions');
    if (!sessions.length) {
      sessionsBody.innerHTML = '<tr><td colspan="6">Todavía no hay sesiones locales.</td></tr>';
      return;
    }
    sessionsBody.innerHTML = sessions.map((session) => `<tr>
      <td>${escapeHtml(session.session_name)}</td><td>${escapeHtml(session.subject_code)}</td><td>${escapeHtml(session.experiment)}</td><td>${Number(session.ground_truth_responses || 0)}</td><td>${escapeHtml(session.status)}</td>
      <td>${session.csv_exists ? (session.csv_exports || []).map((item) => `<button class="download download-button" data-session="${escapeHtml(session.session_name)}" data-operator="${escapeHtml(item.operator)}" data-profile="${escapeHtml(item.profile)}">${escapeHtml(item.operator)} · ${item.profile === 'muse' ? 'Sólo Muse' : 'Muse + Likert'}</button>`).join(' ') : `<button class="download export-button" data-session="${escapeHtml(session.session_name)}">Generar ambas versiones</button>`}</td>
    </tr>`).join('');
  } catch (error) {
    sessionsBody.innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderPreview(result) {
  const entries = Object.entries(result.tables || {});
  if (!result.database_exists || !entries.length) {
    previewOutput.innerHTML = '<p class="empty-state">La base existe, pero aún no contiene tablas con datos.</p>';
    return;
  }
  previewOutput.innerHTML = entries.map(([name, table]) => {
    const rows = table.rows || [];
    const columns = rows.length ? Object.keys(rows[0]) : [];
    return `<section class="preview-table"><div class="preview-title"><strong>${escapeHtml(name)}</strong><span>${Number(table.count).toLocaleString()} filas</span></div>${rows.length ? `<div class="table-wrap"><table><thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p>Sin muestras guardadas.</p>'}</section>`;
  }).join('');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault(); prepareButton.disabled = true; showMessage('Preparando adquisición y buscando diademas…');
  try {
    await api('/api/session/start', {method: 'POST', headers: mutationHeaders, body: JSON.stringify({subject_code: $('#subject-code').value, experiment: $('#experiment').value, hci_devices: $('#hci-devices').value, backend: $('#pipeline-backend').value, notes: $('#notes').value})});
    showMessage('Pipeline preparado. Espera a que los usuarios aparezcan y luego comienza a registrar.');
    await Promise.all([refreshStatus(), refreshSessions(), refreshWorkshop()]);
  } catch (error) { showMessage(error.message, true); prepareButton.disabled = false; }
});

recordButton.addEventListener('click', async () => {
  recordButton.disabled = true; showMessage('Iniciando registro…');
  try { await api('/api/recording/start', {method: 'POST', headers: mutationHeaders}); showMessage('Grabación iniciada.'); await Promise.all([refreshStatus(), refreshWorkshop()]); }
  catch (error) { showMessage(error.message, true); await refreshStatus(); }
});

pauseButton.addEventListener('click', async () => {
  pauseButton.disabled = true; showMessage('Deteniendo registro…');
  try { await api('/api/recording/stop', {method: 'POST', headers: mutationHeaders}); showMessage('Registro detenido; las diademas siguen conectadas.'); await refreshStatus(); }
  catch (error) { showMessage(error.message, true); await refreshStatus(); }
});

finishButton.addEventListener('click', async () => {
  if (!confirm('¿Finalizar la sesión y generar por operador los CSV “Sólo Muse” y “Muse + Likert”?')) return;
  finishButton.disabled = true; showMessage('Finalizando y generando ambas versiones CSV…');
  try {
    const result = await api('/api/session/stop', {method: 'POST', headers: mutationHeaders});
    showMessage(result.export_error ? `Sesión cerrada; CSV pendientes: ${result.export_error}` : 'Sesión finalizada. Se generaron las dos versiones por operador.', Boolean(result.export_error));
    await Promise.all([refreshStatus(), refreshSessions(), refreshWorkshop()]);
  } catch (error) { showMessage(error.message, true); await refreshStatus(); }
});

$('#refresh-users').addEventListener('click', refreshStatus);
$('#refresh-log').addEventListener('click', refreshStatus);
graphButton.addEventListener('click', async () => {
  graphButton.disabled = true; graphOutput.textContent = 'Consultando arquitectura activa…';
  try {
    const graph = await api('/api/ros/graph');
    graphOutput.textContent = `NODOS\n${graph.nodes.join('\n') || '(ninguno)'}\n\nTÓPICOS Y TIPOS\n${graph.topics.join('\n') || '(ninguno)'}`;
    document.querySelector('.graph-details').open = true;
  } catch (error) { graphOutput.textContent = error.message; }
  finally { graphButton.disabled = !latestStatus?.running; }
});
previewButton.addEventListener('click', async () => {
  previewButton.disabled = true; previewOutput.innerHTML = '<p class="empty-state">Consultando datos…</p>';
  try { renderPreview(await api('/api/database/preview?limit=5')); }
  catch (error) { previewOutput.innerHTML = `<p class="empty-state error">${escapeHtml(error.message)}</p>`; }
  finally { previewButton.disabled = false; }
});

usersGrid.addEventListener('click', async (event) => {
  const button = event.target.closest('.hz-button'); if (!button) return;
  const resultNode = $(`#hz-${button.dataset.operator}`); button.disabled = true; resultNode.textContent = `Midiendo ${button.dataset.signal.toUpperCase()} durante 4 segundos…`;
  try {
    const result = await api('/api/topic/hz', {method: 'POST', headers: mutationHeaders, body: JSON.stringify({operator: button.dataset.operator, signal: button.dataset.signal})});
    resultNode.textContent = `${result.topic}: ${result.average_hz.toFixed(2)} Hz`;
  } catch (error) { resultNode.textContent = error.message; }
  finally { button.disabled = false; }
});

workshopSections.addEventListener('click', async (event) => {
  const button = event.target.closest('.section-action'); if (!button) return;
  button.disabled = true;
  groundTruthMessage.classList.remove('error');
  const finishing = button.dataset.action === 'finish';
  groundTruthMessage.textContent = finishing ? 'Terminando la sección…' : 'Marcando inicio sincronizado…';
  try {
    await api(`/api/workshop/section/${finishing ? 'finish' : 'start'}`, {method: 'POST', headers: mutationHeaders, body: JSON.stringify({section_id: button.dataset.section, operator: groundTruthOperator.value})});
    groundTruthMessage.textContent = finishing
      ? 'Sección terminada. Ya puedes iniciar la siguiente.'
      : 'Sección iniciada. La cuenta de 10 minutos está activa y continúa aunque cambies de sección.';
    await Promise.all([refreshWorkshop(), refreshStatus()]);
  } catch (error) { groundTruthMessage.textContent = error.message; groundTruthMessage.classList.add('error'); await refreshWorkshop(); }
});

groundTruthOperator.addEventListener('change', () => renderWorkshop(latestWorkshop));
groundTruthForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = new FormData(groundTruthForm);
  const payload = {operator: groundTruthOperator.value, section_id: groundTruthSection.value};
  ['task_engagement', 'effort', 'persistence', 'flow'].forEach((field) => { payload[field] = Number(data.get(field)); });
  groundTruthSubmit.disabled = true; groundTruthMessage.classList.remove('error'); groundTruthMessage.textContent = 'Guardando la medición sincronizada…';
  try {
    const result = await api('/api/ground-truth', {method: 'POST', headers: mutationHeaders, body: JSON.stringify(payload)});
    groundTruthForm.querySelectorAll('input[type="radio"]').forEach((input) => { input.checked = false; });
    await Promise.all([refreshWorkshop(), ...(isResearcher ? [refreshSessions()] : [])]);
    groundTruthMessage.textContent = `Medición ${result.section_measurement_number} guardada para esta sección. La escala quedó limpia y la sección continúa activa.`;
  } catch (error) { groundTruthMessage.textContent = error.message; groundTruthMessage.classList.add('error'); renderQuestionnaireOptions(); }
});

sessionsBody.addEventListener('click', async (event) => {
  const exportButton = event.target.closest('.export-button');
  const downloadButton = event.target.closest('.download-button');
  if (exportButton) {
    exportButton.disabled = true;
    try { await api('/api/session/export', {method: 'POST', headers: mutationHeaders, body: JSON.stringify({session_name: exportButton.dataset.session})}); await refreshSessions(); }
    catch (error) { showMessage(error.message, true); exportButton.disabled = false; }
  }
  if (downloadButton) {
    downloadButton.disabled = true;
    try {
      const response = await fetch(`/api/session/${encodeURIComponent(downloadButton.dataset.session)}/csv/${encodeURIComponent(downloadButton.dataset.operator)}/${encodeURIComponent(downloadButton.dataset.profile)}`, {headers: {'Authorization': `Bearer ${accessToken}`}});
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Error HTTP ${response.status}`); }
      const link = document.createElement('a');
      link.href = URL.createObjectURL(await response.blob());
      link.download = `${downloadButton.dataset.session}_${downloadButton.dataset.operator}_${downloadButton.dataset.profile}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) { showMessage(error.message, true); }
    finally { downloadButton.disabled = false; }
  }
});

function activateView(name) {
  if (!isResearcher && name !== 'operator') return;
  if (isResearcher && name !== 'pipeline') return;
  document.querySelectorAll('.app-view').forEach((view) => { view.hidden = view.id !== `${name}-view`; });
  document.querySelectorAll('.view-tab').forEach((tab) => {
    const selected = tab.dataset.view === name;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
  });
  $('#operator-footer').hidden = name !== 'operator';
  $('#pipeline-footer').hidden = name !== 'pipeline';
  history.replaceState(null, '', `#${name}`);
}

document.querySelectorAll('.view-tab').forEach((tab) => {
  tab.addEventListener('click', () => activateView(tab.dataset.view));
});

setInterval(() => { if (latestStatus?.session) recordingTime.textContent = formatDuration(recordingSeconds(latestStatus.session)); document.querySelectorAll('[data-connected]').forEach((node) => { const started = Number(node.dataset.connected); if (started) node.textContent = formatDuration(Date.now() / 1000 - started); }); document.querySelectorAll('[data-section-start]').forEach((node) => { node.textContent = formatDuration(Date.now() / 1000 - Number(node.dataset.sectionStart)); }); updateAssessmentAvailability(); }, 1000);
document.querySelector('.view-tabs').hidden = true;
activateView(isResearcher ? 'pipeline' : 'operator');
refreshStatus(); refreshWorkshop();
if (isResearcher) { refreshSessions(); setInterval(refreshSessions, 15000); }
setInterval(() => { refreshStatus(); refreshWorkshop(); }, 3000);
