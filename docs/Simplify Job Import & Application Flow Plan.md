# Simplify Job Import & Application Flow

## Goal

Add a job-discovery and application handoff flow to the resume app that works alongside Simplify.

The resume app should remain responsible for:

- Finding and importing jobs.
- Getting the job description.
- Creating an application record.
- Analyzing the job.
- Generating a tailored resume.
- Keeping track of the exact resume created for that application.

Simplify should remain responsible for helping the user fill out the actual employer application form.

---

## Feature Progress Tracker

Status as of September 28, 2026. Checked items are implemented in the resume app.

### Job discovery

- [x] Browse active, visible internships from SimplifyJobs' public listings feed.
- [x] Show company, role, location, relative posting age, category, and employer link.
- [x] Show the optional Simplify company page link when the feed provides one.
- [x] Search and filter by company or role, category, location, and application progress; paginate results.
- [x] Handle feed errors with a retry option and keep existing applications available.

### Import and review

- [x] Pre-fill job details from a selected listing.
- [x] Attempt bounded, best-effort retrieval of the employer's job description.
- [x] Show retrieved text for editing and require the user to confirm it.
- [x] Offer the employer link and a paste field when retrieval is unavailable or unreliable.
- [x] Let the user correct company, role, location, links, and description and choose a base resume before creating an application.
- [x] Create the application only after review; continue into the existing tailoring workflow.

### Resume and application handoff

- [x] Show the exact generated revision for PDF viewing and download.
- [x] Open the employer application without marking the application submitted.
- [x] Ask the user to confirm submission and select the revision actually uploaded.
- [x] Retain the submitted revision in application history.

### Tracking and reliability

- [x] Match duplicate imports by source listing ID, with an employer URL plus company and role fallback.
- [x] Derive Not imported, Resume in progress, Ready to apply, Applied, and Closed labels from application data.
- [x] Cover the feed, scraper, import, handoff, and submission behavior with backend and desktop/mobile UI tests.

**Deferred:** Save for later before application creation. Simplify extension autofill and employer-form submission occur outside this app and are performed by the user and Simplify.

---

## 1. Browse Jobs

Add a Jobs area where the user can browse current opportunities sourced from Simplify's public job listings.

Each job should clearly show the information available from the listing, such as:

- Company.
- Role.
- Location.
- How recently the listing was added.
- Job category when available.
- A link to the original employer application.
- A link to the corresponding Simplify job page when available.

The user should be able to search or filter through these jobs and choose one they want to apply to.

The initial focus should be internships, while keeping the experience flexible enough to support other Simplify job lists later.

For the first version, read the public `listings.json` from the current SimplifyJobs summer internship repository. Keep the repository and list selection configurable so a later season or another public list does not require a new import model. Use the structured listing ID as the primary identity, and show only listings marked active and visible. Treat the Simplify link as optional because contributed listings may not have one. The current feed's `company_url` points to a Simplify company page in observed listings, so label it as a company page rather than implying it is a job-specific page.

---

## 2. Import a Job

When the user selects a job, they should be able to import it into the resume app.

The app should bring over the known information automatically, including:

- Company.
- Position.
- Location.
- Original application page.
- Simplify page.
- Other useful listing information.

Before creating the application, the app should attempt to obtain the full job description from the original employer application page.

---

## 3. Job Description Retrieval

The app should make a best-effort attempt to retrieve the job description automatically.

There are two possible outcomes.

### Job description found

If a usable job description is found:

- Display it to the user.
- Allow the user to review it.
- Allow the user to edit or correct it if necessary.
- Do not assume automatically retrieved text is perfect.

The user should confirm the description before continuing.

### Job description cannot be retrieved reliably

Some employer application pages may:

- Load their content dynamically.
- Block automated access.
- Require cookies or other browser behavior.
- Hide parts of the posting.
- Return unrelated page content instead of the actual job description.

If the app cannot confidently obtain the description, it should not treat the attempt as successful.

Instead:

- Clearly tell the user that the job description could not be imported reliably.
- Provide a button or link to open the original job posting.
- Provide a large text field where the user can paste the description.
- Let the user continue once they have pasted it.

The fallback should feel like a normal part of the workflow rather than an error.

The first implementation of description retrieval is a bounded server-side HTML/text fetch. It does not execute JavaScript. It must reject private network destinations, limit redirects, response size, and time spent fetching, and report unavailable or low-confidence results separately from a usable extraction. The user reviews and confirms every extracted description. The initial confidence rules and their test evidence are recorded in **Implementation Notes** below.

---

## 4. Review Before Creating the Application

Before creating the application, give the user a simple review screen containing:

- Company.
- Position.
- Location.
- Original application link.
- Job description.
- Base resume to use.

The user should be able to correct any imported information before continuing.

Once confirmed, the job becomes a normal application inside the existing resume app.

There is no separate **Save for later** state in this version. A listing is not imported until this review is confirmed with a nonempty description and a base resume. The application is then created in the existing `draft` state. Saving a listing before that point can be added later.

---

## 5. Tailor the Resume

After the application is created, the user should continue through the resume app's existing tailoring process.

The flow should remain:

1. Analyze the job description.
2. Compare the job requirements with the user's verified resume information.
3. Review suggested changes.
4. Approve the changes the user wants.
5. Generate the tailored resume.
6. Review the finished resume.

