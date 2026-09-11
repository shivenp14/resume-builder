import React, { useEffect, useMemo, useState } from 'react';

/**
 * Source-data workflows.  Every screen in this module talks to the API; the
 * UI deliberately has no seeded records or derived statistics.
 */
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000';

export async function sourceRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || data.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

const json = value => JSON.stringify(value);
const listValue = value => Array.isArray(value) ? value : String(value || '').split(',').map(x => x.trim()).filter(Boolean);
const textValue = value => Array.isArray(value) ? value.join(', ') : (value || '');

function Button({ children, kind = 'primary', type = 'button', disabled = false, onClick }) {
  return <button className={`button ${kind}`} type={type} disabled={disabled} onClick={onClick}>{children}</button>;
}

function Panel({ children, className = '' }) { return <section className={`quiet-card ${className}`}>{children}</section>; }
function ErrorText({ error }) { return error ? <p className="form-error" role="alert">{error}</p> : null; }
function Empty({ title, copy, action }) { return <Panel><div className="empty-state"><h2>{title}</h2><p>{copy}</p>{action}</div></Panel>; }
function Header({ title, copy, action }) { return <div className="page-intro"><div><h1>{title}</h1>{copy && <p>{copy}</p>}</div>{action}</div>; }
function Loading({ label = 'Loading…' }) { return <Panel><p>{label}</p></Panel>; }
function useLoad(loader, deps) {
  const [state, setState] = useState({ data: null, error: '', loading: true });
  const reload = async () => {
    setState(s => ({ ...s, loading: true, error: '' }));
    try { const data = await loader(); setState({ data, error: '', loading: false }); return data; }
    catch (error) { setState({ data: null, error: error.message, loading: false }); throw error; }
  };
  useEffect(() => { reload().catch(() => {}); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload };
}

const itemDefaults = { type: 'experience', title: '', organization: '', location: '', start_date: '', end_date: '', summary: '', tags: '' };
function ItemFields({ value, setValue, skills = [], includeSkills = true }) {
  const update = (key, next) => setValue(v => ({ ...v, [key]: next }));
  const selected = Array.isArray(value.skill_ids) ? value.skill_ids.map(Number) : [];
  return <div className="form-grid">
    <label>Type<select value={value.type || ''} onChange={e => update('type', e.target.value)}><option value="experience">Experience</option><option value="education">Education</option><option value="project">Project</option><option value="certification">Certification</option><option value="volunteer">Volunteer</option><option value="other">Other</option></select></label>
    <label>Title<input required value={value.title || ''} onChange={e => update('title', e.target.value)} /></label>
    <label>Organization<input value={value.organization || ''} onChange={e => update('organization', e.target.value)} /></label>
    <label>Location<input value={value.location || ''} onChange={e => update('location', e.target.value)} /></label>
    <label>Start date<input value={value.start_date || ''} onChange={e => update('start_date', e.target.value)} placeholder="YYYY-MM" /></label>
    <label>End date<input value={value.end_date || ''} onChange={e => update('end_date', e.target.value)} placeholder="YYYY-MM or Present" /></label>
    <label className="full">Summary<textarea rows="4" value={value.summary || ''} onChange={e => update('summary', e.target.value)} /></label>
    <label>Tags<input value={textValue(value.tags)} onChange={e => update('tags', e.target.value)} placeholder="Comma separated" /></label>
    {includeSkills && <label className="full">Linked skills<select multiple value={selected.map(String)} onChange={e => update('skill_ids', [...e.target.selectedOptions].map(o => Number(o.value)))}>{skills.map(skill => <option key={skill.id} value={skill.id}>{skill.name}{skill.verified ? ' · verified' : ''}</option>)}</select><small>Hold Command/Ctrl to select multiple.</small></label>}
  </div>;
}

function normalizeItemPayload(value) {
  const payload = { type: value.type, title: String(value.title || '').trim() };
  ['organization', 'location', 'start_date', 'end_date', 'summary'].forEach(key => { payload[key] = String(value[key] || '').trim() || null; });
  payload.tags = listValue(value.tags);
  if (value.skill_ids !== undefined) payload.skill_ids = (value.skill_ids || []).map(Number).filter(Boolean);
  return payload;
}

export function SourceItemForm({ item, skills = [], onSaved, onCancel, create = false }) {
  const [form, setForm] = useState(() => ({ ...itemDefaults, ...(item || {}), tags: textValue(item?.tags) }));
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const submit = async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const path = create ? '/content-items' : `/content-items/${item.id}`;
      const saved = await sourceRequest(path, { method: create ? 'POST' : 'PATCH', body: json(normalizeItemPayload(form)) });
      onSaved?.(saved);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return <form className="form-card" onSubmit={submit}><ItemFields value={form} setValue={setForm} skills={skills} /> <ErrorText error={error} /><div className="form-actions">{onCancel && <Button kind="ghost" onClick={onCancel}>Cancel</Button>}<Button type="submit" disabled={busy}>{busy ? 'Saving…' : create ? 'Create source item' : 'Save changes'}</Button></div></form>;
}

export function SourceItemCreate({ go, onChanged }) {
  const skillsState = useLoad(() => sourceRequest('/skills'), []);
  if (skillsState.loading) return <Loading label="Loading skills…" />;
  return <><Header title="Add source item" copy="Create a reusable source record for future applications." /><Panel><SourceItemForm skills={skillsState.data || []} create onCancel={() => go?.('/library')} onSaved={item => { onChanged?.(); go?.(`/library/${item.id}`); }} /></Panel></>;
}

export const LibraryCreate = SourceItemCreate;

function BulletForm({ bullet, onSaved, onCancel, create = false, itemId }) {
  const [form, setForm] = useState({ text: '', tags: '', supporting_facts: '', is_locked: false, is_preferred: false, ...(bullet || {}), tags: textValue(bullet?.tags), supporting_facts: textValue(bullet?.supporting_facts) });
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const update = (key, value) => setForm(v => ({ ...v, [key]: value }));
  const submit = async event => {
    event.preventDefault(); setBusy(true); setError('');
    const payload = { text: String(form.text || '').trim(), tags: listValue(form.tags), supporting_facts: Array.isArray(form.supporting_facts) ? form.supporting_facts : String(form.supporting_facts || '').split('\n').map(x => x.trim()).filter(Boolean), is_locked: Boolean(form.is_locked), is_preferred: Boolean(form.is_preferred) };
    try { const saved = await sourceRequest(create ? `/content-items/${itemId}/bullets` : `/bullets/${bullet.id}`, { method: create ? 'POST' : 'PATCH', body: json(payload) }); onSaved?.(saved); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return <form className="form-card" onSubmit={submit}><label className="full">Bullet text<textarea required rows="4" value={form.text || ''} onChange={e => update('text', e.target.value)} /></label><div className="form-grid"><label>Tags<input value={form.tags || ''} onChange={e => update('tags', e.target.value)} placeholder="Comma separated" /></label><label className="full">Supporting facts<textarea rows="3" value={form.supporting_facts || ''} onChange={e => update('supporting_facts', e.target.value)} placeholder="One fact per line" /></label><label><input type="checkbox" checked={Boolean(form.is_locked)} onChange={e => update('is_locked', e.target.checked)} /> Locked</label><label><input type="checkbox" checked={Boolean(form.is_preferred)} onChange={e => update('is_preferred', e.target.checked)} /> Preferred</label></div><ErrorText error={error} /><div className="form-actions">{onCancel && <Button kind="ghost" onClick={onCancel}>Cancel</Button>}<Button type="submit" disabled={busy}>{busy ? 'Saving…' : create ? 'Add bullet' : 'Save bullet'}</Button></div></form>;
}

function BulletRow({ bullet, onChanged }) {
  const [editing, setEditing] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const remove = async () => { if (!window.confirm('Delete this bullet?')) return; setBusy(true); setError(''); try { await sourceRequest(`/bullets/${bullet.id}`, { method: 'DELETE' }); onChanged?.(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <Panel className="list-card"><div className="section-head"><div><b>{bullet.text}</b><small>ID {bullet.id}{bullet.is_preferred ? ' · preferred' : ''}{bullet.is_locked ? ' · locked' : ''}</small></div><div className="form-actions"><Button kind="ghost" onClick={() => setEditing(!editing)}>{editing ? 'Close' : 'Edit'}</Button><Button kind="ghost" disabled={busy} onClick={remove}>Delete</Button></div></div>{bullet.supporting_facts?.length > 0 && <small>Facts: {bullet.supporting_facts.join(' · ')}</small>}{editing && <BulletForm bullet={bullet} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged?.(); }} />}{error && <ErrorText error={error} />}</Panel>;
}

export function BulletHistory({ bulletId, id = bulletId }) {
  const state = useLoad(() => sourceRequest(`/bullets/${id}/versions`), [id]);
  if (state.loading) return <Loading label="Loading bullet history…" />;
  if (state.error) return <Empty title="History unavailable" copy={state.error} />;
  return <Panel><h2>Bullet history</h2>{!state.data?.length ? <p>No history recorded.</p> : <div className="application-list">{state.data.map(version => <div className="list-card" key={version.id}><b>Version {version.version_number} · {version.action}</b><small>{version.created_at ? new Date(version.created_at).toLocaleString() : ''}</small><p>{version.text}</p><small>Changed: {(version.changed_fields || []).join(', ') || 'none'}</small></div>)}</div>}</Panel>;
}

export function SourceItemDetail({ id, go, onChanged }) {
  const itemState = useLoad(() => sourceRequest(`/content-items/${id}`), [id]);
  const skillsState = useLoad(() => sourceRequest('/skills'), []);
  const bulletsState = useLoad(() => sourceRequest(`/content-items/${id}/bullets`), [id]);
  const linksState = useLoad(() => sourceRequest(`/content-items/${id}/skills`), [id]);
  const historyState = useLoad(() => sourceRequest(`/content-items/${id}/versions`), [id]);
  const [editing, setEditing] = useState(false); const [addingBullet, setAddingBullet] = useState(false); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const reload = () => { itemState.reload(); bulletsState.reload(); linksState.reload(); historyState.reload(); onChanged?.(); };
  if (itemState.loading || skillsState.loading || bulletsState.loading || linksState.loading || historyState.loading) return <Loading label="Loading source item…" />;
  if (itemState.error) return <Empty title="Source item unavailable" copy={itemState.error} />;
  const item = itemState.data; const links = linksState.data || []; const linkedIds = links.map(link => link.skill_id);
  const archive = async () => { setBusy(true); setError(''); try { await sourceRequest(`/content-items/${id}`, { method: 'DELETE' }); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const restore = async () => { setBusy(true); setError(''); try { await sourceRequest(`/content-items/${id}/restore`, { method: 'POST' }); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const duplicate = async () => { setBusy(true); setError(''); try { const copy = await sourceRequest(`/content-items/${id}/duplicate`, { method: 'POST', body: json({}) }); onChanged?.(); go?.(`/library/${copy.id}`); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const link = async event => { const skillId = Number(event.target.value); if (!skillId) return; setBusy(true); setError(''); try { await sourceRequest(`/content-items/${id}/skills`, { method: 'POST', body: json({ skill_id: skillId, source: 'manual' }) }); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } event.target.value = ''; };
  const unlink = async skillId => { setBusy(true); setError(''); try { await sourceRequest(`/content-items/${id}/skills/${skillId}`, { method: 'DELETE' }); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <><Header title={item.title} copy={`${item.type}${item.organization ? ` · ${item.organization}` : ''}`} action={<div className="form-actions"><Button kind="ghost" onClick={() => setEditing(!editing)}>{editing ? 'Close editor' : 'Edit'}</Button>{item.is_archived ? <Button onClick={restore} disabled={busy}>Restore</Button> : <Button kind="ghost" onClick={archive} disabled={busy}>Archive</Button>}<Button onClick={duplicate} disabled={busy}>Duplicate</Button></div>} />{error && <ErrorText error={error} />}{editing ? <Panel><SourceItemForm item={{ ...item, skill_ids: linkedIds }} skills={skillsState.data || []} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} /></Panel> : <Panel><dl className="details">{[['Organization', item.organization], ['Location', item.location], ['Dates', [item.start_date, item.end_date].filter(Boolean).join(' – ')], ['Tags', textValue(item.tags)]].filter(x => x[1]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{item.summary && <p>{item.summary}</p>}</Panel>}
    <div className="overview-grid"><Panel><div className="section-head"><h2>Bullets</h2><Button onClick={() => setAddingBullet(!addingBullet)}>{addingBullet ? 'Close' : 'Add bullet'}</Button></div>{addingBullet && <BulletForm create itemId={id} onCancel={() => setAddingBullet(false)} onSaved={() => { setAddingBullet(false); bulletsState.reload(); onChanged?.(); }} />}{!(bulletsState.data || []).length ? <p>No bullets yet.</p> : <div className="application-list">{bulletsState.data.map(bullet => <BulletRow key={bullet.id} bullet={bullet} onChanged={reload} />)}</div>}</Panel><Panel><h2>Skills</h2><label>Link a skill<select defaultValue="" disabled={busy} onChange={link}><option value="">Select skill…</option>{(skillsState.data || []).filter(skill => !linkedIds.includes(skill.id)).map(skill => <option key={skill.id} value={skill.id}>{skill.name}</option>)}</select></label><div className="tag-row">{links.map(linked => <span key={linked.skill_id}>{linked.skill?.name || `Skill ${linked.skill_id}`} <button type="button" onClick={() => unlink(linked.skill_id)} aria-label={`Unlink ${linked.skill?.name || linked.skill_id}`}>×</button></span>)}</div></Panel></div>
    <div className="overview-grid"><Panel><h2>Item history</h2>{!(historyState.data || []).length ? <p>No history recorded.</p> : <div className="application-list">{historyState.data.map(version => <div className="list-card" key={version.id}><b>Version {version.version_number} · {version.action}</b><small>{version.created_at ? new Date(version.created_at).toLocaleString() : ''}</small><p>{version.title}{version.organization ? ` · ${version.organization}` : ''}</p></div>)}</div>}</Panel><Panel><h2>Record state</h2><p>{item.is_archived ? 'Archived. Restore it before adding it to a base resume.' : 'Active and available to base resumes.'}</p></Panel></div>
  </>;
}

export const LibraryDetail = SourceItemDetail;
export const SourceItemEdit = SourceItemDetail;

export function SourceLibrary({ archived = false, go, onChanged }) {
  const state = useLoad(() => sourceRequest(archived ? '/content-items/archived' : '/content-items'), [archived]);
  if (state.loading) return <Loading label="Loading source library…" />;
  if (state.error) return <Empty title="Source library unavailable" copy={state.error} />;
  const items = state.data || [];
  return <><Header title={archived ? 'Archived source items' : 'Source library'} copy="Reusable, editable records with explicit bullets and skill links." action={<Button onClick={() => go?.('/library/new')}>Add source item</Button>} /><div className="library-tabs"><Button kind={!archived ? 'primary' : 'ghost'} onClick={() => go?.('/library')}>Active</Button><Button kind={archived ? 'primary' : 'ghost'} onClick={() => go?.('/library/archived')}>Archive</Button></div>{!items.length ? <Empty title={archived ? 'No archived items' : 'No source items'} copy={archived ? 'Archived records can be restored or duplicated.' : 'Create a source item to begin.'} /> : <div className="source-grid">{items.map(item => <button className="source-card" key={item.id} onClick={() => go?.(`/library/${item.id}`)}><span className="source-type">{item.type}</span><h2>{item.title}</h2><p>{item.organization || item.summary || 'Source record'}</p><div className="source-card-foot"><span>ID {item.id}</span><span>{item.is_archived ? 'Archived' : 'Active'}</span></div></button>)}</div>}</>;
}

export const Library = SourceLibrary;

const skillDefaults = { name: '', category: '', aliases: '', notes: '', verified: false };
function SkillForm({ skill, onSaved, onCancel, create = false }) {
  const [form, setForm] = useState({ ...skillDefaults, ...(skill || {}), aliases: textValue(skill?.aliases) }); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const set = (key, value) => setForm(v => ({ ...v, [key]: value }));
  const submit = async e => { e.preventDefault(); setBusy(true); setError(''); const payload = { name: String(form.name || '').trim(), category: String(form.category || '').trim() || null, aliases: listValue(form.aliases), notes: String(form.notes || '').trim() || null, verified: Boolean(form.verified) }; try { const saved = await sourceRequest(create ? '/skills' : `/skills/${skill.id}`, { method: create ? 'POST' : 'PATCH', body: json(payload) }); onSaved?.(saved); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <form className="form-card" onSubmit={submit}><div className="form-grid"><label>Name<input required value={form.name} onChange={e => set('name', e.target.value)} /></label><label>Category<input value={form.category} onChange={e => set('category', e.target.value)} /></label><label>Aliases<input value={form.aliases} onChange={e => set('aliases', e.target.value)} placeholder="Comma separated" /></label><label className="full">Notes<textarea rows="3" value={form.notes} onChange={e => set('notes', e.target.value)} /></label><label><input type="checkbox" checked={Boolean(form.verified)} onChange={e => set('verified', e.target.checked)} /> Verified</label></div><ErrorText error={error} /><div className="form-actions">{onCancel && <Button kind="ghost" onClick={onCancel}>Cancel</Button>}<Button type="submit" disabled={busy}>{busy ? 'Saving…' : create ? 'Create skill' : 'Save skill'}</Button></div></form>;
}

export function SkillsPage({ go, onChanged }) {
  const state = useLoad(() => sourceRequest('/skills'), []); const [creating, setCreating] = useState(false); const [q, setQ] = useState('');
  if (state.loading) return <Loading label="Loading skills…" />; if (state.error) return <Empty title="Skills unavailable" copy={state.error} />;
  const rows = (state.data || []).filter(skill => `${skill.name} ${skill.category || ''} ${(skill.aliases || []).join(' ')}`.toLowerCase().includes(q.toLowerCase()));
  return <><Header title="Skills" copy="Canonical skills, aliases, verification, and source links." action={<Button onClick={() => setCreating(!creating)}>{creating ? 'Close' : 'Add skill'}</Button>} />{creating && <Panel><SkillForm create onCancel={() => setCreating(false)} onSaved={() => { setCreating(false); state.reload(); onChanged?.(); }} /></Panel>}<input className="search-field solo-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search skills" />{!rows.length ? <Empty title="No skills" copy="Create a canonical skill to link it to source records." /> : <Panel className="skills-table"><div className="table-head"><span>Name</span><span>Aliases</span><span>Category</span><span>State</span><span /></div>{rows.map(skill => <button className="skill-row" key={skill.id} onClick={() => go?.(`/skills/${skill.id}`)}><div><b>{skill.name}</b></div><span>{(skill.aliases || []).join(', ') || '—'}</span><span>{skill.category || '—'}</span><span>{skill.verified ? 'Verified' : 'Unverified'}</span><span>›</span></button>)}</Panel>}</>;
}

export const Skills = SkillsPage;

export function SkillDetail({ id, go, onChanged }) {
  const state = useLoad(() => sourceRequest(`/skills/${id}`), [id]); const linksState = useLoad(() => sourceRequest(`/skills/${id}/content-items`), [id]); const [editing, setEditing] = useState(false); const [alias, setAlias] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  if (state.loading || linksState.loading) return <Loading label="Loading skill…" />; if (state.error) return <Empty title="Skill unavailable" copy={state.error} />;
  const skill = state.data; const reload = () => { state.reload(); linksState.reload(); onChanged?.(); };
  const addAlias = async e => { e.preventDefault(); if (!alias.trim()) return; setBusy(true); setError(''); try { await sourceRequest(`/skills/${id}/aliases`, { method: 'POST', body: json({ alias: alias.trim() }) }); setAlias(''); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const removeAlias = async value => { setBusy(true); setError(''); try { await sourceRequest(`/skills/${id}/aliases/${encodeURIComponent(value)}`, { method: 'DELETE' }); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <><Header title={skill.name} copy={`${skill.category || 'Uncategorized'} · ${skill.verified ? 'Verified' : 'Unverified'}`} action={<Button kind="ghost" onClick={() => setEditing(!editing)}>{editing ? 'Close editor' : 'Edit skill'}</Button>} />{error && <ErrorText error={error} />}{editing ? <Panel><SkillForm skill={skill} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} /></Panel> : <Panel><p>{skill.notes || 'No notes recorded.'}</p><div className="tag-row">{(skill.aliases || []).map(value => <span key={value}>{value} <button type="button" onClick={() => removeAlias(value)} disabled={busy}>×</button></span>)}</div><form className="form-actions" onSubmit={addAlias}><input value={alias} onChange={e => setAlias(e.target.value)} placeholder="New alias" /><Button type="submit" disabled={busy}>Add alias</Button></form></Panel>}<Panel><h2>Linked source items</h2>{!(linksState.data || []).length ? <p>This skill is not linked to a source item.</p> : <div className="application-list">{linksState.data.map(link => <div className="list-card" key={link.id}><b>{link.source || 'manual'} · source item {link.content_item_id}</b><Button kind="ghost" onClick={() => go?.(`/library/${link.content_item_id}`)}>Open source item</Button></div>)}</div>}</Panel></>;
}

export const SkillEdit = SkillDetail;

const profileDefaults = { name: '', location: '', email: '', phone: '', linkedin: '', github: '', website: '', summary: '', notes: '', is_primary: false };
function ProfileForm({ profile, onSaved, onCancel, create = false }) { const [form, setForm] = useState({ ...profileDefaults, ...(profile || {}) }); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const set = (key, value) => setForm(v => ({ ...v, [key]: value })); const submit = async e => { e.preventDefault(); setBusy(true); setError(''); const payload = { ...form }; Object.keys(payload).forEach(key => { if (typeof payload[key] === 'string') payload[key] = payload[key].trim() || null; }); payload.is_primary = Boolean(form.is_primary); try { const saved = await sourceRequest(create ? '/personal-information' : `/personal-information/${profile.id}`, { method: create ? 'POST' : 'PATCH', body: json(payload) }); onSaved?.(saved); } catch (err) { setError(err.message); } finally { setBusy(false); } }; return <form className="form-card" onSubmit={submit}><div className="form-grid">{[['name','Name'],['location','Location'],['email','Email'],['phone','Phone'],['linkedin','LinkedIn'],['github','GitHub'],['website','Website']].map(([key,label]) => <label key={key}>{label}<input value={form[key] || ''} onChange={e => set(key, e.target.value)} /></label>)}<label className="full">Summary<textarea rows="3" value={form.summary || ''} onChange={e => set('summary', e.target.value)} /></label><label className="full">Notes<textarea rows="3" value={form.notes || ''} onChange={e => set('notes', e.target.value)} /></label><label><input type="checkbox" checked={Boolean(form.is_primary)} onChange={e => set('is_primary', e.target.checked)} /> Primary profile</label></div><ErrorText error={error} /><div className="form-actions">{onCancel && <Button kind="ghost" onClick={onCancel}>Cancel</Button>}<Button type="submit" disabled={busy}>{busy ? 'Saving…' : create ? 'Create profile' : 'Save profile'}</Button></div></form>; }

export function ProfilesPage({ go, onChanged }) { const state = useLoad(() => sourceRequest('/personal-information'), []); const [creating, setCreating] = useState(false); if (state.loading) return <Loading label="Loading profiles…" />; if (state.error) return <Empty title="Profiles unavailable" copy={state.error} />; return <><Header title="Profiles" copy="Reusable personal and contact information." action={<Button onClick={() => setCreating(!creating)}>{creating ? 'Close' : 'Add profile'}</Button>} />{creating && <Panel><ProfileForm create onCancel={() => setCreating(false)} onSaved={() => { setCreating(false); state.reload(); onChanged?.(); }} /></Panel>}{!(state.data || []).length ? <Empty title="No profiles" copy="Create a profile before composing a base resume." /> : <div className="source-grid">{state.data.map(profile => <button className="source-card" key={profile.id} onClick={() => go?.(`/profiles/${profile.id}`)}><span className="source-type">{profile.is_primary ? 'Primary profile' : 'Profile'}</span><h2>{profile.name || `Profile ${profile.id}`}</h2><p>{[profile.email, profile.phone, profile.location].filter(Boolean).join(' · ') || 'No contact details recorded'}</p></button>)}</div>}</>; }
export const Profiles = ProfilesPage;

export function ProfileDetail({ id, go, onChanged }) { const state = useLoad(() => sourceRequest(`/personal-information/${id}`), [id]); const [editing, setEditing] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); if (state.loading) return <Loading label="Loading profile…" />; if (state.error) return <Empty title="Profile unavailable" copy={state.error} />; const profile = state.data; const remove = async () => { if (!window.confirm('Delete this profile?')) return; setBusy(true); setError(''); try { await sourceRequest(`/personal-information/${id}`, { method: 'DELETE' }); onChanged?.(); go?.('/profiles'); } catch (err) { setError(err.message); } finally { setBusy(false); } }; return <><Header title={profile.name || `Profile ${id}`} copy={profile.is_primary ? 'Primary profile' : 'Reusable profile'} action={<div className="form-actions"><Button kind="ghost" onClick={() => setEditing(!editing)}>{editing ? 'Close editor' : 'Edit profile'}</Button><Button kind="ghost" disabled={busy} onClick={remove}>Delete</Button></div>} />{error && <ErrorText error={error} />}{editing ? <Panel><ProfileForm profile={profile} onCancel={() => setEditing(false)} onSaved={saved => { setEditing(false); state.reload(); onChanged?.(saved); }} /></Panel> : <Panel><dl className="details">{[['Location', profile.location],['Email', profile.email],['Phone', profile.phone],['LinkedIn', profile.linkedin],['GitHub', profile.github],['Website', profile.website]].filter(x => x[1]).map(([label,value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{profile.summary && <p>{profile.summary}</p>}{profile.notes && <p>{profile.notes}</p>}</Panel>}</>; }
export const ProfileEdit = ProfileDetail;

const resumeDefaults = { name: '', template_id: 'default', section_order: '', layout_settings: '{}', personal_information_id: '' };
function ResumeForm({ resume, profiles = [], onSaved, onCancel, create = false }) { const [form, setForm] = useState({ ...resumeDefaults, ...(resume || {}), section_order: textValue(resume?.section_order), layout_settings: JSON.stringify(resume?.layout_settings || {}, null, 2), personal_information_id: resume?.personal_information_id || '' }); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const set = (key,value) => setForm(v => ({ ...v, [key]: value })); const submit = async e => { e.preventDefault(); setBusy(true); setError(''); let settings; try { settings = JSON.parse(form.layout_settings || '{}'); } catch { setError('Layout settings must be valid JSON.'); setBusy(false); return; } const payload = { name: String(form.name || '').trim(), template_id: String(form.template_id || 'default').trim() || 'default', section_order: listValue(form.section_order), layout_settings: settings, personal_information_id: form.personal_information_id ? Number(form.personal_information_id) : null }; try { const saved = await sourceRequest(create ? '/base-resumes' : `/base-resumes/${resume.id}`, { method: create ? 'POST' : 'PATCH', body: json(payload) }); onSaved?.(saved); } catch (err) { setError(err.message); } finally { setBusy(false); } }; return <form className="form-card" onSubmit={submit}><div className="form-grid"><label>Name<input required value={form.name} onChange={e => set('name', e.target.value)} /></label><label>Template ID<input value={form.template_id} onChange={e => set('template_id', e.target.value)} /></label><label className="full">Section order<input value={form.section_order} onChange={e => set('section_order', e.target.value)} placeholder="experience, education, projects" /></label><label>Personal profile<select value={String(form.personal_information_id || '')} onChange={e => set('personal_information_id', e.target.value)}><option value="">No profile</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name || `Profile ${profile.id}`}</option>)}</select></label><label className="full">Layout settings JSON<textarea rows="4" value={form.layout_settings} onChange={e => set('layout_settings', e.target.value)} /></label></div><ErrorText error={error} /><div className="form-actions">{onCancel && <Button kind="ghost" onClick={onCancel}>Cancel</Button>}<Button type="submit" disabled={busy}>{busy ? 'Saving…' : create ? 'Create base resume' : 'Save resume'}</Button></div></form>; }

function EntryEditor({ resumeId, entry, items, onSaved, onCancel }) { const [itemId, setItemId] = useState(String(entry?.content_item_id || '')); const [bulletIds, setBulletIds] = useState((entry?.selected_bullet_ids || []).map(String)); const [order, setOrder] = useState(entry?.entry_order ?? 0); const [bullets, setBullets] = useState([]); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); useEffect(() => { if (!itemId) { setBullets([]); return; } sourceRequest(`/content-items/${itemId}/bullets`).then(setBullets).catch(err => setError(err.message)); }, [itemId]); const submit = async e => { e.preventDefault(); setBusy(true); setError(''); try { const payload = { content_item_id: Number(itemId), selected_bullet_ids: bulletIds.map(Number), entry_order: Number(order) }; const saved = await sourceRequest(entry ? `/base-entries/${entry.id}` : `/base-resumes/${resumeId}/entries`, { method: entry ? 'PATCH' : 'POST', body: json(payload) }); onSaved?.(saved); } catch (err) { setError(err.message); } finally { setBusy(false); } }; const selectableItems = items.filter(item => !item.is_archived || item.id === entry?.content_item_id); return <form className="form-card" onSubmit={submit}><div className="form-grid"><label className="full">Source item<select required value={itemId} onChange={e => { setItemId(e.target.value); setBulletIds([]); }}><option value="">Select source item…</option>{selectableItems.map(item => <option key={item.id} value={item.id}>{item.title}{item.is_archived ? ' · archived reference' : ''}</option>)}</select></label><label>Entry order<input type="number" min="0" value={order} onChange={e => setOrder(e.target.value)} /></label><label className="full">Selected bullets<select multiple value={bulletIds} onChange={e => setBulletIds([...e.target.selectedOptions].map(option => option.value))}>{bullets.map(bullet => <option key={bullet.id} value={bullet.id}>{bullet.text}</option>)}</select><small>Select exact bullets for this resume entry. Hold Command/Ctrl for multiple.</small></label></div><ErrorText error={error} /><div className="form-actions">{onCancel && <Button kind="ghost" onClick={onCancel}>Cancel</Button>}<Button type="submit" disabled={busy || !itemId}>{busy ? 'Saving…' : entry ? 'Save entry' : 'Add entry'}</Button></div></form>; }

export function BaseResumesPage({ go, onChanged }) { const state = useLoad(() => sourceRequest('/base-resumes'), []); const [profiles, setProfiles] = useState([]); const [creating, setCreating] = useState(false); useEffect(() => { sourceRequest('/personal-information').then(setProfiles).catch(() => {}); }, []); if (state.loading) return <Loading label="Loading base resumes…" />; if (state.error) return <Empty title="Base resumes unavailable" copy={state.error} />; return <><Header title="Base resumes" copy="Reusable compositions of profiles, source items, and exact bullet selections." action={<Button onClick={() => setCreating(!creating)}>{creating ? 'Close' : 'Add base resume'}</Button>} />{creating && <Panel><ResumeForm create profiles={profiles} onCancel={() => setCreating(false)} onSaved={() => { setCreating(false); state.reload(); onChanged?.(); }} /></Panel>}{!(state.data || []).length ? <Empty title="No base resumes" copy="Create one before starting an application." /> : <div className="resume-grid">{state.data.map(resume => <button className="source-card" key={resume.id} onClick={() => go?.(`/resumes/${resume.id}`)}><span className="source-type">{resume.template_id}</span><h2>{resume.name}</h2><p>{(resume.section_order || []).join(' · ') || 'No section order configured'}</p></button>)}</div>}</>; }
export const BaseResumes = BaseResumesPage;

export function BaseResumeDetail({ id, go, onChanged }) { const state = useLoad(() => sourceRequest(`/base-resumes/${id}`), [id]); const entriesState = useLoad(() => sourceRequest(`/base-resumes/${id}/entries`), [id]); const itemsState = useLoad(() => sourceRequest('/content-items?include_archived=true'), []); const profilesState = useLoad(() => sourceRequest('/personal-information'), []); const [editing, setEditing] = useState(false); const [adding, setAdding] = useState(false); const [editEntry, setEditEntry] = useState(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false); if (state.loading || entriesState.loading || itemsState.loading || profilesState.loading) return <Loading label="Loading base resume…" />; if (state.error) return <Empty title="Base resume unavailable" copy={state.error} />; const resume = state.data; const reload = () => { state.reload(); entriesState.reload(); onChanged?.(); }; const removeEntry = async entry => { if (!window.confirm('Remove this entry from the base resume?')) return; setBusy(true); setError(''); try { await sourceRequest(`/base-entries/${entry.id}`, { method: 'DELETE' }); reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } }; return <><Header title={resume.name} copy={`${resume.template_id} template`} action={<Button kind="ghost" onClick={() => setEditing(!editing)}>{editing ? 'Close editor' : 'Edit resume'}</Button>} />{error && <ErrorText error={error} />}{editing && <Panel><ResumeForm resume={resume} profiles={profilesState.data || []} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} /></Panel>}<Panel><div className="section-head"><div><h2>Entries</h2><p>Each entry chooses one source item and exact bullet IDs.</p></div><Button onClick={() => { setAdding(!adding); setEditEntry(null); }}>{adding ? 'Close' : 'Add entry'}</Button></div>{adding && <EntryEditor resumeId={id} items={itemsState.data || []} onCancel={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}{!(entriesState.data || []).length ? <p>No entries yet.</p> : <div className="application-list">{entriesState.data.map(entry => { const item = (itemsState.data || []).find(x => x.id === entry.content_item_id); return <Panel className="list-card" key={entry.id}><div className="section-head"><div><b>{entry.entry_order}. {item?.title || `Source item ${entry.content_item_id}`}</b><small>Selected bullet IDs: {(entry.selected_bullet_ids || []).join(', ') || 'none'}</small></div><div className="form-actions"><Button kind="ghost" onClick={() => { setEditEntry(entry); setAdding(false); }}>Edit</Button><Button kind="ghost" disabled={busy} onClick={() => removeEntry(entry)}>Remove</Button></div></div>{editEntry?.id === entry.id && <EntryEditor resumeId={id} entry={entry} items={itemsState.data || []} onCancel={() => setEditEntry(null)} onSaved={() => { setEditEntry(null); reload(); }} />}</Panel>; })}</div>}</Panel></>; }
export const BaseResumeEdit = BaseResumeDetail;

export function BackupsPage() { const state = useLoad(() => sourceRequest('/backups'), []); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [created, setCreated] = useState(null); const create = async () => { setBusy(true); setError(''); setCreated(null); try { const backup = await sourceRequest('/backups', { method: 'POST' }); setCreated(backup); await state.reload(); } catch (err) { setError(err.message); } finally { setBusy(false); } }; if (state.loading) return <Loading label="Loading backups…" />; return <><Header title="Data safety" copy="Create and inspect local database and artifact backups." action={<Button onClick={create} disabled={busy}>{busy ? 'Creating backup…' : 'Create backup'}</Button>} />{error && <ErrorText error={error} />}{created && <Panel className="success-banner">Backup created: {created.filename}</Panel>}{state.error ? <Empty title="Backups unavailable" copy={state.error} /> : !(state.data || []).length ? <Empty title="No backups" copy="Create a backup when you want a recoverable local archive." /> : <Panel className="backup-list">{state.data.map(backup => <div className="backup-row" key={backup.filename}><span className="backup-version">v{backup.backup_version}</span><div><b>{backup.filename}</b><small>{backup.created_at ? new Date(backup.created_at).toLocaleString() : ''}</small></div><span>{backup.size} bytes</span><span>{backup.artifact_count} artifacts</span><span>{backup.sha256?.slice(0, 10)}…</span></div>)}</Panel>}</>; }
export const Backups = BackupsPage;

export default { SourceLibrary, SourceItemCreate, SourceItemDetail, LibraryCreate, LibraryDetail, SkillsPage, SkillDetail, ProfilesPage, ProfileDetail, BaseResumesPage, BaseResumeDetail, BackupsPage };
