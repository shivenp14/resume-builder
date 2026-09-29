import React, { useEffect, useMemo, useState } from 'react';
import { SuccessNotice } from './feedback.jsx';

/**
 * Application workflow components.
 *
 * Requests share the same API helper and readable validation errors.
 * The API returns source-backed records only; this UI does not invent scores
 * or aggregate statistics.
 */
import { API_BASE, request as apiRequest } from './api.js';
export { API_BASE, apiRequest };

function jsonBody(value) {
  return JSON.stringify(value);
}

function idempotencyKey(prefix) {
  const random = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

function ActionButton({ children, onClick, disabled = false, kind = 'primary', type = 'button' }) {
  return <button className={`button ${kind}`} type={type} onClick={onClick} disabled={disabled} aria-busy={Boolean(disabled && typeof children === 'string' && children.endsWith('…'))}>{children}</button>;
}

function Panel({ children, className = '' }) {
  return <section className={`quiet-card ${className}`}>{children}</section>;
}

function WorkflowError({ error }) {
  return error ? <p className="form-error" role="alert">{error}</p> : null;
}

function Status({ value }) {
  return <span key={value} className="status-pill" data-state={value}>{value || 'unknown'}</span>;
}

function useAsyncLoader(loader, dependencies) {
  const [state, setState] = useState({ data: null, error: '', loading: true });
  const reload = async () => {
    setState(current => ({ ...current, loading: true, error: '' }));
    try {
      const data = await loader();
      setState({ data, error: '', loading: false });
      return data;
    } catch (error) {
      setState({ data: null, error: error.message, loading: false });
      throw error;
    }
  };
  useEffect(() => { reload().catch(() => {}); }, dependencies); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload };
}

function SourceRecordForm({ onSubmit, busy }) {
  const [sourceType, setSourceType] = useState('skill');
  const [form, setForm] = useState({
    name: '', category: '', aliases: '', notes: '',
    content_item_id: '', text: '', supporting_facts: '', tags: '',
  });
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const submit = event => {
    event.preventDefault();
    const common = {
      source_type: sourceType,
      idempotency_key: idempotencyKey('materialize'),
    };
    const payload = sourceType === 'skill'
      ? {
        ...common,
        name: form.name.trim(),
        category: form.category.trim() || undefined,
        aliases: form.aliases.split(',').map(value => value.trim()).filter(Boolean),
        notes: form.notes.trim() || undefined,
      }
      : {
        ...common,
        content_item_id: Number(form.content_item_id),
        text: form.text.trim(),
        supporting_facts: form.supporting_facts.split('\n').map(value => value.trim()).filter(Boolean),
        tags: form.tags.split(',').map(value => value.trim()).filter(Boolean),
      };
    onSubmit(payload);
  };
  return <form className="form-card" onSubmit={submit}>
    <div className="form-grid">
      <label>Source type
        <select value={sourceType} onChange={event => setSourceType(event.target.value)}>
          <option value="skill">Verified skill</option>
          <option value="bullet">Source bullet</option>
        </select>
      </label>
      {sourceType === 'skill' ? <>
        <label>Skill name<input required value={form.name} onChange={event => set('name', event.target.value)} /></label>
        <label>Category<input value={form.category} onChange={event => set('category', event.target.value)} /></label>
        <label>Aliases<input value={form.aliases} onChange={event => set('aliases', event.target.value)} placeholder="Comma separated" /></label>
        <label>Notes<input value={form.notes} onChange={event => set('notes', event.target.value)} /></label>
      </> : <>
        <label>Content item ID<input required type="number" min="1" value={form.content_item_id} onChange={event => set('content_item_id', event.target.value)} /></label>
        <label className="full">Bullet text<textarea required rows="3" value={form.text} onChange={event => set('text', event.target.value)} /></label>
        <label className="full">Supporting facts<textarea required rows="3" value={form.supporting_facts} onChange={event => set('supporting_facts', event.target.value)} placeholder="One fact per line" /></label>
        <label>Tags<input value={form.tags} onChange={event => set('tags', event.target.value)} placeholder="Comma separated" /></label>
      </>}
    </div>
    <div className="form-actions"><ActionButton type="submit" disabled={busy}>{busy ? 'Materializing…' : 'Materialize source record'}</ActionButton></div>
  </form>;
}