The Simplify integration should not change how resume tailoring works.

---

## 6. Resume Ready State

Once a tailored resume has been generated, the application should present a clear next step.

For example:

**Tailored resume ready**

- View Resume
- Download Resume
- Apply with Simplify

The user should be able to inspect or download the exact resume before opening the employer application.

---

## 7. Apply with Simplify

Selecting **Apply with Simplify** should open the original employer application page.

The expected user experience is:

1. The employer application opens.
2. The user's Simplify browser extension detects the application.
3. Simplify fills the fields it supports.
4. The user uses the tailored resume generated by the resume app.
5. The user reviews the complete application.
6. The user submits it themselves.

The resume app should not attempt to replace Simplify's form-filling functionality.

---

## 8. Resume Upload

For this version, the user remains responsible for selecting the tailored resume when the employer application asks for a resume.

The resume app should make this easy by ensuring the generated resume is readily available immediately before the user opens the application.

There should be no requirement for the resume app to automatically send the resume into the user's Simplify account.

---

## 9. Submission Tracking

Opening an employer application does **not** mean the application has been submitted.

The application should remain unsubmitted until the user explicitly confirms that they finished and submitted the employer form.

Once they confirm submission, the resume app should retain which tailored resume was used for that application.

The confirmation should ask the user to identify the generated revision they actually uploaded. Opening the employer page alone must not call the application's submission action or change its status.

This keeps the application history accurate.

---

## 10. Duplicate Jobs

If the same Simplify job appears again during a later refresh, the resume app should recognize that the user has already imported it.

It should avoid presenting the same opportunity as a completely new application.

The interface can indicate states such as:

- Not imported.
- Resume in progress.
- Ready to apply.
- Applied.
- Closed (for rejected or withdrawn applications).

Use the source listing ID for duplicate detection, with the employer URL as a secondary check. Keep the existing application lifecycle statuses. Derive the browse-page progress labels from the linked application's current workflow and submitted revision; do not add these labels as persisted lifecycle statuses.

---

## 11. Simplify's Role

The integration should intentionally stay lightweight.

Simplify is used for two purposes:

1. **Job discovery** through its public job listings.
2. **Application autofill** through Simplify Copilot after the user leaves the resume app.

The resume app should not depend on access to Simplify's private account systems or internal APIs.

If Simplify changes how its browser extension behaves, the resume-generation workflow should still work independently.

---

## 12. Features Explicitly Outside This Version

This version should not attempt to:

- Log the user into Simplify.
- Automatically submit applications.
- Automatically answer employer screening questions.
- Automatically upload the resume to every application form.
- Recreate Simplify's autofill functionality.
- Replace Simplify's job tracker.
- Control the Simplify extension.
- Depend on undocumented Simplify account functionality.

The focus is a reliable handoff between the two products.

---

## Final User Flow

The intended experience should be:

**Browse jobs → Select a job → Import job details → Retrieve or paste the job description → Review → Create application → Tailor resume → Generate resume → Download/review resume → Apply with Simplify → Simplify fills the employer form → User reviews and submits → User confirms submission in the resume app**

The feature should make moving from discovering a job to submitting a tailored application faster while keeping the resume app and Simplify responsible for separate parts of the process.

---

## Implementation Notes

### Data and workflow

1. Add a read-only listing adapter for the public SimplifyJobs internship JSON. Normalize its ID, company, title, locations, posted date, category when available, employer URL, optional Simplify URL, and source metadata. Handle an unavailable or malformed upstream feed without affecting existing applications.
2. Add the source listing ID and optional Simplify URL to application data. Keep the employer URL in the existing job URL field. Prevent duplicate imports of the same source listing. A listing stays unimported if the user leaves the review flow before confirming it.
3. Add browse, search/filter, import review, and description edit/paste screens. The existing application creation and tailoring workflow begins only after the review is confirmed.
4. After generation, show the exact generated PDF for viewing/downloading and a separate employer-page handoff. Label the later submission action as an explicit user confirmation, and record the revision the user says they uploaded.
5. Test feed normalization and duplicate matching; useful, incomplete, blocked, and misleading description pages; the paste fallback; review confirmation; handoff without submission; and explicit submission with the selected revision.

### Description retrieval probe

A first scraper has been implemented in `backend/app/services/job_description.py` with focused tests in `backend/tests/test_job_description_scraper.py`. Live probes against public Greenhouse, Lever, and Ashby employer pages returned substantial readable descriptions on five of six sampled pages (about 3,378–9,326 characters). One Greenhouse page timed out. This supports a best-effort attempt with a normal paste fallback, but does not establish a universal success rate.

The current 400-character minimum is a provisional rejection floor tested with fixtures, not a proven measure of description quality. The scraper accepts substantial structured `JobPosting` text, text from a container with job-specific markers, or visible text with a job-content heading. Long generic page content returns low confidence. HTTP errors, the absolute fetch deadline, and blocked pages return unavailable; very short text returns low confidence. Both cases lead to the normal paste fallback. Keep user confirmation mandatory even for successful extraction. Do not trust an extracted HTML page title to replace the role name supplied by the listing: one sampled Greenhouse page exposed a generic title. The corrected scraper was retested live on Greenhouse, Lever, and Ashby pages, with all three returning usable text.
