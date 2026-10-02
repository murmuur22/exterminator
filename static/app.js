'use strict';
const $ = (selector) => document.querySelector(selector);
const statusNames = {open: 'Open', in_progress: 'In progress', resolved: 'Resolved'};
const views = {
  inbox: ['Inbox', 'Open calls and work in progress. Nothing to keep in your head.'],
  in_progress: ['In progress', 'The calls you’re working on. One thing at a time.'],
  resolved: ['Resolved', 'Finished, not forgotten. Open a report to bring it back.'],
  all: ['All reports', 'Every call, from first sighting to final fix.']
};
let reports = [];
let currentView = 'inbox';
let editingId = null;
let savingEdit = false;
let noticeTimer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {'Content-Type': 'application/json', 'X-Exterminator': '1', ...options.headers}
    });
  } catch {
    throw new Error('Could not reach the local server. Your form is still here; try again when it’s running.');
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The request failed. Your changes have not been confirmed.');
  return result;
}

function notify(message) {
  clearTimeout(noticeTimer);
  $('#notice').textContent = message;
  noticeTimer = setTimeout(() => { $('#notice').textContent = ''; }, 4500);
}

function showError(selector, message) {
  const target = $(selector);
  target.textContent = message;
  target.hidden = !message;
}

function matchingView(report, view) {
  return view === 'all' || (view === 'inbox' ? report.status !== 'resolved' : report.status === view);
}

function render() {
  for (const view of Object.keys(views)) {
    $(`#count-${view}`).textContent = reports.filter(report => matchingView(report, view)).length;
  }
  $('#view-title').textContent = views[currentView][0];
  $('#view-description').textContent = views[currentView][1];
  const query = $('#search').value.trim().toLocaleLowerCase();
  const service = $('#service-filter').value;
  const filtered = reports.filter(report => matchingView(report, currentView)
    && (!service || report.service === service)
    && (!query || [report.title, report.detail, report.service, report.kind, `EX-${String(report.id).padStart(4, '0')}`].join(' ').toLocaleLowerCase().includes(query)));
  $('#results-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'report' : 'reports'}`;
  const container = $('#reports');
  container.replaceChildren();
  if (!filtered.length) {
    const empty = element('div', 'empty');
    empty.append(element('p', '', reports.length ? 'No matching reports.' : 'No reports yet.'));
    empty.append(element('small', '', reports.length ? 'Try another view, search, or service.' : 'Spotted a bug? Call it in using the form.'));
    container.append(empty);
  }
  for (const report of filtered) {
    const row = element('article', 'report-row');
    const meta = element('div', 'report-meta');
    meta.append(element('span', '', `EX-${String(report.id).padStart(4, '0')}`), element('span', 'kind', report.kind));
    const title = element('button', 'report-title', report.title);
    title.type = 'button';
    title.addEventListener('click', () => editReport(report));
    const bottom = element('div', 'report-bottom');
    bottom.append(element('span', 'service-name', report.service || 'No service specified'), element('span', `status ${report.status}`, statusNames[report.status]));
    row.append(meta, title, bottom);
    container.append(row);
  }
}

function refreshServices() {
  const services = [...new Set(reports.map(report => report.service).filter(Boolean))].sort();
  const previous = $('#service-filter').value;
  $('#service-filter').replaceChildren(new Option('All services', ''));
  $('#services').replaceChildren();
  for (const service of services) {
    $('#service-filter').append(new Option(service, service));
    $('#services').append(new Option(service, service));
  }
  if (services.includes(previous)) $('#service-filter').value = previous;
}

async function loadReports() {
  $('#retry').hidden = true;
  showError('#load-error', '');
  try {
    reports = await api('/api/reports');
    refreshServices();
    render();
  } catch (error) {
    $('#reports').replaceChildren();
    $('#results-count').textContent = 'Unavailable';
    showError('#load-error', error.message);
    $('#retry').hidden = false;
  }
}

function editReport(report) {
  editingId = report.id;
  $('#edit-number').textContent = `WORK ORDER / EX-${String(report.id).padStart(4, '0')}`;
  for (const field of ['title', 'service', 'kind', 'detail', 'status']) {
    $(`#edit-${field}`).value = report[field];
  }
  $('#edit-dates').textContent = `Created ${new Date(report.created_at).toLocaleString()} · Updated ${new Date(report.updated_at).toLocaleString()}`;
  showError('#edit-error', '');
  $('#editor').showModal();
}

$('#capture-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  showError('#capture-error', '');
  try {
    const report = await api('/api/reports', {method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form)))});
    reports.unshift(report);
    form.reset();
    refreshServices();
    render();
    notify('Report saved. It’s out of your head.');
    $('#title').focus({preventScroll: true});
  } catch (error) {
    showError('#capture-error', error.message);
  } finally {
    button.disabled = false;
  }
});

$('#edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (savingEdit) return;
  savingEdit = true;
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  $('#close-editor').disabled = true;
  $('#cancel-editor').disabled = true;
  showError('#edit-error', '');
  try {
    const report = await api(`/api/reports/${editingId}`, {method: 'PATCH', body: JSON.stringify(Object.fromEntries(new FormData(form)))});
    reports = reports.map(item => item.id === report.id ? report : item);
    $('#editor').close();
    refreshServices();
    render();
    $('#new-report').focus({preventScroll: true});
    notify('Changes saved.');
  } catch (error) {
    showError('#edit-error', error.message);
  } finally {
    savingEdit = false;
    button.disabled = false;
    $('#close-editor').disabled = false;
    $('#cancel-editor').disabled = false;
  }
});

$('#views').addEventListener('click', event => {
  const button = event.target.closest('[data-view]');
  if (!button) return;
  currentView = button.dataset.view;
  for (const item of document.querySelectorAll('[data-view]')) {
    item.classList.toggle('active', item === button);
    item.setAttribute('aria-pressed', String(item === button));
  }
  render();
});
$('#search').addEventListener('input', render);
$('#service-filter').addEventListener('change', render);
$('#retry').addEventListener('click', loadReports);
$('#close-editor').addEventListener('click', () => {
  if (!savingEdit) $('#editor').close();
});
$('#cancel-editor').addEventListener('click', () => {
  if (!savingEdit) $('#editor').close();
});
$('#editor').addEventListener('cancel', event => {
  if (savingEdit) event.preventDefault();
});
$('#new-report').addEventListener('click', () => $('#title').focus());
loadReports();
