import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ApplicationWorkflows } from './application-workflows.jsx';
import {
  SourceLibrary,
  SourceItemCreate,
  SourceItemDetail,
  SkillsPage,
  SkillDetail,
  ProfilesPage,
  ProfileDetail,
  BaseResumesPage,
  BaseResumeDetail,
  BackupsPage,
} from './source-workflows.jsx';
import './styles.css';

import { request } from './api.js';

function idempotencyKey(prefix) {
  const random = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

const navGroups = [
  { label: 'Work', items: [['/home', 'Home', 'home'], ['/applications', 'Applications', 'briefcase']] },
  {
    label: 'Source records',
    items: [
      ['/library', 'Source library', 'library'],
      ['/skills', 'Skills', 'spark'],
      ['/profiles', 'Profiles', 'user'],
      ['/resumes', 'Base resumes', 'document'],
    ],
  },
  { label: 'System', items: [['/settings/data', 'Data safety', 'shield']] },
];

function Icon({ name, size = 18 }) {
  const paths = {
    home: <><path d="M3.5 9.2 10 3.8l6.5 5.4"/><path d="M5.4 8.2v7.5h9.2V8.2"/><path d="M8.3 15.7v-4.4h3.4v4.4"/></>,
    briefcase: <><rect x="3" y="5.8" width="14" height="10.2" rx="2"/><path d="M7.2 5.8V4.5c0-.8.6-1.4 1.4-1.4h2.8c.8 0 1.4.6 1.4 1.4v1.3M3 10.1h14M8.2 10.1v1.4h3.6v-1.4"/></>,
    library: <><path d="M4.2 3.5h8.2c1.4 0 2.5 1.1 2.5 2.5v10.5H6.7a2.5 2.5 0 0 1-2.5-2.5V3.5Z"/><path d="M6.7 13.5h8.2M7.2 7h4.8M7.2 9.8h3.4"/></>,
    spark: <><path d="m10 2 .8 4.3L15 7l-4.2.8L10 12l-.8-4.2L5 7l4.2-.7L10 2Z"/><path d="m15.5 11 .4 2.2 2.1.4-2.1.4-.4 2.2-.4-2.2-2.1-.4 2.1-.4.4-2.2ZM4.5 11.5l.5 2.7 2.5.4-2.5.5-.5 2.5-.5-2.5-2.5-.5 2.5-.4.5-2.7Z"/></>,
    user: <><circle cx="10" cy="6.3" r="3.1"/><path d="M4.2 17c.5-3.3 2.4-5 5.8-5s5.3 1.7 5.8 5"/></>,
    document: <><path d="M5 2.8h6.2l3.8 3.8v10.6H5V2.8Z"/><path d="M11 2.8v4h4M7.7 10.2h4.6M7.7 13h4.6"/></>,
    shield: <><path d="M10 2.4 16 5v4.4c0 3.8-2.2 6.5-6 8.2-3.8-1.7-6-4.4-6-8.2V5l6-2.6Z"/><path d="m7.3 10 1.8 1.8 3.7-4"/></>,
    menu: <><path d="M3 5.5h14M3 10h14M3 14.5h14"/></>,
    close: <><path d="m4.5 4.5 11 11M15.5 4.5l-11 11"/></>,
    arrow: <><path d="M3.5 10h13M12 5.5l4.5 4.5-4.5 4.5"/></>,
  };
  return <svg aria-hidden="true" className="icon" width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.45" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function Button({ children, onClick, kind = 'primary', disabled, type = 'button' }) {
  return <button type={type} className={`button ${kind}`} onClick={onClick} disabled={disabled} aria-busy={Boolean(disabled && typeof children === 'string' && children.endsWith('…'))}>{children}</button>;
}

function Card({ children, className = '' }) {
  return <section className={`quiet-card ${className}`}>{children}</section>;
}

function Empty({ title, copy, action }) {
  return <Card className="empty-card"><div className="empty-state"><span className="empty-rule" aria-hidden="true"/><div><h2>{title}</h2><p>{copy}</p></div>{action}</div></Card>;
}

function routeLabel(path) {
  if (path === '/home') return 'Overview';
  if (path.startsWith('/applications')) return 'Applications';
  if (path.startsWith('/library')) return 'Source library';
  if (path.startsWith('/skills')) return 'Skills';
  if (path.startsWith('/profiles')) return 'Profiles';
  if (path.startsWith('/resumes')) return 'Base resumes';
  if (path.startsWith('/settings')) return 'Data safety';
  return 'Workspace';
}

function Shell({ path, go, children }) {
  const [navOpen, setNavOpen] = useState(false);
  const sidebarRef = useRef(null);
  const menuRef = useRef(null);
  const mainRef = useRef(null);
  const [mobile, setMobile] = useState(() => matchMedia('(max-width: 760px)').matches);
  useEffect(() => {
    const media = matchMedia('(max-width: 760px)');
    const change = () => { setMobile(media.matches); setNavOpen(false); };
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    mainRef.current?.focus({ preventScroll: true });
  }, [path]);
  useEffect(() => {
    if (!navOpen || !mobile) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Wait until visibility and inert changes have reached the rendered drawer.
    const focusFrame = requestAnimationFrame(() => sidebarRef.current?.querySelector('.mobile-close')?.focus());
    const handleKey = event => {
      if (event.key === 'Escape') { event.preventDefault(); setNavOpen(false); }
      if (event.key === 'Tab') {
        const buttons = [...sidebarRef.current.querySelectorAll('button')].filter(button => !button.disabled);
        const first = buttons[0], last = buttons.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      cancelAnimationFrame(focusFrame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKey);
      menuRef.current?.focus({ preventScroll: true });
    };
  }, [navOpen, mobile]);
  const navigate = next => { setNavOpen(false); go(next); };
  useEffect(() => { setNavOpen(false); }, [path]);

  return <div className="app-shell">
    <div className={`mobile-nav-backdrop ${navOpen ? 'open' : ''}`} aria-hidden="true" onClick={() => setNavOpen(false)} />
    <aside ref={sidebarRef} id="workspace-navigation" className={`sidebar ${navOpen ? 'open' : ''}`} inert={mobile && !navOpen} role={mobile && navOpen ? 'dialog' : undefined} aria-modal={mobile && navOpen ? true : undefined} aria-label="Workspace navigation">
      <div className="brand-row">
        <button className="brand" onClick={() => navigate('/home')} aria-label="Morrow home"><span className="mark"><i /></span><span>morrow</span></button>
        <button className="mobile-close" onClick={() => setNavOpen(false)} aria-label="Close navigation"><Icon name="close" /></button>
      </div>
      <nav className="nav-groups" aria-label="Main navigation">
        {navGroups.map(group => <div className="nav-group" key={group.label}>
          <span className="nav-label">{group.label}</span>
          <div className="nav-list">
            {group.items.map(([url, label, icon]) => {
              const active = path === url || path.startsWith(`${url}/`);
              return <button key={url} className={`nav-link ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} onClick={() => navigate(url)}><Icon name={icon} /><span>{label}</span></button>;
            })}
          </div>
        </div>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="provenance-note"><span className="provenance-track" aria-hidden="true"><i /><i /></span><p><b>Source stays intact.</b><span>Approved changes become separate revisions.</span></p></div>
      </div>
    </aside>
    <div className="main-wrap" inert={mobile && navOpen}>
      <header className="topbar">
        <button ref={menuRef} aria-expanded={navOpen} aria-controls="workspace-navigation" className="menu-button" onClick={() => setNavOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
        <div className="breadcrumb"><button onClick={() => navigate('/home')}>Morrow</button><span>/</span><b>{routeLabel(path)}</b></div>
        <div className="workspace-state"><span aria-hidden="true" />Local workspace</div>
      </header>
      <main key={path} ref={mainRef} tabIndex={-1} className="page-content">{children}</main>
    </div>
  </div>;
}

function Header({ title, copy, action }) {
  return <div className="page-intro"><div><h1>{title}</h1>{copy && <p>{copy}</p>}</div>{action && <div className="page-actions">{action}</div>}</div>;
}

function Rows({ items, render, empty }) {
  return items.length ? <Card className="application-list">{items.map(render)}</Card> : empty;
}

function ApplicationLoadState({ error, loading, reload }) {
  if (loading) return <Empty title="Loading applications" copy="Fetching your saved applications." />;
  if (error) return <Empty title="Applications unavailable" copy={`We couldn’t load your applications. ${error}`} action={<Button onClick={reload}>Retry</Button>} />;
  return null;
}

function ApplicationRow({ application, go }) {
  return <button className="application-row" onClick={() => go(`/applications/${application.id}`)}>
    <span className="company-mark" aria-hidden="true">{application.company?.[0]?.toUpperCase() || '—'}</span>
    <span className="application-copy"><b>{application.company}</b><span>{application.position}</span></span>
    <span className="status-pill">{application.status}</span>
    <Icon name="arrow" size={16} />
  </button>;
}

function Home({ apps, appsError, appsLoading, reload, go }) {
  const next = apps.find(application => application.status === 'draft') || apps[0];
  const loadState = appsLoading || appsError ? <ApplicationLoadState error={appsError} loading={appsLoading} reload={reload} /> : null;
  return <>
    <Header title="Resume workspace" copy="Build from verified source records, then tailor a separate revision for each application." />
    {loadState || <div className="home-grid">
      <section className="home-list">
        <div className="section-head"><div><h2>Applications</h2><p>{apps.length ? 'Continue where you left off.' : 'Your active work will appear here.'}</p></div>{apps.length > 0 && <button className="text-button" onClick={() => go('/applications')}>View all <Icon name="arrow" size={15} /></button>}</div>
        <Rows
          items={apps.slice(0, 6)}
          render={application => <ApplicationRow key={application.id} application={application} go={go} />}
          empty={<Empty title="No applications yet" copy="Create an application when you have a job description and base resume ready." action={<Button onClick={() => go('/applications/new')}>Add application</Button>} />}
        />
      </section>
      <aside className="home-aside">
        {next ? <Card className="focus-panel"><span className="mono-label">Next action</span><h2>{next.company}</h2><p className="focus-role">{next.position}</p><p>Open the application to continue its analysis, proposal, and revision workflow.</p><Button onClick={() => go(`/applications/${next.id}`)}>Continue application <Icon name="arrow" size={15} /></Button></Card> : <Card className="source-principle"><div className="source-principle-line" aria-hidden="true"><span>Source</span><i /><span>Revision</span></div><h2>One source of truth, many tailored revisions.</h2><p>Your reusable records remain editable. Every application keeps its own approved output.</p></Card>}
      </aside>
    </div>}
  </>;
}

function Applications({ apps, appsError, appsLoading, go, reload }) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const visible = useMemo(() => apps.filter(application => (!status || application.status === status) && `${application.company} ${application.position}`.toLowerCase().includes(query.toLowerCase())), [apps, query, status]);
  const loadState = appsLoading || appsError ? <ApplicationLoadState error={appsError} loading={appsLoading} reload={reload} /> : null;
  return <>
    <Header title="Applications" copy="Each opportunity keeps its own analysis, proposal, revision, and submission trail." action={<Button onClick={() => go('/applications/new')}>New application</Button>} />
    {loadState || <>
      <div className="applications-toolbar">
        <label className="search-wrap"><span className="sr-only">Search applications</span><input className="search-field" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search company or role" /></label>
        <label className="filter-wrap"><span>Status</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="">All</option>{['draft', 'applied', 'interviewing', 'offer', 'rejected', 'withdrawn'].map(value => <option key={value}>{value}</option>)}</select></label>
      </div>
      <Rows items={visible} render={application => <ApplicationRow key={application.id} application={application} go={go} />} empty={<Empty title={apps.length ? "No matching applications" : "No applications yet"} copy={apps.length ? "Adjust the search or status filter." : "Add an application to start tailoring a resume for a role."} />} />
    </>}
  </>;
}

function NewApp({ go, reload }) {
  const [resumes, setResumes] = useState([]);
  const [form, setForm] = useState({ company: '', position: '', job_description: '', base_resume_id: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    request('/base-resumes').then(rows => {
      setResumes(rows);
      if (rows[0]) setForm(current => ({ ...current, base_resume_id: rows[0].id }));
    }).catch(caught => setError(caught.message));
  }, []);
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const application = await request('/applications', { method: 'POST', body: JSON.stringify({ ...form, base_resume_id: Number(form.base_resume_id) }) });
      await reload();
      go(`/applications/${application.id}`);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }
  return <>
    <Header title="Add an application" copy="Start a job-specific workspace without changing your source library." />
    <form className="form-layout" onSubmit={submit}>
      <Card className="form-card">
        <div className="form-section"><h2>Role</h2><p>Name the opportunity and choose the resume it starts from.</p></div>
        <div className="form-grid">
          <label>Company<input required value={form.company} onChange={event => set('company', event.target.value)} /></label>
          <label>Role<input required value={form.position} onChange={event => set('position', event.target.value)} /></label>
          <label>Base resume<select required value={form.base_resume_id} onChange={event => set('base_resume_id', event.target.value)}><option value="">Select a base resume</option>{resumes.map(resume => <option key={resume.id} value={resume.id}>{resume.name}</option>)}</select></label>
          <label>Location<input value={form.location || ''} onChange={event => set('location', event.target.value)} /></label>
          <label>Job URL<input type="url" value={form.job_url || ''} onChange={event => set('job_url', event.target.value)} /></label>
          <label>Source<input value={form.source || ''} onChange={event => set('source', event.target.value)} /></label>
          <label className="full">Job description<textarea required rows="11" value={form.job_description} onChange={event => set('job_description', event.target.value)} /></label>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form-actions"><Button kind="ghost" onClick={() => go('/applications')}>Cancel</Button><Button type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create application'}</Button></div>
      </Card>
      <aside className="form-aside"><div className="context-note"><b>What happens next</b><p>Morrow saves the job description, then analyzes requirements against verified source material.</p></div></aside>
    </form>
  </>;
}

const applicationTabs = [['Overview', ''], ['Coverage', 'coverage'], ['Confirmations', 'confirmations'], ['Proposal', 'proposal'], ['Generate', 'preview'], ['Revisions', 'revisions']];

function ApplicationNav({ id, active = '', go }) {
  return <nav className="subnav" aria-label="Application workflow">{applicationTabs.map(([label, tab]) => <button key={label} className={active === tab ? 'active' : ''} aria-current={active === tab ? 'page' : undefined} onClick={() => go(`/applications/${id}${tab ? `/${tab}` : ''}`)}>{label}</button>)}</nav>;
}

function AppView({ id, go }) {
  const [application, setApplication] = useState();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const analyzeKey = useRef(idempotencyKey(`analysis-${id}`));
  useEffect(() => { request(`/applications/${id}`).then(setApplication).catch(caught => setError(caught.message)); }, [id]);
  async function analyze() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await request(`/applications/${id}/analyze`, { method: 'POST', headers: { 'Idempotency-Key': analyzeKey.current }, body: JSON.stringify({ idempotency_key: analyzeKey.current }) });
      go(`/applications/${id}/coverage`);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }
  if (error && !application) return <Empty title="Application unavailable" copy={error} />;
  if (!application) return <Empty title="Loading application" copy="Fetching the saved application." />;
  return <>
    <Header title={application.position} copy={application.company} action={<Button onClick={analyze} disabled={busy}>{busy ? 'Analyzing…' : 'Analyze job description'}</Button>} />
    <ApplicationNav id={id} go={go} />
    {error && <p className="form-error" role="alert">{error} You can try analyzing again.</p>}
    <div className="workflow stepper">{['Base resume', 'Analysis', 'Coverage', 'Proposal', 'Revision', 'Submitted'].map((label, index) => <div className={`step ${index === 1 ? 'current' : ''}`} key={label}><span>{index + 1}</span><label>{label}</label></div>)}</div>
    <div className="overview-grid">
      <Card><div className="card-heading"><h2>Application details</h2><span className="status-pill">{application.status}</span></div><dl className="details">{[['Base resume', application.base_resume_id], ['Location', application.location], ['Deadline', application.application_deadline], ['Source', application.source]].filter(row => row[1]).map(row => <div key={row[0]}><dt>{row[0]}</dt><dd>{String(row[1])}</dd></div>)}</dl>{application.job_url && <a className="inline-link" href={application.job_url} target="_blank" rel="noreferrer">Open job posting <Icon name="arrow" size={14} /></a>}</Card>
      <Card className="soft-panel"><span className="mono-label">Next action</span><h2>Analyze the role</h2><p>Create durable requirements and inspect how each one maps to source-backed evidence.</p><Button onClick={analyze} disabled={busy}>{busy ? 'Analyzing…' : 'Analyze job description'}</Button></Card>
    </div>
  </>;
}

function Coverage({ id, go }) {
  const [data, setData] = useState();
  const [error, setError] = useState('');
  useEffect(() => {
    Promise.all([request(`/applications/${id}/requirements`), request(`/applications/${id}/comparison`), request(`/applications/${id}/missing-confirmations`)])
      .then(result => setData({ req: result[0], comparison: result[1], confirm: result[2] }))
      .catch(caught => setError(caught.message));
  }, [id]);
  if (error) return <Empty title="Coverage unavailable" copy={error} />;
  if (!data) return <Empty title="Loading coverage" copy="Loading requirements and source-backed matches." />;
  const classified = data.req.reduce((all, requirement) => {
    const detail = data.comparison.requirements?.find(row => row.requirement_id === requirement.id || row.id === requirement.id || row.text === requirement.text);
    const group = detail?.classification || (['well_represented', 'weakly_represented', 'library_only', 'unsupported'].find(key => (data.comparison[key] || []).includes(requirement.text)) || 'unsupported');
    (all[group] ??= []).push(requirement);
    return all;
  }, {});
  const groups = [['Well represented', classified.well_represented], ['Weakly represented', classified.weakly_represented], ['Library only', classified.library_only], ['Unsupported', classified.unsupported]].filter(([, items]) => items?.length);
  const unresolved = data.confirm.filter(row => row.status === 'unresolved').length;
  return <>
    <Header title="Analysis & coverage" copy="See which requirements are supported by the selected resume, the wider library, or no confirmed source." />
    <ApplicationNav id={id} active="coverage" go={go} />
    <div className="coverage-layout">
      <div>{groups.length ? groups.map(([label, items]) => <section className="requirements" key={label}><div className="section-head"><h2>{label}</h2><span>{items.length} {items.length === 1 ? 'requirement' : 'requirements'}</span></div>{items.map(requirement => <Card className="requirement-row" key={requirement.id}><b>{requirement.requirement || requirement.text || requirement.source_text}</b><small>{[requirement.category, requirement.priority, `ID ${requirement.id}`].filter(Boolean).join(' · ')}</small></Card>)}</section>) : <Empty title="No requirements found" copy="Analyze the saved job description to create requirement records." />}</div>
      <aside><Card className="soft-panel sticky-panel"><span className="mono-label">Confirmation queue</span><h2>{unresolved} unresolved</h2><p>Confirm only user-authored facts, then materialize them as a verified skill or source bullet.</p><Button onClick={() => go(`/applications/${id}/confirmations`)}>Resolve gaps</Button></Card></aside>
    </div>
  </>;
}

function ApplicationWorkflowPage({ id, active, go, reload }) {
  const titles = {
    confirmations: ['Confirm source facts', 'Resolve unsupported requirements before adding anything to your reusable source library.'],
    proposal: ['Review proposal', 'Inspect and approve a source-backed selection before generating a revision.'],
    preview: ['Generate revision', 'Create immutable artifacts from an approved proposal and submit the exact revision you intend to use.'],
    revisions: ['Revision history', 'Inspect immutable revisions and compare what changed between them.'],
  };
  const [title, copy] = titles[active];
  return <><Header title={title} copy={copy} /><ApplicationNav id={id} active={active} go={go} /><ApplicationWorkflows applicationId={id} active={active} onChanged={reload} /></>;
}

function App() {
  const [path, setPath] = useState(location.pathname === '/' ? '/home' : location.pathname);
  const [apps, setApps] = useState([]);
  const [appsError, setAppsError] = useState('');
  const [appsLoading, setAppsLoading] = useState(true);
  const go = next => { history.pushState({}, '', next); setPath(next); scrollTo({ top: 0, behavior: 'instant' }); };
  const reload = async () => {
    setAppsLoading(true);
    setAppsError('');
    try {
      const next = await request('/applications?limit=50&offset=0');
      setApps(next);
      return next;
    } catch (error) {
      setAppsError(error.message);
      return null;
    } finally {
      setAppsLoading(false);
    }
  };
  useEffect(() => {
    reload();
    const handlePopState = () => setPath(location.pathname);
    addEventListener('popstate', handlePopState);
    return () => removeEventListener('popstate', handlePopState);
  }, []);

  let inner;
  const applicationMatch = path.match(/^\/applications\/(\d+)(?:\/(.*))?$/);
  const libraryMatch = path.match(/^\/library\/(\d+)$/);
  const skillMatch = path.match(/^\/skills\/(\d+)$/);
  const profileMatch = path.match(/^\/profiles\/(\d+)$/);
  const resumeMatch = path.match(/^\/resumes\/(\d+)$/);
  if (path === '/home') inner = <Home apps={apps} appsError={appsError} appsLoading={appsLoading} reload={reload} go={go} />;
  else if (path === '/applications') inner = <Applications apps={apps} appsError={appsError} appsLoading={appsLoading} go={go} reload={reload} />;
  else if (path === '/applications/new') inner = <NewApp go={go} reload={reload} />;
  else if (applicationMatch) {
    const [, id, tab = ''] = applicationMatch;
    if (tab === 'coverage') inner = <Coverage id={id} go={go} />;
    else if (['confirmations', 'proposal', 'preview', 'revisions', 'revisions/compare'].includes(tab)) {
      const active = tab.startsWith('revisions') ? 'revisions' : tab;
      inner = <ApplicationWorkflowPage id={id} active={active} go={go} reload={reload} />;
    } else inner = <AppView id={id} go={go} />;
  } else if (path === '/library/new') inner = <SourceItemCreate go={go} />;
  else if (libraryMatch) inner = <SourceItemDetail id={libraryMatch[1]} go={go} />;
  else if (path === '/library' || path === '/library/archived') inner = <SourceLibrary archived={path === '/library/archived'} go={go} />;
  else if (skillMatch) inner = <SkillDetail id={skillMatch[1]} go={go} />;
  else if (path === '/skills') inner = <SkillsPage go={go} />;
  else if (profileMatch) inner = <ProfileDetail id={profileMatch[1]} go={go} />;
  else if (path === '/profiles') inner = <ProfilesPage go={go} />;
  else if (resumeMatch) inner = <BaseResumeDetail id={resumeMatch[1]} go={go} />;
  else if (path === '/resumes') inner = <BaseResumesPage go={go} />;
  else if (path === '/settings/data') inner = <BackupsPage />;
  else inner = <Empty title="Page unavailable" copy="Return to the workspace to continue." action={<Button onClick={() => go('/home')}>Go home</Button>} />;
  return <Shell path={path} go={go}>{inner}</Shell>;
}

createRoot(document.getElementById('root')).render(<App />);
