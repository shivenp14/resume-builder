import { test, expect } from '@playwright/test';

const api = 'http://127.0.0.1:8011';
const unique = label => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
async function post(request, path, data) {
  const response = await request.post(api + path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function seed(request) {
  const item = await post(request, '/content-items', { type: 'experience', title: unique('Engineer'), organization: 'Test Company' });
  const bullet = await post(request, `/content-items/${item.id}/bullets`, { text: 'Built Python services for 4 teams', supporting_facts: ['Python', '4 teams'] });
  const profile = await post(request, '/personal-information', { name: 'UI Test', email: 'ui@example.test' });
  const resume = await post(request, '/base-resumes', { name: unique('Test resume'), personal_information_id: profile.id });
  await post(request, `/base-resumes/${resume.id}/entries`, { content_item_id: item.id, selected_bullet_ids: [bullet.id], entry_order: 1 });
  const application = await post(request, '/applications', { company: unique('Acme'), position: 'Engineer', base_resume_id: resume.id, job_description: 'Build Python services. Docker experience preferred.' });
  return { item, bullet, profile, resume, application };
}
async function navigate(page, label) {
  const menu = page.getByRole('button', { name: 'Open navigation' });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: label, exact: true }).click();
}

test.beforeEach(async ({ page }) => {
  page.on('pageerror', error => { throw error; });
});

