const componentList = document.querySelector('#components');
const form = document.querySelector('#release-form');
const workspace = document.querySelector('.workspace');
const outputCard = document.querySelector('#output-card');
const output = document.querySelector('#runbook');
const savedRunbookList = document.querySelector('#saved-runbook-list');
const knownComponentSelect = document.querySelector('#known-component');
const integrationStatus = document.querySelector('#integration-status');
const dashboardContent = document.querySelector('#release-dashboard-content');
const connectionForm = document.querySelector('#connection-form');
const connectionNote = document.querySelector('#connection-note');
let integrationSettings = null;
document.head.insertAdjacentHTML('beforeend', '<style>.dashboard-actions{margin-top:7px;padding:0;border:0;background:transparent;color:#315d50;font-size:12px;font-weight:700;text-decoration:underline;cursor:pointer}.action-dialog{position:relative;min-width:min(560px,calc(100vw - 32px));padding:8px}.action-dialog h2{margin:0 0 8px;font:28px Georgia,serif}.action-dialog .close{position:absolute;top:5px;right:8px;padding:3px 7px;border:0;background:transparent;font-size:22px;cursor:pointer}.action-dialog-footer{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}.action-dialog-footer .button{margin:0}@media (max-width:900px){.release-controls,.section-heading{grid-template-columns:1fr}.form-submit{align-items:flex-start;flex-direction:column}}@media (max-width:600px){.form-submit .button,.component-actions-row .button{width:100%}.component-actions-row,.dashboard-heading,.dashboard-runbook,.release-discovery,.connection-heading,.connection-submit{align-items:stretch;flex-direction:column}.connection-fields{grid-template-columns:1fr}.connection-fields .wide{grid-column:auto}.action-dialog{min-width:0}.action-dialog-footer{flex-direction:column}.action-dialog-footer .button{width:100%}}</style>');

const knownComponents = [
  { name: 'gss-ui', kind: 'frontend' },
  { name: 'gssnav-coi-api', kind: 'service' },
  { name: 'gssnav-coi-ui', kind: 'frontend' },
  { name: 'gssnav-elastic-watch', kind: 'service' },
  { name: 'gssnav-exman-api', kind: 'service' },
  { name: 'gssnav-l1controlscope', kind: 'service' },
  { name: 'gssnav-navigator-api-poc', kind: 'service' },
  { name: 'gssnav-navigator-host-api', kind: 'service' },
  { name: 'gssnav-navigator-host-ui', kind: 'frontend' },
  { name: 'gssnav-gss-sql', kind: 'database' },
  { name: 'gssnav-redis', kind: 'database' },
  { name: 'gssnav-task-manager-api', kind: 'service' },
  { name: 'gssnav-task-manager-ui', kind: 'frontend' },
  { name: 'team-view', kind: 'frontend' },
  { name: 'wsp-dashboard', kind: 'frontend' },
];

knownComponentSelect.insertAdjacentHTML('beforeend', knownComponents.map(component => `<option value="${component.name}">${component.name}</option>`).join(''));

function componentRow(values = {}) {
  const row = document.createElement('fieldset');
  row.className = 'component';
  row.dataset.kind = values.kind || '';
  row.innerHTML = `
    <legend>Component release details</legend>
    <div class="component-grid">
      <label><span>Component name <b>Required</b></span><input data-field="name" value="${values.name || ''}" placeholder="Example: gssnav-coi-api" /></label>
      <label><span>Target image tag <b>Required</b></span><input data-field="version" value="${values.version || ''}" placeholder="Example: 26.10.02-main-a1b2c" /></label>
      <label><span>Previous stable image tag <b>Optional</b></span><input data-field="previousVersion" value="${values.previousVersion || ''}" placeholder="Found in ArgoCD History" /></label>
      <label><span>Owner <b>Optional</b></span><input data-field="owner" value="${values.owner || ''}" placeholder="Team or person" /></label>
      <label class="wide"><span>Depends on <b>Optional</b></span><input data-field="dependsOn" value="${values.dependsOn || ''}" placeholder="Example: identity-api, postgres" /></label>
      <label class="wide"><span>Component instructions / steps <b>Optional</b></span><textarea data-field="additionalInstructions" rows="3" placeholder="Example: run the approved migration before rollout, validate the reconciliation report, then notify the data team.">${values.additionalInstructions || ''}</textarea></label>
    </div>
    <button type="button" class="remove" aria-label="Remove component">Remove</button>`;
  row.querySelector('.remove').addEventListener('click', () => row.remove());
  componentList.append(row);
}

