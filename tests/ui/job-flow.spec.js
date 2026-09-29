import { test, expect } from '@playwright/test';

const api = 'http://127.0.0.1:8011';
const unique = label => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

test('browse a listing, paste when retrieval is unreliable, review, then create an application', async ({ page, request }) => {
  const profileResponse = await request.post(`${api}/personal-information`, { data: { name: 'Jobs UI', email: 'jobs-ui@example.test' } });
  expect(profileResponse.ok()).toBeTruthy();
  const profile = await profileResponse.json();
  const resumeResponse = await request.post(`${api}/base-resumes`, { data: { name: unique('Import resume'), personal_information_id: profile.id } });
  expect(resumeResponse.ok()).toBeTruthy();
  const resume = await resumeResponse.json();
  const listing = {
    id: 'ui-test-listing-1', company: unique('Northwind'), position: 'Product Engineering Intern',
    locations: ['New York, NY'], date_posted: Math.floor(Date.now() / 1000), category: 'Software',
    application_url: 'https://jobs.example.test/roles/1', simplify_url: 'https://simplify.jobs/p/1',
    application_id: null, progress: 'not_imported',
  };
  let queriedProgress = '';
  const progressParameterPresent = [];
  await page.route('**/jobs?*', route => {
    const params = new URL(route.request().url()).searchParams;
    queriedProgress = params.get('progress') || '';
    progressParameterPresent.push(params.has('progress'));
    return route.fulfill({ json: { items: [listing], total: 1, page: 1, page_size: 25, categories: ['Software'] } });
  });
  await page.route('**/jobs/description', route => route.fulfill({ status: 200, json: { status: 'unavailable', text: '', title: '', source_url: listing.application_url, reason: 'The employer page blocks automated retrieval.' } }));

  await page.goto('/jobs');
  await expect(page.getByRole('heading', { name: 'Product Engineering Intern' })).toBeVisible();
  await expect(page.getByRole('article').getByText('Not imported')).toBeVisible();
  await expect.poll(() => progressParameterPresent.length > 0).toBe(true);
  expect(progressParameterPresent[0]).toBe(false);
  await page.getByRole('combobox', { name: 'Progress' }).selectOption('not_imported');
  await expect.poll(() => queriedProgress).toBe('not_imported');
  await page.getByRole('combobox', { name: 'Progress' }).selectOption('');
  await expect.poll(() => progressParameterPresent.at(-1)).toBe(false);
  await page.getByRole('button', { name: 'Review import' }).click();
  await expect(page.getByRole('heading', { name: 'Review this opportunity' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('blocks automated retrieval');
  await page.getByLabel('Base resume', { exact: true }).selectOption(String(resume.id));
  const description = 'Build dependable product experiences with the engineering team. Collaborate on design, implementation, testing, and maintenance. Candidates should know JavaScript, write clear code, and communicate technical decisions. The internship includes mentorship and meaningful project ownership.';
  await page.getByLabel('Review or paste the full job description').fill(description);
  await page.getByLabel('I reviewed and confirm this job description for the application.').check();
  await page.getByRole('button', { name: 'Create application and continue' }).click();
  await expect(page.getByRole('heading', { name: 'Product Engineering Intern' })).toBeVisible();
  const applicationId = page.url().split('/').at(-1);
  const saved = await (await request.get(`${api}/applications/${applicationId}`)).json();
  expect(saved.source_listing_id).toBe(listing.id);
  expect(saved.simplify_url).toBe(listing.simplify_url);
  expect(saved.job_url).toBe(listing.application_url);
  expect(saved.job_description).toBe(description);
});

test('keeps edits and review confirmation made while description retrieval is pending', async ({ page, request }) => {
  const profile = await (await request.post(`${api}/personal-information`, { data: { name: 'Race UI', email: 'race-ui@example.test' } })).json();
  const resume = await (await request.post(`${api}/base-resumes`, { data: { name: unique('Race resume'), personal_information_id: profile.id } })).json();
  const listing = {
    id: 'ui-test-listing-race', company: unique('Contoso'), position: 'Software Intern', locations: ['Remote'],
    date_posted: Math.floor(Date.now() / 1000), category: 'Software', application_url: 'https://jobs.example.test/race',
    simplify_url: null, application_id: null, progress: 'not_imported',
  };
  await page.route('**/jobs?*', route => route.fulfill({ json: { items: [listing], total: 1, page: 1, page_size: 25, categories: ['Software'] } }));
  let beginResponse;
  let releaseResponse;
  const responseStarted = new Promise(resolve => { beginResponse = resolve; });
  const responseGate = new Promise(resolve => { releaseResponse = resolve; });
  const extractedText = 'Automatically extracted description which must not replace edits entered while the scraper is still running.';
  await page.route('**/jobs/description', async route => {
    beginResponse();
    await responseGate;
    await route.fulfill({ json: { status: 'ok', text: extractedText, title: 'Software Intern', source_url: listing.application_url, reason: '' } });
  });

  await page.goto('/jobs');
  await page.getByRole('button', { name: 'Review import' }).click();
  await responseStarted;
  const manualText = 'I pasted and reviewed the full employer description while automatic retrieval was pending. The job is focused on product software, testing, and clear communication.';
  await page.getByLabel('Review or paste the full job description').fill(manualText);
  await page.getByLabel('I reviewed and confirm this job description for the application.').check();
  releaseResponse();
  await expect(page.getByLabel('Review or paste the full job description')).toHaveValue(manualText);
  await expect(page.getByLabel('I reviewed and confirm this job description for the application.')).toBeChecked();
  await page.getByLabel('Base resume', { exact: true }).selectOption(String(resume.id));
  await page.getByRole('button', { name: 'Create application and continue' }).click();
  await expect(page.getByRole('heading', { name: 'Software Intern' })).toBeVisible();
  const applicationId = page.url().split('/').at(-1);
  const saved = await (await request.get(`${api}/applications/${applicationId}`)).json();
  expect(saved.job_description).toBe(manualText);
});

test('employer handoff does not submit; confirmation records the selected revision', async ({ page, request, context }) => {
  const profile = await (await request.post(`${api}/personal-information`, { data: { name: 'Handoff UI', email: 'handoff-ui@example.test' } })).json();
  const resume = await (await request.post(`${api}/base-resumes`, { data: { name: unique('Handoff resume'), personal_information_id: profile.id } })).json();
  const application = await (await request.post(`${api}/applications`, { data: {
    company: unique('Fabrikam'), position: 'Design Engineer Intern', base_resume_id: resume.id,
    job_description: 'Build product interfaces with the engineering team. Communicate decisions and test changes.',
    job_url: 'https://jobs.example.test/handoff', simplify_url: 'https://simplify.jobs/p/handoff',
  } })).json();
  const revision = { id: 47, application_id: application.id, revision_number: 3, status: 'generated', generated_at: '2026-09-24T12:00:00Z', pdf_path: 'resumes/handoff.pdf', latex_path: 'resumes/handoff.tex', page_count: 1 };
  await page.route(`**/applications/${application.id}/snapshot`, route => route.fulfill({ json: { contact: {}, sections: [], entries: [] } }));
  await page.route(`**/applications/${application.id}/proposals`, route => route.fulfill({ json: [] }));
  await page.route(`**/applications/${application.id}/revisions`, route => route.fulfill({ json: [revision] }));
  let applicationReads = 0;
  await page.route(`**/applications/${application.id}`, async route => {
    applicationReads += 1;
    if (applicationReads === 1) await route.fulfill({ json: application });
    else await route.fulfill({ status: 503, json: { detail: 'Refresh failed after submission.' } });
  });
  await context.route('https://jobs.example.test/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Employer form</title>' }));
  let submittedPayload = null;
  const submittedApplication = { ...application, status: 'applied', submitted_revision_id: revision.id, submitted_at: '2026-09-25T12:00:00Z' };
  await page.route(`**/applications/${application.id}/submit`, async route => {
    submittedPayload = route.request().postDataJSON();
    await route.fulfill({ json: submittedApplication });
  });

  await page.goto(`/applications/${application.id}/preview`);
  await expect(page.getByRole('heading', { name: 'Ready to apply' })).toBeVisible();
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('link', { name: 'Open employer application (new tab)' }).click(),
  ]);
  await expect(popup).toHaveTitle('Employer form');
  expect(submittedPayload).toBeNull();
  await popup.close();

  const confirm = page.getByRole('button', { name: 'Confirm submission' });
  await expect(confirm).toBeDisabled();
  await page.getByLabel('Revision you uploaded').selectOption(String(revision.id));
  await expect(confirm).toBeDisabled();
  await page.getByLabel('I submitted the employer form and uploaded the selected resume revision.').check();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => submittedPayload?.revision_id).toBe(revision.id);
  await expect(page.locator('.submitted-note')).toContainText(`Submitted with revision ID ${revision.id}`);
  await expect(page.getByRole('button', { name: 'Confirm submission' })).toHaveCount(0);
  await expect(page.getByLabel('Revision you uploaded')).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText('Refresh failed after submission');
});