test('applications load, search, filter, open, and recover from analysis failure', async ({ page, request }) => {
  const { application } = await seed(request);
  await page.goto('/applications');
  await page.getByRole('textbox', { name: 'Search applications' }).fill(application.company);
  await expect(page.getByRole('button', { name: new RegExp(application.company) })).toBeVisible();
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('applied');
  await expect(page.getByRole('heading', { name: 'No matching applications' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('draft');
  await page.getByRole('button', { name: new RegExp(application.company) }).click();
  await page.route(`**/applications/${application.id}/analyze`, route => route.fulfill({ status: 503, json: { detail: 'Analysis provider unavailable.' } }));
  await page.getByRole('button', { name: 'Analyze job description' }).first().click();
  await expect(page.getByRole('alert')).toContainText('Analysis provider unavailable');
  await expect(page.getByRole('navigation', { name: 'Application workflow' })).toBeVisible();
  await page.unroute(`**/applications/${application.id}/analyze`);
  await page.getByRole('button', { name: 'Analyze job description' }).first().click();
  await expect(page.getByRole('heading', { name: 'Analysis & coverage' })).toBeVisible();
  await expect(page.getByText('docker', { exact: true })).toBeVisible();
});

test('new application validates input and persists selected resume', async ({ page, request }) => {
  const { resume } = await seed(request);
  await page.goto('/applications/new');
  await page.getByRole('button', { name: 'Create application' }).click();
  await expect(page).toHaveURL(/\/applications\/new$/);
  const company = unique('UI Company');
  await page.getByLabel('Company', { exact: true }).fill(company);
  await page.getByLabel('Role', { exact: true }).fill('UI Engineer');
  await page.getByRole('combobox', { name: 'Base resume', exact: true }).selectOption(String(resume.id));
  await page.getByLabel('Job description', { exact: true }).fill('Build Python services.');
  await page.getByRole('button', { name: 'Create application' }).click();
  await expect(page.getByRole('heading', { name: 'UI Engineer' })).toBeVisible();
  const id = page.url().split('/').at(-1);
  const saved = await (await request.get(`${api}/applications/${id}`)).json();
  expect(saved.company).toBe(company);
  expect(saved.base_resume_id).toBe(resume.id);
  await navigate(page, 'Applications');
  await expect(page.getByRole('button', { name: new RegExp(company) })).toBeVisible();
});

test('source create, bullet edit preserves facts, duplicate, archive and restore', async ({ page, request }) => {
  const title = unique('Source flow');
  await page.goto('/library');
  await page.getByRole('button', { name: 'Add source item', exact: true }).click();
  await page.getByLabel('Title', { exact: true }).fill(title);
  await page.getByLabel('Organization', { exact: true }).fill('UI Company');
  await page.getByRole('button', { name: 'Create source item' }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  const id = page.url().split('/').at(-1);
  await page.getByRole('button', { name: 'Add bullet', exact: true }).click();
  await page.getByLabel('Bullet text').fill('Built a useful API');
  await page.getByLabel('Supporting facts').fill('First fact\nSecond fact');
  await page.getByRole('button', { name: 'Add bullet', exact: true }).click();
  await expect(page.getByText('Built a useful API', { exact: true })).toBeVisible();
  await page.locator('.list-card').filter({ hasText: 'Built a useful API' }).getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByLabel('Supporting facts')).toHaveValue('First fact\nSecond fact');
  await page.getByLabel('Bullet text').fill('Built a verified API');
  await page.getByRole('button', { name: 'Save bullet' }).click();
  await expect(page.getByText('Built a verified API', { exact: true })).toBeVisible();
  const bullets = await (await request.get(`${api}/content-items/${id}/bullets`)).json();
  expect(bullets[0].supporting_facts).toEqual(['First fact', 'Second fact']);
  await page.getByRole('button', { name: 'Duplicate', exact: true }).click();
  await expect(page).not.toHaveURL(new RegExp(`/library/${id}$`));
  await expect(page.getByRole('heading', { name: /Source flow/ })).toBeVisible();
  await page.getByRole('button', { name: 'Archive', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Restore', exact: true })).toBeVisible();
  await navigate(page, 'Source library');
  await page.getByRole('button', { name: 'Archive', exact: true }).click();
  await page.getByRole('button', { name: new RegExp(title) }).click();
  await page.getByRole('button', { name: 'Restore', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Archive', exact: true })).toBeVisible();
});

test('skills, aliases, profiles and base resume entry composition persist', async ({ page, request }) => {
  const { item, bullet } = await seed(request);
  const skillName = unique('BrowserSkill');
  await page.goto('/skills');
  await page.getByRole('button', { name: 'Add skill' }).click();
  await page.getByLabel('Name', { exact: true }).fill(skillName);
  await page.getByLabel('Verified', { exact: true }).check();
  await page.getByRole('button', { name: 'Create skill' }).click();
  await page.getByRole('textbox', { name: 'Search skills' }).fill(skillName);
  await page.getByRole('button', { name: new RegExp(skillName) }).click();
  await page.getByRole('textbox', { name: 'New alias' }).fill('Browser alias');
  await page.getByRole('button', { name: 'Add alias', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove alias Browser alias' })).toBeVisible();
  await page.getByRole('button', { name: 'Remove alias Browser alias' }).click();
  await expect(page.getByRole('button', { name: 'Remove alias Browser alias' })).toHaveCount(0);
  await navigate(page, 'Profiles');
  await page.getByRole('button', { name: 'Add profile' }).click();
  const profileName = unique('Browser Person');
  await page.getByLabel('Name', { exact: true }).fill(profileName);
  await page.getByLabel('Email', { exact: true }).fill('browser@example.test');
  await page.getByRole('button', { name: 'Create profile' }).click();
  await expect(page.getByRole('button', { name: new RegExp(profileName) })).toBeVisible();
  await navigate(page, 'Base resumes');
  await page.getByRole('button', { name: 'Add base resume' }).click();
  const resumeName = unique('Browser Resume');
  await page.getByLabel('Name', { exact: true }).fill(resumeName);
  await page.getByRole('combobox', { name: 'Personal profile' }).selectOption({ label: profileName });
  await page.getByRole('button', { name: 'Create base resume' }).click();
  await page.getByRole('button', { name: new RegExp(resumeName) }).click();
  await page.getByRole('button', { name: 'Add entry', exact: true }).click();
  await page.getByRole('combobox', { name: 'Source item', exact: true }).selectOption(String(item.id));
  await page.getByRole('listbox', { name: /Selected bullets/ }).selectOption(String(bullet.id));
  await page.getByRole('button', { name: 'Add entry', exact: true }).click();
  await expect(page.getByText(`0. ${item.title}`, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText(`Selected bullet IDs: ${bullet.id}`, { exact: true })).toBeVisible();
});

test('base resume detail previews a PDF before entries and refreshes after an entry save', async ({ page, request }) => {
  const { item, bullet, resume } = await seed(request);
  const replacementBullet = await post(request, `/content-items/${item.id}/bullets`, {
    text: 'Shipped the updated resume preview', supporting_facts: ['updated preview'],
  });
  const previewRequests = [];
  const previewPixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p4sAAAAASUVORK5CYII=';
  await page.route(`**/base-resumes/${resume.id}/preview-pages*`, async route => {
    previewRequests.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        page_count: 2,
        pages: [1, 2].map(page => ({ page, width: 612, height: 792, data_url: previewPixel })),
      }),
    });
  });

  await page.goto(`/resumes/${resume.id}`);
  const preview = page.locator('.base-resume-preview');
  await expect(preview).toBeVisible();
  await expect(page.locator('.resume-preview-page')).toHaveCount(2);
  await expect(page.getByAltText('Resume page 1 of 2')).toBeVisible();
  await expect(page.getByText('2 pages', { exact: true })).toBeVisible();
  await expect(page.getByTitle('Base resume PDF preview')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Open PDF' })).toHaveAttribute('href', new RegExp(`/base-resumes/${resume.id}/preview\\.pdf$`));
  await expect(page.getByRole('link', { name: 'Download PDF' })).toHaveAttribute('href', new RegExp(`/base-resumes/${resume.id}/preview\\.pdf\\?download=true$`));
  await expect.poll(() => previewRequests.length).toBe(1);
  const previewLeadsEntries = await page.evaluate(() => {
    const previewCard = document.querySelector('.base-resume-preview');
    const entriesHeading = [...document.querySelectorAll('h2')].find(heading => heading.textContent === 'Entries');
    return Boolean(previewCard && entriesHeading && (previewCard.compareDocumentPosition(entriesHeading) & Node.DOCUMENT_POSITION_FOLLOWING));
  });
  expect(previewLeadsEntries).toBe(true);
  await expect(page.getByText(`Selected bullet IDs: ${bullet.id}`, { exact: true })).toBeVisible();

  await page.locator('.list-card').filter({ hasText: item.title }).getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Selected bullets').selectOption(String(replacementBullet.id));
  await page.getByRole('button', { name: 'Save entry' }).click();

  await expect(page.getByText(`Selected bullet IDs: ${replacementBullet.id}`, { exact: true })).toBeVisible();
  await expect.poll(() => previewRequests.length).toBe(2);
  expect(new URL(previewRequests[1]).searchParams.get('refresh')).toBe('1');
  const savedEntries = await (await request.get(`${api}/base-resumes/${resume.id}/entries`)).json();
  expect(savedEntries[0].selected_bullet_ids).toEqual([replacementBullet.id]);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.resume-preview-page')).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Open PDF' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Download PDF' })).toBeVisible();
  const previewBounds = await page.locator('.resume-preview-page').first().boundingBox();
  expect(previewBounds.width).toBeLessThanOrEqual(390);
});

test('confirmed requirement materializes through its submit button', async ({ page, request }) => {
  const { application } = await seed(request);
  await post(request, `/applications/${application.id}/analyze`);
  await page.goto(`/applications/${application.id}/confirmations`);
  await page.getByRole('button', { name: 'Create from unsupported requirements' }).click();
  const row = page.locator('.list-card').filter({ has: page.getByText('docker', { exact: true }) });
  await row.getByRole('button', { name: 'Confirm', exact: true }).click();
  await row.getByRole('button', { name: 'Add source data' }).click();
  const skill = unique('Docker confirmed');
  await page.getByLabel('Skill name').fill(skill);
  await page.getByRole('button', { name: 'Materialize source record' }).click();
  await expect(row.getByText('Source record attached.')).toBeVisible();
  await expect(row.getByRole('button', { name: 'Add source data' })).toHaveCount(0);
  const skills = await (await request.get(`${api}/skills`)).json();
  expect(skills.find(item => item.name === skill)?.verified).toBe(true);
});

test('canonical revisions compare by clicking the button and clear stale results', async ({ page, request }) => {
  const { application, profile } = await seed(request);
  await page.goto(`/applications/${application.id}/preview`);
  await page.getByRole('button', { name: 'Save canonical revision' }).click();
  await expect(page.getByText('Revision 1', { exact: true })).toBeVisible();
  await request.patch(`${api}/personal-information/${profile.id}`, { data: { location: 'New York' } });
  await page.reload();
  await page.getByRole('button', { name: 'Save canonical revision' }).click();
  await expect(page.getByText('Revision 2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Revisions', exact: true }).click();
  await page.getByRole('combobox', { name: 'Before', exact: true }).selectOption({ label: 'Revision 1' });
  await page.getByRole('combobox', { name: 'After', exact: true }).selectOption({ label: 'Revision 2' });
  await page.getByRole('button', { name: 'Compare revisions' }).click();
  await expect(page.getByRole('status')).toContainText('Changes found.');
  await page.getByRole('combobox', { name: 'Before', exact: true }).selectOption({ label: 'Revision 2' });
  await expect(page.getByRole('combobox', { name: 'After', exact: true })).toHaveValue('');
  await expect(page.getByRole('status')).toHaveCount(0);
});

test('API load failure retries and validation errors remain readable', async ({ page }) => {
  await page.route('**/applications?*', route => route.fulfill({ status: 503, json: { detail: 'Workspace unavailable' } }));
  await page.goto('/applications');
  await expect(page.getByRole('heading', { name: 'Applications unavailable' })).toBeVisible();
  await page.unroute('**/applications?*');
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('textbox', { name: 'Search applications' })).toBeVisible();
  await navigate(page, 'Skills');
  await page.getByRole('button', { name: 'Add skill' }).click();
  await page.getByLabel('Name', { exact: true }).fill('Invalid skill');
  await page.route('**/skills', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'name'], msg: 'Name is already in use' }] } })
    : route.continue());
  await page.getByRole('button', { name: 'Create skill' }).click();
  await expect(page.getByRole('alert')).toContainText('name: Name is already in use');
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Invalid skill');
});