/** Resolve unsupported requirements and, only after confirmation, create source data. */
export function MissingConfirmations({ applicationId, onChanged }) {
  const [confirmations, setConfirmations] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [materializeId, setMaterializeId] = useState(null);
  const load = async () => {
    setLoading(true); setError('');
    try { setConfirmations(await apiRequest(`/applications/${applicationId}/missing-confirmations`)); }
    catch (err) { setError(err.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [applicationId]);
  const create = async () => {
    setBusyId('create'); setError('');
    try { setConfirmations(await apiRequest(`/applications/${applicationId}/missing-confirmations`, { method: 'POST', body: '{}' })); }
    catch (err) { setError(err.message); }
    finally { setBusyId(null); }
  };
  const decide = async (confirmation, status) => {
    setBusyId(confirmation.id); setError('');
    try {
      await apiRequest(`/missing-confirmations/${confirmation.id}`, {
        method: 'PATCH', body: jsonBody({ status, context: confirmation.context || {}, requirement_id: confirmation.requirement_id }),
      });
      await load();
      onChanged?.();
    } catch (err) { setError(err.message); }
    finally { setBusyId(null); }
  };
  const materialize = async (confirmation, source) => {
    setBusyId(confirmation.id); setError('');
    try {
      await apiRequest(`/applications/${applicationId}/missing-confirmations/${confirmation.id}/materialize`, { method: 'POST', body: jsonBody(source) });
      setMaterializeId(null);
      await load();
      onChanged?.();
    } catch (err) { setError(err.message); }
    finally { setBusyId(null); }
  };
  if (loading) return <Panel><p>Loading confirmations…</p></Panel>;
  return <div className="workflow-stack">
    <Panel>
      <div className="section-head"><div><h2>Missing confirmations</h2><p>Confirm only facts you authored. Confirmed facts can be materialized into reusable source data.</p></div><ActionButton onClick={create} disabled={busyId === 'create'}>{busyId === 'create' ? 'Checking…' : 'Create from unsupported requirements'}</ActionButton></div>
      <WorkflowError error={error} />
      {!confirmations.length ? <p>No confirmation records yet. Create them from the current unsupported requirements.</p> : <div className="application-list">
        {confirmations.map(confirmation => <Panel className="list-card" key={confirmation.id}>
          <div className="section-head"><div><b>{confirmation.requirement}</b><small>Requirement {confirmation.requirement_id || 'without stable ID'}</small></div><Status value={confirmation.status} /></div>
          {confirmation.source_record && <SuccessNotice>Source record attached.</SuccessNotice>}
          <div className="form-actions">
            <ActionButton kind="ghost" disabled={busyId === confirmation.id || confirmation.status === 'confirmed'} onClick={() => decide(confirmation, 'confirmed')}>Confirm</ActionButton>
            <ActionButton kind="ghost" disabled={busyId === confirmation.id || confirmation.status === 'rejected'} onClick={() => decide(confirmation, 'rejected')}>Reject</ActionButton>
            <ActionButton kind="ghost" disabled={busyId === confirmation.id || confirmation.status === 'unresolved'} onClick={() => decide(confirmation, 'unresolved')}>Leave unresolved</ActionButton>
            {confirmation.status === 'confirmed' && !confirmation.materialization && <ActionButton onClick={() => setMaterializeId(materializeId === confirmation.id ? null : confirmation.id)}>Add source data</ActionButton>}
          </div>
          {materializeId === confirmation.id && <SourceRecordForm busy={busyId === confirmation.id} onSubmit={source => materialize(confirmation, source)} />}
        </Panel>)}
      </div>}
    </Panel>
  </div>;
}

function proposalText(value, fallback = '') {
  return typeof value === 'string' ? value : value == null ? fallback : String(value);
}

function ProposalPreview({ payload = {}, snapshot, sourceItems = [], sourceBullets = {}, sourceErrors = {}, snapshotLoading = false, sourceLoading = false, previewId }) {
  const selectedEntries = Array.isArray(payload.selected_entries) ? payload.selected_entries : [];
  const changes = Array.isArray(payload.bullet_changes) ? payload.bullet_changes : [];
  const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(Boolean) : [];
  const libraryItemById = new Map(sourceItems.map(item => [String(item.id), item]));
  const entryById = new Map();
  const sectionById = new Map();
  const bulletById = new Map();
  (snapshot?.sections || []).forEach(section => {
    (section.entries || []).forEach(entry => {
      entryById.set(String(entry.content_item_id), entry);
      sectionById.set(String(entry.content_item_id), section);
      (entry.bullets || []).forEach(bullet => bulletById.set(String(bullet.id), { bullet, entry }));
    });
  });
  sourceItems.forEach(item => {
    const key = String(item.id);
    if (!entryById.has(key)) {
      const type = proposalText(item.type, 'resume entry').toLowerCase();
      entryById.set(key, {
        content_item_id: item.id,
        title: item.title,
        display_title: item.title,
        organization: item.organization,
        dates: [item.start_date, item.end_date].filter(Boolean).join(' -- '),
        summary: item.summary,
      });
      sectionById.set(key, { title: type === 'project' ? 'Projects' : type.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase()) });
    }
    (sourceBullets[key] || []).forEach(bullet => bulletById.set(String(bullet.id), { bullet, entry: entryById.get(key) }));
  });
  const selectedBulletIds = new Set(selectedEntries.flatMap(entry => (entry.bullet_ids || []).map(String)));
  const unavailableChanges = changes.filter(change => !selectedBulletIds.has(String(change.bullet_id)) || !bulletById.has(String(change.bullet_id)));
  const linkedChanges = new Map(changes.map(change => [String(change.bullet_id), change]));
  const rationaleHeadingId = `proposal-rationale-${previewId}`;
  const notesHeadingId = `proposal-notes-${previewId}`;
  const includesHeadingId = `proposal-includes-${previewId}`;

  return <div className="proposal-preview">
    <p className="proposal-source-note">Previewed against current source records. Approval checks that the source data is still current.</p>
    <section className="proposal-preview-section" aria-labelledby={includesHeadingId}>
      <h3 id={includesHeadingId}>Resume content in this proposal</h3>
      {!selectedEntries.length ? <p className="proposal-empty">No resume entries are included in this proposal.</p> : <div className="proposal-entry-list">
        {selectedEntries.map((selection, index) => {
          const key = String(selection.content_item_id);
          const entry = entryById.get(key);
          const section = sectionById.get(key);
          if (!entry) return <article className="proposal-entry proposal-entry-missing" key={`${key}-${index}`}>
            <h4>Source entry unavailable</h4>
            <p>{snapshotLoading || sourceLoading ? 'Loading source details…' : sourceErrors.library ? 'Source details could not be loaded, so this entry cannot be previewed.' : 'This entry is no longer present in the active source library, so its content cannot be previewed.'}</p>
          </article>;
          const selectedIds = (selection.bullet_ids || []).map(String);
          const selectedBullets = selectedIds.map(id => ({ id, source: bulletById.get(id) }));
          const bullets = selectedBullets.filter(item => item.source && String(item.source.entry.content_item_id) === key).map(item => ({ id: item.id, bullet: item.source.bullet }));
          const unavailableBulletCount = selectedBullets.filter(item => !item.source).length;
          const mismatchedBulletCount = selectedBullets.filter(item => item.source && String(item.source.entry.content_item_id) !== key).length;
          const entryMeta = [entry.organization, entry.dates].filter(Boolean).map(value => proposalText(value)).join(' · ');
          const sourceItem = libraryItemById.get(key);
          return <article className="proposal-entry" key={`${key}-${index}`}>
            <div className="proposal-entry-heading">
              <div><small>{proposalText(section?.title, 'Resume entry')}</small><h4>{proposalText(entry.display_title || entry.title, 'Untitled entry')}</h4></div>
              {entryMeta && <span>{entryMeta}</span>}
            </div>
            {(entry.summary || entry.skills) && <p className="proposal-entry-summary">{proposalText(entry.summary || entry.skills)}</p>}
            {sourceItem?.is_archived && <p className="proposal-reference-note">This source item is archived. Restore it before approval if it is no longer part of the base resume.</p>}
            {bullets.length > 0 ? <ul className="proposal-bullet-list">{bullets.map(({ id, bullet }) => {
              const change = linkedChanges.get(id);
              return <li key={id}>
                {change ? <div className="proposal-rewrite">
                  <div className="proposal-wording proposal-before"><small>Current source wording</small><p>{proposalText(bullet.text, 'Source wording unavailable.')}</p></div>
                  <div className="proposal-wording proposal-after"><small>Proposed wording</small><p>{proposalText(change.proposed_text, 'No proposed wording provided.')}</p></div>
                  {change.rationale && <p className="proposal-change-reason">{proposalText(change.rationale)}</p>}
                </div> : <><small>Included wording</small><p>{proposalText(bullet.text, 'Source wording unavailable.')}</p></>}
              </li>;
            })}</ul> : selectedIds.length === 0 ? <p className="proposal-empty">No bullets are selected for this entry.</p> : null}
            {unavailableBulletCount > 0 && <p className="proposal-reference-note">{sourceErrors.bullets?.[key] ? 'Selected bullet wording could not be loaded. Retry the source preview to try again.' : sourceLoading ? 'Loading selected bullet wording…' : `${unavailableBulletCount} selected ${unavailableBulletCount === 1 ? 'bullet is' : 'bullets are'} unavailable in the current source records.`}</p>}
            {mismatchedBulletCount > 0 && <p className="proposal-reference-note">{mismatchedBulletCount === 1 ? 'A selected bullet belongs' : `${mismatchedBulletCount} selected bullets belong`} to a different source entry and could not be shown here.</p>}
          </article>;
        })}
      </div>}
    </section>
    {payload.rationale && <section className="proposal-explanation" aria-labelledby={rationaleHeadingId}>
      <h3 id={rationaleHeadingId}>Why this proposal was made</h3>
      <p>{proposalText(payload.rationale)}</p>
    </section>}
    <section className={`proposal-explanation${warnings.length ? ' proposal-warnings' : ''}`} aria-labelledby={notesHeadingId}>
      <h3 id={notesHeadingId}>{warnings.length ? 'Things to review' : 'Additional notes'}</h3>
      {warnings.length ? <ul>{warnings.map((warning, index) => <li key={`${index}-${warning}`}>{proposalText(warning)}</li>)}</ul> : <p>No additional notes were included.</p>}
    </section>
    {unavailableChanges.length > 0 && <section className="proposal-explanation proposal-reference-note">
      <h3>Changes without a matching source bullet</h3>
      <p>These proposed changes could not be linked to available source wording, so the preview does not attach them to an entry.</p>
      <ul>{unavailableChanges.map((change, index) => <li key={`${change.bullet_id}-${index}`}><small>Proposed wording</small><p>{proposalText(change.proposed_text, 'No proposed wording provided.')}</p></li>)}</ul>
    </section>}
  </div>;
}

/** Generate, inspect, and decide proposals; approval remains guarded by the API. */
export function ProposalWorkflow({ applicationId, onChanged }) {
  const { data: proposals, error: loadError, loading, reload } = useAsyncLoader(
    () => apiRequest(`/applications/${applicationId}/proposals`), [applicationId],
  );
  const snapshotState = useAsyncLoader(() => apiRequest(`/applications/${applicationId}/snapshot`), [applicationId]);
  const libraryState = useAsyncLoader(() => apiRequest('/content-items?include_archived=true'), [applicationId]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const rows = proposals || [];
  const selectedSourceIds = useMemo(() => [...new Set(rows.flatMap(proposal => (
    Array.isArray(proposal.payload?.selected_entries) ? proposal.payload.selected_entries : []
  ).map(selection => selection?.content_item_id).filter(id => id != null).map(String)))], [rows]);
  const selectedSourceIdsKey = selectedSourceIds.join(',');
  const bulletsState = useAsyncLoader(async () => {
    if (!Array.isArray(libraryState.data)) return { byItemId: {}, errors: {} };
    const libraryIds = new Set(libraryState.data.map(item => String(item.id)));
    const selectedIds = selectedSourceIds.filter(id => libraryIds.has(id));
    const results = await Promise.all(selectedIds.map(async id => {
      try { return [id, await apiRequest(`/content-items/${id}/bullets`), '']; }
      catch (loadError) { return [id, [], loadError.message || 'Could not load source bullets.']; }
    }));
    return {
      byItemId: Object.fromEntries(results.map(([id, bullets]) => [id, bullets])),
      errors: Object.fromEntries(results.filter(([, , loadError]) => loadError).map(([id, , loadError]) => [id, loadError])),
    };
  }, [libraryState.data, selectedSourceIdsKey]);
  const generate = async () => {
    setBusy('generate'); setError('');
    try { await apiRequest(`/applications/${applicationId}/proposals/generate`, { method: 'POST', body: jsonBody({ idempotency_key: idempotencyKey('proposal') }) }); await reload(); onChanged?.(); }
    catch (err) { setError(err.message); }
    finally { setBusy(''); }
  };
  const decision = async (proposal, status) => {
    setBusy(String(proposal.id)); setError('');
    try { await apiRequest(`/proposals/${proposal.id}/${status === 'approved' ? 'approve' : 'decision'}`, { method: 'POST', body: status === 'approved' ? undefined : jsonBody({ status }) }); await reload(); onChanged?.(); }
    catch (err) { setError(err.message); }
    finally { setBusy(''); }
  };
  if (loading) return <Panel><p role="status">Loading proposals…</p></Panel>;
  return <Panel className="workflow-stage">
    <div className="section-head"><div><h2>Proposal review</h2><p>Generated selections remain proposals until you approve them against current source data.</p></div><ActionButton onClick={generate} disabled={busy === 'generate'}>{busy === 'generate' ? 'Generating…' : 'Generate proposal'}</ActionButton></div>
    <WorkflowError error={error || loadError} />
    {!rows.length ? <div className="proposal-empty-state"><h3>No proposals yet</h3><p>After analyzing a job description, generate a proposal to review which resume content fits.</p></div> : <div className="application-list">{rows.map(proposal => {
      const sourceErrors = { library: libraryState.error, bullets: bulletsState.data?.errors || {} };
      const sourceError = libraryState.error || Object.keys(sourceErrors.bullets).length > 0;
      return <Panel className="list-card" key={proposal.id}>
        <div className="section-head"><div><b>Proposal {proposal.id}</b><small>{proposal.created_at ? new Date(proposal.created_at).toLocaleString() : ''}</small></div><Status value={proposal.status} /></div>
        {(snapshotState.error || sourceError) && <div className="proposal-load-error" role="status"><p>Some source details could not be loaded. Retry to refresh the preview.</p><ActionButton kind="ghost" onClick={() => { snapshotState.reload().catch(() => {}); libraryState.reload().catch(() => {}); }} disabled={snapshotState.loading || libraryState.loading}>{snapshotState.loading || libraryState.loading ? 'Loading source…' : 'Retry source preview'}</ActionButton></div>}
        {snapshotState.loading && !snapshotState.data ? <p className="proposal-empty" role="status">Loading current resume content…</p> : <ProposalPreview previewId={proposal.id} payload={proposal.payload || {}} snapshot={snapshotState.data} sourceItems={libraryState.data || []} sourceBullets={bulletsState.data?.byItemId || {}} sourceErrors={sourceErrors} snapshotLoading={snapshotState.loading} sourceLoading={libraryState.loading || bulletsState.loading} />}
        <div className="form-actions"><ActionButton disabled={busy === String(proposal.id) || proposal.status === 'approved'} onClick={() => decision(proposal, 'approved')}>Approve</ActionButton><ActionButton kind="ghost" disabled={busy === String(proposal.id) || proposal.status === 'rejected'} onClick={() => decision(proposal, 'rejected')}>Reject</ActionButton><ActionButton kind="ghost" disabled={busy === String(proposal.id) || proposal.status === 'pending'} onClick={() => decision(proposal, 'pending')}>Keep pending</ActionButton></div>
      </Panel>;
    })}</div>}
  </Panel>;
}

function SnapshotDetails({ snapshot }) {
  if (!snapshot) return null;
  const sections = Array.isArray(snapshot.sections) ? snapshot.sections : [];
  const entries = Array.isArray(snapshot.entries) ? snapshot.entries : [];
  return <div className="workflow-stack">
    <div className="details"><div><dt>Contact</dt><dd>{snapshot.contact?.name || 'No name in profile'}{snapshot.contact?.email ? ` · ${snapshot.contact.email}` : ''}</dd></div><div><dt>Selected entries</dt><dd>{entries.length}</dd></div></div>
    {sections.map((section, index) => <Panel className="list-card" key={section.id || section.key || index}><b>{section.title || section.name || section.key || `Section ${index + 1}`}</b><pre>{JSON.stringify(section, null, 2)}</pre></Panel>)}
  </div>;
}

/** Show the canonical snapshot, generate artifacts from one approved proposal, and submit an exact revision. */
export function SnapshotGenerationSubmit({ applicationId, onChanged }) {
  const applicationState = useAsyncLoader(() => apiRequest(`/applications/${applicationId}`), [applicationId]);
  const snapshotState = useAsyncLoader(() => apiRequest(`/applications/${applicationId}/snapshot`), [applicationId]);
  const proposalState = useAsyncLoader(() => apiRequest(`/applications/${applicationId}/proposals`), [applicationId]);
  const revisionState = useAsyncLoader(() => apiRequest(`/applications/${applicationId}/revisions`), [applicationId]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const approved = (proposalState.data || []).filter(proposal => proposal.status === 'approved');
  const [proposalId, setProposalId] = useState('');
  const [generatedArtifacts, setGeneratedArtifacts] = useState({});
  const [selectedRevisionId, setSelectedRevisionId] = useState('');
  const [submissionConfirmed, setSubmissionConfirmed] = useState(false);
  const [submittedApplication, setSubmittedApplication] = useState(null);
  const [notice, setNotice] = useState('');
  const revisions = revisionState.data || [];
  const generate = async () => {
    if (!proposalId) { setError('Select an approved proposal before generating.'); return; }
    setBusy('generate'); setError('');
    try { const generated = await apiRequest(`/applications/${applicationId}/generate`, { method: 'POST', body: jsonBody({ proposal_id: Number(proposalId) }) }); setGeneratedArtifacts(current => ({ ...current, [generated.id]: generated })); setNotice(`Revision ${generated.revision_number} generated.`); await revisionState.reload(); onChanged?.(); }
    catch (err) { setError(err.message); }
    finally { setBusy(''); }
  };
  const createCanonicalRevision = async () => {
    if (!snapshotState.data) return;
    setBusy('snapshot'); setError('');
    try { await apiRequest(`/applications/${applicationId}/revisions`, { method: 'POST', body: jsonBody({ resume_json: snapshotState.data }) }); await revisionState.reload(); onChanged?.(); }
    catch (err) { setError(err.message); }
    finally { setBusy(''); }
  };
  const submit = async revision => {
    setBusy(`submit-${revision.id}`); setError('');
    try {
      const submitted = await apiRequest(`/applications/${applicationId}/submit`, { method: 'POST', body: jsonBody({ revision_id: revision.id }) });
      setSubmittedApplication(submitted);
      setNotice(`Submission recorded with Revision ${revision.revision_number}.`);
      setSubmissionConfirmed(false);
      await Promise.allSettled([revisionState.reload(), applicationState.reload()]);
      onChanged?.();
    }
    catch (err) { setError(err.message); }
    finally { setBusy(''); }
  };
  if (applicationState.loading || snapshotState.loading || proposalState.loading || revisionState.loading) return <Panel><p>Loading snapshot and revisions…</p></Panel>;
  const loadError = applicationState.error || snapshotState.error || proposalState.error || revisionState.error;
  const selectedRevision = revisions.find(revision => String(revision.id) === selectedRevisionId);
  const generatedRevisions = revisions.filter(revision => revision.generated_at && revision.pdf_path && revision.status !== 'failed');
  const pdfHref = revision => `${API_BASE}${generatedArtifacts[revision.id]?.pdf_url || `/${revision.pdf_path}`}`;
  const downloadHref = revision => `${API_BASE}/applications/${applicationId}/revisions/${revision.id}/download.pdf`;
  const application = submittedApplication || applicationState.data;
  return <div className="workflow-stack">
    {notice && <SuccessNotice key={notice}>{notice}</SuccessNotice>}
    <Panel><div className="section-head"><div><h2>Canonical snapshot</h2><p>This is the current source projection. Creating a canonical revision requires it to match source state exactly.</p></div><ActionButton kind="ghost" onClick={createCanonicalRevision} disabled={busy === 'snapshot' || !snapshotState.data}>{busy === 'snapshot' ? 'Saving…' : 'Save canonical revision'}</ActionButton></div><SnapshotDetails snapshot={snapshotState.data} /></Panel>
    <Panel><div className="section-head"><div><h2>Generate artifacts</h2><p>Generation requires an approved proposal and creates an immutable revision with PDF and LaTeX artifacts.</p></div><div className="form-actions"><select aria-label="Approved proposal" value={proposalId} onChange={event => setProposalId(event.target.value)}><option value="">Select approved proposal</option>{approved.map(proposal => <option key={proposal.id} value={proposal.id}>Proposal {proposal.id}</option>)}</select><ActionButton onClick={generate} disabled={busy === 'generate' || !approved.length}>{busy === 'generate' ? 'Generating…' : 'Generate PDF + LaTeX'}</ActionButton></div></div>{!approved.length && <p>No approved proposals available.</p>}</Panel>
    <Panel><div className="section-head"><div><h2>Generated revisions</h2><p>Review the exact PDF before opening the employer’s form.</p></div></div><WorkflowError error={error || loadError} />{!revisions.length ? <p>No revisions yet.</p> : <div className="application-list">{revisions.map(revision => <div className="list-card" key={revision.id}><div className="section-head"><div><b>Revision {revision.revision_number}</b><small>{revision.generated_at ? new Date(revision.generated_at).toLocaleString() : 'Not generated'}</small></div><Status value={revision.status} /></div><p>{revision.page_count ? `${revision.page_count} page${revision.page_count === 1 ? '' : 's'}` : 'Page count unavailable'}</p>{revision.pdf_path && <div className="revision-file-actions"><a href={pdfHref(revision)} target="_blank" rel="noreferrer">View exact PDF</a><a href={downloadHref(revision)}>Download PDF</a></div>}{revision.latex_path && <p><a href={`${API_BASE}${generatedArtifacts[revision.id]?.latex_url || `/${revision.latex_path}`}`} target="_blank" rel="noreferrer">View LaTeX source</a></p>}</div>)}</div>}</Panel>
    {generatedRevisions.length > 0 && <Panel className="handoff-panel"><div className="section-head"><div><h2>Ready to apply</h2><p>Opening the employer page does not submit the application. Review the form there and submit it yourself.</p></div></div>
      {application?.job_url ? <div className="handoff-link-row"><a className="button" href={application.job_url} target="_blank" rel="noreferrer">Open employer application (new tab)</a>{application.simplify_url && <a href={application.simplify_url} target="_blank" rel="noreferrer">Open Simplify company page</a>}</div> : <p>Add an employer application link to this application before handing off.</p>}
      {application?.submitted_revision_id ? <p className="submitted-note" role="status">Submitted with revision ID {application.submitted_revision_id}{application.submitted_at ? ` on ${new Date(application.submitted_at).toLocaleDateString()}` : ''}.</p> : <div className="submission-confirmation">
        <label>Revision you uploaded<select value={selectedRevisionId} onChange={event => { setSelectedRevisionId(event.target.value); setSubmissionConfirmed(false); }}><option value="">Select the exact revision</option>{generatedRevisions.map(revision => <option key={revision.id} value={revision.id}>Revision {revision.revision_number}</option>)}</select></label>
        {selectedRevision && <p>Selected: Revision {selectedRevision.revision_number}. <a href={pdfHref(selectedRevision)} target="_blank" rel="noreferrer">View PDF</a> · <a href={downloadHref(selectedRevision)}>Download PDF</a></p>}
        <label className="review-confirm"><input type="checkbox" checked={submissionConfirmed} onChange={event => setSubmissionConfirmed(event.target.checked)} /><span>I submitted the employer form and uploaded the selected resume revision.</span></label>
        <div className="form-actions"><ActionButton disabled={!selectedRevision || !submissionConfirmed || Boolean(busy)} onClick={() => submit(selectedRevision)}>{busy === `submit-${selectedRevision?.id}` ? 'Confirming…' : 'Confirm submission'}</ActionButton></div>
      </div>}
    </Panel>}
  </div>;
}

/** List immutable revisions and fetch an ephemeral semantic comparison for two selected revisions. */
export function RevisionHistoryCompare({ applicationId }) {
  const { data: revisions, error, loading } = useAsyncLoader(() => apiRequest(`/applications/${applicationId}/revisions`), [applicationId]);
  const [fromId, setFromId] = useState('');
  const [toId, setToId] = useState('');
  const [comparison, setComparison] = useState(null);
  const [compareError, setCompareError] = useState('');
  const [busy, setBusy] = useState(false);
  const rows = revisions || [];
  const choices = useMemo(() => rows.filter(row => row.id !== Number(fromId)), [rows, fromId]);
  const compare = async event => {
    event.preventDefault(); setCompareError('');
    if (!fromId || !toId || fromId === toId) { setCompareError('Choose two different revisions to compare.'); return; }
    setBusy(true);
    try { setComparison(await apiRequest(`/applications/${applicationId}/revisions/compare?from_revision_id=${encodeURIComponent(fromId)}&to_revision_id=${encodeURIComponent(toId)}`)); }
    catch (err) { setCompareError(err.message); }
    finally { setBusy(false); }
  };
  if (loading) return <Panel><p>Loading revision history…</p></Panel>;
  return <div className="workflow-stack"><Panel><div className="section-head"><div><h2>Revision history</h2><p>Revisions are immutable. Select two revisions to inspect semantic changes.</p></div></div><WorkflowError error={error} />{!rows.length ? <p>No revisions have been created.</p> : <div className="application-list">{rows.map(revision => <div className="list-card" key={revision.id}><div className="section-head"><b>Revision {revision.revision_number}</b><Status value={revision.status} /></div><small>ID {revision.id}{revision.page_count ? ` · ${revision.page_count} pages` : ''}</small></div>)}</div>}</Panel>
    {rows.length > 1 && <Panel><form className="form-actions" onSubmit={compare}><label>Before<select value={fromId} onChange={event => { setFromId(event.target.value); if (event.target.value === toId) setToId(''); setComparison(null); setCompareError(''); }}><option value="">Select revision</option>{rows.map(row => <option key={row.id} value={row.id}>Revision {row.revision_number}</option>)}</select></label><label>After<select value={toId} onChange={event => { setToId(event.target.value); setComparison(null); setCompareError(''); }}><option value="">Select revision</option>{choices.map(row => <option key={row.id} value={row.id}>Revision {row.revision_number}</option>)}</select></label><ActionButton type="submit" disabled={busy}>{busy ? 'Comparing…' : 'Compare revisions'}</ActionButton></form><WorkflowError error={compareError} />{comparison && <div className="comparison-result" role="status"><p>{comparison.changed ? 'Changes found.' : 'No semantic changes found.'} Added: {comparison.summary.added}. Removed: {comparison.summary.removed}. Changed: {comparison.summary.changed}.</p><ul>{(comparison.changes || []).map((change, index) => <li key={`${change.path}-${index}`}><b>{change.kind}</b> · {change.entity} · {change.path}</li>)}</ul></div>}</Panel>}
  </div>;
}

/** Convenience composition for an application detail route. */
export function ApplicationWorkflows({ applicationId, active = 'confirmations', onChanged }) {
  if (active === 'confirmations') return <MissingConfirmations applicationId={applicationId} onChanged={onChanged} />;
  if (active === 'proposal') return <ProposalWorkflow applicationId={applicationId} onChanged={onChanged} />;
  if (active === 'preview') return <SnapshotGenerationSubmit applicationId={applicationId} onChanged={onChanged} />;
  if (active === 'revisions') return <RevisionHistoryCompare applicationId={applicationId} />;
  return null;
}