document.querySelector('#add-component').addEventListener('click', () => componentRow());
document.querySelector('#add-known-component').addEventListener('click', () => {
  const component = knownComponents.find(item => item.name === knownComponentSelect.value);
  if (!component) { alert('Select a known component first.'); return; }
  if ([...document.querySelectorAll('[data-field=name]')].some(field => field.value.trim().toLowerCase() === component.name)) { alert(`${component.name} is already in this runbook.`); return; }
  componentRow(component);
  knownComponentSelect.value = '';
});

document.querySelector('#discover-release').addEventListener('click', async () => {
  const releaseNumber = document.querySelector('#release-number').value.trim();
  const note = document.querySelector('#discovery-note');
  if (!releaseNumber) { note.textContent = 'Enter the release number first.'; return; }
  note.textContent = 'Checking the configured Jira release scope…';
  const response = await fetch(`/api/discovery/jira/${encodeURIComponent(releaseNumber)}`);
  const data = await response.json();
  if (!response.ok) { note.textContent = data.detail || 'Jira scope could not be checked.'; return; }
  const components = [...new Set(data.issues.flatMap(issue => issue.components))];
  note.textContent = components.length ? `Jira found ${components.length} component${components.length === 1 ? '' : 's'}. Add image tags through the approved GitHub and Helm workflow before creating the runbook.` : 'Jira did not return a component scope for this Fix Version.';
  components.forEach(name => {
    if (![...document.querySelectorAll('[data-field=name]')].some(field => field.value.trim().toLowerCase() === name.toLowerCase())) componentRow({ name, kind: inferredKind(name) });
  });
});

function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char])); }
function items(list) { return `<ul>${list.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`; }

function inferredKind(name) {
  const lower = name.toLowerCase();
  if (/(?:^|-)(ui|dashboard|view)(?:-|$)/.test(lower)) return 'frontend';
  if (/(redis|sql|postgres|database)/.test(lower)) return 'database';
  if (/(?:^|-)job(?:-|$)/.test(lower)) return 'job';
  if (/(worker|consumer)/.test(lower)) return 'worker';
  return 'service';
}

function displayDate(value) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

async function loadDashboard() {
  const response = await fetch('/api/dashboard');
  if (!response.ok) { dashboardContent.innerHTML = '<p class="form-note">The release overview is unavailable right now.</p>'; return; }
  const data = await response.json();
  if (!data.runbooks.length) { dashboardContent.innerHTML = '<p class="form-note">No production runbooks yet. Start with the next approved release date.</p>'; return; }
  dashboardContent.innerHTML = data.runbooks.slice(0, 6).map(runbook => {
    const state = runbook.published ? 'Published' : runbook.open_actions.length ? `${runbook.open_actions.length} action${runbook.open_actions.length === 1 ? '' : 's'} open` : 'Ready for review';
    const next = runbook.open_actions[0] || 'Review and approve the release plan.';
    return `<article class="dashboard-runbook"><div><strong>${escapeHtml(runbook.release_number)}</strong><span>${escapeHtml(runbook.title)} · ${escapeHtml(runbook.release_date || 'Date to be confirmed')}</span></div><div><span class="risk ${escapeHtml(runbook.risk)}">${escapeHtml(state)}</span><p>${escapeHtml(next)}</p><button class="text-button dashboard-actions" type="button" data-runbook-id="${escapeHtml(runbook.id)}">Review open actions</button></div></article>`;
  }).join('');
}
document.querySelector('#refresh-dashboard').addEventListener('click', loadDashboard);
dashboardContent.addEventListener('click', event => {
  const button = event.target.closest('.dashboard-actions');
  if (button) showOpenActions(button.dataset.runbookId);
});
loadDashboard();