test('mobile navigation traps focus, closes on Escape, and respects reduced motion', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Mobile drawer only');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/home');
  const opener = page.getByRole('button', { name: 'Open navigation' });
  await opener.click();
  const dialog = page.getByRole('dialog', { name: 'Workspace navigation' });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close navigation' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Data safety' }).focus();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Morrow home' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(opener).toBeFocused();
  await expect(opener).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('.sidebar')).toHaveAttribute('inert', '');
  expect(await page.locator('.sidebar').evaluate(el => parseFloat(getComputedStyle(el).transitionDuration))).toBeLessThan(0.01);
  await navigate(page, 'Applications');
  await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Resume workspace' })).toBeVisible();
});

test('data safety creates and lists a local backup', async ({ page }) => {
  await page.goto('/settings/data');
  await page.getByRole('button', { name: 'Create backup', exact: true }).click();
  await expect(page.getByText(/Backup created:/)).toBeVisible();
  await expect(page.locator('.backup-row').first()).toBeVisible();
});

test('proposal review generates real artifacts and submits the exact revision', async ({ page, request }) => {
  test.setTimeout(90_000);
  const { application } = await seed(request);
  await post(request, `/applications/${application.id}/analyze`);
  await page.goto(`/applications/${application.id}/proposal`);
  await page.getByRole('button', { name: 'Generate proposal', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(page.getByText('approved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await page.getByRole('combobox', { name: 'Approved proposal' }).selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Generate PDF + LaTeX' }).click();
  await expect(page.getByRole('link', { name: 'Open PDF' })).toBeVisible({ timeout: 60_000 });
  for (const name of ['Open PDF', 'Open LaTeX']) {
    const response = await request.get(await page.getByRole('link', { name }).getAttribute('href'));
    expect(response.ok()).toBeTruthy();
  }
  await page.getByRole('button', { name: 'Submit this revision' }).click();
  await expect(page.getByRole('status')).toContainText('Revision 1 submitted.');
  await expect(page.getByRole('button', { name: 'Submitted', exact: true })).toBeDisabled();
  const saved = await (await request.get(`${api}/applications/${application.id}`)).json();
  const revisions = await (await request.get(`${api}/applications/${application.id}/revisions`)).json();
  expect(saved.status).toBe('applied');
  expect(saved.submitted_revision_id).toBe(revisions[0].id);
  await page.reload();
  for (const name of ['Open PDF', 'Open LaTeX']) {
    const response = await request.get(await page.getByRole('link', { name }).getAttribute('href'));
    expect(response.ok()).toBeTruthy();
  }
});
