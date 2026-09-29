import React, { useEffect, useRef, useState } from 'react';
import { API_BASE, request } from './api.js';

const PAGE_SIZE = 25;

function formatDate(value) {
  if (!value) return 'Date unavailable';
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return 'Date unavailable';
  const days = Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
  if (date.getTime() > Date.now() + 86400000) return 'Date unavailable';
  if (days === 0) return 'Added today';
  if (days === 1) return 'Added yesterday';
  if (days < 365) return `Added ${days} days ago`;
  const years = Math.floor(days / 365);
  return `Added ${years} ${years === 1 ? 'year' : 'years'} ago`;
}

const progressLabels = {
  not_imported: 'Not imported',
  resume_in_progress: 'Resume in progress',
  ready_to_apply: 'Ready to apply',
  applied: 'Applied',
  closed: 'Closed',
};

export function Jobs({ go }) {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [location, setLocation] = useState('');
  const [progress, setProgress] = useState('');
  const [page, setPage] = useState(1);
  const [response, setResponse] = useState({ items: [], total: 0, categories: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      setError('');
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries({ search, category, location, progress, page, page_size: PAGE_SIZE })) {
        if (value !== '' && value != null) params.set(key, String(value));
      }
      try {
        const next = await request(`/jobs?${params.toString()}`);
        if (!cancelled) setResponse(next);
      } catch (caught) {
        if (!cancelled) setError(caught.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, search ? 220 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [search, category, location, progress, page, retry]);

  const pageItems = response.items || [];
  const visible = pageItems;
  const pageCount = Math.max(1, Math.ceil((response.total || 0) / PAGE_SIZE));
  const clearFilters = () => { setSearch(''); setCategory(''); setLocation(''); setProgress(''); setPage(1); };

  return <>
    <div className="page-intro">
      <div><h1>Jobs</h1><p>Browse current internships, then bring a role into Morrow when you’re ready to tailor a resume.</p></div>
    </div>
    <section className="jobs-toolbar" aria-label="Search and filter jobs">
      <label className="jobs-search"><span>Search jobs</span><input value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} placeholder="Company or role" /></label>
      <label><span>Category</span><select value={category} onChange={event => { setCategory(event.target.value); setPage(1); }}><option value="">All categories</option>{(response.categories || []).map(item => <option key={item} value={item}>{item}</option>)}</select></label>
      <label><span>Location</span><input value={location} onChange={event => { setLocation(event.target.value); setPage(1); }} placeholder="Any location" /></label>
      <label><span>Progress</span><select value={progress} onChange={event => { setProgress(event.target.value); setPage(1); }}><option value="">All progress</option>{Object.entries(progressLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    </section>

    {error ? <section className="quiet-card jobs-state" role="alert"><h2>Jobs unavailable</h2><p>{error}</p><button className="button" onClick={() => setRetry(value => value + 1)}>Retry</button></section> : <>
      <div className="jobs-results-head" aria-live="polite"><p>{loading ? 'Loading opportunities…' : `${response.total || 0} ${response.total === 1 ? 'opportunity' : 'opportunities'}`}</p><a href="https://github.com/SimplifyJobs/Summer2027-Internships" target="_blank" rel="noreferrer">Public internship list</a></div>
      {loading && !pageItems.length ? <div className="quiet-card jobs-state"><p>Loading current listings…</p></div> : visible.length ? <div className="jobs-list">
        {visible.map(job => {
          const locations = (job.locations || []).filter(Boolean);
          const linked = job.application_id;
          return <article key={job.id} className="job-row">
            <div className="job-row-main">
              <div className="job-company-mark" aria-hidden="true">{job.company?.trim()?.[0]?.toUpperCase() || '—'}</div>
              <div className="job-copy"><h2>{job.position}</h2><p>{job.company}</p><div className="job-meta"><span>{locations.join(' · ') || 'Location not listed'}</span><span title={job.date_posted ? new Date(Number(job.date_posted) * 1000).toLocaleDateString() : undefined}>{formatDate(job.date_posted)}</span>{job.category && <span>{job.category}</span>}</div></div>
              <span className={`job-progress ${job.progress || 'not_imported'}`}>{progressLabels[job.progress] || progressLabels.not_imported}</span>
            </div>
            <div className="job-row-actions">
              {job.application_url && <a href={job.application_url} target="_blank" rel="noreferrer">Employer posting <span className="new-tab-note">(new tab)</span></a>}
              {job.simplify_url && <a href={job.simplify_url} target="_blank" rel="noreferrer">Simplify company page <span className="new-tab-note">(new tab)</span></a>}
              <button className="button" onClick={() => linked ? go(`/applications/${linked}`) : go(`/jobs/import/${encodeURIComponent(job.id)}`, { job })}>{linked ? 'Open application' : 'Review import'}</button>
            </div>
          </article>;
        })}
      </div> : <section className="quiet-card jobs-state"><h2>No matching jobs</h2><p>Try another search or clear the filters.</p><button className="button ghost" onClick={clearFilters}>Clear filters</button></section>}
      <nav className="jobs-pagination" aria-label="Job listing pages"><button className="button ghost" disabled={page <= 1 || loading} onClick={() => setPage(value => value - 1)}>Previous</button><span>Page {page} of {pageCount}</span><button className="button ghost" disabled={page >= pageCount || loading} onClick={() => setPage(value => value + 1)}>Next</button></nav>
    </>}
  </>;
}

function ImportPage({ job, id, go, reloadApplications }) {
  const [resumes, setResumes] = useState([]);
  const [form, setForm] = useState({ company: job?.company || '', position: job?.position || '', location: job?.locations?.join(', ') || '', job_url: job?.application_url || '', simplify_url: job?.simplify_url || '', job_description: '', base_resume_id: '' });
  const [descriptionState, setDescriptionState] = useState(job ? 'loading' : 'unavailable');
  const [descriptionNote, setDescriptionNote] = useState('');
  const [descriptionChecked, setDescriptionChecked] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resumeError, setResumeError] = useState('');
  const descriptionEditVersion = useRef(0);
  const jobUrlEditVersion = useRef(0);
  const descriptionRequestId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    request('/base-resumes').then(rows => {
      if (!cancelled) { setResumes(rows); if (rows[0]) setForm(current => ({ ...current, base_resume_id: String(rows[0].id) })); }
    }).catch(caught => { if (!cancelled) setResumeError(caught.message); });
    return () => { cancelled = true; };
  }, []);

  const retrieveDescription = async () => {
    if (!form.job_url) { setDescriptionState('unavailable'); setDescriptionNote('Add the employer posting link to retrieve a description.'); return; }
    const requestId = ++descriptionRequestId.current;
    const capturedDescriptionVersion = descriptionEditVersion.current;
    const capturedUrlVersion = jobUrlEditVersion.current;
    setDescriptionState('loading'); setDescriptionNote(''); setError('');
    try {
      const result = await request('/jobs/description', { method: 'POST', body: JSON.stringify({ url: form.job_url }) });
      if (requestId !== descriptionRequestId.current || capturedUrlVersion !== jobUrlEditVersion.current) return;
      const preserveUserEdit = capturedDescriptionVersion !== descriptionEditVersion.current;
      if (result.text?.trim()) {
        const reliable = result.status === 'ok';
        setDescriptionState(reliable ? 'found' : 'unavailable');
        if (!preserveUserEdit) {
          setForm(current => ({ ...current, job_description: result.text }));
          setDescriptionChecked(false);
        }
        setDescriptionNote(preserveUserEdit
          ? 'A description was retrieved, and your current edits were kept. Compare them with the employer posting before continuing.'
          : reliable
            ? 'Description retrieved. Review and correct it before creating the application.'
            : `${result.reason || 'The page returned text with low confidence.'} Review and correct the extracted text, or replace it with the employer’s description.`);
      } else {
        setDescriptionState('unavailable');
        setDescriptionNote(result.reason || 'The posting did not return a reliable description. Paste it below to continue.');
      }
    } catch (caught) {
      if (requestId !== descriptionRequestId.current || capturedUrlVersion !== jobUrlEditVersion.current) return;
      setDescriptionState('unavailable');
      setDescriptionNote(`${caught.message} Paste the description below to continue.`);
    }
  };

  useEffect(() => { if (job) retrieveDescription(); }, [job?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (key, value) => {
    setForm(current => ({ ...current, [key]: value }));
    if (key === 'job_description') { descriptionEditVersion.current += 1; setDescriptionChecked(false); }
    if (key === 'job_url') {
      jobUrlEditVersion.current += 1;
      descriptionRequestId.current += 1;
      if (descriptionState === 'loading') {
        setDescriptionState('unavailable');
        setDescriptionNote('The employer link changed while retrieval was running. Try retrieval again for the updated link.');
      }
    }
  };
  async function create(event) {
    event.preventDefault();
    if (!descriptionChecked) { setError('Review and confirm the job description before creating this application.'); return; }
    setBusy(true); setError('');
    const payload = { company: form.company.trim(), position: form.position.trim(), location: form.location.trim(), job_url: form.job_url.trim(), job_description: form.job_description.trim(), base_resume_id: Number(form.base_resume_id), source: 'Simplify public listings', source_listing_id: job.id, simplify_url: form.simplify_url.trim() || null };
    try {
      const application = await request('/applications', { method: 'POST', body: JSON.stringify(payload) });
      await reloadApplications();
      go(`/applications/${application.id}`);
    } catch (caught) {
      if (caught.status === 409) {
        const existingId = caught.detail?.application_id || caught.application_id;
        if (existingId) { go(`/applications/${existingId}`); return; }
        try {
          const refreshed = await request(`/jobs?search=${encodeURIComponent(job.company)}&page=1&page_size=${PAGE_SIZE}`);
          const duplicate = (refreshed.items || []).find(item => String(item.id) === String(job.id));
          if (duplicate?.application_id) { go(`/applications/${duplicate.application_id}`); return; }
        } catch { /* Keep the original duplicate response available for recovery. */ }
        setError('This listing is already linked to an application, but Morrow could not find it. Return to Jobs and refresh the list.');
      } else setError(caught.message);
    } finally { setBusy(false); }
  }

  if (!job) return <>
    <div className="page-intro"><div><h1>Import listing</h1><p>This listing is not available in navigation history.</p></div></div>
    <section className="quiet-card jobs-state"><p>Return to Jobs and open the listing again.</p><button className="button" onClick={() => go('/jobs')}>Back to Jobs</button></section>
  </>;

  return <>
    <div className="page-intro"><div><h1>Review this opportunity</h1><p>Check the listing details and job description. Morrow creates the application only after you confirm this review.</p></div><div className="page-actions"><button className="button ghost" onClick={() => go('/jobs')}>Back to Jobs</button></div></div>
    <form className="job-import-form" onSubmit={create}>
      <section className="quiet-card form-card">
        <div className="form-section"><h2>Opportunity details</h2><p>Correct any details that differ from the employer posting.</p></div>
        <div className="form-grid">
          <label>Company<input required value={form.company} onChange={event => set('company', event.target.value)} /></label>
          <label>Role<input required value={form.position} onChange={event => set('position', event.target.value)} /></label>
          <label>Location<input value={form.location} onChange={event => set('location', event.target.value)} /></label>
          <label>Base resume<select aria-label="Base resume" required value={form.base_resume_id} onChange={event => set('base_resume_id', event.target.value)}><option value="">Select a base resume</option>{resumes.map(resume => <option key={resume.id} value={resume.id}>{resume.name}</option>)}</select></label>
          <label className="full">Employer application link<input type="url" required value={form.job_url} onChange={event => set('job_url', event.target.value)} /></label>
          <label className="full">Simplify company page <span className="optional-note">Optional</span><input type="url" value={form.simplify_url} onChange={event => set('simplify_url', event.target.value)} /></label>
        </div>
        {form.simplify_url && <p className="job-import-source">Simplify company page: <a href={form.simplify_url} target="_blank" rel="noreferrer">Open page in a new tab</a></p>}
      </section>
      <section className="quiet-card form-card description-review">
        <div className="form-section"><h2>Job description</h2><p>Retrieved text can be incomplete. Review it, make corrections, or paste the description from the employer page.</p></div>
        {descriptionState === 'loading' && <p className="description-status" role="status">Trying to retrieve the employer’s description…</p>}
        {descriptionNote && <p className={`description-status ${descriptionState === 'unavailable' ? 'notice' : ''}`} role="status">{descriptionNote}</p>}
        {resumeError && <p className="form-error" role="alert">Could not load base resumes: {resumeError}</p>}
        {!resumes.length && !resumeError && <p className="description-status">Loading base resumes…</p>}
        <label className="description-field">Review or paste the full job description<textarea required rows="16" value={form.job_description} onChange={event => set('job_description', event.target.value)} placeholder="Paste the complete job description here" /></label>
        <div className="description-tools">
          <button type="button" className="text-button" onClick={retrieveDescription} disabled={descriptionState === 'loading'}>{descriptionState === 'loading' ? 'Retrieving…' : 'Try retrieval again'}</button>
          {form.job_url && <a href={form.job_url} target="_blank" rel="noreferrer">Open employer posting (new tab)</a>}
        </div>
        <label className="review-confirm"><input type="checkbox" checked={descriptionChecked} onChange={event => setDescriptionChecked(event.target.checked)} /><span>I reviewed and confirm this job description for the application.</span></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form-actions"><button type="button" className="button ghost" onClick={() => go('/jobs')}>Cancel</button><button className="button" type="submit" disabled={busy || !resumes.length || !form.base_resume_id || !form.job_description.trim()}>{busy ? 'Creating application…' : 'Create application and continue'}</button></div>
      </section>
    </form>
  </>;
}

export { ImportPage };