async function loadRunbookForEditing(runbookId) {
  const response = await fetch('/api/runbooks'); if (!response.ok) return null;
  return (await response.json()).find(runbook => runbook.id === runbookId) || null;
}
function populateReleaseForm(runbook) {
  document.querySelector('#release-number').value = runbook.release.release_number;
  document.querySelector('#title').value = runbook.release.title || '';
  document.querySelector('#change-ticket').value = runbook.release.change_ticket || '';
  document.querySelector('#release-date').value = runbook.release.release_date || '';
  document.querySelector('#release-engineer').value = runbook.release.release_engineer || '';
  document.querySelector('#application-owner').value = runbook.release.application_owner || '';
  document.querySelector('#additional-information').value = runbook.release.additional_information || '';
  componentList.innerHTML = '';
  runbook.release.components.forEach(component => componentRow({ name: component.name, version: component.version, kind: component.kind, owner: component.owner || '', previousVersion: component.previous_version || '', dependsOn: (component.depends_on || []).join(', '), additionalInstructions: component.additional_instructions || '' }));
  form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
async function showOpenActions(runbookId) {
  const [readinessResponse, runbook] = await Promise.all([fetch(`/api/runbooks/${encodeURIComponent(runbookId)}/readiness`), loadRunbookForEditing(runbookId)]);
  if (!readinessResponse.ok || !runbook) { alert('The release actions could not be loaded.'); return; }
  const readiness = await readinessResponse.json();
  let dialog = document.querySelector('#open-actions-dialog');
  if (!dialog) { dialog = document.createElement('dialog'); dialog.id = 'open-actions-dialog'; document.body.append(dialog); }
  dialog.innerHTML = `<form method="dialog" class="action-dialog"><button class="close" value="cancel" aria-label="Close">×</button><p class="eyebrow">${escapeHtml(runbook.release.release_number)} · RELEASE ACTIONS</p><h2>${readiness.open_actions.length ? `${readiness.open_actions.length} item${readiness.open_actions.length === 1 ? '' : 's'} to complete` : 'No open actions'}</h2><p class="intro">Complete the missing release details in the editor, then submit the same release number to update its one runbook.</p>${readiness.open_actions.length ? items(readiness.open_actions) : '<p class="form-note">The planning checklist is complete. Review approvals before publishing.</p>'}<div class="action-dialog-footer"><button class="button secondary" value="cancel">Close</button><button class="button" id="edit-runbook" type="button">Edit this runbook</button></div></form>`;
  dialog.querySelector('#edit-runbook').onclick = () => { dialog.close(); populateReleaseForm(runbook); };
  dialog.showModal();
}

function componentChanges(current, previous) {
  if (!previous) return '<p class="form-note">A comparison appears after this runbook is updated at least once.</p>';
  const before = new Map(previous.release.components.map(component => [component.name.toLowerCase(), component]));
  const after = new Map(current.release.components.map(component => [component.name.toLowerCase(), component]));
  const changes = [];
  after.forEach((component, name) => {
    const old = before.get(name);
    if (!old) changes.push(`${component.name} was added with ${component.version}.`);
    else if (old.version !== component.version) changes.push(`${component.name}: ${old.version} → ${component.version}.`);
  });
  before.forEach((component, name) => { if (!after.has(name)) changes.push(`${component.name} was removed from the release scope.`); });
  return changes.length ? items(changes) : '<p class="form-note">No component or image-tag change from the preceding revision.</p>';
}

async function renderReleaseControls(runbook) {
  const [readinessResponse, historyResponse] = await Promise.all([fetch(`/api/runbooks/${encodeURIComponent(runbook.id)}/readiness`), fetch(`/api/runbooks/${encodeURIComponent(runbook.id)}/history`)]);
  if (!readinessResponse.ok) return;
  const readiness = await readinessResponse.json();
  const history = historyResponse.ok ? await historyResponse.json() : [];
  const previous = history.find(item => item.revision === runbook.revision - 1);
  const state = readiness.ready_to_publish ? 'Ready to publish' : runbook.approvals.approved_at ? 'Approved — details still needed' : 'Draft — needs review';
  const confluenceReady = readiness.connectors.confluence === 'ready';
  const container = document.createElement('section');
  container.className = 'release-controls';
  container.innerHTML = `<section class="control-panel"><p class="eyebrow">RUNBOOK STATUS</p><h3>${escapeHtml(state)}</h3><p>Revision ${runbook.revision} · ${history.length} saved revision${history.length === 1 ? '' : 's'}</p></section><section class="control-panel"><p class="eyebrow">READINESS CHECKLIST</p><h3>${readiness.open_actions.length ? `${readiness.open_actions.length === 1 ? '1 item' : `${readiness.open_actions.length} items`} to complete` : 'All required items complete'}</h3>${readiness.open_actions.length ? items(readiness.open_actions.slice(0, 6)) : '<p class="form-note">The release has the required planning evidence. Review before publishing.</p>'}</section><section class="control-panel"><p class="eyebrow">WHAT CHANGED</p><h3>Since revision ${previous ? previous.revision : '—'}</h3>${componentChanges(runbook, previous)}</section><section class="control-panel confluence-panel"><p class="eyebrow">CONFLUENCE</p><h3>${confluenceReady ? 'Ready after approval' : 'Connection not configured'}</h3><p>${confluenceReady ? 'Publishing updates the official page for this release number; it never creates a duplicate page.' : `The official page will be named “${escapeHtml(runbook.release.release_number)} GDC Release Runbook” once the Confluence deployment configuration is supplied.`}</p><button class="button secondary" id="publish-confluence" type="button" ${confluenceReady ? '' : 'disabled'}>Publish / update Confluence</button><p class="form-note" id="confluence-note"></p></section>`;
  const publishButton = container.querySelector('#publish-confluence');
  if (publishButton) publishButton.addEventListener('click', async () => {
    const actor = window.prompt('Record the name of the person approving this publication (optional):', runbook.approvals.approved_by || '') || '';
    publishButton.disabled = true;
    const response = await fetch(`/api/runbooks/${encodeURIComponent(runbook.id)}/publish/confluence`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ approved_for_publishing: true, actor: actor.trim() }) });
    const result = await response.json();
    const note = container.querySelector('#confluence-note');
    if (!response.ok) { note.textContent = result.detail || 'Confluence publishing could not be completed.'; publishButton.disabled = false; return; }
    note.innerHTML = `Published to <a href="${escapeHtml(result.url)}" target="_blank" rel="noopener">the official Confluence page</a>.`;
    publishButton.textContent = 'Confluence updated';
    loadDashboard();
  });
  const approvalPanel = document.createElement('section');
  approvalPanel.className = 'control-panel approval-panel';
  approvalPanel.innerHTML = `<p class="eyebrow">APPROVAL CHECKLIST</p><h3>Confirm release readiness</h3><form id="approval-form"><label><input type="checkbox" name="release_engineer_confirmed" ${runbook.approvals.release_engineer_confirmed ? 'checked' : ''}> Release engineer confirmed</label><label><input type="checkbox" name="application_owner_confirmed" ${runbook.approvals.application_owner_confirmed ? 'checked' : ''}> Application owner confirmed</label><label><input type="checkbox" name="change_ticket_confirmed" ${runbook.approvals.change_ticket_confirmed ? 'checked' : ''}> Change ticket approved</label><label><input type="checkbox" name="rollback_confirmed" ${runbook.approvals.rollback_confirmed ? 'checked' : ''}> Rollback plan confirmed</label><label><input type="checkbox" name="validation_signoff_confirmed" ${runbook.approvals.validation_signoff_confirmed ? 'checked' : ''}> Validation sign-off confirmed</label><input name="approved_by" value="${escapeHtml(runbook.approvals.approved_by || '')}" placeholder="Confirmed by (optional)"><button class="button secondary" type="submit">Save confirmations</button><p class="form-note" id="approval-note"></p></form>`;
  container.append(approvalPanel);
  approvalPanel.querySelector('#approval-form').addEventListener('submit', async event => {
    event.preventDefault(); const formData = new FormData(event.currentTarget);
    const payload = Object.fromEntries(['release_engineer_confirmed', 'application_owner_confirmed', 'change_ticket_confirmed', 'rollback_confirmed', 'validation_signoff_confirmed'].map(name => [name, formData.get(name) === 'on']));
    payload.approved_by = String(formData.get('approved_by') || '').trim();
    const response = await fetch(`/api/runbooks/${encodeURIComponent(runbook.id)}/approvals`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) { approvalPanel.querySelector('#approval-note').textContent = result.detail || 'Confirmations could not be saved.'; return; }
    container.remove(); renderReleaseControls(result); loadDashboard(); loadSavedRunbooks();
  });
  const evidencePanel = document.createElement('section');
  evidencePanel.className = 'control-panel evidence-panel';
  evidencePanel.innerHTML = `<p class="eyebrow">RELEASE EVIDENCE</p><h3>Preview before external publishing</h3><p>Preview is available now. ArgoCD evidence appears after its read-only connector is configured.</p><p class="runbook-links"><button class="button secondary" type="button" id="preview-confluence">Preview Confluence page</button><a class="download-runbook" target="_blank" rel="noopener" href="/api/runbooks/${encodeURIComponent(runbook.id)}/view">Print / save PDF</a></p><p class="form-note" id="preview-note"></p></section>`;
  container.append(evidencePanel);
  evidencePanel.querySelector('#preview-confluence').addEventListener('click', async () => {
    const response = await fetch(`/api/runbooks/${encodeURIComponent(runbook.id)}/confluence-preview`); const result = await response.json();
    evidencePanel.querySelector('#preview-note').textContent = response.ok ? `Preview ready for the page “${result.title}”. This uses the same structured content that will be sent to Confluence.` : (result.detail || 'Preview is unavailable.');
  });
  output.append(container);
}

