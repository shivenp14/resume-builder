import { test, expect } from '@playwright/test';

const api = 'http://127.0.0.1:8011';
const unique = label => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

test('proposal review presents source content and rewrites as a readable preview', async ({ page, request }) => {
  const profile = await (await request.post(`${api}/personal-information`, { data: { name: 'Proposal Preview', email: 'preview@example.test' } })).json();
  const resume = await (await request.post(`${api}/base-resumes`, { data: { name: unique('Preview resume'), personal_information_id: profile.id } })).json();
  const item = await (await request.post(`${api}/content-items`, { data: { type: 'experience', title: 'Platform Engineer', organization: 'Northwind' } })).json();
  const bullet = await (await request.post(`${api}/content-items/${item.id}/bullets`, { data: { text: 'Built internal tools for product teams' } })).json();
  await request.post(`${api}/base-resumes/${resume.id}/entries`, { data: { content_item_id: item.id, selected_bullet_ids: [bullet.id], entry_order: 1 } });
  const libraryItem = await (await request.post(`${api}/content-items`, { data: {
    type: 'project', title: 'API Reliability Toolkit', organization: 'Open Systems Lab',
    start_date: '2024-01', end_date: '2024-06', summary: 'A toolkit for service health and recovery.',
  } })).json();
  const libraryBullet = await (await request.post(`${api}/content-items/${libraryItem.id}/bullets`, { data: { text: 'Extended worker pools to recover from queue spikes' } })).json();
  const application = await (await request.post(`${api}/applications`, { data: {
    company: unique('Northwind'), position: 'Platform Engineer', base_resume_id: resume.id,
    job_description: 'Build platform tools for product teams and improve engineering workflows.',
  } })).json();

  await page.route(`**/applications/${application.id}/proposals`, route => route.fulfill({ json: [
    {
      id: 71, application_id: application.id, status: 'pending', created_at: '2026-09-01T12:00:00Z',
      payload: {
        selected_entries: [{ content_item_id: item.id, bullet_ids: [bullet.id, 9999] }, { content_item_id: 9999, bullet_ids: [] }],
        bullet_changes: [
          { bullet_id: bullet.id, proposed_text: 'Built workflow tools that helped product teams deliver platform changes.', rationale: 'Connects the work to the role.' },
          { bullet_id: 9999, proposed_text: 'Unmatched wording that needs source review.', rationale: 'No source record is available.' },
        ],
        warnings: ['A requested qualification has no verified source record.'],
        rationale: 'Kept the verified experience and proposed a clearer description.',
      },
    },
    {
      id: 72, application_id: application.id, status: 'pending', created_at: '2026-09-01T11:00:00Z',
      payload: {
        selected_entries: [{ content_item_id: item.id, bullet_ids: [bullet.id] }],
        bullet_changes: [],
        warnings: ['A requested qualification has no verified source record.'],
        rationale: 'Kept the selected source wording unchanged.',
      },
    },
    {
      id: 73, application_id: application.id, status: 'pending', created_at: '2026-09-01T10:00:00Z',
      payload: {
        selected_entries: [{ content_item_id: libraryItem.id, bullet_ids: [libraryBullet.id] }],
        bullet_changes: [{ bullet_id: libraryBullet.id, proposed_text: 'Improved service recovery by expanding worker pools to absorb queue spikes.', rationale: 'Clarifies the reliability outcome.' }],
        warnings: [],
        rationale: 'Selected relevant project experience from the source library.',
      },
    },
  ] }));
  await page.route(`**/applications/${application.id}/snapshot`, route => route.fulfill({ json: {
    contact: {}, entries: [{ content_item_id: item.id, bullet_ids: [bullet.id] }], content_items: [],
    bullets: [{ id: bullet.id, content_item_id: item.id, text: 'Built internal tools for product teams' }],
    sections: [{ key: 'experience', title: 'Experience', entries: [{
      content_item_id: item.id, title: item.title, display_title: item.title, organization: item.organization,
      dates: '2023 -- 2025', summary: 'Worked on internal product infrastructure.',
      bullets: [{ id: bullet.id, content_item_id: item.id, text: 'Built internal tools for product teams' }],
    }] }],
  } }));

  await page.goto(`/applications/${application.id}/proposal`);
  const preview = page.locator('.workflow-stage');
  const rewrittenProposal = preview.locator('.list-card').filter({ hasText: 'Proposal 71' });
  await expect(rewrittenProposal.getByText('Experience', { exact: true })).toBeVisible();
  await expect(rewrittenProposal.getByRole('heading', { name: 'Platform Engineer' })).toBeVisible();
  await expect(rewrittenProposal.getByText('Current source wording')).toBeVisible();
  await expect(rewrittenProposal.getByText('Built internal tools for product teams', { exact: true })).toBeVisible();
  await expect(rewrittenProposal.getByText('Built workflow tools that helped product teams deliver platform changes.', { exact: true })).toBeVisible();
  await expect(rewrittenProposal.getByText('A requested qualification has no verified source record.', { exact: true })).toBeVisible();
  await expect(rewrittenProposal.getByText(/selected bullet is unavailable in the current source records/i)).toBeVisible();
  await expect(rewrittenProposal.getByRole('heading', { name: 'Source entry unavailable' })).toBeVisible();
  await expect(rewrittenProposal.getByRole('heading', { name: 'Changes without a matching source bullet' })).toBeVisible();
  const unchangedProposal = preview.locator('.list-card').filter({ hasText: 'Proposal 72' });
  await expect(unchangedProposal.getByText('Built internal tools for product teams', { exact: true })).toBeVisible();
  await expect(unchangedProposal.getByText('A requested qualification has no verified source record.', { exact: true })).toBeVisible();
  await expect(unchangedProposal.getByText('Kept the selected source wording unchanged.', { exact: true })).toBeVisible();
  await expect(unchangedProposal.getByText('No additional notes were included.')).toHaveCount(0);
  await expect(unchangedProposal.getByText('Proposed wording')).toHaveCount(0);
  const libraryProposal = preview.locator('.list-card').filter({ hasText: 'Proposal 73' });
  await expect(libraryProposal.getByText('Projects', { exact: true })).toBeVisible();
  await expect(libraryProposal.getByRole('heading', { name: 'API Reliability Toolkit' })).toBeVisible();
  await expect(libraryProposal.getByText('Open Systems Lab · 2024-01 -- 2024-06', { exact: true })).toBeVisible();
  await expect(libraryProposal.getByText('Extended worker pools to recover from queue spikes', { exact: true })).toBeVisible();
  await expect(libraryProposal.getByText('Improved service recovery by expanding worker pools to absorb queue spikes.', { exact: true })).toBeVisible();
  await expect(preview.locator('pre')).toHaveCount(0);
  await expect(preview.getByText(/schema_version|selected_entries/)).toHaveCount(0);
});