async function loadSavedRunbooks() {
  const response = await fetch('/api/runbooks');
  if (!response.ok) { savedRunbookList.innerHTML = '<p class="form-note">Saved runbooks are unavailable right now.</p>'; return; }
  const runbooks = await response.json();
  if (!runbooks.length) { savedRunbookList.innerHTML = '<p class="form-note">No production runbooks have been created yet.</p>'; return; }
  savedRunbookList.innerHTML = runbooks.map(runbook => `<article class="saved-runbook"><div class="runbook-summary"><strong>${escapeHtml(runbook.filename)}</strong><span>${escapeHtml(runbook.release.title)} · updated ${escapeHtml(displayDate(runbook.updated_at))} · revision ${runbook.revision}</span></div><div class="runbook-links" aria-label="Runbook actions"><a class="download-runbook" target="_blank" rel="noopener" href="/api/runbooks/${encodeURIComponent(runbook.id)}/view">View runbook</a><a class="download-runbook" href="/api/runbooks/${encodeURIComponent(runbook.id)}/markdown">Download Markdown</a></div></article>`).join('');
}

document.querySelector('#refresh-runbooks').addEventListener('click', loadSavedRunbooks);
loadSavedRunbooks();

async function loadIntegrationStatus() {
  const response = await fetch('/api/status');
  if (!response.ok) { integrationStatus.innerHTML = '<p class="form-note">Connection readiness is unavailable right now.</p>'; return; }
  const data = await response.json();
  const visible = [
    ['Jira release scope', data.integrations.jira],
    ['GitHub and Helm discovery', data.integrations.github],
    ['ArgoCD deployment evidence', data.integrations.argocd],
    ['Confluence publishing', data.integrations.confluence],
  ];
  integrationStatus.innerHTML = visible.map(([name, state]) => `<div><strong>${escapeHtml(name)}</strong><span>${escapeHtml(state)}</span></div>`).join('');
}
loadIntegrationStatus();

function setConnectionField(id, value) { document.querySelector(id).value = value || ''; }
async function loadConnectionSettings() {
  const response = await fetch('/api/admin/integrations');
  if (!response.ok) { connectionNote.textContent = 'Connection details require an authenticated administrator session.'; return; }
  integrationSettings = await response.json();
  setConnectionField('#jira-base-url', integrationSettings.jira.base_url); setConnectionField('#jira-project-key', integrationSettings.jira.project_key);
  setConnectionField('#github-repository', integrationSettings.github.repository); setConnectionField('#github-production-branch', integrationSettings.github.production_branch); setConnectionField('#github-workflow-file', integrationSettings.github.workflow_file);
  setConnectionField('#argocd-base-url', integrationSettings.argocd.base_url); setConnectionField('#argocd-application-pattern', integrationSettings.argocd.application_name_pattern);
  setConnectionField('#confluence-base-url', integrationSettings.confluence.base_url); setConnectionField('#confluence-space-key', integrationSettings.confluence.space_key); setConnectionField('#confluence-parent-page-id', integrationSettings.confluence.parent_page_id);
}
connectionForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!integrationSettings) { connectionNote.textContent = 'Load the current connection settings before saving.'; return; }
  integrationSettings.jira.base_url = document.querySelector('#jira-base-url').value.trim(); integrationSettings.jira.project_key = document.querySelector('#jira-project-key').value.trim();
  integrationSettings.github.repository = document.querySelector('#github-repository').value.trim(); integrationSettings.github.production_branch = document.querySelector('#github-production-branch').value.trim() || 'prd'; integrationSettings.github.workflow_file = document.querySelector('#github-workflow-file').value.trim() || 'releasepilot-runbook.yml';
  integrationSettings.argocd.base_url = document.querySelector('#argocd-base-url').value.trim(); integrationSettings.argocd.application_name_pattern = document.querySelector('#argocd-application-pattern').value.trim() || '{component}';
  integrationSettings.confluence.base_url = document.querySelector('#confluence-base-url').value.trim(); integrationSettings.confluence.space_key = document.querySelector('#confluence-space-key').value.trim(); integrationSettings.confluence.parent_page_id = document.querySelector('#confluence-parent-page-id').value.trim();
  const response = await fetch('/api/admin/integrations', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(integrationSettings) }); const result = await response.json();
  if (!response.ok) { connectionNote.textContent = result.detail || 'Connection details could not be saved.'; return; }
  integrationSettings = result; connectionNote.textContent = 'Connection details saved. Add the matching runtime secrets during deployment, then use Check setup.'; loadIntegrationStatus();
});
document.querySelector('#check-connections').addEventListener('click', async () => {
  const response = await fetch('/api/status'); if (!response.ok) { connectionNote.textContent = 'Connection setup could not be checked.'; return; }
  const data = await response.json(); const states = data.integrations;
  connectionNote.textContent = `Jira: ${states.jira}; GitHub: ${states.github}; ArgoCD: ${states.argocd}; Confluence: ${states.confluence}.`;
  loadIntegrationStatus();
});
loadConnectionSettings();

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const rows = [...document.querySelectorAll('.component')];
  const incomplete = rows.some(row => Boolean(row.querySelector('[data-field=name]').value.trim()) !== Boolean(row.querySelector('[data-field=version]').value.trim()));
  if (incomplete) { alert('For each component, enter both the name and version/image tag, or leave both blank.'); return; }
  const components = rows.filter(row => row.querySelector('[data-field=name]').value.trim()).map(row => {
    const name = row.querySelector('[data-field=name]').value.trim();
    return {
    name,
    version: row.querySelector('[data-field=version]').value.trim(),
    kind: row.dataset.kind || inferredKind(name),
    owner: row.querySelector('[data-field=owner]').value.trim() || null,
    previous_version: row.querySelector('[data-field=previousVersion]').value.trim() || null,
    depends_on: row.querySelector('[data-field=dependsOn]').value.split(',').map(v => v.trim()).filter(Boolean),
    additional_instructions: row.querySelector('[data-field=additionalInstructions]').value.trim(),
  }; });
  const request = {
    release_number: document.querySelector('#release-number').value.trim(),
    title: document.querySelector('#title').value.trim(),
    environment: 'production',
    change_ticket: document.querySelector('#change-ticket').value.trim(),
    release_date: document.querySelector('#release-date').value,
    release_engineer: document.querySelector('#release-engineer').value.trim(),
    application_owner: document.querySelector('#application-owner').value.trim(),
    additional_information: document.querySelector('#additional-information').value.trim(),
    components,
  };
  const response = await fetch('/api/runbooks', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(request)});
  const data = await response.json();
  if (!response.ok) { alert(data.detail || 'Could not create the runbook.'); return; }
  document.querySelector('#runbook-title').textContent = data.release.title;
  const risk = document.querySelector('#risk'); risk.textContent = `${data.risk} risk`; risk.className = `risk ${data.risk}`;
  outputCard.hidden = false;
  workspace.classList.add('has-output');
  const releaseInfo = [
    ['Release version', data.release.release_number],
    ['Environment', 'Production'],
    ['Change ticket', data.release.change_ticket || 'To be confirmed'],
    ['Release date', data.release.release_date || 'To be confirmed'],
    ['Release engineer', data.release.release_engineer || 'To be confirmed'],
    ['Application IT owner', data.release.application_owner || 'To be confirmed'],
    ['Deployment method', 'GitOps (GitHub + ArgoCD)'],
    ['Rollback method', 'ArgoCD revision rollback'],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
  const scope = data.release.components.length ? `<div class="table-scroll"><table class="release-scope-table"><thead><tr><th>Component</th><th>Target image tag</th><th>Previous stable image tag</th><th>Owner</th><th>Type</th></tr></thead><tbody>${data.release.components.map(c => `<tr><td><strong>${escapeHtml(c.name)}</strong></td><td><code>${escapeHtml(c.version)}</code></td><td><code>${escapeHtml(c.previous_version || 'Confirm in ArgoCD History')}</code></td><td>${escapeHtml(c.owner || 'To be confirmed')}</td><td>${escapeHtml(c.kind)}</td></tr>${c.additional_instructions ? `<tr class="instructions-row"><td colspan="5"><b>Component instructions:</b> ${escapeHtml(c.additional_instructions)}</td></tr>` : ''}`).join('')}</tbody></table></div>` : '<p>Release scope has not been discovered yet. Run the approved GitHub workflow to compare the release branch with <strong>prd</strong>; it will add the changed components and image versions automatically.</p>';
  const notes = data.evidence.additional_information ? `<section><h3>Additional information</h3><p>${escapeHtml(data.evidence.additional_information)}</p></section>` : '';
  output.innerHTML = `<p class="source">${escapeHtml(data.filename)} · updated revision ${data.revision} · ${escapeHtml(data.source)}</p><p class="runbook-links"><a class="download-runbook" target="_blank" rel="noopener" href="/api/runbooks/${encodeURIComponent(data.id)}/view">View formatted runbook</a><a class="download-runbook" href="/api/runbooks/${encodeURIComponent(data.id)}/markdown">Download Markdown source</a></p><p class="form-note">Submitting this release number again updates this same runbook file.</p><section><h3>1. Release information</h3><div class="runbook-details">${releaseInfo}</div></section><section class="component-summary"><h3>2. Release scope</h3>${scope}</section>${notes}<section><h3>3. Release readiness checks</h3>${items(data.checks_required)}</section>${data.steps.map(step => `<section><h3>${escapeHtml(step.phase)}</h3>${items(step.items)}</section>`).join('')}`;
  renderReleaseControls(data);
  loadSavedRunbooks();
});
